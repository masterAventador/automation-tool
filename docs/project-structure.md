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
│   └── fixtures/
│       ├── executor-v1/           # Python/Rust/TypeScript 共用 valid/invalid wire 样例
│       ├── executor-package-v1/   # Python 生成、Rust 复验的 inert 签名目录样例
│       └── executor-diagnostics-v1.json # Python/Rust 共用脱敏输入与安全结果
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
│   │   │   ├── task-creation-gateway.ts   # 固定抖音任务定义创建 Command
│   │   │   ├── task-projection-source.ts  # 固定 Task 快照/列表/Channel source
│   │   │   ├── task-run-control-gateway.ts # 固定暂停/恢复/取消/紧停 Command
│   │   │   ├── platform-adapter.ts         # 固定 Executor 状态/重启/诊断/本机紧停 Command
│   │   │   ├── platform-session-gateway.ts # 固定抖音状态查询/打开处理/重新检查 Command
│   │   │   └── workbench-gateway.ts       # 固定运行状态与全局紧停 gateway
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
│   ├── tauri.executor-lifecycle-e2e.conf.json # 后台隐藏的 signed Executor 生命周期验收
│   ├── tauri.platform-session-e2e.conf.json # 后台隐藏的平台状态与无头浏览器验收
│   ├── tauri.platform-session-reuse-e2e.conf.json # 后台隐藏的登录复用/失效接管验收
│   ├── tauri.default-profile-isolation-e2e.conf.json # 后台隐藏的默认 Profile 隔离验收
│   └── tauri.workbench-e2e.conf.json # 后台隐藏的工作台真实紧停验收
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
├── wdio.task-run.conf.ts
├── wdio.task-lifecycle.conf.ts
├── wdio.task-restart.conf.ts
├── wdio.executor-lifecycle.conf.ts
├── wdio.platform-session.conf.ts
├── wdio.platform-session-reuse.conf.ts
├── wdio.default-profile-isolation.conf.ts
├── wdio.workbench.conf.ts
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
│       ├── control_plane/         # 独立部署的 FastAPI 业务后端
│       │   ├── bootstrap/         # 配置、注册、设备凭据和 Session 依赖装配
│       │   ├── api/               # REST、设备认证、SSE/WebSocket 和错误映射
│       │   ├── application/       # 注册、凭据、任务、配置、内容和工作流用例
│       │   ├── domain/            # 稳定 ID、Task 执行状态、版本事件、快照与 Command 契约
│       │   └── infrastructure/
│       │       ├── database/      # PostgreSQL 注册认证与 Task/Attempt/Action/Event/Command 持久化
│       │       ├── security/      # Bootstrap 签名验证等密码学适配
│       │       ├── events/
│       │       ├── object_storage/
│       │       └── observability/
│       ├── executor/              # 永远运行在用户电脑的执行器
│       │   ├── __main__.py        # 源码模式与 PyInstaller 共用的模块入口
│       │   ├── authentication.py  # 本机启动令牌校验、可清零 HMAC 事件证明
│       │   ├── bootstrap.py       # 一次性 stdin bootstrap、端点/Session/身份严格校验
│       │   ├── browser_runtime.py # 单 context、页面/窗口、超时和清理的 Playwright BrowserRuntime
│       │   ├── cli.py             # automation-tool-executor 正式控制台入口与信号映射
│       │   ├── diagnostics.py     # 与 Rust 共用 fixtures 的 fail-closed 文本脱敏
│       │   ├── ledger.py          # 本机 SQLite v2 命令/checkpoint/outbox/平台 Session 账本
│       │   ├── platform_commands.py # 认证本机平台命令、扫码 flow 与健康队列
│       │   ├── package_manifest.py # onedir 完整清单、目录摘要和离线 Ed25519 签发工具
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

D6-06 的 `protocol/douyin_candidate.py` 是 Candidate 值、最小摘要、来源和稳定键算法的唯一 Python 来源，`protocol/__init__.py` 提供公共导入。它不依赖 Playwright、RPA、Control Plane、SQLAlchemy 或 App；`frontend/tests/douyin-candidate-model-boundary.test.mjs` 锁定无头像/简介/联系方式/页面原文/URL 字段，并证明 D6-05 滚动层不解析候选、Executor wire/Tauri 尚未提前暴露 Candidate。后续页面提取只能构造该对象，不能扩展自由字典。

D6-07 的 `executor/rpa/douyin/candidate_extraction.py` 只编排页面状态、封闭 evidence 与 D6-06 Candidate tuple，不含 selector、任意 locator、URL 字面量、HTTP/Control Plane 或存储访问。受控作者/名称 selector、有限字段读取及官方作者 href→目标 ID 缩减全部保留在唯一 `search_page.py`；源 href、query、页面正文和其他 DOM 属性不能越过 Page Object。`frontend/tests/douyin-candidate-privacy-boundary.test.mjs` 锁定这条依赖方向；`backend/tests/integration/test_douyin_candidate_extraction_browser.py` 从 D6-04 公开搜索入口使用一次性 Profile 和无头系统 Chrome 验证真实 Playwright 原调用路径，并在退出后关闭完整 Runtime。

D6-08 的 `control_plane/domain/douyin_candidate_policy.py` 只消费公共 Candidate/key 和 UTC 时间，提供固定 30 天历史窗口及四种封闭 disposition，不导入仓储、SQLAlchemy、Executor/RPA、HTTP 或 App。`control_plane/domain/__init__.py` 是 Control Plane 公共领域入口；`frontend/tests/douyin-candidate-policy-boundary.test.mjs` 锁定策略只使用 `dedupe_key`、不读取候选个人字段，且尚未提前进入 Executor Schema/Tauri。D6-09 的基础设施层只向该策略提供匹配当前 Installation/当前候选 key 的唯一最新历史事实，黑名单 key 由当前已认证用例传入；查询对象不能反向塞进领域模块。

D6-09 的 `control_plane/application/task_targets.py` 定义脱敏、不可变的 Target 持久记录，`infrastructure/database/task_target_repository.py` 是唯一 PostgreSQL 适配器，`schema.py` 与 Alembic `20260718_0016` 是同一 `task_targets` 结构的代码/迁移来源。适配器在 Installation/Task 行锁事务中查询当前 Candidate key 的同 Installation 历史、调用 D6-08、替换整批 Decision，并按 `(ordinal,id)` keyset 读取；应用/领域模块不导入 SQLAlchemy。`frontend/tests/task-target-database-boundary.test.mjs` 锁定表的复合归属、唯一顺序、历史索引和未提前进入 Executor Schema/Tauri 的边界；数据库原调用方测试使用独立 Compose project、随机 loopback 端口和真实 PostgreSQL，不启动 App 或浏览器。

E4-04 的 `package_manifest.py` 是唯一 Manifest 生成器和 `automation-tool-build-executor-manifest` CLI：发布私钥只接受 stdin 的 32 字节 seed；整个 `onedir` payload 以受限 ASCII 相对路径排序，逐文件记录大小/SHA-256，并以固定域、长度前缀、大小和原始摘要计算目录 SHA-256。canonical Manifest 原始字节由独立 `atems1` Ed25519 envelope 签名；`contracts/protocol/executor-package-manifest-v1.schema.json` 固化 exact fields，`contracts/fixtures/executor-package-v1/valid/` 用明确的测试 seed 提供 inert 跨语言验签样例。生成器拒绝 symlink、非普通文件、错误入口、平台/架构/版本/build ID、读取竞态和资源超限；Rust 可信读取、安装与防降级不在 Python 中伪造，继续由 E4-05 承接。

E4-05 的 `src-tauri/src/executor_package.rs` 直接消费同一 Manifest/`atems1` fixture，不复制 Python 签发逻辑。它以 `ed25519-dalek::verify_strict`、`sha2`、受维护的 `semver` 和 `walkdir` 完成验签、canonical/exact-field 解析、当前平台/架构/入口绑定、允许范围、已安装版本防降级和完整目录双枚举；每个 payload 由拒绝 symlink 的稳定文件句柄读取并核对 Unix dev/inode 或 Windows volume/file index。公开 Rust API 只返回验证后的版本/build/入口/统计和固定错误码，没有 Tauri Command、React、URL 或远程信任配置。E4-07 只能从 Rust 受信资源边界装配该 verifier 并在启动前使用，不能把路径、公钥或版本策略扩成 IPC。

E4-06 的 `src-tauri/src/executor_bootstrap.rs` 只提供 Rust 内部 `LocalSessionToken`、受限 bootstrap 输入和事件证明 verifier：系统 CSPRNG 每次产生 32 字节，写入 stdin 时临时编码为 64 位小写十六进制，Drop/临时缓冲区均清零；Control Plane Session 保持独立字段。Python `authentication.py` 从 `SecretStr` 派生可清零 HMAC key，stdout 只输出绑定固定域、事件名和协议版本的 `atlep1` 证明。Rust 以维护中的 `hmac` crate 常量时间校验；两端共享固定测试向量但不提交真实令牌。模块没有启动进程、命令行/环境传密、Tauri Command 或 React 面，E4-07 才把它与 E4-05 verifier 组合为 Manager。

E4-07 的 `src-tauri/src/executor_manager.rs` 组合 E4-05/E4-06，但不复制两者规则：Manager 每次 start 复验固定包根并只启动验证后的 Manifest 入口，stdin 一次写入 bootstrap，stdout 只消费认证 lifecycle 事件；一个 Mutex 线性化 start/status/stop，错误、超时与 Drop 回收直接子进程。它不含旧 stdio task invoke、`serde_json::Value`、capability、任意路径/URL/命令或 Tauri Command。`scripts/run_e4_07_acceptance.py` 使用真实 Manifest CLI、PyInstaller onedir、本地 Uvicorn/Session/Registry 和公开 Rust Manager 原入口证明 `registered → heartbeat → unregistered`；私有配置只以 `0600` 临时文件存在并删除。后台退出监管、进程树、stderr 诊断和桌面 PlatformAdapter 分别留给 E4-08～E4-10/E4-13。

E4-08 不新增 supervisor 文件或第二生命周期源，而是在同一 `executor_manager.rs` 中加入显式 `ExecutorRestartPolicy`、唯一命名后台线程和 running/restarting/stopped 内部状态机。只有 OS crash 能在预算内转入 pending restart；每次恢复仍复用 E4-05/E4-06 的原始公开边界重新验包和生成令牌。显式 stop 先移除 pending/running，正常或固定失败退出直接停止，Drop 先 join supervisor；公开状态仅增加 `restartCount`。测试用已签名真实进程和 OS SIGKILL 证明初次+两次恢复的硬事实，临时计数位于包外并由 RAII 删除；PyInstaller/Uvicorn 正式链路另行回归。完整进程树现已由 E4-09 接入，Windows 原生恢复与重启预算已于 2026-07-20 通过实包验收。

E4-09 仍不新建进程服务：`RunningExecutor` 直接拥有跨平台 `ProcessTree`。Unix 在 spawn 前建立独立 process group，Windows 在 suspended child 上先配置并挂入 kill-on-close Job Object 再恢复；所有 setup 失败、启动/停止超时、显式停止后的剩余后代、异常退出准备恢复和 Manager Drop 都复用同一树终止原语。Rust 测试让真实签名主进程生成忽略 `SIGTERM` 的孙进程并核对 PID 消失，未使用 Mock 或 shell 进程列表冒充；Windows 原生签名实包已于 2026-07-20 覆盖显式停止、挂起停止、启动超时、Manager Drop 和崩溃重启整树清理。

E4-10 新建的 `executor_diagnostics.rs` 只负责 stderr 安全文本，不承担生命周期或业务日志。Manager Core 持有唯一内存队列，各次启动的 reader 共享它；输入以固定容量流式消费，超长/非法编码 fail closed，再按 `contracts/fixtures/executor-diagnostics-v1.json` 规则脱敏并执行行数/单行/总字节上限。Python `executor/diagnostics.py` 回放同一 fixtures，为未来 Executor 自身安全消息提供一致策略，但 Rust 仍对原始 stderr 独立重做脱敏，不能信任进程内结论。真实 signed 进程测试证明公开 Manager `diagnostics()` 原入口，不新增 Tauri Command 或持久日志。

E4-11 的 `executor/ledger.py` 是正式 Local Executor 唯一本机 SQLite 入口，不复用 FakeExecutor 内存字典，也不导入 Control Plane 仓储。Rust `ExecutorLaunchConfiguration` 持有状态目录并经既有一次性 bootstrap 传递；Python CLI 在联网前创建固定 `executor-ledger.sqlite3` 和 v1 identity/commands/attempt checkpoints/outbox 四表。command 双键/指纹、Attempt 连续 sequence、checkpoint revision/CAS 和协议 outbox 重放均在该模块内事务化；测试覆盖真实并发、重开恢复、迁移、身份错绑、损坏、symlink/reparse/权限/文件 identity 竞态。

E4-12 的 `executor/command_processor.py` 是正式任务帧进入本机账本的唯一应用层；它不导入 FakeExecutor，不直接访问 Control Plane 仓储，也不执行平台副作用。当前只接受 `task.offer`，先持久 receipt，再用账本单事务提交 terminal checkpoint 与固定六消息 success outbox；`runtime.py` 在 Hello 后恢复并逐条发送 outbox，成功发送后才标 delivered。`scripts/run_e4_12_acceptance.py` 用真实 PostgreSQL/Uvicorn、正式 Device Session、signed PyInstaller 和公开 Rust Manager 两次启动同一状态目录，证明精确消息重放且服务端事实不重复。该账本没有 Tauri Command/React API；E4-13 只装配生命周期 Adapter，E4-14 已完成隐藏 App 验收。

E4-13 的 `src-tauri/src/executor_platform.rs` 是唯一 App 组合根：包、SQLite 状态和稳定 Executor UUID 都从 Tauri `app_data_dir/local-executor` 固定派生，不接受 WebView 路径；重启所需 Installation 和短期 `executor.connect` Session 只由 Rust Control Plane client 换取。`src/platform/tauri/platform-adapter.ts` 只 invoke 四个无参数生命周期 Command，并对状态和诊断 DTO fail closed；`features/diagnostics` 只消费该接口。账本、Session、PID、路径、包信任参数和原始 stderr 都不进入 React。

E4-14 的 `tauri.executor-lifecycle-e2e.conf.json`、`wdio.executor-lifecycle.conf.ts`、对应 spec 与 `scripts/run_e4_14_acceptance.py` 只服务于 `control-plane-e2e` 隐藏 App 验收。它们从真实诊断页驱动正式 PlatformAdapter，使用专属动态端口、Compose project、AppData 和 signed PyInstaller 包，实际注入 OS crash/hang 后验证 supervisor、进程树和私有 SQLite；测试 origin、故障与退出 Command 不进入默认构建。生产 `lib.rs` 的 event loop 在 `ExitRequested/Exit` 显式停止唯一 Executor，避免依赖测试驱动或析构时机回收。

E4-15 的 `build.rs` 是 release 验证公钥的打包前 fail-closed 门；`tauri.dev.conf.json` 是唯一含 1420/devCSP 的开发覆盖，正式 `tauri.conf.json` 不再携带开发地址。`frontend/scripts/audit-production-package.mjs` 检查真实 release 二进制、生产资产、正式配置与 `cargo tree --no-default-features`，而根 `scripts/run_e4_15_acceptance.py` 负责缺失/畸形公钥失败证明、唯一临时 target 构建和精确清理。验收公开公钥不用于发布签名，临时制品不启动、不上传、不保留。

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
├── device/
│   ├── installation.json
│   └── executor-state.db
├── browser-profiles/
│   └── douyin/<canonical-profile-uuid>/
├── artifacts/
│   ├── evidence/
│   └── exports/
├── logs/
├── diagnostics/
└── settings.json
```

规则：

- 目录和文件权限按当前用户最小化；
- 浏览器 Profile 不进入普通备份、日志或导出；
- Artifact、日志和诊断数据都有数量、大小和时间上限；
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
| Core Artifact 引用和 `artifact_refs` | 删除旧模型 | 本机 Artifact 等到 `H8-09` 按当前稳定 ID、摘要、相对路径和最小元数据实现；Executor/Control Plane 都不接收旧 Core 对象 |
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
| HTTPS 下载、redirect allowlist、在线安装 | 延后 | MVP 之后单独立项 | MVP 的 Executor 随 App 安装包交付；没有明确更新威胁模型前不做在线 Sidecar 更新器 |
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
| `backend/src/agent_platform/capabilities/social_operations/device_account_service.py` | `ActorContext`/`PlatformAccount` 绑定 `tenant_id`、`owner_user_id`、设备、权限和账号；注销依赖 `social.manage`、内存账号表与 Core audit | 旧产品账号/RBAC 服务删除；当前只有 Installation/Executor/Task 权威模型和非敏感平台 Session 健康投影 |
| `contracts/capabilities/social-operations/device-account-v1.md` | 契约要求 tenant/owner、`social.read/execute/manage`、五平台枚举、Entitlement/Core Audit，并规定独立 Cookie 密文文件 | 不兼容、不复制；第一期无产品账号/RBAC/Entitlement，MVP 只启用抖音，登录态只存在持久浏览器 Profile |

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
- `profile_id` 是本机生成并持久的 canonical UUIDv4，只表示运营 Profile，不是产品 `account_id`，也不能由昵称、手机号、抖音号或目录片段派生。MVP 不预建小红书、快手或微信目录。
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
| `ActorContext.tenant_id/user_id/permissions`、`owner_user_id`、`social.read/execute/manage` RBAC | 删除 | 第一期无产品账号；App 使用 Installation 身份，Executor 使用独立 Session 能力，平台登录只表示本机 Profile 的抖音状态 |
| DeviceAccountService 的五平台账号、设备 owner、Entitlement 与 Core Audit | 删除 | MVP 只做抖音 Session 健康；Control Plane 只接收当前 Installation-scoped 的非敏感状态/任务事实 |
| 云端平台 Cookie/账号密文、导入导出或跨设备同步 | 禁止 | 平台秘密永不离开 App 私有 Profile；未来即使增加产品账号，也不能静默扩大这一隐私边界 |

“无产品账号”和“需要抖音登录”是两个独立事实：用户打开 App 不登录 automation-tool，但首次使用抖音仍要在 App 管理的可见外部浏览器完成平台登录。后续 B5 任务不得为了复用旧代码重新引入注册页、账号中心、tenant、RBAC、Entitlement 或云端 Cookie。

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
