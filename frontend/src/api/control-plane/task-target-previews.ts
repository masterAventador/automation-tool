import { z } from "zod";

import {
  douyinActionMessageTemplateSchema,
  douyinSearchExposureActionSchema,
} from "./douyin-search-exposure";

const canonicalUuidV4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const canonicalUtcTimestamp =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;
const safeInteger = z.number().int().min(1).max(Number.MAX_SAFE_INTEGER);
const uuid = z.string().regex(canonicalUuidV4);
const timestamp = z.string().regex(canonicalUtcTimestamp);
const publicHandle = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/);
const displayName = z
  .string()
  .min(1)
  .max(80)
  .refine(
    (value) =>
      value.trim() === value &&
      !Array.from(value).some((character) => {
        const point = character.codePointAt(0);
        return (
          point !== undefined &&
          (point < 0x20 ||
            point === 0x7f ||
            (point >= 0x202a && point <= 0x202e) ||
            (point >= 0x2066 && point <= 0x2069))
        );
      }) &&
      !/(?:bearer\s|file:\/\/|data:|(?:cookie|credential|password|secret|token)\s*[:=])/i.test(
        value,
      ),
  );

export const TASK_TARGET_DISPOSITIONS = [
  "eligible",
  "duplicate_in_task",
  "duplicate_in_history",
  "blacklisted",
] as const;

const previewItem = z
  .object({
    targetId: uuid,
    ordinal: z.number().int().min(1).max(100),
    displayName,
    publicHandle: publicHandle.nullable(),
    source: z.literal("general_search_author"),
    disposition: z.enum(TASK_TARGET_DISPOSITIONS),
    userExcluded: z.boolean(),
    selected: z.boolean(),
  })
  .strict()
  .refine(
    (value) =>
      (!value.userExcluded || value.disposition === "eligible") &&
      value.selected === (value.disposition === "eligible" && !value.userExcluded),
  );

const taskTargetPreview = z
  .object({
    taskId: uuid,
    taskStatus: z.enum([
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
    ]),
    taskRevision: safeInteger,
    confirmationRevision: safeInteger,
    lastEventSequence: safeInteger,
    pageRevision: safeInteger,
    action: douyinSearchExposureActionSchema,
    messageTemplate: douyinActionMessageTemplateSchema.nullable(),
    selectedTargetCount: z.number().int().min(0).max(100),
    userExcludedTargetCount: z.number().int().min(0).max(100),
    confirmed: z.boolean(),
    confirmedAt: timestamp.nullable(),
    items: z.array(previewItem).max(100),
    nextCursor: z
      .string()
      .min(1)
      .max(512)
      .regex(/^[A-Za-z0-9_-]+$/)
      .nullable(),
  })
  .strict()
  .superRefine((value, context) => {
    if (
      value.selectedTargetCount + value.userExcludedTargetCount > 100 ||
      (value.action === "browse" && value.messageTemplate !== null) ||
      (value.action !== "browse" && value.messageTemplate === null) ||
      value.confirmed !== (value.confirmedAt !== null) ||
      (!value.confirmed && value.taskStatus !== "awaiting_confirmation") ||
      (!value.confirmed && value.confirmationRevision !== value.taskRevision) ||
      (value.confirmed &&
        ["draft", "validating", "discovering_targets", "awaiting_confirmation"].includes(
          value.taskStatus,
        )) ||
      (value.confirmed && value.selectedTargetCount === 0) ||
      (value.confirmed && value.confirmationRevision >= value.taskRevision) ||
      (value.items.length === 0 && value.nextCursor !== null)
    ) {
      context.addIssue({ code: "custom", message: "Invalid target preview state" });
    }
    const seen = new Set<string>();
    let previous: readonly [number, string] | undefined;
    for (const item of value.items) {
      const current = [item.ordinal, item.targetId] as const;
      if (
        seen.has(item.targetId) ||
        (previous !== undefined &&
          (current[0] < previous[0] ||
            (current[0] === previous[0] && current[1] <= previous[1])))
      ) {
        context.addIssue({ code: "custom", message: "Invalid target preview order" });
        return;
      }
      seen.add(item.targetId);
      previous = current;
    }
  });

export type TaskTargetPreview = z.infer<typeof taskTargetPreview>;

export interface TaskTargetPreviewOptions {
  readonly signal?: AbortSignal;
}

export interface TaskTargetPreviewListRequest extends TaskTargetPreviewOptions {
  readonly taskId: string;
  readonly cursor?: string | null;
  readonly limit: number;
}

export interface TaskTargetExclusionsRequest extends TaskTargetPreviewOptions {
  readonly taskId: string;
  readonly pageRevision: number;
  readonly expectedTaskRevision: number;
  readonly excludedTargetIds: readonly string[];
  readonly idempotencyKey: string;
}

export interface TaskTargetConfirmationRequest extends TaskTargetPreviewOptions {
  readonly taskId: string;
  readonly pageRevision: number;
  readonly confirmationRevision: number;
  readonly idempotencyKey: string;
}

export interface TaskTargetPreviewSource {
  getPreview(request: TaskTargetPreviewListRequest): Promise<TaskTargetPreview>;
  replaceExclusions(request: TaskTargetExclusionsRequest): Promise<TaskTargetPreview>;
  confirm(request: TaskTargetConfirmationRequest): Promise<TaskTargetPreview>;
}

export type TaskTargetPreviewSourceErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "installation_access_denied"
  | "preview_stale"
  | "request_cancelled";

const PUBLIC_MESSAGES: Record<TaskTargetPreviewSourceErrorCode, string> = {
  transport_unavailable: "Target preview service is unavailable",
  protocol_mismatch: "Target preview protocol is incompatible",
  installation_access_denied: "Installation access is unavailable",
  preview_stale: "Target preview changed; refresh and try again",
  request_cancelled: "Target preview request was cancelled",
};

export class TaskTargetPreviewSourceError extends Error {
  readonly code: TaskTargetPreviewSourceErrorCode;
  readonly retryable: boolean;

  constructor(code: TaskTargetPreviewSourceErrorCode, retryable: boolean) {
    super(PUBLIC_MESSAGES[code]);
    this.name = "TaskTargetPreviewSourceError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function parseTaskTargetPreview(value: unknown): TaskTargetPreview {
  const parsed = taskTargetPreview.safeParse(value);
  if (!parsed.success) {
    throw new TaskTargetPreviewSourceError("protocol_mismatch", false);
  }
  return parsed.data;
}
