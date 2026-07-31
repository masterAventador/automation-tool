import { Alert, Button, Card, Flex, Input, Select, Space, Spin, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import {
  VideoEditingServiceGatewayError,
  type AliyunEditingRegion,
  type VideoEditingServiceGateway,
  type VideoEditingServiceSnapshot,
} from "./video-editing-service-gateway";

const REGION_OPTIONS = [
  { value: "cn-beijing", label: "华北2（北京）" },
  { value: "cn-hangzhou", label: "华东1（杭州）" },
  { value: "cn-shanghai", label: "华东2（上海）" },
  { value: "cn-shenzhen", label: "华南1（深圳）" },
  { value: "ap-southeast-1", label: "新加坡" },
  { value: "us-west-1", label: "美国（硅谷）" },
] as const;

const LOAD_FAILURE = "暂时无法读取视频剪辑服务设置，请稍后重试。";
const OPERATION_FAILURE = "视频剪辑服务操作暂时不可用，请稍后重试。";
const MISSING_INPUT_FAILURE =
  "请输入 OSS Bucket、新的 AccessKey ID 和 AccessKey Secret。已保存的配置不会回显。";
const CONNECTION_SUCCESS = "连接成功；访问密钥与所选地域可用。";

const ERROR_MESSAGES: Record<string, string> = {
  authentication_rejected: "访问密钥未通过阿里云验证，请检查后重新保存。",
  configuration_invalid: "配置格式不正确，请检查地域和访问密钥。",
  configuration_required: "请先保存视频剪辑服务配置。",
  invalid_response: "服务返回内容不符合预期，请稍后重试。",
  permission_denied: "当前访问密钥缺少视频剪辑所需权限，请检查授权后重试。",
  rate_limited: "请求过于频繁，请稍后重试。",
  storage_unavailable: "本机暂时无法安全保存配置。",
  timed_out: "连接测试超时，请检查网络后重试。",
  transport_unavailable: "暂时无法连接阿里云视频剪辑服务，请检查网络后重试。",
  protocol_mismatch: "视频剪辑服务设置返回异常，请更新 App 后重试。",
  operation_unavailable: OPERATION_FAILURE,
};

interface VideoEditingServiceSettingsProps {
  readonly gateway: VideoEditingServiceGateway;
}

function safeFailure(error: unknown): string {
  if (error instanceof VideoEditingServiceGatewayError) {
    return ERROR_MESSAGES[error.code] ?? OPERATION_FAILURE;
  }
  return OPERATION_FAILURE;
}

export function VideoEditingServiceSettings({ gateway }: VideoEditingServiceSettingsProps) {
  const [snapshot, setSnapshot] = useState<VideoEditingServiceSnapshot | null>(null);
  const [region, setRegion] = useState<AliyunEditingRegion>("cn-shanghai");
  const [accessKeyId, setAccessKeyId] = useState("");
  const [accessKeySecret, setAccessKeySecret] = useState("");
  const [ossBucket, setOssBucket] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [loadFailure, setLoadFailure] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [connectionMessage, setConnectionMessage] = useState<string | null>(null);

  const acceptSnapshot = (next: VideoEditingServiceSnapshot) => {
    setSnapshot(next);
    if (next.region !== null) {
      setRegion(next.region);
    }
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
        if (active) {
          setLoadFailure(true);
        }
      });
    return () => {
      active = false;
    };
  }, [gateway]);

  const runSnapshotAction = async (
    actionName: string,
    action: () => Promise<VideoEditingServiceSnapshot>,
  ) => {
    setBusy(actionName);
    setFailure(null);
    setConnectionMessage(null);
    try {
      acceptSnapshot(await action());
      setAccessKeyId("");
      setAccessKeySecret("");
      setOssBucket("");
    } catch (error) {
      setFailure(safeFailure(error));
    } finally {
      setBusy(null);
    }
  };

  const configure = () => {
    if (accessKeyId.length === 0 || accessKeySecret.length === 0 || ossBucket.length === 0) {
      setFailure(MISSING_INPUT_FAILURE);
      return;
    }
    void runSnapshotAction("configure", () =>
      gateway.configure({ region, accessKeyId, accessKeySecret, ossBucket }),
    );
  };

  const testConnection = async () => {
    setBusy("test");
    setFailure(null);
    setConnectionMessage(null);
    try {
      await gateway.testConnection();
      setConnectionMessage(CONNECTION_SUCCESS);
    } catch (error) {
      setFailure(safeFailure(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="视频剪辑服务" className="video-editing-service-settings-card">
      <Space orientation="vertical" size="middle" className="video-editing-service-stack">
        <Typography.Text type="secondary">
          当前服务商：阿里云智能媒体服务。访问密钥只保存在 App 本机受保护存储中，页面只能看到配置状态。
          剪辑素材会临时存放到与所选地域相同的对象存储空间，并按保留期自动清理。
        </Typography.Text>
        {loadFailure ? <Alert type="error" showIcon title={LOAD_FAILURE} /> : null}
        {snapshot === null && !loadFailure ? (
          <Flex justify="center" className="video-editing-service-loading">
            <Spin description="正在读取视频剪辑服务设置" />
          </Flex>
        ) : null}
        {snapshot === null ? null : (
          <section className="video-editing-service-fields">
            <Flex justify="space-between" align="center" gap={16}>
              <Typography.Title level={5}>剪辑服务凭据</Typography.Title>
              <Tag color={snapshot.configured ? "green" : "default"}>
                {snapshot.configured ? "已配置" : "未配置"}
              </Tag>
            </Flex>
            <Typography.Paragraph type="secondary">
              请使用只授予剪辑与素材暂存最小权限的子账号访问密钥；本产品不会用它访问其它云资源。
            </Typography.Paragraph>
            <Space orientation="vertical" size="middle" className="video-editing-service-inputs">
              <label>
                <Typography.Text>服务地域</Typography.Text>
                <Select
                  aria-label="视频剪辑服务地域"
                  value={region}
                  options={[...REGION_OPTIONS]}
                  onChange={(value: AliyunEditingRegion) => setRegion(value)}
                />
              </label>
              <label>
                <Typography.Text>同地域 OSS Bucket</Typography.Text>
                <Input
                  aria-label="阿里云 OSS Bucket"
                  autoComplete="off"
                  placeholder={
                    snapshot.configured
                      ? "已安全保存；输入新 Bucket 可替换"
                      : "请输入与服务地域相同的 Bucket 名称"
                  }
                  value={ossBucket}
                  onChange={(event) => setOssBucket(event.target.value)}
                />
              </label>
              <label>
                <Typography.Text>阿里云 AccessKey ID</Typography.Text>
                <Input
                  aria-label="阿里云 AccessKey ID"
                  autoComplete="off"
                  placeholder={snapshot.configured ? "已安全保存；输入新密钥可替换" : "请输入访问密钥 ID"}
                  value={accessKeyId}
                  onChange={(event) => setAccessKeyId(event.target.value)}
                />
              </label>
              <label>
                <Typography.Text>阿里云 AccessKey Secret</Typography.Text>
                <Input.Password
                  aria-label="阿里云 AccessKey Secret"
                  autoComplete="new-password"
                  placeholder={snapshot.configured ? "已安全保存；输入新密钥可替换" : "请输入访问密钥"}
                  value={accessKeySecret}
                  onChange={(event) => setAccessKeySecret(event.target.value)}
                />
              </label>
              {failure === null ? null : <Alert type="error" showIcon title={failure} />}
              {connectionMessage === null ? null : (
                <Alert type="success" showIcon title={connectionMessage} />
              )}
              <Flex wrap gap={8}>
                <Button
                  type="primary"
                  loading={busy === "configure"}
                  disabled={busy !== null && busy !== "configure"}
                  onClick={configure}
                >
                  保存配置
                </Button>
                <Button
                  loading={busy === "test"}
                  disabled={!snapshot.configured || (busy !== null && busy !== "test")}
                  onClick={() => void testConnection()}
                >
                  测试连接
                </Button>
                <Button
                  danger
                  disabled={!snapshot.configured || busy !== null}
                  onClick={() => void runSnapshotAction("clear", () => gateway.clear())}
                >
                  清除配置
                </Button>
              </Flex>
            </Space>
          </section>
        )}
      </Space>
    </Card>
  );
}
