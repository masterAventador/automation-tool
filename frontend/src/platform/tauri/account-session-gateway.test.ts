import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountSessionGatewayError } from "../../features/account-session/account-session-gateway";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import {
  mapAccountSessionNativeError,
  TauriAccountSessionGateway,
} from "./account-session-gateway";

describe("Tauri product account Session gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses fixed account operations and never receives bearer secrets", async () => {
    const unauthenticated = { state: "unauthenticated", account: null };
    const authenticated = {
      state: "authenticated",
      account: {
        userId: "123e4567-e89b-42d3-a456-426614174000",
        loginName: "demo.operator",
        status: "active",
      },
    };
    invoke
      .mockResolvedValueOnce(unauthenticated)
      .mockResolvedValueOnce(authenticated)
      .mockResolvedValueOnce(unauthenticated)
      .mockResolvedValueOnce(unauthenticated)
      .mockResolvedValueOnce(unauthenticated)
      .mockResolvedValueOnce({
        devices: [
          {
            installationId: "223e4567-e89b-42d3-a456-426614174000",
            status: "active",
            revision: 1,
            createdAt: "2026-07-23T02:15:00Z",
            updatedAt: "2026-07-23T02:15:00Z",
          },
        ],
      })
      .mockResolvedValueOnce({
        installationId: "223e4567-e89b-42d3-a456-426614174000",
        status: "revoked",
        revision: 2,
        createdAt: "2026-07-23T02:15:00Z",
        updatedAt: "2026-07-23T02:20:00Z",
      });
    const gateway = new TauriAccountSessionGateway();

    await gateway.restoreSession();
    await gateway.login({ loginName: "demo.operator", password: "Correct-Horse-12" });
    await gateway.recoverPassword({
      recoveryToken: "atrp1.private",
      newPassword: "Recovered-Password-12",
    });
    await gateway.changePassword({
      currentPassword: "Correct-Horse-12",
      newPassword: "Changed-Password-12",
    });
    await gateway.logout();
    await gateway.listDevices();
    await gateway.revokeDevice({
      installationId: "223e4567-e89b-42d3-a456-426614174000",
      expectedRevision: 1,
    });

    expect(invoke.mock.calls).toEqual([
      ["restore_product_account_session", {}],
      [
        "login_product_account",
        { loginName: "demo.operator", password: "Correct-Horse-12" },
      ],
      [
        "recover_product_account_password",
        { recoveryToken: "atrp1.private", newPassword: "Recovered-Password-12" },
      ],
      [
        "change_product_account_password",
        { currentPassword: "Correct-Horse-12", newPassword: "Changed-Password-12" },
      ],
      ["logout_product_account", {}],
      ["list_product_account_devices", {}],
      [
        "revoke_product_account_device",
        {
          installationId: "223e4567-e89b-42d3-a456-426614174000",
          expectedRevision: 1,
        },
      ],
    ]);
  });

  it("preserves a native outcome-uncertain result without treating it as retryable", () => {
    const error = mapAccountSessionNativeError({
      code: "outcome_uncertain",
      retryable: false,
    });
    expect(error).toBeInstanceOf(AccountSessionGatewayError);
    expect(error).toMatchObject({ code: "outcome_uncertain", retryable: false });
  });
});
