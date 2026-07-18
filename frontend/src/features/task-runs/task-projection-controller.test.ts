import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type {
  TaskEvent,
  TaskProjectionSource,
  TaskSnapshot,
} from "../../api/control-plane/task-projections";
import { TaskProjectionSourceError } from "../../api/control-plane/task-projections";
import { followTaskProjection } from "./task-projection-controller";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

function snapshot(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    taskId: TASK_ID,
    status: "running",
    revision: 5,
    lastEventSequence: 2,
    createdAt: "2026-07-18T02:00:00Z",
    updatedAt: "2026-07-18T02:05:00Z",
    ...overrides,
  };
}

function event(overrides: Partial<TaskEvent> = {}): TaskEvent {
  return {
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
    ...overrides,
  };
}

function queryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("Task projection controller", () => {
  it("always reads the authoritative snapshot before opening the event channel", async () => {
    const calls: string[] = [];
    const source: TaskProjectionSource = {
      getTask: vi.fn(async () => {
        calls.push("snapshot");
        return snapshot();
      }),
      listTasks: vi.fn(),
      streamTaskEvents: vi.fn(async (_taskId, afterSequence, onEvent) => {
        calls.push(`stream:${afterSequence}`);
        onEvent(event());
        return { lastSequence: 3, terminal: true };
      }),
    };

    const result = await followTaskProjection({
      queryClient: queryClient(),
      source,
      taskId: TASK_ID,
    });

    expect(calls).toEqual(["snapshot", "stream:2"]);
    expect(result).toMatchObject({
      phase: "terminal",
      snapshot: { status: "succeeded", revision: 6, lastEventSequence: 3 },
    });
  });

  it("reloads a snapshot before resubscribing after an event gap", async () => {
    const calls: string[] = [];
    let snapshotCall = 0;
    let streamCall = 0;
    const source: TaskProjectionSource = {
      getTask: vi.fn(async () => {
        calls.push("snapshot");
        snapshotCall += 1;
        return snapshotCall === 1
          ? snapshot()
          : snapshot({ revision: 7, lastEventSequence: 4 });
      }),
      listTasks: vi.fn(),
      streamTaskEvents: vi.fn(async (_taskId, afterSequence, onEvent) => {
        calls.push(`stream:${afterSequence}`);
        streamCall += 1;
        if (streamCall === 1) {
          onEvent(event({ sequence: 4, taskRevision: 7 }));
          return { lastSequence: 4, terminal: false };
        }
        onEvent(event({ sequence: 5, taskRevision: 8 }));
        return { lastSequence: 5, terminal: true };
      }),
    };

    const result = await followTaskProjection({
      queryClient: queryClient(),
      source,
      taskId: TASK_ID,
    });

    expect(calls).toEqual(["snapshot", "stream:2", "snapshot", "stream:4"]);
    expect(result).toMatchObject({
      phase: "terminal",
      snapshot: { revision: 8, lastEventSequence: 5 },
    });
  });

  it("bounds repeated incompatible recovery and never invents a state", async () => {
    const source: TaskProjectionSource = {
      getTask: vi.fn().mockResolvedValue(snapshot()),
      listTasks: vi.fn(),
      streamTaskEvents: vi.fn().mockRejectedValue(
        new TaskProjectionSourceError("protocol_mismatch", false),
      ),
    };

    const result = await followTaskProjection({
      queryClient: queryClient(),
      source,
      taskId: TASK_ID,
      maxRecoveries: 1,
    });

    expect(source.getTask).toHaveBeenCalledTimes(2);
    expect(source.streamTaskEvents).toHaveBeenCalledTimes(2);
    expect(result).toMatchObject({
      phase: "degraded",
      snapshot: { status: "running", revision: 5, lastEventSequence: 2 },
    });
  });

  it("resets the recovery budget after a healthy stream rotation", async () => {
    let streamCall = 0;
    const source: TaskProjectionSource = {
      getTask: vi.fn().mockResolvedValue(snapshot()),
      listTasks: vi.fn(),
      streamTaskEvents: vi.fn(async (_taskId, _afterSequence, onEvent) => {
        streamCall += 1;
        if (streamCall === 1 || streamCall === 3) {
          throw new TaskProjectionSourceError("protocol_mismatch", false);
        }
        if (streamCall === 2) {
          return { lastSequence: 2, terminal: false };
        }
        onEvent(event());
        return { lastSequence: 3, terminal: true };
      }),
    };

    const result = await followTaskProjection({
      queryClient: queryClient(),
      source,
      taskId: TASK_ID,
      maxRecoveries: 1,
    });

    expect(source.streamTaskEvents).toHaveBeenCalledTimes(4);
    expect(result).toMatchObject({
      phase: "terminal",
      snapshot: { status: "succeeded", revision: 6, lastEventSequence: 3 },
    });
  });
});
