import {
  Alert,
  Button,
  Card,
  Descriptions,
  Flex,
  Modal,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
} from "antd";
import { useEffect, useState } from "react";

import type {
  DiagnosticExportReceipt,
  ExecutorManagerStatus,
  PlatformAdapter,
} from "../../platform/types";

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
  const [captureSuccessfulRuns, setCaptureSuccessfulRuns] = useState(false);
  const [failure, setFailure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmingStop, setConfirmingStop] = useState(false);
  const [exportReceipt, setExportReceipt] = useState<DiagnosticExportReceipt | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([
      platform.getExecutorStatus(),
      platform.getExecutorDiagnostics(),
      platform.getBrowserDiagnosticSettings(),
    ])
      .then(([nextStatus, nextDiagnostics, settings]) => {
        if (active) {
          setStatus(nextStatus);
          setDiagnostics(nextDiagnostics);
          setCaptureSuccessfulRuns(settings.captureSuccessfulRuns);
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

  const refresh = async () => {
    setBusy(true);
    setFailure(false);
    try {
      const [nextStatus, nextDiagnostics] = await Promise.all([
        platform.getExecutorStatus(),
        platform.getExecutorDiagnostics(),
      ]);
      setStatus(nextStatus);
      setDiagnostics(nextDiagnostics);
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

  const updateSuccessfulDiagnostics = async (enabled: boolean) => {
    setBusy(true);
    setFailure(false);
    try {
      const settings = await platform.setCaptureSuccessfulDiagnostics(enabled);
      setCaptureSuccessfulRuns(settings.captureSuccessfulRuns);
    } catch {
      setFailure(true);
    } finally {
      setBusy(false);
    }
  };

  const exportDiagnostics = async () => {
    setBusy(true);
    setFailure(false);
    setExportReceipt(null);
    try {
      setExportReceipt(await platform.exportDiagnostics());
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
              <Button disabled={busy} onClick={() => void refresh()}>
                刷新状态
              </Button>
              <Button danger disabled={busy} onClick={() => setConfirmingStop(true)}>
                本地紧急停止
              </Button>
            </Flex>
          </Space>
        )}
      </Card>

      <Card title="安全诊断记录" className="diagnostics-log-card">
        {exportReceipt === null ? null : (
          <Alert
            type="success"
            showIcon
            message={`诊断包已保存：${exportReceipt.fileName}`}
            description={`共 ${exportReceipt.entryCount} 个受限文件，${exportReceipt.totalBytes} 字节。`}
          />
        )}
        {diagnostics.length === 0 ? (
          <Typography.Text type="secondary">暂无本地执行器诊断记录。</Typography.Text>
        ) : (
          <ol className="diagnostics-lines">
            {diagnostics.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ol>
        )}
        <div
          role="dialog"
          aria-label="导出本机诊断包"
          className="diagnostics-export-confirm"
        >
          <Typography.Title level={5}>导出诊断包</Typography.Title>
          <Typography.Paragraph>
            诊断包只包含脱敏执行器日志、页面结构漂移记录和脱敏浏览器截图，不包含登录凭据、完整评论或私信内容，也不会上传；文件会保存到系统下载目录。
          </Typography.Paragraph>
          <Flex gap={8} justify="end">
            <button
              id="confirm-diagnostic-export"
              type="button"
              className="diagnostics-export-confirm-button"
              disabled={busy}
              onClick={() => void exportDiagnostics()}
            >
              {busy ? "正在导出" : "确认导出"}
            </button>
          </Flex>
        </div>
      </Card>

      <Card title="浏览器诊断采集" className="diagnostics-log-card">
        <Flex justify="space-between" align="center" gap={16}>
          <Space orientation="vertical" size={2}>
            <Typography.Text>保存成功任务的脱敏诊断</Typography.Text>
            <Typography.Text type="secondary">
              失败任务始终保存；成功任务设置会在下次启动执行器时生效。
            </Typography.Text>
          </Space>
          <Switch
            aria-label="保存成功任务的脱敏诊断"
            checked={captureSuccessfulRuns}
            disabled={busy}
            onChange={(enabled) => void updateSuccessfulDiagnostics(enabled)}
          />
        </Flex>
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
