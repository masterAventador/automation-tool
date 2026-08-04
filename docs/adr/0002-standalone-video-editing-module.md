# ADR-0002：独立视频剪辑模块与领域边界

> 状态：已接受
>
> 决策日期：2026-07-23
>
> 修订：2026-07-28。本 ADR 原先还规定了一层「可插拔外部剪辑服务」的 Provider 抽象。
> 那部分已废止——只有一条实现时，Provider 注册表与一致性套件属于为假设性需求的过早
> 抽象。剪辑改由随包 FFmpeg 在用户本机执行，设计见
> `docs/superpowers/specs/2026-07-28-local-smart-edit-design.md`，任务与状态见
> `docs/local-video-editing-roadmap.md`。下述其余决策不受影响，仍然有效。
>
> 适用范围：Control Plane 视频剪辑领域层、剪辑工作台
>
> 关联：ADR-0001（内置 Chromium 运行时）、专项 Roadmap 2.5 节、VF-01 视频领域契约

## 背景

首期产品同时存在三条视频相关工作线：两种视频制作方式（智能素材成片、品牌动效成片）产出成片，独立“视频剪辑”把素材与成片加工为新成片，内容发布把成片投递到外部平台。三者如果共用一个状态机或互相嵌套，任何一方的故障、页面改版或状态语义变化都会污染其余两方。

## 决策

### 独立模块与独立状态机

- “视频剪辑”是左侧独立菜单入口和独立后端模块，不隶属于“视频制作”，也不是发布流程的子步骤。剪辑项目、剪辑任务和剪辑成片记录与制作、发布分开展示和存储。
- `EditingJob` 使用自己的封闭状态机：`queued → running →（paused ⇄ running）→ succeeded / failed / outcome_uncertain`，任何非终态可进入 `cancelling`，`cancelling` 只能收敛到 `succeeded / failed / cancelled / outcome_uncertain`（取消可能与正在进行的处理竞争）。终态不可再转移，非法转换一律抛出 `InvalidEditingJobTransition`。状态语义与既有任务状态机保持一致：暂停是协作式的，取消未确认前不得宣称副作用已停止，结果无法确认时进入 `outcome_uncertain`，禁止自动重投。
- RenderJob（制作）、EditingJob（剪辑）、PublishJob（发布）是三个互不嵌套的状态机。跨模块衔接只通过 Artifact 谱系：制作成片 Artifact 可作为剪辑项目的输入素材；剪辑成品注册为新的 Artifact 后可进入发布链路。一个状态机不读取、不驱动、不内嵌另一个状态机的状态。

### 领域对象职责与生命周期

- `EditingProject`：用户拥有的剪辑工作区，持有标题与输入素材（`ArtifactId` 引用），生命周期由用户创建、编辑、归档/删除决定；标题属于用户内容，不进入 repr 与错误信息。
- 剪辑 `EditingTimeline`：锚定在 `EditingProject` 上的一次时间轴修订（`revision` 单调递增），轨道、片段、字幕、音频、转场完全复用 VF-01 建立的词汇（`TimelineTrack` / `TimelineClip` / `TimelineTransition`），并复用同一 `TimelineId` 类型；制作方式产出的初始 Timeline 因此可以不经翻译送入剪辑。与创作侧 `Timeline` 的唯一差异是锚点：创作侧锚定 Storyboard，剪辑侧锚定 EditingProject，不建立第二套轨道/片段/转场词汇。
- `EditingJob`：对某个冻结的 Timeline 修订执行一次剪辑，引用输入/输出 `ArtifactId`，成功必须有输出成片、失败必须有 `EditingFailureCode`，其余状态不得携带输出或失败码；输入与输出集合不得重叠。
- 成片 Artifact：剪辑不定义第二套 Artifact 或 ID 实现，输出统一注册为既有 `Artifact`（`ArtifactId` + 角色/媒体类型/摘要/谱系），由谱系记录“由哪个剪辑任务、哪些输入产出”。

### 执行边界与单向依赖

- 剪辑在**用户本机**由随包 FFmpeg 执行，不调用任何外部媒体处理服务。执行器的进程细节、命令行、临时路径与原始错误留在执行器私有边界与受限诊断区，不出现在领域对象、页面 DTO、事件、日志或导出中。
- 依赖方向单向：剪辑页面 → Control Plane 剪辑领域 → 本机执行器。`video_editing` 可以引用 `video_creation` 的共享词汇与 `resource_ids` 的稳定 ID；反向引用（创作或基础层依赖剪辑）禁止。
- 与制作方式的边界：制作方式只产出素材、成片和初始 Timeline；剪辑不知道成片来自哪种制作方式或哪个上游实现。与 `PublishJob` 的边界：发布只消费成片 Artifact 与其谱系，不读取剪辑内部状态。

## 影响

- 剪辑相关的一切后端演进都以领域层不可变、构造即校验的对象为契约起点；非法值与非法状态转换在领域层直接拒绝，不依赖上层防守。
- 执行失败与资源不足被隔离在执行器：领域状态机只感知稳定的失败码与 `outcome_uncertain`。
- 复用共享 Timeline 词汇意味着 VF 系列对轨道/片段/转场的演进会同时作用于创作与剪辑，两侧演进需要一起验证，但换来“成片一键送入剪辑”无需翻译层。

## 验收

- 本 ADR 与专项 Roadmap 2.5 节、`docs/backend-architecture.md`、`docs/frontend-architecture.md` 对同一决策无冲突。
- 任何剪辑用户功能只有从正式 App 正常用户入口按真实路径跑通、并核对磁盘上真实产出的成片终态才能标记完成；Mock 与测试替身只作为分层证据。
