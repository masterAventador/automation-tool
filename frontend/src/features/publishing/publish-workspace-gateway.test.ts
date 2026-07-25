import { describe, expect, it } from "vitest";

import {
  PublishWorkspaceGatewayError,
  parsePublishWorkspaceSnapshot,
  publishAvailabilityLabel,
  publishOutcomeLabel,
  publishPlatformLabel,
  publishStageLabel,
} from "./publish-workspace-gateway";

const readySnapshot = {
  platforms: [
    { platform: "bilibili", availability: "awaiting_configuration" },
    { platform: "douyin", availability: "ready" },
  ],
  stage: "awaiting_approval",
  target: "douyin",
  approval: {
    targetAccount: "自动化运营测试账号",
    videoSummary: "clip.mp4 · 12.4 MB",
    title: "自动化运营工具发布验收标题",
    description: "自动化运营工具发布验收简介",
    confirmationId: "123e4567-e89b-42d3-a456-426614174007",
  },
  outcome: null,
  retryable: false,
};

describe("publish workspace snapshot", () => {
  it("accepts the projection the Rust bridge produces", () => {
    const snapshot = parsePublishWorkspaceSnapshot(readySnapshot);

    expect(snapshot.stage).toBe("awaiting_approval");
    expect(snapshot.approval?.targetAccount).toBe("自动化运营测试账号");
    expect(snapshot.platforms.map((entry) => entry.platform)).toEqual(["bilibili", "douyin"]);
  });

  it("keeps an unconfigured platform listed instead of dropping it", () => {
    const snapshot = parsePublishWorkspaceSnapshot(readySnapshot);

    const bilibili = snapshot.platforms.find((entry) => entry.platform === "bilibili");
    expect(bilibili?.availability).toBe("awaiting_configuration");
  });

  it("refuses a platform outside the two the App supports", () => {
    expect(() =>
      parsePublishWorkspaceSnapshot({
        ...readySnapshot,
        platforms: [{ platform: "kuaishou", availability: "ready" }],
      }),
    ).toThrow(PublishWorkspaceGatewayError);
  });

  it("refuses a snapshot that leaks how the platform is reached", () => {
    expect(() =>
      parsePublishWorkspaceSnapshot({ ...readySnapshot, mechanism: "browser_use" }),
    ).toThrow(PublishWorkspaceGatewayError);
  });

  it("refuses an approval stage with nothing to approve", () => {
    expect(() =>
      parsePublishWorkspaceSnapshot({ ...readySnapshot, approval: null }),
    ).toThrow(PublishWorkspaceGatewayError);
  });

  it("refuses a settled stage with no outcome to show", () => {
    expect(() =>
      parsePublishWorkspaceSnapshot({ ...readySnapshot, stage: "settled", approval: null }),
    ).toThrow(PublishWorkspaceGatewayError);
  });

  it("refuses an unsettled stage that already claims an outcome", () => {
    expect(() =>
      parsePublishWorkspaceSnapshot({ ...readySnapshot, outcome: "published" }),
    ).toThrow(PublishWorkspaceGatewayError);
  });

  it("never presents an already attempted publish as a retry", () => {
    expect(() =>
      parsePublishWorkspaceSnapshot({
        ...readySnapshot,
        stage: "settled",
        approval: null,
        outcome: "outcome_uncertain",
        retryable: true,
      }),
    ).toThrow(PublishWorkspaceGatewayError);
  });

  it("accepts a settled publish that may legitimately be retried", () => {
    const snapshot = parsePublishWorkspaceSnapshot({
      ...readySnapshot,
      stage: "settled",
      approval: null,
      outcome: "not_published",
      retryable: true,
    });

    expect(snapshot.retryable).toBe(true);
  });

  it("refuses approval text the operator could not read", () => {
    for (const approval of [
      { ...readySnapshot.approval, targetAccount: "" },
      { ...readySnapshot.approval, title: "   " },
      { ...readySnapshot.approval, description: "简介‮" },
      { ...readySnapshot.approval, confirmationId: "not-a-uuid" },
    ]) {
      expect(() => parsePublishWorkspaceSnapshot({ ...readySnapshot, approval })).toThrow(
        PublishWorkspaceGatewayError,
      );
    }
  });

  it("refuses anything that is not the projection at all", () => {
    for (const source of [null, undefined, "snapshot", 7, [], {}]) {
      expect(() => parsePublishWorkspaceSnapshot(source)).toThrow(PublishWorkspaceGatewayError);
    }
  });
});

describe("publish workspace copy", () => {
  it("names every platform, stage, availability and outcome in the operator's words", () => {
    expect(publishPlatformLabel("bilibili")).toBe("B站");
    expect(publishPlatformLabel("douyin")).toBe("抖音");
    expect(publishAvailabilityLabel("awaiting_configuration")).toBe("待配置");
    expect(publishStageLabel("awaiting_approval")).toBe("待你确认");
    expect(publishOutcomeLabel("outcome_uncertain")).toBe("结果待人工确认");
  });

  it("never says how a platform is reached", () => {
    const copy = [
      ...(["bilibili", "douyin"] as const).map(publishPlatformLabel),
      ...(["ready", "awaiting_configuration", "awaiting_sign_in", "unavailable"] as const).map(
        publishAvailabilityLabel,
      ),
      ...(
        ["idle", "preparing", "awaiting_approval", "publishing", "verifying", "settled"] as const
      ).map(publishStageLabel),
      ...(
        ["published", "outcome_uncertain", "not_published", "handed_off", "cancelled"] as const
      ).map(publishOutcomeLabel),
    ].join(" ");

    for (const upstream of ["browser", "playwright", "chromium", "api", "browser use"]) {
      expect(copy.toLowerCase()).not.toContain(upstream);
    }
  });
});
