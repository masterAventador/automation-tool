import { describe, expect, it } from "vitest";

import {
  appUpdateDecisionSchema,
  appUpdateStateSchema,
  parseAppUpdateState,
} from "./contracts";

const release = {
  version: "1.2.3",
  channel: "stable",
  policy: "optional",
  notes: "A safe release",
  publishedAt: "2026-07-22T00:00:00Z",
  artifact: {
    target: "darwin",
    arch: "aarch64",
    sha256: "a".repeat(64),
    sizeBytes: 1024,
  },
} as const;

describe("generic desktop update contract", () => {
  it("accepts every closed native state and every user decision", () => {
    for (const state of [
      { state: "idle" },
      { state: "checking", trigger: "startup" },
      { state: "up_to_date", trigger: "manual" },
      { state: "available", release },
      { state: "downloading", release, downloadedBytes: 512, totalBytes: 1024 },
      { state: "ready", release, action: "prompt" },
      { state: "installing", release },
      { state: "installation_launched", release },
      {
        state: "failed",
        stage: "download",
        code: "transport_unavailable",
        retryable: true,
      },
    ]) {
      expect(appUpdateStateSchema.parse(state)).toEqual(state);
    }
    for (const decision of ["install_now", "defer", "skip_version"]) {
      expect(appUpdateDecisionSchema.parse(decision)).toBe(decision);
    }
    for (const action of [
      "prompt",
      "deferred",
      "skipped",
      "suppressed",
      "install_requested",
      "forced",
    ]) {
      expect(
        appUpdateStateSchema.parse({ state: "ready", release, action }),
      ).toEqual({ state: "ready", release, action });
    }
  });

  it("fails closed on private updater fields, malformed progress and business policy", () => {
    expect(() =>
      parseAppUpdateState({
        state: "available",
        release: { ...release, url: "https://private" },
      }),
    ).toThrow();
    expect(() =>
      parseAppUpdateState({
        state: "downloading",
        release,
        downloadedBytes: 1025,
        totalBytes: 1024,
      }),
    ).toThrow();
    expect(() =>
      parseAppUpdateState({
        state: "available",
        release: { ...release, policy: "douyin_forced" },
      }),
    ).toThrow();
    expect(() =>
      parseAppUpdateState({
        state: "available",
        release: { ...release, notes: "unsafe\u202evalue" },
      }),
    ).toThrow();
  });
});
