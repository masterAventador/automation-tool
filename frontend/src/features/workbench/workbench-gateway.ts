import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

const canonicalUuidV4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const canonicalUtcTimestamp =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;
const idempotencyKey = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

function isCanonicalUtcTimestamp(value: string): boolean {
  const match = canonicalUtcTimestamp.exec(value);
  if (match === null) return false;
  const [, year, month, day, hour, minute, second, fraction = ""] = match;
  const parts = [year, month, day, hour, minute, second].map(Number);
  const [yearValue, monthValue, dayValue, hourValue, minuteValue, secondValue] = parts;
  if (
    yearValue === undefined ||
    yearValue < 1 ||
    monthValue === undefined ||
    dayValue === undefined ||
    hourValue === undefined ||
    minuteValue === undefined ||
    secondValue === undefined
  ) {
    return false;
  }
  const parsed = new Date(0);
  parsed.setUTCFullYear(yearValue, monthValue - 1, dayValue);
  parsed.setUTCHours(
    hourValue,
    minuteValue,
    secondValue,
    Number(fraction.padEnd(3, "0").slice(0, 3)),
  );
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.getUTCFullYear() === yearValue &&
    parsed.getUTCMonth() === monthValue - 1 &&
    parsed.getUTCDate() === dayValue &&
    parsed.getUTCHours() === hourValue &&
    parsed.getUTCMinutes() === minuteValue &&
    parsed.getUTCSeconds() === secondValue
  );
}

const workbenchRuntimeStatusSchema = z
  .object({
    controlPlaneStatus: z.literal("ready"),
    executorStatus: z.enum(["online", "offline"]),
    executorLastHeartbeatAt: z
      .string()
      .refine(isCanonicalUtcTimestamp)
      .nullable(),
  })
  .strict()
  .refine(
    (value) =>
      (value.executorStatus === "online") ===
      (value.executorLastHeartbeatAt !== null),
  );

const metricCount = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const workbenchMetricsSchema = z
  .object({
    version: z.literal("workbench.metrics.v1"),
    tasks: z
      .object({
        total: metricCount,
        succeeded: metricCount,
        failed: metricCount,
        handoffRequired: metricCount,
        outcomeUncertain: metricCount,
      })
      .strict(),
    actions: z
      .object({
        total: metricCount,
        succeeded: metricCount,
        failed: metricCount,
        outcomeUncertain: metricCount,
      })
      .strict(),
  })
  .strict()
  .refine(
    (value) =>
      value.tasks.succeeded +
        value.tasks.failed +
        value.tasks.handoffRequired +
        value.tasks.outcomeUncertain <=
        value.tasks.total &&
      value.actions.succeeded + value.actions.failed + value.actions.outcomeUncertain <=
        value.actions.total,
  );

const emergencyStopReceiptSchema = z
  .object({
    commandId: z.string().regex(canonicalUuidV4),
    taskId: z.string().regex(canonicalUuidV4),
    executionAttemptId: z.string().regex(canonicalUuidV4),
    sequence: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    commandType: z.literal("task.emergency_stop"),
    status: z.enum(["pending", "in_flight", "delivered", "acknowledged", "expired"]),
  })
  .strict();

export type WorkbenchRuntimeStatus = z.infer<typeof workbenchRuntimeStatusSchema>;
export type WorkbenchMetrics = z.infer<typeof workbenchMetricsSchema>;
export type EmergencyStopReceipt = z.infer<typeof emergencyStopReceiptSchema>;

export interface WorkbenchRequestOptions {
  readonly signal?: AbortSignal;
}

export interface WorkbenchGateway {
  getRuntimeStatus(options?: WorkbenchRequestOptions): Promise<WorkbenchRuntimeStatus>;
  getMetrics(options?: WorkbenchRequestOptions): Promise<WorkbenchMetrics>;
  emergencyStopTask(
    taskId: string,
    idempotencyKey: string,
    options?: WorkbenchRequestOptions,
  ): Promise<EmergencyStopReceipt>;
}

export type WorkbenchGatewayErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "installation_access_denied"
  | "outcome_uncertain"
  | "request_cancelled";

const PUBLIC_ERROR_MESSAGES: Record<WorkbenchGatewayErrorCode, string> = {
  transport_unavailable: "Workbench service is unavailable",
  protocol_mismatch: "Workbench protocol is incompatible",
  installation_access_denied: "Installation access is unavailable",
  outcome_uncertain: "Emergency stop outcome is uncertain",
  request_cancelled: "Workbench request was cancelled",
};

export class WorkbenchGatewayError extends Error {
  readonly code: WorkbenchGatewayErrorCode;
  readonly retryable: boolean;

  constructor(code: WorkbenchGatewayErrorCode, retryable: boolean) {
    super(PUBLIC_ERROR_MESSAGES[code]);
    this.name = "WorkbenchGatewayError";
    this.code = code;
    this.retryable = retryable;
  }
}

function parseValue<T>(schema: z.ZodType<T>, value: unknown): T {
  const parsed = schema.safeParse(value);
  if (!parsed.success) {
    throw new WorkbenchGatewayError("protocol_mismatch", false);
  }
  return parsed.data;
}

export function parseWorkbenchRuntimeStatus(value: unknown): WorkbenchRuntimeStatus {
  return parseValue(workbenchRuntimeStatusSchema, value);
}

export function parseWorkbenchMetrics(value: unknown): WorkbenchMetrics {
  return parseValue(workbenchMetricsSchema, value);
}

export function parseEmergencyStopReceipt(value: unknown): EmergencyStopReceipt {
  return parseValue(emergencyStopReceiptSchema, value);
}

export function validateEmergencyStopInput(taskId: string, key: string): void {
  if (!canonicalUuidV4.test(taskId) || !idempotencyKey.test(key)) {
    throw new WorkbenchGatewayError("protocol_mismatch", false);
  }
}

export const workbenchKeys = {
  all: ["workbench"] as const,
  runtimeStatus: () => [...workbenchKeys.all, "runtime-status"] as const,
  metrics: () => [...workbenchKeys.all, "metrics"] as const,
};

export function workbenchRuntimeStatusQueryOptions(gateway: WorkbenchGateway) {
  return queryOptions({
    queryKey: workbenchKeys.runtimeStatus(),
    queryFn: ({ signal }) => gateway.getRuntimeStatus({ signal }),
    retry: false,
    staleTime: 5_000,
    refetchInterval: 1_000,
    refetchIntervalInBackground: true,
  });
}

export function workbenchMetricsQueryOptions(gateway: WorkbenchGateway) {
  return queryOptions({
    queryKey: workbenchKeys.metrics(),
    queryFn: ({ signal }) => gateway.getMetrics({ signal }),
    retry: false,
    staleTime: 10_000,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  });
}
