import { invoke } from "@tauri-apps/api/core";

import {
  AppUpdateGatewayError,
  parseAppUpdateState,
  type AppUpdateDecision,
  type AppUpdateGateway,
  type AppUpdateState,
} from "../../features/app-updates/contracts";

const COMMANDS = {
  getState: "get_app_update_state",
  checkNow: "check_app_update_now",
  decide: "decide_app_update",
} as const;

async function invokeState(
  command: (typeof COMMANDS)[keyof typeof COMMANDS],
  args: Record<string, never> | { readonly decision: AppUpdateDecision },
): Promise<AppUpdateState> {
  let value: unknown;
  try {
    value = await invoke<unknown>(command, args);
  } catch {
    throw new AppUpdateGatewayError("transport_unavailable", true);
  }
  try {
    return parseAppUpdateState(value);
  } catch {
    throw new AppUpdateGatewayError("protocol_mismatch", false);
  }
}

export class TauriAppUpdateGateway implements AppUpdateGateway {
  getState(): Promise<AppUpdateState> {
    return invokeState(COMMANDS.getState, {});
  }

  checkNow(): Promise<AppUpdateState> {
    return invokeState(COMMANDS.checkNow, {});
  }

  decide(decision: AppUpdateDecision): Promise<AppUpdateState> {
    return invokeState(COMMANDS.decide, { decision });
  }
}
