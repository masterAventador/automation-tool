import {
  Alert,
  Button,
  Card,
  Descriptions,
  Flex,
  Modal,
  Progress,
  Space,
  Tag,
  Typography,
} from "antd";
import { useEffect, useRef, useState } from "react";

import type {
  AppUpdateDecision,
  AppUpdateGateway,
  AppUpdateRelease,
  AppUpdateState,
} from "./contracts";

const STATE_REFRESH_INTERVAL_MILLISECONDS = 1_000;
const SAFE_FAILURE_MESSAGE = "暂时无法读取或操作 App 更新，请稍后重试。";

function releaseFrom(state: AppUpdateState | null): AppUpdateRelease | null {
  if (
    state === null ||
    state.state === "idle" ||
    state.state === "checking" ||
    state.state === "up_to_date" ||
    state.state === "failed"
  ) {
    return null;
  }
  return state.release;
}

type UpdateTagColor = "blue" | "green" | "gold" | "red" | "default";

type FailedAppUpdateState = Extract<AppUpdateState, { state: "failed" }>;

interface UpdateFailurePresentation {
  readonly text: string;
  readonly color: UpdateTagColor;
}

/**
 * 更新服务未配置和暂时连不上更新服务器都不是用户需要处理的错误。发布构建可以显式
 * 关闭更新，此时协调器不存在，原生层只能把这种情况回报成 configuration_invalid；
 * 界面挂载即轮询，用户什么都没点就会看到红色失败文案。
 *
 * 这两种情况改用中性和提示态，但文案仍然说明更新为什么不可用，不做静默处理。下载、
 * 验签、存储和安装的真实失败保持红色错误态；未来新增的失败码默认同样走红色，避免
 * 把真实失败悄悄降级。
 */
function failurePresentation(state: FailedAppUpdateState): UpdateFailurePresentation {
  switch (state.code) {
    case "configuration_invalid":
      return { text: "此版本未启用自动更新", color: "default" };
    case "transport_unavailable":
      return { text: "暂时无法连接更新服务器，可稍后重试", color: "gold" };
    default:
      return {
        text: state.retryable ? "更新暂时失败，可以重试" : "更新当前不可用",
        color: "red",
      };
  }
}

function statusText(state: AppUpdateState | null): string {
  if (state === null) return "正在读取更新状态";
  switch (state.state) {
    case "idle":
      return "尚未检查更新";
    case "checking":
      return "正在检查更新";
    case "up_to_date":
      return "当前已是最新版本";
    case "available":
      return "发现新版本，正在准备下载";
    case "downloading":
      return "正在下载更新";
    case "ready":
      switch (state.action) {
        case "prompt":
          return "新版本已准备好";
        case "deferred":
          return "已暂缓，将在下次检查时重新提示";
        case "skipped":
          return `已跳过版本 ${state.release.version}`;
        case "suppressed":
          return `版本 ${state.release.version} 已按你的选择跳过`;
        case "install_requested":
          return "将在下次启动时安装";
        case "forced":
          return "必须更新，下次启动时自动安装";
      }
      return "更新已准备好";
    case "installing":
      return "正在安全退出并安装更新";
    case "installation_launched":
      return "安装程序已启动";
    case "failed":
      return failurePresentation(state).text;
  }
}

function stateColor(state: AppUpdateState | null): UpdateTagColor {
  if (state === null) return "default";
  switch (state.state) {
    case "up_to_date":
      return "green";
    case "available":
    case "downloading":
    case "ready":
      return "gold";
    case "failed":
      return failurePresentation(state).color;
    case "checking":
    case "installing":
      return "blue";
    default:
      return "default";
  }
}

interface AppUpdateCenterProps {
  readonly gateway: AppUpdateGateway;
  readonly showSettings: boolean;
}

export function AppUpdateCenter({ gateway, showSettings }: AppUpdateCenterProps) {
  const [state, setState] = useState<AppUpdateState | null>(null);
  const [failure, setFailure] = useState(false);
  const [busy, setBusy] = useState(false);
  const readInFlight = useRef(false);
  const operationInFlight = useRef(false);

  useEffect(() => {
    let active = true;
    const read = async () => {
      if (readInFlight.current || operationInFlight.current) return;
      readInFlight.current = true;
      try {
        const next = await gateway.getState();
        if (active) {
          setState(next);
          setFailure(false);
        }
      } catch {
        if (active) setFailure(true);
      } finally {
        readInFlight.current = false;
      }
    };
    void read();
    const interval = globalThis.setInterval(read, STATE_REFRESH_INTERVAL_MILLISECONDS);
    return () => {
      active = false;
      globalThis.clearInterval(interval);
      readInFlight.current = false;
    };
  }, [gateway]);

  const runOperation = async (operation: () => Promise<AppUpdateState>) => {
    if (operationInFlight.current) return;
    operationInFlight.current = true;
    setBusy(true);
    setFailure(false);
    try {
      setState(await operation());
    } catch {
      setFailure(true);
    } finally {
      operationInFlight.current = false;
      setBusy(false);
    }
  };

  const checkNow = () => runOperation(() => gateway.checkNow());

  const decide = (decision: AppUpdateDecision) =>
    runOperation(() => gateway.decide(decision));

  const release = releaseFrom(state);
  const optionalPrompt = state?.state === "ready" && state.action === "prompt";
  const forcedPrompt = state?.state === "ready" && state.action === "forced";
  const checking = state?.state === "checking";
  const downloading = state?.state === "downloading";
  const totalBytes = downloading ? state.totalBytes : null;
  const progress =
    downloading && totalBytes !== null
      ? Math.min(100, Math.round((state.downloadedBytes / totalBytes) * 100))
      : null;

  return (
    <>
      <Modal
        open={optionalPrompt || forcedPrompt}
        closable={false}
        keyboard={false}
        mask={{ closable: false }}
        title={
          <Typography.Title level={3}>
            {forcedPrompt
              ? `必须更新到 ${release?.version ?? "新版本"}`
              : `发现新版本 ${release?.version ?? ""}`}
          </Typography.Title>
        }
        footer={
          optionalPrompt ? (
            <Flex justify="end" gap={8} wrap>
              <Button disabled={busy} onClick={() => void decide("skip_version")}>
                跳过此版本
              </Button>
              <Button disabled={busy} onClick={() => void decide("defer")}>
                稍后提醒
              </Button>
              <Button type="primary" loading={busy} onClick={() => void decide("install_now")}>
                立即安装
              </Button>
            </Flex>
          ) : null
        }
      >
        <Space orientation="vertical" size="middle" className="app-update-prompt">
          {forcedPrompt ? (
            <Alert
              type="warning"
              showIcon
              title="请重新启动 App，更新将在启动时自动安装。"
            />
          ) : null}
          {release?.notes ? <Typography.Paragraph>{release.notes}</Typography.Paragraph> : null}
          <Typography.Text type="secondary">
            安装前会安全停止本地执行器并释放运营浏览器；更新包会再次验签。
          </Typography.Text>
        </Space>
      </Modal>

      {showSettings ? (
        <Card className="app-update-card">
          <Space orientation="vertical" size="middle" className="app-update-settings">
            <Flex justify="space-between" align="center" gap={16} wrap>
              <Space orientation="vertical" size={2}>
                <Typography.Title level={3}>App 更新</Typography.Title>
                <Typography.Text type="secondary">
                  稳定版通道；检查、下载、验签和安装都由 App 内置的更新服务完成。
                </Typography.Text>
              </Space>
              <Tag color={stateColor(state)}>{statusText(state)}</Tag>
            </Flex>
            {failure ? <Alert type="error" showIcon title={SAFE_FAILURE_MESSAGE} /> : null}
            {release ? (
              <Descriptions column={2} size="small">
                <Descriptions.Item label="目标版本">{release.version}</Descriptions.Item>
                <Descriptions.Item label="更新策略">
                  {release.policy === "forced" ? "必须更新" : "可选更新"}
                </Descriptions.Item>
                <Descriptions.Item label="平台">{release.artifact.target}</Descriptions.Item>
                <Descriptions.Item label="架构">{release.artifact.arch}</Descriptions.Item>
              </Descriptions>
            ) : null}
            {downloading ? (
              progress === null ? (
                <Progress status="active" showInfo={false} />
              ) : (
                <Progress percent={progress} status="active" />
              )
            ) : null}
            <Flex gap={8} wrap>
              <Button
                type="primary"
                loading={busy || checking}
                disabled={state?.state === "installing"}
                onClick={() => void checkNow()}
              >
                检查更新
              </Button>
              {optionalPrompt ? (
                <Button disabled={busy} onClick={() => void decide("install_now")}>
                  立即安装
                </Button>
              ) : null}
            </Flex>
          </Space>
        </Card>
      ) : null}
    </>
  );
}
