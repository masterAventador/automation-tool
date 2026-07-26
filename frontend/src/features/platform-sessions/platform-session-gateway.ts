import { z } from "zod";

const canonicalUtcTimestamp =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;

function isCanonicalUtcTimestamp(value: string): boolean {
  const match = canonicalUtcTimestamp.exec(value);
  if (match === null) return false;
  const [, year, month, day, hour, minute, second, fraction = ""] = match;
  const values = [year, month, day, hour, minute, second].map(Number);
  const [y, m, d, h, min, s] = values;
  if ([y, m, d, h, min, s].some((value) => value === undefined)) return false;
  const parsed = new Date(0);
  parsed.setUTCFullYear(y!, m! - 1, d);
  parsed.setUTCHours(h!, min!, s!, Number(fraction.padEnd(3, "0").slice(0, 3)));
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.getUTCFullYear() === y &&
    parsed.getUTCMonth() === m! - 1 &&
    parsed.getUTCDate() === d &&
    parsed.getUTCHours() === h &&
    parsed.getUTCMinutes() === min &&
    parsed.getUTCSeconds() === s
  );
}

const platformSessionState = z.enum([
  "healthy",
  "expired",
  "missing",
  "risk",
  "unknown",
]);

const platformSessionSnapshotSchema = z
  .object({
    platform: z.literal("douyin"),
    state: platformSessionState,
    observedAt: z.string().refine(isCanonicalUtcTimestamp).nullable(),
  })
  .strict()
  .refine((value) => value.observedAt !== null || value.state === "unknown");

const platformSessionActionSchema = z
  .object({
    platform: z.literal("douyin"),
    state: z.enum([
      "login_required",
      "awaiting_scan",
      "awaiting_confirmation",
      "qr_expired",
      "healthy",
      "handoff_required",
      "unknown",
    ]),
    flowVersion: z.literal("douyin.qr-login.v2"),
    // PB-07 gave the publish and login command families one result DTO. Both
    // fields belong to a publish preflight and are always null here, but the
    // Rust producer always serializes them, so a schema that omits them rejects
    // every real login result as `protocol_mismatch`.
    confirmationId: z.string().nullable(),
    targetAccount: z.string().nullable(),
  })
  .strict();

export type PlatformSessionSnapshot = z.infer<typeof platformSessionSnapshotSchema>;
export type PlatformSessionAction = z.infer<typeof platformSessionActionSchema>;

export interface PlatformSessionGateway {
  getDouyinSession(): Promise<PlatformSessionSnapshot>;
  openDouyinLogin(): Promise<PlatformSessionAction>;
  recheckDouyinLogin(): Promise<PlatformSessionAction>;
  logoutDouyinSession(): Promise<PlatformSessionSnapshot>;
}

export type PlatformSessionGatewayErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "operation_unavailable";

export class PlatformSessionGatewayError extends Error {
  readonly code: PlatformSessionGatewayErrorCode;
  readonly retryable: boolean;

  constructor(code: PlatformSessionGatewayErrorCode, retryable: boolean) {
    super("Platform Session operation is unavailable");
    this.name = "PlatformSessionGatewayError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function parsePlatformSessionSnapshot(value: unknown): PlatformSessionSnapshot {
  const parsed = platformSessionSnapshotSchema.safeParse(value);
  if (!parsed.success) throw new PlatformSessionGatewayError("protocol_mismatch", false);
  return parsed.data;
}

export function parsePlatformSessionAction(value: unknown): PlatformSessionAction {
  const parsed = platformSessionActionSchema.safeParse(value);
  if (!parsed.success) throw new PlatformSessionGatewayError("protocol_mismatch", false);
  return parsed.data;
}
