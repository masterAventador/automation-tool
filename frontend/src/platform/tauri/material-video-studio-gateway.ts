import { invoke } from "@tauri-apps/api/core";

import {
  MaterialVideoStudioGatewayError,
  type MaterialVideoStudioErrorCode,
  type MaterialVideoStudioGateway,
  type MaterialVideoStudioSnapshot,
} from "../../features/video-studio/material-video-studio-gateway";

const MODELS = new Set(["deepseek-v4-pro", "glm-5.2", "qwen3.7-max-2026-06-08"]);
const NATIVE_ERRORS = new Set<MaterialVideoStudioErrorCode>([
  "configuration_required",
  "process_unavailable",
  "storage_unavailable",
  "view_unavailable",
]);

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
}
