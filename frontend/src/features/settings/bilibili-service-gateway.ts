export interface BilibiliServiceSnapshot {
  readonly provider: "bilibili";
  readonly providerLabel: "B站开放平台";
  readonly configured: boolean;
  readonly targetAccount: string | null;
  readonly tid: number | null;
  readonly tag: string | null;
  readonly noReprint: 0 | 1 | null;
}

export interface ConfigureBilibiliServiceInput {
  readonly clientId: string;
  readonly appSecret: string;
  readonly accessToken: string;
  readonly refreshToken: string;
  readonly expiresAtEpochSeconds: number;
  readonly targetAccount: string;
  readonly tid: number;
  readonly tag: string;
  readonly noReprint: 0 | 1;
}

export type BilibiliServiceErrorCode =
  | "configuration_invalid"
  | "configuration_required"
  | "storage_unavailable"
  | "protocol_mismatch"
  | "operation_unavailable";

export class BilibiliServiceGatewayError extends Error {
  constructor(
    readonly code: BilibiliServiceErrorCode,
    readonly retryable: boolean,
  ) {
    super("Bilibili service operation unavailable");
    this.name = "BilibiliServiceGatewayError";
  }
}

export interface BilibiliServiceGateway {
  getSettings(): Promise<BilibiliServiceSnapshot>;
  configure(input: ConfigureBilibiliServiceInput): Promise<BilibiliServiceSnapshot>;
  clear(): Promise<BilibiliServiceSnapshot>;
}
