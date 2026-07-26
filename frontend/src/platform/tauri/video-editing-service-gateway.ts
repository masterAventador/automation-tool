import { invoke } from "@tauri-apps/api/core";

import { nativeCommandErrorFields } from "./native-command-error";
import {
  VideoEditingServiceGatewayError,
  type AliyunEditingRegion,
  type ConfigureVideoEditingServiceInput,
  type VideoEditingConnectionSnapshot,
  type VideoEditingServiceErrorCode,
  type VideoEditingServiceGateway,
  type VideoEditingServiceSnapshot,
} from "../../features/settings/video-editing-service-gateway";

const SNAPSHOT_KEYS = [
  "catalogVerifiedAt",
  "configured",
  "provider",
  "providerLabel",
  "region",
] as const;
const CONNECTION_KEYS = ["region", "status"] as const;
const REGIONS = new Set<AliyunEditingRegion>([
  "cn-beijing",
  "cn-hangzhou",
  "cn-shanghai",
  "cn-shenzhen",
  "ap-southeast-1",
  "us-west-1",
]);
const NATIVE_ERROR_CODES = new Set<VideoEditingServiceErrorCode>([
  "authentication_rejected",
  "configuration_invalid",
  "configuration_required",
  "invalid_response",
  "permission_denied",
  "rate_limited",
  "storage_unavailable",
  "timed_out",
  "transport_unavailable",
]);

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

function protocolMismatch(): VideoEditingServiceGatewayError {
  return new VideoEditingServiceGatewayError("protocol_mismatch", false);
}

function isRegion(value: unknown): value is AliyunEditingRegion {
  return typeof value === "string" && REGIONS.has(value as AliyunEditingRegion);
}

function parseSnapshot(value: unknown): VideoEditingServiceSnapshot {
  if (
    !isExactRecord(value, SNAPSHOT_KEYS) ||
    value.provider !== "aliyun_ims" ||
    value.providerLabel !== "阿里云视频剪辑服务" ||
    value.catalogVerifiedAt !== "2026-07-23" ||
    typeof value.configured !== "boolean" ||
    (value.region !== null && !isRegion(value.region)) ||
    (value.configured && value.region === null) ||
    (!value.configured && value.region !== null)
  ) {
    throw protocolMismatch();
  }
  return {
    provider: "aliyun_ims",
    providerLabel: "阿里云视频剪辑服务",
    catalogVerifiedAt: "2026-07-23",
    configured: value.configured,
    region: value.region as AliyunEditingRegion | null,
  };
}

function parseConnection(value: unknown): VideoEditingConnectionSnapshot {
  if (
    !isExactRecord(value, CONNECTION_KEYS) ||
    !isRegion(value.region) ||
    value.status !== "connected"
  ) {
    throw protocolMismatch();
  }
  return { region: value.region, status: "connected" };
}

function safeError(value: unknown): VideoEditingServiceGatewayError {
  if (value instanceof VideoEditingServiceGatewayError) {
    return value;
  }
  const fields = nativeCommandErrorFields(value);
  if (
    fields !== undefined &&
    NATIVE_ERROR_CODES.has(fields.code as VideoEditingServiceErrorCode)
  ) {
    return new VideoEditingServiceGatewayError(
      fields.code as VideoEditingServiceErrorCode,
      fields.retryable,
    );
  }
  return new VideoEditingServiceGatewayError("operation_unavailable", false);
}

async function invokeSnapshot(command: string, args?: Record<string, unknown>) {
  try {
    return parseSnapshot(await invoke<unknown>(command, args));
  } catch (error) {
    throw safeError(error);
  }
}

export class TauriVideoEditingServiceGateway implements VideoEditingServiceGateway {
  async getSettings(): Promise<VideoEditingServiceSnapshot> {
    return invokeSnapshot("get_video_editing_service_settings");
  }

  async configure(
    input: ConfigureVideoEditingServiceInput,
  ): Promise<VideoEditingServiceSnapshot> {
    return invokeSnapshot("configure_video_editing_service", { request: input });
  }

  async clear(): Promise<VideoEditingServiceSnapshot> {
    return invokeSnapshot("clear_video_editing_service");
  }

  async testConnection(): Promise<VideoEditingConnectionSnapshot> {
    try {
      return parseConnection(await invoke<unknown>("test_video_editing_service_connection"));
    } catch (error) {
      throw safeError(error);
    }
  }
}
