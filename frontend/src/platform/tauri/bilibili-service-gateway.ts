import { invoke } from "@tauri-apps/api/core";

import {
  BilibiliServiceGatewayError,
  type BilibiliServiceErrorCode,
  type BilibiliServiceGateway,
  type BilibiliServiceSnapshot,
  type ConfigureBilibiliServiceInput,
} from "../../features/settings/bilibili-service-gateway";
import { nativeCommandErrorFields } from "./native-command-error";

const SNAPSHOT_KEYS = [
  "configured",
  "noReprint",
  "provider",
  "providerLabel",
  "tag",
  "targetAccount",
  "tid",
] as const;
const NATIVE_ERROR_CODES = new Set<BilibiliServiceErrorCode>([
  "configuration_invalid",
  "configuration_required",
  "storage_unavailable",
]);

function protocolMismatch(): BilibiliServiceGatewayError {
  return new BilibiliServiceGatewayError("protocol_mismatch", false);
}

function isExactRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const keys = Object.keys(value).sort();
  const expected = [...SNAPSHOT_KEYS].sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

function parseSnapshot(value: unknown): BilibiliServiceSnapshot {
  if (
    !isExactRecord(value) ||
    value.provider !== "bilibili" ||
    value.providerLabel !== "B站开放平台" ||
    typeof value.configured !== "boolean"
  ) {
    throw protocolMismatch();
  }
  const configuredFieldsAreValid =
    typeof value.targetAccount === "string" &&
    value.targetAccount.length > 0 &&
    Number.isSafeInteger(value.tid) &&
    Number(value.tid) > 0 &&
    typeof value.tag === "string" &&
    value.tag.length > 0 &&
    (value.noReprint === 0 || value.noReprint === 1);
  const emptyFieldsAreValid =
    value.targetAccount === null &&
    value.tid === null &&
    value.tag === null &&
    value.noReprint === null;
  if ((value.configured && !configuredFieldsAreValid) || (!value.configured && !emptyFieldsAreValid)) {
    throw protocolMismatch();
  }
  return {
    provider: "bilibili",
    providerLabel: "B站开放平台",
    configured: value.configured,
    targetAccount: value.targetAccount as string | null,
    tid: value.tid as number | null,
    tag: value.tag as string | null,
    noReprint: value.noReprint as 0 | 1 | null,
  };
}

function safeError(value: unknown): BilibiliServiceGatewayError {
  if (value instanceof BilibiliServiceGatewayError) return value;
  const fields = nativeCommandErrorFields(value);
  if (fields !== undefined && NATIVE_ERROR_CODES.has(fields.code as BilibiliServiceErrorCode)) {
    return new BilibiliServiceGatewayError(
      fields.code as BilibiliServiceErrorCode,
      fields.retryable,
    );
  }
  return new BilibiliServiceGatewayError("operation_unavailable", false);
}

async function invokeSnapshot(
  command: string,
  args?: Record<string, unknown>,
): Promise<BilibiliServiceSnapshot> {
  try {
    return parseSnapshot(await invoke<unknown>(command, args));
  } catch (error) {
    throw safeError(error);
  }
}

export class TauriBilibiliServiceGateway implements BilibiliServiceGateway {
  async getSettings(): Promise<BilibiliServiceSnapshot> {
    return invokeSnapshot("get_bilibili_service_settings");
  }

  async configure(input: ConfigureBilibiliServiceInput): Promise<BilibiliServiceSnapshot> {
    return invokeSnapshot("configure_bilibili_service", { request: input });
  }

  async clear(): Promise<BilibiliServiceSnapshot> {
    return invokeSnapshot("clear_bilibili_service");
  }
}
