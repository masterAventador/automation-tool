import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PublishWorkspace, type SelectedVideo } from "./PublishWorkspace";
import {
  PublishWorkspaceGatewayError,
  type PublishWorkspaceGateway,
  type PublishWorkspaceSnapshot,
} from "./publish-workspace-gateway";

const CONFIRMATION_ID = "123e4567-e89b-42d3-a456-426614174007";
const ARTIFACT_ID = "423e4567-e89b-42d3-a456-426614174001";

/**
 * A finished video the operator picked; the page never invents one.
 *
 * It is an identity, not a path. The page could not resolve a path to a real
 * file even if it held one, and holding one would mean the bridge accepted
 * whatever local file a page named.
 */
const selectedVideo: SelectedVideo = {
  artifactId: ARTIFACT_ID,
  videoSummary: "护肤知识讲解 · 12.4 MB",
};

const TITLE = "三分钟讲清油皮护肤";
const DESCRIPTION = "从洁面到防晒，按顺序讲一遍。";

/** Fill the copy the way an operator does, then hand back the publish button. */
async function writeTheCopy(): Promise<HTMLElement> {
  await userEvent.type(await screen.findByLabelText("标题"), TITLE);
  await userEvent.type(screen.getByLabelText("简介"), DESCRIPTION);
  return screen.getByRole("button", { name: /发布到抖音/ });
}

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
    audit: [],
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
    expect(screen.getByText(/还在接入中/, { exact: false })).toBeInTheDocument();
  });

  it("does not promise the operator something no part of this App can do", async () => {
    // The hint used to read "配置后即可使用". There is no credential entry
    // anywhere in the App and no authorization route behind it, so that told
    // the operator to go looking for a screen that does not exist.
    const { container } = render(<PublishWorkspace gateway={gatewayReturning(snapshot())} />);

    await screen.findByText("待配置");
    expect(container.textContent ?? "").not.toContain("配置后即可使用");
  });

  it("still lets the operator publish to the platform that is ready", async () => {
    const gateway = gatewayReturning(
      snapshot(),
      snapshot({ stage: "preparing", target: "douyin" }),
    );
    render(<PublishWorkspace gateway={gateway} selectedVideo={selectedVideo} />);

    await userEvent.click(await writeTheCopy());

    expect(gateway.beginPublish).toHaveBeenCalledWith({
      platform: "douyin",
      artifactId: ARTIFACT_ID,
      videoSummary: selectedVideo.videoSummary,
      title: TITLE,
      description: DESCRIPTION,
    });
    expect(await screen.findByText("准备中")).toBeInTheDocument();
  });

  it("offers no way to start a publish on an unconfigured platform", async () => {
    render(<PublishWorkspace gateway={gatewayReturning(snapshot())} selectedVideo={selectedVideo} />);

    await screen.findByText("B站");
    expect(screen.queryByRole("button", { name: /发布到B站/ })).toBeNull();
  });

  it("offers no way to publish when no video has been selected", async () => {
    // A button that would post an unspecified file is worse than no button.
    render(<PublishWorkspace gateway={gatewayReturning(snapshot())} />);

    await screen.findByText("抖音");
    expect(screen.queryByRole("button", { name: /发布到抖音/ })).toBeNull();
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

    // The page has no publish job identity to send, and inventing one out of
    // the confirmation identity is what it used to do.
    expect(gateway.approvePublish).toHaveBeenCalledWith({
      confirmationId: CONFIRMATION_ID,
    });
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
        selectedVideo={selectedVideo}
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

    await userEvent.type(await screen.findByLabelText("标题"), TITLE);
    await userEvent.type(screen.getByLabelText("简介"), DESCRIPTION);
    expect(screen.getByRole("button", { name: /重新发布/ })).toBeEnabled();
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

  it("shows which video is about to be published, and lets it be swapped", async () => {
    const changeSelection = vi.fn();
    render(
      <PublishWorkspace
        gateway={gatewayReturning(snapshot())}
        selectedVideo={selectedVideo}
        onChangeSelection={changeSelection}
      />,
    );

    const pending = await screen.findByRole("group", { name: "待发布视频" });
    expect(within(pending).getByText("护肤知识讲解 · 12.4 MB")).toBeInTheDocument();
    await userEvent.click(within(pending).getByRole("button", { name: /换一个/ }));

    expect(changeSelection).toHaveBeenCalledTimes(1);
  });

  it("will not publish a video with no copy written for it", async () => {
    // The executor refuses unreadable copy, and so does the bridge. Finding
    // that out after the operations browser has already been opened wastes the
    // one visible browser the operator has.
    const gateway = gatewayReturning(snapshot());
    render(<PublishWorkspace gateway={gateway} selectedVideo={selectedVideo} />);

    expect(await screen.findByRole("button", { name: /发布到抖音/ })).toBeDisabled();

    await userEvent.type(screen.getByLabelText("标题"), TITLE);
    expect(screen.getByRole("button", { name: /发布到抖音/ })).toBeDisabled();

    await userEvent.type(screen.getByLabelText("简介"), DESCRIPTION);
    expect(screen.getByRole("button", { name: /发布到抖音/ })).toBeEnabled();
    expect(gateway.beginPublish).not.toHaveBeenCalled();
  });

  /**
   * 按钮变灰是有意的，但界面从来没说过要填什么它才会亮。
   *
   * 实测这个按钮 `title` 和 `aria-describedby` 都是 null。用户填完标题按钮还是灰的
   * （还差简介），此时屏幕上没有任何一句话解释差在哪。
   *
   * 也不能用 `title` 修：浏览器对 disabled 的按钮不弹原生 tooltip。
   */
  it("says what is still missing while the publish button is greyed out", async () => {
    render(<PublishWorkspace gateway={gatewayReturning(snapshot())} selectedVideo={selectedVideo} />);

    const button = await screen.findByRole("button", { name: /发布到抖音/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(/标题/);
    expect(button).toHaveAccessibleDescription(/简介/);

    await userEvent.type(screen.getByLabelText("标题"), TITLE);
    await userEvent.type(screen.getByLabelText("简介"), DESCRIPTION);

    // 能按了就不该再挂着一句「还差什么」。
    expect(screen.getByRole("button", { name: /发布到抖音/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /发布到抖音/ })).not.toHaveAccessibleDescription();
  });

  it("offers no copy to write when no video has been selected", async () => {
    render(<PublishWorkspace gateway={gatewayReturning(snapshot())} />);

    await screen.findByText("抖音");
    expect(screen.queryByLabelText("标题")).toBeNull();
    expect(screen.queryByRole("group", { name: "待发布视频" })).toBeNull();
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
