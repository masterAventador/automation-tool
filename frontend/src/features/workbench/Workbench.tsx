import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Empty,
  Flex,
  Popconfirm,
  Row,
  Space,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  taskListQueryOptions,
  taskProjectionKeys,
  type TaskProjectionSource,
  type TaskSnapshot,
  type TaskStatus,
} from "../../api/control-plane/task-projections";
import { followTaskProjection } from "../task-runs/task-projection-controller";
import {
  workbenchKeys,
  workbenchMetricsQueryOptions,
  workbenchRuntimeStatusQueryOptions,
  type WorkbenchGateway,
} from "./workbench-gateway";

const TERMINAL_STATUSES = new Set<TaskStatus>([
  "succeeded",
  "partially_succeeded",
  "failed",
  "cancelled",
  "outcome_uncertain",
]);

const STATUS_LABELS: Record<TaskStatus, string> = {
  draft: "草稿",
  validating: "校验中",
  awaiting_device: "等待执行器",
  awaiting_platform_login: "等待平台登录",
  discovering_targets: "发现目标中",
  awaiting_confirmation: "等待确认",
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  awaiting_human: "等待人工处理",
  cancelling: "正在紧停",
  succeeded: "已成功",
  partially_succeeded: "部分成功",
  failed: "已失败",
  cancelled: "已取消",
  outcome_uncertain: "结果待确认",
};

interface WorkbenchProps {
  readonly taskSource: TaskProjectionSource;
  readonly gateway: WorkbenchGateway;
  readonly onOpenTask?: (taskId: string) => void;
}

function statusColor(status: TaskStatus): string {
  if (status === "succeeded") return "green";
  if (status === "failed" || status === "outcome_uncertain") return "red";
  if (status === "awaiting_human") return "gold";
  if (status === "cancelled") return "default";
  return "blue";
}

function twoDigits(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * How a Task is named on the workbench.
 *
 * The projection carries no title: `taskSnapshotSchema` is `.strict()` over
 * taskId, status, revision, lastEventSequence, createdAt and updatedAt, and the
 * keyword the operator typed lives in a separate table the Task endpoints never
 * join. So the one fact in the snapshot a person can read is when the Task was
 * created, and that is what names it here until the contract carries a title.
 *
 * Seconds are kept: two Tasks created in the same minute would otherwise render
 * as two rows with identical text, which is its own defect.
 *
 * An unreadable timestamp falls back to the identifier — every production path
 * parses the snapshot through Zod before it reaches this component, so nothing
 * should arrive unreadable, but the identifier is exactly what this replaces and
 * so can never read worse than what was here before. `NaN-NaN NaN:NaN:NaN` can.
 */
function taskDisplayName(task: TaskSnapshot): string {
  const createdAt = new Date(task.createdAt);
  if (Number.isNaN(createdAt.getTime())) {
    return task.taskId;
  }
  const day = `${twoDigits(createdAt.getMonth() + 1)}-${twoDigits(createdAt.getDate())}`;
  const time = `${twoDigits(createdAt.getHours())}:${twoDigits(
    createdAt.getMinutes(),
  )}:${twoDigits(createdAt.getSeconds())}`;
  return `${day} ${time} 的任务`;
}

export function Workbench({
  taskSource,
  gateway,
  onOpenTask = () => undefined,
}: WorkbenchProps) {
  const queryClient = useQueryClient();
  const taskList = useQuery(taskListQueryOptions(taskSource, null, 20));
  const runtime = useQuery(workbenchRuntimeStatusQueryOptions(gateway));
  const metrics = useQuery(workbenchMetricsQueryOptions(gateway));
  const [currentProjection, setCurrentProjection] = useState<TaskSnapshot | null>(null);
  const [commandNotice, setCommandNotice] = useState<string | null>(null);
  const emergencyKey = useRef<{ taskId: string; key: string } | null>(null);

  const tasks = useMemo(() => taskList.data?.items ?? [], [taskList.data?.items]);
  const currentTask = useMemo(
    () => tasks.find((task) => !TERMINAL_STATUSES.has(task.status)) ?? null,
    [tasks],
  );

  useEffect(() => {
    if (currentTask === null) {
      return;
    }
    const controller = new AbortController();
    void followTaskProjection({
      queryClient,
      source: taskSource,
      taskId: currentTask.taskId,
      signal: controller.signal,
      onChange: (state) => {
        if (state.snapshot !== null) {
          setCurrentProjection(state.snapshot);
        }
        if (state.phase === "terminal") {
          void queryClient.invalidateQueries({ queryKey: taskProjectionKeys.lists() });
        }
      },
    }).catch(() => undefined);
    return () => {
      controller.abort();
    };
  }, [currentTask, queryClient, taskSource]);

  const emergencyStop = useMutation({
    mutationFn: async (task: TaskSnapshot) => {
      if (emergencyKey.current?.taskId !== task.taskId) {
        emergencyKey.current = {
          taskId: task.taskId,
          key: `workbench:emergency-stop:${crypto.randomUUID()}`,
        };
      }
      return gateway.emergencyStopTask(task.taskId, emergencyKey.current.key);
    },
    onSuccess: async (receipt) => {
      setCommandNotice("紧停命令已提交");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: taskProjectionKeys.detail(receipt.taskId) }),
        queryClient.invalidateQueries({ queryKey: taskProjectionKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: workbenchKeys.runtimeStatus() }),
        queryClient.invalidateQueries({ queryKey: workbenchKeys.metrics() }),
      ]);
    },
    onError: () => {
      setCommandNotice("紧停结果暂时无法确认，请查看任务状态");
    },
  });

  const unavailable = taskList.isError || runtime.isError || metrics.isError;
  const projectedTasks = tasks.map((task) =>
    currentProjection?.taskId === task.taskId && currentProjection.revision >= task.revision
      ? currentProjection
      : task,
  );
  const latestCurrent =
    currentProjection?.taskId === currentTask?.taskId ? currentProjection : currentTask;

  if (unavailable) {
    return (
      <Card className="workbench-state-card">
        <Alert
          type="warning"
          showIcon
          message="工作台数据暂时不可用"
          description="任务与运行状态没有更新；诊断不会显示凭据或底层异常。"
          action={
            <Button
              onClick={() => {
                void taskList.refetch();
                void runtime.refetch();
                void metrics.refetch();
              }}
            >
              重新加载工作台
            </Button>
          }
        />
      </Card>
    );
  }

  if (taskList.isPending || runtime.isPending || metrics.isPending) {
    return <Card className="workbench-state-card" loading />;
  }

  return (
    <Space className="workbench-content" orientation="vertical" size={16}>
      <Flex className="runtime-status-row" wrap gap={10}>
        <Tag color="green">控制服务已连接</Tag>
        <Tag color={runtime.data.executorStatus === "online" ? "green" : "default"}>
          {runtime.data.executorStatus === "online" ? "本机执行器在线" : "本机执行器离线"}
        </Tag>
      </Flex>

      <Row className="overview-grid" gutter={[16, 16]}>
        <Col span={6}>
          <Card><Statistic title="累计任务" value={metrics.data.tasks.total} /></Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="成功任务"
              value={metrics.data.tasks.succeeded}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="失败任务"
              value={metrics.data.tasks.failed}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="当前需接管"
              value={metrics.data.tasks.handoffRequired}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="任务结果待确认" value={metrics.data.tasks.outcomeUncertain} />
          </Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="成功动作" value={metrics.data.actions.succeeded} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="失败动作" value={metrics.data.actions.failed} /></Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="动作结果待确认" value={metrics.data.actions.outcomeUncertain} />
          </Card>
        </Col>
      </Row>

      <Card
        className="current-task-card"
        title={<Typography.Title level={4}>当前任务</Typography.Title>}
        extra={
          <Space>
            <Button
              disabled={latestCurrent === null}
              onClick={() => latestCurrent !== null && onOpenTask(latestCurrent.taskId)}
            >
              查看运行详情
            </Button>
            <Popconfirm
              title="确认紧急停止当前任务？"
              description="命令提交后仍以本机执行器返回的最终事实为准。"
              okText="确认紧停"
              cancelText="继续运行"
              onConfirm={() => latestCurrent !== null && emergencyStop.mutate(latestCurrent)}
              disabled={latestCurrent === null || latestCurrent.status === "cancelling"}
            >
              <Button
                danger
                type="primary"
                loading={emergencyStop.isPending}
                disabled={latestCurrent === null || latestCurrent.status === "cancelling"}
              >
                全局紧急停止
              </Button>
            </Popconfirm>
          </Space>
        }
      >
        {latestCurrent === null ? (
          <Empty description="还没有运行中的任务" />
        ) : (
          <>
            <Descriptions column={2} size="small">
              <Descriptions.Item label="任务">
                {taskDisplayName(latestCurrent)}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColor(latestCurrent.status)}>
                  {STATUS_LABELS[latestCurrent.status]}
                </Tag>
              </Descriptions.Item>
            </Descriptions>
            {/*
              Revision and 事件水位 are how an operator checks that the
              authoritative snapshot and the event projection agree, so they stay
              reachable — but folded, because they are a diagnosis and this is the
              largest card on the first screen the customer sees. The full
              identifier joins them: it is what correlates a Task with a log line,
              and nothing else on this page needs it.
            */}
            <Collapse
              ghost
              size="small"
              items={[
                {
                  key: "diagnostics",
                  label: "诊断信息",
                  children: (
                    <Descriptions column={2} size="small">
                      <Descriptions.Item label="Task ID">
                        {latestCurrent.taskId}
                      </Descriptions.Item>
                      <Descriptions.Item label="Revision">
                        {latestCurrent.revision}
                      </Descriptions.Item>
                      <Descriptions.Item label="事件水位">
                        {latestCurrent.lastEventSequence}
                      </Descriptions.Item>
                    </Descriptions>
                  ),
                },
              ]}
            />
          </>
        )}
        {commandNotice === null ? null : (
          <Alert className="command-notice" type="info" showIcon message={commandNotice} />
        )}
      </Card>

      <Card
        className="recent-tasks-card"
        title={<Typography.Title level={4}>最近任务</Typography.Title>}
      >
        {projectedTasks.length === 0 ? (
          <Empty description="还没有任务记录" />
        ) : (
          <ul className="recent-task-list">
            {projectedTasks.slice(0, 5).map((task) => (
              <li key={task.taskId}>
                <Button type="link" onClick={() => onOpenTask(task.taskId)}>
                  {taskDisplayName(task)}
                </Button>
                <Tag color={statusColor(task.status)}>{STATUS_LABELS[task.status]}</Tag>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Space>
  );
}
