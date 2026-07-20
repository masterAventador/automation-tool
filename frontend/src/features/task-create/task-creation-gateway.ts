import { z } from "zod";

import {
  douyinSearchExposureDefinitionSchema,
  type DouyinSearchExposureTaskDefinition,
} from "../../api/control-plane/douyin-search-exposure";
import {
  parseTaskSnapshot,
  TaskProjectionSourceError,
  type TaskSnapshot,
} from "../../api/control-plane/task-projections";

const canonicalIdempotencyKey = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const idempotencyKeySchema = z.string().regex(canonicalIdempotencyKey);

export {
  douyinActionMessageTemplateSchema,
  douyinSearchExposureDefinitionSchema,
  douyinSearchKeywordSchema,
  MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS,
  MAX_SEARCH_KEYWORD_CHARACTERS,
  MAX_TASK_TARGET_LIMIT,
} from "../../api/control-plane/douyin-search-exposure";
export type { DouyinSearchExposureTaskDefinition };

export interface TaskCreationRequestOptions {
  readonly signal?: AbortSignal;
}

export interface TaskCreationGateway {
  createDouyinSearchExposureTask(
    definition: DouyinSearchExposureTaskDefinition,
    idempotencyKey: string,
    options?: TaskCreationRequestOptions,
  ): Promise<TaskSnapshot>;
}

export type TaskCreationGatewayErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "installation_access_denied"
  | "credential_missing"
  | "request_cancelled";

const publicMessages: Record<TaskCreationGatewayErrorCode, string> = {
  transport_unavailable: "Task creation service is unavailable",
  protocol_mismatch: "Task creation protocol is incompatible",
  installation_access_denied: "Installation access is unavailable",
  credential_missing: "Installation credential is unavailable",
  request_cancelled: "Task creation request was cancelled",
};

export class TaskCreationGatewayError extends Error {
  readonly code: TaskCreationGatewayErrorCode;
  readonly retryable: boolean;

  constructor(code: TaskCreationGatewayErrorCode, retryable: boolean) {
    super(publicMessages[code]);
    this.name = "TaskCreationGatewayError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function validateTaskCreationInput(
  definition: DouyinSearchExposureTaskDefinition,
  idempotencyKey: string,
): DouyinSearchExposureTaskDefinition {
  const parsedDefinition = douyinSearchExposureDefinitionSchema.safeParse(definition);
  if (!parsedDefinition.success || !idempotencyKeySchema.safeParse(idempotencyKey).success) {
    throw new TaskCreationGatewayError("protocol_mismatch", false);
  }
  return parsedDefinition.data;
}

export function parseCreatedTask(value: unknown): TaskSnapshot {
  try {
    const snapshot = parseTaskSnapshot(value);
    if (snapshot.status !== "draft" || snapshot.revision !== 1 || snapshot.lastEventSequence !== 0) {
      throw new TaskCreationGatewayError("protocol_mismatch", false);
    }
    return snapshot;
  } catch (error) {
    if (error instanceof TaskCreationGatewayError) {
      throw error;
    }
    if (error instanceof TaskProjectionSourceError) {
      throw new TaskCreationGatewayError("protocol_mismatch", false);
    }
    throw new TaskCreationGatewayError("protocol_mismatch", false);
  }
}
