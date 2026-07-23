export type ExecutorManagerState = "running" | "restarting" | "stopped";

export interface ExecutorManagerStatus {
  readonly state: ExecutorManagerState;
  readonly version: string | null;
  readonly buildId: string | null;
  readonly restartCount: number;
}

export type SupportedBrowserId = "google_chrome" | "microsoft_edge";

export interface BrowserSettingsSnapshot {
  readonly availableBrowsers: readonly SupportedBrowserId[];
  readonly selectedBrowser: SupportedBrowserId | null;
}

export interface BrowserDiagnosticSettingsSnapshot {
  readonly captureSuccessfulRuns: boolean;
}

export interface LocalStartupEnvironmentSnapshot {
  readonly appData: "ready" | "unavailable";
  readonly executor: "ready" | "configuration_required" | "unavailable";
  readonly embeddedBrowser:
    | "ready"
    | "component_missing"
    | "component_damaged"
    | "version_incompatible";
}

export interface StartupEnvironmentGateway {
  checkLocalEnvironment(): Promise<LocalStartupEnvironmentSnapshot>;
}

export interface DiagnosticExportReceipt {
  readonly fileName: string;
  readonly entryCount: number;
  readonly totalBytes: number;
}

export interface PlatformAdapter {
  getBrowserSettings(): Promise<BrowserSettingsSnapshot>;
  selectBrowser(browser: SupportedBrowserId): Promise<BrowserSettingsSnapshot>;
  getExecutorStatus(): Promise<ExecutorManagerStatus>;
  restartExecutor(): Promise<ExecutorManagerStatus>;
  getExecutorDiagnostics(): Promise<readonly string[]>;
  exportDiagnostics(): Promise<DiagnosticExportReceipt>;
  emergencyStopExecutor(): Promise<ExecutorManagerStatus>;
  getBrowserDiagnosticSettings(): Promise<BrowserDiagnosticSettingsSnapshot>;
  setCaptureSuccessfulDiagnostics(enabled: boolean): Promise<BrowserDiagnosticSettingsSnapshot>;
}

export type PlatformAdapterErrorCode =
  | "already_running"
  | "authentication_rejected"
  | "browser_discovery_unavailable"
  | "browser_unavailable"
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
