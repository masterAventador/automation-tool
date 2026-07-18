export interface ControlPlaneHealth {
  readonly status: "available";
  readonly serviceVersion: string;
}

export interface ControlPlaneRequestOptions {
  readonly signal?: AbortSignal;
}

export interface ControlPlaneTransport {
  checkHealth(options?: ControlPlaneRequestOptions): Promise<ControlPlaneHealth>;
}

export type ControlPlaneTransportErrorCode =
  | "transport_unavailable"
  | "operation_unavailable"
  | "installation_access_denied"
  | "request_cancelled";

const PUBLIC_ERROR_MESSAGES: Record<ControlPlaneTransportErrorCode, string> = {
  transport_unavailable: "Control Plane transport is unavailable",
  operation_unavailable: "Control Plane operation is unavailable",
  installation_access_denied: "Installation access is unavailable",
  request_cancelled: "Control Plane request was cancelled",
};

export class ControlPlaneTransportError extends Error {
  readonly code: ControlPlaneTransportErrorCode;
  readonly retryable: boolean;

  constructor(code: ControlPlaneTransportErrorCode, retryable: boolean) {
    super(PUBLIC_ERROR_MESSAGES[code]);
    this.name = "ControlPlaneTransportError";
    this.code = code;
    this.retryable = retryable;
  }
}
