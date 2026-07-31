import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { BilibiliServiceGatewayError } from "../../features/settings/bilibili-service-gateway";
import { TauriBilibiliServiceGateway } from "./bilibili-service-gateway";

const emptySnapshot = {
  provider: "bilibili",
  providerLabel: "B站开放平台",
  configured: false,
  targetAccount: null,
  tid: null,
  tag: null,
  noReprint: null,
} as const;

const configuredSnapshot = {
  ...emptySnapshot,
  configured: true,
  targetAccount: "运营账号",
  tid: 171,
  tag: "自动化,效率",
  noReprint: 1,
} as const;

describe("Tauri Bilibili service gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses fixed native commands and never returns credentials", async () => {
    invoke
      .mockResolvedValueOnce(emptySnapshot)
      .mockResolvedValueOnce(configuredSnapshot)
      .mockResolvedValueOnce(emptySnapshot);
    const gateway = new TauriBilibiliServiceGateway();
    const request = {
      clientId: "client-1",
      appSecret: "app-secret",
      accessToken: "access-token",
      refreshToken: "refresh-token",
      expiresAtEpochSeconds: 1_800_000_000,
      targetAccount: "运营账号",
      tid: 171,
      tag: "自动化,效率",
      noReprint: 1 as const,
    };

    await expect(gateway.getSettings()).resolves.toEqual(emptySnapshot);
    await expect(gateway.configure(request)).resolves.toEqual(configuredSnapshot);
    await expect(gateway.clear()).resolves.toEqual(emptySnapshot);

    expect(invoke.mock.calls).toEqual([
      ["get_bilibili_service_settings", undefined],
      ["configure_bilibili_service", { request }],
      ["clear_bilibili_service", undefined],
    ]);
    expect(JSON.stringify(configuredSnapshot)).not.toContain("app-secret");
    expect(JSON.stringify(configuredSnapshot)).not.toContain("access-token");
  });

  it("rejects leaked, inconsistent, and malformed snapshots", async () => {
    for (const payload of [
      { ...emptySnapshot, accessToken: "leak" },
      { ...emptySnapshot, configured: true },
      { ...configuredSnapshot, noReprint: 2 },
      { ...configuredSnapshot, tid: 0 },
      null,
      [],
    ]) {
      invoke.mockResolvedValueOnce(payload);
      await expect(new TauriBilibiliServiceGateway().getSettings()).rejects.toMatchObject({
        code: "protocol_mismatch",
      });
    }
  });

  it("keeps known native codes and hides unknown failures", async () => {
    const gateway = new TauriBilibiliServiceGateway();
    invoke.mockRejectedValueOnce({ code: "storage_unavailable", retryable: false });
    await expect(gateway.getSettings()).rejects.toMatchObject({
      code: "storage_unavailable",
      retryable: false,
    });

    invoke.mockRejectedValueOnce(new Error("accessToken=private"));
    const failure = await gateway.getSettings().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(BilibiliServiceGatewayError);
    expect(failure).toMatchObject({ code: "operation_unavailable" });
    expect(JSON.stringify(failure)).not.toContain("private");
  });
});
