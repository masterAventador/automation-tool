import { Alert, Button, Card, Descriptions, Flex, Modal, Space, Spin, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import type { ExecutorManagerStatus, PlatformAdapter } from "../../platform/types";

const SAFE_FAILURE_MESSAGE = "暂时无法读取本地执行器状态。请稍后重试。";

function statusLabel(status: ExecutorManagerStatus): string {
  switch (status.state) {
    case "running":
      return "本地执行器运行中";
    case "restarting":
      return "本地执行器正在恢复";
    case "stopped":
      return "本地执行器已停止";
  }
}

function statusColor(status: ExecutorManagerStatus): "green" | "gold" | "default" {
  switch (status.state) {
    case "running":
      return "green";
    case "restarting":
      return "gold";
    case "stopped":
      return "default";
  }
}

interface DiagnosticsProps {
  readonly platform: PlatformAdapter;
}

export function Diagnostics({ platform }: DiagnosticsProps) {
  const [status, setStatus] = useState<ExecutorManagerStatus | null>(null);
  const [diagnostics, setDiagnostics] = useState<readonly string[]>([]);
  const [failure, setFailure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmingStop, setConfirmingStop] = useState(false);

  useEffect(() => {
    let active = true;
    void Promise.all([platform.getExecutorStatus(), platform.getExecutorDiagnostics()])
      .then(([nextStatus, nextDiagnostics]) => {
        if (active) {
          setStatus(nextStatus);
          setDiagnostics(nextDiagnostics);
          setFailure(false);
        }
      })
      .catch(() => {
        if (active) {
          setFailure(true);
        }
      });
    return () => {
      active = false;
    };
  }, [platform]);

  const restart = async () => {
    setBusy(true);
    setFailure(false);
    try {
      setStatus(await platform.restartExecutor());
      setDiagnostics(await platform.getExecutorDiagnostics());
    } catch {
      setFailure(true);
    } finally {
      setBusy(false);
    }
  };

  const emergencyStop = async () => {
    setBusy(true);
    setFailure(false);
    try {
      setStatus(await platform.emergencyStopExecutor());
      setConfirmingStop(false);
    } catch {
      setFailure(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="diagnostics-content" aria-label="本地执行器诊断">
      {failure ? <Alert type="error" showIcon message={SAFE_FAILURE_MESSAGE} /> : null}
      <Card className="diagnostics-status-card">
        {status === null && !failure ? (
          <Flex justify="center" align="center" className="diagnostics-loading">
            <Spin description="正在读取本地执行器状态" />
          </Flex>
        ) : status === null ? null : (
          <Space orientation="vertical" size="large" className="diagnostics-stack">
            <Flex justify="space-between" align="center" gap={16}>
              <Space orientation="vertical" size={4}>
                <Typography.Title level={3}>{statusLabel(status)}</Typography.Title>
                <Typography.Text type="secondary">
                  这里管理 App 自己启动的本地执行器，不会接触你的日常浏览器 Profile。
                </Typography.Text>
              </Space>
              <Tag color={statusColor(status)}>{status.state}</Tag>
            </Flex>
            <Descriptions column={2} size="small">
              <Descriptions.Item label="版本">{status.version ?? "未启动"}</Descriptions.Item>
              <Descriptions.Item label="构建">{status.buildId ?? "未加载"}</Descriptions.Item>
              <Descriptions.Item label="自动恢复次数">{status.restartCount}</Descriptions.Item>
            </Descriptions>
            <Flex gap={12} wrap>
              <Button type="primary" loading={busy} onClick={() => void restart()}>
                {status.state === "stopped" ? "启动执行器" : "重启执行器"}
              </Button>
              <Button danger disabled={busy} onClick={() => setConfirmingStop(true)}>
                本地紧急停止
              </Button>
            </Flex>
          </Space>
        )}
      </Card>

      <Card title="安全诊断记录" className="diagnostics-log-card">
        {diagnostics.length === 0 ? (
          <Typography.Text type="secondary">暂无本地执行器诊断记录。</Typography.Text>
        ) : (
          <ol className="diagnostics-lines">
            {diagnostics.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ol>
        )}
      </Card>

      <Modal
        title="仅停止本机执行器进程树"
        open={confirmingStop}
        confirmLoading={busy}
        okText="确认停止"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        onOk={() => void emergencyStop()}
        onCancel={() => setConfirmingStop(false)}
      >
        <Typography.Paragraph>
          此操作会立即终止 App 管理的本地执行器进程树；它不是业务任务的协作式紧停，也不会宣称远端副作用已经完成。
        </Typography.Paragraph>
      </Modal>
    </section>
  );
}
