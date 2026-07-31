import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

import {
  editingJobSchema,
  editingProjectSchema,
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
  type EditingVideoArtifactPayload,
  type VideoEditingErrorCode,
  type VideoEditingGateway,
} from "../../features/video-editing/video-editing-gateway";
import { nativeCommandErrorFields } from "./native-command-error";

const projectListSchema = z.array(editingProjectSchema);
const jobListSchema = z.array(editingJobSchema);
const optionalTimelineSchema = editingTimelineSchema.nullable();
const editingVideoArtifactSchema = z.strictObject({
  artifactId: z.string().uuid({ version: "v4" }),
  mediaType: z.literal("video/mp4"),
  base64: z.string().min(1).max(48 * 1024 * 1024).regex(/^[A-Za-z0-9+/]+={0,2}$/u),
});
const NATIVE_ERROR_CODES = new Set<VideoEditingErrorCode>([
  "invalid_project",
  "invalid_timeline",
  "draft_storage_unavailable",
  "editing_service_unavailable",
]);

function protocolMismatch(): VideoEditingGatewayError {
  return new VideoEditingGatewayError("draft_storage_unavailable", false);
}

function safeError(value: unknown): VideoEditingGatewayError {
  if (value instanceof VideoEditingGatewayError) {
    return value;
  }
  const fields = nativeCommandErrorFields(value);
  if (fields !== undefined && NATIVE_ERROR_CODES.has(fields.code as VideoEditingErrorCode)) {
    return new VideoEditingGatewayError(
      fields.code as VideoEditingErrorCode,
      fields.retryable,
    );
  }
  return protocolMismatch();
}

async function native<T>(
  command: string,
  parser: z.ZodType<T>,
  args?: Record<string, unknown>,
): Promise<T> {
  try {
    const parsed = parser.safeParse(await invoke<unknown>(command, args));
    if (!parsed.success) {
      throw protocolMismatch();
    }
    return parsed.data;
  } catch (error) {
    throw safeError(error);
  }
}

export class TauriVideoEditingGateway implements VideoEditingGateway {
  async listProjects(): Promise<readonly EditingProjectSnapshot[]> {
    return native("list_video_editing_projects", projectListSchema);
  }

  async createProject(input: CreateEditingProjectInput): Promise<EditingProjectSnapshot> {
    return native("create_video_editing_project", editingProjectSchema, {
      request: {
        title: input.title,
        sourceArtifactIds: [...input.sourceArtifactIds],
      },
    });
  }

  async getTimeline(projectId: string): Promise<EditingTimelineSnapshot | null> {
    return native("get_video_editing_timeline", optionalTimelineSchema, { projectId });
  }

  async saveTimeline(
    projectId: string,
    draft: EditingTimelineDraft,
  ): Promise<EditingTimelineSnapshot> {
    const validated = editingTimelineDraftSchema.safeParse(draft);
    if (!validated.success) {
      throw new VideoEditingGatewayError("invalid_timeline", false);
    }
    return native("save_video_editing_timeline", editingTimelineSchema, {
      projectId,
      draft: validated.data,
    });
  }

  async listEditingJobs(projectId: string): Promise<readonly EditingJobSnapshot[]> {
    return native("list_video_editing_jobs", jobListSchema, { projectId });
  }

  async submitEditingJob(projectId: string): Promise<EditingJobSnapshot> {
    return native("submit_video_editing_job", editingJobSchema, { projectId });
  }

  async readEditingArtifact(artifactId: string): Promise<EditingVideoArtifactPayload> {
    return native("read_video_editing_artifact", editingVideoArtifactSchema, { artifactId });
  }
}
