import { invoke } from "@tauri-apps/api/core";

import {
  TaskRunControlGatewayError,
  parseTaskRunControlReceipt,
  validateTaskRunControlInput,
  type TaskRunControlGateway,
  type TaskRunControlOperation,
  type TaskRunControlReceipt,
  type TaskRunControlRequestOptions,
} from "../../features/task-runs/task-run-controls";

const COMMAND_BY_OPERATION = {
  pause: "pause_task_run",
  resume: "resume_task_run",
  cancel: "cancel_task_run",
  emergency_stop: "emergency_stop_task_run",
} as const satisfies Record<TaskRunControlOperation, string>;

function cancelled(): TaskRunControlGatewayError {
  return new TaskRunControlGatewayError("request_cancelled", false);
}

function mapNativeError(value: unknown): TaskRunControlGatewayError | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  if (record.code === "installation_access_denied" && record.retryable === false) {
    return new TaskRunControlGatewayError("installation_access_denied", false);
  }
  if (record.code === "outcome_uncertain" && record.retryable === false) {
    return new TaskRunControlGatewayError("outcome_uncertain", false);
  }
  if (record.code === "operation_unavailable" && record.retryable === false) {
    return new TaskRunControlGatewayError("control_rejected", false);
  }
  return undefined;
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

async function controlTask(
  operation: TaskRunControlOperation,
  taskId: string,
  idempotencyKey: string,
  options: TaskRunControlRequestOptions,
): Promise<TaskRunControlReceipt> {
  validateTaskRunControlInput(taskId, idempotencyKey, operation);
  if (options.signal?.aborted === true) {
    throw cancelled();
  }
  try {
    const response = await invokeWithCancellation(
      invoke<unknown>(COMMAND_BY_OPERATION[operation], { taskId, idempotencyKey }),
      options.signal,
    );
    return parseTaskRunControlReceipt(response, taskId, operation);
  } catch (error) {
    if (error instanceof TaskRunControlGatewayError) {
      throw error;
    }
    const mapped = mapNativeError(error);
    if (mapped !== undefined) {
      throw mapped;
    }
    throw new TaskRunControlGatewayError("transport_unavailable", true);
  }
}

export class TauriTaskRunControlGateway implements TaskRunControlGateway {
  pauseTask(
    taskId: string,
    idempotencyKey: string,
    options: TaskRunControlRequestOptions = {},
  ): Promise<TaskRunControlReceipt> {
    return controlTask("pause", taskId, idempotencyKey, options);
  }

  resumeTask(
    taskId: string,
    idempotencyKey: string,
    options: TaskRunControlRequestOptions = {},
  ): Promise<TaskRunControlReceipt> {
    return controlTask("resume", taskId, idempotencyKey, options);
  }

  cancelTask(
    taskId: string,
    idempotencyKey: string,
    options: TaskRunControlRequestOptions = {},
  ): Promise<TaskRunControlReceipt> {
    return controlTask("cancel", taskId, idempotencyKey, options);
  }

  emergencyStopTask(
    taskId: string,
    idempotencyKey: string,
    options: TaskRunControlRequestOptions = {},
  ): Promise<TaskRunControlReceipt> {
    return controlTask("emergency_stop", taskId, idempotencyKey, options);
  }
}
