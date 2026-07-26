import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import contract from "../../../../contracts/video/motion-style-presets.v1.json";
import terminology from "../../../../contracts/quality/user-facing-terminology.v1.json";
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
    submitMotionDraft: vi.fn().mockResolvedValue({
      renderJobId: "f89d8f18-6b4e-4f5a-8325-8da45f71d7e2",
      revision: 1,
      status: "queued",
      progressPercent: 5,
      subject: "新品发布",
      styleDisplayName: "商务蓝",
      artifactId: null,
      artifactSizeBytes: null,
      failureCode: null,
    }),
    motionJobs: vi.fn().mockResolvedValue([]),
    cancelMotionRenderJob: vi.fn().mockResolvedValue(undefined),
    readMotionArtifact: vi.fn().mockResolvedValue({
      artifactId: "2c29395b-1015-43ae-84a7-6f1901caac09",
      mediaType: "video/mp4",
      base64: "AAAA",
    }),
    deleteMotionArtifact: vi.fn().mockResolvedValue(undefined),
  };
}

describe("video studio shell", () => {
  it("exposes every planned page without inventing jobs or artifacts", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    expect(screen.getByRole("tab", { name: "新建视频" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "脚本与分镜" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "制作设置" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "动效零件" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "预览" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "制作任务" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "成片" })).toBeVisible();
    expect(screen.getByRole("button", { name: "打开完整制作界面" })).toBeDisabled();
    expect(
      screen.getByText(
        "“智能素材成片”在独立完整界面制作；“品牌动效成片”在当前 App 内编辑和预览。",
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

    // The comparison questions come from the terminology contract, which is
    // also what the static gate and the real App acceptance read.
    for (const label of terminology.videoCreationMethodCardLabels) {
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

  it("tells the user a still-image result apart from a broken render", async () => {
    // "check the video components and disk space" is the wrong instruction for
    // a film that rendered fine and simply never moved; the two failures need
    // different words or the user retries the thing that was never broken.
    const user = userEvent.setup();
    const studioGateway = gateway();
    vi.mocked(studioGateway.motionJobs).mockResolvedValue([
      {
        renderJobId: "3d594650-b5f4-4498-8e38-0cf85d6dfa72",
        revision: 3,
        status: "failed",
        progressPercent: 55,
        subject: "静止的片子",
        styleDisplayName: "专业蓝",
        artifactId: null,
        artifactSizeBytes: null,
        failureCode: "static_render",
      },
    ]);
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("tab", { name: "制作任务" }));
    expect(await screen.findByText("静止的片子")).toBeVisible();
    expect(screen.getByText(/画面自始至终没有变化/u)).toBeVisible();
    expect(document.body).not.toHaveTextContent(/磁盘空间/u);
  });

  it("lets the user choose the beat count and the seconds each beat runs", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: /选择品牌动效成片/u }));
    await user.click(screen.getByRole("tab", { name: "脚本与分镜" }));

    // The retired fixed template announced itself; nothing may claim a fixed
    // length or a fixed beat count any more.
    expect(document.body).not.toHaveTextContent(/每段 1 秒|三段/u);
    expect(screen.getByText(/^共 \d+ 段 · 每段 \d+ 秒 · 成片约 \d+ 秒$/u)).toBeVisible();

    await user.clear(screen.getByLabelText("段数"));
    await user.type(screen.getByLabelText("段数"), "5");
    await user.clear(screen.getByLabelText("每段时长（秒）"));
    await user.type(screen.getByLabelText("每段时长（秒）"), "4");

    expect(screen.getAllByRole("textbox", { name: /第 \d+ 段标题/u })).toHaveLength(5);
    expect(screen.getAllByRole("textbox", { name: /第 \d+ 段字幕/u })).toHaveLength(5);
    expect(screen.getByText("共 5 段 · 每段 4 秒 · 成片约 20 秒")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "制作设置" }));
    await user.click(screen.getByRole("radio", { name: "专业蓝" }));
    await user.click(screen.getByRole("tab", { name: "预览" }));
    expect(screen.getByRole("region", { name: "品牌动效播放预览" })).toHaveTextContent(
      "第 1 段 / 5",
    );

    await user.click(screen.getByRole("button", { name: "提交本机渲染" }));
    expect(studioGateway.submitMotionDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        secondsPerBeat: 4,
        beats: expect.arrayContaining([expect.objectContaining({ title: "第 5 段" })]),
      }),
    );
    expect(vi.mocked(studioGateway.submitMotionDraft).mock.calls[0]![0].beats).toHaveLength(5);
  });

  it("refuses to submit a beat count and length whose product exceeds the render budget", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: /选择品牌动效成片/u }));
    await user.click(screen.getByRole("tab", { name: "脚本与分镜" }));
    await user.clear(screen.getByLabelText("段数"));
    await user.type(screen.getByLabelText("段数"), "5");
    await user.clear(screen.getByLabelText("每段时长（秒）"));
    await user.type(screen.getByLabelText("每段时长（秒）"), "6");

    const warning = screen.getByText(/成片总长最多 20 秒/u);
    expect(warning).toBeVisible();
    expect(warning.textContent).toContain("30 秒");

    await user.click(screen.getByRole("tab", { name: "制作设置" }));
    await user.click(screen.getByRole("radio", { name: "专业蓝" }));
    await user.click(screen.getByRole("tab", { name: "预览" }));
    expect(screen.getByRole("button", { name: "提交本机渲染" })).toBeDisabled();
    expect(studioGateway.submitMotionDraft).not.toHaveBeenCalled();
  });

  it("edits a three-beat manual draft, plays the real preview and submits it without claiming AI", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: /选择品牌动效成片/u }));
    expect(screen.getByRole("textbox", { name: "视频标题" })).toBeEnabled();
    expect(screen.getByText("固定模板手工制作")).toBeVisible();
    expect(screen.getByText("当前没有调用视频创作模型")).toBeVisible();
    expect(document.body).not.toHaveTextContent(/网址|URL|抓取/u);

    await user.click(screen.getByRole("tab", { name: "脚本与分镜" }));
    const beatTitles = screen.getAllByRole("textbox", { name: /第 \d 段标题/u });
    const beatCaptions = screen.getAllByRole("textbox", { name: /第 \d 段字幕/u });
    expect(beatTitles).toHaveLength(3);
    expect(beatCaptions).toHaveLength(3);
    await user.clear(beatTitles[0]!);
    await user.type(beatTitles[0]!, "增长看得见");
    await user.clear(beatCaptions[0]!);
    await user.type(beatCaptions[0]!, "字幕：本周销售增长 38%");

    await user.click(screen.getByRole("tab", { name: "制作设置" }));
    await user.click(screen.getByRole("radio", { name: "专业蓝" }));
    await user.click(screen.getByRole("tab", { name: "预览" }));
    expect(screen.getByRole("region", { name: "品牌动效播放预览" })).toHaveTextContent(
      "增长看得见",
    );
    await user.click(screen.getByRole("button", { name: "播放预览" }));
    expect(await screen.findByText("第 2 段 / 3")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "提交本机渲染" }));
    expect(studioGateway.submitMotionDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        creationMode: "manual_template_v1",
        beats: expect.arrayContaining([
          expect.objectContaining({
            title: "增长看得见",
            caption: "字幕：本周销售增长 38%",
          }),
        ]),
      }),
    );
  });

  it("shows native motion progress, cancels it and plays the imported MP4 artifact", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    vi.mocked(studioGateway.motionJobs).mockResolvedValue([
      {
        renderJobId: "f89d8f18-6b4e-4f5a-8325-8da45f71d7e2",
        revision: 2,
        status: "rendering",
        progressPercent: 55,
        subject: "新品发布",
        styleDisplayName: "商务蓝",
        artifactId: null,
        artifactSizeBytes: null,
        failureCode: null,
      },
      {
        renderJobId: "d03fe6e3-cf14-41e8-a2a0-1d870db1a122",
        revision: 4,
        status: "succeeded",
        progressPercent: 100,
        subject: "季度增长",
        styleDisplayName: "商务蓝",
        artifactId: "2c29395b-1015-43ae-84a7-6f1901caac09",
        artifactSizeBytes: 4096,
        failureCode: null,
      },
    ]);
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("tab", { name: "制作任务" }));
    expect(await screen.findByText("逐帧渲染中")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "取消品牌动效任务" }));
    await user.click(screen.getByRole("button", { name: /确\s*定/u }));
    expect(studioGateway.cancelMotionRenderJob).toHaveBeenCalledWith(
      "f89d8f18-6b4e-4f5a-8325-8da45f71d7e2",
    );

    await user.click(screen.getByRole("tab", { name: "成片" }));
    await user.click(await screen.findByRole("button", { name: "播放季度增长" }));
    expect(studioGateway.readMotionArtifact).toHaveBeenCalledWith(
      "2c29395b-1015-43ae-84a7-6f1901caac09",
    );
    expect(await screen.findByLabelText("季度增长成片播放器")).toHaveAttribute(
      "src",
      "data:video/mp4;base64,AAAA",
    );
  });

  it("explains that 动效零件 belongs to 品牌动效成片 when another method is picked", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: "选择智能素材成片" }));
    await user.click(screen.getByRole("tab", { name: "动效零件" }));

    expect(screen.queryByRole("region", { name: "动效零件目录" })).toBeNull();
    expect(screen.getByText(/动效零件只属于“品牌动效成片”/)).toBeVisible();
  });

  it("browses the 134 parts catalog and keeps every part visible", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.click(screen.getByRole("tab", { name: "动效零件" }));
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    expect(within(browser).getAllByRole("listitem")).toHaveLength(134);
    expect(
      screen.getByText(/动效零件与 12 套整体风格不同/),
    ).toBeVisible();
    expect(within(browser).getByText("数据图表动画")).toBeVisible();
  });

  // The App can only submit 固定模板手工制作, and that renderer never reads a
  // part id, so ticking one must be impossible rather than quietly ignored.
  it("says in the 动效零件 tab that the submitted job ignores part selections", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.click(screen.getByRole("tab", { name: "动效零件" }));

    expect(screen.getByText(/本次制作方式不会用到零件选择/)).toBeVisible();
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    expect(
      within(browser).queryAllByRole("button", { name: "加入第 1 段" }),
    ).toHaveLength(0);
    const actions = within(browser).getAllByRole("button", {
      name: "本次制作不使用",
    });
    expect(actions).toHaveLength(134);
    for (const action of actions) expect(action).toBeDisabled();

    const overrides = screen.getByRole("region", { name: "分镜零件选用" });
    expect(within(overrides).queryAllByText(/已选/)).toHaveLength(0);
    for (const button of within(overrides).getAllByRole("button", {
      name: "自动推荐",
    })) {
      expect(button).toBeDisabled();
    }
  });
});
