import { invoke } from "@tauri-apps/api/core";

import {
  AppUpdateGatewayError,
  type AppUpdateDecision,
  type AppUpdateGateway,
  type AppUpdateGatewayErrorCode,
  type AppUpdateState,
  parseAppUpdateState,
} from "../../features/app-updates/contracts";

const NATIVE_ERROR_CODES = new Set<AppUpdateGatewayErrorCode>([
  "configuration_unavailable",
  "decision_unavailable",
  "operation_in_progress",
]);

function safeNativeError(value: unknown): AppUpdateGatewayError {
  if (value instanceof AppUpdateGatewayError) return value;
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const code = Reflect.get(value, "code");
    if (typeof code === "string" && NATIVE_ERROR_CODES.has(code as AppUpdateGatewayErrorCode)) {
      return new AppUpdateGatewayError(code as AppUpdateGatewayErrorCode);
    }
  }
  return new AppUpdateGatewayError("operation_unavailable");
}

async function invokeUpdateState(
  command: "get_app_update_state" | "check_app_update_now",
): Promise<AppUpdateState> {
  let value: unknown;
  try {
    value = await invoke<unknown>(command);
  } catch (error) {
    throw safeNativeError(error);
  }
  try {
    return parseAppUpdateState(value);
  } catch {
    throw new AppUpdateGatewayError("protocol_mismatch");
  }
}

export class TauriAppUpdateGateway implements AppUpdateGateway {
  getState(): Promise<AppUpdateState> {
    return invokeUpdateState("get_app_update_state");
  }

  checkNow(): Promise<AppUpdateState> {
    return invokeUpdateState("check_app_update_now");
  }

  async decide(decision: AppUpdateDecision): Promise<AppUpdateState> {
    let value: unknown;
    try {
      value = await invoke<unknown>("decide_app_update", { decision });
    } catch (error) {
      throw safeNativeError(error);
    }
    try {
      return parseAppUpdateState(value);
    } catch {
      throw new AppUpdateGatewayError("protocol_mismatch");
    }
  }
}
