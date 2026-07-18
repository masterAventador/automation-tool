import {
  ControlPlaneTransportError,
  type ControlPlaneTransport,
} from "../api/control-plane/transport";

export type StartupCheckResult =
  | { status: "ready" }
  | { status: "unavailable" }
  | { status: "revoked" };

export interface StartupCheck {
  check(): Promise<StartupCheckResult>;
}

/**
 * F1-08 keeps this deterministic shell-only check for isolated UI composition tests.
 * Production main.tsx composes createTransportStartupCheck with the allowlisted
 * Tauri/Rust Control Plane transport instead.
 */
export const desktopShellStartupCheck: StartupCheck = {
  async check() {
    return { status: "ready" };
  },
};

export function createTransportStartupCheck(transport: ControlPlaneTransport): StartupCheck {
  return {
    async check() {
      try {
        await transport.checkHealth();
        return { status: "ready" };
      } catch (error) {
        if (
          error instanceof ControlPlaneTransportError &&
          error.code === "installation_access_denied"
        ) {
          return { status: "revoked" };
        }
        return { status: "unavailable" };
      }
    },
  };
}
