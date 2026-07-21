import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { TauriTaskDiscoveryGateway } from "./task-discovery-gateway";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const KEY = "task:discover:start:16fd2706-8baf-433b-82eb-8c7fada847da";
const RECEIPT = {
  taskId: TASK_ID,
  taskStatus: "discovering_targets",
  taskRevision: 2,
  lastEventSequence: 1,
  commandId: "16fd2706-8baf-433b-82eb-8c7fada847da",
  executionAttemptId: "adff54bd-3571-44da-8acd-5ea15695e5e9",
  commandStatus: "pending",
};

describe("Tauri Task discovery gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("invokes only the fixed discovery Command with validated input", async () => {
    invoke.mockResolvedValueOnce(RECEIPT);
    const gateway = new TauriTaskDiscoveryGateway();

    await expect(gateway.startDiscovery(TASK_ID, KEY)).resolves.toEqual(RECEIPT);
    expect(invoke).toHaveBeenCalledWith("start_task_discovery", {
      taskId: TASK_ID,
      idempotencyKey: KEY,
    });
  });

  it("maps native details and cancellation to safe closed errors", async () => {
    const gateway = new TauriTaskDiscoveryGateway();
    invoke.mockRejectedValueOnce({ code: "private", message: "password=secret" });
    await expect(gateway.startDiscovery(TASK_ID, KEY)).rejects.toMatchObject({
      code: "transport_unavailable",
      retryable: true,
    });

    const controller = new AbortController();
    controller.abort("private reason");
    await expect(
      gateway.startDiscovery(TASK_ID, KEY, { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "request_cancelled", retryable: false });
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it("rejects a cross-Task receipt", async () => {
    invoke.mockResolvedValueOnce({ ...RECEIPT, taskId: "d9428888-122b-4b3b-a4f8-814f6f5f899a" });
    const gateway = new TauriTaskDiscoveryGateway();
    await expect(gateway.startDiscovery(TASK_ID, KEY)).rejects.toMatchObject({
      code: "protocol_mismatch",
      retryable: false,
    });
  });
});
