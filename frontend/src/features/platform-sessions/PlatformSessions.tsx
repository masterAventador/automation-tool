import { Alert, Button, Card, Flex, Popconfirm, Space, Spin, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";

import { healthPublicationDelays } from "./health-publication-cadence";
import {
  PlatformSessionGatewayError,
  type PlatformSessionAction,
  type PlatformSessionGateway,
  type PlatformSessionSnapshot,
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

const READ_FAILURE_TEXT = "暂时无法读取抖音登录状态，请稍后重试。";

/**
 * T109: what the operator is told when one of the two buttons does not finish.
 *
 * `internal` means the fault is in this program or its installation, and no
 * amount of pressing the button again will change it. `temporary` means
 * something outside settled state and retrying is meaningful. `needs_user`
 * means there is a specific thing to do first. The distinction exists because
 * this page used to answer every one of them with a single "please retry
 * later", which is a lie in two cases out of three and the reason a real defect
 * took a forensic reconstruction to locate instead of one screenshot.
 */
type PlatformSessionFailureKind = "internal" | "temporary" | "needs_user";

interface PlatformSessionFailure {
  readonly kind: PlatformSessionFailureKind;
  readonly text: string;
  /** The closed-set native code, shown so a report names the branch that failed. */
  readonly code: string | null;
}

/**
 * Classify one failed platform-session operation.
 *
 * Anything that is not a recognised gateway failure keeps the shape of an
 * unknown failure rather than borrowing a neighbouring code's copy, and an
 * unrecognised code follows the native side's own `retryable` answer instead of
 * being quietly downgraded to "temporary" — the same rule T85 settled on for the
 * update centre.
 */
function failurePresentation(error: unknown): PlatformSessionFailure {
  if (!(error instanceof PlatformSessionGatewayError)) {
    return { kind: "temporary", text: READ_FAILURE_TEXT, code: null };
  }
  const code = error.code;
  switch (code) {
    case "browser_component_missing":
    case "browser_component_invalid":
    case "browser_component_version_incompatible":
      return {
        kind: "internal",
        text: "运营浏览器组件缺失或损坏，这是本产品安装包自身的问题，重新操作不会有效。请把下面的代码发给我们。",
        code,
      };
    case "package_rejected":
    case "authentication_rejected":
    case "configuration_invalid":
    case "storage_unavailable":
    case "protocol_mismatch":
    case "installation_conflict":
    case "operation_unavailable":
      return {
        kind: "internal",
        text: "抖音登录处理没有完成，这是本产品自身的问题，重新操作不会有效。请把下面的代码发给我们。",
        code,
      };
    case "profile_in_use":
      return {
        kind: "needs_user",
        text: "运营浏览器正在被占用。请先关掉已经打开的运营浏览器窗口，再重新操作。",
        code,
      };
    case "profile_identity_changed":
      return {
        kind: "internal",
        text: "运营浏览器档案被系统外的东西改动过，为安全起见没有继续。请把下面的代码发给我们。",
        code,
      };
    case "profile_missing":
      return {
        kind: "needs_user",
        text: "运营浏览器档案已经不在了，可能被手动清理过。重新登录一次抖音即可。",
        code,
      };
    case "profile_directory_unsafe":
      return {
        kind: "internal",
        text: "运营浏览器档案所在目录不安全，为安全起见没有继续。请把下面的代码发给我们。",
        code,
      };
    case "profile_marker_invalid":
      return {
        kind: "internal",
        text: "运营浏览器档案记录读不出来。请把下面的代码发给我们。",
        code,
      };
    case "profile_recovery_required":
      return {
        kind: "needs_user",
        text: "上一次运营浏览器没有正常收尾。请先执行「安全注销」，再重新登录一次抖音。",
        code,
      };
    case "credential_missing":
    case "installation_access_denied":
      return {
        kind: "needs_user",
        text: "本设备的授权已失效。请重新登录产品账号后再操作。",
        code,
      };
    case "process_unavailable":
      return { kind: "temporary", text: "本机执行器暂时不可用，可以稍后再操作一次。", code };
    case "timed_out":
      return { kind: "temporary", text: "本机执行器这次没有按时回应，可以稍后再操作一次。", code };
    case "already_running":
      return { kind: "temporary", text: "上一次操作还没结束，请等它完成后再操作。", code };
    case "transport_unavailable":
      return { kind: "temporary", text: "暂时连不上服务端，可以稍后再操作一次。", code };
    case "health_publication_timed_out":
      return {
        kind: "temporary",
        text: "本机已确认登录正常，但服务端状态还没同步过来。稍后刷新即可，不需要重复操作。",
        code,
      };
    default:
      return {
        kind: error.retryable ? "temporary" : "internal",
        text: error.retryable
          ? "抖音登录处理这次没有完成，可以稍后再操作一次。"
          : "抖音登录处理没有完成，重新操作不会有效。请把下面的代码发给我们。",
        code,
      };
  }
}

async function readPublishedSnapshot(
  gateway: PlatformSessionGateway,
  action: PlatformSessionAction,
): Promise<PlatformSessionSnapshot> {
  for (const delay of healthPublicationDelays()) {
    const latest = await gateway.getDouyinSession();
    if (action.state !== "healthy" || latest.state === "healthy") return latest;
    await new Promise((resolve) => globalThis.setTimeout(resolve, delay));
  }
  // The local check succeeded; only the authoritative projection lagged. Saying
  // "cannot read the login state" here would describe the opposite of what
  // happened and send the operator back to press the same button.
  throw new PlatformSessionGatewayError("health_publication_timed_out", true);
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
  const [failure, setFailure] = useState<PlatformSessionFailure | null>(null);
  const [pending, setPending] = useState<"open" | "recheck" | "logout" | null>(null);
  const autoOpenConsumed = useRef(false);

  useEffect(() => {
    let active = true;
    void gateway
      .getDouyinSession()
      .then((value) => {
        if (active) {
          setSnapshot(value);
          setFailure(null);
        }
      })
      .catch(() => {
        // A first read that never arrived says nothing about which branch of the
        // local handling would fail, so it keeps its own plain wording.
        if (active) setFailure({ kind: "temporary", text: READ_FAILURE_TEXT, code: null });
      });
    return () => {
      active = false;
    };
  }, [gateway]);

  const run = useCallback(async (kind: "open" | "recheck") => {
    setPending(kind);
    setFailure(null);
    try {
      const result =
        kind === "open" ? await gateway.openDouyinLogin() : await gateway.recheckDouyinLogin();
      const latest = await readPublishedSnapshot(gateway, result);
      setSnapshot(latest);
      setAction(result);
    } catch (error) {
      setFailure(failurePresentation(error));
    } finally {
      setPending(null);
    }
  }, [gateway]);

  useEffect(() => {
    if (!autoOpenLogin) {
      autoOpenConsumed.current = false;
      return;
    }
    if (autoOpenConsumed.current) return;
    autoOpenConsumed.current = true;
    onAutoOpenConsumed?.();
    void Promise.resolve().then(() => run("open"));
  }, [autoOpenLogin, onAutoOpenConsumed, run]);

  const logout = async () => {
    setPending("logout");
    setFailure(null);
    try {
      const result = await gateway.logoutDouyinSession();
      setSnapshot(result);
      setAction(null);
    } catch (error) {
      setFailure(failurePresentation(error));
    } finally {
      setPending(null);
    }
  };

  return (
    <Card className="platform-session-card" title="抖音">
      <Space orientation="vertical" size="middle" className="platform-session-stack">
        <Typography.Text type="secondary">
          App 使用独立运营浏览器档案；登录与人工处理在 App
          内置运营浏览器窗口完成，状态由本机执行器检查并回报服务端。
        </Typography.Text>
        {failure !== null ? (
          <Alert
            type={failure.kind === "temporary" ? "warning" : "error"}
            showIcon
            title={failure.text}
            description={failure.code === null ? undefined : `故障代码：${failure.code}`}
          />
        ) : null}
        {snapshot === null && failure === null ? (
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
            description="将停止本机抖音任务并删除此 App 专用的抖音登录浏览器档案。"
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
