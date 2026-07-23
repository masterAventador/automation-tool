import { Badge, Flex, Layout, Menu, Space, Tag, Typography } from "antd";
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
import type { MaterialVideoStudioGateway } from "../features/video-studio/material-video-studio-gateway";
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
  { key: "platform", label: "平台状态" },
  { key: "diagnostics", label: "设置与诊断" },
];

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
}: WorkbenchShellProps) {
  const [activePage, setActivePage] = useState("workbench");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [autoOpenPlatformLogin, setAutoOpenPlatformLogin] = useState(false);
  const creatingTask = activePage === "task-create";
  const showingTaskRun = activePage === "task-runs";
  const showingDiagnostics = activePage === "diagnostics";
  const showingPlatform = activePage === "platform";
  const showingVideoStudio = activePage === "video-studio";
  const showingVideoEditing = activePage === "video-editing";

  const openTask = (taskId: string) => {
    setSelectedTaskId(taskId);
    setActivePage("task-runs");
  };

  const openPlatformPage = (openLogin: boolean) => {
    setAutoOpenPlatformLogin(openLogin);
    setActivePage("platform");
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
            <Typography.Text className="brand-caption">RPA 运营</Typography.Text>
          </Space>
        </Flex>
        <nav aria-label="桌面主导航">
          <Menu
            mode="inline"
            selectedKeys={[activePage]}
            items={navigationItems}
            onClick={({ key }) => {
              if (
                key === "workbench" ||
                key === "task-create" ||
                key === "task-runs" ||
                key === "video-studio" ||
                key === "video-editing" ||
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
          <Typography.Text type="secondary">抖音运营 MVP</Typography.Text>
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
                    : showingDiagnostics
                      ? "设置与诊断"
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
                    : showingDiagnostics
                      ? "管理模型服务、受信运营浏览器、本地执行器、诊断与 App 更新。"
                    : showingTaskRun
                      ? "从权威快照与持久事件查看运行状态和控制结果。"
                    : "从一个真实平台、一个任务闭环开始，执行过程可见、可暂停、可接管。"}
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
                  : showingDiagnostics
                    ? "本地边界"
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
              <VideoStudio gateway={materialVideoStudioGateway} />
            ) : showingVideoEditing ? (
              <VideoEditingWorkbench gateway={videoEditingGateway} />
            ) : showingDiagnostics ? (
              <Space orientation="vertical" size="large" className="settings-stack">
                <ModelServiceSettings gateway={modelServiceGateway} />
                <VideoEditingServiceSettings gateway={videoEditingServiceGateway} />
                <Diagnostics platform={platformAdapter} />
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
