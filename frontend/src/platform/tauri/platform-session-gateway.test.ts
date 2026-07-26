import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { PlatformSessionGatewayError } from "../../features/platform-sessions/platform-session-gateway";
import { TauriPlatformSessionGateway } from "./platform-session-gateway";

describe("Tauri platform Session gateway", () => {
  beforeEach(() => invoke.mockReset());

  /**
   * T109: the two 抖音 buttons failed on a signed build and nobody could say why,
   * because every native code collapsed into one opaque `transport_unavailable`
   * here. The bridge already puts `{code, message, retryable}` on the wire; the
   * only reason the reason was unknowable is that this gateway threw it away.
   */
  it.each([
    ["browser_component_missing", false],
    ["process_unavailable", true],
    ["profile_in_use", true],
    ["timed_out", true],
    ["authentication_rejected", false],
  ])("keeps the native failure reason %s instead of collapsing it", async (code, retryable) => {
    invoke.mockRejectedValueOnce({
      code,
      message: `native command error: ${code}`,
      retryable,
    });
    const gateway = new TauriPlatformSessionGateway();

    await expect(gateway.recheckDouyinLogin()).rejects.toMatchObject({ code, retryable });
  });

  it("still answers an unrecognisable rejection with a retryable transport failure", async () => {
    invoke.mockRejectedValueOnce(new Error("/private/profile/secret"));
    const gateway = new TauriPlatformSessionGateway();

    const error = await gateway.openDouyinLogin().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(PlatformSessionGatewayError);
    expect(error).toMatchObject({ code: "transport_unavailable", retryable: true });
  });

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
        confirmationId: null,
        targetAccount: null,
      })
      .mockResolvedValueOnce({
        platform: "douyin",
        state: "healthy",
        flowVersion: "douyin.qr-login.v2",
        confirmationId: null,
        targetAccount: null,
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
