import { describe, expect, it, vi } from "vitest";

import {
  createDesktopStartupCheck,
  type StartupEnvironmentGateway,
} from "./startup";
import {
  ControlPlaneTransportError,
  type ControlPlaneTransport,
} from "../api/control-plane/transport";

function controlPlane(checkHealth: ControlPlaneTransport["checkHealth"]): ControlPlaneTransport {
  return { checkHealth };
}

function localEnvironment(
  snapshot: Awaited<ReturnType<StartupEnvironmentGateway["checkLocalEnvironment"]>>,
): StartupEnvironmentGateway {
  return { checkLocalEnvironment: vi.fn().mockResolvedValue(snapshot) };
}

describe("desktop startup environment", () => {
  it("requires Control Plane, Executor, trusted browser, and App data to be ready", async () => {
    const transport = controlPlane(
      vi.fn().mockResolvedValue({ status: "available", serviceVersion: "0.1.0" }),
    );
    const environment = localEnvironment({
      appData: "ready",
      executor: "ready",
      trustedBrowser: "ready",
    });

    await expect(createDesktopStartupCheck(transport, environment).check()).resolves.toEqual({
      status: "ready",
      diagnostics: [],
    });
    expect(transport.checkHealth).toHaveBeenCalledOnce();
    expect(environment.checkLocalEnvironment).toHaveBeenCalledOnce();
  });

  it("returns every fixed local remediation without exposing native details", async () => {
    const transport = controlPlane(
      vi.fn().mockRejectedValue(new Error("token=private-control-plane")),
    );
    const environment = localEnvironment({
      appData: "unavailable",
      executor: "configuration_required",
      trustedBrowser: "selection_required",
    });

    const result = await createDesktopStartupCheck(transport, environment).check();

    expect(result).toEqual({
      status: "blocked",
      diagnostics: [
        "control_plane_unavailable",
        "executor_configuration_required",
        "trusted_browser_selection_required",
        "app_data_unavailable",
      ],
    });
    expect(JSON.stringify(result)).not.toContain("private-control-plane");
  });

  it("preserves Installation revocation while still reporting local readiness failures", async () => {
    const transport = controlPlane(
      vi
        .fn()
        .mockRejectedValue(
          new ControlPlaneTransportError("installation_access_denied", false),
        ),
    );
    const environment = localEnvironment({
      appData: "ready",
      executor: "unavailable",
      trustedBrowser: "unavailable",
    });

    await expect(createDesktopStartupCheck(transport, environment).check()).resolves.toEqual({
      status: "blocked",
      diagnostics: [
        "installation_revoked",
        "executor_unavailable",
        "trusted_browser_unavailable",
      ],
    });
  });

  it("fails every local component closed when the native aggregate cannot be read", async () => {
    const transport = controlPlane(
      vi.fn().mockResolvedValue({ status: "available", serviceVersion: "0.1.0" }),
    );
    const environment: StartupEnvironmentGateway = {
      checkLocalEnvironment: vi.fn().mockRejectedValue(new Error("/private/native/path")),
    };

    const result = await createDesktopStartupCheck(transport, environment).check();

    expect(result).toEqual({
      status: "blocked",
      diagnostics: [
        "executor_unavailable",
        "trusted_browser_unavailable",
        "app_data_unavailable",
      ],
    });
    expect(JSON.stringify(result)).not.toContain("/private/native/path");
  });
});
