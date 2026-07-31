# 方案：作品发布的视频来源接线（`selectedVideo` 与 B站线）

> 状态：**方案待批准**，本轮未写任何生产代码、未改动任何既有文件、未提交、未启动 App /
> 浏览器 / 构建 / 测试。
>
> 日期：2026-07-26
>
> 基线提交：`f710026`（`test(wiring): 装配核对改为检查传的是不是真网关`）
>
> 起因：`docs/development/completed-task-wiring-audit-20260726.md` 第 3.2 节两个断点，
> 以及该报告第 8 节第 7 条明说"没有结论"的问题——`selectedVideo` 应该由谁提供。
>
> **历史方案提示（2026-07-31）**：本文件中的 B站缺口判断与分阶段建议已执行完毕，
> 不再代表当前实现状态。现状与验证证据以 `docs/development/PB-07.md` 末节为准。

---

## 0. 与审计报告的偏差说明

以当前 HEAD 复核，审计报告的证据全部成立，两处需要更正：

| 项 | 审计报告 | 当前 HEAD 实际 |
| --- | --- | --- |
| 发布页路径 | `frontend/src/features/publishing/PublishWorkspace.tsx` ✅ 正确 | 同上。（本任务派单说明里写的 `features/publish/` 是笔误，实际目录名带 `ing`） |
| `production-wiring.test.ts` | 描述的是旧版（11 项、只查"构造了"） | 已被 `f710026` 重写：现在核对"每个网关 prop 传下去的变量必须绑定到 `new Tauri…()`"，并有一条 `it.fails` 把 `videoEditingGateway` 的现状钉在测试里。**但仍然不检查 `selectedVideo`**——它不是网关，不在 `REQUIRED_TAURI_PROPS` 里，第 115 行"每个构造出来的网关都得用上"的反向检查也覆盖不到一个从未被构造的东西 |

行号全部复核一致（`main.tsx:56-74`、`PublishWorkspace.tsx:137-140`、`lib.rs:3745`、
`publish_workspace.rs:316`）。

---

## 1. 现状事实

### 1.1 断点 A：`selectedVideo` 在正式 App 里恒为 `undefined`

完整传递链，每一环都在，只有源头是空的：

| # | 环节 | 位置 | 事实 |
| --- | --- | --- | --- |
| 1 | 生产装配 | `frontend/src/main.tsx:54-75` | `createRoot().render(<App …/>)`，`<App>` 在 `:56`，16 个 props 在 `:57-73`。**没有 `selectedVideo`** |
| 2 | App 层 | `frontend/src/app/App.tsx:50` / `:72` / `:91` | prop 声明为可选、解构、原样透传 `undefined` |
| 3 | Shell 层 | `frontend/src/app/WorkbenchShell.tsx:353` / `:372` / `:525` | 同上，原样透传 |
| 4 | 渲染条件 | `frontend/src/features/publishing/PublishWorkspace.tsx:137-140` | `platform.availability === "ready" && snapshot.stage === "idle" && selectedVideo !== undefined` 三者同时成立才渲染「发布到 X」按钮 |
| 5 | 同一条件的第二处 | 同上 `:222` | 「重新发布」按钮同样要求 `selectedVideo !== undefined` |
| 6 | 唯一生产者 | `frontend/src/test-harness/main.tsx:58` + `frontend/src/test-harness/publishing.ts:120-126` | `HARNESS_SELECTED_VIDEO` 常量；另有 `PublishWorkspace.test.tsx` 的测试替身 |

`SelectedVideo` 的形状（`PublishWorkspace.tsx:47-53`）：

```ts
{ publishJobId, artifactPath, videoSummary, title, description }   // 全部 string
```

组件注释（`:37-43`）写明了设计意图：**没有选定视频就不该给按钮**，因为"提供一个会投出
未指定文件的按钮，比不提供更糟"。这个判断是对的，问题不在这里，在于没有任何界面能产出
这个选定视频。

### 1.2 成片从产出到可被发布，中间缺的每一环

成片本身是**存在且可枚举的**，缺的是选择、标识转换和输入三件事。

**已经有的（不需要新建）：**

| 事实 | 位置 |
| --- | --- |
| 成片落盘位置 | `frontend/src-tauri/src/video_job_workspace.rs:13`——`<app_data>/video-workspaces-v1/artifacts/<artifactId>/{payload,manifest.json}` |
| 成片清单来源 | 同文件 `:566-581` `VideoJobWorkspaceStore::list_artifacts()` → `VideoArtifactRecord{artifact_id, job_id, sha256, size_bytes, media_type, role}` |
| 两条制作线都往这里落 | `material_video_studio.rs:284-291` 与 `motion_video_studio.rs:857-862`，都用 `media_type="video/mp4"`、`role="rendered_video"` |
| 已有可枚举成片的 Tauri Command | `lib.rs:296` `get_material_render_jobs`、`lib.rs:674` `get_motion_render_jobs`；两者都遍历**全部** workspace（`material_video_studio.rs:241-259`），不是只报当前活动任务 |
| 前端已经在用它们 | `VideoStudio.tsx:674-675` 过滤出 `artifactId !== null` 的任务，`:682-745` 渲染「已校验成片」卡片 |
| 已有单条成片读取 | `lib.rs:702` `read_motion_video_artifact` → base64（`VideoStudio.tsx:948-952` 用它做 `data:` 播放） |
| 成片自带的展示字段 | `subject`（两条线都有）、`artifactSizeBytes`、`styleDisplayName`（仅动效线） |

**缺的（本任务要建的）：**

| # | 缺口 | 证据 |
| --- | --- | --- |
| A | **成片页没有「去发布」** | `VideoStudio.tsx:696-745` 每张成片卡片只有「播放成片」和「删除成片」；全文件 grep `发布` 只命中 `:128`（动效方式说明文案里的"产品发布"）与 `:760`（默认草稿主题"新品发布"），没有发布入口 |
| B | **没有跨页选择状态** | `WorkbenchShell.tsx:59-68` 八个导航项各自独立渲染，`VideoStudio` 与 `PublishWorkspace` 之间没有任何共享状态或跳转回调 |
| C | **`artifactId`（UUID）→ `artifactPath`（绝对路径）没有转换方** | `VideoJobWorkspaceStore` 的 `artifact_directory()`（`video_job_workspace.rs:666-669`）是**私有**的；对外只有 `open_artifact()` 返回一个 `Reader`，拿不到路径。要接线必须新增一个受控的路径解析方法 |
| D | **`begin_publish` 直接吃 React 传来的裸路径** | `lib.rs:1244` `artifact_path: String` → `:1281` `std::path::PathBuf::from(artifact_path)`，Rust 侧**零校验、零 containment**。执行器侧 `publish_artifact.py:81-122` 会验绝对路径 / 非软链 / 普通文件 / 大小上限，但**不约束它必须在 App 自己的成片目录内**——也就是说这个契约允许 WebView 指定本机任意 mp4/mov |
| E | **绝对路径进 React 违反架构基线** | `CLAUDE.md` §4.1「业务页面不得直接导入 `@tauri-apps/*`」与 §7「本机私有路径不得进入普通日志或错误响应」，专项 Roadmap §4.1「React 和 Control Plane 看不到浏览器绝对路径」是同一条边界。现在的 `SelectedVideo.artifactPath` 要求 React 持有并回传一个本机绝对路径 |
| F | **载荷文件名没有扩展名，执行器会拒** | 成片实际落在 `<artifacts>/<id>/payload`（`video_job_workspace.rs:19` `ARTIFACT_PAYLOAD = "payload"`）。执行器 `publish_artifact.py:125-130` `_require_media_type()` 用 `path.suffixes` 判定，要求恰好一个后缀且必须是 `mp4`/`mov`。**直接把 `payload` 路径交给执行器必然 `DouyinPublishArtifactRejected`** |
| G | **标题 / 简介没有输入口** | `SelectedVideo` 要求 `title`、`description`，成片记录里只有 `subject`。全 App 没有任何发布文案输入控件 |
| H | **`publishJobId` 是将就出来的** | `PublishWorkspace.tsx:186-189` 把 `confirmationId` 当成 `publishJobId` 回传。`docs/development/PB-07.md`「真实边界」第 6 条自己写明这是接线未完成留下的将就，"随素材来源接线一并修正" |

### 1.3 断点 B：B站恒为「待配置」，且**不止是没有配置入口**

| 环节 | 位置 | 事实 |
| --- | --- | --- |
| 硬编码 | `frontend/src-tauri/src/lib.rs:3744-3746` | `PublishWorkspaceState(Mutex::new(PublishWorkspace::new(false)))`，`official_credentials_configured` 恒 `false` |
| 判定 | `frontend/src-tauri/src/publish_workspace.rs:311-322` | `Bilibili if configured => Ready`，否则 `AwaitingConfiguration` |
| 用户看到的文案 | `frontend/src/features/publishing/publish-workspace-gateway.ts:169-170` | 「该平台还没有配置发布凭据，**配置后即可使用**」——这句话当前没有任何东西能兑现 |
| 无配置 UI | `frontend/src/app/WorkbenchShell.tsx:529-543` | 「设置与诊断」只有 `ModelServiceSettings` + `VideoEditingServiceSettings` + `Diagnostics` + 开源许可入口 |

**审计报告没写、但更要紧的一条：平台参数根本没有进到执行链路。**

`begin_publish`（`lib.rs:1241-1330`）只在 `:1260` 用 `platform` 做了一次可用性判定，
之后一路走：

```
lib.rs:1263  resolve_embedded_browser(&authority)          // 内置 Chromium
lib.rs:1264  profiles.current_douyin_profile()             // 抖音运营 Profile
lib.rs:1276  service.execute_publish_command(...)          // executor_platform.rs:543
             → executor_manager.rs:493  LocalPlatformCommand::PreflightDouyinPublish
approve_publish → executor_manager.rs:517  LocalPlatformCommand::DispatchDouyinPublish
```

也就是说：**今天如果把 `PublishWorkspace::new(false)` 改成 `true`，点「发布到B站」会打开
抖音运营 Profile 的浏览器去执行抖音发布流程。** 这是一个被 `false` 掩盖着的真实缺陷，
不能顺手翻这个开关。

**服务端 B站链路完全不可达**（复核确认审计结论）：

- `bilibili_archive_publishing.py`、`bilibili_archive_reconciliation.py`、
  `infrastructure/bilibili/{signing,material,open_api_client}.py`、
  `infrastructure/database/bilibili_publish_repository.py` 的**全部非测试引用方为零**；
  仓库里只有 `backend/tests/**` 12 个文件和 `scripts/run_pb_08_acceptance.py` 提到它们，
  而后者的 `PUBLISH_SUITES`（`run_pb_08_acceptance.py:50-53`）只跑两个抖音套件，
  脚本 docstring 自己写明"真实平台投稿不在本脚本内"；
- `backend/src/automation_tool/control_plane/api/` 20 个路由模块中无 bilibili；
- `BilibiliAccessTokenProvider`（`bilibili_archive_publishing.py:357`）是一个 Protocol，
  **生产侧没有任何实现**——意味着连"拿到 access_token"这一步（B站开放平台 OAuth 授权）
  都还不存在，不只是"没地方填 client_id/app_secret"。

### 1.4 BU-02～BU-05 的处境

Browser Use 四项的唯一生产消费者是抖音发布链路（`platform_commands.py:39-58` 的三个
`douyin.publish.*` 命令族），入口只有 `begin_publish` / `approve_publish`。断点 A 一通，
它们同时可达；断点 A 不通，它们点不到。**本方案不需要为 BU 单独接线。**

---

## 2. `selectedVideo` 来源：规划里的既有定论

**这个问题规划里已经定了，不需要另创产品形态。**

| 出处 | 原文 | 含义 |
| --- | --- | --- |
| `docs/embedded-browser-video-studio-roadmap.md` §7「用户界面信息架构」第 6 步 | 「成片：预览、下载、保留、删除和**进入现有发布链路**」 | 成片页是发布链路的入口，成片页把选中的成片交给发布页 |
| 同文件 §2.5 | 「生成完成后可**把素材一键送入**剪辑模块」 | 同一种"从产物页一键交接到下游模块"的既定交互模式 |
| `docs/development/PB-07.md`「遗留项」 | 「素材来源接线（视频产物 → 发布）｜ 未做，PB-07 之后的独立接线任务」 | 方向就是"视频产物 → 发布"，且已预告是一个独立任务 |

所以下面两个候选方案里，**方案一是规划已定的形态，方案二是它的替代品**，列出来是为了把
取舍讲清楚，不是把已定的事重新开一次会。

### 方案一（规划已定）：成片页「去发布」→ 跳转到发布页并带上选中成片

**用户操作路径：**

```
视频制作 → 成片 → 某张成片卡片「去发布」
    → 自动切到左侧「作品发布」
    → 顶部出现「待发布视频」卡片（主题、大小、制作方式，可「换一个」退回成片页）
    → 填标题与简介
    → 平台卡片上出现「发布到抖音」
    → 临界点确认 → 发布
```

**需要新增 / 修改：**

| 层 | 改动 |
| --- | --- |
| Rust 工作区 | `video_job_workspace.rs` 新增受控的成片载荷路径解析（含 `role`/`media_type` 白名单校验），并解决 §1.2-F 的扩展名问题 |
| Rust 命令 | `begin_publish` 的入参从 `artifactPath: String` 改为 `artifactId: Uuid`，路径在 Rust 内解析；新增 `VideoJobWorkspaceStore` 依赖注入 |
| 契约 | `contracts/publishing/publish-workspace.v1.json` 更新（发布请求的素材标识形状） |
| 前端网关 | `publish-workspace-gateway.ts` 的 `PublishRequest.artifactPath` → `artifactId`；`SelectedVideo` 同步 |
| 前端页面 | `VideoStudio.tsx` 成片卡片加「去发布」按钮 + 回调 prop；`WorkbenchShell.tsx` 持有 `selectedVideo` 状态并在回调里切页；`PublishWorkspace.tsx` 增加「待发布视频」卡片与标题/简介表单 |
| 装配 | `main.tsx` 不需要新增 prop——`selectedVideo` 由 `WorkbenchShell` 自己的状态产生（见下方"为什么不从 `main.tsx` 传"） |
| 装配测试 | `production-wiring.test.ts` 增加对"发布页拿得到成片来源"的断言 |

**与既有信息架构是否一致：** 完全一致（Roadmap §7 第 6 步）。且不在发布页复制第二份成片
列表，符合项目「代码复用原则」和「写代码前必须先查现有可复用资源」。

**工作量：** 中。前端 3 个文件 + Rust 2 个文件 + 1 份契约。风险集中在 Rust 侧的路径解析
与扩展名（§1.2-C/F）。

### 方案二：发布页自带成片选择器

**用户操作路径：** 直接进「作品发布」→ 页面自己列出本机全部成片 → 选一个 → 填文案 → 发布。

**需要新增 / 修改：** 方案一的 Rust 侧改动一样不少，另外要在 `PublishWorkspace` 里引入
`MaterialVideoStudioGateway`（或新建一个成片查询网关），让发布页去调
`get_material_render_jobs` + `get_motion_render_jobs` 并渲染第二份成片列表。

**与既有信息架构是否一致：** 不一致。Roadmap §7 把成片管理明确划给「视频制作 → 成片」，
发布页在 PB-07 的设计里只负责"平台适用性 / 进度 / 审批 / 结果 / 审计"
（`publish_workspace.rs:1-15` 的模块注释、`PB-07.md`「关键设计判断」一）。把成片列表搬进
发布页会让发布页同时成为第二个成片管理页，两处的删除/播放/命名要长期保持同步。

**工作量：** 比方案一大（多一份列表 UI + 多一条网关依赖），收益是"不经过成片页也能发布"。

### 方案三：两者都做

方案一 + 方案二。工作量最大，且引入"同一份成片列表两处渲染"的重复。**不推荐现在做**——
真要做也应该等方案一在真实 App 上跑通、确认用户确实需要从发布页起步之后再加。

---

## 3. B站那条线怎么办

### 3.1 规划里的既有定论（必须遵守）

| 出处 | 原文要点 |
| --- | --- |
| Roadmap §2.7「首期内容发布范围」表格 | B站「首期路径＝当前正式开放的视频投稿 API」，「首期状态＝**实现**」，「不让 Browser Use 替代可用 API」 |
| Roadmap §2.7 末段 | 「B 站采用'实现完成'和'真实凭据验收'分离的交付状态……只把真实账号上传、投稿及状态查询标记为 `待凭据验收`，不得声称已经通过真实平台。**缺少凭据不是任务执行阻塞条件**」 |
| Roadmap §9.8 PB-07 交付定义 | 「B站未配置凭据时显示可理解的'待配置'，不让整个发布模块启动失败」 |

**结论：不能降级为「首期不支持」。** 规划把 B站定为首期实现范围，而且「待配置」这个状态
本身就是 PB-07 的设计内行为，不是缺陷。

真正的缺陷是两条，都不在"要不要支持 B站"这个层面：

1. **文案承诺了一件做不到的事**——「配置后即可使用」，但全 App 没有配置入口，
   服务端也没有授权链路；
2. **`begin_publish` 会把 B站发布跑成抖音浏览器发布**（§1.3），只是被硬编码 `false` 挡着。

### 3.2 处置建议：分三段，本任务只做第一段

| 段 | 内容 | 归属 |
| --- | --- | --- |
| **① 止损（本任务内做）** | ⑴ `begin_publish` 在把命令交给执行器之前，按平台分派：`Douyin` 走现有链路，`Bilibili` 明确返回"尚未接入"错误而**不是**掉进抖音链路——把 §1.3 的潜伏缺陷用一条失败测试钉死；⑵ 「待配置」提示文案改成不撒谎的版本（如"B站发布正在接入中，暂不可用"），或保留"待配置"但去掉"配置后即可使用"的承诺 | 本方案 |
| **② 凭据入口 + 服务端可达**（独立任务） | B站开放平台 OAuth 授权流程、`BilibiliAccessTokenProvider` 生产实现、FastAPI 路由、`SqlAlchemyBilibiliArchivePublishStore` 构造、Rust 侧从后端读"是否已配置"替换硬编码 `false`、设置页凭据入口 | 建议并入 **PB-07** 的剩余范围（它仍是 🔍 待验收），或作为 PB-07 之后、PB-08 之前的独立接线任务 |
| **③ 真实凭据验收**（已在规划内） | 用户提供 client_id/app_secret 与已获 `ARC_BASE` 授权账号后的真实投稿 | **PB-08**，`PB-02.md` / `PB-03.md` / `PB-04.md` 各自的「真实平台待凭据验收」清单已登记 |

**不建议**本任务顺手做②：它牵涉 OAuth 回调、密钥存储（按 §7 必须落在 Rust 管理的
`app_data_dir` 私有文件，不进 React / localStorage / 日志）、后端路由与迁移，范围比
`selectedVideo` 接线大得多，混在一起会让本任务既做不完也审不清。

---

## 4. 推荐与最大风险

### 4.1 推荐

**方案一 + B站止损①。** 理由：

1. Roadmap §7 第 6 步已经定了这条路径，PB-07 的遗留项也是这么写的——按规划走；
2. 不在发布页复制第二份成片列表，发布页的职责边界（PB-07「关键设计判断」一）不被稀释；
3. 一处接线同时解开 PB-01～PB-04 + BU-02～BU-05 共 8 项已完成任务的用户可达性；
4. B站止损①是纯防御性改动，不引入新依赖，却把一个"翻个开关就会把稿子发错平台"的
   潜伏缺陷变成一条会红的测试。

**同时必须做的契约改动：`artifactPath` → `artifactId`。** 这不是可选的重构，是接线的前提：

- 不改，React 就必须持有本机绝对路径（违反 §1.2-E 的架构边界）；
- 不改，`begin_publish` 就继续接受任意路径（§1.2-D），而执行器的校验只保证"是个合法的
  本地 mp4"，不保证"是这个 App 自己产出的成片"；
- 改了之后，可上传集合被收敛为"`role="rendered_video"` 且 `media_type="video/mp4"` 的
  已登记 Artifact"，并且 Artifact manifest 里本来就有 `sha256`，可以在移交前后各验一次。

### 4.2 最大风险

**风险一（最大）：成片载荷没有扩展名，执行器一定会拒。**
`<artifacts>/<id>/payload` 无后缀 vs `publish_artifact.py:125-130` 要求恰好一个 `mp4`/`mov`
后缀。这条不解决，接线全部做完之后点「发布到抖音」仍然是失败——**而且是三层测试都可能
发现不了的失败**：Rust 单测不跑执行器，UI Harness 用测试 Adapter，只有真实执行器链路会红。
必须在实现的第一步就用一条**跨到执行器**的测试把它钉住。

三种解法，实现时二选一（建议 a）：

- **(a) 在 RenderJob 私有工作区里暂存一个带扩展名的只读副本/硬链接**，把该副本路径交给
  执行器，发布结束后清理。优点：不动执行器契约，不放宽任何校验；缺点：需要处理磁盘配额
  （`VideoJobWorkspacePolicy` 已有 `minimum_free_bytes` / `maximum_workspace_bytes`）与清理时机。
- (b) 让 Rust 传"路径 + 声明的 media_type"，执行器改为校验声明值与实际内容一致而不是靠
  后缀。改动面更大，且放宽了一条现有的 fail-closed 判据，需要单独论证。
- (c) 改变 Artifact 落盘命名（`payload` → `payload.mp4`）。牵动 `import_output` /
  `validate_artifact_directory`（`video_job_workspace.rs:843-863` 硬断言目录内恰好
  `manifest.json` + `payload` 两个文件）与既有 Artifact 的兼容，影响面最大，不建议。

**风险二：这次接线依然不构成 PB-05/PB-06/PB-08 的验收。**
PB-05～PB-08 当前全部是 🔍 待验收，本机没有抖音真实发布账号可做投稿验收（用户今天扫码
取得的是登录态，不等于允许拿真实账号做投稿测试）。接线做完后，能验的最远一步是
"点『发布到抖音』→ 执行器真的打开发布页并停在提交前"，**不能真的投稿**。台账上
PB-07 仍是 🔍 待验收，绝不能因为按钮出现了就标完成。

**风险三：`publishJobId` 的将就（§1.2-H）如果不一起修，会留下一个无法追溯的发布任务标识。**
PB-07.md 自己写了"随素材来源接线一并修正"。修法：由发起方生成一个真正的 UUIDv4
（或由 Rust 侧生成并回传），不再复用 `confirmationId`。

**风险四：`WorkbenchShell` 的跨页状态是本仓库第一处"页面 A 选中 → 页面 B 消费"。**
现有的跨页跳转只有 `openTask` / `openPlatformPage`（`WorkbenchShell.tsx` 内），都只带一个
标识。这次要带一个对象。要避免的是把它做成全局 store——按项目规范，先就近放在
`WorkbenchShell` 的 `useState` 里，等出现第二个消费方再下沉。

**风险五：并行会话冲突。** 仓库当前有 `lib.rs`、`material_video_studio.rs`、
`run_vf_06_acceptance.py` 三个文件处于已修改未提交状态，且存在其他会话在改 `lib.rs`。
实现前必须重新确认这三个文件的实际内容，不能按本方案记录的行号盲改。

---

## 5. 实现拆解

### 5.0 台账归属（先定，避免踩门禁）

`scripts/check_embedded_browser_video_roadmap.py:104` / `:133` 把专项任务数**硬编码为 87**，
`:187-194` 还禁止 `docs/development/` 下出现不在 `EXPECTED_IDS` 里的 `XX-NN.md`。
所以**不要新开一个任务 ID**。本任务作为 **PB-07 剩余范围**执行：

- `docs/embedded-browser-video-studio-roadmap.md` 的 PB-07 行状态保持 `🔍 待验收`
  （接线完成也不能升级——真实投稿归 PB-08）；
- 证据追加写进既有的 `docs/development/PB-07.md`，并把「遗留项」表里
  「素材来源接线（视频产物 → 发布）」与「`approve_publish` 用确认标识兼作发布任务标识」
  两行更新为已修；
- 本文件（`PLAN-publish-video-source.md`）文件名不匹配门禁的 `specialized_name` 正则，
  放在 `docs/development/` 下不会触发检查。

### 5.1 步骤与 TDD（严格 RED → GREEN → REFACTOR，每步先看到失败）

> 顺序是**自底向上**的：先把最容易被三层测试漏掉的那一段（执行器能不能真的收下这个
> 文件）钉住，再往上接。反过来做的话，前面全绿、最后一步才发现风险一。

---

**[T1] 成片载荷可被执行器接受（解决风险一）**

- 改动文件：`frontend/src-tauri/src/video_job_workspace.rs`（新增受控暂存/解析）、
  可能涉及 `frontend/src-tauri/tests/video_job_workspace.rs`
- RED：
  - `cargo test --test video_job_workspace` 新增用例：对一条已登记 Artifact 请求
    "可交付给执行器的路径"，断言得到的文件名以 `.mp4` 结尾、内容摘要等于 manifest 的
    `sha256`、且路径落在 App 私有目录内 → 当前无此 API，编译失败；
  - **跨语言的一条**：`backend/tests/…` 新增用例，用 Rust 侧同样的命名规则构造一个
    `<workspace>/…/xxx.mp4`，调 `open_publish_artifact()` 断言通过；再用 `payload`
    这个无后缀名断言 `DouyinPublishArtifactRejected` → 后者今天就会过，前者是新覆盖，
    这条用来把"命名规则"钉成两侧共识而不是一侧的猜测
- GREEN：实现暂存（推荐硬链接，跨设备时退回复制），复用既有配额与 `ensure_free_space`
- 失败矩阵：磁盘满、配额超限、暂存目标已存在、Artifact 不存在、
  `role != "rendered_video"`、`media_type != "video/mp4"`、暂存后原 Artifact 被替换
  （摘要复验）、暂存文件在发布结束后被清理

---

**[T2] `begin_publish` 改吃 `artifactId`，并按平台分派（解决 §1.2-C/D/E 与 §1.3）**

- 改动文件：`frontend/src-tauri/src/lib.rs`、`frontend/src-tauri/src/publish_workspace.rs`
  （若分派判定放这里）、`contracts/publishing/publish-workspace.v1.json`、
  `frontend/src-tauri/tests/publish_workspace.rs`
- RED：
  - `cargo test --test publish_workspace`：`begin_publish` 传一个不存在的 `artifactId`
    → 期望 `configuration_invalid` 且阶段不变；
  - 传一个存在但 `role` 不是 `rendered_video` 的 Artifact → 期望拒绝；
  - **平台分派**：`platform="bilibili"` 且被判定为 `Ready` 时 → 期望明确的
    "平台尚未接入"错误，**断言没有触碰浏览器 / Profile**（用现有测试替身观察）；
  - `node --test frontend/tests/publish-workspace-contract.test.mjs`：契约里发布请求
    携带的是 Artifact 标识而不是路径 → 当前契约无此字段，红
- GREEN：`begin_publish` 注入 `VideoJobWorkspaceStore`，`artifact_id: Uuid` 入参，
  内部经 [T1] 解析路径；平台分派用 `match` 穷举而不是 `if`
- REFACTOR：`approve_publish` 的 `publishJobId` 不再复用 `confirmationId`（§1.2-H）
- 失败矩阵：未知平台、平台不可发布、Artifact 不存在 / 类型错、暂存失败、
  执行器不可用、发布中途取消后暂存文件的清理、同一 Artifact 并发两次发起

---

**[T3] 前端契约与发布页表单（`SelectedVideo` 形状改造 + 标题/简介输入）**

- 改动文件：`frontend/src/features/publishing/publish-workspace-gateway.ts`、
  `frontend/src/platform/tauri/publish-workspace-gateway.ts`、
  `frontend/src/features/publishing/PublishWorkspace.tsx`、对应 `.test.ts(x)`、
  `frontend/src/test-harness/publishing.ts`（harness 常量同步）
- RED（Vitest）：
  - `PublishWorkspace.test.tsx`：给了 `selectedVideo` 但标题为空时，
    「发布到抖音」按钮 disabled；填了标题与简介后可点；点击后 `beginPublish` 收到的是
    `artifactId` 而不是 `artifactPath`；
  - 「待发布视频」卡片展示主题与大小，并有「换一个」回调；
  - `publish-workspace-gateway.test.ts`：`PublishRequest` 带路径字段时被拒
- GREEN：改 `SelectedVideo`/`PublishRequest` 形状，加表单与卡片
- 失败矩阵：标题超长 / 含控制字符（复用 `readableText` 判据）、简介超长、
  未选视频时不渲染按钮（保留现有语义）、选中的成片在发布前被删除

---

**[T4] 成片页「去发布」+ Shell 跨页交接**

- 改动文件：`frontend/src/features/video-studio/VideoStudio.tsx`、
  `frontend/src/app/WorkbenchShell.tsx`、对应测试
- RED（Vitest）：
  - `VideoStudio.test.tsx`：成片卡片上有「去发布」，点击后回调收到该成片的
    `artifactId`/`subject`/`artifactSizeBytes`/制作方式；没有 `artifactId` 的任务不出现
    该按钮；
  - 新增 `WorkbenchShell` 层测试：从成片页触发「去发布」后，当前页切到「作品发布」，
    且 `PublishWorkspace` 收到的 `selectedVideo` 不是 `undefined`
- GREEN：`VideoStudio` 加 prop 与按钮，`WorkbenchShell` 持 `useState` 并切页
- 失败矩阵：选中成片后回到成片页删掉它（发布页要清空选择而不是留一个悬空标识）、
  连续选两条（后选覆盖先选）、发布进行中再次选择（阶段非 `idle` 时不允许换）

---

**[T5] 装配防线：把 `selectedVideo` 纳入 `production-wiring.test.ts`**

- 改动文件：`frontend/src/app/production-wiring.test.ts`
- RED：新增一条断言"发布页的成片来源在正式装配里是有源头的"。
  注意 `selectedVideo` 不是 `main.tsx` 传的 prop（它由 `WorkbenchShell` 自己产生），
  所以判据要落在 `WorkbenchShell.tsx` 源码上：**存在一处把非 `undefined` 的值写进
  `selectedVideo` 状态的赋值**。写完先跑，确认它在 [T4] 之前是红的
- 说明：这条判据依然是源码文本级的粗判据，和现有的 `tauriBindings` 同一档次。
  它防的是"prop 通道健在但没人往里灌"这一类——正是 PB-07 与本次的共同病根

---

**[T6] 真实用户路径验收（能验到哪就到哪，验不到的如实登记）**

- Playwright UI Harness（`frontend/e2e/ui-harness.spec.ts`）：
  成片页 →「去发布」→ 发布页 → 填文案 → 出现平台按钮。**证明 React 业务投影**；
- 真实 Tauri App（`frontend/e2e-tauri/publishing.spec.ts`）：
  这次必须**走界面**，不能像 PB-07 那样直接 `core.invoke`——PB-07.md 的「事后更正」
  就是被这一点坑的。前置条件是本机备好执行器包与内置浏览器，否则启动门会拦住工作台
  （PB-07.md 已记录这条边界）；
- **不做**真实投稿。走到"执行器打开抖音发布页并停在提交前"为止，
  再往后归 PB-08 `🔍 待真实账号`。

### 5.2 测试粒度

按 `CLAUDE.md`「Superpowers Task 测试粒度规范」：

- [T1]/[T2] 触碰 `video_job_workspace.rs` 与执行器契约，属于被多方引用的基础层 →
  **跑 Rust 全量 `cargo test` + 后端相关包 pytest**；
- [T3]/[T4]/[T5] 限于前端 → `npx vitest run` + `npx tsc -b` + `npx eslint .`
  （注意：`tsc --noEmit` 在本仓库是空转，必须用 `tsc -b`）；
- 合并前 final gate：Rust 全量 + `uv run pytest` 全量 + vitest 全量 +
  `node --test frontend/tests/*.test.mjs` + Playwright UI Harness +
  `scripts/check_user_facing_branding.py` + `scripts/check_embedded_browser_video_roadmap.py`。

### 5.3 本方案明确不做的事

| 不做 | 归属 |
| --- | --- |
| B站 OAuth、凭据存储、FastAPI 路由、store 构造、设置页凭据入口 | 独立任务（§3.2 第②段） |
| 把 `PublishWorkspace::new(false)` 改成从后端读真实配置状态 | 同上——本任务只做"B站不会掉进抖音链路"的止损 |
| 真实抖音投稿 / 真实 B站投稿 | PB-08 |
| 发布页自带成片选择器（方案二） | 暂不做，等方案一在真实 App 跑通再评估 |
| 审计持久化、`motionPartSelections` 接线、VE 网关、更新端点注入 | 各自独立，见审计报告第 9 节 |

---

## 6. 我没能查证的部分

1. **没有运行任何测试或构建。** 全部结论来自静态阅读当前 HEAD。特别是 [T1] 的风险一
   （无扩展名载荷被执行器拒）是从 `publish_artifact.py:125-130` 的代码推出来的，
   **没有实际跑一次证明**。实现的第一步就是把它变成一条真会红的测试。
2. **没有验证硬链接方案在本项目的两个目标平台上都可行。** macOS 上 `<app_data>` 内部
   硬链接没问题；Windows 上同卷硬链接可行但需要 `CreateHardLinkW`，且
   `video_job_workspace.rs` 现有的 Windows 分支只处理了 reparse point 与 `MoveFileExW`。
   实现时需要确认，不行就退回复制。
3. **`get_material_render_jobs` 的完整性只读到 Rust 层。** 它遍历全部 workspace
   （`material_video_studio.rs:249`），但一条成片要出现在列表里依赖
   `load_projection` 读得到 checkpoint；Worker 侧写 observation 的时机没有追。
   IM-05/IM-07 本就是 🔍 待验收，这一段不在本方案范围内。
4. **没有查证 B站开放平台的授权流程当前长什么样。** §3.2 第②段只是把"缺什么"列出来，
   具体是授权码模式还是别的、回调地址在本地单机部署怎么落地（`PB-04.md:72-74` 记录了
   "本地单机部署没有公网入站地址"），需要那个任务开工时重新核对官方文档。
5. **没有读 `frontend/e2e/ui-harness.spec.ts` 与 `e2e-tauri/publishing.spec.ts` 的现有用例。**
   [T6] 的写法是按 PB-07.md 的描述规划的，实现时要先读现有用例再决定是扩写还是新增。
6. **`WorkbenchShell.tsx` 与 `lib.rs` 当前有未提交改动、且有并行会话在改。**
   本文件所有行号以 `f710026` 为准，实现前必须重新核对。
