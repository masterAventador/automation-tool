# automation-tool

面向运营人员的 Tauri 桌面自动化工具。产品优先级固定为：

```text
RPA 运营 > 内容生产与分发 > AI 员工与工作流
```

当前处于第一期 MVP 实施阶段。Wave 1～Wave 6 的工程主线、Wave 7 A7-01～A7-15 与 Wave 8 H8-01～H8-21 已完成；H8-22 的通用更新 UI、隐藏 App 原入口自动化、macOS ad-hoc 实包升级和 Windows 隔离普通包验收器已经完成，Windows 实机结果及 macOS/Windows 正式发布签名证据仍为 `🔍 待验收`。桌面 MVP 闭环、恢复诊断、后台更新下载及安全安装协调均已收口，真实账号证据继续独立待补。

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
- App 打开后直接进入真实 RPA 运营工作台，展示当前/最近任务、运行状态，以及按当前 Installation 从 PostgreSQL 权威事实汇总的任务/动作成功、失败、接管和结果不确定指标；Control Plane 不可用与 Installation 已吊销分别显示脱敏诊断和重试状态，页面不存在产品登录或注册入口；
- 上述无登录入口仅是当前 P9 本地 MVP 状态；任何客户 Demo 交付前必须完成 U9 产品账号、登录/恢复、Session 和账号设备归属，未登录不进入工作台，登录后设备自动绑定账号；
- BaseUrl Profile 使用 Zod fail closed：local 固定为 `127.0.0.1:8765`，demo 强制 HTTPS 且主机必须精确命中构建允许列表；
- ControlPlaneTransport 已接入正式 Tauri IPC/Rust 网络桥：生产入口由真实 App 发起 Health 请求；Rust 侧以固定 origin、封闭 operation allowlist、禁止重定向/代理、请求与响应大小/时间上限和关联 ID 调用 Control Plane，不暴露任意 URL 代理；
- Installation 注册、长期凭据轮换/吊销、两类短期 Session，以及 Task 幂等创建、分页列表、详情、事件 SSE、暂停/恢复和取消/紧停，已通过测试版真实隐藏 Tauri App → 正式 Rust 桥 → 真实 FastAPI/PostgreSQL 纵向验收；设备私钥、Bootstrap、长期凭据和短期票据全程留在 Rust，React/IPC 响应只得到公开结果；
- FastAPI OpenAPI 3.1 快照与 `openapi-typescript` DTO 已覆盖 Health/Version、Installation 注册/访问、设备凭据生命周期、短期 Session 交换、工作台运行状态/结构化指标，以及 Task 创建/列表/详情/事件 SSE/暂停/恢复/取消/紧停，后端/前端分别具备确定性漂移检查；
- Playwright UI Harness 已覆盖工作台、服务不可用、重试恢复，以及创建→暂停→恢复→取消、独立成功与刷新恢复；正式 `dist/` 扫描证明不包含 Harness 页面或测试 Adapter，代表流程另由隐藏真实 Tauri App 从产品入口验收；
- MVP 失败矩阵已固化为 `contracts/quality/mvp-failure-matrix.v1.json`：15 类边界、78 个可自动化失败分支逐项绑定现有正式测试文件和精确测试锚点；Node 门禁同步校验台账词汇、证据文件/锚点、唯一性和真实账号待验收集合，不能用测试总数或 Fake 页面冒充覆盖。PostgreSQL 迁移失败/连接池耗尽和生产安装包误带 Profile、Cookie、SQLite、诊断资料的缺口已补齐；B5-15、D6-16、A7-16、A7-17 继续独立等待真实平台最终证据；
- MVP 规格复审已固化为 `contracts/quality/mvp-spec-review.v1.json`：10 项产品/架构决策全部符合；H8-16A～H8-16E 已关闭正式发现入口、Installation 单活、确认后逐目标授权/投递、Executor 固定假成功和启动诊断缺口。14 条最终验收当前为 7 条自动化完成、2 条待真实平台、2 条待正式安装包、3 条需由整条桌面用户旅程收口；H8-16E 的隐藏 App 环境修复证据不冒充 H8-16F 的可见运营浏览器最终旅程；
- H8-16A 已把 D6-10 的固定目标发现 Command 接到正式 `TaskRunDetails`：草稿、等待登录、等待确认和人工接管状态可启动或重新发现，登录/接管时可直接进入平台状态；同 revision 不确定重试复用幂等键，错误与返回结构均严格校验且不泄露平台私密事实。唯一隐藏 Tauri App 已从真实工作台按钮经正式 TypeScript/IPC/Rust、Uvicorn/PostgreSQL 和 LocalExecutorProcess 收敛到目标预览，测试准备 Command 只创建 draft Task，不再代替用户启动发现；真实抖音候选仍归 D6-16 待账号验收；
- H8-16B 已把未终结 Attempt 的部分唯一索引从 Task 提升到 Installation，并在同一 Installation 行锁事务中保证并发单赢家；竞争启动固定返回 `423 installation_task_active`，App 显示设备已有任务运行且不泄露占用者身份。隐藏 App 已从两个真实草稿 Task 的正式按钮验证“首个启动、第二个拒绝、首个继续收敛”，数据库最终只有一条 Attempt；
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
- Executor v1 Envelope 已建立 Pydantic 判别联合：28 种生命周期/平台健康/任务命令/回执/事件精确分型，显式 `1.0` 版本、规范 UUIDv4、UTC deadline、幂等键、正序号和受限安全 payload 均 fail closed；
- Executor v1 Draft 2020-12 Schema 已从 Pydantic 确定性导出；Python、Rust、TypeScript 正式解析器共同回放 12 个 valid、27 个 invalid 公共 fixtures，并对结构、deadline、隐私和资源边界给出一致结论；
- `WS /api/v1/executors/connect` 已通过真实 Uvicorn 网络边界接入 `executor.connect` 短期 Session：精确子协议、Installation/Executor/运行时版本绑定、独立连接 ID、32 KiB 传输上限、周期重认证和吊销断连均 fail closed；进程内 Registry 以 Installation 为单活键并承载持久命令投递，连接后可接收 heartbeat、严格绑定的命令回执与任务事件，新 Hello 固定 4409 替换旧连接；
- 无副作用 FakeExecutor 已复用正式 v1 parser、envelope、子协议和 Session WebSocket：可确定性回放 accept/reject、成功/部分成功/失败、登录、接管、结果不确定及暂停/恢复/取消/紧停，并按 message/idempotency 双键返回完全相同的结果且不重复事件；它不导入 Control Plane、RPA、文件、子进程或数据库实现；
- 正式 `automation-tool-executor` Python 进程入口已建立：只从 stdin 读取一条 16 KiB 内、拒绝重复 key/未知字段的 bootstrap；Rust `ExecutorManager` 在完整签名包复验后从固定入口启动单实例，每次生成独立 256-bit 本机令牌，Python 健康/停止事件只返回域隔离 `atlep1` HMAC 证明，Rust 以常量时间验证。后台 supervisor 只对异常崩溃执行显式最多两次恢复，每次重新验包并生成新令牌；显式 stop、退出码 0/1/2、坏包和坏认证不重启。每次启动在 Unix 建立独立 process group，正常停止、启动/停止超时、异常恢复和 Manager Drop 均先清理完整进程树；Windows suspended spawn→Job Object→resume、kill-on-close、崩溃恢复和挂起强停已由 x86_64 实体机原生验收。stderr 在读取时即限界，Rust 再次移除凭据、Header/Cookie、完整 URL、页面/消息内容、错误原文和私有路径，并只在内存保留最多 200 行、单行 4096 bytes、总计 64 KiB；超长或非法 UTF-8 行使用固定占位。E4-11 已让 Rust 经同一 stdin bootstrap 传入 App 私有状态目录，Python 在联网前以固定 `executor-ledger.sqlite3` v1 迁移建立 command/idempotency、attempt checkpoint 和协议 outbox，并绑定唯一 Installation/Executor；Session、Cookie、密钥和任意配置不入库、不进钥匙串。E4-12 已从真实 PostgreSQL/Uvicorn 持久 offer 经 signed PyInstaller→Manager→SQLite 回传固定 ACK/五条 Event，并以同一状态目录重启证明消息精确重放、云端事实不重复。E4-13 已由 Tauri `app_data_dir` 固定装配包/状态/稳定 Executor ID，并以四个无参数 PlatformAdapter 操作接入“设置与诊断”；React 不接触路径、Session、PID、信任参数或原始 stderr，本机硬紧停与业务 Task 紧停分离。E4-14 已由唯一 `visible=false` App 从真实诊断页面启动 signed Executor，验证 OS 崩溃恢复、挂起后的超时硬停止、再次启动及 App 正常退出清理；真实链路经正式 TypeScript Adapter、Tauri IPC、Rust 换票、Control Plane/PostgreSQL 与 App 私有 SQLite，测试数据不进钥匙串。当前不执行浏览器或平台副作用；
- Local Executor 已有锁定 PyInstaller 构建依赖和确定性 `onedir` spec；正式 `onedir` 收集 Python Playwright driver，但不执行 `playwright install`、不捆绑或下载 Chromium/Firefox/WebKit。B5-08 正式 BrowserRuntime 在同一 Executor 模块内只持有一个 thread-confined headed persistent context，提供主窗口、开窗、捕获弹窗、有界超时、定向关窗和幂等关闭；启动前再次复验路径，异常统一脱敏。macOS 与 Windows x86_64 原生验收均已沿受信浏览器→私有 Profile→单实例锁启动系统浏览器，完成双窗口正常关闭及独立 process group/Job Object 整树强杀；Hosted Windows CI 仍受账户 Billing 限制，但不再是本机产品验收阻塞。B5-09 已从生产 `BrowserWindow` 的官方抖音页面事实封闭产生 `healthy/expired/missing/risk/unknown`，固定以 `/user/self` 受保护页判断真实登录，只有 `healthy` 关闭熔断。B5-10/B5-11 在同一 Python 页面层用专用 headed 窗口实现 `login_required/awaiting_scan/awaiting_confirmation/qr_expired/healthy/handoff_required/unknown`，只由 `begin()` 和无参数 `recheck()` 推进；B5-12 再把 detector 事实写入 App 私有 SQLite v2 单调 epoch，并经正式 Executor WebSocket 投影到 PostgreSQL 六列最小状态。真实 Chrome 已验证空白 Profile 的官方二维码、实际扫码后收敛到 `healthy`，以及已登录持久 Profile 重开后直接复用。验证码、滑块或风控外层挑战一律进入人工接管并保持熔断，代码不点击、不填写、不拖拽、不绕过，也不读取、导出或上传 Cookie；
- B5-13/B5-14 已提供桌面“平台状态”和安全注销：状态查询只展示服务端平台/状态/观察时间；打开处理与重新检查经同一个 signed Executor 调用本机页面 flow。注销先由 `POST /api/v1/platform-sessions/douyin/logout/prepare` 持久化服务端门闩并拒绝新任务/新投递，已排队的工作命令也不再 claim，只有取消/紧停可继续投递；再停止 Executor 与完整浏览器树、释放 Profile lease，Rust 基于稳定目录句柄只删除 current Douyin Profile，随后重启 Executor 发送无路径 `douyin.logout.complete` 并等待 WebSocket 将权威投影推进到 `missing`。React 不接触凭据、路径、Cookie、页面对象、revision 或 headless 参数；停止或删除失败不会伪报成功，门闩只允许更高 revision 的真实 `healthy` 重新开放；
- B5-15 修复了“已有健康 Profile 作为本机首个观察时被错误拒绝”的 epoch 初始化缺陷：首次健康固定建立 revision 1，只有已有非健康 epoch 的恢复才递增 revision。独立 `visible=false` App 连续四次从原页面入口启动/退出同一个 signed Executor、无头系统 Chrome 和 App 私有 Profile，验证健康重启不进入扫码、过期进入扫码、风控进入人工接管，并核对 marker/目录 identity、SQLite 与 PostgreSQL。确定性官方 origin 页面只存在于单独签名的验收 Executor spec，不进入正式发布包；真实账号 App 双重启仍按台账保持待补；
- B5-16 已把“绝不接管用户默认浏览器 Profile”升级为源码与运行时双证据：生产源码递归扫描拒绝 Chrome/Edge 默认 User Data、Cookie 和 storage-state 入口；唯一 `visible=false` App 从正式页面链路启动 signed Executor 与无头系统 Chrome 后，runner 在 Chrome 活跃期间核对完整进程树的唯一 `--user-data-dir` 和 `lsof` 已打开文件，只允许 AppData 下的 current 私有 Profile。验收结束确认 App、Executor、driver、Chrome、随机端口、Compose 容器/网络/Volume 与专属 AppData 零残留；
- D6-01/D6-02 已在 Python Local Executor 建立唯一 `douyin.web.v1` route contract 与 `douyin.search-page.v1` Page Object：首页、Session probe 和 general 搜索结果只接受精确官方 HTTPS origin 与 canonical 路径/查询；搜索输入、提交、结果列表、登录与阻塞弹窗按语义优先集中封装。未知版本、入口/锚点冲突、缺失、页面异常或动态 DOM 失效统一熔断；页面对象只读且不导航、不点击、不输入、不滚动，不读取 Cookie/storage/page body，也不把页面对象或 selector 暴露给 App/Control Plane；
- D6-03 已把关键词与目标上限收敛为 Control Plane/Local Executor 共同消费的 `douyin.search-input.v1`：关键词按 Unicode code point 限 `1..80` 并拒绝首尾空白、C0/C1/DEL、Bidi 和安全文本违规，目标数限真整数 `1..100`；React 表单/Zod、Rust、FastAPI/OpenAPI 与 PostgreSQL 在各自边界复验同一规则，关键词不会出现在对象表示或错误中；
- D6-04 已在 Local Executor 建立单次、有限时的 `douyin.search-execution.v1`：只接受共享输入对象，只经 D6-02 Page Object 获取输入/提交/结果锚点，固定执行首页导航→精确填写→单次提交→canonical 关键词结果 URL→结果列表复验；慢加载有总时限，导航、动作、结果 URL/锚点超时以及登录、弹窗、页面漂移或异常全部停止且不重试点击。真实生产 `BrowserRuntime` 已用隔离临时 Profile 和系统 Chrome 无头跑通同一公开入口，结束后完整关闭；
- D6-05 已增加 `douyin.bounded-scroll.v1` 只读滚动控制层：最多 20 轮、每轮固定一个真实 wheel、结果增长最多等待 3 秒、达到任务目标或一轮无新增立即停止；运行前、每轮前、等待期间和增长后均检查取消。结果项 selector/计数只存在于 Page Object 且按任务上限裁剪，计数倒退、页面变化、登录/弹窗、取消探针或浏览器异常全部 fail closed；生产 BrowserRuntime 已在同一无头系统 Chrome 中真实跑通搜索→两轮滚动→3 个目标后关闭；
- D6-06 已建立公共 `douyin.candidate.v1` 最小候选模型：只含规范平台目标 ID、展示名、可选公开号、固定 general-search author 来源和跨运行时安全 page revision；`atdck1_` 去重键由目标 ID 经域隔离 SHA-256 确定性生成，不随名称或页面 revision 漂移。模型不接受头像/简介/联系方式/页面原文/绝对链接；D6-09 只持久化该最小事实，D6-10 的 Executor wire 也只传输其中的公开字段，不传 key 或页面原文；
- D6-07 已建立 `douyin.candidate-extraction.v1` 本地隐私边界：只有版本化 Page Object 能读取结果作者节点的目标 ID/作者链接、显示名和可选公开号；官方绝对/相对作者链接在本机立即缩减为规范目标 ID，query 被丢弃，跨域、userinfo、fragment、冲突 ID、非法字段、读取异常或页面漂移整批 fail closed，不返回部分结果。执行层只得到 D6-06 最小 Candidate，不接触 selector、页面正文、头像、简介、联系方式或源链接；真实 BrowserRuntime 已用隔离 Profile 和无头系统 Chrome 跑通搜索→提取原入口并完整关闭；
- D6-08 已建立 `douyin.candidate-policy.v1` 纯领域策略：固定 30 天历史去重窗，只比较 D6-06 稳定 key，并按黑名单 > 本任务后续重复 > 历史重复 > 可用的固定优先级为每条候选保留一个明确原因；顺序和整份预览保留，不用破坏性过滤掩盖排除项。策略拒绝跨 page revision、未来历史、重复查询 key、非 UTC 时间和超出 100 项边界，不读取平台 ID/摘要，也不连接数据库、浏览器或 Executor wire；
- D6-09 已建立 PostgreSQL `task_targets` 和原子仓储：每个 Target 只保存 D6-06 最小 Candidate、任务内顺序、page revision、策略版本/原因和 UTC 时间，以 `(task_id, installation_id)` 复合外键绑定父任务；任务内 ordinal 唯一，但同 key 的后续重复行会完整保留用于预览标记。仓储先锁 Installation/Task，再只查询同 Installation、其他任务中每个候选 key 的最新历史，调用 D6-08 策略后整批替换；旧/同 revision、跨 scope、吊销实例、未来/倒序时间和非法 keyset 全部拒绝。列表按 `(ordinal,id)` 正序 keyset，不使用 offset；
- D6-10 已闭合目标发现链路：App 通过固定 Rust Command 和 `app.control-plane` 短期 Session 调用 `POST /api/v1/tasks/{task_id}/discoveries`，Control Plane 原子创建 Attempt、`task.discover` Outbox 和 `task.discovery_started` 事件；正式 Executor 以 SQLite v3 先持久化命令和完整结果 outbox，再按最多 10 条分批发送 `task.discovery_batch`，最后发送 `task.discovery_completed`。服务端只接受当前 Installation/Task/Attempt/correlation/page revision 的连续完整批次，在同一事务调用 D6-09 保存 Target，并把 Task 收敛到 `awaiting_confirmation`；断线按同一 message/idempotency 精确重放，篡改、缺批、乱序、过期、登录失效和人工接管均 fail closed。隐藏 Tauri App→真实 Uvicorn/PostgreSQL→正式 LocalExecutorProcess 纵向验收已通过且无浏览器副作用；
- D6-11 已提供 Installation-scoped 目标预览、排除与确认闭环：App 只通过三个固定 Tauri Command 调用 `GET /target-preview`、`PUT /exclusions` 与 `POST /confirmations`，游标绑定 page/task revision，排除和确认均要求期望 revision 与幂等键。PostgreSQL 只保存用户排除关系和确认快照，不向 App 暴露平台目标 ID、去重键或页面事实；过期游标、旧快照、跨 Installation、非法排除、全量排除、并发确认和篡改重放全部拒绝。确认写入 `task.targets_confirmed` 并把 Task 推进到 `queued`，重新发现会清除旧确认；隐藏 Tauri App→正式 Rust 网络桥→真实 Uvicorn/PostgreSQL 的列表、排除、确认和重放验收已通过；
- D6-12 已把正式目标预览 source 接入任务详情：用户可查看最小目标摘要、固定来源、执行/排除/策略计数及去重/黑名单原因，逐项或全部调整后进行最终二次确认。排除与确认绑定最新 page/task revision，同意图不确定重试复用幂等键，过期快照回拉，空选择禁止确认，页面不显示私密 Target ID 或底层错误；独立隐藏 Tauri App 已从真实页面取消目标并确认，经正式 TypeScript/IPC/Rust/Uvicorn/PostgreSQL 核对最终 `queued`、排除/确认关系和连续事件；
- D6-13 已在 PostgreSQL Outbox 增加未确认副作用守卫：带 `douyin.search_exposure.v1` 业务定义的 `task.offer` 入队时必须命中当前目标确认并持久绑定确认 message ID，claim 时再次核对同一确认仍存在且 Task 处于 `queued/running`；无绑定、旧确认、确认被删除或预确认状态都保持 `pending` 且投递次数不增加。`task.discover`、暂停、取消、紧停和无业务定义的既有协议骨架不被误拦；真实 Uvicorn/Executor WebSocket 已证明只有当前确认的业务 offer 能离开 Outbox，ActionAuthorization 与真实平台动作仍由 Wave 7 实现；
- D6-14 已把明确的抖音页面版本未知或锚点冲突从普通失败收紧为人工接管：Local Executor 在 App 私有 state 下只写最多 20 份、单份最多 2 KiB 的固定 JSON 诊断，使用 UUIDv4、SHA-256、固定媒体类型和受控相对路径；文件不接受自由文本，也不保存关键词、URL、DOM、页面正文、截图、Cookie 或 Profile 路径。即使磁盘或权限导致诊断写入失败，页面熔断和 `handoff_required` 仍成立；正式 `task.discover` 已通过隔离 Profile 的无头系统 Chrome 验收并确认浏览器完整关闭；
- D6-15 已建立不进入生产包的抖音发现 Fake 页面语料：正常、可见空结果、阻塞弹窗、登录跳转、未知版本和无限滚动六类状态都从正式 `task.discover` 经生产 Page Object/搜索/滚动/提取编排在无头系统 Chrome 中回放。无限页固定证明 20 轮后停止并仅提取 21 个候选；语料不访问外网、不读写 storage/Cookie，也被 D6-04/D6-05/D6-07 既有浏览器测试共同复用；
- D6-16 已用此前用户授权的独立抖音 Profile 开始真实只读验收：受保护页稳定收敛到 `healthy`，但首页当前出现 ByteDance 验证码 iframe，不能安全继续搜索。Session 与搜索 Page Object 现共用同一组风控 selector，正式发现从原先等待锚点超时改为立即 `handoff_required/blocking_dialog`，没有点击、填写、拖拽或绕过挑战；真实候选和 App 预览仍保持待风控解除补验，不阻塞 Wave 7 离线任务；
- A7-01 已建立纯服务端风险策略领域模型：`InstallationId + douyin + browse|comment|direct_message` 组成不可变复合 scope，每份策略必须显式提供正整数秒最小间隔、单任务动作上限、UTC 日动作上限和连续失败阈值。任务上限复用既有 100 目标硬边界，其他计数只使用跨运行时无损整数结构上界；代码没有写入未经真实账号校准的运营默认值；
- A7-02 已把该策略接入 PostgreSQL 原子授权：每次授权先锁定 Installation，再复验当前运行 Task/Attempt、匹配的任务动作、健康且无门闩的 Session、最新目标确认及未排除可执行 Target；任务计数、UTC 日计数和安装实例级最小间隔在同一事务内计算，实际间隔取任务配置与服务端策略较大值。成功时同时写入既有 `task_actions` 和不可变 `action_risk_authorizations` 策略/计数快照；并发请求不能越过硬限制，同一 ActionId 只允许同目标、Attempt、Task、Installation 和动作精确重放；
- A7-03 已建立 `action-authorization.v1` 短期动作授权：Control Plane 用独立 Ed25519 私钥把 Action、Target、Attempt、Task、Installation、Executor、平台动作、固定幂等键和服务端 UTC 授权/截止时间签入 canonical `ataa1` token；Local Executor 只持有固定公钥，先验签再逐字段匹配当前执行意图，并拒绝篡改、错签发者、跨执行器/安装实例/任务/目标/动作、未来超出时钟偏差和到期授权。私钥不进入 App、Executor、SQLite 或系统钥匙串；
- A7-04 已把授权验签与 Executor 本机硬下限合成唯一准入门：调用面只接受 token 和完整执行意图，不接受服务端阈值；显式本机最小间隔和单任务动作上限自 SQLite v4 起只能变严，缺失或损坏策略直接拒绝。持久紧停 latch 先于重放/频控检查，只有匹配本机 revision 且时间单调的明确清除才可恢复；动作准入以 `BEGIN IMMEDIATE` 原子计数并只保存授权 SHA-256 指纹，不保存 token、Cookie、Profile、账号或密钥；
- A7-05 已建立版本化 `action-message-template.v1` 文案策略：评论/私信允许纯固定文案，也只允许 `{{target_display_name}}` 一个目标变量；按 Unicode code point 最多 500 字符，拒绝空文案、纯变量、控制/Bidi、敏感赋值、私有路径及未知/畸形占位符。React 表单、TypeScript Gateway、Rust 桥、Python 领域和 PostgreSQL `20260720_0021` 在各自信任边界复验；当前只校验、不渲染、不调用 LLM、不发送平台动作；
- A7-06 已把最终确认升级为不可变执行意图快照：App 同时展示动作、原始文案模板、选中目标数量和确认 revision，弹窗打开后冻结用户已审阅版本；后台目标变化时旧提交由真实后端 CAS 拒绝并回拉。PostgreSQL `20260720_0022` 将 action/message/selection revision、目标集合计数及版本化 SHA-256 指纹绑定到 confirmation；A7-02 授权和 D6-13 业务 offer 会继续复验当前确认，持久事实或任务定义变化均 fail closed；
- A7-07 已把正式 Executor SQLite 升级到 v5 副作用账本：评论/私信必须先写 `prepared`，原子竞争到唯一一次 `dispatched` 许可后才允许平台点击，随后只能收敛为 `verified` 或 `uncertain`；重启与并发重放不会再次获得执行许可。账本只保存完整资源绑定、UTC 时间和意图/验证 SHA-256，不保存评论私信正文、Token、Cookie、Profile、账号或密钥；当前没有新增 HTTP、Tauri/React 或平台动作，A7-11/A7-12 将直接消费该内部入口；
- A7-08 已新增不执行动作的抖音评论 Page Object：共享页面版本模型只接受 canonical 官方 `/video/<数字 ID>` 详情路由，输入、提交和最终确认分别由固定 selector 组唯一定位；登录、风控/阻塞弹窗、半套或重复锚点、未知路由和 DOM 异常全部 fail closed。隔离 Fake 页面已通过无头系统 Chrome 的正式 `BrowserRuntime` 原入口回放，但不冒充真实账号评论验收；真实填写/点击仍由 A7-11 在 A7-07 唯一许可后编排；
- A7-09 已新增不执行动作的抖音主动私信 Page Object：共享页面版本模型只接受 canonical 官方 `/user/<目标 ID>` 主页路由，进入会话、私信输入/发送、最终确认以及“暂时无法私信/关注后才能私信”权限差异均由固定 selector 组唯一定位；登录/风控优先熔断，半套、重复、冲突、未知路由、驱动异常和中途漂移全部 fail closed。隔离 Fake 页面已通过无头系统 Chrome 的正式 `BrowserRuntime` 原入口回放 profile→conversation→confirmed、权限拒绝和漂移，但不冒充真实账号私信验收；真实动作仍由 A7-12 在 A7-07 唯一许可后编排；
- A7-10 已建立无发送副作用的单次目标浏览基线：只接受 D6-10 最小 `DouyinCandidate`，由共享路由模型构造 canonical 官方用户页，固定一次 `domcontentloaded` 导航并经独立通用 Profile Page Object 确认最终主页锚点；开始、导航后和最终成功前都有取消检查。登录、风控、超时、未知版本、锚点冲突、驱动异常或最后一刻 DOM 漂移全部 fail closed；隔离主页中的评论/私信陷阱按钮触发数保持 0，生产 `BrowserRuntime` 无头回放后完整关闭。该内部 RPA 能力不新增 App/API/Executor wire，A7-11/A7-12 再接入高风险动作链；
- A7-11 已建立 `douyin.comment-action-execution.v1` 单次评论执行：完整 ActionAuthorization 先经本机硬下限准入，精确渲染固定文案或唯一目标显示名变量后只把域隔离 SHA-256 写入 SQLite；Page Object ready 后填写正文，只有 `begin_side_effect_dispatch()` 唯一赢家才能单击提交，最终锚点二次复验后才结算 `verified`。登录、风控、陈旧确认、页面漂移和填充失败保持 `prepared/not_dispatched`；获得许可后的点击、最终确认或持久化失败一律结算或保留 `outcome_uncertain`，重放看到 dispatched/uncertain/verified 都不再点击。结构化 receipt 只含资源 ID、封闭状态/evidence、账本状态/revision 和重放标记，不含正文、Token、页面内容或路径；无头系统 Chrome 已沿生产 BrowserRuntime、真实 locator 和私有 SQLite 在官方-origin 隔离页证明首次单击一次、精确重放零单击，但真实平台最终状态仍归 A7-16；
- A7-12 已建立 `douyin.direct-message-action-execution.v1` 单次主动私信执行：沿用同一 ActionAuthorization、本机硬下限和副作用账本，在 `prepared` 后允许从用户主页进入会话或从已打开会话恢复；进入会话不是发送许可，只有填入最终受限文案并重新取得 send locator 后，唯一 dispatch 原子赢家才能发送一次。“暂时无法私信”和“关注后才能私信”在发送前、发送后均保留不同 evidence；发送前失败保持可恢复 prepared，发送后任何超时、权限变化、页面漂移或结算失败都进入 uncertain 且重放零 DOM。生产 BrowserRuntime 已在官方-origin 隔离页用无头系统 Chrome 和真实私有 SQLite 证明首次进入/发送各一次、精确重放均不增加，但真实平台最终状态仍归 A7-17；
- A7-13 已建立 `douyin.side-effect-recovery.v1` 只读结果恢复：只接收本机账本中已存在的 Action ID，prepared、verified、uncertain 分别直接投影且零 DOM；只有崩溃窗口残留的 dispatched 才按持久 action 类型选择评论/私信 Page Object，读取并二次取得最终确认。证据充分时沿用与即时动作完全相同的验证摘要结算 verified，登录、风控、权限、超时、页面漂移或验证失败结算 uncertain；并发 opposite terminal 只接受账本赢家，结算存储不可用则至少保留 dispatched，恢复源码没有填写、点击、导航、selector、任意 URL、HTTP、OCR 或 LLM。无头生产 BrowserRuntime 已证明评论/私信恢复后提交、进入会话和发送计数全部为 0；
- A7-14 已把确定动作结果接入 PostgreSQL 连续失败熔断：`action_risk_results` 为每个已授权 Action 保存成功/失败、授权阈值、结果后计数和是否触发接管，`action_failure_circuits` 按 Installation/平台/动作保存当前 circuit；复合外键保证结果、授权和 circuit 不可跨 scope 拼接。失败达到该 Action 的授权快照阈值时，同一事务把当前 Task/Attempt 投影为 `awaiting_human` 并写 `task.awaiting_human`，后续新 Action ID 在服务端授权入口被拒绝；精确授权和事件重放不重复计数。成功只清零尚未打开的 streak，晚到成功不能自动关 circuit；只有打开该 circuit 的 Task 经正式已确认 `task.resumed` 才能清零恢复。认证 Executor WebSocket 已从 `/api/v1/executors/connect` 原入口触发完整持久化与后续授权拒绝；
- A7-15 已把 Executor 动作 receipt 收敛为 `action-result-evidence.v1` 封闭证据，并由正式 Task event 写入 PostgreSQL `task_actions.evidence_code`；数据库约束锁定成功、失败、取消与不确定证据的一致性。受 App Session 和 Installation scope 保护的 `GET /api/v1/tasks/{task_id}/target-results` 只投影当前 Attempt 的目标级待执行/进行中/成功/跳过/失败/不确定和固定摘要；现有运行详情通过严格 TypeScript DTO、固定 Tauri Command 与 Rust 网络桥展示结果，不读取 Executor SQLite、不显示正文或页面事实。唯一 `visible=false` App 已从原页面经真实 Uvicorn/PostgreSQL 验证四类终态、重试和控制链；
- H8-01 已把正式 Local Executor 的暂停从“收到命令即宣称暂停”收紧为持久安全检查点：`task.control_ack` 可立即出站，但只在该 Attempt 没有 `dispatched` 副作用时，才把本机 checkpoint 与 `task.paused` Outbox 在同一 SQLite 事务中提交；已有 dispatched 动作先结算，暂停命令一旦落账就阻止任何新的 prepared→dispatched。运行循环会持续推进待生效控制，恢复命令同样以 ACK、checkpoint 和 `task.resumed` 原子收敛后才重新开放 dispatch。隐藏 Tauri App 已经正式 Rust/FastAPI/PostgreSQL/WebSocket/真实 Executor 证明暂停等待、零新增点击、自动 PAUSED 与恢复 RUNNING；本机账本未新增表或保存凭据；
- H8-02 已把普通取消接入同一正式 Local Executor 安全边界：`task.cancel` 只附着到 running/paused checkpoint，命令与 `task.control_ack` 先持久化并立即封锁新 dispatch；已有 dispatched 动作必须先结算，已验证则原子收敛 `task.cancelled`，无法确认则原子收敛 `task.outcome_uncertain`，prepared 动作永不再获得派发许可。唯一隐藏 Tauri App 已从原取消入口经正式 Rust/Uvicorn/PostgreSQL/WebSocket 驱动真实 Executor，证明服务端先保持 `CANCELLING`、最后动作不明时三端一致进入不确定终态；本任务不把 H8-03 的离线硬紧停混入普通取消；
- H8-03 已把任务紧停改为本机先行的离线硬停止：App 在任何 HTTP 前把最小紧停意图原子写入私有 AppData，再终止完整 Executor/浏览器进程树；真实 Executor SQLite 在一个事务中封锁新 dispatch、把已派发未确认动作收敛为 uncertain 并持久化 ACK/Event，网络恢复后 App 从原工作台/详情轮询补发同一命令并启动报告型 Executor，最终 PostgreSQL/SQLite/本机 marker 精确收敛且不重复副作用；
- H8-04 已用两个独立 `visible=false` App 进程证明 App 硬崩溃恢复：第一个 App 从页面创建并运行 Task、通过正式 IPC 启动唯一签名 Executor 后被精确 `SIGKILL`；Executor 继续在线且 Task 不变。第二个 App 只复用同一 AppData，从服务端恢复原运行中工作台和任务时间线。崩溃前后 Task/Attempt/Command/Event 与本机 verified/prepared 副作用逐字段相同，没有重复注册任务、发控制命令、启动 Executor 或执行平台动作；
- H8-05 已把 supervisor restart 与首次启动明确区分：Rust 只在异常重启 bootstrap 中设置 `crash_recovery=true`，Python 在联网和 outbox replay 前只读结算现有副作用。进程树清理后若没有可验证页面上下文，dispatched 只会变成 uncertain，prepared 原样保留；checkpoint 与 `task.outcome_uncertain` outbox 在同一 SQLite 事务推进，稳定幂等键保证重复崩溃不重复上报或点击。唯一隐藏 App 已经正式 IPC 注入真实签名 Executor 崩溃，并从 PostgreSQL/工作台读到“结果待确认”；
- H8-06 已让同一个签名 Executor 进程在收到 Control Plane 的 WebSocket `1012` 服务重启关闭码后原进程内有界重连：首次连接失败和非重启关闭仍固定失败，重连预算只在恢复连接完成健康心跳后重置，停止请求可立即打断等待。App 在 Executor 暂停期间从正式详情页发出的取消命令先持久化为 delivered；真实 Uvicorn 同库停服/重启后，Executor 以相同 PID、相同 Session 和 `restartCount=0` 重连，SQLite 按原 message/idempotency 精确重放命令与 outbox，PostgreSQL 最终只产生一份取消 ACK/终态事件；
- H8-07 把异常断网和网络抖动纳入同一进程的有界恢复：SQLite v6 的持久网络闸门与紧停闸门在一次事务内阻止离线新 dispatch，未交付事件 spool 固定最多 1000 条/16 MiB；异常无关闭帧、初次网络不可达和发送期 `OSError/TimeoutError` 进入原 120×250ms 预算，协议/应用错误仍固定失败。隐藏 App 经真实 Rust 桥和签名 Executor 完成一次硬断网、两次抖动、离线取消落盘及精确续传，同一 PID、`restartCount=0`，云端与本机最终各只有一份事实；
- H8-16D 已把正式 Executor 的 offer/action 两阶段接入真实生产动作：offer 只产生 `task.accept/task.started`，不会提前生成完成；typed `action.execute` 先经 Ed25519 授权、本机硬限制和 SQLite v7 脱敏持久化，再按动作调用 A7-10 浏览、A7-11 评论或 A7-12 私信，并只接受 A7-15 封闭页面证据。Tauri 通过编译期固定公钥和认证 stdin bootstrap 装配生产 Operation，不把私钥、正文、Cookie、Profile、路径或系统钥匙串引入 App。Processor 与真实 LocalExecutorProcess WebSocket 均已通过动态端口、隔离 Profile、全局无头系统 Chrome 验收，评论/私信点击计数和重放零副作用已核对；生产运营浏览器仍保持可见；
- H8-16E 已把正式启动 Gate 扩展为 Control Plane 与本机环境并行聚合：Rust 只返回 AppData、Executor 和受信浏览器的封闭状态，不返回路径、包信息、凭据或底层错误；动作信任配置缺失、签名包异常、浏览器未选择/不可用和私有目录异常都会在业务功能挂载前显示固定诊断。隐藏真实 App 已从诊断页选择受信浏览器、重新检查并进入工作台，正式 IPC 返回三项 `ready`，Executor 保持停止且全程未启动运营浏览器；
- H8-18 已选定官方 Rust `tauri-plugin-updater 2.10.1` 作为 macOS/Windows 平台识别、feed 检查和安装原语；自有 `app_updates.rs` 与 React `features/app-updates` 只维护不含业务名、URL、签名和私有路径的版本化发布/状态/决策契约。JavaScript updater binding 与 Capability 均未开放；H8-20 为断点续传复用同一依赖树的 HTTP 与 Minisign 流式原语，不复制平台安装器；
- H8-19 已把通用可选/强制更新策略装入真实 Tauri 启动路径：App 版本、stable channel、最高见过版本、不可变 Artifact identity 和最后一次可选决策以规范 JSON 原子保存在 App 私有 `app-updates/update-policy-v1`。暂缓会在下一次检查重新提示，跳过只压制当前版本，立即安装意图跨重启保留；强更不接受用户决策，版本回退、同版本换包、并发观察、过期按钮和失败写入均 fail closed；
- H8-20 已部署独立、无业务认证和无数据库依赖的 `GET /desktop-updates/v1/{channel}/{target}/{arch}/{current_version}` 更新 feed，并在正式 Tauri setup 注册 Rust-only 官方 updater。启动、6 小时有界周期和手动检查共用唯一协调入口；安装包在 App 私有缓存以 Range/ETag 续传并流式通过 SHA-256 + Minisign 后原子替换，失败不覆盖旧包且清单不保存 URL/签名/路径。隐藏 App 已经由真实 FastAPI/临时 HTTPS 从中断下载恢复到唯一候选；release 缺少合法 HTTPS endpoint 或公开验签公钥会在构建期失败；
- H8-21 已把 `install_now/defer/skip_version` 接入同一个 Rust 协调入口。立即安装会先从私有缓存重验 identity、SHA-256 和 Minisign，再隐藏窗口、停止 Executor 并释放浏览器 Profile，最后只调用官方 updater 安装原语；Windows 由官方安装器退出并接管，macOS 安装成功后由 App 重启。暂缓在下一次启动/轮询/手动检查重新提示，跳过只压制当前版本；强更首次启动只下载，复用同一 AppData 的下一次启动自动安装且不重复下载。隐藏 App 已从正式 Command 和生产 FastAPI feed 验证这些转换；真实签名安装包跨版本升级归 H8-22；
- H8-22 已在“设置与诊断”加入通用 App 更新卡片，并在任意工作台页面挂载可选/强制更新提示。React 只经三个固定 Tauri Command 读取脱敏状态、主动检查和提交封闭决策；Rust 下载进度字段固定为 camelCase，用户操作期间暂停后台轮询。可选更新支持立即安装、稍后提醒和跳过当前版本，强更提示不可关闭且没有暂缓/跳过入口。除三轮隐藏 App 页面自动化外，macOS arm64 已用独立 ad-hoc 0.1.0 DMG、0.2.0/0.3.0 更新包和损坏 0.4.0 包，在不启用安装探针的正式 Rust/官方 updater 路径完成暂缓、跳过、同版本压制、真实 `.app` 覆盖、强更重启及失败恢复；安装后版本/二进制哈希与旧包不变边界均已核对。Windows 已准备 `currentUser` 普通未签名 NSIS 的隔离验收器，覆盖同一可选/强制/失败矩阵、安装后二进制版本/哈希、HKCU 安装记录和卸载清理；它只能在 Windows x86_64 执行，实体机结果和 Developer ID/notarization、Authenticode 正式发布门禁仍保持 `🔍 待验收`；
- H8-08 在同一 `LocalExecutorProcess` 中用 5 秒有界单调调度间隙识别整机休眠/锁屏后的陈旧连接：先复位 H8-07 网络闸门，再复用原有有界重连，稳定心跳后才报告恢复；正常长页面任务完成时重置观测基线，不冒充休眠。合法但已过 UTC deadline 的命令以独立固定结果忽略且不落账，其他坏协议继续 fail closed。浏览器窗口失效和重新建立只写固定无参数诊断，Rust 仍执行二次脱敏与 200 行/64 KiB 滚动限制。独立隐藏 App 已真实暂停/恢复签名 Executor，同一 PID、`restartCount=0`，并从正式 IPC 读到休眠与传输恢复诊断；另用无头系统浏览器和隔离 Profile 验证窗口丢失/恢复，全程不锁屏整机、不触碰默认 Profile；
- H8-09 新增唯一 `LocalArtifactStore`：可信生产者用固定 Policy 声明受控目录、扩展名、媒体类型、单文件和数量上限，存储返回 UUIDv4、SHA-256、媒体类型、大小与相对路径，不返回绝对路径。写入采用独占创建、稳定重读和目录/文件身份复验，POSIX 固定 `0700/0600`，Windows 复用私有 ACL 校验；页面漂移证据已删除重复文件边界并复用该 Store。正式 `task.discover` 无头浏览器链路已经按 Artifact ID 完成解析、枚举和读取；本任务没有新增 App/API、上传、截图/Trace 或清理策略；
- H8-10 在同一 Store 上新增失败截图与结构化 Trace：失败发现自动采集，成功发现默认关闭且只能由用户在“设置与诊断”页显式开启；设置保存在 App 私有 `local-executor/browser-diagnostic-settings-v1`，经固定 Tauri Command 和严格 bootstrap 布尔值进入 signed Executor，不用系统钥匙串。截图只保留当前 viewport，经注入样式隐藏文字、表单、图片、媒体、iframe 与背景资源，并剥离 PNG 附加元数据；Trace 不是 Playwright 原始归档，只含固定平台/操作/阶段/触发/版本/时间/Artifact ID。两类各最多 8 个，截图最多 1 MiB、Trace 最多 4 KiB、截图调用最多 5 秒；无头真实 Processor 与隐藏 App 原入口均已通过；
- H8-11 把 Control Plane、Executor、Rust Manager 与 App 诊断读取收敛到同一公共脱敏 fixture v2：服务端在 handler 前移除动态参数、异常、stack、请求目标和私有 pathname，关闭 Uvicorn access log；Python/Rust 独立清除凭据、Header/Cookie、完整 URL、页面/DOM/评论/私信内容和本机私有路径。真实冻结 Executor stderr 与隐藏 App 正式 `get_executor_diagnostics` 原入口均已验证。常规 Playwright 已在全局配置固定无头，并由跨目录门禁阻止新的有界面例行测试；
- Executor `onedir` 已有 v1 签名 Manifest：离线构建工具清点入口和每个普通文件的相对路径、大小与 SHA-256，以确定性目录摘要绑定版本、构建 ID、macOS/Windows 和 aarch64/x86_64，再对 canonical Manifest 原始字节生成独立 `atems1` Ed25519 签名。签发私钥只从 stdin 读取且不落盘；非规范路径、symlink、非普通文件、文件替换竞态、超限或错误入口均拒绝；
- Rust 原生包验证器已用可信 Ed25519 公钥先验签，再 exact-field 解析 canonical Manifest，绑定当前 OS/架构，以 `semver` 允许范围和已安装版本拒绝越界/降级，并两次枚举整目录、稳定打开逐文件复算大小/SHA-256/目录摘要；错误 signer、弱公钥、目录增删篡改、symlink、非普通文件和竞态均 fail closed。该能力没有 React/Tauri Command 或在线下载面；macOS arm64 与 Windows x86_64 原生 runner 均已实测，Hosted Windows CI 的 Billing 限制只保留为持续集成覆盖缺口；
- E4-15 已把 `127.0.0.1:1420` 与 devCSP 从正式 Tauri 配置拆到仅 `pnpm tauri:dev` 合并的覆盖文件；release 缺失、畸形或弱 Executor 验证公钥会在打包前 fail closed。实际 macOS arm64 与 Windows x86_64 release 二进制及无默认特性 Cargo 依赖树已经扫描，不含 WebDriver/WDIO、验收 Command、测试 origin/Sidecar、开发验证公钥或调试端口；验收只使用临时公开公钥和唯一临时 target，不启动 App；
- Demo Bootstrap 已建立最多 7 天、精确环境绑定、只允许 installation 注册的 fail-closed 能力模型，不能作为业务 API 凭据；该能力只保留用于本地验收、隔离测试和明确迁移，不作为客户安装、配对或审批机制；
- React 工作台已通过 TanStack Query、严格公开 Task DTO、快照权威事件投影和 Rust SSE → Tauri Channel 展示当前/最近任务、运行状态与基础指标；“新建任务”提供受约束的抖音搜索曝光表单。运行详情展示权威状态、进度、事件时间线与目标级待执行/进行中/成功/跳过/失败/不确定证据，并通过四个固定 Rust operation 提交暂停、恢复、取消与紧停，最终仍以 Executor/PostgreSQL 事实收敛；
- 尚未部署任何服务或执行真实社交平台动作。
