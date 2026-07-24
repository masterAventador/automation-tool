export interface MaterialVideoStudioSnapshot {
  readonly state: "opened";
  readonly modelId: "deepseek-v4-pro" | "glm-5.2" | "qwen3.7-max-2026-06-08";
}

export type MaterialRenderJobStatus = "running" | "succeeded" | "failed" | "cancelled";

export interface MaterialRenderJobSnapshot {
  readonly renderJobId: string;
  readonly revision: number;
  readonly status: MaterialRenderJobStatus;
  readonly progressPercent: number;
  readonly subject: string;
  readonly artifactId: string | null;
  readonly artifactSizeBytes: number | null;
  readonly failureCode: "generation_failed" | null;
}

export interface MotionVideoBeatDraft {
  readonly title: string;
  readonly caption: string;
}

export interface MotionVideoLogoDraft {
  readonly fileName: string;
  readonly mediaType: "image/png" | "image/jpeg" | "image/webp";
  readonly bytes: readonly number[];
}

export interface MotionVideoDraftRequest {
  readonly creationMode: "manual_template_v1";
  readonly subject: string;
  readonly stylePresetId: string;
  readonly primaryColor: string;
  readonly secondaryColor: string;
  readonly beats: readonly MotionVideoBeatDraft[];
  readonly logo: MotionVideoLogoDraft | null;
}

export type MotionRenderJobStatus = "queued" | "rendering" | "encoding" | "succeeded" | "failed" | "cancelled";

export interface MotionRenderJobSnapshot {
  readonly renderJobId: string;
  readonly revision: number;
  readonly status: MotionRenderJobStatus;
  readonly progressPercent: number;
  readonly subject: string;
  readonly styleDisplayName: string;
  readonly artifactId: string | null;
  readonly artifactSizeBytes: number | null;
  readonly failureCode: "render_failed" | "encoding_failed" | "interrupted" | null;
}

export interface MotionVideoArtifactPayload {
  readonly artifactId: string;
  readonly mediaType: "video/mp4";
  readonly base64: string;
}

export type MaterialVideoStudioErrorCode =
  | "configuration_required"
  | "process_unavailable"
  | "storage_unavailable"
  | "view_unavailable"
  | "job_unavailable"
  | "draft_invalid"
  | "render_unavailable"
  | "protocol_mismatch"
  | "operation_unavailable";

export class MaterialVideoStudioGatewayError extends Error {
  constructor(
    readonly code: MaterialVideoStudioErrorCode,
    readonly retryable: boolean,
  ) {
    super("material video studio operation unavailable");
    this.name = "MaterialVideoStudioGatewayError";
  }
}

export interface MaterialVideoStudioGateway {
  open(): Promise<MaterialVideoStudioSnapshot>;
  jobs(): Promise<readonly MaterialRenderJobSnapshot[]>;
  cancel(renderJobId: string): Promise<void>;
  deleteArtifact(artifactId: string): Promise<void>;
  submitMotionDraft(request: MotionVideoDraftRequest): Promise<MotionRenderJobSnapshot>;
  motionJobs(): Promise<readonly MotionRenderJobSnapshot[]>;
  cancelMotionRenderJob(renderJobId: string): Promise<void>;
  readMotionArtifact(artifactId: string): Promise<MotionVideoArtifactPayload>;
  deleteMotionArtifact(artifactId: string): Promise<void>;
}
