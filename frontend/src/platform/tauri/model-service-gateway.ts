import { invoke } from "@tauri-apps/api/core";

import {
  ModelServiceGatewayError,
  type BailianModelId,
  type ConfigureModelServiceInput,
  type ModelConnectionSnapshot,
  type ModelPurposeSnapshot,
  type ModelServiceErrorCode,
  type ModelServiceGateway,
  type ModelServicePurpose,
  type ModelServiceSnapshot,
} from "../../features/settings/model-service-gateway";

const SNAPSHOT_KEYS = [
  "catalogVerifiedAt",
  "provider",
  "providerLabel",
  "sameCredential",
  "script",
  "videoCreative",
] as const;
const PURPOSE_KEYS = ["configured", "modelId", "purpose"] as const;
const CONNECTION_KEYS = ["modelId", "purpose", "quota", "status"] as const;
const QUOTA_KEYS = ["remainingRequests", "remainingTokens"] as const;
const MODEL_IDS = new Set<BailianModelId>([
  "deepseek-v4-pro",
  "glm-5.2",
  "qwen3.7-max-2026-06-08",
]);
const NATIVE_ERROR_CODES = new Set<ModelServiceErrorCode>([
  "authentication_rejected",
  "configuration_invalid",
  "configuration_required",
  "invalid_response",
  "model_unavailable",
  "quota_exhausted",
  "rate_limited",
  "storage_unavailable",
  "timed_out",
  "transport_unavailable",
]);
const MAX_QUOTA = 1_000_000_000_000;

function isExactRecord(
  value: unknown,
  expectedKeys: readonly string[],
): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const keys = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

function protocolMismatch(): ModelServiceGatewayError {
  return new ModelServiceGatewayError("protocol_mismatch", false);
}

function isPurpose(value: unknown): value is ModelServicePurpose {
  return value === "script" || value === "video_creative";
}

function isModelId(value: unknown): value is BailianModelId {
  return typeof value === "string" && MODEL_IDS.has(value as BailianModelId);
}

function parsePurpose(
  value: unknown,
  expectedPurpose: ModelServicePurpose,
): ModelPurposeSnapshot {
  if (
    !isExactRecord(value, PURPOSE_KEYS) ||
    value.purpose !== expectedPurpose ||
    typeof value.configured !== "boolean" ||
    !isModelId(value.modelId) ||
    (expectedPurpose === "video_creative" && value.modelId !== "qwen3.7-max-2026-06-08")
  ) {
    throw protocolMismatch();
  }
  return {
    purpose: expectedPurpose,
    configured: value.configured,
    modelId: value.modelId,
  };
}

function parseSnapshot(value: unknown): ModelServiceSnapshot {
  if (
    !isExactRecord(value, SNAPSHOT_KEYS) ||
    value.provider !== "bailian" ||
    value.providerLabel !== "阿里百炼" ||
    value.catalogVerifiedAt !== "2026-07-23" ||
    typeof value.sameCredential !== "boolean"
  ) {
    throw protocolMismatch();
  }
  const script = parsePurpose(value.script, "script");
  const videoCreative = parsePurpose(value.videoCreative, "video_creative");
  if (value.sameCredential && (!script.configured || !videoCreative.configured)) {
    throw protocolMismatch();
  }
  return {
    provider: "bailian",
    providerLabel: "阿里百炼",
    catalogVerifiedAt: "2026-07-23",
    script,
    videoCreative,
    sameCredential: value.sameCredential,
  };
}

function parseQuota(value: unknown): number | null {
  if (value === null) {
    return null;
  }
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < 0 ||
    value > MAX_QUOTA
  ) {
    throw protocolMismatch();
  }
  return value;
}

function parseConnection(value: unknown): ModelConnectionSnapshot {
  if (
    !isExactRecord(value, CONNECTION_KEYS) ||
    !isPurpose(value.purpose) ||
    !isModelId(value.modelId) ||
    (value.purpose === "video_creative" && value.modelId !== "qwen3.7-max-2026-06-08") ||
    value.status !== "connected" ||
    !isExactRecord(value.quota, QUOTA_KEYS)
  ) {
    throw protocolMismatch();
  }
  return {
    purpose: value.purpose,
    modelId: value.modelId,
    status: "connected",
    quota: {
      remainingRequests: parseQuota(value.quota.remainingRequests),
      remainingTokens: parseQuota(value.quota.remainingTokens),
    },
  };
}

function safeError(value: unknown): ModelServiceGatewayError {
  if (value instanceof ModelServiceGatewayError) {
    return value;
  }
  if (
    isExactRecord(value, ["code", "retryable"]) &&
    typeof value.code === "string" &&
    NATIVE_ERROR_CODES.has(value.code as ModelServiceErrorCode) &&
    typeof value.retryable === "boolean"
  ) {
    return new ModelServiceGatewayError(value.code as ModelServiceErrorCode, value.retryable);
  }
  return new ModelServiceGatewayError("operation_unavailable", false);
}

async function invokeSnapshot(command: string, args?: Record<string, unknown>) {
  try {
    return parseSnapshot(await invoke<unknown>(command, args));
  } catch (error) {
    throw safeError(error);
  }
}

export class TauriModelServiceGateway implements ModelServiceGateway {
  async getSettings(): Promise<ModelServiceSnapshot> {
    return invokeSnapshot("get_model_service_settings");
  }

  async configure(input: ConfigureModelServiceInput): Promise<ModelServiceSnapshot> {
    return invokeSnapshot("configure_model_service", { request: input });
  }

  async reuseScriptForVideo(): Promise<ModelServiceSnapshot> {
    return invokeSnapshot("reuse_script_model_service_for_video");
  }

  async clear(purpose: ModelServicePurpose): Promise<ModelServiceSnapshot> {
    return invokeSnapshot("clear_model_service", { purpose });
  }

  async testConnection(purpose: ModelServicePurpose): Promise<ModelConnectionSnapshot> {
    try {
      return parseConnection(
        await invoke<unknown>("test_model_service_connection", { purpose }),
      );
    } catch (error) {
      throw safeError(error);
    }
  }
}
