import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Popconfirm,
  Progress,
  Space,
  Tag,
  Timeline,
  Typography,
} from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  parseTaskEvent,
  taskProjectionKeys,
  taskSnapshotQueryOptions,
  type TaskEvent,
  type TaskProjectionSource,
  type TaskSnapshot,
  type TaskStatus,
} from "../../api/control-plane/task-projections";
import type { TaskTargetPreviewSource } from "../../api/control-plane/task-target-previews";
import {
  taskTargetResultQueryOptions,
  type TaskTargetResultEvidence,
  type TaskTargetResultSource,
  type TaskTargetResultStatus,
} from "../../api/control-plane/task-target-results";
import { TaskTargetPreviewPanel } from "./TaskTargetPreview";
import {
  TaskDiscoveryGatewayError,
  type TaskDiscoveryGateway,
} from "./task-discovery";
import type {
  TaskRunControlGateway,
  TaskRunControlOperation,
  TaskRunControlReceipt,
} from "./task-run-controls";

const MAX_RETAINED_TIMELINE_EVENTS = 200;
const TARGET_RESULT_REFRESH_EVENTS = new Set<TaskEvent["eventType"]>([
  "step.completed",
  "step.failed",
  "task.completed",
  "task.partially_completed",
  "task.failed",
  "task.outcome_uncertain",
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
  cancelling: "正在取消",
  succeeded: "已成功",
  partially_succeeded: "部分成功",
  failed: "已失败",
  cancelled: "已取消",
  outcome_uncertain: "结果待确认",
};

const EVENT_LABELS: Record<TaskEvent["eventType"], string> = {
  "task.created": "任务创建",
  "task.validation_started": "开始校验",
  "task.validation_failed": "校验失败",
  "task.awaiting_platform_login": "等待平台登录",
  "task.discovery_started": "开始发现目标",
  "task.awaiting_confirmation": "等待确认",
  "task.target_selection_updated": "目标选择已更新",
  "task.targets_confirmed": "目标已确认",
  "task.started": "任务开始",
  "step.started": "步骤开始",
  "step.progress": "步骤进度",
  "step.completed": "步骤完成",
  "step.failed": "步骤失败",
  "task.awaiting_human": "等待人工处理",
  "task.paused": "任务已暂停",
  "task.resumed": "任务已恢复",
  "task.cancelling": "正在取消",
  "task.cancelled": "任务已取消",
  "task.completed": "任务完成",
  "task.partially_completed": "任务部分完成",
  "task.failed": "任务失败",
  "task.outcome_uncertain": "结果待确认",
};

const COMMAND_LABELS: Record<TaskRunControlOperation, string> = {
  pause: "暂停",
  resume: "恢复",
  cancel: "取消",
  emergency_stop: "紧停",
};

const TERMINAL_STATUSES = new Set<TaskStatus>([
  "succeeded",
  "partially_succeeded",
  "failed",
  "cancelled",
  "outcome_uncertain",
]);

const CANCELLABLE_STATUSES = new Set<TaskStatus>([
  "awaiting_platform_login",
  "discovering_targets",
  "awaiting_confirmation",
  "queued",
  "running",
  "paused",
  "awaiting_human",
]);

const EMERGENCY_STOPPABLE_STATUSES = new Set<TaskStatus>([
  "running",
  "paused",
  "awaiting_human",
]);

const DISCOVERY_STARTABLE_STATUSES = new Set<TaskStatus>([
  "draft",
  "awaiting_platform_login",
  "awaiting_confirmation",
  "awaiting_human",
]);

interface TaskRunDetailsProps {
  readonly taskId: string;
  readonly taskSource: TaskProjectionSource;
  readonly controlGateway: TaskRunControlGateway;
  readonly taskTargetPreviewSource: TaskTargetPreviewSource;
  readonly taskTargetResultSource: TaskTargetResultSource;
  readonly discoveryGateway: TaskDiscoveryGateway;
  readonly onBack: () => void;
  readonly onOpenPlatformSession: () => void;
  readonly onPlatformLoginRequired: () => void;
}

interface ControlRequest {
  readonly operation: TaskRunControlOperation;
  readonly baselineRevision: number;
}

interface SubmittedControl extends ControlRequest {
  readonly receipt: TaskRunControlReceipt;
}

function statusColor(status: TaskStatus): string {
  if (status === "succeeded") return "green";
  if (status === "failed" || status === "outcome_uncertain") return "red";
  if (status === "awaiting_human" || status === "partially_succeeded") return "gold";
  if (status === "cancelled") return "default";
  return "blue";
}

function projectSnapshot(snapshot: TaskSnapshot, events: readonly TaskEvent[]): TaskSnapshot {
  let projected = snapshot;
  for (const event of events) {
    if (event.sequence <= projected.lastEventSequence) continue;
    if (
      event.sequence !== projected.lastEventSequence + 1 ||
      event.taskRevision <= projected.revision
    ) {
      return projected;
    }
    projected = {
      ...projected,
      status: event.taskStatus,
      revision: event.taskRevision,
      lastEventSequence: event.sequence,
      updatedAt: event.recordedAt,
    };
  }
  return projected;
}

const RESULT_LABELS: Record<TaskTargetResultStatus, string> = {
  pending: "待执行",
  running: "进行中",
  succeeded: "成功",
  skipped: "跳过",
  failed: "失败",
  outcome_uncertain: "结果不确定",
};

const EVIDENCE_LABELS: Record<TaskTargetResultEvidence, string> = {
  awaiting_execution: "目标已确认，等待执行",
  action_pending: "动作已授权，尚未发送",
  action_in_progress: "动作已发送，等待最终确认",
  profile_visible: "目标主页已确认可见",
  comment_confirmed: "平台页面已确认评论成功",
  message_confirmed: "平台页面已确认私信成功",
  executor_reported_success: "执行器已确认动作成功",
  user_excluded: "用户在预览中排除此目标",
  duplicate_in_task: "本任务内目标重复",
  duplicate_in_history: "近期任务已处理此目标",
  blacklisted: "目标命中黑名单",
  action_cancelled: "动作在执行前已取消",
  admission_rejected: "动作授权或本机准入被拒绝",
  local_safety_limit: "本机安全限额阻止动作",
  login_required: "平台登录状态需要人工处理",
  dialog_blocked: "平台风控或阻塞弹窗中断动作",
  messaging_not_allowed: "目标不允许主动私信",
  follow_required: "平台要求先关注目标",
  timed_out: "页面操作或确认超时",
  page_version_unknown: "页面版本无法安全识别",
  conflicting_anchors: "页面出现冲突状态标记",
  page_unavailable: "平台页面当前不可用",
  verification_unavailable: "最终确认写入失败",
  executor_reported_failure: "执行器已确认动作失败",
  dispatch_timed_out: "动作发送后响应超时",
  dispatch_unavailable: "动作发送后执行器不可用",
  final_state_unconfirmed: "已发送，但平台最终状态无法确认",
  recovery_unconfirmed: "崩溃恢复后仍无法确认最终状态",
};

function resultColor(status: TaskTargetResultStatus): string {
  if (status === "succeeded") return "green";
  if (status === "failed") return "red";
  if (status === "outcome_uncertain") return "gold";
  if (status === "skipped") return "default";
  return "blue";
}

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

async function invokeControl(
  gateway: TaskRunControlGateway,
  taskId: string,
  idempotencyKey: string,
  operation: TaskRunControlOperation,
  signal: AbortSignal,
): Promise<TaskRunControlReceipt> {
  const options = { signal };
  if (operation === "pause") return gateway.pauseTask(taskId, idempotencyKey, options);
  if (operation === "resume") return gateway.resumeTask(taskId, idempotencyKey, options);
  if (operation === "cancel") return gateway.cancelTask(taskId, idempotencyKey, options);
  return gateway.emergencyStopTask(taskId, idempotencyKey, options);
}

export function TaskRunDetails({
  taskId,
  taskSource,
  controlGateway,
  taskTargetPreviewSource,
  taskTargetResultSource,
  discoveryGateway,
  onBack,
  onOpenPlatformSession,
  onPlatformLoginRequired,
}: TaskRunDetailsProps) {
  const queryClient = useQueryClient();
  const taskQuery = useQuery(taskSnapshotQueryOptions(taskSource, taskId));
  const [events, setEvents] = useState<readonly TaskEvent[]>([]);
  const [streamError, setStreamError] = useState(false);
  const [streamGeneration, setStreamGeneration] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [discoveryNotice, setDiscoveryNotice] = useState<string | null>(null);
  const [confirmedPreviewTaskId, setConfirmedPreviewTaskId] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<SubmittedControl | null>(null);
  const commandKeys = useRef(
    new Map<TaskRunControlOperation, { revision: number; key: string }>(),
  );
  const commandAbort = useRef<AbortController | null>(null);
  const discoveryKey = useRef<{ revision: number; key: string } | null>(null);
  const discoveryAbort = useRef<AbortController | null>(null);
  const previousProjectedStatus = useRef<TaskStatus | null>(null);

  const targetResultEventSequence = useMemo(
    () =>
      events.reduce(
        (latest, event) =>
          TARGET_RESULT_REFRESH_EVENTS.has(event.eventType)
            ? Math.max(latest, event.sequence)
            : latest,
        0,
      ),
    [events],
  );
  const targetResultRefreshSequence = Math.max(
    targetResultEventSequence,
    taskQuery.data?.lastEventSequence ?? 0,
  );
  const targetResultQuery = useQuery(
    taskTargetResultQueryOptions(
      taskTargetResultSource,
      taskId,
      targetResultRefreshSequence,
    ),
  );

  useEffect(() => {
    const controller = new AbortController();
    let lastSequence = 0;

    const follow = async () => {
      while (!controller.signal.aborted) {
        const previousSequence = lastSequence;
        const summary = await taskSource.streamTaskEvents(
          taskId,
          previousSequence,
          (unknownEvent) => {
            if (controller.signal.aborted) return;
            let event: TaskEvent;
            try {
              event = parseTaskEvent(unknownEvent);
            } catch {
              setStreamError(true);
              controller.abort();
              return;
            }
            if (event.taskId !== taskId || event.sequence !== lastSequence + 1) {
              setStreamError(true);
              controller.abort();
              return;
            }
            if (event.eventType === "task.targets_confirmed") {
              setConfirmedPreviewTaskId(taskId);
            }
            lastSequence = event.sequence;
            setEvents((current) =>
              [...current, event].slice(-MAX_RETAINED_TIMELINE_EVENTS),
            );
          },
          { signal: controller.signal },
        );
        if (summary.lastSequence !== lastSequence) {
          setStreamError(true);
          return;
        }
        if (summary.terminal) return;
      }
    };

    void Promise.resolve()
      .then(async () => {
        if (controller.signal.aborted) return;
        setEvents([]);
        setStreamError(false);
        await follow();
      })
      .catch(() => {
        if (!controller.signal.aborted) setStreamError(true);
      });
    return () => controller.abort();
  }, [streamGeneration, taskId, taskSource]);

  useEffect(
    () => () => {
      commandAbort.current?.abort();
      discoveryAbort.current?.abort();
    },
    [],
  );

  const projectedSnapshot = useMemo(
    () => (taskQuery.data === undefined ? null : projectSnapshot(taskQuery.data, events)),
    [events, taskQuery.data],
  );

  useEffect(() => {
    const status = projectedSnapshot?.status ?? null;
    const previous = previousProjectedStatus.current;
    previousProjectedStatus.current = status;
    if (
      previous !== null &&
      previous !== "awaiting_platform_login" &&
      status === "awaiting_platform_login"
    ) {
      onPlatformLoginRequired();
    }
  }, [onPlatformLoginRequired, projectedSnapshot?.status]);

  const controls = useMutation({
    mutationFn: async (request: ControlRequest) => {
      const existing = commandKeys.current.get(request.operation);
      const key =
        existing?.revision === request.baselineRevision
          ? existing.key
          : `task-run:${request.operation}:${globalThis.crypto.randomUUID()}`;
      commandKeys.current.set(request.operation, {
        revision: request.baselineRevision,
        key,
      });
      const controller = new AbortController();
      commandAbort.current = controller;
      const receipt = await invokeControl(
        controlGateway,
        taskId,
        key,
        request.operation,
        controller.signal,
      );
      return { ...request, receipt };
    },
    onSuccess: async (result) => {
      setSubmitted(result);
      setNotice(`${COMMAND_LABELS[result.operation]}命令已提交，等待本机执行器确认`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: taskProjectionKeys.detail(taskId) }),
        queryClient.invalidateQueries({ queryKey: taskProjectionKeys.lists() }),
      ]);
    },
    onError: () => {
      setNotice("命令结果暂时无法确认，请查看权威状态后重试");
    },
    onSettled: () => {
      commandAbort.current = null;
    },
  });

  const discovery = useMutation({
    mutationFn: async (baselineRevision: number) => {
      if (discoveryKey.current?.revision !== baselineRevision) {
        discoveryKey.current = {
          revision: baselineRevision,
          key: `task:discover:start:${globalThis.crypto.randomUUID()}`,
        };
      }
      const controller = new AbortController();
      discoveryAbort.current = controller;
      return discoveryGateway.startDiscovery(taskId, discoveryKey.current.key, {
        signal: controller.signal,
      });
    },
    onSuccess: async () => {
      discoveryKey.current = null;
      setDiscoveryNotice("目标发现命令已提交，等待本机执行器返回候选");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: taskProjectionKeys.detail(taskId) }),
        queryClient.invalidateQueries({ queryKey: taskProjectionKeys.lists() }),
      ]);
    },
    onError: (error) => {
      const errorCode = error instanceof TaskDiscoveryGatewayError ? error.code : null;
      if (errorCode === "discovery_rejected") onPlatformLoginRequired();
      setDiscoveryNotice(
        errorCode === "installation_busy"
          ? "当前设备已有任务正在运行，请先完成或终止该任务后再试"
          : errorCode === "discovery_rejected"
          ? "当前平台登录或任务状态尚未满足目标发现条件，请先处理平台状态后重试"
          : "目标发现结果暂时无法确认，请查看权威状态后重试",
      );
    },
    onSettled: () => {
      discoveryAbort.current = null;
    },
  });

  if (taskQuery.isError && taskQuery.data === undefined) {
    return (
      <Card className="task-run-state-card">
        <Alert
          type="warning"
          showIcon
          title="任务详情暂时不可用"
          description="没有显示底层错误或凭据；可安全重新读取权威快照。"
          action={<Button onClick={() => void taskQuery.refetch()}>重新加载详情</Button>}
        />
      </Card>
    );
  }

  if (taskQuery.isPending || projectedSnapshot === null) {
    return <Card className="task-run-state-card" loading />;
  }

  const status = projectedSnapshot.status;
  const latestProgress = [...events]
    .reverse()
    .find((event) => event.progressPercent !== null)?.progressPercent;
  const progress = status === "succeeded" ? 100 : (latestProgress ?? 0);
  const latestStep = [...events].reverse().find((event) => event.eventType.startsWith("step."));
  const results = targetResultQuery.data?.items ?? [];
  const busy = controls.isPending;
  const effectiveSubmitted =
    submitted !== null && submitted.baselineRevision >= projectedSnapshot.revision
      ? submitted
      : null;
  const terminationSubmitted =
    effectiveSubmitted?.operation === "cancel" ||
    effectiveSubmitted?.operation === "emergency_stop";

  const submit = (operation: TaskRunControlOperation) => {
    setNotice(null);
    controls.mutate({ operation, baselineRevision: projectedSnapshot.revision });
  };

  return (
    <Space className="task-run-content" orientation="vertical" size={16}>
      <Flex justify="space-between" align="center" gap={16} wrap>
        <Space orientation="vertical" size={2}>
          <Button type="link" className="task-run-back" onClick={onBack}>
            返回工作台
          </Button>
          <Typography.Title level={3}>任务运行详情</Typography.Title>
          <Typography.Text type="secondary">{taskId}</Typography.Text>
        </Space>
        <Tag color={statusColor(status)}>{STATUS_LABELS[status]}</Tag>
      </Flex>

      <Card title="运行概览">
        <Descriptions column={3} size="small">
          <Descriptions.Item label="当前状态">
            <Tag color={statusColor(status)}>{STATUS_LABELS[status]}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="当前步骤">
            {latestStep === undefined ? "尚无步骤事实" : EVENT_LABELS[latestStep.eventType]}
          </Descriptions.Item>
          <Descriptions.Item label="事件水位">
            {projectedSnapshot.lastEventSequence}
          </Descriptions.Item>
          <Descriptions.Item label="Revision">{projectedSnapshot.revision}</Descriptions.Item>
          <Descriptions.Item label="开始时间">
            {formatTimestamp(projectedSnapshot.createdAt)}
          </Descriptions.Item>
          <Descriptions.Item label="更新时间">
            {formatTimestamp(projectedSnapshot.updatedAt)}
          </Descriptions.Item>
        </Descriptions>
        <Progress percent={progress} status={status === "failed" ? "exception" : "normal"} />
      </Card>

      {DISCOVERY_STARTABLE_STATUSES.has(status) ? (
        <Card title="目标发现">
          <Space orientation="vertical" size="middle">
            <Typography.Text type="secondary">
              目标发现只读取抖音公开搜索结果；平台未登录、出现验证码或风控时不会继续操作。
            </Typography.Text>
            <Flex gap={10} wrap>
              <Button
                type="primary"
                loading={discovery.isPending}
                disabled={discovery.isPending}
                onClick={() => {
                  setDiscoveryNotice(null);
                  discovery.mutate(projectedSnapshot.revision);
                }}
              >
                {status === "awaiting_confirmation" ? "重新发现目标" : "开始目标发现"}
              </Button>
              {status === "awaiting_platform_login" || status === "awaiting_human" ? (
                <Button disabled={discovery.isPending} onClick={onOpenPlatformSession}>
                  打开平台状态
                </Button>
              ) : null}
            </Flex>
            {discoveryNotice === null ? null : (
              <Alert
                type="info"
                showIcon
                title={discoveryNotice}
                action={
                  discovery.isError ? (
                    <Button size="small" onClick={onOpenPlatformSession}>
                      打开平台状态
                    </Button>
                  ) : undefined
                }
              />
            )}
          </Space>
        </Card>
      ) : null}

      {status === "awaiting_confirmation" || confirmedPreviewTaskId === taskId ? (
        <TaskTargetPreviewPanel
          taskId={taskId}
          source={taskTargetPreviewSource}
          onConfirmed={() => setConfirmedPreviewTaskId(taskId)}
        />
      ) : null}

      <Card title="任务控制">
        <Flex gap={10} wrap>
          <Button
            disabled={
              busy ||
              terminationSubmitted ||
              effectiveSubmitted?.operation === "pause" ||
              status !== "running"
            }
            loading={busy && controls.variables?.operation === "pause"}
            onClick={() => submit("pause")}
          >
            暂停
          </Button>
          <Button
            disabled={
              busy ||
              terminationSubmitted ||
              effectiveSubmitted?.operation === "resume" ||
              status !== "paused"
            }
            loading={busy && controls.variables?.operation === "resume"}
            onClick={() => submit("resume")}
          >
            恢复
          </Button>
          <Popconfirm
            title="确认取消当前任务？"
            description="提交后仍以本机执行器确认的最终事实为准。"
            okText="确认取消"
            cancelText="继续运行"
            onConfirm={() => submit("cancel")}
            disabled={busy || terminationSubmitted || !CANCELLABLE_STATUSES.has(status)}
          >
            <Button
              danger
              disabled={busy || terminationSubmitted || !CANCELLABLE_STATUSES.has(status)}
              loading={busy && controls.variables?.operation === "cancel"}
            >
              取消任务
            </Button>
          </Popconfirm>
          <Popconfirm
            title="确认紧急停止当前任务？"
            description="动作结果无法确认时会进入结果待确认，不会伪报成功。"
            okText="确认紧停"
            cancelText="继续运行"
            onConfirm={() => submit("emergency_stop")}
            disabled={busy || terminationSubmitted || !EMERGENCY_STOPPABLE_STATUSES.has(status)}
          >
            <Button
              danger
              type="primary"
              disabled={busy || terminationSubmitted || !EMERGENCY_STOPPABLE_STATUSES.has(status)}
              loading={busy && controls.variables?.operation === "emergency_stop"}
            >
              紧急停止
            </Button>
          </Popconfirm>
        </Flex>
        {notice === null ? null : (
          <Alert className="command-notice" type="info" showIcon title={notice} />
        )}
      </Card>

      {streamError ? (
        <Alert
          type="warning"
          showIcon
          title="事件时间线暂时中断"
          description="页面保留最后一份权威快照；重试会从持久事件起点重新核对。"
          action={
            <Button onClick={() => setStreamGeneration((current) => current + 1)}>
              重新加载时间线
            </Button>
          }
        />
      ) : null}

      <Card title="目标结果">
        {targetResultQuery.isError ? (
          <Alert
            type="warning"
            showIcon
            title="目标结果暂时不可用"
            description="页面不会根据不完整事件猜测结果；可重新读取服务端权威事实。"
            action={<Button onClick={() => void targetResultQuery.refetch()}>重新加载结果</Button>}
          />
        ) : targetResultQuery.isPending ? (
          <Card loading variant="borderless" />
        ) : results.length === 0 ? (
          <Empty description="还没有已发现的目标" />
        ) : (
          <ul className="task-result-list">
            {results.map((result) => (
              <li key={result.targetId}>
                <Space orientation="vertical" size={0}>
                  <Typography.Text strong>{result.displayName}</Typography.Text>
                  {result.publicHandle === null ? null : (
                    <Typography.Text type="secondary">@{result.publicHandle}</Typography.Text>
                  )}
                  <Typography.Text type="secondary">
                    证据摘要：{EVIDENCE_LABELS[result.evidence]}
                  </Typography.Text>
                </Space>
                <Tag color={resultColor(result.resultStatus)}>
                  {RESULT_LABELS[result.resultStatus]}
                </Tag>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="事件时间线">
        {events.length === 0 ? (
          <Empty description="还没有已提交事件" />
        ) : (
          <Timeline
            items={events.map((event) => ({
              color: event.eventType.endsWith("failed") ? "red" : "blue",
              content: (
                <Space orientation="vertical" size={0}>
                  <Typography.Text strong>{EVENT_LABELS[event.eventType]}</Typography.Text>
                  <Typography.Text type="secondary">
                    #{event.sequence} · {formatTimestamp(event.recordedAt)}
                  </Typography.Text>
                  {event.message === null ? null : (
                    <Typography.Text>{event.message}</Typography.Text>
                  )}
                </Space>
              ),
            }))}
          />
        )}
      </Card>

      {TERMINAL_STATUSES.has(status) ? (
        <Alert type="success" showIcon title="任务已进入终态，控制按钮已关闭" />
      ) : null}
    </Space>
  );
}
