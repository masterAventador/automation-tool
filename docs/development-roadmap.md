# 自动化运营工具任务级开发路线图

> 文档性质：后续开发唯一执行台账
> 建立日期：2026-07-18
> 当前阶段：Wave 8 恢复、诊断与 MVP 质量收口（下一项 H8-12）
> 执行顺序：RPA 运营 > 内容生产与分发 > AI 员工与工作流

## 1. 如何使用本路线图

本文件同时承载里程碑、依赖、失败矩阵、完成定义和可独立开发、测试、审查、提交的小任务。以后开始任何产品代码任务前，必须：

1. 确认前置任务全部满足；
2. 把且只把一个任务标为 `🧪 RED` 或 `🚧 实现中`；
3. 先补能证明完成定义的失败测试；
4. 实现最小代码并跑完该任务门禁；
5. 更新实际验证命令与证据；
6. 标记 `✅ 已完成`并形成独立提交；
7. 再启动下一任务。

路线图中的“完成定义”是最小门禁，不替代 [`CLAUDE.md`](../CLAUDE.md) 的失败矩阵、真实平台验收和资源清理规则。

## 2. 状态

| 状态 | 含义 |
| --- | --- |
| `⬜ 未开始` | 前置未满足或尚未进入 RED |
| `🧪 RED` | 目标测试已写并按预期失败 |
| `🚧 实现中` | 正在完成最小实现 |
| `🔍 待验收` | 自动化已通过，等待真实平台/桌面/部署边界 |
| `⛔ 受阻` | 有明确证据和解除条件的外部阻塞 |
| `✅ 已完成` | 完成定义、测试、真实边界和文档更新全部通过 |

## 3. 当前进度快照

快照日期：2026-07-21。

| 范围 | 当前结果 |
| --- | --- |
| 竞品分析 | `✅ 已完成` 已完整阅读并转为能力地图；动态长期稳定性仍需我们自己的真实账号验证 |
| 产品决策 | `✅ 已完成` Tauri-only、无产品登录 UI、RPA 优先、外部浏览器 + 独立 Profile |
| 后端决策 | `✅ 已完成` 独立 FastAPI Control Plane；开发本机、Demo 云端；PostgreSQL 从第一天使用 |
| 本地执行决策 | `✅ 已完成` Python Local Executor 永远在用户电脑，随 Tauri 打包 |
| 项目规则 | `✅ 已完成` 已从 `agent-platform` 筛选、改写并写入仓库 |
| 产品/架构文档 | `✅ 已完成` 已建立产品、工程结构、前端和后端权威文档 |
| 任务级开发台账 | `✅ 已完成` 已建立里程碑、失败矩阵、完成定义、任务和实时状态 |
| 任务级路线图 | `✅ 已完成` 本文件已建立 |
| 产品代码 | `🚧` Wave 1～Wave 6 工程主线、A7-01～A7-15 与 H8-01～H8-11 已完成；D6-16、A7-16、A7-17 与 B5-15 的真实账号证据保持独立待补，下一项为 H8-12 |
| Windows 原生验收集成 | `✅ 已完成` `chore/windows-native-validation` 记录的 Windows x86_64 实体机 GREEN 已逐文件审查并与 D6-09 后的 `main` 冲突解析；该分支无 GitHub Actions/PR 运行记录，未把分支名称当验收证据。合并树在 macOS 补齐跨平台严格 Mypy 边界后，Backend `1275 passed, 5 skipped`，Frontend 84 项 Node/145 项 Vitest 及 Lint/Type/API/生产边界全绿，Rust 三套配置、Rustfmt 与全目标全特性 Clippy 全绿 |
| 稳定资源 ID | `✅ 已完成` installation/executor/task/execution attempt/action/artifact 六类规范 UUIDv4 值对象与非法值矩阵已验证 |
| 本地 PostgreSQL | `✅ 已完成` 18.4 开发/测试双容器、健康检查、loopback 端口和独立存储已验证 |
| 数据访问与迁移 | `✅ 已完成` SQLAlchemy asyncio/asyncpg、事务 session、Alembic 空库升级/回滚、Installation schema/约束和脱敏连接错误已验证 |
| Installation 持久化 | `✅ 已完成` 32 字节公钥、active/revoked、revision CAS、吊销时间、唯一性和时间一致性约束已在 PostgreSQL 18.4 验证 |
| Demo Bootstrap | `✅ 已完成` 最多 7 天、精确 Demo 环境、唯一 installation.register purpose 和业务 API 拒绝模型已验证 |
| Installation 注册 | `✅ 已完成` 离线签名 Bootstrap、最长 5 分钟的一次性 challenge、设备 Ed25519 证明和 PostgreSQL 原子消费已验证 |
| 设备凭据 | `✅ 已完成` `atdc1` 一次返回、摘要持久化、版本历史、最小 session scope、原子轮换/吊销和并发单赢家已验证 |
| 桌面 UI 资产 | `✅ 已完成` React 19、TypeScript 5.9、Vite 8、Ant Design 6 和 pnpm 冻结锁文件基线已验证 |
| Tauri 桌面壳 | `✅ 已完成` v2 真实 macOS 窗口、生产 CSP、零权限 Capability、Cargo 锁文件与桌面构建已验证 |
| 设备身份与凭据存储 | `✅ 已完成` Ed25519 首启生成、Rust 管理的 `app_data_dir` 私有文件、长期凭据替换/删除、React/IPC 零暴露和无系统钥匙串授权已验证 |
| 前端 Transport | `✅ 已完成` 生产 `main.tsx` 已经真实 Tauri IPC/Rust 桥调用 Health；Rust 固定 origin/operation allowlist、凭据注入与严格响应边界已验证，注册/凭据/Session 纵向链路不向 React 暴露秘密 |
| Executor v1 协议 | `✅ 已完成` 28 种消息三端判别解析、显式版本、用途隔离 UUIDv4、UTC 微秒 deadline、幂等键、安全整数序号、安全 payload、Draft 2020-12 Schema 与 37 个公共 fixtures 已验证 |
| Target 发现命令闭环 | `✅ 已完成` 隐藏 Tauri App 已经正式 Rust bridge 启动发现；Control Plane 原子创建 Attempt/Discover Command，Local Executor 经生产 Processor 上报有界 Candidate 批次并持久 Outbox，PostgreSQL 原子替换 Target、追加事件和收敛 Task；成功、登录失效、人工接管、失败、重试与精确重放矩阵已验证 |
| Executor WebSocket | `✅ 已完成` 真实 Uvicorn、精确子协议、Session/Installation/Executor/版本绑定、连接 ID、32 KiB 传输上限、周期重认证、吊销断连和旧 Session 拒绝已验证 |
| Executor Playwright onedir | `✅ 已完成` macOS arm64 与 Windows x86_64 正式 onedir 均已包含 Python Playwright driver 且无浏览器缓存；冻结生产 primitive 已用受信系统浏览器、私有 Profile 与原生锁启动 headed context |
| Executor signed Manifest | `✅ 已完成` onedir 全目录路径/大小/SHA-256、确定性目录摘要、版本/构建/平台/架构/入口和 exact-byte Ed25519 `atems1` 签名已由 Schema、跨语言 fixture、真实 CLI 与 macOS/Windows 冻结实包验证 |
| Rust Executor package verifier | `✅ 已完成` macOS arm64 与 Windows x86_64 均已从公开 Rust verifier 验证签名、完整目录、平台/架构、SemVer 范围、防降级和原生路径 identity |
| Executor stdin 认证 | `✅ 已完成` Rust 每次生成/清零 256-bit 本机令牌并只写 stdin；Python 输出域隔离 `atlep1` HMAC 事件证明，Rust 常量时间校验；与 Control Plane Session 用途隔离且无 argv/env/log/明文响应面 |
| Rust ExecutorManager | `✅ 已完成` macOS 与 Windows 均已从公开 Rust 生命周期入口完成 signed PyInstaller onedir、stdin 认证、真实 Uvicorn、监管/重启、整树清理和 App 纵向验收 |
| 正式桌面制品隔离 | `✅ 已完成` macOS 与 Windows release 实际二进制、正式资产/配置和无默认特性依赖树均确认无 WebDriver、验收 Command、测试 Sidecar/origin、开发公钥和调试端口；release 公钥打包前 fail closed |
| macOS 浏览器受信发现 | `🔍` Rust 生产 API 已用 Apple Security.framework 验证标准路径 Chrome 的签名、Bundle ID、Team ID、全架构/嵌套代码和路径 identity；Edge allowlist/失败矩阵已实现，本机未安装 Edge，保留真实 Edge 实机验收 |
| 运营浏览器选择 | `✅ 已完成` macOS 与 Windows 隐藏真实 App 均已从设置页保存、刷新并读回受信浏览器枚举；WebView/IPC/沙盒文件无可执行路径 |
| 私有浏览器 Profile | `✅ 已完成` macOS/Unix 与 Windows 均已从公开 Rust Store 完成 UUIDv4 Profile 原子创建、重开、私有权限/DACL、symlink/reparse、identity 替换与并发矩阵 |
| Profile 单实例锁 | `✅ 已完成` macOS/Unix 与 Windows 均已从公开 Rust Profile API 验证同 Profile 跨进程排他、不同 Profile 并行、显式释放、原生权限/链接及真实子进程崩溃恢复 |
| BrowserRuntime | `✅ 已完成` macOS 与 Windows 冻结生产模块均已用受信系统浏览器、私有 Profile 与原生锁验证单 context、双窗口正常关闭，并分别由 process group/正式 Manager Job Object 完成整树强杀 |
| 平台 Session 健康投影 | `✅ 已完成` 生产 detector→本机 SQLite v2 单调 epoch→认证 Executor WebSocket→PostgreSQL 六列最小投影已在后台无头系统 Chrome/真实网络边界验证 |
| Executor Connection Registry | `✅ 已完成` Installation 单活、服务端心跳投影、固定旧连接替换、stale 保护、受限 current send API 与进程退出清理已验证 |
| Installation 吊销闭环 | `✅ 已完成` 运维 CLI 原子吊销 Installation/凭据/Session；App 业务访问守卫、Executor 在线断连、未来任务 API 依赖门禁与隐藏 Tauri 吊销诊断已验证 |
| Task 状态机 | `✅ 已完成` 16 个状态、5 个无出边终态、取消确认/完成竞态与结果不确定来源已由 256 个状态对穷举验证 |
| Task 持久化 | `✅ 已完成` `tasks` migration、Installation scope、active 创建门禁、revision CAS、跨 scope 不可见和并发单赢家已在 PostgreSQL 18.4 验证 |
| Attempt/Action 持久化 | `✅ 已完成` current Attempt 复合绑定、单活 Attempt、重试/Action 序号唯一、阶段/结果一致性已在 PostgreSQL 18.4 验证 |
| Task Event 持久化 | `✅ 已完成` `1.0` 事件词汇、单调安全序号、来源去重、复合 scope、安全消息和快照水位已在 PostgreSQL 18.4 验证 |
| Command/Outbox 持久化 | `✅ 已完成` 命令/响应词汇、sequence/idempotency 去重、deadline/lease、投递与 ACK 严格分态已在 PostgreSQL 18.4 验证 |
| Command 投递闭环 | `✅ 已完成` PostgreSQL 原子抢占、current WebSocket 发送、断线/ACK 超时重投、重连恢复、严格回执与 deadline 过期已在真实网络验证 |
| 创建 Task API | `✅ 已完成` `app.control-plane` 守卫、Installation-scoped 幂等键、唯一抖音搜索曝光 DTO、Task/定义原子创建、201/200 重放与隐藏 Tauri App 表单生产同路径已验证 |
| 查询 Task API | `✅ 已完成` Installation-scoped 列表/详情、opaque keyset 分页、跨 scope 统一不可见与隐藏 Tauri App 生产同路径已验证 |
| 暂停/恢复 API | `✅ 已完成` Installation-scoped 幂等控制命令、原子 sequence、ACK 后事件门禁与隐藏 Tauri App/FakeExecutor 生产同路径已验证 |
| 取消/紧停 API | `✅ 已完成` 原子 CANCELLING、幂等重放、ACK 后终态、完成竞态、结果不确定与隐藏 Tauri App/FakeExecutor 生产同路径已验证 |
| Task 桌面投影 | `✅ 已完成` TanStack Query 权威快照、严格 DTO、事件去重、缺口/版本回拉、有限降级及 Rust SSE→Tauri Channel 已由隐藏 App 生产同路径验证 |
| UI Harness | `✅ 已完成` Playwright Chromium 覆盖 ready/unavailable/flaky，以及创建→暂停→恢复→取消、独立成功与刷新恢复；正式 dist 排除 Harness 与测试 Adapter 已验证 |
| 持续集成 | `✅ 已完成` Backend、Frontend、Rust 分层质量门禁，以及 macOS/Windows 真实桌面构建与 Tauri 冒烟矩阵已建立 |
| Git 仓库 | `✅ 已完成` 已初始化 `main` 分支，规划基线随 R0-10 提交 |
| GitHub 私有仓库 | `✅ 已完成` `masterAventador/automation-tool` 已创建为 `PRIVATE`，`main` 已推送 |
| 本机工具链 | `✅ 已完成` macOS arm64、Rust/Clippy/Rustfmt、Node/pnpm、uv Python 3.12、Docker、Chrome、Xcode 签名链和 ffmpeg-full 可用 |
| 本地/云端服务 | `⬜` 开发验收会临时启动并清理本地服务；尚无常驻环境，未部署云端 |

## 4. 全局完成门禁

每个代码任务都必须满足：

- 生产同路径验收：从该接口/功能在正式产品中的原始入口，经正式适配层调用真实依赖并验证最终结果；直接调用下层、Mock/Fake、Test Harness、进程内客户端和日志只能作为分层证据，不能替代完成验收；真实链路未建立时相关跨端或用户功能任务最多标 `🔍 待验收`，并登记补验收依赖；
- 测试先行并保存 RED 证据；
- Python 相关：Ruff、类型检查、相关 pytest；
- TypeScript 相关：Lint、Typecheck、相关 Vitest；
- UI 行为：相关 Playwright UI Harness；
- Rust/Tauri：fmt、Clippy、相关 Rust 测试和 Tauri E2E；
- App 调用的 API：必须由真实测试版 Tauri App 经正式 Rust 网络桥请求真实本地/隔离后端；Mock、Test Harness、直接 HTTP 客户端只算分层测试，不能替代跨端验收；现有 Health/注册/凭据/Session 端点统一在 I2-09 补齐该门禁；
- 协议：OpenAPI/JSON Schema/fixtures 重新生成且无漂移；
- 数据库：真实 PostgreSQL 集成和迁移；
- RPA：真实受控平台最终状态，不以 Mock/点击/日志替代；
- 真实账号非阻塞策略：账号、扫码或平台人工安全校验暂不可用时，使用自建测试页与隔离 Adapter 完成可自动化实现，将真实最终状态验收保持为 `🔍 待真实账号` 并自动继续后续无账号依赖任务；不得把测试页/Fake 标成真实平台已通过；
- 安全：敏感信息、资源上限、取消、超时和清理；
- 文档：同一任务更新本路线图状态和验证证据。

### 4.1 MVP 失败矩阵

每个相关任务在开工前必须把适用项映射到具体测试；不适用项写明理由。

| 边界 | 必须覆盖的失败 |
| --- | --- |
| 安装实例 | 未注册、过期、吊销、重放、跨环境和冒充 |
| Control Plane | 未启动、版本不兼容、数据库不可用、重启和超时 |
| PostgreSQL | 唯一冲突、revision CAS、事务回滚、迁移失败、连接耗尽 |
| Executor | 未安装、签名/版本错误、启动超时、挂起和崩溃循环 |
| App ↔ Executor | token 错误、协议漂移、stdout 破损和 stop/invoke 竞争 |
| Executor ↔ Server | 断网、乱序、重复、迟到事件、心跳丢失和旧连接 |
| 浏览器 | 未安装、路径失效、Profile 锁、版本升级、窗口关闭和进程残留 |
| 平台登录 | 未登录、二维码过期、登录过期、验证码、风控和权限差异 |
| 目标发现 | 空结果、重复、无限滚动、弹窗、页面改版和目标变化 |
| 外部动作 | 超频、取消竞态、发送后断网、验证失败和重复执行 |
| 任务控制 | 暂停/完成、取消/完成、紧停/dispatch 和重复控制 |
| 恢复 | App、Executor、Control Plane 分别崩溃以及机器休眠 |
| Artifact | 磁盘满、过大、过多、权限拒绝、路径替换和清理失败 |
| 隐私 | Cookie、Token、页面、聊天和绝对路径进入日志、DB、事件或诊断包 |
| 安装包 | 测试驱动、调试权限、真实数据、错误平台二进制和未签名文件 |

## 5. Wave 0：仓库与决策基线

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| R0-01 | 完整研读竞品材料 | 静态分析、截图、视频行为和证据限制全部进入竞品报告 | — | ✅ 已完成 |
| R0-02 | 锁定产品优先级 | 文档明确 `RPA > 内容 > AI`，没有旧 AI 中台优先残留 | R0-01 | ✅ 已完成 |
| R0-03 | 锁定 MVP 边界 | 无产品登录 UI、单安装实例、抖音单平台纵向闭环 | R0-02 | ✅ 已完成 |
| R0-04 | 锁定浏览器方案 | 外部 Chrome/Edge + App 独立 Profile；禁止默认 Profile/内嵌 WebView | R0-03 | ✅ 已完成 |
| R0-05 | 锁定部署架构 | Control Plane 独立部署；开发本机、Demo 云端；Executor 本机 | R0-03 | ✅ 已完成 |
| R0-06 | 迁移旧项目规则 | 新 `AGENTS.md`、`CLAUDE.md` 与当前方向一致 | R0-02 | ✅ 已完成 |
| R0-07 | 建立产品与架构文档 | 产品、工程结构、前端和后端权威文档齐全且职责不重叠 | R0-05 | ✅ 已完成 |
| R0-08 | 建立任务级路线图 | 本文件覆盖 MVP、后续 RPA、内容和 AI 的任务及状态 | R0-07 | ✅ 已完成 |
| R0-09 | 建立仓库入口与忽略规则 | README、`.gitignore`、`.editorconfig`；本机数据不进入 Git | R0-06 | ✅ 已完成 |
| R0-10 | 初始化本地 Git | `main` 分支、首个文档提交、工作树干净 | R0-09 | ✅ 已完成 |
| R0-11 | 创建 GitHub 私有仓库 | `automation-tool` 为 private，remote 正确，首个提交推送成功 | R0-10 | ✅ 已完成 |
| R0-12 | 旧代码复用审计 | 对 `local_executor.rs`、`sidecar_package.rs`、`browser_session.rs` 形成逐模块迁移清单 | R0-10 | ✅ 已完成 |
| R0-13 | 全局工具链体检 | 记录 Rust、Node/pnpm、uv/Python、Docker、Chrome/Edge、签名工具版本 | R0-10 | ✅ 已完成 |

## 6. Wave 1：工程骨架与开发闭环

### 目标

建立能本机快速调试、也能构建云端 Control Plane 的最小工程，不实现 RPA 业务。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| F1-01 | 初始化 Backend 包 | `backend/pyproject.toml`、src layout、uv lock、pytest/Ruff/类型检查最小配置 | R0-13 | ✅ 已完成 |
| F1-02 | 初始化 Control Plane | FastAPI app factory、lifespan、结构化错误；失败测试先行 | F1-01 | ✅ 已完成 |
| F1-03 | Health/Version API | `/api/v1/health`、`/api/v1/version`、协议兼容范围和契约测试 | F1-02 | ✅ 已完成 |
| F1-04 | PostgreSQL Compose | 本地 Compose、健康检查、独立开发/测试数据库、无默认弱生产凭据 | F1-01 | ✅ 已完成 |
| F1-05 | SQLAlchemy/Alembic 基线 | async session、空库升级/回滚测试、连接失败安全错误 | F1-04 | ✅ 已完成 |
| F1-06 | 初始化 Frontend | React/TypeScript/Vite/Ant Design/pnpm lock；没有 Web 部署入口 | R0-13 | ✅ 已完成 |
| F1-07 | 初始化 Tauri v2 | `src-tauri`、最小 Capability/CSP、开发窗口启动 | F1-06 | ✅ 已完成 |
| F1-08 | App 无登录启动页 | 启动进入工作台壳；后端不可用进入诊断，不跳登录 | F1-03,F1-07 | ✅ 已完成 |
| F1-09 | BaseUrl Profile | `local/demo` Schema；local 只允许 loopback，demo 强制 HTTPS/允许域名 | F1-07 | ✅ 已完成 |
| F1-10 | ControlPlaneTransport 契约 | 业务层接口、正式 Tauri stub 与测试 Harness 实现边界 | F1-08,F1-09 | ✅ 已完成 |
| F1-11 | OpenAPI 导出 | 后端生成快照、漂移检查、前端 DTO 生成脚本 | F1-03,F1-06 | ✅ 已完成 |
| F1-12 | UI Harness 基线 | Playwright 只测试 React UI；生产构建证明不含测试 Adapter | F1-10 | ✅ 已完成 |
| F1-13 | Tauri 四层测试基线 | Vitest、Playwright、Rust、WebdriverIO 命令和最小绿测 | F1-07,F1-12 | ✅ 已完成 |
| F1-14 | CI 基线 | Backend、Frontend、Rust 分层检查；macOS/Windows 桌面骨架 | F1-05,F1-13 | ✅ 已完成 |

## 7. Wave 2：安装实例认证与跨进程协议

### 目标

用户看不到登录页，但云端 API 和 Executor 通道都有可撤销、限权限的安装实例认证。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| I2-01 | 稳定资源 ID | installation/executor/task/attempt/action/artifact ID 类型与非法值测试 | F1-05 | ✅ 已完成 |
| I2-02 | Installation 表 | 公钥、状态、revision、吊销和迁移；真实 PostgreSQL 测试 | I2-01 | ✅ 已完成 |
| I2-03 | Demo Bootstrap 模型 | 限时、限环境、限用途；不能调用业务 API | I2-02 | ✅ 已完成 |
| I2-04 | 设备密钥生成 | Tauri 首启生成 Ed25519 密钥；私钥不进入 React/普通配置 | F1-07,I2-01 | ✅ 已完成 |
| I2-05 | Installation 注册 API | challenge/response 或等价签名注册；重放、过期、冒充测试 | I2-03,I2-04 | ✅ 已完成 |
| I2-06 | 设备凭据签发 | 凭据版本、吊销、轮换、最小 scope；数据库不存明文私钥 | I2-05 | ✅ 已完成 |
| I2-07 | 短期设备 Session | 长期凭据换短期能力；过期、时钟偏差和吊销测试 | I2-06 | ✅ 已完成 |
| I2-08 | Rust App 私有存储 | `app_data_dir` 读写/替换/删除设备凭据；权限拒绝和存储损坏受控失败 | I2-04,I2-07 | ✅ 已完成 |
| I2-09 | Rust 网络桥 | operation allowlist、凭据注入、关联 ID；真实 Tauri App 调用 Health/注册/凭据/Session；禁止任意 URL 代理 | I2-08,F1-11 | ✅ 已完成 |
| I2-10 | Executor v1 Envelope | Pydantic 判别联合、version/message/deadline/idempotency/sequence | I2-01 | ✅ 已完成 |
| I2-11 | 协议 Schema/Fixtures | 有效/无效样例覆盖未知字段、敏感数据、非法时间和枚举 | I2-10 | ✅ 已完成 |
| I2-12 | Rust/TS 协议一致性 | 三语言回放同一 fixtures，结论一致 | I2-11,F1-11 | ✅ 已完成 |
| I2-13 | Executor WebSocket 认证 | installation/executor/版本绑定；旧连接、冒充和吊销测试 | I2-07,I2-10 | ✅ 已完成 |
| I2-14 | 安装实例吊销闭环 | 吊销阻止 App 请求、新任务和 Executor 连接；UI 明确诊断 | I2-09,I2-13 | ✅ 已完成 |

## 8. Wave 3：Control Plane 任务与事件闭环

### 目标

使用 FakeExecutor 跑通与真实 RPA 相同的任务状态、命令、事件和 UI。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| T3-01 | 任务状态机 | 全部合法/非法转换、终态、CANCELLING 和 OUTCOME_UNCERTAIN 单元测试 | I2-01 | ✅ 已完成 |
| T3-02 | Task 数据模型 | tasks/revision/installation scope/Alembic/仓储集成测试 | T3-01,I2-02 | ✅ 已完成 |
| T3-03 | Attempt/Action 模型 | execution attempt、action 状态与唯一约束 | T3-02 | ✅ 已完成 |
| T3-04 | Event 模型 | `(task_id, sequence)` 唯一、版本、安全消息和快照投影 | T3-02 | ✅ 已完成 |
| T3-05 | Command/Outbox 模型 | 持久命令、幂等、deadline、投递/确认状态 | T3-03 | ✅ 已完成 |
| T3-06 | 创建任务 API | idempotency、参数校验、installation 隔离 | T3-02,I2-14 | ✅ 已完成 |
| T3-07 | 任务查询 API | 列表/详情/分页；跨 installation 按不可见处理 | T3-06 | ✅ 已完成 |
| T3-08 | Executor Connection Registry | 心跳、在线、旧连接替换和单实例 API 约束 | I2-13 | ✅ 已完成 |
| T3-09 | 命令投递服务 | task offer/ack、重连恢复、过期和重复投递 | T3-05,T3-08 | ✅ 已完成 |
| T3-10 | FakeExecutor | 无副作用回放全部任务与控制事件；不放宽生产状态机 | T3-09 | ✅ 已完成 |
| T3-11 | 事件接收与收敛 | sequence、重复、缺口、迟到事件和 revision CAS | T3-04,T3-10 | ✅ 已完成 |
| T3-12 | SSE 事件流 | last-event/断线/重连/终态关闭；事件先落库后推送 | T3-11 | ✅ 已完成 |
| T3-13 | 暂停/恢复 API | 命令与确认语义；未确认不能提前改状态 | T3-09,T3-11 | ✅ 已完成 |
| T3-14 | 取消/紧停 API | CANCELLING、确认、结果不确定和幂等 | T3-13 | ✅ 已完成 |
| T3-15 | 前端 Query/事件 Reducer | 快照权威、事件去重、缺口回拉和版本降级 | T3-07,T3-12 | ✅ 已完成 |
| T3-16 | 工作台页面 | 当前任务、最近任务、后端/Executor 状态和全局紧停 | T3-15 | ✅ 已完成 |
| T3-17 | 新建任务骨架 | 抖音搜索曝光模板字段和客户端/服务端一致校验 | T3-06,T3-15 | ✅ 已完成 |
| T3-18 | 运行详情页面 | 状态、进度、时间线、目标结果和控制按钮 | T3-13,T3-15 | ✅ 已完成 |
| T3-19 | UI Harness E2E | 创建→运行→暂停→恢复→取消/成功→刷新恢复 | T3-16,T3-17,T3-18 | ✅ 已完成 |
| T3-20 | Control Plane 重启恢复 | PostgreSQL 保持任务/命令/事件，FakeExecutor 重连收敛 | T3-11,T3-19 | ✅ 已完成 |

## 9. Wave 4：Tauri 与 Local Executor 生命周期

### 目标

把 FakeExecutor 替换为真实 Python 子进程，但暂不操作平台。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| E4-01 | 审计旧 local_executor | 列出可迁移进程/协议逻辑和必须删除的 tenant/Core 依赖 | R0-12,I2-10 | ✅ 已完成 |
| E4-02 | Executor Python 入口 | stdin bootstrap、健康、信号和出站连接最小进程 | E4-01,I2-13 | ✅ 已完成 |
| E4-03 | PyInstaller onedir PoC | macOS/Windows 各能启动；Playwright 依赖暂不加入 | E4-02 | ✅ 已完成 |
| E4-04 | Executor Manifest | 版本、平台、架构、大小、SHA-256 和 Ed25519 签名 | E4-03 | ✅ 已完成 |
| E4-05 | Rust 包验证 | 签名/摘要/平台/架构/防降级；错误包 fail closed | E4-04 | ✅ 已完成 |
| E4-06 | stdin 随机认证 | 256-bit 会话令牌不进 argv/env/log/响应 | E4-02,E4-05 | ✅ 已完成 |
| E4-07 | Rust ExecutorManager | 固定 start/status/stop Rust 生命周期，单实例和并发线性化 | E4-05,E4-06 | ✅ 已完成 |
| E4-08 | 进程监管 | 后台检测退出、有界重启预算、显式停止不重启 | E4-07 | ✅ 已完成 |
| E4-09 | 超时与进程树清理 | Unix process group、Windows Job Object、挂起调用终止 | E4-07 | ✅ 已完成 |
| E4-10 | stderr 脱敏限界 | 凭据/私有路径脱敏；行数、单行和总大小上限 | E4-07 | ✅ 已完成 |
| E4-11 | Executor 本机 SQLite | command/idempotency/checkpoint/outbox 最小账本与迁移 | E4-02 | ✅ 已完成 |
| E4-12 | 真实协议回放 | Control Plane 向真实 Executor 下发无副作用任务并收事件 | E4-08,E4-11,T3-20 | ✅ 已完成 |
| E4-13 | PlatformAdapter 接入 | React 能看状态、重启、诊断和紧停，不直接连 Executor | E4-07,T3-16 | ✅ 已完成 |
| E4-14 | Tauri 生命周期 E2E | 启动/调用/挂起/崩溃/重启/停止/退出清理 | E4-09,E4-13 | ✅ 已完成 |
| E4-15 | 正式包测试能力审计 | 生产包不含 WebDriver、测试命令、测试 Sidecar 或调试端口 | E4-14 | ✅ 已完成 |

## 10. Wave 5：外部浏览器与抖音登录

### 目标

打开外部浏览器，首次扫码一次后持久复用 App 独立 Profile。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| B5-01 | 审计旧 browser_session | 提取私有目录、Profile、状态机和注销逻辑；排除旧账号/RBAC | R0-12,E4-11 | ✅ 已完成 |
| B5-02 | macOS 浏览器发现 | Chrome/Edge 标准应用、签名/Bundle ID allowlist、路径失效测试 | B5-01 | 🔍 待 Edge 实机验收 |
| B5-03 | Windows 浏览器发现 | 注册表/标准路径、签名/产品 allowlist、路径失效测试 | B5-01 | ✅ 已完成 |
| B5-04 | 浏览器选择设置 | 用户选择受支持浏览器；不能选任意可执行文件 | B5-02,B5-03 | ✅ 已完成 |
| B5-05 | 私有 Profile 目录 | 平台/UUID 规范路径、权限、拒绝 symlink、原子创建 | B5-01 | ✅ 已完成 |
| B5-06 | Profile 单实例锁 | 同一 Profile 多任务/多进程竞争必须拒绝 | B5-05 | ✅ 已完成 |
| B5-07 | Playwright 打包 PoC | PyInstaller Executor 中启动系统 Chrome/Edge headed context | E4-03,B5-04 | ✅ 已完成 |
| B5-08 | BrowserRuntime | 启动、页面、窗口、超时、关闭和进程清理接口 | B5-06,B5-07 | ✅ 已完成 |
| B5-09 | 抖音 Session 检测 | healthy/expired/missing/risk/unknown；使用页面状态而非 Cookie 上传 | B5-08 | ✅ 已完成 |
| B5-10 | 抖音扫码流程 | login_required、外部窗口、二维码过期、重新检查 | B5-09 | ✅ 已完成 |
| B5-11 | 人工接管 | 验证码/滑块/风控进入 handoff，不自动处理 | B5-10 | ✅ 已完成 |
| B5-12 | Session 健康上报 | Control Plane 只存平台/状态/revision/时间，不存 Cookie | B5-09,T3-11 | ✅ 已完成 |
| B5-13 | 平台状态页面 | 查看登录健康、打开处理、重新检查和注销 | B5-10,B5-12 | ✅ 已完成 |
| B5-14 | 安全注销 | 先阻止新任务、停关联执行、再删除平台 Profile | B5-06,B5-13 | ✅ 已完成 |
| B5-15 | 登录复用验收 | App/Executor/浏览器重启后不重扫；失效后正确接管 | B5-14 | 🔍 待真实账号 |
| B5-16 | 默认 Profile 隔离审计 | 测试和运行证据证明未读用户默认 Chrome User Data | B5-15 | ✅ 已完成 |

## 11. Wave 6：抖音目标发现与用户预览

### 目标

完成“关键词搜索 → 有界目标发现 → 去重/黑名单 → 用户预览确认”，不产生评论或私信。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| D6-01 | 抖音页面版本模型 | page version、已知入口、未知版本 fail closed | B5-09 | ✅ 已完成 |
| D6-02 | 页面对象基础 | 搜索入口、结果列表、弹窗和登录跳转集中封装 | D6-01 | ✅ 已完成 |
| D6-03 | 关键词校验 | 长度、空白、控制字符、任务上限和服务端一致规则 | T3-17 | ✅ 已完成 |
| D6-04 | 搜索执行 | 打开页面、输入、提交、等待结果；网络慢/超时测试 | D6-02,D6-03 | ✅ 已完成 |
| D6-05 | 有界滚动 | 最大轮次、最大目标、无新增停止和取消检查点 | D6-04 | ✅ 已完成 |
| D6-06 | Candidate 模型 | 稳定去重键、最小摘要、来源和页面 revision | D6-05,I2-10 | ✅ 已完成 |
| D6-07 | 目标隐私裁剪 | 不上传非必要个人信息、页面原文或绝对链接凭据 | D6-06 | ✅ 已完成 |
| D6-08 | 黑名单/去重 | 本任务去重、历史窗口去重和黑名单原因 | D6-06 | ✅ 已完成 |
| D6-09 | Target 数据库 | task_targets、唯一约束、分页和 installation 隔离 | D6-06,T3-02 | ✅ 已完成 |
| D6-10 | Discover 命令闭环 | Control Plane 投递、Executor 上报、任务状态收敛 | D6-05,D6-09,E4-12 | ✅ 已完成 |
| D6-11 | 目标预览 API | 列表、排除、确认 revision；过期候选拒绝 | D6-09 | ✅ 已完成 |
| D6-12 | 目标预览 UI | 摘要、排除、去重/黑名单标记和确认 | D6-11,T3-18 | ✅ 已完成 |
| D6-13 | 未确认副作用守卫 | 没有确认 command 时 Executor 无法收到 action | D6-10,D6-11 | ✅ 已完成 |
| D6-14 | 页面漂移诊断 | 未知元素时保存受限 Artifact 并进入 handoff | D6-02,E4-10 | ✅ 已完成 |
| D6-15 | Fake 页面回归样例 | 正常、空结果、弹窗、登录跳转、未知版本和无限滚动 | D6-14 | ✅ 已完成 |
| D6-16 | 真实目标发现验收 | 受控抖音账号完成搜索与预览，确认无外部副作用 | D6-15 | 🔍 待真实账号 |

## 12. Wave 7：抖音受控评论与主动私信

### 目标

在自有/授权目标上完成真实动作，具备服务端授权、本机硬限制和结果不确定语义。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| A7-01 | 风险策略领域模型 | 平台/动作/安装实例级最小间隔、任务/日上限、连续失败阈值 | T3-01 | ✅ 已完成 |
| A7-02 | 服务端计数与并发授权 | PostgreSQL 原子授权；并发不能突破上限 | A7-01,T3-03 | ✅ 已完成 |
| A7-03 | ActionAuthorization | action/target/attempt/deadline/idempotency 签名或 MAC | A7-02,I2-10 | ✅ 已完成 |
| A7-04 | Executor 本机硬下限 | 服务器不能放宽最小间隔、任务上限和紧停 | A7-03,E4-11 | ✅ 已完成 |
| A7-05 | 文案校验 | 长度、空内容、控制字符、敏感模式和模板变量 | A7-01 | ✅ 已完成 |
| A7-06 | 高风险最终确认 | UI 展示目标、动作、文案和数量；确认 revision 防旧提交 | A7-03,D6-12 | ✅ 已完成 |
| A7-07 | 副作用账本 | prepared/dispatched/verified/uncertain 本机原子状态 | A7-04,E4-11 | ✅ 已完成 |
| A7-08 | 抖音评论 Page Object | 定位输入/提交/最终状态；页面变化 fail closed | D6-02,A7-05 | ✅ 已完成 |
| A7-09 | 抖音私信 Page Object | 进入会话/输入/发送/最终状态；权限差异处理 | D6-02,A7-05 | ✅ 已完成 |
| A7-10 | 只浏览动作 | 无发送副作用的目标访问，作为低风险基线 | D6-10 | ✅ 已完成 |
| A7-11 | 评论动作执行 | 授权校验→账本→点击→最终验证→结构化 receipt | A7-07,A7-08 | ✅ 已完成 |
| A7-12 | 私信动作执行 | 授权校验→账本→发送→最终验证→结构化 receipt | A7-07,A7-09 | ✅ 已完成 |
| A7-13 | 结果不确定处理 | dispatched 未 verified 先查询；无法确认不重放 | A7-11,A7-12 | ✅ 已完成 |
| A7-14 | 连续失败熔断 | 达阈值停止新动作、打开 handoff、保持审计 | A7-02,A7-13 | ✅ 已完成 |
| A7-15 | 目标级结果 UI | 成功/跳过/失败/不确定和证据摘要 | A7-13,T3-18 | ✅ 已完成 |
| A7-16 | 评论真实验收 | 仅自有/授权目标；平台最终状态与服务端一致 | A7-15 | 🔍 待真实账号 |
| A7-17 | 私信真实验收 | 仅自有/授权目标；重复/断网/确认丢失覆盖 | A7-15 | 🔍 待真实账号 |
| A7-18 | 风险护栏对抗测试 | 篡改授权、超频、重放、取消竞态和服务器放宽均失败 | A7-16,A7-17 | ⬜ 未开始 |

## 13. Wave 8：恢复、诊断与 MVP 质量收口

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| H8-01 | 端到端暂停 | 安全检查点确认后才 PAUSED；运行中原子动作不伪装撤销 | A7-13,T3-13 | ✅ 已完成 |
| H8-02 | 端到端取消 | CANCELLING→确认终态；最后动作不明进入 uncertain | H8-01,T3-14 | ✅ 已完成 |
| H8-03 | 离线紧急停止 | 不依赖网络停止新副作用和完整进程树；重连补报 | E4-09,A7-07 | ✅ 已完成 |
| H8-04 | App 崩溃恢复 | UI 恢复快照，任务不中断或重复 | T3-20,H8-01 | ✅ 已完成 |
| H8-05 | Executor 崩溃恢复 | restart budget、账本对齐、dispatched 未验证处理 | E4-08,A7-13 | ✅ 已完成 |
| H8-06 | Control Plane 重启恢复 | Executor 重连、命令/事件幂等、任务收敛 | T3-20,E4-12 | ✅ 已完成 |
| H8-07 | 断网/抖动 | 停在安全点、事件 spool、重连续传、不烧无限重试 | H8-05,H8-06 | ✅ 已完成 |
| H8-08 | 休眠/锁屏 | 时钟跳变、deadline、窗口不可用和恢复诊断 | H8-07 | ✅ 已完成 |
| H8-09 | Local Artifact | 稳定 ID、摘要、媒体类型、大小、相对路径和权限 | E4-11 | ✅ 已完成 |
| H8-10 | 诊断截图/Trace | 只在失败/用户开启时保存，数量/大小/时间上限 | H8-09,D6-14 | ✅ 已完成 |
| H8-11 | 日志脱敏 | 服务端、Rust、Executor 全链路凭据/页面/路径泄漏测试 | E4-10,H8-10 | ✅ 已完成 |
| H8-12 | 清理与磁盘治理 | 保留策略、磁盘满、清理失败、正在引用 Artifact 保护 | H8-09,H8-10 | ⬜ 未开始 |
| H8-13 | 诊断导出 | 用户主动导出受限包；不含 Cookie/完整私信/绝对私有路径 | H8-11,H8-12 | ⬜ 未开始 |
| H8-14 | 工作台指标 | 任务/动作成功、失败、接管、不确定；只读结构化事实 | A7-15,T3-16 | ⬜ 未开始 |
| H8-15 | 完整失败矩阵自动化 | 本台账第 4.1 节所有可自动化分支有测试或不适用理由 | H8-01..H8-14 | ⬜ 未开始 |
| H8-16 | 规格复审 | 从分叉点审查完整实现是否满足产品/MVP/文档 | H8-15 | ⬜ 未开始 |
| H8-17 | 代码质量复审 | 安全 fail-open、竞态、资源泄漏、假绿测试和平台差异 | H8-16 | ⬜ 未开始 |
| H8-18 | 通用更新底座选型与契约 | 评估现成 SDK；冻结与业务无关的版本、平台、签名、发布策略和状态契约 | H8-17 | ⬜ 未开始 |
| H8-19 | 通用更新策略机 | 可选更新支持立即安装/暂不安装/跳过版本；强制更新不可跳过，状态持久且版本单调 | H8-18 | ⬜ 未开始 |
| H8-20 | 后台检查与下载 | App 启动、有界轮询和用户“检查更新”共用同一检查入口；后台下载、签名验证、断点/失败恢复；新包原子覆盖旧缓存 | H8-19 | ⬜ 未开始 |
| H8-21 | 安装与重启协调 | 立即安装先安全退出主 App；暂缓在启动/轮询继续提示；强更下载后下次启动静默进入安装 | H8-20 | ⬜ 未开始 |
| H8-22 | 更新 UI 与双平台验收 | 通用设置/提示 UI；真实签名包从 App 原入口在 macOS、Windows 完成升级、跳过、覆盖和强更验收 | H8-21 | ⬜ 未开始 |

## 14. Wave 9：双平台安装包与本地候选版

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| P9-01 | macOS Executor 构建 | PyInstaller onedir，依赖完整、无开发路径、签名准备 | H8-22 | ⬜ 未开始 |
| P9-02 | Windows Executor 构建 | PyInstaller onedir，Playwright/UIA 依赖和 Job Object 正常 | H8-22 | ⬜ 未开始 |
| P9-03 | macOS Tauri 候选包 | 签名、公证策略、最小 Capability/CSP | P9-01 | ⬜ 未开始 |
| P9-04 | Windows Tauri 候选包 | 签名、安装/卸载和最小系统权限 | P9-02 | ⬜ 未开始 |
| P9-05 | 正式包内容审计 | 无 WebDriver、调试端口、测试凭据、真实日志/Profile/素材 | P9-03,P9-04 | ⬜ 未开始 |
| P9-06 | macOS 干净安装 | 无 Python 前置；打开即用；Chrome/Edge/扫码/任务/恢复 | P9-03,P9-05 | 🔍 待设备验收 |
| P9-07 | Windows 干净安装 | 无 Python 前置；同上；DPI/杀进程/卸载行为 | P9-04,P9-05 | 🔍 待设备验收 |
| P9-08 | 版本兼容/降级 | App/Executor/Control Plane 兼容矩阵，错误版本 fail closed | P9-06,P9-07 | ⬜ 未开始 |
| P9-09 | 本地 MVP 最终验收 | 产品规划 14 条 MVP 验收全部通过并记录证据 | P9-08 | ⬜ 未开始 |

### 14.1 客户 Demo 前置：账号体系与设备归属

> P9-09 仍以本地单设备、无产品账号完成 MVP；任何客户 Demo 必须先完成 U9-01～U9-06，不得以匿名设备申请、配对码或人工设备审批替代产品账号。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| U9-01 | 账号范围与威胁模型 | 固定首版账号生命周期、登录标识、凭据恢复、Session、设备归属、停用/吊销和审计；Demo 账号由认证运维入口创建，不开放匿名自注册；组织、租户、RBAC、套餐和计费继续排除 | P9-09 | ⬜ 未开始 |
| U9-02 | 账号领域与 PostgreSQL | User、登录凭据、账号状态、审计与迁移；规范唯一标识、强密码哈希、并发创建/停用/恢复和数据最小化 fail closed | U9-01,F1-05 | ⬜ 未开始 |
| U9-03 | 登录与 Session API | 登录、刷新、注销、密码修改/重置；短期访问能力、旋转 refresh、重放检测、限流/锁定、统一脱敏错误和全 Session 吊销 | U9-02,F1-10 | ⬜ 未开始 |
| U9-04 | Tauri 登录与账号状态 UI | 未登录只显示登录/恢复状态，不挂载业务工作台；Token 与 refresh secret 仅由 Rust 私有存储持有，React 只接收安全账号投影；覆盖加载、失败、锁定、离线、注销和重启恢复 | U9-03,F1-08,I2-09 | ⬜ 未开始 |
| U9-05 | 登录账号自动绑定设备 | 登录成功后复用 I2 设备密钥证明，把 Installation 原子绑定当前账号并签发/轮换设备凭据；不使用配对码、设备轮询或后台逐设备审批，跨账号、重放、并发绑定和已吊销设备 fail closed | U9-03,U9-04,I2-14 | ⬜ 未开始 |
| U9-06 | 账号与设备管理验收 | 认证运维创建/停用/重置 Demo 账号，用户可修改密码、注销并查看/吊销自己的设备；真实 Tauri App 完成登录、自动绑定、重启、断网、Session 失效、账号停用和设备吊销纵向验收 | U9-02..U9-05 | ⬜ 未开始 |

## 15. Wave 10：云端客户 Demo

> 本 Wave 的实际部署、域名和云资源操作需要用户在执行时明确授权；U9-01～U9-06 未完成时不得向客户交付 Demo。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| C10-01 | Demo 部署设计 | 单实例 Control Plane、PostgreSQL、HTTPS、域名、备份和资源上限 | U9-06 | ⬜ 未开始 |
| C10-02 | Control Plane Docker | 锁定镜像、非 root、健康检查、优雅停止和版本标签 | F1-14,C10-01 | ⬜ 未开始 |
| C10-03 | 云 PostgreSQL | 最小权限、迁移、备份、恢复演练和网络隔离 | C10-01 | ⬜ 未开始 |
| C10-04 | HTTPS/域名 | TLS、反代、请求大小/超时/限流和安全头 | C10-02 | ⬜ 未开始 |
| C10-05 | Secret 管理 | DB、账号 Session 签发、密码 Pepper 与设备签发密钥；不进入镜像、Git 或日志 | C10-02,C10-03,U9-03 | ⬜ 未开始 |
| C10-06 | Demo 账号初始化与运维 | 通过 U9 认证运维入口创建、停用和重置 Demo 账号，固定最小权限、审计和应急全 Session/设备吊销 | U9-06,C10-05 | ⬜ 未开始 |
| C10-07 | App Demo Profile | 签名 baseUrl/允许域名；local/demo 账号 Session、设备凭据和数据隔离 | F1-09,C10-04,C10-06 | ⬜ 未开始 |
| C10-08 | 云端部署 | 执行迁移、启动单实例、健康检查；不自动扩容多副本 | C10-03..C10-07 | ⬜ 未开始 |
| C10-09 | 云端协议回归 | 同一 OpenAPI/fixtures，App 只切 baseUrl，无业务代码变化 | C10-08 | ⬜ 未开始 |
| C10-10 | 网络/重启恢复 | 服务器重启、网络抖动、Executor 重连和事件续传 | C10-09,H8-07 | ⬜ 未开始 |
| C10-11 | 账号与设备吊销演示 | 停用账号或吊销一个设备立即失效且不影响其他有效账号/设备；无匿名业务写入口 | C10-10 | ⬜ 未开始 |
| C10-12 | 客户视角 Demo 验收 | 安装→账号登录→设备自动绑定→工作台→扫码→预览→动作→结果→接管 | C10-11 | ⬜ 未开始 |
| C10-13 | 部署/回滚手册 | 部署、迁移、备份、恢复、账号/设备吊销、回滚和紧急停服 | C10-12 | ⬜ 未开始 |

## 16. RPA 运营增强路线图

只有 C10 完成后，才按以下顺序扩平台和场景。

### 16.1 抖音运营增强

| ID | 任务 | 完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| RD-01 | 自动曝光模板 | 自动发现、预览、受控动作，复用现有风险引擎 | C10-12 | ⬜ 未开始 |
| RD-02 | 定向曝光模板 | 指定关键词/目标筛选和失效目标处理 | RD-01 | ⬜ 未开始 |
| RD-03 | 链接曝光 | URL 规范化、平台归属校验和目标确认 | RD-02 | ⬜ 未开始 |
| RD-04 | 搜索账号曝光 | 抖音号搜索、重名消歧和预览 | RD-02 | ⬜ 未开始 |
| RD-05 | 批次与失败项重试 | 新 attempt、原幂等链保留、只重试可确认失败 | RD-01..RD-04 | ⬜ 未开始 |
| RD-06 | 抖音运营看板 | 搜索、预览、触达、失败、接管和转化标记 | RD-05 | ⬜ 未开始 |

### 16.2 小红书

| ID | 任务 | 完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| RX-01 | 小红书规则/证据复核 | 当前页面、平台规则、测试账号和允许动作重新验证 | RD-06 | ⬜ 未开始 |
| RX-02 | 独立 Profile/Session | 小红书扫码、健康、过期、风控和注销 | RX-01 | ⬜ 未开始 |
| RX-03 | 搜索与 Candidate | 平台 Adapter，不修改任务引擎 | RX-02 | ⬜ 未开始 |
| RX-04 | 评论动作 | 风险授权、最终验证和真实测试账号 | RX-03 | ⬜ 未开始 |
| RX-05 | 主动私信 | 动态验证平台入口后实现；证据不足不做入站自动回复承诺 | RX-03 | ⬜ 未开始 |
| RX-06 | 小红书真实回归 | 页面漂移、频控、接管和最终状态 | RX-04,RX-05 | ⬜ 未开始 |

### 16.3 抖音客服

| ID | 任务 | 完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| RC-01 | 官方接口可行性 | 优先验证官方授权、接口和合规边界 | RD-06 | ⬜ 未开始 |
| RC-02 | 会话模型 | 会话、消息最小上下文、未读和人工接管 | RC-01 | ⬜ 未开始 |
| RC-03 | 欢迎语 | 条件、频控、黑名单、退订和最终验证 | RC-02 | ⬜ 未开始 |
| RC-04 | 人工回复 | 建议/模板→人工确认→发送→审计 | RC-02 | ⬜ 未开始 |
| RC-05 | 自动回复灰度 | 只在策略和真实验收后启用；敏感内容仍人工 | RC-04 | ⬜ 未开始 |
| RC-06 | 跟进与转化 | 间隔、次数、停止条件、标签和结果 | RC-05 | ⬜ 未开始 |

### 16.4 快手

| ID | 任务 | 完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| RK-01 | 快手规则/页面复核 | 平台规则、测试账号、支持动作和证据等级 | RX-06 | ⬜ 未开始 |
| RK-02 | Profile/Session | 扫码、健康、过期、风控和注销 | RK-01 | ⬜ 未开始 |
| RK-03 | 搜索与目标 | 独立 Adapter、去重和预览 | RK-02 | ⬜ 未开始 |
| RK-04 | 评论/主动触达 | 受控动作、频控、最终验证 | RK-03 | ⬜ 未开始 |
| RK-05 | 快手真实回归 | 平台隔离；失败不影响抖音/小红书 | RK-04 | ⬜ 未开始 |

### 16.5 微信桌面 RPA

| ID | 任务 | 完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| RW-01 | 微信能力/版本矩阵 | Windows/macOS 客户端版本、入口和权限分别确认 | RK-05 | ⬜ 未开始 |
| RW-02 | Windows 环境诊断 | 进程、UIA、DPI、锁屏、遮挡和窗口状态 | RW-01 | ⬜ 未开始 |
| RW-03 | macOS 环境诊断 | AX、Screen Recording、窗口和 Vision 能力；不假设功能等价 | RW-01 | ⬜ 未开始 |
| RW-04 | 结构化定位层 | UIA/AX 优先，版本化 locator 和未知布局 fail closed | RW-02,RW-03 | ⬜ 未开始 |
| RW-05 | OCR 兜底 | 只截必要区域、置信度、脱敏和人工校准 | RW-04 | ⬜ 未开始 |
| RW-06 | 未读识别 | 去重、联系人定位和最小上下文 | RW-04,RW-05 | ⬜ 未开始 |
| RW-07 | 回复建议与人工发送 | 第一版人工确认；发送最终状态和结果不确定 | RW-06 | ⬜ 未开始 |
| RW-08 | 好友申请 | 检查、规则、人工/自动处理和真实测试账号 | RW-07 | ⬜ 未开始 |
| RW-09 | 联系人分群/群发 | 模板变量、批次、黑名单、退订、频控和取消 | RW-08 | ⬜ 未开始 |
| RW-10 | 主动激活 | 沉默条件、触达次数和停止规则 | RW-09 | ⬜ 未开始 |
| RW-11 | 朋友圈发布 | 文案、图片/视频、可见范围和最终状态 | RW-07 | ⬜ 未开始 |
| RW-12 | 朋友圈营销 | 独立风险策略、范围、熔断和真实验收 | RW-11 | ⬜ 未开始 |
| RW-13 | 微信双平台回归 | Windows/macOS 分别按已支持功能验收 | RW-08..RW-12 | ⬜ 未开始 |

## 17. 内容生产与分发路线图

| ID | 任务 | 完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| CT-01 | 内容领域模型 | Material/Timeline/RenderJob/PublishJob/Artifact 稳定接口 | RW-13 | ⬜ 未开始 |
| CT-02 | 对象存储 Provider | 服务端短期上传授权、摘要、大小、清理和最小权限 | CT-01 | ⬜ 未开始 |
| CT-03 | 素材库 API | 视频/图片/音乐、标签、搜索、引用和删除保护 | CT-02 | ⬜ 未开始 |
| CT-04 | 素材库 UI | Tauri 文件选择、上传进度、预览和空间状态 | CT-03 | ⬜ 未开始 |
| CT-05 | 下载任务 | 排队、进度、断点、取消、失败和重试 | CT-03 | ⬜ 未开始 |
| CT-06 | Timeline DTO | 供应商无关轨道、片段、转场、字幕、音频和版本 | CT-01 | ⬜ 未开始 |
| CT-07 | Timeline 编辑/预览 | App 内编辑与预览，供应商对象不泄漏到领域层 | CT-06 | ⬜ 未开始 |
| CT-08 | RenderProvider PoC | 用同一代表样例评估阿里云 IMS/ICE 与 Local FFmpeg | CT-06 | ⬜ 未开始 |
| CT-09 | 首选 RenderProvider | 根据成本、速度、质量、隐私和跨平台结果锁定第一实现 | CT-08 | ⬜ 未开始 |
| CT-10 | 一键剪辑 | 规则→Timeline→Job→成片 Artifact | CT-09 | ⬜ 未开始 |
| CT-11 | 模板剪辑 | 模板版本、素材替换、输入输出追溯 | CT-10 | ⬜ 未开始 |
| CT-12 | 批量剪辑 | 并发、幂等、失败隔离、取消和成本上限 | CT-11 | ⬜ 未开始 |
| CT-13 | 成片管理 | 预览、下载、删除、保留和发布衔接 | CT-10 | ⬜ 未开始 |
| CT-14 | 抖音发布 Adapter | 标题/话题/封面/上传/最终状态，复用本机 Profile | CT-13 | ⬜ 未开始 |
| CT-15 | 小红书发布 Adapter | 平台差异和独立故障隔离 | CT-14 | ⬜ 未开始 |
| CT-16 | 快手发布 Adapter | 平台差异和独立故障隔离 | CT-15 | ⬜ 未开始 |
| CT-17 | 视频号发布 Adapter | 第一阶段只承诺发布，互动另行验证 | CT-16 | ⬜ 未开始 |
| CT-18 | 聚合发布 | 一个成片选择多平台/账号，平台级失败隔离 | CT-14..CT-17 | ⬜ 未开始 |
| CT-19 | 批量发布 | 批量导入、预约、并发和受控重试 | CT-18 | ⬜ 未开始 |
| CT-20 | 发布记录 | 内容版本、平台、目标、最终状态和证据 | CT-18 | ⬜ 未开始 |
| CT-21 | 内容日历 | 计划、状态、冲突和任务关联 | CT-19,CT-20 | ⬜ 未开始 |
| CT-22 | 标题/文案/话题模板 | 模板版本、平台差异和人工编辑 | CT-21 | ⬜ 未开始 |
| CT-23 | AI 视频 Provider | 图生视频/人物替换、输入授权、成本和审核 | CT-13 | ⬜ 未开始 |
| CT-24 | 内容阶段组合验收 | 素材→剪辑→成片→多平台发布→最终记录 | CT-22,CT-23 | ⬜ 未开始 |

## 18. AI 员工与工作流路线图

| ID | 任务 | 完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| AI-01 | AI 场景与风险复核 | 明确哪些只生成建议、哪些允许进入确定性动作 | CT-24 | ⬜ 未开始 |
| AI-02 | ModelProvider 接口 | 供应商无关 chat/structured/tool usage；不泄漏供应商对象 | AI-01 | ⬜ 未开始 |
| AI-03 | 首选模型 Provider | 基于质量、成本、延迟和数据策略选第一实现 | AI-02 | ⬜ 未开始 |
| AI-04 | Prompt/策略版本 | 版本、回滚、输入输出 Schema 和评测样例 | AI-03 | ⬜ 未开始 |
| AI-05 | 轻量知识接口 | 文档、检索、引用；不直接搬旧 RAGFlow 中台 | AI-04 | ⬜ 未开始 |
| AI-06 | AI 专家配置 | 角色、语气、知识、敏感词、黑名单和权限边界 | AI-04,AI-05 | ⬜ 未开始 |
| AI-07 | 运营诊断员工 | 读取结构化任务事实，给建议，不直接点击页面 | AI-06 | ⬜ 未开始 |
| AI-08 | 内容策划员工 | 选题、标题、文案、话题和内容日历建议 | AI-06,CT-22 | ⬜ 未开始 |
| AI-09 | 客服回复员工 | 会话最小上下文、引用、敏感内容和人工确认 | AI-06,RC-04,RW-07 | ⬜ 未开始 |
| AI-10 | RPA 异常诊断员工 | 基于脱敏诊断给处置建议，不能绕过风控 | AI-07,H8-13 | ⬜ 未开始 |
| AI-11 | Workflow Schema | 节点、边、条件、输入输出、版本和无环/可达校验 | AI-06 | ⬜ 未开始 |
| AI-12 | 确定性 Workflow Runtime | 暂停、取消、重试、人工接管和检查点 | AI-11 | ⬜ 未开始 |
| AI-13 | RPA 节点 | 只调用稳定任务 API，不接触页面选择器 | AI-12,RD-06 | ⬜ 未开始 |
| AI-14 | 内容节点 | 素材、剪辑、发布和结果使用稳定 ID | AI-12,CT-24 | ⬜ 未开始 |
| AI-15 | AI 节点 | 结构化输出、预算、超时、回退和人工审批 | AI-12,AI-03 | ⬜ 未开始 |
| AI-16 | 可视化工作流编辑 | 创建、校验、版本、运行和历史 | AI-11..AI-15 | ⬜ 未开始 |
| AI-17 | 定时与触发 | 幂等触发、错过策略、取消和时区 | AI-12 | ⬜ 未开始 |
| AI-18 | 效果分析 | 发布、触达、回复、互动和转化来自结构化事件 | AI-13,AI-14 | ⬜ 未开始 |
| AI-19 | LangGraph/Temporal 决策门 | 只有运行复杂度证据满足条件才引入，形成 ADR | AI-12,AI-17 | ⬜ 未开始 |
| AI-20 | AI/工作流组合验收 | 策划→内容→发布→客服/运营→分析，所有副作用仍受控 | AI-16..AI-19 | ⬜ 未开始 |

## 19. 高冲突与唯一写入区域

开始并行开发前，以下位置必须指定唯一写入者：

- 根目录规则、路线图和锁文件；
- Alembic migration revision；
- OpenAPI、事件和 Executor 协议；
- Tauri 主壳、Capability、CSP 和 `Cargo.lock`；
- 全局导航、Query Client 和 ControlPlaneTransport；
- 抖音 Profile 与页面对象；
- 正式安装包和 Demo Profile；
- CI 和部署清单。

当前默认不使用子代理；只有用户明确要求后才启用并行工作树。

## 20. 每项任务的完成记录格式

任务完成时在对应行标记状态，并在本节追加：

```text
### <任务 ID> <任务名>

- 状态：✅ 已完成
- 日期：YYYY-MM-DD
- 提交：<commit 或“本任务提交”>
- RED：<失败测试与原因>
- GREEN：<实际命令与结果>
- 真实边界：<平台/版本/测试账号范围/最终状态>
- 失败矩阵：<覆盖项与不适用理由>
- 清理：<进程/容器/浏览器/Profile/临时数据>
- 文档：<同步更新文件>
- 遗留：<非阻断低风险项或无>
```

### R0-10 初始化本地 Git

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：初始化前执行 `git status` 返回“not a git repository”，证明仓库基线尚不存在
- GREEN：`git init -b main` 成功；Markdown 相对链接检查通过；`git check-ignore -v .DS_Store` 确认本机文件被忽略
- 真实边界：本地 Git `main` 分支；本任务不涉及产品运行或外部平台
- 失败矩阵：核对本机数据、凭据、数据库、浏览器 Profile、构建产物和测试产物忽略规则
- 清理：`.DS_Store` 保留在本机但不进入 Git；没有启动进程、容器或浏览器
- 文档：README、项目规则、产品/架构文档和本开发台账纳入首个基线提交
- 遗留：无；原 GitHub 认证阻塞已由 `R0-11` 解决

### R0-11 创建 GitHub 私有仓库

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：`gh auth status` 报告 `masterAventador` 的旧 token 无效；只读查询确认同名仓库不存在
- GREEN：通过 GitHub Device Flow 重新认证；`gh repo create automation-tool --private --source=. --remote=origin --push` 创建并首次推送成功
- 真实边界：`gh repo view masterAventador/automation-tool --json nameWithOwner,visibility,url,defaultBranchRef` 返回 `visibility: PRIVATE`、默认分支 `main`
- 失败矩阵：确认没有覆盖同名仓库；远端可见性不是 `PUBLIC`；`origin` 的 fetch/push 地址均为预期仓库
- 清理：未创建临时仓库、分支或工作树；未在文档或命令输出中写入访问令牌
- 文档：同步更新本路线图快照、任务状态、完成记录和当前下一步
- 遗留：无

### R0-12 旧代码复用审计

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：原工程结构文档只有三个模块的概括性复用说明，没有逐能力的迁移、重写、删除、延后结论，也没有旧仓库提交和测试证据
- GREEN：逐项审阅旧提交 `a01cfc9aa93e87e71b78b73eee3e07a3b9d31061` 的三个源模块、关联 runtime 和测试；`cargo test --manifest-path frontend/src-tauri/Cargo.toml --test local_executor --test browser_session --test sidecar_security` 为 35 项通过
- 真实边界：在当前 macOS 开发机运行旧 Rust 测试；Windows `Job Object`、ACL/reparse point 和正式安装包必须在 E4/B5 对应任务重新验证
- 失败矩阵：覆盖并发启动、崩溃重启、挂起超时、进程树终止、日志限界、签名/摘要/回退/替换、symlink、Profile 删除和人工接管；旧测试不替代新协议与真实平台验收
- 清理：未复制旧源文件、未启动持久进程；旧仓库仅保留 Cargo 构建缓存且 tracked 工作树未修改
- 文档：`docs/project-structure.md` 新增审计基线、三个逐模块清单、跨模块排除和实施顺序
- 遗留：在线 Sidecar 更新器明确延后；Windows 专属分支和 PyInstaller 目录包验证归 E4 任务

### R0-13 全局工具链体检

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：体检前没有当前机器基线；实测发现系统 `python3` 为 3.9.6，`rustup`、`corepack`、全局 Ruff/Mypy/PyInstaller 和 Microsoft Edge 不存在，`codesign --version` 不是有效命令
- GREEN：沙盒外确认 macOS 26.4.1 arm64；Rust/Cargo 1.96.1、Clippy 0.1.96、Rustfmt 1.9.0；Node 26.0.0、pnpm 11.9.0；uv 0.11.28 管理 CPython 3.12.13；Docker Client 29.4.3、Server 29.2.1、Compose 5.3.1；Chrome 150.0.7871.128；Xcode 26.5、Apple clang 21.0.0、notarytool 1.1.2；ffmpeg-full 8.1.2_1
- 真实边界：Docker daemon 正常；Chrome 可执行文件存在；`codesign`/`notarytool` 可定位且本机有 1 个 Apple Development identity；Microsoft Edge 和 Developer ID Application identity 当前不存在
- 失败矩阵：确认系统 Python 3.9 不进入项目；项目使用 uv 的 3.12；Ruff/Mypy/PyInstaller 作为锁定项目依赖而非漂移的全局副本；Rust 无 rustup 但 fmt/clippy 可用；pnpm 已全局可用且不依赖 corepack
- 清理：未改变系统 Python、Node、Rust、浏览器或签名配置；未安装非必需重复工具；Homebrew 公式只有 `ffmpeg-full`，`ffmpeg`/`ffprobe` 只有 `/opt/homebrew/bin` 一组入口
- 文档：同步更新本路线图快照、任务状态、环境基线和当前下一步
- 遗留：Edge 只影响后续可选浏览器实机验收；Developer ID 和 notarization 凭据归正式发布任务；Windows 工具链在 CI/Windows 主机验证，均不阻塞 Wave 1～4

### F1-01 初始化 Backend 包

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建包元数据测试，执行 `uv run --no-project --python 3.12 --with pytest pytest backend/tests/unit/test_package_metadata.py -q`，收集阶段因 `ModuleNotFoundError: automation_tool` 失败
- GREEN：`uv sync --locked --dev` 成功；`uv run pytest --cov=automation_tool --cov-report=term-missing` 为 1 项通过、100% 覆盖；`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy` 和 `uv lock --check` 全部通过
- 真实边界：uv 使用 CPython 3.12.13 构建并以 editable package 安装 `automation-tool==0.1.0`；本任务不启动网络服务、数据库或 Executor
- 失败矩阵：验证 src layout 必须经安装后导入、分发版本与公开 `__version__` 一致、锁文件可解析；外部进程、数据库、RPA 和平台失败均不适用
- 清理：虚拟环境、pytest/Ruff/Mypy/coverage 缓存均位于已忽略路径；没有残留服务或临时依赖文件
- 文档：新增 `backend/README.md`，同步根 README、本路线图状态和当前下一步
- 遗留：FastAPI、应用工厂、lifespan 和结构化错误归 `F1-02`

### F1-02 初始化 Control Plane

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建应用工厂和错误边界测试，执行 `uv run pytest tests/unit/control_plane/test_app_factory.py -q`，收集阶段因 `ModuleNotFoundError: automation_tool.control_plane` 失败
- GREEN：FastAPI 0.139.2、Pydantic 2.13.4、Starlette 1.3.1 与 httpx2 2.7.0 锁定；7 项 Backend 测试通过、总覆盖率 100%；`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`、`uv lock --check` 全部通过且无弃用警告
- 真实边界：使用真实 ASGI lifespan 和 TestClient 验证每个 app 实例隔离、startup/shutdown 状态；本任务不绑定端口、不连接数据库、不部署服务
- 失败矩阵：覆盖业务异常、404、其他 HTTP 异常、请求校验、未知异常、私密输入/异常不回显、请求 ID 允许字符/长度和无中间件回退；错误信封固定包含 code、message、requestId、retryable
- 清理：测试客户端均退出 lifespan；无残留端口或进程；虚拟环境与缓存位于忽略路径
- 文档：同步 Backend/根 README、后端架构稳定错误类别、本路线图状态和当前下一步
- 遗留：Health/Version 路由、协议兼容范围和服务器入口归 `F1-03`

### F1-03 Health/Version API

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建系统 API 契约和 CLI 测试，执行 `uv run pytest tests/contract/test_system_api.py tests/unit/control_plane/test_cli.py -q`，收集阶段分别因缺少 `automation_tool.protocol` 和 `uvicorn` 失败
- GREEN：13 项 Backend 测试通过、总覆盖率 100%；`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`、`uv lock --check` 全部通过；真实 Uvicorn HTTP 请求两个端点均返回 200 和预期 JSON
- 真实边界：实际 CLI 绑定 `127.0.0.1:8765`；Health 返回服务/版本，Version 返回 API `v1` 和 Executor 协议 current/min/max `1.0`；响应均 `no-store` 且带 request ID
- 失败矩阵：覆盖精确路径、GET-only、非版本化别名不存在、405 进入统一错误信封、响应模型与 OpenAPI、协议兼容范围和 console script；本机 8000 已被用户 SSH 隧道占用，因此未终止该进程并选定 8765
- 清理：通过 Ctrl-C 完成真实 lifespan shutdown，Uvicorn 进程退出，复查 8765 无监听；未影响现有 8000 SSH 隧道
- 文档：同步 Backend/根 README、前后端架构固定 local BaseUrl、本路线图状态和当前下一步
- 遗留：数据库就绪状态在 `F1-05` 后接入 Health；`/api/v1/capabilities` 随稳定能力实现，不在本任务返回空壳

### F1-04 PostgreSQL Compose

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建 Compose 契约测试，执行 `uv run pytest tests/integration/test_compose_contract.py -q`，因根目录 `compose.yaml` 和 `.env.example` 不存在产生 5 项失败
- GREEN：18 项 Backend 测试通过、总覆盖率 100%；Compose 契约 5 项通过；`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`、`uv lock --check` 全部通过；真实 PostgreSQL 18.4 双容器均 healthy 并能查询各自 database/user
- 真实边界：使用官方 `postgres:18.4-bookworm`；开发和测试服务具有不同用户、密码、数据库、loopback 端口和数据目录，开发库用命名卷、测试库用 tmpfs；测试时因 5432 被现有 SSH 隧道占用，使用隔离端口 55432/55433
- 失败矩阵：覆盖凭据缺失时 Compose config 失败、无密码默认值、无 trust auth、镜像非 latest、健康检查、端口仅 loopback、开发/测试存储不共享和真实数据库身份隔离
- 清理：隔离项目 `automation-tool-f104-validation` 的两个容器、网络和专用卷已 `down --volumes`；55432/55433 已释放；未终止或修改用户的 5432 SSH 隧道；PostgreSQL 镜像作为全局 Docker 缓存保留
- 文档：新增根 `.env.example`/`compose.yaml`，同步 Backend/根 README、工程结构、本路线图状态和当前下一步
- 遗留：SQLAlchemy async session、Alembic 和数据库连接失败模型归 `F1-05`

### F1-05 SQLAlchemy/Alembic 基线

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建真实迁移、session、Health 和安全配置测试，执行 `uv run pytest tests/integration/test_database_baseline.py tests/unit/control_plane/test_database_errors.py -q`，收集阶段因缺少 `sqlalchemy` 和 `automation_tool.control_plane.bootstrap.database` 失败
- GREEN：24 项 Backend 测试通过、总覆盖率 100%；`uv run ruff format --check .`、`uv run ruff check .`、`uv run mypy`、`uv lock --check` 全部通过；真实 PostgreSQL 空库成功升级到 `20260718_0001`、回滚到 base 后 revision 清空并可再次建立连接
- 真实边界：SQLAlchemy 2.0.51 + asyncpg 0.31.0 + Alembic 1.18.5 连接官方 PostgreSQL 18.4 容器；Health 对真实数据库返回 200，对拒绝连接返回脱敏、可重试的 `503 dependency_unavailable`
- 失败矩阵：覆盖数据库配置缺失、非 async PostgreSQL URL、凭据不回显、连接拒绝、真实连接、空库迁移升级/回滚和 lifespan 释放 engine；唯一冲突、revision CAS、事务业务回滚、连接池耗尽随具体仓储任务补充
- 清理：测试使用随机 loopback 端口和隔离 Compose project；结束后容器、网络、卷和端口均已清理；未影响用户现有 5432 SSH 隧道，PostgreSQL 镜像缓存保留
- 文档：同步根/Backend README、环境变量示例、后端架构和本路线图；生产连接信息不进入 Alembic 配置或仓库
- 遗留：业务表与仓储从 `I2-02` 开始按任务新增；下一项进入 `F1-06` Frontend 工程基线

### F1-06 初始化 Frontend

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建三项前端工程契约测试，执行 `node --test frontend/tests/project-baseline.test.mjs`，因 `frontend/package.json`、`index.html`、React 入口和 Vite 配置不存在产生 3 项预期失败
- GREEN：3 项工程契约测试通过；`pnpm peers check` 无兼容性问题；`pnpm install --frozen-lockfile`、`pnpm lint`、`pnpm typecheck`、`pnpm build` 全部通过；Vite 8.1.5 生产资产构建成功
- 真实边界：在 Node 26.0.0 / pnpm 11.9.0 上锁定 React/ReactDOM 19.2.7、Ant Design 6.5.1、TypeScript 6.0.3；真实 Vite server 只监听 `127.0.0.1:1420`，HTTP 返回桌面 UI 入口
- 失败矩阵：覆盖私有 pnpm package、冻结锁文件、peer 兼容、严格 TypeScript、Lint、构建失败、固定 loopback 监听和 Web 发布入口缺失；本任务不含交互、Tauri、Sidecar、网络请求或 RPA，相关失败归后续任务
- 清理：真实 Vite server 已 Ctrl-C 退出并确认 1420 无监听；`dist/`、`node_modules/` 和工具缓存均被 Git 忽略，依赖缓存保留供后续开发复用
- 文档：新增 `frontend/README.md`，同步根 README 和本路线图；明确 Vite 只用于桌面资产/测试 Harness，`dist/` 不得作为 Web 产品发布
- 遗留：Tauri v2、Capability、CSP 和真实桌面窗口归 `F1-07`；当前初始化文案不是 `F1-08` 工作台页面

### F1-07 初始化 Tauri v2

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建三项 Tauri 工程契约测试，执行 `node --test tests/tauri-baseline.test.mjs`，因 `src-tauri/Cargo.toml`、`tauri.conf.json` 和 Capability 不存在产生 3 项预期失败
- GREEN：6 项前端/Tauri 工程契约测试通过；`pnpm install --frozen-lockfile`、peer 检查、ESLint、严格 TypeScript、`cargo fmt --check`、`cargo test --locked`、Clippy `-D warnings` 和 `pnpm tauri build --debug --no-bundle` 全部通过
- 真实边界：锁定 Tauri CLI 2.11.4、tauri 2.11.5 和 tauri-build 2.6.3；真实 `pnpm tauri dev --no-watch` 启动 macOS 前台进程，CoreGraphics 确认一个屏幕内窗口（内容区约 1254×785）；Vite 只监听 `127.0.0.1:1420`
- 失败矩阵：覆盖 Tauri v2/Cargo 锁定、固定 loopback devUrl、bundled frontendDist、无远程窗口 URL、`withGlobalTauri=false`、显式 main Capability、空权限列表、生产/开发 CSP 和关闭时进程/端口清理；Sidecar、网络桥和业务命令尚不存在
- 真实问题修复：首次启动发现 Tauri `freezePrototype` 与 Ant Design/dayjs 初始化冲突并产生只读属性错误；移除非必需选项后重新启动，持续运行无 WebView 错误，CSP 与零权限 Capability 保持不变
- 清理：真实 Tauri/Vite 均 Ctrl-C 退出，Rust PID 与 1420 端口无残留；窗口截图因系统未授权 Screen Recording 而未生成文件；删除生成器额外产出的 Android/iOS 图标目录，仅保留 SVG 源文件及 macOS/Windows 桌面图标
- 文档：同步根/Frontend README、前端架构和本路线图；占位图标明确不是最终品牌设计，生成 Schema 与 target 均保持 Git 忽略
- 遗留：`F1-08` 实现无登录工作台启动/后端故障诊断；测试 Capability 与桌面 E2E 权限归 `F1-13`，正式签名安装包归 Wave 4

### F1-08 App 无登录启动页

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：引入 Vitest/Testing Library 后先创建 ready、Control Plane unavailable/retry、未知异常脱敏三项组件测试；`pnpm test:unit` 为 3 项失败，现有页面只有“桌面工作台正在初始化”，缺少目标工作台和诊断状态
- GREEN：6 项 Node 工程契约 + 3 项 React 组件测试通过；`pnpm install --frozen-lockfile`、peer 检查、ESLint、严格 TypeScript、Vite build、`cargo test --locked` 和 Clippy `-D warnings` 全部通过
- 真实边界：`agent-browser` 在真实 Vite UI Harness 验证主导航、RPA 工作台、概览与空任务态并保存忽略目录截图；控制台无 JS/Ant Design 错误；真实 `pnpm tauri dev --no-watch` 启动新工作台，CoreGraphics 确认屏幕内 1254×785 内容窗口
- 失败矩阵：覆盖启动检查中、ready、Control Plane unavailable、重试恢复、未知异常详情不泄漏，以及页面没有产品登录/注册按钮；BaseUrl 非法、Rust Transport 失败和真实 Health 返回由 F1-09/F1-10 接入后补充
- QA 修复：首次浏览器检查发现 Ant Design 6 的 `Space.direction` 与 `Tag.bordered` 弃用警告；改用 `orientation`/`variant` 后以全新浏览器会话复查，控制台只剩 Vite 连接与 React DevTools 提示
- 清理：两个 agent-browser 隔离会话、Vite 和真实 Tauri 均已关闭，Rust PID 与 1420 端口无残留；QA 截图仅在根 `.local/`，不进入 Git
- 文档：同步根 README、前端架构和本路线图；明确当前 `StartupCheck` 是 UI 组合缝，不以 WebView 直连冒充 F1-10 正式 Transport
- 遗留：`F1-09` 实现并校验 local/demo BaseUrl Profile；`F1-10` 建立 Transport 到启动状态机的适配缝，真实 Rust 健康调用归 `I2-09`；当前禁用菜单随对应业务任务逐项启用

### F1-09 BaseUrl Profile

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建 local/demo 正常值、规范化和绕过矩阵测试，执行 `pnpm test:unit -- src/schemas/base-url-profile.test.ts`，收集阶段因 `src/schemas/base-url-profile.ts` 不存在失败
- GREEN：6 项 Node 工程契约 + 20 项 Vitest 组件/Schema 测试通过；`pnpm install --frozen-lockfile`、peer 检查、ESLint、严格 TypeScript 和 Vite build 全部通过；Zod 4.4.3 作为直接运行依赖锁定
- 真实边界：使用运行时 WHATWG URL 解析而非字符串前缀判断；local 规范化为精确 `http://127.0.0.1:8765`，demo 规范化为允许列表中的 HTTPS origin；本任务不发起网络请求
- 失败矩阵：拒绝 localhost、错端口、local HTTPS、路径、URL 凭据、demo HTTP、相似/后缀域名、`user@host` 欺骗、8443、路径、query、hash、未知 production Profile 和额外字段；固定错误不泄露私密 URL
- 清理：纯单元/构建任务未启动 App、浏览器或服务，无端口和进程残留；pnpm 缓存与生成 dist 保持 Git 忽略
- 文档：同步根 README、前端架构和本路线图；明确 UI Schema 不是 Rust 信任依据，F1-10 必须在原生 Transport 边界再次校验
- 遗留：demo 的真实允许域名在部署任务确定后通过受控构建配置提供；`F1-10` 实现 Transport 契约、正式 Tauri stub 和测试 Harness 边界

### F1-10 ControlPlaneTransport 契约

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建正式 stub、安全错误、测试 handler/AbortSignal、缺失 handler 和启动适配测试，执行 `pnpm test:unit -- src/api/control-plane/transport.test.ts`，收集阶段因 Tauri/Test Harness/公共 Transport 模块不存在失败
- GREEN：6 项 Node 工程契约 + 25 项 Vitest 测试通过；`pnpm install --frozen-lockfile`、peer 检查、ESLint、严格 TypeScript 和 Vite build 全部通过
- 真实边界：公共接口当前只暴露 `checkHealth` 和 AbortSignal，不接收 baseUrl、任意 URL、任意 operation、Header 或凭据；正式 Tauri 类在 I2-09 前固定返回可重试 unavailable；本任务不发起网络或 IPC
- 失败矩阵：覆盖正式 stub 未接通、固定公开错误不携带 cause、测试 handler 显式委托、Signal 透传、缺失 operation fail closed、健康成功映射 ready 和任意异常映射 unavailable
- 清理：纯单元/构建任务未启动 App、浏览器、服务或端口；dist、测试和依赖缓存均保持 Git 忽略
- 文档：同步根 README、前端架构和本路线图；明确测试 Harness 不是正式实现，正式 Rust 网络桥仍归 I2-09
- 遗留：F1-11 用 OpenAPI 生成 DTO 后扩充强类型 operation；F1-12 静态证明生产构建不包含测试 Harness；I2-09 实现 Rust allowlist、凭据注入和真实健康调用

### F1-11 OpenAPI 导出

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：后端先创建确定性导出/漂移/已提交快照测试，收集阶段因 `bootstrap.openapi` 不存在失败；前端 Node 契约测试因 `contracts/openapi/control-plane.v1.json` 不存在失败
- GREEN：Backend 28 项测试通过、总覆盖率 100%；Frontend 7 项 Node 契约 + 25 项 Vitest 通过；后端 OpenAPI `--check`、前端 `pnpm check:api`、Ruff、Mypy、ESLint、严格 TypeScript、Vite build、uv/pnpm 锁检查全部通过
- 真实边界：FastAPI 导出 OpenAPI 3.1，Health/Version 固定 operationId `getSystemHealth`/`getSystemVersion`；`openapi-typescript 7.13.0` 生成 DTO；两侧 check 均从当前来源重新渲染后逐字比较
- 失败矩阵：覆盖无数据库环境仍可导出、连续渲染确定、输出目录创建、快照缺失/内容漂移、CLI 非零退出、前端快照/生成文件存在和 operationId 稳定；业务错误响应 Schema 随后续具体 API 补充
- 工具链修正：`openapi-typescript 7.13.0` 官方 peer 仅支持 TypeScript 5.x，next 标签也不支持 TS6；为消除真实 peer 冲突，将 TypeScript 从 6.0.3 固定到共同支持的 5.9.3，peer 检查、全量类型与构建均通过
- 清理：前端生成检查只使用 `mkdtemp` 创建的精确临时目录并在 finally 删除；无服务、浏览器、数据库或端口残留；快照与生成 DTO 作为版本化源码提交
- 文档：同步根/Backend/Frontend README、前端架构和本路线图；生成 TypeScript 明确禁止手改
- 遗留：F1-12 建立 Playwright UI Harness 并证明正式构建不包含测试 Adapter；后续每个 API 任务都必须同步快照、DTO 和漂移测试

### F1-12 UI Harness 基线

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建 available、unavailable、flaky 三条 Playwright 测试，执行 `pnpm test:e2e`；因 `harness.html` 不存在被 Vite 回退到正式页面，Harness 标记和两种故障状态缺失，3 项全部按目标失败并生成截图/视频/trace
- GREEN：8 项 Node 工程/契约 + 25 项 Vitest + 3 项 Playwright Chromium 测试通过；冻结安装、peer、OpenAPI 漂移、ESLint、严格 TypeScript、生产构建与 `check:production-boundaries` 全部通过
- 真实边界：Playwright 1.61.1 使用匹配 Chromium 149 在真实 `127.0.0.1:1420` Vite 页面点击重试；浏览器 console/pageerror 均为空；正式 dist 实际只有 `index.html`、一个 CSS 和一个 JS
- 失败矩阵：覆盖 ready 工作台、服务不可用诊断、flaky 首次失败后真实点击重试恢复、无产品登录/注册、浏览器错误捕获、Harness 页面误入生产、测试 runtime/Adapter 标记误入生产；不宣称 Tauri IPC 或 RPA 通过
- 扫描器回归：临时构造干净产物、`harness.html` 污染和 Adapter marker 污染，分别验证通过/失败，避免生产排除检查自身静默失效
- 工具：锁定 `@playwright/test 1.61.1`；Chromium/Headless Shell 149 和 Playwright 私有 FFmpeg 缓存在用户级 `~/Library/Caches/ms-playwright` 供后续复用，不进入 PATH、不替换 Homebrew `ffmpeg-full`
- 清理：Playwright 自动关闭浏览器和 Vite；复查 1420 无监听、无 Chrome Headless/Playwright driver 残留；失败证据位于已忽略 `frontend/test-results/`
- 文档：同步根/Frontend README、前端架构和本路线图，持续声明 Harness 不是 Web 产品、生产入口或原生能力验收
- 遗留：F1-13 建立 Vitest/Playwright/Rust/WebdriverIO 四层统一命令与最小绿测；后续 Feature 的 UI Harness 用例长期保留

### F1-13 Tauri 四层测试基线

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增四层命令、真实 Tauri binary、embedded provider 和生产隔离契约，执行 `node --test tests/tauri-test-layers.test.mjs` 得到 3 项目标失败；再新增 WDIO 标记污染样例，生产扫描器因未拒绝产生 1 项目标失败；首次四层总门禁还真实暴露测试 Capability 放在默认目录会破坏普通 `cargo test`
- GREEN：11 项 Node 契约、25 项 Vitest、3 项 Playwright、2 项普通 Rust、2 项 `desktop-e2e` Rust 和 1 项真实 Tauri/WebdriverIO 测试通过；`pnpm test:layers`、正式生产扫描、OpenAPI 漂移、peer、ESLint、严格 TypeScript、Rustfmt 和两种特性的 Clippy 全部通过
- 真实边界：WebdriverIO embedded provider 启动真实 macOS Tauri App 和 WKWebView 605.1.15，验证“RPA 运营工作台”、无产品登录/注册文案和 Tauri 原生 `main` 窗口标签；本任务不宣称 Control Plane 网络桥、Sidecar、外部浏览器或 RPA 可用
- 安全隔离：WDIO Rust 插件是 `desktop-e2e` 可选依赖，前端桥只由 Vite `desktop-e2e` 测试入口引入；`withGlobalTauri` 与 WDIO Capability 只内联在 `tauri.test.conf.json`，production 仍为 false/仅 `main`；正常 Cargo 一层依赖树没有 WDIO，正式 dist 扫描拒绝 Harness 和 WDIO 标记
- 工具问题：锁定 WebdriverIO 9.29.1、Tauri Service/插件 1.2.0；上游 1.2.0 发布清单引用的 native-utils 2.4.0 缺少其已调用导出，使用 pnpm 官方 override 固定 2.5.0，未修改第三方源码；embedded 成功链路仍会打印外部 driver 和会话后清 mock 两条上游诊断噪声，不安装未使用的 `tauri-driver`
- 失败矩阵：覆盖四层命令漂移、真实 binary/provider、原生窗口查询、无登录 UI、测试前端桥误入生产、测试权限被默认 Cargo 扫描、正常/测试 Rust 特性独立编译、依赖版本不自洽和 App/driver 退出清理
- 清理：WebdriverIO 自动结束 App 和 embedded server；复查 4445 无监听、无 App/WDIO/WebDriver 残留；测试产物、报告和 Rust target 均在忽略目录，生产 `dist/` 已重建覆盖测试资产
- 文档：同步根/Frontend README、前端架构和本路线图，明确四层证据边界、测试构建隔离与上游依赖 workaround
- 遗留：F1-14 将四层命令接入 Backend/Frontend/Rust CI 和 macOS/Windows 桌面骨架；后续每个原生能力任务必须扩充真实 Tauri E2E

### F1-14 CI 基线

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建 CI 契约测试，执行 `node --test tests/ci-baseline.test.mjs`，因两个 workflow 均不存在产生 2 项目标失败；补充 Linux/Tauri 依赖断言后又准确捕获 `libwebkit2gtk-4.1-dev` 缺失
- GREEN：2 项 CI 契约、14 项全部 Node 契约、25 项 Vitest、3 项 Playwright、28 项 Backend 与 4 项 Rust 测试通过，Backend 覆盖率 100%；冻结安装、peer、OpenAPI 漂移、Ruff、Mypy、ESLint、严格 TypeScript、生产边界、Rustfmt 和两种 Cargo 特性 Clippy 全部通过；`actionlint 1.7.12` 对两个 workflow 零错误
- 真实边界：GitHub Hosted `ubuntu-latest` 分别执行 Backend、Frontend、Rust 门禁；`macos-latest` 与 `windows-latest` 均构建无测试驱动的 production debug binary，再运行 embedded WebdriverIO 真实 Tauri 冒烟；工作流不发布、不部署、不上传构建产物
- 跨平台修复：首次 Windows Hosted Runner 在 `pnpm check:api` 暴露 Node 26 不能直接 `spawnSync("pnpm.cmd")` 且失败结果无 `stderr`；改为用 `process.execPath` 直接运行锁定包的官方 CLI，并从包根解析真实入口、提供固定失败消息。第二轮进一步暴露 Git checkout/生成器的 CRLF/LF 差异；新增纯函数测试，把生成、比较和写回统一为 LF，不放宽真实内容漂移。第三轮在真实 Tauri 冒烟阶段暴露 WebView2 与预装 `msedgedriver` 版本不匹配；按 `@wdio/tauri-service` 的 Windows 官方策略开启匹配驱动自动下载，仅写入 Runner 临时缓存，不安装外部 `tauri-driver`、不进入生产包
- 失败矩阵：覆盖工作流缺失、职责混跑、Action 非完整 SHA、可读版本注释缺失、write 权限、secret、发布/部署步骤、Linux/Tauri 系统依赖、桌面矩阵缺平台、fail-fast 误停和生产测试边界未复原；业务失败矩阵不适用于纯 CI 基线
- 工具：通过 Homebrew 全局安装并保留 `actionlint 1.7.12`，其全局依赖 `shellcheck 0.11.0` 同时保留供以后工作流检查；没有创建项目内重复副本
- 清理：本地测试自动关闭 PostgreSQL 隔离容器、Playwright/Vite 与 Rust 测试进程；无端口、容器或桌面 App 残留，缓存和构建产物保持 Git 忽略
- 文档：同步根/Frontend README 和本路线图；不新增重复 CI 文档
- 遗留：正式签名、notarization、安装包上传和云端部署属于后续发布/Demo 任务，不在验证型 CI 中提前实现

### I2-01 稳定资源 ID

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建六类资源 ID 的生成、规范解析、类型隔离、不可变和非法值矩阵测试；执行 `uv run pytest tests/unit/control_plane/domain/test_resource_ids.py -q`，收集阶段因 `ActionId` 等领域类型尚未公开而失败
- GREEN：89 项资源 ID 目标测试、117 项 Backend 全量测试通过，总覆盖率 100%；`uv run ruff format --check .`、`uv run ruff check .` 和严格 `uv run mypy` 全部通过
- 真实边界：Python 3.12.13 领域层使用不可变 UUIDv4 值对象；规范外部文本固定为小写带连字符形式；本任务不创建数据库表、不改变 OpenAPI，也不启动 Control Plane、Executor 或桌面 App
- 失败矩阵：覆盖六类独立类型、随机生成、字符串/UUID/同类型回放、跨类型拒绝、nil、UUIDv1、畸形、大小写、首尾空白、无连字符、花括号、URN、非字符串类型、直接构造绕过、不可变和错误值不回显
- 清理：纯单元与静态检查未启动服务、数据库、容器、浏览器或端口；测试和覆盖缓存保持 Git 忽略
- 文档：同步根/Backend README、后端架构的稳定 ID 规范、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：`connection_id`、message/correlation/idempotency ID 随 I2-10/I2-13 的协议与连接语义建立具体类型；I2-02 使用 `InstallationId` 建表并验证真实 PostgreSQL

### I2-02 Installation 表

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增真实迁移、默认状态、revision CAS、吊销、非法状态和唯一约束测试；执行 `uv run pytest tests/integration/test_installation_schema.py -q`，收集阶段因 `InstallationStatus` 尚未公开而失败
- GREEN：4 项 Installation 真实 PostgreSQL 测试、121 项 Backend 全量测试通过，总覆盖率 100%；迁移升级到 `20260718_0002`、`alembic check` 无漂移、降级到 `20260718_0001` 后表被删除并可重新升级；Ruff 格式/检查与严格 Mypy 全部通过
- 真实边界：SQLAlchemy 2.0.51 + asyncpg 0.31.0 + Alembic 1.18.5 连接官方 PostgreSQL 18.4 容器；真实事务验证 active 默认值、32 字节公钥、revision 从 1 原子递增到 2、旧 revision 更新零命中以及 revoked 状态持久化
- 失败矩阵：数据库拒绝 UUIDv1、31 字节公钥、未知状态、revision 0、active 带吊销时间、revoked 缺吊销时间、更新时间/吊销时间早于创建时间、重复 ID 和重复设备公钥；PostgreSQL 18 的额外 NOT NULL 约束不替代显式业务约束断言
- 清理：每轮测试使用随机 loopback 端口、随机密码和隔离 Compose project；结束后测试容器、网络、卷和端口自动删除，不影响开发库与用户现有 5432 隧道
- 文档：同步根/Backend README、后端架构的数据约束、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：I2-03 建立只可注册的 Demo Bootstrap 模型；Installation 仓储和注册 API 分别随 I2-05/I2-06 接入，不在本任务提前开放业务路由

### I2-03 Demo Bootstrap 模型

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建环境 slug、固定注册 purpose、精确时窗、跨环境、业务 API 拒绝、不可变和直接构造绕过测试；执行 `uv run pytest tests/unit/control_plane/domain/test_demo_bootstrap.py -q`，收集阶段因 `MAX_DEMO_BOOTSTRAP_LIFETIME` 等领域类型尚未公开而失败
- GREEN：34 项 Bootstrap 目标测试、155 项 Backend 全量测试通过，总覆盖率 100%；Ruff 格式/检查、严格 Mypy、uv 锁和 OpenAPI 漂移检查全部通过
- 真实边界：Python 3.12.13 纯领域模型；`DemoEnvironmentId` 是最长 64 字符的小写 slug，grant 采用 `[not_before, expires_at)` UTC 半开时窗、最长 7 天，唯一强类型 purpose 为 `installation.register`；本任务不生成或保存真实 bootstrap token
- 失败矩阵：拒绝空/大小写/下划线/路径/首尾连字符/超长/非字符串环境，普通字符串 purpose（包括同文案 `installation.register`）、业务操作、跨环境、未生效、到期、naive 时间、零/负/超长时窗、直接构造类型绕过和冻结对象篡改；固定错误不回显外部值
- 清理：纯单元与静态检查不启动服务、数据库、App 或浏览器；全量回归的隔离 PostgreSQL 容器、网络、卷和随机端口已自动清理
- 文档：同步根/Backend README、后端架构的 Bootstrap 能力边界、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：I2-05 在 challenge/response 注册 API 中承载并验证签名 claims；既有 bootstrap 只保留受控注册与测试用途，不再规划客户设备审批批次。客户 Demo 的账号 Session Secret、账号初始化、设备归属和吊销由 U9-01～U9-06 与 C10-05/C10-06 实现，本任务不提前创建第二套 token 系统

### I2-04 设备密钥生成

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交（PR #2）
- RED：先创建 Rust/React 静态边界契约和设备身份行为测试；`node --test tests/device-identity-boundary.test.mjs` 因 `device_identity.rs` 不存在而失败，随后 `cargo test --manifest-path src-tauri/Cargo.toml --lib device_identity::tests --no-run` 因设备身份类型和安全存储适配器尚不存在而编译失败
- GREEN：6 项 macOS 设备身份目标测试、8 项默认 Rust 测试、8 项 `desktop-e2e` Rust 测试、16 项 Node 契约、25 项 Vitest、3 项 Playwright 和 1 项真实 macOS Tauri/WebdriverIO 冒烟通过；Rustfmt、默认/测试特性 Clippy `-D warnings`、ESLint、严格 TypeScript、生产资产构建与边界扫描全部通过；GitHub Actions 运行 `29620527399` 的 Backend/Frontend/Linux Rust 和 `29620527397` 的 macOS/Windows 桌面矩阵五路通过
- 真实边界：本任务当时完成了 macOS/Windows 平台存储和 production desktop binary 冒烟；I2-08 根据后续明确产品决策，将持久化实现统一迁移为 Tauri `app_data_dir` 下的 App 私有文件，并以新的正式 App 启动、平台文件语义和凭据生命周期证据取代原存储实现，I2-04 的密钥生成与 React/IPC 隔离边界继续保留
- 失败矩阵：覆盖首次生成、既有密钥复用、0/31/33/64 字节损坏拒绝且不轮换、存储读写拒绝、系统随机源失败、不同生成结果和固定不泄密错误；静态扫描拒绝私钥进入 React、Tauri Command、序列化和普通配置。网络、数据库、业务 API、取消、超时和结果不确定对纯本机首启密钥任务不适用
- 清理：I2-08 使用隔离 App 标识和临时 App 数据目录替代旧平台存储测试对象；`desktop-e2e` 身份仍只驻留测试 App 进程，测试不启动后端、数据库、浏览器或云端资源
- 文档：同步根/Frontend README、前端架构、工程结构、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：I2-05 使用公钥完成 Installation 注册签名校验；I2-08 统一迁移私钥存储并增加可轮换、可删除的长期设备凭据，不把服务端签发职责混入本任务

### I2-05 Installation 注册 API

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建 Bootstrap token 签名 claims 单测和真实 PostgreSQL 注册 API 测试；`uv run pytest tests/unit/control_plane/test_bootstrap_tokens.py -q` 因 `infrastructure.security` 不存在而收集失败，`uv run pytest tests/integration/test_installation_registration_api.py -q` 因 `application.registration` 不存在而收集失败；实现路由后 OpenAPI `--check` 按预期报告快照过期
- GREEN：77 项 I2-05 目标契约/单元/真实 PostgreSQL 测试通过，Backend 全量 232 项通过，语句与分支覆盖率均 100%；uv 锁、Ruff 格式/检查、严格 Mypy、Alembic check、OpenAPI/前端 DTO 双向漂移检查全部通过；Frontend 16 项 Node 契约、25 项 Vitest、ESLint、严格 TypeScript、生产构建与边界扫描通过
- 真实边界：`cryptography 49.0.0` 验证离线 Ed25519 签名 `atb1` claims；官方 PostgreSQL 18.4 容器执行迁移 `20260718_0003`、真实 FastAPI 请求、行锁事务、并发双提交、唯一冲突、升级到 head、降级到 `0002` 并重新升级。服务只配置验证公钥，未生成或保存 bootstrap 签发私钥
- 失败矩阵：覆盖 token 空/超长/Unicode/非 canonical base64url、错误 signer、篡改、错误版本/用途/字段/类型、重复 JSON key、非法环境、未生效/过期/超 7 天；API 覆盖缺失 bearer、未配置、非法 UUID/公钥/签名、跨环境、另一份有效 bootstrap、未知/过期/已消费 challenge、篡改 payload、错误设备签名、重复设备公钥，以及两个并发完成只能一个成功；固定错误不回显 token、公钥配置或底层异常
- 清理：每轮真实数据库测试使用随机 loopback 端口、随机密码和独立 Compose project，结束后删除测试容器、网络和卷；最终 `docker ps` 无 `automation-tool-pytest-*` 残留；未启动 App、浏览器、Executor 或云资源
- 文档：同步根/Backend README、后端架构、OpenAPI 3.1 快照、生成 TypeScript DTO、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：I2-06 在注册成功后签发版本化、可撤销、可轮换且最小 scope 的设备凭据；U9-05 再将 Installation 绑定已登录产品账号，C10-06 负责 Demo 账号初始化、停用和审计，本任务不冒充完整账号体系

### I2-06 设备凭据签发

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 256-bit opaque 凭据、canonical 解析、摘要化、轮换/吊销编排单测，执行 `uv run pytest tests/unit/control_plane/test_device_credentials.py -q` 因 `application.device_credentials` 不存在而收集失败；随后真实 Schema 测试因 `device_credentials` 未导出失败，注册原子签发测试因服务不接受 `credential_factory` 失败，生命周期仓储测试因适配器模块不存在失败，HTTP 契约因路由和依赖注入不存在产生 4 项失败
- GREEN：64 项 I2-06 目标契约/单元/真实 PostgreSQL 测试通过，Backend 全量 265 项通过，语句与分支覆盖率均 100%；uv 锁、Ruff 格式/检查、严格 Mypy、Alembic check、OpenAPI/前端 DTO 双向漂移检查全部通过；Frontend 16 项 Node 契约、25 项 Vitest、ESLint、严格 TypeScript、生产构建与边界扫描通过
- 真实边界：官方 PostgreSQL 18.4 容器执行迁移 `20260718_0004`、真实 FastAPI 请求、摘要认证、Installation/credential 行锁、轮换、吊销、并发双轮换、升级到 head、降级到 `0003` 并重新升级；初始 v1 凭据和 Installation/challenge 消费在同一事务提交，服务端没有新增签名私钥
- 安全模型：`atdc1.<credential-id>.<256-bit-secret>` 明文只在初始签发或轮换成功时返回一次；数据库只保存 32 字节 SHA-256 摘要、正数版本、精确 `device.session.exchange` scope 和状态历史。每个 Installation 只有一个 active 版本；生命周期操作按固定顺序锁 Installation 再锁凭据，摘要使用常量时间比较
- 失败矩阵：覆盖空/超长/非 canonical/非法 UUID/错误版本/错误随机源、未知 ID、同 ID 错误秘密、旧版本、重复吊销、已吊销 Installation、非法 scope/status/version/摘要长度/时间/UUID、重复版本/摘要、同 Installation 双 active，以及两个并发轮换只能一个成功；认证错误固定且不回显 bearer，注册重放不再次返回凭据
- 清理：每轮真实数据库测试使用随机 loopback 端口、随机密码和独立 Compose project，结束后删除测试容器、网络和卷；未启动 App、浏览器、Executor 或云资源，构建与覆盖率产物保持 Git 忽略
- 文档：同步根/Backend README、后端架构、工程结构、OpenAPI 3.1 快照、生成 TypeScript DTO、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：I2-07 使用该长期凭据的唯一 scope 换取短期 Session；I2-08 再把长期凭据的读写、原子替换和删除接入 Rust 管理的 App 私有存储；当前 React 和普通配置仍不接触凭据

### I2-07 短期设备 Session

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 `atds1` opaque Session、两种精确 capability、5 分钟寿命与 30 秒时钟偏差单测，执行 `uv run pytest tests/unit/control_plane/test_device_sessions.py -q` 因 `application.device_sessions` 不存在而收集失败；随后 Schema 测试因 `device_sessions` 未导出失败，生命周期测试因 Session 仓储模块不存在失败，HTTP 契约因路由和依赖注入不存在产生 5 项失败
- GREEN：43 项 I2-07 定向契约/单元/真实 PostgreSQL 测试通过，Backend 全量 311 项通过，语句与分支覆盖率均 100%；Ruff 格式/检查、严格 Mypy、Alembic check、OpenAPI/前端 DTO 双向漂移检查全部通过；Frontend 16 项 Node 契约、25 项 Vitest、3 项 Playwright、ESLint、严格 TypeScript、生产构建与边界扫描通过
- 真实边界：官方 PostgreSQL 18.4 容器执行迁移 `20260718_0005`、真实 FastAPI `POST /api/v1/device-sessions` 请求、摘要认证、Installation/credential/session 行锁、父凭据轮换与吊销联动、升级到 head、降级到 `0004` 并重新升级；服务端仍不持有新增签名私钥，前端 DTO 从权威 OpenAPI 机械生成
- 安全模型：长期 `atdc1` bearer 只能换取 `atds1.<session-id>.<256-bit-secret>`，数据库仅保存 SHA-256 摘要和 Installation/credential ID/version 精确复合绑定。每张票只具备 `app.control-plane` 或 `executor.connect` 之一，采用 `[issued_at - 30s, issued_at + 5m)` 半开时窗；认证固定按 Installation、父凭据、Session 顺序锁定并逐次确认仍 active
- 失败矩阵：覆盖空/超长/非 canonical/非法 UUID/错误版本/错误随机源、未知 ID、同 ID 错误秘密、能力混用、未生效前 1 微秒、到期精确边界、非法数据库绑定/能力/摘要/时间、父凭据轮换或撤销、Installation 撤销以及认证中 Session 被并发清理；所有 bearer 错误固定且不回显输入
- 清理：每轮真实数据库测试使用随机 loopback 端口、随机密码和独立 Compose project，结束后删除测试容器、网络和卷；UI 测试 Vite 进程由 Playwright 回收，未启动真实 App、Executor、浏览器运营 Profile 或云资源
- 文档：同步根/Backend README、后端架构、工程结构、OpenAPI 3.1 快照、生成 TypeScript DTO、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：I2-08 将长期设备凭据接入 Rust 管理的 App 私有存储；I2-09/I2-10 再建立跨进程协议与兼容矩阵；I2-13 才让 Executor WebSocket 使用 `executor.connect` Session，业务 API 授权随对应任务接入 `app.control-plane`

### I2-08 Rust App 私有存储

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增长期设备凭据静态边界和 Rust 行为契约；Node 因 `secure_store.rs` 不存在而失败，Rust 因共享存储和 `DeviceCredentialVault` 类型不存在而编译失败。用户明确改为 App 私有目录后，再把契约收紧为必须使用 `app_data_dir`、彻底移除 `keyring`，此时 Node 因依赖和入口仍是系统存储而失败，Rust 因 `AppDataSecretStore` 不存在而编译失败
- GREEN：默认和 `desktop-e2e` 两种 Rust 配置各通过 19 项库测试、2 项配置集成测试和文档测试；warnings-as-errors 全目标编译、Rustfmt、18 项 Node 契约、25 项 Vitest、3 项 Playwright、ESLint、严格 TypeScript、OpenAPI 漂移、生产构建/边界扫描和 1 项真实 macOS Tauri/WebdriverIO 冒烟通过。首轮 GitHub Quality gates 在 Linux 暴露注册仓储已消费 challenge 分支的覆盖率缺口后，立即提取确定性状态判断并补 4 项单元用例；后端全量增至 315 项，语句/分支覆盖率恢复 100%，Ruff 与严格 Mypy 同步通过；后续提交继续由默认/测试特性 Clippy 与 macOS/Windows 桌面矩阵复核
- 真实边界：使用正式、非 `desktop-e2e` Tauri 入口和隔离 App 标识启动真实 macOS App，`app.path().app_data_dir()` 实际创建私有目录与 `device-identity-ed25519-v1`；目录为 `0700`、文件为 `0600` 且精确 32 字节。停止并第二次启动后文件修改时间保持不变，证明既有私钥复用且未静默轮换；未签发凭据时没有空 `device-credential-v1`，全程未调用系统钥匙串、未出现授权提示。长期凭据的真实文件往返、原子替换和幂等删除由同一 Rust 生产存储适配器验证；I2-09 再从真实 App 网络响应消费该仓库
- 安全模型：私钥和长期凭据分别使用两个固定文件名，最大 Secret 为 4096 字节；目录拒绝符号链接和非目录，Secret 拒绝符号链接、非普通文件和 Unix 组/其他用户权限。写入使用同目录 `create_new` 临时文件、`sync_all`、原子 `rename` 和 Unix 目录同步；缓冲区使用 `Zeroizing`，错误固定且不回显路径、凭据或底层异常。React、Tauri Command、序列化和 `localStorage` 均没有读写面，Cargo 锁文件不再包含 `keyring` 及其 macOS/Windows 平台依赖
- 失败矩阵：覆盖缺项、首次生成、既有私钥复用、凭据写入/替换/删除、重复删除、非法前缀/UUID 版本/编码/长度/Unicode、损坏 UTF-8、尾随内容、空值、超限输入和超限已存内容、存储读写删拒绝、目录/文件符号链接、非目录/非普通文件、过宽权限、随机源失败和固定不泄密错误；正式 App 启动与重启补齐真实入口，网络、数据库和 API 调用统一留给 I2-09 的纵向验收
- 清理：正式 App 验收使用启动前确认不存在的隔离标识目录；验证后停止 App/Vite，确认 loopback 端口无监听，再精确删除该测试目录和 Git 忽略的临时配置。未触碰正式 App 数据、后端、数据库、浏览器运营 Profile 或云资源
- 文档：同步项目规则、根/Frontend README、竞品分析中的我方方案、前后端架构、工程结构、本路线图快照、任务状态、完成记录和当前下一步；明确密钥与长期凭据只存 App 私有目录且不使用系统钥匙串
- 遗留：I2-09 通过正式 Rust 网络桥把注册响应、凭据轮换/吊销和 Session 换票接到该仓库，并由真实测试版 Tauri App 请求真实隔离后端；此前 Health/注册/凭据/Session 的服务端证据不能替代这次纵向验收

### I2-09 Rust 网络桥

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 Rust/React 网络边界契约，因 `reqwest`、封闭 operation、正式 Tauri invoke 和响应解析器不存在而失败；随后真实 Tauri 纵向契约因专用配置、WDIO 用例和隔离编排脚本不存在而失败。首轮真实 App 又准确暴露生产前端未装载 WDIO 测试插件、响应凭据未做 canonical 校验、Session 时间倒序仍被接受，以及通用 WDIO glob 误运行专用用例，逐项补失败测试后修复
- GREEN：Backend 全量 315 项通过且语句/分支覆盖率 100%，uv lock、Ruff 和严格 Mypy 通过；Frontend 23 项 Node 契约、29 项 Vitest、3 项 Playwright、OpenAPI 漂移、ESLint、严格 TypeScript 和生产资产边界通过；默认、`desktop-e2e`、`control-plane-e2e` 三种 Rust 配置各通过 29 项库测试和 2 项配置测试，Rustfmt 与三种配置 warnings-as-errors 编译通过；通用后台 Tauri 冒烟和专用真实后端纵向用例各 1 项通过
- 正式链路：生产 `main.tsx` 组合真实 `TauriControlPlaneTransport`，经 Tauri invoke 调用正式 Rust Health Command；Rust `reqwest` 客户端使用固定 `http://127.0.0.1:8765` origin 和封闭 allowlist，覆盖 Health、注册 challenge/complete、凭据轮换/吊销和两种 Session 换票，不接受 React 传入 URL、路径、Header 或 bearer。请求禁止系统代理与重定向，连接/总超时为 3/10 秒，响应体上限 64 KiB，并验证关联 ID、状态、JSON content type、`no-store`、规范 UUIDv4、UTC 时间和 opaque 格式
- 凭据边界：注册签名从正式 `ProductionDeviceIdentity` 按需读取 App 私有身份；初始/轮换 `atdc1` 只进入 Rust `DeviceCredentialVault` 并原子替换，吊销后删除；短期 `atds1` 只进入 Rust `Zeroizing` 缓冲。Bootstrap、设备私钥、长期凭据和 Session 都没有 React、序列化或通用 IPC 读写面，错误只暴露固定类别且不携带 cause/输入
- 生产同路径验收：`uv run python ../scripts/run_i2_09_acceptance.py` 以随机密码/端口启动真实 PostgreSQL 18.4、完整 Alembic 链和真实 FastAPI，再由测试版真实 Tauri/WKWebView 从正式 React 入口完成 Health → 注册 → `app.control-plane` Session → 轮换 → `executor.connect` Session → 吊销；最终核对 App 身份公钥、challenge 已消费、v1/v2 凭据为 rotated/revoked、两张 Session 已撤销且 App 私有目录不再保留长期凭据
- 后台测试：通用和专用 Tauri 测试配置都把 `main` 窗口设为 `visible=false`，验收仅在后台运行，不弹窗、不抢占前台；production 配置仍正常可见。embedded provider 成功用例仍会输出其已记录的外部 `tauri-driver` 误检查、隐藏窗口聚焦和会话后空 mock 清理诊断噪声，不安装未使用驱动，也不影响真实 WKWebView 结果
- 清理：验收结束确认 8765/1420 无监听、无 Tauri/uvicorn 进程、无 I2-09 容器，并精确删除隔离 Compose 网络/卷和 `com.aventador.automationtool.i209acceptance` App 数据目录；未触碰正式 App 数据、开发库、浏览器 Profile 或云资源
- 文档与 CI：同步项目后台测试规则、根/Frontend/Backend README、前后端架构、工程结构和本台账；Ubuntu Rust 门禁增加 `desktop-e2e` 与 `control-plane-e2e` 的 test/Clippy，仍只读、不部署、不读取 secret
- 遗留：I2-10 开始定义 Executor v1 Envelope；I2-13 才实现 Executor WebSocket 认证，SSE/任务业务 API 和 Demo HTTPS 签名 Profile 随对应台账任务接入，不能把本任务的 local Health/认证链路外推成 RPA 已可用

### I2-10 Executor v1 Envelope

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先创建 64 项目标测试并把任务状态置为 RED，执行 `uv run pytest tests/unit/protocol/test_executor_envelope.py -q` 因 `automation_tool.protocol.executor_envelope` 不存在而收集失败；GREEN 后继续补敏感字段片段/赋值文本/通用 data URI 用例得到 3 项准确失败，再补异常链断言准确证明固定错误仍挂有含私密输入的 Pydantic cause
- GREEN：79 项 Envelope 目标测试通过，目标模块语句/分支覆盖率均 100%；Backend 全量增至 394 项且语句/分支覆盖率保持 100%，uv lock、Ruff 格式/检查与严格 Mypy 通过。正式类型入口、判别 Schema 结构、显式版本、所有已声明 message type、用途隔离 ID、时间/幂等/序号、任务作用域、payload 资源与隐私边界、重复 JSON key、wire 大小和固定错误均有确定性回归
- 协议模型：以 `message_type` 判别 `ExecutorLifecycleEnvelope`、`TaskCommandEnvelope`、`TaskCommandResultEnvelope`、`TaskEventEnvelope` 四类、共 24 种 v1 消息。生命周期只绑定 installation/executor，不伪造 task；其余任务消息强制同时绑定 task/attempt。所有模型 frozen、`extra=forbid`，`protocol_version` 无默认值且精确为 `1.0`
- 边界类型：message/correlation/installation/executor/task/attempt 使用不同运行时类型的 canonical 小写 RFC 4122 UUIDv4；RFC3339 时间必须显式 UTC 且 `deadline_at > sent_at`；幂等键只允许 1～128 字符规范字符集；I2-10 初版 sequence 为 `1..2^63-1`，I2-12 的三语言 RED 证明 TypeScript 无法精确表达后统一收紧为 `1..2^53-1` strict safe integer，不接受 bool、float 或字符串强转
- 资源与隐私：正式解析器只接受最大 32 KiB 的 UTF-8 JSON object，拒绝重复 key；payload 最大 16 KiB、深度 8、单集合 64 项、字符串 4096 字符，递归拒绝 Cookie/Token/密码/私钥/凭据字段与赋值文本、Bearer、私有绝对路径、`file://`、inline data URI、控制/双向字符、NaN/Infinity。解析失败只有固定 `Invalid Executor protocol message`，不回显输入，也不保留底层 cause/context
- 旧项目取舍：只吸收显式版本、deadline、幂等、判别联合和安全文本的通用经验；未迁移旧 `tenant_id`、Core capability/governance、social-operations 扩展、同步 stdio request/response 或其领域 payload。stdin 仍只留给 E4-02/E4-06 一次性本机 bootstrap，正式消息通道归 I2-13 WebSocket
- 生产同路径：本任务只建立两进程将共同调用的纯协议解析入口，没有网络、数据库、App UI、Executor 进程或外部副作用，因此真实 Tauri/PostgreSQL/RPA 验收不适用；I2-11 用公共 fixtures 固化跨语言 wire 结论，I2-12 由 Python/Rust/TypeScript 回放同一 fixtures，I2-13 才通过真实 WebSocket 使用该入口
- 文档：同步根/Backend README、后端架构、工程结构、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：generic payload 容器只承担 Envelope 级资源/隐私下限，具体消息 payload 必须随 T3/E4 对应任务改为严格类型；I2-11 先导出 JSON Schema 与有效/无效 fixtures，不能把 generic payload 当成长期业务契约

### I2-11 协议 Schema/Fixtures

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 Schema 漂移、Draft 2020-12、fixture 清单、valid round-trip、invalid 固定拒绝和结构/语义分层测试，执行 `uv run pytest tests/contract/test_executor_protocol_schema.py -q` 因 `automation_tool.protocol.schema` 不存在而收集失败；首次生成后 49/50 通过，准确暴露基础 `jsonschema` 环境没有启用 `date-time` format checker、无时区字符串被标准 Schema 放行
- GREEN：51 项 Schema/fixture 目标契约通过，Schema 导出模块语句/分支覆盖率 100%；Backend 全量增至 445 项且语句/分支覆盖率保持 100%，uv lock、双 Schema 漂移、Ruff 和严格 Mypy 通过；Frontend 23 项 Node 契约通过。没有安装宽泛 format extras，而是把当前协议要求的 UTC RFC3339 直接编码成跨语言 pattern，使无时区与非 UTC 时间都成为结构失败，deadline 先后仍由语义解析器处理
- 权威生成：`ExecutorMessage.model_json_schema()` 是唯一手写来源；确定性导出补充 Draft 2020-12 `$schema`、固定 `$id`、UTC pattern、payload `maxProperties`、wire/payload 五项资源上限和六条 `x-semantic-validation-required`。`automation-tool-export-executor-schema` 支持 write/check，缺失或逐字漂移均固定失败，CLI 错误不回显目标路径
- Fixtures：I2-11 完成时 `contracts/fixtures/executor-v1` 精确包含 5 个 valid 和 22 个 invalid wire 文件，覆盖 lifecycle、task command、command result、event，以及版本、枚举/字段、UUID、幂等键、序号、任务作用域、UTC/deadline、重复 key、隐私与资源边界。I2-12 的跨语言 RED 又增加安全整数、微秒 deadline 和 negative-zero UTC 边界，当前事实源为 6 valid、25 invalid
- 双层结论：I2-11 初始 13 个结构层、9 个语义层 invalid；I2-12 扩展后为 15 个结构层、10 个语义层。结构层必须被标准 Draft 2020-12 validator 和正式 parser 同时拒绝；README 登记的语义层可以通过标准 Schema，但必须由正式 parser 拒绝，避免其他语言误把 Schema 通过当成完整验收
- 工具与 CI：新增锁定 `jsonschema 4.26.0` 和匹配的 `types-jsonschema` 开发依赖，未依赖偶然传递安装；GitHub Backend gate 在 pytest 前执行 Schema `--check`，前端 CI 契约会阻止该门禁被删除。依赖只进入开发组，不进入正式运行依赖
- 生产同路径：本任务产物是进程双方共同消费的静态 wire 合约，没有 App、网络、数据库、Executor 进程或外部副作用；正式完成证据是权威 Pydantic → 生成 Schema → 标准 validator/正式 parser 回放公共文件。随后 I2-12 已建立 Rust/TypeScript 正式解析实现并对同一 fixtures 给出一致结论
- 文档：同步根/Backend README、前后端契约结构、后端架构、本路线图快照、任务状态、完成记录和当前下一步
- 后续：I2-12 已为 Rust/TypeScript 实现同一结构和十项语义样例；I2-13 通过真实 Executor WebSocket 使用这些模型。具体任务 payload 仍必须随 T3/E4 任务收紧为判别类型

### I2-12 Rust/TS 协议一致性

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先把跨语言安全序号 `2^53` 加入 Python 单元、Schema 和公共 invalid fixture，三条 Python 入口均错误接受，证明 I2-10 的 `2^63-1` 上限不能被 TypeScript `number` 无损表达；随后新增 TypeScript/Rust 公共 fixture 契约，分别因 `executor-envelope` 和 `executor_protocol` 正式模块不存在而失败。实现过程中再用共享微秒 deadline 与 `-00:00` 样例锁定 JS 毫秒截断和非 canonical UTC 差异
- GREEN：Python 目标 140 项、TypeScript 目标 32 项、Rust 共享 fixture 3 项全部通过；Backend 全量 455 项且语句/分支覆盖率 100%，Ruff、严格 Mypy、OpenAPI/Executor Schema 漂移通过；Frontend 23 项 Node 契约、61 项 Vitest、ESLint、严格 TypeScript、正式生产构建与边界扫描通过；默认、`desktop-e2e`、`control-plane-e2e` 三种 Rust 配置测试通过，Rustfmt 与三种 all-target Clippy `-D warnings` 通过
- 三端契约：权威 Python Pydantic、TypeScript Zod 判别联合、Rust `serde(deny_unknown_fields)` DTO 精确支持同一 24 种 message type 和四类 envelope。`contracts/fixtures/executor-v1` 当前精确包含 6 个 valid、25 个 invalid 原始 wire；15 个结构层样例由 Schema/三端 parser 同拒绝，10 个语义层样例由三端正式 parser 同拒绝
- 一致性修正：sequence 统一为 `1..2^53-1` strict safe integer；时间只接受 canonical UTC `Z`/`+00:00`、最多 6 位小数并做微秒级 deadline 比较，不接受 `-00:00`；三端都拒绝重复 key、未知字段、任务作用域混淆和非 object payload。TypeScript 在 `JSON.parse` 前递归检测重复 key，Rust 使用递归唯一 key Visitor，避免解析库默认覆盖旧值
- 失败矩阵：覆盖缺失/错误版本、未知 message/字段、非法与跨用途 ID、非 UTC/无时区/negative-zero/相等或倒序 deadline、非法幂等键、零/类型错误/不安全序号、lifecycle/task scope 混淆、重复 key、NaN、深度/集合/字符串/wire 大小、Cookie/Token/凭据字段与赋值、私有路径、inline data URI、控制/双向字符。Python、Rust、TypeScript 失败均收敛为固定 `Invalid Executor protocol message`，不挂 cause、不回显 wire
- 安全边界：React 只新增协议拒绝策略，不新增设备私钥、长期凭据、系统钥匙串或 Tauri Command 表面；既有静态边界对协议 denylist 中唯一 `private_key` 字面量做精确验证后，仍扫描该解析器其余内容及全部 React 源。Rust parser 不读取 App 私有文件，也不扩大 Capability/CSP
- 生产同路径：本任务交付的是后续 WebSocket 两端直接调用的正式纯解析入口，没有网络、数据库、App UI、Executor 进程或外部副作用，因此不启动 Tauri App，也不以 Mock/Harness 冒充跨端验收；I2-13 必须让真实受认证 WebSocket 收发直接经过 Python/Rust parser，TypeScript 只消费经过边界验证的公开投影
- 清理：没有启动 App、服务、容器、浏览器或桌面窗口，无测试进程和临时业务数据遗留；全量门禁两次准确复现既有 Rust 测试仅靠 macOS 时间戳生成临时目录、并行用例同名碰撞的问题，保留原工具并加入进程内原子序号后连续回归，未改产品存储逻辑
- 文档：同步根/Backend/Frontend README、前后端架构、fixture 说明、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：generic payload 仍只是 Envelope 级安全下限，具体 payload 随 T3/E4 收紧；I2-13 接入真实 Executor WebSocket 认证、版本绑定、旧连接和吊销关闭，不在本任务提前建设连接生命周期

### I2-13 Executor WebSocket 认证

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 Executor 连接应用服务单元测试，执行目标用例因正式模块不存在而在收集阶段失败；随后补充 WebSocket 路由契约与真实 PostgreSQL 生命周期测试。首轮真实 PostgreSQL 用例准确暴露测试夹具把凭据创建时间设在未来；修正为稳定历史时钟后通过。首轮真实 Uvicorn 验收又暴露 TestClient 未发现的拒绝握手重复 `Content-Length`，标准客户端按非法 HTTP 拒绝；删除应用层自动长度并固定 Uvicorn SansIO 后真实网络通过
- GREEN：7 项连接应用服务单元、28 项 WebSocket 路由契约、1 项真实 PostgreSQL 生命周期测试通过；Backend 全量 491 项且语句/分支覆盖率 100%，uv lock、Ruff、严格 Mypy、OpenAPI 与 Executor Schema 漂移全部通过。Frontend 23 项 Node 契约、61 项 Vitest、ESLint、严格 TypeScript、API 漂移与生产构建边界通过；默认、`desktop-e2e`、`control-plane-e2e` 三种 Rust 配置各通过 29 项库测试、3 项协议 fixture 和 2 项配置测试，Rustfmt 与三种 all-target Clippy `-D warnings` 通过。WebSocket 路由不进入 REST OpenAPI，协议 Schema 无变化
- 认证与绑定：`WS /api/v1/executors/connect` 只接受唯一 `automation-tool.executor.v1` 子协议与 `executor.connect` Session；认证成功后立即从 ASGI scope 擦除 Authorization Header 并删除局部明文，只保留摘要形式的重认证材料。第一帧必须在 5 秒内通过正式 `parse_executor_message` 成为 `executor.hello`，并绑定 Session 的 Installation、声明 Executor、协议/Executor 版本、macOS/Windows、arm64/x86_64 和独立规范 UUIDv4 `ExecutorConnectionId`
- 生命周期：绑定后当前阶段只接受同一 Installation/Executor 的 `executor.heartbeat`，重复 Hello、任务消息、身份切换、二进制和畸形 wire 均拒绝。服务默认每秒重新从 PostgreSQL 验证 Session、父凭据版本和 Installation；轮换、吊销、过期或绑定变化会关闭在线连接，旧 Session 不能重新升级
- 传输与错误：Uvicorn 本地入口和生产同路径脚本固定 `websockets-sansio`，传输层与正式 parser 共用 32 KiB 上限。握手失败使用 403/503 + `no-store`；已升级连接使用固定 4401/4403/4406/4408/1011 和固定安全原因。未知内部异常不反射 bearer、wire、数据库细节或异常链
- 生产同路径验收：`uv run python ../scripts/run_i2_13_acceptance.py` 后台启动随机端口上的真实 PostgreSQL 18.4、完整 Alembic 链和真实 Uvicorn；经正式 REST 换取 Session，再由标准 WebSocket 客户端验证错误子协议 403、超 32 KiB 帧 1009、Installation 冒充 4403、合法 Hello/heartbeat、真实 REST 凭据吊销导致在线 4401，以及旧 Session 重连 403；最终核对凭据和 Session 均已撤销
- App 测试边界：本任务是服务器 WebSocket 入口，正式调用方式就是网络升级与帧收发，因此不启动 Tauri App、不以进程内 TestClient 代替真实网络；E4-02/E4-12 再由正式 Local Executor 进程复用同一入口完成两端纵向验收。后续任何必须启动 App 的自动化仍只用 `visible=false` 测试配置在后台运行
- 清理：真实验收结束显式终止 Uvicorn、删除隔离 PostgreSQL 容器/网络/卷并确认随机 Control Plane/数据库端口无监听；无 App、浏览器、WebDriver、外部平台动作或本地业务凭据遗留
- 文档：同步根/Backend README、后端架构、工程结构、本路线图快照、任务状态、完成记录和当前下一步；没有新增重复规划文档
- 遗留：I2-14 收口 Installation 吊销对 App API、新任务和在线 Executor 的统一行为；T3-08 建立连接 Registry、在线投影与单实例旧连接替换；E4-02/E4-12 实现正式 Local Executor 出站连接与端到端任务消息，不在认证入口提前维护任务状态

### I2-14 安装实例吊销闭环

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 Installation 吊销应用服务、运维 CLI、App 访问 API 与真实 PostgreSQL 生命周期测试，目标测试因 `installation_revocations` 正式模块不存在而在收集阶段失败；再为 Rust allowlist/严格 DTO、TypeScript 原生错误边界、吊销启动态和 UI Harness 补失败测试，分别因 operation、错误类别和 revoked 状态尚不存在而失败
- GREEN：Backend 全量 515 项且语句/分支覆盖率 100%，uv lock、Ruff、严格 Mypy 与 OpenAPI 漂移通过；Frontend 26 项 Node 契约、63 项 Vitest、4 项 Playwright、ESLint、严格 TypeScript、API 漂移和生产构建边界通过；默认、`desktop-e2e`、`control-plane-e2e` 三套 Rust 配置通过 30 项库测试、3 项协议 fixture 和 3 项配置测试，Rustfmt 与 all-target Clippy `-D warnings` 通过
- 原子吊销：服务端运维命令 `automation-tool-revoke-installation --installation-id ... --expected-revision ...` 只依赖服务器数据库权限；事务按 Installation 锁与 revision CAS 把 Installation 置为 revoked/revision+1，同时吊销 active 长期凭据与全部未吊销 Session。未知、重复、过期 revision 和并发竞争共享固定失败且不会回显目标；并发只有一个赢家
- App 与业务守卫：新增 `GET /api/v1/installations/current`，只接受精确 `app.control-plane` Session；可复用依赖 `require_current_installation_access` 返回强类型 Installation scope，后续所有任务业务路由必须复用。T3-06 已显式依赖 I2-14，因此在守卫前不能开工；未注册 App 仍可直接打开工作台，已保存凭据但服务端 401 时由 Rust 固定映射为 `installation_access_denied`
- UI 与秘密边界：启动页新增独立“当前安装实例已失效”诊断和安全重试，不出现产品登录/注册。设备私钥与长期凭据仍只在 Rust 管理的 `app_data_dir` 私有文件中，不用系统钥匙串、不进入 React/IPC；服务端吊销后本地失效凭据保留用于稳定识别该安装状态，重新授权/替换流程后续单独实现
- Executor：Installation 吊销会使父凭据与 Session 同事务失效；I2-13 的在线周期重认证以 4401 关闭连接，新 Session 换票和旧 Session 连接均被拒绝。真实 PostgreSQL 集成测试同时验证 App 访问、在线 WebSocket、旧 Session 与另一 Installation 隔离
- 生产同路径验收：`uv run python ../scripts/run_i2_14_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic、真实 Uvicorn和 `visible=false` 的真实 Tauri/WKWebView；App 经正式 React 启动入口与 Rust 桥完成注册和受保护访问，服务端运维 CLI 吊销后，同一隐藏 App 刷新进入吊销诊断。最终核对 Installation revision 2、长期凭据与全部 App Session 已撤销，且 App 私有身份/凭据文件权限正确
- 清理：验收结束已终止隐藏 App、WebdriverIO、Uvicorn，删除隔离 PostgreSQL 容器/网络/卷和精确 I2-14 App data；复核无相关进程、容器或目录遗留，未部署服务、未打开可见窗口、未执行外部平台动作
- 文档：同步根/Backend README、前后端架构、OpenAPI/TypeScript DTO、本路线图快照、依赖、状态、完成记录和当前下一步；没有新增重复规划文档
- 遗留：正式重新授权/凭据替换流程随 Demo 授权管理实现；T3-06 必须把 `require_current_installation_access` 作为创建任务入口的强制依赖；T3-08 继续建设连接 Registry，而不是重复认证与吊销逻辑

### T3-01 任务状态机

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增领域状态机目标测试并把台账置为 RED；`uv run pytest tests/unit/control_plane/domain/test_task_state_machine.py -q` 在收集阶段因 `automation_tool.control_plane.domain.task_state_machine` 不存在而失败
- GREEN：12 项目标测试通过并穷举 16×16 共 256 个来源/目标组合；Backend 全量 527 项且 1875 条语句、276 个分支覆盖率 100%，Ruff 与严格 Mypy 通过
- 状态契约：`TaskStatus` 精确包含 draft、validating、awaiting device/platform login、discovering targets、awaiting confirmation、queued、running、paused、awaiting human、cancelling 与五个终态；allowlist 使用只读 mapping + frozenset，字符串和其他非强类型输入不做隐式转换
- 取消与竞态：运行中不能直接跳 `CANCELLED`，必须先进入 `CANCELLING`；如果完成/失败事实与取消并发到达，允许从 `CANCELLING` 收敛到 succeeded、partially succeeded、failed、cancelled 或 outcome uncertain，不能用用户请求覆盖 Executor 最终事实
- 终态与不确定：succeeded、partially succeeded、failed、cancelled、outcome uncertain 无任何出边且禁止自循环；outcome uncertain 只允许从 running、awaiting human、cancelling 进入，执行前阶段不能伪造副作用不确定
- 真实边界：本任务是无 I/O 的 Control Plane 领域规则，正式调用方式是后续应用服务直接使用同一 `TaskStateMachine`；不启动数据库、服务、App、Executor 或浏览器。T3-02/T3-11 分别把它接入持久化 CAS 与正式事件收敛，不能以当前纯单元测试替代后续跨边界验收
- 失败矩阵：覆盖全部合法/非法跳转、同状态重复、终态复活、非强类型输入、暂停/恢复、目标发现人工接管、取消未确认、取消/完成竞态和副作用结果不确定；事件乱序/重复/迟到与 Control Plane/Executor 崩溃恢复不在纯状态机内放宽，分别归 T3-11/T3-20
- 安全：非法转换只返回固定 `Task state transition is invalid`，不回显来源、目标或异常 cause；领域模块只依赖标准库，不读取 Installation、凭据、私有路径或环境配置
- 清理：没有启动 App、服务、数据库、容器、浏览器或 Executor，无进程、端口和临时业务数据需要清理
- 文档：同步根/Backend README、后端架构的精确转换图、本路线图快照、任务状态、完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-02 建立 tasks/revision/installation scope 与 PostgreSQL CAS；T3-11 必须把事件映射到本状态机而非另建转换表；人工接管的恢复目标由后续 attempt/checkpoint 事实决定

### T3-02 Task 数据模型

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 migration/schema 与仓储真实 PostgreSQL 测试并把台账置为 RED；目标测试收集分别因数据库包没有 `tasks` export、`application.tasks` 与正式 Task repository 不存在而失败
- GREEN：1 项输入失败矩阵 + 8 项真实 PostgreSQL 目标测试通过；Backend 全量 536 项且 1950 条语句、288 个分支覆盖率 100%，Ruff、严格 Mypy、uv lock 和 Alembic autogenerate `check` 通过
- Schema：迁移 `20260718_0006` 新增 `tasks`，字段精确为 Task UUIDv4、Installation ID、status、revision、created/updated time；约束完整 16 状态集合、revision > 0、时间不倒退、Installation RESTRICT 外键和 `(id, installation_id)` 唯一绑定，并建立 `(installation_id, updated_at, id)` 索引
- 最小边界：本任务不提前存平台模板、页面原文或任意 JSON definition；T3-17 再按抖音搜索曝光模板的明确 DTO 扩展。`current_attempt_id` 等待 T3-03 创建 Attempt 表后以真实复合外键增加，不先放悬空 UUID
- 仓储：`SqlAlchemyTaskRepository` 先锁 Installation，仅 active 才创建 draft；get/transition 固定 Task + Installation 双条件。转换锁精确 expected revision 行、调用 T3-01 唯一状态机、revision+1，并拒绝旧 revision、跨 scope、时间回退和非强类型输入；错误不回显 Task/Installation
- 并发与吊销：两个相同旧 revision 并发转换只有一个赢家。Task 创建与 Installation 吊销使用相同 Installation 行锁顺序，因此线性化：吊销提交后不能新建 Task；对已吊销与未知 Installation 的创建共享固定失败
- 真实边界：官方 PostgreSQL 18.4 隔离容器执行完整 Alembic upgrade/check、降级到 `0005`、确认 Installation 表保留后再升 head；真实仓储连接同一数据库验证默认值、约束、scope、状态转换、回滚和并发。当前没有 Task API，正式产品调用边界从 T3-06 建立
- 失败矩阵：覆盖非法 UUIDv4、未知 Installation、非法状态/revision/时间、重复 Task、已吊销 Installation、跨 Installation 读取/转换、非法状态跳转、stale revision、时间回退、并发 CAS 和迁移回滚；API 幂等/参数、Attempt/Action 唯一约束与数据库重启恢复分别归 T3-06/T3-03/T3-20
- 安全与清理：Task 表没有 Cookie、Token、页面内容、聊天、本机路径或任意 JSON；测试只启动隔离 PostgreSQL，fixture 结束删除容器、网络、卷，无 App、Executor、浏览器、服务或端口遗留
- 文档：同步根/Backend README、后端架构、工程结构、本路线图快照、任务状态、完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-03 增加 execution attempts、actions 及 Task current attempt 复合绑定；T3-06 创建 API 必须先经过 I2-14 Installation 访问守卫并调用本仓储；T3-07 查询 API 复用相同 scope 条件

### T3-03 Attempt/Action 模型

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增领域契约和真实 migration/schema 测试并把台账置为 RED；目标测试收集分别因 `ActionOutcome`、Attempt/Action 状态契约与数据库表导出不存在而失败
- GREEN：2 项纯领域契约 + 4 项真实 PostgreSQL 模型测试通过；Backend 全量 542 项且 1993 条语句、288 个分支覆盖率 100%，Ruff、严格 Mypy、uv lock 和 Alembic autogenerate `check` 通过
- Attempt：迁移 `20260718_0007` 新增规范 Execution Attempt UUIDv4、Task/Installation 复合归属、正 attempt number/revision、有序 created/updated/started/finished time 和 14 个状态；七个终态必须有 finished time，七个非终态禁止提前完成
- 单活与重试：`(task_id, attempt_number)` 唯一，部分唯一索引限定每个 Task 最多一个非终态 Attempt；旧 Attempt 终结后才能以新序号重试，不能覆盖原链路或并发创建两个活动执行
- Current binding：`tasks.current_attempt_id + tasks.id + tasks.installation_id` 复合外键精确指向 Attempt 的 `id + task_id + installation_id`；空值代表尚无执行，非空时不能引用其他 Task 或 Installation
- Action：每个 Action 使用规范 UUIDv4，`(execution_attempt_id, ordinal)` 唯一，正 revision 和有序时间；planned/authorized/prepared/dispatched 是未结算阶段，verified/cancelled/outcome_uncertain 是终态，结果单独限定为 pending/succeeded/failed/cancelled/outcome_uncertain
- 结果确定性：数据库一致性约束要求未结算阶段只能是 pending 且无完成时间，verified 只能是 succeeded/failed，cancelled 与 outcome_uncertain 必须分别匹配同名结果和完成时间；不允许把 dispatched 伪装成成功或在结果未知时自动重放
- Scope：Attempt 复合外键命中 `(task_id, installation_id)`，Action 复合外键命中 `(execution_attempt_id, task_id, installation_id)`；跨 Task/Installation 绑定与当前 Attempt 冒充都由 PostgreSQL 拒绝，而不是依赖调用者自觉
- 真实边界：官方 PostgreSQL 18.4 隔离容器执行 Alembic 空库升 head、autogenerate check、降级到 `0006`、确认两张新表与 current column 删除后再升 head；真实 INSERT/UPDATE 验证默认值、外键、部分唯一索引、序号和终态组合
- 失败矩阵：覆盖非法 UUIDv4、错误 Task/Installation scope、重复重试序号、两个活动 Attempt、非法状态/revision/时间、终态缺完成时间、非终态提前完成、跨 Task current Attempt、重复/非正 Action ordinal、非法 Action 状态/结果与阶段结果矛盾
- 最小边界：本任务没有加入任意 JSON、页面原文、Cookie、Token、本机路径、命令、事件或平台业务参数；Attempt/Action 的创建与转换应用服务随 T3-05/T3-06/T3-11 建立，不能把本次数据库直连测试冒充未来 API/事件验收
- 清理：测试只启动隔离 PostgreSQL，fixture 结束删除容器、网络、卷；没有 App、Executor、浏览器、服务或端口遗留
- 文档：同步根/Backend README、后端架构、工程结构、本路线图快照、任务状态、完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-04 建立 task events 及复合归属；T3-05 通过持久命令驱动 Attempt offer/ack；T3-11 以事件和 revision CAS 驱动 Attempt/Action/Task 一致收敛，不能在数据库适配器复制状态分支

### T3-04 Event 模型

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 Event/快照领域契约与真实 migration/schema 测试并把台账置为 RED；目标测试收集分别因 `MAX_TASK_EVENT_SEQUENCE`、Event 类型/版本、安全消息/快照模型和 `task_events` 表导出不存在而失败
- GREEN：3 项纯领域契约 + 4 项真实 PostgreSQL Event 模型测试通过；Backend 全量 549 项且 2060 条语句、292 个分支覆盖率 100%，既有 Executor 协议 fixtures 全部通过，Ruff、严格 Mypy、uv lock 和 Alembic autogenerate `check` 通过
- 事件词汇：封闭 19 种 task/step 事件并独立固定 `TaskEventVersion.V1 = 1.0`；未知类型/版本由领域和数据库共同拒绝，不允许消费者按名字猜测新事件
- 序号：`(task_id, sequence)` 主键保证 Task 内唯一，sequence 与 Executor 协议共享 `1..2^53-1` 安全整数上限；同一序号不能覆盖，另一个 Task 可从 1 独立开始
- 来源幂等：可空 `source_message_id` 必须是规范 UUIDv4，非空时 `(installation_id, source_message_id)` 唯一；同一 Executor 来源消息在一个 Installation 内不能重复落库，不阻塞另一个 Installation 的独立来源
- Scope：事件必须命中 `(task_id, installation_id)`；Attempt 引用命中三列复合绑定，Action 引用命中四列复合绑定且 Action 非空时 Attempt 也必须非空；跨 Task/Installation/执行链引用由 PostgreSQL 拒绝
- 安全消息：`SafeTaskEventMessage` 固定错误且不回显原值，限制 1～1024 字符单行并拒绝敏感赋值、Bearer、私有绝对路径、file/data URI、控制与双向字符；共享规则抽到 protocol 层供既有 Executor payload 同源复用。数据库再限制 1024 字符/4096 字节并拒绝空值、控制字符和明显凭据
- 无任意载荷：`task_events` 只保存类型、版本、状态/revision、归属、序号、来源 ID、时间和安全消息，不保存任意 JSON、页面原文、Cookie、Token、本机路径、截图或聊天全文；事件型进度的结构化业务字段在 T3-11 以明确 DTO/列评估，不为赶进度先塞 JSON
- 快照：`tasks.last_event_sequence` 以 0 表示尚无事件，并与 Task status/revision/updated time 组成不可变强类型 `TaskSnapshotProjection`；拒绝字符串冒充、bool revision/sequence、负数/超界水位和 naive time
- 真实边界：官方 PostgreSQL 18.4 隔离容器执行 Alembic 空库升 head、autogenerate check、降级到 `0007`、确认 Event 表与 Task 水位列删除后再升 head；真实 INSERT/UPDATE 验证默认版本/入库时间、索引、去重、所有复合外键、安全消息和水位范围
- 失败矩阵：覆盖未知版本/类型/状态、非正 revision、0/超界 sequence、重复 Task sequence、重复来源消息、非法来源 UUIDv4、跨 Installation/Task/Attempt/Action、Action 缺 Attempt、倒序时间、空/超长/凭据安全消息和非法快照输入
- 原子性边界：本任务只冻结数据模型并证明数据库约束，尚未宣称事件收敛完成；T3-11 必须从正式 WebSocket 事件接收路径在同一事务插入事件、推进水位并 CAS 更新 Task/Attempt/Action，再由 T3-12/T3-15 验证断线续拉与前端降级
- 清理：测试只启动隔离 PostgreSQL，fixture 结束删除容器、网络、卷；没有 App、Executor、浏览器、服务或端口遗留
- 文档：同步根/Backend README、后端/前端架构边界、工程结构、本路线图快照、任务状态、完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-05 建立 Command/Outbox；T3-11 实现事件接收、缺口/迟到/重复与原子快照收敛；T3-12/T3-15 分别实现 SSE 续拉和 App 快照 reducer，均不得绕过本版本/序号/安全消息契约

### T3-05 Command/Outbox 模型

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 Command/Response/Outbox 状态契约与真实 migration/schema 测试并把台账置为 RED；目标测试收集分别因 `TERMINAL_TASK_COMMAND_STATUSES`、命令/响应类型和 `task_commands` 表导出不存在而失败
- GREEN：2 项纯领域契约 + 4 项真实 PostgreSQL Outbox 测试通过；Backend 全量 555 项且 2085 条语句、292 个分支覆盖率 100%，Ruff、严格 Mypy、uv lock 和 Alembic autogenerate `check` 通过
- Wire 身份：`message_id` 直接作为持久命令主键，message/correlation/response ID 均限制为规范 UUIDv4；command/response 类型精确匹配 Executor v1 的五种命令与 accept/reject/control_ack，不复制第二套 wire 名称
- 幂等与顺序：`(execution_attempt_id, sequence)` 唯一且 sequence 为 `1..2^53-1`；`(installation_id, idempotency_key)` 和 `(installation_id, response_message_id)` 分别拒绝业务意图/ACK 重放，相同值在另一个 Installation 内可独立使用
- 状态：pending、in_flight、delivered、acknowledged、rejected、expired 六态封闭；acknowledged/rejected/expired 是终态。pending 才有 next delivery，in_flight 必须持 lease，delivered 只证明 socket 写入，ACK/REJECT 必须有真实响应 message/type/time
- Deadline 与时间：deadline 严格晚于创建；next delivery 必须在窗口内，lease 晚于更新且不越 deadline，投递/确认时间有序且确认不晚于 deadline；过期可以发生在未投递或等待 ACK 阶段，但不得保留 lease/响应伪装成功
- 响应一致性：task.offer 只接受 task.accept/task.reject；pause/resume/cancel/emergency-stop 只接受 task.control_ack；数据库拒绝 offer 收 control_ack、控制命令收 accept、rejected 携带非 reject、未投递先确认和 response UUID 非 v4
- Scope：每条命令通过 `(execution_attempt_id, task_id, installation_id)` 复合外键锁死执行链；跨 Task/Installation/Attempt 冒充由 PostgreSQL 拒绝，不相信发送方自报 scope
- 无任意载荷：Outbox 只保存稳定引用、wire 身份、类型、序号、幂等、deadline、lease、投递/响应状态与时间，不存任意 JSON、页面原文、Cookie、Token、浏览器资料或本机路径；T3-09 从受约束 Task/Attempt 数据构造正式 envelope
- 真实边界：官方 PostgreSQL 18.4 隔离容器执行 Alembic 空库升 head、autogenerate check、降级到 `0008`、确认 Command 表删除且 Event 表保留后再升 head；真实 INSERT 验证六种合法状态、默认值、所有唯一/复合外键、响应 mapping、时间和失败回滚
- 失败矩阵：覆盖非法 message/correlation/response UUIDv4、跨 scope、0/超界/重复 sequence、重复 idempotency/response、非法 key/type/status/revision/attempt count、deadline/next/lease/投递/确认倒序、状态字段矛盾和命令响应错配
- 服务边界：T3-05 只建立持久模型和 due 查询索引；T3-09 必须从正式 Executor Connection Registry 路径原子抢占 lease、发送、重试、过期与接收 ACK，不能把当前数据库 INSERT 当成投递服务验收，也不能把 delivered 提前映射成任务状态完成
- 清理：测试只启动隔离 PostgreSQL，fixture 结束删除容器、网络、卷；没有 App、Executor、浏览器、服务或端口遗留
- 文档：同步根/Backend README、后端架构、工程结构、本路线图快照、任务状态、完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-06/T3-07 建立任务创建与查询 API；T3-08 建立在线连接 Registry；T3-09 才实现 Outbox dispatcher/ACK；T3-13/T3-14 控制 API 必须等确认事件后再改 Task 状态

### T3-06 创建任务 API

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增创建/重放 API 契约、同 Installation/跨 Installation 仓储生命周期和并发同键测试并把台账置为 RED；目标测试收集因 `TaskCreationResult` 尚不存在而失败
- GREEN：创建 API/仓储/迁移聚焦 17 项通过；Backend 全量 566 项且 2173 条语句、306 个分支覆盖率 100%；Ruff、严格 Mypy、uv lock、OpenAPI 漂移通过；Frontend 27 项契约、63 项 Vitest、ESLint、TypeScript、production build/API/边界扫描通过；Rust 默认与 `control-plane-e2e` 测试、fmt 和两组 Clippy `-D warnings` 通过
- API 契约：新增 `POST /api/v1/tasks`/`createTask`；只接受精确空 JSON object 和必填 `Idempotency-Key`，强制复用 `require_current_installation_access` 的 `app.control-plane` scope；首次创建 201，重放同一公开 Task 快照 200，响应固定为 taskId/status/revision/createdAt/updatedAt 且 `no-store`
- 幂等与事务：迁移 `20260718_0010` 新增受协议字符集和 128 字节上限约束的 `creation_idempotency_key`，以 `(installation_id, creation_idempotency_key)` 唯一；仓储先锁 Installation 再查/建，同 Installation 并发同键只产生一条 draft/revision 1 Task，另一个 Installation 可独立复用同键
- 迁移兼容：旧 Task 升级时确定性回填 `legacy:<task-id>`，保持 Task 身份与状态；空库 upgrade/check、从 `0009` 带旧数据升级、约束/索引、降级/再升级均在官方 PostgreSQL 18.4 验证
- App 正式边界：Rust 封闭 operation allowlist 加入固定 `POST /api/v1/tasks`，从 App 私有 vault 取长期凭据并换取短期 App Session，注入幂等键，严格解析 201/200 同形 DTO；没有向 React 暴露 bearer、长期凭据、Session、Header 或任意 URL 代理
- 生产同路径验收：`uv run python ../scripts/run_t3_06_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic、真实 Uvicorn 和唯一 `visible=false` 的真实 Tauri/WKWebView；隐藏 App 经正式 Rust 客户端完成注册后连续两次同键创建，最终核对一个 active Installation、一条 draft/revision 1 Task、两张正式 `app.control-plane` Session，以及 App 私有身份/凭据文件形状和权限
- 失败矩阵：覆盖缺失/非法/过长幂等键、未知请求字段、缺失认证、错误 Session scope、吊销 Installation、服务未装配、仓储拒绝、同键重放、跨 Installation 隔离、并发同键、数据库唯一/check/FK、旧数据迁移和 Rust DTO/metadata/transport 安全失败；平台模板字段明确归 T3-17
- 清理：两次隐藏验收均在 finally 精确终止 App/Uvicorn、删除隔离 App 私有目录、`docker compose down --volumes --remove-orphans` 并释放随机数据库端口和 8765；没有可见 App、浏览器、容器、服务或测试 Profile 遗留
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构、OpenAPI 3.1 快照、生成 TypeScript DTO、本路线图快照/状态/完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-07 建立 Installation-scoped 查询/分页；T3-17 才添加抖音模板字段和生产 UI 创建命令；T3-09/T3-11 分别负责投递与事件收敛，当前创建成功不冒充任务已运行

### T3-07 任务查询 API

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增列表/详情 API 契约、真实 PostgreSQL scope/keyset 生命周期和查询服务非法输入测试并把台账置为 RED；`uv run pytest tests/contract/test_task_query_api.py tests/integration/test_task_query_lifecycle.py tests/unit/control_plane/test_task_query_service.py -q` 因 `automation_tool.control_plane.application.task_queries` 不存在而在收集阶段失败
- GREEN：Backend 全量 577 项且 2315 条语句、342 个分支覆盖率 100%，Ruff、格式和严格 Mypy 通过；OpenAPI 3.1/生成 TypeScript DTO 无漂移；Frontend 28 项 Node 契约、63 项 Vitest、4 项 Playwright、ESLint、TypeScript 和 production build/边界扫描通过；Rust 默认与 `control-plane-e2e` 各 43 项测试、fmt 及全目标全特性 Clippy `-D warnings` 通过
- API 契约：新增 `GET /api/v1/tasks`/`listTasks` 和 `GET /api/v1/tasks/{task_id}`/`getTask`；两者强制复用 `require_current_installation_access`，只返回 taskId/status/revision/createdAt/updatedAt 公开快照并固定 `no-store`
- 分页：列表按 `(updated_at DESC, task_id DESC)` 使用 PostgreSQL keyset 查询并多取一行，`limit` 限制为 `1..100`；下一页 cursor 是长度不超过 256 的 canonical JSON Base64URL，包含 UTC 微秒时间和规范 Task UUIDv4，不包含明文幂等键或凭据
- 隔离与错误：列表查询在 SQL 条件中固定 Installation；详情用 `task_id + installation_id` 同时命中。非法/未知/跨 Installation Task 对外共享同一 `task_not_found` 404；重复/未知 cursor 字段、非法 UUID/时间、非规范 Base64/JSON、极短畸形 Base64 和非法 limit 统一 fail closed 且不回显输入
- App 正式边界：Rust 封闭 allowlist 增加 `ListTasks`/`GetTask`，只构造固定列表路径和通过 UUIDv4 校验的详情路径；每次调用从 App 私有 vault 换取 `app.control-plane` Session，严格解析公开状态、正 revision、UTC 时间、降序列表和 opaque cursor，不向 React/IPC 暴露 bearer、长期凭据、Session、Header 或任意 URL
- 生产同路径验收：`uv run python ../scripts/run_t3_07_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic、真实 Uvicorn 和唯一 `visible=false` 的真实 Tauri/WKWebView；先预置另一个 Installation/Task，再由隐藏 App 注册、创建三个自有 Task、完成 2+1 分页、读取详情并确认外部 Task 不可见，最终核对两个 Installation、四条 Task、七张正式 App Session、已消费 challenge 及 App 私有身份/凭据权限
- 条件编译：Rust `desktop-e2e + control-plane-e2e` 同时启用时明确选择生产身份/凭据边界，避免全特性构建漏失 vault；默认、单独控制面特性和 all-features Clippy 均已验证
- 失败矩阵：覆盖缺失/错误认证、服务未装配、非法 scope/Task ID/limit/cursor、重复与未知 JSON key、非规范编码、同时间 Task ID tie-break、分页边界、未知与跨 Installation 不可见、Rust 固定路径/DTO/顺序/metadata/transport 拒绝，以及查询不修改其他 scope 数据
- 清理：隐藏验收在 finally 精确终止 App/Uvicorn、删除隔离 App 私有目录、`docker compose down --volumes --remove-orphans` 并释放随机数据库端口和 8765；复核无监听、容器或测试 App 数据遗留，全程未弹出或聚焦 App
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构、OpenAPI 3.1 快照、生成 TypeScript DTO、本路线图快照/状态/完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-08 建立 Executor Connection Registry；T3-09 负责持久命令投递；T3-12/T3-15/T3-16 再接事件续拉与 App 投影，当前查询 API 不冒充实时任务闭环

### T3-08 Executor Connection Registry

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增单活在线投影、旧连接替换、heartbeat sequence、stale cleanup、进程退出和非法输入测试并把台账置为 RED；`uv run pytest tests/unit/control_plane/test_executor_connection_registry.py -q` 因 `automation_tool.control_plane.application.executor_connection_registry` 不存在而在收集阶段失败
- GREEN：Registry/路由聚焦 60 项通过，Registry 模块自身 153 条语句、40 个分支覆盖率 100%；Backend 全量 597 项且 2515 条语句、386 个分支覆盖率 100%；uv lock、Ruff/格式、严格 Mypy、OpenAPI 和 Executor Schema 漂移检查通过。任务未改 TypeScript、Rust、REST OpenAPI 或数据库 Schema
- 单活与投影：每个 FastAPI app 拥有一个独立进程内 Registry，以强类型 InstallationId 为唯一 live key；公开不可变投影只含 Connection/Installation/Executor ID、协议/Executor 版本、平台/架构、服务端 connected/heartbeat 时间和最后 sequence，不包含 WebSocket、Session、凭据或客户端时间
- 替换与竞态：新 Hello 先原子成为 current，再用固定 4409/`Executor connection was replaced` 关闭旧 socket；同 Installation 即使 ExecutorId 不同也不能并存。所有 heartbeat、send 和 unregister 都同时核对 Connection ID，旧连接迟到消息或 finally 清理不能覆盖/删除新连接
- Heartbeat：Bound connection 保留 Hello sequence；后续 heartbeat 必须属于同一 Installation/Executor 且在 `1..2^53-1` 内严格递增，在线时间只取服务端 UTC clock。重复/倒序 sequence、时钟回退和 stale heartbeat 均 fail closed，不把客户端 timestamp 当在线权威
- 当前连接发送 API：`send_current` 必须精确命中 Installation + 预期 Connection ID，只接受 1..32 KiB UTF-8 wire；socket 写入失败返回固定 unavailable，写入期间发生替换则在写后复核中返回 stale。T3-09 必须据此驱动持久 Outbox，不能把 send 成功当成 Executor ACK
- 生命周期：WebSocket 认证/Hello 后才注册；周期重认证前确认连接仍 current，合法 heartbeat 更新投影；协议、认证、Registry 和内部失败使用固定 4401/4406/4409/1011。App lifespan 用 1012 关闭并清空全部连接，关闭单个故障不阻塞其他连接清理
- 生产同路径验收：`uv run python ../scripts/run_t3_08_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic 和真实 Uvicorn/SansIO；经正式 REST 换取 `executor.connect` Session，由标准 WebSocket 客户端完成第一连接 heartbeat、不同 ExecutorId 的同 Installation 替换、旧连接 4409、当前 heartbeat、重复 sequence 4406 和重新连接恢复，最终确认凭据/Session 仍 active
- 单实例约束：Registry 是瞬时连接路由，不复制 PostgreSQL 认证、任务、命令或事件事实。MVP/Demo 必须保持单 Control Plane 实例；C10 横向扩容前必须先增加跨副本连接路由与事件总线，不能直接把副本数改为 N
- 失败矩阵：覆盖非法 bound/channel/clock/sequence/wire/Installation/Connection ID、重复注册、shutdown 后注册、不同 Installation 共存、旧连接替换关闭失败、发送失败、发送中替换、stale heartbeat/send/unregister、Registry 未装配、注册/current/heartbeat/cleanup 内部异常及无秘密错误
- App 测试边界：本任务正式入口是服务器 WebSocket，真实网络验收已覆盖原始调用方式，因此不启动 Tauri App、不运行前台窗口；E4-02/E4-12 再由正式 Local Executor 进程复用该入口
- 清理：两次真实验收均在 finally 终止 Uvicorn、删除隔离 PostgreSQL 容器/网络/卷并确认 Control Plane/数据库端口关闭；无 App、浏览器、WebDriver、测试 Profile 或业务数据遗留
- 文档：同步根/Backend README、后端架构、工程结构、本路线图快照/状态/完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-09 使用 `send_current` 建立 Outbox 抢占、投递、ACK、过期和重连恢复；C10 多副本部署前新增跨副本连接路由，当前不引入 Redis 或第二事实源

### T3-09 命令投递服务

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 pending offer 抢占/发送、socket 失败释放、真实 ACK 边界、重连恢复和非法 scope 测试并把台账置为 RED；`uv run pytest tests/unit/control_plane/test_task_command_delivery_service.py -q` 因 `automation_tool.control_plane.application.task_command_delivery` 不存在而在收集阶段失败
- GREEN：投递应用模块 188 条语句/28 个分支、PostgreSQL 仓储 138 条语句/50 个分支、正式 WebSocket 路由 193 条语句/24 个分支分别达到 100%；Backend 全量 620 项、2892 条语句/476 个分支覆盖率 100%；uv lock、Ruff/格式、严格 Mypy、OpenAPI 3.1 与 Executor Schema 漂移检查通过
- 原子 enqueue：服务生成规范 message/correlation UUIDv4，先锁 active Installation，再按 Installation-scoped idempotency key 查建；同一意图并发重放返回既有 Command，改换 Task/Attempt/sequence/type/deadline 的同 key 和数据库唯一冲突固定拒绝
- 抢占与发送：每轮正式 WebSocket 重认证后先批量过期，再用 PostgreSQL `FOR UPDATE SKIP LOCKED` 按 deadline/创建时间抢占 pending、lease 过期 in-flight 或 ACK 超时 delivered；claim 原子递增 revision/delivery attempts 并持有不越 deadline 的 lease，有界批次只通过 Registry `send_current` 发送
- 失败与恢复：socket unavailable/stale 时清 lease 并延迟回 pending；写入成功只记 delivered。新 Hello 立即重投连接前已 delivered 的同 message/idempotency，当前批次刚写入的命令不会自重投；Control Plane 崩溃遗留 in-flight 在持久 lease 到期后恢复，不依赖进程内状态
- 严格 ACK：WebSocket 绑定后只接受同一身份 heartbeat 或 `TaskCommandResultEnvelope`；回执同时核对 Installation、Task、Attempt、correlation、sequence、命令/响应 mapping 与封闭布尔 payload。首个合法 response ID/服务端接收时间成为终态，后续同结论重复 ACK 幂等且不能覆盖，错配、未投递先确认、迟到、跨命令响应 ID 冲突固定拒绝
- 状态边界：socket write 绝不冒充 Executor ACK；ACK 只把 Command 收敛为 acknowledged/rejected，不修改 Task/Attempt/Action。业务状态必须等 T3-11 的持久事件事实，暂停/恢复/取消/紧停的公开 API 分别等待 T3-13/T3-14
- Payload 边界：Outbox 仍不保存任意 JSON；当前 task.offer 发送空 object 安全骨架，供 T3-10 FakeExecutor 无副作用回放。T3-17 必须先建立抖音模板明确列/DTO，再从受约束 Task 事实构造真实业务 payload，不能提前塞任意定义
- 生产同路径验收：`uv run python ../scripts/run_t3_09_acceptance.py` 两次后台启动隔离 PostgreSQL 18.4、完整 Alembic 与真实 Uvicorn/SansIO；经正式 REST 换取 `executor.connect` Session，由标准 WebSocket 客户端验证首次 offer、断线同 message 重投、错误 ACK 4406、再次重投、真实 ACK、不同 response ID 的重复 ACK 幂等和离线 deadline 过期；最终数据库精确为原命令 delivery attempts 3/首个 response acknowledged，过期命令 attempts 0/无 response
- App 测试边界：本任务正式产品入口是 Control Plane 持久 Outbox 到 Executor WebSocket，不是 App API；因此不启动 Tauri App、不弹窗、不抢焦点。T3-10 用正式 FakeExecutor 进程复用该入口，E4-02/E4-12 再由正式 Local Executor 复用
- 失败矩阵：覆盖非法 ID/scope/sequence/type/key/time/config/clock/批次、未知/吊销 Installation、幂等意图改变、唯一冲突、并发抢占、lease/ACK 超时、write/stale/release/mark/持久化失败、错误/重复/迟到 ACK、控制回执 mapping、response ID 冲突、服务未装配和不泄密关闭码；本任务没有 REST/OpenAPI、数据库 Schema、TypeScript 或 Rust 改动
- 清理：两次真实验收均在 finally 终止 Uvicorn、删除隔离 PostgreSQL 容器/网络/卷并确认 Control Plane/数据库端口关闭；复核无 T3-09 进程/容器、App、浏览器、WebDriver、Profile 或业务数据遗留
- 文档：同步根/Backend README、后端架构、工程结构、本路线图快照/状态/完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-10 实现不放宽生产协议/状态机的无副作用 FakeExecutor；T3-11 接收持久事件并收敛 Task/Attempt/Action；T3-13/T3-14 才开放用户控制命令。C10 多副本前仍需跨副本连接路由，本任务不引入第二事实源

### T3-10 FakeExecutor

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增全部场景/控制事件、双键重放、身份/deadline/sequence/state 和零副作用依赖测试并把台账置为 RED；`uv run pytest tests/unit/executor/test_fake_executor.py -q` 因 `automation_tool.executor` 不存在而在收集阶段失败
- GREEN：FakeExecutor 模块 262 条语句、66 个分支覆盖率 100%，23 项定向测试通过；Backend 全量 643 项、3154 条语句、542 个分支覆盖率 100%；uv sync/lock、Ruff/格式、严格 Mypy、OpenAPI 3.1 与 Executor Schema 漂移检查通过
- 正式协议：引擎只复用 `automation_tool.protocol` 的 v1 parser/envelope，精确核对 Installation/Executor、deadline、Attempt 内 command sequence 及 task/attempt 绑定；WebSocket 子协议常量移入共享 protocol，Control Plane 与 Fake 客户端不再复制字面量
- 场景覆盖：offer 支持 accept/reject、成功、部分成功、失败、登录、人工接管、结果不确定和 hold；正式网络回放覆盖全部 14 种当前 TaskEvent，以及 pause/resume/cancel/emergency-stop 的 control ACK 与对应事件，不执行浏览器、桌面、文件或业务副作用
- 幂等与原子性：message ID 与 idempotency key 双账本保存首次生成的完整 envelope；同意图换 message ID 仍精确重放且不重复事件，任一 key 携带不同意图固定拒绝。ID/时钟/模型生成中途失败会回滚任务状态与事件 sequence，重试不会继承半成品
- 状态边界：Fake 只保存验证场景所需的 new/running/paused/awaiting/terminal 内存投影并拒绝非法控制顺序，不导入或放宽 Control Plane 领域状态机；生产 Task/Attempt/Action 状态仍只由 T3-11 的持久事件事实推进
- 传输边界：`FakeExecutorClient` 使用直接运行时依赖 `websockets`，只接受固定 `/api/v1/executors/connect` 的 `ws`/`wss` URL、唯一正式子协议、Bearer Session、32 KiB 限制和 `1..1000` 有界 command 数；配置 repr 与所有失败只暴露固定安全文案
- 生产同路径验收：`uv run ../scripts/run_t3_10_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic 和真实 Uvicorn/SansIO；正式 REST 换取 `executor.connect` Session，FakeExecutor 接收持久 `task.offer`、回传正式 `task.reject`，最终数据库为 rejected、delivery attempts 1、response type `task.reject` 且有服务端 ACK 时间
- 事件验收边界：23 项定向测试以真实 loopback WebSocket controller 验证全部场景和控制消息实际通过 `FakeExecutorClient` 收发；当前 Control Plane 尚不接收 TaskEvent，因此正式 Uvicorn 验收刻意使用零事件 reject 场景。事件落库、去重、缺口/迟到和 revision CAS 必须由 T3-11 经生产 WebSocket 验收，不能以 controller 或直接调用冒充
- App 测试边界：本任务正式产品入口是 Executor 出站 WebSocket，不是 App API；因此不启动 Tauri App、不弹窗、不抢焦点。Wave 4 涉及 App 时继续只用 `visible=false` 后台模式，并从真实 App/Rust 入口验收
- 清理：真实验收在 finally 终止 Uvicorn、删除隔离 PostgreSQL 容器/网络/卷并确认 Control Plane/数据库端口关闭；无 App、浏览器、WebDriver、Profile、RPA、文件或业务副作用遗留
- 文档：同步根/Backend README、后端架构、工程结构、本路线图状态/完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-11 将正式 WebSocket TaskEvent 原子落库并推进 Task/Attempt/Action；T3-13/T3-14 才开放控制 API；E4-02/E4-12 由正式 Local Executor 复用相同协议与传输，Fake 内存账本不得进入生产恢复路径

### T3-11 事件接收与收敛

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先把台账置为 RED 并新增全部事件映射/Action 显式绑定/非法 payload/deadline 测试；`uv run pytest tests/unit/control_plane/test_task_event_convergence_service.py -q` 因 `application.task_event_convergence` 不存在而收集失败。随后新增真实 PostgreSQL 生命周期测试，因 `SqlAlchemyTaskEventConvergenceRepository` 未导出而收集失败；正式 bound WebSocket 测试则证明 TaskEvent 被现有连接入口 4406 拒绝
- GREEN：事件应用、PostgreSQL 仓储、正式 WebSocket 与 bootstrap 合计 471 条语句/120 个分支、100 项定向测试覆盖率 100%；Backend 全量 689 项、3439 条语句/640 个分支覆盖率 100%；uv sync/lock、Ruff/格式、严格 Mypy、OpenAPI 3.1 与 Executor Schema 漂移检查通过
- 迁移：新增可回滚 `20260718_0011`，为旧事件确定性回填后强制非空 `source_idempotency_key` 与 32 字节 `source_fingerprint`，增加格式/长度和 Installation-scoped 唯一约束；空库升级、完整降级恢复和 0010 既有事件升级回填均由真实 PostgreSQL 验证
- 输入收窄：正式 WebSocket bound parser 接受 TaskEvent；14 种 source type 映射到封闭领域事件和 Task/Attempt 目标。非 step payload 必须为空；step 只允许可选 canonical Action ID，progress 必须携带 `0..100` strict integer；不保存任意 payload、页面文本或 Executor 错误原文
- 原子收敛：仓储先按 Installation + Task `FOR UPDATE`，同时核对 current Attempt 与可选 Action 复合归属；事件插入、Task revision/watermark CAS、Attempt 状态/started/finished 和显式 Action 状态/outcome/finished 在同一事务完成，任一失败全部回滚。Task 每条事件增 revision，Attempt/Action 只在明确状态变化时增 revision
- 重放与顺序：message ID 或 idempotency key 任一命中且稳定意图指纹一致即返回当前快照且不增 revision；交叉 key、同 key 改意图、同 sequence 不同事件、sequence 缺口和非精确迟到固定拒绝。不同 Task 并发争用同 Installation/idempotency 只有一条事实和一个快照赢家
- 状态与时间：所有状态变化复用 `TaskStateMachine` 及封闭 Attempt/Action 转换；终态不能复活，step 只允许 running/cancelling Attempt/Task，Action 仅在明确 ID 且阶段相容时更新。occurred 使用 Executor sent time，recorded/updated 使用服务端 UTC 接收时间；未来事件、deadline 到期和服务端时间回退均拒绝
- WebSocket 错误：身份/协议/状态/顺序冲突统一固定 4406；数据库或内部不可用统一固定 1011，公开原因和日志不含 bearer、wire、payload、数据库地址或底层异常文本。heartbeat、Command ACK 与 TaskEvent 保持三条独立处理分支
- 生产同路径验收：`uv run ../scripts/run_t3_11_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic 和真实 Uvicorn/SansIO；正式 REST 换取 `executor.connect` Session，FakeExecutor 从持久 Outbox 收到 offer 后回传 accept 与五条成功事件；最终 Command acknowledged/attempts 1，Task succeeded/revision 6/watermark 5，Attempt succeeded/revision 3 且 started/finished 完整，事件 sequence `1..5`、task revision `2..6` 和类型全部精确
- Fake 语义修正：登录场景改为先发 `session.login_required` 而不伪造 `task.started`，从 awaiting-device 合法收敛到 awaiting-platform-login；其余成功/部分/失败/接管/不确定/hold 网络回放保持通过
- App 测试边界：本任务唯一生产入口是 Executor 出站 WebSocket，不是 App API；未启动 Tauri App，不弹窗、不抢焦点。T3-12/T3-15 再从隐藏 App 的正式 Rust/React 入口验收事件消费
- 清理：真实验收在 finally 终止 Uvicorn、删除隔离 PostgreSQL 容器/网络/卷并确认 Control Plane/数据库端口关闭；无 App、浏览器、WebDriver、Profile、RPA、文件或业务副作用遗留
- 文档：同步根/Backend README、后端架构、工程结构、本路线图状态/完成记录和当前下一步；没有新增重复规划文档
- 遗留：T3-12 必须只推送已提交事件并支持 Last-Event-ID/断线续拉；T3-13/T3-14 先持久命令再等待本收敛入口确认状态；T3-17 再建立真实抖音 Action/模板，不把 Fake payload 扩成任意 JSON

### T3-12 SSE 事件流

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先把台账置为 RED 并新增 Last-Event-ID、终态/分批、gap/错 Task/超前水位、公开模型和安全错误测试；`uv run pytest tests/unit/control_plane/test_task_event_stream_service.py -q` 因 `application.task_event_stream` 不存在而收集失败。随后新增真实 PostgreSQL 已提交/未提交可见性测试，因 `SqlAlchemyTaskEventStreamRepository` 不存在而收集失败；HTTP 契约因路由和 App factory 注入缺失 8 项失败。补产品入口时再先新增隐藏 App 工程契约，因专用 `visible=false` 配置不存在而 RED
- GREEN：SSE 应用、API、PostgreSQL 仓储和 bootstrap 共 219 条语句/48 个分支，45 项定向测试覆盖率 100%；Backend 全量 727 项、3679 条语句/694 个分支覆盖率 100%。Rust 单元 37 项、共享协议 3 项、配置安全 6 项全部通过；Ruff/格式、严格 Mypy、Cargo test/fmt、全特性 Clippy、Frontend lint/type、OpenAPI 与生成 DTO 漂移检查通过
- 持久模型：新增可回滚 `20260718_0012`，只为 `task_events` 增加 nullable `progress_percent` 明确列，并以数据库约束限制为 `step.progress` 的 `0..100`；正式 T3-11 收敛入口保存已经 strict-int 验证的值，不引入任意 JSON、Executor 原文或页面内容
- 已提交边界：`SqlAlchemyTaskEventStreamRepository` 用单条 PostgreSQL outer-join/MVCC statement 同时读取 Installation-scoped Task status/watermark 和其后最多 100 条事件；独立 writer 事务提交前，事件行和 Task 终态投影均不可见。流不依赖进程内队列、LISTEN 缓存或 WebSocket 内存状态，服务重启后仍从同一事实续拉
- 顺序与恢复：标准 `Last-Event-ID` 只接受规范十进制 `0..2^53-1`；每批第一条必须等于 cursor+1，后续连续且属于同 Task。超前水位返回 422，未知/非法/跨 Installation Task 统一 404，已落水位前出现空洞或错 scope 结果固定 503；不缓存乱序事件
- SSE 语义：帧 `id` 等于持久 sequence，`event` 等于封闭事件类型，`data` 只含公开 Task/Attempt/Action ID、版本/类型、revision/status、结构化进度、UTC 时间和安全消息；来源 message/idempotency/fingerprint 不出站。响应固定 `no-store, no-transform`、禁代理缓冲，15 秒 comment keepalive；追平终态水位立即关闭，非终态连接最多 55 秒轮换以便重新换票。响应开始后的失败只断流，不伪造半途 JSON 错误
- Rust 正式入口：既有 `ControlPlaneClient` 增加固定 Task SSE operation；自己从 App 私有 vault 换取 `app.control-plane` Session，禁止任意 URL、重定向和代理，限制单连接 512 KiB/单帧 64 KiB，并验证 request ID、SSE content type/cache 头、唯一 id/event/data、连续序号、版本、事件/状态词汇、UUIDv4、UTC 时间、进度和消息边界。React/IPC 不接触长期凭据、Session、Header 或原始 SSE wire
- 产品同路径验收：`uv run python ../scripts/run_t3_12_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic、真实 Uvicorn/SansIO 和唯一 `visible=false` Tauri/WKWebView。隐藏 App 经正式 Rust 桥注册、创建 Task，并在事件到达前建立 SSE；FakeExecutor 经正式 Executor Session/WebSocket 产生五条事件。App 读取 1、2 后主动断开，以新 App Session 和 `Last-Event-ID: 2` 续拉 3、4、5，核对 progress 50、连续 revision/类型和终态关闭；数据库最终为 succeeded/revision 6/watermark 5
- App 后台边界：唯一自动化主窗口固定 `visible=false`，全程后台运行、不弹窗、不抢焦点；production `tauri.conf.json` 仍正常可见。SSE 请求确实由真实 App/Rust 发出，不以 TestClient、mock、浏览器 Harness 或 Python HTTP 客户端冒充产品入口
- 清理：纵向验收 finally 回收隐藏 App/WDIO、Uvicorn、隔离 PostgreSQL 容器/网络/卷、App 私有测试目录和全部端口；没有浏览器、WebDriver、Profile、RPA、文件或社交平台副作用遗留
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构、本路线图状态/完成记录和当前下一步；OpenAPI 与 `openapi-typescript` 同提交更新，没有新增重复规划文档
- 遗留：T3-15 把现有 Rust 事件源接入 Tauri Channel 与 React 快照 reducer，处理版本降级/缺口回拉，不在 WebView 重新持有 Session 或建立第二条 EventSource

### T3-13 暂停/恢复 API

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先把台账置为 RED，并新增暂停/恢复应用服务、HTTP 契约、真实 PostgreSQL 生命周期测试；目标测试因 `application.task_controls` 不存在而收集失败。实现持久命令后，集成测试准确证明未 ACK 的 `task.paused` 仍会被既有事件收敛入口接受；再把 ACK/correlation 门禁写成失败测试后修复。产品入口契约最初因专用 `visible=false` Tauri 配置和编排脚本不存在而失败
- GREEN：Backend 全量 `737 passed in 57.67s`，3894 条语句/740 个分支覆盖率 100%；30 项 Frontend Node 契约、63 项 Vitest、38 项 Rust 单元、3 项共享协议和 7 项安全配置测试通过。Ruff/格式、严格 Mypy、uv lock、Frontend lint/type、生产边界、Cargo fmt、全特性 Clippy、OpenAPI 与生成 DTO 漂移检查全部通过
- API：新增 `POST /api/v1/tasks/{task_id}/pause` 与 `/resume`，只接受空 JSON、认证 Installation scope 和受限 `Idempotency-Key`；状态相容时原子分配 Attempt 内下一 command sequence 并写 pending Outbox，首次返回 202、同键同意图重放返回 200。响应只含公开 Command/Task/Attempt ID、sequence/type/status/revision 和 UTC 时间
- 状态门禁：API、socket delivered 与 `task.control_ack` 均不修改 Task/Attempt。`task.paused`/`task.resumed` 收敛前必须锁定该 Attempt 最新 pause/resume 命令，并核对类型、acknowledged、control_ack、correlation 与确认时间；无 ACK、旧命令、错 correlation、状态冲突、跨 Installation 和序号耗尽全部 fail closed
- Rust 边界：正式 `ControlPlaneClient` 增加固定 PauseTask/ResumeTask operation；从 App 私有 vault 换取 `app.control-plane` Session，禁止任意 URL/Header/bearer，并严格验证 202/200、UUIDv4、跨运行时安全序号、命令类型/状态、revision 与 deadline。长期凭据仍只在 `app_data_dir`，不用系统钥匙串、不进入 React/IPC
- 生产同路径验收：`uv run python ../scripts/run_t3_13_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic、真实 Uvicorn/SansIO 和唯一 `visible=false` Tauri/WKWebView。隐藏 App 经正式 Rust 注册/创建 Task，HOLD FakeExecutor 经正式 Session/WebSocket 处理 offer、pause、resume；App 写控制命令并通过正式 Rust SSE 等到 paused/resumed，最终命令 sequence 1/2/3 全部 acknowledged、事件 sequence 1..4、Task running/revision 5、Attempt running/revision 4
- App 后台边界：自动化主窗口固定 `visible=false`，全程后台运行、不弹窗、不抢焦点；production `tauri.conf.json` 仍正常可见。暂停/恢复请求确实由真实 App/Rust 发出，直接 HTTP、TestClient、Mock 或 Harness 仅作为分层证据
- 失败矩阵：覆盖非法/缺失幂等键、额外字段、未认证、服务不可用、未知/跨 Installation Task、Task/Attempt 状态错配、并发同键、同键改意图、序号上限、数据库唯一冲突、未投递/未 ACK 事件、ACK 不提前投影、错 correlation、公开响应脱敏和固定错误分类
- 清理：纵向验收 finally 回收隐藏 App/WDIO、Uvicorn、隔离 PostgreSQL 容器/网络/卷、App 私有测试目录和全部端口；没有浏览器 Profile、RPA、文件或社交平台副作用遗留
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构、本路线图状态/完成记录和当前下一步；OpenAPI 与 `openapi-typescript` 同提交更新，没有新增重复规划文档
- 后续承接：T3-14 已在同一持久控制基础上实现 cancel/emergency-stop 并单独处理 CANCELLING、完成竞态和 outcome uncertain；T3-18 已把 pause/resume 接入正式运行详情按钮

### T3-14 取消/紧停 API

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先扩展应用服务、公开 HTTP 契约、真实 PostgreSQL 生命周期与隐藏 App 产品入口测试；Backend 目标测试因 `TaskControlService.cancel` 和 `/cancel`、`/emergency-stop` 尚不存在而 3 项准确失败，Frontend 产品入口契约因正式 Rust 方法与 T3-14 `visible=false` 配置尚不存在而 2 项准确失败
- GREEN：Backend 全量 `743 passed in 60.50s`，3931 条语句/752 个分支覆盖率 100%；31 项 Frontend Node 契约、63 项 Vitest、38 项 Rust 单元、3 项共享协议和 8 项安全配置测试通过。Ruff/格式、严格 Mypy、uv lock、Frontend lint/type/API 漂移、Cargo fmt、三套 feature 测试和全特性 Clippy 全部通过
- API 与幂等：新增 `POST /api/v1/tasks/{task_id}/cancel` 与 `/emergency-stop`，只接受空 JSON、认证 Installation scope 和受限幂等键；首次返回 202，同 scope/key/意图重放返回原 Command 的 200，改意图、再次终止、终态、跨 scope、序号耗尽和不相容投影均 fail closed
- 原子 CANCELLING：仓储按 Installation→Task→current Attempt 锁顺序，在一个事务中分配下一 command sequence、写 pending Outbox，并把 Task/Attempt 各以 revision CAS 前进一次到 cancelling；不写伪造 cancelled 事件、不占用 Executor 事件 sequence，重放不重复增 revision
- ACK 与结果：`task.cancelled` 及 cancelling 下的 `task.outcome_uncertain` 必须匹配最新 cancel/emergency-stop Command 的 acknowledged/control_ack/correlation/确认时间；没有 ACK、旧控制或错 correlation 均整笔拒绝。HOLD FakeExecutor 对普通 cancel 回报 cancelled，对正在执行动作的 emergency-stop 保守回报 outcome uncertain
- 完成竞态：取消请求与 task.completed 并发时由同一 Task 行锁线性化；完成先到则终止 API 冲突，取消先到则完成事实合法从 cancelling 收敛到 succeeded。partially succeeded、failed、cancelled、outcome uncertain 同样只按 Executor 最终事实收敛，不用用户意图覆盖已发生结果
- Rust 边界：正式 `ControlPlaneClient` 增加固定 CancelTask/EmergencyStopTask operation；只由 App 私有 vault 换 `app.control-plane` Session，固定构造 `/cancel`/`/emergency-stop`，严格验证 202/200、UUIDv4、安全 sequence、精确 command type/status/revision/deadline；长期凭据不用系统钥匙串、不进入 React/IPC
- 生产同路径验收：`uv run python ../scripts/run_t3_14_acceptance.py` 后台启动隔离 PostgreSQL 18.4、完整 Alembic、真实 Uvicorn/SansIO 和唯一 `visible=false` Tauri/WKWebView。同一隐藏 App 经正式 Rust 顺序创建两个 Task 并发出 cancel/emergency-stop，同一 HOLD FakeExecutor 经正式 Session/WebSocket 处理两个 offer/control；两组命令 sequence 1/2 全部 acknowledged、事件 sequence 1..3，最终分别为 cancelled/outcome_uncertain，Task revision 5、Attempt revision 4
- App 后台与秘密边界：自动化主窗口固定 `visible=false`，全程后台运行、不弹窗、不抢焦点；请求确实由真实 App/Rust 发出。设备私钥和长期凭据只在独立 `app_data_dir` 私有文件中，不用系统钥匙串；直接 HTTP、TestClient、Mock、Harness 与 Fake 只作为分层证据
- 失败矩阵：覆盖缺失/非法幂等键、额外字段、未认证、服务不可用、未知/跨 Installation Task、无 current Attempt、Task/Attempt 终态或错配、并发同键、同键改意图、重复终止、序号上限、时间回退、未 ACK/错 correlation、取消/完成竞态、cancelled/outcome uncertain 两类终态和公开响应脱敏
- 清理：纵向验收 finally 回收隐藏 App/WDIO、Uvicorn、隔离 PostgreSQL 容器/网络/卷、App 私有测试目录和全部端口；复核没有相关进程、监听、容器或目录遗留，没有浏览器 Profile、RPA、文件或社交平台副作用
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构、本路线图、OpenAPI 与生成 TypeScript DTO；没有新增重复规划文档
- 后续承接：T3-15 已将权威快照、事件去重/缺口与版本降级接入 React reducer；T3-16 已把紧停接入工作台，T3-18 已接运行详情。离线本机紧停与外部动作账本的精确 uncertain 判定分别归 H8-03/A7-13

### T3-15 前端 Query/事件 Reducer

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：先新增 TanStack Query key/取消边界、Task 快照与事件运行时契约、快照权威 Reducer、重复去重、缺口回拉、未知版本/类型降级、有限恢复，以及固定 Tauri Channel source 测试。`./node_modules/.bin/tsc -p tsconfig.app.json --noEmit --incremental false --pretty false` 因 `task-projections`、`task-projection-reducer`、`task-projection-controller` 与 `task-projection-source` 尚不存在而按预期失败；后端契约同时先要求公开快照返回 `lastEventSequence`
- 实现边界：后端已有 `tasks.last_event_sequence` 是唯一权威水位；现有列表/详情/创建同形公开快照增加 `lastEventSequence`。Rust 严格解析快照和 SSE 后边读边推 Tauri Channel，React 使用 TanStack Query、Zod 与纯 Reducer；WebView 不持有 Session、Header、Bearer、原始 SSE 或任意 URL，不建立 EventSource 和第二事实源
- 分层验证：Backend 全量 `743 passed in 56.19s`；Frontend 32 项 Node 工程契约和 88 项 Vitest 全绿；Rust all-features 38 项单元、3 项共享协议、9 项安全配置全绿。Ruff/格式、严格 Mypy、ESLint、TypeScript 类型、OpenAPI 漂移、production boundary、Cargo fmt 和全特性 Clippy 零警告全部通过
- 产品同路径：`uv run python ../scripts/run_t3_15_acceptance.py` 用隔离 PostgreSQL、完整 Alembic、真实 Uvicorn/FakeExecutor 和唯一 `visible=false` Tauri/WKWebView 运行。WebView 中的正式 TypeScript source 先经 Query 调用 Rust 快照 Command，再通过 Tauri Channel 消费正式 Rust SSE，并由同一 Reducer 收敛 sequence 1..5；App 与数据库共同确认最终 `succeeded`、revision 6、lastEventSequence 5
- App 与秘密边界：设备私钥和长期凭据仍只在独立 `app_data_dir` 私有文件中，不使用系统钥匙串；App 全程后台、不弹窗、不抢焦点。浏览器 Harness、直接 HTTP、Mock 与纯 Reducer 只作为分层证据
- 失败与恢复矩阵：覆盖严格快照/列表/事件 DTO、未知字段、非法 UUID/UTC 微秒时间/状态/版本/事件类型、敏感消息、Task scope 错配、重复事件、序号缺口、revision/水位回退、未知版本/类型、正常 SSE 轮换、传输异常、取消和恢复预算耗尽；任何不兼容都先回拉服务端快照，连续失败才进入有界 degraded，不从事件名推断状态
- 清理：纵向验收 finally 回收隐藏 App/WDIO、Uvicorn、隔离 PostgreSQL 容器/网络/卷、App 私有测试目录和端口；复核 8765 无监听、无 `automation-tool-t315` 容器遗留
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构、本路线图、OpenAPI 快照与生成 TypeScript DTO；未新增重复规划文档
- 后续承接：T3-16 已把该投影接入当前任务/最近任务工作台及全局紧停；T3-17/T3-18 分别建立新建任务骨架和运行详情

### T3-16 工作台页面

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- RED：新增 Installation-scoped 工作台运行状态契约、真实 Task 列表/当前任务/最近任务/指标页面、固定 Tauri gateway、全局紧停确认和安全重试测试。Backend 3 项因 `/api/v1/workbench/status` 尚不存在而准确失败；Frontend 两个新 suite 因正式 `Workbench` 与 `workbench-gateway` 尚不存在而准确失败，既有 88 项继续通过
- 实现边界：新增受 `app.control-plane` Session 保护的 `GET /api/v1/workbench/status`，只公开 ready、online/offline 与服务端最后心跳，不暴露 Connection/Executor/Installation ID。React 工作台复用 T3-15 Task source/Query/Reducer，并通过固定 `get_workbench_status`、`emergency_stop_workbench_task` Command 调用 Rust；WebView 不接触 URL、Header、Bearer、长期凭据或系统钥匙串
- 页面闭环：工作台展示后端/Executor 状态、今日任务/成功/失败/待人工指标、当前任务与最近任务；实时投影会覆盖列表中的旧状态。全局紧停要求二次确认，按 Task 复用同一幂等键，提交后失效列表/详情/运行状态并继续以 Executor 最终事件为准；隐藏窗口也持续轮询运行状态
- 分层验证：Backend 全量 `746 passed in 59.23s`；Frontend 33 项 Node 工程契约、96 项 Vitest 和 4 项 Playwright 全绿；Rust all-features 39 项单元、3 项共享协议、10 项安全配置全绿。Ruff/格式、严格 Mypy、ESLint、TypeScript、OpenAPI/Executor Schema 漂移、production boundary、Cargo fmt 与全特性 Clippy 零警告全部通过
- 产品同路径：`uv run python ../scripts/run_t3_16_acceptance.py` 启动隔离 PostgreSQL、完整 Alembic、真实 Uvicorn、HOLD FakeExecutor 和唯一 `visible=false` Tauri/WKWebView。页面先显示真实 running Task 与 Executor online，再真实点击“全局紧急停止/确认紧停”；正式 Rust operation 写入命令，FakeExecutor 收到并 ACK，事件 sequence 1..3 将 Task 收敛到 `outcome_uncertain`、revision 5、lastEventSequence 3
- 失败与恢复矩阵：覆盖 Executor online/offline、未认证、Registry 不可用、非法公开 DTO/UTC 日期、任务列表/运行状态失败脱敏、无当前任务禁用紧停、紧停二次确认、重试复用幂等键、命令提交后权威回拉、后台轮询、实时状态覆盖旧列表、卸载取消和页面无产品登录/注册
- 补充 UI 证据：无头浏览器和现有 Playwright Harness 均验证无登录工作台、空任务状态、紧停禁用、故障重试及零页面错误；Harness 不替代真实 App 验收
- App 与清理：设备私钥和长期凭据仍只在独立 `app_data_dir` 私有文件，不调用系统钥匙串；所有 App 测试全程后台。纵向验收 finally 回收 WDIO、Uvicorn、隔离 PostgreSQL 容器/网络/卷、App 私有测试目录和端口
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构、本路线图、OpenAPI 快照与生成 TypeScript DTO；未新增重复规划文档
- 后续承接：T3-17 新建任务骨架和 T3-18 完整运行详情均已完成；E4 本机 Executor 管理和 H8 业务指标继续按台账实现

### T3-17 新建任务骨架

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- 目标：建立唯一 `douyin.search_exposure.v1` 模板的明确字段、PostgreSQL 持久化、客户端/服务端同形校验、生产 Tauri 创建 Command 和无登录桌面表单；不存任意 JSON，不提前发现目标或执行平台动作
- RED：新增后端模板/OpenAPI/安全失败契约、Frontend 表单/gateway 和生产 Command 工程契约。后端目标 suite 2 项准确失败于空请求模型及有效模板仍被 422 拒绝，既有安全失败矩阵 1 项通过；Frontend 两个 suite 因 `TaskCreate`/gateway 尚不存在而无法加载；Node 工程契约因固定生产 Tauri 创建入口尚不存在而准确失败
- 持久与契约：可回滚迁移 `20260718_0013` 新增 `douyin_search_exposure_definitions`，以 `(task_id, installation_id)` 复合外键绑定父 Task，并只用明确列保存模板版本、关键词、动作、条件消息、`1..100` 目标上限、`1..3600` 有序间隔和固定开启的预览/最终确认。Pydantic/OpenAPI、领域对象、PostgreSQL 约束、生成 TypeScript DTO、Zod 与 Rust 复验共同 fail closed，不保存任意 JSON
- 原子与幂等：创建服务和仓储在一个事务写 Task 与定义；同 Installation/key/完全相同定义返回既有公开快照，同键改任一字段或碰撞旧无定义 Task 固定拒绝。既有旧 Task 仍可查询，公开响应不回显定义、幂等键、Session、凭据或私有路径
- 页面与原生边界：工作台“新建任务”已启用，表单只展示封闭字段和安全校验；`TauriTaskCreationGateway` 只调用固定 `create_douyin_search_exposure_task` Command。Rust 自行从 `app_data_dir` 私有文件换取 Session 并注入请求，React 不接触 BaseUrl、Header、bearer、设备私钥或长期凭据，不使用系统钥匙串
- 分层验证：Backend 全量 `753 passed in 61.98s` 且语句/分支覆盖率 100%；Frontend 35 项 Node 工程契约、100 项 Vitest 和 4 项 Playwright 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置均为 40 项单元、3 项共享协议、11 项安全配置全绿。uv lock、Ruff/格式、严格 Mypy、ESLint、TypeScript、peer dependency、OpenAPI/Executor Schema 漂移、production boundary、Cargo fmt 与三种配置 Clippy 零警告全部通过
- 产品同路径：`backend/.venv/bin/python scripts/run_t3_17_acceptance.py` 后台启动隔离 PostgreSQL、完整 Alembic、真实 Uvicorn 和唯一 `visible=false` Tauri/WKWebView。页面真实进入“新建任务”、填写“新能源汽车”和目标数 12 并点击创建；正式 TypeScript gateway→Rust Command→网络桥写入一条 draft/revision 1 Task 和完全匹配的定义，最终直接核对 PostgreSQL 事实
- 失败与恢复矩阵：覆盖未知模板/字段/动作、空白/控制/过长/敏感文本、动作与消息矛盾、布尔冒充整数、数量/间隔越界和逆序、确认开关关闭、缺失/非法认证与幂等键、吊销 scope、仓储拒绝、同键同定义重放、同键改定义、跨 Installation、并发单赢家、旧数据迁移/碰撞、数据库 check/FK、Rust DTO/metadata/transport 和表单安全重试
- App 与清理：唯一自动化 App 全程隐藏后台运行，不弹窗、不抢焦点；设备私钥和长期凭据只在隔离 `app_data_dir` 私有文件。纵向验收 finally 回收 WDIO、Uvicorn、PostgreSQL 容器/网络/卷、App 测试目录和端口；复核无测试资源遗留
- 真实账号边界：本任务只创建无外部副作用的 draft 定义，不操作抖音，因此无需真实平台账号也不宣称平台验收。可选目标过滤、黑名单/排除、真实平台频控阈值、Candidate 发现与最终状态验收仍由 D6/A7 承接；账号暂不可用时按全局规则用自建测试页继续实现并保持 `🔍 待真实账号`
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构、本路线图、OpenAPI 快照与生成 TypeScript DTO；未新增重复规划文档

### T3-18 运行详情页面

- 状态：✅ 已完成
- 日期：2026-07-18
- 提交：本任务提交
- 目标：用 T3-15 权威快照/SSE 投影建立任务运行详情，展示状态、结构化进度、事件时间线和已有 Action 结果，并从页面正式调用暂停、恢复、取消、紧停；不从事件名伪造未提供的浏览器/平台事实
- 边界：目标摘要、跳过原因、平台证据、浏览器状态与人工接管分别等待 D6/A7/E4/B5 的明确事实；本任务只对已有公开 `actionId` 和 step 事件做脱敏结果投影，缺少事实时展示空态，不以占位数据冒充已执行
- RED：先新增页面、控制契约、固定 Tauri gateway 与产品入口契约测试；Vitest 因 `TaskRunDetails`/`task-run-controls`/gateway 尚不存在而无法加载，Node 契约因四个固定生产 Command 尚不存在而准确失败。实现后第一次隐藏 App 验收已进入真实详情并读到历史，但因 Ant Design 双汉字按钮的可见文本间距导致 WDIO 精确选择器失败；改用受限 XPath 后同链路通过，没有用产品逻辑绕过失败
- 页面与事实：工作台当前/最近任务可进入详情；页面从权威 Query 快照和从 sequence 0 开始的持久 SSE 事件构造状态、revision、水位、时间、结构化进度、当前步骤和最多 200 条时间线。只把明确 `actionId` 的 step 事实投影为进行中/成功/失败；无 Action 时显示空态，终态关闭全部控制
- 控制边界：`TauriTaskRunControlGateway` 只调用 `pause_task_run`、`resume_task_run`、`cancel_task_run`、`emergency_stop_task_run` 四个固定 Command；Rust 复用既有严格网络操作与 App 私有凭据。页面按权威状态启用按钮，取消/紧停二次确认，命令提交不提前伪改状态；同 operation/revision 的不确定重试复用幂等键，响应必须绑定目标 Task 和精确 command type
- 失败与恢复：畸形/错 Task/序号缺口事件立即停止时间线并保留最后权威快照，只能由显式重试从持久起点复核；查询、传输、原生错误均显示固定安全提示，不回显底层内容。测试覆盖断流重试、跨 Task receipt、取消、终态禁用、状态相容控制和 uncertain 重试幂等。全量门禁还暴露 SSE keepalive 的既有时间竞态覆盖偶发降至 99.98%，已用确定性断连序列固定为稳定 100%
- 分层验证：Backend `753 passed`，4045 条语句/772 个分支覆盖率 100%；Frontend 37 项 Node 工程契约、111 项 Vitest 和 4 项 Playwright 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置均为 40 项单元、3 项共享协议、12 项安全配置全绿。Ruff/格式、严格 Mypy、uv lock、ESLint、TypeScript、peer dependency、OpenAPI 漂移、production boundary、Cargo fmt 与三种配置 Clippy 零警告全部通过
- 产品同路径：`backend/.venv/bin/python scripts/run_t3_18_acceptance.py` 启动隔离 PostgreSQL、完整 Alembic、真实 Uvicorn、HOLD FakeExecutor 与唯一 `visible=false` Tauri/WKWebView。页面真实打开两个 Task：第一个从持久时间线读取 started/step started 后依次点击暂停、恢复、取消，第二个点击紧急停止；正式 TypeScript gateway→固定 Rust Command→真实后端 Outbox→Executor ACK→持久事件闭环最终分别收敛为 `cancelled` 与 `outcome_uncertain`
- App、账号与清理：验收核对受控 Task 的 offer/pause/resume/cancel 和紧停 Task 的 offer/emergency-stop 全部 acknowledged，并核对事件序列、revision、watermark、Attempt 终态。App 全程隐藏后台，不弹窗、不抢焦点；设备私钥与长期凭据只在隔离 `app_data_dir` 私有文件，不使用系统钥匙串。finally 回收 App、服务、端口、容器、网络、卷和测试目录
- 真实账号边界：本任务只验证桌面控制面和受控 Executor，无社交平台副作用，不需要真实平台账号也不宣称平台验收。后续真实抖音/小红书行为若暂缺账号，继续用自建测试页/适配器完成工程验收并将平台项保留为 `🔍 待真实账号`，不得阻塞后续任务
- 文档：同步根/Backend/Frontend README、前端架构、工程结构与本路线图；没有新增重复规划文档

### T3-19 UI Harness E2E

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：在 Playwright 测试专用 UI Harness 覆盖创建→运行→暂停→恢复→取消、独立成功任务和页面刷新恢复；同时以唯一 `visible=false` 真实 Tauri App 经正式 TypeScript/Rust/真实后端/Executor 链路重放同一代表流程，Harness 不替代产品入口证据
- 边界：继续使用无平台副作用的受控 Executor，不操作抖音/小红书，不需要真实账号；真实平台最终状态仍不在本任务宣称
- RED：新增显式进入运行详情、Playwright 完整生命周期、隐藏 App 工程契约和安全配置测试。组件测试准确证明当前创建成功会立即调用 `onCreated`；Harness 因没有 Task 测试 Adapter 无法产生创建回执；Node/Rust 分别因 T3-19 隐藏配置、WDIO spec/runner 和注册入口不存在而失败
- 创建交互：创建成功后保留服务端回执，不再自动离开；用户点击“查看运行详情”才进入详情。T3-17 隐藏 App 纵向验收已回归通过，原创建表单→Rust Command→真实 API/PostgreSQL 链路未被破坏
- Harness：新增窄 `TestHarnessTaskLifecycle`，只在 `?scenario=task-lifecycle` 注入，以 `sessionStorage` 保存测试事实。首个任务按页面真实点击暂停、恢复、取消，第二个任务确定性成功；整页刷新后重新进入成功任务，状态与历史仍可恢复
- 生产隔离：正式 Vite 构建仍只有 `index.html`，production boundary 扫描证明不含 Harness 页面、运行标记或测试 Adapter。Harness 只证明 React 交互，不代表 Tauri IPC、Rust、真实网络或 Executor 已通过
- 产品同路径：`backend/.venv/bin/python scripts/run_t3_19_acceptance.py` 启动隔离 PostgreSQL、完整 Alembic、真实 Uvicorn、HOLD/SUCCEED FakeExecutor 与唯一 `visible=false` Tauri/WKWebView。页面真实创建两个定义，首个任务依次点击暂停、恢复、取消，第二个任务成功，再执行整页 WebView refresh 并从工作台/数据库重新读取成功状态
- 最终事实：受控任务的 offer/pause/resume/cancel 命令全部 acknowledged，持久事件为 started、step started、paused、resumed、cancelled，Task 为 `cancelled`、revision 7、watermark 5；成功任务 offer acknowledged，事件为 started、step started、step progress、step completed、task completed，Task 为 `succeeded`、revision 6、watermark 5。两个定义均由同一 Installation 的 App 页面创建，Executor 使用两张精确 `executor.connect` Session
- 失败矩阵：第一次隐藏 App 构建因引用不存在的 `wdio:allow-wdio` 权限而按 Tauri ACL fail closed；第二次因继承 `browserName: webkit` 被 embedded provider 拒绝。最终改为仓库已存在的最小权限集合和 `browserName: tauri`，没有改生产 Capability 或绕过产品路径
- 分层验证：Backend `753 passed`，4045 条语句/772 个分支覆盖率 100%；Frontend 38 项 Node 工程契约、112 项 Vitest、5 项 Playwright 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置均为 40 项单元、3 项共享协议、13 项安全配置全绿。Ruff/格式、严格 Mypy、uv lock/sync、ESLint、TypeScript、peer dependency、OpenAPI 漂移、production boundary、Cargo fmt 与三种配置 Clippy 零警告全部通过
- App、账号与清理：隐藏 App 全程不弹窗、不抢焦点；设备私钥与长期凭据只进入隔离 `app_data_dir` 私有文件，不使用系统钥匙串。T3-17/T3-19 结束均已回收 App、服务、8765 端口、容器、网络、卷和测试目录
- 真实账号边界：本任务无社交平台副作用，不需要真实账号也不宣称抖音/小红书最终状态。后续缺真实账号时继续用自建测试页/隔离 Adapter 完成工程验收，仅把平台最终状态标记为 `🔍 待真实账号`，不阻塞后续 Wave
- 文档：同步根/Backend/Frontend README、前端架构、工程结构与本路线图；没有增加重复规划文档

### T3-20 Control Plane 重启恢复

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：在同一 PostgreSQL 上真实停止并重启 Control Plane，证明 Task、Attempt、Command、Event 与定义不丢失；FakeExecutor 使用同一正式身份和幂等内存账本自动重连、重放未确认命令并继续控制，隐藏 App 刷新后从权威快照恢复
- 边界：本任务仍使用无平台副作用 FakeExecutor，只验证云端 Control Plane 进程恢复和客户端重连语义；Local Executor 进程监管、本机 SQLite 账本与真实 RPA 由 Wave 4 以后实现
- RED：先新增 FakeExecutor 自动重连的真实 WebSocket 测试，以及 T3-20 隐藏 App 配置、WDIO 流程、Rust 注册入口和跨进程 runner 工程契约；后端测试因客户端没有 `run_reconnecting` 准确失败，Node/Rust 分别因 `tauri.task-restart-e2e.conf.json` 等专用桌面验收文件不存在而准确失败
- 重连实现：`FakeExecutorClient.run_reconnecting` 使用同一引擎和正式 WebSocket/Session，按有界次数与正延迟重连；以稳定 Command message ID 计数，因此重投会完整重发首次生成的 ACK/Event 批次，但不会被当成新的业务命令。二进制帧、非 Command 帧、非法预算、非法延迟和预算耗尽统一返回不泄密的 transport unavailable
- 真实 WebSocket：测试先在第一条连接处理 HOLD offer 后以 1012 关闭，再在第二条连接重放同一 offer 并下发 cancel；两次 offer 回执/事件完全相同，重放不占第二个唯一命令名额，cancel 正常收敛。该分层证据覆盖活连接中断；桌面纵向验收另覆盖服务不可达期间的自动连接重试
- 产品同路径：`backend/.venv/bin/python scripts/run_t3_20_acceptance.py` 启动隔离 PostgreSQL、完整 Alembic、首个真实 Uvicorn 和唯一 `visible=false` Tauri/WKWebView。App 页面创建 Task，HOLD FakeExecutor 先经正式 Session/WebSocket 处理 offer；Executor 离线后页面真实提交取消，使 PostgreSQL 形成 offer acknowledged、cancel pending、started/step started 和 `cancelling` 权威快照
- 重启与恢复：runner 真实停止首个 Uvicorn，确认 8765 关闭；App 整页刷新并显示“Control Plane 不可用”。同一 FakeExecutor/Session 在服务不可达时启动有界自动重连，第二个 Uvicorn 以同一环境和 PostgreSQL 重启；App 点击真实“重新检查”恢复工作台，Executor 领取原 pending cancel，ACK 与 cancelled Event 落库，App 再从工作台进入详情读取终态
- 最终事实：重启前 Task 为 `cancelling`、revision 4、watermark 2；重启后为 `cancelled`、revision 5、watermark 3，Attempt 为 `cancelled`。offer/cancel 两条 Command 原 message ID 不变且最终均 acknowledged，started/step started 原 source message ID、Task created_at 和页面创建定义不变；全过程只签发一张 `executor.connect` Session
- 失败矩阵：首次桌面验收已正确进入不可用页，但 WDIO 误写按钮文案“重试连接”，与产品真实“重新检查”不符而失败；只修测试选择器后原链路通过。首次全量覆盖率中 754 项行为均通过但新增失败分支使总覆盖率为 99.77%；补非法上限、二进制/非 Command 帧和预算耗尽测试后达到 100%，未修改生产语义绕过门禁
- 分层验证：Backend `757 passed`，4079 条语句/788 个分支覆盖率 100%；Frontend 39 项 Node 工程契约、112 项 Vitest、5 项 Playwright 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置均为 40 项单元、3 项共享协议、14 项安全配置全绿。Ruff/格式、严格 Mypy、uv lock/sync、ESLint、TypeScript、peer dependency、OpenAPI 漂移、production boundary、Cargo fmt 与三种配置 Clippy 零警告全部通过
- App、秘密与清理：App 全程隐藏后台，不弹窗、不抢焦点；设备私钥与长期凭据只在隔离 `app_data_dir` 私有文件，不使用系统钥匙串。成功与失败运行均回收 App、两个 Uvicorn、8765、PostgreSQL 容器/网络/卷、信号目录和隔离 App 数据
- 真实账号边界：本任务无社交平台副作用，不需要真实账号也不宣称平台最终状态。后续缺账号时继续使用自建测试页/隔离 Adapter，平台最终状态保留 `🔍 待真实账号` 而不阻塞 Wave 4～10
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构与本路线图；没有新增重复规划文档

### E4-01 审计旧 local_executor

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：只读审计 `/Users/aventador/code/agent-platform` 的旧 Local Executor 进程、协议、监管、凭据、配置和 tenant/Core 耦合，形成带来源文件证据的提取/重写/删除/延后清单，作为 E4-02～E4-11 的实现输入
- 边界：本任务不复制旧源码、不修改旧仓库、不提前实现 Executor；当前项目的 Executor v1、无产品账号、单设备、Tauri `app_data_dir` 秘密边界和云端 Control Plane 架构优先于旧项目语义
- RED：先新增 `local-executor-audit.test.mjs` 并实跑；测试准确失败于 `docs/project-structure.md` 缺少 `10.2.1 来源文件覆盖表`，证明原有能力级摘要尚不能逐项锁住旧入口、协议、运行时和 tenant/Core 删除边界
- 来源与旧证据：旧仓库保持在干净提交 `a01cfc9aa93e87e71b78b73eee3e07a3b9d31061`。逐项核对 Rust `local_executor`/测试/`main`/`lib`/React bridge/`SocialOperationsRuntime`、Python 协议/设备账号服务和生成 Schema；旧 `local_executor` 14 项测试全绿，旧协议两项 suite 148 项全绿。`SocialOperationsRuntime` 定向 suite 为 7/8，通过项只作样本，失败闭环在 `invoke` 返回 `ExecutorUnavailable`，未修改废弃仓库掩盖失败
- 迁移结论：只保留可重新测试的进程失败语义；E4-02、E4-06～E4-10 分别重写 bootstrap、一次性 stdin 认证、生命周期、监管、进程树和诊断。旧 `current_exe` 自分叉、同步 stdio 任务通道、任意 `serde_json::Value`、固定 ACK 假 Sidecar、宽 capability Command 与聚合运行时全部删除
- tenant/Core 删除：旧 `tenant_id`、owner/RBAC/Entitlement、`approval_id`、`audit_correlation_id`、Core Artifact、capability/device 选择和旧账号/设备服务不做兼容 Adapter；当前 Installation、Executor v1、Task/Attempt/Action/Event 和出站 WebSocket 是唯一边界。E4-11 从当前需求新建本机账本，不迁旧数据模型
- GREEN：审计合同 1/1 通过；Frontend 全量 40 项 Node 工程契约和 112 项 Vitest 通过，ESLint、TypeScript 与 production boundary/正式 Vite 构建通过。正式产物扫描继续证明审计测试与旧仓库标记没有进入用户构建
- 生产同路径与账号边界：本任务是只读架构审计，没有新增 App 接口、用户功能或平台副作用，因此不启动 Tauri App、不需要真实平台账号，也不把旧测试当当前产品验收；E4-02/E4-12 才用正式 Executor 进程和真实 Control Plane WebSocket 完成纵向链路
- 清理与文档：没有启动本地服务、数据库、浏览器或 App，无运行资源需要回收；旧仓库保持未修改。同步后端架构、工程结构和本路线图，没有新增重复规划文档

### E4-02 Executor Python 入口

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：建立可独立启动的 Python Local Executor 最小进程，通过 stdin 读取一次性受限 bootstrap，完成健康/信号生命周期，并以当前 Executor v1 身份主动连接真实 Control Plane WebSocket；不执行平台动作
- 边界：复用 I2-10～I2-13 当前协议、认证和 FakeExecutor 已验证的连接语义，不复制旧 stdio 任务协议；E4-03 才做 PyInstaller，E4-06 才由 Rust 生成并传递 256-bit 会话令牌，E4-07 以后才由 Tauri 监管进程
- RED：先新增 stdin bootstrap/运行元数据/固定健康投影单元测试和真实 Uvicorn Control Plane→正式认证/Registry→独立 Python 子进程纵向测试；目标 suite 在收集阶段准确失败于 `automation_tool.executor.bootstrap` 与 `automation_tool.executor.runtime` 不存在，证明没有旧实现或 Fake 路径让测试假绿
- Bootstrap 与入口：新增安装脚本 `automation-tool-executor`，只从 stdin 读取一条换行结尾、最大 16 KiB 的严格 JSON object；固定版本、WebSocket URL、短期 `executor.connect` Session、Installation/Executor UUIDv4 和 `1..60` 秒心跳间隔，拒绝重复 key、未知字段、类型强转和超限。`ws` 仅允许 `127.0.0.1` 有效端口，远端仅允许标准 `wss`；Session 由 `SecretStr` 承载，不进 argv、env、健康输出或错误
- 运行与协议：运行平台/架构从进程自身检测，只接受 macOS/Windows 与 arm64/x86_64。正式进程复用唯一 Executor 子协议、禁代理/压缩和 32 KiB 上限，向当前 Control Plane 发送正式 Hello 与单调 Heartbeat；首条心跳存活后只输出固定 `executor.healthy`，SIGINT/SIGTERM 关闭连接后只输出 `executor.stopped`
- 失败矩阵：覆盖空/无换行/超限/非法 UTF-8/非 object/重复 key、版本/字段/URL/Session/ID/心跳非法，unsupported OS/架构，输出失败，连接/时钟/UUID 失败，二进制/文本应用帧、服务器关闭，以及信号与 timeout/close/frame 的四类竞争。提交前审查另以新增 RED 证明显式 falsy Clock 会被 `or` 错换成系统时钟、非法 duration 的固定异常仍挂 transport context；改为显式 `None` 判断和脱离 except 再抛后两项 GREEN。当前收到 Task Command 必须退出，不提前返回 ACK 或事件；连接重试、命令账本和任务处理留给 E4-08/E4-11/E4-12
- 复用与兼容：提取 `executor/transport.py` 给 Fake 与正式进程共享 URL/Session、序列化和 WebSocket 连接逻辑；提取 `protocol/json_object.py` 给 bootstrap 与 Executor envelope 共享有界、无重复 key JSON 解码。既有 FakeExecutor 和 31 个公共协议 fixtures 全量回归未漂移，没有引入旧 social-operations stdio 协议
- 生产同路径：集成测试从 uv 安装生成的 `automation-tool-executor` 可执行脚本启动独立 Python 子进程，通过 stdin 写 bootstrap，连接真实 Uvicorn 的正式 Session 认证、ExecutorConnectionService 和 Registry；服务端最终观测 Hello sequence 1、Heartbeat sequence 2，测试再发真实 SIGTERM，确认退出码 0、固定健康/停止输出和 Registry unregister。没有直接调用 `LocalExecutorProcess.run` 代替该纵向验收
- GREEN：Backend 最终全量 809 项通过，4378 条语句/838 个分支 100%；E4-02 相关 suite 同样保持语句/分支 100%。Ruff、格式、严格 Mypy、`uv lock --check`/`uv sync --locked --all-groups` 和 40 项 Frontend 工程契约全绿。第一次直接用 `.venv/bin/pytest` 跑全量时，集成子进程 PATH 缺少 `alembic` 导致 97 项环境失败；改回项目正式 `uv run --locked` 门禁后全部通过，没有修改产品代码掩盖环境错误
- App、账号与清理：本任务正式消费者是尚未由 Rust 监管的 Executor 控制台入口，不新增 App API 或用户页面，因此不启动 Tauri App、不需要平台账号，也不宣称任务/RPA 验收；E4-07/E4-12 再补隐藏 App/真实 Executor 纵向链路。测试子进程、Uvicorn、socket、线程与隔离 PostgreSQL 资源均由 finally/fixture 回收
- 文档：同步根 README、Backend README、前后端架构、工程结构与本路线图；没有新增第二份规划或实现台账

### E4-03 PyInstaller onedir PoC

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：用唯一正式模块入口构建 macOS/Windows PyInstaller `onedir`，证明用户无需另装 Python即可启动 Executor；本阶段不加入 Playwright，不提前承担 Manifest、签名或 Tauri 监管
- RED：先新增源码模块入口、PyInstaller dev-only 依赖、spec、冻结实包启动和双平台 CI 契约；定向 suite 准确得到 5 个失败，分别是缺少 `executor.__main__`、PyInstaller 依赖、spec、CI job，以及 `No module named PyInstaller`
- 实现：uv 锁定 PyInstaller 6.21.0 及平台依赖，正式运行依赖不含 PyInstaller/Playwright；`automation-tool-executor.spec` 直接执行 `executor/__main__.py` 并以 `EXE(exclude_binaries=True) + COLLECT` 生成 console `onedir`，没有第二份 CLI 逻辑
- macOS GREEN：macOS arm64 从临时目录真实构建冻结产物，使用不含项目 Python 的 PATH 启动；空 stdin 精确返回 bootstrap rejected/退出码 2，合法 bootstrap 连接不可用精确返回 process unavailable/退出码 1，Session 不泄漏。分析 TOC 和目录名均无 Playwright；冻结实包聚焦 9 项、静态/类型/锁文件门禁全绿
- 全量门禁：Backend 最终 `815 passed in 76.80s`，4378 条语句/838 个分支 100%；Frontend 40 项工程契约与 Actionlint 全绿。第一次全量 814 项行为全过但 `__main__` 子进程覆盖未合并且 SSE 时序分支偶发未命中，明确把已由子进程/冻结包验收的入口标为覆盖排除，并用确定性单调时钟测试固定 SSE 分支后恢复 100%，未改业务语义掩盖失败
- Windows 真实边界：`.github/workflows/desktop.yml` 已增加 macOS/Windows `executor-bundle` 矩阵，同一测试不上传、不发布产物。临时分支运行 `29669599452` 的四个桌面 job 均在 0 step、无 runner 阶段失败；GitHub 注解明确为账户近期付款失败或 Actions spending limit 需提高。只读检查确认本机没有 Parallels/VMware/VirtualBox/UTM/QEMU/Wine 或现成 Windows VM，因此不把 macOS、静态契约或 Wine 冒充 Windows 通过
- Windows 本机 GREEN（2026-07-20）：在 Windows x86_64 实体机用锁定环境真实构建 PyInstaller `onedir`，从仅含系统 `PATH` 的环境启动冻结 `.exe`；空 bootstrap 返回 2、连接不可用返回 1，stdout 为空且两类固定 stderr 均为 LF 字节。首次验收暴露 Windows 文本流自动输出 CRLF，已新增跨平台翻译流回归并让正式入口优先写二进制 stderr；单元 7 项与冻结实包 1 项复验全绿
- 失败矩阵：覆盖缺入口/依赖/spec、源码与冻结入口、无 Python PATH、bootstrap 拒绝、WebSocket 连接失败、固定退出码/输出、Session 脱敏、Playwright 依赖与构建分析隔离、macOS/Windows job 配置和工作流只读/无发布；Windows 实际构建/启动已于 2026-07-20 在实体机完成并通过
- 清理：PyInstaller 构建使用 pytest 临时目录并自动删除；无 Executor、PyInstaller、Uvicorn、Docker 或 App 进程残留，`backend/build`/`dist` 持续 Git 忽略。临时 CI 分支在主提交完成后删除；失败运行保留 GitHub 证据
- 文档：同步根/Backend README、后端架构、工程结构和唯一开发台账；没有新增第二份打包或实施文档
- 后续：E4-04 所需的稳定 `onedir` 目录与入口工程依赖已经具备，可继续生成跨平台 Manifest；E4-03 Windows 同一实包测试已于 2026-07-20 在实体机补齐，不阻塞无设备依赖任务

### E4-04 Executor Manifest

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：为整个 PyInstaller `onedir` 建立唯一、确定性、可由下一任务 Rust 独立复验的签名清单；覆盖版本、构建、平台、架构、入口、大小和 SHA-256，不把离线签发私钥或运行时信任职责塞进 App/Python Executor
- 旧代码边界：完整复查旧 `sidecar_package.rs` 799 行；仅迁移 Ed25519、SHA-256、大小、平台/架构绑定和稳定文件 identity 思路，删除旧在线下载器、redirect、`social-operations-sidecar` 名称、自制版本比较与 CrashRecovery 耦合。新模型从单文件改为完整 `onedir` 文件清单
- RED：先加入 Manifest 行为/失败矩阵、真实 CLI、真实 PyInstaller 扩展、双平台 CI 契约和 inert 跨语言 fixture；首跑 3 个模块在收集阶段精确失败为 `No module named automation_tool.executor.package_manifest`，证明没有借用 mock 或旧实现冒充
- 实现：新增唯一 `automation-tool-build-executor-manifest`；离线 Ed25519 seed 只从 stdin 读取精确 32 字节。Manifest v1 使用 compact、键排序 ASCII JSON 加 LF，绑定严格 SemVer、受限 build ID、`macos|windows`、`aarch64|x86_64`、平台精确入口、总大小、目录 SHA-256 和全部普通文件的相对路径/大小/SHA-256；独立签名文件固定 `atems1.<unpadded-base64url>`，签名覆盖 Manifest 原始字节
- 目录摘要：固定域 `automation-tool.executor-package.v1\0`，每个按 ASCII 路径排序的文件依次加入 4 字节大端路径长度、路径、8 字节大端大小和 32 字节原始 SHA-256；metadata 自身不进入 payload，重复运行输出和 Ed25519 签名逐字节一致
- fail closed：拒绝缺失/非目录根、非规范或非 ASCII 路径、Windows 保留字符、symlink、FIFO 等非普通文件、错误/空入口、非法版本/build/platform/arch、非 32 字节私钥、超过 10,000 文件或 8 GiB，以及读取前后 identity 变化；错误只返回固定文案，不回显密钥或私有路径
- 契约与真实边界：Draft 2020-12 Schema 固化 exact fields；固定 `00..1f` 测试 seed 只签 inert fixture，明确禁止发布使用。真实 CLI 子进程从 stdin 签发；真实 PyInstaller macOS arm64 `onedir` 完整清点、签名后仍在无项目 Python PATH 下启动并返回既定错误。实际包无 symlink，因此维持强拒绝边界；不做隐式解引用
- 门禁：聚焦 25 项达到新模块 138 条语句/32 分支 100%；真实 onedir 聚焦共 19 项通过；Backend 全量 `840 passed in 78.21s`，4516 条语句/870 个分支 100%；Ruff、Mypy 161 文件、uv lock 和 Actionlint 全绿。macOS/Windows Executor CI 矩阵已纳入同一 Manifest 单元、真实 CLI 与冻结实包测试；Windows runner 仍继承 E4-03 的 GitHub Billing 外部阻塞，不冒充通过
- 密钥、App 与清理：发布签发私钥不进入仓库、argv、env、日志、构建产物、用户 App 或系统钥匙串；App 用户密钥策略未改变，仍只在 Rust `app_data_dir`。测试 fixture 不是秘密。真实构建使用 pytest 临时目录；另行检查目录已删除，无 PyInstaller/Executor/App/Uvicorn/Docker 进程残留
- 文档：同步根/Backend README、后端架构、工程结构、公共 Schema/fixture 和唯一开发台账；未新增第二份计划文档
- 后续：E4-05 使用 Rust 装配层提供的可信公钥和同一 fixture，在 Rust/Tauri 中 exact-field 解析并复算完整目录，加入平台/架构、SemVer 允许范围、防降级与 TOCTOU 防护；E4-04 不提前宣称运行时可信

### E4-05 Rust 包验证

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：在 Tauri/Rust 原生层验证 E4-04 signed `onedir`，把可信公钥、当前平台/架构、App 允许版本范围和已安装版本共同纳入判断；任何错误包 fail closed，不向 React/服务端暴露信任参数，不提前启动 Executor
- RED：先新增真实 Rust 集成测试和 Node 原生边界契约；Rust 编译精确失败于 `automation_tool_desktop_lib::executor_package` 与 `sha2` 不存在，Node 首次因测试误引用不存在的 helper 失败，修正测试自身后再精确锁定缺少 verifier 文件/依赖，没有把测试错误冒充产品 RED
- 实现：新增 `executor_package.rs`，直接依赖锁定 `ed25519-dalek 3.0.0`、`sha2 0.11.0`、`semver 1.0.28`、`walkdir 2.5.0` 和既有 `base64`；Unix/Windows 文件 identity 分别使用已锁定 `libc 0.2.186` 与 `windows-sys 0.61.2`。构造器拒绝无法解压或 weak 公钥、通配/非法版本要求和非法已安装版本
- 验证顺序：先拒绝根与祖先 symlink，有界稳定读取 Manifest/签名并 `verify_strict`；再 exact-field 解析并重建 compact canonical JSON+LF，重复、未知、缺失、空白或非 canonical 即使由可信 key 签名也拒绝；随后绑定 v1、build ID、当前 `macos|windows`、`aarch64|x86_64` 和平台精确入口
- 版本策略：使用受维护 `semver` crate；候选必须匹配 App 显式允许范围且不得低于已安装版本，默认稳定范围不会意外接纳 prerelease，只有显式包含对应 prerelease comparator 才允许；相等版本允许复验，非法/非规范 SemVer 拒绝
- 完整目录：`walkdir` 不跟随链接并取得排序后的全部 payload，Manifest 文件列表必须严格递增且与实际集合完全相等；每个文件使用 `O_NOFOLLOW` 或 Windows reparse-point 约束安全打开，读前/读后/按路径重开核对 dev+inode 或 volume+file index，复算逐文件大小/SHA-256、总大小和固定域目录摘要。完成后再次枚举目录，验证窗口内成员增删也拒绝
- 失败矩阵：覆盖 current target 成功、Python E4-04 fixture 跨语言验签、显式 prerelease、范围越界、回退、平台/架构错配、错误 signer、weak key、签名 prefix/换行/padding/字符集/长度、可信 signer 下的 unknown/duplicate/noncanonical/非法版本/build/入口、payload 篡改、目录增删和 symlink；错误只返回固定 code/文案，不回显路径或输入
- 本机门禁：Rust 包定向 10 组全绿；默认、`desktop-e2e`、`control-plane-e2e` 三种独立配置均为 40 单元 + 10 package + 3 协议 fixture + 14 安全配置，共 67 项通过；Clippy `--all-targets --all-features -D warnings` 与 Rustfmt 全绿。Frontend 41 项 Node 工程契约、112 项 Vitest、ESLint、TypeScript、正式 Vite/production boundary 全绿；不带测试驱动的 `pnpm tauri build --debug --no-bundle` 成功产出正式 App 二进制但未启动。Backend 共享 contract 回归 `840 passed in 76.64s`，4516 条语句/870 个分支 100%
- Windows 真实边界：正式 `.github/workflows/desktop.yml` 已因 `frontend/**` 在 macOS/Windows runner 执行 `pnpm test:rust` 和桌面构建，Windows 专属 reparse point/file identity 代码会在原生 target 编译运行。本机现有 Homebrew Rust 不含 `rustup`，不为一次交叉检查再安装并保留第二套 Rust；临时分支运行 `29670987419` 的四个 job 均为 0 step、未分配 runner，Windows Rust/Tauri 与 macOS job 注解都再次明确为账户近期付款失败或 Actions spending limit。该轮当时不能声明 Windows 已通过，失败运行保留证据；2026-07-20 已由本机原生实体环境补齐
- Windows 重试：按用户要求从 `main` 手工触发正式矩阵运行 `29671164126`；两个 Windows job 与两个 macOS job 仍全部 0 step，check-run 原文仍为近期付款失败或需提高 spending limit，证明限制尚未恢复而非产品测试失败。E4-03/E4-05 在该轮当时继续保留 `🔍`；2026-07-20 本机原生验收现已补齐
- Windows 本机 GREEN（2026-07-20）：Windows x86_64/MSVC 原生编译并运行 `executor_package` 8 项集成测试，当前平台/架构、签名、摘要、完整目录、SemVer 范围、防回退与失败关闭全部通过；`executor-package-boundary` 公开边界契约通过。验收 Cargo 产物使用项目内隔离目录，避免覆盖正在运行且被 Windows 锁定的桌面 `.exe`
- App、密钥与清理：本任务没有 Tauri Command、页面、网络或 App API，正式消费者是 E4-07 Rust 进程生命周期，故不启动/弹出 App；测试从公开 Rust verifier 原入口调用，不用 mock。发布公钥尚未由 E4-07 装配，测试 seed 只存在测试/fixture；设备私钥与长期凭据仍只在 `app_data_dir`，不使用系统钥匙串。临时目录由 RAII 清理，无子进程、服务、容器或 App 残留
- 文档：同步根/Frontend README、前端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：E4-06 实现 Rust 生成 256-bit stdin 一次性认证并与 Python bootstrap 绑定；E4-07 再从 App 受信资源与编译配置提供固定公钥/版本策略/包路径，验证后监管真实 Executor。E4-05 Windows 原生验收已于 2026-07-20 补齐

### E4-06 stdin 随机认证

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：为每次 Rust→Python Local Executor 启动建立独立 256-bit 本机会话，令牌只经现有单行 stdin bootstrap 传递，不进入 argv、环境、日志、错误或响应；不能把 Control Plane `executor.connect` Session 冒充本机认证
- RED：先新增 Python 认证/严格 bootstrap 测试和 Rust 公共边界集成测试；Python 收集准确失败于 `automation_tool.executor.authentication` 不存在，Rust 编译准确失败于 `executor_bootstrap` 模块与 `hmac` 依赖不存在，证明没有复用旧 Sidecar 或云端 Session 路径让测试假绿
- Rust：新增 `executor_bootstrap.rs`，锁定 `hmac 0.13.0` 并为 HMAC/SHA-256 打开 `zeroize` feature；`LocalSessionToken::generate` 直接使用既有系统 `getrandom` 取得 32 字节，不实现 Clone，Debug 固定脱敏，Drop 通过 `zeroize` 清除。`ExecutorBootstrapInput` 先限制 loopback `ws`/标准端口 `wss`、UUIDv4、心跳和 Control Plane Session，再把两个用途隔离的 Session 写成一条不超过 16 KiB 的 JSON+LF；没有进程、argv、env、日志、网络或 Tauri Command API
- Python：新增 `authentication.py`，只接受 64 位小写十六进制 `local_session_token` 并保留在 `SecretStr`/可清零 bytearray。健康与停止响应增加 `authenticationProof`，固定为 `atlep1.<base64url HMAC-SHA-256>`；MAC 输入绑定 `automation-tool.local-executor-event.v1` 域、精确事件名和 Executor `1.0` 协议，原令牌永不进入 stdout。CLI 无论 bootstrap、认证、网络或输出失败都映射既有固定错误并在退出时清零认证器
- 常量时间与重放边界：Rust 使用 `hmac::Mac::verify_slice` 校验 32 字节证明，先做严格 envelope/base64url 长度检查；每次启动随机 key 使旧进程证明不能跨启动复用，事件名与协议绑定使 healthy 证明不能冒充 stopped。Python/Rust 固定 `00..1f` 测试向量逐字一致；该向量只存在测试，不是发布或设备秘密
- 验收：Python 聚焦 68 项含已安装控制台脚本→真实 Uvicorn/Session/Registry→信号退出的独立子进程链路；新认证模块 39 条语句/8 分支 100%；Backend 全量 `855 passed in 76.21s`，4571 条语句/878 个分支 100%。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置均为 42 单元 + 3 bootstrap + 10 package + 3 协议 fixture + 14 安全配置，各 72 项；固定向量、随机唯一性、stdin framing、错误写入、错事件/错 proof 和非反射均通过。Frontend 42 项 Node 契约、112 项 Vitest、ESLint、TypeScript、OpenAPI 和 production boundary 全绿；Ruff、Mypy 84 个源码文件、uv lock、Clippy all-targets/all-features 与 Rustfmt 全绿。不带测试驱动的 `pnpm tauri build --debug --no-bundle` 成功产出正式二进制且未启动 App
- App 与原始入口：本任务是 E4-07 的 Rust/Python 启动认证原语，没有新增页面、Tauri Command 或用户可调用功能，因此不启动 App、不用 Mock 冒充桌面验收。真实 Python Executor 已从安装后的正式控制台入口验证证明和秘密不反射；E4-07 再由公开 Rust Manager 原入口生成同一 bootstrap、启动签名包并验证健康 proof，Tauri Command/React 产品入口归 E4-13/E4-14
- 密钥与清理：本机会话只在进程内存和 OS pipe 中短暂存在，不写 `app_data_dir`、系统钥匙串、仓库或服务器；它不是用户配置密钥。测试无 App、容器或遗留 Executor 进程；PyInstaller 临时目录由 pytest 回收
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构和唯一开发台账；没有新增第二份计划
- 后续：E4-07 组合 E4-05 包 verifier 与本模块，实现 `ExecutorManager start/status/stop`、单实例和并发线性化，并从公开 Rust 原入口验收 Rust→stdin→真实 Python→HMAC proof 全链路

### E4-07 Rust ExecutorManager

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：组合 E4-05 signed onedir verifier 与 E4-06 一次性 stdin 认证，建立唯一 `ExecutorManager`；只提供固定 start/status/stop 生命周期、单实例与并发线性化，不恢复旧 stdio `invoke`、任意 JSON、capability 命令或可选子进程路径
- RED：先加入 Rust Manager 生命周期集成测试和 Node 架构边界；Rust 准确编译失败于 `automation_tool_desktop_lib::executor_manager` 不存在，Node 准确失败于 `src-tauri/src/executor_manager.rs` 不存在。随后才实现生产模块，没有复用旧 `SocialOperationsRuntime` 或 Mock Manager 让测试假绿
- 实现：`executor_manager.rs` 以一个 Mutex 线性化生命周期；start 每次先复验完整签名目录，再使用 Manifest 精确入口创建无参数子进程，唯一 stdin 写入 E4-06 bootstrap 后立即关闭，stdout 只接受 4096 字节内、LF 结尾、严格字段的 healthy/stopped 事件并常量时间验 proof；status 先 `try_wait` 收敛自然退出，stop 幂等并在证明/退出超时或发信号失败时强制回收直接子进程。公开状态只含 running/stopped、版本和 build ID，不含 PID、路径、Session 或 stderr
- 并发与失败矩阵：8 个并发 start 精确 1 个成功、7 个 `AlreadyRunning`；覆盖包篡改、配置错误、错 proof、启动静默超时和重复 stop。错误 Display/Debug 固定安全，Control Plane Session 由 `Zeroizing<String>` 持有，本机会话仍只存在于 Rust/Python 内存与 pipe。E4-08 才加退出监管/重启预算，E4-09 才加 Unix process group/Windows Job Object，E4-10 才保留限界脱敏 stderr
- 正式原入口验收：`scripts/run_e4_07_acceptance.py` 临时构建真实 PyInstaller onedir，使用正式 Manifest CLI 从 stdin 签名，启动真实本地 Uvicorn/Session/Registry，再把仅含配置文件路径的环境变量交给被忽略的公开 Rust Manager 测试；Manager 自己生成本机会话、启动冻结 Executor 并执行 start/status/stop。服务端最终事实严格为 `registered → heartbeat → unregistered`，Control Plane Session 不出现在 Cargo stdout/stderr。临时配置权限为 `0600` 且随临时目录删除
- 门禁：Manager 4 项普通测试通过，真实 PyInstaller/Uvicorn 编排验收通过；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置均通过，新增并发夹具目录以 PID/时间/原子序号隔离；Clippy all-targets/all-features 与 Rustfmt 全绿。Backend 全量 `855 passed in 80.58s`、4571 条语句/878 个分支 100%；Ruff、Mypy 164 个源码文件、uv lock 全绿。Frontend 43 项 Node 契约、112 项 Vitest、ESLint、TypeScript、OpenAPI、生产边界与正式 `pnpm tauri build --debug --no-bundle` 全绿；构建 App 但未启动窗口
- App 与验收边界：本任务新增的是 Rust 内部生命周期 API，没有注册 Tauri Command、页面或 App 可调用接口，所以正式原入口是公开 Rust Manager，不启动 App。E4-13 才通过固定 PlatformAdapter/Tauri Commands 暴露状态、重启、诊断和紧停，E4-14 再由唯一 `visible=false` App 完成桌面生命周期验收；不能把本任务的 Rust 测试冒充届时的 App 验收
- 历史 CI 状态（2026-07-19）：GitHub Actions run `29671164126` 的 macOS/Windows 四个 job 因账户付款或 spending limit 全部 0 step；当时未把 Windows 原生包/Manager 冒充为通过。2026-07-20 已由下述本机原生 GREEN 补齐，外部 Billing 不再影响本任务验收结论
- Windows 本机 GREEN（2026-07-20）：正式脚本已支持 Windows/`AMD64` 并改用跨平台 `executor_manager_packaged` Rust 目标，且显式要求 `1 passed; 0 failed`，杜绝原 macOS-only 目标在 Windows 上 0 tests 假绿。真实 signed PyInstaller Executor 经公开 Manager 完成 start/status/stop，Control Plane 事实为 `registered → heartbeat → unregistered`，SQLite schema v2 身份与秘密不落库检查通过。修复了生命周期 stdout CRLF、Windows 强制终止后错误等待 stopped proof、SQLite 连接文件锁及脚本 schema v1 过期断言
- 清理：Manager 失败/Drop 都回收直接子进程并 join stdout/stderr reader；验收 Uvicorn、线程、socket、临时 PyInstaller 包、签名、私有配置和测试子进程均在 finally/RAII 回收。正式生产构建未启动 App；进程检查无 Executor/Uvicorn/Cargo 测试残留
- 文档：同步根/Frontend README、前端架构、工程结构和唯一开发台账；没有新增第二份计划
- 后续：E4-08 在 Manager 上增加后台退出检测、有界重启预算与显式停止不重启；E4-03/E4-05/E4-07 Windows 原生验收已于 2026-07-20 在本机补齐

### E4-08 进程监管

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：在 E4-07 唯一 Manager 内后台发现 Executor 非预期退出，以显式有界策略最多恢复两次；显式 stop、正常退出、固定 bootstrap/process 失败、坏包或坏认证均不得形成重启循环，不新增第二 Supervisor 或宽进程接口
- RED：先扩展公开 Rust Manager 测试与独立 Node 架构契约；Rust 准确失败于 `ExecutorRestartPolicy`、第五个 Manager 构造参数和 `restart_count` 不存在，Node 准确失败于缺少显式策略/监管线程，证明不是轮询测试代码或旧 Manager 让测试假绿
- 实现：`ExecutorRestartPolicy` 显式接收 `maximum_restarts`、monitor interval 和 restart delay，拒绝超过 8 次、零时长或 60 秒以上配置；当前 MVP 测试/装配预算固定为 2。Manager 内部仅有一个命名 supervisor thread 和 Mutex 状态机，状态扩为 `running/restarting/stopped`，公开 `restartCount` 不暴露 PID、路径或 Session。每次恢复仍重新执行 E4-05 整包验证、生成新的 E4-06 本机会话并等待认证 healthy，不复用旧进程 proof
- 重启判定：macOS/Unix 只把 signal 终止视为可重启崩溃；正常退出 0、固定 process failure 1 和 bootstrap failure 2 都停止且不消耗新预算。Windows 只把负 NT 异常状态视为崩溃，并已于 2026-07-20 用真实冻结进程连续异常退出验收。显式 stop 在同一线性化锁内先移除 running/pending 生命周期再终止进程，因此 supervisor 无目标可重启；Manager Drop 先关闭/join supervisor，再回收直接子进程
- 失败矩阵与事实：真实签名测试进程前两次 healthy 后收到 SIGKILL，后台恰好启动第三个进程并报告 `restartCount=2`；持续 SIGKILL 也只产生初次+两次恢复，之后稳定 stopped。另行验证显式 stop 后启动次数保持 1、退出码 0/1 均不恢复、非法预算/时长拒绝、E4-07 并发/超时/坏包/坏 proof 全部继续通过。测试计数文件位于独立临时路径，不修改已签名包，RAII 删除
- 正式路径回归：E4-07 的真实 Manifest CLI→signed PyInstaller onedir→公开 Rust Manager→真实 Uvicorn/Session/Registry 验收在加入 supervisor 后再次通过，事实仍为 `registered → heartbeat → unregistered`。本任务的崩溃预算从公开 Manager 原入口驱动真实 OS 子进程与 SIGKILL；正式 PyInstaller 崩溃、隐藏 App 与整棵进程树联合验收按路线图归 E4-14，不能以当前 fixture 冒充
- 门禁：Manager 8 项普通测试通过、1 项 PyInstaller 编排项按专用脚本通过；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置各为 42 单元 + 3 bootstrap + 8 manager + 10 package + 3 协议 fixture + 14 安全配置，共 80 项全绿。Frontend 44 项 Node 契约、112 项 Vitest 全绿；Backend 回归 `855 passed in 88.12s`、4571 条语句/878 个分支 100%；正式 `pnpm tauri build --debug --no-bundle` 成功且未启动 App。Clippy all-targets/all-features、Rustfmt、ESLint、TypeScript、OpenAPI、production boundary、Ruff、Mypy 和 uv lock 继续全绿
- App、凭据与清理：本任务仍只有 Rust 内部生命周期 API，没有 Tauri Command/React 页面，因此不启动 App；E4-13/E4-14 才做固定桌面入口。Control Plane Session 为受监管重启仅保留在 `Zeroizing<String>` 内存，新的本机会话每次独立生成；不写系统钥匙串、`app_data_dir`、环境、argv 或日志。验收后无 Executor、supervisor、Cargo、Uvicorn、PyInstaller 或临时计数文件残留
- 历史 CI 状态（2026-07-19）：Hosted Runner 的 Billing/Actions spending limit 未恢复，因此未重复空跑 workflow；当时 Windows crash code、强制停止和 Job Object 原生语义未宣告通过。2026-07-20 已由下一条本机 GREEN 完成同一矩阵
- Windows 本机 GREEN（2026-07-20）：新增非零测试保护的 `run_e4_08_acceptance.py`，对真实 signed PyInstaller Executor 连续注入三次 `0xc0000005` NT 异常退出；唯一 Supervisor 前两次重新验包、生成新本机会话并恢复到 running，`restartCount` 精确为 1/2，第三次稳定 stopped。Control Plane 三轮均为 `registered → heartbeat → unregistered`，Session 未反射，临时包和进程已回收
- 文档：同步根/Frontend README、前端架构、工程结构和唯一开发台账；没有新增第二份计划
- 后续：E4-09 给每次启动建立 Unix process group/Windows Job Object，超时、显式 stop、Manager Drop 和挂起边界必须清理完整进程树；之后 E4-10 做 stderr 限界脱敏

### E4-09 超时与进程树清理

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：每次启动时建立独立 OS 进程容器，使正常停止、停止超时、启动超时、异常退出和 Manager Drop 都能清理 Executor 及其全部后代；Unix 使用独立 process group，Windows 使用启动即挂入且关闭即杀的 Job Object
- RED：先从公开 Rust Manager 原入口启动真实签名测试进程，并让它生成一个忽略 `SIGTERM`、stdio 与主进程分离的真实孙进程；显式 stop、挂起 stop、启动超时和 Drop 四条测试准确全部失败于孙进程仍存在，既有 8 条 Manager 测试仍绿。独立 Node 边界准确失败于缺少 `process_group(0)`/Windows Job Object 结构，证明不是 PID 扫描、Mock 或测试清理代码让结果假绿
- Unix 实现：在 `Command::spawn` 前调用 `CommandExt::process_group(0)`，spawn 成功后以已验证正 PID 固化 PGID；强制边界只向负 PGID 发 `SIGKILL`，`ESRCH` 视为整组已退出。正常 stop 仍只向主进程发 `SIGTERM` 并验证 stopped HMAC proof，主进程退出后再清理仍存活的后代；setup 失败、启动/停止超时、异常退出准备恢复和 Drop 都先杀树、等待主进程，再 join stdout/stderr reader
- Windows 实现：显式启用 Foundation/Security/ToolHelp/JobObjects/Threading feature，使用 `CREATE_SUSPENDED` 保证 Executor 无法在挂 Job 前派生逃逸进程；随后创建并配置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`、挂入 child、枚举并恢复初始线程。创建、配置、挂载或恢复任一步失败都终止 suspended child 并关闭 Job；强制边界以 `TerminateJobObject` 加 kill-on-close 双重收敛。当前 Homebrew Rust 只有 macOS 标准库且无 `rustup`，因此不叠装冲突工具链，也不把静态结构当 Windows 类型/行为通过
- 失败矩阵与事实：真实签名主进程生成忽略 `SIGTERM` 的真实孙进程；认证正常 stop、主进程忽略信号导致 stop 超时、静默启动导致 healthy 超时、Manager Drop 后孙进程 PID 都在时限内消失。异常恢复另用首代主进程 `SIGKILL` 证明 supervisor 先清理首代孙进程，再启动第二代；显式停止第二代后也无残留。重复 stop、并发 start、两次崩溃预算、包/证明拒绝和正常/固定失败退出继续回归
- 正式路径回归：使用 Backend 锁定 uv 环境重跑 `scripts/run_e4_07_acceptance.py`，真实 Manifest CLI→signed PyInstaller onedir→公开 Rust Manager→Uvicorn/Session/Registry 继续通过，事实仍为 `registered → heartbeat → unregistered`；本任务没有 Tauri Command/React API，隐藏 App 联合验收仍归 E4-14
- 门禁：Manager 13 项普通测试通过、1 项 PyInstaller 编排项由专用脚本通过；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置各为 42 单元 + 3 bootstrap + 13 manager + 10 package + 3 协议 fixture + 14 安全配置，共 85 项全绿。Frontend 45 项 Node 契约、112 项 Vitest 全绿；Backend 回归 `855 passed in 79.83s`、4571 条语句/878 个分支 100%。正式 `pnpm tauri build --debug --no-bundle` 成功且未启动 App；Clippy all-targets/all-features、Rustfmt、ESLint、TypeScript、OpenAPI、production boundary、Ruff、Mypy 163 个源码文件和 uv lock 全绿
- 挂起语义：当前 Manager 刻意没有旧 stdio task invoke，因此本任务只处理启动/停止生命周期挂起，不能声称已经处理 RPA 外部副作用超时；真实命令执行、取消和 `OUTCOME_UNCERTAIN` 由 E4-12 及后续 BrowserRuntime 原入口验收
- 凭据与清理：进程树对象只保存 OS PGID/Job handle，不暴露 PID 给 WebView，也不新增 argv/env/日志/钥匙串或持久秘密；Control Plane Session 和每次本机会话仍只在清零内存/stdin pipe。测试孙进程、Executor、Uvicorn、PyInstaller 临时包、PID marker 与计数文件均由原入口/RAII 回收
- 历史 CI 状态（2026-07-19）：GitHub Actions run `29671164126` 因 Billing/Actions spending limit 0 step 失败，因此当时未宣告 Job Object 原生通过。2026-07-20 已由下一条本机 GREEN 补齐类型检查、打包进程树及正常/超时/Drop/崩溃矩阵
- Windows 本机 GREEN（2026-07-20）：`run_e4_09_acceptance.py` 将测试专属 Python 探针冻结、签名为真实 Windows `.exe`，探针派生脱离 stdio 的长驻 PowerShell 后代。公开 Manager 在显式 stop、挂起后 stop、healthy 启动超时、Manager Drop、异常退出并恢复五条路径均清理完整 Job；崩溃首代后代在第二代 running 前消失，第二代停止后亦退出。严格 Clippy 通过，验收后无探针后代残留
- 文档：同步根/Frontend README、前端架构、工程结构和唯一开发台账；没有新增第二份计划
- 后续：E4-10 在现有 stderr reader 上实现凭据/私有路径脱敏与行数、单行、总大小三重限界；之后依次推进 E4-11～E4-15

### E4-10 stderr 脱敏限界

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：只从真实 Executor stderr 异步读取诊断，Rust 在信任边界再次脱敏并以 200 行、单行 4096 bytes、总计 64 KiB 三重上限保存在内存；超长/非法编码输入不能造成无界分配或以截断半段绕过秘密识别
- RED：新增三端共享 `executor-diagnostics-v1` fixtures，Python 测试准确失败于正式 redactor 模块不存在；公开 Rust Manager 测试准确编译失败于没有 `diagnostics()`，Node 边界准确失败于缺少独立诊断模块。真实签名进程测试从 stderr 写入凭据、Cookie、URL 查询串、私有路径、控制字符及 400 行/超长行，不能以直接调用 redactor 或 Mock pipe 冒充通过
- 流式读取：`read_bounded_diagnostic_line` 只使用 `BufRead::fill_buf/consume` 和固定 4096-byte Vec；无换行恶意输出会持续消费但不扩大缓冲。精确超过单行上限时丢弃内容并仅保留 `[TRUNCATED]`，非法 UTF-8 仅保留 `[REDACTED]`，避免先截断再脱敏导致半段令牌绕过；CRLF 只移除末尾 CR，内嵌控制/Bidi 字符统一为空格
- 脱敏与一致性：根目录 14 组 `executor-diagnostics-v1` fixtures 覆盖 Authorization/Bearer、设备/本机会话 envelope、64 位本机令牌、敏感 JSON/assignment、抖音/平台 Cookie、URL userinfo/全部 query、file/data URL、macOS/Linux/Windows 私有路径与控制字符。Python `executor/diagnostics.py` 和 Rust `executor_diagnostics.rs` 逐字回放同一结果；Rust 不信任 Python，仍对所有原始 stderr 重做规则
- 内存边界：Manager Core 持有唯一 `ExecutorDiagnostics`，首次启动和最多两次恢复共享同一滚动队列；每条先安全化再计 UTF-8 bytes，超过 200 行或 64 KiB 时从最旧项开始淘汰。公开 `diagnostics()` 只克隆安全行，锁失败返回既有固定 `ProcessUnavailable`，不返回原始 stderr、PID、路径、Session 或内部错误
- 真实失败矩阵：第一项真实 signed Python 进程在 healthy 前从 stderr 写入全部共享 fixture，Manager 原入口最终逐行只返回 expected；第二项真实进程写 400 行含秘密的 1000-byte 诊断、一个 5000-byte 无秘密行和完成哨兵，最终同时满足行数、单行、总字节、秘密消失及固定超长占位。E4-07～E4-09 的并发启动、重启预算、超时和进程树 13 项继续通过
- 正式路径与 App：Backend 锁定环境的真实 Manifest CLI→signed PyInstaller onedir→公开 Rust Manager→Uvicorn/Session/Registry 再次通过 `registered → heartbeat → unregistered`。本任务只新增 Rust 内部安全副本 API，没有 Tauri Command/React 页面，因此构建但不启动 App；E4-13 才装配固定诊断展示，E4-14 再由唯一隐藏 App 验收
- 门禁：Manager 15 项普通测试通过、1 项 PyInstaller 编排项由专用脚本通过；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置各为 44 单元 + 3 bootstrap + 15 manager + 10 package + 3 协议 fixture + 14 安全配置，共 89 项全绿。Frontend 46 项 Node 契约、112 项 Vitest 全绿；Backend `856 passed in 77.62s`、4609 条语句/878 个分支 100%。正式 `pnpm tauri build --debug --no-bundle` 成功且未启动 App；Clippy all-targets/all-features、Rustfmt、ESLint、TypeScript、OpenAPI、production boundary、Ruff、Mypy 165 个源码文件和 uv lock 全绿
- 凭据与持久化：原始 stderr 只在 OS pipe/有界 reader buffer 短暂存在，安全诊断只在 Manager 内存；不写仓库、普通配置、`app_data_dir` 或系统钥匙串，也不上传 Control Plane。Python/Rust fixtures 只含明确测试秘密，没有真实账号、Cookie 或设备凭据
- 历史 CI 状态（2026-07-19）：当时本机 Homebrew Rust 无 Windows 标准库且 Hosted Runner 受 Billing 限制，因此未以 macOS 结果冒充 Windows signed PyInstaller stderr 验收。2026-07-20 已由本机 GREEN 补齐 CRLF、超长无换行和 Manager 恢复共享缓冲矩阵
- Windows 本机 GREEN（2026-07-20）：测试专属诊断探针冻结并签名为真实 Windows `.exe`，从真实 stderr pipe 写入共享 14 组敏感 fixture、CRLF、非法 UTF-8、400 行和 5000-byte 无界输入；Rust Manager 的输出逐字匹配共享 expected，并在异常退出恢复第二轮后保持 73 行、单行不超过 4096 bytes、总计不超过 64 KiB，包含固定 `[TRUNCATED]`/`[REDACTED]` 且 Session 消失。非零目标、Clippy 和 Node 边界全绿
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构和唯一开发台账；没有新增第二份计划
- 后续：E4-11 建立 Executor 本机 SQLite command/idempotency/checkpoint/outbox 最小账本与迁移，再由 E4-12 从真实 Control Plane 回放无副作用任务

### E4-11 Executor 本机 SQLite

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：在 App 私有 Executor 状态目录内建立固定 `executor-ledger.sqlite3` v1，把正式命令双键幂等、Attempt checkpoint 与协议 outbox 持久化；Executor 崩溃/重启后可安全重放，但不能复制云端完整业务库或保存任何会话/平台秘密
- RED：先新增正式 `ExecutorLedger` 测试并实跑，收集阶段准确失败于 `automation_tool.executor.ledger` 不存在；没有借用 FakeExecutor 的进程内字典、Control Plane PostgreSQL 仓储或 Mock SQLite 让用例假绿。首版 GREEN 后再从 Rust/CLI 原入口补状态目录和真实独立进程事实
- Bootstrap 与装配：Rust `ExecutorBootstrapInput`/`ExecutorLaunchConfiguration` 增加受限绝对 `state_directory: PathBuf`，同一次性 stdin JSON 传给 Python；相对、根、含 `..`、控制字符、非 UTF-8 或超长路径拒绝。Python CLI 在建立 WebSocket 前完成账本打开/迁移，失败只返回固定 process unavailable。当前没有 Tauri Command/React 路径参数；E4-13 必须从 Tauri `app_data_dir` 派生固定子目录
- v1 schema：`PRAGMA user_version=1` 原子创建且只创建 `executor_identity`、`executor_commands`、`executor_attempt_checkpoints`、`executor_outbox`。数据库绑定唯一 Installation/Executor；未来版本、缺表、身份错绑和迁移损坏 fail closed。使用 Python 内置 `sqlite3`，没有增加 ORM、Alembic 或第二数据库服务
- command/idempotency：正式 `TaskCommandEnvelope` 才能入账；message ID 与 idempotency key 各自唯一，去除 wire 重试身份后的 canonical intent 计算 32 字节 SHA-256。同一意图重放返回首次 receipt，任一双键碰撞但意图改变立即拒绝；Attempt 绑定唯一 Task，command sequence 必须从 1 连续递增
- checkpoint/outbox：每个 Attempt 保存连续 command sequence、单调 event sequence、封闭 received/running/paused/terminal/outcome_uncertain 状态和正 revision；`BEGIN IMMEDIATE`+revision CAS 的两个真实连接同时更新只有一个赢家。outbox 只接受正式 Result/Event envelope，必须匹配来源 command 的 Task/Attempt/correlation，按 ordinal 精确回放；delivered 标记持久且幂等，未知 message 不伪成功
- 文件与秘密边界：固定数据库文件与状态目录在 Unix 分别为 `0600`/`0700`，所有祖先 symlink、Windows reparse point、非普通 leaf、宽权限和打开前后 directory/file identity 变化均拒绝。SQLite 不保存 Control Plane Session、本机 256-bit 会话、Cookie、平台登录态、密钥、浏览器 Profile 或任意 App 配置，也不调用系统钥匙串；真实进程验收扫描数据库原始字节确认 Session 不存在
- 失败矩阵：覆盖空库迁移/重开、身份再绑定拒绝、未来/缺表 schema、双键双行冲突、首次 sequence 非 1/缺口/Task 混用、非法身份和类型、真实并发 CAS/旧 revision/事件倒退、outbox 来源/身份/碰撞/损坏/非法 batch/未知 delivery，以及目录/文件权限、symlink、非普通文件和 identity 替换竞态。`ledger.py` focused 语句/分支覆盖率 100%
- 正式路径：安装后的 `automation-tool-executor` 独立子进程经 stdin→CLI 先创建 SQLite，再连接真实 Uvicorn/正式 Session/Registry，最终验证 Hello/Heartbeat、v1/identity、秘密不落库和 SIGTERM。`scripts/run_e4_07_acceptance.py` 另从真实 Manifest CLI→signed PyInstaller onedir→公开 Rust Manager→同一 Python CLI→Uvicorn 跑通 `registered → heartbeat → unregistered`，并在临时目录删除前直接读取 SQLite v1/identity
- App 与验收口径：E4-11 的正式消费者仍是 Rust Manager/Python CLI，没有 Tauri Command、React 页面或用户可见功能，所以本任务不启动 App；接口验收来自真正启动该功能的 stdin/CLI/Manager 原入口，不用内部函数或 Mock 冒充。E4-12 才消费账本处理真实任务帧，E4-13/E4-14 再通过唯一 `visible=false` App 验收 `app_data_dir` 装配
- 门禁：Backend `866 passed in 75.69s`，4899 条语句/946 个分支覆盖率 100%；Frontend 47 项 Node 契约、112 项 Vitest 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置各为 44 单元 + 3 bootstrap + 15 manager + 10 package + 3 协议 fixture + 14 安全配置，共 89 项全绿，1 项 PyInstaller 编排由专用脚本通过。Clippy all-targets/all-features、Rustfmt、Backend 正式范围 167 个文件 Ruff 格式/lint、严格 Mypy 167 个源码文件、uv lock、ESLint、TypeScript、OpenAPI 漂移和 production boundary 全绿；正式 `pnpm tauri build --debug --no-bundle` 成功且未启动 App
- 历史 CI 状态（2026-07-19）：当时本机无 Windows 标准库且 Hosted Runner 受 Billing 限制，因此未以 macOS 或静态契约冒充目录 ACL/reparse、SQLite frozen runtime 与 Rust Manager 真链路。2026-07-20 已由本机 GREEN 补齐这些原生矩阵
- Windows 本机 GREEN（2026-07-20）：账本 10 项覆盖空库/v1→v2 迁移、重开、双键幂等、CAS、outbox、未来/损坏 schema、Junction/reparse 与 identity race；新增 Win32 DACL 解析，目录和数据库向 Authenticated Users 授权时 fail closed。CLI/真实进程 9 项通过 `CREATE_NEW_PROCESS_GROUP + CTRL_BREAK_EVENT/SIGBREAK` 完成 authenticated stopped，SQLite v2 身份及两个 Session 原始字节均不落库；正式 signed PyInstaller→Rust Manager→Uvicorn 链路复验通过
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构和唯一开发台账；没有新增第二份计划或重复 implementation plan
- 后续：E4-12 从真实 Control Plane 向同一正式 Executor 下发无副作用 Task Command，使用本账本生成/重放 ACK 与 Event，并验证崩溃恢复闭环

### E4-12 真实协议回放

- 状态：✅ 已完成；Windows x86_64 原生全链路已验收
- 日期：2026-07-19
- 提交：本任务提交
- 目标：让正式 Local Executor 从生产 WebSocket 接收 Control Plane 持久 `task.offer`，先落 E4-11 本机账本，再产生无平台副作用的固定成功 ACK/事件；进程重启后只能重放首次持久消息，不能重新生成身份、重复推进云端事实或保存 Session
- RED：先新增 `test_command_processor.py` 并实跑，测试收集准确失败于 `automation_tool.executor.command_processor` 不存在；覆盖首次处理、同 message/idempotency 重试、重开恢复、过期/错身份/控制命令拒绝及 ID 生成中断。没有调用 FakeExecutor、内部 Mock WebSocket 或直接写 Control Plane 数据库让验收假绿
- 原子处理：`ExecutorCommandProcessor` 只接受匹配 Installation/Executor、deadline 未过期的正式 `task.offer`。`receive_command` 先提交 received checkpoint；随后在一个 `BEGIN IMMEDIATE` 事务内把 checkpoint 推进到 terminal/revision 2/event sequence 5，并按全局 ordinal 写入 `task.accept → task.started → step.started → step.progress(100) → step.completed → task.completed` 六条 outbox。生成失败只留下可恢复 received；事务失败若发现并发赢家则读取赢家的原始 outbox
- 精确重放：message ID 或 idempotency key 命中同一 canonical intent 时直接返回首次持久 envelope；runtime 在 Hello 后先把历史 delivered 重新排队，逐条通过正式 WebSocket 发送，只有 `send` 成功后才标记 delivered。部分发送、连接中断或进程重启会重放原 message ID/idempotency/wire，依赖服务端既有 T3-20 幂等收敛，不在本机伪造新业务事实
- 安全边界：本任务刻意不执行浏览器、微信、平台账号、文件或桌面副作用，只证明协议/持久化/恢复骨架；`task.pause/resume/cancel/emergency-stop` 在真实执行层尚未实现前固定拒绝。SQLite 仍只保存 command/checkpoint/outbox，不保存 Control Plane Session、本机会话、Cookie、账号、密钥、浏览器 Profile 或配置，也不调用系统钥匙串
- 正式原入口验收：`scripts/run_e4_12_acceptance.py` 启动隔离 PostgreSQL、完整生产 Alembic 链和真实 Uvicorn，签发正式 Device Session、落一条持久 offer，再临时构建/签名 PyInstaller onedir，由公开 Rust Manager 启动正式 Executor。第一次启动把 PostgreSQL 收敛为 1 条 acknowledged command、5 条连续事件和 succeeded Task；第二次以同一 SQLite 状态目录重启，六条本机消息全文/ID/幂等键不变，服务端 command/event 快照也完全不变，Session 原始字节不在 SQLite
- 测试与失败矩阵：命令处理器和新增账本分支语句/分支覆盖率 100%；覆盖 atomic batch 参数/双键重复/来源错绑/旧 revision/事件倒退/已有 outbox、并发提交赢家、生成/时钟/UUID/账本错误、二进制/畸形/过期/错身份/effectful command、mark/requeue 失败，以及真实 WebSocket 收命令后六帧返回。现有独立 CLI→Uvicorn Hello/Heartbeat/SIGTERM 集成继续通过
- 门禁：Backend `877 passed in 78.97s`、5076 条语句/全部分支覆盖率 100%；Frontend 48 项 Node 架构/契约、112 项 Vitest、ESLint、TypeScript、OpenAPI 与 production boundary 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三种配置各为 44 单元 + 3 bootstrap + 15 manager + 10 package + 3 协议 fixture + 14 安全配置，共 89 项通过、1 项正式 PyInstaller 编排由 E4-12 专用脚本通过。Clippy all-targets/all-features、Rustfmt、Backend 172 文件 Ruff 格式/lint、严格 Mypy 170 个源码文件、uv lock 全绿；正式 `pnpm tauri build --debug --no-bundle` 成功且未启动 App
- App 与账号边界：本任务正式入口是 Control Plane 持久 Outbox→真实 WebSocket→Rust 监管的 Executor，不新增 Tauri Command、React 页面或用户功能，因此不启动 App、不需要真实平台账号；E4-13 才从 Tauri `app_data_dir` 固定装配 Manager 和状态投影，E4-14 再由唯一 `visible=false` App 做桌面生命周期纵向验收
- 历史 CI 状态（2026-07-19）：Hosted Runner 受 Billing 限制时未把跨平台实现冒充为 Windows signed PyInstaller→Job Object Manager→真实 Control Plane→SQLite 重放通过。2026-07-20 已由下一条本机 GREEN 与 E4-03～E4-11 一并补齐
- Windows 本机 GREEN（2026-07-20）：Windows x86_64 使用全局 PostgreSQL 18.4 启动独立临时实例并跑完 14 段 Alembic 链，真实 Uvicorn、signed PyInstaller Executor、公开 Rust Manager 与 SQLite v2 两轮通过；首次验收分别暴露 `pg_ctl` 子进程继承捕获管道造成永久等待，以及 `sqlite3.Connection` 未显式关闭导致账本删除失败，均以回归契约修复。复验确认六条本机 ACK/事件全文不变、服务端 1 条 command/5 条事件快照不变、Session 不落库，临时数据库/端口/进程/工作目录全部回收
- 清理：正式验收的 Uvicorn、PostgreSQL 容器/volume、Cargo 测试进程、PyInstaller 包、私有 `0600` 配置和 SQLite 状态均由 finally/临时目录回收；检查无容器、监听端口或 Executor 残留
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构和唯一开发台账；没有新增第二份 implementation plan
- 后续：E4-13 建立固定 PlatformAdapter/Tauri Commands，从 Tauri 自身 `app_data_dir` 派生 Executor 状态目录，并向 React 只暴露受限状态、重启、脱敏诊断和紧停

### E4-13 PlatformAdapter 接入

- 状态：✅ 已完成；Windows x86_64 原生边界已验收
- 日期：2026-07-19
- 提交：本任务提交
- 目标：从 Tauri 自身 `app_data_dir` 固定装配唯一 Local Executor，把状态、重启、脱敏诊断和本机进程树紧停收敛为四个无参数 Command；React 只能经 `PlatformAdapter` 使用，不能提交 URL、Session、包根、状态目录或 Executor 身份
- RED：先新增 Node 架构边界、Vitest Adapter/诊断页和 Rust App-data 集成测试并实跑；分别准确失败于缺少 `executor_platform.rs`、`platform/types.ts`/Tauri Adapter、`Diagnostics.tsx` 和公开 Rust 模块，证明没有借旧 capability invoke、Mock Manager 或组件空壳让结果假绿。正式诊断 Feature 首建时又发现通用 `diagnostics/` 忽略规则会吞源码，已把四类运行目录规则锚定到仓库根并纳入同一回归
- Rust 装配：新增 `ExecutorPlatformService`，固定使用 `app_data_dir/local-executor/package` 与 `app_data_dir/local-executor/state`，状态目录 Unix 权限 `0700`；`executor-id-v1` 由系统 CSPRNG 生成 canonical UUIDv4，经既有 App 私有原子存储以 `0600` 持久，App 重启复用，损坏、相对/根路径、symlink 和存储拒绝均 fail closed。React/Tauri IPC 没有路径或身份参数
- 会话与启动：`restart_executor` 先由 Rust 凭据仓换取 `app.control-plane` Session，读取当前 active Installation ID，再换取独立 `executor.connect` 短期 Session；Session 只在 `Zeroizing<String>` 与 stdin 启动链短暂存在。Manager 先停止旧进程树，再以固定 WebSocket endpoint、Installation/Executor ID、状态目录和 15 秒心跳启动签名包；E4-14 真实链路暴露原 10 秒启动预算必然早于首个健康心跳，现固定为 30 秒并由架构回归锁定。debug 只信任公开测试 signer；release 构建缺少打包流水线注入的 `AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY` 时 fail closed，不把测试 signer 当发布信任根
- IPC 与 UI：正式 Command allowlist 只有 `get_executor_status`、`restart_executor`、`get_executor_diagnostics`、`emergency_stop_executor`。TypeScript Adapter 严格校验 exact-field 状态、SemVer/build ID、0..8 恢复次数和 200 行/4096-byte 安全诊断，原生异常只映射固定 allowlist 错误且不反射详情。“设置与诊断”页面可查看状态/版本/构建/恢复次数和安全 stderr、启动/重启，并在二次确认后执行本机硬停止；页面明确区分本机进程树停止与业务 Task 协作式紧停，不能宣称远端副作用已停止
- 分层 GREEN：Frontend 49 项 Node 架构/契约、118 项 Vitest 全绿，ESLint、严格 TypeScript、OpenAPI 漂移和 production boundary 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套各为 44 单元 + 3 bootstrap + 15 manager + 10 package + 2 platform + 3 protocol fixture + 14 security，共 91 项通过，另有 1 项正式 PyInstaller 编排由专用脚本负责并在普通 suite 中 ignored。三套 Clippy `-D warnings` 与 Rustfmt 全绿；不带测试驱动的 `pnpm tauri build --debug --no-bundle` 成功，App 未启动
- 原始入口验收：E4-14 已用唯一 `visible=false` App 从真实诊断页面点击启动/紧停/再次启动，经正式 TypeScript Adapter、Tauri IPC、Rust 换票、真实 Control Plane/PostgreSQL 和 signed Executor 覆盖崩溃恢复、挂起超时、退出清理与数据库最终事实；macOS 不再依赖组件 Mock，Windows AppData/Job Object/真实 WebView 链也已于 2026-07-20 在本机完成，任务结论为 `✅ 已完成`
- 失败矩阵：覆盖恶意/未知原生 DTO、超长或控制/Bidi 诊断、原生秘密异常不反射、状态目录/身份损坏、相对路径、Unix 权限、重复启动先停旧树、缺凭据/安装授权/网络/协议、包拒绝、认证拒绝、超时与进程不可用的固定错误映射；E4-14 已补真实崩溃、挂起和正常退出竞态，Windows AppData ACL/Job Object/IPC 仍归原生 runner
- 本地隔离与清理：本任务没有启动前端、后端、Docker、测试服务器或可见/隐藏 App，没有监听或占用端口；只执行编译和进程内测试，临时 App-data 目录由 RAII 删除。项目规则同时新增“启动前查端口、`automation-tool` 专属 Compose/容器/网络/Volume/SQLite/端口段、只清理本次实例”的强制隔离要求，后续 E4-14 起执行
- 文档：同步根/Frontend README、前端架构、工程结构、Git 忽略边界和唯一开发台账；没有新增第二份 implementation plan
- 历史 CI 状态（2026-07-19）：Hosted Runner 受 Billing/Actions spending limit 时没有空跑 workflow，也未以 macOS 或静态契约冒充四个 Command、AppData 身份/权限、签名包、Job Object 和隐藏 App 生命周期通过；2026-07-20 已由下一条本机 GREEN 补齐
- Windows 本机 GREEN（2026-07-20）：Windows x86_64/MSVC 下默认与 `control-plane-e2e` 两套公开 Rust `executor_platform` 各 2 项通过，验证绝对 AppData 派生、固定 package/state 路径、canonical UUIDv4 身份重开及损坏拒绝；四个无参数 Command 的 Node 边界 12 项、PlatformAdapter/诊断页 7 项通过。默认与 `control-plane-e2e` 两套 `cargo clippy --all-targets -D warnings` 通过；门禁首次发现打包验收目标无条件导入 Windows 专属错误码，已按 feature/cfg 收紧并复验
- 后续：E4-15 审计正式包测试能力与发布验证公钥边界

### E4-14 Tauri 生命周期 E2E

- 状态：✅ 已完成；Windows x86_64 隐藏 App 全生命周期已验收
- 日期：2026-07-19
- 提交：本任务提交
- 目标：只通过唯一后台隐藏 App 的真实诊断页面和正式 PlatformAdapter 验证 Executor 启动、状态/诊断调用、异常崩溃恢复、挂起停止超时、再次启动与 App 正常退出清理；不以组件 Mock、直接 Manager 调用或外层脚本杀进程冒充完成
- RED：先加入 Node 架构、Rust loopback origin、真实 OS crash/hang 注入和诊断页“刷新状态”测试，分别准确失败于缺少专用配置/runner、受控测试 origin、故障注入和刷新入口。首轮真实 App 又准确暴露 15 秒心跳与 10 秒启动预算矛盾，返回 `timed_out`；修到 30 秒后，外层守卫继续暴露 WDIO 杀 App 不触发清理，以及 `AppHandle::exit` 不能依赖 State 析构回收子进程，最终把清理提升到生产 `RunEvent::ExitRequested/Exit` 显式路径
- 隔离编排：`scripts/run_e4_14_acceptance.py` 每次先检查动态 loopback 端口，使用 `automation-tool-e414-<pid>` Compose project、随机数据库密码、独立 `com.aventador.automationtool.e414acceptance` AppData 和唯一临时目录；只启动自己的 PostgreSQL/Uvicorn/App/Executor，finally 只按本次精确标识清理，不读取、停止或复用其他项目容器和端口
- App 原入口：专用 Tauri 配置固定唯一 `visible=false` 主窗口；WebView 真实进入“设置与诊断”，点击“启动执行器”，读取 running/`0.1.0`/`e4-14-hidden-app`，随后从页面刷新恢复状态、二次确认“本地紧急停止”，看到安全失败提示与 stopped，最后再次从页面启动。React 不接触 URL、Session、路径、PID、包根或信任参数
- 会话与网络：测试 origin 只在 `control-plane-e2e` 编译期接受规范 `http://127.0.0.1:<port>`，生产仍固定默认 origin；App 注册唯一 Installation，每次启动经正式 Rust client 换取 `app.control-plane` 与独立 `executor.connect` Session，真实 Executor 使用同端口 WebSocket。最终数据库只含预期能力类型，不回传或记录 token
- 真实故障：验收专用 Command 只在 `control-plane-e2e` 构建注册。Unix 对实际子进程发送 `SIGKILL`/`SIGSTOP`，Windows 实现对应 `TerminateProcess`/线程 suspend；崩溃后正式 supervisor 重新验包、换本机会话并恢复为 running/restartCount 1，挂起后页面原紧停等待 10 秒、强制回收进程树并安全显示失败，再刷新为 stopped
- 退出清理：生产 Tauri event loop 在 `ExitRequested` 或 `Exit` 上显式调用唯一 Platform service 停止 Executor，Manager `Drop` 继续作为兜底。测试末尾只请求正常 App 退出；嵌入式 WebDriver 随 App 关闭会让 WDIO 的最终 `DELETE session` 固定得到 `ECONNREFUSED`，编排器仅接受这一精确签名且要求没有测试断言错误，随后必须独立确认 signed Executor 绝对路径无进程，否则验收失败
- 私有状态：验收核对稳定 canonical Executor UUIDv4、`executor-ledger.sqlite3` user_version 2、Installation/Executor 身份绑定和 Unix `0700/0600` 权限；长期设备凭据原始字节不得出现在 SQLite。测试数据只位于专用 App 沙盒，不调用系统钥匙串
- 门禁：正式 E4-14 编排通过；Backend `877 passed`，Ruff/格式、严格 Mypy 170 个源码文件、uv lock、OpenAPI 与 Executor Schema 无漂移；Frontend 50 项 Node 契约、119 项 Vitest、5 项 Playwright、ESLint、TypeScript、API 与 production boundary 全绿；Rust 默认/`desktop-e2e` 各 92 项、`control-plane-e2e` 93 项通过，三套 Clippy `-D warnings` 与 Rustfmt 全绿；不带测试驱动的 `pnpm tauri build --debug --no-bundle` 成功且未启动 App
- 清理复核：每次失败和最终成功均确认专属 Compose 容器/网络/卷、动态端口、App 私有测试目录和 signed Executor 全部消失；并行存在的 `agent-platform-*` 容器始终未被修改
- 历史 CI 状态（2026-07-19）：Hosted Windows Runner 受 Billing 限制时只完成跨平台编译，未以 macOS 或静态检查冒充 signed PyInstaller→隐藏 App→Job Object→正常退出全链路通过；2026-07-20 已由下一条本机 GREEN 完成原生验收
- Windows 本机 GREEN（2026-07-20）：Windows x86_64 从唯一 `visible=false` Tauri App 的真实设置与诊断页面完成 signed PyInstaller Executor 启动、版本/build 展示、`TerminateProcess` 崩溃后 Job Object supervisor 自动恢复到 restartCount 1、线程挂起后本机同步强停、再次启动与 App 正常退出；随后独立验证 SQLite v2 身份绑定、唯一 Installation、仅 `app.control-plane`/`executor.connect` Session 能力和无长期凭据落库。首轮暴露 macOS 超时提示断言不适用于 Windows 同步强停，第二轮暴露验收仍锁定历史 SQLite v1，均以回归契约修复；最终 AppData、临时 PostgreSQL/端口、App/Executor/WebDriver 和工作目录无残留
- 文档：同步根/Frontend README、前后端架构、工程结构和唯一开发台账；没有新增第二份 implementation plan
- 后续：E4-15 审计正式包不含 WebDriver、验收 Command、测试 Sidecar、测试 origin 或调试端口，并固化 release 验证公钥 fail-closed 边界

### E4-15 正式包测试能力审计

- 状态：✅ 已完成；Windows x86_64 release PE 已验收
- 日期：2026-07-19
- 提交：本任务提交
- 目标：不能只凭 Rust `cfg`、源码扫描或 debug build 推断安全；必须在实际 release 制品上证明没有 WebDriver、验收 Command、测试 Executor/Sidecar、测试 origin/标识、Harness 或调试端口，并保证发布 Executor 验证公钥在打包前 fail closed
- RED：新增制品审计测试先因 `audit-production-package.mjs` 不存在而失败；发布公钥测试准确证明原 `build.rs` 只调用 `tauri_build::build()`；正式编排测试再因 runner 不存在而失败。首轮真实 release 随后抓到 `tauri.conf.json` 中未使用的 `http://127.0.0.1:1420` 仍被编入二进制，说明源码特性检查不能替代制品扫描
- 发布公钥：`build.rs` 仅在 Cargo release Profile 读取编译期 `AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY`，在 `tauri_build::build()` 前拒绝缺失、非 canonical Base64URL、非 32 字节、无效或 weak Ed25519 key，固定错误不回显输入。debug 可继续信任公开开发 fixture；实际 release 审计同时拒绝该开发公钥的编码和原始字节，并要求制品确实包含本次预期发布公钥
- 配置隔离：正式 `tauri.conf.json` 删除 `beforeDevCommand`、`devUrl` 和 devCSP；它们只存在于 `tauri.dev.conf.json`，`pnpm tauri:dev` 固定展开为带该 `--config` 的开发命令。自动化各自继续显式合并专用隐藏配置，不能污染正式窗口、Capability 或 CSP
- 制品审计：`frontend/scripts/audit-production-package.mjs` 扫描真实 release binary、正式 Vite `dist`、正式 Tauri 配置和 `cargo tree --locked --edges normal --no-default-features`；拒绝 WDIO/WebDriver 依赖/标记、全部验收 Command、E2E 环境名、测试 App/build ID、测试资源/Sidecar、Harness、开发验证公钥和 1420 调试 URL，同时锁定唯一 `main` Capability、`withGlobalTauri=false`、可见产品窗口与生产 CSP
- 正式编排：`scripts/run_e4_15_acceptance.py` 使用唯一 `automation-tool-e415-target-*` 临时 Cargo target。先分别执行缺公钥和畸形公钥的 `cargo check --release` 并核对精确安全失败，再以与开发 signer 不同的验收专用公开公钥执行 `pnpm tauri build --no-bundle`，审计实际 binary 后由 TemporaryDirectory 删除全部临时制品；不保存私钥、不启动 App、不监听端口、不上传产物
- 真实发现与稳定性：修复 1420 泄漏后同一真实 release 审计通过。全量 `control-plane-e2e` 并发测试曾让故障注入 fixture 的 1 秒测试启动预算超时；单线程原测试立即通过，随后把仅测试的启动预算提高到 10 秒并重跑整套 93 项通过，未改生产 30 秒预算，也没有以单项重跑掩盖不稳定
- 门禁：E4-15 正式 release 编排通过；Frontend 56 项 Node 契约、119 项 Vitest、5 项 Playwright、ESLint、严格 TypeScript、API 和 production boundary 全绿；Rust 默认/`desktop-e2e` 各 92 项、`control-plane-e2e` 93 项通过，三套 Clippy `-D warnings` 与 Rustfmt 全绿；Python runner Ruff、格式与严格 Mypy 通过；唯一 `visible=false` 真实 Tauri/WKWebView 冒烟 1 项通过
- 隔离与清理：任务未启动 Backend、PostgreSQL、Docker 或业务端口；Playwright 前先确认 automation-tool 专属 1420 空闲，结束后再次确认释放。release target 每次唯一并删除；隐藏 Tauri 冒烟结束确认无 App/WebDriver 进程，正式 Vite 资产已恢复，其他项目容器、端口和文件均未读取或修改
- 历史 CI 状态（2026-07-19）：只读 macOS/Windows Desktop matrix 已接入，但 Hosted Windows 因 Billing/Actions spending limit 未启动，因此当时未以 macOS 冒充 Windows PE 字节、可执行路径与 runner 原生结果；2026-07-20 已由本机 release 构建与审计 GREEN 补齐
- Windows 本机 GREEN（2026-07-20）：Windows x86_64 在唯一临时 Cargo target 中先后证明 release 缺失、畸形和 weak Ed25519 验证公钥均在 `tauri_build::build()` 前 fail closed；随后以验收专用公钥构建真实 `automation-tool-desktop.exe`，扫描 PE 字节、正式 Vite assets、唯一 production Tauri capability/config 与无默认测试 feature 的 Cargo tree，确认不含 WebDriver/WDIO、验收 Command、测试 Sidecar/origin/build ID、开发公钥或 1420 调试 URL。首次验收暴露 Python `CreateProcess` 无法解析无扩展名 `pnpm`，已显式安全解析 `pnpm.cmd` 并以 Node runner 契约复验；临时 release target 自动删除，未启动 App 或监听端口
- 文档：同步根/Frontend README、前后端架构、工程结构、开发命令、CI 与唯一开发台账；没有新增第二份 implementation plan
- 后续：B5-01 审计旧 `browser_session`，锁定只迁移私有 Profile/状态机/清理语义并排除旧账号、RBAC、Cookie Vault 与聚合运行时

### B5-01 旧 browser_session 审计

- 状态：✅ 已完成；本任务只冻结迁移/删除边界和后续任务归属，没有平台运行代码或 Windows 专属实现需要冒充验收
- 日期：2026-07-19
- 提交：本任务提交
- 目标：在固定旧提交上完整复核 `browser_session.rs`、直接测试、聚合运行时和设备账号契约，提取当前项目真正需要的私有 Profile、Session 熔断/人工恢复与安全注销语义；明确删除旧产品账号、RBAC、Cookie Vault 和万能 Runtime
- RED：先新增 `frontend/tests/browser-session-audit.test.mjs`，实跑准确失败于缺少 B5-01 的来源证据、当前 Profile/Session 契约、注销时序和强制删除边界；没有靠既有 R0-12 摘要或旧测试已绿直接宣告完成
- 来源证据：逐行核对旧提交 `a01cfc9aa93e87e71b78b73eee3e07a3b9d31061` 的 556 行 `browser_session.rs`、183 行/9 项 `browser_session` 测试、`social_operations_runtime.rs` 的 Profile/账号/注销调用链、`device_account_service.py` 的 tenant/owner/权限模型及 `device-account-v1.md`；R0-12 已实跑的旧 9 项通过仅作样本证据，不替代当前仓库测试
- Profile 契约：当前唯一计划根为 Tauri `app_data_dir/browser-profiles/douyin/<canonical UUIDv4 profile_id>`；路径和浏览器可执行文件不能来自 React/服务端/账号文本。B5-05 重写私有权限、symlink/reparse point、稳定 identity 和创建/删除竞态，B5-06/B5-07 新建跨进程单实例锁与 headed 浏览器资源所有权，任何任务不得读取用户默认浏览器 Profile
- Session 契约：真实页面检测封闭为 `missing/healthy/expired/risk/unknown`，只有 `healthy` 派生 `circuit_open=false`；等待扫码/确认和人工接管属于平台本地工作流，不是产品登录。`session_revision` 本机持久单调，旧 revision 不能恢复执行；Control Plane 只接收平台、状态、revision 和观察时间，不接收 Cookie、Profile 路径、二维码、验证码或页面原文
- 注销缺陷与重写：旧 `logout_account` 在检查 `stop_result` 前已经计算 Cookie/Profile 删除，停止失败仍可能删掉登录态；旧 `BrowserProfile::remove` 也存在检查到 `remove_dir_all` 的替换窗口。B5-14 固定为先持久熔断/拒绝新任务，再安全停止动作、关闭浏览器并释放 Profile 锁，随后稳定解析并只删目标目录，最后递增 revision/投影 missing；停止失败保留 Profile，副作用最终态不明进入 `OUTCOME_UNCERTAIN`
- 强制删除：`HashMap` 账号、`active_account`、`SocialOperationsRuntime`、`EncryptedCookieVault`、`.cookie-key`、`SOC1`、Cookie API、`tenant_id`/`owner_user_id`、RBAC、Entitlement、Core Audit 和五平台一次性账号模型均不得进入当前 Manifest 或实现；浏览器持久 Profile 是平台秘密唯一来源
- GREEN：B5-01 定向契约 1 项与完整 Frontend Node 契约 57 项全绿，ESLint 零告警；锁定五个来源、完整后续落点 B5-02/B5-03/B5-05～B5-12/B5-14、Profile/Session/注销不变量和三个当前 Manifest 的旧依赖排除；前端与后端架构、工程结构和唯一开发台账同步
- 原始入口与 App：本任务交付物是审计判定，不新增或修改运行时接口，因此验收原入口是固定提交只读证据与当前仓库文档契约；没有启动前端、后端、Docker、测试服务器、浏览器或 App，也没有用 Mock/App 空壳冒充浏览器功能完成
- 本地隔离：全程未监听端口、未创建 Compose/容器/网络/Volume/SQLite/Profile，没有读取或清理其他项目运行资源；后续 B5-02 起继续在任何探测/测试服务前检查端口与精确资源归属
- 后续：B5-02 实现 macOS Chrome/Edge 受信发现，固定标准应用、签名/Bundle ID allowlist、路径失效和任意可执行文件拒绝边界

### B5-02 macOS 浏览器受信发现

- 状态：🔍 待 Edge 实机验收；本机真实 Google Chrome 已从公开 Rust 生产 API 完成 Security.framework 验签与路径复验，Edge 代码/契约/失败矩阵已完成但当前机器未安装，工程依赖可继续
- 日期：2026-07-19
- 提交：本任务提交
- 目标：只发现 macOS `/Applications` 下正式 Google Chrome/Microsoft Edge，使用 Apple 原生代码签名 API 同时约束签名有效性、Bundle signing identifier、Developer Team 和固定主可执行文件；返回的候选在后续使用前必须重验，不能让 React、服务端或用户输入任意可执行路径
- RED：先新增 Node 原生边界契约和 Rust 生产 API 集成测试；分别准确失败于 `browser_discovery.rs` 文件不存在与 crate 没有公开 `browser_discovery` 模块，没有用 shell `codesign`、假 App 或静态字符串扫描冒充生产发现成功
- 固定 allowlist：Chrome 只认 `/Applications/Google Chrome.app`、`Contents/MacOS/Google Chrome`、identifier `com.google.Chrome`、Team `EQHXZ8M8AV`；Edge 只认 `/Applications/Microsoft Edge.app`、`Contents/MacOS/Microsoft Edge`、identifier `com.microsoft.edgemac`、Team `UBF8T346G9`。缺失应用正常不返回，标准位置存在但不完整/不可信则整个候选 fail closed；`~/Applications`、PATH、LaunchServices 搜索结果和任意 `.app` 不进入发现面
- 原生验签：macOS 目标精确依赖已锁定的 `security-framework 3.7.0`/`core-foundation 0.10.1`，直接调用 `SecStaticCodeCheckValidity` 等价安全封装。每个 vendor requirement 同时要求 `anchor apple generic`、Developer ID 中间证书/叶证书 OID、精确 identifier 和 leaf OU；校验启用 all architectures、nested code、restrict symlinks 与 no-network，不解析本地化命令输出、不启动 shell
- 严格度实测：本机 Chrome 的默认 sealed-code/Designated Requirement 校验有效；`codesign --strict` 因 Chrome Framework 上已有 FinderInfo xattr 报 sideband metadata 错误。生产实现没有为了追求表面“strict”而拒绝用户当前可正常验证的官方浏览器，也没有降级到只读 Info.plist：仍由 Security.framework 验主程序、sealed resources、嵌套代码、所有架构和精确 vendor requirement
- 路径身份：发现前逐级拒绝 symlink，只把 macOS 固定 `/var|/tmp|/etc` 系统别名规范到 `/private`；App 必须为目录，主入口必须为带执行位普通文件。签名前后读取 dev/inode，`TrustedBrowser` 保存两者；`revalidate_macos_browser` 再要求标准绝对路径、原 identity 和重新验签，缺失、替换、symlink 或签名变化统一 `PathInvalidated`。B5-07 启动前必须调用该复验并在进程启动后继续核对运行代码身份
- 失败矩阵：5 项 macOS 单元测试覆盖固定顺序 Chrome/Edge、缺失与任意 App 忽略、坏签名/不完整 bundle、App/可执行 symlink、发现后替换和路径消失；测试临时目录带 PID/纳秒/原子序号并只删除本次精确目录。跨平台集成在非 macOS 明确返回 `UnsupportedPlatform`，不会误走 Unix 近似实现
- 真实原入口：`frontend/src-tauri/tests/browser_discovery.rs` 只调用公开 `discover_macos_browsers`，本机实际 Chrome 经过生产 Security.framework verifier 被发现为固定路径/identifier/team，再调用公开 `revalidate_macos_browser` 成功；没有通过依赖注入或直接调用内部 verifier。Microsoft Edge 当前未安装，真实 Edge 仍明确保持待验收
- 门禁：B5-02 Node 定向契约、5 项 Rust 单元和 1 项真实 macOS 集成通过；完整 Frontend Node 契约 58 项、ESLint、严格 TypeScript、Rustfmt 全绿。Rust 默认配置共 98 项通过、1 项既有 PyInstaller 编排 ignored，且全目标/全特性 Clippy `-D warnings` 通过；Cargo lock 只增加当前 crate 对锁内既有 Security.framework/CoreFoundation 的 macOS 目标直接依赖
- App 与资源：当前生产消费者是供 B5-04/B5-07 使用的 Rust 原生 API，尚无平台选择页面或 Tauri Command，因此本任务不启动 App，不宣称用户功能已完成；没有前后端、Docker、测试服务器、浏览器进程、端口、SQLite、Profile 或系统钥匙串操作，只读校验已安装应用
- 后续：B5-03 实现 Windows 注册表/标准路径和 Authenticode/产品 allowlist；Edge macOS 安装或受控设备可用时从同一公开 API补真实验收，不阻塞无 Edge 依赖任务

### B5-03 Windows 浏览器受信发现

- 状态：✅ 已完成；Windows x86_64 真实 Chrome/Edge 已验收
- 日期：2026-07-19
- 提交：本任务提交
- 目标：只发现 Windows 标准安装位置的正式 Google Chrome/Microsoft Edge，用系统 Authenticode、签名证书发布者、PE Version Resource 产品字段和稳定文件身份共同约束候选；调用方不能提交任意路径，发现结果在使用前必须再次复验
- RED：先把台账置为 `🧪 RED`，新增 B5-03 Node 边界契约和公开 Rust 集成入口；分别准确失败于缺少 `browser_discovery_windows.rs` 以及缺少 `discover_windows_browsers`/`revalidate_windows_browser`，没有用虚构注册表、Mock 签名或 macOS 结果宣告 Windows 完成
- 固定发现面：根目录只由 `SHGetKnownFolderPath` 读取 Program Files、Program Files (x86) 和 Local AppData；App Paths 只读 HKLM/HKCU 的 32/64 位视图，且注册表值必须与上述已知根拼出的 `Google\\Chrome\\Application\\chrome.exe` 或 `Microsoft\\Edge\\Application\\msedge.exe` 完全一致才进入候选。环境变量、PATH、PowerShell、`cmd.exe`、Shell 搜索结果和任意可执行文件均不进入接口
- 原生信任：`WinVerifyTrust` 使用 Generic Verify V2、无 UI、cache-only URL retrieval，并把已打开的稳定文件句柄传给 `WINTRUST_FILE_INFO.hFile`；随后从同一验证 state 提取 leaf 证书 CN。Chrome 只接受发布者 `Google LLC`、ProductName `Google Chrome`、CompanyName `Google LLC`、OriginalFilename `chrome.exe`；Edge 对应 `Microsoft Corporation`、`Microsoft Edge`、`Microsoft Corporation`、`msedge.exe`，未知/缺失/过长/包含控制字符的资源一律拒绝
- 路径与竞态：候选必须为绝对普通文件，逐级拒绝 reparse point；打开时使用 `FILE_FLAG_OPEN_REPARSE_POINT` 和只共享读取，阻止验证期间写入、替换或删除。`GetFinalPathNameByHandleW` 的规范 DOS 路径必须仍等于固定候选，句柄前后 volume serial/file index/length 和重新打开 identity 必须一致；`TrustedWindowsBrowser` 保存 identity，`revalidate_windows_browser` 再检查固定路径、原 identity、签名和产品，替换或路径漂移统一 `PathInvalidated`
- 失败矩阵与入口：Windows 专属单元覆盖 Chrome/Edge 固定顺序、坏签名 fail closed 和发现后替换；`frontend/src-tauri/tests/browser_discovery.rs` 在 Windows runner 只调用公开生产 API，要求机器至少发现 Edge/Chrome并逐个复验。非 Windows 入口明确返回 `UnsupportedPlatform`，只能证明跨平台契约，不计 Windows 实机验收
- 门禁：B5-03 定向 Node 契约 1 项、完整 Frontend Node 契约 59 项、Vitest 119 项、ESLint、严格 TypeScript、Rustfmt 和全目标/全特性 Clippy `-D warnings` 全绿；macOS 默认 Rust 共 99 项通过、1 项既有 PyInstaller 编排 ignored。另用现有 Homebrew `rust-src` 对直接引用生产 `browser_discovery.rs` 的最小 crate 完成 `x86_64-pc-windows-msvc` 类型检查，实抓并修复四项 FFI 模块/指针/flags 类型错误；整库交叉检查仍会先被本机缺少 Windows SDK 的第三方 `aws-lc-sys` C 编译阻断，原生 runner 实际编译/执行后才补验收
- Windows 本机 GREEN（2026-07-20）：Windows x86_64 原生运行 2 项固定 Chrome/Edge 候选、坏 verifier fail closed 与发现后替换失效单元测试，以及 1 项公开生产 Authenticode 集成测试；真实发现并逐个复验标准安装的 Google Chrome `150.0.7871.125` 和 Microsoft Edge `150.0.4078.83`，产品/公司/发布者 allowlist 均匹配。首次原生单元测试暴露 `%TEMP%` 的 `AVENTA~1` 8.3 短路径与 `GetFinalPathNameByHandleW` 长路径被误判漂移；保持逐级 reparse 拒绝和稳定句柄前提下，改用 `std::fs::canonicalize` 后比较句柄最终路径，并以 Node 回归契约、Rustfmt、全 target/全 feature Clippy `-D warnings` 复验
- App 与本地隔离：本任务提供 B5-04/B5-07 将消费的 Rust 原生能力，尚无 Tauri Command 或用户界面，因此未启动 App、浏览器、Backend、Docker、测试服务器、端口、Profile、SQLite 或系统钥匙串；没有读取、停止或清理其他项目任何进程和资源
- 后续：B5-04 只允许从受信发现结果中选择 Chrome/Edge；GitHub Windows runner 恢复时自动补本任务与此前 E4 系列 Windows 原生门禁，不阻塞无 Windows 设备依赖实现

### B5-04 受信浏览器选择设置

- 状态：✅ 已完成
- 日期：2026-07-20
- 提交：本任务提交
- 目标：用户只能在 Rust 当前真实发现的 Chrome/Edge 枚举中选择运营浏览器；React、Tauri 参数、持久化文件和 Control Plane 均不能接收或看到应用/可执行文件路径、签名信息或 identity，已卸载或失去信任的选择不能继续投影为可用
- RED：先把台账置为 `🧪 RED`；新增 Rust 生产 service 集成测试、React 设置组件测试和 Node 原生边界契约，分别准确失败于缺少 `browser_settings` 模块、缺少 `BrowserSettings.tsx` 和缺少生产/隐藏 App 文件。随后再扩展同一契约，准确失败于缺少专用隐藏 Tauri 配置，未用 Mock 页面或下层存储测试冒充 App 验收
- 原生服务：`BrowserSettingsService` 只由 Tauri setup 的 `app.path().app_data_dir()` 初始化；`snapshot` 每次调用 B5-02/B5-03 平台生产发现，`select_browser` 只接收 serde 固定枚举并在保存前重新发现。返回 DTO 只有 `availableBrowsers`/`selectedBrowser`；两条 Command 没有路径、URL、账号或任意 JSON 参数，服务端没有对应接口
- 沙盒持久化：选择是 exact canonical `{"browser":"google_chrome|microsoft_edge","version":1}`，经既有 App 私有 `AppDataSecretStore` 原子写入 `settings/browser-selection-v1`；Unix 目录/文件为 `0700/0600`。未知字段、字段顺序/编码不 canonical、版本错误、损坏文件或存储锁异常均 fail closed 且不自动覆盖；存储值已卸载时保留磁盘事实但对 UI 投影 `selectedBrowser=null`
- Windows 更新语义：本任务审计出标准 `fs::rename` 在 Windows 不能覆盖既有目标，已把共享私有存储的 Windows 原子替换改为 `MoveFileExW(MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)`；生产 discovery/settings/secure_store 及其 `cfg(test)` 已共同通过现有 Homebrew `rust-src` 的 MSVC 目标类型检查。真实 Windows 文件系统行为仍只由 Windows runner 计入验收
- WebView 边界：正式 `TauriPlatformAdapter` 对两个枚举、顺序、去重、selected 必须属于 available 和 exact response keys 做 fail-closed 解析；设置页只有 Rust 返回项组成的 Radio 与保存按钮，没有文本框、文件选择器或路径回显。无浏览器、原生失败和带路径/未知字段响应均只显示固定安全状态，不反射底层异常
- 真实 App 原入口：`scripts/run_b5_04_acceptance.py` 先动态选择并检查空闲 loopback WebDriver 端口，使用独立 `com.aventador.automationtool.b504acceptance` AppData 和唯一 `visible=false` Tauri App。真实页面进入“设置与诊断”，选择本机受信浏览器并点击保存，调用正式 `TauriPlatformAdapter → get/select Command → Rust discovery/settings → AppData`；刷新 WebView 后再次从同一页面读回，WDIO 1 项通过。runner 随后核对 canonical 文件、无路径和 Unix 权限，恢复 production Vite 资产
- 失败矩阵与门禁：Rust 单元覆盖 canonical round-trip、损坏/非 canonical 不重写和已卸载选择不投影；真实 service 集成覆盖发现、保存、重开与不可用枚举不覆盖；Vitest 覆盖 UI 保存/空态/错误脱敏及 Adapter Edge-only/带路径响应；Node 契约固定无路径 Command、隐藏配置、刷新验收、动态端口和正式包排除标识。完整 Frontend Node 60 项、Vitest 123 项、ESLint、严格 TypeScript、Ruff、Rustfmt 与全目标/全特性 Clippy `-D warnings` 全绿；默认 Rust 共 105 项通过、1 项既有 PyInstaller 编排 ignored
- Windows 本机 GREEN（2026-07-20）：先以真实 runner 复现 Python `CreateProcess` 无法解析无扩展名 `pnpm`，新增 Node 契约后统一解析 `pnpm.cmd`，覆盖测试构建、WDIO 和 production 资产恢复三条路径。独立隐藏 Tauri App 通过 Edge `150.0.0.0` 的真实 WebDriver 会话完成选择、保存与刷新重开，磁盘只持久化 canonical 浏览器枚举且无可执行路径；随机端口关闭、专属 AppData 删除和 production 资产恢复均复核通过。
- 本地隔离与清理：验收不启动 Backend、PostgreSQL、Docker、Executor 进程或运营浏览器，只启动后台隐藏 App 和动态嵌入式 WebDriver；启动前检查端口，结束确认端口关闭、App/WDIO/runner 进程退出、专属 AppData 删除且生产资产恢复。没有读取、停止、复用或清理另一个项目的端口、进程、容器、网络、Volume、SQLite 或文件
- 后续：B5-05 建立 App 沙盒内 `browser-profiles/douyin/<UUIDv4>` 私有目录与稳定路径身份；B5-04 Windows 原生验收已于 2026-07-20 补齐

### B5-05 私有浏览器 Profile 目录

- 状态：✅ 已完成；macOS/Unix 与 Windows 均已从公开生产 `BrowserProfileStore` 原入口完成真实文件系统创建、重开、稳定 identity、reparse point/symlink、私有权限/DACL 和并发矩阵
- 日期：2026-07-20
- 提交：本任务提交
- 目标：只在 Tauri App 私有数据根下建立 `browser-profiles/douyin/<canonical UUIDv4>`；Profile ID 必须由本机 CSPRNG 生成，不接收昵称、手机号、平台账号、路径片段或其他平台枚举，也不读取/迁移用户默认 Chrome/Edge Profile
- RED：先把台账置为 `🧪 RED`；新增真实 Rust 文件系统集成测试和 Node 原生边界契约，分别准确失败于 `automation_tool_desktop_lib::browser_profiles` 与生产 `browser_profiles.rs` 不存在。没有复用旧仓库实现、Mock 文件系统或先写空模块让测试假绿
- Rust 边界：Tauri setup 已从自身 `app.path().app_data_dir()` 管理唯一 `BrowserProfileStore`；公开原生能力仅为本机生成抖音 Profile、按 canonical UUIDv4 重开和对已持有 Profile 做 identity 复验。`SocialPlatform` 当前只有 `Douyin`；没有 Tauri Command、React DTO、Control Plane API、Cookie API、任意路径输入或其他平台目录
- Unix 原子语义：从文件系统根逐级以 `openat(O_DIRECTORY|O_NOFOLLOW)` 打开既有祖先，固定子目录和 Profile 叶子通过父目录句柄的 `mkdirat` 原子创建；AppData、`browser-profiles`、`douyin` 和 Profile 均由已打开句柄 `fchmod(0700)` 并复核。Store/Profile 始终持有目录句柄与 dev+inode，创建、重开和使用前后从同一父句柄重开比较 identity，路径被 rename/替换后 fail closed
- Windows 原子语义：AppData 全链拒绝 reparse point，固定子目录和 Profile 使用父目录 HANDLE 作为 `OBJECT_ATTRIBUTES.RootDirectory` 的 `NtCreateFile(FILE_DIRECTORY_FILE|FILE_OPEN_REPARSE_POINT)` 原子创建/打开，不走 `create_dir_all` 或路径跟随。每层验证 volume/file index、最终规范路径和非 reparse 属性，并在 HANDLE 上应用/复核 protected DACL：owner 为当前用户、唯一继承 ACE 为当前用户 `FILE_ALL_ACCESS`
- 失败矩阵：集成测试覆盖 canonical UUIDv4/精确路径、非法版本/大小写/无连字符/逃逸、缺失 Profile、普通文件冒充目录、AppData/固定子目录/叶子 symlink、过宽权限修复、目录被 rename 后同名替换、8 路并发创建不复用 ID；Unix 单元另覆盖绝对路径祖先 symlink。Windows reparse point、ACL、并发和 identity 运行时矩阵已于 2026-07-20 在原生实体环境通过
- 门禁：完整 Frontend Node 契约 61 项、Vitest 123 项、ESLint、严格 TypeScript、production boundary、Rustfmt 和全目标/全特性 Clippy `-D warnings` 全绿；macOS 默认 Rust 共 114 项通过、1 项既有 PyInstaller 编排 ignored。用现有 Homebrew `rust-src` 对直接引用生产 common/Windows Profile 模块的最小临时 crate 完成 `x86_64-pc-windows-msvc` 类型检查，实抓并修复 Win32 feature 与 SID/ACL 缓冲区对齐问题，临时 crate/target 已删除
- Windows 本机 GREEN（2026-07-20）：公开生产集成首次运行 4 项时，8 路并发初始化稳定暴露 `NtCreateFile` 创建目录后才补 DACL 的窗口，另一个线程可见继承 ACL 并返回 `UnsafeDirectory`。新增回归契约后，以拥有 SID/ACL 生命周期的 `PrivateSecurityDescriptor` 在 `FILE_OPEN_IF/FILE_CREATE` 创建时原子提交当前用户唯一 ACE 和 protected DACL，创建后仍复核 ACL 与 identity；20 轮并发压力全绿。补齐此前被 `cfg(unix)` 排除的 Windows 矩阵后共 6 项通过，覆盖 AppData/固定子目录/Profile junction、普通文件、DACL 扩权篡改、目录替换和并发 UUID；严格 Clippy、Node 契约及临时资源清理复核通过。
- 原始入口与隔离：B5-05 的正式消费者是 B5-06/B5-07 Rust/Executor 运行链，当前没有用户可调用功能；唯一后台隐藏 App 已从正式 Tauri setup 初始化 Store，叶子创建/重开则从公开生产 Store 调用真实 OS 文件系统。验收不启动 Backend、PostgreSQL、Docker、Executor 或运营浏览器；隐藏 App 只使用动态已检查 WebDriver 端口和独立 B5-04 AppData，其他测试数据使用本任务唯一临时 AppData，结束全部精确删除，没有读取、停止、复用或清理另一个项目的资源
- 后续：B5-06 在任何 persistent browser context 之前基于同一稳定 Profile identity 建立跨进程单实例锁；B5-05 Windows 原生验收已于 2026-07-20 补齐

### B5-06 Profile 单实例锁

- 状态：✅ 已完成；macOS/Unix 与 Windows 均已从公开生产锁 API 完成真实文件系统、跨进程争用、原生权限/链接和子进程崩溃恢复矩阵
- 日期：2026-07-20
- 提交：本任务提交
- 目标：任何 BrowserRuntime 或 persistent context 使用 Profile 前都必须持有本机原生排他锁；同一 Profile 的同进程/跨进程竞争必须立即返回 `ProfileInUse`，不同 Profile 可并行，未明确证明安全退出的上次持有者必须返回 `RecoveryRequired`
- RED：先把台账置为 `🧪 RED`；新增 Rust 公开 Profile 集成测试与 Node 原生边界契约，分别精确失败于缺少 `try_acquire_lock`/`ProfileInUse`/`RecoveryRequired` 和缺少平台锁实现；没有先放空方法、进程内 Mutex 或 Mock 文件系统让测试假绿
- 公开边界：`BrowserProfile::try_acquire_lock` 只能基于 B5-05 已持有的稳定 Profile identity 获取锁，返回借用 Profile 生命周期的 `BrowserProfileLock`；只有显式 `release(self)` 才清除活跃标记。Drop、panic、kill 或意外退出只由 OS 释放内核锁，保留活跃标记并在下次返回 `RecoveryRequired`；本任务故意不暴露 WebView/Tauri 锁、解锁或强制恢复 Command
- 状态文件：每个 Profile 固定一个 `.automation-tool-profile-lock-v1`，空文件代表上次明确释放，持有者在内核锁后持久化 exact canonical `{"state":"active","version":1}`。非空、过大、破损或未知状态均不自动覆盖；只有 identity、权限/所有者和 exact marker 仍一致时才能在显式释放中原地清空
- Unix 原生语义：固定文件通过 Profile 目录 fd 的 `openat(O_NOFOLLOW|O_CLOEXEC)` 打开，要求当前 euid、`0600`、普通文件且硬链接数为 1；保存 dev+inode 并在写入/清空前后重开比对，使用 `flock(LOCK_EX|LOCK_NB)` 立即排他。路径被 rename/替换、symlink、过宽权限或状态损坏全部 fail closed
- Windows 原生语义：固定文件使用 Profile 目录 HANDLE 作为 `NtCreateFile` RootDirectory，带 `FILE_NON_DIRECTORY_FILE|FILE_OPEN_REPARSE_POINT`，且不分享 delete 来阻止持锁时改名/删除；要求非 reparse、非目录、硬链接数为 1、稳定 volume/file index、精确最终路径和当前用户唯一 ACE 的 protected DACL。`LockFileEx(LOCKFILE_EXCLUSIVE_LOCK|LOCKFILE_FAIL_IMMEDIATELY)` 完成非阻塞内核排他，显式释放后由 HANDLE Drop 释放锁
- 失败矩阵：6 项真实文件系统/进程集成测试覆盖同 Profile 争用、不同 Profile 并行、显式释放后重获取、真实子进程持锁争用、持锁子进程被 kill、Drop 未释放、symlink/过宽权限/破损状态/文件替换。Windows reparse/DACL/竞争/崩溃实际行为已于 2026-07-20 在原生实体环境通过
- 门禁：完整 Frontend Node 契约 62 项、Vitest 123 项、ESLint、严格 TypeScript、OpenAPI 无漂移、production boundary、Rustfmt 和全目标/全特性 Clippy `-D warnings` 全绿；macOS 默认 Rust 共 120 项通过、1 项既有 PyInstaller 编排 ignored。用现有 Homebrew `rust-src` 对直接引用生产 common/Windows Profile 模块的最小临时 crate 完成 `x86_64-pc-windows-msvc` 类型检查，临时 crate/target 已删除
- Windows 本机 GREEN（2026-07-20）：首次运行 5 项主矩阵全部在首次获取锁失败；先定位并修复固定前导点文件名被目录名称校验器自拒绝，再定位并修复 `NtCreateFile` 未带 `FILE_SYNCHRONOUS_IO_NONALERT` 导致 Rust `File::read/write` 在异步句柄上返回 `StorageUnavailable`。固定名专用校验、同步句柄和创建时原子 DACL 均有 Node/Rust 回归；主矩阵与补齐的 Windows junction、硬链接、DACL 扩权、损坏 marker、强制字节锁和持锁改名拒绝共 6 项全绿，严格 Clippy 与临时进程/文件清理复核通过。
- 原始入口与资源：B5-06 当前的正式消费者是 B5-08 BrowserRuntime，尚无用户可调用的 App 功能；验收从公开 `BrowserProfileStore` 创建/重开 Profile 并调用公开 `BrowserProfile::try_acquire_lock`，使用真实 OS 文件系统和真实测试子进程，没有直接调下层、Mock 或隐藏 App 空壳。任务未启动 App、Backend、PostgreSQL、Docker、Executor、浏览器或任何监听端口；临时 AppData 只按本任务唯一精确路径创建并清理，没有读取、停止、复用或清理另一个项目的资源
- 后续：B5-07 在正式 PyInstaller Executor 中加入 Playwright 并启动受信系统 Chrome/Edge headed persistent context；B5-08 将浏览器进程生命周期与本锁 guard 绑定

### B5-07 Playwright 打包 PoC

- 状态：✅ 已完成；macOS arm64 与 Windows x86_64 均已完成正式 PyInstaller onedir、生产 Playwright primitive、受信系统浏览器 headed persistent context 与退出清理
- 日期：2026-07-20
- 提交：本任务提交
- 目标：证明 Executor 正式冻结目录可以携带 Python Playwright driver，而不下载或捆绑 Playwright 浏览器；从 B5-02/B5-03 受信系统 Chrome/Edge、B5-05 私有 Profile 和 B5-06 排他锁的原始底层链路启动 headed persistent context，为 B5-08 正式资源所有权消除打包风险
- RED：先把台账置为 `🧪 RED`；新增 Python 运行时单元测试和 Node 打包边界契约，分别在收集阶段精确失败于 `ModuleNotFoundError: automation_tool.executor.browser_runtime` 与生产模块文件不存在，没有用下载的 Playwright Chromium、默认用户 Profile、Mock 浏览器或空壳入口制造通过
- 依赖与冻结边界：Playwright 1.61.0 是正式 Executor runtime dependency，PyInstaller spec 对 `playwright` 执行 `collect_all` 并显式收集生产模块；仓库、spec、CI 和测试均不执行 `playwright install`。正式 onedir 清单确认 Python driver 存在，同时不存在 `.local-browsers`、`chromium-*`、`firefox-*`、`webkit-*` 或 `ffmpeg-*` 浏览器缓存目录，用户仍复用本机受信 Chrome/Edge
- 生产 primitive：`BrowserLaunchRequest` 只接受绝对 `Path`，拒绝超长、控制/Bidi 字符、symlink、非普通/不可执行浏览器和无效 Profile；`PackagedBrowserRuntime` 只用显式 `executable_path`、`headless=False`、`accept_downloads=False`、30 秒超时调用 `launch_persistent_context`，不允许 `channel`、安装 fallback 或自定义 flags。Lease 以幂等 close 先关 context 再停 Playwright driver，路径、底层异常和对象表示均固定脱敏
- 原始 PoC 入口：macOS ignored 原生验收先由 Rust 生产 API 发现并复验系统 Chrome，再用公开 `BrowserProfileStore` 创建独立抖音 UUIDv4 Profile、取得真实 B5-06 锁，启动测试专用冻结探针；探针只负责调用生产 `browser_runtime.py` 并返回固定健康标记。context 关闭后 Rust 再复验浏览器/Profile 并显式释放锁，全程未触碰用户默认 Chrome `User Data`
- 产品边界：冻结探针位于 `backend/tests/fixtures`，不进入正式 Executor onedir、React、Tauri Command、Control Plane 或发布物；B5-07 是内部打包/真实资源 PoC，不是 App 用户功能，因此没有可从 App 发出的接口，也不以隐藏 App 空壳冒充完成。B5-08 才会建立正式 BrowserRuntime、进程所有权和业务原始调用路径
- 失败矩阵：覆盖相对/缺失/目录/不可执行浏览器、相对/缺失/普通文件 Profile、浏览器/Profile symlink、starter/launch/close/driver-stop 异常、幂等/上下文关闭和 repr 脱敏；正式包继续覆盖无项目 Python PATH、bootstrap 拒绝和 WebSocket 不可用。Windows 受信系统浏览器启动、路径语义和进程退出实际行为已于 2026-07-20 在原生实体环境通过
- 门禁：B5-07 聚焦 Python 11 项通过；Backend 全量 884 项、5169 条语句/1010 个分支覆盖率 100%，uv lock、Ruff/格式、严格 Mypy 174 个源码文件、OpenAPI/Executor Schema 和 Actionlint 全绿；Frontend 63 项 Node 契约、123 项 Vitest、ESLint、严格 TypeScript、API/production boundary 全绿；macOS Rust 120 项通过、B5-07/E4-07 两项显式编排测试默认 ignored，Rustfmt 和全目标/全特性 Clippy `-D warnings` 全绿
- Windows 本机 GREEN（2026-07-20）：正式 PyInstaller onedir 构建与公开 Rust 编排实际发现/复验受信系统浏览器，创建私有抖音 UUIDv4 Profile、取得原生排他锁并启动 headed persistent context，主窗口/第二窗口操作、正常关闭、浏览器复验与显式解锁全链通过；冻结目录含 Playwright driver 且无 `.local-browsers` 或 Chromium/Firefox/WebKit/ffmpeg 浏览器包。首次运行只因 Python 文本 stdout 在 Windows 输出 CRLF 破坏 canonical ready 字节失败，改为 binary stdout LF 并加 Node 契约后复验通过；随后发现 pytest 保留完整 PyInstaller `tmp_path`，增加成功/失败 finalizer 并第三次从零构建复验，9 项 Python 矩阵、Ruff/Node 门禁和探针/浏览器/Profile/冻结文件零残留检查全绿。
- 资源隔离与清理：任务未启动 Backend、PostgreSQL、Docker、App、测试服务器或监听端口；只短暂启动受信系统 Chrome，使用本任务唯一临时 AppData/Profile 和 PyInstaller 目录，验收后关闭 context/driver、释放锁并精确删除临时目录。进程复查无 frozen probe、Chrome/Profile 或 Playwright driver 残留，没有读取、停止、复用或清理另一个项目的资源
- 后续：B5-08 将 B5-02/B5-03 复验、B5-05 Profile、B5-06 lock guard、Python context 和完整浏览器进程树绑定为一个确定性 BrowserRuntime；B5-09 再从抖音页面对象的正式调用入口消费该 Runtime

### B5-08 BrowserRuntime

- 状态：✅ 已完成；macOS arm64 与 Windows x86_64 均已完成冻结生产模块的窗口生命周期、异常退出和原生 process group/正式 Manager Job Object 整树强停
- 日期：2026-07-20
- 提交：本任务提交
- 目标：在 Python Local Executor 内建立一个窄、确定、可由后续抖音 Adapter 消费的 BrowserRuntime；同时只拥有一个 thread-confined Playwright persistent context，并把启动、主窗口、窗口集合、新窗口、触发式弹窗、有界超时、定向关窗、正常关闭和进程级硬清理边界固定下来
- RED：先把台账置为 `🧪 RED`；新增 Python 生命周期/失败矩阵在收集阶段精确失败于无法导入 `BrowserRuntime`，Node 原生边界契约精确失败于生产模块缺少该类；没有用 Mock 浏览器、下载 Chromium、默认用户 Profile 或空 Tauri Command 冒充真实运行
- 生产接口：`BrowserRuntime.start` 启动前再次复验冻结 B5-07 请求的浏览器/Profile 路径，同时只允许一个 context；Runtime 记录创建线程，跨线程、重复启动、关闭后使用全部固定拒绝。context 固定 headed、显式 executable/Profile、禁止下载，动作/导航默认超时分别 15/30 秒；`capture_window` 只接受 1～60000 ms 显式上限并把 Playwright timeout 映射为独立固定 `BrowserRuntimeTimedOut`
- 页面与窗口：`primary_window` 在已有首窗时复用、没有页面时显式新建；`windows`、`open_window` 和 `capture_window` 只返回带所属 Runtime identity 的 `BrowserWindow`。定向关闭拒绝外来或已关闭 Page，固定 `run_before_unload=False`；原始 `playwright_page` 只供同一 Python Executor 的平台页面对象，不能序列化到协议、Tauri IPC 或 React
- 关闭与硬清理：`close` 先清除可用状态，再尝试关闭 context 和停止 Playwright driver；任一失败不阻止另一项，重复关闭幂等。正常关闭依靠 Playwright persistent context 关闭浏览器；Executor 异常/挂起继续由 E4-09 同一个 `RunningExecutor` 进程树负责，Unix 独立 process group、Windows kill-on-close Job Object，不新增第二 Manager
- 真实冻结验收：测试专用 PyInstaller onedir 仍从生产 `browser_runtime.py` 启动本机受信 Chrome，不含 Playwright 浏览器缓存。正常路径创建主窗口、打开/关闭第二窗口后退出；崩溃路径在 ready 后保持 context，Rust 持有 Profile 锁并以生产 Manager 相同 `process_group(0)` + 负 PGID `SIGKILL` 强停，确认组内后代消失、Profile identity 仍有效并显式释放锁。探针不进入正式 Executor 包或发布物
- 失败矩阵：15 项聚焦 Python 测试覆盖非法 starter/request、启动前浏览器/Profile 替换、超时配置半失败、窗口枚举/主窗/新窗异常、触发器失败/超时、非法超时、外来/失效窗口、Page close 失败、跨线程、重复启动、use-after-close、context/driver 单独及同时关闭失败、上下文异常退出和重启；生产模块 247 条语句、48 个分支覆盖率 100%
- 门禁：Backend 全量 893 项、5323 条语句/1034 个分支覆盖率 100%，uv lock、Ruff/格式、严格 Mypy 189 个源码文件和 OpenAPI/Executor Schema 全绿；Frontend 64 项 Node 契约、123 项 Vitest、ESLint、严格 TypeScript、API/production boundary 全绿；macOS Rust 120 项通过，B5-07/B5-08/E4-07 三项真实编排默认 ignored，Rustfmt、全目标/全特性 Clippy `-D warnings` 与 Actionlint 全绿
- 原始调用边界：B5-08 交付的是 Local Executor 内部生产 API，没有用户可直接触发的 App 功能或服务端接口；真实入口是冻结模块经 Rust 受信浏览器/Profile/锁组合调用，不启动隐藏 App 空壳。B5-09 从真实抖音页面对象消费 `BrowserWindow.playwright_page`，B5-10/B5-13 再建立用户扫码/处理入口；相关任务不得回退到 Mock 或从 Control Plane 下发路径
- Windows 本机 GREEN（2026-07-20）：同一测试专用 PyInstaller onedir 生成正式测试签名 Manifest；公开 Rust API 发现并复验标准安装浏览器、创建私有 Profile 与原生排他锁，先通过主窗口/第二窗口正常关闭，再由公开 `ExecutorManager` 以生产 suspended-attach `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 启动 held BrowserRuntime。冻结探针通过 `ctypes` Toolhelp32 原生快照记录 Node driver 与真实 Chrome/Edge 后代 PID，测试在强停前确认树内含受信浏览器进程，`manager.stop()` 后逐个确认全部 PID 消失，再复验浏览器/Profile 并显式解锁。首次 Manager 模式因 PowerShell/CIM 枚举超时失败，替换为毫秒级 Toolhelp32 后完整双路径通过；18 项 Python 聚焦矩阵、Ruff、Node、Rustfmt、全 target/feature Clippy 及专属进程/冻结目录零残留复核全绿。
- 资源隔离：验收未启动 Backend、PostgreSQL、Docker、App、测试服务器或监听端口，只短暂启动本机受信 Chrome；每轮使用唯一临时 PyInstaller/AppData/Profile，正常退出或整树强停后复验 Profile、释放锁并由 fixture 精确清理。没有读取、停止、复用或清理另一个项目的资源
- 后续：B5-09 建立抖音页面对象与真实页面证据，将 Session 健康封闭为 healthy/expired/missing/risk/unknown；真实账号不可用时用本地隔离测试页完成自动化层并保持真实账号待验收，不阻塞后续任务

### B5-09 抖音 Session 检测

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：从 `BrowserRuntime` 所属的真实抖音页面封闭产生 `healthy/expired/missing/risk/unknown` 与固定 evidence；只有 `healthy` 关闭熔断，不读取、导出或上传 Cookie
- RED：先把台账置为 `🧪 RED`；Python 单元测试在收集阶段准确失败于 `ModuleNotFoundError: automation_tool.executor.rpa`，Node 边界准确失败于生产 `executor/rpa/douyin/session.py` 不存在。没有用 Fake Cookie、storage state、协议 Mock 或测试页结果冒充真实账号状态
- 页面边界：生产模块固定官方 `https://www.douyin.com/user/self` 作为受保护探测入口，只接受精确 HTTPS `www.douyin.com` 与默认/443 端口。版本化选择器把 ByteDance 验证中心 iframe、登录过期、用户资料壳和登录入口分别映射为 `risk/expired/healthy/missing`；多个来源冲突、无证据、DOM/页面异常或非官方 origin 一律 `unknown`，页面异常不保留底层 cause
- 真实页面证据：先用隔离浏览器访问抖音公开页观察到实际 `rmc.bytedance.com/verifycenter/captcha` 风控 iframe；再在用户明确授权下使用 App 沙盒内独立持久 Profile 和系统 Chrome 150.0.7871.128。首页在登录后因内容结构只返回 `unknown`，检测器没有误报；已登录与空白 Profile 对同一官方 `/user/self` 做非个人化结构差分后，正式 detector 从加载期 `unknown` 收敛为 `healthy` 并输出 `ready`，空白 Profile 同一入口保持登录熔断。平台为 macOS 26.4.1 arm64；未记录账号名、二维码、页面原文或 Profile 路径
- 原始调用边界：隔离六态 HTML 通过 Playwright route 绑定到官方 origin 后，由真实系统 Chrome→生产 `BrowserRuntime`→生产 `DouyinSessionDetector` 逐态检查；公开未登录抖音页另以同一路径实跑。真实账号 probe 只装配这两个生产对象并轮询状态，不调用 detector 内部函数。B5-09 尚无用户可调用的 Tauri/App 功能，因此不启动隐藏 App 空壳；B5-10 在 Local Executor 内组合扫码流程，B5-13 再提供 App 原入口
- 隐私与失败矩阵：覆盖 risk/expired/healthy/missing/unknown/conflicting、错误 origin、userinfo/端口/控制与 Bidi URL、页面访问异常、非法观察对象/版本、外来窗口与 absent evidence；源码和跨端边界明确拒绝 `context.cookies`、`document.cookie`、`storage_state`，协议/Tauri Command 不出现 Page、Profile 路径或 Cookie。所有对象 repr 和输出只含固定状态/evidence/version
- 门禁：聚焦真实系统 Chrome 与 live public 对照 `16 passed`；Backend 全量 `908 passed, 1 skipped`，5406 条语句/1046 个分支覆盖率 100%，uv lock、Ruff/格式、严格 Mypy 181 个源码文件、OpenAPI/Executor Schema 全绿。Frontend 65 项 Node 契约、123 项 Vitest、ESLint、TypeScript、production boundary 全绿；Rust 默认与 desktop-e2e 各 120 项、control-plane-e2e 121 项通过，三套 Clippy/Rustfmt 与 Actionlint 全绿。两条既有 Executor 3 秒进程等待在 PyInstaller/并行负载下各波动一次，均按原参数单测及默认并行整套复跑通过，未修改测试、超时或生产代码
- 资源与清理：任务未启动 Backend、PostgreSQL、Docker、App、测试服务器或监听端口；真实/隔离验收只短暂启动系统 Chrome。空白对照 Profile 已按唯一精确路径删除，浏览器与 probe 无残留；用户已登录的 App 私有持久 Profile 按用途保留且目录权限为 `0700`，未触碰默认 Chrome Profile、其他项目资源或系统钥匙串
- 文档：同步根/Backend README、前后端架构、工程结构和唯一开发台账；没有新增第二份计划
- 后续：B5-10 把受信浏览器、私有 Profile、`/user/self` 检测、二维码页面事实和重新检查组合为 Local Executor 内部扫码流程；B5-13 再从 App 平台状态页接入

### B5-10 抖音扫码流程

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：复用生产 `BrowserRuntime` 和 B5-09 Session detector，在一个专用可见外部浏览器窗口中封闭产生 `login_required/awaiting_scan/awaiting_confirmation/qr_expired/healthy/risk/unknown`，并由无参数 `recheck()` 重新读取真实页面事实
- RED：先把台账置为 `🧪 RED`；执行 `uv run pytest tests/unit/executor/test_douyin_qr_login.py -q` 在收集阶段准确失败于 `ModuleNotFoundError: automation_tool.executor.rpa.douyin.login`，`node --test tests/douyin-qr-login-boundary.test.mjs` 则准确失败于生产 `login.py` 不存在；随后才实现生产模块
- 生产接口：`DouyinQrLoginFlow.begin()` 只从同一个 Runtime 打开一个专用 headed 窗口并固定导航到官方 `/user/self`；首次页面仍在异步加载时最多等待 10 秒的健康/二维码共享就绪事实。`recheck()` 不接受 `QrScanned`、`Authenticated` 或布尔结果，只重新观察当前页面；仅在非冲突 `unknown` 时回到受保护入口复验。`close()` 只关闭本 flow 所属窗口且幂等
- 页面事实：二维码必须同时出现“扫码登录”“如何扫码”和语义图片，实际页面使用 `aria-label="二维码"`；实现不读取图片 `src`。扫码成功/手机确认与二维码失效使用有界前缀文本，Session healthy/risk 继续由 B5-09 detector 唯一判断；确认与过期同时存在、页面异常、未知 DOM 或导航失败均 fail closed，只有 `healthy` 关闭熔断
- 真实平台：系统 Chrome 150.0.7871.128 在 macOS 26.4.1 arm64 上沿生产 Runtime/flow 验证空白 Profile 进入 `awaiting_scan`；用户授权扫码后同一临时 Profile 从 `awaiting_scan` 收敛到 `healthy` 并输出 `ready`；原 App 私有持久 Profile 重开后直接为 `healthy`，证明首次登录态仍可复用。当前抖音未扫码二维码会自动刷新，本轮没有把平台未出现的“已过期”文案冒充为真实现象
- 隔离故障注入：真实系统 Chrome 通过 Playwright route 把测试 HTML 绑定到官方 origin，再从生产 `BrowserRuntime`→`DouyinQrLoginFlow` 原入口验证扫码、手机确认、健康、二维码失效、冲突、风险、专用窗口关闭和 Profile `0700`；route 证据只覆盖平台本轮无法自然触发的确定性分支，不冒充真实抖音最终状态
- 隐私与失败矩阵：覆盖未开始/重复 begin、close 前后使用、开关窗失败、导航/等待/定位失败、异步健康、无证据复验、冲突不刷新、风险、过期、确认和类型/版本篡改；源码及跨端契约拒绝 Cookie、`document.cookie`、storage state、页面/Profile 注入和 Tauri 页面句柄。对象表示、异常和 probe 只输出固定状态/evidence/version
- 门禁：B5-09/B5-10 聚焦套件 `35 passed, 3 skipped`，两份生产模块 241 条语句/50 个分支覆盖率 100%；Backend 全量 `928 passed, 3 skipped`，5564 条语句/1084 个分支覆盖率 100%，uv lock、Ruff/格式、严格 Mypy 185 个源码文件、OpenAPI/Executor Schema 全绿。Frontend 66 项 Node 契约、123 项 Vitest、冻结安装、ESLint、严格 TypeScript、API/production boundary 全绿；Rust 默认与 desktop-e2e 各 120 项、control-plane-e2e 121 项通过，三套 Clippy、Rustfmt 与 Actionlint 全绿
- 回归波动：首次连续执行第三套 Rust 测试时，两项既有 E4-13 测试的纳秒命名临时目录发生一次碰撞；未修改 B5-10 或既有测试，按原并行参数单独复跑 2 项和完整 control-plane-e2e 均通过。该波动不涉及运行中产品资源，但后续若再现应独立建账修复测试唯一标识
- 原始调用边界：本任务交付 Local Executor 内部生产页面工作流，没有 Control Plane HTTP、Tauri Command 或 React 用户入口，因此不启动隐藏 App 空壳。B5-13 必须从正式 PlatformAdapter 复用该 flow 提供平台状态、打开处理和重新检查，不得把 Python 选择器复制到 Rust/React 或以 UI Harness 代替浏览器链路
- 资源与清理：没有启动 Backend、PostgreSQL、Docker、App、测试服务器或固定监听端口；真实/隔离验收只短暂启动系统 Chrome。第二次扫码使用的临时 Profile、空白对照 Profile、路由夹具窗口和 probe 进程均已精确清理；首次用户授权的 App 私有持久 Profile 保留且目录权限为 `0700`，未触碰默认 Chrome Profile、其他项目资源或系统钥匙串
- 文档：同步根/Backend README、前后端架构、工程结构和唯一开发台账；没有新增第二份计划
- 后续：B5-11 将 `risk` 扩为显式人工接管生命周期，验证码、滑块和安全校验只允许暂停、引导用户处理和重新检查，禁止自动绕过

### B5-11 人工接管

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：把 B5-09 的验证码、滑块和风控页面证据统一转换为显式人工接管，让同一个可见浏览器窗口停留给用户处理；不得自动操作挑战，处理完成后必须重新读取页面事实才能恢复
- RED：先把台账置为 `🧪 RED`；Python 测试执行时在收集阶段准确失败于 `DouyinQrLoginState` 不存在 `HANDOFF_REQUIRED`，两项 Node 边界契约准确失败于生产 flow 仍是 `risk`/`douyin.qr-login.v1` 且缺少人工接管状态；随后才修改生产模块
- 契约升级：`DOUYIN_QR_LOGIN_FLOW_VERSION` 升为 `douyin.qr-login.v2`，工作流状态以 `handoff_required` 替代低层 `risk`，evidence 仍固定为 `risk_challenge`。B5-09 detector 保留 `risk` 作为页面健康事实，B5-11 只在工作流层投影人工处置要求，避免把页面分类与用户流程混成第二事实源
- 手工边界：ByteDance `verifycenter/captcha` 外层 iframe 是验证码、滑块和平台风控的统一可见挑战边界；实现不尝试读取跨源 iframe 内部类型，也不包含 click/fill/press/drag、OCR、验证码识别、求解或绕过代码。同一个 headed 窗口保持打开，所有挑战态继续 `circuit_open=true`
- 恢复语义：无参数 `recheck()` 只重新观察当前页面；挑战仍存在继续 `handoff_required`，登录失效回到登录流程，冲突/未知/页面故障继续 fail closed，只有 detector 真实返回 `healthy` 才关闭熔断。调用方没有“我已解决”“已认证”布尔值或强制恢复接口
- 真实边界：B5-09 已在官方抖音页观察到实际 ByteDance 验证中心风控 iframe；本任务再由真实系统 Chrome→生产 `BrowserRuntime`→生产 `DouyinQrLoginFlow`，以绑定官方 origin 的隔离路由分别回放验证码、滑块、风控外层挑战，三者均进入 `handoff_required`，页面改为健康事实后才恢复。没有为制造验证码而对真实账号执行异常行为，路由证据不冒充真实平台挑战最终状态
- 任务事件边界：Executor v1 已有 `handoff.requested` 与 Control Plane `awaiting_human` 收敛语义；本任务没有实际 Task/Attempt 上下文，不伪造事件或 ID。Wave 6/7 的真实 RPA runner 必须在执行中观察到 `handoff_required` 时停止新副作用并发出该正式事件；B5-13 先负责平台状态页和“我已处理，重新检查”用户入口
- 失败矩阵：覆盖初次风险、挑战持续、人工处理后健康、登录/二维码分支、冲突、未知、页面/导航/等待/定位失败、生命周期误用和状态/版本篡改；任何非健康状态都保持熔断。若后续动作已发出而最终平台状态不明，仍按既有协议进入 `OUTCOME_UNCERTAIN`，不能自动重放
- 门禁：B5-09～B5-11 聚焦套件 `36 passed, 3 skipped`，两份生产模块 241 条语句/50 个分支覆盖率 100%；Ruff、格式、严格 Mypy、uv lock、OpenAPI 与 Executor Schema 漂移检查全绿。Frontend 全量 67 项 Node 契约、123 项 Vitest、ESLint、严格 TypeScript、API/production boundary 全绿；本任务未修改 Rust、Tauri Command、Control Plane API 或协议 Schema，B5-10 同日全量 Rust/Actionlint 基线继续有效
- 原始调用边界：B5-11 仍是 Local Executor 内部生产页面工作流，没有用户可调用的 Control Plane HTTP、Tauri Command 或 React 页面，因此不启动隐藏 App 空壳；真实 App 点击与 IPC 纵向验收归依赖 B5-12 的 B5-13，不能提前用 UI Harness 冒充
- 资源与清理：未启动 Backend、PostgreSQL、Docker、App、测试服务器或固定监听端口；隔离路由验收只短暂启动系统 Chrome 并由 Runtime 关闭，测试 Profile 由 pytest 唯一临时目录回收。未要求用户再次扫码，未触碰保留的持久 Profile、默认 Chrome Profile、其他项目资源或系统钥匙串
- 文档：同步根/Backend README、前后端架构、工程结构和唯一开发台账；没有新增第二份计划
- 后续：B5-12 持久化本机单调 `session_revision` 并只向 Control Plane 上报平台、健康状态、revision 和观察时间，不上传 Cookie、验证码、页面原文或 Profile 路径

### B5-12 Session 健康上报

- 状态：✅ 已完成
- 日期：2026-07-19
- 提交：本任务提交
- 目标：把 B5-09 生产 detector 的封闭页面事实作为 Executor-scoped 消息上报；本机和服务端都以单调 epoch 阻止旧健康复活，Control Plane 只持久化最小状态，不接收浏览器秘密或页面内容
- RED：先把台账置为 `🧪 RED`；Python 聚焦测试在收集阶段准确失败于缺少 `PlatformSessionHealthEnvelope` 和 `PlatformSessionState`，随后才加入生产协议、账本、应用服务、仓储和 WebSocket 分发。没有用 REST 测试客户端、Mock socket 或直接仓储写入冒充原始调用入口
- 三端协议：Executor v1 新增唯一 Executor-scoped `platform.session_health`；exact payload 只有 `platform/state/session_revision/observed_at`，不允许 task/attempt scope 或附加字段。Pydantic 权威 Schema、TypeScript 与 Rust 正式解析器共同回放新增 valid/invalid fixtures；公共清单现为 7 valid、26 invalid，描述文本也不得出现页面/Profile 敏感概念
- 本机持久化：SQLite v1→v2 在单个排他事务内保留 identity、command、checkpoint 和 outbox，新增 `executor_platform_sessions(platform,state,session_revision,observed_at)`。首次观察建立 revision 1；倒序时间、相同时间不同事实、较低 epoch 和同 epoch 非健康→健康全部拒绝，重新登录或显式恢复必须 `advance_epoch`。正式 Executor 进程验收同步核对 v2 与四列精确表形状
- 服务端投影：Alembic `20260718_0014` 新增 `platform_session_health`，精确六列为 `installation_id/platform/state/session_revision/observed_at/updated_at`。仓储在 active Installation 行锁下拒绝旧 revision、倒序观察和同 epoch 非健康→健康；完全相同事实幂等。WebSocket 只接受当前已认证 Installation/Executor scope，不持久 message/executor ID、Cookie、二维码、验证码、页面原文或 Profile 路径
- 原始调用验收：`scripts/run_b5_12_acceptance.py` 使用后台无头系统 Chrome 把隔离健康 DOM 绑定到官方 origin，经生产 `DouyinSessionDetector`→`DouyinSessionHealthReporter`→SQLite v2→正式 WebSocket transport→真实 Uvicorn/设备 Session→完整 Alembic→PostgreSQL 查询六列 projection，最终输出 `Real-network non-sensitive Session projection acceptance passed`。B5-12 的原始调用者是 Local Executor，不是尚未实现的平台状态页，因此不启动隐藏 App 空壳；B5-13 再从真实 App 用户入口消费该投影
- 安全失败收口：首次真实编排使用系统临时目录时被账本正确拒绝 `/var` symlink，改为 canonical `/private/tmp` 后通过，证明私有状态路径没有为验收放宽。Backend 全量还发现正式进程测试遗留 v1 断言，已升级为 v2/精确列检查；Frontend 边界发现 Schema 说明包含敏感概念关键字，已从发布契约删除而不降低字段级拒绝
- 后台测试规则：本机常规自动化外部浏览器固定无头；既有 B5-07 产品 headed 打包验收改为显式环境开关，仅在独立桌面 CI Runner 或提前告知的专门验收执行。所有浏览器测试必须在成功/失败/超时/取消路径关闭 Page、Context、driver 和完整进程树，并复查本次项目资源无残留；正式产品扫码/人工接管浏览器仍保持可见
- 门禁：Backend 全量 `944 passed, 4 skipped`，uv lock、Ruff/格式 208 个文件、严格 Mypy 192 个源码文件、OpenAPI 与 Executor Schema 漂移全绿；Frontend 68 项 Node 契约、125 项 Vitest、ESLint、严格 TypeScript、API 与 production build/边界全绿；Rust 默认与 `desktop-e2e` 各 120 项、`control-plane-e2e` 121 项通过，Rustfmt、三套全目标 Clippy `-D warnings` 与 Actionlint 全绿
- 波动记录：Rust 默认套件的一项既有 E4-08 崩溃恢复预算测试在并行运行时单次读到 1/预期 2；未修改生产逻辑、超时或断言，按原参数精确用例和完整默认套件复跑均通过。若再次出现必须独立建账定位，不能长期依赖复跑
- 资源与隐私：最终验收使用唯一 Compose project、随机 loopback 端口、专属容器/网络/卷和 `/private/tmp` 唯一 App 状态，结束后容器、网络、卷、Uvicorn、Chrome、Playwright 与探针进程复查为空。未触碰或输出用户已登录的持久 Profile，也未要求再次扫码；默认 Chrome Profile、其他项目资源和系统钥匙串均未读取或修改
- 文档：同步项目规则、根/Backend README、前后端架构、工程结构、通用更新选型和唯一开发台账；没有新增重复计划。自动更新继续按 H8-18～H8-22 使用官方 Tauri updater 安装底座与业务无关的自有策略层，并包含用户主动“检查更新”入口
- 后续：B5-13 从正式 App 平台状态页接入健康查询、打开浏览器处理、无参数重新检查和安全注销入口；相关 Control Plane 请求必须从隐藏真实 App 的正式 TypeScript/Rust 网桥发出

### B5-13 平台状态页面

- 状态：✅ 已完成
- 日期：2026-07-20
- 提交：本任务提交
- 目标：让用户从桌面 App 查看抖音服务端健康投影，并通过同一个本机 Executor 打开登录处理或无参数重新检查；浏览器路径、Profile 路径、设备凭据和页面对象均不能进入 React 或 Control Plane
- RED：先把台账置为 `🧪 RED`；后端契约准确失败于缺少平台状态查询，前端页面/网关测试准确失败于缺少真实 Gateway，跨进程测试准确失败于缺少认证本机命令，隐藏 App 契约准确失败于缺少 B5-13 runner。收尾又新增“动作结果不能伪造服务端快照”测试，准确捕获页面以本机时间覆盖 PostgreSQL 投影，随后才修为动作提示与权威快照分离
- 状态查询：新增 Installation-scoped `GET /api/v1/platform-sessions/douyin`，复用 `app.control-plane` Session 和 active Installation 校验，只返回 exact `{platform,state,observedAt}`、`no-store`；尚无投影时返回 `unknown/null`。React 经 `TauriPlatformSessionGateway` 调用固定 Rust operation，Rust 自行从 App 私有 vault 换票，WebView 不接触 Bearer、Installation ID、revision 或原始错误
- 本机处理协议：`open_douyin_login`/`recheck_douyin_login` 不是服务端 HTTP 动作。Rust 只在本机解析已选择且重新受信的系统浏览器、稳定 current Douyin Profile 和独占 lease；若 Executor 未运行则经正式 Control Plane 换取独立 `executor.connect` Session 自动启动同一 signed package。Manager 在已有 stdin/stdout 上发送域隔离 `atlcp1` HMAC 命令并验签固定结果，认证、超时或畸形响应都会停止完整 Executor/浏览器进程树
- Executor 组合：Python CLI 的 stdin worker 与 WebSocket runtime 共用同一 Executor 进程、SQLite v2 和 `DouyinSessionHealthReporter`。`DouyinLoginCommandOperation` 在线程内复用一个 `BrowserRuntime`/`DouyinQrLoginFlow`，把真实页面事实放入正式 WebSocket 队列；健康、错误、EOF、App 退出和 Manager 强停都会关闭 flow、context、driver 与后代进程。生产扫码/人工接管保持可见，只有 `control-plane-e2e` 隐藏验收硬编码无头，远端不能下发 `headless` 或路径
- 页面事实：平台状态页展示服务端投影与最近观察时间；“打开登录处理”“我已处理，重新检查”展示本机 flow 的固定公开动作事实，但不自行改写服务端状态或生成观察时间。“安全注销”在本任务中明确禁用并标注由 B5-14 启用，避免在删除 Profile 的完整安全时序实现前伪造成功
- 原始调用验收：`scripts/run_b5_13_acceptance.py` 使用唯一隐藏 Tauri App，真实点击平台状态导航和两个操作按钮，经正式 TypeScript Gateway→Tauri IPC→Rust Control Plane client/Executor Manager→signed PyInstaller Executor→无头系统 Chrome→真实抖音页面→正式 WebSocket→Uvicorn/Alembic/PostgreSQL，核对唯一合法 `platform_session_health` 投影。用例末尾由 App 自己触发正常退出钩子，再审计 App、Executor、浏览器、容器和端口无残留；没有用 Mock、直接 HTTP 或直接 Python 函数冒充产品入口
- 门禁：Backend 全量 `958 passed, 4 skipped`，uv lock、Ruff/格式、严格 Mypy、OpenAPI 快照与 DTO 漂移全绿；Frontend 69 项 Node 契约、132 项 Vitest、ESLint、严格 TypeScript、生产构建/边界全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套测试及三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿
- 资源与隐私：真实验收启动前检查随机 loopback 端口，使用唯一 Compose project、容器/网络/卷、App identifier、AppData、SQLite 和 Profile；外部浏览器全程无头，结束后复查无项目进程或容器。没有读取、覆盖或输出用户保留的真实登录 Profile，也未触碰默认 Chrome Profile、其他项目资源或系统钥匙串
- 文档：同步根/Backend README、前后端架构、工程结构、OpenAPI/生成 DTO 和唯一开发台账；没有新增第二份计划
- 后续：B5-14 从当前禁用入口实现安全注销，严格按“持久熔断/拒绝新任务→停止关联执行→关闭浏览器并释放 lease→定向删除唯一 Profile→推进 revision 并投影 missing”完成；任一步失败不得伪报注销成功

### B5-14 安全注销

- 状态：✅ 已完成
- 日期：2026-07-20
- 提交：本任务提交
- 目标：从真实桌面平台状态页实现可确认、可重试、失败关闭的抖音安全注销；必须先持久阻断新工作，再停止并释放浏览器资源，只删除 current Douyin Profile，最后由正式 Executor 上报新的 `missing` epoch，任何中间失败都不能伪报成功
- RED：开始时先把唯一台账置为 `🧪 RED`；后端契约缺少 logout prepare 与持久门闩，Rust/Profile 测试缺少稳定句柄删除和 path-free session command，前端测试仍断言安全注销禁用，隐藏 App 契约缺少确认→删除→服务端 `missing`→拒绝新任务的完整事实，随后才逐层实现
- 服务端门闩：Alembic `20260718_0015` 新增 exact `platform_session_gates(installation_id,platform,state,session_revision,updated_at)`；`POST /api/v1/platform-sessions/douyin/logout/prepare` 复用 `app.control-plane` Session，在 active Installation 行锁下创建当前投影 revision +1 或无投影 revision 1 的 blocked gate，重复调用返回同一事实。Task create 与新 offer 在 gate 存在时 fail closed，已有同键 Task/command 重放仍幂等；claim 与 prepare 共用 Installation 行锁，已排队/待重投工作命令不再取出，只有取消/紧停可继续投递。同 revision 的 `missing` 保持阻断，只有更高 revision 的真实 `healthy` 才删除 gate
- 固定注销时序：`logout_douyin_session` 先调用服务端 prepare；成功后通过唯一 Manager `emergency_stop()` 关闭 flow、persistent context、driver 和完整 Executor/浏览器进程树并释放 Profile lease，任意 stop 错误都在删除前返回。随后只删除 current Douyin Profile，重启 signed Executor，发送不含 executable/Profile/headless 的 `douyin.logout.complete`，把本机 SQLite epoch 推进为 `missing` 并经正式 WebSocket 上报；Rust 最多有界等待 5 秒重新查询服务端，只有权威投影为 `missing` 才返回 React
- 安全删除：macOS/Unix 和 Windows 分别持有 App 私有根、平台目录、Profile 的稳定原生句柄；删除前获取 OS 排他锁并区分真实活跃 lease 与遗留 marker，将目标原子改名为确定 `.removing-<profile-id>` tombstone，重新打开并复验同一目录 identity 后才删除。崩溃重试可续删 tombstone；原目录+tombstone 同时存在、symlink/reparse、非目录、identity 漂移或活跃锁都 fail closed。current marker 只在删除成功后清除，平台父目录、兄弟 Profile、Executor SQLite、设备身份/凭据和设置全部保留
- 本机认证协议：复用 E4-06 每次启动会话与 `atlcp1` 域，新增唯一 `douyin.logout.complete` session command；Pydantic/Rust exact parser 都拒绝路径和 headless 字段，跨语言固定 HMAC 向量一致。结果固定绑定 `douyin.session-control.v1/logged_out`；Python 在处理前关闭任何活跃登录 flow，生成严格单调 sequence，由 `DouyinSessionHealthReporter.record_logout()` 持久递增 revision 并只上报非敏感 `missing` 事实
- 用户入口：平台状态页使用 Ant Design 二次确认，pending 时禁止重复触发；确认前不调用 Gateway，取消不产生副作用。完成后页面只采用 Tauri 返回的权威服务端快照，不以本机时间、按钮结果或乐观状态伪造注销成功；“暂不注销”不改变服务端或 Profile
- 原始调用验收：扩展唯一 `visible=false` 隐藏 App 编排，从真实页面打开平台状态、触发登录检查、确认安全注销，经正式 TypeScript Gateway→Tauri IPC→Rust Control Plane client/Executor Manager→signed PyInstaller Executor→后台系统 Chrome→认证 WebSocket→Uvicorn/Alembic/PostgreSQL。最终数据库只有一个 `missing` projection 与相同 revision 的 blocked gate；测试再从 App 的生产 Task 创建入口发起真实 API 调用并确认被 gate 拒绝。AppData 审计确认 current marker/Profile/tombstone 消失而 `local-executor/state/executor-ledger.sqlite3` 保留
- 关闭边界：测试由 App 自己请求正常退出；嵌入式 WebDriver 在 App 关闭后清理 Session 固定收到 `ECONNREFUSED`，runner 只在没有测试断言错误且数据库、本机文件、端口、进程和容器审计全部通过时接受该精确签名。真实链路最终输出 `Hidden-App platform status and safe logout acceptance passed`
- 门禁：Backend 全量 `993 passed, 4 skipped in 80.13s` 且严格语句/分支覆盖率门禁通过，Ruff/格式 212 个文件、严格 Mypy 196 个源码文件、uv lock、OpenAPI 快照/生成 DTO 全绿；Frontend 70 项 Node 契约、132 项 Vitest、5 项 Playwright 无头 UI、ESLint、严格 TypeScript、生产构建/边界全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套测试、三套全目标 Clippy `-D warnings` 与 Rustfmt 全绿；隐藏 App 真实纵向验收通过
- 隐私与隔离：常规浏览器全程 headless，测试后 Page/Context/driver/App/Executor/浏览器进程、随机端口和项目专属 Compose 容器/网络/Volume 均复查无残留；只使用唯一隔离 App identifier/AppData/Profile/SQLite，没有读取、删除或输出用户保留的真实抖音 Profile，也未触碰默认 Chrome User Data、其他项目资源或系统钥匙串
- 文档：同步根/Backend README、前后端架构、工程结构、OpenAPI/生成 DTO 和唯一开发台账；没有新增第二份计划
- 后续：B5-15 从真实 App/Executor/浏览器重启验证登录复用与失效接管；若需要用户账号而用户不在线，先完成不破坏持久 Profile 的隔离重启/失效矩阵并记录待真实账号证据，不得停住后续无账号任务

### B5-15 登录复用验收

- 状态：🔍 工程实现与隔离纵向验收已完成；真实账号 App 双重启证据待补
- 日期：2026-07-20
- 提交：本任务提交
- 目标：证明同一 App 私有 Profile 可跨 Tauri App、Local Executor 和系统浏览器完整重启继续使用；页面失效时必须进入扫码或人工接管，不能沿用旧健康事实，也不能读取用户默认 Chrome Profile
- RED：先把唯一台账置为 `🧪 RED`。新增 Python 用例准确捕获已有健康 Profile 作为本机首个观察时，`recovered=true` 被 SQLite 错误拒绝；Node 契约准确失败于缺少 B5-15 隐藏 App spec/config/runner。两项失败都发生在生产入口实现前，没有放宽 assertion 或把 Mock 结果标为通过
- epoch 修复：`ExecutorLedger.record_platform_session()` 在本机尚无平台行时固定创建 revision 1，不再因首次事实同时带恢复标记而拒绝；只有已有行之后的显式健康恢复才递增 revision。已有非健康→健康仍必须显式恢复，倒序时间、同时间冲突和隐式复活继续 fail closed
- 四轮原始调用：`scripts/run_b5_15_acceptance.py` 只构建一个 `visible=false` Tauri binary，以同一 AppData/Profile 连续运行 `first/restart/expired/risk` 四个完整 App 生命周期。每轮都从真实 React 页面/TypeScript Gateway→Tauri IPC→Rust 浏览器复验/Profile lease/Executor Manager→本机 HMAC 命令→signed PyInstaller Executor→无头系统 Chrome→正式 WebSocket→Uvicorn/Alembic/PostgreSQL；App 自己退出后再启动下一轮，不用直接 Python 调用代替产品入口
- 复用事实：首轮健康建立 revision 1；第二轮 App、Executor、persistent context 和 Chrome 全部重建后仍直接健康且 revision 变为 2，页面没有进入扫码或人工接管。runner 在四轮之后逐次比较 current marker 摘要与 Profile 目录 device/inode，任何重建或换 Profile 都立即失败；本机 SQLite 最终为 `douyin/risk/revision 2`，PostgreSQL 只有同一最小投影、一个 Installation、零 Task、零 logout gate
- 失效接管：第三轮 expired 页面必须在原页面入口得到“请扫码”且服务端权威投影收敛 expired；第四轮 risk 必须得到人工接管提示并收敛 risk。`healthy` 之外始终保持熔断，不点击、不填写、不拖拽、不识别或绕过验证码，也不从测试代码直接写数据库状态
- 测试边界：确定性 healthy/expired/risk 页面只存在于 `backend/tests/fixtures/automation-tool-executor-b515.spec` 单独构建、单独签名的验收 Executor，通过 Playwright route 绑定官方 `/user/self` origin；正式 `backend/automation-tool-executor.spec`、生产 Vite 资产和 Tauri 配置都不包含该入口。夹具只替代平台不可控页面，不替代生产 detector/flow/BrowserRuntime/IPC/Manager/网络和数据库链路，也不冒充真实账号证据
- 真实账号核查：生产 AppData 当前没有 current Profile marker；系统临时目录内 8 个明确属于本项目的隔离 Chrome Profile 经生产 `BrowserRuntime`/`DouyinQrLoginFlow` 无头只读探测，健康候选为 0。没有扩大到用户默认 Chrome User Data，也没有读取/输出 Cookie、Profile 路径、账号名或页面原文；B5-10 的真实 Profile 直开健康证据仍保留，但因本轮无法从真实 App 双重启复现，状态如实保持 `🔍 待真实账号`，不阻塞 B5-16 及后续无账号任务
- 故障闭环：第一次完整编排在 expired 分支准确发现夹具二维码没有可见尺寸，生产 selector 正确拒绝；修复测试图片可见尺寸后原参数四轮通过。失败轮和成功轮结束后均确认项目专属 App/Executor/driver/Chrome、随机端口、Compose 容器/网络/Volume 和精确 AppData 无残留，未停止或删除其他项目资源
- 门禁：Backend 全量 `994 passed, 4 skipped in 86.94s`，6370 条语句/1276 个分支覆盖率 100%；Ruff/格式 213 个文件、严格 Mypy 197 个源码文件、uv lock、OpenAPI 漂移全绿。Frontend 71 项 Node 契约、132 项 Vitest、5 项 Playwright 无头 UI、ESLint、严格 TypeScript、API/生产构建边界与制品审计全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿；最终按精确格式化/类型修复后的源码重新构建并跑通四轮隐藏 App 纵向验收
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：B5-16 审计源码、构建产物和运行证据，证明正式 App/Executor 从未读取用户默认 Chrome/Edge User Data；用户真实账号和独立 Profile 再次可用时补跑 B5-15 真实 App 双重启并只更新同一台账状态

### B5-16 默认 Profile 隔离审计

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；新增 Node 契约准确失败于缺少 B5-16 隐藏 App spec/config/runner，没有把既有源码约定冒充为运行时证据
- 唯一生产链：契约固定 `BrowserProfileStore.current_douyin_profile()`→Rust owned lease→认证本机命令→Python `launch_persistent_context(request.profile_directory)`；递归扫描 `backend/src`、`frontend/src`、`frontend/src-tauri/src`，拒绝 Chrome/Edge 默认 User Data 常量、`--profile-directory`、Cookie 与 storage-state API，不允许增加第二个 Profile 来源
- 原始 App 验收：`scripts/run_b5_16_acceptance.py` 构建唯一 `visible=false` Tauri App 与单独签名验收 Executor，从真实平台状态页面点击“打开登录处理”，经 TypeScript Gateway→Tauri IPC→Rust 受信浏览器/current Profile/lease→Manager→signed PyInstaller Executor→无头系统 Chrome→正式 WebSocket→Uvicorn/Alembic/PostgreSQL；确定性 expired 页面只让生产 persistent context 保持活跃，不替代 Profile、runtime、网络或数据库链路
- 活跃系统证据：WDIO 只写不含敏感数据的临时 ready/release 信号。Chrome 存活期间 runner 从 OS 进程表定位唯一系统 Chrome 根，递归收集后代及引用私有目录的关联进程，要求所有 `--user-data-dir` 精确指向 current App 私有 Profile；随后用 `lsof` 确认这些进程确实打开私有 Profile 文件，且命令行和打开文件均没有落入用户默认 Chrome/Edge User Data
- 隐私与状态：runner 内部解析并复验 canonical current Profile，但不打印 Profile 路径或 UUID；React/IPC/spec 不接触 Profile、Cookie 或路径。最终本机 SQLite 和 PostgreSQL 均收敛 `douyin/expired/revision 1`，只有一个 Installation、零 Task、零 logout gate
- 清理：App 完成审计后由正式退出 Command 结束；独立复核确认本次 App/Executor/driver/Chrome、随机端口、专属 Compose 容器/网络/Volume 和精确 AppData 无残留，没有停止或删除其他项目资源
- 门禁：Backend 全量 `994 passed, 4 skipped in 84.90s`，6370 条语句/1276 个分支覆盖率 100%；Ruff/格式 213 个文件、严格 Mypy 197 个源码文件、uv lock、OpenAPI 漂移全绿。Frontend 72 项 Node 契约、132 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿；B5-16 隐藏 App 纵向验收按最终源码通过
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：进入 Wave 6 `D6-01` 抖音页面版本模型；B5-15 真实账号 App 双重启证据在独立登录 Profile 再次可用时补跑，不阻塞无账号任务

### D6-01 抖音页面版本模型

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；新增 41 个版本模型用例准确失败于 `page_version` 生产模块不存在，没有先写实现或把既有 Session selector version 冒充成搜索页面版本
- 单一模型：新增 Executor-only `douyin.web.v1` 封闭模型，版本、入口与 evidence 均为 `StrEnum`，合法观察只能是 v1 + `home/session_probe/search_results` 的精确组合；`unknown` 只能携带 `origin_invalid/entry_unknown/search_route_invalid`，伪造版本、入口或 evidence 组合在对象创建阶段拒绝
- 已知入口：首页只接受官方 canonical `/`，Session 入口只接受 `/user/self`，搜索结果只接受 `/search/<canonical percent-encoded term>?type=general`。共同要求精确 HTTPS `www.douyin.com`、无 userinfo、端口仅默认/443、无 fragment、受限 URL 长度；B5-09/B5-10 的 probe URL 改为从该唯一模型导入，不再维护第二份字符串
- 失败关闭：空值、非字符串、控制/Bidi、HTTP、裸域/伪域、userinfo、异常端口、未知页面、首页/Session 额外查询、空搜索词、多层路径、非法 UTF-8/percent escape、非 canonical 小写转义、超长、Bidi 搜索词、未知/重复 query 全部投影 `unknown` 且 `circuit_open=true`；`require_entry()` 对 unknown 和预期入口不匹配统一抛出不反射输入的固定错误
- 真实结构核查：使用生产 `BrowserRuntime`、系统 Chrome、一次性私有临时 Profile 对公开首页和 general 搜索 URL 做无头只读探测，只输出候选结构计数。当前空白 Profile 只能可靠确认官方 origin/route 和根节点，缺少足以宣称可交互页面版本的 DOM 锚点；因此 D6-01 如实只建立严格 route contract，`#root` 不算可操作证据，具体 DOM 版本锚点和页面对象由 D6-02 继续 RED→GREEN
- 边界：模型不导入 Playwright、Control Plane、Task 领域、数据库或网络客户端，不读取正文、Cookie、storage state、账号、Profile 或任意查询值；观察和错误 `repr/str` 不回显源 URL。页面版本继续只属于 Python Local Executor，不进入任务定义、WebSocket、Tauri IPC 或 React
- 故障闭环：首次全局 Node 契约准确发现 B5-09/B5-10 两条旧断言仍要求 probe URL 字面量留在 `session.py`。没有复制字符串回去放宽唯一来源，而是把契约升级为同时验证 Session 只导入中央常量、URL/`douyin.web.v1` 只存在于 `page_version.py`；原参数重跑 72 项契约全绿
- 测试：聚焦版本模型 110 条语句/30 个分支覆盖率 100%；Backend 全量 `1035 passed, 4 skipped in 86.61s`，6480 条语句/1306 个分支覆盖率 100%；Ruff/格式 215 个文件、严格 Mypy 199 个源码文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。Frontend 72 项 Node 契约、132 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿
- 文档：同步根/Backend README、后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：进入 `D6-02`，在该 v1 route contract 上集中封装搜索入口、结果列表、弹窗和登录跳转；任何 DOM 锚点不足或冲突继续 unknown/fail closed

### D6-02 页面对象基础

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；新增页面对象用例准确失败于 `search_page` 生产模块不存在，随后才建立实现。首轮覆盖率验收又准确暴露动态 DOM 下“识别后控件消失/查询失败”的未覆盖分支，补齐失败关闭用例后才转绿
- 唯一页面对象：新增 Executor-only `DouyinSearchPage` 和 `douyin.search-page.v1` selector contract，只接受 Runtime-owned `BrowserWindow` 并复用 D6-01 `DouyinPageVersionModel`，没有复制官方 origin、route 或页面版本判断。搜索输入、提交控件、结果列表、登录弹窗和阻塞弹窗全部集中在一个模块，优先使用 role/label/placeholder 等语义锚点，再使用版本化 `data-e2e` 兜底
- 封闭事实：观察结果只能是 `home_ready/results_ready/login_required/dialog_blocked/unknown` 与固定 evidence 的合法组合；Session probe 不查询 DOM 就投影登录跳转，登录弹窗优先于其通用 dialog 外壳，普通阻塞弹窗优先于搜索/结果锚点。入口与锚点冲突、锚点缺失、未知 route/version、定位异常和识别后 DOM 变化全部保持 `circuit_open=true`
- 只读边界：本任务只识别并返回当前仍可见的受控 Locator，不导航、不点击、不输入、不滚动、不执行脚本，也不读取 Cookie、storage state、Local/Session Storage 或页面正文；对象与异常不输出 URL、selector、DOM、账号或 Profile。跨端契约同时锁定页面对象、selector 和 Page 句柄不能进入 Executor wire schema、Tauri Command 或 Control Plane
- 验收边界：D6-02 交付的是尚未接入 Task/App 的纯只读页面基础，因此使用原对象公开入口和确定性 Page/Locator 双桩验收状态矩阵，没有启动隐藏 App 空壳，也没有把双桩冒充真实抖音 DOM。D6-01 的公开无头实探已说明当前空白 Profile 缺少稳定交互锚点；D6-15 负责浏览器 Fake 页面回归，D6-16 再由真实账号确认最终 DOM 与完整目标发现
- 测试：聚焦 23 项页面对象用例，126 条语句/32 个分支覆盖率 100%；跨边界 Node 契约验证只读、版本唯一来源和协议隔离。Backend 全量 `1058 passed, 4 skipped in 94.15s`，6606 条语句/1338 个分支覆盖率 100%；Ruff/格式 217 个文件、严格 Mypy 201 个源码文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。Frontend 73 项 Node 契约、132 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿
- 文档：同步根/Backend README、后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：进入 `D6-03`，把关键词长度、空白、控制字符、任务上限和服务端一致规则收敛为唯一领域约束

### D6-03 关键词校验

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`。Backend 新测试准确失败于公共 `protocol.douyin_search` 不存在；Frontend 明确复现 Zod 按 UTF-16 code unit 误拒服务端可接收的 80 个 emoji，同时放过 C1 `U+0085`。表单原调用方又证明首尾空白/C1 会调用 Gateway、81 个非 BMP 字符虽被旧长度规则拦截但没有统一错误；公共协议导出测试也在实现前准确失败
- Python 唯一策略：新增公共 `douyin.search-input.v1` 不可变值，`MAX_SEARCH_KEYWORD_CHARACTERS=80` 与 `MAX_TASK_TARGET_LIMIT=100` 只在该模块赋值。关键词必须是原样非空字符串、按 Unicode code point 最多 80 个、首尾无 Unicode 空白，并拒绝 C0/C1/DEL、Bidi、敏感赋值、私有路径、inline data；目标上限只接受真整数 `1..100`。对象和固定错误不回显关键词
- 双端复用：T3-17 `DouyinSearchExposureDefinition` 改为先构造公共输入值，Control Plane 与后续 Local Executor 都只能从 `automation_tool.protocol` 稳定入口导入，不再复制 Python 校验。FastAPI/Pydantic/OpenAPI 与 PostgreSQL 保留入口/持久层复验；数据库故障注入确认 C1 直接写入也被 check constraint 拒绝
- 桌面一致性：React 导出并复用 `douyinSearchKeywordSchema`、80 字符和 100 目标上限；`Array.from` 统一 Unicode code point 计数，表单在调用 Gateway 前拒绝空白、C1/Bidi、过长和安全文本违规，不 trim、截断或改写输入。生产 Gateway、Rust `chars().count()` 与服务端继续逐层 fail closed；跨语言 Node 契约逐项核对 Python、OpenAPI、React、Rust 与隐藏 App 验收边界
- 原调用方验收：扩展 `scripts/run_t3_17_acceptance.py` 的唯一 `visible=false` App。真实页面先输入含 `U+0085` 的关键词并确认只显示校验错误，再输入 80 个非 BMP 字符、目标上限 100，从正式 React 表单→TypeScript Gateway→Tauri IPC→Rust 网络桥→真实 Uvicorn/FastAPI→PostgreSQL 只持久化一条 exact draft 定义；测试没有直接调用下层函数或用 Harness 冒充 App
- 测试：聚焦 28 项 Python 用例，共享策略 22 条语句/2 个分支覆盖率 100%；数据库 C1 故障注入、16 项前端策略/组件用例、Rust Unicode/C1/上限用例和跨语言契约全绿。Backend 全量 `1082 passed, 4 skipped in 93.32s`，6631 条语句/1340 个分支覆盖率 100%；Ruff/格式 219 个文件、严格 Mypy 203 个源码文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。Frontend 74 项 Node 契约、145 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿
- 隔离与清理：隐藏 App 全程后台，未启动外部浏览器；固定 Control Plane 端口启动前为空。验收使用唯一 Compose project/App identifier/动态 PostgreSQL 端口，结束后复核 App、WDIO/Tauri、Uvicorn、固定/动态端口、容器、网络、Volume 与 App 私有测试数据无残留，没有触碰正在运行的其他项目资源
- 文档：同步根/Backend/Frontend README、前后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：进入 `D6-04`，只通过 D6-02 Page Object 和本任务公共输入执行打开首页、输入、提交、等待结果；网络慢、超时、登录/弹窗与未知页面继续失败关闭

### D6-04 搜索执行

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；Backend 新用例准确失败于 canonical 结果 URL 构造器和 `search.py` 不存在，跨边界 Node 契约同时因生产执行模块缺失失败，随后才实现最小闭环
- 单次执行：新增 Executor-only `douyin.search-execution.v1`，构造时只接受 Runtime-owned `BrowserWindow` 与 D6-03 公共 `DouyinSearchInput`。同一实例只允许 `run()` 一次，提交无自动重试；结果状态/evidence 是不可伪造的固定组合，对象与错误不回显关键词、URL、DOM 或 selector
- 固定路径：先用 D6-01 canonical 首页导航并等待 `domcontentloaded`，再只经 D6-02 Page Object 等候和二次确认输入/提交控件；原样填写关键词、恰好一次 `click(no_wait_after=True)`，随后等待 D6-01 构造并复验的精确关键词结果 URL，最后等候并再次取得结果列表才成功。执行模块没有 URL/selector 字面量或任意 locator
- 有界与熔断：导航 30 秒、页面锚点 10 秒总预算、动作 15 秒、结果 URL 30 秒；慢输入/提交/结果锚点按剩余预算推进。导航、动作、结果 URL 和结果锚点超时分别输出固定 evidence；登录、阻塞弹窗、版本未知、入口/锚点冲突、DOM 动态消失、Playwright 异常全部开路停止，点击后不确定也不再次提交
- 边界：本任务不滚动、不读取结果项、不评论、不私信、不执行页面脚本、不访问 Cookie/storage/page body，不创建 Task/Attempt 或调用 Control Plane。D6-05 从已确认的结果页增加有界滚动，D6-10 才接入正式命令闭环
- 原调用方验收：新增生产 `BrowserRuntime` 集成验收，以一次性 `0700` Profile、`headless=True` 系统 Chrome 和仅测试进程内的官方 origin 路由页，从公开 `DouyinSearchExecution.run()` 真实完成导航→输入→点击→URL→结果列表；没有直调下层 Locator、没有启动空壳 App，也没有触碰默认浏览器 Profile。真实账号/最终抖音 DOM 仍由 D6-16 独立验收，不把隔离页冒充线上证据
- 测试：聚焦 85 项 Python 用例，D6-01/D6-02/D6-04 三个变更模块共 394 条语句/90 个分支覆盖率 100%；跨边界 Node 契约验证单次、有界、Page Object 唯一 selector 来源和协议/App 隔离；生产 BrowserRuntime 无头验收通过并确认进程树退出。Backend 全量 `1103 passed, 4 skipped in 85.60s`，6789 条语句/1368 个分支覆盖率 100%；Ruff/格式 222 个文件、严格 Mypy 206 个源码文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。Frontend 75 项 Node 契约、145 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿
- 文档：同步根/Backend README、后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：进入 `D6-05`，只在 D6-04 成功且结果列表已复验的页面上实现最大轮次、最大目标、无新增停止和取消检查点

### D6-05 有界滚动

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；Backend 新用例准确失败于 `bounded_scroll` 生产模块不存在，跨边界 Node 契约同时因同一文件缺失失败，随后才实现最小控制层。首轮覆盖审计继续暴露滚动后页面切换/计数失败、下一轮前取消、增长后取消和备用 selector 等遗漏分支，全部补齐后才达到 100%
- 固定边界：新增 Executor-only `douyin.bounded-scroll.v1`，只接受 Runtime-owned `BrowserWindow`、D6-03 公共 `DouyinSearchInput`、D6-04 成功观察和无参数取消探针；同一实例只运行一次。D6-04 非成功事实、原始字符串/伪对象或不可调用取消源在构造时拒绝
- 有界推进：最多 20 轮，每轮只发一次 `mouse.wheel(0, 800)`；每轮结果增长等待总预算 3 秒、固定 100ms 轮询。初始或后续节点数达到 `target_limit`、一整个窗口无新增、或持续增长达到轮次上限时分别以固定 evidence 完成，不存在无限滚动
- Page Object 与计数：结果项的语义优先/`data-e2e` 兜底 selectors 只新增在 D6-02 `search_page.py`；公开方法只返回 `0..target_limit` 的节点数量，不读取昵称、文案、链接、图片或页面正文。非法上限、负数/bool 计数、定位异常、错误页面和 fallback 漂移均拒绝；D6-05 模块没有 selector 或任意 locator
- 取消与失败关闭：开始、每轮滚动前、滚动后的增长等待中和确认增长后均检查取消。明确 `True` 返回 cancelled；抛错或非 bool 返回 cancellation unavailable。登录、阻塞弹窗、结果页消失、页面观察/节点计数/真实 wheel 异常与计数倒退全部熔断且不再增加滚动轮次；模块不点击、不评论、不私信、不执行脚本、不读 Cookie/storage，也不连接 Control Plane
- 原调用方验收：生产 `BrowserRuntime` 以 `headless=True` 系统 Chrome 和一次性 `0700` Profile，在同一个窗口先经 D6-04 公开入口完成导航/输入/提交/结果确认，再经 D6-05 发送两个真实 wheel，使隔离官方 origin 测试页从 1 个增长到目标 3 个后停止。没有直调 Page Object/Locator，没有启动空壳 App，不读取默认 Profile；结束后 Runtime 和 Chrome 进程树关闭。真实抖音 DOM/账号仍留给 D6-16
- 测试：聚焦 60 项 Python 用例，D6-02/D6-05 两个变更模块共 347 条语句/102 个分支覆盖率 100%；跨边界 Node 契约锁定轮次、取消、Page Object selector 所有权和无副作用边界。Backend 全量 `1121 passed, 4 skipped in 86.15s`，6972 条语句/1426 个分支覆盖率 100%；Ruff/格式 225 个文件、严格 Mypy 209 个源码文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。Frontend 76 项 Node 契约、145 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿
- 文档：同步根/Backend README、后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：进入 `D6-06`，在当前有限结果页上建立稳定去重键、最小摘要、来源与 page revision，不把节点计数冒充可持久化 Candidate

### D6-06 Candidate 模型

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；42 项 Backend 目标用例准确失败于公共 Candidate 导出不存在，跨边界 Node 契约同时因 `protocol/douyin_candidate.py` 缺失失败；随后才加入最小模型与公共入口
- 最小模型：新增不可变 `douyin.candidate.v1`。Candidate 只含规范平台目标 ID、`DouyinCandidateSummary(display_name, public_handle?)`、唯一 MVP 来源 `general_search_author`、page revision 与派生去重键；没有头像、简介、联系方式、页面正文、HTML、绝对/个人链接或任意扩展字典
- 输入边界：目标 ID 为 `1..128` 位 ASCII 字母数字起始、后续只允许字母数字/点/下划线/连字符；展示名为原样非空 `1..80` Unicode 字符，拒绝首尾空白及共享控制/Bidi/secret/path/inline-data 违规；可选公开号为 `1..64` 位同类规范 ASCII；page revision 复用 I2-10 `1..2^53-1` 真整数范围。字符串枚举、bool/float 冒充、URL/path/query 与伪造对象全部拒绝
- 稳定去重键：构造器不接受外部 key，而是以固定域 `automation-tool.douyin.candidate-key.v1\0` + 原始规范目标 ID 做 SHA-256，输出 `atdck1_` + 43 位无 padding Base64URL。固定 golden vector 防算法漂移；同一目标跨名称、公开号和 page revision 变化保持同键，不同目标分离。Key 支持精确 parse，非 canonical 大小写/字符/长度拒绝
- 边界：Candidate/摘要/key 和错误均不回显平台 ID、名称或公开号；模块不依赖 Playwright/RPA、Control Plane、数据库或 Tauri。当前只通过 `automation_tool.protocol` 供后续消费，明确不修改 Executor v1 Schema、不进入 IPC、不持久化；D6-07 才实现页面事实到此模型的隐私裁剪
- 原调用方验收：本任务产物是纯不可变协议值和确定性 key 算法，没有网络、App、浏览器、数据库或外部副作用；直接通过公开构造器/parse、固定 golden vector、跨 revision 等价与拒绝矩阵验收原调用方式，不启动无意义的隐藏 App 或浏览器空壳
- 测试：42 项聚焦 Python 用例，75 条语句/10 个分支覆盖率 100%；跨边界 Node 契约验证字段最小化、依赖隔离和未提前入 wire。Backend 全量 `1163 passed, 4 skipped in 86.27s`，7048 条语句/1436 个分支覆盖率 100%；Ruff/格式 227 个文件、严格 Mypy 211 个源码文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。Frontend 77 项 Node 契约、145 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿
- 文档：同步根/Backend README、后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：进入 `D6-07`，只从受控结果项字段构造 Candidate，裁掉非必要个人信息、页面原文和绝对链接/凭据

### D6-07 目标隐私裁剪

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；Python 目标用例准确在收集期失败于 `candidate_extraction.py` 不存在，跨边界 Node 契约也因同一生产模块缺失失败，随后才实现页面字段裁剪与执行器
- 单次提取：新增 Executor-only `douyin.candidate-extraction.v1`，只接受 Runtime-owned `BrowserWindow`、`1..100` 真整数上限和 `1..2^53-1` page revision，同一对象只运行一次。结果封闭为 completed/blocked/unknown 与固定 evidence，repr/异常不回显候选、源链接或 DOM
- Page Object 边界：结果项、作者和作者名 selector 只存在于 D6-02 `search_page.py`；页面层只读取受控作者节点的 `data-user-id`、`href`、可选 `data-user-handle` 与专用名称节点。相对或官方 HTTPS 作者 href 在本机立即缩减为 path 中的规范目标 ID 并丢弃 query；跨域、scheme-relative、userinfo、fragment、多层路径、超限/控制字符和 data ID/href 冲突均拒绝
- 隐私与一致性：输出只能是 D6-06 `DouyinCandidate` tuple，没有自由字典、绝对链接、query、页面正文、HTML、头像、简介或联系方式。任一字段不规范、节点消失、浏览器读取异常或读取期间页面版本/入口漂移都会丢弃整个快照，不返回部分结果；空结果是合法最小快照。该层不读取 Cookie/storage，不访问网络客户端/Control Plane，不持久化，也未提前修改 Executor v1 wire 或 Tauri IPC
- 原调用方验收：生产 `BrowserRuntime` 以一次性 `0700` Profile、`headless=True` 系统 Chrome，在同一窗口先从 D6-04 公开入口真实完成搜索，再调用 D6-07 公开提取器得到两条 Candidate；隔离官方 origin 页面中故意放入的 token、头像 URL、联系方式、正文和完整作者链接均未进入结果。验收没有直调 Locator、没有启动可见 App、没有读取默认 Profile，结束后 Runtime/Chrome 进程树关闭；真实抖音 DOM 仍由 D6-16 承接，不把隔离页冒充线上证据
- 测试：D6-02/D6-07 聚焦 71 项 Python 用例，383 条语句/112 个分支覆盖率 100%；Backend 全量 `1206 passed, 4 skipped in 86.31s`，7251 条语句/1496 个分支覆盖率 100%。230 个 Python 文件格式、Ruff、严格 Mypy 214 个源码文件、uv lock、OpenAPI 和 Executor Schema 漂移全绿。Frontend 78 项 Node 契约、145 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试及三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿
- 文档：同步根/Backend README、后端架构、工程结构和唯一开发台账；D6-06 历史契约只移除“Page Object 尚未解析 Candidate”的阶段性断言，滚动层无解析和 wire/IPC 未接入门禁保持不变
- 后续：进入 `D6-08`，只对本任务已裁剪 Candidate 做本任务去重、历史窗口去重和黑名单原因，不重新读取页面或扩展个人信息

### D6-08 黑名单/去重

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；Python 领域测试准确在收集期失败于 `douyin_candidate_policy.py` 不存在，跨边界 Node 契约也因同一生产模块缺失失败，随后才实现并从 Control Plane domain 公共入口导出
- 固定策略：新增纯 `douyin.candidate-policy.v1`，MVP 历史去重窗固定 30 天且 UI 不可放宽；以包含 cutoff 的 UTC 区间判断历史。输入 Candidate/history/blacklist 均为 exact tuple、各限 100 项，Candidate 必须属于同一 page revision；历史和黑名单只携带稳定 `DouyinCandidateKey`，不接受平台 ID、昵称、公开号或任意原因文本
- 原因与顺序：评估保留输入顺序和全部 Candidate，不做破坏性过滤；每条 Decision 只有 `eligible/duplicate_in_task/duplicate_in_history/blacklisted` 之一，优先级固定为黑名单 > 本任务后续重复 > 窗口内历史重复 > 可用。同一目标跨名称/handle 变化仍按 D6-06 key 命中；提供分类计数、可用 Candidate tuple、统一 page revision 与 cutoff 供后续预览/持久化
- 失败关闭：未来历史、非 UTC/非 datetime、早于可计算窗口的时钟、重复历史/黑名单 lookup key、伪类型、超限集合和混合 revision 全部固定拒绝。Evaluation 自身拒绝把首条伪造成本任务重复、把后续重复伪造为可用/历史重复或拼接不同 revision，错误/repr 不回显候选或 key
- 边界与原调用方：本任务产物是 Control Plane 纯领域函数，正式调用方式就是传入已构造 Candidate、相关历史/黑名单 key 与 UTC 评估时刻并取得不可变 Evaluation；没有 App、API、数据库、网络、浏览器、Executor 或外部副作用，因此不启动空壳 App/服务。策略不导入 SQLAlchemy/仓储/RPA/HTTP/Tauri，也未修改 Executor wire/IPC；D6-09 才接真实 PostgreSQL 事实和 Target 持久化
- 测试：26 项聚焦 Python 用例，134 条语句/34 个分支覆盖率 100%；Backend 全量 `1232 passed, 4 skipped in 87.29s`，7386 条语句/1530 个分支覆盖率 100%。232 个 Python 文件格式、Ruff、严格 Mypy 216 个源码文件、uv lock、OpenAPI 和 Executor Schema 漂移全绿。Frontend 79 项 Node 契约、145 项 Vitest、5 项 Playwright 无头 UI、peer dependency、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试及三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿；UI 回归结束后 1420 与浏览器/Vite 进程均无残留
- 文档：同步产品规划、根/Backend README、后端架构、工程结构和唯一开发台账；没有新增重复计划
- 后续：进入 `D6-09`，以 PostgreSQL/Alembic 建立 Installation-scoped `task_targets`、唯一约束、稳定分页，并在保存前调用本任务策略

### D6-09 Target 数据库

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`，并新增强类型 Target、真实迁移/Schema、原子仓储、历史窗口、并发替换、稳定分页、跨 scope 和损坏行矩阵；聚焦命令在收集期准确失败于 `TargetId` 未导出、`application.task_targets` 不存在，随后才实现生产代码
- 数据模型：新增 UUIDv4 `TargetId` 和不可变、脱敏 `TaskTargetRecord`。Alembic `20260718_0016`/SQLAlchemy 同步建立 `task_targets`：只保存父 Task/Installation、任务内 `1..100` ordinal、D6-06 的规范目标 ID/稳定 key/最小摘要/来源/page revision、D6-08 disposition/policy version 与 UTC 时间；无自由 JSON、页面正文、源 URL、头像、简介或联系方式。数据库再次约束 UUID 版本、长度/字符集、安全显示名、封闭枚举、跨运行时整数和时间顺序
- 唯一与归属：`(task_id,installation_id)` 复合外键阻止跨 Installation 挂接；Target 主键全局唯一，`(id,task_id,installation_id)` 为后续复合引用保留稳定绑定，`(task_id,installation_id,ordinal)` 保证任务内顺序唯一。dedupe key 刻意只建历史索引而不唯一，因此首条与 `duplicate_in_task` 后续行都能留在预览，不用数据库冲突掩盖策略原因
- 原子策略与历史：`SqlAlchemyTaskTargetRepository.evaluate_and_replace` 先锁 active Installation，再锁精确 Task；只按本批 Candidate key 聚合同 Installation、排除当前 Task 的最新历史事实，黑名单 key 由当前已认证用例传入并由 D6-08 再校验。策略评估、旧快照删除和全部 Decision 插入同事务完成；空批、快照早于父 Task、同/旧 page revision、吊销/未知/跨 scope、非法策略输入和约束冲突全部回滚并统一脱敏拒绝
- 并发与分页：Installation 行锁串行化同安装实例的历史视图；revision 2/3 并发写入最终只留下完整 revision 3，不出现混批。读取固定按 `(ordinal ASC,id ASC)` keyset，多取上限保留到 D6-11，且每次同时约束 Installation、Task 和 page revision；其他安装、其他任务与旧 revision 返回不可见。两个专用索引分别服务当前预览和 30 天历史查询，不使用 offset
- 原调用方：本任务正式入口是 Control Plane 仓储连接真实 PostgreSQL；迁移升级、autogenerate check、降级到 `0015`、重新升级、直接约束写入和仓储生命周期均走实际数据库，没有 Mock 代替。当前没有 HTTP/Executor wire/Tauri IPC 或 App 页面，因此不启动空壳客户端；D6-10/D6-11 才分别接命令闭环和公开预览 API
- 测试：聚焦 130 项通过，新模块/Target ID 共 184 条语句、40 个分支 100%；Backend 全量 `1273 passed, 4 skipped in 93.06s`，7507 条语句/1558 个分支 100%。Ruff/格式 221 个文件、严格 Mypy 221 个源码文件、uv lock/sync、OpenAPI 与 Executor Schema 漂移全绿。Frontend 80 项 Node 契约、145 项 Vitest、5 项 Playwright 无头 UI、依赖/peer、ESLint、严格 TypeScript、API 与生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿；首次并行三套 Rust 时 `desktop-e2e` 因共享 Cargo 锁/进程资源出现 3 个启动超时，未改产品代码，待其他套件退出后用同一正式命令串行重跑通过
- 清理与文档：同步根/Backend README、后端架构、工程结构和唯一开发台账；测试使用随机 loopback 端口、随机数据库密码和独立 Compose project。结束后 1420、Vite、Playwright/Chromium、Executor 及 `automation-tool-pytest-*` 容器/网络/Volume 均无残留，没有触碰默认浏览器 Profile、真实账号、其他项目或系统钥匙串
- 后续：进入 `D6-10`，把 D6-05～D6-07 的发现结果通过正式 Executor wire 上报，并在 Control Plane 调用本仓储后收敛 Task 状态

### D6-10 Discover 命令与 Target 收敛闭环

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；新增 App 入口、HTTP 契约、Executor 三类 wire、批次 accumulator、SQLite v3、PostgreSQL 收敛与隐藏 Tauri 纵向验收。目标用例最初准确失败于 Discover API/协议/本地执行入口不存在；实现后全量门禁又暴露 D6-07 旧 wire 断言、人工接管 Attempt 无法重试及新增分支覆盖不足，均先保留失败证据再修复
- Control Plane 启动：新增 App Session 保护的 `POST /api/v1/tasks/{task_id}/discoveries`，强制 `Idempotency-Key`，首次返回 202、同键精确重放返回 200。PostgreSQL 单事务锁 active Installation、抖音健康门闩、Task 与当前 Attempt，创建新 Attempt、`task.discover` Command 和 `task.discovery_started` Event，并把 Task 收敛到 `discovering_targets`；草稿、登录后、人工接管后和预览重发现使用同一状态机边界
- Executor wire：Executor v1 增加严格区分的 `task.discover`、`task.discovery_batch`、`task.discovery_completed`。命令只携带已持久化的关键词、目标上限和 Attempt page revision；结果每批最多 10 条 D6-06 最小 Candidate、总计最多 100 条，完成消息只携带封闭 outcome/evidence/count。Python、Rust、TypeScript、Draft 2020-12 Schema 和共享 fixtures 同步为 28 种消息、10 个 valid 与 27 个 invalid 文件
- 本地执行与恢复：Rust 授权的浏览器 executable/Profile 只保留在 Executor 进程内 `BrowserLaunchAuthority`，互斥 lease 每次启动前重新验证；`ProductionDouyinDiscoveryOperation` 只组合 D6-04 搜索、D6-05 有界滚动和 D6-07 隐私提取。`ExecutorCommandProcessor` 先 ACK，再把最多 10 条一批的结果和唯一完成消息原子写入 SQLite v3 durable Outbox；断线重发不会重复执行浏览器动作，重启按 command/idempotency 指纹精确回放
- 服务端收敛：WebSocket 只接受当前已 ACK 的 Task/Attempt/correlation/page revision 连续批次；bounded accumulator 最多同时保留 32 个 Attempt，拒绝跳批、改批、超量和跨 Attempt 拼接。完成时同一 PostgreSQL 事务重新验证全部 identity，调用 D6-09 策略替换 Target、追加终态事件并把成功收敛到 `awaiting_confirmation`；登录失效、人工接管和失败不保存 Target，并把本次一次性 Attempt 终结后允许用户重新发起。完成/批次重放必须与已持久化事实逐字段一致
- 失败矩阵：覆盖 Installation 吊销/平台门闩/健康缺失、Task 不存在/过期/非法状态、定义缺失、同键异任务、活动 Attempt、DB 约束、未 ACK/错误 scope、批次乱序/冲突/资源上限、完成计数/page revision 不符、登录/接管/失败、旧消息/坏指纹、断线重放和仓储异常；错误、日志、API、IPC、SQLite 与 PostgreSQL 均不含 Cookie、Profile 路径、页面正文、DOM、绝对 URL 或自由错误文本
- 原调用方验收：`scripts/run_d6_10_acceptance.py` 使用项目专属随机端口、Compose project、PostgreSQL、AppData、SQLite 和 Profile；唯一 `visible=false` 真实 Tauri App 从页面调用正式 TypeScript/Tauri Command/Rust Control Plane client，真实 Uvicorn 接收后由正式 `LocalExecutorProcess + ExecutorCommandProcessor` 上报批次，最终核对 PostgreSQL 两条 Target、Task `awaiting_confirmation`、Attempt succeeded、Command acknowledged 和连续事件。验收不走直接 HTTP/Mock/Harness；D6-04/D6-05/D6-07 已分别证明生产 BrowserRuntime，本纵向链路使用确定性 operation 避免日常测试触碰真实账号或弹出浏览器
- 测试：Backend 全量 `1333 passed, 5 skipped in 98.04s`，8374 条语句/1776 个分支覆盖率 100%；聚焦六个新增核心模块 909 条语句/212 个分支 100%，真实 PostgreSQL D6-10 矩阵 4 项通过。Ruff/格式 254 个文件、严格 Mypy 236 个源码文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。Frontend 86 项 Node 契约、149 项 Vitest、ESLint、严格 TypeScript、API/生产边界全绿；隐藏 App WDIO 1 项通过。Rust default、`desktop-e2e`、`control-plane-e2e` 三套完整测试、三套全目标 Clippy `-D warnings`、Rustfmt与 Actionlint 全绿
- 资源与文档：验收结束后随机端口、Uvicorn、Tauri App、Executor、PostgreSQL 容器/网络/Volume、AppData 和 SQLite 均精确回收；没有可见浏览器、默认 Profile、系统钥匙串或其他项目资源。同步根/Frontend/Backend README、前后端架构、工程结构和本唯一台账，没有新增重复计划
- 后续：进入 `D6-11`，基于已收敛的最新 page revision 暴露 Installation-scoped 目标预览、排除和确认 revision API；过期快照必须拒绝

### D6-11 目标预览 API

- 状态：✅ 已完成
- 提交：本记录、迁移、生成契约、后端、桌面桥和验收脚本属于单一 `feat: 完成目标预览接口闭环` 提交；完成后立即推送 `main`
- RED：先把唯一台账置为 `🧪 RED`，新增应用模型、Repository、真实 PostgreSQL 生命周期、FastAPI/OpenAPI、TypeScript/Rust 严格解析和隐藏 App 纵向测试；初始聚焦测试准确失败于 `task_target_previews` 模块不存在。全量事件精确词汇测试随后因新增两种事件失败；“重新发现必须使旧确认失效”回归在临时撤下实现后准确得到 `confirmation_count == 1`；目标来源强类型契约准确暴露 OpenAPI 仍是任意 string，均在保留 RED 证据后补最小实现
- API 与公开数据：新增 App Session 保护的 `GET /api/v1/tasks/{task_id}/target-preview`、`PUT .../exclusions` 和 `POST .../confirmations`。列表 cursor 绑定 page revision、task revision、ordinal 与 Target UUIDv4；写入口同时绑定期望 revision 和 `Idempotency-Key`。公开 DTO 只含 Target UUID、顺序、最小展示名/公开号、固定来源枚举、策略 disposition、用户排除/最终选择、聚合计数和确认时间，不返回平台目标 ID、dedupe key、页面正文、URL、Cookie、Profile 或服务端路径
- PostgreSQL 与状态收敛：Alembic `20260720_0018`/SQLAlchemy 同步增加 `task_target_exclusions`、`task_target_confirmations`、Target 预览复合绑定和 `task.target_selection_updated`/`task.targets_confirmed` 两种事件。排除在 active Installation、`awaiting_confirmation` Task 与精确 page/task revision 行锁内完整替换，只能选择 `eligible` Target；确认至少保留一个目标，原子写入选择 revision/数量/幂等指纹，追加事件并把 Task 推进到 `queued`。Target 重新发现会先清除旧确认，避免新快照继承旧授权
- 幂等与失败矩阵：同键同体重放返回既有事实且不重复 revision/event；同键改体、旧 cursor/page/task revision、跨 Installation/Task、策略排除项、重复或非法 Target、全部排除、非 UTC/倒序时钟、吊销 Installation、缺失/混合快照、确认后再排除、并发确认输家、持久事实被篡改或服务不可用均 fail closed。读取确认后的 running/paused/terminal 快照保持可用，但未确认快照只允许 `awaiting_confirmation`；错误不反射底层异常或隐私字段
- 桌面原调用方：Rust `ControlPlaneClient` 增加三个封闭 operation，Tauri 注册 `get_task_target_preview`、`replace_task_target_exclusions`、`confirm_task_target_preview`；正式 TypeScript source/Zod 逐层复验。`scripts/run_d6_11_acceptance.py` 由唯一 `visible=false` Tauri App 经正式 TypeScript source、IPC、Rust 网络桥和 App 私有凭据连接真实 Uvicorn/PostgreSQL，读取两条候选、排除第二条、确认并精确重放，最终核对 Task `queued` revision 5、四条事件、一条排除和一条确认；没有 Mock、直接 HTTP、UI Harness、可见窗口或外部浏览器替代 App 入口
- 测试：D6-11 应用/API/Repository 聚焦 46 项、438 条语句/106 个分支覆盖率 100%，其中包含真实 PostgreSQL 重发现失效回归；Backend 最终全量 `1379 passed, 5 skipped in 98.59s`，Ruff/格式 262 个文件、严格 Mypy 243 个源码文件、uv lock、OpenAPI 和 Alembic/Schema 漂移全绿。Frontend 87 项 Node 契约、152 项 Vitest、ESLint、严格 TypeScript、API 漂移和生产构建边界全绿。Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试与三套全目标 Clippy `-D warnings`、Rustfmt 全绿；隐藏 App WDIO 1 项通过。上游 WDIO 仍输出已知的外部 `tauri-driver` 误诊断和退出清理噪声，但实际 embedded WebKit Session 建立且断言通过
- 资源与文档：所有测试使用 `automation-tool-*` 唯一 Compose project、随机 PostgreSQL 端口和 D6-11 独立 App identifier；验收结束后 Uvicorn、Tauri App、WebDriver、端口、容器、网络、Volume 与 `com.aventador.automationtool.d611acceptance` AppData 均零残留，没有启动 Chrome、触碰默认 Profile、系统钥匙串、真实账号或其他项目资源。同步根/Backend/Frontend README、前后端架构、工程结构、OpenAPI/生成 DTO 和本唯一台账，没有新增重复规划文档
- 后续：进入 `D6-12`，将本任务正式 source 接入用户可见目标预览页面，展示摘要、策略标记、排除操作和最终确认；页面验收仍须由隐藏真实 App 发出本任务接口调用

### D6-12 目标预览 UI

- 状态：✅ 已完成
- 提交：本记录、用户页面、组合根注入、组件/契约测试、D6-12 隐藏配置、验收脚本和文档属于单一 `feat: 完成目标预览用户界面` 提交；完成后立即推送 `main`
- RED：先把唯一台账置为 `🧪 RED`；组件测试最初准确失败于 `TaskTargetPreview.tsx` 不存在，任务详情测试准确失败于等待确认页未挂载预览，严格 TypeScript 准确失败于 `WorkbenchShell` 未注入 source，生产组合根契约准确失败于 `main.tsx` 未构造正式 source，Rust 安全配置测试准确失败于 D6-12 独立隐藏配置不存在，Node 契约准确失败于测试准备 Command 不存在。跨 Task 切换回归另准确捕获已打开预览会对新 Task 多发一次读取；最终以 Task-scoped 确认状态修复
- 页面闭环：`TaskTargetPreviewPanel` 接入既有 `TaskRunDetails`，读取最多 100 个当前 revision 目标并展示发现、计划执行、用户排除和策略拦截计数；每行只显示最小名称、公开号、固定“抖音通用搜索作者”来源，以及可执行、本任务重复、30 天内已触达或黑名单标记，不渲染 Target UUID、平台目标 ID、dedupe key、URL 或页面事实。用户可逐项选择、全部取消、恢复全部，并通过明确二次确认进入队列；空选择无法确认，确认后全部编辑关闭
- Revision、幂等与恢复：每次排除都发送完整 eligible 排除集合并绑定当前 page/task revision；同一意图在结果不确定后复用同一幂等键，成功或选择 revision 改变后才换键。确认独立绑定最新 revision 并复用同意图键；过期快照显示固定安全提示并自动回拉，其他失败不反射底层文本。确认成功使任务详情/列表失效并继续等待权威事件；已打开状态以当前 Task ID 隔离，切换到另一个未等待确认的 Task 不读取预览
- 正式组合根：`main.tsx` 唯一构造 `TauriTaskTargetPreviewSource`，经 `App → WorkbenchShell → TaskRunDetails` 注入；业务组件没有 `@tauri-apps`、fetch、base URL、Header、Session 或任意 operation。D6-12 只消费 D6-11 三个固定生产 Command，不新增第二网络层；测试准备 Command 仅在 `control-plane-e2e` 特性注册并只负责注册、创建和发现测试 Task，不读取、排除或确认预览
- 生产同路径验收：`scripts/run_d6_12_acceptance.py` 使用唯一 `visible=false` 的 `com.aventador.automationtool.d612acceptance` Tauri App、真实 Uvicorn/PostgreSQL 与正式 LocalExecutorProcess。WDIO 从真实工作台进入任务详情，页面经正式 TypeScript source/IPC/Rust 网络桥读取 2 个目标、取消第 2 个并确认；PostgreSQL 最终核对 Task `queued` revision 5、Attempt succeeded、1 条第二目标排除、绑定 selection revision 4 的确认和四条连续事件。验收没有 Mock、直接 HTTP、UI Harness、可见窗口或运营浏览器代替页面调用
- 失败矩阵：组件覆盖策略目标不可选、全部取消后空选择禁止确认、过期 revision 回拉、未知传输错误脱敏、排除不确定重试同键、确认后锁定和跨 Task 状态隔离；D6-11 已覆盖并发、跨 Installation、非法 Target、确认重放和持久事实损坏。页面卸载会取消当前 Mutation，请求 Query 使用 TanStack AbortSignal；服务端最终状态仍只认权威快照/事件
- 测试：Frontend 全量 89 项 Node 契约、160 项 Vitest、5 项无头 Playwright 全绿；ESLint、严格 TypeScript、OpenAPI 漂移与正式生产构建边界全绿。Rust 默认与 `desktop-e2e` 各 136 passed/4 ignored，`control-plane-e2e` 137 passed/5 ignored；三套全目标 Clippy `-D warnings`、Rustfmt、Actionlint 全绿。D6-12 隐藏真实 App WDIO 最终复验 1 项通过；共用准备逻辑抽取后的 D6-11 隐藏 App 回归 1 项通过；Python 验收脚本 Ruff/格式全绿。本任务未改变后端业务源码，真实后端/API/迁移链已由该纵向验收执行
- 资源与文档：启动前确认 8765/1420 空闲；所有运行使用 `automation-tool-d612-*` 唯一 Compose project、随机 PostgreSQL 端口、独立 AppData、Executor state 和隐藏窗口。两轮验收及无头 UI Harness 后，Uvicorn、Tauri App、WebDriver、浏览器、端口、容器、网络、Volume 与 D6-12 AppData 均零残留；没有触碰用户默认 Profile、系统钥匙串、真实账号或其他项目资源。同步根/Frontend/Backend README、前端架构、工程结构和本唯一台账，没有新增重复规划文档
- 后续：进入 `D6-13`，在动作命令投递边界增加未确认副作用守卫，确保没有当前确认事实时 Executor 永远收不到评论、私信或其他 action

### D6-13 未确认副作用守卫

- 状态：✅ 已完成
- 提交：本记录、迁移、Outbox 确认绑定、PostgreSQL 失败矩阵、真实 WebSocket 验收和文档属于单一 `feat: 完成未确认副作用投递守卫` 提交；完成后立即推送 `main`
- RED：先把唯一台账置为 `🧪 RED`；真实 PostgreSQL 测试准确证明未确认的业务 `task.offer` 仍会成功入队，确认后的 command record 也没有 `target_confirmation_message_id`。第二条用例继续准确暴露删除 confirmation 后旧 offer 仍可被 claim；失败原因均落在 D6-13 目标边界，而非测试脚手架
- 精确分类：守卫只针对带 `douyin.search_exposure.v1` typed definition、未来可能承载浏览/评论/私信的业务 `task.offer`。无业务定义的 T3-09 offer 仍是空 payload、无平台副作用的协议骨架；`task.discover` 是只读发现，pause/resume/cancel/emergency-stop 是控制命令，均不要求目标确认，紧停在未确认状态下仍可入队和抢占
- 持久绑定：Alembic `20260720_0019` 与 SQLAlchemy 同步给 `task_commands` 增加可空 `target_confirmation_message_id`，数据库要求非空值为 UUIDv4 且只能绑定 `task.offer`。业务 offer 入队事务锁 active Installation，读取 typed definition 与当前 confirmation，要求 Task 为 `queued` 且 revision 精确等于 confirmed revision，再将 confirmation source message ID 固定进 Outbox；同键重放不能换绑。旧库中已有命令不补造确认，nullable 迁移保证可升级并由 claim fail closed
- Claim 守卫：`FOR UPDATE SKIP LOCKED` due 查询对业务 offer 使用关联 `EXISTS` 复验 Task/Installation/确认 message、Task revision 未倒退且状态为 `queued/running`。无绑定、确认被删除、绑定已过期、预确认状态或持久事实不匹配时不取得 lease、不写 WebSocket、不增加 delivery attempts，命令保持 pending；当前确认命令仍沿用既有 delivered/ACK/retry/expiry 语义
- 生产同路径验收：`scripts/run_d6_13_acceptance.py` 使用唯一 `automation-tool-d613-<pid>` Compose project、随机 PostgreSQL/Uvicorn 端口、完整 Alembic、真实设备凭据/`executor.connect` Session 和正式 `/api/v1/executors/connect`。同一 Installation 下，无绑定业务 offer 与入队后删除 confirmation 的旧绑定 offer 在多轮 dispatch 中均未到达 Executor；随后创建当前确认的 offer 后，WebSocket 只收到该 message。最终数据库精确核对前两条 `pending/delivery_attempts=0`、后一条 `delivered/delivery_attempts=1` 且绑定当前确认 ID
- 原调用方边界：D6-13 没有新增 App API，因此不启动 Tauri 或用直接 HTTP 冒充 App 页面；D6-11/D6-12 已由隐藏真实 App 证明 confirmation 的生产入口。本任务的原始消费方是认证 Executor WebSocket，验收使用正式 Uvicorn、Session、Registry、DeliveryService 和 PostgreSQL Outbox。测试未启动运营浏览器、未触碰平台账号，也没有把标准 WebSocket 客户端冒充已经实现的 Wave 7 平台动作
- 失败矩阵：覆盖未确认入队、当前确认绑定、确认删除后 stale claim、紧停不被误拦、平台 logout gate、并发 enqueue/claim 单赢家、lease/reconnect/ACK/expiry、错误 response、UUID/scope/status/time 数据库约束、迁移升降级和无业务定义 offer 回归。A7-03 签名/MAC ActionAuthorization、A7-04 Executor 本机硬下限、动作 payload 与真实平台最终状态均保持 Wave 7 范围，D6-13 不提前伪造
- 测试：Backend 全量 `1381 passed, 5 skipped in 104.43s`，8842 条语句、1890 个分支 100% 覆盖；Ruff/格式 264 个文件、严格 Mypy 243 个源码文件、uv lock、完整 Alembic 升降级/schema check 全绿。D6-13 真实 WebSocket 验收与 T3-09 真实网络重投/ACK/过期回归均通过；本任务未改变 OpenAPI、Executor Schema、Frontend 或 Rust 产品代码
- 资源与文档：验收前检查动态 Control Plane/PostgreSQL 端口；两次运行分别使用 `automation-tool-d613-*` 与 `automation-tool-t309-*` 专属容器、网络和 Volume，finally 后全部为零，Uvicorn/runner/监听端口无残留。没有启动 Tauri、WebDriver、Chrome、用户 Profile 或系统钥匙串；同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增重复规划文档
- 后续：进入 `D6-14`，对未知页面元素保存有界、脱敏诊断 Artifact 并进入人工接管；不得把 DOM/截图原文无界上传，也不得在页面未知时继续动作

### D6-14 页面漂移诊断

- 状态：✅ 已完成
- 提交：本记录、页面漂移专用 Artifact、发现编排/协议收紧、单元/真实浏览器/PostgreSQL 测试和文档属于单一 `feat: 完成页面漂移诊断与人工接管` 提交；完成后立即推送 `main`
- RED：先把唯一台账置为 `🧪 RED`；新增聚焦测试最初在收集阶段准确失败于 `automation_tool.executor.page_drift_artifact` 不存在，随后用例还要求原有 `page_version_unknown/conflicting_anchors` 不再作为普通失败，而必须写入诊断并进入 handoff。失败落在 D6-14 产品能力，不是环境、浏览器或测试脚手架
- 专用本机 Artifact：`executor/page_drift_artifact.py` 只接受固定 `page_version_unknown/conflicting_anchors` evidence、`search` 阶段与正 page revision。每份 JSON 最多 2 KiB、目录最多 20 份，文件名为 canonical UUIDv4；H8-09 已把底层迁移到唯一 Local Artifact Store，当前窄引用包含 SHA-256、固定 `application/vnd.automation-tool.page-drift+json` 媒体类型、大小和 `artifacts/evidence/page-drift/<id>.json` 受控相对路径。POSIX 目录/文件固定 `0700/0600`，不覆盖既有文件
- 隐私与边界：Artifact Schema 只有固定版本、ID、平台、操作、阶段、evidence、page revision 与 UTC 观察时间，没有自由文本输入，因此关键词、URL、DOM、HTML、页面正文、截图、Cookie、Header、凭据和 Profile/私有路径无法进入文件。H8-09 已统一引用与本机字节边界，仍不提供任意文件浏览、导出、上传或截图/Trace；H8-10/H8-12 分别负责诊断捕获和保留治理
- 熔断与收敛：`ProductionDouyinDiscoveryOperation` 只对页面层已明确识别的版本未知或锚点冲突写诊断，并在写入后立即关闭 Runtime，不运行滚动或候选提取。上述两种 evidence 在 `DouyinDiscoveryExecutionResult` 与 Executor v1 语义校验中只能配对 `handoff_required`；Control Plane 复用既有发现收敛事务投影为 `awaiting_human` 且不保存 Target。诊断因磁盘、权限或路径问题不可写时也不能解除熔断，仍进入人工接管
- 失败矩阵：覆盖坏 evidence/stage/revision、非 UUIDv4 ID、坏/naive 时钟、state 目录 identity 替换、非目录、未知目录项、单文件非法名称/类型/大小、超过 20 份、排他创建冲突、磁盘写入失败、write/fsync 中途失败及残片删除；发现层覆盖两种漂移、Artifact 写失败仍 handoff、登录/弹窗/普通不可用不误判、Runtime 总是关闭。现有 H8-12 仍负责完整过期保留、引用保护和清理治理
- 生产同路径验收：`tests/integration/test_page_drift_artifact_browser.py` 从正式 `ExecutorCommandProcessor.handle(task.discover)` 进入 `ProductionDouyinDiscoveryOperation`，使用隔离临时 Profile 与 `headless=true` 系统 Chrome 命中确定性锚点冲突页；最终正式 `task.discovery_completed` 为 `handoff_required/conflicting_anchors`，本机仅生成一份固定诊断，再由 H8-09 Store 按 Artifact ID 解析、枚举和校验读取，且不含测试关键词/页面文本/URL，BrowserRuntime 完整关闭。该能力没有 App API，故不启动 Tauri 或以直接 HTTP 冒充 App；原始调用方就是正式 Executor command processor
- 数据库验证：真实 PostgreSQL 18.4、完整 Alembic 与既有发现收敛仓储分别接收 `blocking_dialog/page_version_unknown/conflicting_anchors` 三种 handoff，均把 Task 投影为 `awaiting_human`，不携带 Candidate。Executor Schema 的结构枚举未扩张，Pydantic 语义约束和确定性 Schema 漂移检查均通过
- 测试：Backend 全量 `1397 passed, 5 skipped in 105.64s`，8996 条语句、1928 个分支覆盖率 100%；Ruff/格式 246 个文件、严格 Mypy 246 个源码/测试文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。聚焦新增 Artifact/发现编排模块分支覆盖率 100%，真实无头 Chrome 用例与真实 PostgreSQL handoff 矩阵均单独通过；本任务未修改 OpenAPI、Frontend、Rust、Tauri Command、数据库 Schema 或迁移
- 资源与文档：全部数据库测试继续使用 `automation-tool-pytest-*` 专属 Compose project 与随机 loopback 端口；浏览器只使用 pytest 临时 Profile/state，未触碰默认 Chrome User Data、真实抖音账号、AppData、系统钥匙串或其他项目。测试结束后 Chrome、Playwright、PostgreSQL 容器/网络/Volume 和监听端口零残留；同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增重复规划文档
- 后续：进入 `D6-15`，把正常、空结果、弹窗、登录跳转、未知版本和无限滚动页面固化为可回放 Fake 页面回归样例；继续使用正式 Page Object/执行器，不把 Fake Adapter 打进生产包

### D6-15 Fake 页面回归样例

- 状态：✅ 已完成
- 提交：本记录、七份静态页面语料、六场景正式命令回归、既有浏览器测试去重和文档属于单一 `test: 固化抖音发现页面回归语料` 提交；完成后立即推送 `main`
- RED：先把唯一台账置为 `🧪 RED`；语料契约最初准确失败于 `tests/fixtures/douyin_discovery_pages/` 不存在。首轮补齐后六场景中的空结果继续准确失败为 `results_ready_timed_out`，证明无高度的空 `role=feed` 在真实浏览器中不可见；修正为“容器可见但零 article”的确定空结果后，正式链路收敛到预期 `failed/no_candidates`
- 封闭语料：目录固定七个小型 UTF-8 HTML：首页、普通阻塞弹窗、Session probe 登录页、未知版本页、正常两候选、可见空结果和无限滚动结果。契约要求文件集合精确、单文件 `1..16 KiB`，不含 `http://`/`https://`、fetch、Cookie 或 Local Storage 依赖；页面不连外网、不读取本机数据，也不进入 PyInstaller/Tauri 正式包
- 正式链路：参数化回归为每个场景创建独立 pytest 临时 state/Profile 与 `headless=true` 系统 Chrome，从 `ExecutorCommandProcessor.handle(task.discover)` 进入 D6-10 生产发现编排；只用 Playwright route 把固定官方 URL 映射到本地 HTML，没有 Mock Page Object、Fake Adapter、直接调用结果构造器或可见窗口。每次结束都断言 BrowserRuntime 已关闭、Profile 权限保持私有
- 六类结果：正常页返回 2 个最小 Candidate 与 `completed/candidates_extracted`；空列表经一轮无增长停止后返回 `failed/no_candidates`；普通 dialog 返回 `handoff_required/blocking_dialog`；302 到 `/user/self` 返回 `login_required/login_required`；未知官方路径触发 D6-14 一份受限 Artifact 与 `handoff_required/page_version_unknown`；无限页每次 wheel 持续增加一项，但 20 轮硬上限后只提取初始 1 + 新增 20 = 21 个 Candidate，不因页面仍可增长而继续
- 去重与回归：D6-04 搜索、D6-05 有界滚动和 D6-07 候选隐私三个既有真实浏览器测试删除各自内联首页/结果 HTML，改读同一语料；原有搜索 URL、2 轮达到目标、隐私字段不离开 Page Object 和 Runtime 关闭断言全部保持。D6-14 锚点冲突专用验收独立保留，避免把页面漂移诊断和本任务未知版本样例混为同一证据
- 测试：Backend 全量 `1404 passed, 5 skipped in 114.94s`，8996 条语句、1928 个分支覆盖率 100%；Ruff/格式 247 个文件、严格 Mypy 247 个源码/测试文件、uv lock、OpenAPI 与 Executor Schema 漂移全绿。D6-15 六场景加语料契约 7 项、D6-04/D6-05/D6-07 共享语料回归 3 项均单独通过；本任务没有修改任何生产代码、协议、API、数据库、Frontend 或 Rust
- 资源与文档：浏览器测试只使用 pytest 临时目录和无头系统 Chrome；全量数据库继续使用 `automation-tool-pytest-*` 专属 Compose project 与随机 loopback 端口。结束后 Chrome/Playwright、pytest、PostgreSQL 容器/网络/Volume 和监听端口零残留，未触碰用户默认 Profile、真实抖音账号、系统钥匙串或其他项目；同步根/Backend README、后端架构、工程结构和本唯一台账
- 后续：进入 `D6-16`，优先复用用户已授权的独立抖音 Profile，从正式只读目标发现链完成真实搜索/预览并证明没有评论、私信或其他外部副作用；若真实 Session 已失效则保持待账号补验并继续 Wave 7 可离线任务

### D6-16 真实目标发现验收（当前待补）

- 状态：🔍 待真实账号；真实 Session 已健康，但首页验证码挑战需要用户按平台正常流程处理，不能自动绕过
- 首轮真实证据：只读检查确认生产 App 私有 Profile 目录仍存在且未被浏览器占用；`scripts/run_d6_16_browser_acceptance.py` 以 `headless=true` 系统 Chrome 先访问固定 `/user/self`，生产 Session detector 从短暂 `unknown` 稳定收敛为 `healthy`，无需重新扫码。随后同一 Profile 从正式 `ExecutorCommandProcessor.handle(task.discover)` 进入生产搜索，未产生 Candidate、Target 或平台副作用
- RED 与修复：首页当前展示 ByteDance verifycenter 验证码 iframe。修复前正式搜索等待 10 秒后为 `failed/home_ready_timed_out`；新增 Page Object RED 在首页/结果页两种入口准确证明 iframe 会被误判为锚点冲突。将 `session.py` 的同一 `DOUYIN_RISK_CHALLENGE_SELECTORS` 公开给 `search_page.py` 复用后，两种入口均立即成为 `DIALOG_BLOCKED/BLOCKING_DIALOG`，正式发现收敛 `handoff_required/blocking_dialog`，不再等待、滚动或提取
- 安全边界：使用 `agent-browser` 只读紧凑可访问性快照独立确认页面只有跨域验证码 iframe；未读取 iframe URL、Cookie、storage、网络响应、页面正文或账号信息，未截图/录屏，未点击、填写、拖拽或调用任何解题能力。诊断会话已按技能规范关闭；真实 runner stdout 只输出封闭 state/outcome/evidence/count，不输出 Profile 路径或候选摘要
- 当前完成度：风控发现与 handoff 修复已完成并验证，但 D6-16 的完成定义要求真实搜索得到候选、经 App 目标预览确认且证明无外部副作用；当前 `candidate_count=0`，因此不得标绿。用户正常解除首页挑战后，复用同一 runner 与隐藏 App 预览链补验；在此之前不重复触发挑战
- 门禁：风控 selector/Page Object/Search/Session 聚焦 `60 passed`；Backend 全量 `1406 passed, 5 skipped in 115.18s`，8997 条语句、1928 个分支 100%；Ruff/格式与严格 Mypy 覆盖 248 个源码/测试/验收脚本文件。真实 runner 最终稳定输出 Session healthy 与 `handoff_required/blocking_dialog`，退出码 3 精确表示真实目标验收未完成
- 后续：按既定规则跳过需要即时用户介入的真实挑战，继续 `A7-01` 风险策略领域模型；D6-16 与 B5-15 均保留为独立真实账号补验项，不伪造完成

### A7-01 风险策略领域模型

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`，新增 43 项纯领域失败矩阵；聚焦测试在收集阶段准确失败于公共 `ACTION_RISK_POLICY_VERSION` 不存在，证明没有复用旧账号服务或提前放入空壳实现让测试假绿
- 旧代码审计：只读复核 `agent-platform` 的 `AccountGovernancePolicy`、授权和连续失败用例；保留最小间隔、任务/日上限与连续失败阈值语义，明确删除旧租户、RBAC、平台账号、冷启动额度、内存锁、自由 `action_type` 和零间隔。旧代码中的 20/5/3 等值没有迁入当前项目
- 复合范围：新增不可变 `ActionRiskScope`，唯一键由强类型 `InstallationId`、封闭 `douyin` 平台和既有 `browse/comment/direct_message` 动作构成；自由字符串、伪造资源 ID/枚举和跨类型对象统一固定错误拒绝，repr 不暴露 Installation ID
- 显式硬限制：`ActionRiskPolicy` 必须调用方逐项提供正整数秒最小间隔、`1..100` 单任务动作上限、正 UTC 日动作上限和正连续失败阈值；零/负/分数秒、bool/float/string、超限与伪造版本 fail closed。最小间隔复用任务现有 1～3600 秒边界，任务动作上限复用目标硬上限
- 无伪安全默认：除固定契约版本外，四个运营阈值都没有默认值。`MAX_ACTION_RISK_LIMIT=2^53-1` 只是 Python/Rust/TypeScript 无损整数的结构上界，不是推荐日额度或安全阈值；具体值继续按产品规则等待受控真实账号校准。任务/日/失败阈值彼此独立，不用未经验证的大小关系制造隐式默认
- 分层边界：本任务只交付 Control Plane 纯领域对象，不导入 SQLAlchemy、FastAPI、Executor wire、Playwright、Tauri 或 React，没有网络、数据库、App API 或外部副作用，因此正式原调用方式就是公共构造器/不可变值/拒绝矩阵，不启动空壳 App 或服务。A7-02 才在 PostgreSQL 原子事务中计数和授权，A7-14 再把连续失败收敛为 handoff
- 门禁：聚焦 43 项通过，新模块 43 条语句、4 个分支覆盖率 100%；Backend 全量 `1449 passed, 5 skipped in 114.93s`，9041 条语句、1932 个分支覆盖率 100%。269 个 Python 文件格式正确，Ruff 全绿，严格 Mypy 249 个源码文件通过，uv lock、OpenAPI 和 Executor Schema 均无漂移
- 资源与文档：纯领域任务没有启动 App、浏览器、Uvicorn 或固定端口；全量数据库测试使用 `automation-tool-pytest-*` 专属动态端口、Compose project、网络和 Volume。同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增重复规划文档
- 后续：进入 `A7-02`，设计 PostgreSQL 风险策略/计数事实与原子授权事务；并发请求不能突破任务、UTC 日或最小间隔硬限制，服务端配置也不得放宽调用方更严格的任务间隔

### A7-02 服务端计数与并发授权

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`，新增应用记录、Repository 和真实 PostgreSQL 生命周期测试；两组聚焦测试均在收集阶段准确失败于 `application.action_risk_authorizations` 不存在。实现后的审计再新增同一 ActionId 动作意图变化用例，准确以 `_same_intent()` 缺少 `action` 参数失败，随后才收紧重放绑定
- 原子事实：迁移 `20260720_0020` 新增 `action_risk_authorizations`；每条记录保存 Action/Target/Attempt/Task/Installation/ordinal 绑定、封闭平台/动作、策略版本、有效最小间隔、三项策略阈值、任务/UTC 日授权后计数与 UTC 时间。成功授权在同一事务插入既有 `task_actions(status=authorized)`，复合外键禁止跨执行链或跨 Target 拼接，两个计数序号唯一约束作为并发第二道防线
- 服务端串行化：Repository 先 `FOR UPDATE` 锁定 active Installation，使同一安装实例的所有平台动作按数据库事务线性化；再复验当前 running Task/Attempt、任务定义动作、healthy Session 且无 gate、最新确认、同 page revision 的 eligible 且未排除 Target。任务上限按 Task/平台/动作，UTC 日上限按 Installation/平台/动作/日期，最小间隔跨 Installation/平台/动作计算；有效间隔固定取任务配置与服务端策略较大值
- 幂等与失败关闭：同一 ActionId 只有 Target、Attempt、Task、Installation 和动作完全一致时返回原不可变事实，不重复计数；任何身份/动作变化、吊销安装、错误状态/定义、Session 风险或门闩、过期确认、排除目标、时钟倒退、重复 ordinal、约束冲突和数据库不可用均固定拒绝或脱敏不可用，事务不留下半条 Action。运营阈值仍无默认值，A7-02 没有把结构上界当额度
- 分层边界：本任务只交付服务端内部 PostgreSQL 授权事实，不新增 HTTP、OpenAPI、Executor wire、Tauri Command、React 或外部平台动作；正式原调用方式是 Repository 对真实数据库的事务入口。A7-03 才从服务端时钟生成带 deadline/idempotency 的签名或 MAC ActionAuthorization，调用方不得把客户端自报时间作为权威时间
- 迁移与测试：空库完整升级到 `20260720_0020`、`alembic check`、降级到 `0019` 和重新升 head 全部通过；真实数据库验证策略快照、精确重放、任务/UTC 日/间隔限流、5 路并发仅 1 路成功、状态/动作/确认拒绝、时钟倒退、重复 Target 唯一冲突、数据库不可用和约束故障注入。聚焦 40 项通过，两份新增生产模块 160 条语句/40 个分支覆盖率 100%
- 门禁：Backend 最终全量 `1489 passed, 5 skipped in 119.36s`，9207 条语句/1972 个分支覆盖率 100%；274 个 Python 文件格式正确，Ruff 全绿，严格 Mypy 253 个源码/测试文件通过，uv lock、OpenAPI 和 Executor Schema 均无漂移
- 资源与文档：没有启动 App、浏览器、Uvicorn 或固定业务端口；真实 PostgreSQL 测试使用 `automation-tool-pytest-*` 独立 Compose project、随机 loopback 端口、专属容器/网络/Volume，并在两次全量和聚焦测试后全部回收，未触碰其他项目的 5432/8000/8080 资源。同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增重复规划文档
- 后续：进入 `A7-03`，在当前原子事实之上建立 action/target/attempt/deadline/idempotency 完整绑定的短期签名或 MAC 授权，并由 Local Executor 严格验签；D6-16、B5-15 继续保持独立真实账号补验，不阻塞主线

### A7-03 ActionAuthorization

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`，新增共享 claims/token、Control Plane 签发器与 Executor 验签器失败矩阵；聚焦测试在收集阶段准确失败于 `automation_tool.protocol.action_authorization`、签发和验签模块不存在。全量回归随后准确发现共享动作枚举新增说明会造成 OpenAPI 文案漂移；保持该失败证据后删除非必要 Schema 描述，原快照逐字恢复，没有以重生成快照掩盖意外 API 变化
- 非对称信任：选择 Ed25519 而非复用 Executor 已知的本机会话 MAC key，避免本机执行器持有可自行签发/扩大权限的共享秘密。Control Plane 签发器显式接受独立 32 字节部署私钥，Local Executor 验签器只接受对应固定公钥；私钥不进入 App、Executor、SQLite、协议、普通配置或系统钥匙串，真实部署注入和公钥固定由后续组合根完成
- 完整 claims：不可变 `action-authorization.v1` 精确绑定 Action、Target、Execution Attempt、Task、Installation、Executor、固定 `douyin` 平台、`browse/comment/direct_message` 动作、派生 `action:<action_id>` 幂等键，以及服务端 UTC `authorized_at/deadline_at`。授权生命周期调用方必须显式给出整秒 `1 秒..5 分钟`；服务端不能从客户端自报时间构造授权
- Canonical token：固定 `ataa1.<payload>.<signature>`，签名输入带独立域；payload 是排序、紧凑、ASCII、exact-field JSON，时间固定六位微秒 UTC，Base64URL 无 padding，token 最大 2 KiB。解析器拒绝重复/缺失/额外字段、错误资源类型、非 canonical JSON/时间/Base64、Unicode、坏签名长度和超限输入；错误、repr 与 fingerprint 不回显资源身份或 token
- Executor 验签：先用固定公钥验 Ed25519，再把九项执行意图逐字段匹配；授权最多允许 30 秒未来时钟偏差，到达 deadline 立即失效且没有过期宽限。篡改、错误 signer、跨 Target/Attempt/Task/Installation/Executor、动作或幂等键变化、坏时钟和非 canonical token 全部统一脱敏拒绝。A7-03 不执行平台动作，也不提前实现 A7-04 本机频控/紧停或 A7-07 副作用账本
- 生产同路径：真实 PostgreSQL 18.4、完整 Alembic 和正式 `SqlAlchemyActionRiskAuthorizationRepository` 先产生唯一 A7-02 原子事实；随后正式 Issuer 签发，Executor Verifier 以从该事实独立构造的完整 expectation 验签，最终数据库仍只有一条授权记录。该内部边界没有 HTTP、OpenAPI、Tauri Command、React 或浏览器调用，因此不启动空壳 App；后续动作 wire 接入时必须继续从真实 Executor 原入口验收
- 测试：协议/签发/验签聚焦 50 项、275 条语句/60 个分支覆盖率 100%，固定 golden token 防 canonical wire 漂移；Backend 全量 `1540 passed, 5 skipped in 120.15s`，9488 条语句/2032 个分支覆盖率 100%。280 个 Python 文件格式正确，Ruff 全绿，严格 Mypy 259 个源码/测试文件通过，uv lock、OpenAPI 和 Executor Schema 均无漂移；Frontend 89 项 Node 跨端契约、160 项 Vitest、ESLint、严格 TypeScript 和 API 快照全绿
- 资源与文档：没有启动 App、Uvicorn、可见浏览器或固定业务端口；全量中的浏览器用例继续 headless，数据库测试使用 `automation-tool-pytest-*` 专属随机端口/Compose/容器/网络/Volume 并由 fixture 回收。同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增第二份规划文档
- 后续：进入 `A7-04`，在 Executor SQLite 建立服务端不可放宽的本机最小间隔、任务动作上限、紧停 latch 与授权验签消费边界；服务器授权、本机硬限制和紧停必须同时通过才允许后续动作进入 prepared

### A7-04 Executor 本机硬下限

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`，新增本机策略、紧停、准入与 SQLite v4 迁移失败矩阵；目标测试最初准确失败于 `automation_tool.executor.action_gate` 不存在。实现后再以“删除策略行并用 1 秒/100 次重启”做安全审计，测试准确证明旧实现会自动重建并放宽 60 秒/1 次；随后才把策略单例固化为迁移事实，缺失或半损坏统一 fail closed
- 双重准入：新增 `ExecutorActionGate`，只接受真实 `Ed25519ActionAuthorizationVerifier`、真实 `ExecutorLedger`、显式本机策略和 UTC 时钟。`admit()` 的调用面只有 token 与 A7-03 完整 expectation，先验签再进入本机事务；服务器 claims、Executor wire、HTTP 与 App IPC 都没有最小间隔或任务上限参数，不能在单次请求中下调阈值
- 单调本机策略：SQLite v4 在迁移时创建固定策略单例，首次本机装配原子绑定；后续只取更长最小间隔和更小任务上限。弱配置、重启和并发构造只能得到已持久化的更严格结果；缺行、单字段 NULL、越界值或数据库异常不会自动修复成宽松默认。MVP 仍不虚构运营默认值，装配者必须显式提供 `1..3600` 整秒和 `1..100` 次
- 原子硬限制：同一 `BEGIN IMMEDIATE` 事务先检查持久紧停 latch，再处理 Action/idempotency 精确重放、Installation/平台/动作最小间隔和 Task/平台/动作计数，最后写入唯一 task ordinal。五路并发在任务上限 1 时只有一个赢家；精确重放不重复计数，意图或指纹变化拒绝；本机时钟倒退不能绕过频控
- 紧停与恢复：latch 独立于进程生命周期，重启后继续阻止所有新准入且优先于重放/频控。重复 engage 幂等；clear 必须同时命中当前本机 expected revision 和严格递增 UTC 时间，旧 revision、坏时钟、缺失 guard 或损坏事实全部拒绝。当前是 Executor 内部动作入口，没有新增服务器清除命令、Tauri/React 接口或真实平台动作；A7-07/A7-11/A7-12 必须先通过它才可进入 prepared/点击
- 隐私与迁移：SQLite 只保存完整资源绑定所需的最小 claims、授权/截止/准入时间、task ordinal 和 token SHA-256 指纹；不保存完整 token、服务端私钥、Cookie、Profile、页面事实、账号或任意配置，也不使用系统钥匙串。v1/v2/v3 原库原地升级到 v4，既有 command/checkpoint/outbox/平台 Session 精确保留；未来 schema、身份错绑、链接替换和损坏行继续 fail closed
- 生产同路径：真实 PostgreSQL 18.4 与正式 A7-02 Repository 先产生原子授权事实，正式 Control Plane Issuer 签发后，由 `ExecutorActionGate.admit()` 经固定公钥验签并写入真实私有 SQLite；验收核对本机策略 `(30,2)` 和 token 原文不在数据库字节中。该能力没有现存 App/HTTP 原调用面，因此没有启动空壳 App；后续动作 wire 接入时仍必须从真实 Executor 入口复验
- 门禁：A7-04 聚焦单元、进程重启和真实 PostgreSQL 集成 `32 passed, 1 skipped`，相关两份生产模块 722 条语句/172 个分支覆盖率 100%；Backend 全量 `1551 passed, 5 skipped in 120.43s`，9776 条语句/2098 个分支覆盖率 100%。282 个 Python 文件格式正确，Ruff 全绿，严格 Mypy 261 个源码/测试文件通过，uv lock、OpenAPI 与 Executor Schema 无漂移；Frontend 91 项 Node 契约、160 项 Vitest、ESLint、严格 TypeScript 和 API 快照全绿
- 资源与文档：没有启动 App、Uvicorn、可见浏览器或固定业务端口；全量浏览器用例保持 headless，测试结束由既有 fixture 关闭浏览器并回收 `automation-tool-pytest-*` 专属随机端口、Compose、容器、网络和 Volume，未触碰其他项目资源。同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增重复规划文档
- 后续：进入 `A7-05`，先以失败测试固定文案长度、空内容、控制字符、敏感模式与模板变量边界；D6-16、B5-15 继续保持独立真实账号补验，不阻塞主线

### A7-05 文案校验

- 状态：✅ 已完成
- RED：先新增 Python 共享策略、Task 领域/HTTP/PostgreSQL 失败矩阵、TypeScript 表单/Gateway 用例和 Rust 边界用例；实现前分别准确失败于公共模块/Schema 缺失、未知变量被放行与 PostgreSQL 仍在 `0020`，没有用旧的普通 500 字符校验冒充封闭模板已完成
- 唯一策略：新增不可变 `action-message-template.v1`，允许纯固定文案，也只允许 `{{target_display_name}}` 一个变量。删除合法占位符后必须仍存在非空字面；纯变量、未知/带空格/点号/单花括号/畸形占位符全部 fail closed。变量集按首次出现去重，异常与 repr 不回显文案
- 安全边界：按 Unicode code point 限制 `1..500`，不改写用户原文；首尾空白、C0/C1/DEL、Bidi、敏感赋值、Bearer/file/data URI、macOS/Linux 私有路径与 Windows 绝对路径均拒绝。`browse` 继续要求模板为 null，comment/direct_message 必须有合法文案
- 五层复验：React `TaskCreate` 直接复用 Gateway Zod Schema，Rust 在发起 HTTP 前复验，Python Task 定义只调用共享对象，FastAPI/Pydantic 保留入口约束，Alembic `20260720_0021` 和 `schema.py` 通过替换唯一合法占位符后禁止剩余花括号，保护直写 PostgreSQL 边界。跨端 Node 契约固定变量/长度/无渲染规则
- 原始调用方验收：唯一 `visible=false` Tauri App 从“新建任务”表单先提交 `{{unknown}}` 并确认 Gateway 未被调用，再提交 `{{target_display_name}}` 合法评论文案；请求经正式 TypeScript Gateway→Tauri Command→Rust Client→真实 Uvicorn/FastAPI→PostgreSQL，最终精确核对一条 Task 定义。全程隐藏、不打开可见浏览器，不使用 Mock HTTP，结束回收 App/Uvicorn/PostgreSQL/端口/Compose/AppData
- 边界：本任务只验证并持久模板原文，不渲染目标名、不调用 LLM、不识别页面 DOM/OCR、不执行评论或私信。A7-06 负责将精确目标/动作/文案/数量与确认 revision 绑定，A7-08/A7-09 再建立平台 Page Object
- 门禁：聚焦 Python 领域、HTTP 与真实 PostgreSQL `40 passed`；Backend 全量 `1584 passed, 5 skipped`，9814 条语句、2100 个分支覆盖率 100%，285 个 Python 文件格式、Ruff、严格 Mypy 263 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿。Frontend 93 项 Node 契约、183 项 Vitest、ESLint、严格 TypeScript 和 API 快照全绿；Rust 默认完整测试、Rustfmt 与全目标/全特性 Clippy `-D warnings` 全绿；隐藏 App 纵向验收 1 项通过
- 文档与台账：同步根/Backend/Frontend README、产品规划、前后端架构、工程结构和本唯一台账；同时将台账中全部历史完成标记统一为 `✅ 已完成`，未改动待验收/RED/未开始语义，没有新增第二份规划
- 后续：进入 `A7-06` 高风险最终确认；D6-16、B5-15 继续保持独立真实账号补验，不阻塞主线

### A7-06 高风险最终确认

- 状态：✅ 已完成
- RED：Backend 契约先准确证明响应缺少 action/messageTemplate/confirmationRevision，使用新 `confirmationRevision` 请求在旧接口返回 422；Frontend 组件准确证明最终确认未展示动作、文案和 revision，Source 仍发送旧字段；Rust 解析器准确失败于缺少新访问器。首次隐藏 App 验收又发现真实时序缺口：弹窗打开后后台 revision 变化可能使提交语义不清，测试坚持要求旧版本由后端真实拒绝，没有把等待放宽或改成 Mock
- 执行意图：新增不可变 `task-target-confirmation-intent.v1`，canonical SHA-256 绑定 Installation、Task、page revision、selection revision、封闭 action、原始 message template 与按预览顺序排列的全部已选 Target ID。确认响应公开动作/模板/确认 revision；确认请求只接受显式 `confirmationRevision`。Alembic `20260720_0022` 从 `0021` typed definition 与目标集合确定性回填既有确认，再强制 action/message/version/fingerprint 非空与封闭约束，降级精确移除新增列
- 服务端防篡改：确认必须命中当前 Task revision 且至少一个已选目标，原子写入完整意图和 `task.targets_confirmed`；读取、确认重放和 A7-02 Action 授权会重算目标集合与完整指纹，动作、模板、计数、版本、指纹、排除或定义任一变化均拒绝。D6-13 业务 offer 入队和 claim 额外复验确认动作/模板仍与 typed Task 定义一致；无副作用发现/控制命令不被误拦
- App 审阅边界：目标预览最终确认区和 Popconfirm 同时展示动作、原始模板、数量与 revision。弹窗以受控 open 状态和同步 ref 冻结用户打开瞬间的 page/revision/action/template/count；后台 Query/事件更新不能偷换已审阅提交。目标预览专用 Tauri 错误映射仅把 Control Plane `RequestRejected` 保留为 `request_rejected`，React 据此显示固定旧版本提示并回拉，其他错误仍统一脱敏
- 生产同路径：`uv run python ../scripts/run_d6_12_acceptance.py` 使用唯一 `automation-tool-d612-<pid>` Compose project、随机 PostgreSQL 端口、完整 Alembic、真实 Uvicorn、正式 Executor discovery 和唯一 `visible=false` Tauri App。App 先排除一个目标并打开 revision 4 确认弹窗，后台正式 Command 恢复目标推进到 revision 5；App 仍提交冻结的 revision 4 并收到真实 409，回拉后再次审阅 revision 5 才确认成功。最终 PostgreSQL 核对 queued/revision 6、两项目标、action/template/version/fingerprint 和连续事件
- 失败矩阵：覆盖旧请求字段、旧 page/task/confirmation revision、空选择、并发确认、跨 Installation/Task、未知 action、browse/文案错配、非法模板、确认/定义/选择/计数/版本/指纹篡改、迁移既有事实、降级、确认重放、授权复验、offer 投递守卫、协议未知字段、乱序目标、弹窗期间后台刷新和原生错误脱敏
- 门禁：Backend 全量 `1587 passed, 5 skipped`，9897 条语句/2120 个分支覆盖率 100%，286 个 Python 文件格式、Ruff、严格 Mypy 263 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 93 项 Node 契约、184 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 快照和 production boundary 全绿；Rust 三套配置、Rustfmt 与全目标/全特性 Clippy `-D warnings` 全绿；隐藏 App 纵向验收 1 项通过
- 资源与文档：隐藏 App 全程后台、不弹窗、不启动运营浏览器；验收前检查目标端口，结束回收 App/WDIO、Uvicorn、Executor、专属 PostgreSQL 容器/网络/Volume、AppData 和端口，未读取、停止或复用其他项目资源。同步根/Backend/Frontend README、产品规划、前后端架构、工程结构与本唯一台账，没有新增重复规划文档
- 后续：进入 `A7-07` 副作用账本；D6-16、B5-15 继续保持独立真实账号补验，不阻塞主线

### A7-07 副作用账本

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；Python 原调用方测试准确在收集阶段失败于缺少 `automation_tool.executor.side_effect_ledger`，跨目录 Node 契约准确失败于生产模块不存在。测试先固定 v4→v5 原地迁移、四态闭合、精确重放、五路并发、重启恢复、期限/紧停与损坏失败矩阵，再实现生产代码
- 封闭状态：新增脱敏不可变 `LocalSideEffect` 与 `prepared/dispatched/verified/uncertain` 四态。prepared revision 1 不带派发/结算时间，dispatched revision 2 只有派发时间，verified/uncertain revision 3 必须有单调结算时间；verified 必须且只能带 32 字节验证摘要，uncertain 不能伪造验证证据。非法 UUIDv4、平台、动作、幂等键、UTC、状态/时间/revision 组合及 repr 泄露全部拒绝
- 原子执行许可：评论/私信必须先命中 A7-04 当前准入事实并在 deadline 与本机紧停打开前写入精确 effect SHA-256。`begin_side_effect_dispatch()` 使用 `BEGIN IMMEDIATE`，同一 Action 五路并发只有一个调用者获得 `replayed=false`；只有该返回值允许 A7-11/A7-12 执行外部点击。此后重启或重复调用只返回 `replayed=true`，绝不重新授予点击许可
- 终态与恢复：dispatched 只能一次性结算到带匹配验证摘要的 verified 或不带证明的 uncertain，两个竞争终态只有一个成功；相反终态、摘要变化、倒序时间和越序结算 fail closed。`list_unresolved_side_effects()` 以稳定顺序有界返回 prepared/dispatched/uncertain，verified 不再进入恢复队列；崩溃窗口宁可保留为待核对/不确定，也不自动重放不可重复动作
- SQLite v5 与隐私：排他迁移新增 `executor_side_effects` 固定八列和恢复索引，外键复用 Action 对 Target/Attempt/Task/Installation/Executor 的完整绑定；v1～v4 既有命令、checkpoint、outbox、平台 Session、动作策略/紧停/准入原地保留。数据库约束拒绝状态、revision、时间和验证摘要矛盾。只保存两个 32 字节摘要、资源 ID 与 UTC 时间，不保存评论/私信正文、完整授权 Token、Cookie、Profile、页面原文、账号或密钥，也不使用系统钥匙串
- 原调用方验收：本任务公开能力的原始消费方是 Python Local Executor 动作执行层，验收直接通过正式 `ExecutorLedger` API 和真实私有 SQLite 完成迁移、并发、重开、损坏与故障注入，不启动无意义的 App、HTTP、PostgreSQL 或运营浏览器，也不以 Mock/Tauri 空壳冒充动作链路。A7-11/A7-12 接入真实动作时仍必须从同一 API 取得唯一许可
- 门禁：A7-07 聚焦 `29 passed, 1 skipped`，相关 `820` 条语句/`206` 个分支覆盖率 100%；Backend 全量 `1594 passed, 5 skipped`，10086 条语句/2168 个分支覆盖率 100%，Ruff/格式、严格 Mypy、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 94 项 Node 契约、184 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 快照、production boundary 与构建全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套测试、Rustfmt 与三套全目标 Clippy `-D warnings` 全绿
- 资源与文档：门禁启动前确认 1420 端口空闲，Playwright 固定无头并由 webServer fixture 关闭；没有启动 App、Uvicorn、PostgreSQL、Docker、运营浏览器或真实平台账号，没有新增固定端口、Profile、SQLite 或系统钥匙串资源。同步根/Backend README、后端架构、工程结构和本唯一台账，没有创建第二份规划
- 后续：进入 `A7-08` 抖音评论 Page Object；D6-16、B5-15 继续保持独立真实账号补验，不阻塞主线

### A7-08 抖音评论 Page Object

- 状态：✅ 已完成
- RED：先把唯一台账置为 `🧪 RED`；Python Page Object 测试准确在收集阶段失败于缺少 `automation_tool.executor.rpa.douyin.comment_page`，跨目录 Node 契约准确失败于生产模块不存在。测试先固定官方视频详情路由、ready/final 状态、selector 所有权、登录/风控优先级、半套/重复锚点、中途漂移、有界等待和错误脱敏，再实现生产模块
- 路由版本：D6-02 的共享 `DouyinPageVersionModel` 新增 `VIDEO_DETAIL/KNOWN_VIDEO_ENTRY`，只接受官方 HTTPS host、无 query/fragment、`/video/<1..32 位非零开头数字 ID>` canonical 路由；空/零/非数字/多段/带 query 视频路由继续 unknown。既有搜索 Page Object 在视频入口只返回 `required_anchor_missing` 熔断，不会把评论页误认成搜索页
- Selector 所有权：`comment_page.py` 独占 comment input、submit、final confirmation、login 和 blocking selector 组；每组使用一个合并 locator 并要求恰好一个可见元素，既避免同一元素多属性造成误判，也拒绝两个真实控件的歧义。访问器每次先重新观察完整页面，再返回对应 locator；Page Object 源码没有 fill/click/press，不提前执行平台副作用
- 封闭状态：只返回 `ready/confirmed/login_required/dialog_blocked/unknown` 与固定 evidence；登录证据优先于通用 dialog，风控/阻塞优先于动作锚点，final confirmation 优先于仍可见输入区，使陈旧成功提示不能继续动作。输入/提交缺一、重复锚点、未知/错误路由、坏 count、驱动异常和访问时 DOM 漂移全部 fail closed，异常与 repr 不回显 URL 或页面内容
- 有界等待：ready 和 final 最多接受 `1..60000` 毫秒并共享单次总预算；等待期间每个锚点出现后都重新观察路由、登录、风控和全部锚点。超时返回当前封闭事实，页面不可用返回固定 unavailable；路由变化、冲突或阻塞立即结束，不把单次 locator 可见冒充页面整体可用
- 原调用方验收：隔离 `douyin_comment_pages` Fake 语料通过生产 `BrowserRuntime → BrowserWindow → DouyinCommentPage` 在无头系统 Chrome、官方 origin 路由回放；正式 locator 完成 ready→输入/提交→confirmed，另覆盖阻塞和双输入漂移，runtime 退出后 Profile 保持 0700 且浏览器完整关闭。该证据验证生产 Page Object 原入口，但不访问真实账号、不冒充 A7-16 真实评论最终状态；A7-11 才在 A7-07 唯一许可后编排真实填写/点击
- 门禁：完整 A7-08 聚焦 `95 passed`；comment/page-version 两个新改模块 290 条语句/76 个分支覆盖率 100%。Backend 全量 `1616 passed, 5 skipped`，10260 条语句/2212 个分支覆盖率 100%，291 个 Python 文件格式、Ruff、严格 Mypy 268 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 95 项 Node 契约、184 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 快照、production boundary 与构建全绿；Rust 三套配置、Rustfmt 与三套全目标 Clippy `-D warnings` 全绿
- 资源与文档：Fake 验收与 UI 门禁均固定 headless，运行前确认 1420 端口空闲；BrowserRuntime/Playwright fixture 退出后无浏览器、Vite、pytest、Executor 或 Uvicorn 进程和监听残留。未启动 App、PostgreSQL、Docker、真实平台账号或固定业务端口，未读取或复用其他项目资源。同步根/Backend README、后端架构、工程结构和本唯一台账
- 后续：进入 `A7-09` 抖音私信 Page Object；D6-16、B5-15 继续保持独立真实账号补验，不阻塞主线

### A7-09 抖音私信 Page Object

- 状态：✅ 已完成
- 日期：2026-07-20
- 提交：本任务提交
- RED：先把唯一台账置为 `🧪 RED`；Python Page Object 测试准确在收集阶段失败于缺少 `automation_tool.executor.rpa.douyin.direct_message_page`，跨目录 Node 契约准确失败于生产模块不存在。测试先固定官方用户主页路由、profile/conversation/final 状态、selector 所有权、两类权限差异、登录/风控优先级、半套/重复/冲突锚点、中途漂移、有界等待和错误脱敏，再实现生产模块
- 路由版本：共享 `DouyinPageVersionModel` 新增 `USER_PROFILE/KNOWN_USER_PROFILE_ENTRY`，只接受官方 HTTPS host、无 query/fragment、`/user/<1..128 位字母、数字、下划线、点或连字符目标 ID>` canonical 路由；空 ID、非法首字符、非法字符、多段或带 query 的用户路由继续 unknown。既有搜索/评论 Page Object 将用户主页视为已知但不属于自身的入口并 fail closed，不会误用私信 DOM
- Selector 与权限所有权：`direct_message_page.py` 独占进入会话、message input/send、final confirmation、messaging unavailable、follow required、login 和 blocking selector 组；每组使用一个合并 locator 并要求恰好一个可见元素。两类权限分别返回固定 evidence，同时出现则冲突；访问器每次先重新观察完整页面再返回对应 locator，生产模块源码没有 fill/click/press，不提前执行平台副作用
- 封闭状态：只返回 `profile_ready/conversation_ready/confirmed/permission_denied/login_required/dialog_blocked/unknown` 与固定 evidence；登录优先于通用 dialog，风控与权限拒绝优先于动作锚点，final confirmation 优先于仍可见的输入区。profile 与 conversation 锚点混杂、输入/发送缺一、重复锚点、未知/错误路由、坏 count、驱动异常和访问时 DOM 漂移全部 fail closed，异常与 repr 不回显 URL、文案或页面内容
- 有界等待：profile、conversation 和 final 最多接受 `1..60000` 毫秒并共享单次总预算；每个锚点出现后均重新观察路由、登录、风控、权限和全部动作锚点。超时返回当前封闭事实，页面不可用返回固定 unavailable；权限变化、路由变化、冲突或阻塞立即结束，不把单个 locator 可见冒充页面整体可用
- 原调用方验收：隔离 `douyin_direct_message_pages` Fake 语料通过生产 `BrowserRuntime → BrowserWindow → DouyinDirectMessagePage` 在无头系统 Chrome、官方 origin 路由回放；测试调用正式 locator 完成 profile→进入会话→输入/发送→confirmed，另覆盖关注后才可私信与 profile/conversation 锚点漂移。Runtime 退出后 Profile 保持 0700 且浏览器完整关闭。该证据验证生产 Page Object 原入口，但不访问真实账号、不冒充 A7-17 真实私信最终状态；A7-12 才在 A7-07 唯一许可后编排真实填写/点击
- 失败矩阵与门禁：A7-09 聚焦 `68 passed`，direct-message/page-version 两个新改模块 334 条语句/88 个分支覆盖率 100%；Backend 全量 `1636 passed, 5 skipped`，10471 条语句/2266 个分支覆盖率 100%，271 个 Python 文件格式、Ruff、严格 Mypy 271 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 96 项 Node 契约、184 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 快照、production boundary 与构建全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 与三套全目标 Clippy `-D warnings` 全绿
- 资源与文档：Fake 验收与 UI 门禁均固定 headless；BrowserRuntime/Playwright fixture 退出后不保留运营浏览器或 Vite。未启动 App、Uvicorn、PostgreSQL、Docker、真实平台账号或固定业务端口，未读取默认 Profile、系统钥匙串或其他项目资源。同步根/Backend README、后端架构、工程结构和本唯一台账，没有创建第二份规划
- 后续：进入 `A7-10` 只浏览动作；D6-16、B5-15 继续保持独立真实账号补验，不阻塞主线

### A7-10 只浏览动作

- 状态：✅ 已完成
- 日期：2026-07-20
- 提交：本任务提交
- RED：先把唯一台账置为 `🧪 RED`；Python 用例准确在收集阶段分别失败于缺少 `profile_page`、`browse` 和 `douyin_user_profile_url`，跨目录 Node 契约准确失败于生产模块不存在。最终主页锚点二次校验又先以行为测试证明旧实现会误报 completed，再补最小复验；没有先写实现或用日志/Mock 结果让用例假绿
- 输入与路由：只接受 D6-10 发现链的完整最小 `DouyinCandidate`，不接受裸 URL、页面对象或私有路径。共享页面版本模块新增 `douyin_user_profile_url()`，以 A7-09 同一 `1..128` 位平台目标 ID 规则构造并复验 canonical 官方 `/user/<id>`；非法首字符、层级、超长、非字符串或未知路由全部拒绝
- Page Object：`profile_page.py` 独占通用 profile root、login 和 blocking selector，不包含评论/私信控件；只返回 `ready/login_required/dialog_blocked/unknown` 与固定 evidence。登录优先于通用 dialog，风控优先于主页锚点；缺失、重复、坏 count、未知路由、驱动异常和访问时 DOM 漂移全部 fail closed，repr 与错误不回显目标、URL 或页面内容
- 单次浏览：`browse.py` 固定一次 30 秒 `domcontentloaded` 导航和一次最多 10 秒的主页等待，执行层没有官方 URL/selector 字面量，也没有 click/fill/press/evaluate、Cookie/storage、HTTP 或 Control Plane 访问。同一实例拒绝重跑；开始、导航后、成功前各检查取消，探针抛错或非 bool 也开路停止；ready 后还须重新观察并二次取得唯一主页根节点才可 completed
- 封闭结果：只返回 `completed/login_required/dialog_blocked/cancelled/timed_out/unknown` 与固定 evidence；导航/主页分别保留超时事实，登录、风控、未知版本、锚点冲突、页面不可用和取消探针不可用不重试。completed 只证明目标主页当前可见，不写 A7-07 账本、不生成服务端 receipt、不宣称 Task 或真实业务动作已完成
- 原调用方验收：生产 `BrowserRuntime → BrowserWindow → DouyinBrowseExecution → DouyinProfilePage` 在无头系统 Chrome、一次性 0700 Profile 和官方-origin 隔离路由中依次回放 ready/login/blocked/drift 四个目标。ready 页面刻意放置评论/私信陷阱按钮，执行后 `window.__browseSideEffects == 0`；四次只产生四个 canonical 导航，Runtime 退出后浏览器完整关闭。该证据验证本任务 Executor RPA 原入口，不访问真实账号、不冒充后续 App/Executor wire 或 A7-15 结果 UI
- 失败矩阵与门禁：A7-10 聚焦 `81 passed`，profile-page/browse/page-version 三个变更模块 395 条语句/96 个分支覆盖率 100%；Backend 全量 `1665 passed, 5 skipped`，10737 条语句/2326 个分支覆盖率 100%，276 个 Python 文件格式、Ruff、严格 Mypy 276 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 97 项 Node 契约、184 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 快照、production boundary 与构建全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 与三套全目标 Clippy `-D warnings` 全绿
- 资源与文档：浏览 Fake 验收与 UI 门禁固定 headless；BrowserRuntime/Playwright fixture 退出后不保留测试浏览器或 Vite。未启动 App、Uvicorn、PostgreSQL、Docker、真实平台账号或固定业务端口，未读取默认 Profile、系统钥匙串或其他项目资源。同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增第二份规划
- 后续：进入 `A7-11` 评论动作执行；A7-10 的通用主页 Page Object 保持与评论/私信选择器隔离，D6-16/B5-15 真实账号补验继续独立保留

### M-01 正常开发 App 首次 Installation 注册补验（当前修复）

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本任务提交
- RED：`pnpm exec vitest run src/platform/tauri/task-creation-gateway.test.ts src/features/task-create/TaskCreate.test.tsx` 精确得到 2 项失败、10 项通过；原生 `credential_missing` 被旧 Gateway 映射成 `transport_unavailable/retryable=true`，旧组件找不到“当前设备尚未授权”提示
- 触发：2026-07-20 Windows 重启后，正常开发 App 的私有目录已有设备身份但缺少长期设备凭据；任务创建在 Rust 网络桥发出 HTTP 前以 `credential_missing` 失败，React 将其误映射为业务服务连接失败。既有隐藏 Tauri 验收使用隔离 App 标识，临时注册后清理，不能证明正常 App 重启后可直接运行
- 范围：先以失败测试固定缺少 Installation 授权的原生错误映射和用户提示，再复用正式 Rust 注册协议为当前正常 App 完成一次性本地授权；不新增产品账号、登录或注册页面，不绕过设备签名、凭据保险库和 Control Plane 鉴权
- GREEN：聚焦 12 项 Vitest 通过；Frontend 完整 96 项 Node 契约、24 个测试文件共 186 项 Vitest、ESLint 与严格 TypeScript 全绿。`credential_missing` 现在保持为不可重试授权错误，任务表单显示“当前设备尚未授权”，未知原生错误继续统一脱敏为传输不可用
- 当前本机真实边界：正常 App 标识使用既有 Rust 两步 challenge 完成 Installation 注册，正式 AppData 持久化设备凭据；同一隐藏真实 App 不调用注册准备命令，直接从生产任务表单创建 `draft`，Control Plane 返回 `POST /api/v1/tasks` 201，随后正常可见 App 成功换取 Session 并读取任务、事件
- Executor 本机装配：复用 E4-07 已验收的正式 PyInstaller spec 与开发 fixture signer，向正常 App 私有 `local-executor/package` 安装 356 个文件、约 148 MB 的 signed onedir；隐藏真实 App 经正式 `restart_executor` 逐文件验签、stdin Bootstrap、Windows 进程树和 WebSocket 首次健康心跳后返回 `running`。验收 App 退出后按正式 Drop 回收 Executor，正常 App 保持按需启动语义
- 交付边界：本补验没有把 Executor 纳入 Tauri 安装包，也没有新增客户 Release 登录入口；陌生 Windows 干净安装仍依赖 Wave 9 `P9-02/P9-04/P9-07`，客户 Demo 的账号登录与设备自动归属依赖 `U9-01`～`U9-06`，云端账号初始化与 Demo Profile 依赖 `C10-06/C10-07`。本任务只提交长期有效的缺凭据错误兜底与测试，不把本机手工装配冒充候选包完成

### M-02 客户 Demo 认证规划改为产品账号

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本任务提交
- 决策：撤销未实施的匿名设备申请、配对码、后台逐设备审批和轮询状态页；P9 恢复本地双平台候选版原编号，任何客户 Demo 改为先完成产品账号体系，登录后由 Rust 复用 I2 设备证明自动绑定当前账号
- 路线图：新增 `U9-01`～`U9-06` 覆盖账号范围、领域数据、登录 Session、Tauri 登录 UI、账号绑定设备和纵向验收；Wave 10 改为账号初始化、账号/设备吊销与“安装→登录→自动绑定→工作台”客户流程
- 架构边界：P9 当前无登录工作台仍是本地 MVP 事实；既有 bootstrap 只保留本地验收、隔离测试和明确迁移用途，不用于客户安装或审批；首个 Demo 不开放匿名自注册，也不提前引入组织、租户、RBAC、套餐、计费或云端平台 Cookie
- 实现边界：本任务只更新项目规则、README、产品规划、工程结构、前后端架构与唯一路线图，没有实现账号 API/UI、修改数据库或部署云资源；M-01 的 `credential_missing` 防御性错误映射继续保留
- 验证：已完成全局引用审计，已撤销任务名称无残留且 U9/C10 依赖一致；`git diff --check` 通过；`pnpm test:contracts` 97 项全部通过

### A7-11 评论动作执行

- 状态：✅ 已完成
- 日期：2026-07-20
- 提交：本任务提交
- RED：先把唯一台账置为 `🧪 RED`，再新增评论动作原调用方测试；Pytest 收集准确失败于 `automation_tool.executor.rpa.douyin.comment_action` 不存在，证明没有旧动作实现或 Fake 结果使测试假绿。测试先固定完整授权、最终文案、prepared/dispatch/verified 顺序、首次单击、receipt 和 SQLite 不落正文，再进入生产实现；后续继续补齐重放竞争、阶段化页面故障、时钟/账本故障和跨目录源码边界契约
- 输入与文案：`DouyinCommentActionIntent` 只接受 A7-03 完整 expectation、action=comment、A7-05 `ActionMessageTemplate` 和 D6-06 最小 `DouyinCandidateSummary`。纯固定文案或唯一 `{{target_display_name}}` 在本机内存展开，展开结果再次经过 500 字符、安全文本和零剩余变量校验；超长、变量递归、错动作或类型漂移固定脱敏拒绝。effect SHA-256 以 canonical ASCII JSON 和独立域绑定完整 Action/Target/Attempt/Task/Installation/Executor、平台动作、幂等键、最终文案及模板版本，SQLite 与 receipt 不保存正文
- 唯一副作用顺序：每个 execution 实例只运行一次。先由 A7-04 `ExecutorActionGate` 完成 Ed25519 expectation 验签、本机紧停、最小间隔和任务上限准入，再由 A7-07 写 `prepared`；A7-08 Page Object ready 后才取得输入并填写，填写后重新取得 ready submit，随后调用 `begin_side_effect_dispatch()`。只有 `replayed=false` 的原子赢家执行唯一一次 `comment_submit.click()`；最终 confirmation 必须等待并再次取得锚点，验证摘要绑定 effect、A7-08 selector version 和固定最终 evidence，成功后账本结算 verified
- 重放与不确定：既有 prepared 允许重新观察页面并继续竞争尚未授出的许可，但 effect 摘要变化立即拒绝；dispatched/uncertain/verified 重放在访问 DOM、填写或点击前直接返回既有事实。许可前的登录、风控、陈旧确认、ready 超时、未知路由、重复锚点、驱动或填写失败保持 `not_dispatched/prepared`；许可后的点击超时/异常、最终登录/风控/超时/漂移、最终锚点二次校验失败、时钟或验证结算失败均返回 `outcome_uncertain`，尽力一次性写 uncertain，若持久层不可用则至少保留 dispatched revision 2，绝不降级成可自动重试失败
- Receipt：`DouyinCommentActionReceipt` 只允许 `not_dispatched/verified/outcome_uncertain` 三态和阶段化固定 evidence，强类型绑定 Action/Target、账本状态/revision、重放位和版本。非法状态/evidence/账本组合、伪造重放、错误 revision/ID/version 全部拒绝；repr 与异常不回显 ID、最终文案、Token、页面、URL、路径或底层异常。该内部执行模块没有新增 Executor wire、HTTP/OpenAPI、Tauri Command、React 或 LLM/OCR 入口
- 原调用方验收：生产 `BrowserRuntime → BrowserWindow → DouyinCommentActionExecution → DouyinCommentPage` 使用一次性 0700 Profile、无头系统 Chrome 和官方-origin 隔离视频页；正式 `ExecutorActionGate` 验签、真实私有 SQLite prepared/dispatched/verified、Playwright locator fill/click 和最终锚点均从原入口执行。页面计数证明首次精确单击 1 次，同 token/intent 重放仍为 1 次且不再查询 DOM；正文只存在于页面输入值，SQLite 原始字节不含正文。Runtime 退出后完整浏览器关闭。本证据不访问真实账号、不冒充 A7-16 的真实抖音最终状态，也不把尚未存在的 App/Executor wire 标成通过
- 失败矩阵：覆盖坏 token、三类本机限制、prepare/dispatch/settle 数据库失败、不同文案重放、prepared 恢复、verified/uncertain 重放、dispatch 两路竞争唯一赢家、fill 超时/异常和填后 DOM 漂移、点击超时/异常、ready/final 登录/风控/超时/未知版本/重复锚点/驱动故障、陈旧确认、最终锚点消失、UTC naive/非零 offset/坏 timezone/时钟异常，以及 receipt/intent/构造/二次运行篡改；所有许可前分支零点击，所有许可后未确认分支不重放
- 门禁：A7-11 聚焦 `34 passed`，新 `comment_action.py` 225 条语句/40 个分支覆盖率 100%；Backend 标准全量 `1697 passed, 5 skipped`，10962 条语句/2366 个分支覆盖率 100%，279 个 Python 文件格式、Ruff、严格 Mypy 279 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 合并 M-01 后 98 项 Node 契约、186 项 Vitest、5 项无头 Playwright、冻结安装、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿
- 资源与文档：运行浏览器前确认 1420 端口空闲；所有 BrowserRuntime/Playwright、Vite、pytest、Uvicorn 与项目隔离 PostgreSQL fixture 已退出，复查无无头 Chrome、Playwright driver、监听端口或 `automation-tool-pytest-*` 容器残留，两个本轮 coverage 缓存已精确删除。未启动可见 App、未接触默认浏览器 Profile、系统钥匙串、真实平台账号或其他项目资源。同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增重复规划文档
- 后续：进入 `A7-12` 私信动作执行；复用同一授权/账本/receipt 原则和 A7-09 权限差异，D6-16/B5-15/A7-16/A7-17 真实账号证据继续独立保留，不阻塞离线主线

### A7-12 私信动作执行

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本任务提交
- RED：先把唯一台账置为 `🧪 RED`，再从原调用面导入尚不存在的 `automation_tool.executor.rpa.douyin.direct_message_action`；Pytest 收集准确失败于 `ModuleNotFoundError`，证明没有沿用 Page Object 演示或 Fake 结果冒充私信执行。随后先固定版本契约，再补齐完整授权、prepared 恢复、会话入口、唯一 send dispatch、最终验证、权限差异、重放和故障矩阵
- 输入与摘要：`DouyinDirectMessageActionIntent` 只接受 action=direct_message 的完整 A7-03 expectation、A7-05 `ActionMessageTemplate` 和 D6-06 最小目标摘要。固定文案或唯一目标显示名变量只在内存展开，展开后再次执行长度、安全文本与零剩余变量校验；effect SHA-256 用独立 direct-message 域和 canonical ASCII JSON 绑定完整授权 scope、最终文案与模板版本，SQLite/receipt/repr/异常均不保存或回显私信正文
- 会话与唯一发送：每个 execution 实例只运行一次。A7-04 完整验签和本机紧停/最小间隔/任务上限先于 A7-07 `prepared`；随后既可从 `profile_ready` 单击一次入口并等待 `conversation_ready`，也可从已打开会话直接恢复。入口点击不取得发送许可；输入最终文案并重新取得 send locator 后，只有 `begin_side_effect_dispatch()` 返回 `replayed=false` 的原子赢家可执行唯一一次 `message_send.click()`。final confirmation 再次取得后才写域隔离验证摘要并结算 verified
- 权限与恢复：发送前的“暂时无法私信”和“关注后才能私信”分别返回 `ready_messaging_not_allowed/ready_follow_required`，入口超时/异常、会话未出现、登录、风控、陈旧确认、未知版本、锚点冲突、填写或账本许可失败均保持 `not_dispatched/prepared`，可从当前会话恢复但不改变 effect。发送后的两类权限分别返回 `final_messaging_not_allowed/final_follow_required` 并结算 uncertain；点击超时/异常、确认缺失/漂移、时钟或验证持久化失败同样不得转回可重试失败
- 重放与 Receipt：prepared 重放可重新观察页面并竞争尚未授出的唯一许可，文案变化因 effect 摘要不一致而拒绝；dispatched/uncertain/verified 重放在任何 DOM 查询、入口、填写或发送前直接投影既有事实。`DouyinDirectMessageActionReceipt` 仅允许 `not_dispatched/verified/outcome_uncertain` 与阶段化封闭 evidence、强类型 Action/Target、账本状态/revision 和重放位的合法组合；非法 ID/type/state/evidence/revision/version 或 execution 二次运行全部脱敏拒绝
- 原调用方验收：生产 `BrowserRuntime → BrowserWindow → DouyinDirectMessageActionExecution → DouyinDirectMessagePage` 使用一次性 0700 Profile、无头系统 Chrome、官方-origin 隔离用户页、正式 gate 和真实私有 SQLite。真实 locator 完成 entry→input→send→final，页面计数证明首次 entry/send 各 1 次，同 token/intent verified 重放仍各为 1；Runtime 退出后浏览器完整关闭。本任务没有 App/API/Executor wire，因此原调用方是 Python RPA 执行层；没有访问真实账号，也不冒充 A7-17 的真实抖音最终状态
- 失败矩阵：41 项单元场景覆盖坏 token、三类本机限制、prepare/dispatch/settle 故障、不同文案重放、prepared 从会话恢复、verified/uncertain 重放、dispatch 竞争输家、入口超时/异常/未进入会话、两类权限在入口前/后和发送后变化、fill 超时/异常与填后漂移、send 超时/异常、ready/final 登录/风控/超时/未知版本/重复锚点/驱动故障、陈旧确认、最终锚点失效、UTC 时钟异常以及 intent/execution/receipt 篡改；许可前 send 零点击，许可后未知结果永不重放
- 门禁：A7-12 聚焦 `44 passed`（含既有 A7-09 浏览器语料复验），新 `direct_message_action.py` 254 条语句/52 个分支覆盖率 100%；Backend 标准全量 `1739 passed, 5 skipped`，11216 条语句/2418 个分支覆盖率 100%，305 个 Python 文件格式、Ruff、严格 Mypy 282 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 99 项 Node 契约、186 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿
- 资源与文档：浏览器/UI 验收均使用 headless；运行前确认 1420 空闲，运行后确认 1420 无监听且无项目 Playwright、无头 Chrome、Vite、pytest 或 Uvicorn 进程残留。未启动可见 App、未接触默认浏览器 Profile、系统钥匙串、真实平台账号或其他项目资源。同步根/Backend README、后端架构、工程结构与本唯一台账，没有新增重复规划文档
- 后续：进入 `A7-13`，把两个动作仍为 dispatched 的崩溃窗口统一收敛为先查询最终页面事实、无法证明则结算 uncertain 且绝不重放；既有 uncertain 继续保持终态，D6-16/B5-15/A7-16/A7-17 真实账号证据独立保留

### A7-13 结果不确定处理

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本任务提交
- RED 与范围校正：台账先置为 `🧪 RED`，新增测试从原调用面导入不存在的 `automation_tool.executor.rpa.douyin.side_effect_recovery`，Pytest 准确收集失败。随后核对 A7-07 状态机与产品规则，确认本任务只自动核对仍为 revision 2 `dispatched` 的崩溃窗口；既有 uncertain 是明确终态，不能因后台查询偷偷翻回成功，后续只能由明确人工结算能力处理
- 恢复契约：`DouyinSideEffectRecovery` 每实例只运行一次且输入仅为强类型 Action ID；先从真实 `ExecutorLedger` 取得完整 Action/Target/Attempt/Task/Installation/Executor/action/effect 绑定。不存在、错类型、账本错误或构造漂移统一脱敏拒绝；prepared 返回 `not_dispatched/prepared_not_dispatched`，verified/uncertain 返回相应 terminal replay，三者均在任何 DOM 查询前结束
- 只读核对：只有 dispatched 按持久 action 选择 A7-08 `DouyinCommentPage` 或 A7-09 `DouyinDirectMessagePage`，执行唯一有界 final wait 和最终锚点二次取得。恢复模块源码没有 click/fill/press、会话入口、评论/私信输入或发送、导航、selector、官方 URL、Cookie/storage、HTTP、OCR、LLM，也不接收正文；H8-05 仅在仍有可验证页面上下文时传入，supervisor 已清理进程树且上下文不存在时明确按 page unavailable 收敛 uncertain，绝不猜测 URL 或重开动作页
- 结算与竞态：评论/私信即时执行各自导出原验证摘要函数，A7-13 对相同 effect 使用相同 action domain、Page Object selector version 与 final evidence，证据充分才调用既有 `verify_side_effect()`；登录、风控、两类私信权限、final 超时、未知路由、锚点冲突、页面/最终复验/时钟/验证错误转 `mark_side_effect_uncertain()`。结算异常时重新读取账本：接受并发赢家的 verified/uncertain；仍不可读或未落盘则结构化 receipt 保留 dispatched revision 2，绝不伪报持久化成功
- Receipt：`DouyinSideEffectRecoveryReceipt` 只允许 `not_dispatched/verified/outcome_uncertain` 与 prepared、新/既有 verified、新/既有 uncertain 的合法 evidence/state/revision/replayed 组合，绑定强类型 Action/Target/action 与固定恢复版本；ID、状态、evidence、revision、重放位、action 或版本篡改全部拒绝，repr 不回显资源 ID、页面、URL、路径、摘要或底层异常
- 原调用方验收：生产 `BrowserRuntime → BrowserWindow → DouyinSideEffectRecovery → A7-08/A7-09 Page Object` 使用一次性 0700 Profile、无头系统 Chrome、官方-origin 隔离评论/私信页和同一真实私有 SQLite 中两条 dispatched 事实。测试只预置最终 confirmation 后从恢复入口结算，两条事实均 verified；页面计数证明评论 submit=0、私信 entry=0、send=0，两个 textarea 均为空，terminal replay 仍零动作。Runtime 退出后浏览器完整关闭；当前无 App/API/Executor wire，H8-05 才装配启动恢复，本证据也不替代 A7-16/A7-17 真实平台验收
- 失败与并发矩阵：38 项恢复单元场景覆盖评论/私信 final 成功，prepared/verified/uncertain 零 DOM，ready/profile/conversation 超时，登录、风控、两类权限、未知版本、缺失/冲突锚点、locator/等待/最终复验错误，验证/uncertain 结算失败、坏 UTC 时钟、当前事实读失败/消失、同向双结算重放，以及 verified 与 uncertain opposite terminal 竞态；A7-11/A7-12 动作矩阵同步回归，公开验证摘要重构不改变即时执行结果
- 门禁：聚焦 A7-11/A7-12/A7-13 单元与生产浏览器 `111 passed`，三个实现文件合计 671 条语句/144 个分支覆盖率 100%；Backend 标准全量 `1778 passed, 5 skipped`，11408 条语句/2470 个分支覆盖率 100%，308 个 Python 文件格式、Ruff、严格 Mypy 285 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 100 项 Node 契约、186 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿
- 资源与文档：运行 UI 前确认 1420 空闲，所有 BrowserRuntime/Playwright、Vite、pytest、Uvicorn 与端口在测试后零残留；未启动可见 App、未接触默认浏览器 Profile、系统钥匙串、真实账号或其他项目资源。同步根/Backend README、后端架构、工程结构与本唯一台账，不新增重复规划文档
- 后续：进入 `A7-14`，用持久连续失败事实在阈值到达时阻止新动作、打开人工接管并保持可审计恢复；D6-16/B5-15/A7-16/A7-17 真实账号证据继续独立保留

### A7-14 连续失败熔断

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、PostgreSQL 动作结果/circuit、事件收敛与授权门禁、迁移/测试和文档属于单一 `feat: 完成连续失败熔断` 提交；完成后立即推送 `main`
- RED 与权威边界：先把唯一台账置为 `🧪 RED`；首个聚焦测试在收集阶段准确失败于 `action_failure_circuits` 无法从正式数据库包导入，证明 A7-01 阈值只停留在授权快照、尚无计数或熔断事实。随后核对 A7-02 与正式 Task event，确定 Control Plane PostgreSQL 是连续失败和 handoff 的唯一权威；没有在 Executor、本机 SQLite、App 或浏览器层复制阈值
- 持久事实：迁移 `20260721_0023` 新增不可变 `action_risk_results` 和当前 `action_failure_circuits`。每个确定成功/失败结果保存 Action ID、授权 scope、授权快照阈值、结果后连续失败数、circuit 是否打开及是否为首次 handoff 触发者；current circuit 以 Installation/平台/动作作主键，保存 revision、最后结果和首次打开结果。result→ActionAuthorization、circuit→最后/打开 result 均用复合外键绑定相同 Installation/平台/动作，跨 scope 拼接由数据库拒绝
- 原子计数与接管：正式 `step.completed/step.failed` 仍从 `TaskEventConvergenceService` 进入；仓储先按与授权相同的顺序锁 Installation，再锁 Task/Attempt/Action，在一个事务中完成动作终态、结果审计、streak/circuit、Task event 和权威快照。未打开时成功清零、失败递增；首次达到当前 ActionAuthorization 持久阈值时把当前 Task/Attempt 改为 `awaiting_human`，事件投影为既有 `task.awaiting_human`。精确事件重放在任何计数前返回，不能重复累加或再开 handoff
- 停止新动作：A7-02 授权仓储在同一 Installation 行锁下，先允许完全一致的既有 Action ID 返回原事实，再检查对应 scope circuit；open 时所有新 Action ID 固定返回 `consecutive_failure_circuit`，且早于 Task 状态、间隔和额度判断。这样既保留审计/幂等读取，又不会因重放语义重新放行动作；A7-04 本机硬下限和紧停继续独立生效，服务器 circuit 不能放宽本机限制
- 安全恢复：circuit 打开后，其他已授权 Task 的晚到成功只写成功结果且保持 circuit，不能用偶然成功自动解除人工接管；另一个 Task 的 resume 也不能代清。只有打开该 circuit 的 Task 经过现有已 ACK `task.resumed` 控制链、服务端时间不回退并在同一事务完成状态恢复时，才能把 streak 清零、关闭 circuit 和递增 revision。非 circuit handoff 的 resume 不创建空风险状态
- 原调用方验收：当前能力没有 App HTTP/Tauri/React API，原始调用方是认证 Local Executor WebSocket。真实 PostgreSQL、完整 Alembic、正式 `create_app`、`ExecutorConnectionService`、短期 `executor.connect` Session 和 `/api/v1/executors/connect` 完成正式子协议/Hello；随后从该 socket 发送绑定已授权 Action 的 `step.failed`，再以 heartbeat 证明前序已处理。最终数据库精确得到一条失败结果、open circuit、`task.awaiting_human`，同 scope 下一 Target 的新授权被 circuit 原因拒绝；测试没有以直接 HTTP 或 App Mock 冒充该入口
- 失败与并发矩阵：覆盖三连失败阈值、成功清零、open 后跨 Task 晚到成功、精确授权/事件重放、两 Task 并发失败串行且仅一个触发者、结果重复、circuit 时钟回退、计数结构上界、错误 Task 恢复、owner 恢复时钟回退、非 circuit handoff、Installation 缺失、隐藏 sequence 唯一冲突、数据库不可用、迁移 exact schema/约束/升降级和原有事件状态矩阵。任何失败均不留下半条结果、半次计数或伪 handoff
- 门禁：A7-14 风险/事件/迁移聚焦 `35 passed`，认证 WebSocket 单项独立通过且修正测试退出时无关 Outbox 轮询造成的连接取消警告；Backend 全量 `1790 passed, 5 skipped in 136.50s`，11479 条语句/2502 个分支覆盖率 100%，310 个 Python 文件格式、Ruff、严格 Mypy 286 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿。Frontend 101 项 Node 契约、186 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿
- 资源与文档：数据库测试只使用 `automation-tool-pytest-*` 专属 Compose project、随机 loopback PostgreSQL 端口、独立容器/网络/Volume，并由 fixture 回收；WebSocket 验收使用进程内正式 ASGI 路由且关闭无关出站 Command 轮询，结束后连接池无未归还警告。没有启动 Tauri、可见浏览器、用户 Profile、真实账号或系统钥匙串；Frontend UI 保持 headless。同步根/Backend README、后端架构、工程结构和本唯一台账，没有新增重复规划文档
- 后续：进入 `A7-15`，把既有 Action/Task event 与 A7-13 receipt 收敛为目标级成功、跳过、失败、不确定和受限证据摘要 UI；先复用 T3-18 运行详情，不新增第二个任务详情页

### A7-15 目标级结果 UI

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、闭合证据协议、PostgreSQL 结果投影、App API/Rust 桥/现有运行详情 UI、迁移、原调用方验收和文档属于单一 `feat: 完成目标级结果界面` 提交；完成后立即推送 `main`
- RED 与唯一事实源：先把台账置为 `🧪 RED`，从既有 A7-11/A7-12 receipt、A7-13 恢复结果、正式 Executor Task event 和 T3-18 运行详情反推边界。目标结果不是 React 本地推断、Executor SQLite 直读或第二套详情模型；Control Plane PostgreSQL 中当前 Task/Target/Action 与终态 evidence 是唯一权威，App 只读展示
- 证据协议与收敛：新增 `action-result-evidence.v1` 封闭词汇，把评论/私信即时 receipt 和崩溃恢复 receipt 映射成带 `evidence/evidence_version` 的正式 `step.completed`、`step.failed` 或 `task.outcome_uncertain` payload。服务端逐字段校验版本、动作终态与 evidence 集合的一致性，再由原有认证 Executor WebSocket 收敛；未知、跨结果或半套证据全部拒绝。迁移 `20260721_0024` 为 `task_actions` 增加最小 `evidence_code`，回填旧终态并用数据库约束锁定 success/failed/cancelled/uncertain 的封闭组合，不保存正文、页面原文、Cookie、Profile、URL 或路径
- 目标级投影：`GET /api/v1/tasks/{task_id}/target-results` 只接受当前 `app.control-plane` Session 和 Installation scope，按目标 ordinal 返回公开显示名、公开号、目标状态、封闭 evidence、可选 Action ID 与 UTC 更新时间。仓储在 PostgreSQL 中把用户排除、任务内/历史重复、黑名单、等待授权、已发送、取消、确定成功/失败和结果不确定统一投影；只关联当前 Attempt 的授权/Action，非法或跨 Installation Task 与不存在 Task 同样不可见，数据库或持久证据异常固定 503/422 且不泄露内部信息
- App 复用：生成 OpenAPI DTO 后新增严格 TypeScript parser/source、固定 Rust `getTaskTargetResults` operation/Tauri Command 与既有依赖注入链；没有任意 URL、任意 command 或第二个 Web 页面。T3-18 的 `TaskRunDetails` 在原页面展示成功、跳过、失败、不确定以及待执行/进行中，证据只翻译成固定中文摘要；查询 loading/empty/failure 独立且可重试，Task SSE 或控制成功时失效并重取权威结果，页面不展示消息正文、平台页面内容或敏感本机事实
- 原调用方验收：扩展 T3-18 隔离 runner，在启动前检查随机端口并使用专属 Compose project、PostgreSQL、AppData、设备凭据和隐藏配置；唯一 `visible=false` Tauri App 从现有运行详情真实发出 TypeScript source → Tauri IPC → Rust Session 网络桥 → Uvicorn/FastAPI → PostgreSQL 请求。正式 Executor 事件与预置目标共同形成成功、跳过、失败和不确定结果，WebdriverIO 从真实 App DOM 核对固定证据文案与既有暂停/恢复/取消/紧停控件；没有用 Mock、直接 HTTP 或可见浏览器冒充 App 验收
- 失败与一致性矩阵：覆盖 evidence 版本/类型/集合错配、无 Action 携带证据、终态回放、success/failed/uncertain 收敛、所有 comment/direct-message receipt 分支、恢复映射、目标排序/重复/身份/时间/摘要异常、所有投影状态、旧数据 fallback、Installation 隔离、仓储/服务/API 错误脱敏、迁移升降级与数据库直写约束。前端覆盖 DTO 额外字段/时间/枚举/资源上限、取消信号、Rust 响应严格解析、UI loading/empty/error/retry 与四类终态
- 门禁：Backend 全量 `1869 passed, 5 skipped in 141.46s`，11864 条语句/2600 个分支覆盖率 100%，322 个 Python 文件格式、Ruff、严格 Mypy 297 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿。Frontend 102 项 Node 契约、195 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿；T3-18 隐藏真实 App 纵向验收通过
- 资源与文档：验收结束后确认专属 AppData、1420/随机 Control Plane/PostgreSQL 端口、Tauri App、Uvicorn、WebdriverIO、Chrome/Playwright、Compose 容器/网络/Volume 均零残留；全程不打开可见 App/浏览器、不触碰用户默认 Profile、真实账号、系统钥匙串或其他项目资源。同步根/Backend README、后端架构、工程结构与本唯一台账，没有新增重复规划文档
- 后续：`A7-16/A7-17` 必须在用户明确指定的自有/授权目标上完成真实评论/私信证据，当前保持 `🔍 待真实账号`；`A7-18` 受其依赖阻塞。按用户“真实账号任务不可用时先跳过”的约定，先进入依赖已满足的 `H8-01` 端到端暂停

### H8-01 端到端暂停

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、正式 Executor 控制处理、安全检查点、隐藏 App 原调用方验收和文档属于单一 `feat: 完成端到端安全暂停` 提交；完成后立即推送 `main`
- RED 与边界：先把唯一台账置为 `🧪 RED`，新增正式 Processor/SQLite 用例后 3 项准确失败于 `task.pause/task.resume` 被拒绝，证明既有 T3-13 只有 HOLD FakeExecutor 的协议投影，尚未约束真实平台动作检查点。H8-01 不改变 App 控制 API、PostgreSQL 状态机、A7-07 副作用状态或 SQLite schema，只把安全暂停语义补到正式 Local Executor
- 持久控制：`ExecutorLedger.receive_command()` 只允许 pause/resume 附着到已有 Attempt，pause 要求 running、resume 要求 paused；command 先落入既有 `executor_commands` 并推进 checkpoint 的 command sequence/revision。Processor 立即持久化封闭 `task.control_ack`，但 ACK 仅表示已接收，不表示外部副作用已经停止
- 安全检查点：pause 一旦成为 Attempt 最新持久命令，`begin_side_effect_dispatch()` 会在同一 `BEGIN IMMEDIATE` 边界拒绝任何新的 prepared→dispatched；已是 dispatched 的动作保持真实状态，不能回滚或伪装撤销。`pending_task_controls()` 只有在该 Attempt 已无 dispatched 时才暴露 pause，因而 ACK 可先到、`task.paused` 必须等原子动作完成或按 A7-13 结算
- 原子投影与恢复：`complete_task_control()` 在一个 SQLite 事务内要求既有 ACK，复验 Installation/Executor、Task/Attempt、correlation、command/event sequence、checkpoint revision/state，并一起提交本机 paused/running checkpoint 与 `task.paused/task.resumed` Outbox；精确重放返回既有事实。`ExecutorProcessRuntime` 在 Outbox 恢复后及每轮空闲时推进待生效控制，所以 dispatched 结算后不需要服务端重发 pause，resume 原子完成后才重新开放 dispatch
- 原调用方验收：`scripts/run_h8_01_acceptance.py` 使用项目专属 Compose project、随机 PostgreSQL 端口、独立 AppData/Executor state、真实 Alembic/Uvicorn 和唯一 `visible=false` Tauri App。HOLD FakeExecutor 只消费一次 offer 以建立权威 running 事实；随后正式 `python -m automation_tool.executor` 经真实 `executor.connect` Session/WebSocket 消费 App 发出的 pause/resume。本机账本预置一条 dispatched 和一条 prepared，验收证明 ACK 后服务端仍 running、第二条 dispatch 被拒绝、首条验证后 runtime 自动上报 paused，最终 App/PostgreSQL/SQLite 全部恢复 running
- 失败、重放与损坏矩阵：覆盖安全点立即暂停、已有 dispatched 延迟暂停、暂停后阻止新 dispatch、恢复后才重新开放、非法状态/控制类型不污染 Attempt、进程级 WebSocket 暂停恢复、命令/事件精确重放、无 ACK、错 identity/correlation/sequence/revision/state、未知命令、损坏 envelope、非法 limit、并发投影赢家和非 ACK Outbox。所有失败统一脱敏拒绝，不保存 wire 凭据或平台内容
- 门禁：H8-01 聚焦 Processor/Ledger/真实进程测试通过；Backend 干净全量 `1877 passed, 5 skipped in 141.89s`，11960 条语句/2634 个分支覆盖率 100%，323 个 Python 文件格式、Ruff、严格 Mypy 298 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿。Frontend 103 项 Node 契约、195 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿；H8-01 隐藏真实 App 纵向验收通过
- 资源与隐私：隐藏 App 全程不显示、不抢焦点；验收未启动运营浏览器或访问真实账号，私有 SQLite 只在 App sandbox 路径保存摘要状态，明文本机会话票据、Executor Session 和设备凭据均未落账。runner 结束后回收 App、Executor、Uvicorn、WebdriverIO、端口、Compose 容器/网络/Volume 和私有目录，不触碰系统钥匙串、默认浏览器 Profile 或其他项目资源
- 后续：进入 `H8-02`，把正式 Executor 的取消从 ACK 扩展为 CANCELLING 后的协作式终止；如果最后平台动作已经 dispatch 且无法证明结果，必须收敛 outcome uncertain，不能伪报 cancelled

### H8-02 端到端取消

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、正式 Executor 普通取消、安全派发门、最后动作不明收敛、隐藏 App 原调用方验收和文档属于单一 `feat: 完成端到端协作式取消` 提交；完成后立即推送 `main`
- RED 与范围：先把唯一台账置为 `🧪 RED`，3 项正式 Processor/SQLite 用例准确失败于 `task.cancel` 被拒绝，证明 T3-14 只有 Control Plane/FakeExecutor 协议投影，尚未约束真实 Local Executor。H8-02 不改 App API、Rust 生产桥、PostgreSQL 状态机、A7-07 副作用状态或 SQLite schema；`task.emergency_stop` 继续由 H8-03 负责并在当前正式 Processor fail closed
- 持久取消与派发门：cancel 只能附着到已有 running/paused checkpoint，command 和 `task.control_ack` 先持久化；最新 cancel 一经落账，`begin_side_effect_dispatch()` 与 pause 共用同一个 `BEGIN IMMEDIATE` 门，拒绝所有新 prepared→dispatched。ACK 只说明 Executor 已接收，服务端继续保持 CANCELLING，不能提前显示取消成功
- 安全终态：Attempt 没有 dispatched 且没有 uncertain 时，checkpoint 与 `task.cancelled` Outbox 原子提交为 terminal；已有 dispatched 必须先由原动作/恢复边界结算，verified 后才允许 cancelled，uncertain 后只能提交 outcome_uncertain checkpoint 与 `task.outcome_uncertain`。prepared 动作保持不可派发；terminal/outcome_uncertain 也在派发事务内永久封门。终态类型在提交事务中根据最新副作用事实重算，错误类型、陈旧 revision、错身份/correlation/sequence、无 ACK 和损坏 replay 全部脱敏拒绝
- 运行与恢复：既有 runtime 在恢复 Outbox 后和每轮空闲继续调用 `poll_controls()`，因此 dispatched 结算后不需要 Control Plane 重发 cancel。精确 command 重放返回首次 ACK/终态；直接 replay 也必须与已持久事件的 Task/Attempt/correlation/type/sequence 相容。safe cancel、paused cancel、verified→cancelled、uncertain→outcome_uncertain、取消后零新增 dispatch 和正式 WebSocket 进程路径均已覆盖
- 原调用方验收：`scripts/run_h8_02_acceptance.py` 使用专属 Compose project、随机 PostgreSQL 端口、独立 AppData/Executor state、完整 Alembic、真实 Uvicorn 和唯一 `visible=false` Tauri task-termination App。HOLD FakeExecutor 只消费 offer 建立服务端 running；正式 `python -m automation_tool.executor` 经真实 `executor.connect` Session/WebSocket 消费 App 发出的 cancel。本机预置一条 dispatched 与一条 prepared，验收证明 ACK 后 Task 仍 CANCELLING、第二条 dispatch 被拒绝、首条结算 uncertain 后 App/PostgreSQL/SQLite 全部进入 outcome uncertain；同一 App 的 emergency-stop 半程由既有 FakeExecutor 收尾，不冒充 H8-03
- 失败发现与修正：首次纵向验收由生产 D6-13 offer 守卫准确拒绝旧 T3-14 无目标确认 fixture；没有绕过守卫，而是把正式目标确认准备链下沉到 T3-14 公共 runner，并由 T3-18、H8-01 和 H8-02 复用，再把 App 预期 revision 调整为真实值。原 T3-14、扩展后的 T3-18 与 H8-02 隐藏 App 均已重跑通过，未用专用绕行破坏历史验收。Backend 100% 覆盖率首轮还准确指出新增 replay 防御分支未命中；补充错误 replay 事件拒绝断言后定向确认该分支，未增加 pragma 或降低门禁
- 门禁：H8-02 Processor/Ledger/真实进程聚焦 `53 passed, 1 skipped`；Backend 最终全量 `1884 passed, 5 skipped`，11985 条语句/2648 个分支覆盖率 100%，325 个 Python 文件格式、Ruff、严格 Mypy 299 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿。Frontend 104 项 Node 契约、195 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿；H8-02 隐藏真实 App 纵向验收通过
- 资源与隐私：验收启动前检查固定端口，使用 `automation-tool-h802-*` 专属 Compose project 和随机数据库端口，没有复用或停止正在运行的 `agent-platform` 容器。App 全程隐藏且未启动运营浏览器；runner 结束后 App、Executor、Uvicorn、WebdriverIO、1420/8765、专属容器/网络/Volume 与 AppData 全部回收。本机账本不含设备凭据、Local/Executor Session，不使用系统钥匙串、默认浏览器 Profile 或真实账号
- 后续：进入 `H8-03`，把正式 `task.emergency_stop` 接到不依赖网络的本机持久 latch、完整 Executor/浏览器进程树停止与重连补报；不得复用普通 cancel 的“等待安全点”语义冒充硬停止

### H8-03 离线紧急停止

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、本机持久紧停、完整进程树硬停、正式 Executor 补报、隐藏 App 断网原调用方验收和文档属于单一 `feat: 完成离线紧急停止闭环` 提交；完成后立即推送 `main`
- RED 与语义边界：先把唯一台账置为 `🧪 RED`，正式 Processor、SQLite、Rust manager/platform、React 投影与隐藏 App 契约准确失败于 `task.emergency_stop` 未被 Local Executor 接受、App 只会先走网络、离线失败后权威快照被 error 覆盖以及没有持久恢复意图。H8-03 保留普通 cancel 的协作式安全点语义，emergency stop 独立采用“先本机、后网络”的硬停语义；不把二者合并，也不新增 WebView 路径、账号或系统钥匙串依赖
- 本机先行硬停：Tauri 在任何 HTTP 前把 version/task/idempotency 的最小意图原子写入 AppData `local-executor/task-emergency-stop-v1`，随后调用 manager 的跨平台完整进程树 emergency stop；Unix 直接杀固定 process group，Windows 终止既有 Job Object，不等待 Python 优雅超时。重复同一意图幂等，冲突意图 fail closed；存在 marker 时普通 restart 被拒绝，App 重启可重新读取意图
- SQLite 原子收敛：报告型 Executor bootstrap 在建立网络前先持久 engage 既有 `executor_action_guard`；`receive_task_emergency_stop()` 在一个 `BEGIN IMMEDIATE` 内写 command、封闭所有新 dispatch、把本 Attempt 已 dispatched 且时间可归属的副作用结算为 uncertain，再由既有控制事务一起生成 `task.control_ack`、`task.outcome_uncertain`、checkpoint revision/sequence。prepared 保持 prepared，凭据、Cookie、DOM、正文、URL 和 Session 均不入账
- 重连补报与并发线性化：App 的 workbench/detail/list 原生产轮询都会尝试恢复；恢复 claim 先原子持有互斥门再读取 pending，避免旧快照在 marker 清理后把同一紧停复活。服务端幂等 emergency command 成功后只签发一个 `executor.connect` Session，Rust 以 `local_emergency_stop=true` 启动报告型 Executor；无论命令已在 SQLite 恢复还是首次从 WebSocket 到达，生命周期都严格先 `executor.healthy` 再 `executor.stopped`，manager 成功接管后才删除 marker
- App 权威状态：Task 详情查询以 1 秒后台轮询权威快照；后台 refetch 暂时失败时保留最后一个已成功 running 快照和紧停入口，不把离线错误替换成空页面。用户在真实详情页点击“紧急停止/确认紧停”后，即使 HTTP 不可达也能看到“命令结果暂时无法确认”，服务恢复后同一页面自动收敛为“结果待确认”
- 原调用方验收：`scripts/run_h8_03_acceptance.py` 构建并签名真实 PyInstaller Executor，安装到独立 T318 AppData，启动 `automation-tool-h803-*` 专属 PostgreSQL/完整 Alembic/真实 Uvicorn 与唯一 `visible=false` Tauri App。HOLD fixture 只建立服务端 running；runner 在 App 固定 SQLite 预置一条 dispatched 与一条 prepared，再由页面启动签名 Executor。服务端完全停止后页面真实点击紧停，验证 marker 先落盘且签名进程树已消失；服务恢复后 App 自动补发，最终 PostgreSQL command/event/Task/Attempt 与 SQLite latch/checkpoint/outbox/副作用精确收敛，`executor.connect` 总数严格为 fixture、正常 Executor、报告 Executor 三次且 marker 被清除
- 失败发现与修正：纵向验收先发现多轮轮询“先读 pending、后抢锁”会重复恢复，改为 claim 同时拥有快照与门闩；随后发现报告进程快速退出时 daemon 线程争抢 buffered stdin 导致 Python fatal，正式入口改为从一开始使用原始无缓冲二进制描述符；最后发现首次在线收到 emergency 只发 stopped、Rust 健康握手失败，补齐在线/恢复两条路径统一 healthy→stopped。所有问题均先由 RED/严格 Session 计数或真实诊断复现，再修实现；没有放宽断言或降低重试标准
- 失败与覆盖矩阵：覆盖本机写入失败、marker 损坏/冲突/重启恢复、进程不存在/运行中/进程树子孙、网络先断/恢复、同一命令重放、恢复 claim 竞争、普通 restart 封门、错 task/idempotency、错误控制入口、未来 dispatched 时间导致整笔回滚、理论锁丢失、SQLite 存储失败、在线首次命令/已持久命令两条报告生命周期，以及 React background refetch 保留。异常只返回固定错误码，不回显 marker、凭据或私有路径
- 门禁：H8-03 最终 Backend 全量 `1893 passed, 5 skipped`，12037 条语句/2674 个分支覆盖率 100%，324 个 Python 文件格式、Ruff、严格 Mypy 299 个源文件、uv lock、OpenAPI 与 Executor Schema 全绿。Frontend 105 项 Node 契约、197 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、peer 依赖、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿；隐藏真实 App 离线纵向验收 1/1 通过
- 资源与隐私：所有验收启动前检查固定 8765 和随机 PostgreSQL 端口，使用专属 Compose project、网络、Volume、AppData 和 SQLite；App 全程隐藏，不启动运营浏览器、不访问真实账号。每次退出均精确回收 App process group、Executor、Uvicorn、WebdriverIO、Chromium、端口、容器/网络/Volume 与私有目录；最终再次确认零监听、零 H8-03 容器、零 App/Executor 进程，不触碰默认 Profile、系统钥匙串或其他项目资源
- 后续：进入 `H8-04`，验证 App 在任务运行中崩溃/被杀后从服务端权威快照和本机状态恢复 UI，且不会重复启动任务、重复发控制命令或重复平台副作用

### H8-04 App 崩溃恢复

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、H8-04 独立隐藏 App 配置、两阶段 WDIO 场景、feature-gated 验收准备/PID 探针、严格跨进程 runner 与文档属于单一 `feat: 完成 App 崩溃恢复验收` 提交；完成后立即推送 `main`
- RED 与边界：先把唯一台账置为 `🧪 RED`，新增工程契约准确失败于 H8-04 专用 Tauri 配置、WDIO 场景和 runner 不存在。T3-20 只证明 Control Plane 重启期间 App 保持运行，不能冒充 App 本身崩溃；H8-04 必须真实销毁第一个 App 进程并启动第二个 App。生产业务状态仍只属于 PostgreSQL/Executor，本任务不新增 App 内任务副本、恢复状态机、Web 页面、账号、平台动作或系统钥匙串依赖
- 原调用方与硬崩溃：`scripts/run_h8_04_acceptance.py` 构建一次 H8-04 独立 `visible=false` Tauri 二进制和真实签名 PyInstaller Executor，使用 `automation-tool-h804-*` 专属 PostgreSQL/完整 Alembic/真实 Uvicorn/AppData/SQLite。第一个 App 从真实页面选择评论、填写封闭模板并创建 Task，经正式 Rust Session、HTTP/PostgreSQL、认证 WebSocket 建立 running；页面再从正式 `restart_executor` IPC 启动唯一签名 Executor。runner 只接受 feature-gated App 自报 PID，经系统进程名复核后仅对该 App 发 `SIGKILL`，不杀 WDIO process group 或 Executor
- Executor 与 UI 连续性：App 被硬杀后，签名 Executor 保持唯一进程并继续向服务端发送在线心跳，Task/Attempt 保持 running。第二个 App 不调用注册/准备/创建/控制或 Executor restart，只复用同一 AppData 身份与长期凭据，通过正式工作台查询恢复 `Executor 在线`、原 Task ID 和 `运行中`，再从原任务按钮进入详情读取 `任务开始/步骤开始` 权威时间线；没有用整页刷新、Mock、直接组件注入或第二个测试 Adapter 冒充重启
- 精确不重复证据：崩溃前后 PostgreSQL 严格保持 1 个 Task、1 个 Attempt、1 个 acknowledged offer、2 个原 source message ID 的 started/step-started Event、1 份页面创建定义和 0 个服务端 Action；Task/Attempt 行、revision、水位、created/started 时间、Command/Event ID、幂等键和完整公开事件事实逐字段相同。`executor.connect` 始终只有 HOLD 准备 Executor 与签名 Executor 两张 Session，第二个 App 没有重复启动 Executor。本机 SQLite 的 offer/checkpoint 以及两条平台副作用事实逐字段不变，状态始终精确为一条 `verified`、一条 `prepared`，没有重新取得 dispatch 许可
- 失败发现与修正：首次真实运行发现 UI 默认 browse 与确认夹具 comment 意图不一致，服务端按正式 offer guard 正确拒绝；页面改为真实选择 comment 并填入相同受限模板。随后三次失败分别来自 runner 误用不存在的 PostgreSQL `payload`、SQLite command `state` 列，以及把 `ps` 的“PID 已不存在”退出码 1 当异常；均按正式 schema/系统语义修正，没有改生产状态、放宽业务断言或把失败复跑冒充通过。最终运行真实完成 SIGKILL、Executor 存活、第二 App 恢复与前后双快照核对
- 门禁：Backend 全量 `1893 passed, 5 skipped`，12037 条语句/2674 个分支覆盖率 100%，325 个 Python 文件格式、Ruff、严格 Mypy 299 个源码文件、uv lock、OpenAPI 与 Executor Schema 全绿。Frontend 106 项 Node 契约、197 项 Vitest、5 项无头 Playwright、锁文件、peer dependency、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、19 项安全配置、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿；H8-04 隐藏双 App 纵向验收 1/1 通过
- 隔离、秘密与清理：固定 8765 与随机 PostgreSQL 端口均在启动前检查；独立 Compose project、网络、Volume、AppData、SQLite 和信号目录不复用其他项目。两个 App 全程隐藏，不启动运营浏览器、不访问真实账号；长期设备凭据只在 AppData 私有文件，SQLite 不含凭据。成功与每次失败均精确回收 App/WDIO/Executor/Uvicorn、端口、容器/网络/Volume 和私有目录，最终复核零 H8-04 监听、容器、App/Executor 进程与 AppData 残留
- 后续：进入 `H8-05`，从现有 E4-08 有界 supervisor、A7-13 只读副作用恢复和 H8-01/H8-02 安全 checkpoint 出发，验证真实 Executor 崩溃后的 restart budget、SQLite/outbox 对齐，以及 dispatched 未验证动作只读收敛且绝不重复点击

### H8-05 Executor 崩溃恢复

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、crash-only bootstrap、恢复协调器与 SQLite 原子投影、H8-05 独立隐藏 App 配置/WDIO/runner 和文档属于单一 `feat: 完成 Executor 崩溃恢复验收` 提交；完成后立即推送 `main`
- RED 与边界：先把唯一台账置为 `🧪 RED`；Python bootstrap 测试准确失败于 `crash_recovery` 不存在，Rust bootstrap/manager 测试要求首次 false、supervisor relaunch true；恢复测试先因 `automation_tool.executor.crash_recovery` 不存在而收集失败，隐藏 App 工程契约再因专用 Tauri 配置不存在失败。E4-08 只证明有界进程重启，A7-13 只证明给定页面时只读结算，均不能单独冒充 H8-05 的跨进程账本与服务端收敛
- 启动语义：Rust `spawn_executor` 使用既有 `restart_count` 生成 bootstrap；首次启动始终 `crash_recovery=false`，只有异常退出且仍在最多两次预算内的 supervisor relaunch 为 true，每次仍重新验包并生成新的 256-bit 本机认证令牌。正常启动、显式停止、固定退出、紧停报告和 App 重启不会误触发崩溃扫描；Python 只在 true 时于建立 WebSocket 前运行一次协调器
- 只读恢复与零重复：协调器只扫描 running/paused/outcome-uncertain Attempt 的 App 私有 SQLite Action。prepared 在任何页面调用前跳过；verified/uncertain 重放终态；dispatched 若仍有显式 `BrowserWindow` 就复用 A7-13 Page Object 只读 final 核对。生产 supervisor 会先清理原完整 Executor/浏览器进程树，而账本按隐私设计不保存 URL、页面正文或 DOM，因此没有可证明页面上下文时直接以 `page_unavailable/recovery_unconfirmed` 结算 uncertain，绝不导航、填充、点击或重新 dispatch
- 原子账本与重放：新增 crash recovery 查询只从原 `task.offer`、Attempt checkpoint、Action admission/side-effect 和 outbox 构造事实。side-effect 已先按 A7-13 结算；随后 `commit_side_effect_recovery()` 在单个 `BEGIN IMMEDIATE` 中复核 Installation/Executor/Task/Attempt/Action/correlation、state/evidence/sequence/revision，把 checkpoint 与 `step.completed` 或 `task.outcome_uncertain` outbox 一起推进。稳定 `executor:recovery:<action_id>` 幂等键使二次崩溃返回首次事件；结算后投影前若再崩溃，下次仍能从 terminal side-effect 补齐，prepared 永远不能借恢复取得 dispatch 许可
- 原调用方验收：`scripts/run_h8_05_acceptance.py` 构建并签名真实 PyInstaller Executor、独立 `visible=false` H8-05 Tauri App、`automation-tool-h805-*` PostgreSQL/完整 Alembic/真实 Uvicorn/AppData/SQLite。App 从真实表单创建 comment Task，runner 只预置一条 dispatched 和一条 prepared 以及匹配服务端 Action；页面经正式 `restart_executor` 启动包，再从正式 IPC 注入异常崩溃。页面最终显示“自动恢复次数 1”、工作台/详情“结果待确认”，进程始终只有一个签名 Executor
- 精确收敛：PostgreSQL 最终严格只有 1 Task、1 Attempt、1 acknowledged offer、1 Action 和 started/step-started/outcome-uncertain 三条连续 Event；Task/Attempt/Action 分别为 outcome uncertain，Action evidence 为 `recovery_unconfirmed`。同一 Manager 重用首次签发的 Executor Session，因此 `executor.connect` 总数精确为准备夹具 1 + 签名 Executor 1，不因 supervisor relaunch 再换票。本机 checkpoint 为 outcome_uncertain/sequence 3/revision 3，副作用恰为 uncertain revision 3 + prepared revision 1，唯一 recovery outbox 已 delivered，长期设备凭据不在 SQLite
- 失败发现与修正：首次纵向运行已经完成崩溃、重启和 UI 收敛，但 runner 误期望 supervisor 新签一张 Session；正式 Manager 实际安全复用同一短期 Session，精确计数从错误的 3 修正为设计值 2 后完整重跑通过，没有改生产行为或放宽 Task/Action/outbox 断言。全量门禁第一次误用 `.venv/bin/pytest`，测试内部 `alembic` 子进程因 PATH 缺失产生基础设施假失败；按仓库权威 `uv run pytest` 重跑全绿
- 失败与覆盖矩阵：覆盖 bootstrap 缺省/true/非严格布尔、首次/重启分离、两次 restart budget 与完整进程树清理、prepared/verified/uncertain/dispatched、评论/私信成功 evidence、无页面 fail-closed、重复恢复、坏 clock/ID/checkpoint/effect/commit、非法 limit/存储、命令/事件漂移、缺 Action、错误 evidence、并发原子 replay 和秘密脱敏；协调器与全仓语句/分支均 100%，不可达的同一锁内 revision 漂移仅保留明确 pragma
- 门禁：Backend 全量 `1907 passed, 5 skipped`，12211 条语句/2728 个分支覆盖率 100%，327 个 Python 文件格式、Ruff、严格 Mypy 301 个源码文件与 uv lock 全绿。Frontend 107 项 Node 契约、197 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、20 项安全配置、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿；H8-05 隐藏 App 纵向验收最终 1/1 通过
- 隔离与清理：固定 8765 与随机 PostgreSQL 端口启动前均检查；专属 Compose project/network/volume、App identifier/AppData、SQLite 和临时信号目录不复用其他项目。App 全程隐藏，不启动运营浏览器、不访问真实账号或默认 Profile。首次断言失败与最终成功都清理 WDIO/App/Executor/Uvicorn、端口、容器/网络/Volume/AppData，最终复核零 H8-05 残留
- 后续：进入 `H8-06`，验证 Control Plane 在 Task/Executor 运行中重启后，Executor 使用现有重连/幂等命令事件边界恢复在线并使任务收敛；不得把 T3-20 的局部 App 观察证据直接冒充完成

### H8-06 Control Plane 重启恢复

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、Executor 进程内有界重连、精确 outbox 恢复、H8-06 独立隐藏 App 配置/WDIO/runner 和文档属于单一 `feat: 完成 Control Plane 重启恢复验收` 提交；完成后立即推送 `main`
- RED 与边界：先把唯一台账置为 `🧪 RED`；Python 进程测试准确失败于已建立连接收到 `1012` 后仍抛出固定进程拒绝，隐藏 App 工程契约准确失败于 H8-06 配置、WDIO 场景和 runner 不存在。T3-20 只由 HOLD FakeExecutor 证明 App 面对同库服务重启可恢复，E4-12 只证明 Executor 整进程重启可重放 SQLite；本任务必须让同一个真实签名 Executor 进程跨越 Control Plane 停服/重启，并从真实 App 控制入口证明正式命令和事件收敛
- 生产重连语义：`LocalExecutorProcess` 只有在已经建立的 WebSocket 收到服务重启关闭码 `1012` 后进入恢复态；初次连接失败、非 1012 关闭、协议错误和应用错误保持固定失败。恢复态默认最多 120 次、每次 250ms，构造边界限制为最多 1000 次/5 秒；连接打开失败与反复 1012 都持续消耗同一预算，stop event 可立即中断等待，只有恢复连接成功收到 heartbeat 才重置预算，避免服务抖动造成无界循环
- 同进程与精确重放：重连沿用原 bootstrap、`executor.connect` Session、PID 和 Rust manager 记录，不退出进程、不触发 H8-05 supervisor，因此 `restartCount` 始终为 0。`recover_outbox()` 重排的仍是相同 envelope/message ID/source message ID/idempotency key；重复收到同一 command 返回首次持久 batch，`_send_outbox`/`_send_local_outbox` 保留 WebSocket close 分类，普通发送错误不会被误判为服务重启
- 原调用方验收：`scripts/run_h8_06_acceptance.py` 构建、签名并安装真实 PyInstaller Executor，启动独立 `visible=false` H8-06 Tauri App、`automation-tool-h806-*` PostgreSQL/完整 Alembic/真实 Uvicorn/AppData/SQLite。App 从真实表单创建 comment Task，经正式诊断入口启动签名 Executor；runner 只暂停 App 自报并经系统复核的准确 Executor PID，App 再从原详情页点击取消。服务端确认同一 cancel 已持久化为 delivered 后真实停止 Uvicorn，恢复该 Executor，再以同一 PostgreSQL 重启 Uvicorn；App 先显示不可用，随后从工作台/详情恢复取消终态
- 双账本精确事实：停服前 Task/Attempt 已进入 cancelling 且 cancel 未 ACK；重启后 PostgreSQL 只保留原 offer/cancel 两条 Command，cancel 由同一 Executor ACK，Event 精确为 started/step.started/cancelled 的 sequence 1/2/3，Task/Attempt 最终 cancelled。SQLite 只保留同一 offer/cancel，checkpoint 为 terminal/command 2/event 3/revision 4，outbox 只有 `task.control_ack` 与 `task.cancelled` 且均 delivered；数据库/SQLite 均无长期设备凭据。`executor.connect` Session 总数仍精确为 HOLD 准备夹具与签名 Executor两张，重连没有换票
- 失败发现与修正：纵向 runner 首先错误假设暂停进程时 cancel 会停在 pending，真实 TCP 已接收后服务端正确记为 delivered；断言改为等待 delivered，以覆盖更强的“服务端已发出但未 ACK”重放窗口。随后按正式事务语义把 Attempt 预期从 running 修正为 cancelling，把本机 checkpoint 状态从业务文案 cancelled 修正为账本封闭值 terminal。失败清理还发现 WDIO 父进程退出后 Tauri 子进程可能重建空 AppData，runner 改为先依据 App 自报 PID 等待/精确停止自有进程，再删除目录；这些修正均只收紧测试脚手架，没有改变或放宽生产行为
- 失败与覆盖矩阵：覆盖初次连接失败、非重启关闭、1012 后连接失败、重复 1012、预算耗尽、无效尝试/延迟配置、stop 打断退避、stop 与 outbox replay 竞争、durable/local outbox 发送时 1012、相同 command/outbox 精确重放及稳定 heartbeat 重置预算。固定失败仍由 Rust supervisor 按既有 H8-05 语义处理，服务重启恢复不冒充网络抖动；持续断网、随机抖动和事件 spool 上限继续由 H8-07 完成
- 门禁：Backend 全量 `1913 passed, 5 skipped`，12262 条语句/2750 个分支覆盖率 100%，327 个 Python 文件格式、Ruff、严格 Mypy 143 个源码文件与 uv lock 全绿。Frontend 108 项 Node 契约、197 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整串行测试、20 项安全配置、Rustfmt 和三套全目标 Clippy `-D warnings` 全绿；H8-06 隐藏真实 App 纵向验收最终 1/1 通过
- 隔离与清理：固定 8765、1420 与随机 PostgreSQL 端口启动前均检查；专属 Compose project/network/volume、App identifier/AppData、SQLite 和临时目录不复用其他项目。App 全程隐藏，不启动运营浏览器、不访问真实账号或默认 Profile。成功和所有严格断言失败都回收 WDIO/App/Executor/Uvicorn、端口、容器/网络/Volume/AppData，最终复核零 H8-06 残留
- 后续：进入 `H8-07`，在当前 1012 服务重启特例之外建立真实断网/随机抖动边界，要求 Executor 停在安全点、事件本机 spool 有界持久化、网络恢复精确续传且不会烧无限重试

### H8-07 断网/抖动

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、SQLite v6 网络闸门与 spool 上限、异常网络有界恢复、H8-07 独立隐藏 App 配置/WDIO/runner 和文档属于单一 `feat: 完成断网与网络抖动恢复验收` 提交；完成后立即推送 `main`
- RED 与边界：先把唯一台账置为 `🧪 RED`；六项 Python 目标测试分别准确暴露账本仍为 v5、spool 上限不存在、网络闸门 API 不存在、平台健康消息发送失败后丢失、无关闭帧断线与初次网络不可达仍固定退出。隐藏 App 工程契约再因 `tauri.network-recovery-e2e.conf.json` 不存在而准确失败。H8-06 的 `1012` 受控重启不能冒充真实断网，H8-03 离线紧停也不证明普通网络中断期间的持久事件续传
- 安全点与本机事实：`executor-ledger.sqlite3` 原地迁移到 v6，在既有 singleton 动作 guard 增加严格 `network_connected`。独立账本默认在线以保留动作层单测边界，正式 `LocalExecutorProcess` 构造立即置离线；Hello、durable outbox replay 和控制轮询全部完成后才置在线，断线先复位。`begin_side_effect_dispatch()` 在同一排他事务同时检查紧停与网络闸门，已开始动作仍按既有 verified/uncertain 语义结算，prepared 新动作在离线期间绝不能进入 dispatched
- 有界 spool 与原子回滚：继续使用原 SQLite `executor_outbox`，没有建立第二事件队列。未交付内容同时限制为 1000 条和 16 MiB，字节数按持久 UTF-8 envelope 计算；普通 command/outbox、crash recovery 与 outcome 四个生产写入入口都在更新 checkpoint/side-effect 前核容，超限整笔回滚。恢复发送循环按 batch 持续排空；内存平台健康队列在网络发送失败时保留同一 envelope，重连后精确重发
- 异常网络恢复：初次连接 `OSError/TimeoutError`、已连接 socket 无 close frame 消失及 durable/local outbox 发送期网络错误都进入与 H8-06 共用的默认 120 次×250ms 预算，构造边界仍限制最多 1000 次/5 秒；stop 立即打断退避，只有恢复后有效 heartbeat 重置预算。`1011`、协议解析、应用逻辑、坏配置和非恢复关闭继续固定失败，预算耗尽明确退出，不形成无限循环或 Rust supervisor 抖动重启
- 原调用方验收：`backend/.venv/bin/python scripts/run_h8_07_acceptance.py` 构建并签名真实 PyInstaller Executor、唯一 `visible=false` H8-07 Tauri App、`automation-tool-h807-*` PostgreSQL/完整 Alembic/真实 Uvicorn/AppData/SQLite。App 从真实表单创建 comment Task、经正式 IPC 启动签名 Executor，并在原详情页点击取消；runner 只暂停系统复核的精确 Executor PID，服务端形成 delivered cancel 后 `SIGKILL` Uvicorn，制造没有 WebSocket close frame 的真实中断
- 断网与抖动事实：恢复 Executor 后，runner 先看到持久网络闸门为 0、原 cancel 对应的 `task.control_ack`/`task.cancelled` 两条未交付 outbox，并用生产账本入口证明 prepared 动作 dispatch 被拒且 revision 不变；App 同时从正式 Rust 网络桥显示 Control Plane 不可用。随后用同一 PostgreSQL 重启 Uvicorn并再强杀/恢复两次，同一 Executor PID 始终存活、`restartCount=0`；稳定后 App 从工作台/详情读到 cancelled，PostgreSQL 与 SQLite 原命令、事件、checkpoint、outbox 只各存在一次，网络恢复后 prepared 动作仍未执行
- 失败发现与修正：首次隐藏 App 运行已完成硬断网、恢复和两次抖动，但 runner 在 WDIO 结束并开始回收 App/Sidecar 后错误要求网络闸门为离线；改为 App 与同一 Executor 尚存活时用双向信号核对“online、outbox 清空、prepared 未执行”，再允许 WDIO 退出，没有改生产行为。首次 Backend 全量中真实冻结 Executor 按新默认预算重试到 20 秒时被旧测试超时强杀；只把该实包观察窗口扩到 45 秒，生产 30 秒预算不变。提交前逐行审查再发现 `ExecutorProcessRejected` 继承 `ConnectionError/OSError` 会让普通固定失败误入网络恢复；补“非法消息不可恢复/真实发送 OSError 可恢复”相反 RED 断言后，按专用网络异常、固定进程/传输拒绝、原始 socket 错误的顺序分类，并让平台消息网络异常保持专用类型。Rust supervisor 既有 3 秒时序用例波动一次，原命令独立与整组复跑通过，未修改超时或产品代码
- 失败与覆盖矩阵：覆盖首次网络不可达、无关闭帧、1012、非恢复关闭、连接/发送 `OSError`、stop 竞态、预算耗尽/重置、坏次数/延迟、网络闸门存储失败、缺失/非法闸门、prepared dispatch 竞争、同 envelope 平台消息保留、durable outbox 多批排空、条数/字节双上限、四种写入原子回滚、相同 command/outbox 精确重放及正常退出复位。断网不访问真实平台账号、不打开运营浏览器，也不把网络恢复当成平台动作成功
- 门禁：Backend 最终全量 `1918 passed, 5 skipped in 214.47s`，12358 条语句/2776 个分支覆盖率 100%，327 个 Python 文件格式、Ruff、严格 Mypy 301 个源码文件、uv lock/sync 全绿；Frontend 109 项 Node 契约、197 项 Vitest、5 项无头 Playwright、冻结安装、peer dependency、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；H8-07 隐藏 App 纵向验收 1/1 通过，Rust 默认/`desktop-e2e`/`control-plane-e2e` 的代码单元、相关集成、Rustfmt、三套全目标 Clippy 与 Actionlint 全绿
- 独立外部环境证据：全量 Rust 的 `browser_discovery`/`browser_settings` 两个既有实机用例发现本机 Chrome 包内 `Google Chrome Framework.framework` 被附加签名不允许的 `com.apple.FinderInfo`；`codesign --verify --deep --strict --all-architectures` 同样明确报该 xattr，生产信任边界正确 fail closed。普通权限删除被系统拒绝、无交互 sudo 需要用户密码，因此没有放宽签名规则或伪报全量 Rust 通过；这是 B5 浏览器本机环境补验，不影响本任务不启动浏览器的真实断网链路，后续在用户可输入管理员密码时清除精确属性并补跑两项实机测试
- 隔离与清理：固定 8765/1420 与随机 PostgreSQL 端口启动前均检查；专属 Compose project/network/volume、App identifier/AppData、SQLite 和临时信号目录不复用其他项目。两次真实验收（含首轮严格断言失败）都在 finally 回收隐藏 App/WDIO、签名 Executor、Uvicorn、容器/网络/Volume、AppData 和端口；无 Playwright/Chrome/业务 Profile 或真实账号副作用。全量无头 Playwright 结束后同样复核 Vite/浏览器无残留
- 后续：进入 `H8-08`，覆盖电脑休眠/锁屏造成的单调时钟跳变、deadline 过期、桌面窗口不可用与恢复诊断；继续复用 H8-07 网络闸门和有界传输恢复，不建立第二套生命周期状态机

### H8-08 休眠/锁屏恢复

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、单调调度间隙守卫、过期命令分类、固定恢复诊断、浏览器窗口恢复诊断、H8-08 独立隐藏 App/无头浏览器验收和文档属于单一 `feat: 完成休眠与锁屏恢复验收` 提交；完成后立即推送 `main`
- RED 与边界：先把唯一台账置为 `🧪 RED`；命令测试准确失败于没有独立过期类型，诊断/浏览器测试准确失败于固定恢复诊断器不存在，真实 WebSocket 测试证明单调调度跳变仍会在旧连接处理帧，隐藏 App 工程契约再因 H8-08 配置、WDIO 场景和 runner 不存在失败。H8-08 不调用整机休眠或真实锁屏，不新增系统电源 API、第二网络状态机、业务协议、数据库迁移、账号或系统钥匙串依赖
- 安全恢复：正式 `LocalExecutorProcess` 在循环开始、socket timeout 后和收到业务帧后三处比较同一单调时钟；超过固定 5 秒的调度间隙在读取陈旧帧或发 heartbeat 前进入 H8-07 专用可恢复断线，外层先把 SQLite `network_connected` 复位，再用原 120×250ms 有界预算重连。只有恢复连接收到有效 heartbeat 才清空恢复标记；单调时钟倒退、非有限值、坏阈值和存储失败继续固定退出
- 误报与 deadline：正式命令/页面处理完成后重置观测基线，真实 WebSocket 反向用例证明 10 秒业务耗时不会冒充休眠，而循环边界、timeout 和收帧三种 10 秒跳变仍安全重连。命令结构、身份和类型先按原协议验证，只有合法但本机 UTC 已达到 `deadline_at` 的帧细分为固定 `ExecutorCommandExpired`；runtime 忽略并记录固定诊断，不写 command/checkpoint/outbox，其他坏协议仍让进程 fail closed
- 窗口与诊断：新增的 `ExecutorRecoveryDiagnostics` 不接收调用方字符串，只能写 `system_suspension_detected`、`command_deadline_expired`、`transport_recovered`、`browser_window_unavailable` 和 `browser_window_recovered` 五种固定事实；输出失败不反射异常。`BrowserRuntime` 在 context/window 操作失败时只标记一次 unavailable，共享诊断器看到后续隔离 runtime 成功启动才记录 recovered。Rust Manager 继续按 E4-10 对所有 stderr 做 4096-byte 读取、二次脱敏和 200 行/64 KiB 滚动保留
- 原调用方验收：`backend/.venv/bin/python scripts/run_h8_08_acceptance.py` 先用系统 Chrome、两个 0700 临时 Profile 和 `headless=true` 运行真实 `BrowserRuntime`，主动关闭 context 后准确得到窗口不可用，再启动第二 runtime 得到恢复；随后构建并签名 PyInstaller Executor、唯一 `visible=false` H8-08 Tauri App、`automation-tool-h808-*` PostgreSQL/完整 Alembic/真实 Uvicorn/AppData/SQLite。App 经正式注册和 `restart_executor` 启动签名进程，runner 只暂停系统复核的精确 PID 6.25 秒再恢复
- 纵向事实：恢复后 App 从原 `get_executor_diagnostics` IPC 读到精确休眠/传输恢复代码，不含 token、私有路径或异常原文；同一签名 Executor PID 始终存在、Rust `restartCount=0`、持久网络闸门恢复为 online，本机 command/outbox 均为 0。验收没有真实锁屏、没有可见 App/浏览器、没有默认 Profile 或真实账号访问，也没有把进程暂停冒充整机平台验收
- 失败发现与修正：首次无头验收暴露 macOS `/var` 是 `/private/var` 符号链接而生产路径守卫正确拒绝，runner 改用真实解析后的隔离路径；第二轮暴露 WDIO 把诊断快照错误当数组，按正式 IPC `{ lines }` 形状修正并补失败日志清理；第三轮完整通过。提交前反向审查再发现 5 秒以上正常页面任务会在下一循环被误报休眠，新增真实 WebSocket RED 后只在命令完成处重置基线，休眠三个检测边界保持不变
- 门禁：Backend 独占全量 `1927 passed, 5 skipped in 243.23s`，12485 条语句/2810 个分支覆盖率 100%，327 个 Python 文件格式、Ruff、严格 Mypy 301 个源码文件、uv lock/sync、OpenAPI 与 Executor Schema 全绿；Frontend 110 项 Node 契约、197 项 Vitest、5 项无头 Playwright、冻结安装、peer dependency、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认/`desktop-e2e`/`control-plane-e2e` 三套完整测试、Rustfmt、三套全目标 Clippy `-D warnings` 与 Actionlint 全绿。三组门禁并行重负载时既有 WebSocket 合约时序断言波动一次，未修改超时或产品逻辑；随后该合约文件及 Backend 独占全量均稳定通过。H8-08 隐藏真实 App/无头浏览器纵向验收 1/1 通过
- 隔离与清理：固定 8765/1420 与随机 PostgreSQL 端口启动前检查，使用专属 Compose project/network/volume、App identifier/AppData、SQLite 与临时 Profile。三轮纵向运行的成功和失败路径均在 finally 回收 WDIO/App、签名 Executor、Uvicorn、Chrome、容器/网络/Volume、AppData 和临时目录；最终复核零 H8-08 监听、容器、App/Executor/浏览器进程与 AppData 残留
- 后续：进入 `H8-09` Local Artifact，冻结稳定 ID、摘要、媒体类型、大小、相对路径和权限；本次固定诊断不承载截图/Trace，H8-10 继续在 Artifact 边界上实现

### H8-09 Local Artifact

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、通用 Local Artifact Store、页面漂移复用、原调用方无头浏览器验收与权威文档属于单一 `feat: 完成本机 Artifact 安全边界` 提交；完成后立即推送 `main`
- RED：先把唯一台账置为 `🧪 RED`；新聚焦测试最初在收集阶段准确失败于 `automation_tool.executor.local_artifact` 不存在，随后枚举契约准确失败于 Store 没有 `list_references`，页面漂移复用准确失败于没有共享 Policy。收口审查又以真实符号链接用例证明初始化虽拒绝受控子目录链接，却会在拒绝前误改链接目标权限；修复后改为先验证既有目录并直接 fail closed，不再沿链接执行 `chmod`
- 通用引用与 Policy：新增不可变 `LocalArtifactPolicy`、`LocalArtifactRef` 和唯一 `LocalArtifactStore`。可信生产者在代码内固定小写受控目录、扩展名、媒体类型、单文件和数量上限；引用只包含 canonical UUIDv4、SHA-256、媒体类型、大小和受控 POSIX 相对路径，不包含绝对路径、任意 JSON、调用方路径或自由媒体类型。Store 只提供独占 capture、ID 解析、完整引用校验读取和按 ID 稳定枚举，不提供覆盖、删除、导出、上传或任意文件访问
- 文件与权限边界：根目录必须已经存在且是当前用户私有目录；初始化和每次操作复验根/叶 dev+inode，目录项只允许匹配固定扩展名的 canonical UUIDv4。普通文件必须非 reparse/symlink、单硬链接、非空且在 Policy 大小内；写入使用 `O_EXCL`/可用时 `O_NOFOLLOW`、`0600`、完整 write/fsync、稳定重读和失败残片清理。POSIX 根/子目录/文件分别精确 `0700/0700/0600`，Windows 复用现有私有 ACL 适配器；目录替换、未知项、坏 ID/摘要/媒体类型/路径、容量、碰撞、部分写、读取/身份竞态和权限扩张均固定拒绝且不回显原始输入
- 复用而非并存：D6-14 `PageDriftArtifactStore` 删除重复的 UUID、摘要、目录清点、权限和独占写实现，只保留页面漂移固定 Schema、共享 Policy 与窄 Ref；生产路径统一为 Executor 私有 state 根下 `artifacts/evidence/page-drift/<id>.json`。现有发现编排仍是唯一调用方，Control Plane 协议、PostgreSQL、App/React 和任务状态没有新增第二套 Artifact/Core 模型
- 原调用方验收：`uv run pytest tests/integration/test_page_drift_artifact_browser.py tests/integration/test_douyin_discovery_fake_pages.py -q` 共 8 项通过。测试从正式 `ExecutorCommandProcessor.handle(task.discover)` 进入生产发现编排和真实 `BrowserRuntime`，每个场景使用独立临时 Profile、`headless=true` 系统 Chrome 与官方 origin 确定性路由；生成后按文件名 Artifact ID 经同一 Store `resolve → list → read`，核对摘要、大小、相对路径和无敏感内容，所有 BrowserRuntime 完整关闭。H8-09 没有 App/API，因此没有用直接 HTTP、Mock 或内部函数冒充 App 调用，也没有新增无业务意义的 Tauri Command
- 门禁：Backend 最终全量 `1939 passed, 5 skipped in 224.24s`，12712 条语句/2882 个分支覆盖率 100%，328 个 Python 文件格式、Ruff、严格 Mypy 303 个源码文件、uv lock/sync、OpenAPI 与 Executor Schema 全绿；H8-09 两个生产模块聚焦 24 项测试语句/分支覆盖率 100%。Frontend 110 项 Node 契约、197 项 Vitest、5 项无头 Playwright、冻结安装、peer dependency、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认/`desktop-e2e`/`control-plane-e2e` 三套完整测试、Rustfmt、三套全目标 Clippy `-D warnings` 与 Actionlint 全绿
- 隔离与清理：H8-09 业务验收不启动 Uvicorn、PostgreSQL、Compose、Tauri App 或真实账号，也不占用固定业务端口；系统 Chrome 只使用 pytest 私有临时 Profile 且固定无头。通用 Frontend Playwright 门禁在启动 1420 前明确确认端口空闲，退出后再次确认释放。成功和失败路径都由 BrowserRuntime 关闭自有浏览器，门禁结束后将本轮精确 pytest 临时目录移入系统废纸篓；最终复核无项目 Chrome/Playwright/Vite/Uvicorn、监听端口、容器、AppData 或活动临时 Artifact 残留
- 后续：进入 `H8-10`，只在失败或用户明确开启时基于当前 Policy/Store 保存受限诊断截图/Trace，并补数量、大小、时间和敏感内容边界；不复制文件安全实现，不在本任务提前加入上传或清理

### H8-10 诊断截图/Trace

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、受限诊断生产器、发现任务触发链、App 私有设置、隐藏 App 原调用方验收和权威文档属于单一 `feat: 完成受限浏览器诊断` 提交；完成后立即推送 `main`
- RED 与边界：先把唯一台账置为 `🧪 RED`；聚焦测试最初准确失败于 `browser_diagnostic_artifact` 模块不存在，随后正式发现任务失败路径准确证明截图调用次数仍为 0，Rust/React 契约再准确失败于没有成功任务诊断设置和 Executor bootstrap 字段。H8-10 不提供任意截图/Trace API、文件浏览、导出、上传、自动清理或 Control Plane 数据面，也不把 Playwright 原始 Trace、DOM、网络、页面正文和调用方自由文本写入 Artifact
- 受限诊断事实：新增唯一 `BrowserDiagnosticArtifactStore` 并复用 H8-09 `LocalArtifactStore`，截图固定保存到 `artifacts/diagnostics/screenshots`，只截当前 viewport，禁用动画并在截图前隐藏文字、表单值、图片、视频、Canvas、SVG、iframe、背景、阴影和滤镜；写入前严格验证 PNG signature/chunk/CRC/IHDR 与最大 4096×4096，只保留 IHDR/PLTE/IDAT/IEND，剥离全部 ancillary metadata。Trace 不是 Playwright 原始跟踪包，只是固定 JSON 事实：版本、Trace/截图 Artifact ID、平台、操作、页面 revision、阶段、触发原因、脱敏版本和 UTC 时间，不含 Task/Attempt ID、URL、DOM、HTML、请求、响应、Header、Cookie、页面快照或自由文本
- 触发、上限与失败隔离：发现任务异常始终尝试保存一张受限截图和一份固定 Trace；成功任务默认不保存，只有 App 私有设置明确开启后才在下次 Executor 启动时通过 bootstrap 生效。截图固定 1 MiB、Trace 固定 4 KiB、各最多 8 个，截图本身有 5 秒超时；坏 PNG、超尺寸、CRC 错误、容量耗尽、存储/权限/时钟失败和浏览器截图失败都只产生固定非泄漏拒绝，绝不改变原任务成功/失败结果，也不回显不可信网页内容或本机路径
- App 私有设置：Rust 只在 `app_data_dir/local-executor/browser-diagnostic-settings-v1` 保存精确 `{version:"1",capture_successful_runs:boolean}`，复用 App 私有文件存储并保持 POSIX `0600`，畸形内容以固定存储错误拒绝初始化；不使用系统钥匙串，不返回路径或密钥。设置页提供“保存成功任务的脱敏诊断”开关，失败任务始终保存；React 只经固定 `PlatformAdapter`/Tauri Command 读写布尔值，重启 Executor 时 Rust 把当前值写入 stdin bootstrap，崩溃恢复继续沿用同一启动配置
- 原调用方验收：真实无头浏览器集成从 `ExecutorCommandProcessor.handle(task.discover)` 进入正式发现编排、`BrowserRuntime` 和系统 Chrome；失败任务自动生成受限截图/Trace，显式开启的成功任务生成，默认成功任务不生成，并从同一 Store 核验摘要、大小、相对路径、精确 Trace 字段和敏感内容缺失。`scripts/run_e4_14_acceptance.py` 再构建签名 Executor，从唯一 `visible=false` Tauri App 的设置开关、启动/崩溃恢复/挂起紧停/再次启动正式 IPC 链路验证设置文件、`0600`、SQLite v6 和 bootstrap；最终输出 `[E4-14] Hidden-App signed Executor lifecycle acceptance passed`
- 失败发现与修正：真实 macOS 隐藏 App 证明挂起后的紧急停止既可能直接收敛为 stopped，也可能先返回固定安全失败再刷新收敛，因此删除旧的 Windows 专属分支，保留两条安全路径；runner 的历史 SQLite v2 断言同步到当前 v6，并新增诊断设置文件精确校验。Backend 全量期间发现两条旧的扫码/Session Fake 页面例行测试漏传 `headless=true`，先停止该轮测试并补显式无头参数，针对性回归后重新执行全量；真实扫码手工用例仍由环境变量显式门控，不会在例行门禁弹窗
- 失败与覆盖矩阵：覆盖成功默认关闭/显式开启/设置重启持久化、失败自动捕获、成功或失败诊断写入自身失败、截图超时/异常/过大/畸形 signature/chunk/CRC/dimension/metadata、Trace 固定字段与大小、空/满 Store、坏时钟、目录/文件权限和不可信 ID/阶段/平台输入；浏览器运行时始终由确定所有者在成功、失败和截图异常后关闭，不读取默认 Profile、Cookie 或真实账号
- 门禁：Backend 最终全量 `1948 passed, 5 skipped in 221.93s`，12877 条语句/2938 个分支覆盖率 100%，330 个 Python 文件格式、Ruff、严格 Mypy 305 个源码文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 110 项 Node 契约、198 项 Vitest、5 项无头 Playwright、ESLint、严格 TypeScript、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt、三套全目标 Clippy `-D warnings` 与 Actionlint 全绿。新诊断模块聚焦 19 项测试语句/分支覆盖率 100%，H8-10 相关真实浏览器聚焦 78 项通过，隐藏真实 App 纵向验收 1/1 通过
- 隔离与清理：真实浏览器与 UI Harness 固定无头，Tauri App 固定隐藏；启动 1420 前确认端口空闲，结束后确认释放。隐藏验收使用专属 App identifier/AppData、随机 Control Plane/PostgreSQL 端口、`automation-tool-e414-*` Compose 资源和独立 SQLite，成功与失败路径都回收 WDIO/App/Executor/Uvicorn、浏览器、容器/网络/Volume 和临时 Profile；不访问真实平台账号，也不产生外部副作用
- 后续：进入 `H8-11`，从服务端、Rust、Executor 和 App 原入口建立凭据、页面内容、URL、Header、Cookie、错误原文与本机私有路径的全链路日志泄漏矩阵；继续复用 E4-10 固定脱敏器和本任务受限 Artifact，不建立第二套日志或诊断存储

### H8-11 日志脱敏

- 状态：✅ 已完成
- 日期：2026-07-21
- 提交：本记录、Python 共享脱敏器、Control Plane 进程日志边界、Executor/Rust 公共 fixture v2、真实冻结进程与隐藏 App 原调用方验收、全局无头门禁和权威文档属于单一 `feat: 完成全链路日志脱敏` 提交；完成后立即推送 `main`
- RED 与边界：先把唯一台账置为 `🧪 RED`；Control Plane 测试准确在收集阶段失败于 `automation_tool.control_plane.logging` 不存在，CLI 测试准确证明 Uvicorn access log 仍开启，Rust 公共 fixture 又准确证明旧规则会保留 URL base。H8-11 不新增日志文件、云端日志服务、任意日志查询/导出、页面读取、数据库表、账号或系统钥匙串，也不把 H8-10 Artifact 当日志存储
- 单一规则与 fixture：新增根级 `logging_redaction.py`，Executor `diagnostics.py` 只委托该实现，不再复制 Python 正则；`executor-diagnostics-v1.json` 升级为 fixture v2、18 个公共样例，Python 与 Rust 同时回放。凭据、Authorization/Header/Cookie、64 位秘密、任意 scheme URL、data URL、页面/HTML/DOM/评论/私信/请求响应正文、POSIX/Windows 私有路径、控制字符和 Bidi 字符统一替换为固定 `[REDACTED]`，不再保留 URL base/query 或错误原文
- Control Plane 边界：应用工厂和正式 CLI 在任何业务 handler 前安装幂等 `LogRecord` factory，只处理 `automation_tool.control_plane*` 与 `uvicorn*`；动态参数、异常和 stack 只形成固定占位，pathname 固定脱敏，单条最终 UTF-8 最多 4096 bytes。`uvicorn.access` 在 handler 前折叠为固定 `Control Plane request`，正式 CLI 同时关闭 access log；AST 门禁要求生产 `logger.*` 只有一个字面量消息参数，`extra` 只允许固定 `request_id`，避免未经治理的动态对象绕开边界
- Executor、Rust 与 App 原调用方：真实 Python Executor stderr 先经过共享规则，Rust Manager 仍把子进程视为不可信并独立按相同 fixture v2 二次脱敏、限界和内存滚动保留。`scripts/run_e4_10_acceptance.py` 已跨 macOS/Windows 从正式 signed PyInstaller Executor → 公开 Manager `diagnostics()` 验证完整 hostile stderr；唯一 `visible=false` E4-14 App 再经测试特性内的固定无参数准备命令注入同一公共 fixture，页面随后只调用正式 `get_executor_diagnostics` 读取并拒绝全部精确私密值，测试命令不进入正式制品
- 无头全局兜底：常规 Playwright 在全局配置显式固定 `headless: true`；新增 Node 门禁同时扫描常规 Python 浏览器集成用例的 `BrowserLaunchRequest`，任何漏传 `headless=True` 都直接失败。真实扫码/账号和冻结 headed 探针仍是显式环境变量门控的唯一可见例外，默认全量门禁不会弹窗；浏览器和 WebServer 结束后继续核对并回收
- 失败与覆盖矩阵：覆盖非字符串/超长多字节消息、动态格式参数、异常、stack、Uvicorn 请求目标、非目标 logger、重复安装、页面内容、URL/签名查询、Header/Cookie、数据库 DSN、凭据 envelope、私有路径、非法 UTF-8、超长行、行数/总字节边界，以及 Python/Rust fixture 漂移；Control Plane 生产日志调用面、Executor 双重边界和 App 正式读取面均 fail closed
- 门禁：Backend 全量 `1953 passed, 5 skipped in 224.38s`，12930 条语句/2954 个分支覆盖率 100%，333 个 Python 文件格式、Ruff、严格 Mypy 308 个源码文件、uv lock、OpenAPI 与 Executor Schema 全绿；Frontend 111 项 Node 契约、198 项 Vitest、5 项全局无头 Playwright、ESLint、严格 TypeScript、API 漂移、production boundary 与 Vite build 全绿；Rust 默认、`desktop-e2e`、`control-plane-e2e` 三套完整测试、Rustfmt、三套全目标 Clippy `-D warnings` 与 Actionlint 全绿。E4-10 冻结 Executor 与 E4-14 隐藏 App 原调用方验收均 1/1 通过
- 隔离与清理：冻结 Executor 和隐藏 App 验收复用既有专属临时 AppData、SQLite、随机端口及 `automation-tool-e414-*` Compose 资源，成功/失败均回收；UI Harness 启动 1420 前确认空闲并在结束后确认释放。例行浏览器固定无头，不访问真实平台账号或默认 Profile，不产生平台副作用；最终复核本项目 App、Executor、WDIO、Vite/Uvicorn、浏览器、端口、容器/网络/Volume 和临时目录零残留
- 后续：进入 `H8-12`，在 H8-09/H8-10 唯一 Store 上实现保留策略、磁盘满/清理失败和正在引用 Artifact 保护；不复制文件安全边界，不提前实现 H8-13 导出

## 21. 当前下一步

严格按顺序：

1. `A7-16/A7-17`（🔍 待真实账号）：只在用户明确指定的自有/授权目标上完成真实评论与私信最终状态验收；没有目标时跳过，不制造外部副作用；
2. `A7-18`（依赖阻塞）：待 A7-16/A7-17 真实证据完成后执行风险护栏对抗测试，不把离线 Fake 证据冒充通过；
3. `H8-12`（⬜ 未开始）：在唯一 Local Artifact Store 上实现保留策略、磁盘满/清理失败和正在引用 Artifact 保护，不提前增加导出或上传；
4. `D6-16` 真实账号补验：用户按正常平台流程解除首页验证码后，完成真实搜索、App 预览与零副作用核对；
5. `B5-15` 真实账号补验：独立登录 Profile 再次可用时，从真实 App 连续重启两次验证直接健康；账号不可用时继续保持 `🔍`，不阻塞后续任务；
6. `B5-02` 本机环境补验：在用户可输入管理员密码时，仅清除 Chrome Framework 上破坏深度签名的 `com.apple.FinderInfo` 后重跑真实发现/设置测试；另在安装 Microsoft Edge 的 macOS 设备上验证真实签名、Bundle ID 和 Team ID。其余本轮 Windows 原生验收已于 2026-07-20 补齐。
