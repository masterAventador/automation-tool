import {
  ControlPlaneTransportError,
  type ControlPlaneHealth,
  type ControlPlaneRequestOptions,
  type ControlPlaneTransport,
} from "../../api/control-plane/transport";
import { invoke } from "@tauri-apps/api/core";

function isControlPlaneHealth(value: unknown): value is ControlPlaneHealth {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    Object.keys(record).length === 2 &&
    record.status === "available" &&
    typeof record.serviceVersion === "string" &&
    record.serviceVersion.length > 0 &&
    record.serviceVersion.length <= 64
  );
}

function installationAccessError(value: unknown): ControlPlaneTransportError | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  if (
    Object.keys(record).length === 2 &&
    record.code === "installation_access_denied" &&
    record.retryable === false
  ) {
    return new ControlPlaneTransportError("installation_access_denied", false);
  }
  return undefined;
}

export class TauriControlPlaneTransport implements ControlPlaneTransport {
  async checkHealth(
    options: ControlPlaneRequestOptions = {},
  ): Promise<ControlPlaneHealth> {
    if (options.signal?.aborted === true) {
      throw new ControlPlaneTransportError("request_cancelled", false);
    }

    let response: unknown;
    try {
      response = await invokeWithCancellation(
        invoke<unknown>("check_control_plane_health"),
        options.signal,
      );
    } catch (error) {
      if (error instanceof ControlPlaneTransportError) {
        throw error;
      }
      const denied = installationAccessError(error);
      if (denied !== undefined) {
        throw denied;
      }
      throw new ControlPlaneTransportError("transport_unavailable", true);
    }

    if (!isControlPlaneHealth(response)) {
      throw new ControlPlaneTransportError("operation_unavailable", false);
    }
    return response;
  }
}

async function invokeWithCancellation<T>(
  request: Promise<T>,
  signal: AbortSignal | undefined,
): Promise<T> {
  if (signal === undefined) {
    return request;
  }

  let cancelRequest: (() => void) | undefined;
  const cancellation = new Promise<never>((_resolve, reject) => {
    cancelRequest = () =>
      reject(new ControlPlaneTransportError("request_cancelled", false));
    signal.addEventListener("abort", cancelRequest, { once: true });
    if (signal.aborted) {
      cancelRequest();
    }
  });
  try {
    return await Promise.race([request, cancellation]);
  } finally {
    if (cancelRequest !== undefined) {
      signal.removeEventListener("abort", cancelRequest);
    }
  }
}
