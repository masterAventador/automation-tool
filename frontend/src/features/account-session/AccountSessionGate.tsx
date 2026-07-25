import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  List,
  Popconfirm,
  Result,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useEffect, useState, type ReactNode } from "react";

import {
  AccountSessionGatewayError,
  type AccountDevice,
  type AccountSessionGateway,
  type AccountSessionSnapshot,
} from "./account-session-gateway";

type GateState =
  | { readonly kind: "checking" }
  | { readonly kind: "offline" }
  | { readonly kind: "unauthenticated"; readonly notice?: string }
  | {
      readonly kind: "authenticated";
      readonly snapshot: Extract<AccountSessionSnapshot, { state: "authenticated" }>;
    };

interface AccountSessionGateProps {
  readonly gateway: AccountSessionGateway;
  readonly children: ReactNode;
}

function authenticationMessage(error: unknown): string {
  if (error instanceof AccountSessionGatewayError) {
    if (error.code === "authentication_invalid") {
      return "登录信息无效或账号暂不可用";
    }
    if (error.code === "recovery_invalid") {
      return "恢复票据无效或已失效";
    }
    if (error.code === "session_invalid") {
      return "产品账号会话已失效，请重新登录";
    }
  }
  return "暂时无法完成账号操作，请稍后重试";
}

export function AccountSessionGate({ gateway, children }: AccountSessionGateProps) {
  const [state, setState] = useState<GateState>({ kind: "checking" });
  const [mode, setMode] = useState<"login" | "recovery">("login");
  const [changingPassword, setChangingPassword] = useState(false);
  const [managingDevices, setManagingDevices] = useState(false);
  const [devices, setDevices] = useState<readonly AccountDevice[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string>();
  const [restoreAttempt, setRestoreAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    void gateway
      .restoreSession()
      .then((snapshot) => {
        if (active) {
          setState(
            snapshot.state === "authenticated"
              ? { kind: "authenticated", snapshot }
              : { kind: "unauthenticated" },
          );
        }
      })
      .catch(() => {
        if (active) setState({ kind: "offline" });
      });
    return () => {
      active = false;
    };
  }, [gateway, restoreAttempt]);

  if (state.kind === "checking") {
    return (
      <main className="startup-screen" aria-label="产品账号状态检查">
        <Space orientation="vertical" size="large" align="center">
          <Spin size="large" />
          <Typography.Title level={3}>正在恢复产品账号</Typography.Title>
        </Space>
      </main>
    );
  }

  if (state.kind === "offline") {
    return (
      <main className="startup-screen">
        <Result
          status="warning"
          title={<Typography.Title level={2}>暂时无法确认账号状态</Typography.Title>}
          subTitle="网络或账号服务暂不可用。业务工作台保持关闭，恢复连接后可重新检查。"
          extra={
            <Button
              type="primary"
              onClick={() => {
                setState({ kind: "checking" });
                setRestoreAttempt((current) => current + 1);
              }}
            >
              重新检查
            </Button>
          }
        />
      </main>
    );
  }

  if (state.kind === "authenticated") {
    const loadDevices = () => {
      setDevicesLoading(true);
      setErrorMessage(undefined);
      void gateway
        .listDevices()
        .then(setDevices)
        .catch((error: unknown) => {
          if (error instanceof AccountSessionGatewayError && error.code === "session_invalid") {
            setState({ kind: "unauthenticated", notice: "产品账号会话已失效，请重新登录" });
            return;
          }
          setErrorMessage(authenticationMessage(error));
        })
        .finally(() => setDevicesLoading(false));
    };
    return (
      <div className="account-session-layout">
        <div className="account-session-bar" aria-label="产品账号状态">
          <Typography.Text>{state.snapshot.account.loginName}</Typography.Text>
          <Button
            disabled={submitting}
            onClick={() => {
              setErrorMessage(undefined);
              setManagingDevices(false);
              setChangingPassword((current) => !current);
            }}
          >
            修改密码
          </Button>
          <Button
            disabled={submitting}
            onClick={() => {
              const opening = !managingDevices;
              setManagingDevices(opening);
              setChangingPassword(false);
              if (opening) loadDevices();
            }}
          >
            设备管理
          </Button>
          <Button
            danger
            loading={submitting}
            onClick={() => {
              setSubmitting(true);
              void gateway
                .logout()
                .then(() => {
                  setErrorMessage(undefined);
                  setState({ kind: "unauthenticated" });
                })
                .catch((error: unknown) => setErrorMessage(authenticationMessage(error)))
                .finally(() => setSubmitting(false));
            }}
          >
            退出产品账号
          </Button>
        </div>
        {errorMessage === undefined ? null : (
          <Alert type="error" showIcon message={errorMessage} />
        )}
        {managingDevices ? (
          <Card title="我的设备" className="account-login-card">
            <List
              loading={devicesLoading}
              locale={{ emptyText: "暂无已绑定设备" }}
              dataSource={[...devices]}
              renderItem={(device) => (
                <List.Item
                  actions={
                    device.status === "active"
                      ? [
                          <Popconfirm
                            key="revoke"
                            title="确认吊销这台设备？"
                            description="吊销后该设备凭据与在线会话会立即失效，且不能重新绑定。"
                            okText="确认吊销"
                            cancelText="取消"
                            onConfirm={() => {
                              setDevicesLoading(true);
                              setErrorMessage(undefined);
                              return gateway
                                .revokeDevice({
                                  installationId: device.installationId,
                                  expectedRevision: device.revision,
                                })
                                .then((revoked) =>
                                  setDevices((current) =>
                                    current.map((item) =>
                                      item.installationId === revoked.installationId
                                        ? revoked
                                        : item,
                                    ),
                                  ),
                                )
                                .catch((error: unknown) =>
                                  setErrorMessage(authenticationMessage(error)),
                                )
                                .finally(() => setDevicesLoading(false));
                            }}
                          >
                            <Button danger size="small">
                              吊销设备
                            </Button>
                          </Popconfirm>,
                        ]
                      : []
                  }
                >
                  <List.Item.Meta
                    title={
                      <Space wrap>
                        <Typography.Text code>{device.installationId}</Typography.Text>
                        <Tag color={device.status === "active" ? "green" : "default"}>
                          {device.status === "active" ? "有效" : "已吊销"}
                        </Tag>
                      </Space>
                    }
                    description={`版本 ${device.revision} · 绑定于 ${new Date(device.createdAt).toLocaleString()}`}
                  />
                </List.Item>
              )}
            />
          </Card>
        ) : changingPassword ? (
          <Card className="account-login-card">
            <Form
              layout="vertical"
              onFinish={(values: { currentPassword: string; newPassword: string }) => {
                setSubmitting(true);
                setErrorMessage(undefined);
                void gateway
                  .changePassword(values)
                  .then(() => {
                    setChangingPassword(false);
                    setState({
                      kind: "unauthenticated",
                      notice: "密码已修改，请重新登录",
                    });
                  })
                  .catch((error: unknown) => setErrorMessage(authenticationMessage(error)))
                  .finally(() => setSubmitting(false));
              }}
            >
              <Form.Item
                name="currentPassword"
                label="当前密码"
                rules={[{ required: true, min: 12, max: 128 }]}
              >
                <Input.Password autoComplete="current-password" maxLength={128} />
              </Form.Item>
              <Form.Item
                name="newPassword"
                label="新密码"
                rules={[{ required: true, min: 12, max: 128 }]}
              >
                <Input.Password autoComplete="new-password" maxLength={128} />
              </Form.Item>
              <Space wrap>
                <Button type="primary" htmlType="submit" loading={submitting}>
                  确认修改
                </Button>
                <Button onClick={() => setChangingPassword(false)}>取消</Button>
              </Space>
            </Form>
          </Card>
        ) : (
          children
        )}
      </div>
    );
  }

  return (
    <main className="startup-screen account-login-screen">
      <Card className="account-login-card">
        <Space orientation="vertical" size="large" className="settings-stack">
          <Space orientation="vertical" size={4}>
            <Typography.Title level={2}>登录自动化运营工具</Typography.Title>
            <Typography.Text type="secondary">
              客户演示版需要产品账号；平台扫码登录将在进入工作台后单独处理。
            </Typography.Text>
          </Space>
          {state.notice === undefined ? null : (
            <Alert type="success" showIcon message={state.notice} />
          )}
          {errorMessage === undefined ? null : (
            <Alert type="error" showIcon message={errorMessage} />
          )}
          {mode === "login" ? (
            <Form
              layout="vertical"
              onFinish={(values: { loginName: string; password: string }) => {
                setSubmitting(true);
                setErrorMessage(undefined);
                void gateway
                  .login(values)
                  .then((snapshot) =>
                    setState(
                      snapshot.state === "authenticated"
                        ? { kind: "authenticated", snapshot }
                        : { kind: "unauthenticated" },
                    ),
                  )
                  .catch((error: unknown) => setErrorMessage(authenticationMessage(error)))
                  .finally(() => setSubmitting(false));
              }}
            >
              <Form.Item
                name="loginName"
                label="登录名"
                rules={[{ required: true }, { pattern: /^[A-Za-z][A-Za-z0-9._-]{2,63}$/ }]}
              >
                <Input autoComplete="username" maxLength={64} />
              </Form.Item>
              <Form.Item
                name="password"
                label="密码"
                rules={[{ required: true, min: 12, max: 128 }]}
              >
                <Input.Password autoComplete="current-password" maxLength={128} />
              </Form.Item>
              <Space wrap>
                <Button type="primary" htmlType="submit" loading={submitting}>
                  登录
                </Button>
                <Button onClick={() => setMode("recovery")}>使用恢复票据</Button>
              </Space>
            </Form>
          ) : (
            <Form
              layout="vertical"
              onFinish={(values: { recoveryToken: string; newPassword: string }) => {
                setSubmitting(true);
                setErrorMessage(undefined);
                void gateway
                  .recoverPassword(values)
                  .then(() => {
                    setMode("login");
                    setState({
                      kind: "unauthenticated",
                      notice: "密码已重置，请使用新密码登录",
                    });
                  })
                  .catch((error: unknown) => setErrorMessage(authenticationMessage(error)))
                  .finally(() => setSubmitting(false));
              }}
            >
              <Form.Item name="recoveryToken" label="恢复票据" rules={[{ required: true }]}>
                <Input.Password autoComplete="off" maxLength={256} />
              </Form.Item>
              <Form.Item
                name="newPassword"
                label="新密码"
                rules={[{ required: true, min: 12, max: 128 }]}
              >
                <Input.Password autoComplete="new-password" maxLength={128} />
              </Form.Item>
              <Space wrap>
                <Button type="primary" htmlType="submit" loading={submitting}>
                  重置密码
                </Button>
                <Button onClick={() => setMode("login")}>返回登录</Button>
              </Space>
            </Form>
          )}
        </Space>
      </Card>
    </main>
  );
}
