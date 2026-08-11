import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Flex, Typography } from "antd";
import { TauriAuthError, type TauriAuthClientApi } from "@unified-login/tauri";

type GateState = "checking" | "unauthenticated" | "authenticated";

/**
 * 统一登录门：App 启动时恢复令牌，未登录则引导用系统浏览器完成
 * 统一登录（Authorization Code + PKCE），登录后放行整个工作台。
 */
export function UnifiedLoginGate({
  client,
  children,
}: {
  client: TauriAuthClientApi;
  children: React.ReactNode;
}) {
  const [state, setState] = useState<GateState>("checking");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    const unsubscribe = client.onAuthStateChange((authenticated) => {
      if (!disposed) setState(authenticated ? "authenticated" : "unauthenticated");
    });
    client
      .getAccessToken()
      .then(() => {
        if (!disposed) setState("authenticated");
      })
      .catch(() => {
        if (!disposed) setState("unauthenticated");
      });
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [client]);

  const login = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await client.login();
      setState("authenticated");
    } catch (caught) {
      if (caught instanceof TauriAuthError && caught.code === "loginInProgress") {
        setError("登录已在系统浏览器中进行，请在浏览器里完成");
      } else {
        setError("登录未完成，请重试");
      }
    } finally {
      setBusy(false);
    }
  }, [client]);

  const logout = useCallback(async () => {
    try {
      await client.logout();
    } finally {
      setState("unauthenticated");
    }
  }, [client]);

  if (state === "checking") {
    return null;
  }

  if (state === "authenticated") {
    return (
      <>
        {children}
        <Button
          size="small"
          style={{ position: "fixed", right: 16, bottom: 16, zIndex: 1000, opacity: 0.75 }}
          onClick={() => void logout()}
        >
          退出登录
        </Button>
      </>
    );
  }

  return (
    <Flex align="center" justify="center" style={{ minHeight: "100vh" }}>
      <Card style={{ width: 360 }}>
        <Flex vertical gap={16}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            登录
          </Typography.Title>
          <Typography.Text type="secondary">
            使用统一登录账号继续。点击后将打开系统浏览器完成登录。
          </Typography.Text>
          {error === null ? null : <Alert type="warning" message={error} showIcon />}
          <Button type="primary" block loading={busy} onClick={() => void login()}>
            用浏览器登录
          </Button>
        </Flex>
      </Card>
    </Flex>
  );
}
