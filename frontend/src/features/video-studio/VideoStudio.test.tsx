import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { MaterialVideoStudioGateway } from "./material-video-studio-gateway";
import { VideoStudio } from "./VideoStudio";

function gateway(): MaterialVideoStudioGateway {
  return {
    open: vi.fn().mockResolvedValue({
      state: "opened",
      modelId: "qwen3.7-max-2026-06-08",
    }),
    jobs: vi.fn().mockResolvedValue([]),
    cancel: vi.fn().mockResolvedValue(undefined),
    deleteArtifact: vi.fn().mockResolvedValue(undefined),
  };
}

describe("video studio shell", () => {
  it("exposes every planned page without inventing jobs or artifacts", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    expect(screen.getByRole("tab", { name: "新建视频" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "脚本与分镜" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "制作设置" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "预览" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "制作任务" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "成片" })).toBeVisible();
    expect(screen.getByRole("button", { name: "打开完整制作界面" })).toBeDisabled();
    expect(
      screen.getByText(
        "选择“智能素材成片”后可打开完整制作界面；“品牌动效成片”将在对应流程接入后开放。",
      ),
    ).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "制作任务" }));
    expect(screen.getByText("还没有真实制作任务"))
      .toBeVisible();
    expect(document.body).not.toHaveTextContent(/完成 100%|示例成片|假任务/u);

    await user.click(screen.getByRole("tab", { name: "成片" }));
    expect(screen.getByText("还没有已导入的成片"))
      .toBeVisible();
  });

  it("uses only product-facing Chinese names", () => {
    render(<VideoStudio gateway={gateway()} />);

    expect(document.body).not.toHaveTextContent(/moneyprinter|hyperframes|b-roll/iu);
  });

  it("helps ordinary users compare and select exactly two creation methods", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    const materialMethod = screen.getByRole("button", { name: /选择智能素材成片/u });
    const motionMethod = screen.getByRole("button", { name: /选择品牌动效成片/u });
    expect(materialMethod).toHaveAttribute("aria-pressed", "false");
    expect(motionMethod).toHaveAttribute("aria-pressed", "false");

    for (const label of [
      "最适合",
      "不适合",
      "举个例子",
      "外部服务",
      "本机处理",
      "预计耗时",
      "设备占用",
      "临时磁盘",
      "网络消耗",
      "数据与隐私",
    ]) {
      expect(screen.getAllByText(label)).toHaveLength(2);
    }

    await user.click(materialMethod);
    expect(materialMethod).toHaveAttribute("aria-pressed", "true");
    expect(motionMethod).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("已选择：智能素材成片")).toBeVisible();
    const openButton = screen.getByRole("button", { name: "打开完整制作界面" });
    expect(openButton).toBeEnabled();
    await user.click(openButton);
    expect(studioGateway.open).toHaveBeenCalledOnce();
    expect(await screen.findByText("完整制作界面已打开。")).toBeVisible();

    await user.click(motionMethod);
    expect(materialMethod).toHaveAttribute("aria-pressed", "false");
    expect(motionMethod).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("已选择：品牌动效成片")).toBeVisible();
    expect(openButton).toBeDisabled();

    expect(document.body).not.toHaveTextContent(/真人生成|网址转视频/iu);
  });

  it("shows only reconciled jobs and artifacts without inventing file paths", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    vi.mocked(studioGateway.jobs).mockResolvedValue([
      {
        renderJobId: "3d594650-b5f4-4498-8e38-0cf85d6dfa72",
        revision: 3,
        status: "running",
        progressPercent: 42,
        subject: "新品介绍",
        artifactId: null,
        artifactSizeBytes: null,
        failureCode: null,
      },
      {
        renderJobId: "01b70168-c90d-4ac7-938a-51eb4754f32a",
        revision: 4,
        status: "succeeded",
        progressPercent: 100,
        subject: "知识讲解",
        artifactId: "0f48954d-2df1-4168-8f33-b62c5772845a",
        artifactSizeBytes: 2 * 1024 * 1024,
        failureCode: null,
      },
    ]);
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("tab", { name: "制作任务" }));
    expect(await screen.findByText("新品介绍")).toBeVisible();
    expect(screen.getByText("制作中")).toBeVisible();
    expect(screen.getByRole("button", { name: "取消任务" })).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "成片" }));
    expect(await screen.findByText("2.0 MB", { exact: false })).toBeVisible();
    expect(screen.getByRole("button", { name: "删除成片" })).toBeVisible();
    expect(document.body).not.toHaveTextContent(/\/private\/|[A-Z]:\\/u);
  });
});
