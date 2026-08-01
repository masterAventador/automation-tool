import { invoke } from "@tauri-apps/api/core";

import {
  SmartEditGatewayError,
  smartEditGenerationRequestSchema,
  smartEditGenerationSnapshotSchema,
  type SmartEditGateway,
  type SmartEditGatewayErrorCode,
  type SmartEditGenerationRequest,
  type SmartEditGenerationSnapshot,
  type SmartEditPollOptions,
} from "../../features/video-editing/smart-edit-gateway";
import { editingResourceIdSchema } from "../../features/video-editing/video-editing-dto";
import { nativeCommandErrorFields } from "./native-command-error";

const POLL_INTERVAL_MS = 250;
const MAX_POLL_ATTEMPTS = 14_400;
const NATIVE_ERROR_CODES: ReadonlySet<SmartEditGatewayErrorCode> = new Set([
  "invalid_request",
  "generation_not_found",
  "generation_not_cancellable",
  "storage_unavailable",
  "operation_unavailable",
]);

function operationUnavailable(): SmartEditGatewayError {
  return new SmartEditGatewayError("operation_unavailable", false);
}

function mapNativeError(value: unknown): SmartEditGatewayError {
  const fields = nativeCommandErrorFields(value);
  if (
    fields !== undefined &&
    fields.retryable === false &&
    NATIVE_ERROR_CODES.has(fields.code as SmartEditGatewayErrorCode)
  ) {
    return new SmartEditGatewayError(
      fields.code as SmartEditGatewayErrorCode,
      false,
    );
  }
  return operationUnavailable();
}

async function safeInvoke(
  command: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  try {
    return await invoke<unknown>(command, args);
  } catch (error) {
    throw mapNativeError(error);
  }
}

function requireGenerationId(value: string): string {
  const parsed = editingResourceIdSchema.safeParse(value);
  if (!parsed.success) {
    throw new SmartEditGatewayError("invalid_request", false);
  }
  return parsed.data;
}

function parseSnapshot(
  value: unknown,
  expectedGenerationId?: string,
): SmartEditGenerationSnapshot {
  const parsed = smartEditGenerationSnapshotSchema.safeParse(value);
  if (
    !parsed.success ||
    (expectedGenerationId !== undefined &&
      parsed.data.generationId !== expectedGenerationId)
  ) {
    throw operationUnavailable();
  }
  return parsed.data;
}

function pollingCancelled(): SmartEditGatewayError {
  return new SmartEditGatewayError("polling_cancelled", false);
}

function waitForNextPoll(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted === true) {
    return Promise.reject(pollingCancelled());
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(pollingCancelled());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, POLL_INTERVAL_MS);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function isTerminal(snapshot: SmartEditGenerationSnapshot): boolean {
  return (
    snapshot.status === "succeeded" ||
    snapshot.status === "failed" ||
    snapshot.status === "cancelled"
  );
}

export class TauriSmartEditGateway implements SmartEditGateway {
  async start(
    request: SmartEditGenerationRequest,
  ): Promise<SmartEditGenerationSnapshot> {
    const parsedRequest = smartEditGenerationRequestSchema.safeParse(request);
    if (!parsedRequest.success) {
      throw new SmartEditGatewayError("invalid_request", false);
    }
    const snapshot = parseSnapshot(
      await safeInvoke("start_smart_edit_generation", {
        request: parsedRequest.data,
      }),
    );
    if (
      snapshot.projectId !== parsedRequest.data.projectId ||
      snapshot.mode !== parsedRequest.data.mode ||
      snapshot.status !== "running"
    ) {
      throw operationUnavailable();
    }
    return snapshot;
  }

  async get(generationId: string): Promise<SmartEditGenerationSnapshot> {
    const identifier = requireGenerationId(generationId);
    return parseSnapshot(
      await safeInvoke("get_smart_edit_generation", { generationId: identifier }),
      identifier,
    );
  }

  async cancel(generationId: string): Promise<SmartEditGenerationSnapshot> {
    const identifier = requireGenerationId(generationId);
    const snapshot = parseSnapshot(
      await safeInvoke("cancel_smart_edit_generation", { generationId: identifier }),
      identifier,
    );
    if (snapshot.status !== "cancelling") {
      throw operationUnavailable();
    }
    return snapshot;
  }

  async waitForTerminal(
    generationId: string,
    options: SmartEditPollOptions = {},
  ): Promise<SmartEditGenerationSnapshot> {
    const identifier = requireGenerationId(generationId);
    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt += 1) {
      if (options.signal?.aborted === true) {
        throw pollingCancelled();
      }
      const snapshot = await this.get(identifier);
      if (isTerminal(snapshot)) {
        return snapshot;
      }
      await waitForNextPoll(options.signal);
    }
    throw new SmartEditGatewayError("polling_exhausted", false);
  }
}
