# 自动化运营工具整体工程结构

> 状态：当前项目实现基线
> 建立日期：2026-07-18
> 适用范围：仓库目录、依赖方向、协议、测试、打包和本地运行数据

## 1. 核心决策

项目采用单一 Git 仓库，只有一个面向用户的交付物：Tauri 桌面客户端。

```text
同一个仓库
├── frontend/    # React UI + Tauri/Rust 桌面壳
└── backend/     # Python 可部署业务后端 + 本地 RPA Executor
```

“前端”不代表另做 Web 产品：

- React 页面运行在 Tauri WebView 中；
- Vite 浏览器模式只用于开发和 Playwright UI Harness；
- 不部署静态网页，不提供用户网页入口；
- 真实浏览器 RPA、微信、系统权限和 Sidecar 生命周期只能由 Tauri 桌面链路完成正式验收。

## 2. 根目录

```text
automation-tool/
├── frontend/                      # 桌面客户端 UI 与 Tauri 原生壳
├── backend/                       # Python 可部署业务后端和本地执行器
├── contracts/                     # 跨 Rust/TypeScript/Python 的生成协议
│   ├── openapi/                   # FastAPI OpenAPI 快照
│   ├── protocol/                  # Executor wire 与 signed package Manifest JSON Schema
│   ├── events/                    # 任务事件 JSON Schema
│   ├── quality/
│   │   ├── mvp-failure-matrix.v1.json # H8-15 可执行失败矩阵与测试证据登记
│   │   └── mvp-spec-review.v1.json # H8-16 MVP 决策、验收状态与修复任务登记
│   └── fixtures/
│       └── douyin_discovery_pages/ # D6-15 六类离线 Fake 页面语料；只进测试
│       ├── executor-v1/           # Python/Rust/TypeScript 共用 valid/invalid wire 样例
│       ├── executor-package-v1/   # Python 生成、Rust 复验的 inert 签名目录样例
│       └── executor-diagnostics-v1.json # Python/Rust 共用 fixture v2 脱敏输入与安全结果
├── docs/
│   ├── dt-ai-helper-competitive-analysis.md
│   ├── product-plan.md
│   ├── project-structure.md
│   ├── frontend-architecture.md
│   ├── backend-architecture.md
│   ├── development-roadmap.md
│   └── adr/                       # 后续重要架构决策
├── scripts/                       # 跨工程生成、检查、纵向验收和打包脚本
│   ├── run_i2_09_acceptance.py   # 隐藏 Tauri→Rust→FastAPI/PostgreSQL 隔离验收
│   ├── run_i2_13_acceptance.py   # 后台 Uvicorn→WebSocket→PostgreSQL 隔离验收
│   ├── run_i2_14_acceptance.py   # 隐藏 Tauri 吊销诊断与最终状态验收
│   ├── run_t3_06_acceptance.py   # 隐藏 Tauri 幂等创建 Task 纵向验收
│   ├── run_t3_07_acceptance.py   # 隐藏 Tauri Task 分页/详情/scope 纵向验收
│   ├── run_t3_08_acceptance.py   # 后台真实 WebSocket Registry 单活/心跳验收
│   ├── run_t3_09_acceptance.py   # 后台 PostgreSQL Outbox→WebSocket 重投/ACK/过期验收
│   ├── run_t3_10_acceptance.py   # FakeExecutor 正式协议纵向验收
│   ├── run_t3_11_acceptance.py   # Task 事件原子收敛纵向验收
│   ├── run_t3_12_acceptance.py   # 隐藏 Tauri SSE 断线续拉验收
│   ├── run_t3_13_acceptance.py   # 隐藏 Tauri 暂停/恢复验收
│   ├── run_t3_14_acceptance.py   # 隐藏 Tauri 取消/紧停验收
│   ├── run_t3_15_acceptance.py   # 隐藏 Tauri Query/Reducer/Channel 验收
│   ├── run_t3_16_acceptance.py   # 隐藏 Tauri 工作台页面真实紧停验收
│   ├── run_t3_17_acceptance.py   # 隐藏 Tauri 新建表单→API→定义持久化验收
│   ├── run_t3_18_acceptance.py   # 隐藏 Tauri 运行详情→四类控制→事件终态验收
│   ├── run_t3_19_acceptance.py   # 隐藏 Tauri 创建/控制/成功→整页刷新恢复验收
│   ├── run_t3_20_acceptance.py   # 隐藏 Tauri→Control Plane 同库重启→Executor 恢复验收
│   ├── run_h8_01_acceptance.py   # 隐藏 Tauri→真实 Executor 安全暂停/恢复验收
│   ├── run_h8_16e_acceptance.py  # 隐藏 Tauri 启动诊断→浏览器选择→ready 验收
│   ├── run_e4_07_acceptance.py   # signed Executor→Manager→Control Plane 生命周期验收
│   ├── run_e4_12_acceptance.py   # signed Executor 任务回放与 SQLite 恢复验收
│   ├── run_e4_14_acceptance.py   # 隐藏 Tauri→signed Executor 全生命周期验收
│   ├── run_e4_15_acceptance.py   # 临时 release→实际二进制/依赖树安全审计
│   ├── run_b5_12_acceptance.py   # 无头浏览器→Executor WebSocket→平台投影验收
│   ├── run_b5_13_acceptance.py   # 隐藏 App→signed Executor→无头浏览器→平台页面验收
│   ├── run_b5_15_acceptance.py   # 四轮隐藏 App/Executor/浏览器复用与接管验收
│   └── run_b5_16_acceptance.py   # 活跃 Chrome 进程树/lsof 默认 Profile 隔离审计
├── .github/
│   └── workflows/                 # macOS/Windows CI 与安装包验证
├── .local/                        # 开发运行数据，必须忽略
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── .editorconfig
├── .gitignore
├── compose.yaml                   # 本地独立 PostgreSQL 开发库与测试库
└── .env.example                   # 只放非敏感示例
```

根目录不放业务源码、数据库、浏览器 Profile、测试截图、真实素材或临时脚本。

## 3. Frontend 目录

```text
frontend/
├── src/
│   ├── app/                       # Provider、路由、布局和错误边界
│   ├── features/
│   │   ├── app-updates/            # 通用桌面更新状态、决策与 UI gateway 契约
│   │   ├── workbench/             # RPA 运营工作台
│   │   ├── task-create/           # 受约束的新建任务表单
│   │   ├── task-runs/             # 运行详情、事件和结果
│   │   ├── platform-sessions/     # 平台登录态和人工接管，不是产品账号中心
│   │   ├── diagnostics/           # 环境、浏览器和 Sidecar 诊断
│   │   ├── settings/              # 本地设置、保留与清理
│   │   ├── content-studio/        # P2 内容生产与素材
│   │   └── ai-workflows/          # P3 AI 员工与工作流
│   ├── components/                # 真正跨 Feature 复用的 UI
│   ├── api/
│   │   ├── generated/             # 由 OpenAPI 生成，禁止手改
│   │   ├── control-plane/         # 窄 Transport 接口与测试 Harness
│   │   ├── protocol/              # Executor v1 Zod 正式解析与公共 fixture 回归
│   │   ├── client.ts              # 后续业务 API 客户端
│   │   ├── events.ts              # 后续实时事件连接与投影
│   │   └── query-client.ts
│   ├── platform/
│   │   ├── tauri/
│   │   │   ├── control-plane-transport.ts # 正式启动检查 Tauri invoke 适配器
│   │   │   ├── startup-environment-gateway.ts # AppData/Executor/浏览器封闭启动快照适配器
│   │   │   ├── task-creation-gateway.ts   # 固定抖音任务定义创建 Command
│   │   │   ├── task-projection-source.ts  # 固定 Task 快照/列表/Channel source
│   │   │   ├── task-run-control-gateway.ts # 固定暂停/恢复/取消/紧停 Command
│   │   │   ├── platform-adapter.ts         # 固定 Executor 状态/重启/诊断/本机紧停 Command
│   │   │   ├── platform-session-gateway.ts # 固定抖音状态查询/打开处理/重新检查 Command
│   │   │   ├── app-update-gateway.ts       # 固定更新状态/检查/决策 Command
│   │   │   └── workbench-gateway.ts       # 固定运行状态、只读指标与全局紧停 gateway
│   │   ├── types.ts               # PlatformAdapter 公共接口
│   │   └── test-harness.ts        # 仅测试构建可用
│   ├── schemas/                   # Zod 运行时校验
│   ├── stores/                    # 少量纯客户端 Zustand 状态
│   ├── styles/
│   ├── test/
│   ├── test-harness/              # 仅 Playwright 入口可达的窄测试 Adapter
│   └── main.tsx
├── e2e/                           # Playwright 测试专用 UI Harness
├── e2e-tauri/                     # WebdriverIO 真实 Tauri E2E
├── scripts/
│   └── audit-production-package.mjs # release 二进制、依赖、配置和资产审计
├── src-tauri/
│   ├── src/
│   │   ├── commands/              # 有界 Tauri Command
│   │   ├── executor/              # Local Executor 握手、监管和事件桥
│   │   ├── security/              # Capability、路径和令牌边界
│   │   ├── platform/              # 文件、通知、窗口和系统能力
│   │   ├── app_update_cache.rs    # Range 恢复、SHA-256/Minisign 流式验证与唯一私有候选缓存
│   │   ├── app_update_coordinator.rs # 启动/周期/手动统一检查、并发门和 official updater 适配
│   │   ├── app_update_installation.rs # 缓存重验、运行环境安全退出与官方安装/重启交接
│   │   ├── app_update_policy.rs   # 可选/强制更新决策、单调版本与 App 私有持久状态
│   │   ├── app_updates.rs         # 官方 updater raw JSON 校验与通用状态/决策契约
│   │   ├── browser_discovery.rs  # macOS/Windows 标准浏览器原生发现、签名与路径 identity
│   │   ├── browser_profiles.rs   # 固定抖音 UUIDv4 Profile、稳定 identity 与跨平台组合根
│   │   ├── browser_profiles_unix.rs # openat/mkdirat、0700 与 symlink 防护
│   │   ├── browser_profiles_windows.rs # NtCreateFile、reparse 防护与当前用户私有 ACL
│   │   ├── browser_settings.rs   # 受信浏览器枚举选择与 App 私有原子持久化
│   │   ├── control_plane.rs       # 固定 origin、operation allowlist、凭据注入与 SSE 严格解析
│   │   ├── device_identity.rs     # Ed25519 设备身份与 App 私有存储
│   │   ├── device_credentials.rs  # 长期设备凭据的校验、替换与删除
│   │   ├── executor_bootstrap.rs  # 256-bit stdin 启动令牌与 HMAC 健康证明校验
│   │   ├── executor_diagnostics.rs # stderr 流式限界、脱敏与内存滚动保留
│   │   ├── executor_manager.rs    # signed Executor 生命周期、监管与跨平台进程树
│   │   ├── executor_package.rs    # signed onedir 验签、完整目录复算与防降级
│   │   ├── executor_platform.rs   # app_data 固定路径、稳定 Executor ID 与 Manager 组合根
│   │   ├── executor_protocol.rs   # Executor v1 Rust 正式解析与安全失败边界
│   │   ├── startup_environment.rs # 无路径 AppData/Executor/浏览器启动诊断 DTO 与复验
│   │   ├── secure_store.rs        # app_data_dir 私有文件与原子替换
│   │   ├── lib.rs
│   │   └── main.rs
│   ├── tests/
│   │   ├── browser_discovery.rs  # 真实系统浏览器的生产 API 发现与复验
│   │   ├── browser_profiles.rs   # UUID、权限、symlink、identity 替换与并发创建
│   │   ├── browser_settings.rs   # 真实发现、枚举保存、损坏与不可用失败矩阵
│   │   ├── executor_bootstrap.rs  # 随机令牌、stdin 文档、常量时间证明与失败矩阵
│   │   ├── executor_manager.rs    # 单实例、监管、超时、进程树与真实包入口
│   │   ├── executor_package.rs    # 当前目标包、Python fixture 与失败矩阵
│   │   ├── executor_platform.rs   # App 私有固定路径、身份持久化与权限失败矩阵
│   │   └── executor_protocol_fixtures.rs # 回放三端共享原始 wire
│   ├── binaries/                  # 构建产物目录，不提交未签名临时包
│   ├── capabilities/              # 正式最小权限
│   ├── tauri.conf.json
│   ├── tauri.dev.conf.json        # 只由 tauri:dev 合并的 loopback URL/devCSP
│   ├── tauri.test.conf.json       # 后台隐藏的通用桌面测试配置
│   ├── tauri.control-plane-e2e.conf.json # 后台隐藏的网络桥纵向验收配置
│   ├── tauri.browser-settings-e2e.conf.json # 后台隐藏的浏览器选择真实入口验收
│   ├── tauri.installation-revocation-e2e.conf.json # 后台隐藏的吊销验收
│   ├── tauri.task-creation-e2e.conf.json # 后台隐藏的创建 Task 验收
│   ├── tauri.task-create-form-e2e.conf.json # 后台隐藏的新建表单真实入口验收
│   ├── tauri.task-query-e2e.conf.json # 后台隐藏的 Task 查询验收
│   ├── tauri.task-event-stream-e2e.conf.json # 后台隐藏的 SSE 断线续拉验收
│   ├── tauri.task-control-e2e.conf.json # 后台隐藏的暂停/恢复验收
│   ├── tauri.task-termination-e2e.conf.json # 后台隐藏的取消/紧停验收
│   ├── tauri.task-projection-e2e.conf.json # 后台隐藏的 Query/Channel 验收
│   ├── tauri.task-run-e2e.conf.json # 后台隐藏的运行详情四类控制验收
│   ├── tauri.task-lifecycle-e2e.conf.json # 后台隐藏的完整生命周期与刷新验收
│   ├── tauri.task-restart-e2e.conf.json # 后台隐藏的 Control Plane 重启恢复验收
│   ├── tauri.control-plane-recovery-e2e.conf.json # 后台隐藏的签名 Executor 重连验收
│   ├── tauri.network-recovery-e2e.conf.json # 后台隐藏的异常断网/抖动恢复验收
│   ├── tauri.executor-lifecycle-e2e.conf.json # 后台隐藏的 signed Executor 生命周期验收
│   ├── tauri.platform-session-e2e.conf.json # 后台隐藏的平台状态与无头浏览器验收
│   ├── tauri.platform-session-reuse-e2e.conf.json # 后台隐藏的登录复用/失效接管验收
│   ├── tauri.default-profile-isolation-e2e.conf.json # 后台隐藏的默认 Profile 隔离验收
│   ├── tauri.workbench-e2e.conf.json # 后台隐藏的工作台真实紧停验收
│   └── tauri.workbench-metrics-e2e.conf.json # 后台隐藏的工作台权威指标验收
├── public/
├── package.json
├── pnpm-lock.yaml
├── vite.config.ts
├── playwright.config.ts
├── wdio.conf.ts
├── wdio.control-plane.conf.ts
├── wdio.browser-settings.conf.ts
├── wdio.installation-revocation.conf.ts
├── wdio.task-creation.conf.ts
├── wdio.task-create-form.conf.ts
├── wdio.task-query.conf.ts
├── wdio.task-event-stream.conf.ts
├── wdio.task-control.conf.ts
├── wdio.task-termination.conf.ts
├── wdio.task-projection.conf.ts
├── wdio.network-recovery.conf.ts
├── wdio.task-run.conf.ts
├── wdio.task-lifecycle.conf.ts
├── wdio.task-restart.conf.ts
├── wdio.executor-lifecycle.conf.ts
├── wdio.platform-session.conf.ts
├── wdio.platform-session-reuse.conf.ts
├── wdio.default-profile-isolation.conf.ts
├── wdio.workbench.conf.ts
├── wdio.workbench-metrics.conf.ts
├── tsconfig.json
└── README.md
```

### 3.1 Frontend 依赖方向

```text
app
 └── features
      ├── components
      ├── api
      ├── platform interface
      └── schemas/stores

Tauri implementation ──implements──> platform interface
Test harness implementation ────────> platform interface
```

B5-02 的 `src-tauri/src/browser_discovery.rs` 是系统浏览器信任根，不是 React Adapter。macOS 只枚举 `/Applications/Google Chrome.app` 与 `/Applications/Microsoft Edge.app`，用 Security.framework 对完整签名、所有 Mach-O 架构、嵌套代码、精确 Bundle signing identifier 和 Developer Team requirement 做验证；同时固定主可执行文件相对路径并在验签前后保存 App/入口的 dev+inode。公开复验 API 只接受模块自己产生的 `TrustedBrowser`，使用前路径缺失、替换、symlink 或签名变化都会 fail closed。B5-04 才把安全浏览器枚举投影给 UI，路径和 identity 永不进入 React。

B5-04 的 `browser_settings.rs` 是选择边界而不是第二套发现逻辑。Tauri setup 从自身 AppData 初始化唯一 service；`get_browser_settings`/`select_browser` 只投影和接收固定枚举，每次保存前调用 B5-02/B5-03 真实发现。canonical v1 选择以私有目录和原子替换保存，React 没有路径 DTO、文本框、文件选择器或服务端回退。专用隐藏 App 验收使用动态已检查 WebDriver 端口和独立标识，刷新后从同一产品页面读回选择，再精确清理 AppData 与端口；测试配置、WDIO 入口和标识继续被 E4-15 正式包扫描拒绝。

B5-05 的 `browser_profiles.rs` 是后续浏览器运行时唯一 Profile 组合根。Tauri setup 从自身 AppData 管理唯一 Store；它当前只允许本机生成/打开 canonical UUIDv4 抖音 Profile，未注册 WebView Command。Unix 用父目录 fd 相对创建/打开并保持 dev+inode，Windows 用父 HANDLE 相对 `NtCreateFile` 并保持 volume/file index；每层私有权限、symlink/reparse、最终路径和重开 identity 均 fail closed。B5-06/B5-07 必须继续使用该对象，不能重新从字符串路径构造 Profile。

H8-18 的 `src-tauri/src/app_updates.rs` 是 updater 原生响应进入产品状态前的唯一契约边界。官方 `tauri-plugin-updater` 持有网络 URL、签名和安装对象；该模块只从其 `raw_json` 验证通用 `update_contract` v1，并向 `features/app-updates/contracts.ts` 对应的闭集投影版本、channel、可选/强制策略、平台、架构、摘要、大小和安全发布说明。React 不导入 updater binding，Capability 不开放 updater Command；H8-19～H8-22 继续在这个 Rust 边界内实现策略、私有缓存、安装协调和 UI，不能在业务 Feature 复制状态机。

H8-19 的 `src-tauri/src/app_update_policy.rs` 是唯一更新决策机。正式 Tauri setup 管理单实例，它以包版本建立不会下降的 floor，并在 `app-updates/update-policy-v1` 原子保存最高已见 release identity、可选决策和 revision；持久文件没有 URL、签名、notes、路径或业务数据。每次提示只接受一个决策，新观察才允许再次决策；强更、回退、同版本换包和存储失败全部拒绝。后续 scheduler、下载缓存、安装协调和 UI 只消费它的 action，不得自行解释 `defer/skip/forced`。

H8-20 的 `app_update_coordinator.rs` 是更新调度的唯一组合根：official updater 检查适配、启动/周期/手动触发、重叠检查合并、策略观察和公开状态都在此串行；生产 endpoint/公钥来自构建期验证配置，React 不能传 URL。`app_update_cache.rs` 只持有 Rust 内部 `DownloadSource`，固定管理 `app-updates/cache-v1` 的 partial/package/两份小清单；它不把 URL、签名或路径写盘，只有 Range 恢复后的完整大小、SHA-256 和 Minisign 都通过才原子替换旧 package。

H8-21 的 `app_update_installation.rs` 是唯一安装交接边界。协调层把当前 official `Update` 作为不序列化的 installer 保留；立即安装或 startup 自动安装时先让 cache 以 release identity 和当前签名重验完整 bytes，再隐藏窗口、停止 Executor/释放 Profile，最后调用官方 `Update::install`。预检失败不触碰运行环境，停止或安装失败恢复窗口；Windows 由官方安装器退出接管，macOS 安装成功后 Tauri restart。`decide_app_update` 只接收封闭决策枚举，不接收版本、URL、签名、bytes 或路径；安装探针同时要求 debug、`desktop-e2e` 且排除 `control-plane-e2e`，release/生产和其他验收特性不编译该类型，仅为 H8-21 隐藏 App 验证交接顺序，H8-22 必须以真实签名包替代该平台底层探针。H8-21 配置另用 `frontend/dist-h821`，不复用 production `dist`；runner 前后精确清理它，避免 release 与验收构建资产相互覆盖。

H8-22 的 `features/app-updates/AppUpdateCenter.tsx` 只解释公开状态为设置卡片、进度和可选/强制提示；`platform/tauri/app-update-gateway.ts` 是唯一原生适配器，只调用 `get_app_update_state`、`check_app_update_now`、`decide_app_update`。Rust `UpdateState` enum 字段固定 camelCase，组件以单一 operation gate 阻止主动检查/决策与轮询重叠。生产 `main.tsx` 显式注入该 gateway，业务组件没有 updater JavaScript binding、任意 invoke、URL、签名、包 bytes 或路径。`e2e-tauri/update-ui.spec.ts` 与 `scripts/run_h8_22_acceptance.py` 复用 H8-21 的隔离更新源和三轮真实 App，从页面按钮完成更新决策与安装交接。`tauri.update-macos-package-e2e.conf.json`、`wdio.update-macos-package.conf.ts`、`e2e-tauri/update-macos-package.spec.ts` 与 `scripts/run_h8_22_macos_package_acceptance.py` 只负责 macOS ad-hoc 实包：构建并挂载旧 DMG、从页面触发 official updater、核对 `.app` 版本/哈希/codesign，并注入签名正确但格式损坏的包验证窗口恢复；配置和产物使用唯一 identifier/`dist-h822-mac`/`/private/tmp`，finally 全量清理。对应的 `tauri.update-windows-package-e2e.conf.json`、`wdio.update-windows-package.conf.ts`、`e2e-tauri/update-windows-package.spec.ts` 与 `scripts/run_h8_22_windows_package_acceptance.py` 只允许 Windows x86_64：以唯一 `currentUser` product/identifier/main binary/AppData 构建普通 `NotSigned` NSIS，从页面驱动 0.1.0→0.2.0/0.3.0 与损坏 0.4.0 矩阵，由外层核对安装后 PE 版本/哈希、HKCU 安装/卸载记录、等待 updater 安装器退出并逐轮调用专属卸载器。两个 runner 都明确移除安装探针与运行期私钥且不进入正式包；Windows 实机结果和两平台正式发布签名保留为独立验收。

规则：

- `features/` 之间通过稳定 ID、公开组件或任务事件协作，不深层导入内部文件；
- 业务页面、业务 Store 和普通 API 代码不得直接依赖 `@tauri-apps/*`；
- `src-tauri/` 不承载运营业务规则，只负责原生权限、Local Executor、系统资源和协议桥接；
- 测试 Harness 只能模拟原生边界，不能被生产构建引用；
- `content-studio` 和 `ai-workflows` 在对应阶段开始前不创建空壳菜单。

## 4. Backend 目录

```text
backend/
├── src/
│   └── automation_tool/
│       ├── logging_redaction.py  # H8-11 Control Plane/Executor 共用 fail-closed 文本脱敏
│       ├── control_plane/         # 独立部署的 FastAPI 业务后端
│       │   ├── logging.py         # H8-11 进程级 LogRecord/Uvicorn 脱敏与限界
│       │   ├── bootstrap/         # 配置、注册、设备凭据和 Session 依赖装配
│       │   ├── api/               # REST、设备认证、SSE/WebSocket 和错误映射
│       │   ├── application/       # 注册、凭据、任务、工作台指标、配置、内容和工作流用例
│       │   ├── domain/            # 稳定 ID、Task 执行状态、版本事件、快照与 Command 契约
│       │   └── infrastructure/
│       │       ├── database/      # PostgreSQL 注册认证、任务、动作 evidence/连续失败、目标结果与工作台指标投影
│       │       ├── security/      # Bootstrap 签名验证等密码学适配
│       │       ├── events/
│       │       ├── object_storage/
│       │       └── observability/
│       ├── executor/              # 永远运行在用户电脑的执行器
│       │   ├── __main__.py        # 源码模式与 PyInstaller 共用的模块入口
│       │   ├── action_authorization.py # A7-03 固定公钥验签和完整执行意图匹配
│       │   ├── action_gate.py     # A7-04 验签 + SQLite 本机硬下限唯一动作准入门
│       │   ├── action_operation.py # H8-16D 授权命令到浏览/评论/私信生产执行的唯一编排
│       │   ├── authentication.py  # 本机启动令牌校验、可清零 HMAC 事件证明
│       │   ├── bootstrap.py       # 一次性 stdin bootstrap、端点/Session/身份严格校验
│       │   ├── browser_authority.py # 登录与发现共享的受信浏览器请求/lease 所有权
│       │   ├── browser_runtime.py # 单 context、页面/窗口、超时和清理的 Playwright BrowserRuntime
│       │   ├── rpa/douyin/browse.py # A7-10 单次、可取消、零发送的目标主页访问
│       │   ├── rpa/douyin/comment_action.py # A7-11 授权/账本/单击/验证/receipt 评论编排
│       │   ├── rpa/douyin/comment_page.py # A7-08 评论输入/提交/最终确认的唯一 selector 所有者
│       │   ├── rpa/douyin/direct_message_action.py # A7-12 会话恢复/唯一发送/权限/receipt 私信编排
│       │   ├── rpa/douyin/direct_message_page.py # A7-09 私信会话/发送/权限/最终确认的唯一 selector 所有者
│       │   ├── rpa/douyin/action_result.py # A7-15 动作/恢复 receipt → 封闭 Task event evidence
│       │   ├── rpa/douyin/profile_page.py # A7-10 通用用户主页/登录/风控 Page Object
│       │   ├── rpa/douyin/side_effect_recovery.py # A7-13 dispatched 只读核对/终态结算
│       │   ├── cli.py             # automation-tool-executor 正式控制台入口与信号映射
│       │   ├── command_processor.py # 正式命令、SQLite checkpoint 和持久结果 outbox
│       │   ├── diagnostics.py     # 与 Rust 共用 fixtures 的 fail-closed 文本脱敏
│       │   ├── discovery_operation.py # 搜索/滚动/提取的单次只读发现组合
│       │   ├── ledger.py          # 本机 SQLite v7 命令/Session/准入/副作用/网络闸门账本
│       │   ├── local_artifact.py  # H8-09 通用本机 Artifact 引用、权限和稳定读写边界
│       │   ├── browser_diagnostic_artifact.py # H8-10 脱敏 viewport PNG/结构 Trace 固定 Policy
│       │   ├── side_effect_ledger.py # A7-07 封闭且脱敏的副作用状态值对象
│       │   ├── page_drift_artifact.py # 复用通用 Store 的页面漂移固定 Schema/Policy
│       │   ├── platform_commands.py # 认证本机平台命令、扫码 flow 与健康队列
│       │   ├── package_manifest.py # onedir 完整清单、目录摘要和离线 Ed25519 签发工具
│       │   ├── macos_candidate.py # P9-01 隔离 PyInstaller、Mach-O/依赖/路径与 ad-hoc 签名准备审计
│       │   ├── windows_candidate.py # P9-02 隔离 PyInstaller、PE/依赖/路径与 reparse point 审计
│       │   ├── runtime.py         # Hello/Heartbeat、固定健康投影和有界停止
│       │   ├── transport.py       # Fake/正式 Executor 共用的受认证 WebSocket 传输
│       │   ├── fake.py            # 无 I/O 场景引擎；复用正式 parser/envelope/幂等规则
│       │   ├── fake_client.py     # 正式 Session WebSocket 的有界联调客户端
│       │   ├── application/       # 后续领取、执行、暂停、取消和上报
│       │   ├── rpa/
│       │   │   ├── base/          # 平台 Adapter、动作和页面契约
│       │   │   ├── browser/       # Playwright、Profile 和页面证据
│       │   │   ├── douyin/        # MVP 抖音实现；v1 路由/搜索 Page Object、Session、扫码和健康上报
│       │   │   ├── xiaohongshu/   # P1.3
│       │   │   ├── kuaishou/      # P1.5
│       │   │   ├── wechat_channels/
│       │   │   └── wechat/        # 按 OS 分实现
│       │   └── infrastructure/
│       │       ├── filesystem/
│       │       ├── browser/
│       │       ├── desktop/
│       │       └── logging/
│       ├── protocol/              # Control Plane ↔ Executor 版本化协议
│       │   ├── action_result.py   # A7-15 成功/失败/跳过/不确定证据词汇与封闭集合
│       │   ├── action_message_template.py # A7-05 固定文案/单变量封闭校验
│       │   ├── douyin_candidate.py # 最小 Candidate、来源与稳定去重键
│       │   ├── douyin_search.py   # 双端共享关键词/目标上限与脱敏输入对象
│       │   ├── executor_envelope.py # v1 判别联合、ID/时限/幂等/序号和安全 payload
│       │   ├── json_object.py     # Bootstrap/Envelope 共用的有界无重复 key JSON 解码
│       │   ├── schema.py          # Draft 2020-12 确定性导出与漂移检查
│       │   └── version.py         # 当前与最小/最大兼容版本
│       └── capabilities/
│           ├── content_studio/    # P2 服务端能力
│           └── ai_workflows/      # P3 服务端能力
├── migrations/                    # PostgreSQL/Alembic 迁移
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── rpa/                       # 录制页面样例与受控适配测试
│   └── fixtures/
├── pyproject.toml
├── uv.lock
├── automation-tool-executor.spec  # Local Executor 的 PyInstaller 配置
├── Dockerfile                     # Control Plane 部署镜像
└── README.md
```

T3-09 的具体落点保持分层：`application/task_command_delivery.py` 定义命令记录、投递用例与安全错误；`infrastructure/database/task_command_repository.py` 实现 PostgreSQL enqueue/lease/retry/ACK；`bootstrap/task_commands.py` 只做依赖装配；`api/executor_websocket.py` 仍是唯一网络收发入口。没有另建 dispatcher 数据库、消息队列或第二套协议模型。

T3-10 的 `executor/fake.py` 只依赖共享 `protocol/`，以正式 command envelope 驱动确定性无副作用场景并保存进程内回放账本；`fake_client.py` 只负责正式 WebSocket 传输。它们不能导入 `control_plane/`、RPA Adapter、文件系统、子进程或数据库，也不能替代 Wave 4 正式 Executor 的本机持久幂等账本、生命周期监管和真实平台实现。

T3-11 的 `application/task_event_convergence.py` 只做正式 TaskEvent payload 收窄、领域映射、deadline 和安全错误分类；`infrastructure/database/task_event_convergence_repository.py` 是唯一原子落库与 Task/Attempt/显式 Action 投影入口；`bootstrap/task_events.py` 只装配依赖；`api/executor_websocket.py` 仍是唯一生产接收入口。迁移 `20260718_0011` 只增加持久重放身份，不引入事件 JSON、队列、缓存或第二套状态机。

T3-12 的 `application/task_event_stream.py` 定义公开事件记录、batch/watermark 不变量和 Last-Event-ID 用例；`infrastructure/database/task_event_stream_repository.py` 只从 PostgreSQL 已提交事实读取；`bootstrap/task_event_stream.py` 装配依赖；`api/task_event_stream.py` 负责固定 SSE 帧、keepalive、终态/限时关闭和安全错误映射。迁移 `20260718_0012` 只增加结构化进度列。桌面侧正式解析放在既有 `frontend/src-tauri/src/control_plane.rs`，T3-12 专用 `visible=false` 配置、WDIO spec 和编排脚本仅用于真实入口验收；T3-15 已在同一 Rust SSE 源上接入 Tauri Channel/React reducer，没有创建第二个前端数据源。

T3-15 的 `frontend/src/api/control-plane/task-projections.ts` 定义严格公开 DTO、TanStack Query Key/options 与 source 契约；`features/task-runs/task-projection-reducer.ts` 只合并服务端快照和事件 post-state，`task-projection-controller.ts` 负责快照优先、续订、缺口回拉与有限降级；`platform/tauri/task-projection-source.ts` 只调用固定 Task Command 并接收 Tauri Channel。Rust `stream_task_events_with` 在正式 SSE 解析循环逐条推送，不把 Session/Header/原始帧交给 WebView。`tauri.task-projection-e2e.conf.json`、对应 WDIO spec/runner 和 `scripts/run_t3_15_acceptance.py` 仅存在于 `control-plane-e2e` 隐藏 App 验收，不进入生产资产。

T3-17 的 `domain/task_definitions.py` 定义唯一 `douyin.search_exposure.v1` 领域对象，迁移 `20260718_0013` 与既有 Task 复合 scope 绑定明确列定义；`api/tasks.py`、`application/tasks.py` 和 `infrastructure/database/task_repository.py` 沿同一正式创建链原子保存并校验幂等重放。桌面侧 `features/task-create/` 只处理封闭表单，`platform/tauri/task-creation-gateway.ts` 只调用固定 Rust Command；专用隐藏 Tauri 配置、WDIO spec 与 `scripts/run_t3_17_acceptance.py` 只承担真实 App→API→PostgreSQL 最终状态验收。

D6-03 的 `protocol/douyin_search.py` 是 Control Plane 与 Local Executor 之间唯一 Python 输入策略，公开版本化不可变值、80 个 Unicode code point 和 100 个目标的硬上限；`domain/task_definitions.py` 只消费并转译固定错误。桌面 `task-creation-gateway.ts`/`TaskCreate.tsx` 共享同一 Zod 关键词 Schema 和边界常量，Rust/OpenAPI/PostgreSQL 保留信任边界复验；`frontend/tests/douyin-search-input-boundary.test.mjs` 防止各层数值、Unicode 计数和引用关系漂移。D6-04 必须从公共 protocol 输入对象开始搜索，不得再写 Executor 私有关键词规则。

T3-18 的 `features/task-runs/TaskRunDetails.tsx` 组合权威 Task 快照、持久事件时间线、已有 Action 结果与状态相容控制；`platform/tauri/task-run-control-gateway.ts` 只映射四个固定 Rust Command，不提供通用 operation。专用隐藏 Tauri 配置、WDIO spec 与 `scripts/run_t3_18_acceptance.py` 从真实页面发起暂停、恢复、取消、紧停，并核对 PostgreSQL 命令、事件和终态。

T3-19 的 `src/test-harness/task-lifecycle.ts` 只实现 Feature 已有的窄 gateway/source 契约，以 `sessionStorage` 支持 Playwright 创建、控制、独立成功和整页刷新恢复；正式构建扫描继续拒绝任何 Harness 标记。`tauri.task-lifecycle-e2e.conf.json`、对应 WDIO spec 和 `scripts/run_t3_19_acceptance.py` 则从唯一隐藏真实 App 页面创建两个 Task，经正式 TypeScript/Rust/FastAPI/PostgreSQL/Executor 链完成取消与成功，再刷新 WebView 并核对数据库事实；Harness 不能替代该产品入口证据。

T3-20 的 `FakeExecutorClient.run_reconnecting` 只扩展无副作用测试 Executor 的有界连接恢复，不成为正式 Local Executor 生命周期实现。`tauri.task-restart-e2e.conf.json`、对应 WDIO spec 与 `scripts/run_t3_20_acceptance.py` 协调唯一隐藏 App、两个先后启动的 Uvicorn 进程和同一 PostgreSQL：页面在 Executor 离线时留下 pending cancel，停服刷新验证不可用，再由同一 FakeExecutor/Session 重连消费并从 App 读取取消终态；专用文件和信号目录均不进入生产资产。

E4-02 的正式进程入口固定为 `automation-tool-executor`。`bootstrap.py` 只读一条受限 stdin JSON，`runtime.py` 发送 Executor v1 Hello/Heartbeat、消费 E4-12 正式任务帧并输出固定健康事件，`transport.py` 是正式进程和 Fake 客户端唯一共享的网络零件，`cli.py` 只装配账本/处理器、安装 SIGINT/SIGTERM 并映射固定退出码。真实集成测试从安装后的控制台脚本启动独立子进程，连接真实 Uvicorn/正式 Session/Registry 后再发信号退出；没有调用内部函数冒充进程验收，也没有引入旧 stdio 任务协议。PyInstaller、Tauri 监管、本机账本与无副作用协议回放现已分别由 E4-03、E4-07、E4-11、E4-12 接入。

E4-03 使用 `automation-tool-executor.spec` 从同一 `executor/__main__.py` 构建 console `onedir`，不维护第二份 Python 入口；其历史交付当时未加入 Playwright。B5-07 已把 Playwright 提升为正式运行依赖并由同一 spec 收集 Python driver，但不执行浏览器安装，正式目录不得出现 `.local-browsers` 或 Playwright Chromium/Firefox/WebKit 缓存。常规冻结入口继续验证 bootstrap 与网络失败契约；测试专用探针另从生产 `browser_runtime.py` 启动显式系统 Chrome/Edge，探针不进入正式 onedir。`.github/workflows/desktop.yml` 已为 macOS/Windows 配置同一实包测试，只验证构建和启动，不上传、发布或签名产物；macOS 与 Windows 本机原生验收均已通过；Windows Hosted Runner 仍因 GitHub 账户 Billing/Actions spending limit 未启动，但不再作为本机产品验收阻塞。目录签名与可信安装仍由 E4-04/E4-05 承担，正式浏览器进程所有权由 B5-08 承担。

B5-08 的 `BrowserRuntime` 是 Local Executor 内部窄服务，不新增第二 Manager 或万能平台 runtime。它同时只拥有一个 Playwright driver/context，线程约束地投影主窗口、窗口集合、新窗口与触发式弹窗，固定动作/导航超时并限制单次等待上限；`BrowserWindow` 只能由所属 Runtime 定向关闭，原始 Page 只交给后续 Python 平台页面对象。正常退出关闭 context/driver，硬停止继续落到 E4-09 同一 Executor process group/Job Object。macOS 冻结探针已验证双窗口正常关闭和 `SIGKILL` 整树退出后 Profile 复验/显式解锁；Windows 同一冻结探针已由公开 ExecutorManager/Job Object 验证真实浏览器后代整树退出，探针不进入正式包。

B5-09 的 `executor/rpa/douyin/session.py` 是首个正式页面对象边界。它公开固定 `/user/self` 探测 URL、版本化 selector、五态枚举、固定 evidence 与派生熔断，只接受 `BrowserRuntime` 所属窗口；非官方 origin、冲突/缺失证据和页面异常 fail closed 为 `unknown`。测试专用 live probe 只输出状态变化与 `ready`，不输出 DOM、账号、Cookie 或 Profile 路径，也不进入产品 UI；B5-10 必须复用该模块，不能在 Rust/React 复制页面选择器。

B5-10 的 `executor/rpa/douyin/login.py` 是 detector 的窄工作流组合，不是第二 BrowserRuntime。它创建一个 Runtime-owned 专用窗口，固定打开 `/user/self`，以最多 10 秒的 Playwright 页面事实等待吸收异步二维码/用户资料加载，再由无参数 `recheck()` 投影扫码、手机确认、过期、健康、人工接管和未知状态。B5-11 将契约升级到 `douyin.qr-login.v2`，Session `risk` 只能成为 `handoff_required/risk_challenge`；生产模块不读取跨源挑战内部内容，也没有点击、填写、拖拽、识别或绕过方法。调用方没有 `QrScanned`、`Authenticated` 或任意页面状态注入面；冲突、等待/页面失败和未知 DOM 保持熔断，人工处理后只有页面重新成为 `healthy` 才恢复。测试 live probe 仅输出固定状态变化，二维码、验证码、Page、Profile 路径和账号均不输出；B5-13 组合 App 原入口时必须复用该对象，不能把选择器迁到 WebView。

B5-13/B5-14 的服务端查询和 logout prepare 落在 `control_plane/api/platform_sessions.py`、同一 application service 和 PostgreSQL repository，只返回 current Installation 的最小公开投影或 blocked revision；`platform_session_gates` 是新 Task/offer 的持久熔断事实。桌面页面落在 `features/platform-sessions/`，Tauri 适配器落在 `platform/tauri/platform-session-gateway.ts`；四个方法分别映射固定查询、打开处理、重新检查和安全注销 Command，不提供 URL、路径、Header、revision 或任意 invoke。Rust `executor_bootstrap.rs`/`executor_manager.rs` 在同一个 Executor stdin/stdout 上发送和验证 `atlcp1` 本机命令，`executor_platform.rs` 只从 AppData 解析 current Profile、owned lease 与重新受信的浏览器；注销由 `lib.rs` 固定编排 prepare→stop→定向删除→restart→path-free complete→权威查询。Python `executor/platform_commands.py` 在线程内复用 `DouyinQrLoginFlow` 或记录显式退出 epoch，健康消息仍由 `runtime.py` 经正式 WebSocket 发送。`scripts/run_b5_13_acceptance.py` 以唯一隐藏 App、隔离 Control Plane/PostgreSQL/signed package 和无头系统浏览器验收状态/注销链路并审计只删目标 Profile、保留 SQLite、阻断新任务且零残留；测试专用 headless 与动态 origin 不进入生产构建。

B5-15 的 `scripts/run_b5_15_acceptance.py` 使用另一个唯一 App 标识和同一 AppData 连续运行四次隐藏 App，逐轮复验 Profile marker/device/inode、Executor/Chrome 退出及服务端权威状态。`backend/tests/fixtures/automation-tool-executor-b515.spec` 只构建独立签名的验收 Executor，固定路由官方 probe origin 的 healthy/expired/risk 页面事实；正式 `backend/automation-tool-executor.spec` 不包含该入口。首次健康 epoch 的生产修复位于 `executor/ledger.py`，测试夹具不修改生产 detector、flow、Rust Manager、Tauri Gateway 或 Control Plane。

B5-16 的隐藏配置、WDIO spec 与 `scripts/run_b5_16_acceptance.py` 只协调一个正式页面调用期间的活跃浏览器审计。spec 只写临时 ready/release 信号，不读取 Profile；runner 从 current marker 在内部解析私有目录，核对唯一 Chrome 根和完整后代树的 `--user-data-dir`，再用 `lsof` 验证实际打开文件没有触碰默认 Chrome/Edge User Data，所有路径和 UUID 都不输出。`frontend/tests/default-browser-profile-isolation.test.mjs` 递归扫描生产源码并锁定 Rust→Executor→Playwright 唯一 Profile 链；验收配置、信号和审计代码不进入发布包。

D6-01 的 `executor/rpa/douyin/page_version.py` 是平台页面 route/version 唯一来源，不是 Task 领域模型或通用 URL parser。它封闭定义 `douyin.web.v1`、首页/Session/search 三个入口、固定非敏感 evidence 和 `require_entry()` 熔断；B5-09 的 probe URL 已从该文件导入。模块不依赖 BrowserRuntime/Playwright、Control Plane、数据库或协议，D6-02 页面对象只能消费该模型，不能复制 origin/path/version 判断。

D6-02 的 `executor/rpa/douyin/search_page.py` 是搜索 DOM 的唯一组合根。它只接收 Runtime-owned `BrowserWindow`，消费 D6-01 路由事实后集中管理 `douyin.search-page.v1` 的语义优先 selectors，并封闭输出 ready/login/dialog/unknown 状态；页面入口与锚点不一致、缺失、异常或动态消失均 fail closed。模块只返回重新确认可见的窄 Locator，不含导航或交互动作，不读 Cookie/storage/page body，也不把 URL、DOM、selector 或页面对象送入 Task、协议、Rust、React 或 Control Plane。D6-04 必须复用该对象，不能复制 selectors。

D6-04 的 `executor/rpa/douyin/search.py` 只负责编排一次受控搜索：消费公共 `DouyinSearchInput`，复用 `page_version.py` 的首页/canonical 结果 URL 与 `search_page.py` 的有界等待和控件访问器，依次导航、填写、单击、等待精确 URL 并复验结果列表。`search.py` 不含 selector 或官方 URL 字面量，Page Object 不因本任务获得写动作；`frontend/tests/douyin-search-execution-boundary.test.mjs` 锁定这条依赖方向和无滚动/评论/私信/存储/Control Plane 边界。`backend/tests/integration/test_douyin_search_execution_browser.py` 从生产 `BrowserRuntime` 启动无头系统 Chrome，以一次性 Profile 和隔离官方 origin 测试页调用真实执行入口；它不是 Web 产品、不会接管默认 Profile，也不冒充 D6-16 的真实账号验收。

D6-05 的 `executor/rpa/douyin/bounded_scroll.py` 是搜索成功后的有限控制层，不是 Candidate parser。它消费 D6-04 成功观察、公共目标上限和取消探针，只调用 `search_page.py` 的结果页观察/有界节点计数以及 Playwright `mouse.wheel`；最大轮次、滚动量、增长等待和轮询间隔固定在该模块。结果项 selectors 新增到唯一 Page Object，`frontend/tests/douyin-bounded-scroll-boundary.test.mjs` 阻止 selector、任意 locator、脚本、Cookie/storage、点击或 Control Plane 越界。`backend/tests/integration/test_douyin_bounded_scroll_browser.py` 通过一次性 Profile 和无头系统 Chrome 从 D6-04 搜索原入口继续真实 wheel；D6-06 才能读取并封闭候选字段。

D6-06 的 `protocol/douyin_candidate.py` 是 Candidate 值、最小摘要、来源和稳定键算法的唯一 Python 来源，`protocol/__init__.py` 提供公共导入。它不依赖 Playwright、RPA、Control Plane、SQLAlchemy 或 App；`frontend/tests/douyin-candidate-model-boundary.test.mjs` 锁定无头像/简介/联系方式/页面原文/URL 字段，D6-05 滚动层仍不解析候选。D6-10 的 wire payload 只复制最小公开字段，不传 dedupe key，也不能扩展自由字典。

D6-07 的 `executor/rpa/douyin/candidate_extraction.py` 只编排页面状态、封闭 evidence 与 D6-06 Candidate tuple，不含 selector、任意 locator、URL 字面量、HTTP/Control Plane 或存储访问。受控作者/名称 selector、有限字段读取及官方作者 href→目标 ID 缩减全部保留在唯一 `search_page.py`；源 href、query、页面正文和其他 DOM 属性不能越过 Page Object。`frontend/tests/douyin-candidate-privacy-boundary.test.mjs` 锁定这条依赖方向；`backend/tests/integration/test_douyin_candidate_extraction_browser.py` 从 D6-04 公开搜索入口使用一次性 Profile 和无头系统 Chrome 验证真实 Playwright 原调用路径，并在退出后关闭完整 Runtime。

D6-08 的 `control_plane/domain/douyin_candidate_policy.py` 只消费公共 Candidate/key 和 UTC 时间，提供固定 30 天历史窗口及四种封闭 disposition，不导入仓储、SQLAlchemy、Executor/RPA、HTTP 或 App。`control_plane/domain/__init__.py` 是 Control Plane 公共领域入口；`frontend/tests/douyin-candidate-policy-boundary.test.mjs` 锁定策略只使用 `dedupe_key`、不读取候选个人字段，且尚未提前进入 Executor Schema/Tauri。D6-09 的基础设施层只向该策略提供匹配当前 Installation/当前候选 key 的唯一最新历史事实，黑名单 key 由当前已认证用例传入；查询对象不能反向塞进领域模块。

D6-09 的 `control_plane/application/task_targets.py` 定义脱敏、不可变的 Target 持久记录，`infrastructure/database/task_target_repository.py` 是唯一 PostgreSQL 适配器，`schema.py` 与 Alembic `20260718_0016` 是同一 `task_targets` 结构的代码/迁移来源。适配器在 Installation/Task 行锁事务中查询当前 Candidate key 的同 Installation 历史、调用 D6-08、替换整批 Decision，并按 `(ordinal,id)` keyset 读取；应用/领域模块不导入 SQLAlchemy。`frontend/tests/task-target-database-boundary.test.mjs` 锁定表的复合归属、唯一顺序和历史索引，且持久 disposition/dedupe key 不泄漏到 Executor wire 或 Tauri。D6-10 收敛仓储从正式 PostgreSQL 原入口调用它，D6-11 再增加公开预览读取。

D6-10 的服务端路径按 `api/task_discoveries.py`（App Session/HTTP）、`application/task_discovery.py`（启动、有限批次累计与收敛）、`infrastructure/database/task_discovery_repository.py`（唯一事务事实）和 `bootstrap/task_discovery.py`（装配）分层；迁移 `20260720_0017` 只扩展既有 Task command/event 封闭词汇。Executor 侧 `browser_authority.py`、`discovery_operation.py`、`command_processor.py` 与当时的 SQLite v3 分别负责浏览器所有权、现有 RPA adapter 组合、正式 envelope/outbox 和重放；A7-04 已原地升级为 v4，并增加 `action_gate.py`、动作策略、紧停和准入事实。Rust `control_plane.rs`/`lib.rs` 只向 App 提供固定 `start_task_discovery`，WebView 不接触 Session、Candidate、浏览器或 Profile。`scripts/run_d6_10_acceptance.py` 使用唯一 hidden App、真实 Uvicorn/PostgreSQL 与正式 LocalExecutorProcess 验证这条纵向链，并只清理自己的 AppData、端口和 Compose 资源。

A7-08 的 `executor/rpa/douyin/comment_page.py` 与既有 `page_version.py` 共同拥有评论页信任边界：版本模型新增 canonical 视频详情入口，评论模块集中管理 input/submit/final/login/blocking selector 和有界等待，只返回封闭观察或 locator，不执行填写/点击。`tests/fixtures/douyin_comment_pages` 与对应 BrowserRuntime 集成测试只存在于测试树，以无头系统 Chrome 回放 ready→confirmed、阻塞和漂移；生产包不包含 Fake 页面，真实评论编排仍由 A7-11 承接。

A7-09 的 `executor/rpa/douyin/direct_message_page.py` 与同一 `page_version.py` 共同拥有主动私信页信任边界：版本模型新增 canonical 用户主页入口，私信模块集中管理进入会话、input/send/final、两类 permission、login/blocking selector 和有界等待，只返回封闭观察或 locator，不执行填写/点击。`tests/fixtures/douyin_direct_message_pages` 与对应 BrowserRuntime 集成测试只存在于测试树，以无头系统 Chrome 回放 profile→conversation→confirmed、关注权限拒绝和冲突漂移；生产包不包含 Fake 页面，真实私信编排仍由 A7-12 承接。

A7-10 的 `executor/rpa/douyin/profile_page.py` 是与评论/私信页面对象平行的通用只读主页信任边界，只集中管理 profile root、login/blocking selector；`browse.py` 只编排 D6-10 Candidate→共享 canonical 用户 URL→一次导航→有界 Page Object 复验与取消检查，不含 selector、发送控件、任意 URL、Cookie/storage、Control Plane 或持久化。`tests/fixtures/douyin_browse_pages` 与 BrowserRuntime 集成测试只存在于测试树，以无头系统 Chrome 回放 ready/login/blocked/drift，并用页面内陷阱计数证明评论/私信按钮未触发；A7-11/A7-12 才能进入真实动作执行。

A7-11 的 `executor/rpa/douyin/comment_action.py` 只编排既有 A7-04 `ExecutorActionGate`、A7-07 `ExecutorLedger` 和 A7-08 `DouyinCommentPage`，不复制授权、频控、selector 或持久化。最终文案只在内存展开和填写，持久事实仅为域隔离 effect/verification SHA-256；唯一 dispatch 许可前不点击，许可后无法确认的动作只能收敛 uncertain 或保留 dispatched，精确重放不再访问 DOM。`tests/integration/test_douyin_comment_action_browser.py` 通过生产 BrowserRuntime、无头系统 Chrome、官方-origin 隔离页和真实私有 SQLite 验证首次真实 locator 单击一次、verified 与重放零单击；该测试不进入生产包，也不替代 A7-16 的真实账号最终状态验收。

A7-12 的 `executor/rpa/douyin/direct_message_action.py` 只组合 A7-04 gate、A7-07 ledger 与 A7-09 Page Object。`prepared` 后可进入会话或从已打开会话恢复，填入仅驻内存的最终文案后才竞争唯一 send dispatch；两类权限在准备阶段和发送后分别投影，许可后不确定永不重发。`tests/integration/test_douyin_direct_message_action_browser.py` 从生产 BrowserRuntime 在无头官方-origin 隔离页执行真实 entry/input/send/final locator 与私有 SQLite，验证首次 entry/send 各一次、verified 重放零新增；Fake 页面和测试计数不进入生产包，也不替代 A7-17 真实账号最终状态验收。

A7-13 的 `executor/rpa/douyin/side_effect_recovery.py` 复用两类动作导出的验证摘要与既有 Page Object，只对 SQLite `dispatched` 执行最终锚点只读核对；prepared/verified/uncertain 原样投影，恢复层不拥有导航、selector 或任何动作 locator。`tests/integration/test_douyin_side_effect_recovery_browser.py` 以生产 BrowserRuntime、无头系统 Chrome、官方-origin 隔离页和两条真实 dispatched 事实验证评论/私信均可只读结算，页面评论提交、会话入口和私信发送计数保持 0；H8-05 已把该单次能力装入 crash-only 启动编排，无可靠页面上下文时明确结算 uncertain。

A7-14 不新增独立服务目录：现有 `action_risk_authorization_repository.py` 在精确重放后阻止 open circuit 的新授权，`task_event_convergence_repository.py` 在正式 Executor 结果事务中维护连续失败与 handoff，`schema.py` 和迁移 `20260721_0023_action_failure_circuits.py` 定义 `action_risk_results/action_failure_circuits` 及完整 scope 外键。`tests/integration/test_action_risk_authorization_lifecycle.py` 覆盖计数、并发、恢复、故障注入和认证 WebSocket 原入口，`test_action_failure_circuit_lifecycle.py` 覆盖迁移精确结构与降级；没有新增 App、浏览器、HTTP 业务接口或第二份结果协议。

A7-15 的共享证据位于 `protocol/action_result.py`，Executor 适配只存在于 `executor/rpa/douyin/action_result.py`；即时评论/私信和只读恢复 receipt 仍由各自模块拥有，适配器只输出正式 Task event 所需的 message type、Action ID 与封闭 evidence。Control Plane 按 `api/task_target_results.py`、`application/task_target_results.py`、`infrastructure/database/task_target_result_repository.py`、`bootstrap/task_target_results.py` 分层提供 Installation-scoped 只读投影；`schema.py` 与迁移 `20260721_0024_task_action_evidence.py` 将 evidence 和 Action outcome 一致性锁进 PostgreSQL。前端沿 `api/control-plane/task-target-results.ts`、`platform/tauri/task-target-result-source.ts`、Rust `control_plane.rs/lib.rs` 注入既有 `TaskRunDetails.tsx`，没有第二个详情页、Web 路由、Executor SQLite 读取或任意 IPC/URL 面。

`scripts/run_t3_18_acceptance.py` 是 A7-15 的原调用方验收：它只使用 `visible=false` Tauri 配置、专属 Compose project/随机端口/PostgreSQL/AppData 和正式 App Session，WebdriverIO 从现有运行详情触发固定 Tauri Command 并核对四类目标终态及受限证据。测试准备数据和 FakeExecutor 只提供远端边界的确定事实，页面请求必须真实经过 TypeScript、Rust、Uvicorn 与数据库；退出后 runner 回收 App、服务、端口、容器与私有目录。

H8-01 不新增第二套控制服务或 SQLite 表：`executor/command_processor.py` 接受正式 pause/resume 并生成 ACK/事件，`executor/ledger.py` 复用 command、checkpoint、outbox、action admission 与 side-effect 表实现安全检查点，`executor/runtime.py` 在恢复和空闲轮询推进待生效控制。`scripts/run_h8_01_acceptance.py` 复用既有隐藏 task-control App 入口，但在服务端 running 准备完成后切换到真实 Executor 进程；专属 Compose project、随机 PostgreSQL 端口、私有 AppData/Executor state 和后台窗口均在退出时清理。

H8-02 继续只修改 `executor/command_processor.py` 与 `executor/ledger.py`：普通 cancel 与 pause 共用持久控制推进器和 dispatch 门，账本根据同一 Attempt 的 verified/uncertain 副作用事实选择 cancelled/outcome_uncertain，不新增 cancel service、线程内存状态或 SQLite 表。`scripts/run_h8_02_acceptance.py` 复用 T3-14 的隐藏 task-termination App/Rust Command 和既有隔离脚手架，真实 Executor 只负责普通 cancel；原 spec 的 emergency-stop 半程由既有 FakeExecutor 收尾。H8-03 随后在现有 `executor_platform.rs`、manager、command processor/ledger/runtime 和运行详情轮询上接入独立离线 latch、完整进程树停止与重连补报，没有建立第二个紧停服务或 SQLite。

H8-04 的新增文件只属于跨进程验收：`tauri.app-crash-recovery-e2e.conf.json`、`wdio.app-crash-recovery.conf.ts`、`e2e-tauri/app-crash-recovery.spec.ts` 与 `scripts/run_h8_04_acceptance.py`。Rust 的准备与 App PID 探针只在 `control-plane-e2e` feature 存在，正式生产 handler/Capability/二进制不包含；生产业务目录、API、数据库 schema 和 React 页面均未新增恢复分支。runner 复用既有签名 Executor、任务确认/offer、真实 Control Plane 与账本夹具，先硬杀第一个隐藏 App，再以同一 AppData 启动第二个 App 并精确比较前后事实。

H8-05 的生产实现集中在 `executor_bootstrap.rs`/`executor_manager.rs` 的 crash-only bootstrap 标记，以及 `backend/src/automation_tool/executor/crash_recovery.py`、`ledger.py` 和 A7-13 recovery adapter；没有新增业务 HTTP API、页面状态机或云端表。纵向验收文件为 `tauri.executor-crash-recovery-e2e.conf.json`、`wdio.executor-crash-recovery.conf.ts`、`e2e-tauri/executor-crash-recovery.spec.ts` 与 `scripts/run_h8_05_acceptance.py`，专用 IPC 仍只存在于 `control-plane-e2e` feature。runner 使用独立 AppData/Compose/SQLite，真实注入签名 Executor 崩溃并核对本机/云端双账本。

H8-06 只修改既有 `backend/src/automation_tool/executor/runtime.py` 传输循环：收到服务端 `1012` 后，同一进程有界重连并从原 SQLite outbox 精确续传；没有新增恢复服务、表或协议。纵向验收文件为 `tauri.control-plane-recovery-e2e.conf.json`、`wdio.control-plane-recovery.conf.ts`、`e2e-tauri/control-plane-recovery.spec.ts` 与 `scripts/run_h8_06_acceptance.py`；feature-gated 准备命令只用于唯一隐藏 App。runner 使用独立 AppData/Compose/SQLite，在同一签名 Executor PID 被暂停时由 App 发出正式取消，再真实停止/重启 Uvicorn 并核对原命令、事件、checkpoint 与 `restartCount=0`。

H8-07 继续复用唯一 `runtime.py` 和 `ledger.py`，不新增第二个网络代理、事件队列或协议：SQLite v6 网络闸门与既有紧停 guard 原子阻止离线 dispatch，原 outbox 同时承担最多 1000 条/16 MiB 的有界 spool，异常 socket/初次网络错误走既有有界重连预算。纵向验收文件为 `tauri.network-recovery-e2e.conf.json`、`wdio.network-recovery.conf.ts`、`e2e-tauri/network-recovery.spec.ts` 与 `scripts/run_h8_07_acceptance.py`；runner 复用 H8-06 的签名包、双账本和进程辅助函数，强杀并反复恢复真实 Uvicorn，精确核对同一 PID、离线安全点、事件续传和 `restartCount=0`。

D6-11 的服务端路径按 `api/task_target_previews.py`（App Session/HTTP DTO）、`application/task_target_previews.py`（强类型快照、cursor、排除/确认用例）、`infrastructure/database/task_target_preview_repository.py`（行锁、revision、幂等与事件事务）和 `bootstrap/task_target_previews.py`（装配）分层；迁移 `20260720_0018` 增加最小排除/确认关系。前端 `api/control-plane/task-target-previews.ts` 与 `platform/tauri/task-target-preview-source.ts` 只处理生成 DTO 和固定 Command；Rust `control_plane.rs`/`lib.rs` 负责 Session 注入、固定 URL 和严格响应解析。`scripts/run_d6_11_acceptance.py` 通过唯一 hidden App、真实 Uvicorn/PostgreSQL 验证列表、排除、确认和重放，且只清理本次 AppData、端口与 Compose 资源。

D6-12 的用户页面位于既有 `features/task-runs/`：`TaskTargetPreview.tsx` 管理目标摘要、策略标记、完整排除集合 Mutation、同意图幂等重试和确认，`TaskRunDetails.tsx` 只按当前 Task 状态/持久确认事件决定挂载，避免把预览状态带到另一个 Task。正式 source 仍由 `main.tsx` 在唯一组合根构造并逐层注入，不进入业务组件的 Tauri import。`e2e-tauri/task-target-preview-ui.spec.ts` 与 `scripts/run_d6_12_acceptance.py` 使用 D6-12 独立隐藏配置、AppData、Compose project、端口和 Executor state，从真实页面发出读取/排除/确认调用并核对 PostgreSQL；测试准备 Command 仅存在于 `control-plane-e2e` 特性，正式包不包含。

D6-13 复用既有 `application/task_command_delivery.py` 与 `infrastructure/database/task_command_repository.py`，没有新增 dispatcher、消息队列或第二协议。`schema.py`/Alembic `20260720_0019` 只给 Outbox 增加确认 message 绑定；仓储以 typed Task definition 判断 offer 是否可能承载业务动作，在 enqueue 固定当前确认并在 claim 关联复验。`scripts/run_d6_13_acceptance.py` 复用共享隔离 PostgreSQL/Uvicorn/WebSocket 脚手架，证明未绑定和确认失效命令不离开数据库、当前绑定命令才到达正式 Executor 网络入口；它不调用 App API、不启动 Tauri/浏览器，也不伪造 Wave 7 ActionAuthorization 或平台动作。

D6-14 的 `executor/page_drift_artifact.py` 只拥有页面漂移固定 Schema、Policy 与窄引用；H8-09 的 `executor/local_artifact.py` 统一拥有稳定 ID、摘要、媒体类型、大小、受控相对路径、独占写入、按 ID 解析/枚举/读取和文件系统权限边界。H8-10 的 `executor/browser_diagnostic_artifact.py` 复用同一 Store，只生产脱敏 viewport PNG 与固定结构 Trace；`discovery_operation.py` 对失败自动触发，对成功只接受 bootstrap 的用户设置。设置链位于既有 `Diagnostics.tsx` → `platform-adapter.ts` → `lib.rs` → `executor_platform.rs` → `executor_manager.rs`/`executor_bootstrap.rs`，AppData 只保存 exact bool，不暴露 Artifact 路径。`tests/integration/test_douyin_discovery_fake_pages.py` 从正式 command processor 与无头系统 Chrome 覆盖失败/默认成功/用户开启成功，E4-14 隐藏 App 验收覆盖真实设置与 signed Executor 启动。当前没有新增数据库表、通用文件浏览器、上传或删除通道。

H8-14 沿现有工作台分层增加唯一只读指标链：`control_plane/application/workbench_metrics.py` 定义结构化快照和安全不变量，`infrastructure/database/workbench_metrics_repository.py` 以单条 Installation-scoped SQL 聚合既有 `tasks`/`task_actions`，`bootstrap/workbench.py` 装配，`api/workbench.py` 暴露固定 `/metrics`。桌面侧继续使用 `features/workbench/`、`platform/tauri/workbench-gateway.ts` 和既有 Rust `control_plane.rs`/`lib.rs`，没有第二个页面、通用 HTTP/IPC、客户端数据库或新表。`tauri.workbench-metrics-e2e.conf.json`、WDIO spec 与 `scripts/run_h8_14_acceptance.py` 只用于唯一隐藏 App 的真实 PostgreSQL/跨 Installation/只读验收，并在结束后回收专属 AppData、端口和 Compose 资源。

H8-15 不新增运行时服务，而以 `contracts/quality/mvp-failure-matrix.v1.json` 作为第 4.1 节失败矩阵的可执行证据登记：15 类边界、78 个原子失败分支逐项绑定仓库内测试文件和精确测试锚点。`frontend/tests/mvp-failure-matrix.test.mjs` 校验边界顺序、词汇同步、唯一性、证据路径/锚点与真实平台待验收集合；B5-15、D6-16、A7-16、A7-17 仍保持独立真实证据，不能被 Fake 页面或分层测试替代。审计补齐了 PostgreSQL 未知迁移目标不改变当前 revision、连接池耗尽安全映射，以及生产安装包拒绝 Profile、Cookie、Executor SQLite 与诊断资料的门禁。

H8-16 的 `contracts/quality/mvp-spec-review.v1.json` 把产品规划 14 条 MVP 验收与 10 项关键架构决策逐项绑定到当前证据，并把不符合项映射到 H8-16A～H8-16F。审计不把“底层模块存在”当成“用户闭环完成”：当时正式 React 组合根尚未消费已有 `start_task_discovery`，确认事务只收敛到 `queued`，生产端没有逐目标授权/投递，`ExecutorCommandProcessor` 对 `task.offer` 仍生成固定 success batch，数据库单活索引当前按 Task 而非 Installation，启动 Gate 也只检查 Control Plane。`frontend/tests/mvp-spec-review.test.mjs` 持续校验产品验收原文、状态枚举、修复任务与源码锚点，后续每个修复任务必须将对应 finding 从 open 收敛并更新原调用方证据。

H8-16A 在既有 `features/task-runs/` 内新增 `task-discovery.ts` 严格领域边界，在 `platform/tauri/task-discovery-gateway.ts` 增加唯一固定 Command Adapter，并由 `main.tsx → App → WorkbenchShell → TaskRunDetails` 注入；业务组件不导入 Tauri、不接触 HTTP、Session、Candidate 私密字段或浏览器路径。D6-10 的 feature-gated 验收 Command 现在只注册 Installation 并创建 draft Task，`e2e-tauri/task-discovery.spec.ts` 必须从隐藏 App 的正式页面点击“开始目标发现”，再由 `scripts/run_d6_10_acceptance.py` 的真实 PostgreSQL/Uvicorn/LocalExecutorProcess 核对收敛事实；正式包不包含测试准备命令。

H8-16B 只扩展既有发现纵向切片：Alembic `20260721_0025` 与 `schema.py` 拥有 Installation 级非终态 Attempt 部分唯一索引，`task_discovery_repository.py` 在既有 Installation 行锁内执行同键重放与竞争检查，API/Rust/TypeScript 沿固定发现调用链传递封闭忙碌码。D6-10 验收准备 Command 只多创建一个竞争草稿 Task；独立同步 Command 只在隐藏 App 已观察忙碌提示后写测试信号，不启动发现、Executor 或平台动作，且受 `control-plane-e2e` feature 隔离。

H8-16C 的唯一推进链为 `api/executor_websocket.py → application/action_execution_orchestration.py → infrastructure/database/action_execution_orchestration_repository.py → application/task_command_delivery.py`。仓储复用 A7-02 授权、既有确认/Target/Attempt/Outbox 表，只由迁移 `20260721_0026` 增加 `task_commands.action_id` 绑定；`protocol/executor_envelope.py` 拥有 `action.execute` typed payload，TypeScript/Rust 适配器只做同一共享 fixture 的严格解析。`bootstrap/action_execution.py` 是服务端私钥与风控阈值的唯一部署组合根；React、Tauri IPC、本机 SQLite 和系统钥匙串没有新入口。

H8-16D 的 Executor 应用入口仍是 `executor/command_processor.py`，新 `executor/action_operation.py` 只负责把已持久接收的 typed `action.execute` 组合到 A7-03/A7-04 准入、A7-10 浏览、A7-11 评论、A7-12 私信和 A7-15 结果适配，不复制 Page Object、selector 或副作用状态机。`executor/cli.py` 只从 Tauri 认证 stdin bootstrap 取得编译期固定公钥与本机硬限制并构造生产 Operation；`executor_bootstrap.rs`/`executor_manager.rs` 不新增 WebView/HTTP 配置面。SQLite v7 仅扩展封闭命令类型并对动作 envelope 保存脱敏投影，完整 authority、展示名和文案仍只驻留当前内存。`tests/integration/test_action_command_processor_browser.py` 同时覆盖 Processor 原入口与真实 LocalExecutorProcess WebSocket 原入口，全部使用动态 loopback 端口、隔离 Profile 和无头系统浏览器。

D6-15 只在 `tests/fixtures/douyin_discovery_pages/` 增加七个静态 HTML，并由 `tests/integration/test_douyin_discovery_fake_pages.py` 统一编排六种场景；生产 `executor/rpa/`、协议、Control Plane、Tauri 与打包配置零改动。D6-04/D6-05/D6-07 的三个真实浏览器集成测试改为读取同一首页和结果样例，删除重复内联 DOM。语料契约固定文件集合、16 KiB 单文件上限并拒绝外部 URL/fetch/Cookie/storage；正式 task command、Page Object、有界滚动、隐私提取、D6-14 Artifact 和 Runtime 清理仍是被测主体，Fake 只替代远端页面内容。

D6-16 的 `scripts/run_d6_16_browser_acceptance.py` 是显式真实账号验收 runner，不进入 App/Executor 包。它只从进程环境接收既有 App 私有 Profile 路径，用无头系统 Chrome 先探测 Session，再构造正式 `task.discover` command；stdout 只允许封闭 session/outcome/evidence/candidate count，不输出候选摘要或路径。首轮真实运行确认 Session healthy，但首页验证码 iframe 进入 handoff；对应产品修复仅把 `session.py` 的同一风控 selector 导入 `search_page.py`，没有复制 selector 或新增验证码 Adapter。D6-16 仍待真实候选与隐藏 App 预览补验。

A7-01 新增 `backend/src/automation_tool/control_plane/domain/action_risk_policy.py` 与对应纯单元测试。模块只依赖现有 Installation 资源 ID、抖音任务动作枚举、任务目标上限和跨运行时整数上限；不导入 SQLAlchemy、FastAPI、Executor、Playwright 或 Tauri。`ActionRiskScope` 是安装实例/平台/动作复合键，`ActionRiskPolicy` 保存显式硬限制且无运营默认值；不得把旧 `agent-platform` 的账号、租户、RBAC、冷启动额度或内存锁迁入。

A7-02 新增应用层不可变 `ActionRiskAuthorization`/稳定失败类型、`infrastructure/database/action_risk_authorization_repository.py`、Alembic `20260720_0020` 和对应单元/真实 PostgreSQL 生命周期测试。Schema 中的 `action_risk_authorizations` 只保存强类型资源绑定、封闭平台/动作、策略版本与授权时计数，不保存任意 JSON、页面内容、Cookie、Token、文案或本机路径；两个补充复合唯一约束只为 Action/Target 外键绑定 ordinal。Repository 复用既有 Task、Attempt、Target、确认、Session health/gate 与 `task_actions` 表，不另建账号中心、内存计数器、HTTP 协议或第二套副作用账本。A7-03 在此内部事实之上建立可下发短期授权。

A7-03 新增共享 `protocol/action_authorization.py`、Control Plane-only `infrastructure/security/action_authorizations.py` 和 Executor-only `executor/action_authorization.py`。共享层拥有 canonical claims/token/parser、`ProtocolActionId`/`ProtocolTargetId` 与从任务定义上移的封闭动作枚举；签发层只把 A7-02 事实映射为 Ed25519 token，验签层只用固定公钥匹配当前执行意图。私钥加载与公钥固定由后续部署/Executor 组合根负责，不能从 React、HTTP payload、普通配置、SQLite 或系统钥匙串传入；当前没有迁移、API、Executor v1 Schema、Rust 或 Frontend 产品代码。真实 PostgreSQL 集成测试从正式 A7-02 Repository 授权后再签发/验签，不用 Mock 数据库或内部伪事实替代。

A7-05 新增共享 `protocol/action_message_template.py` 和 Alembic `20260720_0021`，并收紧既有 `domain/task_definitions.py`、React/TypeScript 表单 Gateway、Rust `control_plane.rs` 与 PostgreSQL check constraint。各层只复验同一 500 字符、安全文本、非空字面和 `{{target_display_name}}` 封闭变量集；不新建模板引擎、LLM 调用、动作 wire 或第二份任务定义。`frontend/tests/action-message-template-boundary.test.mjs` 防止五个生产边界变量/长度规则漂移，`scripts/run_t3_17_acceptance.py` 从唯一隐藏 App 原表单入口验证非法模板不发请求、合法模板经真实 HTTP 落 PostgreSQL。

A7-06 在既有目标预览模块内扩展 `TaskTargetConfirmationIntent`、仓储与 API，不另建第二套确认服务。Alembic `20260720_0022_task_target_confirmation_intent.py` 负责既有事实回填/降级；共享 TypeScript 任务定义 Schema 下沉到 `api/control-plane/douyin-search-exposure.ts`，创建 Gateway 仅重导出，预览 Source、Rust Client 与 React 面板消费同一封闭 action/template/revision 结构。隐藏验收继续复用 `scripts/run_d6_12_acceptance.py`，只增加后台 revision 变化与旧提交拒绝场景。

E4-04 的 `package_manifest.py` 是唯一 Manifest 生成器和 `automation-tool-build-executor-manifest` CLI：发布私钥只接受 stdin 的 32 字节 seed；整个 `onedir` payload 以受限 ASCII 相对路径排序，逐文件记录大小/SHA-256，并以固定域、长度前缀、大小和原始摘要计算目录 SHA-256。canonical Manifest 原始字节由独立 `atems1` Ed25519 envelope 签名；`contracts/protocol/executor-package-manifest-v1.schema.json` 固化 exact fields，`contracts/fixtures/executor-package-v1/valid/` 用明确的测试 seed 提供 inert 跨语言验签样例。生成器拒绝 symlink、非普通文件、错误入口、平台/架构/版本/build ID、读取竞态和资源超限；Rust 可信读取、安装与防降级不在 Python 中伪造，继续由 E4-05 承接。

E4-05 的 `src-tauri/src/executor_package.rs` 直接消费同一 Manifest/`atems1` fixture，不复制 Python 签发逻辑。它以 `ed25519-dalek::verify_strict`、`sha2`、受维护的 `semver` 和 `walkdir` 完成验签、canonical/exact-field 解析、当前平台/架构/入口绑定、允许范围、已安装版本防降级和完整目录双枚举；每个 payload 由拒绝 symlink 的稳定文件句柄读取并核对 Unix dev/inode 或 Windows volume/file index。公开 Rust API 只返回验证后的版本/build/入口/统计和固定错误码，没有 Tauri Command、React、URL 或远程信任配置。E4-07 只能从 Rust 受信资源边界装配该 verifier 并在启动前使用，不能把路径、公钥或版本策略扩成 IPC。

E4-06 的 `src-tauri/src/executor_bootstrap.rs` 只提供 Rust 内部 `LocalSessionToken`、受限 bootstrap 输入和事件证明 verifier：系统 CSPRNG 每次产生 32 字节，写入 stdin 时临时编码为 64 位小写十六进制，Drop/临时缓冲区均清零；Control Plane Session 保持独立字段。Python `authentication.py` 从 `SecretStr` 派生可清零 HMAC key，stdout 只输出绑定固定域、事件名和协议版本的 `atlep1` 证明。Rust 以维护中的 `hmac` crate 常量时间校验；两端共享固定测试向量但不提交真实令牌。模块没有启动进程、命令行/环境传密、Tauri Command 或 React 面，E4-07 才把它与 E4-05 verifier 组合为 Manager。

E4-07 的 `src-tauri/src/executor_manager.rs` 组合 E4-05/E4-06，但不复制两者规则：Manager 每次 start 复验固定包根并只启动验证后的 Manifest 入口，stdin 一次写入 bootstrap，stdout 只消费认证 lifecycle 事件；一个 Mutex 线性化 start/status/stop，错误、超时与 Drop 回收直接子进程。它不含旧 stdio task invoke、`serde_json::Value`、capability、任意路径/URL/命令或 Tauri Command。`scripts/run_e4_07_acceptance.py` 使用真实 Manifest CLI、PyInstaller onedir、本地 Uvicorn/Session/Registry 和公开 Rust Manager 原入口证明 `registered → heartbeat → unregistered`；私有配置只以 `0600` 临时文件存在并删除。后台退出监管、进程树、stderr 诊断和桌面 PlatformAdapter 分别留给 E4-08～E4-10/E4-13。

E4-08 不新增 supervisor 文件或第二生命周期源，而是在同一 `executor_manager.rs` 中加入显式 `ExecutorRestartPolicy`、唯一命名后台线程和 running/restarting/stopped 内部状态机。只有 OS crash 能在预算内转入 pending restart；每次恢复仍复用 E4-05/E4-06 的原始公开边界重新验包和生成令牌。显式 stop 先移除 pending/running，正常或固定失败退出直接停止，Drop 先 join supervisor；公开状态仅增加 `restartCount`。测试用已签名真实进程和 OS SIGKILL 证明初次+两次恢复的硬事实，临时计数位于包外并由 RAII 删除；PyInstaller/Uvicorn 正式链路另行回归。完整进程树现已由 E4-09 接入，Windows 原生恢复与重启预算已于 2026-07-20 通过实包验收。

E4-09 仍不新建进程服务：`RunningExecutor` 直接拥有跨平台 `ProcessTree`。Unix 在 spawn 前建立独立 process group，Windows 在 suspended child 上先配置并挂入 kill-on-close Job Object 再恢复；所有 setup 失败、启动/停止超时、显式停止后的剩余后代、异常退出准备恢复和 Manager Drop 都复用同一树终止原语。Rust 测试让真实签名主进程生成忽略 `SIGTERM` 的孙进程并核对 PID 消失，未使用 Mock 或 shell 进程列表冒充；Windows 原生签名实包已于 2026-07-20 覆盖显式停止、挂起停止、启动超时、Manager Drop 和崩溃重启整树清理。

E4-10/H8-11 的 `executor_diagnostics.rs` 只负责 stderr 安全文本，不承担生命周期或业务日志。Manager Core 持有唯一内存队列，各次启动的 reader 共享它；输入以固定容量流式消费，超长/非法编码 fail closed，再按 `contracts/fixtures/executor-diagnostics-v1.json` fixture v2 清除凭据、Header/Cookie、完整 URL、页面/消息内容、错误原文和私有路径，并执行行数/单行/总字节上限。Python `executor/diagnostics.py` 委托根级 `logging_redaction.py` 回放同一 fixtures，但 Rust 仍对原始 stderr 独立重做脱敏，不能信任进程内结论。真实 signed 进程测试证明公开 Manager `diagnostics()` 原入口；隐藏 App 继续从既有 `get_executor_diagnostics` 读取，不新增生产 Tauri Command 或持久日志。

E4-11 的 `executor/ledger.py` 是正式 Local Executor 唯一本机 SQLite 入口，不复用 FakeExecutor 内存字典，也不导入 Control Plane 仓储。Rust `ExecutorLaunchConfiguration` 持有状态目录并经既有一次性 bootstrap 传递；Python CLI 在联网前创建固定 `executor-ledger.sqlite3`，从 v1 identity/commands/attempt checkpoints/outbox、v2 平台 Session、v3 发现状态、v4 动作策略/紧停/准入、v5 副作用状态、H8-07 v6 网络闸门原地迁移到 H8-16D 当前 v7 typed action/脱敏命令投影。command 双键/指纹、Attempt 连续 sequence、checkpoint revision/CAS、最多 1000 条/16 MiB 的未交付协议 outbox、动作准入及 prepared/dispatched/verified/uncertain 状态均在该模块内事务化；`side_effect_ledger.py` 只承载封闭脱敏值对象。测试覆盖真实并发、重开恢复、逐版迁移、身份错绑、损坏、symlink/reparse/权限/文件 identity 竞态。

E4-12 的 `executor/command_processor.py` 是正式任务帧进入本机账本的唯一应用层；它不导入 FakeExecutor，也不直接访问 Control Plane 仓储。`task.offer` 现在只原子提交 `task.accept/task.started` 并保持 running，绝不再生成固定平台成功；H8-16D 的后续 `action.execute` 必须经过生产 Operation 才能提交 `action.accept/step.started/封闭结果`。`runtime.py` 在 Hello 后恢复并逐条发送 outbox，成功发送后才标 delivered；精确重放不再次访问浏览器或副作用入口。既有 E4-12 验收继续证明进程/账本重放边界，H8-16D 浏览器集成测试证明真实动作入口。该账本没有 Tauri Command/React API；E4-13 只装配生命周期 Adapter，E4-14 已完成隐藏 App 验收。

E4-13 的 `src-tauri/src/executor_platform.rs` 是唯一 App 组合根：SQLite 状态和稳定 Executor UUID 从 Tauri `app_data_dir/local-executor` 固定派生，debug 包根也保留在该目录；P9-03 的 release 包根改由 Tauri `resource_dir()/local-executor/package` 固定装配，不接受 WebView 路径。重启所需 Installation 和短期 `executor.connect` Session 只由 Rust Control Plane client 换取。`src/platform/tauri/platform-adapter.ts` 只 invoke 四个无参数生命周期 Command，并对状态和诊断 DTO fail closed；`features/diagnostics` 只消费该接口。账本、Session、PID、路径、包信任参数和原始 stderr 都不进入 React。

E4-14 的 `tauri.executor-lifecycle-e2e.conf.json`、`wdio.executor-lifecycle.conf.ts`、对应 spec 与 `scripts/run_e4_14_acceptance.py` 只服务于 `control-plane-e2e` 隐藏 App 验收。它们从真实诊断页驱动正式 PlatformAdapter，使用专属动态端口、Compose project、AppData 和 signed PyInstaller 包，实际注入 OS crash/hang 后验证 supervisor、进程树和私有 SQLite；测试 origin、故障与退出 Command 不进入默认构建。生产 `lib.rs` 的 event loop 在 `ExitRequested/Exit` 显式停止唯一 Executor，避免依赖测试驱动或析构时机回收。

E4-15 的 `build.rs` 是 release 验证公钥的打包前 fail-closed 门；`tauri.dev.conf.json` 是唯一含 1420/devCSP 的开发覆盖，正式 `tauri.conf.json` 不再携带开发地址。`frontend/scripts/audit-production-package.mjs` 检查真实 release 二进制、生产资产、正式配置与 `cargo tree --no-default-features`，而根 `scripts/run_e4_15_acceptance.py` 负责缺失/畸形公钥失败证明、唯一临时 target 构建和精确清理。验收公开公钥不用于发布签名，临时制品不启动、不上传、不保留。

P9-01 的 `executor/macos_candidate.py` 与 `scripts/run_p9_01_acceptance.py` 把既有 PyInstaller/Manifest 能力收敛成唯一 macOS 候选构建链。命令只向一个不存在的新目录输出，build/cache/dist 使用输出同级临时目录并在结束删除；候选必须包含生产 Playwright driver 和 base library、不含浏览器缓存、symlink、仓库/临时绝对路径，所有 Mach-O 与当前架构匹配并在 framework 别名规整后完成可复验的 ad-hoc 签名。验收用一次性测试 seed 证明离线 Manifest 签名准备后连同产物删除；没有 Developer ID、公证、上传或 Tauri 资源装配，后两者归 P9-03。

P9-02 的 `executor/windows_candidate.py` 与 `scripts/run_p9_02_acceptance.py` 沿同一正式 spec 建立唯一 Windows 候选构建链。命令只向不存在的新目录输出，复制前后都审计当前架构 PE、Playwright driver/base library、开发路径、浏览器缓存、symlink/reparse point、特殊文件和资源上限；原生 runner 再验证一次性 Manifest、冻结入口固定安全失败、只读 UIAutomationClient 能力及 E4-09 Job Object 后代树清理。该入口不启动 App 或浏览器、不安装驱动、不上传产物；Windows 实机运行前保持 `🔍 待验收`。

P9-03 的 `tauri.macos-candidate.conf.json` 与 `scripts/run_p9_03_acceptance.py` 在不增加 Capability/CSP/测试 Feature 的前提下，把 P9-01 输出映射到 App 的 `Resources/local-executor/package`。候选配置不锁死发布 identity；runner 只在临时覆盖中强制 ad-hoc，并用一次性 Ed25519 seed 签发正式格式 Manifest，构建 production-mode `.app/.dmg`，逐文件比较源/Resources/DMG 中的 SHA-256 与大小，复验 Manifest、Mach-O、App codesign、DMG 校验/只读挂载及 E4-15 生产二进制边界；所有输出只在 `/private/tmp` 存活且不启动 App、浏览器或服务。Developer ID Application、公证和 Gatekeeper 无警告分发仍是外部证书门禁。

T3-13 的 `application/task_controls.py` 定义 pause/resume 用例、公开结果和稳定错误；`api/task_controls.py` 只做 Installation-scoped HTTP 映射；既有 `SqlAlchemyTaskCommandRepository` 在同一 Outbox 内原子分配控制 sequence，既有事件仓储再校验最新 ACK/correlation 后收敛状态，没有新增控制表或第二状态机。桌面侧继续扩展同一个 `control_plane.rs` 固定 operation allowlist，专用 `visible=false` 配置、WDIO spec 与 `scripts/run_t3_13_acceptance.py` 只承担真实产品入口验收。

T3-14 复用同一 `task_controls.py`、HTTP router、Outbox repository 和事件收敛仓储：cancel/emergency-stop 首次请求在命令事务内把 Task/Attempt 投影到 CANCELLING，终态再由匹配最新 ACK/correlation 的 Executor 事件决定，完成竞态仍服从领域状态机。桌面侧仍只扩展同一个 `control_plane.rs` 固定 operation；T3-14 专用 `visible=false` 配置、WDIO spec 与 `scripts/run_t3_14_acceptance.py` 顺序验证取消和紧停，不建立第二网络桥或第二状态源。

### 4.1 Backend 依赖方向

```text
Control Plane API ──> application ──> domain
                           │              ▲
                           ▼              │
                     infrastructure ──────┘

Control Plane ──> protocol <── Executor application ──> RPA ports
                                                        │
                                                        ▼
                                             Executor infrastructure
```

规则：

- Control Plane 不得导入 Playwright、微信桌面实现、平台 Cookie 或用户本机路径；
- Executor 不得实现产品页面、内容/AI 工作流 API 或服务端业务数据库；
- Control Plane `domain/` 不依赖 FastAPI、PostgreSQL 或部署环境；
- 两个进程只通过 `protocol/` 的版本化消息协作，不互相导入内部实现；
- Executor `application/` 编排执行，不直接写页面选择器；
- `rpa/base/` 定义平台契约，各平台实现只影响自身目录；
- 页面选择器和页面版本不得进入任务领域模型；
- 两侧 `infrastructure/` 只实现端口，不承载频控、幂等或状态转换决策；
- P2/P3 能力只依赖公开任务、事件、Artifact 和 Adapter，不反向污染 RPA 核心。

## 5. 前后端契约

```text
Python Pydantic / FastAPI
      │
      ├── OpenAPI ───────────→ contracts/openapi/
      │                            │
      │                            ▼
      │                    TypeScript generated client
      │
      └── Executor JSON Schema → contracts/protocol/
                                      │
                                      ▼
                              共享原始 wire fixtures
                              ├── Python parser
                              ├── Rust parser
                              └── TypeScript parser
```

规则：

- Pydantic 模型是 REST 和事件 wire format 的唯一手写来源；
- TypeScript 类型和客户端从 OpenAPI 生成；
- Rust 只维护进程桥接所需的最小 DTO，并使用公共 fixtures 做一致性测试；
- 所有对象拒绝未知字段，协议版本不匹配时明确失败；
- 破坏性字段变化升级协议版本，不通过“可选字段堆积”偷偷改变语义；
- `contracts/fixtures/` 同时保存有效和无效样例，三种语言必须得到一致结论。

## 6. 进程与发布单元

开发环境：

```text
Tauri App ──HTTP/SSE──> 本机 Control Plane（FastAPI 热更新）
    │                         │
    │                         └── PostgreSQL（本机 Docker）
    │
    └──监管 Local Executor ──受认证通道──> Control Plane
         └── 系统 Chrome/Edge + App 独立运营 Profile
```

客户 Demo：

```text
Tauri App ──HTTPS/SSE──> 云端 Control Plane（同一 Python 包）
    │                         │
    │                         └── 云端 PostgreSQL
    │
    └──监管 Local Executor ──出站设备认证通道──> Control Plane
```

App 使用受控 Profile 配置 `baseUrl`，开发和 Demo 只切换端点、凭据与基础设施，不修改业务源码。

Tauri 正式安装包包含 React WebView 资源、Rust 原生桥接和 PyInstaller `onedir` Local Executor。它不包含：

- Control Plane 或 PostgreSQL；
- Web 前端服务器或公开网页；
- 单独安装的 Python；
- 用户默认 Chrome Profile；
- 测试 WebDriver、测试 Adapter 或真实平台凭据；
- 第一阶段未启用的 AI 中台。

## 7. 本地运行数据

设备侧运行数据使用操作系统 App 私有数据目录，不能写入源码目录：

```text
app-data/
├── device-identity-ed25519-v1
├── device-credential-v1
├── local-executor/
│   ├── executor-id-v1
│   ├── package/
│   └── state/
│       ├── executor-ledger.sqlite3
│       ├── artifacts/evidence/page-drift/<artifact-id>.json
│       └── artifacts/diagnostics/{screenshots,traces}/<artifact-id>.*
├── browser-profiles/
│   └── douyin/<canonical-profile-uuid>/
├── logs/
├── diagnostics/
└── settings.json
```

规则：

- 目录和文件权限按当前用户最小化；
- 浏览器 Profile 不进入普通备份、日志或导出；
- Artifact、日志和诊断数据都有数量、大小和时间上限；H8-12 由唯一 Local Artifact Store 在初始化和写入前执行到期清理、最小磁盘余量治理与精确引用保护；
- 数据迁移必须有 schema version、备份或可回滚策略；
- 测试使用临时目录，不能读写真实 App 数据。

## 8. 测试归属

| 测试 | 位置 |
| --- | --- |
| Python 领域和组件单元测试 | `backend/tests/unit/` |
| PostgreSQL、文件、FastAPI 集成测试 | `backend/tests/integration/` |
| REST、事件与跨语言协议 | `backend/tests/contract/`、`contracts/fixtures/` |
| 平台 Adapter 和页面样例 | `backend/tests/rpa/` |
| React 单元/组件测试 | 与 `frontend/src/` 被测文件就近 |
| 测试专用 UI Harness E2E | `frontend/e2e/` |
| Rust 原生单元/集成测试 | `frontend/src-tauri/` 与 `tests/` |
| 真实 Tauri E2E | `frontend/e2e-tauri/` |
| 真实社交平台验收记录 | 对应实施条目和本地受控证据，不提交敏感原件 |

根目录不再创建混合 `tests/`。

## 9. 包管理与生成

- Frontend：pnpm，提交 `pnpm-lock.yaml`；
- Backend：uv，提交 `uv.lock`；
- Rust：Cargo，提交 `Cargo.lock`；
- Local Executor：PyInstaller 按目标操作系统分别构建；
- Control Plane：Docker 构建，开发与 Demo 使用同一 Python 包和迁移；
- 禁止用 npm/yarn/pip requirements 与上述锁文件并存形成第二套来源；
- 生成文件必须通过脚本可重复生成，CI 检查是否漂移。

## 10. 从 agent-platform 复用边界

### 10.1 审计基线和判定方式

R0-12 审计基于旧仓库 `/Users/aventador/code/agent-platform` 的提交
`a01cfc9aa93e87e71b78b73eee3e07a3b9d31061`。只把旧代码视为经过验证的实现样本，不把它作为当前仓库的依赖、Git 子模块或运行时来源。

迁移判定统一为：

- **提取迁移**：保留失败语义和核心算法，在当前仓库先写失败测试后提取；
- **按新契约重写**：旧实现解决的问题仍存在，但公开类型、协议或存储方式已经变化；
- **删除**：与当前架构冲突，不得进入新仓库；
- **延后**：不属于 MVP，只有路线图任务进入时才能重新评估。

旧模块现有 Rust 测试结果为 35 项通过：`browser_session` 9 项、`local_executor` 14 项、`sidecar_security` 12 项。测试通过只证明旧仓库样本可参考，不替代新仓库的 RED/GREEN、macOS/Windows 和正式安装包验收。

### 10.2 `local_executor.rs` 逐项清单

#### 10.2.1 来源文件覆盖表

本轮 E4-01 在旧提交 `a01cfc9aa93e87e71b78b73eee3e07a3b9d31061` 上逐文件只读审计。下表中的“迁移”只表示保留可验证的失败语义；旧仓库不会成为当前项目的构建、运行或协议依赖。

| 旧仓库来源 | 已核对事实 | 当前判定与落点 |
| --- | --- | --- |
| `frontend/src-tauri/src/local_executor.rs` | `LocalExecutorManager` 同时承担启动互斥、stdin token、同步 stdio 调用、watchdog、重启、stderr 和跨平台进程树终止 | 拆分后重写：bootstrap 到 `E4-02`，随机认证到 `E4-06`，生命周期到 `E4-07`，监管到 `E4-08`，进程树到 `E4-09`，诊断到 `E4-10` |
| `frontend/src-tauri/tests/local_executor.rs` | 14 项旧测试实跑全绿，覆盖认证、协议拒绝、并发启动、两次崩溃恢复、调用超时、停止抢占、进程树、stderr 限界/脱敏和路径替换 | 将行为拆到对应 E4 任务重新 RED/GREEN；旧测试不能复制后直接算当前实现通过 |
| `frontend/src-tauri/src/main.rs` | 用 `current_exe` 参数 `--social-operations-sidecar` 把 App 自身分叉成假 Sidecar | 删除该入口；`E4-02` 使用独立 Python 入口，`E4-03` 生成 PyInstaller `onedir` |
| `frontend/src-tauri/src/lib.rs` | 全局管理旧 Manager 与 `SocialOperationsRuntime`，并注册面向 capability 的宽 Tauri Commands | 删除聚合运行时和通用命令；`E4-07` 只暴露固定 allowlist 的生命周期操作，`E4-13` 经 `PlatformAdapter` 使用 |
| `frontend/src/platform/tauri.ts` | React 发送 `capabilityId: social-operations` 并调用 `local_executor_*` 原始命令 | 不迁移；页面不能选择 capability、进程、路径或任意 payload，只能调用当前项目固定适配器方法 |
| `frontend/src-tauri/src/social_operations_runtime.rs` | 把账号内存表、浏览器 Profile、Cookie vault、Sidecar 安装和 Executor 聚成一个对象；旧定向测试本轮 7/8 通过，闭环用例在 `invoke` 返回 `ExecutorUnavailable` | `SocialOperationsRuntime` 整体删除；E4 只管进程，Profile/平台登录归 Wave 5，平台动作归 Wave 6/7，不能为了让废弃实现全绿而修改旧仓库 |
| `backend/src/agent_platform/capabilities/social_operations/local_executor_protocol.py` | 旧 `task.request/cancel/response/error`、`step.progress`、`handoff.requested`、`diagnostic.event` 协议携带 tenant、capability、设备和 Core 治理引用 | 不做兼容 Adapter；当前 I2-10～I2-13 Executor v1 是唯一 wire 契约，E4-02 直接消费当前 fixtures |
| `backend/src/agent_platform/capabilities/social_operations/device_account_service.py` | `ActorContext`、tenant/owner、RBAC、Entitlement、Core audit 与设备/账号/任务仓储耦合 | 全部删除，不迁入 Executor；当前 MVP 用 Installation 强 ID 和既有 Control Plane Task/Attempt/Action 领域 |
| `contracts/capabilities/social-operations/local-executor-v1.schema.json` | 旧生成 Schema 固化 `tenant_id`、`approval_id`、`audit_correlation_id`、Core Artifact 引用和任意业务扩展；两项协议 suite 本轮 148 项通过 | 只说明旧协议内部自洽，不说明与当前产品兼容；不得复制 Schema、fixtures 或 Markdown 到当前 `contracts/executor/` |

#### 10.2.2 能力迁移矩阵

| 旧能力 | 决策 | 当前项目落点 | 迁移要求 |
| --- | --- | --- | --- |
| Unix process group、Windows Job Object | 提取迁移 | `E4-09` | 保留整棵进程树终止、重复停止幂等和挂起调用清理测试；Windows 必须在真实打包产物复验 |
| 生命周期互斥、单实例 Supervisor | 按新契约重写 | `E4-07` | 保留并发启动线性化；改名 `ExecutorManager`，不暴露任意子进程能力 |
| 后台退出检测和最多两次重启 | 提取迁移 | `E4-08` | 重启预算进入显式配置；正常停止、版本不兼容、认证失败和风控停止都不得自动重启 |
| 调用超时并终止不确定进程 | 提取迁移 | `E4-07`、`E4-09` | 使用 Executor v1 类型化消息；外部副作用超时必须进入 `OUTCOME_UNCERTAIN`，不能当普通失败重试 |
| stderr 独立排空、脱敏和内存限界 | 提取迁移 | `E4-10` | 保留单行、行数、总字节上限；补设备凭据、平台 Cookie、URL 查询串和本机路径样例 |
| 256-bit stdin 会话令牌、常量时间比较 | 提取迁移 | `E4-06` | 令牌只能经 stdin bootstrap 传递，不进入 argv、env、日志、错误或响应 |
| 逐行 JSON stdio request/response | 按新契约重写 | `I2-10`～`I2-13`、`E4-02` | stdin 只做本机 bootstrap；任务、命令和事件使用带版本、ID、deadline、幂等键和序号的协议，Executor 主动连接 Control Plane |
| `serde_json::Value` 任意请求 | 删除 | — | 必须换成三语言共享 fixtures 验证的判别联合，未知字段 fail closed |
| `current_exe + --social-operations-sidecar` 自身分叉 | 删除 | — | 当前 Executor 是独立 Python/PyInstaller `onedir` 发布单元，由 Tauri 解析受信资源路径 |
| 硬编码 `CAPABILITY_ID` 和通用 Tauri invoke | 删除 | — | React 只能调用 allowlist 后的 `PlatformAdapter`/ControlPlaneTransport 操作 |
| `run_sidecar_io` 固定 ACK 假执行器 | 删除 | — | 无副作用联调统一使用 `T3-10 FakeExecutor`；不得混入正式 Executor |
| 已验证字节再执行的 TOCTOU 防护思路 | 按新契约重写 | `E4-05`、`E4-07` | 适配 PyInstaller 目录包和平台签名；不能只验证入口单文件后信任其余目录 |

#### 10.2.3 强制删除的 tenant/Core 依赖图

```text
旧 React capabilityId / serde_json::Value
        ↓
旧 Tauri Commands / SocialOperationsRuntime
        ↓
旧 Local Executor stdio 协议
        ↓
ActorContext + tenant/RBAC/Entitlement + Core Approval/Audit/Artifact
```

这条链不能通过兼容层保留。字段和替代边界固定如下：

| 旧依赖 | 处理 | 当前唯一边界 |
| --- | --- | --- |
| `tenant_id`、owner、企业与 RBAC | 删除 | 第一期没有产品账号或租户；资源以强类型 `installation_id` 归属，不能引入隐式默认 tenant |
| `approval_id`、`audit_correlation_id`、Core Approval/Audit | 删除 | MVP 的确认、人工接管、事件和安全结果使用当前 Task/Attempt/Action/Event 明确事实，不伪造旧治理资源 |
| Core Artifact 引用和 `artifact_refs` | 删除旧模型 | H8-09 已按当前 UUIDv4、SHA-256、媒体类型、大小、受控相对路径和权限实现本机引用；Executor/Control Plane 仍不接收旧 Core 对象 |
| `capability_id=social-operations`、`target_device_id` | 删除 | 当前连接身份是 Installation/Executor，任务类型由当前判别联合决定；React 不选择设备或 capability |
| 任意 `extensions`、任意 input/result 和 Rust `serde_json::Value` | 删除 | 只接受 Pydantic 生成并由 Python/TypeScript/Rust 共享 fixtures 验证的封闭类型，未知字段 fail closed |
| 同步逐行 stdio 任务通道 | 删除 | stdin 只在 `E4-02`/`E4-06` 传一次性 bootstrap；正式命令、ACK 和事件走当前出站 WebSocket |
| 旧账号/设备服务与 `SocialOperationsRuntime` | 删除 | Executor 进程监管、Profile/登录、平台 Adapter 和 Control Plane 领域分别归属，不再由一个内存聚合对象掌权 |

迁移执行顺序是 `E4-02` 入口与 bootstrap → `E4-06` 会话认证 → `E4-07` 生命周期 → `E4-08` 监管 → `E4-09` 进程树 → `E4-10` 诊断。`E4-11` 新建的是当前 Executor 本机幂等账本，不从旧 `device_account_service.py` 搬运 tenant、账号或 Core 数据模型。

### 10.3 `sidecar_package.rs` 逐项清单

| 旧能力 | 决策 | 当前项目落点 | 迁移要求 |
| --- | --- | --- | --- |
| Ed25519 manifest 签名、SHA-256、大小、平台和架构绑定 | 提取迁移 | `E4-04`、`E4-05` | Manifest 增加协议版本、构建 ID、入口和目录文件清单；任何未知/缺失字段拒绝 |
| 防版本回退 | 提取迁移 | `E4-05` | 使用明确的版本解析规则；已安装版本和 App 允许范围都参与判断 |
| 私有目录、拒绝 symlink、原子 staging/replace | 提取迁移 | `E4-05` | 安装根必须来自 Tauri app data/resource；路径不能由 React 或服务端任意指定 |
| 打开文件后的稳定 identity 检查 | 提取迁移 | `E4-05` | 覆盖验证后替换、目录成员替换和入口被调包；macOS/Windows 分别验证 |
| 凭据、Cookie、URL 和私有路径脱敏规则 | 提取迁移 | `E4-10` | 与 Python Executor 使用同一脱敏 fixtures，避免两端结论漂移 |
| `CrashRecoveryPolicy` | 移位迁移 | `E4-08` | 归入 Executor 监管模块，不留在包验证模块 |
| Executor Sidecar 自行 HTTPS 下载、redirect allowlist、在线安装 | 删除 | — | Executor 仍随 App 安装包交付；通用 App 更新已由 H8-18～H8-22 的受控 feed、官方 updater、签名缓存和原生安装边界负责，Sidecar 不建立第二套更新器 |
| `reqwest::blocking` 下载器和远程 URL 输入 | 删除 | — | Tauri 不接受 React/Control Plane 下发的任意下载 URL |
| 单文件名 `social-operations-sidecar` | 删除 | — | 改为平台相关 PyInstaller `onedir` manifest，不沿用旧产品命名 |
| 自定义 `x.y.z` 字符串比较 | 按新契约重写 | `E4-05` | 用受维护的语义版本库或严格内部版本类型，并覆盖预发布/非法版本 |

### 10.4 `browser_session.rs` 逐项清单

本轮 B5-01 继续使用 R0-12 固定的旧提交
`a01cfc9aa93e87e71b78b73eee3e07a3b9d31061`，重新核对完整模块、直接测试、聚合运行时和服务端账号契约。结论只冻结迁移语义与后续任务归属，不把旧仓库加入当前构建，也不提前实现 B5-02 之后的浏览器能力。

#### 10.4.1 B5-01 来源文件与测试证据

| 旧仓库来源 | 已核对事实 | 当前判定 |
| --- | --- | --- |
| `frontend/src-tauri/src/browser_session.rs` | 556 行；Profile 在 60～99 行，独立 Cookie Vault 在 101～324 行，目录/权限防护在 326～452 行，`QrLoginSession` 在 454～556 行 | 只保留私有目录、规范身份、熔断/人工恢复和定向清理意图；公开类型、路径、状态与文件操作全部按当前契约重写 |
| `frontend/src-tauri/tests/browser_session.rs` | 9 项旧测试覆盖路径逃逸、祖先 symlink、Unix 私有目录、Cookie 密文/AAD/重开、状态转换与 Profile 定向删除；R0-12 已在固定提交实跑 9 项通过 | 测试是行为样本，不复制为当前 GREEN；没有覆盖目录创建/删除竞态、Windows reparse point、Profile 锁、浏览器仍持有目录、停止失败或真实页面状态 |
| `frontend/src-tauri/src/social_operations_runtime.rs` | 58～71 行把 Profile、登录状态、Cookie、Sidecar 和进程内 `HashMap`/`active_account` 聚合；294～323 行实现注销 | `SocialOperationsRuntime` 整体删除；Profile、锁、浏览器进程、Session 检测、任务门禁与注销协调分别归属，禁止恢复成新的万能 Runtime |
| `backend/src/agent_platform/capabilities/social_operations/device_account_service.py` | `ActorContext`/`PlatformAccount` 绑定 `tenant_id`、`owner_user_id`、设备、权限和账号；注销依赖 `social.manage`、内存账号表与 Core audit | 旧产品账号/RBAC 服务删除；P9 仍以 Installation/Executor/Task 为权威，U9 产品账号与设备归属必须按新契约独立建设，不能复用该内存服务 |
| `contracts/capabilities/social-operations/device-account-v1.md` | 契约要求 tenant/owner、`social.read/execute/manage`、五平台枚举、Entitlement/Core Audit，并规定独立 Cookie 密文文件 | 不兼容、不复制；P9 本地 MVP 无产品账号，U9 只新增独立产品账号/Session/Installation 归属，仍不引入旧 tenant/RBAC/Entitlement 或云端平台 Cookie |

旧模块 9 项通过只能说明样本内部自洽。当前三个 Manifest 均不依赖旧仓库、`social-operations` 包或 `chacha20poly1305` Cookie Vault；后续每项仍须在当前仓库重新 RED/GREEN，并从真实 App/Executor/外部浏览器原入口验收。

原能力迁移矩阵固定如下：

| 旧能力 | 决策 | 当前项目落点 | 迁移要求 |
| --- | --- | --- | --- |
| App-owned `platform/UUID` Profile 目录 | 提取迁移 | `B5-02`、`B5-03`、`B5-05` | 根目录改为当前 App 标识；浏览器发现不接收任意路径，只创建路线图已启用的平台 |
| UUID 规范化和路径逃逸拒绝 | 提取迁移 | `B5-05` | 使用 `profile_id`，不能用昵称、手机号或平台账号作为目录名 |
| 祖先 symlink 防护和私有权限 | 提取迁移 | `B5-05` | 补目录竞争、稳定 identity、权限修复失败和 Windows reparse point 测试 |
| Profile 单实例与浏览器资源所有权 | 新建 | `B5-06`、`B5-07` | 旧模块没有锁和真实浏览器进程；必须先持锁再启动 headed persistent context，并由确定的运行实例释放 |
| Profile 定向删除 | 按新契约重写 | `B5-14` | 先阻止新任务、停止关联运行并释放 `B5-06` 锁，再只删除目标 Profile；失败可诊断、可重试 |
| `QrLoginSession` 状态机、revision 和 circuit open | 按新契约重写 | `B5-08`、`B5-09`、`B5-10`、`B5-11`、`B5-12` | 状态来自真实页面检测而非调用方信号；风险、验证码、过期和未知状态必须打开熔断并人工恢复 |
| 进程内 `HashMap` 账号和单 active account | 删除 | — | 账号/Profile 事实不能只在 Tauri 内存；并发由 Profile 锁和任务账本决定 |
| `EncryptedCookieVault` 和 Cookie 导入导出 | 删除 | — | Playwright 持久 Profile 是会话唯一来源；Cookie 不复制到独立文件、不上传 Control Plane |
| 普通文件 `.cookie-key` | 删除 | — | 不再单独保存 Cookie；设备密钥只允许进入 Rust 管理的 `app_data_dir` 固定私有文件，不能形成第二份配置或导出文件 |
| ChaCha20Poly1305 Cookie 文件格式 `SOC1` | 删除 | — | 与当前持久 Profile 方案重复，避免形成第二份登录态 |
| Douyin/Xiaohongshu/Kuaishou/Wechat 等一次性枚举 | 按阶段重写 | `B5-08` 及后续平台任务 | MVP 只实现抖音 Adapter；新增平台必须有独立页面对象、契约和真实验收 |

#### 10.4.2 当前 Profile 与私有目录契约

当前唯一计划路径为：

```text
Tauri app_data_dir/
└── browser-profiles/
    └── douyin/
        └── <canonical UUIDv4 profile_id>/
```

- `app_data_dir` 只能由 Tauri Rust 组合根解析；React、Control Plane、任务 payload 和用户输入都不能提交根目录、Profile 路径或可执行文件路径。
- `profile_id` 是本机生成并持久的 canonical UUIDv4，只表示运营 Profile，即使 U9 增加产品账号也不是产品 `account_id`，不能由昵称、手机号、抖音号或目录片段派生。MVP 不预建小红书、快手或微信目录。
- `B5-05` 已逐级拒绝 symlink/非目录，macOS/Unix 固定目录 `0700`；Windows 使用 handle-relative 创建、拒绝 reparse point 并应用当前用户 protected DACL。创建、打开和交给后续消费者前后都校验稳定 identity；B5-14 删除仍须沿用句柄/identity 语义，不能退回旧实现的 `metadata → remove_dir_all` 竞态窗口。
- `B5-06` 在任何 persistent context 启动前取得跨进程 Profile 单实例锁；`B5-07` 让具体浏览器运行实例拥有 context、进程和锁。App/Executor 崩溃恢复不能把仍被浏览器占用的 Profile 当成空闲。
- `B5-02`/`B5-03` 只发现签名/产品 allowlist 内的系统 Chrome/Edge，`B5-07` 始终传独立 Profile；任何代码都不得读取、复制或迁移用户默认 Chrome/Edge `User Data`。
- Profile 内由浏览器自行管理 Cookie、Local Storage 和站点数据；应用没有 Cookie 导入、导出、查看、上传或第二份加密文件 API。日志、事件、诊断和 Control Plane 数据都不能包含 Profile 绝对路径或内容。

#### 10.4.3 当前 Session 状态契约

旧 `LoginSignal` 是调用方可直接注入的内存信号，不能作为真实状态来源。当前 `B5-08`、`B5-09`、`B5-10`、`B5-11`、`B5-12` 必须由抖音页面对象观察得到封闭 Session 健康：

| 当前状态 | 事实与门禁 |
| --- | --- |
| `missing` | 未发现可用登录；允许进入本地 `awaiting_scan/awaiting_confirmation` 登录流程，但禁止业务副作用 |
| `healthy` | 真实页面确认已登录且没有风险/验证码；唯一令 `circuit_open=false` 的状态 |
| `expired` | 页面明确显示登录过期或重新登录；`circuit_open=true`，停止新副作用并请求重新扫码 |
| `risk` | 验证码、滑块、风控或权限异常；`circuit_open=true`，只能由用户在可见外部浏览器处理后显式重新检查 |
| `unknown` | 页面版本、网络或证据不足，不能猜测健康；`circuit_open=true` 并保存最小脱敏诊断 |

`circuit_open` 是由上述状态派生的安全门禁，不提供独立“强制关闭”接口。`session_revision` 是本机持久单调正整数：新 Profile 建立初始 epoch，注销、重新登录、风险/过期后的显式恢复都创建新 revision；旧 revision 的任务、页面观察或健康上报不能重新打开执行。`B5-12` 向 Control Plane 只上报平台、状态、`session_revision` 和观察时间，不上传 Cookie、Profile 路径、二维码、验证码或页面原文。

`B5-10`/`B5-11` 的二维码等待和人工接管是本地工作流，不是产品登录或账号体系。任何风险/过期/未知都必须保留旧实现“人工恢复前不能被 authenticated 绕过”的语义。如果外部动作已经发出但在最终页面确认前观察到会话异常，后续动作必须停止，Attempt 进入 `OUTCOME_UNCERTAIN`，不能自动重试造成重复副作用。

#### 10.4.4 安全注销时序

旧 `SocialOperationsRuntime::logout_account` 不可提取：它先在内存改状态，却会在检查 `stop_result` 前立即计算 Cookie/Profile 删除结果，因此即使停止执行失败也可能已经删除 Profile；旧测试也没有覆盖该故障。`BrowserProfile::remove` 的路径检查与 `remove_dir_all` 之间同样存在替换窗口。B5-14 已按以下持久、可恢复时序重写：

1. 以当前 Installation 和 `douyin` 原子创建或复用服务端 logout gate，revision 为当前投影 +1；门闩立即阻止新 Task 与新 offer；
2. 通过唯一 Manager 紧停 Executor，先关闭 flow、headed persistent context 和完整浏览器进程树并释放 `B5-06` lease；停止失败时保持门闩和 Profile，不进入删除；
3. 从 Rust 内部持有的平台/current marker/Profile 稳定句柄解析目标，拒绝 symlink、reparse point、非目录、identity 变化、活跃锁和原目录+tombstone 冲突；
4. 将唯一目标原子改名为 `.removing-<profile_id>`、重新打开复验同一 identity 后删除；重试可续删 tombstone，不存在时幂等成功；
5. 只清除 current marker 和目标 Profile，保留平台父目录、其他 Profile、Executor SQLite、设备凭据、Artifact 与设置；
6. 重启 signed Executor 并发送不含路径/headless 的 `douyin.logout.complete`，持久递增本机 `session_revision`、经正式 WebSocket 投影 `missing`，再从服务端查询确认。删除或上报失败保持持久门闩，不能伪报已注销；只有更高 revision 的真实 `healthy` 才恢复新任务。

注销原始入口已由 B5-14 从真实平台状态页启用：确认后依次经过固定 Gateway/Tauri Command、Control Plane 持久门闩、唯一 Executor Manager 停机、Profile 锁释放、稳定句柄定向删除、path-free Executor 命令和权威 `missing` 查询。隐藏 App 验收还从生产 Task 创建入口证明门闩确实拒绝新任务，并检查 current Profile/删除 tombstone 不存在而 Executor SQLite 保留；没有用直接目录函数、Mock 页面或直接 HTTP 冒充通过。

#### 10.4.5 强制删除的账号、RBAC 与 Cookie 边界

| 旧构造 | 处理 | 当前唯一替代 |
| --- | --- | --- |
| `HashMap<String, ManagedAccount>`、`active_account` | 删除 | Profile 锁决定本机互斥；Task/Attempt/Action 与 Executor 账本保存运行事实，不能由 Tauri 内存账号表决定 |
| `SocialOperationsRuntime` 聚合对象与通用账号 invoke | 删除 | 浏览器发现、Profile store、process/session detector、平台 Adapter 和 logout coordinator 各自窄接口；React 无任意 payload/路径命令 |
| `EncryptedCookieVault`、`.cookie-key`、`SOC1`、Cookie store/load/logout | 删除 | 浏览器持久 Profile 是登录态唯一来源；没有可调用的 Cookie API，也不新增 `chacha20poly1305` 依赖 |
| `ActorContext.tenant_id/user_id/permissions`、`owner_user_id`、`social.read/execute/manage` RBAC | 删除 | 不复用旧 Actor/RBAC；P9 使用 Installation，U9 另建最小产品 User/Session 和 Installation 归属，Executor 仍使用独立 Session，平台登录只表示本机 Profile 的抖音状态 |
| DeviceAccountService 的五平台账号、设备 owner、Entitlement 与 Core Audit | 删除 | P9 只做抖音 Session 健康；U9 的产品账号设备归属不等于平台账号 owner，不上传平台凭据，也不恢复旧五平台/Entitlement 服务 |
| 云端平台 Cookie/账号密文、导入导出或跨设备同步 | 禁止 | 平台秘密永不离开 App 私有 Profile；未来即使增加产品账号，也不能静默扩大这一隐私边界 |

“产品账号”和“抖音平台登录”始终是两个独立事实：P9 用户不登录 automation-tool，U9 客户 Demo 用户先登录产品账号；两种阶段首次使用抖音仍要在 App 管理的可见外部浏览器完成平台登录。B5 任务不得为了复用旧代码引入产品账号或云端 Cookie；产品账号只由 U9 按新契约建设，首版不引入旧 tenant、RBAC、Entitlement。

### 10.5 跨模块保留与明确排除

继续保留并在对应任务重新实现：

- `PlatformAdapter` 的业务隔离规则；
- UI Harness、Rust 集成、Tauri E2E 和正式包审计四层测试体系；
- 版本、幂等、截止时间、事件序号、人工接管、资源清理和脱敏原则。

明确不迁移：

- 产品注册登录、企业、租户、RBAC 和 Entitlement；
- 旧项目的多租户设备注册、远程调度和 `SocialOperationsRuntime` 聚合实现；
- LangGraph、Deep Agents、RAGFlow、LiteLLM 和 AI 中台 Core；
- 旧项目绑定的腾讯云/阿里云部署基线；
- 依赖旧租户、审批或审计模型的业务 API。

实施顺序固定为：先在新仓库建立公开契约和失败测试，再提取最小实现，随后删除旧产品命名与依赖，最后通过当前项目的 macOS/Windows、真实 Control Plane 和正式安装包门禁。禁止直接复制三个旧源文件后再修改。

## 11. 禁止事项

- 禁止增加用户可访问的 Web 产品、Web 部署或第二套页面；
- 禁止把 Python 业务代码放进 `src-tauri/`，或把 Rust 原生实现放进 React Feature；
- 禁止前端手写一套与 Pydantic 不一致的 DTO；
- 禁止页面直接调用 Local Executor 任意端口、任意 URL 或任意命令；
- 禁止 Control Plane 直接操作浏览器、微信、用户文件或操作系统 UI；
- 禁止云端 Demo 暴露匿名写接口，未做用户登录时仍必须校验安装实例/设备凭据；
- 禁止把浏览器选择器散落到 API、任务服务或 React 页面；
- 禁止把 Cookie、数据库、截图、聊天、素材和运行日志提交 Git；
- 禁止为小红书、微信或未来 AI 预建没有用户闭环的空目录和空菜单；
- 禁止为了单仓库强制 TypeScript、Rust 和 Python 共用依赖管理器。
