export type ModelServicePurpose = "script" | "video_creative";

export type ScriptModelId =
  | "deepseek-v4-pro"
  | "glm-5.2"
  | "qwen3.7-max-2026-06-08";

export type VideoCreativeModelId = "qwen3.7-max-2026-06-08";
export type BailianModelId = ScriptModelId | VideoCreativeModelId;

export interface ModelPurposeSnapshot {
  readonly purpose: ModelServicePurpose;
  readonly configured: boolean;
  readonly modelId: BailianModelId;
}

export interface ModelServiceSnapshot {
  readonly provider: "bailian";
  readonly providerLabel: "阿里百炼";
  readonly catalogVerifiedAt: "2026-07-23";
  readonly script: ModelPurposeSnapshot;
  readonly videoCreative: ModelPurposeSnapshot;
  readonly sameCredential: boolean;
}

export interface ConfigureModelServiceInput {
  readonly purpose: ModelServicePurpose;
  readonly modelId: BailianModelId;
  readonly apiKey: string;
}

export interface ModelConnectionSnapshot {
  readonly purpose: ModelServicePurpose;
  readonly modelId: BailianModelId;
  readonly status: "connected";
  readonly quota: {
    readonly remainingRequests: number | null;
    readonly remainingTokens: number | null;
  };
}

export type ModelServiceErrorCode =
  | "authentication_rejected"
  | "configuration_invalid"
  | "configuration_required"
  | "invalid_response"
  | "model_unavailable"
  | "quota_exhausted"
  | "rate_limited"
  | "storage_unavailable"
  | "timed_out"
  | "transport_unavailable"
  | "protocol_mismatch"
  | "operation_unavailable";

export class ModelServiceGatewayError extends Error {
  readonly code: ModelServiceErrorCode;
  readonly retryable: boolean;

  constructor(code: ModelServiceErrorCode, retryable: boolean) {
    super("model service operation unavailable");
    this.name = "ModelServiceGatewayError";
    this.code = code;
    this.retryable = retryable;
  }
}

export interface ModelServiceGateway {
  getSettings(): Promise<ModelServiceSnapshot>;
  configure(input: ConfigureModelServiceInput): Promise<ModelServiceSnapshot>;
  reuseScriptForVideo(): Promise<ModelServiceSnapshot>;
  clear(purpose: ModelServicePurpose): Promise<ModelServiceSnapshot>;
  testConnection(purpose: ModelServicePurpose): Promise<ModelConnectionSnapshot>;
}
