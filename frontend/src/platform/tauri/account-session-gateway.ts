import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

import {
  AccountSessionGatewayError,
  parseAccountSessionSnapshot,
  type AccountLoginInput,
  type AccountPasswordChangeInput,
  type AccountRecoveryInput,
  type AccountSessionGateway,
  type AccountSessionGatewayErrorCode,
  type AccountSessionSnapshot,
} from "../../features/account-session/account-session-gateway";

const nativeErrorSchema = z
  .object({
    code: z.enum([
      "authentication_invalid",
      "recovery_invalid",
      "session_invalid",
      "transport_unavailable",
      "storage_unavailable",
      "outcome_uncertain",
      "operation_unavailable",
    ]),
    retryable: z.boolean(),
  })
  .strict();

export function mapAccountSessionNativeError(error: unknown): AccountSessionGatewayError {
  const parsed = nativeErrorSchema.safeParse(error);
  if (parsed.success) {
    return new AccountSessionGatewayError(
      parsed.data.code as AccountSessionGatewayErrorCode,
      parsed.data.retryable,
    );
  }
  return new AccountSessionGatewayError("transport_unavailable", true);
}

async function safeInvoke(command: string, args: Record<string, string>): Promise<unknown> {
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
}
