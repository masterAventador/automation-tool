import { invoke } from "@tauri-apps/api/core";

import {
  parseCreatedTask,
  TaskCreationGatewayError,
  validateTaskCreationInput,
  type DouyinSearchExposureTaskDefinition,
  type TaskCreationGateway,
  type TaskCreationRequestOptions,
} from "../../features/task-create/task-creation-gateway";
import type { TaskSnapshot } from "../../api/control-plane/task-projections";

function cancelled(): TaskCreationGatewayError {
  return new TaskCreationGatewayError("request_cancelled", false);
}

function mapNativeError(value: unknown): TaskCreationGatewayError | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  if (record.code === "installation_access_denied" && record.retryable === false) {
    return new TaskCreationGatewayError("installation_access_denied", false);
  }
  if (record.code === "operation_unavailable" && record.retryable === false) {
    return new TaskCreationGatewayError("protocol_mismatch", false);
  }
  return undefined;
}

async function invokeWithCancellation(
  definition: DouyinSearchExposureTaskDefinition,
  idempotencyKey: string,
  signal: AbortSignal | undefined,
): Promise<unknown> {
  if (signal?.aborted === true) {
    throw cancelled();
  }
  const request = invoke("create_douyin_search_exposure_task", {
    definition,
    idempotencyKey,
  });
  if (signal === undefined) {
    return await request;
  }
  let cancelRequest: (() => void) | undefined;
  const cancellation = new Promise<never>((_resolve, reject) => {
    cancelRequest = () => reject(cancelled());
    signal.addEventListener("abort", cancelRequest, { once: true });
  });
  try {
    return await Promise.race([request, cancellation]);
  } finally {
    if (cancelRequest !== undefined) {
      signal.removeEventListener("abort", cancelRequest);
    }
  }
}

export class TauriTaskCreationGateway implements TaskCreationGateway {
  async createDouyinSearchExposureTask(
    definition: DouyinSearchExposureTaskDefinition,
    idempotencyKey: string,
    options: TaskCreationRequestOptions = {},
  ): Promise<TaskSnapshot> {
    const validated = validateTaskCreationInput(definition, idempotencyKey);
    try {
      const response = await invokeWithCancellation(validated, idempotencyKey, options.signal);
      return parseCreatedTask(response);
    } catch (error) {
      if (error instanceof TaskCreationGatewayError) {
        throw error;
      }
      const mapped = mapNativeError(error);
      if (mapped !== undefined) {
        throw mapped;
      }
      throw new TaskCreationGatewayError("transport_unavailable", true);
    }
  }
}
