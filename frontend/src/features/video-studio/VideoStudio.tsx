import { useCallback, useEffect, useState } from "react";

import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Progress,
  Space,
  Tabs,
  Tag,
  Typography,
} from "antd";

import type { SelectedVideo } from "../publishing/PublishWorkspace";
import {
  MaterialVideoStudioGatewayError,
  type MaterialRenderJobSnapshot,
  type MaterialVideoStudioErrorCode,
  type MaterialVideoStudioGateway,
  type MotionRenderJobSnapshot,
  type MotionRenderJobStatus,
  type MotionVideoBeatDraft,
  type MotionVideoBriefRequest,
  type MotionVideoDraftRequest,
  type RenderedVideoArtifactPayload,
} from "./material-video-studio-gateway";
import {
  MOTION_DURATION_LIMITS,
  motionDurationProblem,
  motionRenderCeilingSeconds,
  motionSpokenDuration,
  motionStoryboardSummary,
  resizeMotionBeats,
} from "./motion-duration";
import { MOTION_AUTHORING_IDLE_WAIT } from "./motion-model-call";
import {
  DURATION_SECONDS_MINIMUM,
  MOTION_AUTHORING_MEASURED,
  MOTION_BRIEF_LIMITS,
  motionBriefProblem,
  motionBriefWaitEstimate,
} from "./motion-one-sentence";
import {
  dismissMotionRunMessage,
  failMotionRun,
  forgetMotionJob,
  setMotionActiveTab,
  setMotionBrief,
  setMotionFilmSeconds,
  setMotionMethod,
  settleMotionRun,
  startMotionRun,
  useMotionRun,
  type MotionRunPending,
  type OwnMotionJob,
  type VideoCreationMethodId,
} from "./motion-run-store";
import { motionPartsUsage } from "./motion-parts-catalog";
import { MotionPartsCatalog } from "./MotionPartsCatalog";
import { MotionStyleCatalog, type MotionStyleDraftSelection } from "./MotionStyleCatalog";
import { MOTION_STYLE_CATALOG } from "./motion-style-catalog";

// The single creation mode the App can submit. Declared once so the parts page
// and the submitted request can never disagree about which mode is in play.
const MOTION_CREATION_MODE: MotionVideoDraftRequest["creationMode"] =
  "manual_template_v1";

const SEED_MOTION_BEATS: readonly MotionVideoBeatDraft[] = [
  { title: "一个清晰的核心信息", caption: "字幕：先让观众知道这次更新是什么。" },
  { title: "三个值得关注的亮点", caption: "字幕：用简洁画面解释价值和变化。" },
  { title: "现在就开始行动", caption: "字幕：给观众一个明确的下一步。" },
] as const;

function createMotionBeat(index: number): MotionVideoBeatDraft {
  return {
    title: `第 ${index + 1} 段`,
    caption: `字幕：第 ${index + 1} 段要说的话。`,
  };
}

const DEFAULT_MOTION_BEATS = resizeMotionBeats(
  SEED_MOTION_BEATS,
  MOTION_DURATION_LIMITS.beatCountDefault,
  createMotionBeat,
);


function spokenMinutes(seconds: number): string {
  return `${Math.round(seconds / 60)} 分钟`;
}

/**
 * How long a submission that has not come back yet has been going, and whether
 * that is normal.
 *
 * There is no job to show for it: `submit_motion_video_brief` writes the job
 * snapshot only after the authoring pass has succeeded, so for 136–178 seconds
 * (measured) the jobs list was literally empty and the operator had no way to
 * tell a submission in flight from one that never happened. Some of them
 * pressed the button again.
 *
 * A clock alone fixed only half of that. At 87 seconds the number answers "is
 * it alive" and says nothing at all about "is this normal", and without a
 * reference a healthy two minute wait looks exactly like a dead one — which is
 * the same button-pressed-twice again, just later. So the measured range is on
 * screen beside the clock.
 *
 * Past the longest run ever measured it stops offering reassurance it no longer
 * has. What it says then is still a fact — this has gone on longer than any
 * measured run — plus the likeliest reason, because a model that took the
 * connection and then went quiet is exactly what this looks like from here and
 * the operator can go and check it. It is not a claim that the run has failed:
 * nothing here knows that.
 */
function motionPendingLabel(pending: MotionRunPending, now: number): string {
  const seconds = Math.max(0, Math.floor((now - pending.startedAt) / 1000));
  const elapsed = motionSpokenDuration(seconds);
  if (pending.kind !== "one_sentence") {
    return `正在提交本机渲染任务 · 已用 ${elapsed}`;
  }
  const reference =
    seconds > MOTION_AUTHORING_MEASURED.longestSeconds
      ? `已经超过实测最长的 ${spokenMinutes(
          MOTION_AUTHORING_MEASURED.longestSeconds,
        )}，可能是视频创作模型服务没有回应`
      : `通常 ${spokenMinutes(
          MOTION_AUTHORING_MEASURED.typicalSeconds,
        )}左右，最长约 ${spokenMinutes(MOTION_AUTHORING_MEASURED.longestSeconds)}`;
  return `正在自动编排这条视频 · 已用 ${elapsed} · ${reference}`;
}

/**
 * What the jobs page says about time while a film this App started is running.
 *
 * The percentage alone cannot answer "is it stuck?", because the percentage is
 * a status in disguise: `validate_snapshot` in `motion_video_studio.rs` admits
 * exactly four values — queued 5, rendering 55, encoding 85, succeeded 100 — so
 * the bar stands perfectly still for the whole of each stage. Two numbers are
 * needed instead: how long it has been going, and the point at which the render
 * sandbox itself would stop it. Both are facts rather than predictions; the
 * ceiling is computed from the shared duration contract, never written down.
 *
 * It is worded as the stop condition it is, never as "预计还需". Measured on
 * 2026-07-26, a twelve second film renders in about ten seconds while its
 * contract ceiling is 174 — the contract sizes the sandbox's stall guard, not
 * the expected run. Printing that number as an estimate would invent a
 * three minute wait out of a ten second one.
 */
function motionJobTiming(own: OwnMotionJob | undefined, now: number): string | null {
  if (own === undefined) return null;
  const elapsed = Math.max(0, Math.floor((now - own.startedAt) / 1000));
  // A one-sentence film is many renders, so its bound is the one the length
  // control already showed before the run started — the same number, so the
  // page cannot contradict what the operator was told he was buying.
  const ceiling =
    own.kind === "one_sentence"
      ? motionBriefWaitEstimate(own.filmSeconds).ceilingSeconds
      : motionRenderCeilingSeconds(own.filmSeconds);
  return `已用 ${motionSpokenDuration(elapsed)} · 渲染超过 ${motionSpokenDuration(
    ceiling,
  )} 会自动停下`;
}

interface MotionDraft {
  readonly subject: string;
  readonly secondsPerBeat: number;
  readonly beats: readonly MotionVideoBeatDraft[];
  readonly style: MotionStyleDraftSelection;
}

const EMPTY_MOTION_STYLE: MotionStyleDraftSelection = {
  stylePresetId: null,
  primaryColor: "",
  secondaryColor: "",
  logo: null,
};

interface VideoCreationMethodOption {
  readonly id: VideoCreationMethodId;
  readonly name: string;
  readonly shortDescription: string;
  readonly details: ReadonlyArray<{
    readonly label: string;
    readonly value: string;
  }>;
}

const VIDEO_CREATION_METHODS: readonly VideoCreationMethodOption[] = [
  {
    id: "material_montage_v1",
    name: "智能素材成片",
    shortDescription: "让旁白、字幕和素材画面配合起来，快速制作信息类短视频。",
    details: [
      {
        label: "最适合",
        value: "知识讲解、观点、榜单、教程，以及旁白配城市、办公、做饭等补充画面的内容。",
      },
      {
        label: "不适合",
        value: "固定真人连续出镜、电影感剧情、精确产品动作，或同一人物跨镜头保持一致。",
      },
      {
        label: "举个例子",
        value: "输入一段护肤知识，生成旁白和字幕，并配上洗脸、护肤品与生活场景画面。",
      },
      {
        label: "外部服务",
        value: "通常需要文案模型、配音服务和素材网站；具体使用哪些服务会在提交前列明。",
      },
      {
        label: "本机处理",
        value: "素材整理、字幕排版和视频合成主要在本机完成。",
      },
      {
        label: "预计耗时",
        value: "30–60 秒视频通常约 5–20 分钟，实际取决于素材和配音服务。",
      },
      { label: "设备占用", value: "合成时会持续占用较多处理器和内存。" },
      { label: "临时磁盘", value: "建议预留 2–6 GB，任务提交前会按设置重新估算。" },
      { label: "网络消耗", value: "中到高，通常需要请求文案、配音并下载素材。" },
      {
        label: "数据与隐私",
        value: "输入内容及发送给外部服务的文案、配音文本或素材检索词会离开本机。",
      },
    ],
  },
  {
    id: "motion_composition_v1",
    name: "品牌动效成片",
    shortDescription: "用品牌配色、文字、图表和界面动画制作结构清晰的宣传视频。",
    details: [
      {
        label: "最适合",
        value: "品牌宣传、产品发布、数据图表、产品界面演示、标题动画和代码讲解。",
      },
      {
        label: "不适合",
        value: "只凭一句话生成逼真人物、自然运动，或真实世界中的复杂镜头。",
      },
      {
        label: "举个例子",
        value: "输入一次产品更新，按品牌颜色生成标题、功能界面、数据和转场动画。",
      },
      {
        label: "外部服务",
        value: "一句话自动制作通常需要视频创作模型；固定模板手工调整时可以不调用模型。",
      },
      {
        label: "本机处理",
        value: "画面排版、逐帧渲染和视频合成主要在本机完成。",
      },
      {
        label: "预计耗时",
        value: "30–60 秒视频通常约 3–15 分钟，复杂动画会更久。",
      },
      { label: "设备占用", value: "渲染时会持续占用较多处理器、内存，部分效果还会使用显卡。" },
      { label: "临时磁盘", value: "建议预留 1–4 GB，任务提交前会按设置重新估算。" },
      { label: "网络消耗", value: "低到中；使用模型时需要联网，本机渲染不会上传整条视频。" },
      {
        label: "数据与隐私",
        value: "使用模型时，需求和选中的品牌文字可能离开本机；本机素材不会被自动上传。",
      },
    ],
  },
] as const;

/**
 * How a finished video is described once it leaves this page.
 *
 * The publishing page shows this string back to the operator as "待发布视频",
 * so it has to say which video *and* which of the two creation methods made
 * it — two videos about the same subject are otherwise indistinguishable
 * there. The method name is read from the catalog above rather than written
 * out again, so the label can never disagree with the card the user picked.
 */
function publishHandoff(
  method: VideoCreationMethodId,
  subject: string,
  artifactId: string,
): SelectedVideo {
  const name = VIDEO_CREATION_METHODS.find((item) => item.id === method)!.name;
  return { artifactId, videoSummary: `${subject} · ${name}` };
}

/**
 * Turn a verified artifact into something the player can accept.
 *
 * A `data:` URL is what keeps playback inside the App: the bytes are already
 * verified and already in hand, so no file path has to be opened up to the
 * WebView for a file the App itself owns.
 */
function playableSource(
  artifact: Promise<RenderedVideoArtifactPayload>,
): Promise<string> {
  return artifact.then((value) => `data:${value.mediaType};base64,${value.base64}`);
}

const EMPTY_PAGES = {
  script: {
    title: "脚本与分镜尚未生成",
    description: "创建真实视频草稿后，脚本和镜头安排会显示在这里。",
  },
  settings: {
    title: "尚未选择制作方式",
    description: "制作方式、画面风格、声音和输出规格将在后续任务接入。",
  },
  parts: {
    title: "动效零件只属于“品牌动效成片”",
    description:
      "动效零件是插入单个分镜的画面模块，选择“品牌动效成片”后才会用到；“智能素材成片”改用旁白配素材画面，不需要挑选零件。",
  },
  preview: {
    title: "还没有可预览内容",
    description: "只有真实生成或导入的画面才会进入预览。",
  },
  jobs: {
    title: "还没有真实制作任务",
    description: "任务提交后，这里会展示由本机状态机确认的进度和结果。",
  },
  artifacts: {
    title: "还没有已导入的成片",
    description: "完成并校验过的视频文件会显示在这里。",
  },
} as const;

function EmptyVideoPage({ page }: { readonly page: keyof typeof EMPTY_PAGES }) {
  const copy = EMPTY_PAGES[page];
  return (
    <Card className="video-studio-panel">
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space orientation="vertical" size={4}>
            <Typography.Text strong>{copy.title}</Typography.Text>
            <Typography.Text type="secondary">{copy.description}</Typography.Text>
          </Space>
        }
      />
    </Card>
  );
}

/**
 * What each way an automatic authoring run can fail means for the user.
 *
 * All of them used to arrive as `render_unavailable` and therefore read
 * "本机渲染组件暂时不可用，请到设置与诊断检查组件" — a specific instruction
 * attached to a code too coarse to justify it, which sends the user to inspect
 * a component that was never involved. That is the same shape of misdirection
 * this line already shipped once. Each code now says what happened and what to
 * do about it.
 *
 * The split that matters most is refused against crashed. One is the agent
 * deciding this film cannot be made, where the user's move is to describe it
 * differently; the other is our software falling over, where the user has no
 * move at all and being told to rewrite their sentence is both useless and
 * untrue. Those two must never share a sentence.
 *
 * Only `authoring_refused` may ask for a different sentence, because it is the
 * only code here where anything read the sentence at all. Failure injection on
 * 2026-07-26 found three failures wearing that sentence while nothing had read
 * anything: a model service that was never reached (two seconds), one that took
 * the connection and then went silent (363 seconds), and a tree whose pinned
 * files were simply absent (two seconds) — a packaging defect, told to the user
 * as their own description being impossible.
 *
 * Each of those now has its own code, because each has a different next move
 * and that is the only thing a failure message is for:
 *
 * - never reached      → the network, then the address in 设置与诊断
 * - reached then quiet → not the network; wait, or pick another model
 * - damaged install    → reinstall; retrying reads the same broken files
 * - our defect         → retry, then tell us
 * - actually refused   → and only here, describe the film differently
 *
 * Written once and used by both the studio's open path and the one-sentence
 * submit path so the two can never describe the same code differently.
 */
const AUTHORING_ERRORS = {
  // No longer guesses at the model service: a model that never answered has its
  // own code now, so all this one still knows is that the whole run ran long.
  authoring_timed_out:
    "自动编排超时被停下，视频没有开始制作。整个过程超过了允许的最长时间。这不是描述的问题，请稍后重试。",
  authoring_refused:
    "自动编排读完之后，判定这次描述做不出来，视频没有开始制作。这不是网络或模型服务的问题，请换一句更具体的描述后重试。",
  authoring_crashed:
    "自动编排没能完成，视频没有开始制作。这不是描述的问题，是我们这边出错了，请重试；如果一直这样，请反馈给我们。",
  authoring_answer_invalid:
    "自动编排的结果没有通过本机校验，视频没有开始制作。这是我们这边的问题，不是描述写得不好，请重试；如果一直这样请反馈给我们。",
  authoring_model_transport_failed:
    "没有收到视频创作模型服务的任何回应，视频没有开始制作。这不是描述的问题：可能是网络不通，也可能是「设置与诊断」里填的服务地址不对或者服务没在运行。请先检查网络，再到「设置与诊断」测试视频创作模型服务，然后重试。",
  // Says the connection was established, not that the network is fine: a
  // connection that dies without an RST also surfaces here as a read timeout,
  // and "网络也是通的" would send that user away from the real cause.
  //
  // The wait is named rather than described. It comes from the contract the
  // Executor obeys, so the figure here cannot drift away from the budget that
  // actually ran out.
  authoring_model_timed_out:
    `视频创作模型服务已经接上，但连续 ${MOTION_AUTHORING_IDLE_WAIT}没有再返回内容，视频没有开始制作。这不是描述的问题——服务可能在排队或过载，也可能是连接中途断了。请稍后重试；如果一直这样，到「设置与诊断」测试视频创作模型服务，或者换一个模型。`,
  authoring_installation_damaged:
    "自动编排要用的程序文件没有通过完整性校验，视频没有开始制作。这不是描述的问题，也不是重试能解决的：请重新安装 App；重装之后仍然这样的话，请反馈给我们。",
} as const satisfies Partial<Record<MaterialVideoStudioErrorCode, string>>;

const OPEN_ERRORS: Record<MaterialVideoStudioErrorCode, string> = {
  configuration_required: "请先到“设置与诊断”配置并测试文案模型服务。",
  ...AUTHORING_ERRORS,
  process_unavailable: "本机视频制作服务暂时无法启动，请稍后重试。",
  storage_unavailable: "无法创建本机视频工作区，请检查磁盘空间和目录权限。",
  view_unavailable: "完整制作界面暂时无法打开，请稍后重试。",
  job_unavailable: "制作任务状态暂时不可用，请稍后重试。",
  draft_invalid: "草稿内容不符合要求，请检查后重试。",
  render_unavailable: "本机渲染组件暂时不可用，请到设置与诊断检查组件。",
  protocol_mismatch: "视频制作服务版本不匹配，请更新 App 后重试。",
  operation_unavailable: "视频制作暂时不可用，请稍后重试。",
};

/**
 * What the one-sentence card says when a submission comes back a failure.
 *
 * It reads as a lookup rather than a chain of conditionals because the native
 * side now distinguishes five outcomes here, and a chain that long is where a
 * new code quietly falls through to the catch-all sentence.
 *
 * `render_unavailable` keeps its instruction to go and check the components,
 * because after the authoring failures were split out it is the only code left
 * that really does mean a packaged part could not be resolved.
 */
const BRIEF_ERRORS: Partial<Record<MaterialVideoStudioErrorCode, string>> = {
  // Also where a saved-but-unusable model configuration lands, so the wording
  // covers both "you have not set one up" and "the one you saved is not
  // acceptable" — the move is the same page either way.
  configuration_required: "请先到“设置与诊断”配置并测试视频创作模型服务。",
  ...AUTHORING_ERRORS,
  render_unavailable: "本机渲染组件暂时不可用，请到“设置与诊断”检查组件。",
};

const BRIEF_SUBMIT_FALLBACK = "一句话自动制作暂时无法提交，请稍后重试。";

/** Ties the 打开完整制作界面 button to the note saying why it is greyed out. */
const OPEN_STUDIO_HINT_ID = "video-studio-open-full-hint";

function NewVideoPage({
  gateway,
  onOpened,
  selectedMethod,
  onSelectMethod,
  motionSubject,
  onMotionSubjectChange,
  brief,
  onBriefChange,
  briefFilmSeconds,
  onBriefFilmSecondsChange,
  briefBusy,
  onSubmitBrief,
  briefProblem,
  embedded,
}: {
  readonly gateway: MaterialVideoStudioGateway;
  readonly onOpened: () => void;
  readonly selectedMethod: VideoCreationMethodId | null;
  readonly onSelectMethod: (method: VideoCreationMethodId) => void;
  readonly motionSubject: string;
  readonly onMotionSubjectChange: (subject: string) => void;
  readonly brief: string;
  readonly onBriefChange: (brief: string) => void;
  readonly briefFilmSeconds: number;
  readonly onBriefFilmSecondsChange: (filmSeconds: number) => void;
  readonly briefBusy: boolean;
  readonly onSubmitBrief: () => void;
  readonly embedded: boolean;
  /**
   * What is wrong with the sentence as typed, or null.
   *
   * Deliberately not in the run store next to the submission results: nothing
   * has been submitted, the operator is looking straight at the box, and a
   * complaint about typing has no business surviving a page change or putting
   * a mark on the sidebar.
   */
  readonly briefProblem: string | null;
}) {
  const [opening, setOpening] = useState(false);
  const [openMessage, setOpenMessage] = useState<{ type: "success" | "error"; text: string } | null>(
    null,
  );
  const selectedName = VIDEO_CREATION_METHODS.find(
    (method) => method.id === selectedMethod,
  )?.name;
  /**
   * Put the one-sentence card on screen the moment it appears.
   *
   * The method cards are the last thing on this page, so the operator has
   * scrolled to the bottom to reach them. Choosing 品牌动效成片 inserts this
   * card *above* that position; the browser's scroll anchoring then holds the
   * old view still and the card arrives off screen — measured at y = -412 with
   * nothing on screen changing except a tag. Pressing the button that starts
   * the demo looked like it did nothing.
   *
   * A callback ref rather than an effect because the one moment that matters is
   * the node arriving. The optional call is for the unit-test DOM, which has no
   * layout and therefore does not implement `scrollIntoView`; `toBeInViewport`
   * in `e2e/video-studio-one-sentence.spec.ts` is what holds the real behaviour.
   */
  const revealOneSentenceCard = useCallback((node: HTMLDivElement | null) => {
    node?.scrollIntoView?.({ block: "start" });
  }, []);
  const briefWait = motionBriefWaitEstimate(briefFilmSeconds);

  return (
    /*
     * The card used to be called 从一句话开始 and led with a 1102×115px textarea
     * that was disabled, unexplained, labelled 视频需求 and bound to the film
     * *title*. Three separate lies in the first thing a new user sees: the
     * biggest control on the page did nothing and never said why, its
     * accessible name described a field it was not, and the real one-sentence
     * entry was a sub-card further down. Deleting it removes all three at once
     * and costs nothing — the title it wrote into is still edited by the
     * 视频标题 field inside 固定模板手工制作, and the one-sentence path never
     * read that value at all.
     */
    /*
     * No card head.
     *
     * It said 「新建视频」, and so does the tab immediately above it — the two
     * sat 17px apart, y=188 and y=254, the same three words twice. The head cost
     * 56px of the single screen this step gets while telling the operator
     * nothing the active tab, the page title and the sidebar entry had not
     * already told him three times over. The tab is the copy that has to stay:
     * it is how you come back to this step from the other six.
     */
    <Card className="video-studio-panel">
      <Space orientation="vertical" size="middle" className="video-studio-new-form">
        <Typography.Text type="secondary">
          先选择下面的制作方式，再填写这次视频的内容。
        </Typography.Text>
        {selectedMethod === "motion_composition_v1" ? (
          <div ref={revealOneSentenceCard}>
            <Card size="small" title="一句话自动制作">
              <Space orientation="vertical" size="small">
                <Input.TextArea
                  aria-label="一句话视频需求"
                  rows={3}
                  maxLength={MOTION_BRIEF_LIMITS.maxBriefCharacters}
                  value={brief}
                  onChange={(event) => onBriefChange(event.target.value)}
                  placeholder="例如：用蓝色商务风做一段本周销售增长说明"
                />
                <span className="motion-brief-length">
                  <label htmlFor="motion-brief-seconds">成片时长（秒）</label>
                  <InputNumber
                    id="motion-brief-seconds"
                    min={DURATION_SECONDS_MINIMUM}
                    max={MOTION_BRIEF_LIMITS.durationSecondsMaximum}
                    precision={0}
                    value={briefFilmSeconds}
                    onChange={(value) => {
                      if (typeof value === "number") onBriefFilmSecondsChange(value);
                    }}
                  />
                </span>
                <Typography.Text type="secondary">
                  {`描述一句就够了。按 ${briefFilmSeconds} 秒来安排内容，实际片长以成片为准、通常会更长一些——每个镜头会等它的话说完、动效播完，不会中途切断。文案、分镜和画面由视频创作模型自动生成，渲染仍在本机完成。`}
                </Typography.Text>
                {/*
                  * 时间代价必须在拉之前看得见。
                  *
                  * 路线 A 是一个镜头渲染一次，每次都要重新起浏览器（契约记作 30 秒），
                  * 所以等待随镜头数涨、涨得比片长快。只给控件不给这句话，
                  * 就是请人顺手拉到 180 秒，然后对着不动的屏幕等一个多小时。
                  */}
                <Typography.Text type="secondary">
                  {`本机是一个镜头渲染一次：${briefFilmSeconds} 秒大约 ${briefWait.shots} 个镜头，编排加渲染最长约 ${motionSpokenDuration(briefWait.ceilingSeconds)}。片子越长镜头越多，等待时间涨得比片长快。`}
                </Typography.Text>
                <Button
                  type="primary"
                  loading={briefBusy}
                  disabled={briefBusy}
                  onClick={onSubmitBrief}
                >
                  开始自动制作
                </Button>
                {briefProblem === null ? null : (
                  <Alert type="error" showIcon title={briefProblem} />
                )}
              </Space>
            </Card>
          </div>
        ) : null}
        {selectedMethod === "motion_composition_v1" ? (
          <Card size="small" title="固定模板手工制作">
            <Space orientation="vertical" size="small">
              <label>
                <span>视频标题</span>
                <Input
                  aria-label="视频标题"
                  maxLength={80}
                  value={motionSubject}
                  onChange={(event) => onMotionSubjectChange(event.target.value)}
                />
              </label>
              <Alert
                type="info"
                showIcon
                title="当前没有调用视频创作模型"
                description="你正在从固定模板开始，段数、每段时长、文字、分镜、风格和品牌素材都由你手工调整。"
              />
            </Space>
          </Card>
        ) : null}
        <div className="video-method-heading">
          <div>
            <Typography.Title level={4}>选择制作方式</Typography.Title>
            <Typography.Text type="secondary">
              以下耗时和空间是 30–60 秒视频的参考范围，提交任务前会结合设备与设置重新估算。
            </Typography.Text>
          </div>
          {selectedName ? <Tag color="blue">已选择：{selectedName}</Tag> : <Tag>尚未选择</Tag>}
        </div>
        <div className="video-method-grid" aria-label="视频制作方式">
          {VIDEO_CREATION_METHODS.map((method) => {
            const selected = method.id === selectedMethod;
            return (
              <article
                key={method.id}
                className={`video-method-card${selected ? " video-method-card-selected" : ""}`}
              >
                <div className="video-method-card-header">
                  <Typography.Title level={3}>{method.name}</Typography.Title>
                  <Tag color={selected ? "blue" : "default"}>{selected ? "已选择" : "可选择"}</Tag>
                </div>
                <Typography.Paragraph className="video-method-summary">
                  {method.shortDescription}
                </Typography.Paragraph>
                {/*
                 * The button comes before the ten rows that explain the method,
                 * not after them.
                 *
                 * After them is where it was, and the rows are 466px tall, so
                 * on the 1280x800 window this product actually ships the two
                 * 选择 buttons sat at y=1043 and y=1066 — 243 and 266px past
                 * the fold. Choosing a method is the first move in making a
                 * video and it is the whole point of this screen, yet the
                 * opening view of it carried no pressable control at all: the
                 * customer met a wall of prose and had to scroll roughly 340px
                 * on faith before finding anything to click.
                 *
                 * The rows themselves are kept, in full and unedited — they are
                 * how someone works out which of the two methods they want, and
                 * cutting them to win back height would trade one bad screen
                 * for a different one. Reading them is just no longer the toll
                 * for reaching the button. `e2e/video-studio-density.spec.ts`
                 * pins both the fold and this order, because the `<dl>` grows
                 * with every row added to `VIDEO_CREATION_METHODS`.
                 */}
                <Button
                  type={selected ? "primary" : "default"}
                  aria-label={`选择${method.name}`}
                  aria-pressed={selected}
                  className="video-method-select"
                  onClick={() => onSelectMethod(method.id)}
                >
                  {selected ? `已选择${method.name}` : `选择${method.name}`}
                </Button>
                {/*
                  * Ten rows, behind a disclosure that names its own card.
                  *
                  * Expanded, the two `<dl>`s were 466px of the 1240px page and
                  * the single reason 「新建视频」 could not fit the 736px it is
                  * given. Collapsed by default rather than open: the one-line
                  * summary above stays visible and is what tells a first-time
                  * viewer what each method is for, while these ten rows are the
                  * comparison you consult once you are choosing between them —
                  * 最适合 against 不适合, 耗时 against 磁盘 against 隐私. Default
                  * open would keep the page at 1240px and give back the scroll
                  * this whole task exists to remove.
                  *
                  * The label carries the method name because there are two of
                  * these controls side by side; 「详细说明」 alone would leave a
                  * screen reader user with two identical toggles and no way to
                  * tell which card each one opens. Naming it in the visible
                  * label rather than an `aria-label` override keeps the
                  * accessible name and the visible text the same string.
                  *
                  * `Collapse` rather than a native `<details>` because this
                  * product already discloses long reference text exactly this
                  * way — see the licence texts in `ThirdPartySoftwareNotice`.
                  */}
                <Collapse
                  ghost
                  size="small"
                  className="video-method-disclosure"
                  /*
                   * Supplying the arrow to keep it out of the button's name.
                   *
                   * antd's default expand icon is an `<span role="img">` whose
                   * `aria-label` is the literal string "collapsed", and a name
                   * computed from content swallows it: Chromium reported this
                   * control as 「collapsed 智能素材成片的详细说明」 — an English
                   * state word read aloud in the middle of a Chinese label, and
                   * redundant besides, since `aria-expanded` already carries the
                   * state. Playwright's substring matching hid it; the aria
                   * snapshot is what showed it. A decorative arrow marked
                   * `aria-hidden` leaves the name as exactly the label.
                   */
                  expandIcon={({ isActive }) => (
                    <span
                      aria-hidden="true"
                      className={
                        isActive === true
                          ? "video-method-disclosure-arrow video-method-disclosure-arrow-open"
                          : "video-method-disclosure-arrow"
                      }
                    />
                  )}
                  items={[
                    {
                      key: "details",
                      label: `${method.name}的详细说明`,
                      children: (
                        <dl className="video-method-details">
                          {method.details.map((detail) => (
                            <div key={detail.label}>
                              <dt>{detail.label}</dt>
                              <dd>{detail.value}</dd>
                            </div>
                          ))}
                        </dl>
                      ),
                    },
                  ]}
                />
              </article>
            );
          })}
        </div>
        {embedded ? (
          <Alert
            type="info"
            showIcon
            title="完整制作流程将直接嵌入当前 App，不会打开额外窗口。当前真实内嵌服务尚未接入。"
          />
        ) : openMessage === null ? (
          <Alert
            type="info"
            showIcon
            title="“智能素材成片”在独立完整界面制作；“品牌动效成片”在当前 App 内编辑和预览。"
          />
        ) : (
          <Alert type={openMessage.type} showIcon title={openMessage.text} />
        )}
        {/*
         * A greyed button that never says why.
         *
         * Note it cannot be fixed with `title`: browsers do not fire the native
         * tooltip on a `disabled` button, so the attribute would be there and
         * the user would still see nothing. A visible note carrying an id the
         * button points at is the pattern this product already got right on
         * 提交本机渲染 in the preview tab, and it works for a screen reader too.
         */}
        {embedded ? null : (
          <Space orientation="vertical" size={4}>
            <Button
              type="primary"
              loading={opening}
              disabled={selectedMethod !== "material_montage_v1" || opening}
              {...(selectedMethod === "material_montage_v1"
                ? {}
                : { "aria-describedby": OPEN_STUDIO_HINT_ID })}
              onClick={() => {
                setOpening(true);
                setOpenMessage(null);
                void gateway
                  .open()
                  .then(() => {
                    onOpened();
                    setOpenMessage({ type: "success", text: "完整制作界面已打开。" });
                  })
                  .catch((error: unknown) => {
                    const code =
                      error instanceof MaterialVideoStudioGatewayError
                        ? error.code
                        : "operation_unavailable";
                    setOpenMessage({ type: "error", text: OPEN_ERRORS[code] });
                  })
                  .finally(() => setOpening(false));
              }}
            >
              打开完整制作界面
            </Button>
            {selectedMethod === "material_montage_v1" ? null : (
              <Typography.Text id={OPEN_STUDIO_HINT_ID} type="secondary">
                这个界面只用于「智能素材成片」，先在上面选中它才能打开。
              </Typography.Text>
            )}
          </Space>
        )}
      </Space>
    </Card>
  );
}

function MotionScriptPage({
  beats,
  secondsPerBeat,
  onChange,
  onBeatCountChange,
  onSecondsPerBeatChange,
}: {
  readonly beats: readonly MotionVideoBeatDraft[];
  readonly secondsPerBeat: number;
  readonly onChange: (beats: readonly MotionVideoBeatDraft[]) => void;
  readonly onBeatCountChange: (beatCount: number) => void;
  readonly onSecondsPerBeatChange: (secondsPerBeat: number) => void;
}) {
  const problem = motionDurationProblem(beats.length, secondsPerBeat);
  return (
    <Card className="video-studio-panel" title="脚本与分镜">
      <Space orientation="vertical" size="middle" className="motion-script-editor">
        <Space wrap size="middle" className="motion-script-duration">
          <span>
            <label htmlFor="motion-beat-count">段数</label>
            <InputNumber
              id="motion-beat-count"
              min={MOTION_DURATION_LIMITS.beatCountMinimum}
              max={MOTION_DURATION_LIMITS.beatCountMaximum}
              precision={0}
              value={beats.length}
              onChange={(value) => {
                if (typeof value === "number") onBeatCountChange(value);
              }}
            />
          </span>
          <span>
            <label htmlFor="motion-seconds-per-beat">每段时长（秒）</label>
            <InputNumber
              id="motion-seconds-per-beat"
              min={MOTION_DURATION_LIMITS.secondsPerBeatMinimum}
              max={MOTION_DURATION_LIMITS.secondsPerBeatMaximum}
              precision={0}
              value={secondsPerBeat}
              onChange={(value) => {
                if (typeof value === "number") onSecondsPerBeatChange(value);
              }}
            />
          </span>
        </Space>
        <Alert
          type={problem === null ? "info" : "warning"}
          showIcon
          title={motionStoryboardSummary(beats.length, secondsPerBeat)}
          description={
            problem ??
            "这里编辑的是固定模板声明过的文字变量；提交前仍可反复预览和精修。"
          }
        />
        {beats.map((beat, index) => (
          <Card key={index} size="small" title={`第 ${index + 1} 段`}>
            <Space orientation="vertical" size="small" className="motion-script-fields">
              <Input
                aria-label={`第 ${index + 1} 段标题`}
                maxLength={160}
                value={beat.title}
                onChange={(event) => {
                  const next = beats.map((item, beatIndex) =>
                    beatIndex === index ? { ...item, title: event.target.value } : item,
                  );
                  onChange(next);
                }}
              />
              <Input
                aria-label={`第 ${index + 1} 段字幕`}
                maxLength={160}
                value={beat.caption}
                onChange={(event) => {
                  const next = beats.map((item, beatIndex) =>
                    beatIndex === index ? { ...item, caption: event.target.value } : item,
                  );
                  onChange(next);
                }}
              />
            </Space>
          </Card>
        ))}
      </Space>
    </Card>
  );
}

function MotionPreviewPage({
  draft,
  submitting,
  onSubmit,
}: {
  readonly draft: MotionDraft;
  readonly submitting: boolean;
  readonly onSubmit: () => void;
}) {
  const [activeBeat, setActiveBeat] = useState(0);
  const [playing, setPlaying] = useState(false);
  const lastBeat = draft.beats.length - 1;
  /*
   * Hold each beat for as long as the film will hold it.
   *
   * This was a hard-coded 500ms while the storyboard page one tab away wrote
   * 「共 3 段 · 每段 4 秒 · 成片约 12 秒」, so the preview ran at eight times
   * the speed of the thing it was previewing and three beats were over in a
   * second and a half. It is the mirror of the retired three-second default:
   * the data layer was fixed to read its length from the duration contract and
   * the player was left behind. Watching a twelve second film take twelve
   * seconds is the point of a preview.
   */
  const beatMillis = draft.secondsPerBeat * 1000;
  useEffect(() => {
    if (!playing) return;
    const timer = window.setTimeout(() => {
      setActiveBeat((current) => {
        if (current >= lastBeat) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, beatMillis);
    return () => window.clearTimeout(timer);
  }, [activeBeat, beatMillis, lastBeat, playing]);
  const style = MOTION_STYLE_CATALOG.find((item) => item.id === draft.style.stylePresetId);
  const primary = draft.style.primaryColor || style?.preview.accent || "#1f4fd8";
  const secondary = draft.style.secondaryColor || style?.preview.paper || "#f4f1ea";
  const beat = draft.beats[Math.min(activeBeat, lastBeat)]!;
  const durationProblem = motionDurationProblem(draft.beats.length, draft.secondsPerBeat);
  const valid =
    draft.subject.trim() !== "" &&
    draft.beats.every((item) => item.title.trim() !== "" && item.caption.trim() !== "") &&
    draft.style.stylePresetId !== null &&
    durationProblem === null;
  return (
    <Card className="video-studio-panel" title="品牌动效播放预览">
      <Space orientation="vertical" size="middle" className="motion-preview-workspace">
        <section
          role="region"
          aria-label="品牌动效播放预览"
          className="motion-playback-preview"
          style={{
            background: `linear-gradient(135deg, ${secondary}, #ffffff 58%, ${primary}22)`,
          }}
        >
          <div className="motion-playback-brand" style={{ color: primary }}>
            {draft.subject}
          </div>
          {draft.style.logo === null ? null : (
            <img
              className="motion-playback-logo"
              src={draft.style.logo.previewUrl}
              alt="品牌动效 Logo"
            />
          )}
          <div className="motion-playback-scene">
            <span style={{ color: primary }}>
              第 {Math.min(activeBeat, lastBeat) + 1} 段 · {style?.displayName ?? "尚未选择风格"}
            </span>
            <h3>{beat.title}</h3>
            <p>{beat.caption}</p>
          </div>
          <div className="motion-playback-progress" style={{ backgroundColor: primary }}>
            第 {Math.min(activeBeat, lastBeat) + 1} 段 / {draft.beats.length}
          </div>
        </section>
        <Space wrap>
          <Button
            onClick={() => {
              setActiveBeat(0);
              setPlaying(true);
            }}
          >
            播放预览
          </Button>
          <Button
            type="primary"
            loading={submitting}
            disabled={!valid || submitting}
            onClick={onSubmit}
          >
            提交本机渲染
          </Button>
        </Space>
        {valid ? null : (
          <Alert
            type="warning"
            showIcon
            title={durationProblem ?? "请先补全标题、每段脚本并选择一套整体画面风格。"}
          />
        )}
      </Space>
    </Card>
  );
}

function SettingsPage({
  method,
  onMotionStyleChange,
}: {
  readonly method: VideoCreationMethodId | null;
  readonly onMotionStyleChange: (style: MotionStyleDraftSelection) => void;
}) {
  if (method === null) {
    return <EmptyVideoPage page="settings" />;
  }
  if (method === "material_montage_v1") {
    return (
      <Card className="video-studio-panel" title="智能素材成片设置">
        <Typography.Text type="secondary">
          智能素材成片的素材来源、配音和字幕设置在完整制作界面中调整。
        </Typography.Text>
      </Card>
    );
  }
  return <MotionStyleCatalog onDraftChange={onMotionStyleChange} />;
}

const STATUS_COPY = {
  running: { label: "制作中", color: "processing" },
  succeeded: { label: "已完成", color: "success" },
  failed: { label: "制作失败", color: "error" },
  cancelled: { label: "已取消", color: "default" },
} as const;

const MOTION_STATUS_COPY = {
  queued: { label: "准备中", color: "default" },
  rendering: { label: "逐帧渲染中", color: "processing" },
  encoding: { label: "正在合成视频", color: "processing" },
  // 「正在取消」不是「已取消」：按钮按下去只是把请求写下来，真正停下来的是
  // 那个开着浏览器和编码器的执行线程。这一格存在的意义就是让按下按钮立刻有
  // 反应，同时不去替执行器宣布它已经停了。
  cancelling: { label: "正在取消", color: "processing" },
  succeeded: { label: "已完成", color: "success" },
  failed: { label: "制作失败", color: "error" },
  cancelled: { label: "已取消", color: "default" },
} as const;

/** 还在跑、还需要显示用时的状态。 */
const MOTION_IN_FLIGHT: readonly MotionRenderJobStatus[] = [
  "queued",
  "rendering",
  "encoding",
  "cancelling",
];

/** 还能被取消的状态——已经在取消的不再重复提供按钮。 */
const MOTION_CANCELLABLE: readonly MotionRenderJobStatus[] = [
  "queued",
  "rendering",
  "encoding",
];

function JobPage({
  jobs,
  motionJobs,
  ownMotionJobs,
  pending,
  now,
  busy,
  onCancel,
  onCancelMotion,
}: {
  readonly jobs: readonly MaterialRenderJobSnapshot[];
  readonly motionJobs: readonly MotionRenderJobSnapshot[];
  readonly ownMotionJobs: ReadonlyMap<string, OwnMotionJob>;
  readonly pending: MotionRunPending | null;
  readonly now: number;
  readonly busy: boolean;
  readonly onCancel: (id: string) => void;
  readonly onCancelMotion: (id: string) => void;
}) {
  if (jobs.length === 0 && motionJobs.length === 0 && pending === null) {
    return <EmptyVideoPage page="jobs" />;
  }
  return (
    <Card className="video-studio-panel" title="本机制作任务">
      {pending === null ? null : (
        <Space orientation="vertical" size="middle" className="video-job-card">
          <Space wrap>
            <Typography.Text strong>{pending.subject}</Typography.Text>
            <Tag color="blue">品牌动效成片</Tag>
            <Tag color="processing">
              {pending.kind === "one_sentence" ? "正在自动编排" : "正在提交"}
            </Tag>
          </Space>
          <Typography.Text type="secondary">
            {motionPendingLabel(pending, now)}
          </Typography.Text>
          {pending.kind === "one_sentence" ? (
            <Typography.Text type="secondary">
              文案、分镜和画面由视频创作模型生成，这一步不在本机跑，暂时也不能取消；编排返回后本机渲染才开始。
            </Typography.Text>
          ) : null}
        </Space>
      )}
      {motionJobs.map((job) => (
        <Space key={job.renderJobId} orientation="vertical" size="middle" className="video-job-card">
          <Space wrap>
            <Typography.Text strong>{job.subject}</Typography.Text>
            <Tag color="blue">品牌动效成片</Tag>
            <Tag>{job.styleDisplayName}</Tag>
            <Tag color={MOTION_STATUS_COPY[job.status].color}>
              {MOTION_STATUS_COPY[job.status].label}
            </Tag>
          </Space>
          <Progress
            percent={job.progressPercent}
            {...(job.status === "failed" ? { status: "exception" as const } : {})}
          />
          {job.status === "failed" ? (
            <Alert type="error" showIcon title={motionFailureAdvice(job.failureCode)} />
          ) : null}
          {MOTION_IN_FLIGHT.includes(job.status)
            ? (() => {
                const timing = motionJobTiming(ownMotionJobs.get(job.renderJobId), now);
                return timing === null ? null : (
                  <Typography.Text type="secondary">{timing}</Typography.Text>
                );
              })()
            : null}
          {MOTION_CANCELLABLE.includes(job.status) ? (
            <Popconfirm
              title="确定取消这个品牌动效任务吗？"
              okText="确定"
              cancelText="返回"
              onConfirm={() => onCancelMotion(job.renderJobId)}
            >
              <Button danger disabled={busy} aria-label="取消品牌动效任务">
                取消任务
              </Button>
            </Popconfirm>
          ) : null}
        </Space>
      ))}
      {jobs.map((job) => (
        <Space key={job.renderJobId} orientation="vertical" size="middle" className="video-job-card">
          <Space>
            <Typography.Text strong>{job.subject}</Typography.Text>
            <Tag color={STATUS_COPY[job.status].color}>{STATUS_COPY[job.status].label}</Tag>
          </Space>
          <Progress
            percent={job.progressPercent}
            {...(job.status === "failed" ? { status: "exception" as const } : {})}
          />
          {job.status === "failed" ? <Alert type="error" showIcon title="本次制作未成功，可以返回完整制作界面调整后重试。" /> : null}
          {job.status === "running" ? (
            <Popconfirm
              title="确定取消这个制作任务吗？"
              okText="确定"
              cancelText="返回"
              onConfirm={() => onCancel(job.renderJobId)}
            >
              <Button danger disabled={busy}>取消任务</Button>
            </Popconfirm>
          ) : null}
        </Space>
      ))}
    </Card>
  );
}

/// A film that rendered every frame and never moved is not a broken renderer,
/// and telling the user to check their disk sends them to fix something that
/// was never wrong. The still-image case gets its own words.
function motionFailureAdvice(
  failureCode: MotionRenderJobSnapshot["failureCode"],
): string {
  if (failureCode === "static_render") {
    return "这条成片的画面自始至终没有变化，已经停下来没有生成视频。换一句更具体的描述重新制作通常就能解决。";
  }
  return "本机渲染未完成，请检查视频组件与磁盘空间后重试。";
}

function ArtifactPage({
  jobs,
  motionJobs,
  busy,
  onDelete,
  onDeleteMotion,
  onReadMotion,
  onReadMaterial,
  onPublish,
}: {
  readonly jobs: readonly MaterialRenderJobSnapshot[];
  readonly motionJobs: readonly MotionRenderJobSnapshot[];
  readonly busy: boolean;
  readonly onDelete: (id: string) => void;
  readonly onDeleteMotion: (id: string) => void;
  readonly onReadMotion: (id: string) => Promise<string>;
  readonly onReadMaterial: (id: string) => Promise<string>;
  readonly onPublish: ((video: SelectedVideo) => void) | undefined;
}) {
  const artifacts = jobs.filter((job) => job.artifactId !== null);
  const motionArtifacts = motionJobs.filter((job) => job.artifactId !== null);
  const [playing, setPlaying] = useState<{ subject: string; source: string } | null>(null);
  const [playError, setPlayError] = useState(false);
  // One player, one error banner, one way in: both creation methods produce the
  // same kind of MP4, and a second copy of this handler would be a second place
  // for the failure path to drift.
  const play = (subject: string, read: Promise<string>) => {
    setPlayError(false);
    void read
      .then((source) => setPlaying({ subject, source }))
      .catch(() => setPlayError(true));
  };
  if (artifacts.length === 0 && motionArtifacts.length === 0) {
    return <EmptyVideoPage page="artifacts" />;
  }
  return (
    <Card className="video-studio-panel" title="已校验成片">
      {playing === null ? null : (
        <video
          aria-label={`${playing.subject}成片播放器`}
          className="motion-artifact-player"
          src={playing.source}
          autoPlay
          controls
          playsInline
          onError={() => setPlayError(true)}
        />
      )}
      {playError ? <Alert type="error" showIcon title="暂时无法读取这条成片。" /> : null}
      {motionArtifacts.map((job) => (
        <Space key={job.artifactId} orientation="vertical" size="small" className="video-job-card">
          <Space wrap>
            <Typography.Text strong>{job.subject}</Typography.Text>
            <Tag color="blue">品牌动效成片</Tag>
            <Tag>{job.styleDisplayName}</Tag>
          </Space>
          <Typography.Text type="secondary">
            MP4 视频 · {((job.artifactSizeBytes ?? 0) / 1024 / 1024).toFixed(1)} MB
          </Typography.Text>
          <Space wrap>
            <Button
              aria-label={`播放${job.subject}`}
              disabled={busy}
              onClick={() => play(job.subject, onReadMotion(job.artifactId!))}
            >
              播放成片
            </Button>
            {onPublish === undefined ? null : (
              <Button
                type="primary"
                aria-label={`发布${job.subject}`}
                disabled={busy}
                onClick={() =>
                  onPublish(
                    publishHandoff("motion_composition_v1", job.subject, job.artifactId!),
                  )
                }
              >
                去发布
              </Button>
            )}
            <Popconfirm
              title="删除后无法恢复，确定删除吗？"
              okText="确定"
              cancelText="返回"
              onConfirm={() => onDeleteMotion(job.artifactId!)}
            >
              <Button danger disabled={busy}>删除成片</Button>
            </Popconfirm>
          </Space>
        </Space>
      ))}
      {artifacts.map((job) => (
        <Space key={job.artifactId} orientation="vertical" size="small" className="video-job-card">
          <Typography.Text strong>{job.subject}</Typography.Text>
          <Typography.Text type="secondary">
            MP4 视频 · {((job.artifactSizeBytes ?? 0) / 1024 / 1024).toFixed(1)} MB
          </Typography.Text>
          <Space wrap>
            <Button
              aria-label={`播放${job.subject}`}
              disabled={busy}
              onClick={() => play(job.subject, onReadMaterial(job.artifactId!))}
            >
              播放成片
            </Button>
            {onPublish === undefined ? null : (
              <Button
                type="primary"
                aria-label={`发布${job.subject}`}
                disabled={busy}
                onClick={() =>
                  onPublish(
                    publishHandoff("material_montage_v1", job.subject, job.artifactId!),
                  )
                }
              >
                去发布
              </Button>
            )}
            <Popconfirm
              title="删除后无法恢复，确定删除吗？"
              okText="确定"
              cancelText="返回"
              onConfirm={() => onDelete(job.artifactId!)}
            >
              <Button danger disabled={busy}>删除成片</Button>
            </Popconfirm>
          </Space>
        </Space>
      ))}
    </Card>
  );
}

export function VideoStudio({
  gateway,
  onPublishArtifact,
  embedded = false,
}: {
  readonly gateway: MaterialVideoStudioGateway;
  readonly embedded?: boolean | undefined;
  /**
   * Send a finished video on to the publishing page.
   *
   * Optional because the studio still has to render where there is no
   * publishing page to hand to; a button that leads nowhere is worse than no
   * button at all.
   */
  readonly onPublishArtifact?: ((video: SelectedVideo) => void) | undefined;
}) {
  const [jobs, setJobs] = useState<readonly MaterialRenderJobSnapshot[]>([]);
  const [motionJobs, setMotionJobs] = useState<readonly MotionRenderJobSnapshot[]>([]);
  const [busy, setBusy] = useState(false);
  const [motionPartSelections, setMotionPartSelections] = useState<
    readonly (readonly string[])[]
  >(() => DEFAULT_MOTION_BEATS.map(() => []));
  const [jobError, setJobError] = useState(false);
  const [briefProblem, setBriefProblem] = useState<string | null>(null);
  /*
   * The run, the sentence, the chosen method and the open tab all live outside
   * this component, because the shell unmounts it the moment the operator
   * clicks another sidebar entry and a run takes minutes. See
   * `motion-run-store.ts` for what that cost before.
   *
   * The tab is controlled from there too: finishing a step should move the
   * operator to where that step's result landed, and it should still be there
   * when he comes back.
   */
  const {
    pending,
    message,
    ownJobs: ownMotionJobs,
    brief,
    filmSeconds: briefFilmSeconds,
    selectedMethod,
    activeTab,
  } = useMotionRun();
  const [now, setNow] = useState(() => Date.now());
  const [motionDraft, setMotionDraft] = useState<MotionDraft>({
    subject: "新品发布",
    secondsPerBeat: MOTION_DURATION_LIMITS.secondsPerBeatDefault,
    beats: DEFAULT_MOTION_BEATS,
    style: EMPTY_MOTION_STYLE,
  });
  // Per-beat part selections are indexed by beat, so they have to grow and
  // shrink with the storyboard or a newly added beat would silently discard
  // every part the user picks for it.
  const changeMotionBeatCount = useCallback((beatCount: number) => {
    setMotionDraft((current) => ({
      ...current,
      beats: resizeMotionBeats(current.beats, beatCount, createMotionBeat),
    }));
    setMotionPartSelections((current) =>
      resizeMotionBeats(current, beatCount, () => []),
    );
  }, []);
  const refresh = useCallback(() => {
    void Promise.all([gateway.jobs(), gateway.motionJobs()]).then(([material, motion]) => {
      setJobs(material);
      setMotionJobs(motion);
      setJobError(false);
    }).catch(() => setJobError(true));
  }, [gateway]);
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);
  // Only tick while something is being timed. An always-running second hand
  // would re-render this page forever on a machine with nothing to render.
  // A stale `now` on the first frame after a clock starts reads as a negative
  // age, which every reader clamps to zero — the right answer — and the next
  // tick corrects it a second later. So there is no need to set it here.
  const timingSomething = pending !== null || ownMotionJobs.size > 0;
  useEffect(() => {
    if (!timingSomething) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [timingSomething]);
  const act = (operation: Promise<void>) => {
    setBusy(true);
    void operation.then(refresh).catch(() => setJobError(true)).finally(() => setBusy(false));
  };
  const onMotionStyleChange = useCallback((style: MotionStyleDraftSelection) => {
    setMotionDraft((current) => ({ ...current, style }));
  }, []);
  /**
   * Hand a one-sentence brief to the authoring agent.
   *
   * The brief is checked here first so an empty or over-long sentence reads as
   * a sentence to fix rather than as a failed submission; the same check runs
   * again in the gateway and once more natively, because this one only guards
   * the typing, not the request.
   */
  const submitBrief = () => {
    const problem = motionBriefProblem(brief, briefFilmSeconds);
    if (problem !== null) {
      setBriefProblem(problem);
      return;
    }
    setBriefProblem(null);
    const request: MotionVideoBriefRequest = {
      creationMode: "one_sentence_v1",
      brief: brief.trim(),
      aspectRatio: MOTION_BRIEF_LIMITS.aspectRatios[0]!,
      durationSeconds: briefFilmSeconds,
      language: MOTION_BRIEF_LIMITS.languages[0]!,
    };
    setBusy(true);
    /*
     * Record the run before it leaves, and move to where it will be watched.
     *
     * The native command does not return until the authoring pass is over —
     * 136 to 178 seconds, measured — and it only writes the job snapshot at the
     * end of it. Waiting for that to happen before showing anything is what
     * left the jobs list empty for the whole wait. Every callback below writes
     * to the store rather than to this component, because by the time they run
     * this component may well have been unmounted by a sidebar click, and a
     * failure written into a dead component is a failure the operator is never
     * told about.
     */
    startMotionRun({
      kind: "one_sentence",
      subject: request.brief,
      startedAt: Date.now(),
    });
    setMotionActiveTab("jobs");
    void gateway
      .submitMotionBrief(request)
      .then((snapshot) => {
        settleMotionRun(snapshot.renderJobId, briefFilmSeconds, "one_sentence", {
          tone: "info",
          text: "已提交一句话自动制作，编排完成，本机渲染开始了。",
        });
        refresh();
      })
      .catch((error: unknown) => {
        const code =
          error instanceof MaterialVideoStudioGatewayError
            ? error.code
            : "operation_unavailable";
        failMotionRun({ tone: "error", text: BRIEF_ERRORS[code] ?? BRIEF_SUBMIT_FALLBACK });
      })
      .finally(() => setBusy(false));
  };
  const submitMotion = () => {
    const style = MOTION_STYLE_CATALOG.find(
      (item) => item.id === motionDraft.style.stylePresetId,
    );
    if (style === undefined) return;
    const request: MotionVideoDraftRequest = {
      creationMode: MOTION_CREATION_MODE,
      subject: motionDraft.subject.trim(),
      stylePresetId: style.id,
      primaryColor: motionDraft.style.primaryColor || style.preview.accent,
      secondaryColor: motionDraft.style.secondaryColor || style.preview.paper,
      secondsPerBeat: motionDraft.secondsPerBeat,
      beats: motionDraft.beats.map((beat) => ({
        title: beat.title.trim(),
        caption: beat.caption.trim(),
      })),
      logo:
        motionDraft.style.logo === null
          ? null
          : {
              fileName: motionDraft.style.logo.fileName,
              mediaType: motionDraft.style.logo.mediaType,
              bytes: motionDraft.style.logo.bytes,
            },
    };
    setBusy(true);
    startMotionRun({
      kind: "manual_template",
      subject: request.subject,
      startedAt: Date.now(),
    });
    setMotionActiveTab("jobs");
    void gateway
      .submitMotionDraft(request)
      .then((snapshot) => {
        settleMotionRun(
          snapshot.renderJobId,
          motionDraft.beats.length * motionDraft.secondsPerBeat,
          "manual_template",
          { tone: "info", text: "已提交真实本机渲染任务，已经转到「制作任务」。" },
        );
        refresh();
      })
      .catch((error: unknown) => {
        const code =
          error instanceof MaterialVideoStudioGatewayError
            ? error.code
            : "operation_unavailable";
        failMotionRun({
          tone: "error",
          text:
            code === "draft_invalid"
              ? "草稿内容或品牌素材不符合本机冻结规则，请检查后重试。"
              : code === "render_unavailable"
                ? "本机渲染组件暂时不可用，请到设置与诊断检查组件。"
                : "品牌动效任务暂时无法提交，请稍后重试。",
        });
      })
      .finally(() => setBusy(false));
  };
  /*
   * Finishing used to be the quietest moment of the whole run: the cancel
   * button disappeared from a card the user was probably not looking at, and
   * the film appeared in a different tab with nothing said. After three and a
   * half minutes of waiting, the one thing the App owes the operator is to tell
   * him it is done and open the door to it.
   *
   * Only films this session started are announced. A finished job found on
   * startup is not news.
   */
  const finishedOwnJobs = motionJobs.filter(
    (job) => job.status === "succeeded" && ownMotionJobs.has(job.renderJobId),
  );
  return (
    <section className="video-studio" aria-label="视频制作工作区">
      {jobError ? <Alert type="warning" showIcon title="暂时无法读取制作任务，请稍后重试。" /> : null}
      {message === null ? null : (
        <Alert
          type={message.tone}
          showIcon
          closable
          onClose={dismissMotionRunMessage}
          title={message.text}
        />
      )}
      {finishedOwnJobs.map((job) => (
        <Alert
          key={job.renderJobId}
          type="success"
          showIcon
          title={`「${job.subject}」已经做好了`}
          action={
            <Button
              size="small"
              onClick={() => {
                // Acting on the result is what marks it seen: the film stops
                // being timed, the notice goes, and the sidebar dot with it.
                // Without this last part the dot stays lit until the next
                // submission and stops meaning anything.
                forgetMotionJob(job.renderJobId);
                dismissMotionRunMessage();
                setMotionActiveTab("artifacts");
              }}
            >
              去看成片
            </Button>
          }
        />
      ))}
      <Tabs
        activeKey={activeTab}
        onChange={setMotionActiveTab}
        items={[
          {
            key: "new",
            label: "新建视频",
            children: (
              <NewVideoPage
                gateway={gateway}
                onOpened={refresh}
                selectedMethod={selectedMethod}
                onSelectMethod={setMotionMethod}
                motionSubject={motionDraft.subject}
                onMotionSubjectChange={(subject) =>
                  setMotionDraft((current) => ({ ...current, subject }))
                }
                brief={brief}
                onBriefChange={setMotionBrief}
                briefFilmSeconds={briefFilmSeconds}
                onBriefFilmSecondsChange={setMotionFilmSeconds}
                briefBusy={busy || pending !== null}
                onSubmitBrief={submitBrief}
                briefProblem={briefProblem}
                embedded={embedded}
              />
            ),
          },
          {
            key: "script",
            label: "脚本与分镜",
            children:
              selectedMethod === "motion_composition_v1" ? (
                <MotionScriptPage
                  beats={motionDraft.beats}
                  secondsPerBeat={motionDraft.secondsPerBeat}
                  onChange={(beats) =>
                    setMotionDraft((current) => ({ ...current, beats }))
                  }
                  onBeatCountChange={changeMotionBeatCount}
                  onSecondsPerBeatChange={(secondsPerBeat) =>
                    setMotionDraft((current) => ({ ...current, secondsPerBeat }))
                  }
                />
              ) : (
                <EmptyVideoPage page="script" />
              ),
          },
          {
            key: "settings",
            label: "制作设置",
            children: (
              <SettingsPage
                method={selectedMethod}
                onMotionStyleChange={onMotionStyleChange}
              />
            ),
          },
          {
            key: "parts",
            label: "动效零件",
            children:
              selectedMethod === "motion_composition_v1" ? (
                <MotionPartsCatalog
                  beats={motionDraft.beats}
                  usage={motionPartsUsage(MOTION_CREATION_MODE)}
                  selections={motionPartSelections}
                  onSelectionsChange={setMotionPartSelections}
                />
              ) : (
                <EmptyVideoPage page="parts" />
              ),
          },
          {
            key: "preview",
            label: "预览",
            children:
              selectedMethod === "motion_composition_v1" ? (
                <MotionPreviewPage
                  draft={motionDraft}
                  submitting={busy || pending !== null}
                  onSubmit={submitMotion}
                />
              ) : (
                <EmptyVideoPage page="preview" />
              ),
          },
          {
            key: "jobs",
            label: "制作任务",
            children: (
              <JobPage
                jobs={jobs}
                motionJobs={motionJobs}
                ownMotionJobs={ownMotionJobs}
                pending={pending}
                now={now}
                busy={busy}
                onCancel={(id) => act(gateway.cancel(id))}
                onCancelMotion={(id) => act(gateway.cancelMotionRenderJob(id))}
              />
            ),
          },
          {
            key: "artifacts",
            label: "成片",
            children: (
              <ArtifactPage
                jobs={jobs}
                motionJobs={motionJobs}
                busy={busy}
                onDelete={(id) => act(gateway.deleteArtifact(id))}
                onDeleteMotion={(id) => act(gateway.deleteMotionArtifact(id))}
                onPublish={onPublishArtifact}
                onReadMotion={(id) => playableSource(gateway.readMotionArtifact(id))}
                onReadMaterial={(id) => playableSource(gateway.readMaterialArtifact(id))}
              />
            ),
          },
        ]}
      />
    </section>
  );
}
