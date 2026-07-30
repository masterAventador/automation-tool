import { invoke } from "@tauri-apps/api/core";

import { nativeCommandErrorFields } from "./native-command-error";
import {
  MaterialVideoStudioGatewayError,
  type MaterialVideoStudioErrorCode,
  type MaterialVideoStudioGateway,
  type MaterialVideoStudioSnapshot,
  type MaterialVideoStudioView,
  type MaterialRenderJobSnapshot,
  type MotionRenderJobSnapshot,
  type RenderedVideoArtifactPayload,
  type MotionVideoBriefRequest,
  type MotionVideoDraftRequest,
} from "../../features/video-studio/material-video-studio-gateway";
import { motionDurationProblem } from "../../features/video-studio/motion-duration";
import {
  MOTION_BRIEF_LIMITS,
  motionBriefProblem,
} from "../../features/video-studio/motion-one-sentence";

const MODELS = new Set(["deepseek-v4-pro", "glm-5.2", "qwen3.7-max-2026-06-08"]);
const NATIVE_ERRORS = new Set<MaterialVideoStudioErrorCode>([
  "configuration_required",
  "authoring_timed_out",
  "authoring_refused",
  "authoring_crashed",
  "authoring_answer_invalid",
  "authoring_model_transport_failed",
  "authoring_model_timed_out",
  "authoring_installation_damaged",
  "process_unavailable",
  "storage_unavailable",
  "view_unavailable",
  "job_unavailable",
  "draft_invalid",
  "render_unavailable",
]);
const JOB_STATUSES = new Set(["running", "succeeded", "failed", "cancelled"]);
const MOTION_JOB_STATUSES = new Set(["queued", "rendering", "encoding", "succeeded", "failed", "cancelled"]);
const MOTION_FAILURES = new Set([
  "render_failed",
  "encoding_failed",
  "interrupted",
  "static_render",
]);
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/u;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u;

function exactRecord(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function parseSnapshot(value: unknown): MaterialVideoStudioSnapshot {
  if (
    !exactRecord(value, ["modelId", "state"]) ||
    value.state !== "opened" ||
    typeof value.modelId !== "string" ||
    !MODELS.has(value.modelId)
  ) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
  return {
    state: "opened",
    modelId: value.modelId as MaterialVideoStudioSnapshot["modelId"],
  };
}

function validateView(view: MaterialVideoStudioView): void {
  if (
    !Number.isFinite(view.x) ||
    !Number.isFinite(view.y) ||
    !Number.isFinite(view.width) ||
    !Number.isFinite(view.height) ||
    Math.abs(view.x) > 8_192 ||
    Math.abs(view.y) > 8_192 ||
    view.width < 320 ||
    view.width > 8_192 ||
    view.height < 240 ||
    view.height > 8_192 ||
    typeof view.visible !== "boolean"
  ) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
}

function parseJob(value: unknown): MaterialRenderJobSnapshot {
  if (
    !exactRecord(value, [
      "artifactId", "artifactSizeBytes", "failureCode", "progressPercent", "renderJobId",
      "revision", "status", "subject",
    ]) ||
    typeof value.renderJobId !== "string" || !UUID_V4.test(value.renderJobId) ||
    typeof value.revision !== "number" || !Number.isSafeInteger(value.revision) || value.revision < 1 ||
    typeof value.status !== "string" || !JOB_STATUSES.has(value.status) ||
    typeof value.progressPercent !== "number" || !Number.isInteger(value.progressPercent) ||
    value.progressPercent < 0 || value.progressPercent > 100 ||
    typeof value.subject !== "string" || value.subject.length === 0 || [...value.subject].length > 240 ||
    (value.artifactId !== null && (typeof value.artifactId !== "string" || !UUID_V4.test(value.artifactId))) ||
    (value.artifactSizeBytes !== null && (typeof value.artifactSizeBytes !== "number" || !Number.isSafeInteger(value.artifactSizeBytes) || value.artifactSizeBytes < 0)) ||
    (value.failureCode !== null && value.failureCode !== "generation_failed") ||
    (value.status === "succeeded" && value.progressPercent !== 100) ||
    (value.status === "running" && value.progressPercent >= 100) ||
    ((value.artifactId === null) !== (value.artifactSizeBytes === null)) ||
    (value.artifactId !== null && value.status !== "succeeded") ||
    ((value.status === "failed") !== (value.failureCode === "generation_failed"))
  ) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
  return value as unknown as MaterialRenderJobSnapshot;
}

function parseJobs(value: unknown): readonly MaterialRenderJobSnapshot[] {
  if (!Array.isArray(value) || value.length > 100) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
  return value.map(parseJob);
}

function parseMotionJob(value: unknown): MotionRenderJobSnapshot {
  if (
    !exactRecord(value, [
      "artifactId", "artifactSizeBytes", "failureCode", "progressPercent", "renderJobId",
      "revision", "shotStructure", "status", "styleDisplayName", "subject",
    ]) ||
    typeof value.renderJobId !== "string" || !UUID_V4.test(value.renderJobId) ||
    typeof value.revision !== "number" || !Number.isSafeInteger(value.revision) || value.revision < 1 ||
    typeof value.status !== "string" || !MOTION_JOB_STATUSES.has(value.status) ||
    typeof value.progressPercent !== "number" || !Number.isInteger(value.progressPercent) ||
    value.progressPercent < 0 || value.progressPercent > 100 ||
    typeof value.subject !== "string" || value.subject.length === 0 || [...value.subject].length > 80 ||
    typeof value.styleDisplayName !== "string" || value.styleDisplayName.length === 0 ||
    [...value.styleDisplayName].length > 40 ||
    (value.artifactId !== null && (typeof value.artifactId !== "string" || !UUID_V4.test(value.artifactId))) ||
    (value.artifactSizeBytes !== null && (typeof value.artifactSizeBytes !== "number" ||
      !Number.isSafeInteger(value.artifactSizeBytes) || value.artifactSizeBytes <= 0)) ||
    !validMotionShotStructure(value.shotStructure, value.status) ||
    (value.failureCode !== null && (typeof value.failureCode !== "string" ||
      !MOTION_FAILURES.has(value.failureCode))) ||
    ((value.artifactId === null) !== (value.artifactSizeBytes === null)) ||
    (value.artifactId !== null && value.status !== "succeeded") ||
    ((value.status === "failed") !== (value.failureCode !== null)) ||
    (value.status === "succeeded" && value.progressPercent !== 100) ||
    (value.status !== "succeeded" && value.progressPercent >= 100)
  ) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
  return value as unknown as MotionRenderJobSnapshot;
}

const MOTION_PART_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/u;

function validMotionShotStructure(value: unknown, status: unknown): boolean {
  if (!Array.isArray(value) || value.length > 100) return false;
  // Checkpoints created before T2.2 deserialize to an empty table. New native
  // jobs always carry at least one shot, while old jobs remain readable.
  if (value.length === 0) return true;
  let declaredStart = 0;
  let renderedStart = 0;
  const measured =
    exactRecord(value[0], [
      "frameCount", "index", "narrationSeconds", "part",
      "renderedFrameCount", "renderedStartFrame", "startFrame",
    ]) && value[0].renderedFrameCount !== null;
  for (const [offset, shot] of value.entries()) {
    if (
      !exactRecord(shot, [
        "frameCount", "index", "narrationSeconds", "part",
        "renderedFrameCount", "renderedStartFrame", "startFrame",
      ]) ||
      shot.index !== offset + 1 ||
      shot.startFrame !== declaredStart ||
      typeof shot.frameCount !== "number" ||
      !Number.isSafeInteger(shot.frameCount) ||
      shot.frameCount <= 0 ||
      (shot.part !== null &&
        (typeof shot.part !== "string" || !MOTION_PART_ID.test(shot.part))) ||
      (shot.narrationSeconds !== null &&
        (typeof shot.narrationSeconds !== "number" ||
          !Number.isFinite(shot.narrationSeconds) ||
          shot.narrationSeconds <= 0 ||
          shot.narrationSeconds > shot.frameCount / 30 + 0.5))
    ) {
      return false;
    }
    declaredStart += shot.frameCount;
    if (!Number.isSafeInteger(declaredStart)) return false;
    if (measured) {
      if (
        typeof shot.renderedStartFrame !== "number" ||
        !Number.isSafeInteger(shot.renderedStartFrame) ||
        shot.renderedStartFrame !== renderedStart ||
        Math.abs(shot.renderedStartFrame - shot.startFrame) > 1 ||
        typeof shot.renderedFrameCount !== "number" ||
        !Number.isSafeInteger(shot.renderedFrameCount) ||
        shot.renderedFrameCount <= 0
      ) {
        return false;
      }
      renderedStart += shot.renderedFrameCount;
      if (
        !Number.isSafeInteger(renderedStart) ||
        Math.abs(renderedStart - declaredStart) > 1
      ) {
        return false;
      }
    } else if (
      shot.renderedStartFrame !== null ||
      shot.renderedFrameCount !== null
    ) {
      return false;
    }
  }
  return status !== "succeeded" || measured;
}

function parseMotionJobs(value: unknown): readonly MotionRenderJobSnapshot[] {
  if (!Array.isArray(value) || value.length > 100) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
  return value.map(parseMotionJob);
}

function parseRenderedVideoArtifact(value: unknown): RenderedVideoArtifactPayload {
  if (
    !exactRecord(value, ["artifactId", "base64", "mediaType"]) ||
    typeof value.artifactId !== "string" || !UUID_V4.test(value.artifactId) ||
    value.mediaType !== "video/mp4" ||
    typeof value.base64 !== "string" || value.base64.length === 0 ||
    value.base64.length > 48 * 1024 * 1024 || !BASE64.test(value.base64)
  ) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
  return value as unknown as RenderedVideoArtifactPayload;
}

function validateMotionRequest(request: MotionVideoDraftRequest): void {
  const textValid = (value: string, maximum: number) =>
    value.trim().length > 0 && [...value.trim()].length <= maximum &&
    !/[<>\0]|:\/\/|www\./iu.test(value);
  const logo = request.logo;
  if (
    request.creationMode !== "manual_template_v1" ||
    !textValid(request.subject, 80) ||
    request.stylePresetId.length === 0 || request.stylePresetId.length > 64 ||
    !HEX_COLOR.test(request.primaryColor) || !HEX_COLOR.test(request.secondaryColor) ||
    motionDurationProblem(request.beats.length, request.secondsPerBeat) !== null ||
    request.beats.some((beat) => !textValid(beat.title, 160) || !textValid(beat.caption, 160)) ||
    (logo !== null && (
      !/^[^/\\\0]{1,128}\.(?:png|jpe?g|webp)$/iu.test(logo.fileName) ||
      !["image/png", "image/jpeg", "image/webp"].includes(logo.mediaType) ||
      logo.bytes.length === 0 || logo.bytes.length > 4 * 1024 * 1024 ||
      logo.bytes.some((byte) => !Number.isInteger(byte) || byte < 0 || byte > 255)
    ))
  ) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
}

function mapError(error: unknown): MaterialVideoStudioGatewayError {
  const fields = nativeCommandErrorFields(error);
  if (fields !== undefined && NATIVE_ERRORS.has(fields.code as MaterialVideoStudioErrorCode)) {
    return new MaterialVideoStudioGatewayError(
      fields.code as MaterialVideoStudioErrorCode,
      fields.retryable,
    );
  }
  return new MaterialVideoStudioGatewayError("operation_unavailable", false);
}

export class TauriMaterialVideoStudioGateway implements MaterialVideoStudioGateway {
  async open(view: MaterialVideoStudioView): Promise<MaterialVideoStudioSnapshot> {
    validateView(view);
    try {
      return parseSnapshot(await invoke("open_material_video_studio", { view }));
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) {
        throw error;
      }
      throw mapError(error);
    }
  }

  async updateView(view: MaterialVideoStudioView): Promise<void> {
    validateView(view);
    try {
      await invoke("update_material_video_studio_view", { view });
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) throw error;
      throw mapError(error);
    }
  }

  async close(): Promise<void> {
    try {
      await invoke("close_material_video_studio");
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) throw error;
      throw mapError(error);
    }
  }

  async jobs(): Promise<readonly MaterialRenderJobSnapshot[]> {
    try {
      return parseJobs(await invoke("get_material_render_jobs"));
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) throw error;
      throw mapError(error);
    }
  }

  async cancel(renderJobId: string): Promise<void> {
    try {
      await invoke("cancel_material_render_job", { renderJobId });
    } catch (error) {
      throw mapError(error);
    }
  }

  async deleteArtifact(artifactId: string): Promise<void> {
    try {
      await invoke("delete_material_video_artifact", { artifactId });
    } catch (error) {
      throw mapError(error);
    }
  }

  async submitMotionDraft(request: MotionVideoDraftRequest): Promise<MotionRenderJobSnapshot> {
    validateMotionRequest(request);
    try {
      return parseMotionJob(await invoke("submit_motion_video_draft", { request }));
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) throw error;
      throw mapError(error);
    }
  }

  async motionJobs(): Promise<readonly MotionRenderJobSnapshot[]> {
    try {
      return parseMotionJobs(await invoke("get_motion_render_jobs"));
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) throw error;
      throw mapError(error);
    }
  }

  async cancelMotionRenderJob(renderJobId: string): Promise<void> {
    if (!UUID_V4.test(renderJobId)) {
      throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
    }
    try {
      await invoke("cancel_motion_render_job", { renderJobId });
    } catch (error) {
      throw mapError(error);
    }
  }

  async submitMotionBrief(
    request: MotionVideoBriefRequest,
  ): Promise<MotionRenderJobSnapshot> {
    // The brief is judged against the same shared contract the authoring agent
    // reads, so an input the agent would refuse never becomes a native call
    // the user has to watch fail.
    if (
      request.creationMode !== "one_sentence_v1" ||
      !MOTION_BRIEF_LIMITS.aspectRatios.includes(request.aspectRatio) ||
      !MOTION_BRIEF_LIMITS.languages.includes(request.language) ||
      motionBriefProblem(request.brief, request.durationSeconds) !== null
    ) {
      throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
    }
    try {
      return parseMotionJob(await invoke("submit_motion_video_brief", { request }));
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) throw error;
      throw mapError(error);
    }
  }

  async readMotionArtifact(artifactId: string): Promise<RenderedVideoArtifactPayload> {
    return this.readRenderedVideo("read_motion_video_artifact", artifactId);
  }

  async readMaterialArtifact(artifactId: string): Promise<RenderedVideoArtifactPayload> {
    return this.readRenderedVideo("read_material_video_artifact", artifactId);
  }

  /**
   * The two creation methods keep separate native commands because their
   * failure vocabularies differ, but the identifier check, the payload check
   * and the error mapping are the same work and are written once.
   */
  private async readRenderedVideo(
    command: "read_motion_video_artifact" | "read_material_video_artifact",
    artifactId: string,
  ): Promise<RenderedVideoArtifactPayload> {
    if (!UUID_V4.test(artifactId)) {
      throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
    }
    try {
      return parseRenderedVideoArtifact(await invoke(command, { artifactId }));
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) throw error;
      throw mapError(error);
    }
  }

  async deleteMotionArtifact(artifactId: string): Promise<void> {
    if (!UUID_V4.test(artifactId)) {
      throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
    }
    try {
      await invoke("delete_motion_video_artifact", { artifactId });
    } catch (error) {
      throw mapError(error);
    }
  }
}
