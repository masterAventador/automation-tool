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

稳定业务资源 ID 使用规范小写 UUIDv4。Python 领域层分别使用 `InstallationId`、`ExecutorId`、`TaskId`、`ExecutionAttemptId`、`ActionId` 和 `ArtifactId` 不可变值对象；即使底层 UUID 相同，不同资源类型也不相等、不可互相解析。外部输入拒绝 nil、非 v4、大小写/空白/URN/无连字符等非规范形式，错误不得回显原始值。`connection_id`、协议 message/correlation ID 在各自任务中沿用同一规范并建立具体类型。

私钥和长期设备凭据留在 Tauri 安全存储，不进入 React 或 Python 普通配置。

### 9.2 Demo 注册

受控 Demo 安装包使用限时、限环境、限一次或限次数的 bootstrap 授权完成设备注册。bootstrap 只允许注册，不允许创建或执行任务。注册后后端签发设备凭据并可随时吊销。

领域层的 `DemoBootstrapGrant` 固定唯一 purpose `installation.register`，绑定一个小写规范 Demo 环境 slug，采用 `[not_before, expires_at)` 半开时窗且硬上限 7 天。调用必须使用强类型 purpose/环境；原始字符串即使内容相同也不能越过能力边界，跨环境、未生效和到期均返回固定拒绝原因且不回显输入。这个对象只表达待签名 claims 的授权语义，不存 token、不读取环境变量，也不提前替代 C10-06 的注册次数、吊销、批次持久化和审计。

I2-05 将这些 claims 封装为验证专用的 `atb1.<payload>.<signature>`：payload 必须是 exact-field canonical JSON，签名算法固定 Ed25519，Control Plane 只从部署配置读取 32 字节验证公钥和精确 Demo 环境，不持有离线签发私钥。未知字段、重复 JSON key、非 canonical base64url、错误版本/用途/时间类型、超长、篡改和错误 signer 统一拒绝；服务端只保存 token 的 SHA-256 指纹用于 challenge 绑定，不保存原 token。

注册固定两步：`issueInstallationRegistrationChallenge` 验证 bootstrap 后产生 32 字节 CSPRNG nonce，并返回最长 5 分钟、且不晚于 bootstrap 到期的 opaque canonical signing payload；`completeInstallationRegistration` 再次验证同一 bootstrap，按 challenge ID `SELECT ... FOR UPDATE`，常量时间核对环境、bootstrap 指纹和 payload 摘要，再用 challenge 绑定的设备公钥验证 Ed25519 签名。同一事务创建 Installation 并标记 challenge 已消费，因此进程重启、串行或并发重放都只能成功一次。到期采用半开边界，错误设备、另一份有效 bootstrap、篡改 payload、未知 challenge 和跨环境都不消费 challenge。

I2-06 把初始凭据签发并入同一个注册事务：凭据使用 `atdc1.<credential-id>.<256-bit-secret>` opaque 格式，明文只在注册或轮换成功响应中出现一次；PostgreSQL 只保存秘密的 SHA-256 摘要，不保存明文凭据、设备私钥或额外服务端签名私钥。凭据版本从 1 开始，唯一 scope 固定为 `device.session.exchange`，数据库保存 `active`、`rotated`、`revoked` 历史，并通过部分唯一索引保证每个 Installation 同时最多一个 active 版本。

设备可使用当前 bearer 调用 `rotateDeviceCredential` 或 `revokeDeviceCredential`。仓储先按公开 credential ID 定位，再按固定顺序锁 Installation 和凭据，用常量时间比较摘要并确认 Installation/凭据仍 active；轮换在单事务中把旧版本标记为 rotated、关联新版本并插入下一正数版本，吊销则将当前版本标记 revoked。两个并发轮换只有一个能成功，旧版本、错误秘密、未知凭据、重复吊销和已吊销 Installation 对外共享固定 401，不能据此枚举凭据状态。scope 只授权 I2-07 的短期 Session 交换，不直接授权任务或业务 API；凭据轮换/吊销属于 bearer 自身生命周期，而非额外业务 scope。

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

所有 Control Plane 与 Executor 消息包含：

```text
protocol_version
message_id
message_type
sent_at
deadline_at
installation_id
executor_id
task_id
execution_attempt_id
correlation_id
idempotency_key
sequence
payload
```

规则：

- 精确匹配支持的协议版本；
- 未知字段拒绝；
- `deadline_at` 严格晚于 `sent_at`；
- 同一任务事件序号单调；
- message ID 和幂等键有唯一约束；
- payload 不允许平台 Cookie、验证码、私有路径和内联截图；
- 大文件通过受控 Artifact 引用，不通过 WebSocket 内联。

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
DRAFT
  → VALIDATING
  → AWAITING_DEVICE
  → AWAITING_PLATFORM_LOGIN
  → DISCOVERING_TARGETS
  → AWAITING_CONFIRMATION
  → QUEUED
  → RUNNING
      ↔ PAUSED
      → AWAITING_HUMAN
      → CANCELLING
  → SUCCEEDED | PARTIALLY_SUCCEEDED | FAILED | CANCELLED | OUTCOME_UNCERTAIN
```

约束：

- 任一状态只允许显式列出的转换；
- 终态不可再次运行；
- “取消请求已发送”是 `CANCELLING`，不是 `CANCELLED`；
- Executor 断连不自动等于失败；按租约和动作阶段决定等待、暂停或结果不确定；
- 已发生副作用的执行尝试不通过创建新任务偷偷覆盖；
- 重试失败目标创建新的 execution attempt 和 idempotency key，保留原链路。

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
GET  /api/v1/tasks/{task_id}/events/stream
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

当前权威机器契约为 `contracts/openapi/control-plane.v1.json`，只包含已经实现的 Health/Version 与两个 Installation 注册 operation，不为其他规划路由生成空壳。后端用 `automation-tool-export-openapi` 从 `create_app(database=None)` 确定性导出并检查漂移；前端 DTO 只能从该快照生成。每个后续 API 任务必须固定 operationId，并在同一提交更新快照和生成类型。

## 16. 事件与实时连接

MVP/Demo 使用单个 Control Plane 实例：

- Executor WebSocket 连接保存在当前 API 进程；
- 所有事件先持久化 PostgreSQL，再推送给 SSE 客户端；
- App 断线后从数据库快照与最后事件序号恢复；
- 进程重启丢失连接不丢事件，Executor 自动重连并重新声明未完成 attempt；
- 不把进程内队列当成唯一事实源。

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
