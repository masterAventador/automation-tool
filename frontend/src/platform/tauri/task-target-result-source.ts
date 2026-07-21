import { invoke } from "@tauri-apps/api/core";

import {
  TaskTargetResultSourceError,
  parseTaskTargetResultSnapshot,
  type TaskTargetResultRequestOptions,
  type TaskTargetResultSnapshot,
  type TaskTargetResultSource,
} from "../../api/control-plane/task-target-results";

function cancelled(): TaskTargetResultSourceError {
  return new TaskTargetResultSourceError("request_cancelled", false);
}

function mapNativeError(value: unknown): TaskTargetResultSourceError {
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    if (record.code === "installation_access_denied" && record.retryable === false) {
      return new TaskTargetResultSourceError("installation_access_denied", false);
    }
    if (record.code === "operation_unavailable" && record.retryable === false) {
      return new TaskTargetResultSourceError("protocol_mismatch", false);
    }
  }
  return new TaskTargetResultSourceError("transport_unavailable", true);
}

export class TauriTaskTargetResultSource implements TaskTargetResultSource {
  async getResults(
    taskId: string,
    options: TaskTargetResultRequestOptions = {},
  ): Promise<TaskTargetResultSnapshot> {
    if (options.signal?.aborted === true) throw cancelled();
    let abort: (() => void) | undefined;
    const cancellation = new Promise<never>((_resolve, reject) => {
      abort = () => reject(cancelled());
      options.signal?.addEventListener("abort", abort, { once: true });
      if (options.signal?.aborted === true) abort();
    });
    try {
      const value = await Promise.race([
        invoke<unknown>("get_task_target_results", { taskId }),
        cancellation,
      ]);
      const snapshot = parseTaskTargetResultSnapshot(value);
      if (snapshot.taskId !== taskId) {
        throw new TaskTargetResultSourceError("protocol_mismatch", false);
      }
      return snapshot;
    } catch (error) {
      if (error instanceof TaskTargetResultSourceError) throw error;
      throw mapNativeError(error);
    } finally {
      if (abort !== undefined) options.signal?.removeEventListener("abort", abort);
    }
  }
}
