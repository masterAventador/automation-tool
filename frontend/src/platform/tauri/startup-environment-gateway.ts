import { invoke } from "@tauri-apps/api/core";

import {
  PlatformAdapterError,
  type LocalStartupEnvironmentSnapshot,
  type StartupEnvironmentGateway,
} from "../types";
import { safeNativeError } from "./platform-adapter";

const SNAPSHOT_KEYS = ["appData", "executor", "trustedBrowser"];

function parseSnapshot(value: unknown): LocalStartupEnvironmentSnapshot {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new PlatformAdapterError("protocol_mismatch", false);
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  if (
    keys.length !== SNAPSHOT_KEYS.length ||
    !keys.every((key, index) => key === SNAPSHOT_KEYS[index]) ||
    (record.appData !== "ready" && record.appData !== "unavailable") ||
    (record.executor !== "ready" &&
      record.executor !== "configuration_required" &&
      record.executor !== "unavailable") ||
    (record.trustedBrowser !== "ready" &&
      record.trustedBrowser !== "selection_required" &&
      record.trustedBrowser !== "unavailable")
  ) {
    throw new PlatformAdapterError("protocol_mismatch", false);
  }
  return {
    appData: record.appData,
    executor: record.executor,
    trustedBrowser: record.trustedBrowser,
  };
}

export class TauriStartupEnvironmentGateway implements StartupEnvironmentGateway {
  async checkLocalEnvironment(): Promise<LocalStartupEnvironmentSnapshot> {
    try {
      return parseSnapshot(await invoke<unknown>("check_local_startup_environment"));
    } catch (error) {
      throw safeNativeError(error);
    }
  }
}
