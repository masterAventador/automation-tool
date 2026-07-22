import {
  ControlPlaneTransportError,
  type ControlPlaneTransport,
} from "../api/control-plane/transport";
import type {
  LocalStartupEnvironmentSnapshot,
  StartupEnvironmentGateway,
} from "../platform/types";

export type {
  LocalStartupEnvironmentSnapshot,
  StartupEnvironmentGateway,
} from "../platform/types";

export type StartupCheckResult =
  | { status: "ready" }
  | { status: "unavailable" }
  | { status: "revoked" }
  | {
      status: "ready" | "blocked";
      diagnostics: readonly StartupDiagnosticCode[];
    };

export type StartupDiagnosticCode =
  | "installation_revoked"
  | "control_plane_unavailable"
  | "executor_configuration_required"
  | "executor_unavailable"
  | "trusted_browser_selection_required"
  | "trusted_browser_unavailable"
  | "app_data_unavailable";

export interface StartupCheck {
  check(): Promise<StartupCheckResult>;
}

/**
 * F1-08 keeps this deterministic shell-only check for isolated UI composition tests.
 * Production main.tsx composes createDesktopStartupCheck with the allowlisted
 * Tauri/Rust Control Plane transport and path-free local environment gateway instead.
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

export function createDesktopStartupCheck(
  transport: ControlPlaneTransport,
  environment: StartupEnvironmentGateway,
): StartupCheck {
  return {
    async check() {
      const [controlPlane, local] = await Promise.allSettled([
        transport.checkHealth(),
        environment.checkLocalEnvironment(),
      ]);
      const diagnostics: StartupDiagnosticCode[] = [];

      if (controlPlane.status === "rejected") {
        diagnostics.push(
          controlPlane.reason instanceof ControlPlaneTransportError &&
            controlPlane.reason.code === "installation_access_denied"
            ? "installation_revoked"
            : "control_plane_unavailable",
        );
      }

      if (local.status === "fulfilled" && isLocalEnvironment(local.value)) {
        if (local.value.executor === "configuration_required") {
          diagnostics.push("executor_configuration_required");
        } else if (local.value.executor === "unavailable") {
          diagnostics.push("executor_unavailable");
        }
        if (local.value.trustedBrowser === "selection_required") {
          diagnostics.push("trusted_browser_selection_required");
        } else if (local.value.trustedBrowser === "unavailable") {
          diagnostics.push("trusted_browser_unavailable");
        }
        if (local.value.appData === "unavailable") {
          diagnostics.push("app_data_unavailable");
        }
      } else {
        diagnostics.push(
          "executor_unavailable",
          "trusted_browser_unavailable",
          "app_data_unavailable",
        );
      }

      return diagnostics.length === 0
        ? { status: "ready", diagnostics }
        : { status: "blocked", diagnostics };
    },
  };
}

function isLocalEnvironment(value: unknown): value is LocalStartupEnvironmentSnapshot {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return (
    keys.length === 3 &&
    keys[0] === "appData" &&
    keys[1] === "executor" &&
    keys[2] === "trustedBrowser" &&
    (record.appData === "ready" || record.appData === "unavailable") &&
    (record.executor === "ready" ||
      record.executor === "configuration_required" ||
      record.executor === "unavailable") &&
    (record.trustedBrowser === "ready" ||
      record.trustedBrowser === "selection_required" ||
      record.trustedBrowser === "unavailable")
  );
}
