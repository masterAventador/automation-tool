# 自动化运营工具任务级开发路线图

> 文档性质：后续开发的任务定义、依赖、状态与当前下一步台账；详细执行证据按任务拆分到 [`docs/development/`](./development/)
> 建立日期：2026-07-18
> 当前阶段：Wave 9 本地 MVP 最终验收报告已建立；P9-09 保持待真实账号与双平台正式设备验收；U9-01～U9-06、C10-01～C10-07 已完成，下一工程任务为 C10-08 云端部署
> 执行顺序：RPA 运营 > 内容生产与分发 > AI 员工与工作流
> 当前开发终点：完成 Wave 10 全部任务后停止；第 16 节“RPA 运营增强路线图”及之后任务本轮不启动

## 1. 如何使用本路线图

本文件承载里程碑、依赖、失败矩阵、完成定义和可独立开发、测试、审查、提交的小任务，但不承载逐任务完成记录。以后开始任何产品代码任务前，必须：

1. 确认前置任务全部满足；
2. 把且只把一个任务标为 `🧪 RED` 或 `🚧 实现中`；
3. 先补能证明完成定义的失败测试；
4. 实现最小代码并跑完该任务门禁；
5. 在 `docs/development/<任务ID>.md` 更新实际验证命令与证据；
6. 标记 `✅ 已完成`并形成独立提交；
7. 再启动下一任务。

路线图中的“完成定义”是最小门禁，不替代 [`CLAUDE.md`](../CLAUDE.md) 的失败矩阵、真实平台验收和资源清理规则。会话恢复只需读取本路线图和当前任务的独立记录，不应读取全部历史任务文件。

### 1.1 内置浏览器、Browser Use、视频制作/剪辑与发布专项规划

[embedded-browser-video-studio-roadmap.md](./embedded-browser-video-studio-roadmap.md) 已把 App 内置 Chromium、Browser Use 开源框架、“智能素材成片”、“品牌动效成片”、独立视频剪辑、首期 B站/抖音发布和后续自愈式自动化拆成 87 个候选任务，其中网址转视频 BM-09～BM-10 与 SA-01～SA-07 明确不属于首期。该文件现在是专项细化执行 Roadmap，87 行均有当前状态；每次激活、实现、待验收或完成任务，必须在同一次代码提交中同步专项状态与本文件的全局阶段、证据和提交，不能仅凭规划行宣称能力已实现。App 尚未发布，专项方案按全新安装设计，不迁移开发期浏览器 Profile 或旧版 App 数据；EB-02 必须最先验证 RPA、Browser Use 与动效渲染共用同一完整 Chromium，通过后后续任务只按单一浏览器发行物实施。锁定动效 registry 的 134 个可安装零件计划全部离线内置并替换无明确再分发权的示例资产；视频剪辑独立成模块，首期只接阿里云；发布首期只做 B站正式 API 和抖音 Browser Use，快手/小红书/微信视频号暂不实现。现有搜索、评论、私信 Playwright 第一版只原样迁移到内置浏览器，待抖音发布的 Browser Use 路径真实验收后再另立 AI 改造任务。B站凭据未由用户提供时仍需完成 Adapter、Mock/fixture、契约测试和失败矩阵，只把真实账号联调标为“待凭据验收”；缺少凭据不得阻塞抖音、视频制作、剪辑或后续任务，也不得用 Mock 冒充真实平台证据。品牌动效首期只接受一句话和用户主动提供的品牌资料，不提供 URL 输入、网站抓取或网址转视频入口。

实施前必须先在本文件激活专项规划的 AV-01～AV-04：用 ADR 同步产品与架构基线，保留并标记外部 Chrome/Edge 历史任务的替代关系，再把准备执行的小任务及依赖登记到本文件。之后本文件维护全局阶段、当前任务、真实证据和提交，专项 Roadmap 维护 87 项明细状态；两处必须在同一次代码提交中同步。

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

快照日期：2026-07-22。

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
| 产品代码 | `✅ 已完成` Wave 1～Wave 6 工程主线、A7-01～A7-15、H8-01～H8-21、P9-01、P9-02、P9-08 与 U9-01～U9-05 已完成；P9-04 Windows 原生工程链、P9-03 macOS 候选、P9-05 完整包审计以及 P9-06/P9-07 双平台干净安装 runner 已就绪并保持各自 `🔍` 设备/正式签名验收。U9-04 已建立 customer-demo 外层账号门禁与 Rust 私有账号 Session vault；U9-05 已接入账号 Session + Ed25519 设备证明的 Installation 原子不可变归属、凭据签发/轮换及登录前置绑定。H8-22 正式发布签名证据与 D6-16、A7-16、A7-17、B5-15 真实账号证据继续独立待补 |
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
| Executor v1 协议 | `✅ 已完成` 31 种消息三端判别解析、显式版本、用途隔离 UUIDv4、UTC 微秒 deadline、幂等键、安全整数序号、安全 payload、Draft 2020-12 Schema 与 39 个公共 fixtures 已验证 |
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
| Attempt/Action 持久化 | `✅ 已完成` current Attempt 复合绑定、Installation 级非终态单活 Attempt、重试/Action 序号唯一、阶段/结果一致性已在 PostgreSQL 18.4 验证 |
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
| H8-12 | 清理与磁盘治理 | 保留策略、磁盘满、清理失败、正在引用 Artifact 保护 | H8-09,H8-10 | ✅ 已完成 |
| H8-13 | 诊断导出 | 用户主动导出受限包；不含 Cookie/完整私信/绝对私有路径 | H8-11,H8-12 | ✅ 已完成 |
| H8-14 | 工作台指标 | 任务/动作成功、失败、接管、不确定；只读结构化事实 | A7-15,T3-16 | ✅ 已完成 |
| H8-15 | 完整失败矩阵自动化 | 本台账第 4.1 节所有可自动化分支有测试或不适用理由 | H8-01..H8-14 | ✅ 已完成 |
| H8-16 | 规格复审 | 从分叉点审查完整实现是否满足产品/MVP/文档 | H8-15 | ✅ 已完成 |
| H8-16A | 桌面发现入口闭环 | 正式页面从 Task 草稿/登录接管状态启动或重试发现，调用固定 Tauri→API 原路径 | H8-16 | ✅ 已完成 |
| H8-16B | Installation 单活任务 | 同一 Installation 只能有一个未终结 RPA Attempt，数据库并发单赢家且 UI 明确提示 | H8-16A | ✅ 已完成 |
| H8-16C | 服务端动作执行编排 | 确认后按目标逐个授权并投递 typed 执行命令，保留确认、频控、幂等和结果收敛 | H8-16B | ✅ 已完成 |
| H8-16D | Executor 真实动作编排 | 正式 Processor 消费授权命令并调用 browse/comment/direct-message 生产实现，不再返回固定成功批次 | H8-16C | ✅ 已完成 |
| H8-16E | 启动环境诊断闭环 | App 启动统一检查 Control Plane、Executor、受信浏览器和 App 私有数据目录并给出安全修复入口 | H8-16D | ✅ 已完成 |
| H8-16F | MVP 规格差异收口 | 校准页面承诺/后置范围，并由隐藏 App 跑通创建→登录/发现→预览→确认→受控动作→结果 | H8-16E | ✅ 已完成 |
| H8-17 | 代码质量复审 | 安全 fail-open、竞态、资源泄漏、假绿测试和平台差异 | H8-16F | ✅ 已完成 |
| H8-18 | 通用更新底座选型与契约 | 评估现成 SDK；冻结与业务无关的版本、平台、签名、发布策略和状态契约 | H8-17 | ✅ 已完成 |
| H8-19 | 通用更新策略机 | 可选更新支持立即安装/暂不安装/跳过版本；强制更新不可跳过，状态持久且版本单调 | H8-18 | ✅ 已完成 |
| H8-20 | 后台检查与下载 | App 启动、有界轮询和用户“检查更新”共用同一检查入口；后台下载、签名验证、断点/失败恢复；新包原子覆盖旧缓存 | H8-19 | ✅ 已完成 |
| H8-21 | 安装与重启协调 | 立即安装先安全退出主 App；暂缓在启动/轮询继续提示；强更下载后下次启动静默进入安装 | H8-20 | ✅ 已完成 |
| H8-22 | 更新 UI 与双平台验收 | 通用设置/提示 UI；真实签名包从 App 原入口在 macOS、Windows 完成升级、跳过、覆盖和强更验收 | H8-21 | 🔍 待验收 |

## 14. Wave 9：双平台安装包与本地候选版

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| P9-01 | macOS Executor 构建 | PyInstaller onedir，依赖完整、无开发路径、签名准备 | H8-22 | ✅ 已完成 |
| P9-02 | Windows Executor 构建 | PyInstaller onedir，Playwright/UIA 依赖和 Job Object 正常 | H8-22 | ✅ 已完成 |
| P9-03 | macOS Tauri 候选包 | 签名、公证策略、最小 Capability/CSP | P9-01 | 🔍 待验收 |
| P9-04 | Windows Tauri 候选包 | 签名、安装/卸载和最小系统权限 | P9-02 | 🔍 待验收 |
| P9-05 | 正式包内容审计 | 无 WebDriver、调试端口、测试凭据、真实日志/Profile/素材 | P9-03,P9-04 | 🔍 待验收 |
| P9-06 | macOS 干净安装 | 无 Python 前置；打开即用；Chrome/Edge/扫码/任务/恢复 | P9-03,P9-05 | 🔍 待设备验收 |
| P9-07 | Windows 干净安装 | 无 Python 前置；同上；DPI/杀进程/卸载行为 | P9-04,P9-05 | 🔍 待设备验收 |
| P9-08 | 版本兼容/降级 | App/Executor/Control Plane 兼容矩阵，错误版本 fail closed | P9-06,P9-07 | ✅ 已完成 |
| P9-09 | 本地 MVP 最终验收 | 产品规划 14 条 MVP 验收全部通过并记录证据 | P9-08 | 🔍 待验收 |

### 14.1 客户 Demo 前置：账号体系与设备归属

> P9-09 仍以本地单设备、无产品账号完成 MVP；任何客户 Demo 必须先完成 U9-01～U9-06，不得以匿名设备申请、配对码或人工设备审批替代产品账号。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| U9-01 | 账号范围与威胁模型 | 固定首版账号生命周期、登录标识、凭据恢复、Session、设备归属、停用/吊销和审计；Demo 账号由认证运维入口创建，不开放匿名自注册；组织、租户、RBAC、套餐和计费继续排除 | P9-09 | ✅ 已完成 |
| U9-02 | 账号领域与 PostgreSQL | User、登录凭据、账号状态、审计与迁移；规范唯一标识、强密码哈希、并发创建/停用/恢复和数据最小化 fail closed | U9-01,F1-05 | ✅ 已完成 |
| U9-03 | 登录与 Session API | 登录、刷新、注销、密码修改/重置；短期访问能力、旋转 refresh、重放检测、限流/锁定、统一脱敏错误和全 Session 吊销 | U9-02,F1-10 | ✅ 已完成 |
| U9-04 | Tauri 登录与账号状态 UI | 未登录只显示登录/恢复状态，不挂载业务工作台；Token 与 refresh secret 仅由 Rust 私有存储持有，React 只接收安全账号投影；覆盖加载、失败、锁定、离线、注销和重启恢复 | U9-03,F1-08,I2-09 | ✅ 已完成 |
| U9-05 | 登录账号自动绑定设备 | 登录成功后复用 I2 设备密钥证明，把 Installation 原子绑定当前账号并签发/轮换设备凭据；不使用配对码、设备轮询或后台逐设备审批，跨账号、重放、并发绑定和已吊销设备 fail closed | U9-03,U9-04,I2-14 | ✅ 已完成 |
| U9-06 | 账号与设备管理验收 | 认证运维创建/停用/重置 Demo 账号，用户可修改密码、注销并查看/吊销自己的设备；真实 Tauri App 完成登录、自动绑定、重启、断网、Session 失效、账号停用和设备吊销纵向验收 | U9-02..U9-05 | ✅ 已完成 |

## 15. Wave 10：云端客户 Demo

> 本 Wave 的实际部署、域名和云资源操作需要用户在执行时明确授权；U9-01～U9-06 未完成时不得向客户交付 Demo。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| C10-01 | Demo 部署设计 | 单实例 Control Plane、PostgreSQL、HTTPS、域名、备份和资源上限 | U9-06 | ✅ 已完成 |
| C10-02 | Control Plane Docker | 锁定镜像、非 root、健康检查、优雅停止和版本标签 | F1-14,C10-01 | ✅ 已完成 |
| C10-03 | 云 PostgreSQL | 最小权限、迁移、备份、恢复演练和网络隔离 | C10-01 | ✅ 已完成 |
| C10-04 | HTTPS/域名 | TLS、反代、请求大小/超时/限流和安全头 | C10-02 | ✅ 已完成 |
| C10-05 | Secret 管理 | DB、账号 Session 签发、密码 Pepper 与设备签发密钥；不进入镜像、Git 或日志 | C10-02,C10-03,U9-03 | ✅ 已完成 |
| C10-06 | Demo 账号初始化与运维 | 通过 U9 认证运维入口创建、停用和重置 Demo 账号，固定最小权限、审计和应急全 Session/设备吊销 | U9-06,C10-05 | ✅ 已完成 |
| C10-07 | App Demo Profile | 签名 baseUrl/允许域名；local/demo 账号 Session、设备凭据和数据隔离 | F1-09,C10-04,C10-06 | ✅ 已完成 |
| C10-08 | 云端部署 | 执行迁移、启动单实例、健康检查；不自动扩容多副本 | C10-03..C10-07 | ✅ 已完成 |
| C10-09 | 云端协议回归 | 同一 OpenAPI/fixtures，App 只切 baseUrl，无业务代码变化 | C10-08 | ✅ 已完成 |
| C10-10 | 网络/重启恢复 | 服务器重启、网络抖动、Executor 重连和事件续传 | C10-09,H8-07 | ✅ 已完成 |
| C10-11 | 账号与设备吊销演示 | 停用账号或吊销一个设备立即失效且不影响其他有效账号/设备；无匿名业务写入口 | C10-10 | ✅ 已完成 |
| C10-12 | 客户视角 Demo 验收 | 安装→账号登录→设备自动绑定→工作台→扫码→预览→动作→结果→接管 | C10-11 | ✅ 已完成 |
| C10-13 | 部署/回滚手册 | 部署、迁移、备份、恢复、账号/设备吊销、回滚和紧急停服 | C10-12 | ✅ 已完成 |

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

## 20. 独立任务记录

任务的 RED、GREEN、真实边界、失败矩阵、清理、文档与遗留证据统一存放在 [`docs/development/`](./development/) 下的 `<任务ID>.md` 文件中。本路线图只维护任务定义、依赖、状态和当前下一步；会话恢复时只读取当前任务文件，不读取全部历史记录。

## 21. 当前下一步

严格按顺序：

1. `P9-02`（✅ 已完成）：2026-07-24 在 Windows 11 x86_64 实体机执行 `pnpm --dir frontend test:p9-02-windows-executor`，真实候选 365 个文件、148 个 PE 文件、157,924,248 bytes，PE/依赖、Manifest、冻结入口、UIAutomation 和 Job Object 清理全部通过；
2. `P9-04`（🔍 待验收）：production-mode NSIS/currentUser 配置、只读 Executor Resources、非提权隔离安装/卸载/注册表/普通签名 runner 与 CI 已就绪；稍后在 Windows 实体机执行 `pnpm --dir frontend test:p9-04-windows-package`，正式发布再补 Authenticode；
3. `P9-05`（🔍 待验收）：统一完整包 auditor 已在真实 macOS App/DMG 通过并接入 P9-04 Windows 安装根；稍后在 Windows 执行 `pnpm --dir frontend test:p9-05-package-audit` 补最终统计；
4. `P9-06`（🔍 待设备验收）：Developer ID/notarization/Gatekeeper、fresh 用户级安装、零 Python 环境、Executor/Chrome/Edge/私有 Profile、扫码/browse/结果和双启动恢复的显式 runner 已就绪；等待正式 DMG、授权账号及可交付本地服务/首次设备注册链后执行，不能用 ad-hoc 或人工勾选冒充；
5. `P9-07`（🔍 待设备验收）：正式同 signer Authenticode、HKCU-only、零 Python、至少 125% DPI、Job-owned Executor/私有浏览器、主 PID 强停恢复、正式卸载和最小 ACL 证据 runner 已就绪；等待 Windows/签名包/授权账号/本地服务注册链补事实；
6. `P9-08`（✅ 已完成）：三端精确兼容矩阵、App `/version` 启动协商、Executor 包/Hello 双边降级拒绝和全量门禁已完成；
7. `P9-09`（🔍 待验收）：14 条最终验收的可执行报告已完成；当前 7 条自动化确认、4 条待授权真实平台、3 条待正式双平台设备/包，未达到 14/14 前不得改绿；
8. `U9-01`（✅ 已完成）：客户 Demo 账号生命周期、登录/恢复、opaque Session、不可变设备归属、停用/吊销、审计及 12 类威胁已冻结为可执行契约；
9. `U9-02`（✅ 已完成）：User/canonical login、固定 Argon2id + Pepper、三态 revision、三张最小 PostgreSQL 表、并发单赢家和 append-only 审计已完成；
10. `U9-03`（✅ 已完成）：产品登录、刷新、注销、改密/运维恢复、短期 access/旋转 refresh、重放整族吊销、keyed 限流/临时锁和统一脱敏错误已完成；
11. `U9-04`（✅ 已完成）：customer-demo 外层账号门禁、Rust 私有账号 Session vault、登录/恢复/改密/注销、离线 fail-closed、重启 refresh 和隐藏真实 Tauri 未登录边界已完成；React 不接收 bearer secret；
12. `C10-07`（✅ 已完成）：签名 Demo origin/allowlist 原生信任边界、HTTPS/WSS Transport 与 local/demo 全私有数据 namespace 隔离已完成；下一工程任务为 `C10-08`；
13. `H8-22/P9-03`（🔍 待验收）：Windows 普通包更新矩阵、Developer ID/notarization 与 Authenticode 在受控实机/签名环境补验，不阻塞上述工程任务；
14. `A7-16/A7-17`、`D6-16`、`B5-15`（🔍 待真实账号）：只在用户明确指定的自有/授权目标上补真实平台证据；账号不可用时跳过，不制造外部副作用；`B5-02` 的 Chrome FinderInfo 与未安装 Edge 也继续作为设备补验，不混入离线任务。
