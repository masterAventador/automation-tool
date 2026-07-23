import { invoke } from "@tauri-apps/api/core";

import {
  MaterialVideoStudioGatewayError,
  type MaterialVideoStudioErrorCode,
  type MaterialVideoStudioGateway,
  type MaterialVideoStudioSnapshot,
  type MaterialRenderJobSnapshot,
} from "../../features/video-studio/material-video-studio-gateway";

const MODELS = new Set(["deepseek-v4-pro", "glm-5.2", "qwen3.7-max-2026-06-08"]);
const NATIVE_ERRORS = new Set<MaterialVideoStudioErrorCode>([
  "configuration_required",
  "process_unavailable",
  "storage_unavailable",
  "view_unavailable",
  "job_unavailable",
]);
const JOB_STATUSES = new Set(["running", "succeeded", "failed", "cancelled"]);
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;

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
}
