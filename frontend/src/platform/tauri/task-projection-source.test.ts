import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskEvent, TaskSnapshot } from "../../api/control-plane/task-projections";
import { TauriTaskProjectionSource } from "./task-projection-source";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const SNAPSHOT: TaskSnapshot = {
  taskId: TASK_ID,
  status: "running",
  revision: 5,
  lastEventSequence: 2,
  createdAt: "2026-07-18T02:00:00Z",
  updatedAt: "2026-07-18T02:05:00Z",
};
const EVENT: TaskEvent = {
  taskId: TASK_ID,
  sequence: 3,
  eventVersion: "1.0",
  eventType: "task.completed",
  taskRevision: 6,
  taskStatus: "succeeded",
  executionAttemptId: "16fd2706-8baf-433b-82eb-8c7fada847da",
  actionId: null,
  progressPercent: null,
  occurredAt: "2026-07-18T02:06:00Z",
  recordedAt: "2026-07-18T02:06:01Z",
  message: null,
};

const invoke = vi.hoisted(() => vi.fn());
const channels = vi.hoisted(
  () => [] as Array<{ onmessage: ((value: unknown) => void) | undefined }>,
);

vi.mock("@tauri-apps/api/core", () => ({
  invoke,
  Channel: class {
    onmessage: ((value: unknown) => void) | undefined;

    constructor(onmessage?: (value: unknown) => void) {
      this.onmessage = onmessage;
      channels.push(this);
    }
  },
}));

describe("Tauri Task projection source", () => {
  beforeEach(() => {
    invoke.mockReset();
    channels.splice(0);
  });

  it("gets a Task snapshot only through the fixed native command", async () => {
    invoke.mockResolvedValueOnce(SNAPSHOT);
    const source = new TauriTaskProjectionSource();

    await expect(source.getTask(TASK_ID)).resolves.toEqual(SNAPSHOT);
    expect(invoke).toHaveBeenCalledWith("get_task_snapshot", { taskId: TASK_ID });
  });

  it("lists Task snapshots through the fixed native query command", async () => {
    invoke.mockResolvedValueOnce({ items: [SNAPSHOT], nextCursor: null });
    const source = new TauriTaskProjectionSource();

    await expect(source.listTasks({ limit: 20 })).resolves.toEqual({
      items: [SNAPSHOT],
      nextCursor: null,
    });
    expect(invoke).toHaveBeenCalledWith("list_task_snapshots", {
      cursor: null,
      limit: 20,
    });
  });

  it("receives validated events over a Tauri Channel and returns the stream watermark", async () => {
    invoke.mockImplementationOnce(async (_command, args: { onEvent: { onmessage?: (value: unknown) => void } }) => {
      args.onEvent.onmessage?.(EVENT);
      return { lastSequence: 3, terminal: true };
    });
    const source = new TauriTaskProjectionSource();
    const received = vi.fn();

    await expect(source.streamTaskEvents(TASK_ID, 2, received)).resolves.toEqual({
      lastSequence: 3,
      terminal: true,
    });
    expect(received).toHaveBeenCalledWith(EVENT);
    expect(invoke).toHaveBeenCalledWith("stream_task_projection_events", {
      taskId: TASK_ID,
      afterSequence: 2,
      onEvent: channels[0],
    });
  });

  it("fails closed when a channel event is malformed even if native invocation resolves", async () => {
    invoke.mockImplementationOnce(async (_command, args: { onEvent: { onmessage?: (value: unknown) => void } }) => {
      args.onEvent.onmessage?.({ ...EVENT, eventVersion: "private-future" });
      return { lastSequence: 3, terminal: true };
    });
    const source = new TauriTaskProjectionSource();

    const request = source.streamTaskEvents(TASK_ID, 2, vi.fn());
    await expect(request).rejects.toMatchObject({
      code: "protocol_mismatch",
      retryable: false,
    });
    await expect(request).rejects.not.toHaveProperty("cause");
  });

  it("does not invoke native code for an already cancelled request", async () => {
    const controller = new AbortController();
    controller.abort();
    const source = new TauriTaskProjectionSource();

    await expect(
      source.getTask(TASK_ID, { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "request_cancelled", retryable: false });
    expect(invoke).not.toHaveBeenCalled();
  });

  it("maps native details to one safe public failure", async () => {
    invoke.mockRejectedValueOnce({
      code: "private-native-error",
      message: "credential=private-value",
    });
    const source = new TauriTaskProjectionSource();

    const request = source.getTask(TASK_ID);
    await expect(request).rejects.toMatchObject({
      code: "transport_unavailable",
      message: "Task projection service is unavailable",
      retryable: true,
    });
    await expect(request).rejects.not.toHaveProperty("cause");
  });
});
