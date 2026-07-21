import { z } from "zod";

import { TASK_STATUSES } from "../../api/control-plane/task-projections";

const canonicalUuidV4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const canonicalIdempotencyKey = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

const taskDiscoveryReceiptSchema = z
  .object({
    taskId: z.string().regex(canonicalUuidV4),
    taskStatus: z.enum(TASK_STATUSES),
    taskRevision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    lastEventSequence: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    commandId: z.string().regex(canonicalUuidV4),
    executionAttemptId: z.string().regex(canonicalUuidV4),
    commandStatus: z.enum([
      "pending",
      "in_flight",
      "delivered",
      "acknowledged",
      "rejected",
      "expired",
    ]),
  })
  .strict()
  .refine((value) => value.lastEventSequence <= value.taskRevision);

export type TaskDiscoveryReceipt = z.infer<typeof taskDiscoveryReceiptSchema>;

export interface TaskDiscoveryRequestOptions {
  readonly signal?: AbortSignal;
}

export interface TaskDiscoveryGateway {
  startDiscovery(
    taskId: string,
    idempotencyKey: string,
    options?: TaskDiscoveryRequestOptions,
  ): Promise<TaskDiscoveryReceipt>;
}

export type TaskDiscoveryGatewayErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "installation_access_denied"
  | "discovery_rejected"
  | "request_cancelled";

const PUBLIC_MESSAGES: Record<TaskDiscoveryGatewayErrorCode, string> = {
  transport_unavailable: "Task discovery service is unavailable",
  protocol_mismatch: "Task discovery protocol is incompatible",
  installation_access_denied: "Installation access is unavailable",
  discovery_rejected: "Task discovery request was rejected",
  request_cancelled: "Task discovery request was cancelled",
};

export class TaskDiscoveryGatewayError extends Error {
  readonly code: TaskDiscoveryGatewayErrorCode;
  readonly retryable: boolean;

  constructor(code: TaskDiscoveryGatewayErrorCode, retryable: boolean) {
    super(PUBLIC_MESSAGES[code]);
    this.name = "TaskDiscoveryGatewayError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function validateTaskDiscoveryInput(taskId: string, idempotencyKey: string): void {
  if (!canonicalUuidV4.test(taskId) || !canonicalIdempotencyKey.test(idempotencyKey)) {
    throw new TaskDiscoveryGatewayError("protocol_mismatch", false);
  }
}

export function parseTaskDiscoveryReceipt(
  value: unknown,
  taskId: string,
): TaskDiscoveryReceipt {
  const parsed = taskDiscoveryReceiptSchema.safeParse(value);
  if (!parsed.success || parsed.data.taskId !== taskId) {
    throw new TaskDiscoveryGatewayError("protocol_mismatch", false);
  }
  return parsed.data;
}
