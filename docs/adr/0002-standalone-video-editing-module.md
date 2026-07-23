# ADR-0002：独立视频剪辑模块与领域边界

> 状态：已接受
>
> 决策日期：2026-07-23
>
> 适用范围：Control Plane 视频剪辑领域层、后续 VE-02～VE-08 的 Provider 契约、剪辑工作台与阿里云 IMS/ICE 接入
>
> 关联：ADR-0001（内置 Chromium 运行时）、专项 Roadmap 2.5 节、VF-01 视频领域契约

## 背景

首期产品同时存在三条视频相关工作线：两种视频制作方式（智能素材成片、品牌动效成片）产出成片，独立“视频剪辑”把素材与成片加工为新成片，内容发布把成片投递到外部平台。三者如果共用一个状态机或互相嵌套，任何一方的供应商故障、页面改版或状态语义变化都会污染其余两方。专项 Roadmap 已确定：剪辑是独立产品模块和独立后端边界，首期只接阿里云 IMS/ICE 云剪辑，本地 FFmpeg 只作受控媒体基础设施，不作为首期第二个用户可选云剪辑服务。

## 决策

### 独立模块与独立状态机

- “视频剪辑”是左侧独立菜单入口和独立后端模块，不隶属于“视频制作”，也不是发布流程的子步骤。剪辑项目、剪辑任务和剪辑成片记录与制作、发布分开展示和存储。
- `EditingJob` 使用自己的封闭状态机：`queued → running →（paused ⇄ running）→ succeeded / failed / outcome_uncertain`，任何非终态可进入 `cancelling`，`cancelling` 只能收敛到 `succeeded / failed / cancelled / outcome_uncertain`（取消可能与云端完成竞争）。终态不可再转移，非法转换一律抛出 `InvalidEditingJobTransition`。状态语义与既有任务状态机保持一致：暂停是协作式的，取消未确认前不得宣称副作用已停止，云端结果无法确认时进入 `outcome_uncertain`，禁止自动重投。
- RenderJob（制作）、EditingJob（剪辑）、PublishJob（发布）是三个互不嵌套的状态机。跨模块衔接只通过 Artifact 谱系：制作成片 Artifact 可作为剪辑项目的输入素材；剪辑成品注册为新的 Artifact 后可进入发布链路。一个状态机不读取、不驱动、不内嵌另一个状态机的状态。

### 领域对象职责与生命周期

- `EditingProject`：用户拥有的剪辑工作区，持有标题与输入素材（`ArtifactId` 引用），生命周期由用户创建、编辑、归档/删除决定，与任何供应商资源无关；标题属于用户内容，不进入 repr 与错误信息。
- 剪辑 `EditingTimeline`：锚定在 `EditingProject` 上的一次时间轴修订（`revision` 单调递增），轨道、片段、字幕、音频、转场完全复用 VF-01 建立的供应商无关词汇（`TimelineTrack` / `TimelineClip` / `TimelineTransition`），并复用同一 `TimelineId` 类型；制作方式产出的初始 Timeline 因此可以不经翻译送入剪辑。与创作侧 `Timeline` 的唯一差异是锚点：创作侧锚定 Storyboard，剪辑侧锚定 EditingProject，不建立第二套轨道/片段/转场词汇。
- `EditingJob`：对某个冻结的 Timeline 修订执行一次剪辑，引用输入/输出 `ArtifactId`，成功必须有输出成片、失败必须有供应商无关的 `EditingFailureCode`，其余状态不得携带输出或失败码；输入与输出集合不得重叠。
- 成片 Artifact：剪辑不定义第二套 Artifact 或 ID 实现，输出统一注册为既有 `Artifact`（`ArtifactId` + 角色/媒体类型/摘要/谱系），由谱系记录“由哪个剪辑任务、哪些输入产出”。

### 供应商边界与单向依赖

- 领域层只认识 `EditingProject`、供应商无关 `Timeline`、`EditingJob`、`Artifact` 和（VE-02 定义的）`VideoEditingProvider` 抽象。阿里云、腾讯云等供应商 DTO、Job ID、区域、密钥、回调载荷一律留在 Adapter 私有边界与受限诊断区，不出现在领域对象、页面 DTO、事件、日志或导出中。本模块字段经精确字段测试证明不存在 provider、vendor、region、api_key 等供应商字段。
- 依赖方向单向：剪辑页面 → Control Plane 剪辑领域 → Provider Adapter（首期 `AliyunImsEditingProvider`）。`video_editing` 可以引用 `video_creation` 的共享词汇与 `resource_ids` 的稳定 ID；反向引用（创作或基础层依赖剪辑）禁止。未来新增供应商只增加 Adapter 与映射，不修改领域层、页面和项目数据。
- 与 `VideoCreationProvider` 的边界：制作方式只产出素材、成片和初始 Timeline；剪辑不知道成片来自哪种制作方式或哪个上游实现。与 `PublishJob` 的边界：发布只消费成片 Artifact 与其谱系，不读取剪辑内部状态。

## 首期边界

- VE-01 只交付本 ADR 与领域对象；`VideoEditingProvider` 契约与注册表（VE-02）、剪辑工作台（VE-03）、阿里云凭据/编译/对账/成片导入（VE-04～VE-07）与 Provider 一致性验收（VE-08）分别由后续任务完成。
- 首期只实现阿里云 IMS/ICE 一个真实 Provider；假的第二 Provider 只用于一致性测试，证明未来接腾讯云不需要改领域层与页面。
- 本 ADR 不宣称任何剪辑用户功能可用；数据库表、API、UI 与迁移均不在本任务范围。

## 影响

- 剪辑相关的一切后端演进都以 `backend/src/automation_tool/control_plane/domain/video_editing.py` 的不可变、构造即校验对象为契约起点；非法值与非法状态转换在领域层直接拒绝，不依赖上层防守。
- 供应商故障与限流被隔离在 Adapter：领域状态机只感知供应商无关的失败码与 `outcome_uncertain`。
- 复用共享 Timeline 词汇意味着 VF 系列对轨道/片段/转场的演进会同时作用于创作与剪辑，两侧演进需要一起验证，但换来“成片一键送入剪辑”无需翻译层。

## 验收

- 本 ADR 与专项 Roadmap 2.5 节、`docs/backend-architecture.md`、`docs/frontend-architecture.md` 对同一决策无冲突。
- VE-01 按纯领域契约 + ADR 任务验收：确定性单元测试覆盖构造校验、非法值矩阵、状态机全矩阵、不可变性与脱敏 repr；无用户入口，正常用户路径验收不适用。
- 后续任何剪辑用户功能只有从正式 App 正常用户入口按真实路径跑通并核对真实供应商最终状态才能标记完成；Mock 与假 Provider 只作为分层证据。
