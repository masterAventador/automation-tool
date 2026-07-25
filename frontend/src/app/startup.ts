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
  | "installation_conflict"
  | "control_plane_unavailable"
  | "executor_configuration_required"
  | "executor_unavailable"
  | "browser_component_missing"
  | "browser_component_damaged"
  | "browser_component_version_incompatible"
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

/**
 * Only the two categories the native health command reports on purpose become
 * their own diagnostic. Anything else is an outage, so no native cause can
 * reach a rendered message.
 */
function controlPlaneDiagnostic(reason: unknown): StartupDiagnosticCode {
  if (!(reason instanceof ControlPlaneTransportError)) {
    return "control_plane_unavailable";
  }
  switch (reason.code) {
    case "installation_access_denied":
      return "installation_revoked";
    case "installation_conflict":
      return "installation_conflict";
    default:
      return "control_plane_unavailable";
  }
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
        diagnostics.push(controlPlaneDiagnostic(controlPlane.reason));
      }

      if (local.status === "fulfilled" && isLocalEnvironment(local.value)) {
        if (local.value.executor === "configuration_required") {
          diagnostics.push("executor_configuration_required");
        } else if (local.value.executor === "unavailable") {
          diagnostics.push("executor_unavailable");
        }
        if (local.value.embeddedBrowser === "component_missing") {
          diagnostics.push("browser_component_missing");
        } else if (local.value.embeddedBrowser === "component_damaged") {
          diagnostics.push("browser_component_damaged");
        } else if (local.value.embeddedBrowser === "version_incompatible") {
          diagnostics.push("browser_component_version_incompatible");
        }
        if (local.value.appData === "unavailable") {
          diagnostics.push("app_data_unavailable");
        }
      } else {
        diagnostics.push(
          "executor_unavailable",
          "browser_component_missing",
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
    keys[1] === "embeddedBrowser" &&
    keys[2] === "executor" &&
    (record.appData === "ready" || record.appData === "unavailable") &&
    (record.executor === "ready" ||
      record.executor === "configuration_required" ||
      record.executor === "unavailable") &&
    (record.embeddedBrowser === "ready" ||
      record.embeddedBrowser === "component_missing" ||
      record.embeddedBrowser === "component_damaged" ||
      record.embeddedBrowser === "version_incompatible")
  );
}
