import { z } from "zod";

import {
  parseTaskSnapshot,
  TaskProjectionSourceError,
  type TaskSnapshot,
} from "../../api/control-plane/task-projections";

const canonicalIdempotencyKey = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const sensitiveAssignment =
  /(?:^|[^a-z0-9_])(?:access[_-]?token|api[_-]?key|authorization|cookie|credential|password|private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)\s*[:=]/i;
const inlineDataUri = /\bdata:[a-z0-9.+-]+\/[a-z0-9.+-]+[^,]*,/i;
const privatePosixPath = /(?:^|[\s"'=])\/(?:users|home|root|tmp|var\/folders)(?:\/|$)/i;
const windowsAbsolutePath = /(?:^|[\s"'=])[a-z]:[\\/]/i;

export const MAX_SEARCH_KEYWORD_CHARACTERS = 80;
export const MAX_TASK_TARGET_LIMIT = 100;

function containsControlOrBidi(value: string): boolean {
  return Array.from(value).some((character) => {
    const point = character.codePointAt(0);
    return (
      point !== undefined &&
      (point < 0x20 ||
        point === 0x7f ||
        (point >= 0x80 && point <= 0x9f) ||
        (point >= 0x202a && point <= 0x202e) ||
        (point >= 0x2066 && point <= 0x2069))
    );
  });
}

function safeExactText(maximumCharacters: number) {
  return z
    .string()
    .min(1)
    .refine((value) => Array.from(value).length <= maximumCharacters)
    .refine((value) => value.trim() === value)
    .refine((value) => {
      const folded = value.toLowerCase();
      return (
        !containsControlOrBidi(value) &&
        !folded.includes("bearer ") &&
        !folded.includes("file://") &&
        !sensitiveAssignment.test(value) &&
        !inlineDataUri.test(value) &&
        !privatePosixPath.test(value) &&
        !windowsAbsolutePath.test(value)
      );
    });
}

export const douyinSearchKeywordSchema = safeExactText(MAX_SEARCH_KEYWORD_CHARACTERS);

export const douyinSearchExposureDefinitionSchema = z
  .object({
    template: z.literal("douyin.search_exposure.v1"),
    searchKeyword: douyinSearchKeywordSchema,
    action: z.enum(["browse", "comment", "direct_message"]),
    messageTemplate: safeExactText(500).nullable(),
    targetLimit: z.number().int().min(1).max(MAX_TASK_TARGET_LIMIT),
    minimumIntervalSeconds: z.number().int().min(1).max(3600),
    maximumIntervalSeconds: z.number().int().min(1).max(3600),
    previewRequired: z.literal(true),
    finalConfirmationRequired: z.literal(true),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.minimumIntervalSeconds > value.maximumIntervalSeconds) {
      context.addIssue({ code: "custom", message: "Invalid Task interval" });
    }
    if (
      (value.action === "browse" && value.messageTemplate !== null) ||
      (value.action !== "browse" && value.messageTemplate === null)
    ) {
      context.addIssue({ code: "custom", message: "Invalid Task message relation" });
    }
  });

const idempotencyKeySchema = z.string().regex(canonicalIdempotencyKey);

export type DouyinSearchExposureTaskDefinition = z.infer<
  typeof douyinSearchExposureDefinitionSchema
>;

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
  | "request_cancelled";

const publicMessages: Record<TaskCreationGatewayErrorCode, string> = {
  transport_unavailable: "Task creation service is unavailable",
  protocol_mismatch: "Task creation protocol is incompatible",
  installation_access_denied: "Installation access is unavailable",
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
