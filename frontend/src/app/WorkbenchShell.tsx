import { Badge, Flex, Layout, Menu, Space, Tag, Typography } from "antd";
import { useState } from "react";

import {
  TaskProjectionSourceError,
  type TaskProjectionSource,
} from "../api/control-plane/task-projections";
import { TaskCreate } from "../features/task-create/TaskCreate";
import {
  TaskCreationGatewayError,
  type TaskCreationGateway,
} from "../features/task-create/task-creation-gateway";
import { TaskRunDetails } from "../features/task-runs/TaskRunDetails";
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
import { BrowserSettings } from "../features/settings/BrowserSettings";
import type { PlatformAdapter } from "../platform/types";

const navigationItems = [
  { key: "workbench", label: "工作台" },
  { key: "task-create", label: "新建任务" },
  { key: "task-runs", label: "任务记录" },
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

const shellPlatformAdapter: PlatformAdapter = {
  async getBrowserSettings() {
    return { availableBrowsers: [], selectedBrowser: null };
  },
  async selectBrowser() {
    throw new Error("Browser selection is unavailable");
  },
  async getExecutorStatus() {
    return { state: "stopped", version: null, buildId: null, restartCount: 0 };
  },
  async restartExecutor() {
    throw new Error("Local Executor restart is unavailable");
  },
  async getExecutorDiagnostics() {
    return [];
  },
  async emergencyStopExecutor() {
    return { state: "stopped", version: null, buildId: null, restartCount: 0 };
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

interface WorkbenchShellProps {
  readonly taskSource?: TaskProjectionSource | undefined;
  readonly gateway?: WorkbenchGateway | undefined;
  readonly taskCreationGateway?: TaskCreationGateway | undefined;
  readonly taskRunControlGateway?: TaskRunControlGateway | undefined;
  readonly platformAdapter?: PlatformAdapter | undefined;
  readonly platformSessionGateway?: PlatformSessionGateway | undefined;
}

export function WorkbenchShell({
  taskSource = shellTaskSource,
  gateway = shellWorkbenchGateway,
  taskCreationGateway = shellTaskCreationGateway,
  taskRunControlGateway = shellTaskRunControlGateway,
  platformAdapter = shellPlatformAdapter,
  platformSessionGateway = shellPlatformSessionGateway,
}: WorkbenchShellProps) {
  const [activePage, setActivePage] = useState("workbench");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const creatingTask = activePage === "task-create";
  const showingTaskRun = activePage === "task-runs";
  const showingDiagnostics = activePage === "diagnostics";
  const showingPlatform = activePage === "platform";

  const openTask = (taskId: string) => {
    setSelectedTaskId(taskId);
    setActivePage("task-runs");
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
                    : showingDiagnostics
                      ? "选择受信运营浏览器，并管理 App 自己的本地执行器。"
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
                  : showingDiagnostics
                    ? "本地边界"
                  : showingTaskRun
                    ? "任务事实已连接"
                    : "工作台已就绪"}
              </Tag>
            </Flex>

            {creatingTask ? (
              <TaskCreate
                gateway={taskCreationGateway}
                onCreated={openTask}
              />
            ) : showingPlatform ? (
              <div className="platform-session-content">
                <PlatformSessions gateway={platformSessionGateway} />
              </div>
            ) : showingDiagnostics ? (
              <Space orientation="vertical" size="large" className="settings-stack">
                <BrowserSettings platform={platformAdapter} />
                <Diagnostics platform={platformAdapter} />
              </Space>
            ) : showingTaskRun && selectedTaskId !== null ? (
              <TaskRunDetails
                taskId={selectedTaskId}
                taskSource={taskSource}
                controlGateway={taskRunControlGateway}
                onBack={() => setActivePage("workbench")}
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
