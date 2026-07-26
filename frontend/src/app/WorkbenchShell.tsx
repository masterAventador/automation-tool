import { Badge, Button, Flex, Layout, Menu, Space, Tag, Typography } from "antd";
import { useState } from "react";

import {
  TaskProjectionSourceError,
  type TaskProjectionSource,
} from "../api/control-plane/task-projections";
import {
  TaskTargetPreviewSourceError,
  type TaskTargetPreviewSource,
} from "../api/control-plane/task-target-previews";
import {
  TaskTargetResultSourceError,
  type TaskTargetResultSource,
} from "../api/control-plane/task-target-results";
import { TaskCreate } from "../features/task-create/TaskCreate";
import {
  TaskCreationGatewayError,
  type TaskCreationGateway,
} from "../features/task-create/task-creation-gateway";
import { TaskRunDetails } from "../features/task-runs/TaskRunDetails";
import {
  TaskDiscoveryGatewayError,
  type TaskDiscoveryGateway,
} from "../features/task-runs/task-discovery";
import {
  TaskRunControlGatewayError,
  type TaskRunControlGateway,
} from "../features/task-runs/task-run-controls";
import { Workbench } from "../features/workbench/Workbench";
import type { WorkbenchGateway } from "../features/workbench/workbench-gateway";
import { Diagnostics } from "../features/diagnostics/Diagnostics";
import { PlatformSessions } from "../features/platform-sessions/PlatformSessions";
import {
  PlatformSessionGatewayError,
  type PlatformSessionGateway,
} from "../features/platform-sessions/platform-session-gateway";
import type { PlatformAdapter } from "../platform/types";
import { AppUpdateCenter } from "../features/app-updates/AppUpdateCenter";
import type { AppUpdateGateway } from "../features/app-updates/contracts";
import { ModelServiceSettings } from "../features/settings/ModelServiceSettings";
import type { ModelServiceGateway } from "../features/settings/model-service-gateway";
import { VideoEditingServiceSettings } from "../features/settings/VideoEditingServiceSettings";
import type { VideoEditingServiceGateway } from "../features/settings/video-editing-service-gateway";
import { VideoStudio } from "../features/video-studio/VideoStudio";
import {
  motionRunAttention,
  useMotionRun,
  type MotionRunAttention,
} from "../features/video-studio/motion-run-store";
import { useMotionRunWatch } from "../features/video-studio/motion-run-watch";
import type { MaterialVideoStudioGateway } from "../features/video-studio/material-video-studio-gateway";
import { PublishWorkspace, type SelectedVideo } from "../features/publishing/PublishWorkspace";
import {
  PublishWorkspaceGatewayError,
  type PublishWorkspaceGateway,
} from "../features/publishing/publish-workspace-gateway";
import { ThirdPartySoftwareNotice } from "../features/legal/third-party-software/ThirdPartySoftwareNotice";
import { VideoEditingWorkbench } from "../features/video-editing/VideoEditingWorkbench";
import {
  VideoEditingGatewayError,
  type VideoEditingGateway,
} from "../features/video-editing/video-editing-gateway";

const navigationItems = [
  { key: "workbench", label: "工作台" },
  { key: "task-create", label: "新建任务" },
  { key: "task-runs", label: "任务记录" },
  { key: "video-studio", label: "视频制作" },
  { key: "video-editing", label: "视频剪辑" },
  { key: "publishing", label: "作品发布" },
  { key: "platform", label: "平台状态" },
  { key: "diagnostics", label: "设置与诊断" },
];

/**
 * What the mark on the 视频制作 entry looks like, and what it says when hovered.
 *
 * A run in flight is a bare dot, so the entry's accessible name stays 视频制作
 * and nothing that navigates by name has to change. The three states that give
 * the operator something to do carry their own word instead, because a mark
 * whose meaning is only in a `title` is a mark nobody reads.
 *
 * 完成 is green and the other two worded marks are not. Colour is the only
 * thing separating "there is a film waiting for you" from "something is wrong",
 * and the operator reads it before he reads the word — a finished film wearing
 * the same red as 失败 and 未知 would make every glance at the sidebar an alarm
 * and teach him to stop looking. All three are two characters wide on purpose:
 * whether the 232px sidebar clips this badge is still unverified (T91, T91b),
 * and a wider word would make that open question worse rather than reuse the
 * answer.
 */
const VIDEO_STUDIO_MARKS = {
  running: { title: "视频制作正在进行中", badge: { dot: true } },
  failed: { title: "视频制作失败了，去看看", badge: { count: "失败" } },
  unknown: { title: "读不到视频制作进度，去看看", badge: { count: "未知" } },
  finished: { title: "视频制作做好了，去看成片", badge: { count: "完成", color: "green" } },
} as const;

/**
 * Put a mark on 视频制作 while a film is being made, and until its result has
 * been seen.
 *
 * Making one takes minutes — 136 to 178 of them are spent authoring, measured —
 * and nobody stands on one page that long. Every other surface of this shell
 * was silent about it: leave the page and the App looked exactly as if nothing
 * had ever been submitted, which is what made operators submit a second time
 * and start a second authoring run.
 *
 * A run in flight is a dot, so the entry's accessible name stays 视频制作 and
 * nothing that navigates by name has to change; the reason is carried in a
 * `title` so it is available rather than merely visible.
 *
 * A failure is not a dot. Measured on 2026-07-26: a run failed at four seconds
 * while the operator was elsewhere, and at twelve minutes a full-screen sweep
 * for 失败, 出错, 超时, 不可用 and 无法 came back empty — because the only mark
 * was a dot, and its hover text said 视频制作正在进行中. The mark was there and
 * it was telling the operator the opposite of what had happened. A failure has
 * to carry its own word, on screen, without hovering anything.
 *
 * That word does join the entry's accessible name, which becomes 视频制作 失败
 * for as long as the failure is unread. That is the point rather than a cost —
 * a screen reader is exactly where a silent failure is most silent — and it is
 * only reachable from the failed branch. Anything selecting this entry has to
 * match the name as a substring, which is what both Playwright's `getByRole`
 * and the desktop suite's `normalize-space()` XPath already do.
 *
 * 未知 is the third thing this entry can say, and it exists because watching a
 * render from outside the page (`motion-run-watch.ts`) added something that can
 * itself fail. When the App cannot read a run it is still waiting on, it says
 * so. Falling back to the dot there would put the original lie straight back —
 * a film reported as in progress by an App that has no idea.
 *
 * 完成 is the fourth, and it is the same defect with the sign flipped. The
 * watcher already knew the render had ended; it just had no way to say so, so
 * the dot went on reading 正在进行中 over a film that had been sitting finished
 * for minutes. It stays lit until 去看成片 is pressed on the studio page — the
 * one action that also clears the notice there, so the two surfaces are never
 * out of step and there is nothing new to remember about when the mark goes.
 */
function navigationItemsWith(attention: MotionRunAttention) {
  if (attention === "none") return navigationItems;
  const mark = VIDEO_STUDIO_MARKS[attention];
  return navigationItems.map((item) =>
    item.key === "video-studio"
      ? {
          ...item,
          label: (
            <Badge {...mark.badge} offset={[6, 2]}>
              <span title={mark.title}>{item.label}</span>
            </Badge>
          ),
        }
      : item,
  );
}

/**
 * The open source licence notice is a licence obligation, not a daily operating
 * tool, so it is not a sidebar destination. It hangs off the foot of 设置与诊断
 * and keeps that entry selected while it is open — nothing else in the sidebar
 * leads here, and an unselected sidebar would read as a broken page.
 */
const LEGAL_PAGE = "legal";
const LEGAL_PAGE_SECTION = "diagnostics";
const LEGAL_PAGE_TITLE = "开源软件许可";

const shellTaskSource: TaskProjectionSource = {
  async getTask() {
    throw new TaskProjectionSourceError("transport_unavailable", true);
  },
  async listTasks() {
    return { items: [], nextCursor: null };
  },
  async streamTaskEvents(_taskId, afterSequence) {
    return { lastSequence: afterSequence, terminal: false };
  },
};

const shellWorkbenchGateway: WorkbenchGateway = {
  async getRuntimeStatus() {
    return {
      controlPlaneStatus: "ready",
      executorStatus: "offline",
      executorLastHeartbeatAt: null,
    };
  },
  async getMetrics() {
    return {
      version: "workbench.metrics.v1",
      tasks: {
        total: 0,
        succeeded: 0,
        failed: 0,
        handoffRequired: 0,
        outcomeUncertain: 0,
      },
      actions: { total: 0, succeeded: 0, failed: 0, outcomeUncertain: 0 },
    };
  },
  async emergencyStopTask() {
    throw new Error("Workbench emergency stop is unavailable");
  },
};

const shellTaskCreationGateway: TaskCreationGateway = {
  async createDouyinSearchExposureTask() {
    throw new TaskCreationGatewayError("transport_unavailable", true);
  },
};

const shellTaskRunControlGateway: TaskRunControlGateway = {
  async pauseTask() {
    throw new TaskRunControlGatewayError("transport_unavailable", true);
  },
  async resumeTask() {
    throw new TaskRunControlGatewayError("transport_unavailable", true);
  },
  async cancelTask() {
    throw new TaskRunControlGatewayError("transport_unavailable", true);
  },
  async emergencyStopTask() {
    throw new TaskRunControlGatewayError("transport_unavailable", true);
  },
};

const shellTaskDiscoveryGateway: TaskDiscoveryGateway = {
  async startDiscovery() {
    throw new TaskDiscoveryGatewayError("transport_unavailable", true);
  },
};

const shellTaskTargetPreviewSource: TaskTargetPreviewSource = {
  async getPreview() {
    throw new TaskTargetPreviewSourceError("transport_unavailable", true);
  },
  async replaceExclusions() {
    throw new TaskTargetPreviewSourceError("transport_unavailable", true);
  },
  async confirm() {
    throw new TaskTargetPreviewSourceError("transport_unavailable", true);
  },
};

const shellTaskTargetResultSource: TaskTargetResultSource = {
  async getResults() {
    throw new TaskTargetResultSourceError("transport_unavailable", true);
  },
};

const shellPlatformAdapter: PlatformAdapter = {
  async getExecutorStatus() {
    return { state: "stopped", version: null, buildId: null, restartCount: 0 };
  },
  async restartExecutor() {
    throw new Error("Local Executor restart is unavailable");
  },
  async getExecutorDiagnostics() {
    return [];
  },
  async exportDiagnostics() {
    throw new Error("Diagnostic export is unavailable");
  },
  async emergencyStopExecutor() {
    return { state: "stopped", version: null, buildId: null, restartCount: 0 };
  },
  async getBrowserDiagnosticSettings() {
    return { captureSuccessfulRuns: false };
  },
  async setCaptureSuccessfulDiagnostics() {
    return { captureSuccessfulRuns: false };
  },
};

const shellPlatformSessionGateway: PlatformSessionGateway = {
  async getDouyinSession() {
    throw new PlatformSessionGatewayError("transport_unavailable", true);
  },
  async openDouyinLogin() {
    throw new PlatformSessionGatewayError("operation_unavailable", false);
  },
  async recheckDouyinLogin() {
    throw new PlatformSessionGatewayError("operation_unavailable", false);
  },
  async logoutDouyinSession() {
    throw new PlatformSessionGatewayError("operation_unavailable", false);
  },
};

const shellAppUpdateGateway: AppUpdateGateway = {
  async getState() {
    return {
      state: "failed",
      stage: "configuration",
      code: "configuration_invalid",
      retryable: false,
    };
  },
  async checkNow() {
    return this.getState();
  },
  async decide() {
    return this.getState();
  },
};

const shellModelServiceGateway: ModelServiceGateway = {
  async getSettings() {
    return {
      provider: "bailian",
      providerLabel: "阿里百炼",
      catalogVerifiedAt: "2026-07-23",
      script: { purpose: "script", configured: false, modelId: "qwen3.7-max-2026-06-08" },
      videoCreative: {
        purpose: "video_creative",
        configured: false,
        modelId: "qwen3.7-max-2026-06-08",
      },
      sameCredential: false,
    };
  },
  async configure() {
    throw new Error("Model service configuration is unavailable");
  },
  async reuseScriptForVideo() {
    throw new Error("Model service configuration is unavailable");
  },
  async clear() {
    throw new Error("Model service configuration is unavailable");
  },
  async testConnection() {
    throw new Error("Model service connection test is unavailable");
  },
};

/// Without a real bridge the page must say it cannot read the state, not
/// invent one: a fabricated "ready" would offer a publish nothing can carry out.
const shellPublishWorkspaceGateway: PublishWorkspaceGateway = {
  async getWorkspace() {
    throw new PublishWorkspaceGatewayError("bridge_unavailable");
  },
  async beginPublish() {
    throw new PublishWorkspaceGatewayError("bridge_unavailable");
  },
  async approvePublish() {
    throw new PublishWorkspaceGatewayError("bridge_unavailable");
  },
  async cancelPublish() {
    throw new PublishWorkspaceGatewayError("bridge_unavailable");
  },
};

const shellVideoEditingGateway: VideoEditingGateway = {
  async listProjects() {
    return [];
  },
  async createProject() {
    throw new VideoEditingGatewayError("draft_storage_unavailable", true);
  },
  async getTimeline() {
    return null;
  },
  async saveTimeline() {
    throw new VideoEditingGatewayError("draft_storage_unavailable", true);
  },
  async listEditingJobs() {
    return [];
  },
  async submitEditingJob() {
    throw new VideoEditingGatewayError("editing_service_unavailable", false);
  },
};

const shellVideoEditingServiceGateway: VideoEditingServiceGateway = {
  async getSettings() {
    return {
      provider: "aliyun_ims",
      providerLabel: "阿里云视频剪辑服务",
      catalogVerifiedAt: "2026-07-23",
      configured: false,
      region: null,
    };
  },
  async configure() {
    throw new Error("Video editing service configuration is unavailable");
  },
  async clear() {
    throw new Error("Video editing service configuration is unavailable");
  },
  async testConnection() {
    throw new Error("Video editing service connection test is unavailable");
  },
};

const shellMaterialVideoStudioGateway: MaterialVideoStudioGateway = {
  async open() {
    throw new Error("Material video studio is unavailable");
  },
  async jobs() {
    return [];
  },
  async cancel() {
    throw new Error("Material video studio is unavailable");
  },
  async deleteArtifact() {
    throw new Error("Material video studio is unavailable");
  },
  async submitMotionDraft() {
    throw new Error("Motion video studio is unavailable");
  },
  async motionJobs() {
    return [];
  },
  async cancelMotionRenderJob() {
    throw new Error("Motion video studio is unavailable");
  },
  async readMotionArtifact() {
    throw new Error("Motion video studio is unavailable");
  },
  async deleteMotionArtifact() {
    throw new Error("Motion video studio is unavailable");
  },
  async submitMotionBrief() {
    throw new Error("Motion video studio is unavailable");
  },
  async readMaterialArtifact() {
    throw new Error("Material video studio is unavailable");
  },
};

interface WorkbenchShellProps {
  readonly taskSource?: TaskProjectionSource | undefined;
  readonly gateway?: WorkbenchGateway | undefined;
  readonly taskCreationGateway?: TaskCreationGateway | undefined;
  readonly taskRunControlGateway?: TaskRunControlGateway | undefined;
  readonly taskDiscoveryGateway?: TaskDiscoveryGateway | undefined;
  readonly taskTargetPreviewSource?: TaskTargetPreviewSource | undefined;
  readonly taskTargetResultSource?: TaskTargetResultSource | undefined;
  readonly platformAdapter?: PlatformAdapter | undefined;
  readonly platformSessionGateway?: PlatformSessionGateway | undefined;
  readonly appUpdateGateway?: AppUpdateGateway | undefined;
  readonly modelServiceGateway?: ModelServiceGateway | undefined;
  readonly videoEditingServiceGateway?: VideoEditingServiceGateway | undefined;
  readonly materialVideoStudioGateway?: MaterialVideoStudioGateway | undefined;
  readonly videoEditingGateway?: VideoEditingGateway | undefined;
  readonly publishWorkspaceGateway?: PublishWorkspaceGateway | undefined;
  /**
   * The video the publishing page starts with, if one is already chosen.
   *
   * Only a starting point. The shell owns the selection from then on, because
   * choosing a video happens on one page and publishing it happens on another,
   * and the two have to agree on which one it is.
   */
  readonly selectedVideo?: SelectedVideo | undefined;
}

export function WorkbenchShell({
  taskSource = shellTaskSource,
  gateway = shellWorkbenchGateway,
  taskCreationGateway = shellTaskCreationGateway,
  taskRunControlGateway = shellTaskRunControlGateway,
  taskDiscoveryGateway = shellTaskDiscoveryGateway,
  taskTargetPreviewSource = shellTaskTargetPreviewSource,
  taskTargetResultSource = shellTaskTargetResultSource,
  platformAdapter = shellPlatformAdapter,
  platformSessionGateway = shellPlatformSessionGateway,
  appUpdateGateway = shellAppUpdateGateway,
  modelServiceGateway = shellModelServiceGateway,
  videoEditingServiceGateway = shellVideoEditingServiceGateway,
  materialVideoStudioGateway = shellMaterialVideoStudioGateway,
  videoEditingGateway = shellVideoEditingGateway,
  publishWorkspaceGateway = shellPublishWorkspaceGateway,
  selectedVideo: initialSelectedVideo,
}: WorkbenchShellProps) {
  const [activePage, setActivePage] = useState("workbench");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [autoOpenPlatformLogin, setAutoOpenPlatformLogin] = useState(false);
  const [selectedVideo, setSelectedVideo] = useState<SelectedVideo | undefined>(
    initialSelectedVideo,
  );
  const creatingTask = activePage === "task-create";
  const showingTaskRun = activePage === "task-runs";
  const showingDiagnostics = activePage === "diagnostics";
  const showingPlatform = activePage === "platform";
  const showingVideoStudio = activePage === "video-studio";
  // Read from the store rather than from `VideoStudio`, which is unmounted
  // exactly when this mark matters most.
  const videoStudioAttention = motionRunAttention(useMotionRun());
  /*
   * And keep the store true while that component is gone. The mark above can
   * only be as honest as the last thing that went and looked, and until this
   * existed the only thing that ever looked was the studio page itself. Costs
   * and cleanup are argued in `motion-run-watch.ts`; the short version is that
   * it runs only while a film this session started is still owed an outcome.
   */
  useMotionRunWatch(materialVideoStudioGateway);
  const showingVideoEditing = activePage === "video-editing";
  const showingPublishing = activePage === "publishing";
  const showingLegal = activePage === LEGAL_PAGE;

  const openTask = (taskId: string) => {
    setSelectedTaskId(taskId);
    setActivePage("task-runs");
  };

  const openPlatformPage = (openLogin: boolean) => {
    setAutoOpenPlatformLogin(openLogin);
    setActivePage("platform");
  };

  /**
   * Go choose a video, and stop claiming one is selected on the way.
   *
   * Finished videos are managed on one page and published from another, so
   * "换一个" is a trip back to the first. The selection is dropped before the
   * trip rather than after it: a stale selection left showing while the
   * operator picks a different one is how the wrong video gets published.
   */
  const chooseAnotherVideo = () => {
    setSelectedVideo(undefined);
    setActivePage("video-studio");
  };

  /**
   * Take a finished video from the studio over to publishing.
   *
   * The return leg of `chooseAnotherVideo`. Adopting the selection and moving
   * pages has to be one action: a selection recorded without the trip leaves
   * the operator staring at the finished-videos page wondering what happened,
   * and a trip without the selection lands them on a publishing page that
   * still says nothing is chosen.
   */
  const publishSelectedVideo = (video: SelectedVideo) => {
    setSelectedVideo(video);
    setActivePage("publishing");
  };

  return (
    <Layout className="desktop-shell">
      <Layout.Sider className="desktop-sidebar" width={232} theme="light">
        <Flex className="desktop-brand" align="center" gap={12}>
          <div className="brand-mark brand-mark--small" aria-hidden="true">
            A
          </div>
          <Space orientation="vertical" size={0}>
            <Typography.Text strong>自动化运营工具</Typography.Text>
            <Typography.Text className="brand-caption">自动替你操作网页</Typography.Text>
          </Space>
        </Flex>
        <nav aria-label="桌面主导航">
          <Menu
            mode="inline"
            selectedKeys={[showingLegal ? LEGAL_PAGE_SECTION : activePage]}
            items={navigationItemsWith(videoStudioAttention)}
            onClick={({ key }) => {
              if (
                key === "workbench" ||
                key === "task-create" ||
                key === "task-runs" ||
                key === "video-studio" ||
                key === "video-editing" ||
                key === "publishing" ||
                key === "diagnostics" ||
                key === "platform"
              ) {
                setActivePage(key);
              }
            }}
          />
        </nav>
        <div className="sidebar-status">
          <Badge status="processing" text="桌面端已就绪" />
        </div>
      </Layout.Sider>

      <Layout>
        <Layout.Header className="desktop-header">
          <Typography.Text type="secondary">抖音运营</Typography.Text>
          <Tag color="blue">本机桌面模式</Tag>
        </Layout.Header>
        <Layout.Content className="desktop-content">
          <main>
            <Flex justify="space-between" align="end" gap={24}>
              <Space orientation="vertical" size={4}>
                <Typography.Title level={2}>
                  {creatingTask
                    ? "新建运营任务"
                    : showingPlatform
                      ? "平台状态"
                    : showingVideoStudio
                      ? "视频制作"
                    : showingVideoEditing
                      ? "视频剪辑"
                    : showingPublishing
                      ? "作品发布"
                    : showingDiagnostics
                      ? "设置与诊断"
                    : showingLegal
                      ? LEGAL_PAGE_TITLE
                    : showingTaskRun
                      ? "任务记录"
                      : "RPA 运营工作台"}
                </Typography.Title>
                <Typography.Text type="secondary">
                  {creatingTask
                    ? "配置一个可预览、可确认的抖音搜索曝光任务。"
                    : showingPlatform
                      ? "查看抖音登录健康，并在系统运营浏览器中完成人工处理。"
                    : showingVideoStudio
                      ? "从需求、脚本与分镜到预览、任务和成片，按真实制作状态逐步推进。"
                    : showingVideoEditing
                      ? "把制作或导入的素材整理成时间轴，独立于视频制作管理剪辑项目与任务。"
                    : showingPublishing
                      ? "把做好的视频发到 B站或抖音，发布前先确认账号与文案。"
                    : showingDiagnostics
                      ? "管理模型服务、受信运营浏览器、本地执行器、诊断与 App 更新。"
                    : showingLegal
                      ? "本产品分发的开源组件、它们的许可证、固定版本和源码获取地址。"
                    : showingTaskRun
                      ? "从权威快照与持久事件查看运行状态和控制结果。"
                    : "RPA 就是自动替你操作网页：从一个真实平台、一个任务闭环开始，执行过程可见、可暂停、可接管。"}
                </Typography.Text>
              </Space>
              <Tag variant="filled" color="green">
                {creatingTask
                  ? "任务模板已就绪"
                  : showingPlatform
                    ? "登录边界"
                  : showingVideoStudio
                    ? "视频工作区"
                  : showingVideoEditing
                    ? "剪辑工作区"
                  : showingPublishing
                    ? "发布边界"
                  : showingDiagnostics
                    ? "本地边界"
                  : showingLegal
                    ? "开源合规"
                  : showingTaskRun
                    ? "任务事实已连接"
                    : "工作台已就绪"}
              </Tag>
            </Flex>

            <AppUpdateCenter gateway={appUpdateGateway} showSettings={showingDiagnostics} />

            {creatingTask ? (
              <TaskCreate
                gateway={taskCreationGateway}
                onCreated={openTask}
              />
            ) : showingPlatform ? (
              <div className="platform-session-content">
                <PlatformSessions
                  gateway={platformSessionGateway}
                  autoOpenLogin={autoOpenPlatformLogin}
                  onAutoOpenConsumed={() => setAutoOpenPlatformLogin(false)}
                />
              </div>
            ) : showingVideoStudio ? (
              <VideoStudio
                gateway={materialVideoStudioGateway}
                onPublishArtifact={publishSelectedVideo}
              />
            ) : showingVideoEditing ? (
              <VideoEditingWorkbench gateway={videoEditingGateway} />
            ) : showingPublishing ? (
              // Carries the step below the title row, the same way
              // `.platform-session-content` does. It belongs out here rather
              // than on the component's own root because `PublishWorkspace`
              // returns from three different places.
              <div className="publish-workspace-content">
                <PublishWorkspace
                  gateway={publishWorkspaceGateway}
                  selectedVideo={selectedVideo}
                  onChangeSelection={chooseAnotherVideo}
                />
              </div>
            ) : showingLegal ? (
              <ThirdPartySoftwareNotice />
            ) : showingDiagnostics ? (
              <Space orientation="vertical" size="large" className="settings-stack">
                <ModelServiceSettings gateway={modelServiceGateway} />
                <VideoEditingServiceSettings gateway={videoEditingServiceGateway} />
                <Diagnostics platform={platformAdapter} />
                <div className="settings-legal-entry">
                  <Button
                    type="link"
                    size="small"
                    onClick={() => setActivePage(LEGAL_PAGE)}
                  >
                    {LEGAL_PAGE_TITLE}
                  </Button>
                </div>
              </Space>
            ) : showingTaskRun && selectedTaskId !== null ? (
              <TaskRunDetails
                taskId={selectedTaskId}
                taskSource={taskSource}
                controlGateway={taskRunControlGateway}
                discoveryGateway={taskDiscoveryGateway}
                taskTargetPreviewSource={taskTargetPreviewSource}
                taskTargetResultSource={taskTargetResultSource}
                onBack={() => setActivePage("workbench")}
                onOpenPlatformSession={() => openPlatformPage(false)}
                onPlatformLoginRequired={() => openPlatformPage(true)}
              />
            ) : showingTaskRun ? (
              <div className="task-run-empty">
                <Typography.Title level={4}>请选择一个任务</Typography.Title>
                <Typography.Text type="secondary">
                  从工作台当前任务或最近任务进入运行详情。
                </Typography.Text>
              </div>
            ) : (
              <Workbench taskSource={taskSource} gateway={gateway} onOpenTask={openTask} />
            )}
          </main>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
