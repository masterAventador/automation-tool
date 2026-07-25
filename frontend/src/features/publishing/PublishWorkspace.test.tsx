import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PublishWorkspace } from "./PublishWorkspace";
import {
  PublishWorkspaceGatewayError,
  type PublishWorkspaceGateway,
  type PublishWorkspaceSnapshot,
} from "./publish-workspace-gateway";

const CONFIRMATION_ID = "123e4567-e89b-42d3-a456-426614174007";

function snapshot(overrides: Partial<PublishWorkspaceSnapshot> = {}): PublishWorkspaceSnapshot {
  return {
    platforms: [
      { platform: "bilibili", availability: "awaiting_configuration" },
      { platform: "douyin", availability: "ready" },
    ],
    stage: "idle",
    target: null,
    approval: null,
    outcome: null,
    retryable: false,
    ...overrides,
  } as PublishWorkspaceSnapshot;
}

const approval = {
  targetAccount: "自动化运营测试账号",
  videoSummary: "护肤知识讲解 · 12.4 MB",
  title: "三分钟讲清油皮护肤",
  description: "从洁面到防晒，按顺序讲一遍。",
  confirmationId: CONFIRMATION_ID,
};

function gatewayReturning(...snapshots: PublishWorkspaceSnapshot[]): PublishWorkspaceGateway {
  const queue = [...snapshots];
  const next = () => queue.length > 1 ? queue.shift()! : queue[0]!;
  return {
    getWorkspace: vi.fn(async () => next()),
    beginPublish: vi.fn(async () => next()),
    approvePublish: vi.fn(async () => next()),
    cancelPublish: vi.fn(async () => next()),
  };
}

describe("publish workspace", () => {
  it("lists both platforms and says which one is not configured yet", async () => {
    render(<PublishWorkspace gateway={gatewayReturning(snapshot())} />);

    expect(await screen.findByText("B站")).toBeInTheDocument();
    expect(screen.getByText("抖音")).toBeInTheDocument();
    expect(screen.getByText("待配置")).toBeInTheDocument();
    expect(
      screen.getByText(/还没有配置发布凭据/, { exact: false }),
    ).toBeInTheDocument();
  });

  it("still lets the operator publish to the platform that is ready", async () => {
    const gateway = gatewayReturning(
      snapshot(),
      snapshot({ stage: "preparing", target: "douyin" }),
    );
    render(<PublishWorkspace gateway={gateway} />);

    await userEvent.click(await screen.findByRole("button", { name: /发布到抖音/ }));

    expect(gateway.beginPublish).toHaveBeenCalledWith("douyin");
    expect(await screen.findByText("准备中")).toBeInTheDocument();
  });

  it("offers no way to start a publish on an unconfigured platform", async () => {
    render(<PublishWorkspace gateway={gatewayReturning(snapshot())} />);

    await screen.findByText("B站");
    expect(screen.queryByRole("button", { name: /发布到B站/ })).toBeNull();
  });

  it("shows the account, the video and the copy before anything is published", async () => {
    render(
      <PublishWorkspace
        gateway={gatewayReturning(
          snapshot({ stage: "awaiting_approval", target: "douyin", approval }),
        )}
      />,
    );

    const critical = await screen.findByRole("group", { name: "确认发布内容" });
    expect(within(critical).getByText("自动化运营测试账号")).toBeInTheDocument();
    expect(within(critical).getByText("护肤知识讲解 · 12.4 MB")).toBeInTheDocument();
    expect(within(critical).getByText("三分钟讲清油皮护肤")).toBeInTheDocument();
    expect(within(critical).getByText("从洁面到防晒，按顺序讲一遍。")).toBeInTheDocument();
  });

  it("spends the approval the executor issued, not one the page invented", async () => {
    const gateway = gatewayReturning(
      snapshot({ stage: "awaiting_approval", target: "douyin", approval }),
      snapshot({ stage: "publishing", target: "douyin" }),
    );
    render(<PublishWorkspace gateway={gateway} />);

    await userEvent.click(await screen.findByRole("button", { name: /确认发布/ }));

    expect(gateway.approvePublish).toHaveBeenCalledWith(CONFIRMATION_ID);
    expect(await screen.findByText("发布中")).toBeInTheDocument();
  });

  it("can cancel before the approval is spent", async () => {
    const gateway = gatewayReturning(
      snapshot({ stage: "awaiting_approval", target: "douyin", approval }),
      snapshot({ stage: "settled", target: "douyin", outcome: "cancelled", retryable: true }),
    );
    render(<PublishWorkspace gateway={gateway} />);

    await userEvent.click(await screen.findByRole("button", { name: /取\s?消/ }));

    expect(gateway.cancelPublish).toHaveBeenCalled();
    expect(await screen.findByText("已取消")).toBeInTheDocument();
  });

  it("offers no cancel once the publish is in flight", async () => {
    render(
      <PublishWorkspace
        gateway={gatewayReturning(snapshot({ stage: "publishing", target: "douyin" }))}
      />,
    );

    await screen.findByText("发布中");
    expect(screen.queryByRole("button", { name: /取\s?消/ })).toBeNull();
  });

  it("never offers to repeat a publish whose result is unknown", async () => {
    render(
      <PublishWorkspace
        gateway={gatewayReturning(
          snapshot({
            stage: "settled",
            target: "douyin",
            outcome: "outcome_uncertain",
            retryable: false,
          }),
        )}
      />,
    );

    expect(await screen.findByText("结果待人工确认")).toBeInTheDocument();
    expect(screen.getByText(/系统不会自动重试/, { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重新发布/ })).toBeNull();
  });

  it("offers to publish again only when nothing was attempted", async () => {
    render(
      <PublishWorkspace
        gateway={gatewayReturning(
          snapshot({
            stage: "settled",
            target: "douyin",
            outcome: "not_published",
            retryable: true,
          }),
        )}
      />,
    );

    expect(await screen.findByRole("button", { name: /重新发布/ })).toBeInTheDocument();
  });

  it("keeps the module usable when the bridge cannot be read", async () => {
    const gateway: PublishWorkspaceGateway = {
      getWorkspace: vi.fn(async () => {
        throw new PublishWorkspaceGatewayError("projection_rejected");
      }),
      beginPublish: vi.fn(),
      approvePublish: vi.fn(),
      cancelPublish: vi.fn(),
    };
    render(<PublishWorkspace gateway={gateway} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/发布状态/);
    await userEvent.click(screen.getByRole("button", { name: /重\s?试/ }));
    await waitFor(() => expect(gateway.getWorkspace).toHaveBeenCalledTimes(2));
  });

  it("never tells the operator how a platform is reached", async () => {
    const { container } = render(
      <PublishWorkspace
        gateway={gatewayReturning(
          snapshot({ stage: "awaiting_approval", target: "douyin", approval }),
        )}
      />,
    );

    await screen.findByRole("group", { name: "确认发布内容" });
    const rendered = container.textContent?.toLowerCase() ?? "";
    for (const upstream of ["browser use", "playwright", "chromium", "api", "browser_use"]) {
      expect(rendered).not.toContain(upstream);
    }
  });
});
