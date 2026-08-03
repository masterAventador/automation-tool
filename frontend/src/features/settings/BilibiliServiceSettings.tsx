import { Alert, Button, Card, Flex, Input, InputNumber, Select, Space, Spin, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import {
  BilibiliServiceGatewayError,
  type BilibiliServiceGateway,
  type BilibiliServiceSnapshot,
} from "./bilibili-service-gateway";

const OPERATION_FAILURE = "B站发布配置暂时不可用，请稍后重试。";
const ERROR_MESSAGES: Record<string, string> = {
  configuration_invalid: "配置格式不正确，请检查凭据、分区、标签和授权有效期。",
  configuration_required: "请先保存 B站开放平台配置。",
  storage_unavailable: "本机暂时无法安全保存 B站发布凭据。",
  protocol_mismatch: "B站发布设置返回异常，请更新 App 后重试。",
  operation_unavailable: OPERATION_FAILURE,
};

function failureMessage(error: unknown): string {
  return error instanceof BilibiliServiceGatewayError
    ? (ERROR_MESSAGES[error.code] ?? OPERATION_FAILURE)
    : OPERATION_FAILURE;
}

interface BilibiliServiceSettingsProps {
  readonly gateway: BilibiliServiceGateway;
}

export function BilibiliServiceSettings({ gateway }: BilibiliServiceSettingsProps) {
  const [snapshot, setSnapshot] = useState<BilibiliServiceSnapshot | null>(null);
  const [clientId, setClientId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [targetAccount, setTargetAccount] = useState("");
  const [tid, setTid] = useState<number>(0);
  const [tag, setTag] = useState("");
  const [noReprint, setNoReprint] = useState<0 | 1>(1);
  const [busy, setBusy] = useState(false);
  const [loadFailure, setLoadFailure] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const acceptSnapshot = (next: BilibiliServiceSnapshot) => {
    setSnapshot(next);
    setTargetAccount(next.targetAccount ?? "");
    setTid(next.tid ?? 0);
    setTag(next.tag ?? "");
    setNoReprint(next.noReprint ?? 1);
  };

  useEffect(() => {
    let active = true;
    void gateway
      .getSettings()
      .then((next) => {
        if (active) {
          acceptSnapshot(next);
          setLoadFailure(false);
        }
      })
      .catch(() => {
        if (active) setLoadFailure(true);
      });
    return () => {
      active = false;
    };
  }, [gateway]);

  const clearSecrets = () => {
    setClientId("");
    setAppSecret("");
    setAccessToken("");
    setRefreshToken("");
    setExpiresAt("");
  };

  const run = async (action: () => Promise<BilibiliServiceSnapshot>) => {
    setBusy(true);
    setFailure(null);
    try {
      acceptSnapshot(await action());
      clearSecrets();
    } catch (error) {
      setFailure(failureMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const configure = () => {
    const parsedExpiry = Date.parse(expiresAt);
    if (
      [clientId, appSecret, accessToken, refreshToken, targetAccount, tag].some(
        (value) => value.trim().length === 0,
      ) ||
      tid < 1 ||
      !Number.isFinite(parsedExpiry)
    ) {
      setFailure("请完整填写开放平台凭据、授权有效期、目标账号、分区和标签。");
      return;
    }
    void run(() =>
      gateway.configure({
        clientId,
        appSecret,
        accessToken,
        refreshToken,
        expiresAtEpochSeconds: Math.floor(parsedExpiry / 1000),
        targetAccount,
        tid,
        tag,
        noReprint,
      }),
    );
  };

  return (
    <Card title="B站发布服务" className="bilibili-service-settings-card">
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          使用 B站开放平台正式接口发布。应用密钥与授权凭证只保存在本机受保护存储中，页面只会回显账号与投稿默认项。
        </Typography.Text>
        {loadFailure ? <Alert type="error" showIcon title="暂时无法读取 B站发布设置。" /> : null}
        {snapshot === null && !loadFailure ? <Spin description="正在读取 B站发布设置" /> : null}
        {snapshot === null ? null : (
          <>
            <Flex justify="space-between" align="center">
              <Typography.Title level={5}>开放平台授权与投稿默认项</Typography.Title>
              <Tag color={snapshot.configured ? "green" : "default"}>
                {snapshot.configured ? "已配置" : "未配置"}
              </Tag>
            </Flex>
            <Input aria-label="B站 Client ID" autoComplete="off" placeholder="Client ID" value={clientId} onChange={(event) => setClientId(event.target.value)} />
            <Input.Password aria-label="B站 App Secret" autoComplete="new-password" placeholder="App Secret" value={appSecret} onChange={(event) => setAppSecret(event.target.value)} />
            <Input.Password aria-label="B站访问凭证" autoComplete="new-password" placeholder="访问凭证" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} />
            <Input.Password aria-label="B站更新凭证" autoComplete="new-password" placeholder="更新凭证" value={refreshToken} onChange={(event) => setRefreshToken(event.target.value)} />
            <Input aria-label="B站授权有效期" type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} />
            <Input aria-label="B站目标账号" placeholder="审批时显示的账号名称" value={targetAccount} onChange={(event) => setTargetAccount(event.target.value)} />
            <InputNumber aria-label="B站投稿分区 ID" min={1} precision={0} value={tid || null} placeholder="投稿分区 tid" onChange={(value) => setTid(value ?? 0)} />
            <Input aria-label="B站投稿标签" placeholder="多个标签用英文逗号分隔" value={tag} onChange={(event) => setTag(event.target.value)} />
            <Select
              aria-label="B站转载设置"
              value={noReprint}
              options={[
                { value: 1, label: "未经作者授权禁止转载" },
                { value: 0, label: "允许转载" },
              ]}
              onChange={(value: 0 | 1) => setNoReprint(value)}
            />
            {failure === null ? null : <Alert type="error" showIcon title={failure} />}
            <Flex gap={8} wrap>
              <Button type="primary" loading={busy} onClick={configure}>保存配置</Button>
              <Button danger disabled={!snapshot.configured || busy} onClick={() => void run(() => gateway.clear())}>清除配置</Button>
            </Flex>
          </>
        )}
      </Space>
    </Card>
  );
}
