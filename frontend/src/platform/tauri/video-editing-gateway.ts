import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

import {
  editingCaptionStyleSchema,
  editingJobSchema,
  editingOutputSpecSchema,
  editingProjectSchema,
  editingProjectTitleSchema,
  editingResourceIdSchema,
  editingTimelineDraftSchema,
  editingTimelineSchema,
  type EditingJobSnapshot,
  type EditingProjectSnapshot,
  type EditingTimelineDraft,
  type EditingTimelineSnapshot,
} from "../../features/video-editing/video-editing-dto";
import {
  VideoEditingGatewayError,
  type CreateEditingProjectInput,
  type VideoEditingGateway,
} from "../../features/video-editing/video-editing-gateway";
import { nativeCommandErrorFields } from "./native-command-error";

const PAGE_LIMIT = 100;
const MAX_PAGES = 100;
const MAX_ITEMS = PAGE_LIMIT * MAX_PAGES;
const cursorSchema = z.string().min(1).max(256).regex(/^[A-Za-z0-9_-]+$/u);
const projectPageSchema = z.strictObject({
  items: z.array(editingProjectSchema).max(PAGE_LIMIT),
  nextCursor: cursorSchema.nullable(),
});
const jobPageSchema = z.strictObject({
  items: z.array(editingJobSchema).max(PAGE_LIMIT),
  nextCursor: cursorSchema.nullable(),
});
const createProjectInputSchema = z
  .strictObject({
    title: editingProjectTitleSchema,
    output: editingOutputSpecSchema,
    captionStyle: editingCaptionStyleSchema,
  })
  .refine((input) => input.captionStyle.fontPx <= input.output.height);

// Every command this gateway issues is a Control Plane request, so a
// recognized Control Plane error code has to surface as one. Mapping them to
// "editing service unavailable" sent the operator to check a local process
// that was never involved (measured 2026-08-05: a stale cloud deployment
// answered 404 on the editing endpoints). The names mirror
// `ControlPlaneErrorCode` in `control_plane.rs`; `outcome_uncertain` is
// handled first because it has its own meaning and copy.
const CONTROL_PLANE_ERROR_CODES = new Set([
  "transport_unavailable",
  "protocol_invalid",
  "request_rejected",
  "credential_missing",
  "identity_unavailable",
  "storage_unavailable",
  "installation_access_denied",
  "installation_busy",
  "installation_conflict",
  "operation_unavailable",
  "resource_not_found",
]);

// Session failures get their own bucket: their remedy is signing in again,
// and "please check your network" would be a ready-made misdirection the day
// the U9 product account ships (REVIEW-2026-08-06 M3).
const ACCOUNT_SESSION_ERROR_CODES = new Set([
  "authentication_invalid",
  "recovery_invalid",
  "account_session_invalid",
]);

function unavailable(retryable = false): VideoEditingGatewayError {
  return new VideoEditingGatewayError("editing_service_unavailable", retryable);
}

function mapNativeError(value: unknown): VideoEditingGatewayError {
  const fields = nativeCommandErrorFields(value);
  if (fields?.code === "outcome_uncertain" && fields.retryable === false) {
    return new VideoEditingGatewayError("outcome_uncertain", false);
  }
  if (fields !== undefined && ACCOUNT_SESSION_ERROR_CODES.has(fields.code)) {
    return new VideoEditingGatewayError("account_session_expired", false);
  }
  if (fields !== undefined && CONTROL_PLANE_ERROR_CODES.has(fields.code)) {
    return new VideoEditingGatewayError("control_plane_unavailable", fields.retryable);
  }
  // Every command this gateway issues is a Control Plane request, so a shape
  // we do not recognize is still a failed Control Plane request; pointing at
  // the local editing process here was the same misdirection this file was
  // rewritten to remove, only narrower (REVIEW-2026-08-06 M4).
  return new VideoEditingGatewayError("control_plane_unavailable", false);
}

async function safeInvoke(command: string, args: Record<string, unknown>): Promise<unknown> {
  try {
    return await invoke<unknown>(command, args);
  } catch (error) {
    throw mapNativeError(error);
  }
}

function requireProjectId(projectId: string): string {
  const parsed = editingResourceIdSchema.safeParse(projectId);
  if (!parsed.success) {
    throw new VideoEditingGatewayError("invalid_project", false);
  }
  return parsed.data;
}

function parseProject(value: unknown): EditingProjectSnapshot {
  const parsed = editingProjectSchema.safeParse(value);
  if (!parsed.success) {
    throw new VideoEditingGatewayError("invalid_project", false);
  }
  return parsed.data;
}

function parseTimeline(
  value: unknown,
  projectId: string,
): EditingTimelineSnapshot {
  const parsed = editingTimelineSchema.safeParse(value);
  if (!parsed.success || parsed.data.projectId !== projectId) {
    throw new VideoEditingGatewayError("invalid_timeline", false);
  }
  return parsed.data;
}

function parseJob(value: unknown, projectId: string): EditingJobSnapshot {
  const parsed = editingJobSchema.safeParse(value);
  if (!parsed.success || parsed.data.projectId !== projectId) {
    throw unavailable(false);
  }
  return parsed.data;
}

function projectMatchesInput(
  project: EditingProjectSnapshot,
  input: CreateEditingProjectInput,
): boolean {
  return (
    project.title === input.title &&
    project.output.width === input.output.width &&
    project.output.height === input.output.height &&
    project.output.fps === input.output.fps &&
    project.captionStyle.fontKey === input.captionStyle.fontKey &&
    project.captionStyle.fontPx === input.captionStyle.fontPx &&
    project.captionStyle.strokePx === input.captionStyle.strokePx &&
    project.captionStyle.lineSpacing === input.captionStyle.lineSpacing
  );
}

function timelineMatchesDraft(
  timeline: EditingTimelineSnapshot,
  draft: EditingTimelineDraft,
): boolean {
  return (
    timeline.durationMs === draft.durationMs &&
    JSON.stringify(timeline.tracks) === JSON.stringify(draft.tracks)
  );
}

async function collectProjects(): Promise<readonly EditingProjectSnapshot[]> {
  const items: EditingProjectSnapshot[] = [];
  const identifiers = new Set<string>();
  const cursors = new Set<string>();
  let cursor: string | null = null;
  for (let pageNumber = 0; pageNumber < MAX_PAGES; pageNumber += 1) {
    const response = await safeInvoke("list_editing_projects", {
      cursor,
      limit: PAGE_LIMIT,
    });
    const page = projectPageSchema.safeParse(response);
    if (!page.success) {
      throw unavailable(false);
    }
    for (const project of page.data.items) {
      if (identifiers.has(project.projectId) || items.length >= MAX_ITEMS) {
        throw unavailable(false);
      }
      identifiers.add(project.projectId);
      items.push(project);
    }
    cursor = page.data.nextCursor;
    if (cursor === null) {
      return items;
    }
    if (cursors.has(cursor)) {
      throw unavailable(false);
    }
    cursors.add(cursor);
  }
  throw unavailable(false);
}

async function collectJobs(projectId: string): Promise<readonly EditingJobSnapshot[]> {
  const items: EditingJobSnapshot[] = [];
  const identifiers = new Set<string>();
  const cursors = new Set<string>();
  let cursor: string | null = null;
  for (let pageNumber = 0; pageNumber < MAX_PAGES; pageNumber += 1) {
    const response = await safeInvoke("list_editing_jobs", {
      projectId,
      cursor,
      limit: PAGE_LIMIT,
    });
    const page = jobPageSchema.safeParse(response);
    if (!page.success) {
      throw unavailable(false);
    }
    for (const value of page.data.items) {
      const job = parseJob(value, projectId);
      if (identifiers.has(job.jobId) || items.length >= MAX_ITEMS) {
        throw unavailable(false);
      }
      identifiers.add(job.jobId);
      items.push(job);
    }
    cursor = page.data.nextCursor;
    if (cursor === null) {
      return items;
    }
    if (cursors.has(cursor)) {
      throw unavailable(false);
    }
    cursors.add(cursor);
  }
  throw unavailable(false);
}

export class TauriVideoEditingGateway implements VideoEditingGateway {
  async listProjects(): Promise<readonly EditingProjectSnapshot[]> {
    return collectProjects();
  }

  async createProject(input: CreateEditingProjectInput): Promise<EditingProjectSnapshot> {
    const validated = createProjectInputSchema.safeParse(input);
    if (!validated.success) {
      throw new VideoEditingGatewayError("invalid_project", false);
    }
    const project = parseProject(
      await safeInvoke("create_editing_project", { request: validated.data }),
    );
    if (!projectMatchesInput(project, validated.data)) {
      throw new VideoEditingGatewayError("invalid_project", false);
    }
    return project;
  }

  async getTimeline(projectId: string): Promise<EditingTimelineSnapshot | null> {
    const validatedProjectId = requireProjectId(projectId);
    const response = await safeInvoke("get_editing_project_timeline", {
      projectId: validatedProjectId,
    });
    return response === null ? null : parseTimeline(response, validatedProjectId);
  }

  async saveTimeline(
    projectId: string,
    draft: EditingTimelineDraft,
  ): Promise<EditingTimelineSnapshot> {
    const validatedProjectId = requireProjectId(projectId);
    const validatedDraft = editingTimelineDraftSchema.safeParse(draft);
    if (!validatedDraft.success) {
      throw new VideoEditingGatewayError("invalid_timeline", false);
    }
    const response = await safeInvoke("save_editing_project_timeline", {
      projectId: validatedProjectId,
      draft: validatedDraft.data,
    });
    const timeline = parseTimeline(response, validatedProjectId);
    if (!timelineMatchesDraft(timeline, validatedDraft.data)) {
      throw new VideoEditingGatewayError("invalid_timeline", false);
    }
    return timeline;
  }

  async listEditingJobs(projectId: string): Promise<readonly EditingJobSnapshot[]> {
    return collectJobs(requireProjectId(projectId));
  }

  async submitEditingJob(projectId: string): Promise<EditingJobSnapshot> {
    const validatedProjectId = requireProjectId(projectId);
    const response = await safeInvoke("submit_editing_job", {
      projectId: validatedProjectId,
    });
    const job = parseJob(response, validatedProjectId);
    if (job.status !== "queued") {
      throw unavailable(false);
    }
    return job;
  }
}
