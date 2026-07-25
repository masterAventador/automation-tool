import { Alert, Button, Card, Divider, Flex, Input, Select, Space, Spin, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import {
  ModelServiceGatewayError,
  type BailianModelId,
  type ModelConnectionSnapshot,
  type ModelServiceGateway,
  type ModelServicePurpose,
  type ModelServiceSnapshot,
} from "./model-service-gateway";

const SCRIPT_MODELS = [
  { value: "deepseek-v4-pro", label: "DeepSeek V4 Pro（最新稳定版）" },
  { value: "glm-5.2", label: "GLM 5.2（最新稳定版）" },
  {
    value: "qwen3.7-max-2026-06-08",
    label: "通义千问 3.7 Max（2026-06-08 多模态稳定版）",
  },
] as const;
const VIDEO_MODELS = [SCRIPT_MODELS[2]] as const;
const PURPOSE_COPY = {
  script: {
    title: "文案模型服务",
    description: "用于文案、脚本和分镜，不会获得浏览器、发布平台或视频文件权限。",
  },
  video_creative: {
    title: "视频创作模型服务",
    description:
      "用于生成和修正视频画面代码，可读取明确提交的预览图，但不会获得运营浏览器或发布平台权限。",
  },
} as const;
const LOAD_FAILURE = "暂时无法读取模型服务设置，请稍后重试。";
const OPERATION_FAILURE = "模型服务操作暂时不可用，请稍后重试。";

const ERROR_MESSAGES: Record<string, string> = {
  authentication_rejected: "密钥未通过阿里百炼验证，请检查后重新保存。",
  configuration_invalid: "配置格式不正确，请检查模型和密钥。",
  configuration_required: "请先保存该用途的模型服务配置。",
  invalid_response: "服务返回内容不符合预期，请稍后重试。",
  model_unavailable: "所选模型当前不可用，请更换模型或稍后重试。",
  quota_exhausted: "当前服务额度已用尽，请在阿里百炼控制台处理。",
  rate_limited: "请求过于频繁，请稍后重试。",
  storage_unavailable: "本机暂时无法安全保存配置。",
  timed_out: "连接测试超时，请检查网络后重试。",
  transport_unavailable: "暂时无法连接阿里百炼，请检查网络后重试。",
  protocol_mismatch: "模型服务设置返回异常，请更新 App 后重试。",
  operation_unavailable: OPERATION_FAILURE,
};

interface ModelServiceSettingsProps {
  readonly gateway: ModelServiceGateway;
}

function safeFailure(error: unknown): string {
  if (error instanceof ModelServiceGatewayError) {
    return ERROR_MESSAGES[error.code] ?? OPERATION_FAILURE;
  }
  return OPERATION_FAILURE;
}

function quotaText(connection: ModelConnectionSnapshot): string {
  const values: string[] = [];
  if (connection.quota.remainingRequests !== null) {
    values.push(`剩余请求 ${connection.quota.remainingRequests.toLocaleString("zh-CN")} 次`);
  }
  if (connection.quota.remainingTokens !== null) {
    values.push(`剩余用量 ${connection.quota.remainingTokens.toLocaleString("zh-CN")}`);
  }
  return values.length === 0 ? "连接成功；服务未返回可用额度。" : `连接成功；${values.join("，")}。`;
}

export function ModelServiceSettings({ gateway }: ModelServiceSettingsProps) {
  const [snapshot, setSnapshot] = useState<ModelServiceSnapshot | null>(null);
  const [scriptModel, setScriptModel] = useState<BailianModelId>("qwen3.7-max-2026-06-08");
  const [videoModel, setVideoModel] = useState<BailianModelId>("qwen3.7-max-2026-06-08");
  const [scriptKey, setScriptKey] = useState("");
  const [videoKey, setVideoKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [loadFailure, setLoadFailure] = useState(false);
  const [purposeFailure, setPurposeFailure] = useState<Partial<Record<ModelServicePurpose, string>>>({});
  const [connections, setConnections] = useState<Partial<Record<ModelServicePurpose, ModelConnectionSnapshot>>>({});

  const acceptSnapshot = (next: ModelServiceSnapshot) => {
    setSnapshot(next);
    setScriptModel(next.script.modelId);
    setVideoModel(next.videoCreative.modelId);
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
    purpose: ModelServicePurpose,
    actionName: string,
    action: () => Promise<ModelServiceSnapshot>,
  ) => {
    setBusy(actionName);
    setPurposeFailure((current) => ({ ...current, [purpose]: undefined }));
    setConnections((current) => ({ ...current, [purpose]: undefined }));
    try {
      acceptSnapshot(await action());
      if (purpose === "script") {
        setScriptKey("");
      } else {
        setVideoKey("");
      }
    } catch (error) {
      setPurposeFailure((current) => ({ ...current, [purpose]: safeFailure(error) }));
    } finally {
      setBusy(null);
    }
  };

  const configure = (purpose: ModelServicePurpose) => {
    const modelId = purpose === "script" ? scriptModel : videoModel;
    const apiKey = purpose === "script" ? scriptKey : videoKey;
    if (apiKey.length === 0) {
      setPurposeFailure((current) => ({
        ...current,
        [purpose]: "请输入新的阿里百炼 API Key（接口密钥）。已保存的密钥不会回显。",
      }));
      return;
    }
    void runSnapshotAction(purpose, `configure-${purpose}`, () =>
      gateway.configure({ purpose, modelId, apiKey }),
    );
  };

  const testConnection = async (purpose: ModelServicePurpose) => {
    setBusy(`test-${purpose}`);
    setPurposeFailure((current) => ({ ...current, [purpose]: undefined }));
    setConnections((current) => ({ ...current, [purpose]: undefined }));
    try {
      const connection = await gateway.testConnection(purpose);
      setConnections((current) => ({ ...current, [purpose]: connection }));
    } catch (error) {
      setPurposeFailure((current) => ({ ...current, [purpose]: safeFailure(error) }));
    } finally {
      setBusy(null);
    }
  };

  const renderPurpose = (purpose: ModelServicePurpose) => {
    if (snapshot === null) {
      return null;
    }
    const isScript = purpose === "script";
    const purposeSnapshot = isScript ? snapshot.script : snapshot.videoCreative;
    const model = isScript ? scriptModel : videoModel;
    const apiKey = isScript ? scriptKey : videoKey;
    const modelOptions = isScript ? SCRIPT_MODELS : VIDEO_MODELS;
    const copy = PURPOSE_COPY[purpose];
    return (
      <section className={`model-service-purpose model-service-purpose--${purpose}`}>
        <Flex justify="space-between" align="center" gap={16}>
          <Typography.Title level={5}>{copy.title}</Typography.Title>
          <Tag color={purposeSnapshot.configured ? "green" : "default"}>
            {purposeSnapshot.configured ? "已配置" : "未配置"}
          </Tag>
        </Flex>
        <Typography.Paragraph type="secondary">{copy.description}</Typography.Paragraph>
        <Space orientation="vertical" size="middle" className="model-service-fields">
          <label>
            <Typography.Text>模型</Typography.Text>
            <Select
              aria-label={`${copy.title}模型`}
              value={model}
              options={[...modelOptions]}
              onChange={(value: BailianModelId) => {
                if (isScript) {
                  setScriptModel(value);
                } else {
                  setVideoModel(value);
                }
              }}
            />
          </label>
          <label>
            <Typography.Text>阿里百炼 API Key（接口密钥）</Typography.Text>
            <Input.Password
              aria-label={`${copy.title} API Key`}
              autoComplete="new-password"
              placeholder={purposeSnapshot.configured ? "已安全保存；输入新密钥可替换" : "请输入 sk- 开头的密钥"}
              value={apiKey}
              onChange={(event) => {
                if (isScript) {
                  setScriptKey(event.target.value);
                } else {
                  setVideoKey(event.target.value);
                }
              }}
            />
          </label>
          {purposeFailure[purpose] === undefined ? null : (
            <Alert type="error" showIcon title={purposeFailure[purpose]} />
          )}
          {connections[purpose] === undefined ? null : (
            <Alert type="success" showIcon title={quotaText(connections[purpose])} />
          )}
          <Flex wrap gap={8}>
            <Button
              type="primary"
              loading={busy === `configure-${purpose}`}
              disabled={busy !== null && busy !== `configure-${purpose}`}
              onClick={() => configure(purpose)}
            >
              保存配置
            </Button>
            <Button
              loading={busy === `test-${purpose}`}
              disabled={!purposeSnapshot.configured || (busy !== null && busy !== `test-${purpose}`)}
              onClick={() => void testConnection(purpose)}
            >
              测试连接
            </Button>
            <Button
              danger
              disabled={!purposeSnapshot.configured || busy !== null}
              onClick={() =>
                void runSnapshotAction(purpose, `clear-${purpose}`, () => gateway.clear(purpose))
              }
            >
              清除配置
            </Button>
          </Flex>
        </Space>
      </section>
    );
  };

  return (
    <Card title="模型服务" className="model-service-settings-card">
      <Space orientation="vertical" size="middle" className="model-service-settings-stack">
        <Typography.Text type="secondary">
          当前服务商：阿里百炼。密钥只保存在 App 本机受保护存储中，页面只能看到配置状态。
        </Typography.Text>
        {loadFailure ? <Alert type="error" showIcon title={LOAD_FAILURE} /> : null}
        {snapshot === null && !loadFailure ? (
          <Flex justify="center" className="model-service-loading">
            <Spin description="正在读取模型服务设置" />
          </Flex>
        ) : null}
        {renderPurpose("script")}
        {snapshot === null ? null : <Divider />}
        {renderPurpose("video_creative")}
        {snapshot === null ? null : (
          <Flex align="center" gap={8} wrap>
            <Button
              disabled={!snapshot.script.configured || busy !== null}
              loading={busy === "reuse-video"}
              onClick={() =>
                void runSnapshotAction("video_creative", "reuse-video", () =>
                  gateway.reuseScriptForVideo(),
                )
              }
            >
              视频创作复用文案服务密钥
            </Button>
            {snapshot.sameCredential ? <Tag color="blue">两种用途当前复用同一密钥</Tag> : null}
          </Flex>
        )}
      </Space>
    </Card>
  );
}
