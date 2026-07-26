import { invoke } from "@tauri-apps/api/core";

import { nativeCommandErrorFields } from "./native-command-error";
import {
  AccountSessionGatewayError,
  parseAccountDevice,
  parseAccountDevices,
  parseAccountSessionSnapshot,
  type AccountDevice,
  type AccountDeviceRevocationInput,
  type AccountLoginInput,
  type AccountPasswordChangeInput,
  type AccountRecoveryInput,
  type AccountSessionGateway,
  type AccountSessionGatewayErrorCode,
  type AccountSessionSnapshot,
} from "../../features/account-session/account-session-gateway";

const NATIVE_ERROR_CODES: ReadonlySet<string> = new Set([
  "authentication_invalid",
  "recovery_invalid",
  "session_invalid",
  "transport_unavailable",
  "storage_unavailable",
  "outcome_uncertain",
  "operation_unavailable",
]);

export function mapAccountSessionNativeError(error: unknown): AccountSessionGatewayError {
  const fields = nativeCommandErrorFields(error);
  if (fields !== undefined && NATIVE_ERROR_CODES.has(fields.code)) {
    return new AccountSessionGatewayError(
      fields.code as AccountSessionGatewayErrorCode,
      fields.retryable,
    );
  }
  return new AccountSessionGatewayError("transport_unavailable", true);
}

async function safeInvoke(command: string, args: Record<string, string | number>): Promise<unknown> {
  try {
    return await invoke<unknown>(command, args);
  } catch (error) {
    throw mapAccountSessionNativeError(error);
  }
}

export class TauriAccountSessionGateway implements AccountSessionGateway {
  async restoreSession(): Promise<AccountSessionSnapshot> {
    return parseAccountSessionSnapshot(await safeInvoke("restore_product_account_session", {}));
  }

  async login(input: AccountLoginInput): Promise<AccountSessionSnapshot> {
    return parseAccountSessionSnapshot(
      await safeInvoke("login_product_account", {
        loginName: input.loginName,
        password: input.password,
      }),
    );
  }

  async recoverPassword(input: AccountRecoveryInput): Promise<AccountSessionSnapshot> {
    return parseAccountSessionSnapshot(
      await safeInvoke("recover_product_account_password", {
        recoveryToken: input.recoveryToken,
        newPassword: input.newPassword,
      }),
    );
  }

  async changePassword(input: AccountPasswordChangeInput): Promise<AccountSessionSnapshot> {
    return parseAccountSessionSnapshot(
      await safeInvoke("change_product_account_password", {
        currentPassword: input.currentPassword,
        newPassword: input.newPassword,
      }),
    );
  }

  async logout(): Promise<AccountSessionSnapshot> {
    return parseAccountSessionSnapshot(await safeInvoke("logout_product_account", {}));
  }

  async listDevices(): Promise<readonly AccountDevice[]> {
    return parseAccountDevices(await safeInvoke("list_product_account_devices", {}));
  }

  async revokeDevice(input: AccountDeviceRevocationInput): Promise<AccountDevice> {
    return parseAccountDevice(
      await safeInvoke("revoke_product_account_device", {
        installationId: input.installationId,
        expectedRevision: input.expectedRevision,
      }),
    );
  }
}
