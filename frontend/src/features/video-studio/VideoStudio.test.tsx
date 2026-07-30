import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  motionRunAttention,
  motionRunSnapshot,
  resetMotionRunStore,
} from "./motion-run-store";

import contract from "../../../../contracts/video/motion-style-presets.v1.json";
import durationContract from "../../../../contracts/video/motion-storyboard-duration.v1.json";
import modelCallContract from "../../../../contracts/video/motion-authoring-model-call.v1.json";
import terminology from "../../../../contracts/quality/user-facing-terminology.v1.json";
import {
  MaterialVideoStudioGatewayError,
  type MaterialVideoStudioErrorCode,
  type MaterialVideoStudioGateway,
} from "./material-video-studio-gateway";
import { motionSpokenDuration } from "./motion-duration";
import { motionThinkingNotice } from "./motion-model-call";
import {
  DURATION_SECONDS_MINIMUM,
  MOTION_BRIEF_FILM_SECONDS,
  MOTION_BRIEF_LIMITS,
  motionBriefWaitEstimate,
} from "./motion-one-sentence";
import { VideoStudio } from "./VideoStudio";

function gateway(): MaterialVideoStudioGateway {
  return {
    open: vi.fn().mockResolvedValue({
      state: "opened",
      modelId: "qwen3.7-max-2026-06-08",
    }),
    updateView: vi.fn().mockResolvedValue(undefined),
    close: vi.fn().mockResolvedValue(undefined),
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
  // 运行状态现在活在组件之外（这正是它能挺过切页的原因），所以每条用例开始前
  // 必须清空，否则上一条的选择和提交会渗进下一条。
  beforeEach(() => {
    resetMotionRunStore();
  });

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
    expect(screen.queryByRole("button", { name: "打开完整制作界面" })).toBeNull();
    expect(
      screen.getByText(
        "选择一种制作方式后，当前 App 会显示对应的制作工具。",
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
    const studioGateway = gateway() as MaterialVideoStudioGateway & {
      close(): Promise<void>;
      updateView(view: {
        readonly x: number;
        readonly y: number;
        readonly width: number;
        readonly height: number;
        readonly visible: boolean;
      }): Promise<void>;
    };
    studioGateway.close = vi.fn().mockResolvedValue(undefined);
    studioGateway.updateView = vi.fn().mockResolvedValue(undefined);
    const bounds = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      bottom: 760,
      height: 640,
      left: 40,
      right: 940,
      top: 120,
      width: 900,
      x: 40,
      y: 120,
      toJSON: () => ({}),
    });
    const view = render(<VideoStudio gateway={studioGateway} />);

    const materialMethod = screen.getByRole("button", { name: /选择智能素材成片/u });
    const motionMethod = screen.getByRole("button", { name: /选择品牌动效成片/u });
    expect(materialMethod).toHaveAttribute("aria-pressed", "false");
    expect(motionMethod).toHaveAttribute("aria-pressed", "false");

    // The comparison questions come from the terminology contract, which is
    // also what the static gate and the real App acceptance read.
    //
    // They start behind a disclosure on each card: expanded, the two ten-row
    // tables were 466px and the single reason this step could not fit the
    // screen it is given. Collapsed is therefore the state a customer meets,
    // and it is asserted here rather than assumed — antd does not even mount a
    // shut panel's contents, so a regression that dropped the rows entirely
    // would otherwise look identical to a regression that merely hid them.
    const firstLabel = terminology.videoCreationMethodCardLabels[0]!;
    expect(screen.queryAllByText(firstLabel)).toHaveLength(0);

    for (const method of ["智能素材成片", "品牌动效成片"]) {
      await user.click(screen.getByRole("button", { name: `${method}的详细说明` }));
    }

    for (const label of terminology.videoCreationMethodCardLabels) {
      expect(screen.getAllByText(label)).toHaveLength(2);
    }

    await user.click(materialMethod);
    expect(materialMethod).toHaveAttribute("aria-pressed", "true");
    expect(motionMethod).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("已选择：智能素材成片")).toBeVisible();
    expect(
      screen.getByRole("region", { name: "智能素材成片完整制作界面" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "打开完整制作界面" })).toBeNull();
    await waitFor(() =>
      expect(studioGateway.open).toHaveBeenCalledWith({
        x: 40,
        y: 120,
        width: 900,
        height: 640,
        visible: true,
      }),
    );

    await user.click(motionMethod);
    expect(materialMethod).toHaveAttribute("aria-pressed", "false");
    expect(motionMethod).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("已选择：品牌动效成片")).toBeVisible();
    await waitFor(() => expect(studioGateway.close).toHaveBeenCalledOnce());

    expect(document.body).not.toHaveTextContent(/真人生成|网址转视频/iu);
    view.unmount();
    bounds.mockRestore();
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

  // 这条实测稳定在 4.6 秒，而 vitest 的默认单条上限是 5 秒——不是它慢的问题，是
  // 余量只剩 0.4 秒，机器一忙就翻面，而它一旦超时，同文件后面几条会跟着报
  // 「找不到 开始自动制作 按钮」，看起来像是别的地方坏了。给一个明确的上限，让它
  // 要么是真失败要么是真通过。
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
    // 预览现在按草稿真正的每段时长走（默认 4 秒），不再是写死的 500ms，
    // 所以等待窗口必须比一段长；默认的 1000ms 只够旧的假节奏。
    expect(await screen.findByText("第 2 段 / 3", {}, { timeout: 8000 })).toBeVisible();

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
  }, 20_000);

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
   * 取消是协作式的：按下按钮只是把请求写下来，真正停下来的是那个开着浏览器
   * 和编码器的执行线程。快照因此有「正在取消」这个中间态，界面必须认得它。
   *
   * 两件事都要成立：
   *   - 按下之后立刻有反应（不是几秒钟毫无变化），
   *   - 已经在停的任务不再重复提供取消按钮。
   */
  it("shows a cancellation that has been asked for but not yet confirmed", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    vi.mocked(studioGateway.motionJobs).mockResolvedValue([
      {
        renderJobId: "b1f2b0a5-3a1e-4b62-9f0d-6b3c1d2e4f50",
        revision: 3,
        status: "cancelling",
        progressPercent: 85,
        subject: "新品发布",
        styleDisplayName: "商务蓝",
        artifactId: null,
        artifactSizeBytes: null,
        failureCode: null,
      },
    ]);
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("tab", { name: "制作任务" }));

    expect(await screen.findByText("正在取消")).toBeVisible();
    expect(screen.queryByText("已取消")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "取消品牌动效任务" }),
    ).toBeNull();
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
      await screen.findByText("已提交一句话自动制作，编排完成，本机渲染开始了。"),
    ).toBeVisible();
  });

  /**
   * 一句话卡片必须在点按钮之前就说清楚会得到多长的片子。
   *
   * 客户在演示现场随口说「做一个三分钟的产品介绍」，那句话会作为描述被接受，
   * 然后系统安静地做出十几秒的片子——需求被丢掉且不给任何提示。
   * 这条用例把界面上的数字和真正提交的时长绑在一起，免得文案和行为各说各的。
   */
  it("says the length steers the film rather than fixing it, and submits it", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    // The number is what gets submitted and what steers how much the storyboard
    // tries to say. What it must not do is promise the finished length: a shot
    // runs for whichever is longer, its line or its part's own motion, and the
    // film is the sum of its shots (the product owner's correction of
    // 2026-07-27). Measured 2026-07-28 against the real model: a 20 second brief
    // produced a 900 frame film — 30 seconds — so the old wording told the user
    // a number the product would not deliver.
    expect(screen.getByLabelText("成片时长（秒）")).toHaveDisplayValue(
      String(MOTION_BRIEF_FILM_SECONDS),
    );
    const note = screen.getByText(/实际片长以成片为准/);
    expect(note).toBeVisible();
    expect(note.textContent).not.toMatch(/会生成一段 \d+ 秒的视频/);

    await user.clear(screen.getByLabelText("一句话视频需求"));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    expect(studioGateway.submitMotionBrief).toHaveBeenCalledWith(
      expect.objectContaining({ durationSeconds: MOTION_BRIEF_FILM_SECONDS }),
    );
  });

  /**
   * 片长写死 12 秒不只是少一个设置，它让整批零件在原理上够得着、实际上一个都用不上。
   *
   * 2026-07-28 对着真实模型量过：12 秒预算下最短的零件也要 4.5 秒、占掉 37%，
   * 模型每次都判定放不下——这是对的，提示词本来就告诉它零件时长从整片预算里扣。
   * 同一句话给 20 秒，立刻选中一到两个。所以「能改片长」是 134 个零件能不能进片子
   * 的前提，不是锦上添花。这条守住操作者改的数真的会送到编排那一侧去。
   */
  it("submits the length the operator chose rather than the default", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.clear(screen.getByLabelText("成片时长（秒）"));
    await user.type(screen.getByLabelText("成片时长（秒）"), "60");
    await user.clear(screen.getByLabelText("一句话视频需求"));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    expect(studioGateway.submitMotionBrief).toHaveBeenCalledWith(
      expect.objectContaining({ durationSeconds: 60 }),
    );
  });

  /** 上限是产品定的 180 秒，控件自己就得挡住，不能等提交才报错。 */
  it("caps the length control at the contract ceiling", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));

    const control = screen.getByLabelText("成片时长（秒）");
    expect(control).toHaveAttribute(
      "aria-valuemax",
      String(MOTION_BRIEF_LIMITS.durationSecondsMaximum),
    );
    expect(control).toHaveAttribute("aria-valuemin", String(DURATION_SECONDS_MINIMUM));
  });

  /**
   * 拉长片长要付的时间必须当场看得见。
   *
   * 路线 A 是一个镜头渲染一次，每次都重新起一遍浏览器（契约里记作 30 秒固定开销），
   * 所以耗时随镜头数涨而不只随帧数涨。一个只能拉、不说代价的控件，会让人顺手拉到头
   * 然后等将近一小时，中途以为卡死了——这正是「AI 自测全绿、用户一用傻眼」那类问题的
   * 界面版本。
   */
  it("tells the operator what a longer film costs in waiting", async () => {
    const user = userEvent.setup();
    render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));

    const shortWait = motionBriefWaitEstimate(MOTION_BRIEF_FILM_SECONDS);
    expect(
      screen.getByText(new RegExp(motionSpokenDuration(shortWait.ceilingSeconds))),
    ).toBeVisible();

    await user.clear(screen.getByLabelText("成片时长（秒）"));
    await user.type(
      screen.getByLabelText("成片时长（秒）"),
      String(MOTION_BRIEF_LIMITS.durationSecondsMaximum),
    );

    const longWait = motionBriefWaitEstimate(MOTION_BRIEF_LIMITS.durationSecondsMaximum);
    expect(longWait.ceilingSeconds).toBeGreaterThan(shortWait.ceilingSeconds);
    expect(
      screen.getByText(new RegExp(motionSpokenDuration(longWait.ceilingSeconds))),
    ).toBeVisible();
  });

  /**
   * 「渲染超过 X 会自动停下」在长片上不能再用单次渲染的公式算。
   *
   * 那个公式是「一次渲染的沙箱停摆阈值」，片长写死 12 秒时它报 174 秒、实际约
   * 234 秒，差一分钟看不出来。片长放开之后差出 12 分钟：180 秒的片子它报
   * 36 分 30 秒，而这一轮渲染合法耗时可达 48 分——于是一部**健康**的片子跑到
   * 40 分钟时，界面会显示「已用 40 分 · 渲染超过 36 分 30 秒 会自动停下」，
   * 已用时间超过了它自己声称的自动停止点。
   *
   * 用的是渲染那一段的上限而不是总时长：这块表是渲染开始才起的（`settleMotionRun`
   * 在提交返回后才盖时间戳），把编排那三分钟算进参照就是往危险的方向多给时间。
   */
  it("times a one-sentence film by what a film of many shots may take", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    vi.mocked(studioGateway.motionJobs).mockResolvedValue([
      {
        renderJobId: "b1f0d0c6-1d2f-4a0e-9c3a-2b6f5e7d8a90",
        revision: 2,
        status: "rendering",
        progressPercent: 55,
        subject: "用蓝色商务风做一段说明",
        styleDisplayName: "专业蓝",
        artifactId: null,
        artifactSizeBytes: null,
        failureCode: null,
      },
    ]);
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.clear(screen.getByLabelText("成片时长（秒）"));
    await user.type(
      screen.getByLabelText("成片时长（秒）"),
      String(MOTION_BRIEF_LIMITS.durationSecondsMaximum),
    );
    await user.clear(screen.getByLabelText("一句话视频需求"));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));
    expect(
      await screen.findByText("已提交一句话自动制作，编排完成，本机渲染开始了。"),
    ).toBeVisible();

    const longest = motionBriefWaitEstimate(MOTION_BRIEF_LIMITS.durationSecondsMaximum);
    expect(
      await screen.findByText(
        new RegExp(`渲染超过 ${motionSpokenDuration(longest.renderCeilingSeconds)} 会自动停下`),
      ),
    ).toBeVisible();
  });

  /**
   * 深度思考要能关，而且关之前得先看见这笔账。
   *
   * 2026-07-28 拿真实编排 prompt 对着真实模型各跑三次：开着 41.7 秒
   * （40.5~51.0），关掉 10.9 秒（8.5~23.5）。省下的这半分钟对着急演示的人是有
   * 意义的，但代价没量过——只量了时间，没量质量。所以给开关、给账单，
   * 默认不动，判断交给操作者。
   */
  it("lets the operator turn the model's own reasoning off, and prices it first", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    const thinking = screen.getByRole("switch", { name: "让模型先想一遍再落笔" });
    expect(thinking).toBeChecked();
    expect(screen.getByText(new RegExp(motionThinkingNotice(true)))).toBeVisible();

    await user.click(thinking);
    expect(thinking).not.toBeChecked();
    expect(screen.getByText(new RegExp(motionThinkingNotice(false)))).toBeVisible();

    await user.clear(screen.getByLabelText("一句话视频需求"));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    expect(studioGateway.submitMotionBrief).toHaveBeenCalledWith(
      expect.objectContaining({ modelThinking: false }),
    );
  });

  /**
   * 侧边栏一点就把这个组件卸载掉——句子当年就是这么丢的（motion-run-store 的
   * 开头记着那次实测）。片长是同一张表单上的字段，必须一起活下来，
   * 否则会出现最难查的那种：设好 180 秒，去别处看一眼回来，句子还在、数字悄悄退回 12。
   */
  it("keeps the chosen length when the page is left and reopened", async () => {
    const user = userEvent.setup();
    const view = render(<VideoStudio gateway={gateway()} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.clear(screen.getByLabelText("成片时长（秒）"));
    await user.type(screen.getByLabelText("成片时长（秒）"), "45");

    view.unmount();
    render(<VideoStudio gateway={gateway()} />);

    expect(screen.getByLabelText("成片时长（秒）")).toHaveDisplayValue("45");
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
      ["authoring_crashed", "自动编排没能完成"],
      ["authoring_answer_invalid", "没有通过本机校验"],
    ] as const) {
      // 运行状态活在组件之外，所以每一轮都要从干净的store 重新开始，
      // 否则上一轮选中的制作方式会让这一轮的「选择品牌动效成片」找不到。
      resetMotionRunStore();
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

  /**
   * 提交一句话需求，让网关以 `code` 失败，把卡片上那条提示原文取回来。
   *
   * 用 `paste` 而不是 `type`：走完一轮的开销主要在渲染和点击，但逐字符敲还要再花
   * 半秒，而下面那条互不相同的用例要连走七轮。这里要断言的是文案，不是输入法。
   */
  const briefFailureText = async (code: MaterialVideoStudioErrorCode) => {
    resetMotionRunStore();
    const user = userEvent.setup();
    const studioGateway = gateway();
    studioGateway.submitMotionBrief = vi
      .fn()
      .mockRejectedValue(new MaterialVideoStudioGatewayError(code, true));
    const view = render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.clear(screen.getByLabelText("一句话视频需求"));
    await user.click(screen.getByLabelText("一句话视频需求"));
    await user.paste("用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    const text = (await screen.findByRole("alert")).textContent ?? "";
    view.unmount();
    return text;
  };

  /**
   * 只有真的读过这句话的失败，才有资格让用户去改这句话。
   *
   * 失败注入实测（2026-07-26）：模型服务连不上，2 秒后界面说「判定这次描述做不
   * 出来…请换一句更具体的描述后重试」；模型接上了却不再回话，363 秒后一字不差的
   * 同一句；worktree 里 vendor 是空的，2 秒后还是那一句。三次都没有任何东西读过
   * 用户那句描述。演示当天讲解人会照着这句话去改文案，越改越错——真因分别在模型
   * 服务和安装包，改多少遍句子都没用。
   *
   * 子进程侧现在按原因 token 把这些移出拒绝通道（见
   * contracts/video/motion-authoring-refusal.v1.json 的 nonRefusalOutcomes），
   * 各自落在自己的码上。这条用例只守一件事：那句「换一句」只许出现在
   * `authoring_refused` 上，因为它是唯一一个代理真的读完并拒绝了的。
   */
  // 只找「叫用户去改这句话」的说法。「不是描述写得不好」是撇清，不是指责，
  // 不能算进来。
  const ASKS_FOR_A_NEW_SENTENCE = /换一句|更具体的描述|把描述写得/u;

  /**
   * 每个码各自该说什么、不该说什么。
   *
   * 一个码一条用例，而不是一个循环走七遍：每次提交都是一整轮渲染加真实点击，
   * 七轮加起来接近 vitest 默认的五秒上限，单独跑这个文件时过、全量并行时随机红。
   * 拆开之后每条 400 毫秒上下，红的时候用例名也直接点出是哪个码。
   */
  it.each([
    // 代理真的读完并拒绝了：唯一有资格叫用户改句子的一条。
    ["authoring_refused", true, /换一句/u, null],
    ["authoring_timed_out", false, /最长时间/u, null],
    ["authoring_crashed", false, /我们这边/u, null],
    ["authoring_answer_invalid", false, /本机校验/u, null],
    // 完全没回应：能查的是网络和模型服务地址。
    ["authoring_model_transport_failed", false, /网络.*视频创作模型服务|视频创作模型服务.*网络/su, null],
    // 接上了但不回话：说清「已经接上」，不要再把人支去查网络。但也不许宣称
    // 「网络是通的」——连接无声断掉同样会以读超时的形式到这里，那种情况下这句
    // 话会把用户支离真正的原因。
    ["authoring_model_timed_out", false, /已经接上/u, /检查网络|网络也是通的|网络没问题/u],
    // 安装坏了：重试没有用，唯一的出路是重装。
    ["authoring_installation_damaged", false, /重新安装/u, /请重试|稍后重试/u],
  ] as const)(
    "tells the user what to do about %s without blaming their sentence",
    async (code, blames, required, forbidden) => {
      const text = await briefFailureText(code);

      // 兜底那句等于这个码根本没人写文案。
      expect(text).not.toContain("一句话自动制作暂时无法提交");
      expect(ASKS_FOR_A_NEW_SENTENCE.test(text)).toBe(blames);
      expect(text).toMatch(required);
      if (forbidden !== null) {
        expect(text).not.toMatch(forbidden);
      }
    },
  );

  /**
   * 「超过允许的最长等待时间」这句话没有告诉用户任何事。
   *
   * 它想说的是一个具体的数：模型接上之后，我们最多等它多久不说话才停。用户拿这句话
   * 判断不了刚才那次到底是等够了才失败，还是别的原因；也判断不了下一次值不值得再等。
   * 这个数今天只活在 `agent.py` 里，写不进文案的原因就是写进来就多一份手抄的副本。
   *
   * 这条走到卡片上的那句话，断言里的数直接来自契约，所以改契约而文案没跟着改会红。
   * Executor 侧同一个数也从这份契约读、不留副本，那件事由
   * `frontend/tests/motion-authoring-model-call.test.mjs` 守。
   */
  it("tells the user how long the model gets before we stop waiting", async () => {
    const text = await briefFailureText("authoring_model_timed_out");

    expect(text).toMatch(/\d+\s*分/u);
    expect(text).toContain(
      `连续 ${motionSpokenDuration(modelCallContract.streamIdleTimeoutSeconds)}没有再返回内容`,
    );
  });

  /**
   * 拆出来的码只有说出不同的话才算拆开。
   *
   * 2026-07-26 的故障注入里，模型连不上和模型接上后不再回话走到同一句，
   * 一个 2 秒一个 363 秒，用户看到的字一模一样。上面那组用例逐个盯落脚点，
   * 这条盯的是它们之间的关系——七条提示必须两两不同，这是逐条断言看不出来的。
   *
   * 它确实要跑满七轮，所以给了明确的上限而不是靠默认值撞运气。
   */
  it(
    "never says the same thing about two different authoring failures",
    async () => {
      const said = new Set<string>();
      for (const code of [
        "authoring_refused",
        "authoring_timed_out",
        "authoring_crashed",
        "authoring_answer_invalid",
        "authoring_model_transport_failed",
        "authoring_model_timed_out",
        "authoring_installation_damaged",
      ] as const) {
        said.add(await briefFailureText(code));
      }

      expect(said.size).toBe(7);
    },
    20_000,
  );

  /**
   * 我们自己的安装包坏了，绝不能说成用户的描述有问题。
   *
   * T90 顺手撞见的就是这条：worktree 里 `vendor` 是空的，子进程 2 秒后因为
   * 锁定文件不在而退出——一个纯打包缺陷，卡片却说「请换一句更具体的描述」。
   */
  it("never blames the description for a damaged installation", async () => {
    const text = await briefFailureText("authoring_installation_damaged");

    expect(text).not.toMatch(/换一句|更具体的描述|把描述写得/u);
    expect(text).toMatch(/不是描述/u);
    expect(text).toMatch(/重新安装/u);
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

      expect(await screen.findByText(/正在自动编排这条视频/u)).toBeVisible();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(95_000);
      });

      expect(screen.getByText(/已用 1 分 35 秒/u)).toBeVisible();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 一个只会往上走的秒表，回答不了「这正常吗」。
   *
   * 走字的钟解决的是「它还活着吗」。但等到 87 秒的时候，用户唯一想知道的是这算
   * 不算久——没有参照物，2 分钟的正常等待和已经挂掉的等待长得一模一样，于是他要
   * 么白等，要么在正常范围内就重新提交，真的再跑一遍编排。演示当天更糟：讲解人
   * 得当着客户面盯着一个不知道要转多久的圈。
   *
   * 参照物用实测值，不用预测：2026-07-26 连续七次成功，提交到完成中位数 124 秒、
   * 最长 178 秒。说给人听的时候取整到分钟——「通常 2 分 4 秒」是一种假精度，这个
   * 数字不配。超过实测最长的那一次之后，说的仍然是事实（已经比实测最长的还久）
   * 加上最可能的成因，不是断言。
   */
  it("says how long the wait normally is, and says when it has gone past that", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const studioGateway = gateway();
      studioGateway.submitMotionBrief = vi.fn().mockReturnValue(new Promise(() => {}));
      render(<VideoStudio gateway={studioGateway} />);

      await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
      await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
      await user.click(screen.getByRole("button", { name: "开始自动制作" }));

      const jobs = await screen.findByRole("tabpanel");
      // 94 秒：还在实测的正常范围里，给出参照物，不要报警。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(94_000);
      });
      expect(jobs).toHaveTextContent(/通常 2 分钟左右/u);
      expect(jobs).toHaveTextContent(/最长约 3 分钟/u);
      expect(jobs).not.toHaveTextContent(/超过/u);

      // 208 秒：过了实测最长的 178 秒，必须说出来。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(114_000);
      });
      expect(jobs).toHaveTextContent(/超过/u);
      expect(jobs).toHaveTextContent(/视频创作模型服务/u);
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
      // 上限只能写成「到点会停」，不能写成「预计还需」：实测 12 秒的片子渲染
      // 约 10 秒，而契约上限是 174 秒——那是沙箱的卡死保护，不是预期耗时。
      expect(
        screen.getByText(
          new RegExp(`超过 ${motionSpokenDuration(ceiling)} 会自动停下`, "u"),
        ),
      ).toBeVisible();
      expect(screen.queryByText(/预计还需|预计剩余/u)).toBeNull();
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
    // 用户已经处理过这条结果了，侧边栏那个「有事」的标记就该灭掉，
    // 否则它会一直亮到下一次提交。
    expect(screen.queryByText(/已提交一句话自动制作/u)).toBeNull();
    expect(motionRunAttention(motionRunSnapshot())).toBe("none");
    expect(
      await screen.findByRole("button", {
        name: "播放用蓝色商务风做一段本周销售增长说明",
      }),
    ).toBeVisible();
  });

  /**
   * 预览按 500ms 一段放，同一屏上方却写着「每段 4 秒」。
   *
   * 这是用户点名的「默认一个片段就 1 秒钟」的镜像：数据层的默认值早就从 1 秒
   * 改成契约里的 4 秒了，预览播放器的节奏还是写死的 500ms。用户设了 4 秒，
   * 它按 0.5 秒放，三段 1.5 秒就完了——预览预览的是另一条片子。
   *
   * 节奏必须跟着草稿走，而草稿的默认值来自契约，所以用例也从契约取这个数。
   */
  it("plays the preview at the beat length the same screen promises", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<VideoStudio gateway={gateway()} />);

      await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
      await user.click(screen.getByRole("tab", { name: "制作设置" }));
      await user.click(screen.getByRole("radio", { name: "专业蓝" }));
      await user.click(screen.getByRole("tab", { name: "预览" }));

      const preview = screen.getByRole("region", { name: "品牌动效播放预览" });
      await user.click(screen.getByRole("button", { name: "播放预览" }));

      const beatMillis = durationContract.secondsPerBeatDefault * 1000;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(beatMillis - 500);
      });
      expect(preview).toHaveTextContent("第 1 段 / 3");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
      expect(preview).toHaveTextContent("第 2 段 / 3");
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 全产品 13 个禁用控件，13 个都没有说明——`title` 和 `aria-describedby` 全是 null。
   * 「新建视频」页上的「打开完整制作界面」是演示路径上第一个撞到的。
   *
   * 注意不能用 `title` 修：浏览器对 `disabled` 的按钮不触发原生 tooltip，
   * 挂上去等于没挂。产品自己已经有一处做对了——预览页「提交本机渲染」禁用时
   * 旁边有一条写清楚缺什么的旁注——照着那个来。
   */
  it("explains every disabled button on the page instead of just greying it", () => {
    render(<VideoStudio gateway={gateway()} />);

    for (const button of screen.getAllByRole("button")) {
      if (!(button as HTMLButtonElement).disabled) continue;
      expect(
        button,
        `禁用按钮「${button.textContent}」没有任何说明`,
      ).toHaveAccessibleDescription();
    }
  });

  /**
   * 提交完，「制作任务」页还是空的——足足 136 秒。
   *
   * 原生侧编排成功之后才写任务快照，实测那次成功的运行任务在 +140 秒才第一次
   * 出现。也就是说用户点完「开始自动制作」，切到「制作任务」看到的是空态
   * 「还没有真实制作任务」。他会以为没提交上去，然后再点一次——而再点一次
   * 会真的再跑一遍编排。
   *
   * 所以提交那一刻就得有一条本地记录，不能等原生侧承认它存在。
   */
  it("shows the submission in the jobs list from the moment it is sent", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    // 编排跑完之前不 resolve，实测 136～178 秒。
    studioGateway.submitMotionBrief = vi.fn().mockReturnValue(new Promise(() => {}));
    render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    await user.click(screen.getByRole("tab", { name: "制作任务" }));

    const jobs = screen.getByRole("tabpanel");
    expect(within(jobs).queryByText("还没有真实制作任务")).toBeNull();
    expect(within(jobs).getByText("用蓝色商务风做一段说明")).toBeVisible();
    expect(within(jobs).getByText(/正在自动编排这条视频/u)).toBeVisible();
  });

  /**
   * 切走再切回来，什么都没发生过——而任务其实已经失败了。
   *
   * 实测：提交后切走 75 秒再回来，任务列表是空的，输入的句子没了，选中的制作
   * 方式没了。更糟的是失败被吞掉：编排的 Promise 是几分钟后才 settle 的，
   * 那时 `WorkbenchShell` 早就把 `VideoStudio` 卸载了，错误文案写进死掉的
   * state，用户永远不知道任务挂了。云端验收线的一次运行就是这么把失败原因
   * 永久弄丢的。
   *
   * 原生侧没问题：渲染在切页时不会中断。这纯粹是前端状态的生命周期问题。
   */
  it("keeps the sentence, the method and a failure that landed after the page was gone", async () => {
    const user = userEvent.setup();
    const studioGateway = gateway();
    let crash: (error: unknown) => void = () => {};
    studioGateway.submitMotionBrief = vi
      .fn()
      .mockReturnValue(
        new Promise((_resolve, reject) => {
          crash = reject;
        }),
      );
    const view = render(<VideoStudio gateway={studioGateway} />);

    await user.click(screen.getByRole("button", { name: "选择品牌动效成片" }));
    await user.type(screen.getByLabelText("一句话视频需求"), "用蓝色商务风做一段说明");
    await user.click(screen.getByRole("button", { name: "开始自动制作" }));

    // 用户去看别的功能：外壳把整个视频制作页卸载掉。
    view.unmount();

    // 编排在这之后才失败。
    await act(async () => {
      crash(new MaterialVideoStudioGatewayError("authoring_crashed", true));
      await Promise.resolve();
    });

    render(<VideoStudio gateway={studioGateway} />);

    expect(await screen.findByText(/自动编排没能完成/u)).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "新建视频" }));
    // 选择按钮的无障碍名恒为「选择…」，选中与否挂在 aria-pressed 上。
    expect(screen.getByRole("button", { name: "选择品牌动效成片" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByLabelText("一句话视频需求")).toHaveValue("用蓝色商务风做一段说明");
  });
});
