export interface MaterialVideoStudioSnapshot {
  readonly state: "opened";
  readonly modelId: "deepseek-v4-pro" | "glm-5.2" | "qwen3.7-max-2026-06-08";
}

export type MaterialVideoStudioErrorCode =
  | "configuration_required"
  | "process_unavailable"
  | "storage_unavailable"
  | "view_unavailable"
  | "protocol_mismatch"
  | "operation_unavailable";

export class MaterialVideoStudioGatewayError extends Error {
  constructor(
    readonly code: MaterialVideoStudioErrorCode,
    readonly retryable: boolean,
  ) {
    super("material video studio operation unavailable");
    this.name = "MaterialVideoStudioGatewayError";
  }
}

export interface MaterialVideoStudioGateway {
  open(): Promise<MaterialVideoStudioSnapshot>;
}
