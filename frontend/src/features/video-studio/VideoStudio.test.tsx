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
});
