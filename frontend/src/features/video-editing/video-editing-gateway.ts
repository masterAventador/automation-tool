import type {
  EditingJobSnapshot,
  EditingProjectSnapshot,
  EditingTimelineDraft,
  EditingTimelineSnapshot,
} from "./video-editing-dto";

export type VideoEditingErrorCode =
  | "invalid_project"
  | "invalid_timeline"
  | "draft_storage_unavailable"
  | "editing_service_unavailable";

export class VideoEditingGatewayError extends Error {
  constructor(
    readonly code: VideoEditingErrorCode,
    readonly retryable: boolean,
  ) {
    super("video editing operation unavailable");
    this.name = "VideoEditingGatewayError";
  }
}

export interface CreateEditingProjectInput {
  readonly title: string;
  readonly sourceArtifactIds: readonly string[];
}

export interface EditingVideoArtifactPayload {
  readonly artifactId: string;
  readonly mediaType: "video/mp4";
  readonly base64: string;
}

export interface VideoEditingGateway {
  listProjects(): Promise<readonly EditingProjectSnapshot[]>;
  createProject(input: CreateEditingProjectInput): Promise<EditingProjectSnapshot>;
  getTimeline(projectId: string): Promise<EditingTimelineSnapshot | null>;
  saveTimeline(
    projectId: string,
    draft: EditingTimelineDraft,
  ): Promise<EditingTimelineSnapshot>;
  listEditingJobs(projectId: string): Promise<readonly EditingJobSnapshot[]>;
  submitEditingJob(projectId: string): Promise<EditingJobSnapshot>;
  readEditingArtifact(artifactId: string): Promise<EditingVideoArtifactPayload>;
}
