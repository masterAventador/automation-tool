import { beforeEach, describe, expect, it, vi } from "vitest";

import { TauriTaskRunControlGateway } from "./task-run-control-gateway";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const KEY = "task-run:pause:0f8fad5b-d9cb-469f-a165-70867728950e";
const RECEIPT = {
  commandId: "16fd2706-8baf-433b-82eb-8c7fada847da",
  taskId: TASK_ID,
  executionAttemptId: "adff54bd-3571-44da-8acd-5ea15695e5e9",
  sequence: 2,
  commandType: "task.pause",
  status: "pending",
};

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

describe("Tauri Task run control gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses four fixed native commands without accepting a generic operation", async () => {
    const gateway = new TauriTaskRunControlGateway();
    const cases = [
      ["pauseTask", "pause_task_run", "task.pause"],
      ["resumeTask", "resume_task_run", "task.resume"],
      ["cancelTask", "cancel_task_run", "task.cancel"],
      ["emergencyStopTask", "emergency_stop_task_run", "task.emergency_stop"],
    ] as const;

    for (const [method, command, commandType] of cases) {
      invoke.mockResolvedValueOnce({ ...RECEIPT, commandType });
      const key = KEY.replace("pause", method.replace("Task", ""));
      await expect(gateway[method](TASK_ID, key)).resolves.toMatchObject({ commandType });
      expect(invoke).toHaveBeenLastCalledWith(command, { taskId: TASK_ID, idempotencyKey: key });
    }
  });

  it("maps native details to one safe error", async () => {
    invoke.mockRejectedValueOnce({ code: "private", message: "password=secret" });
    const gateway = new TauriTaskRunControlGateway();

    await expect(gateway.pauseTask(TASK_ID, KEY)).rejects.toMatchObject({
      code: "transport_unavailable",
      retryable: true,
    });
  });

  it("rejects cross-task receipts and honours cancellation without leaking native data", async () => {
    const gateway = new TauriTaskRunControlGateway();
    invoke.mockResolvedValueOnce({
      ...RECEIPT,
      taskId: "d9428888-122b-4b3b-a4f8-814f6f5f899a",
    });
    await expect(gateway.pauseTask(TASK_ID, KEY)).rejects.toMatchObject({
      code: "protocol_mismatch",
      retryable: false,
    });

    const controller = new AbortController();
    controller.abort("password=secret");
    await expect(
      gateway.pauseTask(TASK_ID, KEY, { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "request_cancelled", retryable: false });
    expect(invoke).toHaveBeenCalledTimes(1);
  });
});
