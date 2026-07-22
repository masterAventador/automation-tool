import { Alert, Button, Card, Empty, Input, Space, Tabs, Typography } from "antd";

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

function NewVideoPage() {
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
        <Alert
          type="info"
          showIcon
          title="制作方式接入后开放创建，不会生成演示任务。"
        />
        <div>
          <Button type="primary" disabled>
            创建视频草稿
          </Button>
        </div>
      </Space>
    </Card>
  );
}

export function VideoStudio() {
  return (
    <section className="video-studio" aria-label="视频制作工作区">
      <Tabs
        defaultActiveKey="new"
        items={[
          { key: "new", label: "新建视频", children: <NewVideoPage /> },
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
