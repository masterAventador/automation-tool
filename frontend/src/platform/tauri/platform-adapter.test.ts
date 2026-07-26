import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { PlatformAdapterError } from "../types";
import { TauriPlatformAdapter } from "./platform-adapter";

describe("Tauri PlatformAdapter", () => {
  beforeEach(() => {
    invoke.mockReset();
  });

  it("uses only fixed Executor lifecycle and diagnostics Commands", async () => {
    const stopped = {
      state: "stopped",
      version: null,
      buildId: null,
      restartCount: 0,
    };
    invoke
      .mockResolvedValueOnce(stopped)
      .mockResolvedValueOnce({
        state: "running",
        version: "0.1.0",
        buildId: "demo-build",
        restartCount: 0,
      })
      .mockResolvedValueOnce({ lines: ["safe diagnostic"] })
      .mockResolvedValueOnce({
        fileName: "automation-tool-diagnostics-123e4567-e89b-42d3-a456-426614174000.zip",
        entryCount: 2,
        totalBytes: 512,
      })
      .mockResolvedValueOnce(stopped)
      .mockResolvedValueOnce({ captureSuccessfulRuns: false })
      .mockResolvedValueOnce({ captureSuccessfulRuns: true });
    const adapter = new TauriPlatformAdapter();

    await expect(adapter.getExecutorStatus()).resolves.toEqual(stopped);
    await expect(adapter.restartExecutor()).resolves.toMatchObject({ state: "running" });
    await expect(adapter.getExecutorDiagnostics()).resolves.toEqual(["safe diagnostic"]);
    await expect(adapter.exportDiagnostics()).resolves.toEqual({
      fileName: "automation-tool-diagnostics-123e4567-e89b-42d3-a456-426614174000.zip",
      entryCount: 2,
      totalBytes: 512,
    });
    await expect(adapter.emergencyStopExecutor()).resolves.toEqual(stopped);
    await expect(adapter.getBrowserDiagnosticSettings()).resolves.toEqual({
      captureSuccessfulRuns: false,
    });
    await expect(adapter.setCaptureSuccessfulDiagnostics(true)).resolves.toEqual({
      captureSuccessfulRuns: true,
    });
    expect(invoke.mock.calls).toEqual([
      ["get_executor_status"],
      ["restart_executor"],
      ["get_executor_diagnostics"],
      ["export_diagnostics"],
      ["emergency_stop_executor"],
      ["get_browser_diagnostic_settings"],
      ["set_capture_successful_diagnostics", { enabled: true }],
    ]);
  });

  it("rejects malformed native values and never reflects native errors", async () => {
    const adapter = new TauriPlatformAdapter();
    invoke.mockResolvedValueOnce({
      state: "private-state",
      version: null,
      buildId: null,
      restartCount: 0,
    });
    await expect(adapter.getExecutorStatus()).rejects.toMatchObject({
      code: "protocol_mismatch",
    });

    invoke.mockResolvedValueOnce({ lines: ["x".repeat(4097)] });
    await expect(adapter.getExecutorDiagnostics()).rejects.toMatchObject({
      code: "protocol_mismatch",
    });

    invoke.mockRejectedValueOnce(new Error("password=private-native-secret"));
    const error = await adapter.restartExecutor().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(PlatformAdapterError);
    expect(String(error)).not.toContain("private-native-secret");

    invoke.mockResolvedValueOnce({ captureSuccessfulRuns: 1 });
    await expect(adapter.getBrowserDiagnosticSettings()).rejects.toMatchObject({
      code: "protocol_mismatch",
    });

    invoke.mockResolvedValueOnce({
      fileName: "/private/diagnostics.zip",
      entryCount: 2,
      totalBytes: 512,
    });
    await expect(adapter.exportDiagnostics()).rejects.toMatchObject({
      code: "protocol_mismatch",
    });
  });

  it("keeps the native code when the error carries its readable message", async () => {
    const adapter = new TauriPlatformAdapter();

    invoke.mockRejectedValueOnce({
      code: "installation_access_denied",
      message: "native command error: installation_access_denied",
      retryable: false,
    });
    const error = await adapter.restartExecutor().catch((value: unknown) => value);

    expect(error).toBeInstanceOf(PlatformAdapterError);
    expect(error).toMatchObject({ code: "installation_access_denied", retryable: false });
  });

  it("never reflects a native message, however it was worded", async () => {
    const adapter = new TauriPlatformAdapter();

    invoke.mockRejectedValueOnce({
      code: "storage_unavailable",
      message: "password=private-native-secret",
      retryable: false,
    });
    const error = await adapter.restartExecutor().catch((value: unknown) => value);

    expect(error).toMatchObject({ code: "storage_unavailable" });
    expect(JSON.stringify(error)).not.toContain("private-native-secret");
    expect(String(error)).not.toContain("private-native-secret");
  });
});
