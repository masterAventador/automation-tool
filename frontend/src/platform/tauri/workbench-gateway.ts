import { invoke } from "@tauri-apps/api/core";

import {
  WorkbenchGatewayError,
  parseEmergencyStopReceipt,
  parseWorkbenchRuntimeStatus,
  validateEmergencyStopInput,
  type EmergencyStopReceipt,
  type WorkbenchGateway,
  type WorkbenchRequestOptions,
  type WorkbenchRuntimeStatus,
} from "../../features/workbench/workbench-gateway";

function cancelled(): WorkbenchGatewayError {
  return new WorkbenchGatewayError("request_cancelled", false);
}

function mapNativeError(value: unknown): WorkbenchGatewayError | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  if (record.code === "installation_access_denied" && record.retryable === false) {
    return new WorkbenchGatewayError("installation_access_denied", false);
  }
  if (record.code === "outcome_uncertain" && record.retryable === false) {
    return new WorkbenchGatewayError("outcome_uncertain", false);
  }
  if (record.code === "operation_unavailable" && record.retryable === false) {
    return new WorkbenchGatewayError("protocol_mismatch", false);
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
    if (error instanceof WorkbenchGatewayError) {
      throw error;
    }
    const mapped = mapNativeError(error);
    if (mapped !== undefined) {
      throw mapped;
    }
    throw new WorkbenchGatewayError("transport_unavailable", true);
  }
}

export class TauriWorkbenchGateway implements WorkbenchGateway {
  async getRuntimeStatus(
    options: WorkbenchRequestOptions = {},
  ): Promise<WorkbenchRuntimeStatus> {
    const response = await safeInvoke("get_workbench_status", {}, options.signal);
    return parseWorkbenchRuntimeStatus(response);
  }

  async emergencyStopTask(
    taskId: string,
    idempotencyKey: string,
    options: WorkbenchRequestOptions = {},
  ): Promise<EmergencyStopReceipt> {
    validateEmergencyStopInput(taskId, idempotencyKey);
    const response = await safeInvoke(
      "emergency_stop_workbench_task",
      { taskId, idempotencyKey },
      options.signal,
    );
    const receipt = parseEmergencyStopReceipt(response);
    if (receipt.taskId !== taskId) {
      throw new WorkbenchGatewayError("protocol_mismatch", false);
    }
    return receipt;
  }
}

