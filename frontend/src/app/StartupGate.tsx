import { Button, Card, Result, Space, Spin, Typography } from "antd";
import { useEffect, useState } from "react";

import type { StartupCheck } from "./startup";

type StartupState = "checking" | "ready" | "unavailable" | "revoked";

interface StartupGateProps {
  startupCheck: StartupCheck;
  children: React.ReactNode;
}

export function StartupGate({ startupCheck, children }: StartupGateProps) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<StartupState>("checking");

  useEffect(() => {
    let active = true;

    void startupCheck
      .check()
      .then((result) => {
        if (active) {
          setState(result.status);
        }
      })
      .catch(() => {
        if (active) {
          setState("unavailable");
        }
      });

    return () => {
      active = false;
    };
  }, [attempt, startupCheck]);

  if (state === "ready") {
    return children;
  }

  if (state === "checking") {
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

  const revoked = state === "revoked";

  return (
    <main className="startup-screen startup-screen--diagnostic">
      <Result
        status="error"
        title={
          <Typography.Title level={2}>
            {revoked ? "当前安装实例已失效" : "暂时无法连接业务服务"}
          </Typography.Title>
        }
        subTitle={
          revoked
            ? "此设备的演示授权已被吊销或失效，请联系演示管理员重新授权。"
            : "桌面应用已启动，但 Control Plane 当前不可用。请检查本地服务或网络后重试。"
        }
        extra={
          <Button
            type="primary"
            size="large"
            onClick={() => {
              setState("checking");
              setAttempt((value) => value + 1);
            }}
          >
            重新检查
          </Button>
        }
      >
        <Card className="diagnostic-card" size="small">
          <Space orientation="vertical" size={6}>
            <Typography.Text strong>
              {revoked ? "安装实例授权不可用" : "Control Plane 不可用"}
            </Typography.Text>
            <Typography.Text type="secondary">
              {revoked
                ? "重新授权后可使用此按钮再次检查，不需要登录产品账号。"
                : "诊断信息不会显示连接凭据或底层异常。稍后可在“设置与诊断”中查看安全报告。"}
            </Typography.Text>
          </Space>
        </Card>
      </Result>
    </main>
  );
}
