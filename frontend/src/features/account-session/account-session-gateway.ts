import { z } from "zod";

const canonicalUuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const canonicalLoginName = /^[a-z][a-z0-9._-]{2,63}$/;
const utcTimestamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;

const accountProjectionSchema = z
  .object({
    userId: z.string().regex(canonicalUuidV4),
    loginName: z.string().regex(canonicalLoginName),
    status: z.literal("active"),
  })
  .strict();

const accountSessionSnapshotSchema = z.discriminatedUnion("state", [
  z.object({ state: z.literal("unauthenticated"), account: z.null() }).strict(),
  z.object({ state: z.literal("authenticated"), account: accountProjectionSchema }).strict(),
]);

export type AccountSessionSnapshot = z.infer<typeof accountSessionSnapshotSchema>;

const accountDeviceSchema = z
  .object({
    installationId: z.string().regex(canonicalUuidV4),
    status: z.enum(["active", "revoked"]),
    revision: z.number().int().positive(),
    createdAt: z.string().regex(utcTimestamp),
    updatedAt: z.string().regex(utcTimestamp),
  })
  .strict();

const accountDeviceListSchema = z.object({ devices: z.array(accountDeviceSchema) }).strict();

export type AccountDevice = z.infer<typeof accountDeviceSchema>;

export interface AccountDeviceRevocationInput {
  readonly installationId: string;
  readonly expectedRevision: number;
}

export interface AccountLoginInput {
  readonly loginName: string;
  readonly password: string;
}

export interface AccountRecoveryInput {
  readonly recoveryToken: string;
  readonly newPassword: string;
}

export interface AccountPasswordChangeInput {
  readonly currentPassword: string;
  readonly newPassword: string;
}

export interface AccountSessionGateway {
  restoreSession(): Promise<AccountSessionSnapshot>;
  login(input: AccountLoginInput): Promise<AccountSessionSnapshot>;
  recoverPassword(input: AccountRecoveryInput): Promise<AccountSessionSnapshot>;
  changePassword(input: AccountPasswordChangeInput): Promise<AccountSessionSnapshot>;
  logout(): Promise<AccountSessionSnapshot>;
  listDevices(): Promise<readonly AccountDevice[]>;
  revokeDevice(input: AccountDeviceRevocationInput): Promise<AccountDevice>;
}

export type AccountSessionGatewayErrorCode =
  | "authentication_invalid"
  | "recovery_invalid"
  | "session_invalid"
  | "transport_unavailable"
  | "storage_unavailable"
  | "outcome_uncertain"
  | "protocol_mismatch"
  | "operation_unavailable";

export class AccountSessionGatewayError extends Error {
  readonly code: AccountSessionGatewayErrorCode;
  readonly retryable: boolean;

  constructor(code: AccountSessionGatewayErrorCode, retryable: boolean) {
    super("Product account operation is unavailable");
    this.name = "AccountSessionGatewayError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function parseAccountSessionSnapshot(value: unknown): AccountSessionSnapshot {
  const parsed = accountSessionSnapshotSchema.safeParse(value);
  if (!parsed.success) throw new AccountSessionGatewayError("protocol_mismatch", false);
  return parsed.data;
}

export function parseAccountDevices(value: unknown): readonly AccountDevice[] {
  const parsed = accountDeviceListSchema.safeParse(value);
  if (!parsed.success) throw new AccountSessionGatewayError("protocol_mismatch", false);
  return parsed.data.devices;
}

export function parseAccountDevice(value: unknown): AccountDevice {
  const parsed = accountDeviceSchema.safeParse(value);
  if (!parsed.success) throw new AccountSessionGatewayError("protocol_mismatch", false);
  return parsed.data;
}
