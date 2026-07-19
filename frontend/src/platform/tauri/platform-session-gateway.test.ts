import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { TauriPlatformSessionGateway } from "./platform-session-gateway";

describe("Tauri platform Session gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses fixed zero-argument Commands for query and local handling", async () => {
    invoke
      .mockResolvedValueOnce({
        platform: "douyin",
        state: "missing",
        observedAt: "2026-07-19T14:30:00Z",
      })
      .mockResolvedValueOnce({
        platform: "douyin",
        state: "awaiting_scan",
        flowVersion: "douyin.qr-login.v2",
      })
      .mockResolvedValueOnce({
        platform: "douyin",
        state: "healthy",
        flowVersion: "douyin.qr-login.v2",
      })
      .mockResolvedValueOnce({
        platform: "douyin",
        state: "missing",
        observedAt: "2026-07-19T14:31:00Z",
      });
    const gateway = new TauriPlatformSessionGateway();

    await expect(gateway.getDouyinSession()).resolves.toMatchObject({ state: "missing" });
    await expect(gateway.openDouyinLogin()).resolves.toMatchObject({ state: "awaiting_scan" });
    await expect(gateway.recheckDouyinLogin()).resolves.toMatchObject({ state: "healthy" });
    await expect(gateway.logoutDouyinSession()).resolves.toMatchObject({ state: "missing" });
    expect(invoke.mock.calls).toEqual([
      ["get_douyin_platform_session", {}],
      ["open_douyin_login", {}],
      ["recheck_douyin_login", {}],
      ["logout_douyin_session", {}],
    ]);
  });
});
