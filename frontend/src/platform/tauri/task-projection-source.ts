import { Channel, invoke } from "@tauri-apps/api/core";

import {
  TaskProjectionSourceError,
  parseTaskEvent,
  parseTaskEventStreamSummary,
  parseTaskListPage,
  parseTaskSnapshot,
  type TaskEvent,
  type TaskEventStreamSummary,
  type TaskListPage,
  type TaskListRequest,
  type TaskProjectionRequestOptions,
  type TaskProjectionSource,
  type TaskSnapshot,
} from "../../api/control-plane/task-projections";

function exactNativeError(value: unknown): TaskProjectionSourceError | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  if (record.code === "operation_unavailable" && record.retryable === false) {
    return new TaskProjectionSourceError("protocol_mismatch", false);
  }
  if (record.code === "installation_access_denied" && record.retryable === false) {
    return new TaskProjectionSourceError("installation_access_denied", false);
  }
  return undefined;
}

function cancelled(): TaskProjectionSourceError {
  return new TaskProjectionSourceError("request_cancelled", false);
}

async function invokeWithCancellation<T>(
  request: Promise<T>,
  signal: AbortSignal | undefined,
): Promise<T> {
  if (signal === undefined) {
    return request;
  }
  let cancelRequest: (() => void) | undefined;
  const cancellation = new Promise<never>((_resolve, reject) => {
    cancelRequest = () => reject(cancelled());
    signal.addEventListener("abort", cancelRequest, { once: true });
    if (signal.aborted) {
      cancelRequest();
    }
  });
  try {
    return await Promise.race([request, cancellation]);
  } finally {
    if (cancelRequest !== undefined) {
      signal.removeEventListener("abort", cancelRequest);
    }
  }
}

async function safeInvoke(
  command: string,
  args: Record<string, unknown>,
  signal: AbortSignal | undefined,
): Promise<unknown> {
  if (signal?.aborted === true) {
    throw cancelled();
  }
  try {
    return await invokeWithCancellation(invoke<unknown>(command, args), signal);
  } catch (error) {
    if (error instanceof TaskProjectionSourceError) {
      throw error;
    }
    const mapped = exactNativeError(error);
    if (mapped !== undefined) {
      throw mapped;
    }
    throw new TaskProjectionSourceError("transport_unavailable", true);
  }
}

export class TauriTaskProjectionSource implements TaskProjectionSource {
  async getTask(
    taskId: string,
    options: TaskProjectionRequestOptions = {},
  ): Promise<TaskSnapshot> {
    const response = await safeInvoke("get_task_snapshot", { taskId }, options.signal);
    return parseTaskSnapshot(response);
  }

  async listTasks(request: TaskListRequest): Promise<TaskListPage> {
    const response = await safeInvoke(
      "list_task_snapshots",
      { cursor: request.cursor ?? null, limit: request.limit },
      request.signal,
    );
    return parseTaskListPage(response);
  }

  async streamTaskEvents(
    taskId: string,
    afterSequence: number,
    onEvent: (event: TaskEvent) => void,
    options: TaskProjectionRequestOptions = {},
  ): Promise<TaskEventStreamSummary> {
    if (options.signal?.aborted === true) {
      throw cancelled();
    }
    let channelError: TaskProjectionSourceError | undefined;
    const onEventChannel = new Channel<unknown>((value) => {
      if (channelError !== undefined || options.signal?.aborted === true) {
        return;
      }
      try {
        onEvent(parseTaskEvent(value));
      } catch {
        channelError = new TaskProjectionSourceError("protocol_mismatch", false);
      }
    });
    const response = await safeInvoke(
      "stream_task_projection_events",
      { taskId, afterSequence, onEvent: onEventChannel },
      options.signal,
    );
    if (channelError !== undefined) {
      throw channelError;
    }
    return parseTaskEventStreamSummary(response);
  }
}
