import { useCallback, useEffect, useState } from "react";

import {
  Alert,
  Button,
  Card,
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

import {
  MaterialVideoStudioGatewayError,
  type MaterialRenderJobSnapshot,
  type MaterialVideoStudioErrorCode,
  type MaterialVideoStudioGateway,
  type MotionRenderJobSnapshot,
  type MotionVideoBeatDraft,
  type MotionVideoDraftRequest,
} from "./material-video-studio-gateway";
import {
  MOTION_DURATION_LIMITS,
  motionDurationProblem,
  motionStoryboardSummary,
  resizeMotionBeats,
} from "./motion-duration";
import { motionPartsUsage } from "./motion-parts-catalog";
import { MotionPartsCatalog } from "./MotionPartsCatalog";
import { MotionStyleCatalog, type MotionStyleDraftSelection } from "./MotionStyleCatalog";
import { MOTION_STYLE_CATALOG } from "./motion-style-catalog";

type VideoCreationMethodId = "material_montage_v1" | "motion_composition_v1";

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

const OPEN_ERRORS: Record<MaterialVideoStudioErrorCode, string> = {
  configuration_required: "请先到“设置与诊断”配置并测试文案模型服务。",
  process_unavailable: "本机视频制作服务暂时无法启动，请稍后重试。",
  storage_unavailable: "无法创建本机视频工作区，请检查磁盘空间和目录权限。",
  view_unavailable: "完整制作界面暂时无法打开，请稍后重试。",
  job_unavailable: "制作任务状态暂时不可用，请稍后重试。",
  draft_invalid: "草稿内容不符合要求，请检查后重试。",
  render_unavailable: "本机渲染组件暂时不可用，请到设置与诊断检查组件。",
  protocol_mismatch: "视频制作服务版本不匹配，请更新 App 后重试。",
  operation_unavailable: "视频制作暂时不可用，请稍后重试。",
};

function NewVideoPage({
  gateway,
  onOpened,
  selectedMethod,
  onSelectMethod,
  motionSubject,
  onMotionSubjectChange,
}: {
  readonly gateway: MaterialVideoStudioGateway;
  readonly onOpened: () => void;
  readonly selectedMethod: VideoCreationMethodId | null;
  readonly onSelectMethod: (method: VideoCreationMethodId) => void;
  readonly motionSubject: string;
  readonly onMotionSubjectChange: (subject: string) => void;
}) {
  const [opening, setOpening] = useState(false);
  const [openMessage, setOpenMessage] = useState<{ type: "success" | "error"; text: string } | null>(
    null,
  );
  const selectedName = VIDEO_CREATION_METHODS.find(
    (method) => method.id === selectedMethod,
  )?.name;

  return (
    <Card className="video-studio-panel" title="从一句话开始">
      <Space orientation="vertical" size="middle" className="video-studio-new-form">
        <Typography.Text type="secondary">
          后续可以在这里描述主题、受众和想表达的重点，再选择合适的制作方式。
        </Typography.Text>
        <Input.TextArea
          aria-label="视频需求"
          disabled={selectedMethod !== "motion_composition_v1"}
          rows={5}
          maxLength={80}
          value={selectedMethod === "motion_composition_v1" ? motionSubject : ""}
          onChange={(event) => onMotionSubjectChange(event.target.value)}
          placeholder="例如：用品牌动效介绍新品的三个亮点"
        />
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
                <dl className="video-method-details">
                  {method.details.map((detail) => (
                    <div key={detail.label}>
                      <dt>{detail.label}</dt>
                      <dd>{detail.value}</dd>
                    </div>
                  ))}
                </dl>
                <Button
                  type={selected ? "primary" : "default"}
                  aria-label={`选择${method.name}`}
                  aria-pressed={selected}
                  className="video-method-select"
                  onClick={() => onSelectMethod(method.id)}
                >
                  {selected ? `已选择${method.name}` : `选择${method.name}`}
                </Button>
              </article>
            );
          })}
        </div>
        {openMessage === null ? (
          <Alert
            type="info"
            showIcon
            title="“智能素材成片”在独立完整界面制作；“品牌动效成片”在当前 App 内编辑和预览。"
          />
        ) : (
          <Alert type={openMessage.type} showIcon title={openMessage.text} />
        )}
        <div>
          <Button
            type="primary"
            loading={opening}
            disabled={selectedMethod !== "material_montage_v1" || opening}
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
        </div>
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
    }, 500);
    return () => window.clearTimeout(timer);
  }, [activeBeat, lastBeat, playing]);
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
  succeeded: { label: "已完成", color: "success" },
  failed: { label: "制作失败", color: "error" },
  cancelled: { label: "已取消", color: "default" },
} as const;

function JobPage({
  jobs,
  motionJobs,
  busy,
  onCancel,
  onCancelMotion,
}: {
  readonly jobs: readonly MaterialRenderJobSnapshot[];
  readonly motionJobs: readonly MotionRenderJobSnapshot[];
  readonly busy: boolean;
  readonly onCancel: (id: string) => void;
  readonly onCancelMotion: (id: string) => void;
}) {
  if (jobs.length === 0 && motionJobs.length === 0) return <EmptyVideoPage page="jobs" />;
  return (
    <Card className="video-studio-panel" title="本机制作任务">
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
            <Alert type="error" showIcon title="本机渲染未完成，请检查视频组件与磁盘空间后重试。" />
          ) : null}
          {["queued", "rendering", "encoding"].includes(job.status) ? (
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

function ArtifactPage({
  jobs,
  motionJobs,
  busy,
  onDelete,
  onDeleteMotion,
  onReadMotion,
}: {
  readonly jobs: readonly MaterialRenderJobSnapshot[];
  readonly motionJobs: readonly MotionRenderJobSnapshot[];
  readonly busy: boolean;
  readonly onDelete: (id: string) => void;
  readonly onDeleteMotion: (id: string) => void;
  readonly onReadMotion: (id: string) => Promise<string>;
}) {
  const artifacts = jobs.filter((job) => job.artifactId !== null);
  const motionArtifacts = motionJobs.filter((job) => job.artifactId !== null);
  const [playing, setPlaying] = useState<{ subject: string; source: string } | null>(null);
  const [playError, setPlayError] = useState(false);
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
              onClick={() => {
                setPlayError(false);
                void onReadMotion(job.artifactId!)
                  .then((source) => setPlaying({ subject: job.subject, source }))
                  .catch(() => setPlayError(true));
              }}
            >
              播放成片
            </Button>
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
          <Popconfirm
            title="删除后无法恢复，确定删除吗？"
            okText="确定"
            cancelText="返回"
            onConfirm={() => onDelete(job.artifactId!)}
          >
            <Button danger disabled={busy}>删除成片</Button>
          </Popconfirm>
        </Space>
      ))}
    </Card>
  );
}

export function VideoStudio({ gateway }: { readonly gateway: MaterialVideoStudioGateway }) {
  const [jobs, setJobs] = useState<readonly MaterialRenderJobSnapshot[]>([]);
  const [motionJobs, setMotionJobs] = useState<readonly MotionRenderJobSnapshot[]>([]);
  const [busy, setBusy] = useState(false);
  const [motionPartSelections, setMotionPartSelections] = useState<
    readonly (readonly string[])[]
  >(() => DEFAULT_MOTION_BEATS.map(() => []));
  const [jobError, setJobError] = useState(false);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [selectedMethod, setSelectedMethod] = useState<VideoCreationMethodId | null>(null);
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
  const act = (operation: Promise<void>) => {
    setBusy(true);
    void operation.then(refresh).catch(() => setJobError(true)).finally(() => setBusy(false));
  };
  const onMotionStyleChange = useCallback((style: MotionStyleDraftSelection) => {
    setMotionDraft((current) => ({ ...current, style }));
  }, []);
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
    setSubmitMessage(null);
    void gateway
      .submitMotionDraft(request)
      .then(() => {
        setSubmitMessage("已提交真实本机渲染任务，可到“制作任务”查看进度。");
        refresh();
      })
      .catch((error: unknown) => {
        const code =
          error instanceof MaterialVideoStudioGatewayError
            ? error.code
            : "operation_unavailable";
        setSubmitMessage(
          code === "draft_invalid"
            ? "草稿内容或品牌素材不符合本机冻结规则，请检查后重试。"
            : code === "render_unavailable"
              ? "本机渲染组件暂时不可用，请到设置与诊断检查组件。"
              : "品牌动效任务暂时无法提交，请稍后重试。",
        );
      })
      .finally(() => setBusy(false));
  };
  return (
    <section className="video-studio" aria-label="视频制作工作区">
      {jobError ? <Alert type="warning" showIcon title="暂时无法读取制作任务，请稍后重试。" /> : null}
      {submitMessage === null ? null : <Alert type="info" showIcon title={submitMessage} />}
      <Tabs
        defaultActiveKey="new"
        items={[
          {
            key: "new",
            label: "新建视频",
            children: (
              <NewVideoPage
                gateway={gateway}
                onOpened={refresh}
                selectedMethod={selectedMethod}
                onSelectMethod={setSelectedMethod}
                motionSubject={motionDraft.subject}
                onMotionSubjectChange={(subject) =>
                  setMotionDraft((current) => ({ ...current, subject }))
                }
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
                  submitting={busy}
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
                onReadMotion={(id) =>
                  gateway
                    .readMotionArtifact(id)
                    .then((artifact) => `data:${artifact.mediaType};base64,${artifact.base64}`)
                }
              />
            ),
          },
        ]}
      />
    </section>
  );
}
