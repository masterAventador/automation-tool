import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { TASK_STATUSES } from "./task-projections";

const canonicalUuidV4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const canonicalUtcTimestamp =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;
const publicHandle = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

export const TASK_TARGET_RESULT_STATUSES = [
  "pending",
  "running",
  "succeeded",
  "skipped",
  "failed",
  "outcome_uncertain",
] as const;
export const TASK_TARGET_RESULT_EVIDENCE = [
  "awaiting_execution",
  "action_pending",
  "action_in_progress",
  "profile_visible",
  "comment_confirmed",
  "message_confirmed",
  "executor_reported_success",
  "user_excluded",
  "duplicate_in_task",
  "duplicate_in_history",
  "blacklisted",
  "action_cancelled",
  "admission_rejected",
  "local_safety_limit",
  "login_required",
  "dialog_blocked",
  "messaging_not_allowed",
  "follow_required",
  "timed_out",
  "page_version_unknown",
  "conflicting_anchors",
  "page_unavailable",
  "verification_unavailable",
  "executor_reported_failure",
  "dispatch_timed_out",
  "dispatch_unavailable",
  "final_state_unconfirmed",
  "recovery_unconfirmed",
] as const;

const statusSchema = z.enum(TASK_TARGET_RESULT_STATUSES);
const evidenceSchema = z.enum(TASK_TARGET_RESULT_EVIDENCE);
const uuidSchema = z.string().regex(canonicalUuidV4);
const timestampSchema = z.string().regex(canonicalUtcTimestamp);
const safeDisplayName = z
  .string()
  .min(1)
  .max(80)
  .refine(
    (value) =>
      value.trim() === value &&
      !Array.from(value).some((character) => {
        const point = character.codePointAt(0);
        return point !== undefined && (point < 0x20 || point === 0x7f);
      }) &&
      !/(?:bearer |file:\/\/|data:[^,]+,|(?:password|secret|token)\s*[:=])/i.test(value),
  );

const statusEvidence: Record<
  (typeof TASK_TARGET_RESULT_STATUSES)[number],
  ReadonlySet<(typeof TASK_TARGET_RESULT_EVIDENCE)[number]>
> = {
  pending: new Set(["awaiting_execution", "action_pending"]),
  running: new Set(["action_in_progress"]),
  succeeded: new Set([
    "profile_visible",
    "comment_confirmed",
    "message_confirmed",
    "executor_reported_success",
  ]),
  skipped: new Set([
    "user_excluded",
    "duplicate_in_task",
    "duplicate_in_history",
    "blacklisted",
    "action_cancelled",
  ]),
  failed: new Set([
    "admission_rejected",
    "local_safety_limit",
    "login_required",
    "dialog_blocked",
    "messaging_not_allowed",
    "follow_required",
    "timed_out",
    "page_version_unknown",
    "conflicting_anchors",
    "page_unavailable",
    "verification_unavailable",
    "executor_reported_failure",
  ]),
  outcome_uncertain: new Set([
    "dispatch_timed_out",
    "dispatch_unavailable",
    "final_state_unconfirmed",
    "recovery_unconfirmed",
  ]),
};

const itemSchema = z
  .object({
    targetId: uuidSchema,
    ordinal: z.number().int().min(1).max(100),
    displayName: safeDisplayName,
    publicHandle: z.string().regex(publicHandle).nullable(),
    resultStatus: statusSchema,
    evidence: evidenceSchema,
    actionId: uuidSchema.nullable(),
    updatedAt: timestampSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (!statusEvidence[value.resultStatus].has(value.evidence)) {
      context.addIssue({ code: "custom", message: "Invalid target result evidence" });
    }
    const actionRequired = ["running", "succeeded", "failed", "outcome_uncertain"].includes(
      value.resultStatus,
    );
    const actionOptional =
      (value.resultStatus === "pending" && value.evidence === "action_pending") ||
      (value.resultStatus === "skipped" && value.evidence === "action_cancelled");
    if ((actionRequired || actionOptional) !== (value.actionId !== null)) {
      context.addIssue({ code: "custom", message: "Invalid target result Action scope" });
    }
  });

const snapshotSchema = z
  .object({
    taskId: uuidSchema,
    taskStatus: z.enum(TASK_STATUSES),
    taskRevision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    lastEventSequence: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER),
    items: z.array(itemSchema).max(100),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.lastEventSequence > value.taskRevision) {
      context.addIssue({ code: "custom", message: "Invalid target result watermark" });
    }
    const ordinals = value.items.map((item) => item.ordinal);
    const targetIds = value.items.map((item) => item.targetId);
    if (
      ordinals.some((ordinal, index) => index > 0 && ordinal <= ordinals[index - 1]!) ||
      new Set(targetIds).size !== targetIds.length
    ) {
      context.addIssue({ code: "custom", message: "Invalid target result ordering" });
    }
  });

export type TaskTargetResultStatus = z.infer<typeof statusSchema>;
export type TaskTargetResultEvidence = z.infer<typeof evidenceSchema>;
export type TaskTargetResultItem = z.infer<typeof itemSchema>;
export type TaskTargetResultSnapshot = z.infer<typeof snapshotSchema>;

export interface TaskTargetResultRequestOptions {
  readonly signal?: AbortSignal;
}

export interface TaskTargetResultSource {
  getResults(
    taskId: string,
    options?: TaskTargetResultRequestOptions,
  ): Promise<TaskTargetResultSnapshot>;
}

export type TaskTargetResultSourceErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "installation_access_denied"
  | "request_cancelled";

const PUBLIC_MESSAGES: Record<TaskTargetResultSourceErrorCode, string> = {
  transport_unavailable: "Task target results are unavailable",
  protocol_mismatch: "Task target result protocol is incompatible",
  installation_access_denied: "Installation access is unavailable",
  request_cancelled: "Task target result request was cancelled",
};

export class TaskTargetResultSourceError extends Error {
  readonly code: TaskTargetResultSourceErrorCode;
  readonly retryable: boolean;

  constructor(code: TaskTargetResultSourceErrorCode, retryable: boolean) {
    super(PUBLIC_MESSAGES[code]);
    this.name = "TaskTargetResultSourceError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function parseTaskTargetResultSnapshot(value: unknown): TaskTargetResultSnapshot {
  const parsed = snapshotSchema.safeParse(value);
  if (!parsed.success) {
    throw new TaskTargetResultSourceError("protocol_mismatch", false);
  }
  return parsed.data;
}

export const taskTargetResultKeys = {
  all: ["task-target-results"] as const,
  detail: (taskId: string) => [...taskTargetResultKeys.all, taskId] as const,
  eventSnapshot: (taskId: string, eventSequence: number) =>
    [...taskTargetResultKeys.detail(taskId), eventSequence] as const,
};

export function taskTargetResultQueryOptions(
  source: TaskTargetResultSource,
  taskId: string,
  eventSequence = 0,
) {
  return queryOptions({
    queryKey: taskTargetResultKeys.eventSnapshot(taskId, eventSequence),
    queryFn: ({ signal }) => source.getResults(taskId, { signal }),
    retry: false,
    staleTime: 0,
  });
}
