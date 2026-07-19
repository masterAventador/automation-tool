import { describe, expect, it } from "vitest";

import {
  PlatformSessionGatewayError,
  parsePlatformSessionAction,
  parsePlatformSessionSnapshot,
} from "./platform-session-gateway";

describe("platform Session gateway contracts", () => {
  it("accepts only the exact non-sensitive Control Plane projection", () => {
    expect(
      parsePlatformSessionSnapshot({
        platform: "douyin",
        state: "healthy",
        observedAt: "2026-07-19T14:30:00Z",
      }),
    ).toEqual({
      platform: "douyin",
      state: "healthy",
      observedAt: "2026-07-19T14:30:00Z",
    });
    expect(
      parsePlatformSessionSnapshot({
        platform: "douyin",
        state: "unknown",
        observedAt: null,
      }),
    ).toMatchObject({ state: "unknown", observedAt: null });

    for (const invalid of [
      { platform: "private", state: "healthy", observedAt: "2026-07-19T14:30:00Z" },
      { platform: "douyin", state: "healthy", observedAt: null },
      { platform: "douyin", state: "missing", observedAt: null },
      {
        platform: "douyin",
        state: "healthy",
        observedAt: "2026-07-19T14:30:00Z",
        profilePath: "/private/path",
      },
    ]) {
      expect(() => parsePlatformSessionSnapshot(invalid)).toThrow(PlatformSessionGatewayError);
    }
  });

  it("accepts only the frozen local QR flow result", () => {
    expect(
      parsePlatformSessionAction({
        platform: "douyin",
        state: "awaiting_scan",
        flowVersion: "douyin.qr-login.v2",
      }),
    ).toEqual({
      platform: "douyin",
      state: "awaiting_scan",
      flowVersion: "douyin.qr-login.v2",
    });
    expect(() =>
      parsePlatformSessionAction({
        platform: "douyin",
        state: "awaiting_scan",
        flowVersion: "private.v1",
      }),
    ).toThrow(PlatformSessionGatewayError);
  });
});
