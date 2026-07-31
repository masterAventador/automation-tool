import { z } from "zod";

import {
  editingProjectSchema,
  editingTimelineDraftSchema,
  editingTimelineSchema,
  type EditingProjectSnapshot,
  type EditingTimelineDraft,
} from "./video-editing-dto";
import {
  VideoEditingGatewayError,
  type CreateEditingProjectInput,
  type VideoEditingGateway,
} from "./video-editing-gateway";

/**
 * Local draft store for the standalone editing workbench.
 *
 * The cloud editing provider chain (VE-04+) is not connected yet, so projects
 * and timeline revisions live in an App-local draft store; there are no
 * editing jobs and submission always fails closed as unavailable.
 */

const STORAGE_KEY = "automation-tool.video-editing.local-draft.v1";
const STATE_VERSION = "video-editing.local-draft.v1";

export interface LocalDraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const stateSchema = z.strictObject({
  version: z.literal(STATE_VERSION),
  projects: z.array(editingProjectSchema),
  timelines: z.record(z.string(), editingTimelineSchema),
});

type LocalDraftState = z.infer<typeof stateSchema>;

const EMPTY_STATE: LocalDraftState = {
  version: STATE_VERSION,
  projects: [],
  timelines: {},
};

export function createLocalVideoEditingGateway(
  storage: LocalDraftStorage,
): VideoEditingGateway {
  function readState(): LocalDraftState {
    let raw: string | null;
    try {
      raw = storage.getItem(STORAGE_KEY);
    } catch {
      throw new VideoEditingGatewayError("draft_storage_unavailable", true);
    }
    if (raw === null) {
      return EMPTY_STATE;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new VideoEditingGatewayError("draft_storage_unavailable", false);
    }
    const state = stateSchema.safeParse(parsed);
    if (!state.success) {
      throw new VideoEditingGatewayError("draft_storage_unavailable", false);
    }
    return state.data;
  }

  function writeState(state: LocalDraftState): void {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      throw new VideoEditingGatewayError("draft_storage_unavailable", true);
    }
  }

  function requireProject(state: LocalDraftState, projectId: string): EditingProjectSnapshot {
    const project = state.projects.find((candidate) => candidate.projectId === projectId);
    if (project === undefined) {
      throw new VideoEditingGatewayError("invalid_project", false);
    }
    return project;
  }

  return {
    async listProjects() {
      return readState().projects;
    },

    async createProject(input: CreateEditingProjectInput) {
      const state = readState();
      const now = new Date().toISOString();
      const candidate = {
        projectId: crypto.randomUUID(),
        title: input.title,
        output: input.output,
        captionStyle: input.captionStyle,
        createdAt: now,
      };
      const project = editingProjectSchema.safeParse(candidate);
      if (!project.success) {
        throw new VideoEditingGatewayError("invalid_project", false);
      }
      writeState({ ...state, projects: [...state.projects, project.data] });
      return project.data;
    },

    async getTimeline(projectId: string) {
      const state = readState();
      requireProject(state, projectId);
      return state.timelines[projectId] ?? null;
    },

    async saveTimeline(projectId: string, draft: EditingTimelineDraft) {
      const state = readState();
      requireProject(state, projectId);
      const validated = editingTimelineDraftSchema.safeParse(draft);
      if (!validated.success) {
        throw new VideoEditingGatewayError("invalid_timeline", false);
      }
      const previous = state.timelines[projectId];
      const candidate = {
        timelineId: previous?.timelineId ?? crypto.randomUUID(),
        projectId,
        revision: (previous?.revision ?? 0) + 1,
        durationMs: validated.data.durationMs,
        tracks: validated.data.tracks,
        createdAt: new Date().toISOString(),
      };
      const timeline = editingTimelineSchema.safeParse(candidate);
      if (!timeline.success) {
        throw new VideoEditingGatewayError("invalid_timeline", false);
      }
      writeState({
        ...state,
        timelines: { ...state.timelines, [projectId]: timeline.data },
      });
      return timeline.data;
    },

    async listEditingJobs(projectId: string) {
      const state = readState();
      requireProject(state, projectId);
      return [];
    },

    async submitEditingJob(projectId: string) {
      const state = readState();
      requireProject(state, projectId);
      throw new VideoEditingGatewayError("editing_service_unavailable", false);
    },
  };
}
