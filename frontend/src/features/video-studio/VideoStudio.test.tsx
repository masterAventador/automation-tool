import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import contract from "../../../../contracts/video/motion-style-presets.v1.json";
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

  it("keeps the settings page gated until the brand motion method is selected", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("tab", { name: "制作设置" }));
    expect(screen.getByText("尚未选择制作方式")).toBeVisible();
    expect(screen.queryByRole("radiogroup", { name: "选择整体画面风格" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "新建视频" }));
    await user.click(screen.getByRole("button", { name: /选择智能素材成片/u }));
    await user.click(screen.getByRole("tab", { name: "制作设置" }));
    expect(
      screen.getByText("智能素材成片的素材来源、配音和字幕设置在完整制作界面中调整。"),
    ).toBeVisible();
    expect(screen.queryByRole("radiogroup", { name: "选择整体画面风格" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "新建视频" }));
    await user.click(screen.getByRole("button", { name: /选择品牌动效成片/u }));
    await user.click(screen.getByRole("tab", { name: "制作设置" }));
    expect(screen.getByRole("radiogroup", { name: "选择整体画面风格" })).toBeVisible();
  });

  it("recommends three styles first and still lets users inspect all twelve", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: /选择品牌动效成片/u }));
    await user.click(screen.getByRole("tab", { name: "制作设置" }));

    const group = screen.getByRole("radiogroup", { name: "选择整体画面风格" });
    const recommended = within(group).getAllByRole("radio");
    expect(recommended).toHaveLength(3);
    expect(within(group).getAllByText("推荐")).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "查看全部 12 套风格" }));
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(12);
    expect(radios.map((radio) => radio.getAttribute("aria-label"))).toEqual(
      contract.presets.map((preset) => preset.displayName),
    );
    for (const radio of radios) {
      expect(radio).toHaveAttribute("aria-checked", "false");
      expect(within(radio).getByText("适用场景")).toBeVisible();
      expect(within(radio).getByText("风格标签")).toBeVisible();
      expect(within(radio).getByText("实际内容预览")).toBeVisible();
    }
    expect(screen.getByText("尚未选择风格")).toBeVisible();

    expect(group).not.toHaveTextContent(
      /biennale|blockframe|blue-professional|bold-poster|broadside|capsule|cartesian|cobalt-grid|coral|creative-mode|daisy-days|editorial-forest|code-editorial/iu,
    );
    expect(document.body).not.toHaveTextContent(/hyperframes|moneyprinter|b-roll/iu);
  });

  it("supports keyboard focus, arrow navigation and Enter/Space selection", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: /选择品牌动效成片/u }));
    await user.click(screen.getByRole("tab", { name: "制作设置" }));
    await user.click(screen.getByRole("button", { name: "查看全部 12 套风格" }));

    const group = screen.getByRole("radiogroup", { name: "选择整体画面风格" });
    const radios = within(group).getAllByRole("radio");
    const first = contract.presets[0]!;
    const second = contract.presets[1]!;
    const last = contract.presets[11]!;

    radios[0]!.focus();
    expect(radios[0]).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(radios[1]).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(radios[1]).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(`已选择风格：${second.displayName}`)).toBeVisible();

    await user.keyboard("{ArrowDown}");
    expect(radios[2]).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(radios[1]).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(radios[0]).toHaveFocus();
    await user.keyboard(" ");
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[1]).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(`已选择风格：${first.displayName}`)).toBeVisible();

    await user.keyboard("{End}");
    expect(radios[11]).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByText(`已选择风格：${last.displayName}`)).toBeVisible();
    await user.keyboard("{Home}");
    expect(radios[0]).toHaveFocus();

    const checked = radios.filter(
      (radio) => radio.getAttribute("aria-checked") === "true",
    );
    expect(checked).toHaveLength(1);
  });

  it("previews actual copy with brand colors, font and a local logo", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: /选择品牌动效成片/u }));
    await user.click(screen.getByRole("tab", { name: "制作设置" }));

    const headline = screen.getByRole("textbox", { name: "预览标题" });
    const body = screen.getByRole("textbox", { name: "预览正文" });
    await user.clear(headline);
    await user.type(headline, "本周销售增长 38%");
    await user.clear(body);
    await user.type(body, "华东区和续费业务共同推动增长。");
    await user.type(screen.getByRole("textbox", { name: "品牌主色" }), "#1234ab");
    await user.type(screen.getByRole("textbox", { name: "品牌辅助色" }), "#f2eadb");
    await user.type(screen.getByRole("textbox", { name: "品牌字体" }), "Acme Sans");
    await user.upload(
      screen.getByLabelText("品牌 Logo 文件"),
      new File(["logo"], "acme-logo.png", { type: "image/png" }),
    );

    const preview = screen.getByRole("region", { name: "实际内容风格预览" });
    expect(within(preview).getByText("本周销售增长 38%")).toBeVisible();
    expect(within(preview).getByText("华东区和续费业务共同推动增长。")).toBeVisible();
    expect(within(preview).getByText("Acme Sans")).toBeVisible();
    expect(within(preview).getByText(/acme-logo\.png/u)).toBeVisible();
    expect(
      await within(preview).findByRole("img", { name: "品牌 Logo 预览" }),
    ).toHaveAttribute("src", expect.stringMatching(/^data:image\/png;base64,/u));
    expect(preview).toHaveStyle({ backgroundColor: "#f2eadb" });
    expect(within(preview).getByText("本周销售增长 38%")).toHaveStyle({
      color: "#1234ab",
      fontFamily: "Acme Sans",
    });
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
