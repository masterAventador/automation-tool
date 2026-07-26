import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import contract from "../../../../contracts/video/motion-style-presets.v1.json";
import durationContract from "../../../../contracts/video/motion-storyboard-duration.v1.json";
import { motionSpokenDuration } from "./motion-duration";
import terminology from "../../../../contracts/quality/user-facing-terminology.v1.json";
import {
  MaterialVideoStudioGatewayError,
  type MaterialVideoStudioGateway,
} from "./material-video-studio-gateway";
import { MOTION_BRIEF_FILM_SECONDS } from "./motion-one-sentence";
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
    submitMotionBrief: vi.fn().mockResolvedValue({
      renderJobId: "b1f0d0c6-1d2f-4a0e-9c3a-2b6f5e7d8a90",
      revision: 1,
      status: "queued",
      progressPercent: 5,
      subject: "用蓝色商务风做一段本周销售增长说明",
      styleDisplayName: "一句话自动制作",
      artifactId: null,
      artifactSizeBytes: null,
      failureCode: null,
    }),
    readMaterialArtifact: vi.fn().mockResolvedValue({
      artifactId: "0f48954d-2df1-4168-8f33-b62c5772845a",
      mediaType: "video/mp4",
      base64: "BBBB",
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

  /**
   * 成片页是「做完之后」唯一的落脚点，发布页却在另一个页面。
   *
   * 没有这一步，用户做完一条视频就走到了死路：发布页只会说「还没有选定要发布的视频」，
   * 而成片页不提供任何把它送过去的办法。交接必须带上 artifactId（发布端凭它取件）和
   * 一句人能看懂的说明（发布端把它显示给用户确认发的是哪一条）。
   */
  it("hands a finished video on to publishing", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    const publish = vi.fn();
    vi.mocked(studioGateway.motionJobs).mockResolvedValue([
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
    vi.mocked(studioGateway.jobs).mockResolvedValue([
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
    render(<VideoStudio gateway={studioGateway} onPublishArtifact={publish} />);

    await user.click(screen.getByRole("tab", { name: "成片" }));
    await user.click(await screen.findByRole("button", { name: "发布季度增长" }));
    expect(publish).toHaveBeenCalledWith({
      artifactId: "2c29395b-1015-43ae-84a7-6f1901caac09",
      videoSummary: "季度增长 · 品牌动效成片",
    });

    // 两种制作方式产出的成片都存成同一种可发布 Artifact，成片页不能只放行其中一种。
    await user.click(screen.getByRole("button", { name: "发布知识讲解" }));
    expect(publish).toHaveBeenLastCalledWith({
      artifactId: "0f48954d-2df1-4168-8f33-b62c5772845a",
      videoSummary: "知识讲解 · 智能素材成片",
    });
  });

  /**
   * 「一句话生成视频并且可以本地预览」是这次客户 Demo 的底线，而这条底线上更接近可用的
   * 是「智能素材成片」——它的一句话生成能力来自完整制作界面。可是做完之后，成片页只给
   * 「品牌动效成片」放了播放器：素材成片只能删除或送去发布，用户在 App 里根本看不到自己
   * 刚做完的视频长什么样，只能去别处找文件。两种制作方式产出的是同一种 MP4 成片，
   * 预览不应该只认其中一种。
   */
  it("plays a finished smart-material video inside the App", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    vi.mocked(studioGateway.jobs).mockResolvedValue([
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

    await user.click(screen.getByRole("tab", { name: "成片" }));
    await user.click(await screen.findByRole("button", { name: "播放知识讲解" }));
    expect(studioGateway.readMaterialArtifact).toHaveBeenCalledWith(
      "0f48954d-2df1-4168-8f33-b62c5772845a",
    );
    expect(await screen.findByLabelText("知识讲解成片播放器")).toHaveAttribute(
      "src",
      "data:video/mp4;base64,BBBB",
    );
  });

  /**
   * 客户 Demo 的底线是「一句话生成视频」。品牌动效线此前只有固定模板手工制作：
   * 用户要自己写段数、每段标题和字幕，那不是一句话，是填表。
   * 这条用例守住真正的一句话入口：描述一句 → 提交 → 进「制作任务」看进度。
   */
  it("submits a one-sentence brief for automatic authoring", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.clear(screen.getByLabelText("一句话视频需求"));
    await user.type(
      screen.getByLabelText("一句话视频需求"),
      "用蓝色商务风做一段本周销售增长说明",
    );
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    expect(studioGateway.submitMotionBrief).toHaveBeenCalledWith(
      expect.objectContaining({
        creationMode: "one_sentence_v1",
        brief: "用蓝色商务风做一段本周销售增长说明",
      }),
    );
    expect(
      await screen.findByText("已提交一句话自动制作，已经转到「制作任务」，本机渲染开始了。"),
    ).toBeVisible();
  });

  /**
   * 一句话卡片必须在点按钮之前就说清楚会得到多长的片子。
   *
   * 这个入口没有片长控件，成片长度固定。客户在演示现场随口说「做一个三分钟的
   * 产品介绍」，那句话会作为描述被接受，然后系统安静地做出十几秒的片子——
   * 需求被丢掉且不给任何提示。这条用例把界面上的数字和真正提交的时长绑在一起，
   * 免得文案和行为各说各的。
   */
  it("says how long the film will be, and submits exactly that length", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    expect(
      screen.getByText(new RegExp(`${MOTION_BRIEF_FILM_SECONDS} 秒`)),
    ).toBeVisible();

    await user.clear(screen.getByLabelText("一句话视频需求"));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    expect(studioGateway.submitMotionBrief).toHaveBeenCalledWith(
      expect.objectContaining({ durationSeconds: MOTION_BRIEF_FILM_SECONDS }),
    );
  });

  /**
   * 一句话自动制作有好几种失败方式，界面上此前是同一句话：
   * 「本机渲染组件暂时不可用，请到设置与诊断检查组件」。
   * 如果真因是编排超时、编排子进程失败或者答复没通过本机校验，
   * 这句话会把用户支去检查一个根本没坏的东西——错误码粗、文案却给了具体指引，
   * 于是指错方向。三个码各说各的成因，也各给该做的事。
   */
  it("says which part of the automatic run failed instead of blaming the renderer", async () => {
    for (const [code, expected] of [
      ["authoring_timed_out", "自动编排超时"],
      ["authoring_refused", "判定这次描述做不出来"],
      ["authoring_crashed", "自动编排中途出错"],
      ["authoring_answer_invalid", "没有通过本机校验"],
    ] as const) {
      const user = userEvent.setup();
      const studioGateway = gateway();
      studioGateway.submitMotionBrief = vi
        .fn()
        .mockRejectedValue(new MaterialVideoStudioGatewayError(code, true));
      const view = render(<VideoStudio gateway={studioGateway} />);

      await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
      await user.clear(screen.getByLabelText("一句话视频需求"));
      await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
      await user.click(screen.getByRole("button", { name: "开始自动制作" }));

      expect(await screen.findByText(new RegExp(expected))).toBeVisible();
      expect(screen.queryByText(/本机渲染组件暂时不可用/)).toBeNull();
      view.unmount();
    }
  });

  // 空描述不该发出去：让原生侧去拒绝，用户看到的是一次失败而不是一条说明。
  it("explains an empty one-sentence brief instead of submitting it", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.clear(screen.getByLabelText("一句话视频需求"));
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    expect(studioGateway.submitMotionBrief).not.toHaveBeenCalled();
    expect(
      await screen.findByText("请先用一句话描述你想要的视频内容。"),
    ).toBeVisible();
  });

  // 没接发布页的场合（比如还没装配好的外壳）不能凭空多出一个点了没反应的按钮。
  it("omits the publish handoff when there is nowhere to hand it to", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    vi.mocked(studioGateway.motionJobs).mockResolvedValue([
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

    await user.click(screen.getByRole("tab", { name: "成片" }));
    expect(await screen.findByRole("button", { name: "播放季度增长" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "发布季度增长" })).toBeNull();
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

  /**
   * 打开 App 后看到的第一个界面里，最大的那个控件是点不动的。
   *
   * 实测（2026-07-26，1440×900）：「新建视频」首屏是一张 1102×115px 的输入框，
   * 灰的、点不动，`title` 和 `aria-describedby` 都是 null——没有任何地方说
   * 为什么点不动、要做什么才能用。客户第一眼看到的就是它。
   *
   * 这条用例不规定怎么修：框可以是能用的，也可以带上说明，也可以根本不摆在这里。
   * 它只拒绝「又不能用又不解释」这一种。
   */
  it("never shows a dead input box with no explanation of why", () => {
    render(<VideoStudio gateway={gateway()} />);

    for (const box of screen.queryAllByRole("textbox")) {
      const explained =
        box.getAttribute("title") !== null ||
        box.getAttribute("aria-describedby") !== null;
      const dead = (box as HTMLInputElement | HTMLTextAreaElement).disabled;
      expect(
        { name: box.getAttribute("aria-label"), dead, explained },
        "首屏出现了既不能用又没有说明的输入框",
      ).not.toMatchObject({ dead: true, explained: false });
    }
  });

  /**
   * 「视频需求」和「视频标题」是同一个字段的两个名字，还同屏显示。
   *
   * 实测：两个框都绑 `motionDraft.subject`，改任一个另一个跟着变，而更大更靠上
   * 的那个叫「视频需求」——它存的其实是标题。用户会以为自己填漏了或者填重了。
   *
   * 用例按「值」而不是按「元素个数」断言：只要屏幕上找不到第二个同值输入框，
   * 无论最后是删掉、合并还是改名，这个歧义就消失了。
   */
  it("keeps the film title in exactly one field, under one name", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    const title = screen.getByRole("textbox", { name: "视频标题" });
    await user.clear(title);
    await user.type(title, "季度增长");

    const echoes = screen
      .queryAllByRole("textbox")
      .filter((box) => (box as HTMLInputElement).value === "季度增长");
    expect(echoes).toEqual([title]);
  });

  /**
   * 一句话卡片的说明文字是多行 JSX 字符串，JSX 把每处折行 + 缩进渲染成一个空格，
   * 于是这段中文里出现两处句中断裂：「…的视频，␣文案、分镜…」「…本机完成。␣这个入口…」。
   * 它就在演示路径正中。同一个文件里已经有写对的地方（`{"…"}`），这里只是漏了。
   */
  it("writes the one-sentence explanation without JSX line-break spaces", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));

    const explanation = screen.getByText(/描述一句就够了/u);
    expect(explanation.textContent).not.toMatch(
      /[一-鿿、。，；]\s+[一-鿿]/u,
    );
  });

  /**
   * 一句话制作实测 3 分 34 秒，其中 178 秒界面完全不说话。
   *
   * 原生侧 `submit_motion_video_brief` 把整个编排跑在这一条命令里：
   * `run_motion_authoring(...)` 返回之后才 `accept_authored_render_job(...)`
   * 建出任务快照。也就是说这 178 秒里**任务根本还不存在**——「制作任务」页
   * 显示的是空态「还没有真实制作任务」，界面上唯一在动的东西是按钮上的转圈。
   * 没有已用时间，没有阶段，没有任何证据说明它还活着。
   *
   * 这条用例要的是一个走字的钟。上限不要：编排的 600 秒预算是 lib.rs 里的一个
   * 裸常量，不在任何契约里，把它抄进前端就是造第二个事实源；而且对一段 3 分钟的
   * 等待来说「最长 10 分钟」也安慰不到人。已用时间才是「它没死」的证据。
   */
  it("counts the authoring wait out loud instead of only spinning a button", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const studioGateway = gateway();
      // 编排跑完之前这个 Promise 不会 resolve，实测 178 秒。
      studioGateway.submitMotionBrief = vi.fn().mockReturnValue(new Promise(() => {}));
      render(<VideoStudio gateway={studioGateway} />);

      await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
      await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
      await user.click(screen.getByRole("button", { name: "开始自动制作" }));

      expect(await screen.findByText(/正在自动编排/u)).toBeVisible();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(95_000);
      });

      expect(screen.getByText(/已用 1 分 35 秒/u)).toBeVisible();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 渲染阶段的任务卡此前只有一条百分比。
   *
   * 而这条百分比在原生侧是**状态的另一种写法**：`validate_snapshot` 只允许
   * queued=5、rendering=55、encoding=85、succeeded=100 四个值，它在一个阶段里
   * 一动不动。盯着一个不动的「55%」，用户没有任何办法判断是在跑还是卡死了。
   *
   * 已用时间解决「还活着吗」，契约上限解决「什么时候才该担心」。上限来自
   * motion-storyboard-duration.v1 的 renderWallSecondsBase 与
   * renderWallMillisPerFrame——沙箱真正会执行的那个数，不是编出来的估计。
   */
  it("puts a clock and the contract render ceiling on a job it submitted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const studioGateway = gateway();
      render(<VideoStudio gateway={studioGateway} />);

      await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
      await user.click(screen.getByRole("tab", { name: "制作设置" }));
      await user.click(screen.getByRole("radio", { name: "专业蓝" }));
      await user.click(screen.getByRole("tab", { name: "预览" }));

      vi.mocked(studioGateway.motionJobs).mockResolvedValue([
        {
          renderJobId: "f89d8f18-6b4e-4f5a-8325-8da45f71d7e2",
          revision: 2,
          status: "rendering",
          progressPercent: 55,
          subject: "新品发布",
          styleDisplayName: "专业蓝",
          artifactId: null,
          artifactSizeBytes: null,
          failureCode: null,
        },
      ]);
      await user.click(screen.getByRole("button", { name: "提交本机渲染" }));
      await user.click(screen.getByRole("tab", { name: "制作任务" }));
      expect(await screen.findByText("逐帧渲染中")).toBeVisible();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(21_000);
      });

      // 默认草稿是 3 段 × 4 秒；上限完全由契约算出，用例里不写死秒数。
      const film =
        durationContract.beatCountDefault * durationContract.secondsPerBeatDefault;
      const ceiling =
        durationContract.renderWallSecondsBase +
        (film * durationContract.framesPerSecond * durationContract.renderWallMillisPerFrame) /
          1000;

      expect(screen.getByText(/已用 21 秒/u)).toBeVisible();
      expect(
        screen.getByText(new RegExp(`最长 ${motionSpokenDuration(ceiling)}`, "u")),
      ).toBeVisible();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 编排返回时用户还停在「新建视频」页，界面只多一行小字让他自己去别的 Tab。
   * 那条小字是这条链路上唯一的交接，演示时一定会卡在这里。既然编排返回的
   * 那一刻任务已经真的存在了，就把人带过去。
   */
  it("moves to the jobs tab as soon as the authoring run returns", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    expect(await screen.findByText(/已提交一句话自动制作/u)).toBeVisible();
    expect(screen.getByRole("tab", { name: "制作任务" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  /**
   * 做完的时候界面什么都不说：任务卡上的取消按钮消失，成片静悄悄出现在另一个 Tab。
   * 等了三分半的人得自己去翻才知道好了没有。
   */
  it("announces a finished film and offers the way to it", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");

    vi.mocked(studioGateway.motionJobs).mockResolvedValue([
      {
        renderJobId: "b1f0d0c6-1d2f-4a0e-9c3a-2b6f5e7d8a90",
        revision: 6,
        status: "succeeded",
        progressPercent: 100,
        subject: "用蓝色商务风做一段本周销售增长说明",
        styleDisplayName: "一句话自动制作",
        artifactId: "2c29395b-1015-43ae-84a7-6f1901caac09",
        artifactSizeBytes: 4096,
        failureCode: null,
      },
    ]);
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    expect(
      await screen.findByText(/「用蓝色商务风做一段本周销售增长说明」已经做好了/u),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "去看成片" }));

    expect(screen.getByRole("tab", { name: "成片" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      await screen.findByRole("button", {
        name: "播放用蓝色商务风做一段本周销售增长说明",
      }),
    ).toBeVisible();
  });
});
