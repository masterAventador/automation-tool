import {
  TASK_EVENT_TYPES,
  parseTaskEvent,
  parseTaskSnapshot,
  type TaskEvent,
  type TaskSnapshot,
  type TaskStatus,
} from "../../api/control-plane/task-projections";

const TERMINAL_TASK_STATUSES = new Set<TaskStatus>([
  "succeeded",
  "partially_succeeded",
  "failed",
  "cancelled",
  "outcome_uncertain",
]);
const MAX_RETAINED_EVENTS = 200;

export type TaskProjectionRecoveryReason =
  | "sequence_gap"
  | "incompatible_event"
  | "protocol_mismatch"
  | "task_mismatch"
  | "revision_regression"
  | "snapshot_regression"
  | "stream_interrupted";

export type TaskProjectionPhase =
  | "empty"
  | "live"
  | "terminal"
  | "refresh_required"
  | "degraded";

export interface TaskProjectionState {
  readonly taskId: string;
  readonly phase: TaskProjectionPhase;
  readonly snapshot: TaskSnapshot | null;
  readonly events: readonly TaskEvent[];
  readonly recoveryReason: TaskProjectionRecoveryReason | null;
}

export type TaskProjectionAction =
  | { readonly type: "snapshot.loaded"; readonly snapshot: unknown }
  | { readonly type: "event.received"; readonly event: unknown }
  | {
      readonly type: "recovery.required";
      readonly reason: TaskProjectionRecoveryReason;
    }
  | {
      readonly type: "recovery.exhausted";
      readonly reason: TaskProjectionRecoveryReason;
    };

export function createTaskProjectionState(taskId: string): TaskProjectionState {
  return {
    taskId,
    phase: "empty",
    snapshot: null,
    events: [],
    recoveryReason: null,
  };
}

function recovery(
  state: TaskProjectionState,
  reason: TaskProjectionRecoveryReason,
): TaskProjectionState {
  return {
    ...state,
    phase: "refresh_required",
    recoveryReason: reason,
  };
}

function eventCompatibilityReason(value: unknown): TaskProjectionRecoveryReason {
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    if (
      record.eventVersion !== "1.0" ||
      (typeof record.eventType === "string" &&
        !TASK_EVENT_TYPES.some((eventType) => eventType === record.eventType))
    ) {
      return "incompatible_event";
    }
  }
  return "protocol_mismatch";
}

export function reduceTaskProjection(
  state: TaskProjectionState,
  action: TaskProjectionAction,
): TaskProjectionState {
  if (action.type === "recovery.required") {
    return recovery(state, action.reason);
  }
  if (action.type === "recovery.exhausted") {
    return {
      ...state,
      phase: "degraded",
      recoveryReason: action.reason,
    };
  }
  if (action.type === "snapshot.loaded") {
    let snapshot: TaskSnapshot;
    try {
      snapshot = parseTaskSnapshot(action.snapshot);
    } catch {
      return recovery(state, "protocol_mismatch");
    }
    if (snapshot.taskId !== state.taskId) {
      return recovery(state, "task_mismatch");
    }
    if (
      state.snapshot !== null &&
      (snapshot.revision < state.snapshot.revision ||
        snapshot.lastEventSequence < state.snapshot.lastEventSequence)
    ) {
      return recovery(state, "snapshot_regression");
    }
    return {
      taskId: state.taskId,
      phase: TERMINAL_TASK_STATUSES.has(snapshot.status) ? "terminal" : "live",
      snapshot,
      events: [],
      recoveryReason: null,
    };
  }

  if (state.phase === "refresh_required" || state.phase === "degraded") {
    return state;
  }
  let event: TaskEvent;
  try {
    event = parseTaskEvent(action.event);
  } catch {
    return recovery(state, eventCompatibilityReason(action.event));
  }
  if (state.snapshot === null) {
    return recovery(state, "protocol_mismatch");
  }
  if (event.taskId !== state.taskId) {
    return recovery(state, "task_mismatch");
  }
  if (event.sequence <= state.snapshot.lastEventSequence) {
    return state;
  }
  if (event.sequence !== state.snapshot.lastEventSequence + 1) {
    return recovery(state, "sequence_gap");
  }
  if (event.taskRevision <= state.snapshot.revision) {
    return recovery(state, "revision_regression");
  }

  const nextSnapshot: TaskSnapshot = {
    ...state.snapshot,
    status: event.taskStatus,
    revision: event.taskRevision,
    lastEventSequence: event.sequence,
    updatedAt: event.recordedAt,
  };
  const retainedEvents = [...state.events, event].slice(-MAX_RETAINED_EVENTS);
  return {
    taskId: state.taskId,
    phase: TERMINAL_TASK_STATUSES.has(nextSnapshot.status) ? "terminal" : "live",
    snapshot: nextSnapshot,
    events: retainedEvents,
    recoveryReason: null,
  };
}
