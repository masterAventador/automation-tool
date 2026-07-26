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

/**
 * T109: every reason `open_douyin_login` and `recheck_douyin_login` can fail with.
 *
 * The two buttons failed on a signed build and no layer could say why: this
 * gateway had three codes, so the fifteen the bridge can actually answer with
 * were all flattened into `transport_unavailable` before anyone saw them. The
 * set is closed on purpose — it mirrors the Rust mappers behind those two
 * Commands (`map_executor_platform_error`, `map_executor_connection_error`,
 * `resolve_embedded_browser`, `map_browser_profile_error`) — so an unknown code
 * is treated as an unknown failure rather than rendered as if it were understood.
 */
export const PLATFORM_SESSION_NATIVE_ERROR_CODES: ReadonlySet<string> = new Set([
  // Executor process and command exchange.
  "process_unavailable",
  "timed_out",
  "already_running",
  "authentication_rejected",
  "package_rejected",
  "configuration_invalid",
  "storage_unavailable",
  // Packaged operations browser.
  "browser_component_missing",
  "browser_component_invalid",
  "browser_component_version_incompatible",
  // The App's private operations Profile.
  "profile_in_use",
  "profile_recovery_required",
  // Control Plane leg of starting the executor.
  "transport_unavailable",
  "credential_missing",
  "installation_access_denied",
  "installation_conflict",
  "operation_unavailable",
]);

export type PlatformSessionGatewayErrorCode =
  | "transport_unavailable"
  | "protocol_mismatch"
  | "operation_unavailable"
  | "storage_unavailable"
  | "configuration_invalid"
  | "already_running"
  | "authentication_rejected"
  | "package_rejected"
  | "process_unavailable"
  | "timed_out"
  | "browser_component_missing"
  | "browser_component_invalid"
  | "browser_component_version_incompatible"
  | "profile_in_use"
  | "profile_recovery_required"
  | "credential_missing"
  | "installation_access_denied"
  | "installation_conflict"
  /**
   * The executor confirmed the platform login locally, but the authoritative
   * Control Plane projection did not catch up in time. Nothing is broken on this
   * machine, so telling the operator to "retry reading the login state" would be
   * a lie about what happened.
   */
  | "health_publication_timed_out";

export class PlatformSessionGatewayError extends Error {
  readonly code: PlatformSessionGatewayErrorCode;
  readonly retryable: boolean;

  constructor(code: PlatformSessionGatewayErrorCode, retryable: boolean) {
    super(`Platform Session operation is unavailable: ${code}`);
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
