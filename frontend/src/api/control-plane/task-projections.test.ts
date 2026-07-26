import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import {
  TaskProjectionSourceError,
  parseTaskEvent,
  parseTaskSnapshot,
  taskListQueryOptions,
  taskProjectionKeys,
  taskSnapshotQueryOptions,
  type TaskProjectionSource,
  type TaskSnapshot,
} from "./task-projections";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const SNAPSHOT: TaskSnapshot = {
  taskId: TASK_ID,
  status: "running",
  revision: 5,
  lastEventSequence: 2,
  createdAt: "2026-07-18T02:00:00Z",
  updatedAt: "2026-07-18T02:05:00Z",
};

describe("Task projection query boundary", () => {
  it("uses stable Installation-scoped task query keys without secrets", () => {
    expect(taskProjectionKeys.all).toEqual(["tasks"]);
    expect(taskProjectionKeys.lists()).toEqual(["tasks", "list"]);
    expect(taskProjectionKeys.detail(TASK_ID)).toEqual(["tasks", "detail", TASK_ID]);
  });

  it("loads detail through the source with the Query cancellation signal", async () => {
    const getTask = vi.fn().mockResolvedValue(SNAPSHOT);
    const source = { getTask } as unknown as TaskProjectionSource;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    await expect(client.fetchQuery(taskSnapshotQueryOptions(source, TASK_ID))).resolves.toEqual(
      SNAPSHOT,
    );
    expect(getTask).toHaveBeenCalledOnce();
    expect(getTask.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
  });

  it("keeps polling the authoritative snapshot so an offline emergency stop reconciles", () => {
    const source = {} as TaskProjectionSource;
    const options = taskSnapshotQueryOptions(source, TASK_ID);

    expect(options.refetchInterval).toBe(1_000);
    expect(options.refetchIntervalInBackground).toBe(true);
  });

  it("keeps polling the Task list so a Task nobody is following stops going stale", () => {
    const source = {} as TaskProjectionSource;
    const options = taskListQueryOptions(source, null, 20);

    expect(options.refetchInterval).toBe(1_000);
    expect(options.refetchIntervalInBackground).toBe(true);
  });

  it("accepts only an exact public snapshot with a safe integer watermark", () => {
    expect(parseTaskSnapshot(SNAPSHOT)).toEqual(SNAPSHOT);
    expect(() =>
      parseTaskSnapshot({ ...SNAPSHOT, privateCredential: "atdc1.private" }),
    ).toThrowError(TaskProjectionSourceError);
    expect(() =>
      parseTaskSnapshot({ ...SNAPSHOT, lastEventSequence: Number.MAX_SAFE_INTEGER + 1 }),
    ).toThrowError(TaskProjectionSourceError);
  });

  it("classifies unknown event versions and types as a safe protocol mismatch", () => {
    const base = {
      taskId: TASK_ID,
      sequence: 3,
      eventVersion: "1.0",
      eventType: "step.progress",
      taskRevision: 6,
      taskStatus: "running",
      executionAttemptId: "16fd2706-8baf-433b-82eb-8c7fada847da",
      actionId: null,
      progressPercent: 50,
      occurredAt: "2026-07-18T02:06:00Z",
      recordedAt: "2026-07-18T02:06:01Z",
      message: null,
    };

    expect(parseTaskEvent(base)).toEqual(base);
    for (const invalid of [
      { ...base, eventVersion: "2.0" },
      { ...base, eventType: "task.future_event" },
      { ...base, message: "password=private" },
      { ...base, message: "path=/Users/private/Profile" },
      { ...base, occurredAt: "2026-02-30T02:06:00Z" },
      {
        ...base,
        occurredAt: "2026-07-18T02:06:00.000002Z",
        recordedAt: "2026-07-18T02:06:00.000001Z",
      },
      { ...base, eventType: "task.completed", progressPercent: 50 },
      { ...base, executionAttemptId: null, actionId: base.executionAttemptId },
    ]) {
      let captured: unknown;
      try {
        parseTaskEvent(invalid);
      } catch (error) {
        captured = error;
      }
      expect(captured).toMatchObject({
        name: "TaskProjectionSourceError",
        code: "protocol_mismatch",
        message: "Task projection protocol is incompatible",
        retryable: false,
      });
      expect(captured).not.toHaveProperty("cause");
    }
  });
});
