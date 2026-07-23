import { Button, Card, Result, Space, Spin, Typography } from "antd";
import { useEffect, useState } from "react";

import type {
  StartupCheck,
  StartupCheckResult,
  StartupDiagnosticCode,
} from "./startup";

type StartupState = { status: "checking" } | StartupCheckResult;

const DIAGNOSTICS: Record<
  StartupDiagnosticCode,
  { readonly title: string; readonly description: string }
> = {
  installation_revoked: {
    title: "安装实例授权不可用",
    description: "此设备的演示授权已被吊销或失效，请联系演示管理员重新授权。",
  },
  control_plane_unavailable: {
    title: "Control Plane 不可用",
    description: "请检查本地服务或网络；诊断不会显示连接凭据或底层异常。",
  },
  executor_configuration_required: {
    title: "本地执行器动作配置缺失",
    description: "当前安装包没有完整的动作信任配置，请安装由管理员正式配置的版本。",
  },
  executor_unavailable: {
    title: "本地执行器不可用",
    description: "请使用下方本地修复工具检查执行器安装包和运行状态。",
  },
  browser_component_missing: {
    title: "浏览器组件缺失",
    description: "当前安装不完整，请重新安装官方客户端；无需也不要单独安装其他浏览器。",
  },
  browser_component_damaged: {
    title: "浏览器组件损坏",
    description: "组件完整性校验未通过，请重新安装官方客户端；请勿手动修改安装目录内容。",
  },
  browser_component_version_incompatible: {
    title: "浏览器组件版本不兼容",
    description: "组件版本与当前客户端不匹配，请下载并重新安装同一版本的官方客户端。",
  },
  app_data_unavailable: {
    title: "App 私有数据目录不可用",
    description: "请重启 App；如果仍未恢复，请保留脱敏诊断并重新安装客户端。",
  },
};

const LOCAL_DIAGNOSTICS = new Set<StartupDiagnosticCode>([
  "executor_configuration_required",
  "executor_unavailable",
  "browser_component_missing",
  "browser_component_damaged",
  "browser_component_version_incompatible",
  "app_data_unavailable",
]);

interface StartupGateProps {
  startupCheck: StartupCheck;
  repairTools?: React.ReactNode;
  children: React.ReactNode;
}

export function StartupGate({ startupCheck, repairTools, children }: StartupGateProps) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<StartupState>({ status: "checking" });
  const [showRepairTools, setShowRepairTools] = useState(false);

  useEffect(() => {
    let active = true;

    void startupCheck
      .check()
      .then((result) => {
        if (active) {
          setState(result);
        }
      })
      .catch(() => {
        if (active) {
          setState({ status: "unavailable" });
        }
      });

    return () => {
      active = false;
    };
  }, [attempt, startupCheck]);

  if (state.status === "ready") {
    return children;
  }

  if (state.status === "checking") {
    return (
      <main className="startup-screen" aria-label="桌面工作台启动检查">
        <Space orientation="vertical" size={20} align="center">
          <div className="brand-mark" aria-hidden="true">
            A
          </div>
          <Spin size="large" />
          <Space orientation="vertical" size={4} align="center">
            <Typography.Title level={3}>正在启动运营工作台</Typography.Title>
            <Typography.Text type="secondary">正在检查桌面运行环境</Typography.Text>
          </Space>
        </Space>
      </main>
    );
  }

  const diagnostics: readonly StartupDiagnosticCode[] =
    state.status === "blocked"
      ? state.diagnostics
      : state.status === "revoked"
        ? ["installation_revoked"]
        : ["control_plane_unavailable"];
  const revoked = diagnostics.includes("installation_revoked");
  const onlyControlPlane =
    diagnostics.length === 1 && diagnostics[0] === "control_plane_unavailable";
  const canOpenRepairTools =
    repairTools !== undefined && diagnostics.some((code) => LOCAL_DIAGNOSTICS.has(code));

  return (
    <main className="startup-screen startup-screen--diagnostic">
      <Result
        status="error"
        title={
          <Typography.Title level={2}>
            {revoked
              ? "当前安装实例已失效"
              : onlyControlPlane
                ? "暂时无法连接业务服务"
                : "桌面运行环境需要处理"}
          </Typography.Title>
        }
        subTitle={
          revoked
            ? "重新授权前不会启动业务功能；本机检查结果仍会安全列出。"
            : onlyControlPlane
              ? "桌面应用已启动，但 Control Plane 当前不可用。请检查本地服务或网络后重试。"
              : "业务功能保持关闭，处理下面的本机环境问题后重新检查。"
        }
        extra={
          <Space wrap>
            <Button
              type="primary"
              size="large"
              onClick={() => {
                setShowRepairTools(false);
                setState({ status: "checking" });
                setAttempt((value) => value + 1);
              }}
            >
              重新检查
            </Button>
            {canOpenRepairTools ? (
              <Button size="large" onClick={() => setShowRepairTools((value) => !value)}>
                {showRepairTools ? "收起本地修复工具" : "打开本地修复工具"}
              </Button>
            ) : null}
          </Space>
        }
      >
        <Space orientation="vertical" size="middle" className="settings-stack">
          {diagnostics.map((code) => (
            <Card className="diagnostic-card" size="small" key={code}>
              <Space orientation="vertical" size={6}>
                <Typography.Text strong>{DIAGNOSTICS[code].title}</Typography.Text>
                <Typography.Text type="secondary">
                  {DIAGNOSTICS[code].description}
                </Typography.Text>
              </Space>
            </Card>
          ))}
          {showRepairTools ? repairTools : null}
        </Space>
      </Result>
    </main>
  );
}
