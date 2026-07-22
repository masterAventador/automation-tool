import {
  Alert,
  Button,
  Card,
  Flex,
  Modal,
  Progress,
  Space,
  Spin,
  Typography,
} from "antd";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type {
  AppUpdateDecision,
  AppUpdateGateway,
  AppUpdateState,
} from "./contracts";

const DEFAULT_LOCAL_STATE_POLL_INTERVAL_MS = 1_000;
const SAFE_FAILURE_MESSAGE = "暂时无法读取更新状态。请稍后重试。";

interface AppUpdateContextValue {
  readonly state: AppUpdateState | null;
  readonly busy: boolean;
  readonly failure: boolean;
  checkNow(): Promise<void>;
  decide(decision: AppUpdateDecision): Promise<void>;
}

const AppUpdateContext = createContext<AppUpdateContextValue | null>(null);

interface AppUpdatesProps {
  readonly gateway?: AppUpdateGateway | undefined;
  readonly pollIntervalMs?: number | undefined;
  readonly children: ReactNode;
}

function isOperationState(state: AppUpdateState | null): boolean {
  return (
    state?.state === "checking" ||
    state?.state === "downloading" ||
    state?.state === "installing" ||
    state?.state === "installation_launched"
  );
}

export function AppUpdates({
  gateway,
  pollIntervalMs = DEFAULT_LOCAL_STATE_POLL_INTERVAL_MS,
  children,
}: AppUpdatesProps) {
  const [state, setState] = useState<AppUpdateState | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(false);
  const operationInProgress = useRef(false);

  useEffect(() => {
    if (gateway === undefined) {
      return;
    }
    let active = true;
    let reading = false;
    const refresh = async () => {
      if (reading || operationInProgress.current) {
        return;
      }
      reading = true;
      try {
        const nextState = await gateway.getState();
        if (active) {
          setState(nextState);
          setFailure(false);
        }
      } catch {
        if (active) {
          setFailure(true);
        }
      } finally {
        reading = false;
      }
    };
    void refresh();
    const timer = globalThis.setInterval(() => void refresh(), pollIntervalMs);
    return () => {
      active = false;
      globalThis.clearInterval(timer);
    };
  }, [gateway, pollIntervalMs]);

  const run = useCallback(async (operation: () => Promise<AppUpdateState>) => {
    if (operationInProgress.current) {
      return;
    }
    operationInProgress.current = true;
    setBusy(true);
    setFailure(false);
    try {
      setState(await operation());
    } catch {
      setFailure(true);
    } finally {
      operationInProgress.current = false;
      setBusy(false);
    }
  }, []);

  if (gateway === undefined) {
    return <AppUpdateContext.Provider value={null}>{children}</AppUpdateContext.Provider>;
  }

  const value: AppUpdateContextValue = {
    state,
    busy,
    failure,
    checkNow: () => run(() => gateway.checkNow()),
    decide: (decision) => run(() => gateway.decide(decision)),
  };

  const optionalPrompt =
    state?.state === "ready" && state.action === "prompt" ? state.release : null;
  const forcedReady =
    state?.state === "ready" && state.action === "forced" ? state.release : null;
  const downloading = state?.state === "downloading" ? state : null;
  const installing =
    state?.state === "installing" || state?.state === "installation_launched" ? state : null;

  return (
    <AppUpdateContext.Provider value={value}>
      {children}
      <>
        <Modal
          open={optionalPrompt !== null}
          title={optionalPrompt === null ? "软件更新" : `发现新版本 ${optionalPrompt.version}`}
          closable={false}
          mask={{ closable: false }}
          keyboard={false}
          footer={
            optionalPrompt === null
              ? null
              : [
                  <Button
                    key="skip"
                    disabled={busy}
                    onClick={() => void value.decide("skip_version")}
                  >
                    跳过此版本
                  </Button>,
                  <Button
                    key="defer"
                    disabled={busy}
                    onClick={() => void value.decide("defer")}
                  >
                    暂不安装
                  </Button>,
                  <Button
                    key="install"
                    type="primary"
                    loading={busy}
                    onClick={() => void value.decide("install_now")}
                  >
                    立即安装
                  </Button>,
                ]
          }
        >
          {optionalPrompt?.notes === null || optionalPrompt?.notes === undefined ? (
            <Typography.Text type="secondary">此更新已下载并通过签名验证。</Typography.Text>
          ) : (
            <Typography.Paragraph className="app-update-notes">
              {optionalPrompt.notes}
            </Typography.Paragraph>
          )}
        </Modal>

        {forcedReady === null ? null : (
          <Alert
            className="app-update-banner"
            type="warning"
            showIcon
            message="必须安装的更新已准备好"
            description="将在下次启动 App 时自动安装。"
          />
        )}
        {downloading === null ? null : (
          <Alert
            className="app-update-banner"
            type="info"
            showIcon
            message={`正在后台下载版本 ${downloading.release.version}`}
            description={
              <Progress
                {...(downloading.totalBytes === null
                  ? {}
                  : {
                      percent: Math.floor(
                        (downloading.downloadedBytes / downloading.totalBytes) * 100,
                      ),
                    })}
                status="active"
                showInfo={downloading.totalBytes !== null}
              />
            }
          />
        )}
        {installing === null ? null : (
          <Alert
            className="app-update-banner"
            type="info"
            showIcon
            message="正在进入安装流程"
            description={`版本 ${installing.release.version} 已通过验证，请勿强制结束 App。`}
          />
        )}
      </>
    </AppUpdateContext.Provider>
  );
}

function updateStatus(state: AppUpdateState | null): string {
  if (state === null) return "正在读取更新状态";
  switch (state.state) {
    case "idle":
      return "尚未检查更新";
    case "checking":
      return "正在检查更新";
    case "up_to_date":
      return "当前已是最新版本";
    case "available":
      return `发现版本 ${state.release.version}`;
    case "downloading":
      return `正在后台下载版本 ${state.release.version}`;
    case "ready":
      if (state.action === "deferred") return `版本 ${state.release.version} 已暂缓安装`;
      if (state.action === "skipped") {
        return `版本 ${state.release.version} 已跳过`;
      }
      if (state.action === "suppressed") return `版本 ${state.release.version} 已跳过，不再提示`;
      if (state.action === "forced") return `必须更新的版本 ${state.release.version} 已就绪`;
      return `版本 ${state.release.version} 已下载并通过验证`;
    case "installing":
    case "installation_launched":
      return `正在安装版本 ${state.release.version}`;
    case "failed":
      return "更新检查未完成";
  }
}

export function AppUpdateSettings() {
  const updates = useContext(AppUpdateContext);
  if (updates === null) {
    return null;
  }
  return (
    <Card title="软件更新" className="app-update-settings-card">
      <Space orientation="vertical" size="middle" className="app-update-settings-stack">
        <Typography.Text type="secondary">
          App 会在启动时和后台定期检查更新，也可以在这里主动检查。
        </Typography.Text>
        {updates.failure ? <Alert type="error" showIcon message={SAFE_FAILURE_MESSAGE} /> : null}
        {updates.state === null && !updates.failure ? (
          <Flex justify="center" align="center" className="app-update-settings-loading">
            <Spin description="正在读取更新状态" />
          </Flex>
        ) : (
          <Typography.Text>{updateStatus(updates.state)}</Typography.Text>
        )}
        <div>
          <Button
            type="primary"
            loading={updates.busy || updates.state?.state === "checking"}
            disabled={isOperationState(updates.state)}
            onClick={() => void updates.checkNow()}
          >
            检查更新
          </Button>
        </div>
      </Space>
    </Card>
  );
}
