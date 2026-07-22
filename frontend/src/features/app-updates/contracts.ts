import { z } from "zod";

const MAX_UPDATE_ARTIFACT_BYTES = 1024 * 1024 * 1024;
const SAFE_SEMVER = /^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/u;
const SAFE_CHANNEL = /^[a-z][a-z0-9-]{0,31}$/u;
const SAFE_SHA256 = /^[0-9a-f]{64}$/u;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u;

function isSafeText(value: string): boolean {
  if (new TextEncoder().encode(value).byteLength > 8192) {
    return false;
  }
  for (const character of value) {
    const codepoint = character.codePointAt(0);
    if (
      codepoint === undefined ||
      (codepoint <= 0x1f && character !== "\n" && character !== "\t") ||
      codepoint === 0x7f ||
      (codepoint >= 0x202a && codepoint <= 0x202e) ||
      (codepoint >= 0x2066 && codepoint <= 0x2069)
    ) {
      return false;
    }
  }
  return true;
}

export const appUpdateReleaseSchema = z
  .object({
    version: z.string().regex(SAFE_SEMVER),
    channel: z.string().regex(SAFE_CHANNEL),
    policy: z.enum(["optional", "forced"]),
    notes: z.string().refine(isSafeText).nullable(),
    publishedAt: z.string().regex(RFC3339).nullable(),
    artifact: z
      .object({
        target: z.enum(["darwin", "windows"]),
        arch: z.enum(["aarch64", "x86_64"]),
        sha256: z.string().regex(SAFE_SHA256),
        sizeBytes: z.number().int().min(1).max(MAX_UPDATE_ARTIFACT_BYTES),
      })
      .strict(),
  })
  .strict();

const checkTriggerSchema = z.enum(["startup", "periodic", "manual"]);
const updatePolicyActionSchema = z.enum([
  "prompt",
  "deferred",
  "skipped",
  "suppressed",
  "install_requested",
  "forced",
]);
const failedUpdateSchema = z
  .object({
    state: z.literal("failed"),
    stage: z.enum(["configuration", "check", "download", "storage", "install"]),
    code: z.enum([
      "configuration_invalid",
      "manifest_rejected",
      "transport_unavailable",
      "signature_rejected",
      "storage_unavailable",
      "installation_failed",
    ]),
    retryable: z.boolean(),
  })
  .strict();

const downloadingUpdateSchema = z
  .object({
    state: z.literal("downloading"),
    release: appUpdateReleaseSchema,
    downloadedBytes: z.number().int().min(0).max(MAX_UPDATE_ARTIFACT_BYTES),
    totalBytes: z.number().int().min(1).max(MAX_UPDATE_ARTIFACT_BYTES).nullable(),
  })
  .strict()
  .refine(
    ({ downloadedBytes, totalBytes }) => totalBytes === null || downloadedBytes <= totalBytes,
  );

const readyUpdateSchema = z
  .object({
    state: z.literal("ready"),
    release: appUpdateReleaseSchema,
    action: updatePolicyActionSchema,
  })
  .strict()
  .refine(
    ({ release, action }) =>
      release.policy === "forced" ? action === "forced" : action !== "forced",
    { path: ["action"] },
  );

export const appUpdateStateSchema = z.discriminatedUnion("state", [
  z.object({ state: z.literal("idle") }).strict(),
  z.object({ state: z.literal("checking"), trigger: checkTriggerSchema }).strict(),
  z.object({ state: z.literal("up_to_date"), trigger: checkTriggerSchema }).strict(),
  z.object({ state: z.literal("available"), release: appUpdateReleaseSchema }).strict(),
  downloadingUpdateSchema,
  readyUpdateSchema,
  z.object({ state: z.literal("installing"), release: appUpdateReleaseSchema }).strict(),
  z
    .object({ state: z.literal("installation_launched"), release: appUpdateReleaseSchema })
    .strict(),
  failedUpdateSchema,
]);

export const appUpdateDecisionSchema = z.enum(["install_now", "defer", "skip_version"]);

export type AppUpdateRelease = z.infer<typeof appUpdateReleaseSchema>;
export type AppUpdateState = z.infer<typeof appUpdateStateSchema>;
export type AppUpdateDecision = z.infer<typeof appUpdateDecisionSchema>;

export interface AppUpdateGateway {
  getState(): Promise<AppUpdateState>;
  checkNow(): Promise<AppUpdateState>;
  decide(decision: AppUpdateDecision): Promise<AppUpdateState>;
}

export type AppUpdateGatewayErrorCode =
  | "configuration_unavailable"
  | "decision_unavailable"
  | "operation_in_progress"
  | "operation_unavailable"
  | "protocol_mismatch";

export class AppUpdateGatewayError extends Error {
  readonly code: AppUpdateGatewayErrorCode;

  constructor(code: AppUpdateGatewayErrorCode) {
    super("App 更新操作暂时不可用");
    this.name = "AppUpdateGatewayError";
    this.code = code;
  }
}

export function parseAppUpdateState(value: unknown): AppUpdateState {
  return appUpdateStateSchema.parse(value);
}
