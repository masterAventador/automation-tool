export interface MaterialVideoStudioSnapshot {
  readonly state: "opened";
  readonly modelId: "deepseek-v4-pro" | "glm-5.2" | "qwen3.7-max-2026-06-08";
}

/** Logical child-WebView geometry; it contains no private service address. */
export interface MaterialVideoStudioView {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly visible: boolean;
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
  /** How long every beat is held; the film length is this times the beat count. */
  readonly secondsPerBeat: number;
  readonly beats: readonly MotionVideoBeatDraft[];
  readonly logo: MotionVideoLogoDraft | null;
}

/**
 * A one-sentence brief, authored automatically instead of filled in by hand.
 *
 * It is a different submission from the fixed template, not a variant of it:
 * the template carries the finished copy, this carries the intent and lets the
 * authoring agent produce the copy, the storyboard and the composition.
 */
export interface MotionVideoBriefRequest {
  readonly creationMode: "one_sentence_v1";
  readonly brief: string;
  readonly aspectRatio: string;
  readonly durationSeconds: number;
  readonly language: string;
  /**
   * Whether the video-creation model reasons before it answers.
   *
   * Carried on the request rather than configured once, because it is a
   * per-film trade: measured 2026-07-28, the reasoning phase is 31 of the 42
   * seconds authoring takes, and whether that is worth paying depends on
   * whether the operator is rehearsing or delivering.
   */
  readonly modelThinking: boolean;
}

export type MotionRenderJobStatus =
  | "queued"
  | "rendering"
  | "encoding"
  // A stop has been asked for; the executor has not confirmed it yet.
  // Cancellation is collaborative, so "asked" and "happened" are two
  // states rather than one.
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface MotionRenderJobSnapshot {
  readonly renderJobId: string;
  readonly revision: number;
  readonly status: MotionRenderJobStatus;
  readonly progressPercent: number;
  readonly subject: string;
  readonly styleDisplayName: string;
  readonly artifactId: string | null;
  readonly artifactSizeBytes: number | null;
  readonly failureCode:
    | "render_failed"
    | "encoding_failed"
    | "interrupted"
    | "static_render"
    | null;
}

/**
 * One finished film, encoded for the in-App player.
 *
 * Both creation methods import the same kind of MP4 artifact and both are read
 * back through this shape, so it is not named after either of them.
 */
export interface RenderedVideoArtifactPayload {
  readonly artifactId: string;
  readonly mediaType: "video/mp4";
  readonly base64: string;
}

/**
 * The four ways an automatic authoring run ends badly, kept apart from each
 * other and from `render_unavailable`.
 *
 * They used to be one code, together with a missing packaged runtime and a
 * worker that will not start, so a run that failed after fourteen minutes could
 * not be told from one that failed instantly on a broken install. Any code the
 * native side sends that this file does not list is flattened to
 * `operation_unavailable` by the gateway, which on screen looks like the job
 * vanishing — so a new native code must always arrive here too.
 */
export type MaterialVideoStudioErrorCode =
  | "configuration_required"
  | "authoring_timed_out"
  | "authoring_refused"
  | "authoring_crashed"
  | "authoring_answer_invalid"
  | "authoring_model_transport_failed"
  | "authoring_model_timed_out"
  | "authoring_installation_damaged"
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
  open(view: MaterialVideoStudioView): Promise<MaterialVideoStudioSnapshot>;
  updateView(view: MaterialVideoStudioView): Promise<void>;
  close(): Promise<void>;
  jobs(): Promise<readonly MaterialRenderJobSnapshot[]>;
  cancel(renderJobId: string): Promise<void>;
  deleteArtifact(artifactId: string): Promise<void>;
  submitMotionDraft(request: MotionVideoDraftRequest): Promise<MotionRenderJobSnapshot>;
  motionJobs(): Promise<readonly MotionRenderJobSnapshot[]>;
  cancelMotionRenderJob(renderJobId: string): Promise<void>;
  readMotionArtifact(artifactId: string): Promise<RenderedVideoArtifactPayload>;
  deleteMotionArtifact(artifactId: string): Promise<void>;
  submitMotionBrief(request: MotionVideoBriefRequest): Promise<MotionRenderJobSnapshot>;
  readMaterialArtifact(artifactId: string): Promise<RenderedVideoArtifactPayload>;
}
