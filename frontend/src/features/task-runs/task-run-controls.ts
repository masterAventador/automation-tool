import { z } from "zod";

const canonicalUuidV4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const canonicalIdempotencyKey = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

export const TASK_RUN_CONTROL_OPERATIONS = [
  "pause",
  "resume",
  "cancel",
  "emergency_stop",
] as const;

export type TaskRunControlOperation = (typeof TASK_RUN_CONTROL_OPERATIONS)[number];

const COMMAND_TYPE_BY_OPERATION = {
  pause: "task.pause",
  resume: "task.resume",
  cancel: "task.cancel",
  emergency_stop: "task.emergency_stop",
} as const satisfies Record<TaskRunControlOperation, string>;

const taskRunControlReceiptSchema = z
  .object({
    commandId: z.string().regex(canonicalUuidV4),
    taskId: z.string().regex(canonicalUuidV4),
    executionAttemptId: z.string().regex(canonicalUuidV4),
    sequence: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    commandType: z.enum([
      "task.pause",
      "task.resume",
      "task.cancel",
      "task.emergency_stop",
    ]),
    status: z.enum(["pending", "in_flight", "delivered", "acknowledged", "expired"]),
  })
  .strict();

export type TaskRunControlReceipt = z.infer<typeof taskRunControlReceiptSchema>;

export interface TaskRunControlRequestOptions {
  readonly signal?: AbortSignal;
}

export interface TaskRunControlGateway {
  pauseTask(
    taskId: string,
    idempotencyKey: string,
    options?: TaskRunControlRequestOptions,
  ): Promise<TaskRunControlReceipt>;
  resumeTask(
    taskId: string,
    idempotencyKey: string,
    options?: TaskRunControlRequestOptions,
  ): Promise<TaskRunControlReceipt>;
  cancelTask(
    taskId: string,
    idempotencyKey: string,
    options?: TaskRunControlRequestOptions,
  ): Promise<TaskRunControlReceipt>;
  emergencyStopTask(
    taskId: string,
    idempotencyKey: string,
    options?: TaskRunControlRequestOptions,
  ): Promise<TaskRunControlReceipt>;
}

export type TaskRunControlGatewayErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "installation_access_denied"
  | "control_rejected"
  | "outcome_uncertain"
  | "request_cancelled";

const PUBLIC_MESSAGES: Record<TaskRunControlGatewayErrorCode, string> = {
  transport_unavailable: "Task control service is unavailable",
  protocol_mismatch: "Task control protocol is incompatible",
  installation_access_denied: "Installation access is unavailable",
  control_rejected: "Task control request was rejected",
  outcome_uncertain: "Task control outcome is uncertain",
  request_cancelled: "Task control request was cancelled",
};

export class TaskRunControlGatewayError extends Error {
  readonly code: TaskRunControlGatewayErrorCode;
  readonly retryable: boolean;

  constructor(code: TaskRunControlGatewayErrorCode, retryable: boolean) {
    super(PUBLIC_MESSAGES[code]);
    this.name = "TaskRunControlGatewayError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function validateTaskRunControlInput(
  taskId: string,
  idempotencyKey: string,
  operation: TaskRunControlOperation,
): void {
  if (
    !canonicalUuidV4.test(taskId) ||
    !canonicalIdempotencyKey.test(idempotencyKey) ||
    !(operation in COMMAND_TYPE_BY_OPERATION)
  ) {
    throw new TaskRunControlGatewayError("protocol_mismatch", false);
  }
}

export function parseTaskRunControlReceipt(
  value: unknown,
  taskId: string,
  operation: TaskRunControlOperation,
): TaskRunControlReceipt {
  const parsed = taskRunControlReceiptSchema.safeParse(value);
  if (
    !parsed.success ||
    parsed.data.taskId !== taskId ||
    parsed.data.commandType !== COMMAND_TYPE_BY_OPERATION[operation]
  ) {
    throw new TaskRunControlGatewayError("protocol_mismatch", false);
  }
  return parsed.data;
}
