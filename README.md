# automation-tool

面向运营人员的 Tauri 桌面自动化工具。产品优先级固定为：

```text
RPA 运营 > 内容生产与分发 > AI 员工与工作流
```

当前处于第一期 MVP 实施阶段。Wave 1～Wave 3 已完成，Wave 4 的 macOS 工程与正式包门禁已跑通；Wave 5 已完成旧会话审计和 macOS Chrome 真实受信发现，Edge 因本机未安装保留实机验收，当前进入 Windows Chrome/Edge 受信发现。

## 第一阶段

- 只有 Tauri 桌面客户端，不建设或部署 Web 产品；
- 用户打开 App 后直接使用，不提供产品注册或登录页面；
- 首个闭环只做抖音：平台登录、目标搜索、预览、受控评论/主动私信、人工接管和恢复；
- 抖音、小红书等平台页面在外部 Chrome/Edge 窗口运行；
- 使用 App 独立运营 Profile，不接管用户默认浏览器 Profile；
- 业务 FastAPI 后端独立部署：开发时本机启动，客户 Demo 时部署云端；
- Python Local Executor 随 App 运行在用户电脑，负责浏览器、微信、OCR 和本地文件。

## 架构

```text
Tauri App ──HTTP/SSE──> Python/FastAPI Control Plane ──> PostgreSQL
    │
    └── Python Local Executor ──> Chrome/Edge / 微信
```

开发与客户 Demo 使用同一套 Control Plane 代码、数据库迁移和 API；App 通过受控 Profile 切换 `baseUrl`。

## 文档

- [竞品完整分析](docs/dt-ai-helper-competitive-analysis.md)
- [产品规划](docs/product-plan.md)
- [整体工程结构](docs/project-structure.md)
- [前端架构](docs/frontend-architecture.md)
- [后端架构](docs/backend-architecture.md)
- [任务级开发路线图与进度台账](docs/development-roadmap.md)
- [项目协作规则](CLAUDE.md)

## 当前状态

- 产品、架构、MVP 和任务级开发台账已完成；
- 仓库规则已从旧 `agent-platform` 项目筛选并改写；
- Backend 已建立 uv/Python 3.12、src layout、Pytest、Ruff 和 Mypy 基线；
- Control Plane 已具备独立应用工厂、lifespan、请求关联 ID、不泄密错误信封，以及 Health/Version 和协议兼容响应；
- PostgreSQL 18.4 开发库与测试库使用独立容器、凭据和存储，Compose 凭据缺失时 fail closed；
- SQLAlchemy 使用 asyncpg、事务作用域 session 和连接预检；Alembic 已验证真实空库升级与回滚；数据库不可用时 Health 返回脱敏、可重试的 503；
- Installation 已具备 PostgreSQL 表、32 字节设备公钥、active/revoked 状态、revision CAS、吊销时间、UUIDv4/唯一性/时间一致性约束和可回滚迁移；
- Installation 注册已具备离线 Ed25519 签名 Bootstrap claims、5 分钟一次性 challenge、设备私钥持有证明和 PostgreSQL 原子消费；重放、过期、冒充、跨环境、并发和重复设备均 fail closed；
- 注册成功会在同一事务签发 `atdc1` 长期设备凭据；服务端只保存 32 字节摘要和版本历史，不保存明文凭据或设备私钥；凭据只具备 `device.session.exchange` scope，并支持原子轮换、自吊销、旧版本立即失效和并发单赢家；
- `POST /api/v1/device-sessions` 可把当前长期凭据换成 5 分钟 `atds1` 短期票据；每张票只具备 App Control Plane 或 Executor 连接能力之一，数据库只保存摘要和精确父凭据版本绑定，父凭据/Installation 失效会立即拒绝既有票据；
- 服务端运维 CLI 可用 revision CAS 原子吊销 Installation、active 长期凭据与全部 Session；`GET /api/v1/installations/current` 和可复用业务守卫强制 `app.control-plane` scope；Task 创建、列表和详情 API 均复用该守卫，客户端不能自报 Installation scope；
- Frontend 已锁定 React、TypeScript、Vite、Ant Design、TanStack Query 和 Zod，严格类型、Lint、冻结安装与生产资产构建通过；Vite 仅绑定 loopback 且仓库没有 Web 部署入口；
- Tauri v2 已具备真实 macOS 主窗口、生产 CSP、零 IPC 权限 Capability、桌面图标与 Cargo 锁文件，Rust/Clippy/无 bundle 构建通过；
- Tauri 首启设备身份已在 Rust 内生成 Ed25519 密钥：私钥和长期设备凭据只进入 `app_data_dir` 下由 Rust 管理的 App 私有文件，不进入 React、Tauri IPC、`localStorage` 或普通配置，也不调用系统钥匙串；
- App 打开后直接进入真实 RPA 运营工作台，展示当前/最近任务、运行状态和基础指标；Control Plane 不可用与 Installation 已吊销分别显示脱敏诊断和重试状态，页面不存在产品登录或注册入口；
- BaseUrl Profile 使用 Zod fail closed：local 固定为 `127.0.0.1:8765`，demo 强制 HTTPS 且主机必须精确命中构建允许列表；
- ControlPlaneTransport 已接入正式 Tauri IPC/Rust 网络桥：生产入口由真实 App 发起 Health 请求；Rust 侧以固定 origin、封闭 operation allowlist、禁止重定向/代理、请求与响应大小/时间上限和关联 ID 调用 Control Plane，不暴露任意 URL 代理；
- Installation 注册、长期凭据轮换/吊销、两类短期 Session，以及 Task 幂等创建、分页列表、详情、事件 SSE、暂停/恢复和取消/紧停，已通过测试版真实隐藏 Tauri App → 正式 Rust 桥 → 真实 FastAPI/PostgreSQL 纵向验收；设备私钥、Bootstrap、长期凭据和短期票据全程留在 Rust，React/IPC 响应只得到公开结果；
- FastAPI OpenAPI 3.1 快照与 `openapi-typescript` DTO 已覆盖 Health/Version、Installation 注册/访问、设备凭据生命周期、短期 Session 交换、工作台运行状态，以及 Task 创建/列表/详情/事件 SSE/暂停/恢复/取消/紧停，后端/前端分别具备确定性漂移检查；
- Playwright UI Harness 已覆盖工作台、服务不可用、重试恢复，以及创建→暂停→恢复→取消、独立成功与刷新恢复；正式 `dist/` 扫描证明不包含 Harness 页面或测试 Adapter，代表流程另由隐藏真实 Tauri App 从产品入口验收；
- Control Plane 已在隐藏真实 App 运行期间完成同库停服/重启验收：PostgreSQL 保留 Task/Attempt/Command/Event/定义，FakeExecutor 有界自动重连并消费原 pending Command，App 刷新经历不可用页后恢复权威取消终态；
- 桌面端已建立 Vitest、Playwright、Rust、WebdriverIO 四层统一门禁；WebdriverIO 使用 embedded provider 在真实 macOS Tauri/WKWebView 中验证无登录工作台和原生窗口标签，测试插件只由 `desktop-e2e` 特性启用；
- GitHub Actions 已建立 Backend、Frontend、Rust 三路质量门禁，以及 macOS/Windows 真实桌面构建与 Tauri 冒烟矩阵；所有第三方 Action 固定完整提交 SHA，工作流只读且不发布、不部署；
- installation、executor、task、execution attempt、action 和 artifact 已使用六种不可混用的规范 UUIDv4 领域类型；
- Task 纯领域状态机已锁定 16 个状态、5 个无出边终态和全部显式转换；256 个状态对均已穷举，取消先进入 `CANCELLING`、取消/完成竞态按最终事实收敛，`OUTCOME_UNCERTAIN` 不可从执行前阶段伪造；
- `tasks` 已具备 PostgreSQL/Alembic 持久化、Task UUIDv4、Installation scope、状态/revision/时间约束和仓储 CAS；只允许 active Installation 创建，跨 Installation 查询/更新不可见，并发旧 revision 只有一个赢家；
- `POST /api/v1/tasks` 要求精确 `app.control-plane` Session、Installation-scoped `Idempotency-Key` 和唯一 `douyin.search_exposure.v1` 封闭定义；Task 与明确列定义在同一事务创建，同键同定义返回同一公开 Task，同键改定义拒绝，不保存任意 JSON；
- `GET /api/v1/tasks` 使用不透明 canonical Base64URL keyset cursor，按 `updated_at DESC, task_id DESC` 稳定分页；`GET /api/v1/tasks/{task_id}` 返回包含 `status/revision/lastEventSequence` 的同一权威公开快照，未知、非法或其他 Installation 的 Task 均统一为不可见；
- `execution_attempts`、`task_actions` 与 `tasks.current_attempt_id` 已形成 Task/Installation 复合绑定；每个 Task 只有一个非终态 Attempt、重试序号不可重复，每个 Attempt 内 Action ordinal 唯一，Action 阶段与结果确定性由数据库一致性约束锁定；
- `task_events` 已建立版本化封闭事件词汇、连续 `(task_id, sequence)` 时间线、Installation/Attempt/Action 复合归属，以及 message/idempotency 双键和 32 字节意图指纹去重；正式 WebSocket 事件在一个 PostgreSQL 事务内落库并以 revision CAS 推进 Task/Attempt/显式 Action 与 `last_event_sequence`；
- `GET /api/v1/tasks/{task_id}/events` 已建立受 `app.control-plane` Session 保护的 SSE：只从 PostgreSQL 已提交事件按 sequence 读取，支持标准 `Last-Event-ID`、断线续拉、keepalive、限时换票重连和终态关闭；正式 Rust App 客户端严格验证响应头、公开字段、版本、序号、UUID、UTC 时间与资源上限；
- `POST /api/v1/tasks/{task_id}/pause` 与 `/resume` 只在当前 Task/Attempt 状态相容时原子分配下一命令序号并写入持久 Outbox，首次返回 202、同键重放返回 200；投递和 `task.control_ack` 都不能提前修改 Task/Attempt，只有与最新已确认控制命令 correlation 一致的 `task.paused`/`task.resumed` 事件才会收敛状态；
- `POST /api/v1/tasks/{task_id}/cancel` 与 `/emergency-stop` 在同一事务写入持久命令，并把可取消的 Task/Attempt 各前进一次到 `cancelling`；同键重放不重复增 revision，终态必须来自匹配最新已确认终止命令的事件，完成事实并发到达时仍按真实 succeeded/partial/failed 收敛，硬紧停动作无法确认时进入 `outcome_uncertain`；
- `task_commands` 持久 Outbox 已接入正式 Executor WebSocket：PostgreSQL 以 `FOR UPDATE SKIP LOCKED` 原子抢占 lease，经当前连接写入后只记 delivered；断线、写入失败、ACK 超时和重连使用同一 message/idempotency 安全重投，只有匹配 Installation/Task/Attempt/correlation/sequence 的 accept/reject/control_ack 才能确认，deadline 到期固定 expired；
- Executor v1 Envelope 已建立 Pydantic 判别联合：25 种生命周期/平台健康/任务命令/回执/事件精确分型，显式 `1.0` 版本、规范 UUIDv4、UTC deadline、幂等键、正序号和受限安全 payload 均 fail closed；
- Executor v1 Draft 2020-12 Schema 已从 Pydantic 确定性导出；Python、Rust、TypeScript 正式解析器共同回放 7 个 valid、26 个 invalid 公共 fixtures，并对结构、deadline、隐私和资源边界给出一致结论；
- `WS /api/v1/executors/connect` 已通过真实 Uvicorn 网络边界接入 `executor.connect` 短期 Session：精确子协议、Installation/Executor/运行时版本绑定、独立连接 ID、32 KiB 传输上限、周期重认证和吊销断连均 fail closed；进程内 Registry 以 Installation 为单活键并承载持久命令投递，连接后可接收 heartbeat、严格绑定的命令回执与任务事件，新 Hello 固定 4409 替换旧连接；
- 无副作用 FakeExecutor 已复用正式 v1 parser、envelope、子协议和 Session WebSocket：可确定性回放 accept/reject、成功/部分成功/失败、登录、接管、结果不确定及暂停/恢复/取消/紧停，并按 message/idempotency 双键返回完全相同的结果且不重复事件；它不导入 Control Plane、RPA、文件、子进程或数据库实现；
- 正式 `automation-tool-executor` Python 进程入口已建立：只从 stdin 读取一条 16 KiB 内、拒绝重复 key/未知字段的 bootstrap；Rust `ExecutorManager` 在完整签名包复验后从固定入口启动单实例，每次生成独立 256-bit 本机令牌，Python 健康/停止事件只返回域隔离 `atlep1` HMAC 证明，Rust 以常量时间验证。后台 supervisor 只对异常崩溃执行显式最多两次恢复，每次重新验包并生成新令牌；显式 stop、退出码 0/1/2、坏包和坏认证不重启。每次启动在 Unix 建立独立 process group，正常停止、启动/停止超时、异常恢复和 Manager Drop 均先清理完整进程树；Windows 已实现 suspended spawn→Job Object→resume 与 kill-on-close，待原生 runner 验收。stderr 在读取时即限界，Rust 再次移除凭据/Cookie/查询串/私有路径并只在内存保留最多 200 行、单行 4096 bytes、总计 64 KiB；超长或非法 UTF-8 行使用固定占位。E4-11 已让 Rust 经同一 stdin bootstrap 传入 App 私有状态目录，Python 在联网前以固定 `executor-ledger.sqlite3` v1 迁移建立 command/idempotency、attempt checkpoint 和协议 outbox，并绑定唯一 Installation/Executor；Session、Cookie、密钥和任意配置不入库、不进钥匙串。E4-12 已从真实 PostgreSQL/Uvicorn 持久 offer 经 signed PyInstaller→Manager→SQLite 回传固定 ACK/五条 Event，并以同一状态目录重启证明消息精确重放、云端事实不重复。E4-13 已由 Tauri `app_data_dir` 固定装配包/状态/稳定 Executor ID，并以四个无参数 PlatformAdapter 操作接入“设置与诊断”；React 不接触路径、Session、PID、信任参数或原始 stderr，本机硬紧停与业务 Task 紧停分离。E4-14 已由唯一 `visible=false` App 从真实诊断页面启动 signed Executor，验证 OS 崩溃恢复、挂起后的超时硬停止、再次启动及 App 正常退出清理；真实链路经正式 TypeScript Adapter、Tauri IPC、Rust 换票、Control Plane/PostgreSQL 与 App 私有 SQLite，测试数据不进钥匙串。当前不执行浏览器或平台副作用；
- Local Executor 已有锁定 PyInstaller 构建依赖和确定性 `onedir` spec；正式 `onedir` 收集 Python Playwright driver，但不执行 `playwright install`、不捆绑或下载 Chromium/Firefox/WebKit。B5-08 正式 BrowserRuntime 在同一 Executor 模块内只持有一个 thread-confined headed persistent context，提供主窗口、开窗、捕获弹窗、有界超时、定向关窗和幂等关闭；启动前再次复验路径，异常统一脱敏。macOS 冻结验收已沿受信浏览器→私有 Profile→单实例锁启动系统 Chrome，完成双窗口正常关闭及独立 process group 整树强杀；Windows Job Object 代码已存在，原生 BrowserRuntime 验收仍受 GitHub Billing/Actions spending limit 阻塞。B5-09 已从生产 `BrowserWindow` 的官方抖音页面事实封闭产生 `healthy/expired/missing/risk/unknown`，固定以 `/user/self` 受保护页判断真实登录，只有 `healthy` 关闭熔断。B5-10/B5-11 在同一 Python 页面层用专用 headed 窗口实现 `login_required/awaiting_scan/awaiting_confirmation/qr_expired/healthy/handoff_required/unknown`，只由 `begin()` 和无参数 `recheck()` 推进；B5-12 再把 detector 事实写入 App 私有 SQLite v2 单调 epoch，并经正式 Executor WebSocket 投影到 PostgreSQL 六列最小状态。真实 Chrome 已验证空白 Profile 的官方二维码、实际扫码后收敛到 `healthy`，以及已登录持久 Profile 重开后直接复用。验证码、滑块或风控外层挑战一律进入人工接管并保持熔断，代码不点击、不填写、不拖拽、不绕过，也不读取、导出或上传 Cookie；
- B5-13/B5-14 已提供桌面“平台状态”和安全注销：状态查询只展示服务端平台/状态/观察时间；打开处理与重新检查经同一个 signed Executor 调用本机页面 flow。注销先由 `POST /api/v1/platform-sessions/douyin/logout/prepare` 持久化服务端门闩并拒绝新任务/新投递，已排队的工作命令也不再 claim，只有取消/紧停可继续投递；再停止 Executor 与完整浏览器树、释放 Profile lease，Rust 基于稳定目录句柄只删除 current Douyin Profile，随后重启 Executor 发送无路径 `douyin.logout.complete` 并等待 WebSocket 将权威投影推进到 `missing`。React 不接触凭据、路径、Cookie、页面对象、revision 或 headless 参数；停止或删除失败不会伪报成功，门闩只允许更高 revision 的真实 `healthy` 重新开放；
- Executor `onedir` 已有 v1 签名 Manifest：离线构建工具清点入口和每个普通文件的相对路径、大小与 SHA-256，以确定性目录摘要绑定版本、构建 ID、macOS/Windows 和 aarch64/x86_64，再对 canonical Manifest 原始字节生成独立 `atems1` Ed25519 签名。签发私钥只从 stdin 读取且不落盘；非规范路径、symlink、非普通文件、文件替换竞态、超限或错误入口均拒绝；
- Rust 原生包验证器已用可信 Ed25519 公钥先验签，再 exact-field 解析 canonical Manifest，绑定当前 OS/架构，以 `semver` 允许范围和已安装版本拒绝越界/降级，并两次枚举整目录、稳定打开逐文件复算大小/SHA-256/目录摘要；错误 signer、弱公钥、目录增删篡改、symlink、非普通文件和竞态均 fail closed。该能力没有 React/Tauri Command 或在线下载面；macOS arm64 与 Python fixture 已实测，Windows 原生 runner 仍受 GitHub Billing 阻塞，保留待验收；
- E4-15 已把 `127.0.0.1:1420` 与 devCSP 从正式 Tauri 配置拆到仅 `pnpm tauri:dev` 合并的覆盖文件；release 缺失、畸形或弱 Executor 验证公钥会在打包前 fail closed。实际 macOS release 二进制及无默认特性 Cargo 依赖树已经扫描，不含 WebDriver/WDIO、验收 Command、测试 origin/Sidecar、开发验证公钥或调试端口；验收只使用临时公开公钥和唯一临时 target，不启动 App，Windows 原生仍待 Hosted Runner 恢复；
- Demo Bootstrap 已建立最多 7 天、精确环境绑定、只允许 installation 注册的 fail-closed 能力模型，不能作为业务 API 凭据；
- React 工作台已通过 TanStack Query、严格公开 Task DTO、快照权威事件投影和 Rust SSE → Tauri Channel 展示当前/最近任务、运行状态与基础指标；“新建任务”提供受约束的抖音搜索曝光表单。运行详情展示权威状态、进度、事件时间线和已有 Action 结果，并通过四个固定 Rust operation 提交暂停、恢复、取消与紧停，最终仍以 Executor 事实收敛。正式 Local Executor 最小进程已能联网和健康退出，真实任务处理与 RPA 尚未实现；
- 尚未部署任何服务或执行真实社交平台动作。
