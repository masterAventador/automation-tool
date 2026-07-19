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
- 安装实例认证；
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

E4-02 已实现该层的最小正式入口 `automation-tool-executor`。stdin bootstrap 只允许一条换行结尾、最多 16 KiB、无重复 key/未知字段的 JSON object；字段固定为 bootstrap 版本、受控 WebSocket URL、短期 `executor.connect` Session、Installation/Executor UUIDv4 和心跳间隔。`ws` 只允许 `127.0.0.1` 有效端口，远端只允许标准端口 `wss`，Session 只驻留在 `SecretStr`/Authorization Header，不进入 argv、环境、stdout、stderr 或异常。

进程从自身运行环境确定 macOS/Windows 与 arm64/x86_64，向真实 Control Plane 发送正式 Hello，连接存活后按单调 sequence 发送 Heartbeat；首条心跳后 stdout 只投影固定 `executor.healthy`，SIGINT/SIGTERM 后关闭 WebSocket 并投影 `executor.stopped`。当前任何 Task Command 或其他应用帧都 fail closed，不提前伪造 ACK/事件；E4-12 在本机幂等账本和监管完成后才接入无副作用命令回放。

E4-03 将该入口锁为 PyInstaller 6.21.0 `onedir`：spec 直接执行 `executor/__main__.py`，冻结产物不依赖用户另装 Python。构建依赖只在 uv dev group，当前未加入 Python Playwright；macOS 本机已从冻结入口验证 bootstrap 与网络失败的固定退出契约，GitHub macOS/Windows 矩阵使用同一测试，但当前 Hosted Runner 因账户 Billing/Actions spending limit 在启动前被拒绝，因此 Windows 仍是明确待验收项。该 PoC 不承担目录 Manifest、签名、完整性或防降级，以上边界继续由 E4-04/E4-05 实现。

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

### 8.2 浏览器发现

按平台检测稳定路径：

- macOS：Google Chrome、Microsoft Edge；
- Windows：注册表和标准安装位置；
- 用户可在诊断页选择一个受支持浏览器；
- 路径必须解析为允许的浏览器应用/签名，不能执行任意文件；
- 未安装受支持浏览器时返回明确诊断，不静默下载未知程序。

### 8.3 页面定位

定位策略按优先级：

1. 稳定语义角色、label 和可访问属性；
2. 稳定业务文案与明确父级范围；
3. 版本化 DOM locator；
4. 受控视觉/OCR 兜底；
5. 人工接管。

选择器集中在平台页面对象中，记录 `platform_page_version`。页面变化时安全失败，不用模糊坐标继续点击。

MVP 不使用 AI 页面理解、stealth 或验证码识别。

## 9. 安装实例与设备认证

产品第一期没有用户账号，但 Demo 云端需要服务认证。

### 9.1 实体

- `installation_id`：一次 App 安装的稳定 ID；
- `device_public_key`：本机生成密钥对的公钥；
- `device_credential`：后端签发、可撤销；
- `device_session`：短期访问能力；
- `executor_id`：一次 Executor 安装/版本实例；
- `connection_id`：一次在线连接。

稳定业务资源 ID 使用规范小写 UUIDv4。Python 领域层分别使用 `InstallationId`、`ExecutorId`、`TaskId`、`ExecutionAttemptId`、`ActionId` 和 `ArtifactId` 不可变值对象；即使底层 UUID 相同，不同资源类型也不相等、不可互相解析。外部输入拒绝 nil、非 v4、大小写/空白/URN/无连字符等非规范形式，错误不得回显原始值。一次在线连接使用独立短生命周期 `ExecutorConnectionId`；协议 message/correlation ID 也沿用同一规范并保持用途隔离。

私钥和长期设备凭据留在 Tauri `app_data_dir` 下由 Rust 管理的 App 私有文件，不调用系统钥匙串，也不进入 React、Tauri Command 或 Python 普通配置。

### 9.2 Demo 注册

受控 Demo 安装包使用限时、限环境、限一次或限次数的 bootstrap 授权完成设备注册。bootstrap 只允许注册，不允许创建或执行任务。注册后后端签发设备凭据并可随时吊销。

领域层的 `DemoBootstrapGrant` 固定唯一 purpose `installation.register`，绑定一个小写规范 Demo 环境 slug，采用 `[not_before, expires_at)` 半开时窗且硬上限 7 天。调用必须使用强类型 purpose/环境；原始字符串即使内容相同也不能越过能力边界，跨环境、未生效和到期均返回固定拒绝原因且不回显输入。这个对象只表达待签名 claims 的授权语义，不存 token、不读取环境变量，也不提前替代 C10-06 的注册次数、吊销、批次持久化和审计。

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

这个方案满足“用户无登录页面、打开即用”，但不是正式账号体系。若以后开放公开产品，必须增加用户身份、设备归属、恢复和撤销流程。

### 9.3 请求授权

- App 业务请求作用域固定到 installation；
- Executor 连接同时绑定 installation、executor、版本和平台；
- 服务端每次创建/读取/控制任务都校验 installation；
- Executor 只能领取目标 installation 的任务；
- 设备被吊销后禁止新任务并要求 Executor 断开；
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

I2-10 的正式 Pydantic 入口是 `parse_executor_message`。Envelope 以 `message_type` 做判别联合，把 24 种已声明 v1 类型精确分到 Executor 生命周期、任务命令、任务回执和任务事件四类；公共字段拒绝未知项且模型冻结。`protocol_version` 必须显式为 `1.0`；message/correlation/installation/executor/task/attempt ID 都是用途隔离的 canonical 小写 RFC 4122 UUIDv4 字符串；时间必须为带时区 RFC3339 且精确 UTC，deadline 严格晚于 sent；幂等键为 1～128 字符受限字符集。I2-12 的跨语言 RED 证明 `2^63-1` 不能被 TypeScript `number` 精确表达，因此 sequence 已统一收紧为 `1..2^53-1` 的 strict safe integer。

正式解析只接受最大 32 KiB 的 UTF-8 JSON object，并拒绝重复 key。Payload 当前是后续任务消息 Schema 的受限容器：最大 16 KiB、深度 8、每层集合最多 64 项、字符串最多 4096 字符；递归拒绝 Cookie/Token/密码/私钥/凭据字段、凭据赋值文本、Bearer、私有绝对路径、`file://`、inline data URI、控制/双向字符和 NaN/Infinity。任何结构、语义、编码或资源限制失败都收敛成固定 `ExecutorProtocolError`，且异常对象不保留原始 cause/context；调用方不得直接把 Pydantic `ValidationError` 暴露到日志或远端。具体 payload 业务字段仍须随对应消息任务收紧。

I2-11 已把 Pydantic 判别联合确定性导出为 `contracts/protocol/executor-v1.schema.json`，dialect 固定 Draft 2020-12，并内嵌 `$id`、wire/payload 资源上限和 `x-semantic-validation-required`。Schema 尽可能结构化表达 24 种 message type、required/unknown field、用途 ID pattern、幂等键、序号、任务作用域、payload 顶层项数和 UTC RFC3339 pattern；无法由标准 JSON Schema表达的 deadline 先后、重复 key、递归 payload 限制和隐私文本由显式语义扩展声明，不能静默省略。

公共 `contracts/fixtures/executor-v1` 固定 6 个 valid 与 25 个 invalid wire 文件：15 个结构层无效样例必须由标准 Schema 和正式解析器同时拒绝，另 10 个语义层无效样例允许标准 Schema 接受但必须由正式解析器拒绝。Schema 生成器提供 write/check 两种模式，缺失或逐字漂移都用固定错误失败；Backend CI 在测试前执行 `--check`。Python、Rust、TypeScript 只能回放这套公共 fixtures 并实现相同语义扩展，不得各自另造“等价”样例。

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

约束：

- 每个资源都绑定 `installation_id`；
- 状态更新使用 revision/CAS，避免旧请求覆盖新状态；
- task event 以 `(task_id, sequence)` 唯一；
- message、command 和 idempotency key 使用数据库唯一约束；
- Cookie、Token、页面原文、聊天全文和本机绝对路径不入库；
- 删除任务不立即删除审计；Artifact 按保留策略异步清理。

Executor 本机 SQLite 只保存：

- 已接收命令和幂等结果；
- 当前执行尝试和安全检查点；
- action 副作用账本；
- 待上传事件和 Artifact spool；
- Profile revision 与非敏感会话健康；
- 不保存可由 Control Plane 恢复的第二套完整业务数据库。

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
GET  /api/v1/platform-sessions
POST /api/v1/platform-sessions/{platform}/login
POST /api/v1/platform-sessions/{platform}/logout
GET  /api/v1/diagnostics
```

### Executor

```text
WS   /api/v1/executors/connect
POST /api/v1/executors/{executor_id}/artifacts/prepare
POST /api/v1/executors/{executor_id}/artifacts/complete
```

具体请求体在实现前通过契约测试锁定，不以本清单代替 OpenAPI。

当前权威机器契约为 `contracts/openapi/control-plane.v1.json`，只包含已经实现的 Health/Version、两个 Installation 注册 operation、Installation 当前访问、设备凭据轮换/吊销、短期 Session 交换和 Task 创建/列表/详情/事件 SSE/暂停/恢复/取消/紧停，不为其他规划路由生成空壳。后端用 `automation-tool-export-openapi` 从 `create_app(database=None)` 确定性导出并检查漂移；前端 DTO 只能从该快照生成。每个后续 API 任务必须固定 operationId，并在同一提交更新快照和生成类型。

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
- Demo Control Plane 强制 HTTPS、安装实例认证和请求限流；
- bootstrap 授权最小化、短期、可撤销且不能调用业务 API；
- 数据库凭据、设备签发密钥和对象存储密钥只在服务端 Secret 管理；
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
Tauri App: pnpm tauri dev
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
- 配置 HTTPS、域名、安装实例签发密钥和限流；
- App `demo` Profile 指向云端 baseUrl；
- 每个 Demo 安装包/批次使用隔离 bootstrap 授权；
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
