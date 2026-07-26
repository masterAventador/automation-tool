import { invoke } from "@tauri-apps/api/core";

import {
  PLATFORM_SESSION_NATIVE_ERROR_CODES,
  PlatformSessionGatewayError,
  parsePlatformSessionAction,
  parsePlatformSessionSnapshot,
  type PlatformSessionAction,
  type PlatformSessionGateway,
  type PlatformSessionGatewayErrorCode,
  type PlatformSessionSnapshot,
} from "../../features/platform-sessions/platform-session-gateway";
import { nativeCommandErrorFields } from "./native-command-error";

/**
 * T109: carry the bridge's own reason across, the way the other gateways do.
 *
 * The wire has always been `{code, message, retryable}`; this gateway used to
 * discard all three and substitute one retryable transport failure. That turned
 * "the packaged browser is missing" — which no amount of retrying fixes — into
 * "please retry", and left the page unable to say anything true about a failure.
 * `message` is deliberately not carried: it is native diagnostic text, and the
 * page maps the closed-set `code` to its own copy.
 */
export function mapPlatformSessionNativeError(error: unknown): PlatformSessionGatewayError {
  if (error instanceof PlatformSessionGatewayError) return error;
  const fields = nativeCommandErrorFields(error);
  if (fields !== undefined && PLATFORM_SESSION_NATIVE_ERROR_CODES.has(fields.code)) {
    return new PlatformSessionGatewayError(
      fields.code as PlatformSessionGatewayErrorCode,
      fields.retryable,
    );
  }
  return new PlatformSessionGatewayError("transport_unavailable", true);
}

async function safeInvoke(command: string): Promise<unknown> {
  try {
    return await invoke<unknown>(command, {});
  } catch (error) {
    throw mapPlatformSessionNativeError(error);
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

  async logoutDouyinSession(): Promise<PlatformSessionSnapshot> {
    return parsePlatformSessionSnapshot(await safeInvoke("logout_douyin_session"));
  }
}
