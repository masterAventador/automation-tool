import {
  FireOutlined,
  MessageOutlined,
  PlaySquareOutlined,
  RobotOutlined,
  SendOutlined,
  SettingOutlined,
  StopOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Alert, Badge, Button, Layout, Menu, Space, Typography } from "antd";
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
import {
  motionRunAttention,
  useMotionRun,
  type MotionRunAttention,
} from "../features/video-studio/motion-run-store";
import { useMotionRunWatch } from "../features/video-studio/motion-run-watch";
import type { MaterialVideoStudioGateway } from "../features/video-studio/material-video-studio-gateway";
import type { SelectedVideo } from "../features/publishing/PublishWorkspace";
import {
  PublishWorkspaceGatewayError,
  type PublishWorkspaceGateway,
} from "../features/publishing/publish-workspace-gateway";
import { ThirdPartySoftwareNotice } from "../features/legal/third-party-software/ThirdPartySoftwareNotice";
import {
  VideoEditingGatewayError,
  type VideoEditingGateway,
} from "../features/video-editing/video-editing-gateway";
import {
  AccountPlatformOverview,
  AiAssistantHome,
  AutomationCenter,
  CreationHub,
  HotspotDiscovery,
  InteractionCenter,
  PublishingHub,
} from "../features/operations/OperationsWorkspace";

const navigationItems = [
  { key: "assistant", icon: <RobotOutlined aria-hidden="true" />, label: "AI 助理" },
  { key: "hotspots", icon: <FireOutlined aria-hidden="true" />, label: "热点发现" },
  { key: "creation", icon: <PlaySquareOutlined aria-hidden="true" />, label: "创作" },
  { key: "publishing", icon: <SendOutlined aria-hidden="true" />, label: "发布" },
  {
    key: "interactions",
    icon: <MessageOutlined aria-hidden="true" />,
    label: "消息与互动",
  },
  {
    key: "automation",
    icon: <ThunderboltOutlined aria-hidden="true" />,
    label: "自动化",
  },
  {
    key: "accounts",
    className: "sidebar-bottom-start",
    icon: <UserOutlined aria-hidden="true" />,
    label: "账号与平台",
  },
  { key: "settings", icon: <SettingOutlined aria-hidden="true" />, label: "设置" },
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
    item.key === "creation"
      ? {
          ...item,
          label: (
            <Badge {...mark.badge} offset={[6, 2]}>
              <span title={mark.title}>创作</span>
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
const LEGAL_PAGE_SECTION = "settings";
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
      catalogVerifiedAt: "2026-07-31",
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

const shellMaterialVideoStudioGateway: MaterialVideoStudioGateway = {
  async open() {
    throw new Error("Material video studio is unavailable");
  },
  async updateView() {
    throw new Error("Material video studio is unavailable");
  },
  async close() {},
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
  materialVideoStudioGateway = shellMaterialVideoStudioGateway,
  videoEditingGateway = shellVideoEditingGateway,
  publishWorkspaceGateway = shellPublishWorkspaceGateway,
  selectedVideo: initialSelectedVideo,
}: WorkbenchShellProps) {
  const [activePage, setActivePage] = useState("assistant");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [autoOpenPlatformLogin, setAutoOpenPlatformLogin] = useState(false);
  const [selectedVideo, setSelectedVideo] = useState<SelectedVideo | undefined>(
    initialSelectedVideo,
  );
  const [emergencyState, setEmergencyState] = useState<
    "idle" | "stopping" | "stopped" | "failed"
  >("idle");
  const creatingTask = activePage === "task-create";
  const showingTaskRun = activePage === "task-runs";
  const showingSettings = activePage === "settings";
  const showingAccounts = activePage === "accounts";
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
  const showingLegal = activePage === LEGAL_PAGE;
  const selectedNavigationKey =
    creatingTask || showingTaskRun
      ? "automation"
      : showingLegal
        ? LEGAL_PAGE_SECTION
        : activePage;

  const openTask = (taskId: string) => {
    setSelectedTaskId(taskId);
    setActivePage("task-runs");
  };

  const openPlatformPage = (openLogin: boolean) => {
    setAutoOpenPlatformLogin(openLogin);
    setActivePage("accounts");
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
    setActivePage("creation");
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

  const emergencyStop = async () => {
    if (emergencyState === "stopping") return;
    setEmergencyState("stopping");
    try {
      await platformAdapter.emergencyStopExecutor();
      setEmergencyState("stopped");
    } catch {
      setEmergencyState("failed");
    }
  };

  return (
    <Layout className="desktop-shell">
      {/*
       * The update prompt has to exist wherever the user is standing.
       *
       * `AppUpdateCenter` renders its prompt Modal unconditionally and adds the
       * management card only when `showSettings`. So mounting it *only* inside
       * the settings page — which is what this shell did until 2026-07-29 —
       * makes the prompt's visibility depend on the user happening to be on
       * that page. Bad for an optional update and wrong for a forced one, whose
       * whole meaning is "you cannot keep using this until you update".
       *
       * Exactly one instance is mounted at a time: this one while the user is
       * anywhere else, the settings one (which also draws the card) while they
       * are there. Two would mean two subscriptions and two modals.
       *
       * Found by H8-21's desktop acceptance, which waits for 发现新版本 right
       * after startup and sat there for 25 seconds.
       */}
      {showingSettings ? null : (
        <AppUpdateCenter gateway={appUpdateGateway} showSettings={false} />
      )}
      <Layout.Sider className="desktop-sidebar" width={184} theme="dark">
        <div className="desktop-brand">
          <div className="brand-mark brand-mark--small" aria-hidden="true">
            <RobotOutlined />
          </div>
          <div>
            <Typography.Text strong>运营助理</Typography.Text>
            <Typography.Text className="brand-caption">AI 自动化工作台</Typography.Text>
          </div>
        </div>
        <nav aria-label="桌面主导航">
          <Menu
            mode="inline"
            theme="dark"
            selectedKeys={[selectedNavigationKey]}
            items={navigationItemsWith(videoStudioAttention)}
            onClick={({ key }) => {
              if (
                key === "assistant" ||
                key === "hotspots" ||
                key === "creation" ||
                key === "publishing" ||
                key === "interactions" ||
                key === "automation" ||
                key === "accounts" ||
                key === "settings"
              ) {
                setActivePage(key);
              }
            }}
          />
        </nav>
        <div className="sidebar-status">
          <Badge status="success" text="AI 助理在线" />
          <Typography.Text>一个长期主会话</Typography.Text>
        </div>
      </Layout.Sider>

      <Layout>
        <Layout.Header className="desktop-header">
          <div className="desktop-header__title">
            <Typography.Title level={4}>
              {activePage === "assistant"
                ? "AI 运营助理"
                : activePage === "hotspots"
                  ? "热点发现"
                  : activePage === "creation"
                    ? "创作"
                    : activePage === "publishing"
                      ? "发布"
                      : activePage === "interactions"
                        ? "消息与互动"
                        : activePage === "automation" || creatingTask || showingTaskRun
                          ? "自动化"
                          : showingAccounts
                            ? "账号与平台"
                            : showingLegal
                              ? LEGAL_PAGE_TITLE
                              : "设置"}
            </Typography.Title>
            <Badge status="success" text="运行正常" />
          </div>
          <Button
            className="emergency-stop-button"
            danger
            icon={<StopOutlined aria-hidden="true" />}
            loading={emergencyState === "stopping"}
            onClick={() => void emergencyStop()}
          >
            紧急停止
          </Button>
        </Layout.Header>
        <Layout.Content
          className={`desktop-content${activePage === "assistant" ? " desktop-content--assistant" : ""}`}
        >
          <main>
            {emergencyState === "stopped" ? (
              <Alert
                className="global-stop-alert"
                type="warning"
                showIcon
                closable
                title="本机自动执行已停止"
                description="正在运行的浏览器和本机自动化已进入停止状态。重新启用前不会继续执行动作。"
                onClose={() => setEmergencyState("idle")}
              />
            ) : emergencyState === "failed" ? (
              <Alert
                className="global-stop-alert"
                type="error"
                showIcon
                title="无法确认紧急停止结果"
                description="请立即检查本机执行器状态；界面不会把这次操作标成已成功。"
              />
            ) : null}

            {activePage === "assistant" ? (
              <AiAssistantHome onOpenHotspots={() => setActivePage("hotspots")} />
            ) : activePage === "hotspots" ? (
              <HotspotDiscovery onCreateFromHotspot={() => setActivePage("creation")} />
            ) : activePage === "creation" ? (
              <CreationHub
                gateway={materialVideoStudioGateway}
                editingGateway={videoEditingGateway}
                onPublishArtifact={publishSelectedVideo}
              />
            ) : activePage === "publishing" ? (
              <PublishingHub
                gateway={publishWorkspaceGateway}
                selectedVideo={selectedVideo}
                onChangeSelection={chooseAnotherVideo}
              />
            ) : activePage === "interactions" ? (
              <InteractionCenter />
            ) : activePage === "automation" ? (
              <AutomationCenter
                onOpenRuns={() => setActivePage("task-runs")}
                onCreateTask={() => setActivePage("task-create")}
              />
            ) : creatingTask ? (
              <section className="ops-page legacy-task-page">
                <Button type="link" onClick={() => setActivePage("automation")}>
                  返回自动化
                </Button>
                <Typography.Title level={2}>新建运营任务</Typography.Title>
                <TaskCreate gateway={taskCreationGateway} onCreated={openTask} />
              </section>
            ) : showingAccounts ? (
              <AccountPlatformOverview>
                <PlatformSessions
                  gateway={platformSessionGateway}
                  autoOpenLogin={autoOpenPlatformLogin}
                  onAutoOpenConsumed={() => setAutoOpenPlatformLogin(false)}
                />
              </AccountPlatformOverview>
            ) : showingLegal ? (
              <section className="ops-page settings-page">
                <Button type="link" onClick={() => setActivePage("settings")}>
                  返回设置
                </Button>
                <ThirdPartySoftwareNotice />
              </section>
            ) : showingSettings ? (
              <section className="ops-page settings-page">
                <div className="ops-page-intro">
                  <Typography.Text className="ops-eyebrow">本机与服务</Typography.Text>
                  <Typography.Title level={2}>设置</Typography.Title>
                  <Typography.Paragraph>
                    管理模型、本机执行器、诊断和更新。真实凭据不会进入页面状态。
                  </Typography.Paragraph>
                </div>
                <Space orientation="vertical" size="large" className="settings-stack">
                  <AppUpdateCenter gateway={appUpdateGateway} showSettings />
                  <ModelServiceSettings gateway={modelServiceGateway} />
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
              </section>
            ) : showingTaskRun && selectedTaskId !== null ? (
              <section className="ops-page legacy-task-page">
                <TaskRunDetails
                  taskId={selectedTaskId}
                  taskSource={taskSource}
                  controlGateway={taskRunControlGateway}
                  discoveryGateway={taskDiscoveryGateway}
                  taskTargetPreviewSource={taskTargetPreviewSource}
                  taskTargetResultSource={taskTargetResultSource}
                  /*
                   * 离开一条任务时把选中一起清掉。
                   *
                   * `selectedTaskId` 此前只有 `openTask` 一处写入、从不清空，而这一页
                   * 的渲染是「有选中就显示详情、否则显示列表」——于是用户只要点开过
                   * 任何一条任务，「查看运行记录」在本次会话里就永远停在那一条上，
                   * 列表再也回不去，唯一的出路是重启 App。
                   *
                   * 由 T3-18 的桌面验收发现：它取消完第一条任务、返回、再进运行记录
                   * 要打开第二条，而页面还停在第一条的详情上。
                   */
                  onBack={() => {
                    setSelectedTaskId(null);
                    setActivePage("automation");
                  }}
                  onOpenPlatformSession={() => openPlatformPage(false)}
                  onPlatformLoginRequired={() => openPlatformPage(true)}
                />
              </section>
            ) : showingTaskRun ? (
              <section className="ops-page legacy-task-page">
                <Button type="link" onClick={() => setActivePage("automation")}>
                  返回自动化
                </Button>
                <Typography.Title level={2}>运行记录</Typography.Title>
                <Workbench taskSource={taskSource} gateway={gateway} onOpenTask={openTask} />
              </section>
            ) : (
              <AiAssistantHome onOpenHotspots={() => setActivePage("hotspots")} />
            )}
          </main>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
