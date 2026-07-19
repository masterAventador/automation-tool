export type ExecutorManagerState = "running" | "restarting" | "stopped";

export interface ExecutorManagerStatus {
  readonly state: ExecutorManagerState;
  readonly version: string | null;
  readonly buildId: string | null;
  readonly restartCount: number;
}

export interface PlatformAdapter {
  getExecutorStatus(): Promise<ExecutorManagerStatus>;
  restartExecutor(): Promise<ExecutorManagerStatus>;
  getExecutorDiagnostics(): Promise<readonly string[]>;
  emergencyStopExecutor(): Promise<ExecutorManagerStatus>;
}

export type PlatformAdapterErrorCode =
  | "already_running"
  | "authentication_rejected"
  | "configuration_invalid"
  | "credential_missing"
  | "installation_access_denied"
  | "operation_unavailable"
  | "package_rejected"
  | "process_unavailable"
  | "protocol_mismatch"
  | "storage_unavailable"
  | "timed_out"
  | "transport_unavailable";

export class PlatformAdapterError extends Error {
  readonly code: PlatformAdapterErrorCode;
  readonly retryable: boolean;

  constructor(code: PlatformAdapterErrorCode, retryable = false) {
    super("本地平台操作暂时不可用");
    this.name = "PlatformAdapterError";
    this.code = code;
    this.retryable = retryable;
  }
}
