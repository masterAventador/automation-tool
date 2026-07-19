import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { PlatformAdapterError } from "../types";
import { TauriPlatformAdapter } from "./platform-adapter";

describe("Tauri PlatformAdapter", () => {
  beforeEach(() => {
    invoke.mockReset();
  });

  it("uses only four fixed Executor lifecycle Commands", async () => {
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
      .mockResolvedValueOnce(stopped);
    const adapter = new TauriPlatformAdapter();

    await expect(adapter.getExecutorStatus()).resolves.toEqual(stopped);
    await expect(adapter.restartExecutor()).resolves.toMatchObject({ state: "running" });
    await expect(adapter.getExecutorDiagnostics()).resolves.toEqual(["safe diagnostic"]);
    await expect(adapter.emergencyStopExecutor()).resolves.toEqual(stopped);
    expect(invoke.mock.calls).toEqual([
      ["get_executor_status"],
      ["restart_executor"],
      ["get_executor_diagnostics"],
      ["emergency_stop_executor"],
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
  });
});
