import { Alert, Button, Card, Flex, Popconfirm, Space, Spin, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";

import type {
  PlatformSessionAction,
  PlatformSessionGateway,
  PlatformSessionSnapshot,
} from "./platform-session-gateway";

const STATE_LABELS: Record<PlatformSessionSnapshot["state"], string> = {
  healthy: "登录正常",
  expired: "登录已过期",
  missing: "需要登录",
  risk: "需要人工处理",
  unknown: "尚未确认",
};

const STATE_COLORS: Record<PlatformSessionSnapshot["state"], string> = {
  healthy: "green",
  expired: "orange",
  missing: "orange",
  risk: "red",
  unknown: "default",
};

const ACTION_MESSAGES: Record<PlatformSessionAction["state"], string> = {
  awaiting_scan: "请在打开的运营浏览器中扫码登录。",
  awaiting_confirmation: "扫码成功，请在手机抖音中确认登录。",
  qr_expired: "二维码已过期，请重新打开登录处理。",
  handoff_required: "页面需要人工处理，请在运营浏览器中完成后重新检查。",
  login_required: "抖音仍未登录，请在运营浏览器中继续处理。",
  unknown: "暂时无法确认页面状态，请检查运营浏览器后重试。",
  healthy: "登录正常",
};

const HEALTH_PUBLICATION_ATTEMPTS = 100;
const HEALTH_PUBLICATION_INTERVAL_MILLISECONDS = 50;

async function readPublishedSnapshot(
  gateway: PlatformSessionGateway,
  action: PlatformSessionAction,
): Promise<PlatformSessionSnapshot> {
  for (let attempt = 0; attempt < HEALTH_PUBLICATION_ATTEMPTS; attempt += 1) {
    const latest = await gateway.getDouyinSession();
    if (action.state !== "healthy" || latest.state === "healthy") return latest;
    await new Promise((resolve) =>
      globalThis.setTimeout(resolve, HEALTH_PUBLICATION_INTERVAL_MILLISECONDS),
    );
  }
  throw new Error("authoritative platform health publication timed out");
}

interface PlatformSessionsProps {
  readonly gateway: PlatformSessionGateway;
  readonly autoOpenLogin?: boolean;
  readonly onAutoOpenConsumed?: () => void;
}

export function PlatformSessions({
  gateway,
  autoOpenLogin = false,
  onAutoOpenConsumed,
}: PlatformSessionsProps) {
  const [snapshot, setSnapshot] = useState<PlatformSessionSnapshot | null>(null);
  const [action, setAction] = useState<PlatformSessionAction | null>(null);
  const [failure, setFailure] = useState(false);
  const [pending, setPending] = useState<"open" | "recheck" | "logout" | null>(null);

  useEffect(() => {
    let active = true;
    void gateway
      .getDouyinSession()
      .then((value) => {
        if (active) {
          setSnapshot(value);
          setFailure(false);
        }
      })
      .catch(() => {
        if (active) setFailure(true);
      });
    return () => {
      active = false;
    };
  }, [gateway]);

  const run = useCallback(async (kind: "open" | "recheck") => {
    setPending(kind);
    setFailure(false);
    try {
      const result =
        kind === "open" ? await gateway.openDouyinLogin() : await gateway.recheckDouyinLogin();
      const latest = await readPublishedSnapshot(gateway, result);
      setSnapshot(latest);
      setAction(result);
    } catch {
      setFailure(true);
    } finally {
      setPending(null);
    }
  }, [gateway]);

  useEffect(() => {
    if (!autoOpenLogin) return;
    onAutoOpenConsumed?.();
    void Promise.resolve().then(() => run("open"));
  }, [autoOpenLogin, onAutoOpenConsumed, run]);

  const logout = async () => {
    setPending("logout");
    setFailure(false);
    try {
      const result = await gateway.logoutDouyinSession();
      setSnapshot(result);
      setAction(null);
    } catch {
      setFailure(true);
    } finally {
      setPending(null);
    }
  };

  return (
    <Card className="platform-session-card" title="抖音">
      <Space orientation="vertical" size="middle" className="platform-session-stack">
        <Typography.Text type="secondary">
          App 使用独立运营 Profile；登录与人工处理在系统浏览器窗口完成，状态由本机执行器检查并回报服务端。
        </Typography.Text>
        {failure ? (
          <Alert type="error" showIcon title="暂时无法读取抖音登录状态，请稍后重试。" />
        ) : null}
        {snapshot === null && !failure ? (
          <Flex className="platform-session-loading" justify="center">
            <Spin description="正在读取登录状态" />
          </Flex>
        ) : snapshot === null ? null : (
          <Flex align="center" gap={12} wrap>
            <Typography.Text strong>当前状态</Typography.Text>
            <Tag color={STATE_COLORS[snapshot.state]}>{STATE_LABELS[snapshot.state]}</Tag>
            <Typography.Text type="secondary">
              {snapshot.observedAt === null
                ? "尚无检查记录"
                : `最近检查：${new Date(snapshot.observedAt).toLocaleString("zh-CN")}`}
            </Typography.Text>
          </Flex>
        )}
        {action !== null ? (
          <Alert type="info" showIcon title={ACTION_MESSAGES[action.state]} />
        ) : null}
        <Space wrap>
          <Button
            type="primary"
            loading={pending === "open"}
            disabled={pending !== null}
            onClick={() => void run("open")}
          >
            打开登录处理
          </Button>
          <Button
            loading={pending === "recheck"}
            disabled={pending !== null}
            onClick={() => void run("recheck")}
          >
            我已处理，重新检查
          </Button>
          <Popconfirm
            title="确定安全注销抖音吗？"
            description="将停止本机抖音任务并删除此 App 专用的抖音登录 Profile。"
            okText="确认注销"
            cancelText="取消"
            onConfirm={() => logout()}
          >
            <Button danger loading={pending === "logout"} disabled={pending !== null}>
              安全注销
            </Button>
          </Popconfirm>
        </Space>
      </Space>
    </Card>
  );
}
