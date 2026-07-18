import { Badge, Flex, Layout, Menu, Space, Tag, Typography } from "antd";

import {
  TaskProjectionSourceError,
  type TaskProjectionSource,
} from "../api/control-plane/task-projections";
import { Workbench } from "../features/workbench/Workbench";
import type { WorkbenchGateway } from "../features/workbench/workbench-gateway";

const navigationItems = [
  { key: "workbench", label: "工作台" },
  { key: "task-create", label: "新建任务", disabled: true },
  { key: "task-runs", label: "任务记录", disabled: true },
  { key: "platform", label: "平台状态", disabled: true },
  { key: "diagnostics", label: "设置与诊断", disabled: true },
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

interface WorkbenchShellProps {
  readonly taskSource?: TaskProjectionSource | undefined;
  readonly gateway?: WorkbenchGateway | undefined;
}

export function WorkbenchShell({
  taskSource = shellTaskSource,
  gateway = shellWorkbenchGateway,
}: WorkbenchShellProps) {
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
          <Menu mode="inline" selectedKeys={["workbench"]} items={navigationItems} />
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
                <Typography.Title level={2}>RPA 运营工作台</Typography.Title>
                <Typography.Text type="secondary">
                  从一个真实平台、一个任务闭环开始，执行过程可见、可暂停、可接管。
                </Typography.Text>
              </Space>
              <Tag variant="filled" color="green">
                工作台已就绪
              </Tag>
            </Flex>

            <Workbench taskSource={taskSource} gateway={gateway} />
          </main>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
