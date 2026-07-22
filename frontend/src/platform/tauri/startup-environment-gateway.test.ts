import { beforeEach, describe, expect, it, vi } from "vitest";

import { TauriStartupEnvironmentGateway } from "./startup-environment-gateway";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

describe("Tauri startup environment gateway", () => {
  beforeEach(() => {
    invoke.mockReset();
  });

  it("invokes one fixed path-free aggregate command and parses its exact snapshot", async () => {
    invoke.mockResolvedValueOnce({
      appData: "ready",
      executor: "configuration_required",
      trustedBrowser: "selection_required",
    });

    const result = await new TauriStartupEnvironmentGateway().checkLocalEnvironment();

    expect(result).toEqual({
      appData: "ready",
      executor: "configuration_required",
      trustedBrowser: "selection_required",
    });
    expect(invoke).toHaveBeenCalledOnce();
    expect(invoke).toHaveBeenCalledWith("check_local_startup_environment");
  });

  it.each([
    null,
    {},
    { appData: "ready", executor: "ready", trustedBrowser: "ready", path: "/private" },
    { appData: "private", executor: "ready", trustedBrowser: "ready" },
    { appData: "ready", executor: "running", trustedBrowser: "ready" },
    { appData: "ready", executor: "ready", trustedBrowser: "chrome" },
  ])("rejects malformed or expanded native snapshots", async (snapshot) => {
    invoke.mockResolvedValueOnce(snapshot);

    const request = new TauriStartupEnvironmentGateway().checkLocalEnvironment();

    await expect(request).rejects.toMatchObject({
      name: "PlatformAdapterError",
      code: "protocol_mismatch",
      retryable: false,
    });
  });

  it("maps native failures without reflecting paths or credentials", async () => {
    invoke.mockRejectedValueOnce(new Error("token=private /Users/private/app-data"));

    const request = new TauriStartupEnvironmentGateway().checkLocalEnvironment();

    await expect(request).rejects.toMatchObject({
      name: "PlatformAdapterError",
      code: "operation_unavailable",
      retryable: false,
    });
    await expect(request).rejects.not.toHaveProperty("cause");
  });
});
