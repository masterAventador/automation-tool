import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

const canonicalUuidV4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const canonicalUtcTimestamp =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;
const sensitiveMessage =
  /(?:^|[^a-z0-9_])(?:access[_-]?token|api[_-]?key|authorization|cookie|credential|password|private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)\s*[:=]/i;
const inlineDataUri = /\bdata:[a-z0-9.+-]+\/[a-z0-9.+-]+[^,]*,/i;
const privatePosixPath = /(?:^|[\s"'=])\/(?:users|home|root|tmp|var\/folders)(?:\/|$)/i;
const windowsAbsolutePath = /(?:^|[\s"'=])[a-z]:[\\/]/i;

function parseCanonicalUtcTimestamp(value: string): bigint | null {
  const match = canonicalUtcTimestamp.exec(value);
  if (match === null) {
    return null;
  }
  const [, year, month, day, hour, minute, second, fraction = ""] = match;
  const numeric = [year, month, day, hour, minute, second].map(Number);
  if (numeric.some((part) => !Number.isInteger(part))) {
    return null;
  }
  const [yearValue, monthValue, dayValue, hourValue, minuteValue, secondValue] = numeric;
  if (
    yearValue === undefined ||
    yearValue < 1 ||
    monthValue === undefined ||
    dayValue === undefined ||
    hourValue === undefined ||
    minuteValue === undefined ||
    secondValue === undefined
  ) {
    return null;
  }
  const paddedFraction = fraction.padEnd(6, "0");
  const parsed = new Date(0);
  parsed.setUTCFullYear(yearValue, monthValue - 1, dayValue);
  parsed.setUTCHours(hourValue, minuteValue, secondValue, Number(paddedFraction.slice(0, 3)));
  if (
    !Number.isFinite(parsed.getTime()) ||
    parsed.getUTCFullYear() !== yearValue ||
    parsed.getUTCMonth() !== monthValue - 1 ||
    parsed.getUTCDate() !== dayValue ||
    parsed.getUTCHours() !== hourValue ||
    parsed.getUTCMinutes() !== minuteValue ||
    parsed.getUTCSeconds() !== secondValue
  ) {
    return null;
  }
  return BigInt(parsed.getTime()) * 1000n + BigInt(paddedFraction.slice(3));
}

function containsControlOrBidi(value: string): boolean {
  return Array.from(value).some((character) => {
    const point = character.codePointAt(0);
    return (
      point !== undefined &&
      (point < 0x20 ||
        point === 0x7f ||
        (point >= 0x202a && point <= 0x202e) ||
        (point >= 0x2066 && point <= 0x2069))
    );
  });
}

export const TASK_STATUSES = [
  "draft",
  "validating",
  "awaiting_device",
  "awaiting_platform_login",
  "discovering_targets",
  "awaiting_confirmation",
  "queued",
  "running",
  "paused",
  "awaiting_human",
  "cancelling",
  "succeeded",
  "partially_succeeded",
  "failed",
  "cancelled",
  "outcome_uncertain",
] as const;
export const TASK_EVENT_TYPES = [
  "task.created",
  "task.validation_started",
  "task.validation_failed",
  "task.awaiting_platform_login",
  "task.discovery_started",
  "task.awaiting_confirmation",
  "task.target_selection_updated",
  "task.targets_confirmed",
  "task.started",
  "step.started",
  "step.progress",
  "step.completed",
  "step.failed",
  "task.awaiting_human",
  "task.paused",
  "task.resumed",
  "task.cancelling",
  "task.cancelled",
  "task.completed",
  "task.partially_completed",
  "task.failed",
  "task.outcome_uncertain",
] as const;
const taskStatusSchema = z.enum(TASK_STATUSES);
const taskEventTypeSchema = z.enum(TASK_EVENT_TYPES);
const uuidSchema = z.string().regex(canonicalUuidV4);
const timestampSchema = z
  .string()
  .refine((value) => parseCanonicalUtcTimestamp(value) !== null);
const safeIntegerSchema = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const safeMessageSchema = z
  .string()
  .min(1)
  .max(1024)
  .refine(
    (value) =>
      !containsControlOrBidi(value) &&
      !value.toLowerCase().includes("bearer ") &&
      !value.toLowerCase().includes("file://") &&
      !sensitiveMessage.test(value) &&
      !inlineDataUri.test(value) &&
      !privatePosixPath.test(value) &&
      !windowsAbsolutePath.test(value),
  );

const taskSnapshotSchema = z
  .object({
    taskId: uuidSchema,
    status: taskStatusSchema,
    revision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    lastEventSequence: safeIntegerSchema,
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .strict()
  .refine((value) => {
    const createdAt = parseCanonicalUtcTimestamp(value.createdAt);
    const updatedAt = parseCanonicalUtcTimestamp(value.updatedAt);
    return (
      createdAt !== null &&
      updatedAt !== null &&
      updatedAt >= createdAt &&
      value.lastEventSequence <= value.revision
    );
  });

const taskEventSchema = z
  .object({
    taskId: uuidSchema,
    sequence: safeIntegerSchema.min(1),
    eventVersion: z.literal("1.0"),
    eventType: taskEventTypeSchema,
    taskRevision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    taskStatus: taskStatusSchema,
    executionAttemptId: uuidSchema.nullable(),
    actionId: uuidSchema.nullable(),
    progressPercent: z.number().int().min(0).max(100).nullable(),
    occurredAt: timestampSchema,
    recordedAt: timestampSchema,
    message: safeMessageSchema.nullable(),
  })
  .strict()
  .superRefine((value, context) => {
    const occurredAt = parseCanonicalUtcTimestamp(value.occurredAt);
    const recordedAt = parseCanonicalUtcTimestamp(value.recordedAt);
    if (occurredAt === null || recordedAt === null || recordedAt < occurredAt) {
      context.addIssue({ code: "custom", message: "Invalid Task event time" });
    }
    if (value.actionId !== null && value.executionAttemptId === null) {
      context.addIssue({ code: "custom", message: "Invalid Task event scope" });
    }
    if (value.eventType !== "step.progress" && value.progressPercent !== null) {
      context.addIssue({ code: "custom", message: "Invalid Task event progress" });
    }
  });

const taskListPageSchema = z
  .object({
    items: z.array(taskSnapshotSchema).max(100),
    nextCursor: z.string().min(1).max(256).nullable(),
  })
  .strict();

const taskEventStreamSummarySchema = z
  .object({
    lastSequence: safeIntegerSchema,
    terminal: z.boolean(),
  })
  .strict();

export type TaskStatus = z.infer<typeof taskStatusSchema>;
export type TaskEventType = z.infer<typeof taskEventTypeSchema>;
export type TaskSnapshot = z.infer<typeof taskSnapshotSchema>;
export type TaskEvent = z.infer<typeof taskEventSchema>;
export type TaskListPage = z.infer<typeof taskListPageSchema>;
export type TaskEventStreamSummary = z.infer<typeof taskEventStreamSummarySchema>;

export interface TaskProjectionRequestOptions {
  readonly signal?: AbortSignal;
}

export interface TaskListRequest extends TaskProjectionRequestOptions {
  readonly cursor?: string | null;
  readonly limit: number;
}

export interface TaskProjectionSource {
  getTask(taskId: string, options?: TaskProjectionRequestOptions): Promise<TaskSnapshot>;
  listTasks(request: TaskListRequest): Promise<TaskListPage>;
  streamTaskEvents(
    taskId: string,
    afterSequence: number,
    onEvent: (event: TaskEvent) => void,
    options?: TaskProjectionRequestOptions,
  ): Promise<TaskEventStreamSummary>;
}

export type TaskProjectionSourceErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "installation_access_denied"
  | "request_cancelled";

const PUBLIC_ERROR_MESSAGES: Record<TaskProjectionSourceErrorCode, string> = {
  transport_unavailable: "Task projection service is unavailable",
  protocol_mismatch: "Task projection protocol is incompatible",
  installation_access_denied: "Installation access is unavailable",
  request_cancelled: "Task projection request was cancelled",
};

export class TaskProjectionSourceError extends Error {
  readonly code: TaskProjectionSourceErrorCode;
  readonly retryable: boolean;

  constructor(code: TaskProjectionSourceErrorCode, retryable: boolean) {
    super(PUBLIC_ERROR_MESSAGES[code]);
    this.name = "TaskProjectionSourceError";
    this.code = code;
    this.retryable = retryable;
  }
}

function parseProjection<T>(schema: z.ZodType<T>, value: unknown): T {
  const result = schema.safeParse(value);
  if (!result.success) {
    throw new TaskProjectionSourceError("protocol_mismatch", false);
  }
  return result.data;
}

export function parseTaskSnapshot(value: unknown): TaskSnapshot {
  return parseProjection(taskSnapshotSchema, value);
}

export function parseTaskEvent(value: unknown): TaskEvent {
  return parseProjection(taskEventSchema, value);
}

export function parseTaskListPage(value: unknown): TaskListPage {
  return parseProjection(taskListPageSchema, value);
}

export function parseTaskEventStreamSummary(value: unknown): TaskEventStreamSummary {
  return parseProjection(taskEventStreamSummarySchema, value);
}

export const taskProjectionKeys = {
  all: ["tasks"] as const,
  lists: () => [...taskProjectionKeys.all, "list"] as const,
  list: (cursor: string | null, limit: number) =>
    [...taskProjectionKeys.lists(), { cursor, limit }] as const,
  details: () => [...taskProjectionKeys.all, "detail"] as const,
  detail: (taskId: string) => [...taskProjectionKeys.details(), taskId] as const,
};

export function taskSnapshotQueryOptions(source: TaskProjectionSource, taskId: string) {
  return queryOptions({
    queryKey: taskProjectionKeys.detail(taskId),
    queryFn: ({ signal }) => source.getTask(taskId, { signal }),
    retry: false,
    staleTime: 0,
  });
}

export function taskListQueryOptions(
  source: TaskProjectionSource,
  cursor: string | null,
  limit: number,
) {
  return queryOptions({
    queryKey: taskProjectionKeys.list(cursor, limit),
    queryFn: ({ signal }) => source.listTasks({ cursor, limit, signal }),
    retry: false,
    staleTime: 0,
  });
}
