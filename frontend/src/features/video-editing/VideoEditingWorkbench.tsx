import { useCallback, useEffect, useRef, useState } from "react";

import { Alert, Button, Card, Empty, Input, Space, Tabs, Tag, Typography } from "antd";

import type {
  EditingJobSnapshot,
  EditingJobStatus,
  EditingTimelineDraft,
  EditingProjectSnapshot,
  EditingTimelineSnapshot,
  OriginalAudioMode,
  TimelineTrackKind,
} from "./video-editing-dto";
import {
  VideoEditingGatewayError,
  type VideoEditingGateway,
} from "./video-editing-gateway";
import type { MaterialLibraryGateway } from "./material-library-gateway";
import { MaterialLibraryPage } from "./MaterialLibraryPage";

const TRACK_KIND_LABELS: Record<TimelineTrackKind, string> = {
  visual: "画面轨道",
  narration: "旁白轨道",
  ambient: "原声轨道",
  music: "音乐轨道",
  caption: "字幕轨道",
};

const TRACK_KIND_SHORT_LABELS: Record<TimelineTrackKind, string> = {
  visual: "画面",
  narration: "旁白",
  ambient: "原声",
  music: "音乐",
  caption: "字幕",
};

const TRANSITION_OPTIONS = [
  { value: "none", label: "硬切" },
  { value: "fade", label: "淡入淡出" },
  { value: "dissolve", label: "叠化" },
  { value: "wipe", label: "划像" },
] as const;

type TransitionChoice = (typeof TRANSITION_OPTIONS)[number]["value"];

const TRANSITION_LABELS: Record<TransitionChoice, string> = {
  none: "硬切",
  fade: "淡入淡出",
  dissolve: "叠化",
  wipe: "划像",
};

const JOB_STATUS_LABELS: Record<EditingJobStatus, string> = {
  queued: "排队中",
  running: "剪辑中",
  cancelling: "正在取消",
  succeeded: "已完成",
  failed: "剪辑失败",
  cancelled: "已取消",
};

const SERVICE_UNAVAILABLE_TEXT =
  "本机剪辑服务暂时不可用，请确认本机服务正在运行后再试。";
const INVALID_TIMELINE_TEXT =
  "时间轴还不完整：请确认每个画面或音频片段已填写素材引用、字幕片段已填写文字，并且时长为有效的毫秒数。";
const OUTCOME_UNCERTAIN_TEXT =
  "提交结果暂时无法确认。请刷新任务列表确认最终结果，在确认前不要再次提交。";

interface ClipForm {
  readonly formId: string;
  readonly clipId: string;
  readonly durationText: string;
  readonly materialText: string;
  readonly sourceInText: string;
  readonly captionText: string;
  readonly gainText: string;
  readonly originalAudioMode: OriginalAudioMode;
  readonly transition: TransitionChoice;
  readonly transitionDurationText: string;
}

interface TrackForm {
  readonly formId: string;
  readonly trackId: string;
  readonly kind: TimelineTrackKind;
  readonly clips: readonly ClipForm[];
}

function newLocalId(prefix: "clip" | "track"): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

function newClipForm(materialText: string): ClipForm {
  return {
    formId: crypto.randomUUID(),
    clipId: newLocalId("clip"),
    durationText: "3000",
    materialText,
    sourceInText: "0",
    captionText: "",
    gainText: "-6.5",
    originalAudioMode: "auto_duck",
    transition: "none",
    transitionDurationText: "500",
  };
}

function newTrackForm(kind: TimelineTrackKind, materialText: string): TrackForm {
  return {
    formId: crypto.randomUUID(),
    trackId: newLocalId("track"),
    kind,
    clips: [newClipForm(kind === "caption" ? "" : materialText)],
  };
}

function parseMilliseconds(text: string): number {
  const value = text.trim();
  return /^\d+$/.test(value) ? Number(value) : Number.NaN;
}

function parseLevel(text: string): number {
  const value = text.trim();
  return /^-?(?:\d+\.\d+|\d+)$/.test(value) ? Number(value) : Number.NaN;
}

function buildDraft(tracks: readonly TrackForm[]): EditingTimelineDraft {
  let durationMs = 0;
  const draftTracks = tracks.map((track) => {
    let cursor = 0;
    const clips = track.clips.map((clip) => {
      const clipDuration = parseMilliseconds(clip.durationText);
      const transition =
        track.kind === "visual" && clip.transition !== "none"
          ? {
              kind: clip.transition,
              durationMs: parseMilliseconds(clip.transitionDurationText),
            }
          : null;
      const overlap = transition?.durationMs ?? 0;
      const startMs = track.kind === "visual" ? cursor - overlap : cursor;
      cursor = startMs + (Number.isNaN(clipDuration) ? 0 : clipDuration);
      const material = clip.materialText.trim();
      const caption = clip.captionText.trim();
      const isCaption = track.kind === "caption";
      const sourceInMs =
        isCaption || clip.sourceInText.trim() === ""
          ? null
          : parseMilliseconds(clip.sourceInText);
      return {
        clipId: clip.clipId,
        startMs,
        durationMs: clipDuration,
        sourceMaterialId: isCaption || material === "" ? null : material,
        sourceInMs,
        sourceOutMs:
          sourceInMs === null || Number.isNaN(sourceInMs) || Number.isNaN(clipDuration)
            ? null
            : sourceInMs + clipDuration,
        text: !isCaption || caption === "" ? null : caption,
        gainDb:
          track.kind === "narration" || track.kind === "ambient" || track.kind === "music"
            ? parseLevel(clip.gainText)
            : null,
        transitionIn: transition,
        originalAudioMode: track.kind === "ambient" ? clip.originalAudioMode : null,
      };
    });
    durationMs = Math.max(durationMs, cursor);
    return { trackId: track.trackId, kind: track.kind, clips };
  });
  return { durationMs: Math.max(durationMs, 100), tracks: draftTracks };
}

function hydrateForm(timeline: EditingTimelineSnapshot): TrackForm[] {
  return timeline.tracks.map((track) => ({
    formId: crypto.randomUUID(),
    trackId: track.trackId,
    kind: track.kind,
    clips: track.clips.map((clip) => ({
      formId: crypto.randomUUID(),
      clipId: clip.clipId,
      durationText: String(clip.durationMs),
      materialText: clip.sourceMaterialId ?? "",
      sourceInText: clip.sourceInMs === null ? "" : String(clip.sourceInMs),
      captionText: clip.text ?? "",
      gainText: String(clip.gainDb ?? -6.5),
      originalAudioMode: clip.originalAudioMode ?? "auto_duck",
      transition: clip.transitionIn?.kind ?? "none",
      transitionDurationText: String(clip.transitionIn?.durationMs ?? 500),
    })),
  }));
}

function formatSeconds(milliseconds: number): string {
  return `${(milliseconds / 1000).toFixed(1)} 秒`;
}

interface Message {
  readonly type: "success" | "error" | "warning";
  readonly text: string;
}

function MessageAlert({ message }: { readonly message: Message | null }) {
  if (message === null) {
    return null;
  }
  return <Alert type={message.type} showIcon title={message.text} />;
}

function ProjectsPage({
  projects,
  loaded,
  loading,
  creating,
  onCreate,
  onOpen,
  onRefresh,
  message,
}: {
  readonly projects: readonly EditingProjectSnapshot[];
  readonly loaded: boolean;
  readonly loading: boolean;
  readonly creating: boolean;
  readonly onCreate: (title: string) => void;
  readonly onOpen: (projectId: string) => void;
  readonly onRefresh: () => void;
  readonly message: Message | null;
}) {
  const [title, setTitle] = useState("");
  return (
    <Space orientation="vertical" size="middle" className="video-editing-projects">
      {/*
       * No card head.
       *
       * It said 「新建剪辑项目」 directly under the 「剪辑项目」 tab, and directly
       * above 「输入项目标题和要剪辑的素材引用…」 and the 「创建剪辑项目」 button —
       * four consecutive lines, 剪辑项目 in every one of them. The head is the
       * one carrying nothing the other three do not: the tab says which step
       * this is, the sentence says what to do, the button says what happens.
       *
       * It cost 56px, and this page had 680px of `main` at the production
       * 1280x800 window while needing 687px, so those 56px were also the
       * difference between one screen and a scrollbar. The same head was
       * removed from 新建视频 on 视频制作 for the same reason; that card is the
       * shape this one now matches — no head, leading with the secondary line
       * that explains it. `e2e/video-editing-tabs.spec.ts` holds the fold.
       */}
      <Card className="video-editing-panel">
        <Space orientation="vertical" size="middle" className="video-editing-create-form">
          <Typography.Text type="secondary">
            输入项目标题。创建后可在时间轴中填写已导入的素材编号。
          </Typography.Text>
          <Input
            aria-label="剪辑项目标题"
            placeholder="例如：新品发布会精剪"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <div>
            <Space size="small">
              <Button
                type="primary"
                aria-label="创建剪辑项目"
                loading={creating}
                onClick={() => onCreate(title)}
              >
                创建剪辑项目
              </Button>
              <Button aria-label="刷新项目" loading={loading} onClick={onRefresh}>
                刷新项目
              </Button>
            </Space>
          </div>
          <MessageAlert message={message} />
        </Space>
      </Card>
      {!loaded ? (
        loading ? (
          <Card className="video-editing-panel">
            <Typography.Text type="secondary">正在读取本机剪辑项目…</Typography.Text>
          </Card>
        ) : null
      ) : projects.length === 0 ? (
        <Card className="video-editing-panel">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Typography.Text strong>还没有剪辑项目</Typography.Text>
                <Typography.Text type="secondary">
                  创建剪辑项目后，可以在时间轴上整理轨道、片段、字幕和转场。
                </Typography.Text>
              </Space>
            }
          />
        </Card>
      ) : (
        <Card className="video-editing-panel" title="剪辑项目列表">
          {projects.map((project) => (
            <Space key={project.projectId} className="video-editing-project-row">
              <Typography.Text strong>{project.title}</Typography.Text>
              <Typography.Text type="secondary">
                {project.output.width}×{project.output.height} · {project.output.fps} 帧/秒
              </Typography.Text>
              <Button onClick={() => onOpen(project.projectId)}>打开时间轴编辑</Button>
            </Space>
          ))}
        </Card>
      )}
    </Space>
  );
}

function ClipEditor({
  trackKind,
  trackIndex,
  clipIndex,
  clip,
  clipCount,
  onChange,
  onMoveUp,
  onMoveDown,
  onDelete,
}: {
  readonly trackKind: TimelineTrackKind;
  readonly trackIndex: number;
  readonly clipIndex: number;
  readonly clip: ClipForm;
  readonly clipCount: number;
  readonly onChange: (next: ClipForm) => void;
  readonly onMoveUp: () => void;
  readonly onMoveDown: () => void;
  readonly onDelete: () => void;
}) {
  const position = `轨道${trackIndex + 1}片段${clipIndex + 1}`;
  return (
    <div className="video-editing-clip">
      <Space wrap size="small" align="center">
        <Typography.Text strong>片段 {clipIndex + 1}</Typography.Text>
        <Input
          aria-label={`${position}时长毫秒`}
          className="video-editing-duration-input"
          value={clip.durationText}
          onChange={(event) => onChange({ ...clip, durationText: event.target.value })}
          suffix="毫秒"
        />
        {trackKind === "caption" ? (
          <Input
            aria-label={`${position}字幕文字`}
            placeholder="这一段显示的字幕"
            value={clip.captionText}
            onChange={(event) => onChange({ ...clip, captionText: event.target.value })}
          />
        ) : (
          <>
            <Input
              aria-label={`${position}素材编号`}
              placeholder="素材编号"
              value={clip.materialText}
              onChange={(event) => onChange({ ...clip, materialText: event.target.value })}
            />
            <Input
              aria-label={`${position}素材起点毫秒`}
              className="video-editing-duration-input"
              placeholder="静态图片留空"
              value={clip.sourceInText}
              onChange={(event) => onChange({ ...clip, sourceInText: event.target.value })}
              suffix="毫秒"
            />
          </>
        )}
        {trackKind === "narration" || trackKind === "ambient" || trackKind === "music" ? (
          <Input
            aria-label={`${position}音量分贝`}
            className="video-editing-duration-input"
            value={clip.gainText}
            onChange={(event) => onChange({ ...clip, gainText: event.target.value })}
            suffix="分贝"
          />
        ) : null}
        {trackKind === "ambient" ? (
          <select
            aria-label={`${position}原声处理`}
            className="video-editing-transition-select"
            value={clip.originalAudioMode}
            onChange={(event) =>
              onChange({
                ...clip,
                originalAudioMode: event.target.value as OriginalAudioMode,
              })
            }
          >
            <option value="auto_duck">自动闪避</option>
            <option value="fixed_volume">固定音量</option>
            <option value="muted">静音</option>
          </select>
        ) : null}
        {trackKind === "visual" ? (
          <>
            <select
              aria-label={`${position}转场`}
              className="video-editing-transition-select"
              value={clip.transition}
              onChange={(event) =>
                onChange({ ...clip, transition: event.target.value as TransitionChoice })
              }
            >
              {TRANSITION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {clip.transition === "none" ? null : (
              <Input
                aria-label={`${position}转场时长毫秒`}
                className="video-editing-duration-input"
                value={clip.transitionDurationText}
                onChange={(event) =>
                  onChange({ ...clip, transitionDurationText: event.target.value })
                }
                suffix="毫秒"
              />
            )}
          </>
        ) : null}
        <Button
          size="small"
          aria-label={`上移${position}`}
          disabled={clipIndex === 0}
          onClick={onMoveUp}
        >
          上移
        </Button>
        <Button
          size="small"
          aria-label={`下移${position}`}
          disabled={clipIndex === clipCount - 1}
          onClick={onMoveDown}
        >
          下移
        </Button>
        <Button
          size="small"
          danger
          aria-label={`删除${position}`}
          disabled={clipCount === 1}
          onClick={onDelete}
        >
          删除片段
        </Button>
      </Space>
    </div>
  );
}

function TimelinePage({
  project,
  tracks,
  savedTimeline,
  loading,
  saving,
  saveNeedsConfirmation,
  message,
  onTracksChange,
  onSave,
  onRefresh,
}: {
  readonly project: EditingProjectSnapshot | null;
  readonly tracks: readonly TrackForm[];
  readonly savedTimeline: EditingTimelineSnapshot | null;
  readonly loading: boolean;
  readonly saving: boolean;
  readonly saveNeedsConfirmation: boolean;
  readonly message: Message | null;
  readonly onTracksChange: (tracks: readonly TrackForm[]) => void;
  readonly onSave: () => void;
  readonly onRefresh: () => void;
}) {
  if (project === null) {
    return (
      <Card className="video-editing-panel">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Space orientation="vertical" size={4}>
              <Typography.Text strong>请先创建或选择一个剪辑项目</Typography.Text>
              <Typography.Text type="secondary">
                在“剪辑项目”页创建项目后，这里会出现可编辑的时间轴。
              </Typography.Text>
            </Space>
          }
        />
      </Card>
    );
  }
  const defaultMaterial = "";
  const replaceTrack = (index: number, next: TrackForm | null) => {
    const updated = tracks.flatMap((track, candidate) =>
      candidate === index ? (next === null ? [] : [next]) : [track],
    );
    onTracksChange(updated);
  };
  return (
    <Card className="video-editing-panel" title={`正在编辑：${project.title}`}>
      <Space orientation="vertical" size="middle" className="video-editing-timeline">
        <Space wrap size="small">
          <Typography.Text type="secondary">
            片段按先后顺序排列，起止时间由各片段时长自动计算。
          </Typography.Text>
          <Tag>
            {loading
              ? "正在读取时间轴"
              : savedTimeline === null
              ? "尚未保存"
              : `当前修订：第 ${savedTimeline.revision} 版`}
          </Tag>
        </Space>
        {tracks.map((track, trackIndex) => (
          <div key={track.formId} className="video-editing-track">
            <Space wrap size="small" align="center">
              <Typography.Text strong>
                {TRACK_KIND_LABELS[track.kind]} {trackIndex + 1}
              </Typography.Text>
              <Button
                size="small"
                danger
                aria-label={`删除轨道${trackIndex + 1}`}
                disabled={tracks.length === 1}
                onClick={() => replaceTrack(trackIndex, null)}
              >
                删除轨道
              </Button>
              <Button
                size="small"
                aria-label={`在轨道${trackIndex + 1}添加片段`}
                onClick={() =>
                  replaceTrack(trackIndex, {
                    ...track,
                    clips: [
                      ...track.clips,
                      newClipForm(track.kind === "caption" ? "" : defaultMaterial),
                    ],
                  })
                }
              >
                添加片段
              </Button>
            </Space>
            {track.clips.map((clip, clipIndex) => (
              <ClipEditor
                key={clip.formId}
                trackKind={track.kind}
                trackIndex={trackIndex}
                clipIndex={clipIndex}
                clip={clip}
                clipCount={track.clips.length}
                onChange={(next) =>
                  replaceTrack(trackIndex, {
                    ...track,
                    clips: track.clips.map((candidate, index) =>
                      index === clipIndex ? next : candidate,
                    ),
                  })
                }
                onMoveUp={() => {
                  if (clipIndex === 0) return;
                  const clips = [...track.clips];
                  const [moved] = clips.splice(clipIndex, 1);
                  clips.splice(clipIndex - 1, 0, moved!);
                  replaceTrack(trackIndex, { ...track, clips });
                }}
                onMoveDown={() => {
                  if (clipIndex === track.clips.length - 1) return;
                  const clips = [...track.clips];
                  const [moved] = clips.splice(clipIndex, 1);
                  clips.splice(clipIndex + 1, 0, moved!);
                  replaceTrack(trackIndex, { ...track, clips });
                }}
                onDelete={() =>
                  replaceTrack(trackIndex, {
                    ...track,
                    clips: track.clips.filter((_, index) => index !== clipIndex),
                  })
                }
              />
            ))}
          </div>
        ))}
        <Space wrap size="small">
          <Button onClick={() => onTracksChange([...tracks, newTrackForm("visual", defaultMaterial)])}>
            添加画面轨道
          </Button>
          <Button onClick={() => onTracksChange([...tracks, newTrackForm("narration", defaultMaterial)])}>
            添加旁白轨道
          </Button>
          <Button onClick={() => onTracksChange([...tracks, newTrackForm("ambient", defaultMaterial)])}>
            添加原声轨道
          </Button>
          <Button onClick={() => onTracksChange([...tracks, newTrackForm("music", defaultMaterial)])}>
            添加音乐轨道
          </Button>
          <Button onClick={() => onTracksChange([...tracks, newTrackForm("caption", "")])}>
            添加字幕轨道
          </Button>
          <Button aria-label="刷新时间轴" onClick={onRefresh} loading={loading}>
            刷新时间轴
          </Button>
          <Button
            type="primary"
            aria-label="保存时间轴"
            onClick={onSave}
            loading={saving}
            disabled={loading || saving || saveNeedsConfirmation}
          >
            保存时间轴
          </Button>
        </Space>
        <MessageAlert message={message} />
      </Space>
    </Card>
  );
}

function PreviewPage({
  savedTimeline,
}: {
  readonly savedTimeline: EditingTimelineSnapshot | null;
}) {
  if (savedTimeline === null) {
    return (
      <Card className="video-editing-panel">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Space orientation="vertical" size={4}>
              <Typography.Text strong>还没有可预览内容</Typography.Text>
              <Typography.Text type="secondary">
                保存时间轴后，这里会显示轨道与片段的结构预览。
              </Typography.Text>
            </Space>
          }
        />
      </Card>
    );
  }
  return (
    <Card className="video-editing-panel" title="时间轴结构预览">
      <Space orientation="vertical" size="middle" className="video-editing-preview">
        <Typography.Text type="secondary">
          总时长 {formatSeconds(savedTimeline.durationMs)} · 第 {savedTimeline.revision} 版
        </Typography.Text>
        {savedTimeline.tracks.map((track, trackIndex) => (
          <div key={track.trackId}>
            <Typography.Text strong>
              轨道 {trackIndex + 1}（{TRACK_KIND_SHORT_LABELS[track.kind]}）·{" "}
              {track.clips.length} 个片段
            </Typography.Text>
            <ul className="video-editing-preview-clips">
              {track.clips.map((clip) => (
                <li key={clip.clipId}>
                  {formatSeconds(clip.startMs)} 至 {formatSeconds(clip.startMs + clip.durationMs)}
                  {clip.text === null ? "" : ` · 字幕“${clip.text}”`}
                  {clip.transitionIn === null
                    ? ""
                    : ` · 转场：${TRANSITION_LABELS[clip.transitionIn.kind]}`}
                </li>
              ))}
            </ul>
          </div>
        ))}
        <Alert
          type="info"
          showIcon
          title="这里展示时间轴结构；本机剪辑完成后，任务列表会标记成片已入库。"
        />
      </Space>
    </Card>
  );
}

function JobsPage({
  project,
  jobs,
  loading,
  submitting,
  submissionNeedsConfirmation,
  message,
  onSubmit,
  onRefresh,
}: {
  readonly project: EditingProjectSnapshot | null;
  readonly jobs: readonly EditingJobSnapshot[];
  readonly loading: boolean;
  readonly submitting: boolean;
  readonly submissionNeedsConfirmation: boolean;
  readonly message: Message | null;
  readonly onSubmit: () => void;
  readonly onRefresh: () => void;
}) {
  return (
    <Space orientation="vertical" size="middle" className="video-editing-jobs">
      <Card className="video-editing-panel" title="提交剪辑">
        <Space orientation="vertical" size="middle">
          <Typography.Text type="secondary">
            提交后会按已保存的时间轴修订执行本机剪辑，执行状态和结果显示在下方任务列表。
          </Typography.Text>
          <div>
            <Space size="small">
              <Button
                type="primary"
                aria-label="提交剪辑任务"
                disabled={project === null || submitting || submissionNeedsConfirmation}
                loading={submitting}
                onClick={onSubmit}
              >
                提交剪辑任务
              </Button>
              <Button
                aria-label="刷新任务"
                disabled={project === null}
                loading={loading}
                onClick={onRefresh}
              >
                刷新任务
              </Button>
            </Space>
          </div>
          <MessageAlert message={message} />
        </Space>
      </Card>
      {jobs.length === 0 ? (
        <Card className="video-editing-panel">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Typography.Text strong>还没有剪辑任务</Typography.Text>
                <Typography.Text type="secondary">
                  提交本机剪辑任务后，执行状态会显示在这里。
                </Typography.Text>
              </Space>
            }
          />
        </Card>
      ) : (
        <Card className="video-editing-panel" title="剪辑任务">
          {jobs.map((job) => (
            <Space
              key={job.jobId}
              orientation="vertical"
              size="small"
              className="video-editing-job"
            >
              <Space size="small">
                <Tag>{JOB_STATUS_LABELS[job.status]}</Tag>
                <Typography.Text type="secondary">
                  时间轴第 {job.timelineRevision} 版
                </Typography.Text>
              </Space>
              <Typography.Text type="secondary">
                {job.outputArtifactId === null ? "尚未产出成片" : "成片已入库"}
              </Typography.Text>
              {job.status === "failed" ? (
                <Alert type="error" showIcon title="本次剪辑未成功，可以调整时间轴后重新提交。" />
              ) : null}
            </Space>
          ))}
        </Card>
      )}
    </Space>
  );
}

export function VideoEditingWorkbench({
  gateway,
  materialLibraryGateway,
}: {
  readonly gateway: VideoEditingGateway;
  readonly materialLibraryGateway?: MaterialLibraryGateway | undefined;
}) {
  const [projects, setProjects] = useState<readonly EditingProjectSnapshot[]>([]);
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [tracks, setTracks] = useState<readonly TrackForm[]>([]);
  const [savedTimeline, setSavedTimeline] = useState<EditingTimelineSnapshot | null>(null);
  const [jobs, setJobs] = useState<readonly EditingJobSnapshot[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveNeedsConfirmation, setSaveNeedsConfirmation] = useState(false);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState("projects");
  const [projectMessage, setProjectMessage] = useState<Message | null>(null);
  const [saveMessage, setSaveMessage] = useState<Message | null>(null);
  const [submitMessage, setSubmitMessage] = useState<Message | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submissionNeedsConfirmation, setSubmissionNeedsConfirmation] = useState(false);

  const mountedRef = useRef(true);
  const selectedProjectIdRef = useRef<string | null>(null);
  const projectsRequestRef = useRef(0);
  const timelineRequestRef = useRef(0);
  const jobsRequestRef = useRef(0);
  const creatingRef = useRef(false);
  const savingRef = useRef(false);
  const submittingRef = useRef(false);
  const saveOperationRef = useRef(0);
  const submitOperationRef = useRef(0);
  const tracksVersionRef = useRef(0);
  const autoLoadedGatewayRef = useRef<VideoEditingGateway | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      projectsRequestRef.current += 1;
      timelineRequestRef.current += 1;
      jobsRequestRef.current += 1;
      saveOperationRef.current += 1;
      submitOperationRef.current += 1;
    };
  }, []);

  const loadProjects = useCallback(
    async (clearMessage: boolean, requiredProjectId?: string): Promise<void> => {
      const request = ++projectsRequestRef.current;
      setProjectsLoading(true);
      if (clearMessage) {
        setProjectMessage(null);
      }
      try {
        const value = await gateway.listProjects();
        if (!mountedRef.current || request !== projectsRequestRef.current) {
          return;
        }
        if (
          requiredProjectId !== undefined &&
          !value.some((project) => project.projectId === requiredProjectId)
        ) {
          return;
        }
        setProjects(value);
        setProjectsLoaded(true);
        const selectedId = selectedProjectIdRef.current;
        if (selectedId !== null && !value.some((project) => project.projectId === selectedId)) {
          selectedProjectIdRef.current = null;
          setSelectedProjectId(null);
          timelineRequestRef.current += 1;
          jobsRequestRef.current += 1;
          setSavedTimeline(null);
          tracksVersionRef.current += 1;
          setTracks([]);
          setJobs([]);
        }
      } catch {
        if (mountedRef.current && request === projectsRequestRef.current) {
          setProjectMessage({ type: "error", text: SERVICE_UNAVAILABLE_TEXT });
        }
      } finally {
        if (mountedRef.current && request === projectsRequestRef.current) {
          setProjectsLoading(false);
        }
      }
    },
    [gateway],
  );

  useEffect(() => {
    queueMicrotask(() => {
      if (mountedRef.current && autoLoadedGatewayRef.current !== gateway) {
        autoLoadedGatewayRef.current = gateway;
        void loadProjects(false);
      }
    });
  }, [gateway, loadProjects]);

  type LoadResult = "loaded" | "failed" | "stale";

  const loadTimeline = useCallback(
    async (
      projectId: string,
      reset: boolean,
      reportFailure: boolean,
      minimumRevision?: number,
      preserveEditsAfterVersion?: number,
    ): Promise<LoadResult> => {
      const request = ++timelineRequestRef.current;
      if (reset) {
        setSavedTimeline(null);
        tracksVersionRef.current += 1;
        setTracks([newTrackForm("visual", "")]);
      }
      setTimelineLoading(true);
      try {
        const timeline = await gateway.getTimeline(projectId);
        if (
          !mountedRef.current ||
          request !== timelineRequestRef.current ||
          selectedProjectIdRef.current !== projectId
        ) {
          return "stale";
        }
        if (
          minimumRevision !== undefined &&
          (timeline === null || timeline.revision < minimumRevision)
        ) {
          return "failed";
        }
        setSavedTimeline(timeline);
        if (
          preserveEditsAfterVersion === undefined ||
          tracksVersionRef.current === preserveEditsAfterVersion
        ) {
          tracksVersionRef.current += 1;
          setTracks(timeline === null ? [newTrackForm("visual", "")] : hydrateForm(timeline));
        }
        return "loaded";
      } catch {
        if (
          !mountedRef.current ||
          request !== timelineRequestRef.current ||
          selectedProjectIdRef.current !== projectId
        ) {
          return "stale";
        }
        if (reportFailure) {
          setSaveMessage({ type: "error", text: SERVICE_UNAVAILABLE_TEXT });
        }
        return "failed";
      } finally {
        if (
          mountedRef.current &&
          request === timelineRequestRef.current &&
          selectedProjectIdRef.current === projectId
        ) {
          setTimelineLoading(false);
        }
      }
    },
    [gateway],
  );

  const loadJobs = useCallback(
    async (
      projectId: string,
      reset: boolean,
      reportFailure: boolean,
      retainedJobs: readonly EditingJobSnapshot[] = [],
    ): Promise<LoadResult> => {
      const request = ++jobsRequestRef.current;
      if (reset) {
        setJobs([]);
      }
      setJobsLoading(true);
      try {
        const value = await gateway.listEditingJobs(projectId);
        if (
          !mountedRef.current ||
          request !== jobsRequestRef.current ||
          selectedProjectIdRef.current !== projectId
        ) {
          return "stale";
        }
        const refreshedIds = new Set(value.map((job) => job.jobId));
        setJobs([
          ...retainedJobs.filter((job) => !refreshedIds.has(job.jobId)),
          ...value,
        ]);
        return "loaded";
      } catch {
        if (
          !mountedRef.current ||
          request !== jobsRequestRef.current ||
          selectedProjectIdRef.current !== projectId
        ) {
          return "stale";
        }
        if (reportFailure) {
          setSubmitMessage({ type: "error", text: SERVICE_UNAVAILABLE_TEXT });
        }
        return "failed";
      } finally {
        if (
          mountedRef.current &&
          request === jobsRequestRef.current &&
          selectedProjectIdRef.current === projectId
        ) {
          setJobsLoading(false);
        }
      }
    },
    [gateway],
  );

  const openProject = useCallback(
    (project: EditingProjectSnapshot) => {
      const reset = selectedProjectIdRef.current !== project.projectId;
      if (reset) {
        saveOperationRef.current += 1;
        savingRef.current = false;
        setSaving(false);
        setSaveNeedsConfirmation(false);
        submitOperationRef.current += 1;
        submittingRef.current = false;
        setSubmitting(false);
        setSubmissionNeedsConfirmation(false);
      }
      selectedProjectIdRef.current = project.projectId;
      setSelectedProjectId(project.projectId);
      setSaveMessage(null);
      setSubmitMessage(null);
      void loadTimeline(project.projectId, reset, true);
      void loadJobs(project.projectId, reset, true);
    },
    [loadJobs, loadTimeline],
  );

  const selectedProject =
    projects.find((project) => project.projectId === selectedProjectId) ?? null;

  const createProject = (title: string) => {
    if (creatingRef.current) {
      return;
    }
    creatingRef.current = true;
    setCreating(true);
    setProjectMessage(null);
    void (async () => {
      try {
        const project = await gateway.createProject({
          title: title.trim(),
          output: { width: 720, height: 1280, fps: 20 },
          captionStyle: {
            fontKey: "noto-sans-cjk-sc-bold",
            fontPx: 48,
            strokePx: 3,
            lineSpacing: 1.2,
          },
        });
        if (!mountedRef.current) {
          return;
        }
        setProjectsLoaded(true);
        setProjects((current) => [
          ...current.filter((candidate) => candidate.projectId !== project.projectId),
          project,
        ]);
        setProjectMessage({ type: "success", text: `已创建剪辑项目：${project.title}` });
        openProject(project);
        void loadProjects(false, project.projectId);
      } catch (error: unknown) {
        if (!mountedRef.current) {
          return;
        }
        setProjectMessage({
          type: "error",
          text:
            error instanceof VideoEditingGatewayError && error.code === "invalid_project"
              ? "无法创建剪辑项目：请填写有效的项目标题。"
              : SERVICE_UNAVAILABLE_TEXT,
        });
      } finally {
        creatingRef.current = false;
        if (mountedRef.current) {
          setCreating(false);
        }
      }
    })();
  };

  const saveTimeline = () => {
    if (selectedProject === null || savingRef.current) {
      return;
    }
    const projectId = selectedProject.projectId;
    const operation = ++saveOperationRef.current;
    const tracksVersion = tracksVersionRef.current;
    savingRef.current = true;
    setSaving(true);
    setSaveMessage(null);
    void (async () => {
      try {
        const timeline = await gateway.saveTimeline(projectId, buildDraft(tracks));
        if (
          !mountedRef.current ||
          operation !== saveOperationRef.current ||
          selectedProjectIdRef.current !== projectId
        ) {
          return;
        }
        setSavedTimeline(timeline);
        setSaveNeedsConfirmation(false);
        const refreshed = await loadTimeline(
          projectId,
          false,
          false,
          timeline.revision,
          tracksVersion,
        );
        if (
          operation === saveOperationRef.current &&
          selectedProjectIdRef.current === projectId
        ) {
          setSaveMessage(
            refreshed === "failed"
              ? {
                  type: "warning",
                  text: `已保存第 ${timeline.revision} 版，但暂时无法刷新时间轴。`,
                }
              : {
                  type: "success",
                  text: `已保存修订：第 ${timeline.revision} 版`,
                },
          );
        }
      } catch (error: unknown) {
        if (
          !mountedRef.current ||
          operation !== saveOperationRef.current ||
          selectedProjectIdRef.current !== projectId
        ) {
          return;
        }
        if (error instanceof VideoEditingGatewayError && error.code === "outcome_uncertain") {
          setSaveMessage({
            type: "warning",
            text: "保存结果暂时无法确认，请刷新时间轴确认当前修订，在确认前不要再次保存。",
          });
          setSaveNeedsConfirmation(true);
        } else {
          setSaveMessage({
            type: "error",
            text:
              error instanceof VideoEditingGatewayError && error.code === "invalid_timeline"
                ? INVALID_TIMELINE_TEXT
                : SERVICE_UNAVAILABLE_TEXT,
          });
        }
      } finally {
        if (operation === saveOperationRef.current) {
          savingRef.current = false;
        }
        if (
          mountedRef.current &&
          operation === saveOperationRef.current &&
          selectedProjectIdRef.current === projectId
        ) {
          setSaving(false);
        }
      }
    })();
  };

  const submitJob = () => {
    if (selectedProject === null || submittingRef.current) {
      return;
    }
    const projectId = selectedProject.projectId;
    const operation = ++submitOperationRef.current;
    submittingRef.current = true;
    setSubmitting(true);
    setSubmitMessage(null);
    void (async () => {
      try {
        const job = await gateway.submitEditingJob(projectId);
        if (
          !mountedRef.current ||
          operation !== submitOperationRef.current ||
          selectedProjectIdRef.current !== projectId
        ) {
          return;
        }
        setJobs((current) => [job, ...current.filter((candidate) => candidate.jobId !== job.jobId)]);
        setSubmissionNeedsConfirmation(false);
        setSubmitMessage({ type: "success", text: "已提交剪辑任务，正在排队。" });
        const refreshed = await loadJobs(projectId, false, false, [job]);
        if (
          refreshed === "failed" &&
          operation === submitOperationRef.current &&
          selectedProjectIdRef.current === projectId
        ) {
          setSubmitMessage({
            type: "warning",
            text: "任务已提交，但暂时无法刷新任务列表。",
          });
        }
      } catch (error: unknown) {
        if (
          !mountedRef.current ||
          operation !== submitOperationRef.current ||
          selectedProjectIdRef.current !== projectId
        ) {
          return;
        }
        setSubmitMessage({
          type:
            error instanceof VideoEditingGatewayError && error.code === "outcome_uncertain"
              ? "warning"
              : "error",
          text:
            error instanceof VideoEditingGatewayError && error.code === "outcome_uncertain"
              ? OUTCOME_UNCERTAIN_TEXT
              : SERVICE_UNAVAILABLE_TEXT,
        });
        if (error instanceof VideoEditingGatewayError && error.code === "outcome_uncertain") {
          setSubmissionNeedsConfirmation(true);
        }
      } finally {
        if (operation === submitOperationRef.current) {
          submittingRef.current = false;
        }
        if (
          mountedRef.current &&
          operation === submitOperationRef.current &&
          selectedProjectIdRef.current === projectId
        ) {
          setSubmitting(false);
        }
      }
    })();
  };

  return (
    <section className="video-editing" aria-label="视频剪辑工作区">
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          ...(materialLibraryGateway === undefined
            ? []
            : [
                {
                  key: "materials",
                  label: "素材库",
                  children: <MaterialLibraryPage gateway={materialLibraryGateway} />,
                },
              ]),
          {
            key: "projects",
            label: "剪辑项目",
            children: (
              <ProjectsPage
                projects={projects}
                loaded={projectsLoaded}
                loading={projectsLoading}
                creating={creating}
                onCreate={createProject}
                onRefresh={() => void loadProjects(true)}
                onOpen={(projectId) => {
                  const project = projects.find(
                    (candidate) => candidate.projectId === projectId,
                  );
                  if (project !== undefined) {
                    openProject(project);
                    setActiveTab("timeline");
                  }
                }}
                message={projectMessage}
              />
            ),
          },
          {
            key: "timeline",
            label: "时间轴编辑",
            children: (
              <TimelinePage
                project={selectedProject}
                tracks={tracks}
                savedTimeline={savedTimeline}
                loading={timelineLoading}
                saving={saving}
                saveNeedsConfirmation={saveNeedsConfirmation}
                message={saveMessage}
                onTracksChange={(nextTracks) => {
                  tracksVersionRef.current += 1;
                  setTracks(nextTracks);
                }}
                onSave={saveTimeline}
                onRefresh={() => {
                  if (selectedProject !== null) {
                    const projectId = selectedProject.projectId;
                    setSaveMessage(null);
                    void (async () => {
                      const result = await loadTimeline(projectId, false, true);
                      if (
                        result === "loaded" &&
                        mountedRef.current &&
                        selectedProjectIdRef.current === projectId
                      ) {
                        setSaveNeedsConfirmation(false);
                      }
                    })();
                  }
                }}
              />
            ),
          },
          {
            key: "preview",
            label: "预览",
            children: <PreviewPage savedTimeline={savedTimeline} />,
          },
          {
            key: "jobs",
            label: "提交与任务",
            children: (
              <JobsPage
                project={selectedProject}
                jobs={jobs}
                loading={jobsLoading}
                submitting={submitting}
                submissionNeedsConfirmation={submissionNeedsConfirmation}
                message={submitMessage}
                onSubmit={submitJob}
                onRefresh={() => {
                  if (selectedProject !== null) {
                    const projectId = selectedProject.projectId;
                    setSubmitMessage(null);
                    void (async () => {
                      const result = await loadJobs(projectId, false, true);
                      if (
                        result === "loaded" &&
                        mountedRef.current &&
                        selectedProjectIdRef.current === projectId
                      ) {
                        setSubmissionNeedsConfirmation(false);
                      }
                    })();
                  }
                }}
              />
            ),
          },
        ]}
      />
    </section>
  );
}
