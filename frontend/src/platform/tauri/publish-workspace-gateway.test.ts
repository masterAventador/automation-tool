import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { PublishWorkspaceGatewayError } from "../../features/publishing/publish-workspace-gateway";
import { TauriPublishWorkspaceGateway } from "./publish-workspace-gateway";

const CONFIRMATION_ID = "123e4567-e89b-42d3-a456-426614174007";

const idle = {
  platforms: [
    { platform: "bilibili", availability: "awaiting_configuration" },
    { platform: "douyin", availability: "ready" },
  ],
  stage: "idle",
  target: null,
  approval: null,
  outcome: null,
  retryable: false,
  audit: [],
};

const awaitingApproval = {
  ...idle,
  stage: "awaiting_approval",
  target: "douyin",
  approval: {
    targetAccount: "自动化运营测试账号",
    videoSummary: "护肤知识讲解 · 12.4 MB",
    title: "三分钟讲清油皮护肤",
    description: "从洁面到防晒，按顺序讲一遍。",
    confirmationId: CONFIRMATION_ID,
  },
  audit: [
    { step: "publish_started", platform: "douyin", confirmationId: null, outcome: null },
  ],
};

describe("Tauri publish workspace gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("reads the workspace through a fixed zero-argument Command", async () => {
    invoke.mockResolvedValueOnce(idle);

    await expect(new TauriPublishWorkspaceGateway().getWorkspace()).resolves.toMatchObject({
      stage: "idle",
    });
    expect(invoke.mock.calls).toEqual([["get_publish_workspace", {}]]);
  });

  it("sends the whole publish request the bridge needs to open the page", async () => {
    invoke.mockResolvedValueOnce(awaitingApproval);

    await new TauriPublishWorkspaceGateway().beginPublish({
      platform: "douyin",
      publishJobId: "423e4567-e89b-42d3-a456-426614174001",
      artifactPath: "/videos/clip.mp4",
      videoSummary: "护肤知识讲解 · 12.4 MB",
      title: "三分钟讲清油皮护肤",
      description: "从洁面到防晒，按顺序讲一遍。",
    });

    expect(invoke.mock.calls).toEqual([
      [
        "begin_publish",
        {
          platform: "douyin",
          publishJobId: "423e4567-e89b-42d3-a456-426614174001",
          artifactPath: "/videos/clip.mp4",
          videoSummary: "护肤知识讲解 · 12.4 MB",
          title: "三分钟讲清油皮护肤",
          description: "从洁面到防晒，按顺序讲一遍。",
        },
      ],
    ]);
  });

  it("spends exactly the confirmation the bridge issued", async () => {
    invoke.mockResolvedValueOnce({
      ...idle,
      stage: "settled",
      target: "douyin",
      outcome: "published",
    });

    await new TauriPublishWorkspaceGateway().approvePublish({
      publishJobId: "423e4567-e89b-42d3-a456-426614174001",
      confirmationId: CONFIRMATION_ID,
    });

    expect(invoke.mock.calls).toEqual([
      [
        "approve_publish",
        {
          publishJobId: "423e4567-e89b-42d3-a456-426614174001",
          confirmationId: CONFIRMATION_ID,
        },
      ],
    ]);
  });

  it("cancels through a fixed zero-argument Command", async () => {
    invoke.mockResolvedValueOnce({
      ...idle,
      stage: "settled",
      target: "douyin",
      outcome: "cancelled",
      retryable: true,
    });

    await new TauriPublishWorkspaceGateway().cancelPublish();

    expect(invoke.mock.calls).toEqual([["cancel_publish", {}]]);
  });

  it("refuses a projection the bridge should never have produced", async () => {
    invoke.mockResolvedValueOnce({ ...idle, mechanism: "browser_use" });

    await expect(new TauriPublishWorkspaceGateway().getWorkspace()).rejects.toBeInstanceOf(
      PublishWorkspaceGatewayError,
    );
  });

  it("turns a transport failure into one the page can explain", async () => {
    invoke.mockRejectedValueOnce({ code: "process_unavailable", retryable: true });

    await expect(new TauriPublishWorkspaceGateway().getWorkspace()).rejects.toBeInstanceOf(
      PublishWorkspaceGatewayError,
    );
  });
});
