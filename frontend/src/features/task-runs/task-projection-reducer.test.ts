import { describe, expect, it } from "vitest";

import type { TaskEvent, TaskSnapshot } from "../../api/control-plane/task-projections";
import {
  createTaskProjectionState,
  reduceTaskProjection,
} from "./task-projection-reducer";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const OTHER_TASK_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

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
    eventType: "step.progress",
    taskRevision: 6,
    taskStatus: "running",
    executionAttemptId: "16fd2706-8baf-433b-82eb-8c7fada847da",
    actionId: null,
    progressPercent: 50,
    occurredAt: "2026-07-18T02:06:00Z",
    recordedAt: "2026-07-18T02:06:01Z",
    message: null,
    ...overrides,
  };
}

describe("Task projection reducer", () => {
  it("loads the server snapshot as the authoritative status, revision, and event watermark", () => {
    const state = reduceTaskProjection(createTaskProjectionState(TASK_ID), {
      type: "snapshot.loaded",
      snapshot: snapshot({ status: "paused", revision: 8, lastEventSequence: 7 }),
    });

    expect(state).toMatchObject({
      phase: "live",
      snapshot: { status: "paused", revision: 8, lastEventSequence: 7 },
      events: [],
    });
  });

  it("uses the event post-state fields and never guesses status from the event name", () => {
    const loaded = reduceTaskProjection(createTaskProjectionState(TASK_ID), {
      type: "snapshot.loaded",
      snapshot: snapshot(),
    });
    const projected = reduceTaskProjection(loaded, {
      type: "event.received",
      event: event({ eventType: "step.progress", taskStatus: "awaiting_human" }),
    });

    expect(projected).toMatchObject({
      phase: "live",
      snapshot: {
        status: "awaiting_human",
        revision: 6,
        lastEventSequence: 3,
        updatedAt: "2026-07-18T02:06:01Z",
      },
    });
  });

  it("deduplicates events already covered by the authoritative watermark", () => {
    const loaded = reduceTaskProjection(createTaskProjectionState(TASK_ID), {
      type: "snapshot.loaded",
      snapshot: snapshot(),
    });

    expect(
      reduceTaskProjection(loaded, {
        type: "event.received",
        event: event({ sequence: 2, taskRevision: 99, taskStatus: "failed" }),
      }),
    ).toBe(loaded);
  });

  it("requests a snapshot reload for a sequence gap without applying the event", () => {
    const loaded = reduceTaskProjection(createTaskProjectionState(TASK_ID), {
      type: "snapshot.loaded",
      snapshot: snapshot(),
    });
    const projected = reduceTaskProjection(loaded, {
      type: "event.received",
      event: event({ sequence: 4, taskRevision: 7, taskStatus: "failed" }),
    });

    expect(projected).toMatchObject({
      phase: "refresh_required",
      recoveryReason: "sequence_gap",
      snapshot: { status: "running", revision: 5, lastEventSequence: 2 },
    });
  });

  it.each([
    ["unknown version", { ...event(), eventVersion: "2.0" }],
    ["unknown type", { ...event(), eventType: "task.future_event" }],
    ["other Task", event({ taskId: OTHER_TASK_ID })],
    ["regressive revision", event({ taskRevision: 5 })],
  ])("fails closed and requests recovery for %s", (_label, value) => {
    const loaded = reduceTaskProjection(createTaskProjectionState(TASK_ID), {
      type: "snapshot.loaded",
      snapshot: snapshot(),
    });
    const projected = reduceTaskProjection(loaded, {
      type: "event.received",
      event: value,
    });

    expect(projected.phase).toBe("refresh_required");
    expect(projected.snapshot).toEqual(snapshot());
    if (_label === "unknown version" || _label === "unknown type") {
      expect(projected.recoveryReason).toBe("incompatible_event");
    }
  });

  it("rejects a regressive replacement snapshot instead of rolling the UI back", () => {
    const loaded = reduceTaskProjection(createTaskProjectionState(TASK_ID), {
      type: "snapshot.loaded",
      snapshot: snapshot(),
    });
    const projected = reduceTaskProjection(loaded, {
      type: "snapshot.loaded",
      snapshot: snapshot({ revision: 4, lastEventSequence: 1 }),
    });

    expect(projected).toMatchObject({
      phase: "refresh_required",
      recoveryReason: "snapshot_regression",
      snapshot: { revision: 5, lastEventSequence: 2 },
    });
  });

  it("closes the projection only from a validated terminal post-state", () => {
    const loaded = reduceTaskProjection(createTaskProjectionState(TASK_ID), {
      type: "snapshot.loaded",
      snapshot: snapshot(),
    });
    const projected = reduceTaskProjection(loaded, {
      type: "event.received",
      event: event({
        eventType: "task.completed",
        taskStatus: "succeeded",
        progressPercent: null,
      }),
    });

    expect(projected.phase).toBe("terminal");
  });

  it("enters a stable degraded state when the bounded recovery budget is exhausted", () => {
    const loaded = reduceTaskProjection(createTaskProjectionState(TASK_ID), {
      type: "snapshot.loaded",
      snapshot: snapshot(),
    });
    const projected = reduceTaskProjection(loaded, {
      type: "recovery.exhausted",
      reason: "protocol_mismatch",
    });

    expect(projected).toMatchObject({
      phase: "degraded",
      recoveryReason: "protocol_mismatch",
      snapshot: snapshot(),
    });
  });
});
