import { useCallback, useEffect, useState } from "react";

import { Alert, Button, Card, Empty, Input, Space, Tabs, Tag, Typography } from "antd";

import type {
  EditingJobSnapshot,
  EditingJobStatus,
  EditingTimelineDraft,
  EditingProjectSnapshot,
  EditingTimelineSnapshot,
  TimelineTrackKind,
} from "./video-editing-dto";
import {
  VideoEditingGatewayError,
  type VideoEditingGateway,
} from "./video-editing-gateway";

const TRACK_KIND_LABELS: Record<TimelineTrackKind, string> = {
  visual: "画面轨道",
  audio: "音频轨道",
  caption: "字幕轨道",
};

const TRACK_KIND_SHORT_LABELS: Record<TimelineTrackKind, string> = {
  visual: "画面",
  audio: "音频",
  caption: "字幕",
};

const TRANSITION_OPTIONS = [
  { value: "none", label: "无转场" },
  { value: "cut", label: "硬切" },
  { value: "fade", label: "淡入淡出" },
  { value: "dissolve", label: "叠化" },
  { value: "wipe", label: "划像" },
] as const;

type TransitionChoice = (typeof TRANSITION_OPTIONS)[number]["value"];

const TRANSITION_LABELS: Record<TransitionChoice, string> = {
  none: "无转场",
  cut: "硬切",
  fade: "淡入淡出",
  dissolve: "叠化",
  wipe: "划像",
};

const JOB_STATUS_LABELS: Record<EditingJobStatus, string> = {
  queued: "排队中",
  running: "剪辑中",
  paused: "已暂停",
  cancelling: "正在取消",
  succeeded: "已完成",
  failed: "剪辑失败",
  cancelled: "已取消",
  outcome_uncertain: "结果待确认",
};

const STORAGE_UNAVAILABLE_TEXT = "本机剪辑草稿暂时无法读取，请稍后重试。";
const INVALID_TIMELINE_TEXT =
  "时间轴还不完整：请确认每个画面或音频片段已填写素材引用、字幕片段已填写文字，并且时长为有效的毫秒数。";
const SERVICE_UNAVAILABLE_TEXT =
  "当前无法提交云端剪辑：请先检查剪辑服务配置；“结果待确认”任务会在 App 重启后自动续查，但系统不会自动重发。";

interface ClipForm {
  readonly formId: string;
  readonly durationText: string;
  readonly artifactText: string;
  readonly captionText: string;
  readonly transition: TransitionChoice;
  readonly transitionDurationText: string;
}

interface TrackForm {
  readonly formId: string;
  readonly kind: TimelineTrackKind;
  readonly clips: readonly ClipForm[];
}

function newClipForm(artifactText: string): ClipForm {
  return {
    formId: crypto.randomUUID(),
    durationText: "3000",
    artifactText,
    captionText: "",
    transition: "none",
    transitionDurationText: "500",
  };
}

function newTrackForm(kind: TimelineTrackKind, artifactText: string): TrackForm {
  return {
    formId: crypto.randomUUID(),
    kind,
    clips: [newClipForm(kind === "caption" ? "" : artifactText)],
  };
}

function parseMilliseconds(text: string): number {
  const value = text.trim();
  return /^\d+$/.test(value) ? Number(value) : Number.NaN;
}

function buildDraft(tracks: readonly TrackForm[]): EditingTimelineDraft {
  let durationMs = 0;
  const draftTracks = tracks.map((track, trackIndex) => {
    let cursor = 0;
    const clips = track.clips.map((clip, clipIndex) => {
      const clipDuration = parseMilliseconds(clip.durationText);
      const startMs = cursor;
      cursor += Number.isNaN(clipDuration) ? 0 : clipDuration;
      const artifact = clip.artifactText.trim();
      const caption = clip.captionText.trim();
      return {
        clipId: `clip-${clipIndex + 1}`,
        startMs,
        durationMs: clipDuration,
        sourceArtifactId: track.kind === "caption" || artifact === "" ? null : artifact,
        text: track.kind !== "caption" || caption === "" ? null : caption,
        transitionIn:
          clip.transition === "none"
            ? null
            : {
                kind: clip.transition,
                durationMs: parseMilliseconds(clip.transitionDurationText),
              },
      };
    });
    durationMs = Math.max(durationMs, cursor);
    return { trackId: `track-${trackIndex + 1}`, kind: track.kind, clips };
  });
  return { durationMs: Math.max(durationMs, 100), tracks: draftTracks };
}

function hydrateForm(timeline: EditingTimelineSnapshot): TrackForm[] {
  return timeline.tracks.map((track) => ({
    formId: crypto.randomUUID(),
    kind: track.kind,
    clips: track.clips.map((clip) => ({
      formId: crypto.randomUUID(),
      durationText: String(clip.durationMs),
      artifactText: clip.sourceArtifactId ?? "",
      captionText: clip.text ?? "",
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
  storageBroken,
  onCreate,
  onOpen,
  message,
}: {
  readonly projects: readonly EditingProjectSnapshot[];
  readonly storageBroken: boolean;
  readonly onCreate: (title: string, sourceReferences: string) => void;
  readonly onOpen: (projectId: string) => void;
  readonly message: Message | null;
}) {
  const [title, setTitle] = useState("");
  const [sourceReferences, setSourceReferences] = useState("");
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
            输入项目标题和要剪辑的素材引用，素材可以来自视频制作的成片或已导入的文件。
          </Typography.Text>
          <Input
            aria-label="剪辑项目标题"
            placeholder="例如：新品发布会精剪"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <Input.TextArea
            aria-label="输入素材引用"
            rows={3}
            placeholder="每行一个素材编号，可以先留空"
            value={sourceReferences}
            onChange={(event) => setSourceReferences(event.target.value)}
          />
          <div>
            <Button
              type="primary"
              disabled={storageBroken}
              onClick={() => onCreate(title, sourceReferences)}
            >
              创建剪辑项目
            </Button>
          </div>
          <MessageAlert message={message} />
        </Space>
      </Card>
      {storageBroken ? null : projects.length === 0 ? (
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
                输入素材 {project.sourceArtifactIds.length} 个
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
          <Input
            aria-label={`${position}素材引用`}
            placeholder="素材编号"
            value={clip.artifactText}
            onChange={(event) => onChange({ ...clip, artifactText: event.target.value })}
          />
        )}
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
  message,
  onTracksChange,
  onSave,
}: {
  readonly project: EditingProjectSnapshot | null;
  readonly tracks: readonly TrackForm[];
  readonly savedTimeline: EditingTimelineSnapshot | null;
  readonly message: Message | null;
  readonly onTracksChange: (tracks: readonly TrackForm[]) => void;
  readonly onSave: () => void;
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
  const defaultArtifact = project.sourceArtifactIds[0] ?? "";
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
            {savedTimeline === null
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
                      newClipForm(track.kind === "caption" ? "" : defaultArtifact),
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
          <Button onClick={() => onTracksChange([...tracks, newTrackForm("visual", defaultArtifact)])}>
            添加画面轨道
          </Button>
          <Button onClick={() => onTracksChange([...tracks, newTrackForm("audio", defaultArtifact)])}>
            添加音频轨道
          </Button>
          <Button onClick={() => onTracksChange([...tracks, newTrackForm("caption", "")])}>
            添加字幕轨道
          </Button>
          <Button type="primary" onClick={onSave}>
            保存时间轴
          </Button>
        </Space>
        <MessageAlert message={message} />
      </Space>
    </Card>
  );
}

function EditingFilmPreview({
  artifactId,
  gateway,
}: {
  readonly artifactId: string;
  readonly gateway: VideoEditingGateway;
}) {
  const [videoSource, setVideoSource] = useState<string | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);

  useEffect(() => {
    let active = true;
    void gateway
      .readEditingArtifact(artifactId)
      .then((artifact) => {
        if (active) {
          setVideoSource(`data:${artifact.mediaType};base64,${artifact.base64}`);
        }
      })
      .catch(() => {
        if (active) {
          setPreviewFailed(true);
        }
      });
    return () => {
      active = false;
    };
  }, [artifactId, gateway]);

  if (previewFailed) {
    return <Alert type="error" showIcon title="成片已生成，但暂时无法读取预览。" />;
  }
  if (videoSource === null) {
    return null;
  }
  return (
    <video
      aria-label="剪辑成片预览"
      className="video-editing-film-preview"
      controls
      src={videoSource}
    />
  );
}

function PreviewPage({
  savedTimeline,
  jobs,
  gateway,
}: {
  readonly savedTimeline: EditingTimelineSnapshot | null;
  readonly jobs: readonly EditingJobSnapshot[];
  readonly gateway: VideoEditingGateway;
}) {
  const outputArtifactId =
    [...jobs]
      .reverse()
      .find((job) => job.status === "succeeded" && job.outputArtifactIds.length > 0)
      ?.outputArtifactIds[0] ?? null;

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
        {outputArtifactId === null ? null : (
          <EditingFilmPreview
            key={outputArtifactId}
            artifactId={outputArtifactId}
            gateway={gateway}
          />
        )}
        {outputArtifactId === null ? (
          <Alert type="info" showIcon title="提交剪辑任务并成功生成成片后，可在这里播放预览。" />
        ) : null}
      </Space>
    </Card>
  );
}

function JobsPage({
  project,
  jobs,
  submitting,
  message,
  onSubmit,
}: {
  readonly project: EditingProjectSnapshot | null;
  readonly jobs: readonly EditingJobSnapshot[];
  readonly submitting: boolean;
  readonly message: Message | null;
  readonly onSubmit: () => void;
}) {
  return (
    <Space orientation="vertical" size="middle" className="video-editing-jobs">
      <Card className="video-editing-panel" title="提交剪辑">
        <Space orientation="vertical" size="middle">
          <Typography.Text type="secondary">
            提交后会按已保存的时间轴修订执行云端剪辑，执行进度和结果显示在下方任务列表。
          </Typography.Text>
          <div>
            <Button
              type="primary"
              disabled={project === null || submitting}
              loading={submitting}
              onClick={onSubmit}
            >
              提交剪辑任务
            </Button>
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
                  云端剪辑服务接入后，提交的剪辑任务会显示在这里。
                </Typography.Text>
              </Space>
            }
          />
        </Card>
      ) : (
        <Card className="video-editing-panel" title="剪辑任务">
          {jobs.map((job) => (
            <Space
              key={job.editingJobId}
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
                输入素材 {job.inputArtifactIds.length} 个 · 产出成片{" "}
                {job.outputArtifactIds.length} 个
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
}: {
  readonly gateway: VideoEditingGateway;
}) {
  const [projects, setProjects] = useState<readonly EditingProjectSnapshot[]>([]);
  const [storageBroken, setStorageBroken] = useState(false);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [tracks, setTracks] = useState<readonly TrackForm[]>([]);
  const [savedTimeline, setSavedTimeline] = useState<EditingTimelineSnapshot | null>(null);
  const [jobs, setJobs] = useState<readonly EditingJobSnapshot[]>([]);
  const [activeTab, setActiveTab] = useState("projects");
  const [projectMessage, setProjectMessage] = useState<Message | null>(null);
  const [saveMessage, setSaveMessage] = useState<Message | null>(null);
  const [submitMessage, setSubmitMessage] = useState<Message | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const failStorage = useCallback(() => {
    setStorageBroken(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void gateway
      .listProjects()
      .then((value) => {
        if (!cancelled) {
          setProjects(value);
        }
      })
      .catch(() => {
        if (!cancelled) {
          failStorage();
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gateway, failStorage]);

  const selectedProject =
    projects.find((project) => project.projectId === selectedProjectId) ?? null;
  const hasRecoverableJob = jobs.some((job) => job.status === "outcome_uncertain");

  useEffect(() => {
    if (selectedProjectId === null || !hasRecoverableJob) {
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const refresh = () => {
      void gateway
        .listEditingJobs(selectedProjectId)
        .then((value) => {
          if (!cancelled) {
            setJobs(value);
          }
        })
        .catch(() => {
          if (!cancelled) {
            cancelled = true;
            failStorage();
          }
        })
        .finally(() => {
          if (!cancelled) {
            timer = window.setTimeout(refresh, 1_000);
          }
        });
    };
    timer = window.setTimeout(refresh, 1_000);
    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [gateway, failStorage, hasRecoverableJob, selectedProjectId]);

  const openProject = (project: EditingProjectSnapshot) => {
    setSelectedProjectId(project.projectId);
    setSaveMessage(null);
    setSubmitMessage(null);
    void gateway
      .getTimeline(project.projectId)
      .then((timeline) => {
        setSavedTimeline(timeline);
        setTracks(
          timeline === null
            ? [newTrackForm("visual", project.sourceArtifactIds[0] ?? "")]
            : hydrateForm(timeline),
        );
      })
      .catch(() => failStorage());
    void gateway
      .listEditingJobs(project.projectId)
      .then(setJobs)
      .catch(() => failStorage());
  };

  const createProject = (title: string, sourceReferences: string) => {
    const sourceArtifactIds = sourceReferences
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== "");
    void gateway
      .createProject({ title: title.trim(), sourceArtifactIds })
      .then((project) => {
        setProjects((current) => [...current, project]);
        setProjectMessage({ type: "success", text: `已创建剪辑项目：${project.title}` });
        openProject(project);
      })
      .catch((error: unknown) => {
        if (
          error instanceof VideoEditingGatewayError &&
          error.code === "draft_storage_unavailable"
        ) {
          failStorage();
          return;
        }
        setProjectMessage({
          type: "error",
          text: "无法创建剪辑项目：请填写项目标题，素材引用需为有效的素材编号且不能重复。",
        });
      });
  };

  const saveTimeline = () => {
    if (selectedProject === null) {
      return;
    }
    setSaveMessage(null);
    void gateway
      .saveTimeline(selectedProject.projectId, buildDraft(tracks))
      .then((timeline) => {
        setSavedTimeline(timeline);
        setSaveMessage({
          type: "success",
          text: `已保存修订：第 ${timeline.revision} 版`,
        });
      })
      .catch((error: unknown) => {
        if (
          error instanceof VideoEditingGatewayError &&
          error.code === "draft_storage_unavailable"
        ) {
          failStorage();
          setSaveMessage({ type: "error", text: STORAGE_UNAVAILABLE_TEXT });
          return;
        }
        setSaveMessage({ type: "error", text: INVALID_TIMELINE_TEXT });
      });
  };

  const submitJob = () => {
    if (selectedProject === null) {
      return;
    }
    setSubmitting(true);
    setSubmitMessage(null);
    void gateway
      .submitEditingJob(selectedProject.projectId)
      .then(() => gateway.listEditingJobs(selectedProject.projectId).then(setJobs))
      .catch((error: unknown) => {
        if (
          error instanceof VideoEditingGatewayError &&
          error.code === "editing_service_unavailable"
        ) {
          setSubmitMessage({ type: "warning", text: SERVICE_UNAVAILABLE_TEXT });
          return;
        }
        setSubmitMessage({ type: "error", text: "剪辑任务提交失败，请稍后重试。" });
      })
      .finally(() => setSubmitting(false));
  };

  return (
    <section className="video-editing" aria-label="视频剪辑工作区">
      {storageBroken ? (
        <Alert type="error" showIcon title={STORAGE_UNAVAILABLE_TEXT} />
      ) : null}
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "projects",
            label: "剪辑项目",
            children: (
              <ProjectsPage
                projects={projects}
                storageBroken={storageBroken}
                onCreate={createProject}
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
                message={saveMessage}
                onTracksChange={setTracks}
                onSave={saveTimeline}
              />
            ),
          },
          {
            key: "preview",
            label: "预览",
            children: (
              <PreviewPage savedTimeline={savedTimeline} jobs={jobs} gateway={gateway} />
            ),
          },
          {
            key: "jobs",
            label: "提交与任务",
            children: (
              <JobsPage
                project={selectedProject}
                jobs={jobs}
                submitting={submitting}
                message={submitMessage}
                onSubmit={submitJob}
              />
            ),
          },
        ]}
      />
    </section>
  );
}
