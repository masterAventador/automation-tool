import type { QueryClient } from "@tanstack/react-query";

import {
  TaskProjectionSourceError,
  taskProjectionKeys,
  taskSnapshotQueryOptions,
  type TaskProjectionSource,
} from "../../api/control-plane/task-projections";
import {
  createTaskProjectionState,
  reduceTaskProjection,
  type TaskProjectionRecoveryReason,
  type TaskProjectionState,
} from "./task-projection-reducer";

export interface FollowTaskProjectionOptions {
  readonly queryClient: QueryClient;
  readonly source: TaskProjectionSource;
  readonly taskId: string;
  readonly signal?: AbortSignal;
  readonly maxRecoveries?: number;
  readonly onChange?: (state: TaskProjectionState) => void;
}

function recoveryReason(error: unknown): TaskProjectionRecoveryReason {
  if (
    error instanceof TaskProjectionSourceError &&
    error.code === "protocol_mismatch"
  ) {
    return "protocol_mismatch";
  }
  return "stream_interrupted";
}

function isAborted(signal: AbortSignal | undefined): boolean {
  return signal?.aborted === true;
}

export async function followTaskProjection({
  queryClient,
  source,
  taskId,
  signal,
  maxRecoveries = 3,
  onChange = () => undefined,
}: FollowTaskProjectionOptions): Promise<TaskProjectionState> {
  let state = createTaskProjectionState(taskId);
  let recoveries = 0;
  let reloading = false;

  const publish = (next: TaskProjectionState) => {
    state = next;
    onChange(state);
  };
  const readState = (): TaskProjectionState => state;

  while (!isAborted(signal)) {
    if (reloading) {
      await queryClient.invalidateQueries({ queryKey: taskProjectionKeys.detail(taskId) });
    }
    const snapshot = await queryClient.fetchQuery(taskSnapshotQueryOptions(source, taskId));
    publish(reduceTaskProjection(state, { type: "snapshot.loaded", snapshot }));
    if (state.phase === "terminal" || state.phase === "degraded") {
      return state;
    }
    if (state.phase === "refresh_required") {
      if (recoveries >= maxRecoveries) {
        publish(
          reduceTaskProjection(state, {
            type: "recovery.exhausted",
            reason: state.recoveryReason ?? "protocol_mismatch",
          }),
        );
        return state;
      }
      recoveries += 1;
      reloading = true;
      continue;
    }

    const afterSequence = state.snapshot?.lastEventSequence;
    if (afterSequence === undefined) {
      publish(
        reduceTaskProjection(state, {
          type: "recovery.exhausted",
          reason: "protocol_mismatch",
        }),
      );
      return state;
    }

    try {
      const summary = await source.streamTaskEvents(
        taskId,
        afterSequence,
        (event) => {
          publish(reduceTaskProjection(state, { type: "event.received", event }));
        },
        signal === undefined ? {} : { signal },
      );
      const streamedState = readState();
      if (streamedState.phase === "terminal") {
        if (
          summary.terminal &&
          summary.lastSequence === streamedState.snapshot?.lastEventSequence
        ) {
          return streamedState;
        }
        publish(
          reduceTaskProjection(streamedState, {
            type: "recovery.required",
            reason: "protocol_mismatch",
          }),
        );
      } else if (
        streamedState.phase === "live" &&
        summary.lastSequence !== streamedState.snapshot?.lastEventSequence
      ) {
        publish(
          reduceTaskProjection(streamedState, {
            type: "recovery.required",
            reason: "protocol_mismatch",
          }),
        );
      }
    } catch (error) {
      if (
        isAborted(signal) ||
        (error instanceof TaskProjectionSourceError && error.code === "request_cancelled")
      ) {
        return state;
      }
      publish(
        reduceTaskProjection(state, {
          type: "recovery.required",
          reason: recoveryReason(error),
        }),
      );
    }

    const recoveredState = readState();
    if (recoveredState.phase === "refresh_required") {
      if (recoveries >= maxRecoveries) {
        publish(
          reduceTaskProjection(recoveredState, {
            type: "recovery.exhausted",
            reason: recoveredState.recoveryReason ?? "protocol_mismatch",
          }),
        );
        return state;
      }
      recoveries += 1;
    } else {
      recoveries = 0;
    }
    reloading = true;
  }
  return state;
}
