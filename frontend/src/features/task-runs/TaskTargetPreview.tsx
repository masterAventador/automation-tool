import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Flex,
  Popconfirm,
  Space,
  Tag,
  Typography,
} from "antd";
import { useEffect, useRef, useState } from "react";

import { taskProjectionKeys } from "../../api/control-plane/task-projections";
import {
  TaskTargetPreviewSourceError,
  type TaskTargetPreview,
  type TaskTargetPreviewSource,
} from "../../api/control-plane/task-target-previews";

const TARGET_PREVIEW_LIMIT = 100;

const DISPOSITION_LABELS: Record<
  TaskTargetPreview["items"][number]["disposition"],
  string
> = {
  eligible: "可执行",
  duplicate_in_task: "本任务重复",
  duplicate_in_history: "30 天内已触达",
  blacklisted: "黑名单",
};

const DISPOSITION_COLORS: Record<
  TaskTargetPreview["items"][number]["disposition"],
  string
> = {
  eligible: "green",
  duplicate_in_task: "default",
  duplicate_in_history: "gold",
  blacklisted: "red",
};

interface TaskTargetPreviewPanelProps {
  readonly taskId: string;
  readonly source: TaskTargetPreviewSource;
  readonly onConfirmed?: () => void;
}

interface ExclusionRequest {
  readonly pageRevision: number;
  readonly taskRevision: number;
  readonly excludedTargetIds: readonly string[];
}

interface ConfirmationRequest {
  readonly pageRevision: number;
  readonly taskRevision: number;
}

interface IdempotencyEntry {
  readonly fingerprint: string;
  readonly key: string;
}

function targetPreviewKey(taskId: string): readonly [string, string] {
  return ["task-target-preview", taskId];
}

function exclusionFingerprint(request: ExclusionRequest): string {
  return [
    request.pageRevision,
    request.taskRevision,
    [...request.excludedTargetIds].sort().join(","),
  ].join(":");
}

function confirmationFingerprint(request: ConfirmationRequest): string {
  return `${request.pageRevision}:${request.taskRevision}`;
}

function currentExcludedTargetIds(preview: TaskTargetPreview): readonly string[] {
  return preview.items
    .filter((item) => item.disposition === "eligible" && item.userExcluded)
    .map((item) => item.targetId);
}

function safeFailureNotice(error: unknown): string {
  if (error instanceof TaskTargetPreviewSourceError && error.code === "preview_stale") {
    return "目标列表已变化，已重新加载最新版本";
  }
  return "目标选择结果暂时无法确认，请重新核对后重试";
}

export function TaskTargetPreviewPanel({
  taskId,
  source,
  onConfirmed,
}: TaskTargetPreviewPanelProps) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const exclusionKey = useRef<IdempotencyEntry | null>(null);
  const confirmationKey = useRef<IdempotencyEntry | null>(null);
  const mutationAbort = useRef<AbortController | null>(null);
  const previewQuery = useQuery({
    queryKey: targetPreviewKey(taskId),
    queryFn: async ({ signal }) => {
      const preview = await source.getPreview({ taskId, cursor: null, limit: TARGET_PREVIEW_LIMIT, signal });
      if (preview.nextCursor !== null) {
        throw new TaskTargetPreviewSourceError("protocol_mismatch", false);
      }
      return preview;
    },
    retry: false,
  });

  useEffect(
    () => () => {
      mutationAbort.current?.abort();
    },
    [],
  );

  const replaceExclusions = useMutation({
    mutationFn: async (request: ExclusionRequest) => {
      const fingerprint = exclusionFingerprint(request);
      if (exclusionKey.current?.fingerprint !== fingerprint) {
        exclusionKey.current = {
          fingerprint,
          key: `task-targets:exclude:${globalThis.crypto.randomUUID()}`,
        };
      }
      const controller = new AbortController();
      mutationAbort.current = controller;
      return source.replaceExclusions({
        taskId,
        pageRevision: request.pageRevision,
        expectedTaskRevision: request.taskRevision,
        excludedTargetIds: request.excludedTargetIds,
        idempotencyKey: exclusionKey.current.key,
        signal: controller.signal,
      });
    },
    onSuccess: (preview) => {
      exclusionKey.current = null;
      confirmationKey.current = null;
      setNotice("目标选择已保存");
      queryClient.setQueryData(targetPreviewKey(taskId), preview);
    },
    onError: async (error) => {
      setNotice(safeFailureNotice(error));
      if (error instanceof TaskTargetPreviewSourceError && error.code === "preview_stale") {
        await previewQuery.refetch();
      }
    },
    onSettled: () => {
      mutationAbort.current = null;
    },
  });

  const confirm = useMutation({
    mutationFn: async (request: ConfirmationRequest) => {
      const fingerprint = confirmationFingerprint(request);
      if (confirmationKey.current?.fingerprint !== fingerprint) {
        confirmationKey.current = {
          fingerprint,
          key: `task-targets:confirm:${globalThis.crypto.randomUUID()}`,
        };
      }
      const controller = new AbortController();
      mutationAbort.current = controller;
      return source.confirm({
        taskId,
        pageRevision: request.pageRevision,
        expectedTaskRevision: request.taskRevision,
        idempotencyKey: confirmationKey.current.key,
        signal: controller.signal,
      });
    },
    onSuccess: async (preview) => {
      confirmationKey.current = null;
      setNotice("目标已确认，任务已进入执行队列");
      queryClient.setQueryData(targetPreviewKey(taskId), preview);
      onConfirmed?.();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: taskProjectionKeys.detail(taskId) }),
        queryClient.invalidateQueries({ queryKey: taskProjectionKeys.lists() }),
      ]);
    },
    onError: async (error) => {
      setNotice(safeFailureNotice(error));
      if (error instanceof TaskTargetPreviewSourceError && error.code === "preview_stale") {
        await previewQuery.refetch();
      }
    },
    onSettled: () => {
      mutationAbort.current = null;
    },
  });

  if (previewQuery.isPending) {
    return <Card className="task-target-preview-card" loading />;
  }

  if (previewQuery.isError) {
    return (
      <Card className="task-target-preview-card">
        <Alert
          type="warning"
          showIcon
          title="目标预览暂时不可用"
          description="没有显示平台原文、凭据或底层异常；可安全重新读取最新目标。"
          action={<Button onClick={() => void previewQuery.refetch()}>重新加载目标</Button>}
        />
      </Card>
    );
  }

  const preview = previewQuery.data;
  const policyBlockedCount = preview.items.filter(
    (item) => item.disposition !== "eligible",
  ).length;
  const busy = replaceExclusions.isPending || confirm.isPending;
  const editingDisabled = preview.confirmed || busy;

  const updateExclusions = (excludedTargetIds: readonly string[]) => {
    setNotice(null);
    replaceExclusions.mutate({
      pageRevision: preview.pageRevision,
      taskRevision: preview.taskRevision,
      excludedTargetIds,
    });
  };

  const toggleTarget = (targetId: string, selected: boolean) => {
    const excluded = new Set(currentExcludedTargetIds(preview));
    if (selected) {
      excluded.delete(targetId);
    } else {
      excluded.add(targetId);
    }
    updateExclusions([...excluded]);
  };

  const excludeAll = () => {
    updateExclusions(
      preview.items
        .filter((item) => item.disposition === "eligible")
        .map((item) => item.targetId),
    );
  };

  const restoreAll = () => {
    updateExclusions([]);
  };

  return (
    <Card className="task-target-preview-card">
      <Space orientation="vertical" size={16} className="task-target-preview-stack">
        <Flex justify="space-between" align="center" gap={16} wrap>
          <Space orientation="vertical" size={2}>
            <Typography.Title level={4}>目标预览</Typography.Title>
            <Typography.Text type="secondary">
              仅展示执行所需的最小公开摘要；确认前不会产生评论或私信。
            </Typography.Text>
          </Space>
          <Tag color={preview.confirmed ? "green" : "blue"}>
            {preview.confirmed ? "已确认" : `候选版本 ${preview.pageRevision}`}
          </Tag>
        </Flex>

        <Flex className="task-target-preview-summary" gap={12} wrap>
          <Typography.Text>已发现 {preview.items.length} 个目标</Typography.Text>
          <Typography.Text>计划执行 {preview.selectedTargetCount} 个</Typography.Text>
          <Typography.Text>本次排除 {preview.userExcludedTargetCount} 个</Typography.Text>
          <Typography.Text>策略拦截 {policyBlockedCount} 个</Typography.Text>
        </Flex>

        {preview.items.length === 0 ? (
          <Empty description="没有可预览目标" />
        ) : (
          <ul className="task-target-preview-list">
            {preview.items.map((item) => {
              const policyExcluded = item.disposition !== "eligible";
              return (
                <li key={item.targetId}>
                  <Checkbox
                    aria-label={
                      policyExcluded
                        ? `策略已排除 ${item.displayName}`
                        : `选择目标 ${item.displayName}`
                    }
                    checked={item.selected}
                    disabled={editingDisabled || policyExcluded}
                    onChange={(event) => toggleTarget(item.targetId, event.target.checked)}
                  />
                  <Space orientation="vertical" size={0} className="task-target-preview-identity">
                    <Typography.Text strong>{item.displayName}</Typography.Text>
                    <Typography.Text type="secondary">
                      {item.publicHandle === null ? "未提供公开号" : `@${item.publicHandle}`}
                    </Typography.Text>
                  </Space>
                  <Typography.Text type="secondary">抖音通用搜索作者</Typography.Text>
                  <Tag color={DISPOSITION_COLORS[item.disposition]}>
                    {item.userExcluded ? "本次已排除" : DISPOSITION_LABELS[item.disposition]}
                  </Tag>
                </li>
              );
            })}
          </ul>
        )}

        <Flex justify="space-between" gap={12} wrap>
          <Space>
            <Button
              disabled={editingDisabled || preview.selectedTargetCount === 0}
              onClick={excludeAll}
            >
              全部取消
            </Button>
            <Button
              disabled={editingDisabled || preview.userExcludedTargetCount === 0}
              onClick={restoreAll}
            >
              恢复全部
            </Button>
          </Space>
          <Popconfirm
            title="确认目标并进入执行队列？"
            description={`将按当前选择执行 ${preview.selectedTargetCount} 个目标，确认后不能在本页修改。`}
            okText="确认目标"
            cancelText="继续检查"
            disabled={editingDisabled || preview.selectedTargetCount === 0}
            onConfirm={() => {
              setNotice(null);
              confirm.mutate({
                pageRevision: preview.pageRevision,
                taskRevision: preview.taskRevision,
              });
            }}
          >
            <Button
              type="primary"
              loading={confirm.isPending}
              disabled={editingDisabled || preview.selectedTargetCount === 0}
            >
              确认执行
            </Button>
          </Popconfirm>
        </Flex>

        {notice === null ? null : (
          <Alert
            className="task-target-preview-notice"
            type={preview.confirmed ? "success" : "info"}
            showIcon
            title={notice}
          />
        )}
      </Space>
    </Card>
  );
}
