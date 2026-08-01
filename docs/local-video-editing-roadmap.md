# 本地智能剪辑专项 Roadmap

## 0. 本文件的定位

本文件是「废弃云剪辑、改用随包 FFmpeg 本地剪辑」这条线全部任务的唯一状态台账，只维护任务、依赖和当前状态。

- 设计依据：`docs/superpowers/specs/2026-07-28-local-smart-edit-design.md`
- 每个任务的提交、RED/GREEN、失败矩阵、验收证据与边界单独写入 `docs/development/<任务ID>.md`
- **禁止**把完成记录、测试命令明细或历史证据追加回本文件
- 本线任务不向 `docs/development-roadmap.md` 或 `docs/embedded-browser-video-studio-roadmap.md` 双写状态
- **每条工作线**同一时间最多一个任务处于 `🧪 RED` 或 `🚧 实现中`。2026-07-29 起经用户授权开三条并行线，各自独占一棵 worktree（`wt/smart-edit` 走 Control Plane 线、`wt/le-07-probe` 走素材探测、`wt/le-09-captions` 走字幕渲染）；三条线的**代码文件**零交集；**台账、门禁脚本与设计文档是共享面**，合并时按 §4.1 处理（原写「改动文件零交集」，已被实际推翻：`dcfeb8e` 改了门禁脚本、`cf5ae10` 改了台账与设计文档，而且这个交集已经造成过一次事故——门禁在一条分支上被放宽、而那条分支看不到台账改动，被终审判为 Critical）

## 1. 为什么装配单独立项

**LE-05、LE-06、LE-17 是独立立项的装配任务，不是某个功能任务的附属步骤。**

理由来自本仓库出过的一次真实事故：领域层写完、pytest 够得着、台账全标 ✅，但 Control Plane 没有 REST API、前端注入的是 sessionStorage 草稿网关、提交按钮固定抛错——**分层实现完成，从未装配到产品路径**，而装配缺口没有落成任何一行待办任务，掉进了任务之间的缝里。台账因此长期显示「完成」，而用户打开正式包发现整块功能不可用。

所以本线把「让它真的跑在产品路径上」当成独立交付物来排期，而不是指望它顺带发生。同样的理由，LE-22、LE-23 要求在**正式安装包**上验收，而不是在测试构建上。

## 2. 状态图例

| 标记 | 含义 |
| --- | --- |
| ⬜ 未开始 | 尚未启动 |
| 🧪 RED | 已写失败测试，尚未实现 |
| 🚧 实现中 | 测试已 RED，正在写实现 |
| 🔍 待验收 | 实现与分层测试完成，缺真实环境/正式包验收证据 |
| ✅ 已完成 | 含正式 App 用户路径验收与可外部核对的终态证据 |

## 3. 任务

### 3.1 清理（1 项）

| ID | 任务 | 交付与验收 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| LE-01 | 删除云剪辑路线 | 删除阿里云 IMS 全部生产代码与测试、供应商无关 Provider 抽象层、`schema.py` 三张表声明（`aliyun_editing_intents`、`editing_output_lineages`、`editing_output_artifacts`）、`aliyun-ims-editing-staging.v1.json`、前端剪辑服务设置页与网关、Tauri 4 个 service command；**迁移文件不删而是新增 `0035` drop 迁移**（`0032` 在链中间，`0033` 指向它，删文件会断链）；从专项台账移除 VE-01～VE-08 并修正计数；新建 `scripts/check_local_editing_roadmap_counts.py` 守护本文件计数；**保留** `frontend/e2e-tauri/video-editing.spec.ts`（由 LE-17 重写而非删除）；全量测试与门禁脚本通过 | — | ✅ 已完成 |

### 3.2 领域层重写（3 项）

| ID | 任务 | 交付与验收 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| LE-02 | Material 素材库领域对象 | `Material`（kind/时长/分辨率/内容摘要/has_audio/响度/镜头边界/AI 描述与标签）、`MaterialId`、校验与去重规则；用户改过的描述不被 AI 覆盖 | LE-01 | ✅ 已完成 |
| LE-03 | Timeline 重写（含 Material 尺寸形状决策） | `TimelineClip` 补 `source_in_ms`/`source_out_ms`/`gain_db`；`TimelineTrackKind` 拆成 visual/narration/ambient/music/caption；首期锁死"取片时长等于占位时长"（不变速）并有拒绝用例；**顺带决定 `Material.width`/`height` 对音频素材的形状**——当前音频被强制要求填 [1,8192] 的宽高，属强制荒谬而非可选荒谬，改成 `int \| None` 按 kind 分叉是形状变更，LE-02 终审建议单独决策而非顺手折进去 | LE-02 | ✅ 已完成 |
| LE-04 | 剪辑项目与任务状态机 | `EditingProject`、`EditingJob`、状态转换与非法转换拒绝、失败码归类；不含任何供应商概念；并把 `Timeline` 接到 `EditingProject`（LE-03 有意未加 owner 字段）；接入时会先碰到前端 `video-editing-dto.ts` 的契约形状（见 LE-17 行三处 drift），本任务不处理前端，仅提前知会；**`EditingProject` 必须承载输出规格**——输出画幅、帧率与字幕样式基线（字号/描边/行距/字体）。LE-03 终审发现这三样是渲染必需项却无人认领：`Timeline` 没有、创作线放在 `ContentBrief.aspect_ratio` 而剪辑线无对应物、全库 `caption_style\|font_size\|stroke_width` 零命中，而 LE-10 的完成定义要求 ffprobe 断言分辨率与帧数、LE-09 承诺字幕换行描边行距可控、LE-20 承诺用户可选字体。这正是本线立项要防的「装配缺口掉进任务之间的缝里」 | LE-03 | ✅ 已完成 |

### 3.3 Control Plane 装配（2 项）

| ID | 任务 | 交付与验收 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| LE-05 | 数据库迁移与仓储 | 项目/素材/时间轴/任务表迁移、SQLAlchemy 仓储；**真实 PostgreSQL** 集成测试，断言落库行；**同任务内加结构性边界测试守住 Material 的描述保护**——`with_ai_description` 只挡住走它的调用方，`dataclasses.replace()` 与直接构造 `Material(...)` 都能到达它要阻止的状态（转换不变式无法由单快照构造校验表达，LE-02 T5 审查实跑证明）。用 AST 做法（**注意：`backend/tests/unit/executor/test_shipped_package_boundary.py` 这个样板不在本线分支上**——它由 `ddc6632` 引入，只存在于未合并的 `feature-audit` / `pc21-b`，`main` 与三条 LE 分支都没有，本线自 `5875191` 分出、比它早。所以 LE-05 要么自己从零写这个 AST 检查，要么先等那条分支合并。连带事实：三条 LE 分支的 `executor/__init__.py` 至今仍导出 `FakeExecutorEngine` 等测试替身，即 CLAUDE.md §9.2 禁止的形态，而 §9.2 点名的守卫在这里并不存在），禁止 `material.py` 之外的模块调用 `replace(` 于 Material 或直接构造它；**并承接 LE-04 新造的三条跨聚合根不变式**（领域对象不持有彼此引用，只能在仓储层成立，LE-04 终审实测三者当前全部 ACCEPTED）：① `EditingJob.project_id` 必须等于其 `timeline_id` 所属 `Timeline.project_id`——`project_id` 同时挂在两处是有意冗余，**普通外键管不住这个三角，需要复合外键或 CHECK**；② `EditingJob.timeline_revision` 必须真实存在；③ 同一 `(timeline_id, revision)` 不得同时有多个 QUEUED 作业；**T1～T5 全部完成**（四表、迁移 `0036`～`0039`、四个 SQLAlchemy 仓储、Material 描述保护的 AST 结构守卫、三条跨聚合根不变式全部由库结构而非应用层检查挡住），证据见 `docs/development/LE-05.md`；LE-17 已在 macOS/Windows 正式 App 正常入口以真实 PostgreSQL 落库 Project、Timeline、Job 与 Artifact，并由 ffprobe 核对终态，补齐原缺失的用户可操作验收 | LE-04 | ✅ 已完成 |
| LE-06 | 剪辑 REST API | `control_plane/api/` 下新增剪辑路由，首期严格对齐现有 `VideoEditingGateway` 六个操作：项目列表/创建（项目 write-once，**不做更新删除**）、时间轴读取/保存、作业列表/提交；素材登记与查询作为 LE-18 的后端前置一并交付。为项目/作业补带总序游标的仓储分页查询；Timeline 保持 write-once，`EditingJob.update(previous, changed)` 的 CAS 不得错接到时间轴路由，Worker 写回由 LE-12 消费。修订冲突的 `currentRevision` 走 `ErrorEnvelope` 严格可选 `details`，不塞 message；数据库守住一个项目唯一 timeline 身份。**T1～T6 已完成**：真实 Uvicorn、HTTP 与 PostgreSQL 纵向契约、模块门禁和逐任务 Codex Review 均已收口，证据见 `docs/development/LE-06.md`；LE-17 已从 macOS/Windows 正式 App 正常入口穿过同一 REST API 完成创建、保存、提交、轮询成功与 Artifact 核验，补齐用户路径验收 | LE-05 | ✅ 已完成 |

### 3.4 本地渲染引擎（6 项）

| ID | 任务 | 交付与验收 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| LE-07 | 素材探测 | Local Executor 侧用随包 ffprobe 读时长/分辨率/编码，`silencedetect` 判有无有效音频与响度，内容摘要去重；路径映射只存本机不上报 Control Plane。T1–T7 全部完成，三项收口已补：① `_run_probe` 与测量 sink 合到同一个 `_run_bounded`，输出落文件、边写边量、超限即杀；② `probe_material` 与 `register` 之间的残余写在两处 docstring 上，并由 `require_source_unchanged` 关掉——调用方探测前取一次 stat、登记后再交回来核；③ `resolve`→使用的窗口用同一个公开操作闭合（它内部走 `_require_source_file` 全套 + `_held_still`，消费方不再自己拼窗口，也不再需要引私有名）。已有证据：**9 类真实素材**端到端判定逐格（7 条产出事实 + 2 条拒绝）、**14 个拒绝码**逐个 provoke（真实文件 11 条，其中随包工具真读 8 条；连文件都没有 2 条；用包装脚本 1 条，即 `probe_crashed`，脚本先跑真实 ffprobe 再自杀——**不靠替身 13 条**）、每条素材的事实真造出 `Material`。修复轮（2 Critical + 10 Important + 12 单点条目）已收：暂存卷满不再报成「文件坏了」而是 `WORKSPACE_UNUSABLE`、清理失败不再改判已定拒绝码、飞行中 stat 不再把含源路径的 argv 挂上异常链、state 目录补 reparse point 与每操作重校验、消费方配方补 `approve_source`。LE-18 已在 macOS/Windows 同一提交的正式 App 入口完成真实视频/音频/图片导入，并展示时长、画幅与声音事实，补验收依赖已关闭；依据见 `docs/development/LE-07.md` T7、修复轮与收口节及 `docs/development/LE-18.md` T7 | LE-02 | ✅ 已完成 |
| LE-08 | 自适应抽帧 | `select='eq(n,0)+gt(scene,TH)'` 场景检测抽帧、长镜头按时间补抽、按时长分档封顶、超限时保切点降采样；产出 768px JPEG 并断言帧数与文件存在。**LE-07 交接**：`PackagedMediaTools` 是公开的直接用；**`_run_bounded` 是私有的不要跨模块引**，照 `_require_tool` 先例在本任务侧按同一判据重述（转公开的前置条件是把两条只对 LE-07 成立的语义写成契约——`open("wb")` 会截断、飞行中按**路径**而非 fstat 取尺寸——抽帧往同一目录写多帧未必还成立）。必须照抄的**四条硬性质**：① 输出落文件不走管道；② 边写边量；③ 超限即杀；④ **拒绝理由用返回值而不是抛异常**——第四条是 C1 的教训，抛异常会让工作区清理失败改判已定拒绝码（实测把 `undecodable` 变成 `probe_failed`）。T1～T5 全部完成；前四轮 Review 的五项 P2 均已补 RED 并修复，第五轮确认生产修复正确、另找到行级故障注入会清空 pytest-cov tracer；第六轮再补唯一台账同步与最外层 tracer 恢复的捕获力断言。最终修复提交 `954430e` 复审干净，全模块 `54 passed`，Ruff、严格 mypy、专项路线图计数与验收证据深度门禁全绿，证据见 `docs/development/LE-08.md`。**补验收依赖**：LE-13 真实消费受控 JPEG，LE-17/LE-19 接入正式 App 用户路径，LE-22/LE-23 完成 macOS/Windows 正式包纵向验收 | LE-07 | 🔍 待验收 |
| LE-09 | 字幕渲染与 fallback 机制 | PIL 渲染字幕 PNG；`fontTools` 读 cmap 实现缺字 fallback **机制**；换行、描边、行距可控。**验收判据不是「PNG 非空」——LE-09 调研实测证明那条零捕捉力**：中文字体渲染不在 cmap 的 `😀` 会画出 1226 个非零像素的实心方框（与 `.notdef` 逐字节相同），而拉丁字体渲染 `中` 画的是空白；豆腐块有墨、缺字无墨，非空断言两头都抓不住。**正确判据是与 `.notdef` 位图差分**（`font.getmask(chr(0x10FFFF))`）。**只用生产在册的 Noto Sans CJK SC 加一个在册拉丁字体验证 fallback 链路本身**，字体扩充与装配属于 LE-20，两者不得互相阻塞。**缺字且整条链都没有时 fail closed**（抛异常、带码位不带原文、不留半成品文件），不画替代符号——画了会让所有下游断言照常通过，正是 T108 事故的形状。**T1～T5 全部完成**（机制、注册表、cmap fallback、样式与字体加载、排版出图、真实面验收），证据见 `docs/development/LE-09.md`；**顶格 `🔍 待验收`**——补验收依赖 LE-10（PNG 进 ffmpeg overlay 出成片）、LE-20（字体进执行器包并有出厂门禁）、LE-17（工作台接上形成用户可操作路径），三条都不满足之前不得标完成。**原登记的两项待定已在实现中定型**：换行宽度首期为固定边距断行（回填 `CaptionStyle` 登记在 `docs/development/LE-09.md` 遗留项 4，归 Control Plane 侧），字幕位置/边距归 LE-10 的 overlay | LE-01 | 🔍 待验收 |
| LE-10 | 视频渲染管线 | trim(in/out) → scale/crop → fps 归一 → concat → `xfade` 转场 → 字幕 overlay；补齐 `ffmpeg-toolchain.v1.json` 的 `required_capabilities.filters` 声明（xfade/select/scdet 等，**无需重建 ffmpeg**）；产出 mp4 并以 ffprobe 断言编码/分辨率/帧数/时长。**输出画幅与帧率已由 `EditingProject.output`（`OutputSpec`）承载，ffprobe 断言的目标值取自它**；**并决定两项 LE-04 未覆盖的渲染必需信息**：① 16:9 素材放进 9:16 画幅时是 scale 加黑边还是 crop 裁切（两个完全不同的成片，加黑边还需填充色），`OutputSpec` 只有 width/height/fps、没有字段表达这个选择；② **毫秒时间轴与 fps 帧栅格的对账**——LE-04 终审实测 1ms 的视觉 clip 与 1ms 的 xfade 转场当前都被接受，而任何合法 fps（12–60）下一帧至少 16.7ms，这段渲不出任何一帧；且 `TimelineTrack` 要求 `start_ms == previous_end - overlap` 精确成立、`Timeline` 要求 `picture.end_ms == duration_ms` 精确相等，而渲染器必须把它们量化到帧栅格，量化误差累积后成片时长会偏离 `duration_ms`——**而本任务的完成定义恰恰是 ffprobe 断言帧数与时长**。谁对账、允许多大偏差，需在此定死；**LE-07 交接**：要不要转码只能在持有文件的本机判断——`video_codec`/`audio_codec` 只存在于执行器侧的 `MaterialFacts`，**`Material` 里没有编码字段**，Control Plane 拿不到；若确认必须跨层传，要在 domain 加字段并同步缩小 `test_material_probe_media.py` 的 `FACTS_WITH_NO_FIELD_IN_THE_DOMAIN`（那里有结构测试守着，不会静默）。依据见 `docs/development/LE-07.md`「拿探测产物真造 `Material`」。2026-08-01 已按 `docs/development/LE-10.md` 定死居中 crop、绝对边界半向上帧量化与 7 个 Task；T1～T7、逐任务 Review、macOS/Windows 既有随包候选和完整集成门禁均已完成，证据见 `docs/development/LE-10.md`；正式 App 用户路径与正式安装包验收依赖 LE-17/LE-19/LE-22/LE-23，故顶格不标完成 | LE-03,LE-09 | 🔍 待验收 |
| LE-11 | 音频管线 | 旁白/原声/BGM 三轨；`sidechaincompress` 以旁白为 sidechain 自动闪避；`has_audio` 为假时不排 ambient 轨；采样率归一；断言输出音轨时长与成片一致；**必须实现设计 §5.3 的「原声处理方式」三态开关**（自动闪避 / 固定音量 / 静音，默认自动闪避）。LE-03 终审指出模型只有 `gain_db`（对应三态里的基准音量那一维），三态本身表达不出来：静音可靠不排 ambient clip 表达，但「固定音量」需要一条**既不被旁白压、也不作为 sidechain 源**的音频通路，而五种轨道里没有这样一条——NARRATION 是 sidechain 源，AMBIENT 与 MUSIC 按 §5 都要过 `sidechaincompress`。2026-08-01 已在 `docs/development/LE-11.md` 定为 clip 级强类型三态字段，不新增第六轨、不收窄承诺；T1～T5 已全部完成，交付模型/API/JSONB、无路径协议、三态 compiler、本地 `has_audio` 门禁、共享受控 H.264 + AAC 执行、来源快照、原子发布和 1ms ffprobe 核验；AUTO/FIXED/MUTED 双平台真实 PCM 电平分别证明显著闪避/固定/静音，LE 结束全受影响 `502 passed`、完整 integration `460 passed, 17 skipped`，逐任务 Review finding 全部闭环，证据见 `docs/development/LE-11.md`；正式 App/安装包用户路径依赖 LE-17/19/22/23，故顶格不标完成 | LE-10 | 🔍 待验收 |
| LE-12 | Worker 生命周期与任务控制 | Tauri 调度渲染 Worker：随机 loopback、高熵会话令牌、健康检查、进度上报、取消与紧停、崩溃恢复、App 退出后任务恢复；`cargo test` 覆盖。**LE-07 交接三条**：① Rust 侧把已校验的 ffmpeg/ffprobe 路径下发给 Worker 的装配归本任务，Python 侧接收端（`PackagedMediaTools`，不发现、不查 PATH、不读环境变量）已交付；② 同一批编码事实（`video_codec`/`audio_codec`）只在执行器侧，见 LE-10 行；③ 探测与测量都需要可写暂存空间，卷满时产出 `WORKSPACE_UNUSABLE`（不是「文件坏了」也不是「重试」），Worker 的失败上报要能表达这一类。T1～T5 已交付认证 stdin 工具对、path-free 作业协议、取消/紧停、App 私有原子账本、有限进程/App 重启恢复，以及 macOS/Windows 同目标真实进程验收；认证事件先落盘再暴露，generation 与 job 恢复预算独立持久，完整 Tauri `cargo test` 全绿，证据见 `docs/development/LE-12.md` | LE-11 | ✅ 已完成 |

### 3.5 AI 编排（4 项）

| ID | 任务 | 交付与验收 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| LE-13 | 素材理解 | 抽帧送百炼多模态，产出描述、标签与镜头时间区间并写回 Material；**关闭深度思考**（`enable_thinking=false`，作为配置项非硬编码，见设计文档 §4.5）；超时/拒答/空描述/token 超限的失败矩阵；真实模型调用验收。T1～T4、Codex Review finding 修复与真实纵向复验全部完成，证据见 `docs/development/LE-13.md` | LE-08 | ✅ 已完成 |
| LE-14 | 人声检测与转写 | 三级漏斗：`silencedetect` 判有无声音 → Silero VAD（本地 ONNX，约 2 MB）判是人声还是纯环境音 → 仅对人声素材调百炼语音识别转写（只传音轨不传视频，避免为筛选步骤打包 140 MB+ 的本地 Whisper）。产出 `has_speech`、`speech_segments_ms`、`speech_transcript` 写回 Material；**onnxruntime 与 VAD 模型必须进安装包并有出厂门禁核对**；纯音乐误判人声、人声判成环境音、转写为空、方言/嘈杂环境、ASR 超时、路人背景说话被当主体的失败矩阵。T1～T5、真实纵向验收与模块收口门禁均已完成；四轮 Review 共十三项 finding 与一项同类自审问题均已按 TDD 修复，最终完整复审无 finding，证据见 `docs/development/LE-14.md`。**补验收依赖**：LE-17 接入正式 App 用户入口，LE-22/LE-23 完成 macOS/Windows 正式包纵向验收 | LE-07 | 🔍 待验收 |
| LE-15 | 文案分句与旁白合成 | 一句话经百炼文本模型产出脚本分句（**关闭深度思考**，见设计文档 §4.5）；TTS 合成每句并取**真实音频时长**；支持用户上传录音替代（转写后对齐句子，复用 LE-14 ASR）。T1～T4 全部完成；首轮模块收口相关测试 258 条、整目录 integration 458 条、三个自有模块 604 条语句/214 分支 100%。T4 首轮 Review 的四项 P2 已补四条 RED，驱动契约现为 7 条；真实复验终态为 3 个音频、`4320/5040/5680 ms`，第二轮 Review 无 finding。**补验收依赖**：LE-17/LE-19 接入正式 App 用户路径，LE-22/LE-23 完成 macOS/Windows 正式包纵向验收，证据见 `docs/development/LE-15.md` | LE-06,LE-14 | 🔍 待验收 |
| LE-16 | 语义匹配与片段选择 | 句子与素材描述语义匹配（有转写的素材把转写文本一并纳入匹配依据；**关闭深度思考**，见设计文档 §4.5）；在选中素材的镜头区间内挑最贴片段产出 in/out 点；**有人声素材按"自带旁白片段"编排：时长由原声内容决定、独立占段、用原声与转写字幕、不配 TTS**；文案句子只分配给无人声素材；素材不足、单条过短、匹配全低于阈值、全部素材均有人声的处理；产出 Timeline 草稿。**LE-07 交接的硬约束**：探测报出的 duration 取自容器头，**不保证时长内每一帧都存在**——实测 faststart 截断件（半个下载）报出的是完整时长，截到 25% 仍报 60000 ms 而实际只剩 268/1500 帧，且它不产生任何拒绝码、按码分支看不见；挑 in/out 点不得默认「时长内帧完备」。T1～T5、逐任务 Review、分层功能验收与真实 PostgreSQL 纵向落库均已完成，证据见 `docs/development/LE-16.md`；正式 App 用户路径依赖 LE-19/LE-22/LE-23，完成前顶格 `🔍 待验收` | LE-13,LE-15 | 🔍 待验收 |

### 3.6 前端接线（4 项）

| ID | 任务 | 交付与验收 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| LE-17 | 工作台接真实网关 | `main.tsx` 生产组合根从 sessionStorage 草稿网关换成真实 Control Plane 网关；提交路径打通不再固定抛错；重写 `e2e-tauri/video-editing.spec.ts`，断言目标从"诚实展示不可用"改为真实出片；**同任务内必须重写前端 Timeline 契约**——`video-editing-dto.ts` 那份手写 zod 副本与新后端模型**整体不兼容，需按 `timeline.py` 重写而非逐点修补**（drift 由 LE-03 在 T2/T3/T4 改后端时造成，终审核基线 `d00f547` 确认此前前后端逐字同步）。LE-03 终审用真实 zod 探针实测：带转场的后端时间轴、带 `sourceInMs`/`sourceOutMs`/`gainDb` 的 clip、`narration`/`ambient`/`music` 三种轨道，**前端一律拒绝**。分歧至少七处：① `timelineClipSchema` 是 `z.strictObject` 且缺三个新字段，后端快照是硬报错不是静默忽略；② 仍用 `sourceArtifactId`（`ArtifactId`），后端已改指 `MaterialId`；③ 布局 refine 对所有轨道要求「不许重叠」，而新后端转场靠真实重叠实现；④ 无画面轨首尾相接、`visual.end == duration`、每种轨道至多一条等核心不变式；⑤ `transitionKindSchema` 仍含 `"cut"`；⑥ `timelineTrackKindSchema` 仍是单一音频轨；⑦ `MAX_TRACKS = 32`（应为 5）。2026-08-01 已按 `docs/development/LE-17.md` 拆为六项；T1～T6 已完成领域契约、Rust/Tauri 边界、真实前端网关、生产组合根接线、工作台真实状态/刷新语义、取消终态审查修复和双平台真实出片。最终提交 `904f2075` 在 macOS/Windows 各由同一非 ignored 正式 App 驱动得到 `1 passing`，ffprobe 均为 H.264 `720×1280@20`、20 帧、1 秒；完整 backend integration `460 passed, 17 skipped`，模块门禁与最终 Review 全部闭环，证据见 `docs/development/LE-17.md` | LE-06 | ✅ 已完成 |
| LE-18 | 素材库界面 | 导入素材、展示 AI 描述与标签、**标注哪些素材有人说话并可试听/查看转写**、编辑描述、去重提示、缺失素材提示；缺失素材严格区分 `FILE_MISSING`、`FILE_UNREADABLE`、`FILE_CHANGED`，并保留 `UNDECODABLE` 稍后重试、`SOURCE_NOT_AT_REST` 等待写完与 `WORKSPACE_UNUSABLE` 本机暂存空间动作。T1～T7 已完成注册表幂等 forget、Control Plane 分页/受约束删除与引用投影、双平台冻结 Worker 四步导入/补偿、Rust/Tauri 原生协调、仅凭 Material ID 的本机 Range 预览、严格 DTO/gateway/正式工作台 UI，以及 macOS/Windows 同一 SHA 的正式 App 正常/失败纵向验收。真实旅程覆盖视频/音频/图片、去重、取消 picker、预览、人工描述、引用冲突、未引用素材删除和三类本机状态；完整 integration 与模块门禁、最终 Review 均已收口，证据见 `docs/development/LE-18.md` | LE-17 | ✅ 已完成 |
| LE-19 | 智能剪辑入口 | 一句话输入 → 生成 Timeline 草稿落进工作台；"一键直出片"跳过审阅走同一生成器；进度与取消可见；**剪辑模块的产品形态在此最终确定，同任务内复核 `contracts/quality/user-facing-terminology.v1.json` 的 `video_editing_module` 条目与实际界面一致，并让当前已绿的 `check_user_facing_branding.py` 保持绿色**（见 §7 现状）。2026-08-01 已按 `docs/development/LE-19.md` 拆为七项并进入 T1 RED；开工审计确认正式 App 当前只有手工时间轴与渲染入口，LE-13～16 的分析/生成能力尚未装配到 Worker/Tauri/界面，不能用预置数据库事实冒充纵向路径 | LE-16,LE-18 | 🧪 RED |
| LE-24 | 深度思考开关与耗时告知 | 智能剪辑入口的高级选项里增加一个总开关，统一控制素材理解、文案分句、语义匹配三处模型调用是否开启深度思考；默认关闭；用户偏好持久化并在下次进入时保持；**开关旁必须显示开启后预计多花的时间，该数字由实测得出，禁止估计或编造**——先用固定素材集实测开/关两种模式的端到端耗时差，若耗时随素材条数线性增长则按条数动态计算展示（如"约多花 3 秒 × 素材条数"），否则展示实测区间；实测数据与计算方式写入 `docs/development/LE-24.md`；用户可见文案无未解释术语，过 `check_user_facing_branding.py`；开关状态真实传达到模型请求参数，断言请求体中该字段随开关变化 | LE-16,LE-19 | ⬜ 未开始 |

### 3.7 字体（1 项）

| ID | 任务 | 交付与验收 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| LE-20 | 中文字体扩充与装配 | **本任务的前提已被 LE-09 调研推翻，开工前必须先重估必要性**：设计文档 §6.1 称「Noto Sans SC 覆盖约 3 万字、扩展 B 以上是豆腐块」，但生产在册的中文字体其实是 `contracts/quality/asset-rights-policy.v1.json` 锁的 **Noto Sans CJK SC**（静态 OTF，`Sans2.004`，由 `scripts/subtitle_font_assets.py` 按 SHA-256 取到构建缓存），LE-09 实测 `𠮷`（U+20BB7，扩展 B）在其 cmap 中映射到真实字形 `cid59625`。**先量清楚它到底缺哪些字**，再决定是否引入 Plangothic/文津宋体/霞鹜文楷 GB；若确需引入：锁版本、锁 SHA256，登记许可证与 SBOM，**必须有生产装配路径与出厂门禁**，不允许只有测试路径；用户可选字体。**装配整体归本任务**（LE-09 只交付机制，完成后最多 `🔍 待验收`）。原先硬编码的 `local-executor: 177 MiB` 已在 LE-14 T2 按最小 ONNX Runtime 实包重测并更新为 246 MiB；若本任务再引入字体资产，仍须按最终实包重新核对声明负载与正式包上限 | LE-09 | ⬜ 未开始 |

### 3.8 验收（3 项）

| ID | 任务 | 交付与验收 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| LE-21 | 失败矩阵联合验收 | 设计文档 §8 全部场景；素材消失、磁盘满、权限拒绝、渲染超时、进程被杀、取消竞争、App 退出恢复均有测试 | LE-12,LE-19 | ⬜ 未开始 |
| LE-22 | macOS 正式包纵向验收 | 从全新安装的正式 App 正常入口导入本地素材 → 一句话生成草稿 → 出片 → 成片入库并可播放；素材含至少一条有人声素材，核对它走的是原声而非 TTS；证据含 ffprobe 读数与产物尺寸 | LE-20,LE-21 | ⬜ 未开始 |
| LE-23 | Windows 正式包纵向验收 | 同 LE-22，在 Windows 正式包上独立完成；核对随包 ffmpeg、VAD/ASR 运行时与字体在 Windows 包内真实存在 | LE-22 | ⬜ 未开始 |

## 4. 进度

任务总数与各状态计数由 `scripts/check_local_editing_roadmap_counts.py` 守护，只在此处记录一次：

- 任务总数：24
- ✅ 已完成：11
- 🔍 待验收：7
- 🧪 RED / 🚧 实现中：1
- ⬜ 未开始：5

## 5. 历史进度与当前下一步

**LE-18 已完成；当前按依赖进入 LE-19 智能剪辑入口。** 下文保留 LE-10～LE-18 的推进背景，
不再把其中“下一步进入 LE-17”的历史句子解释为当前状态。

**LE-12 T1～T5 已完成；下一步进入 LE-17 工作台接真实网关。** LE-10 已交付从真实领域投影、绝对
帧栅格、VIDEO/IMAGE 居中 crop、硬切/连续 xfade、LE-09 字幕 overlay 到受控 FFmpeg、
ffprobe 核验、摘要和原子发布的完整分层实现。macOS/Windows 既有随包 FFmpeg 8.1.2 候选
均通过 18 项精确滤镜能力合约；组合真实链在两端均得到 H.264 `720×1280@20`、20 帧、
1000ms、无音轨的唯一 `render.mp4`。LE 结束门禁为 567 条受影响回归、执行模块 100% 分支
覆盖、VF-04 9 条 Rust 测试和完整 integration `464 passed, 8 skipped`。T7 Review 找到并
修复能力名称子串误判，双平台复验无新增 finding。正式 App 用户路径与安装包验收仍由
LE-17/LE-19/LE-22/LE-23 关闭，因此 LE-10 顶格保持 `🔍 待验收`。LE-11 已定为在
`AMBIENT` clip 上增加自动闪避/固定音量/静音强类型字段，同轨可逐片段切换，静音也不会因
删除 clip 丢失用户选择；T1 的领域/API/JSONB/OpenAPI 与历史 Timeline 兼容均已完成。T2
已交付无路径音频计划、完整领域投影和确定性 sidechain filter graph，macOS/Windows 真实
音频图均为 48kHz stereo、1000ms；T3 已把 LE-07 的 `has_audio` 事实和本地绝对路径精确
绑定到计划，两端真实进程都证明无声 ambient 与 muted 素材不进入 FFmpeg 输入。T4 已把
音频图装配进 LE-10 共享受控执行，两端真实成片均通过 AAC/48k/stereo 与 1ms 音轨时长的
ffprobe fail-closed 核验。T5 又以同源三份真实成片的 PCM 频段电平证明自动闪避、固定音量
和静音确实可听地区分，并完成全量集成与最终 Review。LE-11 顶格保持 `🔍 待验收`；现在按
依赖顺序进入 LE-12。T1 已把 Rust 校验过的 ffmpeg/ffprobe 唯一工具对只经认证 stdin 交给
Python 本地剪辑 Worker，Python 侧精确解析并直接构造 `PackagedMediaTools`；双平台专项通过，
Windows 专属测试目标的历史构造参数漂移也由真机编译 RED 修复。T2 又在同一 HMAC 通道交付
path-free 作业命令、单调进度、协作取消竞态和进程树紧停，并保持 `workspace_unusable` 独立失败
分类。T3 又交付 App 私有原子账本和唯一调度器，认证事件先落盘再暴露，错误权限/链接/reparse/
超限/损坏 schema 均 fail closed。T4 再把真实进程崩溃、有限 generation 恢复、恢复预算、App
组件销毁重建和启动期两阶段 checkpoint 预检闭环。T5 已在 macOS/Windows 用同一非 ignored 目标
启动真实本地剪辑 Worker，覆盖成功、取消、紧停、崩溃恢复和 App 重开恢复；完整 Tauri 测试全绿。
现在按依赖顺序进入 LE-17，重写前端 Timeline 契约、接通生产 Control Plane 网关并恢复真实出片 E2E。

## 7. 用户可见文案门禁现已恢复绿色

合入 main 的真实工作台功能后，`OperationsWorkspace.tsx` 已有「视频剪辑」及
「独立于视频制作」的实际说明，不再靠已删除设置卡片中的“视频剪辑服务”巧合满足契约。
2026-07-31 在 LE-14 模块收口实跑 `python3 scripts/check_user_facing_branding.py`，
结果为 `user-facing branding and plain-language scan passed (54 frontend, 284 native files)`。

LE-19 仍负责按最终产品形态复核 `video_editing_module` 契约，但目标改为保持门禁绿色，
不再把这里登记成当前已知红项。

### 7.1 覆盖率与全库门禁（LE-03 实测登记，均非本线引入）

**`material.py` 覆盖率缺口，归属 LE-02。** 实测：在 LE-02 收口提交 `495e922` 上跑 `test_material.py` 得 90%、11 行未覆盖；LE-03 T1（改动 `material.py` 的宽高分叉）之后是 91%、仍是同样 11 行——T1 自己的新代码全覆盖，既没恶化也没改善。门禁 `fail_under = 100`（`backend/pyproject.toml:89`），也就是说 **LE-02 标 ✅ 已完成时，覆盖率门禁在 `material.py` 上就是红的**。归属 LE-02，非本线引入，本线也未修复。

**后端 CI 有三个全库红的门禁，均非本线引入。** `ruff check .` 约 98 个错误、`mypy` 17 个错误、`pytest --cov` 的 `fail_under = 100`；三者都在 `.github/workflows/quality.yml` 的 backend job 里（第 57、58、61 行），意味着该 job 在 main 上本来就过不去。LE-03 T5 复核（2026-07-29）：`ruff check .` 仍 98 个（与 LE-03 改动前后一致，未新增）；`mypy` 仍 17 个，分布跨 8 个文件（`src/automation_tool/executor/` 5 个、`tests/integration/` 3 个、`tests/unit/`（非 executor 子目录）3 个、`tests/unit/executor/` 6 个），并非此前记录所说的"全在 `tests/unit/executor/` 下"，但总数一致，未新增。详见 `docs/development/LE-03.md`。

**验收证据深度门禁已补交。** `scripts/check_acceptance_evidence_depth.py` 现已存在；
LE-14 模块收口实跑 `acceptance evidence depth: 40 checks passed`。此前 LE-03 登记的
“脚本不存在”事实已失效，不再作为遗留项。

## 6. 首期不做

以下记录在案，首期不实施：

- **素材驱动编排**：AI 理解全部素材后自行组织叙事顺序
- **节奏驱动编排（卡点）**：分析 BGM 节拍，画面卡鼓点切换
- **模板驱动编排**：选成片模板、素材填槽位（可复用品牌动效成片的 12 套风格与槽位机制）
- **变速适配**：`source_out_ms - source_in_ms` 与 `duration_ms` 不等时的变速
- **长视频自动剪成多条短视频**
- **说话人分离**：同一条素材里多人对话时区分说话人（LE-14 只判断有无人声并整体转写，不做 diarization）
- **重新构建 ffmpeg 以获得 drawtext/libass**：首期字幕走 PIL，不重建

## 8. LE-01 终审遗留（有 owner，未在 LE-01 内解决）

全分支终审（25 提交）确认删除本身可合并，但留下六项，按归属分列。

### 8.1 需用户决定：阿里云凭据已无消费者

`docs/credentials-aliyun-video-editing.md` 含真实 RAM AccessKey 明文，入库理由是「视频剪辑模块 VE-04+ 的 OSS 暂存与 IMS 云剪辑验收」。LE-01 删除了它的全部消费者：两个 VE 验收驱动，以及 `run_cq_04_acceptance.py` 对 `.local/secrets/aliyun-video-editing.json` 的探测。该密钥现在没有任何用途。

**该密钥已进入 git 历史，删除文件并不能移除它——唯一有效的补救是在阿里云控制台轮换或禁用。** 文件本身写明「不要顺手清理；如迁移到更安全的方案需用户确认」，故 LE-01 不擅自处理。`docs/development/RESEARCH-cloud-deployment-readiness.md:395` 仍指示把该密钥预置进 Demo Profile，一并待处理。

### 8.2 归 LE-17：工作台四句文案已成假话

`VideoEditingWorkbench.tsx:63,553,577,600` 仍写着「云端剪辑功能尚未开通」「视频画面预览将在云端剪辑服务接入后提供」等。这些话在 LE-01 之前为真，之后为假——不会再有云端剪辑服务，替代方案是本地 FFmpeg。由 `VideoEditingWorkbench.test.tsx:143,160` 钉住。两个删除守卫只查模块与文件存在性，不覆盖文案。LE-17 重写工作台时一并改。

### 8.3 归 LE-19：文案门禁缺机制化豁免

§7 的「必须恰好两条」目前只是散文约定，而 `check_user_facing_branding.py` 是 `.github/workflows/quality.yml` governance 作业的必跑步骤，在 LE-02 到 LE-19 这段长窗口里，第三条出现在一个已经红的门禁里不会有人发现。建议在 checker 内把这两条编码为显式豁免并加测试断言「恰好这两条」，LE-19 转绿时一并删除。

### 8.4 归后续文档任务：架构基线仍描述已删结构

`docs/development-roadmap.md:25,27`、`docs/project-structure.md:100`、`docs/backend-architecture.md:913-921`（描述已删的 `video_editing.py`、`EditingProject`、`VideoEditingProvider` 契约与「首期只接阿里云 IMS/ICE」）、`docs/frontend-architecture.md:339`（「剪辑入口由 VE-03 交付」）。CLAUDE.md §1 把这些列为必读基线，冲突不得静默保留。

### 8.5 归后续：Demo 检查清单含误导性判据

`docs/demo-preflight-checklist.md:690` 用「视频剪辑服务凭据表单是否够宽」判断装的是不是旧包，而该卡片已整个删除，操作者会看到卡片消失而误判为包坏了。另见 `:603,620-621,681,686,709,719`（引用已删除的 Playwright 用例名）。

### 8.6 归 LE-17：保留的 E2E spec 无执行归属

`frontend/e2e-tauri/video-editing.spec.ts` 按决定保留待 LE-17 重写，但其唯一执行方 `run_ve_03_acceptance.py` 已删除，也没有任何 `wdio.*.conf.ts` 引用它；`check_acceptance_driver_ownership.py` 只审计驱动不审计 spec，故无人会报告它无主。它也不在 `no-cloud-editing.test.ts` 的 `RETAINED_FILES` 里，「保留」这个决定当前没有任何东西钉住。
