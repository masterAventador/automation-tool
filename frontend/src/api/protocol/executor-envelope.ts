import { z } from "zod";

const MAX_MESSAGE_BYTES = 32 * 1024;
const MAX_PAYLOAD_BYTES = 16 * 1024;
const MAX_PAYLOAD_DEPTH = 8;
const MAX_COLLECTION_ITEMS = 64;
const MAX_STRING_LENGTH = 4096;
const canonicalUuidV4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const idempotencyKey = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const utcTimestamp =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;
const inlineDataUri = /\bdata:[a-z0-9.+-]+\/[a-z0-9.+-]+[^,]*,/i;
const sensitiveAssignment =
  /(?:^|[^a-z0-9_])(?:access[_-]?token|api[_-]?key|authorization|cookie|credential|password|private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)\s*[:=]/i;
const privatePosixPath = /(?:^|[\s"'=])\/(?:users|home|root|tmp|var\/folders)(?:\/|$)/i;
const windowsAbsolutePath = /(?:^|[\s"'=])[a-z]:[\\/]/i;
const sensitiveNames = new Set([
  "access_token",
  "api_key",
  "authorization",
  "captcha_code",
  "cookie",
  "cookies",
  "credential",
  "credentials",
  "file_path",
  "image",
  "image_data",
  "inline_image",
  "inline_screenshot",
  "local_path",
  "otp",
  "password",
  "private_key",
  "refresh_token",
  "screenshot",
  "secret",
  "secrets",
  "session_cookie",
  "token",
  "tokens",
  "verification_code",
]);
const sensitiveSegments = new Set([
  "cookie",
  "cookies",
  "credential",
  "credentials",
  "password",
  "secret",
  "secrets",
  "token",
  "tokens",
]);

function parseCanonicalUtcTimestamp(value: string): bigint | null {
  const match = utcTimestamp.exec(value);
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
  const milliseconds = Number(paddedFraction.slice(0, 3));
  const parsed = new Date(0);
  parsed.setUTCFullYear(yearValue, monthValue - 1, dayValue);
  parsed.setUTCHours(hourValue, minuteValue, secondValue, milliseconds);
  if (
    Number.isFinite(parsed.getTime()) &&
    parsed.getUTCFullYear() === yearValue &&
    parsed.getUTCMonth() === monthValue - 1 &&
    parsed.getUTCDate() === dayValue &&
    parsed.getUTCHours() === hourValue &&
    parsed.getUTCMinutes() === minuteValue &&
    parsed.getUTCSeconds() === secondValue
  ) {
    return BigInt(parsed.getTime()) * 1000n + BigInt(paddedFraction.slice(3));
  }
  return null;
}

function normalizedPayloadName(value: string): string {
  return value
    .replace(/(?<=[a-z0-9])(?=[A-Z])/g, "_")
    .replace(/[.-]+/g, "_")
    .toLowerCase();
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

function unsafePayloadString(value: string): boolean {
  const folded = value.toLowerCase();
  return (
    Array.from(value).length > MAX_STRING_LENGTH ||
    containsControlOrBidi(value) ||
    folded.includes("bearer ") ||
    folded.includes("file://") ||
    sensitiveAssignment.test(value) ||
    inlineDataUri.test(value) ||
    privatePosixPath.test(value) ||
    windowsAbsolutePath.test(value)
  );
}

function validatePayloadValue(value: unknown, depth: number): boolean {
  if (depth > MAX_PAYLOAD_DEPTH) {
    return false;
  }
  if (Array.isArray(value)) {
    return (
      value.length <= MAX_COLLECTION_ITEMS &&
      value.every((item) => validatePayloadValue(item, depth + 1))
    );
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value);
    return (
      entries.length <= MAX_COLLECTION_ITEMS &&
      entries.every(([key, child]) => {
        const normalized = normalizedPayloadName(key);
        const segments = normalized.split("_");
        return (
          key.length > 0 &&
          Array.from(key).length <= 128 &&
          !containsControlOrBidi(key) &&
          !sensitiveNames.has(normalized) &&
          !segments.some((segment) => sensitiveSegments.has(segment)) &&
          validatePayloadValue(child, depth + 1)
        );
      })
    );
  }
  if (typeof value === "string") {
    return !unsafePayloadString(value);
  }
  return typeof value !== "number" || Number.isFinite(value);
}

const payloadSchema = z.record(z.string(), z.json()).superRefine((payload, context) => {
  const encodedLength = new TextEncoder().encode(JSON.stringify(payload)).byteLength;
  if (!validatePayloadValue(payload, 0) || encodedLength > MAX_PAYLOAD_BYTES) {
    context.addIssue({ code: "custom", message: "Invalid Executor payload" });
  }
});
const timestampSchema = z.string().refine((value) => parseCanonicalUtcTimestamp(value) !== null);
const commonEnvelope = z
  .object({
    protocol_version: z.literal("1.0"),
    message_id: z.string().regex(canonicalUuidV4),
    sent_at: timestampSchema,
    deadline_at: timestampSchema,
    installation_id: z.string().regex(canonicalUuidV4),
    executor_id: z.string().regex(canonicalUuidV4),
    correlation_id: z.string().regex(canonicalUuidV4),
    idempotency_key: z.string().regex(idempotencyKey),
    sequence: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    payload: payloadSchema,
  })
  .strict();
const taskScope = {
  task_id: z.string().regex(canonicalUuidV4),
  execution_attempt_id: z.string().regex(canonicalUuidV4),
};
const lifecycleEnvelope = commonEnvelope.extend({
  message_type: z.enum(["executor.hello", "executor.heartbeat"]),
});
const platformSessionHealthEnvelope = commonEnvelope.extend({
  message_type: z.literal("platform.session_health"),
  payload: z
    .object({
      platform: z.literal("douyin"),
      state: z.enum(["healthy", "expired", "missing", "risk", "unknown"]),
      session_revision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
      observed_at: timestampSchema,
    })
    .strict(),
});
const taskCommandEnvelope = commonEnvelope.extend({
  ...taskScope,
  message_type: z.enum([
    "task.offer",
    "task.pause",
    "task.resume",
    "task.cancel",
    "task.emergency_stop",
  ]),
});
const discoverySequence = z.number().int().min(1).max(Number.MAX_SAFE_INTEGER);
const discoverySafeText = (maximum: number) =>
  z
    .string()
    .min(1)
    .max(maximum)
    .refine((value) => value.trim() === value && !unsafePayloadString(value));
const platformIdentifier = (maximum: number) =>
  z
    .string()
    .min(1)
    .max(maximum)
    .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]*$/);
const discoveryCandidate = z
  .object({
    candidate_version: z.literal("douyin.candidate.v1"),
    platform_target_id: platformIdentifier(128),
    display_name: discoverySafeText(80),
    public_handle: platformIdentifier(64).nullable(),
    source: z.literal("general_search_author"),
    page_revision: discoverySequence,
  })
  .strict();
const taskDiscoveryCommandEnvelope = commonEnvelope.extend({
  ...taskScope,
  message_type: z.literal("task.discover"),
  payload: z
    .object({
      discovery_version: z.literal("douyin.discovery.v1"),
      keyword: discoverySafeText(80),
      target_limit: z.number().int().min(1).max(100),
      page_revision: discoverySequence,
    })
    .strict(),
});
const taskDiscoveryBatchEnvelope = commonEnvelope.extend({
  ...taskScope,
  message_type: z.literal("task.discovery_batch"),
  payload: z
    .object({
      discovery_version: z.literal("douyin.discovery.v1"),
      page_revision: discoverySequence,
      batch_index: z.number().int().min(1).max(10),
      batch_count: z.number().int().min(1).max(10),
      candidates: z.array(discoveryCandidate).min(1).max(10),
    })
    .strict()
    .refine(
      (payload) =>
        payload.batch_index <= payload.batch_count &&
        payload.candidates.every(
          (candidate) => candidate.page_revision === payload.page_revision,
        ),
    ),
});
const taskDiscoveryCompletedEnvelope = commonEnvelope.extend({
  ...taskScope,
  message_type: z.literal("task.discovery_completed"),
  payload: z
    .object({
      discovery_version: z.literal("douyin.discovery.v1"),
      outcome: z.enum(["completed", "login_required", "handoff_required", "failed"]),
      evidence: z.enum([
        "candidates_extracted",
        "login_required",
        "blocking_dialog",
        "no_candidates",
        "navigation_timed_out",
        "home_ready_timed_out",
        "action_timed_out",
        "result_url_timed_out",
        "results_ready_timed_out",
        "page_version_unknown",
        "conflicting_anchors",
        "results_unavailable",
        "privacy_rejected",
        "result_count_decreased",
        "cancellation_unavailable",
        "cancellation_requested",
        "page_unavailable",
      ]),
      page_revision: discoverySequence,
      batch_count: z.number().int().min(0).max(10),
      candidate_count: z.number().int().min(0).max(100),
    })
    .strict()
    .refine((payload) => {
      const evidenceMatches =
        (payload.outcome === "completed" && payload.evidence === "candidates_extracted") ||
        (payload.outcome === "login_required" && payload.evidence === "login_required") ||
        (payload.outcome === "handoff_required" && payload.evidence === "blocking_dialog") ||
        (payload.outcome === "failed" &&
          !["candidates_extracted", "login_required", "blocking_dialog"].includes(
            payload.evidence,
          ));
      if (!evidenceMatches) {
        return false;
      }
      if (payload.outcome !== "completed") {
        return payload.batch_count === 0 && payload.candidate_count === 0;
      }
      return (
        payload.candidate_count > 0 &&
        payload.batch_count === Math.ceil(payload.candidate_count / 10)
      );
    }),
});
const taskCommandResultEnvelope = commonEnvelope.extend({
  ...taskScope,
  message_type: z.enum(["task.accept", "task.reject", "task.control_ack"]),
});
const taskEventEnvelope = commonEnvelope.extend({
  ...taskScope,
  message_type: z.enum([
    "task.started",
    "step.started",
    "step.progress",
    "step.completed",
    "step.failed",
    "session.login_required",
    "handoff.requested",
    "task.paused",
    "task.resumed",
    "task.cancelled",
    "task.completed",
    "task.partially_completed",
    "task.failed",
    "task.outcome_uncertain",
  ]),
});
const executorEnvelopeSchema = z
  .discriminatedUnion("message_type", [
    lifecycleEnvelope,
    platformSessionHealthEnvelope,
    taskCommandEnvelope,
    taskDiscoveryCommandEnvelope,
    taskCommandResultEnvelope,
    taskDiscoveryBatchEnvelope,
    taskDiscoveryCompletedEnvelope,
    taskEventEnvelope,
  ])
  .superRefine((message, context) => {
    const sentAt = parseCanonicalUtcTimestamp(message.sent_at);
    const deadlineAt = parseCanonicalUtcTimestamp(message.deadline_at);
    if (sentAt === null || deadlineAt === null || deadlineAt <= sentAt) {
      context.addIssue({ code: "custom", message: "Invalid Executor deadline" });
    }
    if (message.message_type === "platform.session_health") {
      const observedAt = parseCanonicalUtcTimestamp(message.payload.observed_at);
      if (observedAt === null || sentAt === null || observedAt > sentAt) {
        context.addIssue({ code: "custom", message: "Invalid Session observation time" });
      }
    }
  });

export type ExecutorEnvelope = z.infer<typeof executorEnvelopeSchema>;

export class ExecutorProtocolError extends Error {
  constructor() {
    super("Invalid Executor protocol message");
    this.name = "ExecutorProtocolError";
  }
}

function assertNoDuplicateObjectKeys(source: string): void {
  let index = 0;
  const skipWhitespace = () => {
    while (/\s/u.test(source[index] ?? "")) {
      index += 1;
    }
  };
  const parseString = (): string => {
    const start = index;
    index += 1;
    while (index < source.length) {
      const character = source[index];
      if (character === "\\") {
        index += 2;
      } else if (character === '"') {
        index += 1;
        return JSON.parse(source.slice(start, index)) as string;
      } else {
        index += 1;
      }
    }
    throw new Error("unterminated string");
  };
  const parseValue = (): void => {
    skipWhitespace();
    const character = source[index];
    if (character === "{") {
      index += 1;
      skipWhitespace();
      const keys = new Set<string>();
      if (source[index] === "}") {
        index += 1;
        return;
      }
      while (index < source.length) {
        if (source[index] !== '"') {
          throw new Error("invalid object key");
        }
        const key = parseString();
        if (keys.has(key)) {
          throw new Error("duplicate object key");
        }
        keys.add(key);
        skipWhitespace();
        if (source[index] !== ":") {
          throw new Error("missing object colon");
        }
        index += 1;
        parseValue();
        skipWhitespace();
        if (source[index] === "}") {
          index += 1;
          return;
        }
        if (source[index] !== ",") {
          throw new Error("invalid object separator");
        }
        index += 1;
        skipWhitespace();
      }
      throw new Error("unterminated object");
    }
    if (character === "[") {
      index += 1;
      skipWhitespace();
      if (source[index] === "]") {
        index += 1;
        return;
      }
      while (index < source.length) {
        parseValue();
        skipWhitespace();
        if (source[index] === "]") {
          index += 1;
          return;
        }
        if (source[index] !== ",") {
          throw new Error("invalid array separator");
        }
        index += 1;
      }
      throw new Error("unterminated array");
    }
    if (character === '"') {
      parseString();
      return;
    }
    const start = index;
    while (index < source.length && !/[\s,}\]]/u.test(source[index] ?? "")) {
      index += 1;
    }
    if (index === start) {
      throw new Error("invalid JSON value");
    }
  };

  parseValue();
  skipWhitespace();
  if (index !== source.length) {
    throw new Error("trailing JSON content");
  }
}

export function parseExecutorMessage(source: string): ExecutorEnvelope {
  try {
    if (
      typeof source !== "string" ||
      new TextEncoder().encode(source).byteLength > MAX_MESSAGE_BYTES
    ) {
      throw new Error("invalid message size");
    }
    assertNoDuplicateObjectKeys(source);
    return executorEnvelopeSchema.parse(JSON.parse(source) as unknown);
  } catch {
    throw new ExecutorProtocolError();
  }
}
