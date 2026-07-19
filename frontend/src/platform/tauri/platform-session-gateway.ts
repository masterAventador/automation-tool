import { invoke } from "@tauri-apps/api/core";

import {
  PlatformSessionGatewayError,
  parsePlatformSessionAction,
  parsePlatformSessionSnapshot,
  type PlatformSessionAction,
  type PlatformSessionGateway,
  type PlatformSessionSnapshot,
} from "../../features/platform-sessions/platform-session-gateway";

async function safeInvoke(command: string): Promise<unknown> {
  try {
    return await invoke<unknown>(command, {});
  } catch (error) {
    if (error instanceof PlatformSessionGatewayError) throw error;
    throw new PlatformSessionGatewayError("transport_unavailable", true);
  }
}

export class TauriPlatformSessionGateway implements PlatformSessionGateway {
  async getDouyinSession(): Promise<PlatformSessionSnapshot> {
    return parsePlatformSessionSnapshot(await safeInvoke("get_douyin_platform_session"));
  }

  async openDouyinLogin(): Promise<PlatformSessionAction> {
    return parsePlatformSessionAction(await safeInvoke("open_douyin_login"));
  }

  async recheckDouyinLogin(): Promise<PlatformSessionAction> {
    return parsePlatformSessionAction(await safeInvoke("recheck_douyin_login"));
  }
}
