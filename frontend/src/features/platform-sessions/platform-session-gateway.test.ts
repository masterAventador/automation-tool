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
    // The document below is what the Rust Command actually serializes; the two
    // publish fields are always null for a login, and dropping them here is what
    // made every real click fail as `protocol_mismatch`.
    expect(
      parsePlatformSessionAction({
        platform: "douyin",
        state: "awaiting_scan",
        flowVersion: "douyin.qr-login.v2",
        confirmationId: null,
        targetAccount: null,
      }),
    ).toEqual({
      platform: "douyin",
      state: "awaiting_scan",
      flowVersion: "douyin.qr-login.v2",
      confirmationId: null,
      targetAccount: null,
    });
    for (const invalid of [
      {
        platform: "douyin",
        state: "awaiting_scan",
        flowVersion: "private.v1",
        confirmationId: null,
        targetAccount: null,
      },
      { platform: "douyin", state: "awaiting_scan", flowVersion: "douyin.qr-login.v2" },
      {
        platform: "douyin",
        state: "awaiting_scan",
        flowVersion: "douyin.qr-login.v2",
        confirmationId: null,
        targetAccount: null,
        profilePath: "/private/path",
      },
    ]) {
      expect(() => parsePlatformSessionAction(invalid)).toThrow(PlatformSessionGatewayError);
    }
  });
});
