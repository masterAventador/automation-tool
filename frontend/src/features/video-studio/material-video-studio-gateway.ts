export interface MaterialVideoStudioSnapshot {
  readonly state: "opened";
  readonly modelId: "deepseek-v4-pro" | "glm-5.2" | "qwen3.7-max-2026-06-08";
}

export type MaterialRenderJobStatus = "running" | "succeeded" | "failed" | "cancelled";

export interface MaterialRenderJobSnapshot {
  readonly renderJobId: string;
  readonly revision: number;
  readonly status: MaterialRenderJobStatus;
  readonly progressPercent: number;
  readonly subject: string;
  readonly artifactId: string | null;
  readonly artifactSizeBytes: number | null;
  readonly failureCode: "generation_failed" | null;
}

export type MaterialVideoStudioErrorCode =
  | "configuration_required"
  | "process_unavailable"
  | "storage_unavailable"
  | "view_unavailable"
  | "job_unavailable"
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
  jobs(): Promise<readonly MaterialRenderJobSnapshot[]>;
  cancel(renderJobId: string): Promise<void>;
  deleteArtifact(artifactId: string): Promise<void>;
}
