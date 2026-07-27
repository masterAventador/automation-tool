import {
  BellOutlined,
  CalendarOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  CommentOutlined,
  FireFilled,
  InboxOutlined,
  LinkOutlined,
  MessageOutlined,
  MoreOutlined,
  PaperClipOutlined,
  PlayCircleFilled,
  PlusOutlined,
  ReloadOutlined,
  RightOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SendOutlined,
  SettingOutlined,
  SoundOutlined,
  ThunderboltFilled,
  VideoCameraOutlined,
  WechatFilled,
} from "@ant-design/icons";
import {
  Alert,
  Avatar,
  Button,
  Card,
  Checkbox,
  Flex,
  Input,
  Modal,
  Progress,
  Segmented,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import { useMemo, useState, type ReactNode } from "react";

import type { SelectedVideo } from "../publishing/PublishWorkspace";
import { PublishWorkspace } from "../publishing/PublishWorkspace";
import type { PublishWorkspaceGateway } from "../publishing/publish-workspace-gateway";
import { VideoEditingWorkbench } from "../video-editing/VideoEditingWorkbench";
import type { VideoEditingGateway } from "../video-editing/video-editing-gateway";
import { VideoStudio } from "../video-studio/VideoStudio";
import type { MaterialVideoStudioGateway } from "../video-studio/material-video-studio-gateway";

const { Text, Title, Paragraph } = Typography;

function SectionIntro({
  eyebrow,
  title,
  description,
  action,
}: {
  readonly eyebrow?: string;
  readonly title: string;
  readonly description: string;
  readonly action?: ReactNode;
}) {
  return (
    <Flex className="ops-page-intro" justify="space-between" align="start" gap={24}>
      <div>
        {eyebrow === undefined ? null : <Text className="ops-eyebrow">{eyebrow}</Text>}
        <Title level={2}>{title}</Title>
        <Paragraph>{description}</Paragraph>
      </div>
      {action}
    </Flex>
  );
}

interface AssistantMessage {
  readonly id: string;
  readonly role: "assistant" | "user";
  readonly text: string;
  readonly meta?: string;
}

const STARTER_MESSAGES: readonly AssistantMessage[] = [
  {
    id: "assistant-welcome",
    role: "assistant",
    text: "下午好。我会在这里长期跟进你的运营工作。你可以直接说目标，也可以让我查询某项状态。",
    meta: "长期主会话",
  },
];

const ASSISTANT_SUGGESTIONS = [
  "查一下新能源热点",
  "看看哪些消息需要我处理",
  "准备一条今天能发布的视频",
] as const;

function assistantReply(input: string): string {
  if (input.includes("新能源")) {
    return "我会先查询“新能源”的最新热点，按新鲜度和上升速度排序，再给你三个可做的视频角度。热点接口接入后会在这里返回真实结果。";
  }
  if (input.includes("消息") || input.includes("评论") || input.includes("私信")) {
    return "我会优先检查投诉、敏感内容和低置信度会话；普通评论和私信仍按默认规则由 AI 自动回复。";
  }
  if (input.includes("发布")) {
    return "我可以先把作品、平台、文案和时间整理成确认单。只有你明确确认后，发布流程才会继续。";
  }
  return "收到。我会先核对当前已连接的能力和真实状态，再把可执行的下一步告诉你；尚未接通的能力不会被当成已经完成。";
}

export function AiAssistantHome({
  onOpenHotspots,
}: {
  readonly onOpenHotspots: () => void;
}) {
  const [messages, setMessages] = useState<readonly AssistantMessage[]>(STARTER_MESSAGES);
  const [draft, setDraft] = useState("");

  const send = (provided?: string) => {
    const text = (provided ?? draft).trim();
    if (text.length === 0) return;
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", text },
      {
        id: crypto.randomUUID(),
        role: "assistant",
        text: assistantReply(text),
        meta: "基于当前可用能力",
      },
    ]);
    setDraft("");
  };

  return (
    <section className="assistant-home" aria-label="AI 助理主会话">
      <div className="assistant-home__canvas">
        <div className="assistant-home__welcome">
          <div className="assistant-orbit">
            <Avatar size={72} icon={<RobotOutlined />} />
          </div>
          <Title level={1}>想先做什么？</Title>
          <Paragraph>
            直接告诉我目标。我会查询热点、准备内容、整理发布确认，并在需要你介入时提醒你。
          </Paragraph>
        </div>

        <div className="assistant-thread" aria-live="polite">
          {messages.map((message) => (
            <article
              key={message.id}
              className={`assistant-message assistant-message--${message.role}`}
            >
              {message.role === "assistant" ? (
                <Avatar size={30} icon={<RobotOutlined />} />
              ) : null}
              <div>
                <div className="assistant-message__bubble">{message.text}</div>
                {message.meta === undefined ? null : (
                  <Text className="assistant-message__meta">{message.meta}</Text>
                )}
              </div>
            </article>
          ))}
        </div>
      </div>

      <div className="assistant-composer-wrap">
        <div className="assistant-suggestions" aria-label="快捷提问">
          {ASSISTANT_SUGGESTIONS.map((suggestion) => (
            <Button
              key={suggestion}
              size="small"
              shape="round"
              onClick={() => send(suggestion)}
            >
              {suggestion}
            </Button>
          ))}
          <Button size="small" type="link" onClick={onOpenHotspots}>
            打开热点发现
          </Button>
        </div>
        <div className="assistant-composer">
          <Button
            type="text"
            aria-label="添加附件"
            icon={<PaperClipOutlined />}
          />
          <Input.TextArea
            aria-label="给 AI 助理发消息"
            rows={1}
            placeholder="告诉 AI 你想完成什么，或查询某项状态…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onPressEnter={(event) => {
              if (!event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
          />
          <Button
            type="primary"
            shape="circle"
            aria-label="发送"
            icon={<SendOutlined />}
            disabled={draft.trim().length === 0}
            onClick={() => send()}
          />
        </div>
        <Text className="assistant-composer-note">
          AI 会先核对真实状态；发布、敏感互动等动作仍需要确认。
        </Text>
      </div>
    </section>
  );
}

interface HotspotItem {
  readonly id: string;
  readonly rank: number;
  readonly title: string;
  readonly platform: string;
  readonly age: string;
  readonly velocity: string;
  readonly score: number;
  readonly likes: string;
  readonly reason: string;
  readonly tag: string;
}

const HOTSPOTS: readonly HotspotItem[] = [
  {
    id: "hot-1",
    rank: 1,
    title: "固态电池量产时间表再次提前，产业链讨论快速升温",
    platform: "抖音",
    age: "8 分钟前",
    velocity: "+286%",
    score: 96,
    likes: "3.8 万",
    reason: "刚进入上升段，争议点明确，适合做“普通人该关注什么”的快速解释。",
    tag: "刚刚起量",
  },
  {
    id: "hot-2",
    rank: 2,
    title: "新能源车保险费用为什么变了？车主评论区集中追问",
    platform: "抖音",
    age: "21 分钟前",
    velocity: "+173%",
    score: 89,
    likes: "2.4 万",
    reason: "评论意图集中，适合做问题拆解、成本对比和避坑清单。",
    tag: "高互动",
  },
  {
    id: "hot-3",
    rank: 3,
    title: "夏季续航实测引发两派争论，多个账号开始跟进",
    platform: "抖音",
    age: "36 分钟前",
    velocity: "+118%",
    score: 82,
    likes: "1.9 万",
    reason: "仍在早期扩散，适合从地区、车型和使用场景切入，避免重复观点。",
    tag: "正在扩散",
  },
] as const;

export function HotspotDiscovery({
  onCreateFromHotspot,
}: {
  readonly onCreateFromHotspot: () => void;
}) {
  const [keyword, setKeyword] = useState("动漫");
  const [searchedKeyword, setSearchedKeyword] = useState("动漫");
  const [savedKeywords, setSavedKeywords] = useState<readonly string[]>(["动漫"]);
  const [selected, setSelected] = useState(HOTSPOTS[0]!.id);

  const saveKeyword = () => {
    if (!savedKeywords.includes(searchedKeyword)) {
      setSavedKeywords((current) => [...current, searchedKeyword]);
    }
  };

  return (
    <section className="ops-page hotspot-page">
      <SectionIntro
        eyebrow="实时机会"
        title="热点发现"
        description="输入任意关键词，优先看刚出现、正在加速的内容。接口接入前，这里使用结构化演示数据。"
        action={
          <Tag icon={<ReloadOutlined />} className="ops-soft-tag">
            普通变化每小时汇总
          </Tag>
        }
      />

      <Card className="hotspot-search-card" variant="borderless">
        <Flex gap={12}>
          <Input
            aria-label="热点关键词"
            size="large"
            prefix={<SearchOutlined />}
            placeholder="例如：新能源、母婴、家装"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onPressEnter={() => {
              if (keyword.trim().length > 0) setSearchedKeyword(keyword.trim());
            }}
          />
          <Button
            type="primary"
            size="large"
            icon={<ThunderboltFilled aria-hidden="true" />}
            disabled={keyword.trim().length === 0}
            onClick={() => setSearchedKeyword(keyword.trim())}
          >
            立即查找
          </Button>
        </Flex>
        <Flex className="hotspot-watch-row" justify="space-between" align="center" gap={16}>
          <Space wrap>
            <Text type="secondary">长期监测</Text>
            {savedKeywords.map((item) => (
              <Tag key={item} closable={item !== "动漫"}>
                {item}
              </Tag>
            ))}
          </Space>
          <Space>
            <ClockCircleOutlined />
            <Text>每 20 分钟监测</Text>
          </Space>
        </Flex>
      </Card>

      <Alert
        className="hotspot-alert"
        type="warning"
        showIcon
        icon={<FireFilled />}
        title="发现 1 条高潜力新热点"
        description="新鲜度和上升速度同时达到提醒阈值，已进入需要立即查看的范围。"
      />

      <div className="hotspot-layout">
        <div className="hotspot-results">
          <Flex justify="space-between" align="center">
            <div>
              <Title level={3}>{searchedKeyword}热点</Title>
              <Text type="secondary">按新鲜度、上升速度和互动质量综合排序</Text>
            </div>
            <Button
              icon={
                savedKeywords.includes(searchedKeyword) ? (
                  <CheckCircleFilled aria-hidden="true" />
                ) : (
                  <PlusOutlined aria-hidden="true" />
                )
              }
              disabled={savedKeywords.includes(searchedKeyword)}
              onClick={saveKeyword}
            >
              {savedKeywords.includes(searchedKeyword) ? "已加入监测" : "加入长期监测"}
            </Button>
          </Flex>
          <div className="hotspot-list">
            {HOTSPOTS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`hotspot-row${selected === item.id ? " hotspot-row--selected" : ""}`}
                onClick={() => setSelected(item.id)}
              >
                <span className="hotspot-rank">{item.rank}</span>
                <span className="hotspot-row__body">
                  <span className="hotspot-row__meta">
                    <Tag color="cyan">{item.platform}</Tag>
                    <span>{item.age}</span>
                    <Tag color="volcano">{item.tag}</Tag>
                  </span>
                  <strong>{item.title}</strong>
                  <span>{item.reason}</span>
                </span>
                <span className="hotspot-row__metrics">
                  <strong>{item.velocity}</strong>
                  <span>上升速度</span>
                  <span>{item.likes} 赞</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <Card className="hotspot-insight-card" variant="borderless">
          <Tag color="cyan">AI 已准备</Tag>
          <Title level={4}>现在值得跟进的原因</Title>
          <Paragraph>
            话题刚进入扩散段，现有内容多在复述新闻，面向普通用户的判断框架仍然空缺。
          </Paragraph>
          <div className="hotspot-angle">
            <Text type="secondary">可做角度</Text>
            <ol>
              <li>三分钟看懂这次变化影响谁</li>
              <li>别只看参数，真正要看这三个信号</li>
              <li>支持派与质疑派分别忽略了什么</li>
            </ol>
          </div>
          <Button
            type="primary"
            block
            icon={<VideoCameraOutlined aria-hidden="true" />}
            onClick={onCreateFromHotspot}
          >
            用这个热点开始创作
          </Button>
        </Card>
      </div>
    </section>
  );
}

type CreationSection = "works" | "material" | "motion" | "editing";

function CreationMethodPanel({
  kind,
  onOpenStudio,
}: {
  readonly kind: "material" | "motion";
  readonly onOpenStudio: () => void;
}) {
  const material = kind === "material";
  return (
    <div className="creation-method">
      <Card className="creation-method__hero" variant="borderless">
        <Flex justify="space-between" align="center" gap={32}>
          <div>
            <Tag color={material ? "cyan" : "purple"}>
              {material ? "适合热点解读与知识内容" : "适合品牌宣传与产品演示"}
            </Tag>
            <Title level={3}>{material ? "智能素材成片" : "品牌动效成片"}</Title>
            <Paragraph>
              {material
                ? "输入主题或脚本，AI 整理旁白、字幕和镜头，并在当前 App 内完成素材制作流程。"
                : "用一句话和品牌资料生成分镜，AI 选择模板、动效、颜色和节奏，仍在当前 App 内完成。"}
            </Paragraph>
            <Space wrap>
              <Button type="primary" size="large" onClick={onOpenStudio}>
                打开完整制作面板
              </Button>
              <Button size="large">从热点导入</Button>
            </Space>
          </div>
          <div className="creation-method__preview" aria-label="创作流程预览">
            <div className="creation-preview__stage">
              <PlayCircleFilled />
              <span>{material ? "素材与字幕预览" : "品牌动效预览"}</span>
            </div>
            <div className="creation-preview__timeline">
              <Progress percent={72} showInfo={false} />
              <Space wrap size={6}>
                <Tag>片头</Tag>
                <Tag color={material ? "cyan" : "purple"}>核心内容</Tag>
                <Tag>行动引导</Tag>
              </Space>
            </div>
          </div>
        </Flex>
      </Card>

      <div className="creation-steps">
        {[
          ["01", "说明目标", "主题、受众、时长与语气"],
          ["02", "AI 准备", material ? "脚本、素材与旁白" : "分镜、模板与动效"],
          ["03", "你来调整", "文案、镜头、字幕、声音与封面"],
          ["04", "生成作品", "进入作品列表，等待发布确认"],
        ].map(([number, title, copy]) => (
          <div key={number}>
            <span>{number}</span>
            <strong>{title}</strong>
            <Text type="secondary">{copy}</Text>
          </div>
        ))}
      </div>

      {material ? (
        <Alert
          type="info"
          showIcon
          title="制作界面会嵌入当前 App"
          description="不会再打开第二个 App 窗口。服务接通前，这个入口只展示设计状态，不会声称已经生成视频。"
        />
      ) : null}
    </div>
  );
}

export function CreationHub({
  gateway,
  editingGateway,
  onPublishArtifact,
}: {
  readonly gateway: MaterialVideoStudioGateway;
  readonly editingGateway: VideoEditingGateway;
  readonly onPublishArtifact: (video: SelectedVideo) => void;
}) {
  const [section, setSection] = useState<CreationSection>("works");
  const [studioOpen, setStudioOpen] = useState(false);

  const sectionLabels: Record<CreationSection, string> = {
    works: "作品",
    material: "智能素材成片",
    motion: "品牌动效成片",
    editing: "轻量剪辑",
  };
  const focusedWorkbench = section === "editing" || studioOpen;

  return (
    <section
      className={`ops-page creation-page${focusedWorkbench ? " creation-page--focused" : ""}`}
    >
      {focusedWorkbench ? null : (
        <SectionIntro
          eyebrow="从想法到成片"
          title="创作"
          description="生成、整理和轻量编辑都留在一个工作区；需要时仍可以进入每个真实制作面板。"
          action={<Button type="primary" icon={<PlusOutlined />}>新建作品</Button>}
        />
      )}
      <Segmented
        className="ops-segmented"
        block
        value={section}
        options={(Object.keys(sectionLabels) as CreationSection[]).map((key) => ({
          value: key,
          label: sectionLabels[key],
        }))}
        onChange={(value) => {
          setSection(value);
          setStudioOpen(false);
        }}
      />

      {section === "works" ? (
        <div className="works-grid">
          <Card className="work-card work-card--create" variant="borderless">
            <PlusOutlined />
            <strong>从一个想法开始</strong>
            <Text type="secondary">AI 会先帮你补齐脚本和镜头计划</Text>
          </Card>
          <Card className="work-card" variant="borderless">
            <div className="work-card__thumb">
              <img
                src="/media/work-cover-insight.png"
                alt="新能源热点作品封面"
              />
              <PlayCircleFilled />
              <span>00:42</span>
            </div>
            <Tag color="gold">草稿</Tag>
            <strong>新能源热点 · 三个判断信号</strong>
            <Text type="secondary">智能素材成片 · 12 分钟前更新</Text>
          </Card>
          <Card className="work-card" variant="borderless">
            <div className="work-card__thumb">
              <img
                src="/media/work-cover-product.png"
                alt="产品更新作品封面"
              />
              <PlayCircleFilled />
              <span>00:36</span>
            </div>
            <Tag color="green">可发布</Tag>
            <strong>产品更新 · 夏季功能发布</strong>
            <Text type="secondary">品牌动效成片 · 昨天完成</Text>
          </Card>
        </div>
      ) : section === "editing" ? (
        <div className="embedded-workbench embedded-workbench--focused">
          <VideoEditingWorkbench gateway={editingGateway} />
        </div>
      ) : studioOpen ? (
        <div className="embedded-workbench embedded-workbench--focused">
          <VideoStudio
            gateway={gateway}
            onPublishArtifact={onPublishArtifact}
            embedded
          />
        </div>
      ) : (
        <CreationMethodPanel kind={section} onOpenStudio={() => setStudioOpen(true)} />
      )}
    </section>
  );
}

interface ScheduledWork {
  readonly id: string;
  readonly title: string;
  readonly platform: string;
  readonly scheduledAt: string;
  readonly status: "awaiting" | "scheduled" | "draft";
}

const INITIAL_PUBLISH_ITEMS: readonly ScheduledWork[] = [
  {
    id: "publish-1",
    title: "新能源热点 · 三个判断信号",
    platform: "抖音",
    scheduledAt: "今天 18:30",
    status: "awaiting",
  },
  {
    id: "publish-2",
    title: "产品更新 · 夏季功能发布",
    platform: "小红书",
    scheduledAt: "明天 10:00",
    status: "draft",
  },
  {
    id: "publish-3",
    title: "门店服务流程 · 30 秒版",
    platform: "视频号",
    scheduledAt: "周五 12:00",
    status: "scheduled",
  },
] as const;

export function PublishingHub({
  gateway,
  selectedVideo,
  onChangeSelection,
}: {
  readonly gateway: PublishWorkspaceGateway;
  readonly selectedVideo?: SelectedVideo | undefined;
  readonly onChangeSelection: () => void;
}) {
  const [view, setView] = useState<"list" | "calendar" | "workspace">("list");
  const [items, setItems] = useState<readonly ScheduledWork[]>(INITIAL_PUBLISH_ITEMS);
  const [confirming, setConfirming] = useState<ScheduledWork | null>(null);

  const approve = () => {
    if (confirming === null) return;
    setItems((current) =>
      current.map((item) =>
        item.id === confirming.id ? { ...item, status: "scheduled" } : item,
      ),
    );
    setConfirming(null);
  };

  return (
    <section className="ops-page publishing-page">
      <SectionIntro
        eyebrow="确认后发布"
        title="发布"
        description="发布清单是主视图，日历用于查看节奏。任何作品、平台、文案或时间变化后都要重新确认。"
        action={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setView("workspace")}>
            新建发布
          </Button>
        }
      />
      <div className="publishing-toolbar">
        <Segmented
          value={view === "workspace" ? "list" : view}
          options={[
            { value: "list", label: "发布清单", icon: <InboxOutlined /> },
            { value: "calendar", label: "内容日历", icon: <CalendarOutlined /> },
          ]}
          onChange={(value) => {
            if (value === "list" || value === "calendar") setView(value);
          }}
        />
        <Tag icon={<SafetyCertificateOutlined />} color="cyan">
          发布确认无法被自动化绕过
        </Tag>
      </div>

      {view === "workspace" ? (
        <div className="embedded-workbench">
          <div className="embedded-workbench__note">
            <div>
              <strong>真实发布工作台</strong>
              <Text type="secondary">这里读取真实桥接状态，确认前不会执行发布。</Text>
            </div>
            <Button onClick={() => setView("list")}>返回发布清单</Button>
          </div>
          <PublishWorkspace
            gateway={gateway}
            selectedVideo={selectedVideo}
            onChangeSelection={onChangeSelection}
          />
        </div>
      ) : view === "calendar" ? (
        <Card className="publish-calendar" variant="borderless">
          <div className="publish-calendar__week">
            {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map((day, index) => (
              <div key={day}>
                <Text type="secondary">{day}</Text>
                <strong>{27 + index}</strong>
                {index === 0 ? <span>18:30 · 抖音</span> : null}
                {index === 1 ? <span>10:00 · 小红书</span> : null}
                {index === 4 ? <span>12:00 · 视频号</span> : null}
              </div>
            ))}
          </div>
        </Card>
      ) : (
        <div className="publish-list">
          {items.map((item) => (
            <Card key={item.id} className="publish-row" variant="borderless">
              <Checkbox aria-label={`选择${item.title}`} />
              <div className="publish-row__thumb">
                <PlayCircleFilled />
              </div>
              <div className="publish-row__body">
                <strong>{item.title}</strong>
                <Space size={8}>
                  <Tag>{item.platform}</Tag>
                  <Text type="secondary">{item.scheduledAt}</Text>
                </Space>
              </div>
              {item.status === "awaiting" ? (
                <Button type="primary" onClick={() => setConfirming(item)}>
                  确认发布
                </Button>
              ) : item.status === "scheduled" ? (
                <Tag color="green" icon={<CheckCircleFilled />}>
                  已确认
                </Tag>
              ) : (
                <Tag>草稿</Tag>
              )}
              <Button type="text" aria-label={`更多${item.title}`} icon={<MoreOutlined />} />
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={confirming !== null}
        title="确认这次发布"
        okText="我确认，加入发布队列"
        cancelText="再检查一下"
        onOk={approve}
        onCancel={() => setConfirming(null)}
      >
        <Paragraph>
          请确认作品、平台和时间：<strong>{confirming?.title}</strong> 将发布到{" "}
          <strong>{confirming?.platform}</strong>，时间为{" "}
          <strong>{confirming?.scheduledAt}</strong>。
        </Paragraph>
        <Alert
          type="info"
          showIcon
          title="确认后如果内容发生变化，系统会要求重新确认。"
        />
      </Modal>
    </section>
  );
}

interface InteractionItem {
  readonly id: string;
  readonly platform: "抖音" | "微信" | "小红书" | "快手";
  readonly type: string;
  readonly sender: string;
  readonly preview: string;
  readonly time: string;
  readonly risk: "normal" | "attention";
  readonly source?: string;
}

const INTERACTIONS: readonly InteractionItem[] = [
  {
    id: "message-1",
    platform: "抖音",
    type: "作品评论",
    sender: "小林聊车",
    preview: "你这里说的电池寿命有数据来源吗？",
    time: "刚刚",
    risk: "attention",
    source: "《新能源车别只看续航》",
  },
  {
    id: "message-2",
    platform: "微信",
    type: "好友消息",
    sender: "王女士",
    preview: "你好，想了解一下你们的合作方式。",
    time: "3 分钟前",
    risk: "normal",
  },
  {
    id: "message-3",
    platform: "小红书",
    type: "私信",
    sender: "北岛日记",
    preview: "能发一下你视频里提到的清单吗？",
    time: "12 分钟前",
    risk: "normal",
  },
  {
    id: "message-4",
    platform: "快手",
    type: "作品评论",
    sender: "实用派老周",
    preview: "这个说法我不太认同，售后成本算了吗？",
    time: "18 分钟前",
    risk: "attention",
    source: "《买车前先算这笔账》",
  },
] as const;

const PLATFORM_ICONS: Record<InteractionItem["platform"], ReactNode> = {
  抖音: <PlayCircleFilled />,
  微信: <WechatFilled />,
  小红书: <CommentOutlined />,
  快手: <VideoCameraOutlined />,
};

export function InteractionCenter() {
  const ordered = useMemo(
    () => [...INTERACTIONS].sort((a, b) => Number(b.risk === "attention") - Number(a.risk === "attention")),
    [],
  );
  const [selectedId, setSelectedId] = useState(ordered[0]!.id);
  const [manual, setManual] = useState(false);
  const selected = ordered.find((item) => item.id === selectedId)!;

  return (
    <section className="ops-page interaction-page">
      <SectionIntro
        eyebrow="异常优先"
        title="消息与互动"
        description="把评论、私信和微信会话放在一起处理；每条消息都会重复标明来源平台。"
        action={<Tag icon={<RobotOutlined />} color="cyan">普通消息由 AI 托管</Tag>}
      />

      <Alert
        type="info"
        showIcon
        title="普通评论和私信默认由 AI 自动回复"
        description="投诉、敏感内容、低置信度和风控提示会暂停对应会话，等待你处理。"
      />

      <div className="interaction-layout">
        <aside className="interaction-list-panel">
          <div className="interaction-filters">
            <Segmented block options={["待处理", "全部", "AI 已回复"]} defaultValue="待处理" />
          </div>
          <ul className="interaction-list" aria-label="消息与互动列表">
            {ordered.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={selectedId === item.id ? "interaction-row--selected" : ""}
                  onClick={() => {
                    setSelectedId(item.id);
                    setManual(false);
                  }}
                >
                  <span
                    className={`platform-badge platform-badge--${item.platform}`}
                    data-testid="message-platform"
                  >
                    {PLATFORM_ICONS[item.platform]}
                    <span>{item.platform}</span>
                  </span>
                  <span className="interaction-row__main">
                    <span>
                      <strong>{item.sender}</strong>
                      <time>{item.time}</time>
                    </span>
                    <span>{item.type}</span>
                    <span>{item.preview}</span>
                  </span>
                  {item.risk === "attention" ? <span className="attention-dot" /> : null}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <article className="conversation-panel">
          <header>
            <div>
              <Space>
                <span className={`platform-badge platform-badge--${selected.platform}`}>
                  {PLATFORM_ICONS[selected.platform]}
                  <span>{selected.platform}</span>
                </span>
                <strong>{selected.sender}</strong>
                <Tag>{selected.type}</Tag>
              </Space>
              {selected.source === undefined ? null : (
                <Text type="secondary">
                  <LinkOutlined /> 来自作品：{selected.source}
                </Text>
              )}
            </div>
            <Tag color={manual ? "gold" : "cyan"}>{manual ? "人工处理中" : "AI 托管中"}</Tag>
          </header>

          <div className="conversation-stream">
            <div className="conversation-source">
              <Avatar>{selected.sender.slice(0, 1)}</Avatar>
              <div>
                <span>{selected.preview}</span>
                <Text type="secondary">{selected.time}</Text>
              </div>
            </div>
            <div className="conversation-ai-note">
              <RobotOutlined />
              <div>
                <strong>
                  {selected.risk === "attention" ? "AI 建议先由你确认" : "AI 已准备回复"}
                </strong>
                <Text>
                  {selected.risk === "attention"
                    ? "这条消息带有质疑或潜在投诉，已暂停自动发送。建议先说明数据口径，再给出来源。"
                    : "已结合当前会话准备简短回复，发送后会继续由 AI 跟进。"}
                </Text>
              </div>
            </div>
          </div>

          <footer className="conversation-composer">
            <Input.TextArea
              aria-label="回复消息"
              rows={3}
              placeholder={manual ? "输入你的回复…" : "AI 托管中；也可以直接输入并接管本会话"}
              onFocus={() => setManual(true)}
            />
            <Flex justify="space-between">
              <Space>
                <Button type="text" icon={<PaperClipOutlined />}>附件</Button>
                <Button type="text" icon={<SoundOutlined />}>语音</Button>
              </Space>
              <Space>
                {manual ? (
                  <Button icon={<RobotOutlined />} onClick={() => setManual(false)}>
                    交还给 AI
                  </Button>
                ) : null}
                <Button type="primary" icon={<SendOutlined />}>发送</Button>
              </Space>
            </Flex>
          </footer>
        </article>
      </div>
    </section>
  );
}

interface AutomationRule {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly enabled: boolean;
  readonly locked?: boolean;
  readonly status: string;
}

const AUTOMATION_RULES: readonly AutomationRule[] = [
  {
    id: "rule-comment",
    name: "普通评论自动回复",
    description: "低风险、置信度足够的评论由 AI 自动回复。",
    enabled: true,
    status: "运行中",
  },
  {
    id: "rule-dm",
    name: "普通私信自动回复",
    description: "普通咨询由 AI 回复；投诉、敏感内容和低置信度转人工。",
    enabled: true,
    status: "运行中",
  },
  {
    id: "rule-hotspot",
    name: "热点关键词监测",
    description: "每 20 分钟扫描一次，高潜力热点立即提醒。",
    enabled: true,
    status: "3 个关键词",
  },
  {
    id: "rule-wechat",
    name: "微信好友与消息自动化",
    description: "自动通过好友申请、打招呼和回复微信消息。",
    enabled: false,
    status: "默认关闭",
  },
  {
    id: "rule-outbound",
    name: "主动评论与私信",
    description: "对外主动触达，风险较高，启用后仍受频率与熔断限制。",
    enabled: false,
    status: "默认关闭",
  },
  {
    id: "rule-publish",
    name: "发布前必须确认",
    description: "发布动作永远需要明确确认，任何自动化都不能绕过。",
    enabled: true,
    locked: true,
    status: "强制开启",
  },
] as const;

export function AutomationCenter({
  onOpenRuns,
  onCreateTask,
}: {
  readonly onOpenRuns: () => void;
  readonly onCreateTask: () => void;
}) {
  const [states, setStates] = useState<Record<string, boolean>>(
    Object.fromEntries(AUTOMATION_RULES.map((rule) => [rule.id, rule.enabled])),
  );

  return (
    <section className="ops-page automation-page">
      <SectionIntro
        eyebrow="简单规则"
        title="自动化"
        description="把常用自动化作为清晰的开关管理。规则只决定何时运行，危险动作仍受确认、频控和熔断保护。"
        action={
          <Space>
            <Button onClick={onOpenRuns}>查看运行记录</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={onCreateTask}>
              新建运营任务
            </Button>
          </Space>
        }
      />

      <div className="automation-summary">
        <Card variant="borderless">
          <ThunderboltFilled />
          <div><strong>3</strong><Text type="secondary">已启用规则</Text></div>
        </Card>
        <Card variant="borderless">
          <MessageOutlined />
          <div><strong>28</strong><Text type="secondary">今天自动处理</Text></div>
        </Card>
        <Card variant="borderless">
          <BellOutlined />
          <div><strong>2</strong><Text type="secondary">等待人工</Text></div>
        </Card>
      </div>

      <div className="automation-list">
        {AUTOMATION_RULES.map((rule) => (
          <Card key={rule.id} className="automation-row" variant="borderless">
            <div className="automation-row__icon">
              {rule.id === "rule-hotspot" ? <FireFilled /> : rule.locked ? <SafetyCertificateOutlined /> : <RobotOutlined />}
            </div>
            <div className="automation-row__body">
              <Space>
                <strong>{rule.name}</strong>
                {rule.locked ? <Tag color="cyan">安全底线</Tag> : null}
              </Space>
              <Text type="secondary">{rule.description}</Text>
            </div>
            <Tag>{rule.status}</Tag>
            <Switch
              aria-label={rule.name}
              checked={states[rule.id] ?? false}
              disabled={rule.locked === true}
              onChange={(checked) =>
                setStates((current) => ({ ...current, [rule.id]: checked }))
              }
            />
            <Button type="text" aria-label={`设置${rule.name}`} icon={<SettingOutlined />} />
          </Card>
        ))}
      </div>
    </section>
  );
}

export function AccountPlatformOverview({ children }: { readonly children: ReactNode }) {
  return (
    <section className="ops-page accounts-page">
      <SectionIntro
        eyebrow="每个平台一个账号"
        title="账号与平台"
        description="查看连接状态、登录健康和可用能力。未连接的平台会在相关页面明确标为不可用。"
        action={<Button icon={<PlusOutlined />}>连接平台</Button>}
      />
      <div className="account-platform-grid">
        {[
          ["抖音", "需要检查", "当前已具备真实登录状态查询链路"],
          ["小红书", "未连接", "发布与互动能力尚未接入"],
          ["快手", "未连接", "发布与互动能力尚未接入"],
          ["微信视频号", "未连接", "发布能力尚未接入"],
          ["微信", "未连接", "桌面自动化默认关闭"],
        ].map(([platform, status, copy]) => (
          <Card key={platform} variant="borderless">
            <Flex justify="space-between">
              <Avatar icon={platform === "微信" ? <WechatFilled /> : <PlayCircleFilled />} />
              <Tag color={status === "需要检查" ? "gold" : "default"}>{status}</Tag>
            </Flex>
            <strong>{platform}</strong>
            <Text type="secondary">{copy}</Text>
            <Button type="link">查看连接 <RightOutlined /></Button>
          </Card>
        ))}
      </div>
      <div className="embedded-workbench account-platform-real">
        <div className="embedded-workbench__note">
          <div>
            <strong>抖音真实登录状态</strong>
            <Text type="secondary">以下区域读取现有平台会话桥接，不会用演示状态代替。</Text>
          </div>
        </div>
        {children}
      </div>
    </section>
  );
}
