export type AliyunEditingRegion =
  | "cn-beijing"
  | "cn-hangzhou"
  | "cn-shanghai"
  | "cn-shenzhen"
  | "ap-southeast-1"
  | "us-west-1";

export interface VideoEditingServiceSnapshot {
  readonly provider: "aliyun_ims";
  readonly providerLabel: "阿里云视频剪辑服务";
  readonly catalogVerifiedAt: "2026-07-23";
  readonly configured: boolean;
  readonly region: AliyunEditingRegion | null;
}

export interface ConfigureVideoEditingServiceInput {
  readonly region: AliyunEditingRegion;
  readonly accessKeyId: string;
  readonly accessKeySecret: string;
}

export interface VideoEditingConnectionSnapshot {
  readonly region: AliyunEditingRegion;
  readonly status: "connected";
}

export type VideoEditingServiceErrorCode =
  | "authentication_rejected"
  | "configuration_invalid"
  | "configuration_required"
  | "invalid_response"
  | "permission_denied"
  | "rate_limited"
  | "storage_unavailable"
  | "timed_out"
  | "transport_unavailable"
  | "protocol_mismatch"
  | "operation_unavailable";

export class VideoEditingServiceGatewayError extends Error {
  readonly code: VideoEditingServiceErrorCode;
  readonly retryable: boolean;

  constructor(code: VideoEditingServiceErrorCode, retryable: boolean) {
    super("video editing service operation unavailable");
    this.name = "VideoEditingServiceGatewayError";
    this.code = code;
    this.retryable = retryable;
  }
}

export interface VideoEditingServiceGateway {
  getSettings(): Promise<VideoEditingServiceSnapshot>;
  configure(input: ConfigureVideoEditingServiceInput): Promise<VideoEditingServiceSnapshot>;
  clear(): Promise<VideoEditingServiceSnapshot>;
  testConnection(): Promise<VideoEditingConnectionSnapshot>;
}
