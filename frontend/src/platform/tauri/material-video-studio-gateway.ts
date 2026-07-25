import { invoke } from "@tauri-apps/api/core";

import {
  MaterialVideoStudioGatewayError,
  type MaterialVideoStudioErrorCode,
  type MaterialVideoStudioGateway,
  type MaterialVideoStudioSnapshot,
  type MaterialRenderJobSnapshot,
  type MotionRenderJobSnapshot,
  type MotionVideoArtifactPayload,
  type MotionVideoDraftRequest,
} from "../../features/video-studio/material-video-studio-gateway";
import { motionDurationProblem } from "../../features/video-studio/motion-duration";

const MODELS = new Set(["deepseek-v4-pro", "glm-5.2", "qwen3.7-max-2026-06-08"]);
const NATIVE_ERRORS = new Set<MaterialVideoStudioErrorCode>([
  "configuration_required",
  "process_unavailable",
  "storage_unavailable",
  "view_unavailable",
  "job_unavailable",
  "draft_invalid",
  "render_unavailable",
]);
const JOB_STATUSES = new Set(["running", "succeeded", "failed", "cancelled"]);
const MOTION_JOB_STATUSES = new Set(["queued", "rendering", "encoding", "succeeded", "failed", "cancelled"]);
const MOTION_FAILURES = new Set(["render_failed", "encoding_failed", "interrupted"]);
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
      "revision", "status", "styleDisplayName", "subject",
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

function parseMotionJobs(value: unknown): readonly MotionRenderJobSnapshot[] {
  if (!Array.isArray(value) || value.length > 100) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
  return value.map(parseMotionJob);
}

function parseMotionArtifact(value: unknown): MotionVideoArtifactPayload {
  if (
    !exactRecord(value, ["artifactId", "base64", "mediaType"]) ||
    typeof value.artifactId !== "string" || !UUID_V4.test(value.artifactId) ||
    value.mediaType !== "video/mp4" ||
    typeof value.base64 !== "string" || value.base64.length === 0 ||
    value.base64.length > 48 * 1024 * 1024 || !BASE64.test(value.base64)
  ) {
    throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
  }
  return value as unknown as MotionVideoArtifactPayload;
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
  if (
    exactRecord(error, ["code", "retryable"]) &&
    typeof error.code === "string" &&
    NATIVE_ERRORS.has(error.code as MaterialVideoStudioErrorCode) &&
    typeof error.retryable === "boolean"
  ) {
    return new MaterialVideoStudioGatewayError(
      error.code as MaterialVideoStudioErrorCode,
      error.retryable,
    );
  }
  return new MaterialVideoStudioGatewayError("operation_unavailable", false);
}

export class TauriMaterialVideoStudioGateway implements MaterialVideoStudioGateway {
  async open(): Promise<MaterialVideoStudioSnapshot> {
    try {
      return parseSnapshot(await invoke("open_material_video_studio"));
    } catch (error) {
      if (error instanceof MaterialVideoStudioGatewayError) {
        throw error;
      }
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

  async readMotionArtifact(artifactId: string): Promise<MotionVideoArtifactPayload> {
    if (!UUID_V4.test(artifactId)) {
      throw new MaterialVideoStudioGatewayError("protocol_mismatch", false);
    }
    try {
      return parseMotionArtifact(await invoke("read_motion_video_artifact", { artifactId }));
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
