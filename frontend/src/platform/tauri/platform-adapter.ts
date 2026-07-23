import { invoke } from "@tauri-apps/api/core";

import {
  PlatformAdapterError,
  type BrowserDiagnosticSettingsSnapshot,
  type DiagnosticExportReceipt,
  type ExecutorManagerState,
  type ExecutorManagerStatus,
  type PlatformAdapter,
  type PlatformAdapterErrorCode,
} from "../types";

const STATUS_KEYS = ["buildId", "restartCount", "state", "version"];
const DIAGNOSTIC_KEYS = ["lines"];
const BROWSER_DIAGNOSTIC_SETTINGS_KEYS = ["captureSuccessfulRuns"];
const DIAGNOSTIC_EXPORT_KEYS = ["entryCount", "fileName", "totalBytes"];
const MAX_DIAGNOSTIC_LINES = 200;
const MAX_DIAGNOSTIC_LINE_BYTES = 4096;
const MAX_RESTART_COUNT = 8;
const MAX_DIAGNOSTIC_EXPORT_BYTES = 12 * 1024 * 1024;
const MAX_DIAGNOSTIC_EXPORT_ENTRIES = 38;
const SAFE_VERSION = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/u;
const SAFE_BUILD_ID = /^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$/u;
const SAFE_DIAGNOSTIC_EXPORT_FILE =
  /^automation-tool-diagnostics-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.zip$/u;
const NATIVE_ERROR_CODES = new Set<PlatformAdapterErrorCode>([
  "already_running",
  "authentication_rejected",
  "browser_discovery_unavailable",
  "browser_unavailable",
  "configuration_invalid",
  "credential_missing",
  "installation_access_denied",
  "operation_unavailable",
  "package_rejected",
  "process_unavailable",
  "storage_unavailable",
  "timed_out",
  "transport_unavailable",
]);

function isExactRecord(value: unknown, expectedKeys: readonly string[]): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const keys = Object.keys(value).sort();
  return keys.length === expectedKeys.length && keys.every((key, index) => key === expectedKeys[index]);
}

function parseExecutorState(value: unknown): ExecutorManagerState | null {
  return value === "running" || value === "restarting" || value === "stopped" ? value : null;
}

function containsUnsafeText(value: string): boolean {
  for (const character of value) {
    const codepoint = character.codePointAt(0);
    if (
      codepoint === undefined ||
      (codepoint <= 0x1f && codepoint !== 0x09) ||
      codepoint === 0x7f ||
      (codepoint >= 0x202a && codepoint <= 0x202e) ||
      (codepoint >= 0x2066 && codepoint <= 0x2069)
    ) {
      return true;
    }
  }
  return false;
}

function parseExecutorStatus(value: unknown): ExecutorManagerStatus {
  if (!isExactRecord(value, STATUS_KEYS)) {
    throw protocolMismatch();
  }
  const state = parseExecutorState(value.state);
  if (
    state === null ||
    !Number.isInteger(value.restartCount) ||
    typeof value.restartCount !== "number" ||
    value.restartCount < 0 ||
    value.restartCount > MAX_RESTART_COUNT
  ) {
    throw protocolMismatch();
  }
  if (state === "stopped") {
    if (value.version !== null || value.buildId !== null) {
      throw protocolMismatch();
    }
  } else if (
    typeof value.version !== "string" ||
    !SAFE_VERSION.test(value.version) ||
    typeof value.buildId !== "string" ||
    !SAFE_BUILD_ID.test(value.buildId)
  ) {
    throw protocolMismatch();
  }
  return {
    state,
    version: value.version as string | null,
    buildId: value.buildId as string | null,
    restartCount: value.restartCount,
  };
}

function parseDiagnostics(value: unknown): readonly string[] {
  if (!isExactRecord(value, DIAGNOSTIC_KEYS) || !Array.isArray(value.lines)) {
    throw protocolMismatch();
  }
  if (value.lines.length > MAX_DIAGNOSTIC_LINES) {
    throw protocolMismatch();
  }
  const encoder = new TextEncoder();
  const lines: string[] = [];
  for (const line of value.lines) {
    if (
      typeof line !== "string" ||
      containsUnsafeText(line) ||
      encoder.encode(line).byteLength > MAX_DIAGNOSTIC_LINE_BYTES
    ) {
      throw protocolMismatch();
    }
    lines.push(line);
  }
  return lines;
}

function parseBrowserDiagnosticSettings(value: unknown): BrowserDiagnosticSettingsSnapshot {
  if (
    !isExactRecord(value, BROWSER_DIAGNOSTIC_SETTINGS_KEYS) ||
    typeof value.captureSuccessfulRuns !== "boolean"
  ) {
    throw protocolMismatch();
  }
  return { captureSuccessfulRuns: value.captureSuccessfulRuns };
}

function parseDiagnosticExportReceipt(value: unknown): DiagnosticExportReceipt {
  if (
    !isExactRecord(value, DIAGNOSTIC_EXPORT_KEYS) ||
    typeof value.fileName !== "string" ||
    !SAFE_DIAGNOSTIC_EXPORT_FILE.test(value.fileName) ||
    typeof value.entryCount !== "number" ||
    !Number.isInteger(value.entryCount) ||
    value.entryCount < 2 ||
    value.entryCount > MAX_DIAGNOSTIC_EXPORT_ENTRIES ||
    typeof value.totalBytes !== "number" ||
    !Number.isSafeInteger(value.totalBytes) ||
    value.totalBytes < 1 ||
    value.totalBytes > MAX_DIAGNOSTIC_EXPORT_BYTES
  ) {
    throw protocolMismatch();
  }
  return {
    fileName: value.fileName,
    entryCount: value.entryCount,
    totalBytes: value.totalBytes,
  };
}

function protocolMismatch(): PlatformAdapterError {
  return new PlatformAdapterError("protocol_mismatch", false);
}

export function safeNativeError(value: unknown): PlatformAdapterError {
  if (value instanceof PlatformAdapterError) {
    return value;
  }
  if (
    isExactRecord(value, ["code", "retryable"]) &&
    typeof value.code === "string" &&
    NATIVE_ERROR_CODES.has(value.code as PlatformAdapterErrorCode) &&
    typeof value.retryable === "boolean"
  ) {
    return new PlatformAdapterError(value.code as PlatformAdapterErrorCode, value.retryable);
  }
  return new PlatformAdapterError("operation_unavailable", false);
}

async function invokeStatus(command: "get_executor_status" | "restart_executor" | "emergency_stop_executor") {
  try {
    return parseExecutorStatus(await invoke<unknown>(command));
  } catch (error) {
    throw safeNativeError(error);
  }
}

export class TauriPlatformAdapter implements PlatformAdapter {
  getExecutorStatus(): Promise<ExecutorManagerStatus> {
    return invokeStatus("get_executor_status");
  }

  restartExecutor(): Promise<ExecutorManagerStatus> {
    return invokeStatus("restart_executor");
  }

  async getExecutorDiagnostics(): Promise<readonly string[]> {
    try {
      return parseDiagnostics(await invoke<unknown>("get_executor_diagnostics"));
    } catch (error) {
      throw safeNativeError(error);
    }
  }

  async exportDiagnostics(): Promise<DiagnosticExportReceipt> {
    try {
      return parseDiagnosticExportReceipt(await invoke<unknown>("export_diagnostics"));
    } catch (error) {
      throw safeNativeError(error);
    }
  }

  emergencyStopExecutor(): Promise<ExecutorManagerStatus> {
    return invokeStatus("emergency_stop_executor");
  }

  async getBrowserDiagnosticSettings(): Promise<BrowserDiagnosticSettingsSnapshot> {
    try {
      return parseBrowserDiagnosticSettings(await invoke<unknown>("get_browser_diagnostic_settings"));
    } catch (error) {
      throw safeNativeError(error);
    }
  }

  async setCaptureSuccessfulDiagnostics(
    enabled: boolean,
  ): Promise<BrowserDiagnosticSettingsSnapshot> {
    try {
      return parseBrowserDiagnosticSettings(
        await invoke<unknown>("set_capture_successful_diagnostics", { enabled }),
      );
    } catch (error) {
      throw safeNativeError(error);
    }
  }
}
