import type { ControlPlaneTransport } from "../api/control-plane/transport";

export type StartupCheckResult = { status: "ready" } | { status: "unavailable" };

export interface StartupCheck {
  check(): Promise<StartupCheckResult>;
}

/**
 * F1-08 proves the desktop shell states without bypassing the Rust network boundary.
 * F1-10 adds the transport adapter below; I2-09 replaces this shell-only default
 * when the allowlisted Rust network bridge is available.
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
      } catch {
        return { status: "unavailable" };
      }
    },
  };
}
