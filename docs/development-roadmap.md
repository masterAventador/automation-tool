# 自动化运营工具任务级开发路线图

> 文档性质：后续开发唯一执行台账
> 建立日期：2026-07-18
> 当前阶段：Wave 2 安装实例认证与跨进程协议
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

快照日期：2026-07-18。

| 范围 | 当前结果 |
| --- | --- |
| 竞品分析 | `✅` 已完整阅读并转为能力地图；动态长期稳定性仍需我们自己的真实账号验证 |
| 产品决策 | `✅` Tauri-only、无产品登录 UI、RPA 优先、外部浏览器 + 独立 Profile |
| 后端决策 | `✅` 独立 FastAPI Control Plane；开发本机、Demo 云端；PostgreSQL 从第一天使用 |
| 本地执行决策 | `✅` Python Local Executor 永远在用户电脑，随 Tauri 打包 |
| 项目规则 | `✅` 已从 `agent-platform` 筛选、改写并写入仓库 |
| 产品/架构文档 | `✅` 已建立产品、工程结构、前端和后端权威文档 |
| 任务级开发台账 | `✅` 已建立里程碑、失败矩阵、完成定义、任务和实时状态 |
| 任务级路线图 | `✅` 本文件已建立 |
| 产品代码 | `🚧` Wave 1 工程闭环与六类稳定资源 ID 已完成，正在实现安装实例认证与跨进程协议；RPA 功能尚未开始 |
| 稳定资源 ID | `✅` installation/executor/task/execution attempt/action/artifact 六类规范 UUIDv4 值对象与非法值矩阵已验证 |
| 本地 PostgreSQL | `✅` 18.4 开发/测试双容器、健康检查、loopback 端口和独立存储已验证 |
| 数据访问与迁移 | `✅` SQLAlchemy asyncio/asyncpg、事务 session、Alembic 空库升级/回滚、Installation schema/约束和脱敏连接错误已验证 |
| Installation 持久化 | `✅` 32 字节公钥、active/revoked、revision CAS、吊销时间、唯一性和时间一致性约束已在 PostgreSQL 18.4 验证 |
| Demo Bootstrap | `✅` 最多 7 天、精确 Demo 环境、唯一 installation.register purpose 和业务 API 拒绝模型已验证 |
| Installation 注册 | `✅` 离线签名 Bootstrap、最长 5 分钟的一次性 challenge、设备 Ed25519 证明和 PostgreSQL 原子消费已验证 |
| 设备凭据 | `✅` `atdc1` 一次返回、摘要持久化、版本历史、最小 session scope、原子轮换/吊销和并发单赢家已验证 |
| 桌面 UI 资产 | `✅` React 19、TypeScript 5.9、Vite 8、Ant Design 6 和 pnpm 冻结锁文件基线已验证 |
| Tauri 桌面壳 | `✅` v2 真实 macOS 窗口、生产 CSP、零权限 Capability、Cargo 锁文件与桌面构建已验证 |
| 设备身份与凭据存储 | `✅` Ed25519 首启生成、Rust 管理的 `app_data_dir` 私有文件、长期凭据替换/删除、React/IPC 零暴露和无系统钥匙串授权已验证 |
| 前端 Transport | `✅` 生产 `main.tsx` 已经真实 Tauri IPC/Rust 桥调用 Health；Rust 固定 origin/operation allowlist、凭据注入与严格响应边界已验证，注册/凭据/Session 纵向链路不向 React 暴露秘密 |
| Executor v1 Envelope | `✅` 24 种消息 Pydantic 判别联合、显式版本、用途隔离 UUIDv4、UTC deadline、幂等键、正序号、安全 payload 与固定解析错误已验证 |
| UI Harness | `✅` Playwright Chromium 覆盖 ready/unavailable/flaky；正式 dist 排除 Harness 与测试 Adapter 已验证 |
| 持续集成 | `✅` Backend、Frontend、Rust 分层质量门禁，以及 macOS/Windows 真实桌面构建与 Tauri 冒烟矩阵已建立 |
| Git 仓库 | `✅` 已初始化 `main` 分支，规划基线随 R0-10 提交 |
| GitHub 私有仓库 | `✅` `masterAventador/automation-tool` 已创建为 `PRIVATE`，`main` 已推送 |
| 本机工具链 | `✅` macOS arm64、Rust/Clippy/Rustfmt、Node/pnpm、uv Python 3.12、Docker、Chrome、Xcode 签名链和 ffmpeg-full 可用 |
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
| I2-11 | 协议 Schema/Fixtures | 有效/无效样例覆盖未知字段、敏感数据、非法时间和枚举 | I2-10 | ⬜ 未开始 |
| I2-12 | Rust/TS 协议一致性 | 三语言回放同一 fixtures，结论一致 | I2-11,F1-11 | ⬜ 未开始 |
| I2-13 | Executor WebSocket 认证 | installation/executor/版本绑定；旧连接、冒充和吊销测试 | I2-07,I2-10 | ⬜ 未开始 |
| I2-14 | 安装实例吊销闭环 | 吊销阻止 App 请求、新任务和 Executor 连接；UI 明确诊断 | I2-09,I2-13 | ⬜ 未开始 |

## 8. Wave 3：Control Plane 任务与事件闭环

### 目标

使用 FakeExecutor 跑通与真实 RPA 相同的任务状态、命令、事件和 UI。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| T3-01 | 任务状态机 | 全部合法/非法转换、终态、CANCELLING 和 OUTCOME_UNCERTAIN 单元测试 | I2-01 | ⬜ 未开始 |
| T3-02 | Task 数据模型 | tasks/revision/installation scope/Alembic/仓储集成测试 | T3-01,I2-02 | ⬜ 未开始 |
| T3-03 | Attempt/Action 模型 | execution attempt、action 状态与唯一约束 | T3-02 | ⬜ 未开始 |
| T3-04 | Event 模型 | `(task_id, sequence)` 唯一、版本、安全消息和快照投影 | T3-02 | ⬜ 未开始 |
| T3-05 | Command/Outbox 模型 | 持久命令、幂等、deadline、投递/确认状态 | T3-03 | ⬜ 未开始 |
| T3-06 | 创建任务 API | idempotency、参数校验、installation 隔离 | T3-02,I2-09 | ⬜ 未开始 |
| T3-07 | 任务查询 API | 列表/详情/分页；跨 installation 按不可见处理 | T3-06 | ⬜ 未开始 |
| T3-08 | Executor Connection Registry | 心跳、在线、旧连接替换和单实例 API 约束 | I2-13 | ⬜ 未开始 |
| T3-09 | 命令投递服务 | task offer/ack、重连恢复、过期和重复投递 | T3-05,T3-08 | ⬜ 未开始 |
| T3-10 | FakeExecutor | 无副作用回放全部任务与控制事件；不放宽生产状态机 | T3-09 | ⬜ 未开始 |
| T3-11 | 事件接收与收敛 | sequence、重复、缺口、迟到事件和 revision CAS | T3-04,T3-10 | ⬜ 未开始 |
| T3-12 | SSE 事件流 | last-event/断线/重连/终态关闭；事件先落库后推送 | T3-11 | ⬜ 未开始 |
| T3-13 | 暂停/恢复 API | 命令与确认语义；未确认不能提前改状态 | T3-09,T3-11 | ⬜ 未开始 |
| T3-14 | 取消/紧停 API | CANCELLING、确认、结果不确定和幂等 | T3-13 | ⬜ 未开始 |
| T3-15 | 前端 Query/事件 Reducer | 快照权威、事件去重、缺口回拉和版本降级 | T3-07,T3-12 | ⬜ 未开始 |
| T3-16 | 工作台页面 | 当前任务、最近任务、后端/Executor 状态和全局紧停 | T3-15 | ⬜ 未开始 |
| T3-17 | 新建任务骨架 | 抖音搜索曝光模板字段和客户端/服务端一致校验 | T3-06,T3-15 | ⬜ 未开始 |
| T3-18 | 运行详情页面 | 状态、进度、时间线、目标结果和控制按钮 | T3-13,T3-15 | ⬜ 未开始 |
| T3-19 | UI Harness E2E | 创建→运行→暂停→恢复→取消/成功→刷新恢复 | T3-16,T3-17,T3-18 | ⬜ 未开始 |
| T3-20 | Control Plane 重启恢复 | PostgreSQL 保持任务/命令/事件，FakeExecutor 重连收敛 | T3-11,T3-19 | ⬜ 未开始 |

## 9. Wave 4：Tauri 与 Local Executor 生命周期

### 目标

把 FakeExecutor 替换为真实 Python 子进程，但暂不操作平台。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| E4-01 | 审计旧 local_executor | 列出可迁移进程/协议逻辑和必须删除的 tenant/Core 依赖 | R0-12,I2-10 | ⬜ 未开始 |
| E4-02 | Executor Python 入口 | stdin bootstrap、健康、信号和出站连接最小进程 | E4-01,I2-13 | ⬜ 未开始 |
| E4-03 | PyInstaller onedir PoC | macOS/Windows 各能启动；Playwright 依赖暂不加入 | E4-02 | ⬜ 未开始 |
| E4-04 | Executor Manifest | 版本、平台、架构、大小、SHA-256 和 Ed25519 签名 | E4-03 | ⬜ 未开始 |
| E4-05 | Rust 包验证 | 签名/摘要/平台/架构/防降级；错误包 fail closed | E4-04 | ⬜ 未开始 |
| E4-06 | stdin 随机认证 | 256-bit 会话令牌不进 argv/env/log/响应 | E4-02,E4-05 | ⬜ 未开始 |
| E4-07 | Rust ExecutorManager | start/status/invoke/stop，单实例和并发线性化 | E4-05,E4-06 | ⬜ 未开始 |
| E4-08 | 进程监管 | 后台检测退出、有界重启预算、显式停止不重启 | E4-07 | ⬜ 未开始 |
| E4-09 | 超时与进程树清理 | Unix process group、Windows Job Object、挂起调用终止 | E4-07 | ⬜ 未开始 |
| E4-10 | stderr 脱敏限界 | 凭据/私有路径脱敏；行数、单行和总大小上限 | E4-07 | ⬜ 未开始 |
| E4-11 | Executor 本机 SQLite | command/idempotency/checkpoint/outbox 最小账本与迁移 | E4-02 | ⬜ 未开始 |
| E4-12 | 真实协议回放 | Control Plane 向真实 Executor 下发无副作用任务并收事件 | E4-08,E4-11,T3-20 | ⬜ 未开始 |
| E4-13 | PlatformAdapter 接入 | React 能看状态、重启、诊断和紧停，不直接连 Executor | E4-07,T3-16 | ⬜ 未开始 |
| E4-14 | Tauri 生命周期 E2E | 启动/调用/挂起/崩溃/重启/停止/退出清理 | E4-09,E4-13 | ⬜ 未开始 |
| E4-15 | 正式包测试能力审计 | 生产包不含 WebDriver、测试命令、测试 Sidecar 或调试端口 | E4-14 | ⬜ 未开始 |

## 10. Wave 5：外部浏览器与抖音登录

### 目标

打开外部浏览器，首次扫码一次后持久复用 App 独立 Profile。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| B5-01 | 审计旧 browser_session | 提取私有目录、Profile、状态机和注销逻辑；排除旧账号/RBAC | R0-12,E4-11 | ⬜ 未开始 |
| B5-02 | macOS 浏览器发现 | Chrome/Edge 标准应用、签名/Bundle ID allowlist、路径失效测试 | B5-01 | ⬜ 未开始 |
| B5-03 | Windows 浏览器发现 | 注册表/标准路径、签名/产品 allowlist、路径失效测试 | B5-01 | ⬜ 未开始 |
| B5-04 | 浏览器选择设置 | 用户选择受支持浏览器；不能选任意可执行文件 | B5-02,B5-03 | ⬜ 未开始 |
| B5-05 | 私有 Profile 目录 | 平台/UUID 规范路径、权限、拒绝 symlink、原子创建 | B5-01 | ⬜ 未开始 |
| B5-06 | Profile 单实例锁 | 同一 Profile 多任务/多进程竞争必须拒绝 | B5-05 | ⬜ 未开始 |
| B5-07 | Playwright 打包 PoC | PyInstaller Executor 中启动系统 Chrome/Edge headed context | E4-03,B5-04 | ⬜ 未开始 |
| B5-08 | BrowserRuntime | 启动、页面、窗口、超时、关闭和进程清理接口 | B5-06,B5-07 | ⬜ 未开始 |
| B5-09 | 抖音 Session 检测 | healthy/expired/missing/risk/unknown；使用页面状态而非 Cookie 上传 | B5-08 | ⬜ 未开始 |
| B5-10 | 抖音扫码流程 | login_required、外部窗口、二维码过期、重新检查 | B5-09 | ⬜ 未开始 |
| B5-11 | 人工接管 | 验证码/滑块/风控进入 handoff，不自动处理 | B5-10 | ⬜ 未开始 |
| B5-12 | Session 健康上报 | Control Plane 只存平台/状态/revision/时间，不存 Cookie | B5-09,T3-11 | ⬜ 未开始 |
| B5-13 | 平台状态页面 | 查看登录健康、打开处理、重新检查和注销 | B5-10,B5-12 | ⬜ 未开始 |
| B5-14 | 安全注销 | 先阻止新任务、停关联执行、再删除平台 Profile | B5-06,B5-13 | ⬜ 未开始 |
| B5-15 | 登录复用验收 | App/Executor/浏览器重启后不重扫；失效后正确接管 | B5-14 | 🔍 待真实账号 |
| B5-16 | 默认 Profile 隔离审计 | 测试和运行证据证明未读用户默认 Chrome User Data | B5-15 | ⬜ 未开始 |

## 11. Wave 6：抖音目标发现与用户预览

### 目标

完成“关键词搜索 → 有界目标发现 → 去重/黑名单 → 用户预览确认”，不产生评论或私信。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| D6-01 | 抖音页面版本模型 | page version、已知入口、未知版本 fail closed | B5-09 | ⬜ 未开始 |
| D6-02 | 页面对象基础 | 搜索入口、结果列表、弹窗和登录跳转集中封装 | D6-01 | ⬜ 未开始 |
| D6-03 | 关键词校验 | 长度、空白、控制字符、任务上限和服务端一致规则 | T3-17 | ⬜ 未开始 |
| D6-04 | 搜索执行 | 打开页面、输入、提交、等待结果；网络慢/超时测试 | D6-02,D6-03 | ⬜ 未开始 |
| D6-05 | 有界滚动 | 最大轮次、最大目标、无新增停止和取消检查点 | D6-04 | ⬜ 未开始 |
| D6-06 | Candidate 模型 | 稳定去重键、最小摘要、来源和页面 revision | D6-05,I2-10 | ⬜ 未开始 |
| D6-07 | 目标隐私裁剪 | 不上传非必要个人信息、页面原文或绝对链接凭据 | D6-06 | ⬜ 未开始 |
| D6-08 | 黑名单/去重 | 本任务去重、历史窗口去重和黑名单原因 | D6-06 | ⬜ 未开始 |
| D6-09 | Target 数据库 | task_targets、唯一约束、分页和 installation 隔离 | D6-06,T3-02 | ⬜ 未开始 |
| D6-10 | Discover 命令闭环 | Control Plane 投递、Executor 上报、任务状态收敛 | D6-05,D6-09,E4-12 | ⬜ 未开始 |
| D6-11 | 目标预览 API | 列表、排除、确认 revision；过期候选拒绝 | D6-09 | ⬜ 未开始 |
| D6-12 | 目标预览 UI | 摘要、排除、去重/黑名单标记和确认 | D6-11,T3-18 | ⬜ 未开始 |
| D6-13 | 未确认副作用守卫 | 没有确认 command 时 Executor 无法收到 action | D6-10,D6-11 | ⬜ 未开始 |
| D6-14 | 页面漂移诊断 | 未知元素时保存受限 Artifact 并进入 handoff | D6-02,E4-10 | ⬜ 未开始 |
| D6-15 | Fake 页面回归样例 | 正常、空结果、弹窗、登录跳转、未知版本和无限滚动 | D6-14 | ⬜ 未开始 |
| D6-16 | 真实目标发现验收 | 受控抖音账号完成搜索与预览，确认无外部副作用 | D6-15 | 🔍 待真实账号 |

## 12. Wave 7：抖音受控评论与主动私信

### 目标

在自有/授权目标上完成真实动作，具备服务端授权、本机硬限制和结果不确定语义。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| A7-01 | 风险策略领域模型 | 平台/动作/安装实例级最小间隔、任务/日上限、连续失败阈值 | T3-01 | ⬜ 未开始 |
| A7-02 | 服务端计数与并发授权 | PostgreSQL 原子授权；并发不能突破上限 | A7-01,T3-03 | ⬜ 未开始 |
| A7-03 | ActionAuthorization | action/target/attempt/deadline/idempotency 签名或 MAC | A7-02,I2-10 | ⬜ 未开始 |
| A7-04 | Executor 本机硬下限 | 服务器不能放宽最小间隔、任务上限和紧停 | A7-03,E4-11 | ⬜ 未开始 |
| A7-05 | 文案校验 | 长度、空内容、控制字符、敏感模式和模板变量 | A7-01 | ⬜ 未开始 |
| A7-06 | 高风险最终确认 | UI 展示目标、动作、文案和数量；确认 revision 防旧提交 | A7-03,D6-12 | ⬜ 未开始 |
| A7-07 | 副作用账本 | prepared/dispatched/verified/uncertain 本机原子状态 | A7-04,E4-11 | ⬜ 未开始 |
| A7-08 | 抖音评论 Page Object | 定位输入/提交/最终状态；页面变化 fail closed | D6-02,A7-05 | ⬜ 未开始 |
| A7-09 | 抖音私信 Page Object | 进入会话/输入/发送/最终状态；权限差异处理 | D6-02,A7-05 | ⬜ 未开始 |
| A7-10 | 只浏览动作 | 无发送副作用的目标访问，作为低风险基线 | D6-10 | ⬜ 未开始 |
| A7-11 | 评论动作执行 | 授权校验→账本→点击→最终验证→结构化 receipt | A7-07,A7-08 | ⬜ 未开始 |
| A7-12 | 私信动作执行 | 授权校验→账本→发送→最终验证→结构化 receipt | A7-07,A7-09 | ⬜ 未开始 |
| A7-13 | 结果不确定处理 | dispatched 未 verified 先查询；无法确认不重放 | A7-11,A7-12 | ⬜ 未开始 |
| A7-14 | 连续失败熔断 | 达阈值停止新动作、打开 handoff、保持审计 | A7-02,A7-13 | ⬜ 未开始 |
| A7-15 | 目标级结果 UI | 成功/跳过/失败/不确定和证据摘要 | A7-13,T3-18 | ⬜ 未开始 |
| A7-16 | 评论真实验收 | 仅自有/授权目标；平台最终状态与服务端一致 | A7-15 | 🔍 待真实账号 |
| A7-17 | 私信真实验收 | 仅自有/授权目标；重复/断网/确认丢失覆盖 | A7-15 | 🔍 待真实账号 |
| A7-18 | 风险护栏对抗测试 | 篡改授权、超频、重放、取消竞态和服务器放宽均失败 | A7-16,A7-17 | ⬜ 未开始 |

## 13. Wave 8：恢复、诊断与 MVP 质量收口

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| H8-01 | 端到端暂停 | 安全检查点确认后才 PAUSED；运行中原子动作不伪装撤销 | A7-13,T3-13 | ⬜ 未开始 |
| H8-02 | 端到端取消 | CANCELLING→确认终态；最后动作不明进入 uncertain | H8-01,T3-14 | ⬜ 未开始 |
| H8-03 | 离线紧急停止 | 不依赖网络停止新副作用和完整进程树；重连补报 | E4-09,A7-07 | ⬜ 未开始 |
| H8-04 | App 崩溃恢复 | UI 恢复快照，任务不中断或重复 | T3-20,H8-01 | ⬜ 未开始 |
| H8-05 | Executor 崩溃恢复 | restart budget、账本对齐、dispatched 未验证处理 | E4-08,A7-13 | ⬜ 未开始 |
| H8-06 | Control Plane 重启恢复 | Executor 重连、命令/事件幂等、任务收敛 | T3-20,E4-12 | ⬜ 未开始 |
| H8-07 | 断网/抖动 | 停在安全点、事件 spool、重连续传、不烧无限重试 | H8-05,H8-06 | ⬜ 未开始 |
| H8-08 | 休眠/锁屏 | 时钟跳变、deadline、窗口不可用和恢复诊断 | H8-07 | ⬜ 未开始 |
| H8-09 | Local Artifact | 稳定 ID、摘要、媒体类型、大小、相对路径和权限 | E4-11 | ⬜ 未开始 |
| H8-10 | 诊断截图/Trace | 只在失败/用户开启时保存，数量/大小/时间上限 | H8-09,D6-14 | ⬜ 未开始 |
| H8-11 | 日志脱敏 | 服务端、Rust、Executor 全链路凭据/页面/路径泄漏测试 | E4-10,H8-10 | ⬜ 未开始 |
| H8-12 | 清理与磁盘治理 | 保留策略、磁盘满、清理失败、正在引用 Artifact 保护 | H8-09,H8-10 | ⬜ 未开始 |
| H8-13 | 诊断导出 | 用户主动导出受限包；不含 Cookie/完整私信/绝对私有路径 | H8-11,H8-12 | ⬜ 未开始 |
| H8-14 | 工作台指标 | 任务/动作成功、失败、接管、不确定；只读结构化事实 | A7-15,T3-16 | ⬜ 未开始 |
| H8-15 | 完整失败矩阵自动化 | 本台账第 4.1 节所有可自动化分支有测试或不适用理由 | H8-01..H8-14 | ⬜ 未开始 |
| H8-16 | 规格复审 | 从分叉点审查完整实现是否满足产品/MVP/文档 | H8-15 | ⬜ 未开始 |
| H8-17 | 代码质量复审 | 安全 fail-open、竞态、资源泄漏、假绿测试和平台差异 | H8-16 | ⬜ 未开始 |

## 14. Wave 9：双平台安装包与本地候选版

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| P9-01 | macOS Executor 构建 | PyInstaller onedir，依赖完整、无开发路径、签名准备 | H8-17 | ⬜ 未开始 |
| P9-02 | Windows Executor 构建 | PyInstaller onedir，Playwright/UIA 依赖和 Job Object 正常 | H8-17 | ⬜ 未开始 |
| P9-03 | macOS Tauri 候选包 | 签名、公证策略、最小 Capability/CSP | P9-01 | ⬜ 未开始 |
| P9-04 | Windows Tauri 候选包 | 签名、安装/卸载和最小系统权限 | P9-02 | ⬜ 未开始 |
| P9-05 | 正式包内容审计 | 无 WebDriver、调试端口、测试凭据、真实日志/Profile/素材 | P9-03,P9-04 | ⬜ 未开始 |
| P9-06 | macOS 干净安装 | 无 Python 前置；打开即用；Chrome/Edge/扫码/任务/恢复 | P9-03,P9-05 | 🔍 待设备验收 |
| P9-07 | Windows 干净安装 | 无 Python 前置；同上；DPI/杀进程/卸载行为 | P9-04,P9-05 | 🔍 待设备验收 |
| P9-08 | 版本兼容/降级 | App/Executor/Control Plane 兼容矩阵，错误版本 fail closed | P9-06,P9-07 | ⬜ 未开始 |
| P9-09 | 本地 MVP 最终验收 | 产品规划 14 条 MVP 验收全部通过并记录证据 | P9-08 | ⬜ 未开始 |

## 15. Wave 10：云端客户 Demo

> 本 Wave 的实际部署、域名和云资源操作需要用户在执行时明确授权。

| ID | 任务 | 交付物与完成定义 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| C10-01 | Demo 部署设计 | 单实例 Control Plane、PostgreSQL、HTTPS、域名、备份和资源上限 | P9-09 | ⬜ 未开始 |
| C10-02 | Control Plane Docker | 锁定镜像、非 root、健康检查、优雅停止和版本标签 | F1-14,C10-01 | ⬜ 未开始 |
| C10-03 | 云 PostgreSQL | 最小权限、迁移、备份、恢复演练和网络隔离 | C10-01 | ⬜ 未开始 |
| C10-04 | HTTPS/域名 | TLS、反代、请求大小/超时/限流和安全头 | C10-02 | ⬜ 未开始 |
| C10-05 | Secret 管理 | DB、签发密钥、bootstrap；不进入镜像、Git 或日志 | C10-02,C10-03 | ⬜ 未开始 |
| C10-06 | Demo Bootstrap 批次 | 限时、限环境、限注册次数、可吊销和审计 | I2-14,C10-05 | ⬜ 未开始 |
| C10-07 | App Demo Profile | 签名 baseUrl/允许域名；local/demo 凭据隔离 | F1-09,C10-04,C10-06 | ⬜ 未开始 |
| C10-08 | 云端部署 | 执行迁移、启动单实例、健康检查；不自动扩容多副本 | C10-03..C10-07 | ⬜ 未开始 |
| C10-09 | 云端协议回归 | 同一 OpenAPI/fixtures，App 只切 baseUrl，无业务代码变化 | C10-08 | ⬜ 未开始 |
| C10-10 | 网络/重启恢复 | 服务器重启、网络抖动、Executor 重连和事件续传 | C10-09,H8-07 | ⬜ 未开始 |
| C10-11 | 安装实例吊销演示 | 吊销一个 Demo 不影响其他安装；无匿名业务写入口 | C10-10 | ⬜ 未开始 |
| C10-12 | 客户视角 Demo 验收 | 安装→打开即用→扫码→预览→动作→结果→接管 | C10-11 | ⬜ 未开始 |
| C10-13 | 部署/回滚手册 | 部署、迁移、备份、恢复、吊销、回滚和紧急停服 | C10-12 | ⬜ 未开始 |

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
- 遗留：I2-05 在 challenge/response 注册 API 中承载并验证签名 claims；bootstrap 批次次数、撤销、持久化、审计和 Secret 管理由 C10-05/C10-06 实现，本任务不提前创建第二套 token 系统

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
- 遗留：I2-06 在注册成功后签发版本化、可撤销、可轮换且最小 scope 的设备凭据；C10-06 再增加 bootstrap 批次次数、吊销、持久化和审计，本任务不冒充完整账号体系

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
- 边界类型：message/correlation/installation/executor/task/attempt 使用不同运行时类型的 canonical 小写 RFC 4122 UUIDv4；RFC3339 时间必须显式 UTC 且 `deadline_at > sent_at`；幂等键只允许 1～128 字符规范字符集；sequence 是 `1..2^63-1` 的 strict integer，不接受 bool、float 或字符串强转
- 资源与隐私：正式解析器只接受最大 32 KiB 的 UTF-8 JSON object，拒绝重复 key；payload 最大 16 KiB、深度 8、单集合 64 项、字符串 4096 字符，递归拒绝 Cookie/Token/密码/私钥/凭据字段与赋值文本、Bearer、私有绝对路径、`file://`、inline data URI、控制/双向字符、NaN/Infinity。解析失败只有固定 `Invalid Executor protocol message`，不回显输入，也不保留底层 cause/context
- 旧项目取舍：只吸收显式版本、deadline、幂等、判别联合和安全文本的通用经验；未迁移旧 `tenant_id`、Core capability/governance、social-operations 扩展、同步 stdio request/response 或其领域 payload。stdin 仍只留给 E4-02/E4-06 一次性本机 bootstrap，正式消息通道归 I2-13 WebSocket
- 生产同路径：本任务只建立两进程将共同调用的纯协议解析入口，没有网络、数据库、App UI、Executor 进程或外部副作用，因此真实 Tauri/PostgreSQL/RPA 验收不适用；I2-11 用公共 fixtures 固化跨语言 wire 结论，I2-12 由 Python/Rust/TypeScript 回放同一 fixtures，I2-13 才通过真实 WebSocket 使用该入口
- 文档：同步根/Backend README、后端架构、工程结构、本路线图快照、任务状态、完成记录和当前下一步
- 遗留：generic payload 容器只承担 Envelope 级资源/隐私下限，具体消息 payload 必须随 T3/E4 对应任务改为严格类型；I2-11 先导出 JSON Schema 与有效/无效 fixtures，不能把 generic payload 当成长期业务契约

## 21. 当前下一步

严格按顺序：

1. `I2-11`：为协议建立跨语言 Schema 与有效/无效 fixtures；
2. `I2-12`：由 Python、Rust、TypeScript 回放同一份协议 fixtures；
3. 按台账顺序持续执行 Wave 2、Wave 3 和 Wave 4，不在单个工程任务后停止。
