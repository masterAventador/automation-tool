# 已完成任务接线审计（2026-07-26）

纯静态审计。未修改任何生产代码，未启动 App / 浏览器 / 构建 / 测试。

> **后续更正（2026-07-31）**：第 3.2 节记录的 B站永久“待配置”断点已关闭；正式设置
> 入口、Tauri/Rust 分派、Control Plane 发布链和容器装配均已接通。最新证据见
> `docs/development/PB-07.md` 末节。本文其余内容仍保留为 7 月 26 日时点审计记录。

## 0. 审计起因

今天在 macOS 正式包上试用时发现多块功能完全不可用，而三层自动化验收长期全绿。已确认的两例：

- **PB-07**：`TauriPublishWorkspaceGateway` 写好了、Command 通了、验收时直接 `core.invoke("get_publish_workspace")` 验过，但 `main.tsx` 从未把网关传给工作台（已在 `8899602` 修复）；
- **VE-01～VE-08**：八项全部 ✅，阿里云 IMS/ICE 链路全部实现且用真实凭据验证过，但 `main.tsx:50` 用的是 `createLocalVideoEditingGateway(window.sessionStorage)`。

共同模式：**交付物实现了、测试全过、任务标记 ✅ 已完成，但从来没有接进用户实际能走到的路径。** 本审计逐项核对全部 214 项 ✅ 已完成任务。

---

## 1. 计数方法与准确计数

`grep` / `ripgrep` 在这些状态 emoji 上给出错误答案且不报错（同一份文件三个工具三个不同的错数）。本报告全部计数使用 Python 解析 Markdown 表格：

```python
# 逐行取 strip("|").split("|")，过滤分隔行，只保留 5 列且首列匹配 ^[A-Z]{1,3}\d*-\d+$ 的行，
# 判定末列包含哪一个状态串（唯一命中才计数，多命中/零命中单独列出）
```

| 文件 | 任务行 | ✅ 已完成 | 🔍 待验收 | ⬜ 未开始 | ⏸ 后置 | 其他 |
| --- | --- | --- | --- | --- | --- | --- |
| `docs/embedded-browser-video-studio-roadmap.md` | 87 | **51** | 27 | 0 | 9 | 0 |
| `docs/development-roadmap.md` | 256 | **163** | 2 | 81 | 0 | 10（带说明的待验收/替代行） |
| 合计 | 343 | **214** | — | — | — | — |

`docs/development-roadmap.md` 末列非唯一命中的 10 行（人工判读）：`R0-04` ✅ 历史完成已由 AV-01 替代、`B5-02` ⛔ 已由 EB-10 替代、`B5-15`/`D6-16`/`A7-16`/`A7-17` 🔍 待真实账号、`H8-22`/`P9-04`/`P9-06`/`P9-07` 🔍 待特定外部条件。这 10 行都**不计入** 163。

---

## 2. 三档分类统计

| 档次 | 专项 51 | 主 roadmap 163 | 合计 214 |
| --- | --- | --- | --- |
| **已接线** | 23 | 112 | **135** |
| **未接线** | 19 | 6 | **25** |
| **无需接线** | 9 | 45 | **54** |

**未接线 25 项占已完成总量的 11.7%。**

---

## 3. 未接线清单（最重要的产出）

### 3.1 VE-01 ~ VE-08｜独立视频剪辑（8 项）— 每一层都断

**交付了什么**：`EditingProject/Timeline/EditingJob/Artifact` 领域模型、`VideoEditingProvider` 契约与注册表、阿里云 IMS/ICE 凭据校验与媒资暂存、内部 Timeline → 阿里轨道编译、任务提交、MNS 回调验签与对账、成片导入与谱系、第二 Provider 一致性验收。

**断在哪一环**：全部六层同时断开——没有 API 路由、没有 Rust 操作、没有 Tauri Command、没有前端网关、没有装配、凭据无消费者。

**代码证据**：

| 断点 | 证据 |
| --- | --- |
| 前端装配 | `frontend/src/main.tsx:50` — `const videoEditingGateway = createLocalVideoEditingGateway(window.sessionStorage);` |
| 该网关自述 | `frontend/src/features/video-editing/local-video-editing-gateway.ts:16-22` — "there are no editing jobs and submission always fails closed as unavailable" |
| 无 Tauri 网关 | `frontend/src/platform/tauri/` 下 33 个文件中唯一相关的是 `video-editing-service-gateway.ts`（VF-05 的密钥设置），没有剪辑工作台网关 |
| 无 Tauri Command | `frontend/src-tauri/src/lib.rs:3844-3906`（生产 `invoke_handler`，`cfg(all(not(control-plane-e2e), not(desktop-e2e)))`）61 条命令中无任何剪辑命令 |
| 无 HTTP 端点 | FastAPI 全部 36 条路由中无 editing 路由（`backend/src/automation_tool/control_plane/api/` 20 个文件枚举所得） |
| 领域代码全孤儿 | `domain/aliyun_ims_editing_provider.py`、`aliyun_ims_editing_staging.py`、`aliyun_ims_editing_callback.py`、`aliyun_ims_editing_reconciliation.py`、`aliyun_ims_editing_output.py`、`video_editing_provider_conformance.py`、`video_editing_outputs.py`、`fake_second_editing_provider.py` —— 从 `bootstrap/app.py` 出发的导入闭包不可达 |
| 持久层全孤儿 | `infrastructure/database/aliyun_editing_intent_repository.py`、`editing_output_ledger_repository.py` —— 连 `database/__init__.py` 都没有 re-export，全仓库只有各自的 integration test 导入它们 |
| 凭据只写不读 | `frontend/src-tauri/src/lib.rs:233-277` + `:3741` —— `video_editing_service_settings` 只被自己的四条设置命令消费，无其他读取方 |

**用户看到什么**：左侧「视频剪辑」能打开，能建项目、拖时间轴（存在 `sessionStorage`，关掉 App 就没了），**没有素材上传入口**，点提交永远失败「剪辑服务暂时不可用」。在「设置与诊断」里填的阿里云 IMS AK/SK 被保存后没有任何东西会用到它。

---

### 3.2 PB-01 ~ PB-04｜作品发布（4 项）— 没有可选中的视频

**交付了什么**：发布领域与 capability 冻结、B站开放平台授权与版本契约、B站分片上传与创建稿件、B站状态查询与对账。

**断点 A（影响两个平台）**：正式装配从不提供 `selectedVideo`，发布按钮的渲染条件永远为假。

| 环节 | 证据 |
| --- | --- |
| 生产装配 | `frontend/src/main.tsx:56-74` —— `<App>` 的 props 里没有 `selectedVideo` |
| 传递链 | `frontend/src/app/App.tsx:91` → `frontend/src/app/WorkbenchShell.tsx:525` 都原样透传 `undefined` |
| 渲染条件 | `frontend/src/features/publishing/PublishWorkspace.tsx:137-140` —— `platform.availability === "ready" && snapshot.stage === "idle" && selectedVideo !== undefined` 才渲染「发布到 X」按钮 |
| 唯一生产者 | `frontend/src/test-harness/main.tsx:58`（`HARNESS_SELECTED_VIDEO`）与 `PublishWorkspace.test.tsx:16` —— **只有测试和 UI Harness 提供它** |

**断点 B（只影响 B站）**：B站可用性被硬编码成永久「待配置」，且没有任何配置入口。

| 环节 | 证据 |
| --- | --- |
| 硬编码 | `frontend/src-tauri/src/lib.rs:3745` —— `PublishWorkspace::new(false)`，`official_credentials_configured` 恒为 `false` |
| 判定 | `frontend/src-tauri/src/publish_workspace.rs:316` —— `PublishPlatform::Bilibili => PublishAvailability::AwaitingConfiguration` |
| 无配置 UI | 「设置与诊断」只有 `ModelServiceSettings` 与 `VideoEditingServiceSettings`（`WorkbenchShell.tsx:529-543`），没有 B站凭据入口 |
| 服务端孤儿 | `application/bilibili_archive_publishing.py`、`application/bilibili_archive_reconciliation.py`、`infrastructure/bilibili/{signing,material,open_api_client}.py` 从 `bootstrap/app.py` 不可达；36 条路由中无 bilibili 路由；`SqlAlchemyBilibiliArchivePublishStore` 虽在 `database/__init__.py:14` re-export，但全仓库无构造方 |

**用户看到什么**：「作品发布」能打开、能读到平台状态卡（PB-07 修复后不再报「暂时读不到发布状态」），但 B站卡永远是灰色「待配置」且无处可配，抖音卡即使登录成功显示「就绪」也**不会出现发布按钮**——因为没有任何界面能把一个做好的视频交给这个页面。整条发布链路在正式 App 里是死的。

---

### 3.3 BU-02 ~ BU-05｜Browser Use（4 项）— 唯一生产消费者被 PB 断点堵死

**交付了什么**：单一 Chromium 双模式适配（独立临时 Profile / CDP 接管运营进程）、受限 Agent 与 Tools、`BrowserSurfaceLease` 页面动作所有权租约、安全策略与副作用确认门禁。

**断在哪一环**：Browser Use 在生产代码里只有一个消费者——抖音发布链路，而该链路被 3.2 的 `selectedVideo` 断点堵死。

**代码证据**：
- 生产引用点仅四处：`backend/.../control_plane/domain/video_publishing.py`、`backend/.../executor/rpa/douyin/publish_preflight.py`、`backend/.../executor/rpa/douyin/publish_release.py`、`backend/.../executor/platform_commands.py:44`（`platform_commands.py` 中 `browser_use_safety` 的导入只服务于同文件的 publish preflight/release 分发）；
- 触发入口只有 Tauri `begin_publish`（`lib.rs:1241`）/ `approve_publish`（`lib.rs:1340`），两者都由发布页按钮调用；
- 界面没有通用 Browser Use 菜单（这是 BU-06 的显式设计约束）。

**用户看到什么**：Browser Use 这套能力在正式 App 里没有任何可触达入口。它不是"能用但没人用"，是"点不到"。

> 说明：BU-06/BU-07 本就是 🔍 待验收，不在本次 214 项范围内。这里说的是 BU-02～BU-05 四项 ✅ 的部分。

---

### 3.4 BM-12 / BM-13 / BM-14｜134 项离线动效目录（3 项）— 构建到 `.local/`，永远不进包

**交付了什么**：BM-12 离线化并锁定 GSAP/Three.js/D3/TopoJSON/Draco/地图/字体；BM-13 原创资产替换与权利台账；BM-14 把上游代码 + 离线依赖 + 资产 overlay 合成为只读版本化目录。

**断在哪一环**：两条独立的断裂。

**断点 A —— 产物落在 `.local/`，没有装配路径**：

| 环节 | 证据 |
| --- | --- |
| 产物位置 | `contracts/video/motion-catalog-release.v1.json:6` —— `"releaseRoot": ".local/motion-catalog-release"` |
| 该目录禁止提交 | 项目规则第 3 节：运行数据不得提交 Git；`.local/` 在忽略列表 |
| 无装配 | `scripts/release_assembly.py:153-172` 的 `VIDEO_RUNTIME_RESOURCES` 只有 media-toolchain / motion-video-worker / material-video-worker 三项，无 catalog |
| Worker 包不含它 | `contracts/quality/motion-video-worker-package.v1.json` 的 `packageLayout` 只有 `runtime/node` + `app/worker.mjs` |
| Worker 也不找它 | `workers/motion_composition/worker.mjs` 只接受一个 `assetRoot`（RenderJob 工作区），无 catalog 解析 |

**断点 B —— 用户选的零件根本没进提交请求**：

| 环节 | 证据 |
| --- | --- |
| UI 收集选择 | `frontend/src/features/video-studio/VideoStudio.tsx:753` `const [motionPartSelections, setMotionPartSelections] = useState<...>` |
| 传给目录组件 | `VideoStudio.tsx:904` `selections={motionPartSelections}` |
| **然后就没了** | 全文件 grep `motionPartSelections` 只有上面两处；`submitMotion()`（`VideoStudio.tsx:796-820`）构造的 `MotionVideoDraftRequest` 里**没有这个字段** |
| DTO 也没有 | `frontend/src/features/video-studio/material-video-studio-gateway.ts:30-40` —— `MotionVideoDraftRequest` 只有 `creationMode/subject/stylePresetId/primaryColor/secondaryColor/secondsPerBeat/beats/logo` |
| Rust 端只写 logo | `frontend/src-tauri/src/motion_video_studio.rs:567-571` —— 唯一写进工作区的资产是用户上传的 logo |

**用户看到什么**：「视频制作 → 品牌动效成片 → 动效零件」能按中文分类浏览全部 134 项、能勾选，**但勾选对成片没有任何影响**——选择值从未离开那个 React 组件，而且即使传下去了，134 项的实际资源也不在安装包里。

> 说明：BM-15（134 项自动选用与高级界面）是 🔍 待验收，覆盖"选择生效"这件事。但 BM-12/13/14 三项 ✅ 声称的是"离线目录已建成"，而这个目录从来没有到过用户机器。

---

### 3.5 H8-18 ~ H8-21｜App 自动更新（4 项）— 可发布包里从未配置

**交付了什么**：通用更新底座选型与契约、更新策略机（强制/可选/跳过/暂缓）、后台检查与下载、安装与重启协调。

**断在哪一环**：生产构建通过编译期 `option_env!` 读取更新端点与公钥，而**唯一能产出可分发包的脚本从不设置这两个变量**。

| 环节 | 证据 |
| --- | --- |
| 生产读取方式 | `frontend/src-tauri/src/app_update_coordinator.rs:574-590` —— `#[cfg(not(debug_assertions))]` 分支用 `option_env!("AUTOMATION_TOOL_UPDATE_ENDPOINT")` / `option_env!("AUTOMATION_TOOL_UPDATE_PUBLIC_KEY")`，两者缺失即 `Ok(None)` |
| 缺失后果 | `frontend/src-tauri/src/lib.rs:3686` —— `update_configuration` 为 `None` 则 `update_coordinator` 为 `None` |
| 命令返回 | `frontend/src-tauri/src/lib.rs:104-118` —— coordinator 为 `None` 时恒返回 `Failed{stage: Configuration, code: ConfigurationInvalid, retryable: false}` |
| 唯一装配脚本不设 | `scripts/run_eb_16_acceptance.py` 与 `scripts/run_eb_16_windows_acceptance.py` 中 grep `AUTOMATION_TOOL_UPDATE` **零命中** |
| 设了的都是测试构建 | `run_h8_20/21_acceptance.py`、`run_h8_22_*_package_acceptance.py`、`run_p9_03/04_acceptance.py`、`run_e4_15_acceptance.py` —— 全是 e2e/候选包脚本，产出的都不是可分发包 |
| 界面表现 | `frontend/src/features/app-updates/AppUpdateCenter.tsx:221` —— `{failure ? <Alert type="error" ... title={SAFE_FAILURE_MESSAGE} /> : null}`，`SAFE_FAILURE_MESSAGE = "暂时无法读取或操作 App 更新，请稍后重试。"`（第 23 行） |

**用户看到什么**：「设置与诊断 → App 更新」卡片常驻红色报错「暂时无法读取或操作 App 更新，请稍后重试。」，状态标签显示失败态。永远检查不到更新，也永远装不上。

---

### 3.6 B5-03 / B5-04｜浏览器发现与选择（2 项）— 已被 EB-10 删除，台账未同步

**交付了什么**：B5-03 Windows 注册表/标准路径浏览器发现 + 签名 allowlist；B5-04 用户选择受支持浏览器的设置界面。

**断在哪一环**：EB-10「删除生产浏览器选择链路」把生产入口删干净了，模块本身留成死代码，但两行状态仍是 ✅ 已完成。

| 环节 | 证据 |
| --- | --- |
| 模块仍在 | `frontend/src-tauri/src/lib.rs:7` `pub mod browser_discovery;`、`:9` `pub mod browser_settings;` |
| 生产零引用 | 除这两行 `mod` 声明外，`frontend/src-tauri/src/` 下无任何引用；引用者全部在 `frontend/src-tauri/tests/`（`browser_discovery.rs`、`browser_settings.rs`、`browser_packaged_runtime.rs`、`security_configuration.rs:42`） |
| 无 Command | 61 条生产命令中无浏览器发现/选择命令 |
| 无前端 | `frontend/src/` 下 grep `browserSettings`/`browser-settings`/`selectBrowser` 零命中 |
| 台账对同类行做过处理 | `docs/development-roadmap.md:268` B5-02 已改成 `⛔ 已由 EB-10 替代`，B5-03/B5-04 漏改 |

**用户看到什么**：这本来就是**应该**看不到的——EB-10 的设计目标就是不让用户选浏览器。问题不在产品行为，在台账：两项已作废的交付仍算作"已完成的功能"，且 `browser_discovery.rs` / `browser_discovery_windows.rs` / `browser_settings.rs` 三个死文件还在参与编译。按项目「重构后清理规范」应删除，按台账规范状态应改为 `⛔ 已由 EB-10 替代`。

---

## 4. `main.tsx` 网关装配完整核对表

`frontend/src/main.tsx` 共 76 行，构造 17 个对象并向 `<App>` 传 16 个 props。

| # | 网关 / 对象 | 构造 | 传给 `<App>` | 传到消费组件 | 消费组件 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `createDesktopStartupCheck(TauriControlPlaneTransport, TauriStartupEnvironmentGateway)` | `:32-35` | `:57` | `App.tsx:96` | `StartupGate` | ✅ |
| 2 | `TauriTaskProjectionSource` | `:36` | `:58` | `WorkbenchShell.tsx:547,564` | `TaskRunDetails` / `Workbench` | ✅ |
| 3 | `TauriTaskCreationGateway` | `:37` | `:59` | `WorkbenchShell.tsx:507` | `TaskCreate` | ✅ |
| 4 | `TauriTaskRunControlGateway` | `:38` | `:60` | `WorkbenchShell.tsx:548` | `TaskRunDetails` | ✅ |
| 5 | `TauriTaskDiscoveryGateway` | `:39` | `:61` | `WorkbenchShell.tsx:549` | `TaskRunDetails` | ✅ |
| 6 | `TauriTaskTargetPreviewSource` | `:40` | `:62` | `WorkbenchShell.tsx:550` | `TaskRunDetails` → `TaskTargetPreviewPanel` | ✅ |
| 7 | `TauriTaskTargetResultSource` | `:41` | `:63` | `WorkbenchShell.tsx:551` | `TaskRunDetails` | ✅ |
| 8 | `TauriWorkbenchGateway` | `:42` | `:64` | `WorkbenchShell.tsx:564` | `Workbench` | ✅ |
| 9 | `TauriPlatformAdapter` | `:43` | `:65` | `WorkbenchShell.tsx:533` / `App.tsx:106` | `Diagnostics` | ✅ |
| 10 | `TauriPlatformSessionGateway` | `:44` | `:66` | `WorkbenchShell.tsx:513` | `PlatformSessions` | ✅ |
| 11 | `TauriAppUpdateGateway` | `:45` | `:67` | `WorkbenchShell.tsx:503` | `AppUpdateCenter` | ✅ 装配到位（但后端配置缺失，见 3.5） |
| 12 | `TauriModelServiceGateway` | `:46` | `:68` | `WorkbenchShell.tsx:531` / `App.tsx:101` | `ModelServiceSettings` | ✅ |
| 13 | `TauriVideoEditingServiceGateway` | `:47` | `:69` | `WorkbenchShell.tsx:532` / `App.tsx:104` | `VideoEditingServiceSettings` | ✅ 装配到位（但存的凭据无消费者，见 3.1） |
| 14 | `TauriMaterialVideoStudioGateway` | `:48` | `:70` | `WorkbenchShell.tsx:519` | `VideoStudio` | ✅ |
| 15 | `TauriPublishWorkspaceGateway` | `:49` | `:71` | `WorkbenchShell.tsx:524` | `PublishWorkspace` | ✅ 已由 `8899602` 修复 |
| 16 | `createLocalVideoEditingGateway(sessionStorage)` | `:50` | `:72` | `WorkbenchShell.tsx:521` | `VideoEditingWorkbench` | ❌ **是本地草稿壳，不是真实网关** |
| 17 | `TauriAccountSessionGateway` | `:51-52` | `:73` | `App.tsx:131` | `AccountSessionGate` | ✅ 条件装配（仅 `import.meta.env.MODE === "customer-demo"`，符合设计） |
| — | `selectedVideo` | **从未构造** | **从未传** | — | `PublishWorkspace` | ❌ **正式 App 恒为 `undefined`，发布按钮永不渲染** |

### 4.1 `production-wiring.test.ts` 覆盖缺口

`frontend/src/app/production-wiring.test.ts:24-36` 的 `REQUIRED_GATEWAYS` 有 11 项，只断言 `new X(` 出现在 `main.tsx` 源码里；额外一条断言 `publishWorkspaceGateway={publishWorkspaceGateway}` 确实传下去了。

**缺口**：

1. **6 个已构造的网关不在清单里**：`TauriControlPlaneTransport`、`TauriTaskProjectionSource`、`TauriTaskTargetPreviewSource`、`TauriTaskTargetResultSource`、`TauriPlatformAdapter`、`TauriAccountSessionGateway`；
2. **只有 publish 一项检查"传下去"**，其余 10 项只检查"构造了"——PB-07 的确切形态（构造了但没传）对这 10 项仍然不设防；
3. **完全不检查"传的是不是真网关"**：`videoEditingGateway` 传下去了，传的是 `createLocalVideoEditingGateway`，这条测试全绿。VE 的问题它抓不到；
4. **不检查 `selectedVideo`**：`PublishWorkspace` 的第二个必要输入没有任何装配断言。

---

## 5. 只在测试 feature 下存在的 Tauri Command

`frontend/src-tauri/src/lib.rs` 有 3 个 `invoke_handler` 分支，共 98 处 `#[tauri::command]` 标注、94 个唯一函数名（`check_control_plane_health` ×3、`check_local_startup_environment` ×2、`restart_executor` ×2 是 cfg 变体）。

| 分支 | cfg 条件 | 位置 | 命令数 |
| --- | --- | --- | --- |
| desktop-e2e | `all(not(control-plane-e2e), desktop-e2e)` | `lib.rs:3809-3842` | 31 |
| **生产** | `all(not(control-plane-e2e), not(desktop-e2e))` | `lib.rs:3843-3906` | **61** |
| control-plane-e2e | `feature = "control-plane-e2e"` | `lib.rs:3907-4002` | 93 |

**结论：没有任何生产功能被 cfg 挡在生产构建之外。** 生产分支缺席的 33 个命令全部以 `_for_acceptance` / `_for_revocation_acceptance` 结尾，且都带 `#[cfg(feature = "control-plane-e2e")]` 或 `#[cfg(all(desktop-e2e, not(control-plane-e2e)))]` 定义级门禁：

```
advance_task_target_confirmation_revision_for_acceptance  lib.rs:2284
app_process_id_for_acceptance                            lib.rs:3443
control_task_for_acceptance                              lib.rs:2609
create_task_for_acceptance                               lib.rs:3550
exit_app_for_acceptance                                  lib.rs:3258
get_update_policy_record_for_acceptance                   lib.rs:94    ← 仅 desktop-e2e
inject_executor_crash_for_acceptance                     lib.rs:3238
inject_executor_hang_for_acceptance                      lib.rs:3248
inject_hostile_executor_diagnostics_for_acceptance       lib.rs:959
prepare_app_crash_recovery_for_acceptance                lib.rs:3304
prepare_control_plane_recovery_for_acceptance            lib.rs:3392
prepare_executor_crash_recovery_for_acceptance           lib.rs:3334
prepare_executor_lifecycle_for_acceptance                lib.rs:3208
prepare_network_recovery_for_acceptance                  lib.rs:3409
prepare_platform_session_for_acceptance                  lib.rs:3040
prepare_platform_session_reuse_for_acceptance            lib.rs:3070
prepare_system_resume_for_acceptance                     lib.rs:3426
prepare_task_create_form_for_acceptance                  lib.rs:3100
prepare_task_discovery_for_acceptance                    lib.rs:2078
prepare_task_lifecycle_for_acceptance                    lib.rs:3178
prepare_task_projection_for_acceptance                   lib.rs:2923
prepare_task_restart_for_acceptance                      lib.rs:3274
prepare_task_run_for_acceptance                          lib.rs:3130
prepare_task_target_preview_ui_for_acceptance            lib.rs:2265
prepare_workbench_for_acceptance                         lib.rs:2962
prepare_workbench_metrics_for_acceptance                 lib.rs:3001
preview_task_for_acceptance                              lib.rs:2186
query_tasks_for_acceptance                               lib.rs:3449
register_installation_for_revocation_acceptance          lib.rs:3610
run_control_plane_acceptance                             lib.rs:3646
signal_task_discovery_busy_for_acceptance                lib.rs:2149
stream_task_events_for_acceptance                        lib.rs:2850
terminate_tasks_for_acceptance                           lib.rs:2712
```

这一项**没有发现问题**。P9-05「正式包内容审计」在这一点上是真的。

---

## 6. `Contents/Resources/` 生产期望资源全清单

生产 Rust 代码有 **4 处** `resource_dir()` 调用，解析出 **5 棵资源树**：

| # | 资源路径 | 生产解析点 | 目录名常量 | 装配路径 | 出厂门禁 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `embedded-browser/` | `lib.rs:3727-3733` `EmbeddedBrowserAuthority::new` | `embedded_browser_distribution.rs:19` `DISTRIBUTION_DIRECTORY` | `release_assembly.install_and_seal` | `require_packaged_browser`（逐文件比对 EB-05 manifest） | ✅ 有 |
| 2 | `local-executor/package/` | `lib.rs:3766-3770`（`#[cfg(not(debug_assertions))]`） | `executor_platform.rs:39` `EXECUTOR_DIRECTORY` | `run_eb_16_acceptance.py:180-191` 动态生成 release 配置注入 `bundle.resources` | `executor_package.rs` 运行时验签+清单校验 | ✅ 有 |
| 3 | `media-toolchain/` | `lib.rs:360` `VideoMediaToolchain::load` | `video_media_toolchain.rs:11` `TOOLCHAIN_DIRECTORY` | `release_assembly.install_video_runtime` | `require_packaged_video_runtime`（`bin/ffmpeg`、`bin/ffprobe`、`manifest.json` 非空） | ✅ 刚补（`2e9a3a6`） |
| 4 | `motion-video-worker/package/` | `lib.rs:363-367` | `lib.rs:323` `MOTION_VIDEO_WORKER_DIRECTORY` | 同上 | 同上（`runtime/node`、`app/worker.mjs`） | ✅ 刚补（`2e9a3a6`） |
| 5 | `material-video-worker/package/` | `material_video_studio.rs:522` | 字面量 `"material-video-worker/package"` | 同上 | 同上（`automation-tool-material-video-worker`） | ✅ 刚补（`2e9a3a6`） |

**核对结论：没有第 6 份缺失资源。** 生产代码期望的资源恰好是这 5 棵，全部有装配路径和出厂门禁。

### 6.1 但装配路径本身有两个问题

**问题 1：装配只挂在 EB-16 验收脚本上，没有独立的发版命令。**
`install_and_seal` / `install_video_runtime` / `require_packaged_*` 的**全部非测试调用方**只有 `scripts/run_eb_16_acceptance.py` 和 `scripts/run_eb_16_windows_acceptance.py`。P9-03（macOS 候选包）和 P9-04（Windows 候选包）走 `run_p9_03/04_acceptance.py`，产出的包不含这 5 份资源——这也正是它们仍标 🔍 待验收的原因，与台账一致。

**问题 2：CI 不跑装配。**
`.github/workflows/desktop.yml` 只跑 `run_p9_01/02/04/05/06/07_acceptance.py` 与 `run_e4_15_acceptance.py`，**没有 `run_eb_16_acceptance.py`**。也就是说唯一能产出完整包的路径不在任何自动门禁里，只能人工在本机执行。三份视频资源缺失能潜伏到用户手上，根因就在这里。

**观察（非缺陷）**：`.github/workflows/video-media-toolchain.yml:24` 把 ffmpeg 构建进 `frontend/src-tauri/resources/media-toolchain`。这个路径与生产无关——`tauri.conf.json` 及全部 45 个 `tauri.*.conf.json` 都**没有 `bundle.resources` 键**，该目录只用于 CI 的 `check_video_media_toolchain.py` 校验。

---

## 7. 已接线判定的抽样依据

135 项"已接线"里，以下骨干是逐点核对过的，其余同组任务按"骨干通了 + 该组无孤儿模块"归类（见第 8 节的诚实说明）：

- **任务链路（T3 / D6 / A7 / H8-01~14）**：`main.tsx` 7 个任务相关网关 → `WorkbenchShell` → `TaskCreate`/`Workbench`/`TaskRunDetails`/`TaskTargetPreviewPanel` 全部核对；`TaskCreate.tsx:137-139` 确认三种动作 `browse`/`comment`/`direct_message` 都在下拉框里，A7-10/11/12 的执行链路可达；
- **Rust ↔ 后端**：`ControlPlaneOperation` 34 个操作（`control_plane.rs:46-80`）与 FastAPI 36 条路由逐条比对，**只有 2 条路由不在 allowlist，且都不应该在**——`WEBSOCKET /api/v1/executors/connect`（Executor 用，非 App）和 `GET /desktop-updates/v1/{channel}/{target}/{arch}/{current_version}`（走 Tauri updater 插件）。**后端端点没有被 allowlist 遗漏的情况；这一项没有发现问题**；
- **导航可达性**：`WorkbenchShell.tsx:59-68` 8 个导航项 + `LEGAL_PAGE` 隐藏页，与 `frontend/src/features/` 下 16 个非测试组件逐一对应，**没有"写好了没挂进导航"的孤儿组件**——16 个全部有渲染点（`AccountSessionGate` 条件挂载、`ThirdPartySoftwareNotice` 通过设置页链接 `WorkbenchShell.tsx:535-541` 进入）；
- **后端可达性**：从 `bootstrap/app.py` + 5 个 CLI 入口 + `executor/__main__` 做导入闭包（含函数内 import），208 个模块中 167 个可达。不可达的非测试模块只有第 3 节点名的 bilibili / aliyun-editing 两组，其余不可达项是 `executor/fake*.py`（测试替身）、`macos_candidate/windows_candidate/pyinstaller_support/package_manifest`（构建期）、`bootstrap/openapi.py`（契约导出）、`protocol/schema.py`（契约）。

---

## 8. 我没能查证的部分

**必须说清楚的方法论边界。** 以下内容本次审计**没有**做到逐项一级证据，判定强度低于第 3 节：

1. **135 项"已接线"里约 90 项是组级判定，不是逐任务代码证据。**
   我核对了每一组的骨干链路（网关装配、导航挂载、Command 注册、路由 allowlist、后端导入闭包），并确认该组没有孤儿模块。但像 `T3-03 Attempt/Action 模型`、`A7-07 副作用账本`、`E4-10 stderr 脱敏限界` 这类"骨干中间某一段"的任务，我是**推断**它在通路上，没有为每一项单独追一条从点击到副作用的完整链。第 3 节的 25 项未接线是逐项一级证据，可以直接采信；第 2 节的 135/54 这两个数字是分类统计，其中组级推断部分可能有个别误判。

2. **没有运行任何验证。** 全部结论来自静态阅读。特别是：装配脚本 `run_eb_16_acceptance.py` 是否真能在当前代码状态下跑通、`2e9a3a6` 补的三份视频资源门禁是否真的拒绝当前包，我只读了代码和提交说明，**没有实际执行**。

3. **没有读单个任务台账。** 按指示优先读 roadmap 表格行 + 代码。`docs/development/` 下 270 个任务文件我一个都没打开。因此"某任务台账里是否已经自己声明了这个缺口"我不知道——PB-07 台账据提交说明已更正，其余未知。

4. **Windows 侧只做了脚本层核对。** `run_eb_16_windows_acceptance.py` 的资源装配我读了调用点（`:559-577`），但 Windows 的 `resource_directory` 是 App 根目录而非 `Contents/Resources`（`release_assembly.py:174-181`），实际 Windows 包布局没有验证。

5. **`VideoStudio` 的"智能素材成片"链路只核到网关层。** `open_material_video_studio` → moneyprinter WebUI → `.automation-tool-webui/capability-v1` 观测文件 → `material-result.mp4` 这条链的中段（Worker 进程内行为）没有追。IM-05/IM-07/IM-08 本就是 🔍 待验收，不在范围内，但这意味着我对 IM-02/IM-03「已接线」的判定只覆盖到 Rust 启动器，不覆盖 Worker 是否真能产出成片。

6. **契约 / 门禁类任务的"有非测试调用方"只查到 CI workflow 层。** 例如 AV-02 的 `check_third_party_sources.py` 在 `quality.yml:31` 有调用，我据此判"无需接线"。但该 workflow 是否在每个 PR 上真的跑、是否 required check，我没查。

7. **`selectedVideo` 断点的"应该由谁提供"没有结论。** 我确认了它在正式装配里恒为 `undefined`，但没有查证设计上本应由哪个环节产出它（视频制作完成后的成片列表？剪辑成片导入？还是一个尚未实现的"选择作品"页面）。修复方案需要先回答这个问题。

---

## 9. 按修复紧迫度排序的建议（不在本次改动范围）

| 优先级 | 项 | 理由 |
| --- | --- | --- |
| P0 | `selectedVideo` 装配（3.2 断点 A） | 一处断点同时废掉 PB-01~04 + BU-02~05 共 8 项已完成任务 |
| P0 | 把 `run_eb_16_acceptance.py` 的装配步骤提成独立发版命令并接进 CI（6.1） | 三份视频资源缺失能到用户手上，根因在此；不修就还会有第四份 |
| P1 | VE 的 Tauri 网关 + Command + 路由（3.1） | 8 项已完成任务，全链路缺失，工作量最大 |
| P1 | 扩写 `production-wiring.test.ts`（4.1） | 当前防不住 VE 型（传了假网关）和 `selectedVideo` 型（根本没这个 prop）缺口 |
| P2 | EB-16 装配路径注入更新端点/公钥（3.5） | 4 项已完成任务，用户当前看到常驻红色报错 |
| P2 | `motionPartSelections` 接进 `MotionVideoDraftRequest` + catalog 装配（3.4） | 界面已经在骗用户"选了有用" |
| P3 | B5-03/B5-04 状态改 `⛔ 已由 EB-10 替代`，删除三个死模块（3.6） | 台账诚实性 + 编译产物瘦身 |
| P3 | `PublishWorkspace::new(false)` 硬编码 + B站凭据配置入口（3.2 断点 B） | 依赖 P0 先解决才有意义 |
