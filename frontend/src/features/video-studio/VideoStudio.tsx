import { useState } from "react";

import { Alert, Button, Card, Empty, Input, Space, Tabs, Tag, Typography } from "antd";

import {
  MaterialVideoStudioGatewayError,
  type MaterialVideoStudioGateway,
} from "./material-video-studio-gateway";

type VideoCreationMethodId = "material_montage_v1" | "motion_composition_v1";

interface VideoCreationMethodOption {
  readonly id: VideoCreationMethodId;
  readonly name: string;
  readonly shortDescription: string;
  readonly details: ReadonlyArray<{
    readonly label: string;
    readonly value: string;
  }>;
}

const VIDEO_CREATION_METHODS: readonly VideoCreationMethodOption[] = [
  {
    id: "material_montage_v1",
    name: "智能素材成片",
    shortDescription: "让旁白、字幕和素材画面配合起来，快速制作信息类短视频。",
    details: [
      {
        label: "最适合",
        value: "知识讲解、观点、榜单、教程，以及旁白配城市、办公、做饭等补充画面的内容。",
      },
      {
        label: "不适合",
        value: "固定真人连续出镜、电影感剧情、精确产品动作，或同一人物跨镜头保持一致。",
      },
      {
        label: "举个例子",
        value: "输入一段护肤知识，生成旁白和字幕，并配上洗脸、护肤品与生活场景画面。",
      },
      {
        label: "外部服务",
        value: "通常需要文案模型、配音服务和素材网站；具体使用哪些服务会在提交前列明。",
      },
      {
        label: "本机处理",
        value: "素材整理、字幕排版和视频合成主要在本机完成。",
      },
      {
        label: "预计耗时",
        value: "30–60 秒视频通常约 5–20 分钟，实际取决于素材和配音服务。",
      },
      { label: "设备占用", value: "合成时会持续占用较多处理器和内存。" },
      { label: "临时磁盘", value: "建议预留 2–6 GB，任务提交前会按设置重新估算。" },
      { label: "网络消耗", value: "中到高，通常需要请求文案、配音并下载素材。" },
      {
        label: "数据与隐私",
        value: "输入内容及发送给外部服务的文案、配音文本或素材检索词会离开本机。",
      },
    ],
  },
  {
    id: "motion_composition_v1",
    name: "品牌动效成片",
    shortDescription: "用品牌配色、文字、图表和界面动画制作结构清晰的宣传视频。",
    details: [
      {
        label: "最适合",
        value: "品牌宣传、产品发布、数据图表、产品界面演示、标题动画和代码讲解。",
      },
      {
        label: "不适合",
        value: "只凭一句话生成逼真人物、自然运动，或真实世界中的复杂镜头。",
      },
      {
        label: "举个例子",
        value: "输入一次产品更新，按品牌颜色生成标题、功能界面、数据和转场动画。",
      },
      {
        label: "外部服务",
        value: "一句话自动制作通常需要视频创作模型；固定模板手工调整时可以不调用模型。",
      },
      {
        label: "本机处理",
        value: "画面排版、逐帧渲染和视频合成主要在本机完成。",
      },
      {
        label: "预计耗时",
        value: "30–60 秒视频通常约 3–15 分钟，复杂动画会更久。",
      },
      { label: "设备占用", value: "渲染时会持续占用较多处理器、内存，部分效果还会使用显卡。" },
      { label: "临时磁盘", value: "建议预留 1–4 GB，任务提交前会按设置重新估算。" },
      { label: "网络消耗", value: "低到中；使用模型时需要联网，本机渲染不会上传整条视频。" },
      {
        label: "数据与隐私",
        value: "使用模型时，需求和选中的品牌文字可能离开本机；本机素材不会被自动上传。",
      },
    ],
  },
] as const;

const EMPTY_PAGES = {
  script: {
    title: "脚本与分镜尚未生成",
    description: "创建真实视频草稿后，脚本和镜头安排会显示在这里。",
  },
  settings: {
    title: "尚未选择制作方式",
    description: "制作方式、画面风格、声音和输出规格将在后续任务接入。",
  },
  preview: {
    title: "还没有可预览内容",
    description: "只有真实生成或导入的画面才会进入预览。",
  },
  jobs: {
    title: "还没有真实制作任务",
    description: "任务提交后，这里会展示由本机状态机确认的进度和结果。",
  },
  artifacts: {
    title: "还没有已导入的成片",
    description: "完成并校验过的视频文件会显示在这里。",
  },
} as const;

function EmptyVideoPage({ page }: { readonly page: keyof typeof EMPTY_PAGES }) {
  const copy = EMPTY_PAGES[page];
  return (
    <Card className="video-studio-panel">
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space orientation="vertical" size={4}>
            <Typography.Text strong>{copy.title}</Typography.Text>
            <Typography.Text type="secondary">{copy.description}</Typography.Text>
          </Space>
        }
      />
    </Card>
  );
}

const OPEN_ERRORS = {
  configuration_required: "请先到“设置与诊断”配置并测试文案模型服务。",
  process_unavailable: "本机视频制作服务暂时无法启动，请稍后重试。",
  storage_unavailable: "无法创建本机视频工作区，请检查磁盘空间和目录权限。",
  view_unavailable: "完整制作界面暂时无法打开，请稍后重试。",
  protocol_mismatch: "视频制作服务版本不匹配，请更新 App 后重试。",
  operation_unavailable: "视频制作暂时不可用，请稍后重试。",
} as const;

function NewVideoPage({ gateway }: { readonly gateway: MaterialVideoStudioGateway }) {
  const [selectedMethod, setSelectedMethod] = useState<VideoCreationMethodId | null>(null);
  const [opening, setOpening] = useState(false);
  const [openMessage, setOpenMessage] = useState<{ type: "success" | "error"; text: string } | null>(
    null,
  );
  const selectedName = VIDEO_CREATION_METHODS.find(
    (method) => method.id === selectedMethod,
  )?.name;

  return (
    <Card className="video-studio-panel" title="从一句话开始">
      <Space orientation="vertical" size="middle" className="video-studio-new-form">
        <Typography.Text type="secondary">
          后续可以在这里描述主题、受众和想表达的重点，再选择合适的制作方式。
        </Typography.Text>
        <Input.TextArea
          aria-label="视频需求"
          disabled
          rows={5}
          placeholder="例如：用 30 秒介绍新品的三个亮点"
        />
        <div className="video-method-heading">
          <div>
            <Typography.Title level={4}>选择制作方式</Typography.Title>
            <Typography.Text type="secondary">
              以下耗时和空间是 30–60 秒视频的参考范围，提交任务前会结合设备与设置重新估算。
            </Typography.Text>
          </div>
          {selectedName ? <Tag color="blue">已选择：{selectedName}</Tag> : <Tag>尚未选择</Tag>}
        </div>
        <div className="video-method-grid" aria-label="视频制作方式">
          {VIDEO_CREATION_METHODS.map((method) => {
            const selected = method.id === selectedMethod;
            return (
              <article
                key={method.id}
                className={`video-method-card${selected ? " video-method-card-selected" : ""}`}
              >
                <div className="video-method-card-header">
                  <Typography.Title level={3}>{method.name}</Typography.Title>
                  <Tag color={selected ? "blue" : "default"}>{selected ? "已选择" : "可选择"}</Tag>
                </div>
                <Typography.Paragraph className="video-method-summary">
                  {method.shortDescription}
                </Typography.Paragraph>
                <dl className="video-method-details">
                  {method.details.map((detail) => (
                    <div key={detail.label}>
                      <dt>{detail.label}</dt>
                      <dd>{detail.value}</dd>
                    </div>
                  ))}
                </dl>
                <Button
                  type={selected ? "primary" : "default"}
                  aria-label={`选择${method.name}`}
                  aria-pressed={selected}
                  className="video-method-select"
                  onClick={() => setSelectedMethod(method.id)}
                >
                  {selected ? `已选择${method.name}` : `选择${method.name}`}
                </Button>
              </article>
            );
          })}
        </div>
        {openMessage === null ? (
          <Alert
            type="info"
            showIcon
            title="选择“智能素材成片”后可打开完整制作界面；“品牌动效成片”将在对应流程接入后开放。"
          />
        ) : (
          <Alert type={openMessage.type} showIcon title={openMessage.text} />
        )}
        <div>
          <Button
            type="primary"
            loading={opening}
            disabled={selectedMethod !== "material_montage_v1" || opening}
            onClick={() => {
              setOpening(true);
              setOpenMessage(null);
              void gateway
                .open()
                .then(() => {
                  setOpenMessage({ type: "success", text: "完整制作界面已打开。" });
                })
                .catch((error: unknown) => {
                  const code =
                    error instanceof MaterialVideoStudioGatewayError
                      ? error.code
                      : "operation_unavailable";
                  setOpenMessage({ type: "error", text: OPEN_ERRORS[code] });
                })
                .finally(() => setOpening(false));
            }}
          >
            打开完整制作界面
          </Button>
        </div>
      </Space>
    </Card>
  );
}

export function VideoStudio({ gateway }: { readonly gateway: MaterialVideoStudioGateway }) {
  return (
    <section className="video-studio" aria-label="视频制作工作区">
      <Tabs
        defaultActiveKey="new"
        items={[
          { key: "new", label: "新建视频", children: <NewVideoPage gateway={gateway} /> },
          { key: "script", label: "脚本与分镜", children: <EmptyVideoPage page="script" /> },
          { key: "settings", label: "制作设置", children: <EmptyVideoPage page="settings" /> },
          { key: "preview", label: "预览", children: <EmptyVideoPage page="preview" /> },
          { key: "jobs", label: "制作任务", children: <EmptyVideoPage page="jobs" /> },
          { key: "artifacts", label: "成片", children: <EmptyVideoPage page="artifacts" /> },
        ]}
      />
    </section>
  );
}
