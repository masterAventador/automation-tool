import type {
  PublishApprovalRequest,
  PublishRequest,
  PublishWorkspaceGateway,
  PublishWorkspaceSnapshot,
} from "../features/publishing/publish-workspace-gateway";
import { PublishWorkspaceGatewayError } from "../features/publishing/publish-workspace-gateway";

const CONFIRMATION_ID = "123e4567-e89b-42d3-a456-426614174007";
const TARGET_ACCOUNT = "自动化运营测试账号";

/**
 * PB-07 UI Harness: the publish states a real bridge can put the page in.
 *
 * This is a controlled test Adapter, not the bridge. It exists so the real user
 * path — open the page, start a publish, read the terms, confirm or cancel —
 * can be exercised in a browser; what it proves is the React business
 * projection, never that a publish actually reached a platform.
 */
export class TestHarnessPublishing implements PublishWorkspaceGateway {
  private snapshot: PublishWorkspaceSnapshot;

  constructor(private readonly outcome: "published" | "outcome_uncertain") {
    this.snapshot = {
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
  }

  async getWorkspace(): Promise<PublishWorkspaceSnapshot> {
    return this.snapshot;
  }

  async beginPublish(request: PublishRequest): Promise<PublishWorkspaceSnapshot> {
    if (request.platform !== "douyin") {
      throw new PublishWorkspaceGatewayError("bridge_unavailable");
    }
    this.snapshot = {
      ...this.snapshot,
      stage: "awaiting_approval",
      target: "douyin",
      outcome: null,
      retryable: false,
      // The terms come from the bridge, so the page renders the account it was
      // told about rather than anything it could have assembled itself.
      approval: {
        targetAccount: TARGET_ACCOUNT,
        videoSummary: request.videoSummary,
        title: request.title,
        description: request.description,
        confirmationId: CONFIRMATION_ID,
      },
      audit: [
        { step: "publish_started", platform: "douyin", confirmationId: null, outcome: null },
        {
          step: "approval_presented",
          platform: "douyin",
          confirmationId: CONFIRMATION_ID,
          outcome: null,
        },
      ],
    };
    return this.snapshot;
  }

  async approvePublish(request: PublishApprovalRequest): Promise<PublishWorkspaceSnapshot> {
    if (request.confirmationId !== CONFIRMATION_ID) {
      throw new PublishWorkspaceGatewayError("bridge_unavailable");
    }
    this.snapshot = {
      ...this.snapshot,
      stage: "settled",
      approval: null,
      outcome: this.outcome,
      // An attempted publish is never offered as a retry, whichever way it went.
      retryable: false,
      audit: [
        ...this.snapshot.audit,
        {
          step: "approval_given",
          platform: "douyin",
          confirmationId: CONFIRMATION_ID,
          outcome: null,
        },
        {
          step: "settled",
          platform: "douyin",
          confirmationId: null,
          outcome: this.outcome,
        },
      ],
    };
    return this.snapshot;
  }

  async cancelPublish(): Promise<PublishWorkspaceSnapshot> {
    this.snapshot = {
      ...this.snapshot,
      stage: "settled",
      approval: null,
      outcome: "cancelled",
      retryable: true,
      audit: [
        ...this.snapshot.audit,
        { step: "settled", platform: "douyin", confirmationId: null, outcome: "cancelled" },
      ],
    };
    return this.snapshot;
  }
}

export const HARNESS_SELECTED_VIDEO = {
  publishJobId: "423e4567-e89b-42d3-a456-426614174001",
  artifactPath: "/videos/harness-clip.mp4",
  videoSummary: "护肤知识讲解 · 12.4 MB",
  title: "三分钟讲清油皮护肤",
  description: "从洁面到防晒，按顺序讲一遍。",
} as const;
