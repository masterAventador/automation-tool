# 自动化运营工具后端架构

> 状态：当前项目实现基线
> 建立日期：2026-07-18
> 适用范围：Python Control Plane、Local Executor、RPA、协议、数据、部署与测试

## 1. 建设目标

后端采用 Python，但不是一个混合进程。它由两个部署位置和职责完全不同的部分组成：

```text
Control Plane（本地开发 / 云端 Demo）
  负责任务、事件、配置、安装实例、后续内容与 AI

Local Executor（永远在用户电脑）
  负责 Chrome/Edge、微信、OCR、文件、平台登录态和真实副作用
```

目标是让开发环境和客户 Demo 只切换 Control Plane `baseUrl`，不迁移业务代码或 RPA 实现。

## 2. 核心技术栈

### Control Plane

- Python；
- FastAPI：REST、SSE 和 Executor WebSocket；
- Pydantic：请求、响应、事件和执行协议；
- SQLAlchemy + Alembic：PostgreSQL 数据访问与迁移；
- asyncpg：PostgreSQL 异步驱动；
- OpenTelemetry：API、任务和执行链观测；
- pytest：单元、集成与契约测试；
- Docker：本机依赖和云端 Demo 部署。

### Local Executor

- Python；
- Playwright async API：外部 Chrome/Edge 自动化；
- Pydantic：执行协议；
- SQLite：仅保存本机执行幂等、恢复和 Artifact spool 元数据；
- Windows UI Automation / macOS Accessibility：后续微信实现；
- OCR/OpenCV：只有 UI 结构不足时按平台引入；
- PyInstaller `onedir`：随 Tauri 安装包分发。

MVP 不引入 Redis、Celery、Temporal、LangGraph 或通用 Agent 框架。只有真实出现多副本连接路由、长周期可靠编排或高并发后再评估。

## 3. 总体架构

```text
Tauri App
   │
   ├── HTTP/SSE ───────────────> Control Plane / FastAPI
   │                               │
   │                               ├── PostgreSQL
   │                               └── Executor Connection Registry
   │
   └── 启动与监管
           │
           ▼
      Local Executor ──outbound WebSocket──> Control Plane
           │
           ├── Playwright ──> 外部 Chrome/Edge + 独立 Profile
           └── UIA/AX/OCR ──> 微信客户端（后续）
```

开发环境中的 Control Plane 运行在开发机；客户 Demo 的 Control Plane 运行在服务器。Local Executor 始终不变。

## 4. Control Plane 职责

Control Plane 负责：

- 健康、版本和兼容范围；
- 安装实例和设备凭据；
- 任务创建、参数校验和状态机；
- 目标预览的服务端任务记录；
- 风险策略、频控授权和执行命令；
- Executor 连接、心跳和命令投递；
- 结构化事件、任务快照和结果；
- 幂等、截止时间、取消和结果不确定；
- 诊断元数据和脱敏审计；
- P2 素材、内容、发布和 Provider；
- P3 模型、AI 员工和工作流。

Control Plane 不负责：

- 直接启动 Playwright；
- 读取浏览器 Profile/Cookie；
- 操作用户文件、鼠标、键盘或微信窗口；
- 保存平台 Cookie、验证码或完整页面内容；
- 假设自己能看到 Executor 所在设备的本机路径。

## 5. Local Executor 职责

Local Executor 负责：

- 向 Tauri 证明版本、平台和健康状态；
- 通过出站连接注册在线状态和心跳；
- 接收版本化执行命令；
- 在每次外部副作用前重新校验命令、截止时间和本机硬限制；
- 管理系统 Chrome/Edge 和 App 独立运营 Profile；
- 检测平台登录态并请求扫码或人工接管；
- 搜索、目标采集、预览、动作执行和结果验证；
- 保存最少量本机执行账本和诊断 Artifact；
- 上报结构化进度、结果和脱敏错误；
- 响应暂停、取消和紧急停止；
- App 退出或进程异常时安全清理浏览器和桌面自动化资源。

Local Executor 不负责：

- 产品业务 API；
- 用户、套餐或工作流定义；
- 生成服务端任务 ID；
- 修改服务端风险策略；
- 直接信任网页文案为最终成功；
- 在无法确认外部结果时自动重放。

### 5.1 旧 Executor 迁移边界

E4-01 对旧仓库提交 `a01cfc9aa93e87e71b78b73eee3e07a3b9d31061` 的结论是：只重用可由测试证明的失败语义，不复用旧产品协议或聚合架构。

- 可按当前契约重写的只有进程生命周期、stdin 高熵 bootstrap、后台退出检测、有界重启、超时终止、跨平台进程树清理和 stderr 脱敏限界；分别由 E4-02、E4-06～E4-10 承接；
- `current_exe + --social-operations-sidecar`、同步逐行 JSON 任务通道、任意 `serde_json::Value`、通用 capability Command 和固定 ACK 假 Sidecar 全部删除；
- 旧协议的 `tenant_id`、`approval_id`、`audit_correlation_id`、Core Artifact、RBAC、Entitlement 和 `SocialOperationsRuntime` 不进入当前仓库，也不建立兼容 Adapter；
- 当前 I2-10～I2-13 Executor v1、Installation 作用域、Task/Attempt/Action/Event 和出站 WebSocket 是唯一正式边界；stdin 只传一次性本机 bootstrap；
- E4-11 从当前需求新建 command/idempotency/checkpoint/outbox 账本，不能迁移旧账号或设备服务的数据模型。

完整逐文件证据和删除映射以 `docs/project-structure.md` 第 10.2 节为准。

## 6. Control Plane 分层

```text
api
  ↓
application
  ↓
domain
  ↑
infrastructure
```

### 6.1 API

- FastAPI 路由和 WebSocket 入口；
- 产品账号与安装实例认证；
- 输入校验、请求大小、超时和错误映射；
- 关联 ID 和协议版本；
- 调用应用服务；
- 不写 SQL，不做任务状态决策，不调用 RPA。

### 6.2 Application

- 任务创建和命令编排；
- 目标预览与确认用例；
- Executor 连接与命令投递；
- 事件接收、快照更新和结果收敛；
- 暂停、恢复、取消、紧停和人工接管；
- 仓储、事件和时间端口的事务协调。

### 6.3 Domain

- 任务状态机；
- 风险与频控规则；
- 幂等和执行尝试；
- 结果确定性；
- 事件类型与转换；
- 只依赖 Python 标准类型和领域端口。

### 6.4 Infrastructure

- PostgreSQL 仓储；
- 事件 outbox 和持久化；
- Executor 在线连接注册；
- 对象存储和外部 Provider；
- OpenTelemetry 和结构化日志；
- 不能反向定义业务语义。

当前数据库基线固定为 SQLAlchemy asyncio + asyncpg。进程只持有一个 engine，应用用事务作用域 session；正常退出由 FastAPI lifespan 释放连接池。数据库 URL 只从 `AUTOMATION_TOOL_DATABASE_URL` 读取并要求 `postgresql+asyncpg://`，缺失或非法时启动 fail closed，错误不得回显凭据。Alembic 使用同一受校验配置，迁移文件和 `alembic.ini` 不保存连接信息。

## 7. Executor 分层

```text
bootstrap
  ↓
application
  ↓
rpa/base ports
  ↑
browser/desktop infrastructure
```

### 7.1 Bootstrap

- 读取 Tauri 通过 stdin 提供的 bootstrap；
- 校验一次性本机会话令牌；
- 读取受控 Control Plane 端点和短期设备能力；
- 启动出站连接和健康循环；
- 安装信号处理和有界停止；
- 令牌、私有路径和原始异常不进入日志。

E4-02 已实现该层的最小正式入口 `automation-tool-executor`。stdin bootstrap 只允许一条换行结尾、最多 16 KiB、无重复 key/未知字段的 JSON object；字段固定为 bootstrap 版本、受控 WebSocket URL、本机启动令牌、短期 `executor.connect` Session、Installation/Executor UUIDv4、心跳间隔和 Rust 提供的 App 私有 Executor 状态目录。`ws` 只允许 `127.0.0.1` 有效端口，远端只允许标准端口 `wss`；两个 Session 用途隔离并分别只驻留在秘密类型中，不进入 argv、环境、stderr 或异常。状态目录只允许绝对、非根、无 `..`/控制字符的有界路径；E4-13 已从 Tauri `app_data_dir/local-executor/state` 固定派生，完全不相信 React 输入。

E4-14 已用唯一隐藏 Tauri App 经正式 Rust client 连接动态 loopback Control Plane，并以真实 PostgreSQL、短期 Session、WebSocket 和 signed PyInstaller Executor 验证启动、异常恢复、挂起停止、再次启动及 App 退出清理。动态 origin 和 OS 故障注入只存在于 `control-plane-e2e` 编译，不改变服务端生产协议或部署拓扑；生产仍由固定 Profile/BaseUrl 连接独立部署的同一 Control Plane。最终数据库只出现 `app.control-plane` 与 `executor.connect` 两类最小能力，秘密不进入 Executor SQLite 或服务端日志。

E4-15 不改变 Control Plane 协议或部署边界。桌面 release 在打包前强制绑定非开发 Executor 验证公钥，并对实际二进制/依赖树排除验收 Command、测试 origin、WebDriver 和调试端口；因此服务端不能通过配置把测试能力重新打开。正式公钥是公开信任根，不是签发私钥或设备秘密；验收公钥只进入随即删除的临时制品，真实发布仍必须由打包流水线注入对应发布 signer 的公钥。

E4-10 增加 Python `executor/diagnostics.py`，与 Rust 回放同一 `executor-diagnostics-v1` fixtures，固定清除凭据/Cookie、URL userinfo/query、data/file URL、私有路径和控制/Bidi 字符。该模块为后续 Executor 结构化安全消息提供单一规则，但不是信任捷径：Tauri/Rust 仍把整个 Python 进程视为不可信，对原始 stderr 在读取阶段重新限界和脱敏。Python 当前正式 CLI 仍只输出既有固定错误，不新增任意异常或秘密日志。

E4-11 增加 Python `executor/ledger.py`，只使用标准库 `sqlite3` 并在正式 CLI 联网前打开。初始 `PRAGMA user_version=1` 创建 identity、commands、attempt checkpoints、outbox 四表；B5-12 以排他事务迁移到 v2 并增加四列平台 Session 健康表；D6-10 再迁移到 v3，把 `task.discover` 及 discovery batch/completed 纳入同一命令与 outbox 精确重放边界。数据库绑定唯一 Installation/Executor，未来版本、缺表/损坏、身份错绑、symlink/reparse point、宽权限、非普通文件和打开 identity 变化全部 fail closed。命令以 message ID、idempotency key、32 字节意图 SHA-256 和 Attempt 连续 sequence 去重；checkpoint 以 revision/CAS 和单调 event sequence 更新；outbox 只接受正式结果/事件/发现消息并保留精确 wire 重放身份。SQLite 不保存 bootstrap/Control Plane Session、Cookie、浏览器登录数据、密钥、页面原文或任意配置，不调用系统钥匙串，也不替代云端 PostgreSQL 权威状态。

进程从自身运行环境确定 macOS/Windows 与 arm64/x86_64，向真实 Control Plane 发送正式 Hello，连接存活后按单调 sequence 发送 Heartbeat；首条心跳后 stdout 只投影固定 `executor.healthy`，SIGINT/SIGTERM 后关闭 WebSocket 并投影 `executor.stopped`。E4-12 已接入无副作用命令回放：只接受身份/deadline 合法的 `task.offer`，先持久 receipt，再以一个 SQLite 事务提交 terminal checkpoint 与固定 `task.accept` 加五条 success Event；其他命令和非法帧继续 fail closed。

`executor/command_processor.py` 不复用 FakeExecutor 内存状态。message/idempotency 命中同一意图时读取首次持久 outbox；生成中断只保留 received checkpoint；并发提交失败时只接受已出现的赢家 outbox。runtime 在每次 Hello 后把已发送 outbox 重新排队并按 ordinal 发送，每帧成功写入 WebSocket 后才标 delivered，所以崩溃/部分发送只会重放原 ID/幂等键/正文。正式 E4-12 编排已用同一 SQLite 状态目录两次启动 signed PyInstaller Executor，Control Plane 的 acknowledged command 与五条 PostgreSQL Event 快照保持不变。该路径仍不执行浏览器、微信或平台账号副作用，后续 Adapter 才接入真实动作。

E4-03 将该入口锁为 PyInstaller 6.21.0 `onedir`：spec 直接执行 `executor/__main__.py`，冻结产物不依赖用户另装 Python；该任务完成时尚未加入 Python Playwright。B5-07 现已把 Playwright 1.61.0 作为正式运行依赖并由 spec 收集 Python driver，同时明确不执行浏览器安装、不把任何 Playwright 浏览器缓存塞入包。macOS arm64 与 Windows x86_64 本机均从冻结的生产 `browser_runtime.py` 以显式系统浏览器、私有 Profile、headed persistent context 完成真实启动/关闭；测试专用探针不属于正式入口，业务任务仍不会执行浏览器。GitHub macOS/Windows 矩阵使用同一实包验证；Hosted Windows Runner 仍受账户 Billing/Actions spending limit 限制，但实体机原生验收已补齐。目录 Manifest、签名、完整性和防降级仍由 E4-04/E4-05 承担，正式资源所有权由 B5-08 承担。

E4-04 增加唯一离线构建入口 `automation-tool-build-executor-manifest`。它只从 stdin 读取精确 32 字节 Ed25519 seed，拒绝把发布私钥放入 argv、环境、输出、仓库或 App；输出是 `onedir` 根内的 `executor-manifest.v1.json` 与 `executor-manifest.v1.sig`。Manifest v1 精确绑定 SemVer、受限 build ID、`macos|windows`、`aarch64|x86_64`、平台精确入口、payload 总大小、目录摘要，以及按 ASCII 相对路径排序的全部普通文件路径/大小/SHA-256；Manifest/签名 metadata 自身不参与 payload 清单。

目录摘要以 `automation-tool.executor-package.v1\0` 起始，随后对每个已排序文件依次加入 4 字节大端路径长度、ASCII 路径、8 字节大端文件大小和 32 字节原始 SHA-256。Manifest 是键排序、compact ASCII JSON 加一个 LF；Ed25519 签名覆盖其完整原始字节，签名 envelope 固定为 `atems1.<unpadded-base64url>\n`。构建器拒绝非规范/过长路径、symlink、非普通文件、超过 10,000 个文件或 8 GiB、空/错误入口，并在读取前后核对文件 identity，避免把验证期间被替换的文件写入可信清单。Draft 2020-12 Schema 和固定测试 seed 生成的 inert 跨语言 fixture 已提交；测试 seed 不是发布密钥。E4-05 仍必须在 Rust 中用编译期可信公钥解析 exact fields、复算全部摘要、绑定当前平台/架构、执行防降级并 fail closed，不能把 E4-04 的离线生成当成运行时信任。

E4-06 把本机启动认证与 Control Plane 设备认证彻底分开。Rust `executor_bootstrap.rs` 使用系统 CSPRNG 生成精确 256-bit、不可克隆且 Drop 清零的 `LocalSessionToken`，其 canonical 小写十六进制只随完整 JSON 写入子进程 stdin；模块没有 argv、环境、日志、Tauri Command 或网络发送入口。Python `authentication.py` 严格解析 64 位小写十六进制到可清零 bytearray，并为 `executor.healthy`/`executor.stopped` 生成 `atlep1` HMAC-SHA-256：输入以固定域、事件名和 `1.0` 协议版本绑定，响应从不包含原令牌。Rust 使用同一固定跨语言向量和 `hmac::Mac::verify_slice` 常量时间校验；E4-07 Manager 必须持有每次启动对应的令牌并在接受健康状态前验签，旧进程证明不能跨启动或跨事件复用。

### 7.2 Application

- 领取并校验命令；
- 恢复或创建执行尝试；
- 驱动平台 Adapter；
- 在安全检查点响应控制命令；
- 写本机执行账本；
- 上报事件与结果。

### 7.3 RPA Adapter

平台公共接口至少包含：

```python
class SocialPlatformAdapter(Protocol):
    async def diagnose(self, context: ExecutionContext) -> DiagnosticReport: ...
    async def check_session(self, context: ExecutionContext) -> SessionHealth: ...
    async def request_login(self, context: ExecutionContext) -> LoginChallenge: ...
    async def discover_targets(self, request: DiscoveryRequest) -> AsyncIterator[TargetCandidate]: ...
    async def verify_target(self, candidate: TargetCandidate) -> VerifiedTarget: ...
    async def execute_action(self, action: AuthorizedAction) -> ActionReceipt: ...
    async def verify_outcome(self, receipt: ActionReceipt) -> OutcomeVerification: ...
    async def capture_evidence(self, input: EvidenceRequest) -> LocalArtifactRef: ...
    async def cleanup(self, reason: CleanupReason) -> None: ...
```

抖音、小红书等只能通过该接口接入，不得修改任务引擎来适配某个页面。

## 8. 浏览器运行时

### 8.1 主方案

```text
系统已安装 Chrome/Edge 可执行文件
       +
App 私有 browser-profile/<platform>/<profile-id>
       +
Playwright headed persistent context
```

- 浏览器在 App 外部显示；
- 不使用 Tauri WebView 承载平台网页；
- 不指向用户默认 Chrome `User Data`；
- 每个平台 Profile 隔离；
- 同一 Profile 同时只允许一个执行实例；
- 首次扫码后复用独立 Profile 登录态；
- Executor 只上报平台、健康、过期和 revision，不上传 Cookie。

B5-01 已明确不复用旧 `device_account_service` 的 tenant、owner、RBAC、Entitlement 或云端账号模型。当前 Profile ID 是 App 本机生成的 canonical UUIDv4，不是产品账号；Session 健康由真实页面封闭为 `missing/healthy/expired/risk/unknown`，只有 `healthy` 允许后续动作。B5-12 已把该事实接入正式 Executor WebSocket：本机 SQLite v2 为每个平台持久化正数、单调递增的 `session_revision`，同 epoch 的旧观察或从非健康状态直接回到健康均拒绝，重新登录或显式恢复必须推进 epoch。Control Plane 只在 `platform_session_health` 保存 Installation、平台、状态、revision、观察时间和更新时间六列；较低 revision、倒序观察和同 epoch 非健康→健康同样 fail closed，不保存 Cookie、二维码、验证码、页面原文、Executor/message ID 或 Profile 路径。

B5-13 在该投影上增加唯一已实现的查询 `GET /api/v1/platform-sessions/douyin`。路由复用 `require_current_installation_access` 和 `app.control-plane` Session，只读取当前 Installation 的 PostgreSQL 记录；缺项返回 `unknown/null`，有记录只返回平台、封闭状态和观察时间，并固定 `no-store`。打开登录处理和重新检查不是云端 HTTP 动作：Tauri Rust 在本机重新解析受信浏览器、稳定 current Profile 与独占 lease，经同一个 signed Executor 的认证 stdin/stdout 命令调用 Python `DouyinQrLoginFlow`，再由该进程的正式 WebSocket 上报投影。Control Plane 不能下发可执行文件、Profile 路径、`headless`、页面句柄或扫码结果，React 也不能直接连接 Executor。

B5-14 已实现跨边界安全注销。`POST /api/v1/platform-sessions/douyin/logout/prepare` 在 active Installation 行锁下创建或复用 `platform_session_gates`，revision 为当前投影 +1（无投影为 1）；门闩存在时 Task create 与新 offer 都 fail closed，同键既有 Task/command 重放仍保持幂等。command claim 也先锁同一 Installation：已排队、in-flight 待恢复或 delivered 待重投的工作命令不再取出，仅 `task.cancel`/`task.emergency_stop` 终止命令可继续投递。`missing` 不解除门闩，只有更高 revision 的真实 `healthy` 上报才能恢复新工作。App 在 prepare 后停止唯一 Executor/浏览器树并释放 Profile 锁，定向删除 current Profile，再重启 Executor 发送 path-free `douyin.logout.complete` 推进本机 epoch并经正式 WebSocket 上报 `missing`；最后必须重新查询到权威 `missing` 才成功。停止失败不删除 Profile，删除或上报失败不伪报完成且持续阻断新任务。

### 8.2 浏览器发现

按平台检测稳定路径：

- macOS：Google Chrome、Microsoft Edge；
- Windows：注册表和标准安装位置；
- 用户可在诊断页选择一个受支持浏览器；
- 路径必须解析为允许的浏览器应用/签名，不能执行任意文件；
- 未安装受支持浏览器时返回明确诊断，不静默下载未知程序。

B5-02 已实现 Rust macOS 原生信任边界：只检查 `/Applications/Google Chrome.app` 与 `/Applications/Microsoft Edge.app`，分别绑定固定内部可执行文件、`com.google.Chrome/EQHXZ8M8AV` 与 `com.microsoft.edgemac/UBF8T346G9`。Apple Security.framework 直接验证 vendor requirement、sealed resources、所有架构和嵌套代码；应用与入口的 dev/inode 在验证前后保持一致，并在实际使用前通过公开 API重新验签。不存在的标准应用不返回，存在但坏签名、错误 Bundle/Team、不完整、symlink 或路径被替换则 fail closed。当前本机真实 Chrome 已通过，Edge 未安装仍保留真实验收；该模块不调用浏览器、不读取默认 Profile，也不把路径暴露给 React 或 Control Plane。

B5-04 的浏览器选择是纯本机 App 设置：Control Plane 没有查询、写入或同步接口，也不会接收浏览器枚举、安装路径、Profile 路径或本机选择。Tauri Command 只在本机受信发现结果中保存一个固定枚举，后续 Executor/BrowserRuntime 使用前仍必须重新发现和复验，服务端下发任务不能覆盖该设置。

B5-08 的正式 `BrowserRuntime` 位于 Python Local Executor，不在 Control Plane 或 React。一个实例只能在创建它的线程拥有一个 headed persistent context；默认动作/导航超时分别为 15/30 秒，捕获新窗口只接受 1～60000 ms 显式上限。主窗口、当前窗口、新窗口和触发式弹窗都包装为 Runtime-owned `BrowserWindow`，外来/已关闭窗口、跨线程调用、重复启动和关闭后调用全部 fail closed。Runtime 在每次启动前重验 B5-07 路径形状，正常关闭先关闭 context 再停 driver；即使任一关闭失败也执行另一项。macOS 冻结实包已用 B5-02 受信 Chrome、B5-05 Profile 与 B5-06 锁验证双窗口和完整 process-group 强杀；Windows 冻结实包已用 B5-03 受信系统浏览器、私有 Profile 与原生锁验证正常关闭，并由公开 ExecutorManager 的 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 逐个清理真实浏览器后代 PID。页面原始对象仅在 Python 平台 Adapter 内可见，不能通过 WebSocket payload 或 Tauri IPC 传路径/页面句柄。

B5-09 在同一 Python 边界新增抖音 Session detector：调用方先把 Runtime-owned 页面导航到固定受保护入口 `https://www.douyin.com/user/self`，检测器再用版本化、有界的页面选择器产生 `healthy/expired/missing/risk/unknown` 和固定 evidence。官方 origin 采用精确 HTTPS host/port 校验；ByteDance 验证中心 iframe、登录过期、用户资料壳和登录入口分别映射到 `risk/expired/healthy/missing`，来源冲突、DOM 漂移、页面异常和非官方来源统一 `unknown`。`circuit_open` 只在 `healthy` 时为 false。真实 macOS Chrome 已用用户授权的已登录持久 Profile 验证 `unknown → healthy`，并用空白 Profile 和实际风控页验证未登录/风险边界；检测过程不读取 Cookie、Local Storage 或 storage state，不返回页面原文、账号或 Profile 路径。B5-10 组合本机扫码页面工作流，B5-13 再建立 App 原入口。

D6-01 在该 Executor 页面边界新增唯一 `douyin.web.v1` route contract。首页、Session probe 和 general 搜索结果分别只接受 canonical `/`、`/user/self`、`/search/<canonical encoded term>?type=general`；精确 HTTPS host、端口、userinfo、fragment、长度、路径、查询和 percent/UTF-8 编码共同决定兼容性。合法 version/entry/evidence 组合在不可变对象创建时锁死，未知来源、路径或搜索形状只能成为 `unknown/circuit_open`，`require_entry()` 对预期不一致立即拒绝且不反射 URL。当前空白私有 Profile 的无头实探只证明 origin/route，`#root` 不被视为 DOM 版本锚点；页面版本不能由 Task、Control Plane、Rust 或 React 自报。

D6-02 在同一边界新增唯一 `douyin.search-page.v1` Page Object。它先消费 D6-01 的 route/version 观察，再按 role/label/placeholder 等可访问语义和版本化 `data-e2e` 依次确认搜索入口、结果列表、登录弹窗与普通阻塞弹窗；合法事实封闭为 `home_ready/results_ready/login_required/dialog_blocked/unknown`。登录弹窗优先于通用 dialog 外壳，阻塞弹窗优先于搜索锚点；route 与锚点冲突、锚点不完整、页面异常或二次读取时 DOM 已变化都立即拒绝。该对象只暴露经再次可见性确认的窄 Locator，不执行导航、点击、输入、滚动、脚本或任何存储读取；D6-04 才能在 Task 执行层通过这些受控入口实现搜索动作。

D6-03 把 T3-17 任务定义中的发现输入抽成公共 `douyin.search-input.v1`。Python `protocol/douyin_search.py` 是 Control Plane 与 Executor 唯一共享实现，封闭持有原样关键词和目标上限：Unicode code point 为 `1..80`、首尾不得有 Unicode 空白、C0/C1/DEL/Bidi 与安全文本违规拒绝，目标数为真整数 `1..100`。Control Plane 领域对象必须先构造该值，D6-04 Executor 搜索执行也必须从公共 `automation_tool.protocol` 导入，双方不得复制规则；对象与错误表示始终隐藏关键词。OpenAPI、PostgreSQL、React 与 Rust 在各自信任边界保留独立复验，跨语言契约锁定同一数值和 Unicode/C1 语义。

D6-04 新增 Executor-only `douyin.search-execution.v1` 单次状态机。输入只能是 D6-03 已构造值，DOM 动作只能从 D6-02 Page Object 的二次可见性确认获得；执行顺序固定为 canonical 首页 `domcontentloaded`、最多 10 秒等候完整搜索入口、原样填写、一次无隐式导航等待的提交、最多 30 秒等候由 D6-01 生成的精确关键词结果 URL、最多 10 秒等候并复验结果列表。导航/动作/URL/锚点超时分别保留固定 evidence；登录、普通弹窗、版本未知、锚点冲突和页面异常均开路停止，同一对象拒绝重跑，因此提交后不确定时不会重复点击。该层没有 Task/Attempt、Control Plane、滚动、评论、私信、脚本执行或存储读取；后续 D6-05/D6-10 分别承接结果滚动与正式命令闭环。

D6-05 在同一 Executor 页面边界新增 `douyin.bounded-scroll.v1` 单次控制器。它必须同时拿到 D6-04 成功观察与当前结果页的 D6-02 权威事实，不能仅凭 URL 或调用方布尔值开始。滚动上限固定 20 轮，每轮仅发送一次纵向 800 像素 wheel；Page Object 用版本化结果项 selector 只返回裁剪至 D6-03 `target_limit` 的节点数，控制器在每轮 3 秒总窗口内按 100ms 间隔等待增长。达到目标、一轮无新增或 20 轮持续增长分别形成三个完成 evidence；开始、每轮前、等待循环和增长后均调用无参数取消检查点。取消源异常/非 bool、计数倒退、登录/弹窗、页面/版本漂移与 Playwright 失败均保持熔断，不增加下一轮。该层仍不读取候选字段或正文；D6-06 才定义稳定 Candidate 和累计去重语义。

D6-06 把候选事实定义在公共 `protocol/douyin_candidate.py`。`DouyinCandidate` 只持有规范平台目标 ID、`display_name/public_handle` 最小摘要、唯一 `general_search_author` 来源和 I2-10 同范围的 page revision；构造时确定性生成不可由调用方覆盖的去重键。键算法固定为 `SHA-256("automation-tool.douyin.candidate-key.v1\0" + ASCII target id)` 后 Base64URL 无 padding，并加 `atdck1_` 前缀；名称、handle、来源展示或 revision 变化不影响键。所有值不可变且 repr/error 脱敏，模型不提供头像、简介、联系方式、页面原文或 URL 字段；D6-07 负责页面隐私裁剪，D6-09 持久化最小事实，D6-10 的 Executor Envelope 只传公开字段且不传 dedupe key。

D6-07 新增 Executor-only `douyin.candidate-extraction.v1` 单次边界。执行器先复验 D6-02 结果页状态，再只调用 Page Object 的 `candidate_items(maximum, page_revision)`；选择器、节点遍历、`data-user-id`/`data-user-handle` 和作者 href 读取都不进入执行层。Page Object 只接受相对 `/user/<id>` 或无 userinfo/fragment 的官方 HTTPS 作者链接，在本机取出 path ID 并丢弃完整 href/query；如果同时存在 data ID 与 href，两者必须相同。显示名只从专用作者名节点读取并裁剪外围空白，其他页面正文、HTML、头像、简介、联系字段、Cookie/storage 从不读取。任一项非法、跨域/凭据链接、字段冲突、DOM 读取异常或提取中页面漂移都丢弃整个 tuple 并返回固定脱敏 evidence；没有部分结果、自动重试或上传动作。Candidate 仍未加入 Executor wire/IPC/数据库，D6-08 才对该最小集合做本任务/历史去重与黑名单判断。

D6-08 在 Control Plane 领域层新增纯 `douyin.candidate-policy.v1`。MVP 的不可放宽历史去重窗固定为 30 天，以 `evaluated_at - 30 days <= observed_at <= evaluated_at` 判断；时钟和历史事实必须为 UTC，未来事实或发生 datetime 下溢一律拒绝。输入集合均为 exact tuple 且各限 100 项，一个页面快照只能有一个 revision；历史和黑名单只携带稳定 Candidate key，且 lookup key 必须唯一，不接收平台 ID、摘要或任意理由文本。评估按原顺序输出全量 Decision，不删除排除项；同一 key 固定采用黑名单、本任务后续重复、窗口内历史重复、可用的优先级。公开 Evaluation 还会拒绝首条伪造为本任务重复、后续重复伪造为可用/历史重复和跨 revision 组合。策略自身始终不查询或写入数据库、不进入 Executor wire/Tauri IPC；D6-09 的仓储负责提供 Installation-scoped 历史事实并持久化 Target，黑名单 key 由当前已认证用例提供。

D6-09 以 Alembic `20260718_0016` 增加 `task_targets`。每行只持有 UUIDv4 Target 身份、父 Task/Installation、任务内 ordinal、D6-06 Candidate 的规范目标 ID/稳定 key/最小摘要/来源/page revision、D6-08 disposition/policy version 与 UTC 评估/创建时间；数据库对长度、字符集、范围、封闭枚举、时间顺序和 `(task_id,installation_id)` 父归属再次约束，不允许自由 JSON、页面正文、绝对链接或附加个人资料。`(task_id,installation_id,ordinal)` 唯一保证稳定显示顺序；dedupe key 不设唯一，因为策略必须保留并展示同任务后续重复行。`(installation_id,task_id,page_revision,ordinal,id)` 支撑预览 keyset，`(installation_id,dedupe_key,evaluated_at)` 支撑历史查询。

`SqlAlchemyTaskTargetRepository.evaluate_and_replace` 先以 Installation 行锁串行化同一安装实例的历史视图，再锁精确 Task；只有 active Installation 和不早于父 Task 的非空单 revision 快照可进入。仓储只为本批 Candidate key 聚合同 Installation、排除当前 Task 的最新历史时间，调用 D6-08 后在一个事务中删除旧快照并写入全部 Decision。新 page revision 必须严格大于已保存 revision；并发新旧快照最终只能保留较高 revision，不会混批。列表按 `(ordinal ASC,id ASC)` 做有界 keyset，并在每次查询同时约束 Installation、Task 和 page revision；其他 Installation、其他 Task 或旧 revision 均不可见。D6-10 已从发现完成收敛事务调用该仓储；D6-11 再接公开预览 API。

D6-10 新增 `TaskDiscoveryStartService`、`TaskDiscoveryConvergenceService` 与唯一 `SqlAlchemyTaskDiscoveryRepository`。`POST /api/v1/tasks/{task_id}/discoveries` 复用 `app.control-plane` Session，只在 active Installation、健康抖音 Session、无 logout gate 且 Task 状态可达时启动；一个事务创建 Attempt、`task.discover` 持久命令、`task.discovery_started` 事件并把 Task 推进到 `discovering_targets`。命令 payload 由已持久化 Task 定义生成，不接受客户端提供关键词、Target 或浏览器事实；Idempotency-Key 同任务精确重放返回原 command，跨任务复用拒绝。

Executor 的 `browser_authority.py` 把登录窗口创建的受信浏览器请求收敛成单一可撤销 lease；`discovery_operation.py` 只有拿到健康本机 Session 与该 lease 后，才能依次调用 D6-04 搜索、D6-05 有界滚动和 D6-07 最小提取。`ExecutorCommandProcessor` 先将 `task.discover` 写入 D6-10 当时的 SQLite v3，再把最多 100 条 Candidate 拆成每批最多 10 条的 `task.discovery_batch`，最后写入唯一 `task.discovery_completed`；命令结果、所有批次与完成事实在一个本机事务进入 outbox，重启只精确重放，不重跑浏览器动作。A7-04/A7-07 已在保留全部命令、checkpoint、outbox 与平台 Session 的前提下依次原地迁移到 v4/v5，并增加动作策略、紧停、准入与最小副作用事实。

Control Plane 只在认证 Executor WebSocket 接收 batch/completed。内存 accumulator 最多保留 32 个 Attempt，要求 batch 从 1 开始、连续、总批数一致且同批重放指纹完全一致；每批先经 PostgreSQL 核对当前已确认 discover command。completed 到达后复验候选数、page revision、Installation/Task/Attempt/correlation 和 deadline，在一个事务调用 D6-09 替换 Target、完成 Attempt、追加事件并把 Task 收敛到 `awaiting_confirmation`。缺批、乱序、篡改和过期拒绝；登录失效、人工接管或失败不保存 Target，并分别投影到封闭状态。断线后的完整 batch/completed 重放必须与已持久化 Target 和 command 精确一致。

D6-11 新增 `TaskTargetPreviewService` 与唯一 `SqlAlchemyTaskTargetPreviewRepository`。读取只返回同 Installation/Task 最新单一 page revision，按 `(ordinal,id)` 做 revision-bound keyset；API 只投影 Target UUID、ordinal、最小公开摘要、封闭来源/策略 disposition、用户排除与选择状态，不返回平台目标 ID、dedupe key 或页面事实。排除入口在 active Installation、`awaiting_confirmation` Task 与精确 page/task revision 行锁内完整替换用户排除集合，只允许排除 `eligible` Target，并追加 `task.target_selection_updated`。确认入口至少要求一个最终选择，持久化 page/selection/confirmed revision、选择数量、幂等指纹和 UTC 时间，追加 `task.targets_confirmed` 并把 Task 原子推进到 `queued`；同键精确重放可读，改体、过期、跨 scope、并发输家、损坏重放和空选择全部拒绝。迁移 `20260720_0018` 增加 `task_target_exclusions`、`task_target_confirmations`、Target 预览复合绑定与两种事件；重新发现替换 Target 前清除旧确认，避免新快照继承旧授权。

D6-13 在动作承载命令离开 Control Plane 前增加第二道确认门。迁移 `20260720_0019` 只给 `task_commands` 增加可空、UUIDv4 且只允许用于 `task.offer` 的 `target_confirmation_message_id`；它引用确认的稳定 source identity，不复制选择列表、页面事实或动作 payload。带 `douyin.search_exposure.v1` 定义的业务 offer 在 enqueue 时必须看到 active Installation、`queued` Task、精确 confirmed revision 和当前 confirmation，并把 message ID 固定进 Outbox；同键重放不能换绑。claim 以 Task/Installation/确认 ID 关联复验确认仍存在、Task revision 未倒退且状态为 `queued/running`，否则行保持 pending 且不增加 delivery attempts。迁移前遗留的无绑定业务 offer、重新发现后失效的旧绑定和篡改行都不会发到 Executor；`task.discover`、pause/resume/cancel/emergency-stop 以及无业务定义的既有无副作用协议骨架不受误拦。该层不是 A7-03 ActionAuthorization，也不允许 Executor 执行真实评论/私信；它只消除“动作协议落地前可绕过用户确认”的投递空窗。

D6-14 在 `ProductionDouyinDiscoveryOperation` 增加页面漂移专用本机证据边界。只有页面层已封闭确认的 `page_version_unknown` 或 `conflicting_anchors` 才触发 `PageDriftArtifactStore`；慢网络超时、登录、普通弹窗、隐私拒绝和一般页面不可用不会伪装成漂移。诊断文件位于 Tauri 已授权的 Executor 私有 state 根内，固定为最多 20 个、单个最多 2 KiB 的 JSON，携带 UUIDv4、SHA-256、固定媒体类型、受控相对路径、平台/阶段/evidence/page revision/UTC 时间；API 不接受自由文本，因此关键词、URL、DOM、页面正文、截图、Cookie、凭据和 Profile 路径无法进入文件。目录替换、未知文件、坏权限/磁盘/ID/时钟或部分写入全部拒绝并尽力移除残片；诊断不可写时仍保持熔断。上述两种 evidence 在 Executor v1 中只能配对 `handoff_required`，Control Plane 只收敛到 `awaiting_human`，不会继续滚动、提取或动作。H8-09 后续把这一专用引用纳入通用 Local Artifact 展示/上传/保留接口，H8-10 才允许受限失败截图或 Trace。

D6-15 不增加第二套 Adapter，而是在 `tests/fixtures/douyin_discovery_pages/` 固定生产页面契约的离线回归语料。所有场景仍从 `ExecutorCommandProcessor.handle(task.discover)` 进入 D6-10 编排，并由真实 `BrowserRuntime`/系统 Chrome 加载官方 URL 的测试路由；测试路由和 HTML 不进入发布包。正常、空结果、普通弹窗、Session probe 跳转、未知官方路径和每次 wheel 持续增长的无界源分别验证成功、`no_candidates`、人工接管、登录接管、D6-14 Artifact/handoff 和 D6-05 二十轮硬停止。语料禁止外部 URL/fetch/Cookie/storage 依赖；D6-04/D6-05/D6-07 浏览器集成测试复用相同首页/结果文件，避免维护另一套内联 DOM。

D6-16 首轮真实只读验收使用此前用户明确授权的 App 私有抖音 Profile 与无头系统 Chrome。`/user/self` 经生产 Session detector 从短暂 unknown 收敛为 healthy，但首页加载 ByteDance verifycenter captcha iframe；这证明“登录 Session 健康”和“当前页面无风控”是两个独立门禁，不能以前者绕过后者。Session 与 Search Page Object 现在共享 `DOUYIN_RISK_CHALLENGE_SELECTORS`，搜索首页/结果页一旦看到验证码 iframe 或 captcha container，优先于业务锚点返回阻塞并经正式 discovery wire 收敛到 handoff，而不是继续等候或点击。真实候选尚未产生，D6-16 继续待挑战由用户正常处理后补验；当前证据不能替代目标预览成功。

A7-01 在 Control Plane 领域层增加 `ActionRiskScope` 与 `ActionRiskPolicy`。scope 以强类型 `InstallationId`、封闭社交平台和既有任务动作组成，令所有计数天然按安装实例/平台/动作隔离；Policy 必须显式提供最小间隔、单任务动作上限、UTC 日动作上限和连续失败阈值，拒绝零间隔、分数秒、bool/float、越界数值、自由字符串和伪造版本。单任务上限复用 `MAX_TASK_TARGET_LIMIT`，日/失败计数的 `2^53-1` 只保证跨运行时无损，不是运营默认或“安全值”。

A7-02 用 `action_risk_authorizations` 保存每次成功授权的不可变策略与计数快照，并在同一 PostgreSQL 事务插入既有 `task_actions(status=authorized)`。事务先锁 Installation 行，因此同一安装实例的并发动作被线性化；随后复验 active Installation、当前 running Task/Attempt、任务定义动作、healthy Session 且没有 logout/risk gate、当前确认 revision、eligible 且未排除 Target。任务上限按 Task/平台/动作计数，日上限按 Installation/平台/动作/UTC 日期计数，最小间隔跨同一 Installation/平台/动作执行并取 `max(任务配置, 服务端硬下限)`。复合外键把 Action/Target/Attempt/Task/Installation/ordinal 锁成一个事实，计数序号唯一约束作为事务并发的第二道防线；任何拒绝、限流或数据库冲突都不留下半条 Action。同一 ActionId 只有完整资源身份与动作一致才返回原事实，意图变化 fail closed。该仓储是服务端内部边界，不接收 App/Executor wire；A7-03 必须由服务端时钟产生 deadline 并签名或 MAC，不能把客户端自报时间当权威授权时间。

A7-03 选择非对称 Ed25519，而不是复用 Executor 已知的本机会话 HMAC：如果 Executor 持有签发 MAC key，它就能自行扩大授权；现在只有 Control Plane 部署边界持有独立私钥，Local Executor 只固定公开验证公钥。`action-authorization.v1` claims 精确绑定 Action、Target、Attempt、Task、Installation、Executor、平台/动作、派生 `action:<action_id>` 幂等键、服务端 `authorized_at` 与最长五分钟 `deadline_at`；canonical `ataa1` token 使用域隔离签名输入、排序紧凑 ASCII JSON、六位 UTC 时间和无 padding Base64URL。解析器拒绝重复/缺失/额外字段、非 canonical 编码、错误资源类型和超限 token；Verifier 先验签，再以最多 30 秒未来时钟偏差和无过期宽限匹配本次完整意图。该签发/验签是 Control Plane/Executor 内部安全边界，没有新增 HTTP、OpenAPI、Tauri Command 或 React 接口。

A7-04 在 Local Executor 增加 `ExecutorActionGate`，把 A7-03 验签和本机 SQLite v4 准入组成逻辑与门。公开 `admit()` 只有 token 与完整 expectation 两个输入；最小间隔和单任务动作上限只能在构造时由本机显式注入，服务端 claims、wire 或调用参数都没有阈值字段。策略单例首次绑定后只允许 `max(当前间隔, 新间隔)` 与 `min(当前上限, 新上限)`，重启、弱配置或并发构造都不能放宽；策略行缺失、半损坏或越界直接拒绝，不能自动重建弱值。

同一个 `BEGIN IMMEDIATE` 准入事务依次检查持久紧停 latch、Action/idempotency 精确重放、Installation/平台/动作的最近准入时间，以及 Task/平台/动作计数，然后写入唯一 ordinal。紧停先于重放和频控，重启后仍保持；清除必须提供当前本机 revision 且使用严格递增 UTC 时间。时钟倒退、数据库损坏、身份错绑和五路并发冲突都 fail closed。SQLite 只存 Action 完整绑定所需的最小 claims、准入时间、ordinal 与 token SHA-256 指纹，不存 token、服务端私钥、Cookie、Profile、页面事实或账号信息。A7-07 已让该准入事实成为副作用账本的强制前置条件；动作 wire、App UI 与真实平台副作用仍由 A7-11/A7-12 承接。

A7-05 将 `action-message-template.v1` 放在共享 `protocol/` 而不是页面定位或 LLM 模块：允许纯固定文案，也只允许 `{{target_display_name}}` 一个变量，且删除合法占位符后必须仍有非空字面。Python 领域先校验 500 Unicode code point、控制/Bidi、敏感模式、私有路径和封闭变量；FastAPI 只返回固定错误，不回显原文。Alembic `20260720_0021` 替换原消息 check constraint，用同一合法占位符替换和剩余花括号拒绝保护直写 PostgreSQL 边界，并保留可回滚的旧约束。当前不渲染、不下发文案、不执行平台副作用；A7-06 再把精确动作/文案/数量与确认 revision 绑定。

A7-06 将 confirmation 从“数量事实”提升为版本化执行意图。`task-target-confirmation-intent.v1` 的 canonical 指纹绑定 Installation、Task、page/selection revision、动作、原始模板和有序 Target ID；迁移 `20260720_0022` 对 `0021` 既有事实从 typed definition 与目标集合确定性回填，再增加非空和封闭约束。仓储在确认、读取与重放时重算意图；A7-02 在授权前重算全部字段，D6-13 在业务 offer 入队/claim 时复验动作与模板。定义、目标、排除、revision、计数、版本或指纹任一变化都不能静默沿用旧确认。

A7-07 将 Local Executor SQLite 升级到 v5，并把不可重复的平台副作用拆成四个封闭状态：`prepared` 在任何平台点击前持久化精确效果 SHA-256；`begin_side_effect_dispatch()` 在同一 `BEGIN IMMEDIATE` 事务内把它原子推进为 `dispatched`，五路并发只有一个调用者得到 `replayed=false` 的执行许可；最终页面事实只能把 dispatched 一次性结算为带验证 SHA-256 的 `verified`，或不带伪证据的 `uncertain`。重启会列出 prepared/dispatched/uncertain 供恢复，但精确重放只返回 `replayed=true`，摘要、资源、状态或终态变化全部 fail closed，不会重新授权点击。

副作用表通过外键复用 A7-04 的完整 Action/Target/Attempt/Task/Installation/Executor 绑定，不复制云端业务事实。它只新增 effect/verification 两个 32 字节摘要、封闭状态、UTC 时间与 revision，不保存评论或私信正文、Token、Cookie、Profile、页面原文、账号或密钥。A7-07 没有 HTTP、OpenAPI、Tauri Command、React 或浏览器入口；A7-11/A7-12 必须以该公开 Python 账本 API 作为真实点击前后的原调用面。

A7-08 在 D6-02 页面版本模型内新增 `VIDEO_DETAIL`，只接受 HTTPS 官方 host、无 query/fragment 的 canonical `/video/<1..32 位非零开头数字 ID>`；搜索 Page Object 在该已知但不属于搜索的入口只返回熔断状态，不能误用评论 DOM。`comment_page.py` 独占评论 input、submit、final confirmation、登录与阻塞 selector 组，每组通过一个合并 locator 要求恰好一个可见元素，避免相同元素多属性误报，也拒绝两个真实控件造成的歧义。

评论 Page Object 本身不调用 fill/click/press，不接收正文、授权或账本，也不读取页面文本作为业务数据。它只返回封闭 `ready/confirmed/login_required/dialog_blocked/unknown` 观察和经再次观察后取得的 locator；最终确认优先于仍可见的输入区，陈旧确认会使页面不可继续动作。ready/final 等待共用有界总预算，路由变化、半套/重复锚点、登录、风控和驱动异常都停止等待。A7-11 必须在动作前拒绝陈旧 confirmed，再从 A7-07 获得唯一分发许可后使用这些 locator，并自行负责精确文案和最终证据绑定。

A7-09 在同一页面版本模型内新增 `USER_PROFILE`，只接受 HTTPS 官方 host、无 query/fragment 的 canonical `/user/<1..128 位字母、数字、下划线、点或连字符目标 ID>`；搜索和评论 Page Object 在该已知但不属于自身的入口只返回熔断状态，不会误用私信 DOM。`direct_message_page.py` 独占进入会话、message input/send、final confirmation、两类 permission notice、登录与阻塞 selector 组；每组通过一个合并 locator 要求恰好一个可见元素，两个权限事实同时出现或 profile/conversation 锚点混杂都视为页面冲突。

私信 Page Object 本身不调用 fill/click/press，不接收正文、授权或账本，也不读取会话或页面文本作为业务数据。它只返回封闭 `profile_ready/conversation_ready/confirmed/permission_denied/login_required/dialog_blocked/unknown` 观察和经再次观察后取得的 locator；登录、风控和权限拒绝优先于动作锚点，final confirmation 优先于仍可见的输入区。profile/conversation/final 等待共用有界总预算，路由变化、半套/重复锚点、权限变化、驱动异常或中途漂移都会立即停止。A7-12 必须在动作前拒绝陈旧 confirmed/permission denied，再从 A7-07 获得唯一分发许可后使用这些 locator，并自行负责精确文案和最终证据绑定。

A7-10 将“只浏览”收敛为 Executor 内部 `douyin.browse-execution.v1` 单次状态机，而不是新的 Web/App 接口。输入必须是 D6-10 链生成的完整最小 `DouyinCandidate`；共享页面版本模块用同一平台目标 ID 规则构造且复验 canonical 官方 `/user/<id>`，执行层没有官方 URL 或 selector 字面量，也不接受调用方任意 URL。`profile_page.py` 只拥有通用主页、登录、风控 selector，刻意不包含评论输入、私信入口或发送按钮。

浏览执行固定一次 30 秒 `domcontentloaded` 导航、一次最多 10 秒的主页 ready 等待，并在最终成功前重新观察页面且二次取得唯一主页根节点。取消在导航前、导航后和成功前检查；明确取消、探针异常/非 bool、登录、风控、导航/主页超时、未知版本、重复锚点、驱动失败和中途漂移都返回封闭脱敏状态且不重试导航。完成只证明目标主页在当前窗口可见，不写 A7-07 副作用账本、不签发结果 receipt、不更新服务端 Task；A7-11/A7-12 才分别组合授权、账本和发送动作，A7-15 再建立目标级结果 UI。

A7-11 将评论执行收敛在 Executor 内部 `douyin.comment-action-execution.v1`。输入只接受完整 `ActionAuthorizationExpectation`、A7-05 `ActionMessageTemplate` 和 D6-06 最小目标摘要；模板只展开唯一目标显示名变量，展开结果再次经过 500 字符、安全文本和零剩余变量校验。效果摘要用域隔离 canonical JSON 绑定完整授权 scope、最终文案和模板版本，SQLite 与 receipt 都不保存正文。执行顺序固定为 ActionAuthorization/本机硬限制准入 → prepared 摘要 → Page Object ready → 填写 → 原子 dispatch 许可 → 单次提交 → 最终锚点二次复验 → verified 摘要，执行层不拥有 selector、官方 URL、Cookie/storage 或任意 HTTP 面。

结构化 receipt 只允许 `not_dispatched/verified/outcome_uncertain` 和阶段化固定 evidence，并绑定 Action/Target、账本状态/revision 与重放位。许可前的登录、风控、陈旧确认、页面版本/锚点漂移、填写或 dispatch 许可失败不点击且保持 prepared；许可后的 Playwright 超时/异常、登录或风控变化、最终确认缺失/漂移、时钟或结算失败都不能回到失败重试，而是持久化 uncertain 或至少保留 dispatched 事实。prepare/dispatch 竞争返回的 dispatched/uncertain/verified 重放立即投影既有事实且不访问 DOM；只有 prepared 重放仍可重新观察页面并竞争尚未授出的唯一许可。该模块当前没有 Executor wire、Control Plane API、Tauri Command 或 React 入口，A7-15 才消费目标级结果，A7-16 才能以真实平台最终状态完成评论验收。

A7-12 将主动私信执行收敛在 Executor 内部 `douyin.direct-message-action-execution.v1`，复用与评论动作相同的授权、模板再校验、域隔离 effect/verification 摘要和 receipt 不变量。顺序固定为准入 → prepared → 已打开会话恢复或一次入口点击 → conversation 复验 → 填写 → send locator 复验 → 原子 dispatch 许可 → 单次发送 → final 二次复验 → verified；进入会话不等于副作用许可，入口失败仍可从 prepared 恢复。

“暂时无法私信”与“关注后才能私信”不会合并成通用失败：发送前分别保持 prepared evidence，发送后分别结算 uncertain evidence。登录、风控、未知版本、锚点冲突、入口/填写失败和 dispatch 许可失败均在发送前停止；获得发送许可后任何点击、权限变化、确认缺失、页面/时钟/账本异常都不能自动重发。既有 dispatched/uncertain/verified 在页面访问前返回，只有 prepared 可恢复会话并再次竞争尚未授出的许可。当前模块仍是 Local Executor 原调用面，不新增 HTTP/Tauri/React 入口；A7-13 统一处理结果查询，A7-15 投影目标级 UI，A7-17 才完成真实平台私信验收。

A7-13 将点击/发送后进程退出留下的 `dispatched` 崩溃窗口收敛为 Executor 内部 `douyin.side-effect-recovery.v1`。恢复输入只有强类型 Action ID；先从 App 私有 SQLite 读取完整绑定的 `LocalSideEffect`，prepared 不得伪造外部结果，verified/uncertain 作为既有终态直接返回且不访问页面。只有 dispatched 按账本 action 选择评论或私信 Page Object，在调用方已恢复的动作页面只执行有界 `wait_for_final()` 和最终 locator 二次取得，不导航、不填写、不点击，也不读取正文、Cookie/storage 或页面 body。

最终证据充分时，恢复层使用 A7-11/A7-12 与即时执行共用的 action-specific effect→selector-version→final-evidence 验证摘要结算 verified；登录、风控、两类私信权限、等待超时、未知路由、锚点冲突、驱动或最终复验失败结算 uncertain。若 SQLite 结算不可用则保留 revision 2 dispatched，不能假装 uncertain 已落盘；并发同向重放投影首次 verified/uncertain，opposite terminal 竞争只接受 `BEGIN IMMEDIATE` 的账本赢家。A7-07 的 uncertain 终态没有被 A7-13 放宽，后续人工核对必须走明确用户操作；H8-05 才负责启动时枚举 unresolved 并把正确页面上下文交给本能力。

A7-14 把“动作执行结果”接到服务端风险 scope，而不是在 Executor 再造一套阈值。`step.completed/step.failed` 仍由认证 Executor WebSocket 进入 `TaskEventConvergenceService`；PostgreSQL 仓储先按与授权相同的顺序锁 Installation，再锁 Task/Attempt/Action，在一个事务中更新动作终态、写不可变 `action_risk_results`、更新 `(Installation, douyin, action)` 的 `action_failure_circuits`，最后追加 Task event 和权威快照。复合外键从 result 到 ActionAuthorization、从 circuit 到最后/打开 result 固定同一 Installation/平台/动作，避免只靠应用查询条件维持审计归属。

未打开 circuit 时，成功把连续失败清零，失败递增；达到当前 ActionAuthorization 中持久化的阈值快照时，首次触发者把 Task/Attempt 转为 `awaiting_human`，事件投影为既有 `task.awaiting_human`。授权仓储在返回精确 Action ID 重放后检查 circuit，确保既有授权事实仍可幂等读取而任何新 Action ID 都被 `consecutive_failure_circuit` 拒绝。circuit 打开后，跨 Task 晚到成功只记录审计而不能自动恢复；恢复只复用已 ACK 的正式 `task.resumed`，且必须是打开该 circuit 的 Task、时间单调、事务完整。服务端因此是计数与接管总指挥，本机 A7-04 硬下限和紧停仍独立生效，任何一层都不能放宽另一层。

A7-15 不新增第二条结果总线。共享 `action-result-evidence.v1` 只给既有 Executor Task event 增加封闭、无正文的 evidence/version；`executor/rpa/douyin/action_result.py` 将 A7-11/A7-12 即时 receipt 与 A7-13 恢复 receipt 映射为 `step.completed`、`step.failed` 或 `task.outcome_uncertain`。应用层在数据库事务前验证事件类型与 success/failed/uncertain 证据集合，仓储把 evidence 与 Action 终态一起持久化；迁移 `20260721_0024` 回填历史终态并以 PostgreSQL check constraint 阻止跨 outcome、空终态或非封闭证据。旧 Executor 没有 evidence 时仍只按已存在的终态使用明确 generic fallback，不能猜测平台页面证据。

目标结果读取遵循 `api/task_target_results.py → application/task_target_results.py → infrastructure/database/task_target_result_repository.py` 单向分层。唯一 `GET /api/v1/tasks/{task_id}/target-results` 受 `app.control-plane` Session 和 Installation scope 保护，仓储只连接 Task 的 current Attempt 授权/Action，并按 Target ordinal 合并用户排除、候选 disposition 与 Action 状态，返回 pending/running/succeeded/skipped/failed/outcome_uncertain 和固定 evidence；无 Task、跨 Installation、非法 ID、损坏持久事实或数据库故障分别收敛为不可见/校验失败/脱敏不可用。响应只含公开目标摘要、状态、证据、可选 Action ID 与 UTC 时间，不含文案、页面内容、Cookie、Profile、URL、路径、SQLite 内容或策略内部字段。

桌面端沿正式 `TypeScript source → 固定 Tauri Command → Rust ControlPlaneClient → HTTPS` 路径读取该投影，React 继续复用 T3-18 `TaskRunDetails`，不创建 Web 产品或第二个任务详情页。Task SSE 前进、控制成功和用户重试只触发权威查询失效；UI 不从事件文本或本地状态推断结果。T3-18 隐藏验收以唯一 `visible=false` App、真实 Uvicorn/Alembic/PostgreSQL 和 App Session 从原页面请求并展示四类终态，同时保留既有控制验收；测试配置、FakeExecutor 和准备数据不进入正式构建。

B5-10 的 `DouyinQrLoginFlow` 只组合生产 `BrowserRuntime` 与 B5-09 detector：每个 flow 通过 `open_window()` 拥有一个专用 headed Page，`begin()` 固定打开官方 `/user/self`，`recheck()` 无入参并只重新读取页面。初始登录页或证据不足时最多等待 10 秒的共享健康/二维码就绪事实；二维码选择器只使用真实页面可访问语义 `aria-label="二维码"`（兼容等价 `alt`），不读取二维码地址或内容。明确二维码失效、手机端待确认和健康分别投影到封闭状态；过期与确认同时可见、页面异常或未知结构 fail closed，冲突不会被自动刷新掩盖。

B5-11 将同一 flow 契约升级到 `douyin.qr-login.v2`：B5-09 的 `risk` 页面证据在工作流层只能成为 `handoff_required/risk_challenge`，覆盖验证码、滑块和风控使用的 ByteDance 验证中心外层 iframe，不读取跨源挑战内部内容。生产模块没有自动点击、填写、拖拽、OCR、验证码识别或绕过路径；挑战窗口保持可见，用户处理后只能通过无参数 `recheck()` 重新读取页面，且仅真实 `healthy` 关闭熔断。当前仍是 Local Executor 内部页面能力，不新增 Control Plane、Tauri Command 或 React 状态；已有 `handoff.requested` 任务事件供后续真实 RPA runner 使用，但本任务没有 Task/Attempt 上下文，不伪造事件。B5-13 才从 App 平台页触发，且不得复制 Python 选择器。

B5-13/B5-14 的本机命令复用 E4-06 的每次启动 256-bit 本机会话，但使用独立 `automation-tool.local-executor-command.v1` / `result.v1` HMAC 域和固定 `atlcp1` envelope；command ID、动作类型、生产内部解析的浏览器/Profile 路径和验收专用 headless 位全部签名。Python 对登录只接受 `douyin.login.open|recheck` exact object；注销另用 exact path-free `douyin.logout.complete`，拒绝可执行文件、Profile 和 headless 字段，结果绑定 `douyin.session-control.v1/logged_out`。Rust 只接受匹配 command ID、平台、flow 版本和封闭状态的 exact result。同一个 CLI 同时运行 stdin worker 与 WebSocket runtime，二者通过有界本机队列连接；命令认证失败、超时、畸形结果、App 退出或 Manager stop 都关闭 stdin 并终止完整 Executor/浏览器进程树。正式构建始终 headed，只有 `control-plane-e2e` 隐藏验收在 Rust 内硬编码 headless。

### 8.3 页面定位

定位策略按优先级：

1. 稳定语义角色、label 和可访问属性；
2. 稳定业务文案与明确父级范围；
3. 版本化 DOM locator；
4. 受控视觉/OCR 兜底；
5. 人工接管。

选择器集中在平台页面对象中，记录 `platform_page_version`。页面变化时安全失败，不用模糊坐标继续点击。

MVP 不使用 AI 页面理解、stealth 或验证码识别。

## 9. 产品账号、安装实例与设备认证

P9 本地 MVP 暂不要求产品账号，只使用已实现的 Installation 服务认证；任何客户 Demo 必须先完成 U9 账号体系，并让业务访问同时受产品账号与账号所属 Installation 约束。

### 9.1 实体

- `user_id`：U9 新增的产品账号稳定 ID；
- `account_session`：登录后可刷新、可全量吊销的短期产品访问能力；
- `installation_id`：一次 App 安装的稳定 ID；
- `device_public_key`：本机生成密钥对的公钥；
- `device_credential`：后端签发、可撤销；
- `device_session`：短期访问能力；
- `executor_id`：一次 Executor 安装/版本实例；
- `connection_id`：一次在线连接。

稳定业务资源 ID 使用规范小写 UUIDv4。Python 领域层分别使用 `InstallationId`、`ExecutorId`、`TaskId`、`ExecutionAttemptId`、`ActionId` 和 `ArtifactId` 不可变值对象；即使底层 UUID 相同，不同资源类型也不相等、不可互相解析。外部输入拒绝 nil、非 v4、大小写/空白/URN/无连字符等非规范形式，错误不得回显原始值。一次在线连接使用独立短生命周期 `ExecutorConnectionId`；协议 message/correlation ID 也沿用同一规范并保持用途隔离。

私钥和长期设备凭据留在 Tauri `app_data_dir` 下由 Rust 管理的 App 私有文件，不调用系统钥匙串，也不进入 React、Tauri Command 或 Python 普通配置。

### 9.2 受控注册与账号归属

I2 已实现限时、限环境、限用途的 bootstrap 设备注册，用于 P9 本地验收、隔离测试和受控迁移。bootstrap 只允许注册，不允许创建或执行任务；它不再作为客户 Demo 的分发、配对或逐设备审批机制。

领域层的 `DemoBootstrapGrant` 固定唯一 purpose `installation.register`，绑定一个小写规范 Demo 环境 slug，采用 `[not_before, expires_at)` 半开时窗且硬上限 7 天。调用必须使用强类型 purpose/环境；原始字符串即使内容相同也不能越过能力边界，跨环境、未生效和到期均返回固定拒绝原因且不回显输入。这个对象只表达既有 I2 受控注册 claims 的授权语义，不存 token、不读取环境变量，也不替代 U9 产品账号、账号 Session 或账号设备归属。

I2-05 将这些 claims 封装为验证专用的 `atb1.<payload>.<signature>`：payload 必须是 exact-field canonical JSON，签名算法固定 Ed25519，Control Plane 只从部署配置读取 32 字节验证公钥和精确 Demo 环境，不持有离线签发私钥。未知字段、重复 JSON key、非 canonical base64url、错误版本/用途/时间类型、超长、篡改和错误 signer 统一拒绝；服务端只保存 token 的 SHA-256 指纹用于 challenge 绑定，不保存原 token。

注册固定两步：`issueInstallationRegistrationChallenge` 验证 bootstrap 后产生 32 字节 CSPRNG nonce，并返回最长 5 分钟、且不晚于 bootstrap 到期的 opaque canonical signing payload；`completeInstallationRegistration` 再次验证同一 bootstrap，按 challenge ID `SELECT ... FOR UPDATE`，常量时间核对环境、bootstrap 指纹和 payload 摘要，再用 challenge 绑定的设备公钥验证 Ed25519 签名。同一事务创建 Installation 并标记 challenge 已消费，因此进程重启、串行或并发重放都只能成功一次。到期采用半开边界，错误设备、另一份有效 bootstrap、篡改 payload、未知 challenge 和跨环境都不消费 challenge。

I2-06 把初始凭据签发并入同一个注册事务：凭据使用 `atdc1.<credential-id>.<256-bit-secret>` opaque 格式，明文只在注册或轮换成功响应中出现一次；PostgreSQL 只保存秘密的 SHA-256 摘要，不保存明文凭据、设备私钥或额外服务端签名私钥。凭据版本从 1 开始，唯一 scope 固定为 `device.session.exchange`，数据库保存 `active`、`rotated`、`revoked` 历史，并通过部分唯一索引保证每个 Installation 同时最多一个 active 版本。

设备可使用当前 bearer 调用 `rotateDeviceCredential` 或 `revokeDeviceCredential`。仓储先按公开 credential ID 定位，再按固定顺序锁 Installation 和凭据，用常量时间比较摘要并确认 Installation/凭据仍 active；轮换在单事务中把旧版本标记为 rotated、关联新版本并插入下一正数版本，吊销则将当前版本标记 revoked。两个并发轮换只有一个能成功，旧版本、错误秘密、未知凭据、重复吊销和已吊销 Installation 对外共享固定 401，不能据此枚举凭据状态。scope 只授权 I2-07 的短期 Session 交换，不直接授权任务或业务 API；凭据轮换/吊销属于 bearer 自身生命周期，而非额外业务 scope。

I2-07 实现唯一换票端点 `exchangeDeviceSession`。客户端以当前 `atdc1` bearer 换取 `atds1.<session-id>.<256-bit-secret>`；票据固定 5 分钟，`not_before` 向前回退 30 秒容纳客户端落后时钟，过期仍采用严格半开边界且没有到期后宽限。每张票必须精确选择 `app.control-plane` 或 `executor.connect`，不能组合或使用通配符。PostgreSQL 仅保存 Session 秘密的 SHA-256 摘要，并以复合外键绑定 Installation、父凭据 ID 和父凭据版本；认证固定按 Installation、父凭据、Session 顺序锁定，三者任一失效即统一拒绝。父凭据轮换/吊销会在同一事务写入相关 Session 的 `revoked_at`，Installation 撤销也会在每次认证时即时生效。服务端不为 Session 增加签名私钥。

I2-09 已建立正式桌面消费链路。生产 React 只通过窄 `TauriControlPlaneTransport` 发起 Health 检查；Rust 以固定 origin 和封闭 operation allowlist 调用 Health、注册、凭据生命周期和 Session 端点，不接受 WebView 下发任意 URL、路径、Header 或 bearer。设备私钥从 App 私有目录按需加载并只在 Rust 内签名，注册/轮换返回的长期凭据直接原子写回 Rust 凭据仓，Session 只保存在及时清零的原生缓冲。请求具有关联 ID、超时、响应大小、内容类型、禁止缓存和严格 DTO 校验；所有底层失败收敛为不泄密固定错误。

生产同路径验收由隐藏窗口的真实测试版 Tauri App 发起，连接真实 FastAPI 与隔离 PostgreSQL，依次完成 Health、Installation 注册、`app.control-plane` Session、凭据轮换、`executor.connect` Session 和吊销。验收同时核对设备公钥与 App 私有身份一致、challenge 已消费、v1/v2 凭据为 rotated/revoked、两张 Session 均随父凭据失效，以及长期凭据文件已删除；测试结束精确回收本地服务、容器、卷和隔离 App 数据。

I2-14 增加服务器运维侧 Installation 吊销入口和统一 App 业务访问守卫。`automation-tool-revoke-installation` 只能在具备服务器数据库配置的运维环境运行，以 Installation ID + expected revision 做 fail-closed CAS；同一 PostgreSQL 事务把 Installation 置为 revoked/revision+1、吊销 active 长期凭据并吊销全部未失效 Session。未知、重复、stale revision 和并发失败使用固定文案，不输出目标或底层异常。它不是匿名 HTTP 管理接口，也不向桌面 App 暴露运维能力。

`require_current_installation_access` 是后续 App 业务路由的统一 FastAPI 依赖：只认证 `app.control-plane` Session 并返回强类型 `InstallationId` scope。`GET /api/v1/installations/current` 是正式启动探针，也是该守卫的首个消费者；缺少、畸形、过期或已吊销认证共享固定 401，依赖不可用使用固定可重试 503，所有响应禁止缓存。未来创建、读取和控制任务不得复制认证逻辑或只相信请求体中的 Installation ID；T3-06 在路线图上强制依赖 I2-14。

隐藏 Tauri 生产同路径验收会先由 App 正式 Rust 桥注册并访问探针，再由服务器运维 CLI 吊销，最后让同一 App 重新走启动入口并得到独立吊销诊断；同时核对 Installation、长期凭据和全部 App Session 的原子最终状态。服务端吊销后本地凭据仍保留在 Rust 管理的 App 私有目录以稳定识别该安装状态，但已无法换取任何 Session；重新授权与本地凭据替换必须由后续显式流程完成。

上述 I2 方案是当前 P9 本地 MVP 的设备安全基线，不是客户账号体系。U9-02/U9-03 将新增产品账号、登录凭据、账号状态和可刷新/可全量吊销的账号 Session；账号停用必须立即使其全部产品 Session 和业务访问失效。

U9-05 在有效账号 Session 下复用现有两步设备密钥证明：Control Plane 把新 Installation 与当前 `user_id` 原子绑定后签发既有设备凭据，设备公钥证明与账号授权缺一不可。已绑定设备重启时同时复验账号状态、账号 Session、Installation 归属和设备状态；跨账号绑定、重复公钥、并发绑定、账号停用与设备吊销全部 fail closed。

客户 Demo 不创建匿名设备申请端点，不生成配对码或 poll secret，也不提供后台逐设备审批队列。Demo 账号由认证运维入口创建/恢复，用户登录后客户端自动完成设备绑定；既有 bootstrap 仅保留在受控测试和明确迁移边界，不能用于客户业务 API。

### 9.3 请求授权

- P9 本地 App 业务请求继续作用域固定到 installation；
- U9 客户 Demo 业务请求必须同时绑定有效产品账号和该账号所属 installation；
- Executor 连接同时绑定 installation、executor、版本和平台；
- 服务端每次创建/读取/控制任务都校验账号状态、Session、installation 归属与设备状态；
- Executor 只能领取目标 installation 的任务；
- 账号停用、全 Session 注销或设备吊销后禁止新任务并要求 Executor 断开；
- 前端隐藏按钮不能代替服务端校验。

## 10. 执行协议

### 10.1 Envelope

所有 Control Plane 与 Executor 消息包含下列公共字段：

```text
protocol_version
message_id
message_type
sent_at
deadline_at
installation_id
executor_id
correlation_id
idempotency_key
sequence
payload
```

只有 `task.*`、`step.*`、`session.login_required` 和 `handoff.requested` 等任务作用域消息额外强制包含 `task_id` 与 `execution_attempt_id`。`executor.hello`/`executor.heartbeat` 是安装实例与 Executor 作用域，不允许为了凑字段伪造 task/attempt ID。

规则：

- 精确匹配支持的协议版本；
- 未知字段拒绝；
- `deadline_at` 严格晚于 `sent_at`；
- 同一任务事件序号单调；
- message ID 和幂等键有唯一约束；
- payload 不允许平台 Cookie、验证码、私有路径和内联截图；
- 大文件通过受控 Artifact 引用，不通过 WebSocket 内联。

I2-10 的正式 Pydantic 入口是 `parse_executor_message`。Envelope 以 `message_type` 做判别联合，当前把 28 种已声明 v1 类型精确分到 Executor 生命周期、平台健康、任务命令、任务回执、任务事件与发现批次/完成六类；公共字段拒绝未知项且模型冻结。`protocol_version` 必须显式为 `1.0`；message/correlation/installation/executor/task/attempt ID 都是用途隔离的 canonical 小写 RFC 4122 UUIDv4 字符串；时间必须为带时区 RFC3339 且精确 UTC，deadline 严格晚于 sent；幂等键为 1～128 字符受限字符集。I2-12 的跨语言 RED 证明 `2^63-1` 不能被 TypeScript `number` 精确表达，因此 sequence 已统一收紧为 `1..2^53-1` 的 strict safe integer。

正式解析只接受最大 32 KiB 的 UTF-8 JSON object，并拒绝重复 key。Payload 当前是后续任务消息 Schema 的受限容器：最大 16 KiB、深度 8、每层集合最多 64 项、字符串最多 4096 字符；递归拒绝 Cookie/Token/密码/私钥/凭据字段、凭据赋值文本、Bearer、私有绝对路径、`file://`、inline data URI、控制/双向字符和 NaN/Infinity。任何结构、语义、编码或资源限制失败都收敛成固定 `ExecutorProtocolError`，且异常对象不保留原始 cause/context；调用方不得直接把 Pydantic `ValidationError` 暴露到日志或远端。具体 payload 业务字段仍须随对应消息任务收紧。

I2-11 已把 Pydantic 判别联合确定性导出为 `contracts/protocol/executor-v1.schema.json`，dialect 固定 Draft 2020-12，并内嵌 `$id`、wire/payload 资源上限和 `x-semantic-validation-required`。Schema 尽可能结构化表达当前 28 种 message type、required/unknown field、用途 ID pattern、幂等键、序号、任务作用域、payload 顶层项数和 UTC RFC3339 pattern；无法由标准 JSON Schema表达的 deadline 先后、重复 key、递归 payload 限制和隐私文本由显式语义扩展声明，不能静默省略。

公共 `contracts/fixtures/executor-v1` 当前固定 10 个 valid 与 27 个 invalid wire 文件：17 个结构层无效样例必须由标准 Schema 和正式解析器同时拒绝，另 10 个语义层无效样例允许标准 Schema 接受但必须由正式解析器拒绝。Schema 生成器提供 write/check 两种模式，缺失或逐字漂移都用固定错误失败；Backend CI 在测试前执行 `--check`。Python、Rust、TypeScript 只能回放这套公共 fixtures 并实现相同语义扩展，不得各自另造“等价”样例。

I2-12 已实现三端一致性：TypeScript 以严格 Zod 判别联合校验完整 envelope，并在普通 `JSON.parse` 前扫描所有对象的重复 key；Rust 以 `serde(deny_unknown_fields)` DTO、递归唯一 key visitor 和同一资源/隐私策略完成正式解析。两端都只暴露固定 `ExecutorProtocolError`，不保留或反射被拒绝的 wire。时间比较精确到 RFC3339 允许的 6 位小数，`-00:00` 不作为 canonical UTC 接受；sequence 上限固定为三端都能无损表示的 `2^53-1`。

I2-13 已把 Python 正式解析入口接入 `WS /api/v1/executors/connect`。握手必须只提供 `automation-tool.executor.v1` 子协议和单个 Bearer `executor.connect` Session；第一帧限时为 `executor.hello`，绑定已认证 Installation、声明的 Executor、协议/Executor 版本、平台、架构、Hello sequence 和新生成的 `ExecutorConnectionId`。T3-09 之后，连接期间只接受同一身份的 heartbeat 或任务命令回执；heartbeat 更新 Registry 单调水位，回执进入持久 Outbox 核对。服务周期重新验证数据库中的 Session、父凭据与 Installation；任一失效立即固定 4401 断开，旧 Session 不能重新升级。Uvicorn 固定 `websockets-sansio` 并在传输层执行 32 KiB 上限；拒绝握手和关闭只返回固定状态/原因，原始 bearer、wire 和底层异常不进入公开响应或日志。

T3-08 在认证入口之后增加单进程 `ExecutorConnectionRegistry`。Registry 以 Installation 为唯一 live key，因此同一安装实例即使声明不同 ExecutorId 也只能有一个 current 连接；注册新 Hello 时先原子替换投影，再用固定 4409 关闭旧 socket。在线投影只包含强类型连接/Installation/Executor ID、运行时元数据、服务端连接/最后心跳时间和严格递增 sequence，不含 WebSocket、Session、凭据或客户端自报时间。旧连接的迟到 heartbeat、发送和 finally 清理均以 Connection ID 校验，不能覆盖或删除新连接；重复/倒序 heartbeat 固定按协议错误关闭。

T3-16 增加 Installation-scoped `GET /api/v1/workbench/status`。路由强制复用 `require_current_installation_access`，从 Registry 只投影 Control Plane `ready`、Executor `online/offline` 和可空服务端最后心跳时间，固定 `no-store`；不返回 Installation、Executor、Connection ID、channel、Session 或底层错误。该端点只描述当前进程的在线事实，Task 状态与指标仍从 PostgreSQL 权威快照/事件计算；MVP/Demo 多副本前必须先建设跨实例 Executor 路由，不能把单进程 Registry 冒充集群在线状态。

Registry 为 T3-09 提供 `send_current(installation_id, connection_id, wire)`：只接受 1..32 KiB UTF-8 文本，写入前后都确认目标仍是 current；传输失败与写入期间替换分别返回不泄密的 unavailable/stale 结果，调用者不能把 socket write 当成 Executor ACK。应用 lifespan 用 1012 清空所有连接。Registry 是单实例瞬时路由，不持久化认证、任务、命令或事件；PostgreSQL 仍是认证和业务事实源。

T3-09 在每次连接重认证之后调用持久投递服务。仓储先把 deadline 已到的 pending/in-flight/delivered 批量置为 expired，再用 PostgreSQL `FOR UPDATE SKIP LOCKED` 按 deadline/创建顺序抢占 pending、lease 已过期的 in-flight，或 ACK 超时的 delivered；抢占时 revision 与 delivery attempts 同步递增并持有不越 deadline 的短 lease。同一连接内按有界批次发送，失败或 stale current 只释放成带延迟的 pending；写入成功清 lease 并记 delivered，不能改 Task/Attempt 状态。

新 Executor Hello 的第一轮 dispatch 使用连接时刻作为恢复水位，立即重投此前 delivered 命令；本轮刚写入的 delivered 不会被同一批次再次抢占。Control Plane 崩溃留下的 in-flight 不靠内存恢复，而在持久 lease 到期后重新可抢占。message ID、correlation、sequence、idempotency key 和 deadline 在重投时保持不变，只有 sent time 与当前 Executor ID 来自本次发送，因此 Executor 必须以 message/idempotency 做本机去重，不能假设网络 exactly-once。

任务回执必须通过正式 parser，并与当前连接身份、Outbox 的 Installation、Task、Attempt、correlation 和 sequence 全部一致。`task.accept` payload 精确为 `{"accepted":true}`，`task.reject` 为 `{"accepted":false}`，`task.control_ack` 为 `{"acknowledged":true}`；offer 只能收 accept/reject，控制命令只能收 control_ack。首个合法响应 ID 与服务端收到时间成为事实；同命令同结论的后续重复回执直接返回已有终态，不覆盖首个响应；错配、未投递先确认、迟到和跨命令响应 ID 冲突 fail closed。ACK 只结束 Command，Task/Attempt/Action 的业务状态必须等 T3-11 事件收敛。

T3-10 增加独立 `FakeExecutorEngine` 与 `FakeExecutorClient`。引擎只导入共享 protocol，正式解析每条 command，并核对 Installation/Executor、deadline、Attempt 内 command sequence 及 task/attempt 绑定；message ID 与 idempotency key 任一重放都返回首次生成的完全相同 envelope，不再次递增事件序号，意图变化则拒绝。成功、部分成功、失败、登录、接管、结果不确定、拒绝和 hold 场景覆盖全部当前任务事件；hold 只允许合法 pause/resume/cancel/emergency-stop，生成阶段失败会同时回滚状态与事件水位。

Fake 客户端只接受无 userinfo/query/fragment 的固定 Executor `ws`/`wss` 路径、受限 Session 和有界命令数，使用共享的唯一子协议、32 KiB 限制和正式 Hello/结果/事件 envelope。T3-20 增加有界 `run_reconnecting`：同一 Engine/Session 跨连接保留幂等投影，按稳定 Command message ID 统计唯一命令，重投完整返回首次批次但不重复占用处理名额；非法预算/延迟、二进制或非 Command 帧、连接失败和预算耗尽统一 fail closed 且不泄密。核心不读取文件、不启动进程、不操作浏览器/桌面、不访问 Control Plane 数据库；内存状态只是测试场景投影，不能成为生产任务事实。T3-10 的真实 Uvicorn 验收选择不产生事件的 reject 场景验证当时的 Outbox/ACK 全链路；T3-11 已由生产 WebSocket 完成事件持久闭环，T3-20 再以真实停服/同库重启证明 pending Command 和已有事件跨进程恢复。

T3-11 将 `TaskEventEnvelope` 纳入 bound WebSocket 消息，但不混入 heartbeat sequence 或 Command ACK 逻辑。事件应用服务只接受当前 14 种类型：非 step payload 必须为空；step 只允许可选 canonical `action_id`，progress 另要求 `0..100` strict integer。Action 无显式 ID 时只记录 Attempt-scoped 事件，不通过 ordinal、最近一条或“唯一活动动作”猜测归属。事件 deadline 已到、客户端时间晚于服务端、身份冒充和非法 payload 都在持久化前拒绝。

PostgreSQL 仓储按 Installation + Task 锁行，要求事件 sequence 恰好等于 `last_event_sequence + 1`。`source_message_id`、`source_idempotency_key` 任一命中且 32 字节稳定意图指纹一致时幂等返回当前快照；key 冲突、同 sequence 不同事实、缺口和非精确迟到均拒绝，不缓存乱序事件。合法事件在同一事务内插入 `task_events`，Task 每条事件都以 revision/watermark CAS 前进；Attempt/Action 只在明确状态变化时各自 CAS 增 revision，并使用服务端接收时间写 started/finished。任一 scope、状态、时间、唯一约束或写入失败使整笔事务回滚。

WebSocket 对收敛拒绝使用固定 4406，对数据库不可用/内部失败使用固定 1011，日志不包含 wire、payload 或底层异常文本。`20260718_0011` 对旧事件回填受限幂等键和 32 字节指纹后再设非空/唯一约束，可完整降级而不删除事件事实。真实验收由 FakeExecutor 成功场景经 Session、Outbox 和 Uvicorn 发送五条事件，数据库最终形成连续 sequence/revision 与一致 Task/Attempt 终态。

T3-12 的 `TaskEventStreamService` 只解析规范 Task ID、标准十进制 `Last-Event-ID` 和有界 batch，并核对仓储返回的 Task、连续 sequence 与 watermark；未知、非法和跨 Installation Task 保持不可区分。`SqlAlchemyTaskEventStreamRepository` 用单条 outer-join 查询从一个 PostgreSQL MVCC statement snapshot 同时得到当前 Task status/watermark 与其后最多 100 条事件，所以收敛事务提交前，事件行和 Task 投影都不会进入 SSE；它不订阅进程内队列，也不把轮询结果缓存成第二事实源。

`GET /api/v1/tasks/{task_id}/events` 复用精确 `app.control-plane` Session。每帧 `id` 就是持久 sequence，`event` 是封闭事件类型，`data` 只包含公开 Task/Attempt/Action ID、版本、类型、revision/status、结构化 `progressPercent`、UTC 时间与安全消息；来源 message/idempotency/fingerprint 永不出站。迁移 `20260718_0012` 只增加受事件类型和 `0..100` 约束的 nullable `progress_percent` 明确列，不保存 Executor 任意 payload。

SSE 空闲时发送不改变水位的 comment keepalive；追平终态 watermark 后关闭。非终态连接最多 55 秒主动轮换，使 Rust App 重新换取短期 Session 并携带相同 Last-Event-ID 续接；响应开始后数据库异常只安全断流，不能再伪造 JSON 503 或事件。响应固定 `no-store, no-transform`、禁代理缓冲和有界公开帧。真实验收由唯一 `visible=false` Tauri/WKWebView 通过正式 Rust 客户端先读取 1、2 并断线，再用新 App Session 从 2 续拉 3、4、5 到终态；FakeExecutor 同时走正式 Executor Session/WebSocket。

T3-13 公开 `POST /api/v1/tasks/{task_id}/pause` 与 `/resume`。两者只接受空 JSON、必填幂等键和认证得到的 Installation scope；仓储锁定 active Installation、Task/current Attempt，在 running/running 或 paused/paused 精确组合下按 Attempt 现有最大 command sequence 原子分配下一值并写 pending Outbox。请求事务不更新 Task、Attempt、Action 或事件；首次返回 202，同键同意图重放返回 200，改意图、状态冲突、跨 scope、序号耗尽和数据库冲突均 fail closed。

控制状态只由确认后的事件推进。Outbox delivered 和 `task.control_ack` 仍只改变 Command；`task.paused`/`task.resumed` 收敛时必须锁定该 Attempt 最新 pause/resume 命令，并同时核对目标类型、acknowledged、`task.control_ack` response、correlation 和确认时间。没有 ACK、旧命令、错 correlation 或 ACK 晚于事件接收时间都拒绝整笔事件事务，因此公开 API 不会把“请求已受理”伪装成“已经暂停”。隐藏 Tauri App、真实 Rust/Uvicorn/PostgreSQL 和 HOLD FakeExecutor 已完成 offer→pause→resume 整链，最终 Task/Attempt 恢复 running。

H8-01 把同一语义延伸到正式 Local Executor。控制命令不能创建本机 Attempt；pause 只接受 running checkpoint，resume 只接受 paused checkpoint，并先把 command/ACK 写入既有 SQLite v5 表。最新 pause 一经 `receive_command()` 持久化，`begin_side_effect_dispatch()` 会在同一个 `BEGIN IMMEDIATE` 锁边界拒绝后续 prepared→dispatched；此前已经 dispatched 的平台动作仍保留真实事实，既不撤销也不伪报 paused。只有该 Attempt 不再有 dispatched 时，`pending_task_controls()` 才返回 pause；`complete_task_control()` 在单事务复验 ACK、身份、correlation、command/event sequence、checkpoint revision/state，并一起提交 checkpoint 状态和 `task.paused` Outbox。resume 用同一路径原子恢复 running，之后才能重新 dispatch。运行循环在 Outbox 恢复后和每次空闲轮询推进待生效控制，因此命令无需重发；并发重放只返回既有事件。

该设计没有复制 Control Plane 状态机，也没有新增 SQLite schema：PostgreSQL 仍是 App 可见 Task/Attempt 的权威事实，本机 checkpoint 只决定 Executor 是否已经到达可安全上报的边界。H8-01 原调用方验收由唯一 `visible=false` Tauri App 发起正式 pause/resume；准备阶段的 HOLD FakeExecutor 只建立服务端 running 事实，随后正式 `python -m automation_tool.executor` 经认证 WebSocket 消费控制。真实 SQLite 中预置一条 dispatched 与一条 prepared，验收证明 ACK 时服务端仍 running、新 dispatch 被拒绝、前一动作验证后 runtime 自动上报 paused，最后 App 恢复到服务端/本机均 running。

T3-14 在同一 API/Outbox 边界增加 `POST /api/v1/tasks/{task_id}/cancel` 与 `/emergency-stop`。首次请求锁定 active Installation、Task/current Attempt，在领域状态机允许取消且 Attempt 尚未终止时，原子写入 pending Command，并把 Task/Attempt 各以 revision CAS 前进一次到 `CANCELLING`；不写伪造的取消终态，也不占用 Executor 持有的事件 sequence。相同 scope/key/意图重放返回原 Command 且不重复增 revision；改意图、再次终止、终态、错 scope、时间回退和不相容投影均 fail closed。

`task.cancelled` 及 cancelling 下的 `task.outcome_uncertain` 收敛前必须锁定最新 cancel/emergency-stop Command，并核对 acknowledged、control ACK、correlation 和确认时间。完成、部分完成或失败事实与取消并发时不要求伪造取消 ACK，可从 CANCELLING 收敛到 Executor 已确认的真实终态。HOLD FakeExecutor 对正常 cancel 回报 cancelled，对硬 emergency-stop 保守回报 outcome uncertain；同一 `visible=false` App 已经通过正式 Rust/Uvicorn/PostgreSQL/WebSocket 跑通两个路径。

### 10.2 命令

```text
executor.hello
executor.heartbeat
task.offer
task.accept
task.reject
task.pause
task.resume
task.cancel
task.emergency_stop
task.control_ack
```

### 10.3 事件

```text
task.started
step.started
step.progress
step.completed
step.failed
session.login_required
handoff.requested
task.paused
task.resumed
task.cancelled
task.completed
task.partially_completed
task.failed
task.outcome_uncertain
diagnostic.event
```

Pydantic 是 wire format 单一来源，导出 JSON Schema 与有效/无效 fixtures，TypeScript 和 Rust 必须回放一致性测试。

## 11. 任务状态机

Control Plane 任务状态：

```text
DRAFT                  → VALIDATING
VALIDATING             → AWAITING_DEVICE | CANCELLING | FAILED
AWAITING_DEVICE        → AWAITING_PLATFORM_LOGIN | CANCELLING | FAILED
AWAITING_PLATFORM_LOGIN → DISCOVERING_TARGETS | CANCELLING | FAILED
DISCOVERING_TARGETS    → AWAITING_CONFIRMATION | AWAITING_HUMAN | CANCELLING | FAILED
AWAITING_CONFIRMATION  → DISCOVERING_TARGETS | QUEUED | CANCELLING
QUEUED                 → AWAITING_DEVICE | RUNNING | CANCELLING | FAILED
RUNNING                → PAUSED | AWAITING_HUMAN | CANCELLING
                       → SUCCEEDED | PARTIALLY_SUCCEEDED | FAILED | OUTCOME_UNCERTAIN
PAUSED                 → RUNNING | AWAITING_HUMAN | CANCELLING
AWAITING_HUMAN         → DISCOVERING_TARGETS | RUNNING | CANCELLING
                       → FAILED | OUTCOME_UNCERTAIN
CANCELLING             → SUCCEEDED | PARTIALLY_SUCCEEDED | FAILED
                       → CANCELLED | OUTCOME_UNCERTAIN
```

约束：

- 任一状态只允许显式列出的转换；
- `SUCCEEDED`、`PARTIALLY_SUCCEEDED`、`FAILED`、`CANCELLED`、`OUTCOME_UNCERTAIN` 是无出边终态，不能再次运行；
- “取消请求已发送”是 `CANCELLING`，不是 `CANCELLED`；
- 取消与完成并发时，`CANCELLING` 允许按 Executor 已确认的真实最终事实收敛到任一终态，不能为了迎合取消请求覆盖已经发生的完成或失败；
- `OUTCOME_UNCERTAIN` 只允许从 `RUNNING`、`AWAITING_HUMAN` 或 `CANCELLING` 进入，校验、排队和目标发现阶段不能伪造外部副作用不确定；
- 同状态转换一律非法；重复/迟到事件由 T3-11 按序号和幂等处理，不通过放宽领域状态机吸收；
- Executor 断连不自动等于失败；按租约和动作阶段决定等待、暂停或结果不确定；
- 已发生副作用的执行尝试不通过创建新任务偷偷覆盖；
- 重试失败目标创建新的 execution attempt 和 idempotency key，保留原链路。

T3-01 的 `TaskStateMachine` 是无 I/O 的唯一领域转换策略。输入必须是强类型 `TaskStatus`，不把字符串隐式转换成状态；拒绝使用固定不回显来源/目标的 `InvalidTaskTransition`。测试穷举 16×16 全部状态对并核对 immutable allowlist，后续 API、事件投影、FakeExecutor 和仓储不得复制或放宽矩阵。

## 12. 幂等与结果不确定

### 12.1 幂等

- 创建任务接受客户端 idempotency key；
- 每个目标动作有稳定 `action_id`；
- 每次执行尝试有独立 `execution_attempt_id`；
- Executor 本机账本在外部副作用前记录 `prepared`，确认发生后记录 `dispatched`，验证后记录 `verified`；
- 服务端按 installation/task/action/attempt 去重事件和结果。

### 12.2 结果不确定

典型情况：Executor 点击“发送”后网络或进程中断，但没有看到最终确认。

处理规则：

- 不自动再次点击；
- 先尝试无副作用查询或页面核对；
- 能确认则收敛为成功/失败；
- 无法确认进入 `OUTCOME_UNCERTAIN`；
- 用户人工核对后通过明确操作结算；
- 结算记录原任务、操作者动作、时间和证据摘要。

## 13. 风险与频控

采用“服务端授权 + 本机硬下限”双层防御：

- Control Plane 保存平台/动作/安装实例级策略和计数；
- 每个动作前签发短期 `action_authorization`；
- Executor 验证授权、目标、动作、过期时间和幂等键；
- Executor 内置不可由服务器放宽的最小间隔、单任务上限和紧停；
- 两侧规则冲突时执行更严格的结果；
- 登录失效、验证码、风控、连续失败和页面版本未知立即阻止新副作用；
- 用户控制命令不能绕过平台和本机硬限制。

MVP 的实际阈值通过专用测试账号 PoC 校准，不在架构文档虚构安全数字。

## 14. 数据模型

Control Plane PostgreSQL 最小表：

| 表 | 作用 |
| --- | --- |
| `installations` | 安装实例、公钥、状态和吊销 |
| `installation_registration_challenges` | 5 分钟设备证明、bootstrap 指纹和原子消费状态，不存原 token |
| `device_credentials` | 凭据版本、状态和过期，不存明文私钥 |
| `executors` | Executor 平台、架构、版本和最后在线 |
| `platform_session_health` | 平台登录健康元数据，不存 Cookie |
| `tasks` | 任务定义、状态、revision 和当前 attempt |
| `task_targets` | 预览目标的最小必要摘要和去重键 |
| `execution_attempts` | 每次执行尝试、租约和结果 |
| `task_actions` | 目标动作、授权、状态和结果确定性 |
| `task_events` | 单调事件流和安全消息 |
| `task_commands` | 待投递控制命令和幂等状态 |
| `artifacts` | 本地/云端 Artifact 元数据、摘要和保留策略 |
| `audit_events` | 安装实例、任务控制和安全动作 |

`installations` 的首个迁移固定 UUIDv4 主键、唯一 32 字节 Ed25519 公钥、`active`/`revoked` 状态、从 1 开始的正数 revision、`created_at`/`updated_at` 和可空 `revoked_at`。数据库同时约束公钥长度、ID 版本、状态集合、状态与吊销时间一致、时间不倒退；更新使用 `id + revision` 条件并原子递增，旧 revision 不得覆盖新状态。

`installation_registration_challenges` 固定 UUIDv4、规范环境、32 字节 bootstrap 指纹/设备公钥/payload 摘要、创建与到期时间，以及成对出现的 `consumed_at + installation_id`。数据库约束到期晚于创建、消费早于到期、长度/环境/状态一致和 Installation 外键；应用层在行锁事务中验证并消费，唯一设备公钥冲突回滚整个消费。

T3-02 的 `tasks` 表建立跨模板稳定骨架；T3-17 通过独立 `douyin_search_exposure_definitions` 表增加 `douyin.search_exposure.v1`。定义以 `(task_id, installation_id)` 复合外键绑定父 Task，并用明确列保存关键词、动作、消息模板、目标上限、有序间隔和强制预览/最终确认，不保存任意 JSON、页面原文或供应商对象。数据库、Pydantic 与领域对象共同拒绝未知模板、敏感/控制文本、动作/消息矛盾、越界数量和间隔。

`SqlAlchemyTaskRepository.create` 先锁目标 Installation，只有 active 状态才能在同一事务创建 draft Task，因此与 Installation 吊销按同一行锁线性化；未知、已吊销或重复目标共享固定拒绝。读取和状态更新始终同时携带 Task ID + Installation ID，跨 Installation 与未知 Task 对仓储调用者不可见。状态更新锁定精确 `id + installation_id + expected_revision`，调用唯一 `TaskStateMachine` 后 revision 原子加一；两个并发旧 revision 只有一个成功，非法转换、旧 revision、scope 冒充和时间回退均不修改行。

T3-06 用迁移 `20260718_0010` 把创建幂等固定为 Task 的持久事实：`creation_idempotency_key` 只能使用协议允许字符且最长 128 字节，`(installation_id, creation_idempotency_key)` 唯一；旧行回填 `legacy:<task-id>`，升级不改变原 Task 身份。仓储在同一 Installation 行锁内先查同键快照再插入，因此两个并发请求只能得到一条 Task，另一个 Installation 可复用同键且不会泄露前者。

`POST /api/v1/tasks` 必须经过 `require_current_installation_access` 取得服务端认证的强类型 scope，只接受精确 `douyin.search_exposure.v1` DTO 和必填 `Idempotency-Key`；第一次原子创建 Task/定义返回 201，同 scope/key/定义重放返回相同公开快照和 200，同键改定义拒绝。响应不含幂等键、凭据、Session、模板或任意 payload。查询与分页继续只投影公开 Task 快照，不能把定义或不受约束 JSON 泄漏到响应。

T3-07 建立同一 scope 下的 Task 只读边界。`GET /api/v1/tasks` 按 `(updated_at DESC, id DESC)` 使用 PostgreSQL keyset 查询并多取一行决定下一页，避免 offset 在并发写入下重复或跳项；opaque cursor 只编码规范 UTC 微秒时间与 Task UUIDv4，并要求 canonical JSON/Base64URL 往返一致。`GET /api/v1/tasks/{task_id}` 将非法 ID、未知 Task 和其他 Installation 的 Task 收敛成相同 404。T3-15 将已有数据库水位加入同一 DTO，两条路由当前只返回 taskId/status/revision/lastEventSequence/createdAt/updatedAt 并统一 `no-store`；任何 cursor、scope 或仓储输入失败不回显原值。React Query/Reducer 已消费这份权威快照与 T3-12 SSE，没有另建客户端事实源。

T3-03 的 `execution_attempts` 为一次任务投递与执行事实分配独立 UUIDv4 和正 `attempt_number`。状态闭集为 pending、offered、accepted、running、paused、awaiting_human、cancelling，以及 succeeded、partially_succeeded、failed、cancelled、rejected、expired、outcome_uncertain 七个终态；终态必须带 `finished_at`，非终态禁止提前带完成时间。`(task_id, attempt_number)` 不可重复，部分唯一索引保证同一 Task 最多存在一个非终态 Attempt；完成后重试必须保留旧行并使用新序号。

`task_actions` 表示一次 Attempt 中可外部观察的动作，正 `ordinal` 在 Attempt 内唯一。状态将副作用阶段固定为 planned、authorized、prepared、dispatched、verified、cancelled、outcome_uncertain，结果单独固定为 pending、succeeded、failed、cancelled、outcome_uncertain：未结算阶段只能是 pending 且没有完成时间；verified 只能结算为 succeeded/failed；cancelled 与 outcome_uncertain 必须和同名结果及完成时间一致。因此“已经派发但尚未确认”的动作不会被误写成成功，也不能靠非法组合触发自动重放。

归属链使用数据库复合外键而非应用约定：Attempt 必须命中 `(task_id, installation_id)`；Action 必须命中 `(execution_attempt_id, task_id, installation_id)`；`tasks.current_attempt_id` 必须命中自身 `(id, installation_id)` 下的 Attempt。三张表保留正 revision 和有序时间供 T3-11 事件 CAS；T3-03 只冻结数据契约，不提前实现命令投递、事件转换或任意 JSON payload。

T3-04 的 `task_events` 使用 `(task_id, sequence)` 作为主键，sequence 与 Executor 协议共享 `1..2^53-1` 跨运行时安全整数上限。事件版本精确为 `1.0`，事件类型是前端架构列出的 19 项封闭词汇；每条事件持久化事件发生/入库时间，以及事件后的 Task status 和 revision，但不保存任意 JSON、页面原文或原始 Executor 文案。可选的 Attempt/Action 引用分别使用包含 Task/Installation 的三列、四列复合外键，不能把其他执行链的事件挂进当前 Task。

Executor 来源的规范 UUIDv4 message ID 以 `(installation_id, source_message_id)` 唯一，用于 T3-11 区分同一来源消息重放；Control Plane 内部事件可以没有 source message。`SafeTaskEventMessage` 最多 1024 字符且必须是单行，和 Executor payload 共享敏感赋值、Bearer、私有绝对路径、file/data URI、控制字符与双向文本拒绝策略；PostgreSQL 再以命名约束拒绝空值、超过 1024 字符/4096 字节、控制字符和明显凭据，即使适配器误用也不会静默落入常见秘密。

Task 行新增从 0 开始的 `last_event_sequence`，与现有 status、revision、updated time 组成 `TaskSnapshotProjection`。App 重连先拉该权威快照，再从水位后的事件续订；事件行携带的 post-event status/revision 用于审计和版本降级，不允许前端自行猜测快照。T3-04 只冻结模型，T3-11 必须在一个 PostgreSQL 事务中校验序号/revision、更新 Task/Attempt/Action、推进水位并插入事件，不能把本任务的独立 INSERT/UPDATE 测试当成收敛已完成。

T3-05 的 `task_commands` 是 Control Plane 到 Executor 的持久 Outbox。主键直接使用正式 wire `message_id`，同时持久化 correlation ID、Installation/Task/Attempt 复合归属、Attempt 内安全 sequence、Executor v1 命令类型、Installation 内幂等键、deadline、正 revision、投递次数与下一投递时间。表不保存任意 JSON；当前 offer 只带安全空骨架，后续业务参数从 T3-17 已持久化的受约束 Task 定义按正式协议构造；控制命令只需稳定引用执行链，避免在 Outbox 复制一份可漂移的任务定义。

Outbox 状态精确为 pending、in_flight、delivered、acknowledged、rejected、expired。pending 才有 next delivery；in_flight 必须有未过 deadline 的 lease 且投递次数大于 0；delivered 只说明写入当前 WebSocket 成功；acknowledged/rejected 必须在 deadline 内收到独立 UUIDv4 response message 并保留确认时间；expired 不能带 ACK。offer 仅允许 task.accept/task.reject，pause/resume/cancel/emergency-stop 仅允许 task.control_ack，数据库拒绝“未发送先确认”、控制命令收到 accept、过期伪确认和倒序时间。

`(execution_attempt_id, sequence)`、`(installation_id, idempotency_key)` 和 `(installation_id, response_message_id)` 分别去重命令顺序、业务意图与响应重放；相同幂等键/响应 ID 不跨 Installation 互相阻塞。T3-09 已使用 due index 与 `FOR UPDATE SKIP LOCKED` 实现原子抢占、lease 恢复、ACK 超时重投、连接恢复和 deadline 过期；socket write 仍只等于 delivered，绝不等于 Executor 已处理。

Outbox 不保存任意 payload。T3-09 的 task.offer 当前仍发送空 object 安全骨架，用于 FakeExecutor 无副作用闭环；T3-17 已提供可读取的明确 Task 定义事实，后续 Executor 业务 payload 只能从这些受约束列按正式版本构造，不能在 Outbox 复制定义或退回任意 JSON。pause/resume 与 cancel/emergency-stop 继续复用同一确认门禁。

D6-13 将上述“无副作用骨架”和“业务动作承载 offer”按是否存在 `douyin.search_exposure.v1` 定义明确区分。后者必须把当时的 `task_target_confirmations.source_message_id` 固定为 Outbox 事实，并在每次 claim 重新匹配；绑定不存在或已失效时不抢 lease、不写 socket。这个数据库门禁不能替代 Executor 侧 A7-03/A7-04 的短期动作授权与本机硬限制，两侧后续必须同时满足。

约束：

- 每个资源都绑定 `installation_id`；
- 状态更新使用 revision/CAS，避免旧请求覆盖新状态；
- task event 以 `(task_id, sequence)` 唯一；
- message、command 和 idempotency key 使用数据库唯一约束；
- Cookie、Token、页面原文、聊天全文和本机绝对路径不入库；
- 删除任务不立即删除审计；Artifact 按保留策略异步清理。

E4-11 建立并由 B5-12、D6-10、A7-04、A7-07 逐步升级到 v5 的 Executor 本机 SQLite 只保存：

- 已接收正式命令的封闭 envelope、message/idempotency 双键与意图 SHA-256；
- 当前 Attempt 的 task 绑定、连续 command sequence、单调 event sequence、封闭 checkpoint state 和 revision；
- 与来源命令、Task/Attempt/correlation 严格绑定的待发送正式回执/事件及 delivered 标记；
- 每个平台最新的封闭健康状态、单调 `session_revision` 和观察时间；
- 已准入评论/私信的最小资源绑定、effect/verification SHA-256、封闭副作用状态、UTC 时间和 revision；
- 不保存可由 Control Plane 恢复的第二套完整业务数据库。

v1→v5 在单个排他迁移事务内保留既有 identity、command、checkpoint、outbox、平台 Session 与动作准入事实；损坏、未来版本或身份错绑继续拒绝。副作用表保持固定列，不接收任意 JSON；Artifact spool 仍由 H8 独立承接。任何 Control Plane Session、完整授权 Token、Cookie、浏览器登录态、密钥、评论/私信正文、页面原文和普通 App 配置都不进入 SQLite；用户秘密继续只在 App 私有存储或浏览器持久 Profile 的既定边界内。

B5-15 明确首次健康 Profile 的 epoch 语义：若本机尚无平台行，无论调用方是否标记“恢复”，都只能创建 revision 1；只有已有行之后的显式健康恢复才递增 revision。这样 App/Executor 重启后可从现存 Profile 直接建立首个健康事实，同时仍禁止已有非健康 epoch 被隐式健康覆盖。四轮隐藏 App 验收验证健康→健康(revision 2)→expired→risk，后两次非健康变化保持同一 revision，Control Plane 最终只保存最小 risk 投影。

B5-16 没有新增 Control Plane Profile API，也没有把浏览器路径下发给 Executor。运行时证据从隐藏 App 的正式平台页面入口启动同一个 signed Executor/BrowserRuntime，OS 进程树必须只有一个系统 Chrome 根且其 `--user-data-dir` 精确等于 Rust current Profile；`lsof` 对该根、后代与引用私有目录的关联进程逐一取证，必须观察到私有 Profile 文件且不能观察到用户默认 Chrome/Edge User Data。源码递归门禁同时拒绝生产层出现默认 User Data 常量、`--profile-directory`、Cookie 或 storage-state 读取；因此服务端仍只接收非敏感 Session 健康投影。

## 15. API 基线

### 健康与兼容

```text
GET  /api/v1/health
GET  /api/v1/version
GET  /api/v1/capabilities
```

### 安装实例

```text
POST /api/v1/installations/register
POST /api/v1/installations/session
GET  /api/v1/installations/current
POST /api/v1/installations/rotate
```

### 任务

```text
POST /api/v1/tasks
GET  /api/v1/tasks
GET  /api/v1/tasks/{task_id}
POST /api/v1/tasks/{task_id}/discover
POST /api/v1/tasks/{task_id}/confirm
POST /api/v1/tasks/{task_id}/pause
POST /api/v1/tasks/{task_id}/resume
POST /api/v1/tasks/{task_id}/cancel
POST /api/v1/tasks/{task_id}/emergency-stop
POST /api/v1/tasks/{task_id}/resolve-uncertain
GET  /api/v1/tasks/{task_id}/events
```

### 平台状态与诊断

```text
GET  /api/v1/platform-sessions/douyin
POST /api/v1/platform-sessions/douyin/logout/prepare
GET  /api/v1/diagnostics
```

当前机器契约实现平台状态查询和 logout prepare；prepare 只持久化 current Installation 的阻断门闩并返回 blocked revision，不负责也不能接收 Profile/Cookie/本机路径。登录处理、重新检查以及 prepare 之后的停止、删除、退出上报固定走本机 Tauri→Executor 链路。

### Executor

```text
WS   /api/v1/executors/connect
POST /api/v1/executors/{executor_id}/artifacts/prepare
POST /api/v1/executors/{executor_id}/artifacts/complete
```

具体请求体在实现前通过契约测试锁定，不以本清单代替 OpenAPI。

当前权威机器契约为 `contracts/openapi/control-plane.v1.json`，只包含已经实现的 Health/Version、两个 Installation 注册 operation、Installation 当前访问、设备凭据轮换/吊销、短期 Session 交换、Task 创建/列表/详情/事件 SSE/暂停/恢复/取消/紧停，以及抖音平台 Session 查询与 logout prepare，不为其他规划路由生成空壳。后端用 `automation-tool-export-openapi` 从 `create_app(database=None)` 确定性导出并检查漂移；前端 DTO 只能从该快照生成。每个后续 API 任务必须固定 operationId，并在同一提交更新快照和生成类型。

## 16. 事件与实时连接

MVP/Demo 使用单个 Control Plane 实例：

- Executor WebSocket 连接保存在当前 API 进程；
- Registry 以 Installation 为单活键，心跳只更新不含秘密的瞬时在线投影；
- 所有事件先持久化 PostgreSQL，再推送给 SSE 客户端；
- App 断线后用标准 Last-Event-ID 从数据库最后已消费序号恢复；
- 进程重启丢失连接不丢事件，Executor 自动重连并重新声明未完成 attempt；
- 不把进程内队列当成唯一事实源。

T3-20 已验证上述单实例恢复契约：隐藏 App 页面创建并运行 Task，在 Executor 离线时通过正式 Rust/API 提交取消，数据库在停服点持有 acknowledged offer、pending cancel、连续两条 Event 与 `cancelling` 快照。首个 Uvicorn 退出且端口关闭后，App 整页刷新进入固定不可用诊断；同一 FakeExecutor/Session 在服务不可达期间有界重试，第二个 Uvicorn 连接同一 PostgreSQL 后领取原 cancel，最终以 ACK 和第三条 Event 收敛为 `cancelled`。Task/Command/Event 原 ID、定义和创建时间不变；Registry 在线投影按新进程重建，不被持久化或冒充业务事实。

出现 Control Plane 多副本需求时，必须先增加跨副本连接路由和事件总线，再水平扩容；不能简单把副本数从 1 改成 N。

## 17. Artifact

MVP RPA 证据默认保存在本机：

- Executor 生成 `LocalArtifactRef`，包含稳定 ID、摘要、媒体类型、大小和受控相对路径；
- Control Plane 只保存元数据，不保存绝对路径；
- Tauri 根据 Artifact ID 在本机解析和展示；
- 云端无法直接读取本地 Artifact；
- 客户主动导出或后续启用上传时，使用短期上传授权；
- 失败截图、Trace 和日志有数量、大小和保留期限上限。

P2 内容素材和成片需要云端共享时再启用对象存储，继续使用同一 Artifact 领域接口。

## 18. 错误模型

稳定类别：

```text
validation
not_found
request_rejected
installation_unauthorized
executor_offline
protocol_mismatch
platform_login_required
human_handoff_required
platform_risk_detected
rate_limited
conflict
deadline_exceeded
cancelled
outcome_uncertain
dependency_unavailable
internal
```

错误响应包含稳定 code、可安全展示 message、request/correlation ID 和明确 retryable；不返回原始异常、Cookie、页面内容或本机路径。

## 19. 安全与隐私

- local Control Plane 只绑定 loopback；
- Demo Control Plane 强制 HTTPS、产品账号认证、账号所属 Installation 认证和请求限流；
- 既有 bootstrap 只允许受控测试/迁移注册，保持最小化、短期且不能调用业务 API；
- 数据库凭据、账号 Session 签发 Secret、密码 Pepper、设备签发密钥和对象存储密钥只在服务端 Secret 管理；
- Executor 连接为出站连接，不要求客户电脑开放入站端口；
- 平台 Cookie 和浏览器 Profile永不上传；
- Pydantic 拒绝未知字段和超大 payload；
- 数据库和执行命令使用参数化与结构化模型；
- 日志脱敏覆盖 Authorization、Cookie、Token、密码、私钥、签名 URL 和私有路径；
- 诊断数据限界，不能用“调试需要”无限保存页面和聊天；
- 验证码、滑块、风控和设备绑定只能请求人工处理。

## 20. 开发与部署

### 20.1 本地开发

```text
PostgreSQL: Docker Compose
Control Plane: uv + FastAPI reload
Tauri App: pnpm tauri:dev
Local Executor: uv 源码模式或测试构建
```

开发配置：

- App `baseUrl` 指向 `http://127.0.0.1:8765`；
- 数据库使用独立开发库；
- 测试使用独立随机数据库/Schema；
- 不用云端 Demo 数据进行本地自动化测试。

### 20.2 客户 Demo

- Control Plane 构建 Docker 镜像；
- 执行同一 Alembic 迁移；
- 使用云端 PostgreSQL；
- 配置 HTTPS、域名、账号 Session 签发 Secret、密码 Pepper、设备签发密钥和限流；
- 通过认证运维入口创建首个 Demo 账号，并验证停用、重置、全 Session 与设备吊销；
- App `demo` Profile 指向云端 baseUrl，启动后先登录产品账号，再自动绑定当前设备；
- 不使用匿名设备申请、配对码、轮询审批或随安装包分发的客户 bootstrap；
- 部署、采购和公开访问必须由用户明确指示，规划文档不构成自动部署授权。

### 20.3 配置原则

- local/demo 共享同一业务配置 Schema；
- 环境变量只在进程边界读取，领域代码不直接读环境；
- 所有超时、上限、协议版本和安全阈值有单一来源；
- 配置缺失时 fail closed，不使用隐式生产默认值；
- Demo 禁止使用 `latest` 漂移镜像标签。

## 21. 测试策略

### Control Plane 单元

- 状态机全部合法/非法转换；
- 风险策略、频控、幂等和结果不确定；
- 安装实例作用域和吊销；
- 事件序号、快照和错误脱敏。

### Control Plane 集成

- 真实 PostgreSQL 事务、唯一约束和 revision CAS；
- Alembic 从空库升级；
- FastAPI 认证、REST、SSE 和 WebSocket；
- 进程重启和连接恢复；
- outbox/命令投递与失败补偿。

### Executor 单元/集成

- 协议校验和 fixtures；
- 本机幂等账本；
- Playwright BrowserRuntime；
- 独立 Profile 锁和清理；
- 页面对象和录制样例；
- 暂停、取消、紧停、断网和崩溃恢复；
- 敏感数据脱敏和 Artifact 上限。

### 真实边界

- macOS/Windows 外部 Chrome/Edge；
- 抖音测试账号扫码、复用、过期和注销；
- 搜索、预览、受控动作和最终状态；
- 验证码/风控转人工；
- 本地 Control Plane 与云端 Demo Control Plane 各完成一次代表性端到端；
- UI Harness 不能替代上述验收。

## 22. P2/P3 扩展边界

### 内容生产与分发

- Control Plane 增加素材、Timeline、渲染任务和发布计划；
- 云端对象存储成为共享素材的权威字节存储；
- Local Executor 只接收 Artifact ID 和短期授权执行平台发布；
- `VideoRenderProvider` 隔离阿里云 IMS/ICE 或本地 FFmpeg。

### AI 员工与工作流

- 模型供应商通过 Adapter；
- AI 生成建议不能直接越过风险门禁产生外部副作用；
- 工作流节点只调用稳定 Control Plane 用例；
- 是否采用 LangGraph/Temporal以真实复杂度为依据，不能把旧项目框架直接搬入。

## 23. 禁止事项

- 禁止把 Control Plane 打进 Tauri 安装包作为正式 Demo 架构；
- 禁止 Control Plane 直接导入 Executor/RPA 实现；
- 禁止 Executor 直连 PostgreSQL；
- 禁止匿名公开云端任务写接口；
- 禁止上传平台 Cookie、浏览器 Profile、验证码或完整聊天；
- 禁止用进程内状态替代 PostgreSQL 任务事实；
- 禁止结果不确定时自动重试外部副作用；
- 禁止在未实现跨副本连接路由时盲目扩容 API；
- 禁止为未来 AI 中台提前引入多租户、RBAC、LangGraph、RAGFlow 或 LiteLLM。
