import { invoke } from "@tauri-apps/api/core";

import {
  TaskTargetPreviewSourceError,
  parseTaskTargetPreview,
  type TaskTargetConfirmationRequest,
  type TaskTargetExclusionsRequest,
  type TaskTargetPreview,
  type TaskTargetPreviewListRequest,
  type TaskTargetPreviewSource,
} from "../../api/control-plane/task-target-previews";

function cancelled(): TaskTargetPreviewSourceError {
  return new TaskTargetPreviewSourceError("request_cancelled", false);
}

function mapNativeError(value: unknown): TaskTargetPreviewSourceError {
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    if (record.code === "installation_access_denied" && record.retryable === false) {
      return new TaskTargetPreviewSourceError("installation_access_denied", false);
    }
    if (record.code === "request_rejected" && record.retryable === false) {
      return new TaskTargetPreviewSourceError("preview_stale", false);
    }
    if (record.code === "operation_unavailable" && record.retryable === false) {
      return new TaskTargetPreviewSourceError("protocol_mismatch", false);
    }
  }
  return new TaskTargetPreviewSourceError("transport_unavailable", true);
}

async function invokePreview(
  command: string,
  args: Record<string, unknown>,
  signal: AbortSignal | undefined,
): Promise<TaskTargetPreview> {
  if (signal?.aborted === true) {
    throw cancelled();
  }
  let rejectCancellation: (() => void) | undefined;
  const cancellation = new Promise<never>((_resolve, reject) => {
    rejectCancellation = () => reject(cancelled());
    signal?.addEventListener("abort", rejectCancellation, { once: true });
  });
  try {
    const request = invoke<unknown>(command, args).catch((error: unknown) => {
      throw mapNativeError(error);
    });
    const response = signal === undefined ? await request : await Promise.race([request, cancellation]);
    return parseTaskTargetPreview(response);
  } finally {
    if (rejectCancellation !== undefined) {
      signal?.removeEventListener("abort", rejectCancellation);
    }
  }
}

export class TauriTaskTargetPreviewSource implements TaskTargetPreviewSource {
  getPreview(request: TaskTargetPreviewListRequest): Promise<TaskTargetPreview> {
    return invokePreview(
      "get_task_target_preview",
      {
        taskId: request.taskId,
        cursor: request.cursor ?? null,
        limit: request.limit,
      },
      request.signal,
    );
  }

  replaceExclusions(request: TaskTargetExclusionsRequest): Promise<TaskTargetPreview> {
    return invokePreview(
      "replace_task_target_exclusions",
      {
        taskId: request.taskId,
        pageRevision: request.pageRevision,
        expectedTaskRevision: request.expectedTaskRevision,
        excludedTargetIds: [...request.excludedTargetIds],
        idempotencyKey: request.idempotencyKey,
      },
      request.signal,
    );
  }

  confirm(request: TaskTargetConfirmationRequest): Promise<TaskTargetPreview> {
    return invokePreview(
      "confirm_task_target_preview",
      {
        taskId: request.taskId,
        pageRevision: request.pageRevision,
        expectedTaskRevision: request.expectedTaskRevision,
        idempotencyKey: request.idempotencyKey,
      },
      request.signal,
    );
  }
}
