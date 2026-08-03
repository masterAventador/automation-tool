import type {
  EditingCaptionStyle,
  EditingJobSnapshot,
  EditingOutputSpec,
  EditingProjectSnapshot,
  EditingTimelineDraft,
  EditingTimelineSnapshot,
} from "./video-editing-dto";

export type VideoEditingErrorCode =
  | "invalid_project"
  | "invalid_timeline"
  | "draft_storage_unavailable"
  | "editing_service_unavailable"
  | "outcome_uncertain";

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
  readonly output: EditingOutputSpec;
  readonly captionStyle: EditingCaptionStyle;
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
}
