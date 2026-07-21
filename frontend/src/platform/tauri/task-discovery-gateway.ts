import { invoke } from "@tauri-apps/api/core";

import {
  TaskDiscoveryGatewayError,
  parseTaskDiscoveryReceipt,
  validateTaskDiscoveryInput,
  type TaskDiscoveryGateway,
  type TaskDiscoveryReceipt,
  type TaskDiscoveryRequestOptions,
} from "../../features/task-runs/task-discovery";

function cancelled(): TaskDiscoveryGatewayError {
  return new TaskDiscoveryGatewayError("request_cancelled", false);
}

function mapNativeError(value: unknown): TaskDiscoveryGatewayError {
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    if (record.code === "installation_access_denied" && record.retryable === false) {
      return new TaskDiscoveryGatewayError("installation_access_denied", false);
    }
    if (record.code === "operation_unavailable" && record.retryable === false) {
      return new TaskDiscoveryGatewayError("discovery_rejected", false);
    }
  }
  return new TaskDiscoveryGatewayError("transport_unavailable", true);
}

async function invokeWithCancellation<T>(
  request: Promise<T>,
  signal: AbortSignal | undefined,
): Promise<T> {
  if (signal === undefined) return request;
  let cancelRequest: (() => void) | undefined;
  const cancellation = new Promise<never>((_resolve, reject) => {
    cancelRequest = () => reject(cancelled());
    signal.addEventListener("abort", cancelRequest, { once: true });
    if (signal.aborted) cancelRequest();
  });
  try {
    return await Promise.race([request, cancellation]);
  } finally {
    if (cancelRequest !== undefined) signal.removeEventListener("abort", cancelRequest);
  }
}

export class TauriTaskDiscoveryGateway implements TaskDiscoveryGateway {
  async startDiscovery(
    taskId: string,
    idempotencyKey: string,
    options: TaskDiscoveryRequestOptions = {},
  ): Promise<TaskDiscoveryReceipt> {
    validateTaskDiscoveryInput(taskId, idempotencyKey);
    if (options.signal?.aborted === true) throw cancelled();
    try {
      const response = await invokeWithCancellation(
        invoke<unknown>("start_task_discovery", { taskId, idempotencyKey }),
        options.signal,
      );
      return parseTaskDiscoveryReceipt(response, taskId);
    } catch (error) {
      if (error instanceof TaskDiscoveryGatewayError) throw error;
      throw mapNativeError(error);
    }
  }
}
