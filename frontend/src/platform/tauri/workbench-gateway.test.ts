import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import {
  WorkbenchGatewayError,
  workbenchMetricsQueryOptions,
  workbenchRuntimeStatusQueryOptions,
} from "../../features/workbench/workbench-gateway";
import { TauriWorkbenchGateway } from "./workbench-gateway";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const IDEMPOTENCY_KEY =
  "workbench:emergency-stop:16fd2706-8baf-433b-82eb-8c7fada847da";

describe("Tauri workbench gateway", () => {
  beforeEach(() => {
    invoke.mockReset();
  });

  it("keeps runtime presence polling active while the desktop window is hidden", () => {
    const options = workbenchRuntimeStatusQueryOptions(new TauriWorkbenchGateway());

    expect(options.refetchInterval).toBe(1_000);
    expect(options.refetchIntervalInBackground).toBe(true);
    const metrics = workbenchMetricsQueryOptions(new TauriWorkbenchGateway());
    expect(metrics.refetchInterval).toBe(10_000);
    expect(metrics.refetchIntervalInBackground).toBe(true);
  });

  it("uses only fixed runtime status, metrics, and emergency-stop Commands", async () => {
    invoke
      .mockResolvedValueOnce({
        controlPlaneStatus: "ready",
        executorStatus: "offline",
        executorLastHeartbeatAt: null,
      })
      .mockResolvedValueOnce({
        version: "workbench.metrics.v1",
        tasks: {
          total: 9,
          succeeded: 3,
          failed: 2,
          handoffRequired: 1,
          outcomeUncertain: 1,
        },
        actions: { total: 12, succeeded: 7, failed: 2, outcomeUncertain: 1 },
      })
      .mockResolvedValueOnce({
        commandId: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        taskId: TASK_ID,
        executionAttemptId: "adff54bd-3571-44da-8acd-5ea15695e5e9",
        sequence: 2,
        commandType: "task.emergency_stop",
        status: "pending",
      });
    const gateway = new TauriWorkbenchGateway();

    await expect(gateway.getRuntimeStatus()).resolves.toMatchObject({
      controlPlaneStatus: "ready",
      executorStatus: "offline",
    });
    await expect(gateway.getMetrics()).resolves.toMatchObject({
      version: "workbench.metrics.v1",
      tasks: { total: 9 },
      actions: { total: 12 },
    });
    await expect(
      gateway.emergencyStopTask(TASK_ID, IDEMPOTENCY_KEY),
    ).resolves.toMatchObject({
      taskId: TASK_ID,
      commandType: "task.emergency_stop",
    });
    expect(invoke.mock.calls).toEqual([
      ["get_workbench_status", {}],
      ["get_workbench_metrics", {}],
      ["emergency_stop_workbench_task", { taskId: TASK_ID, idempotencyKey: IDEMPOTENCY_KEY }],
    ]);
  });

  it("rejects malformed native values and maps errors without reflecting secrets", async () => {
    const gateway = new TauriWorkbenchGateway();
    invoke.mockResolvedValueOnce({
      controlPlaneStatus: "ready",
      executorStatus: "private-status",
      executorLastHeartbeatAt: null,
    });
    await expect(gateway.getRuntimeStatus()).rejects.toMatchObject({
      code: "protocol_mismatch",
      message: "Workbench protocol is incompatible",
    });

    invoke.mockResolvedValueOnce({
      version: "workbench.metrics.v1",
      tasks: {
        total: 1,
        succeeded: 1,
        failed: 1,
        handoffRequired: 0,
        outcomeUncertain: 0,
      },
      actions: { total: 0, succeeded: 0, failed: 0, outcomeUncertain: 0 },
    });
    await expect(gateway.getMetrics()).rejects.toMatchObject({ code: "protocol_mismatch" });

    invoke.mockResolvedValueOnce({
      controlPlaneStatus: "ready",
      executorStatus: "online",
      executorLastHeartbeatAt: "2026-02-30T12:00:00Z",
    });
    await expect(gateway.getRuntimeStatus()).rejects.toMatchObject({
      code: "protocol_mismatch",
      message: "Workbench protocol is incompatible",
    });

    invoke.mockRejectedValueOnce(new Error("password=private-native-secret"));
    const error = await gateway.getRuntimeStatus().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(WorkbenchGatewayError);
    expect(error).toMatchObject({
      code: "transport_unavailable",
      message: "Workbench service is unavailable",
    });
    expect(String(error)).not.toContain("private-native-secret");
  });
});
