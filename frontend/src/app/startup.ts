export type StartupCheckResult = { status: "ready" } | { status: "unavailable" };

export interface StartupCheck {
  check(): Promise<StartupCheckResult>;
}

/**
 * F1-08 proves the desktop shell states without bypassing the Rust network boundary.
 * F1-10 replaces this composition default with the real Tauri transport check.
 */
export const desktopShellStartupCheck: StartupCheck = {
  async check() {
    return { status: "ready" };
  },
};
