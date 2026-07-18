# Backend

同一个 Python 包包含可独立部署的 Control Plane 和始终运行在用户电脑上的 Local Executor；两者只能通过 `automation_tool.protocol` 的稳定协议协作，不能互相导入内部实现。

当前已建立包与质量基线、Control Plane 应用工厂、lifespan、统一错误处理、Health/Version API、SQLAlchemy asyncpg/Alembic 数据库基线、六类不可混用的稳定资源 ID、Installation 持久化表、无账号 Installation 注册 API、版本化设备凭据生命周期、短期设备 Session 交换、Executor v1 Envelope、受认证 Executor WebSocket、纯领域任务状态机，以及 Task/Attempt/Action 持久化模型；尚未提供 Task 业务路由或 Local Executor 进程。

`automation_tool.protocol.executor_envelope` 是 Control Plane 与 Local Executor 唯一共享的 v1 wire envelope。正式输入必须使用 `parse_executor_message` 解析：只接受最大 32 KiB 的 UTF-8 JSON object，拒绝重复 key、未知 envelope 字段、非 `1.0` 版本、未知 message type、非 canonical UUIDv4、非 UTC 时间、倒序 deadline、非法幂等键和超出 JavaScript 安全整数范围的序号。生命周期消息没有伪造的 task ID；任务命令、回执和事件必须同时绑定 task/attempt。Payload 最大 16 KiB、深度 8、单集合 64 项、单字符串 4096 字符，并拒绝 Cookie/Token/密钥字段、私有路径、inline data URI、非有限数字和双向控制字符；所有解析失败只返回不挂底层异常链的固定错误。

权威 Schema 固定为 `contracts/protocol/executor-v1.schema.json`，只能由 Pydantic 源通过 `automation-tool-export-executor-schema` 生成。`contracts/fixtures/executor-v1/` 包含 6 个 valid 和 25 个 invalid wire 样例；标准 Draft 2020-12 validator 负责 15 个结构层失败，Python、Rust、TypeScript 正式解析器另外实现 Schema `x-semantic-validation-required` 声明的 10 个 deadline、重复 key、敏感信息、私有路径、inline data、非有限数字和递归资源失败。三端必须回放同一目录，不能复制另一套 fixture。

资源 ID 统一使用规范小写 UUIDv4，并通过 `InstallationId`、`ExecutorId`、`TaskId`、`ExecutionAttemptId`、`ActionId` 和 `ArtifactId` 值对象隔离；一次在线连接另使用短生命周期的 `ExecutorConnectionId`。外部字符串必须先调用对应类型的 `parse`，新资源调用 `new`；不能把普通字符串、另一类资源 ID 或非 UUIDv4 值直接带入领域层。

`control_plane.domain.task_state_machine` 定义 16 个 `TaskStatus`、5 个无出边终态和唯一显式转换矩阵。取消必须先进入 `cancelling`；取消/完成竞态从 `cancelling` 按真实事实收敛；`outcome_uncertain` 只可从已执行、人工接管或取消中的状态进入。所有 256 个状态对均由单元测试分类，字符串输入、自循环、终态复活和未列出的跳转固定拒绝，后续应用服务不得另建状态分支。

`tasks` 表已通过迁移 `20260718_0006` 建立 Task UUIDv4、Installation 外键、状态、正 revision 和有序时间，并保留 `(id, installation_id)` 复合绑定及 Installation 更新时间索引。`SqlAlchemyTaskRepository` 只允许 active Installation 创建 draft Task；读取/转换始终携带 Installation scope，状态转换复用领域状态机并以 expected revision + 行锁做 CAS。未知/已吊销 Installation、重复 Task、跨 scope、旧 revision、时间回退和并发输家固定拒绝。平台模板参数将在 T3-17 以明确 DTO 增加，当前表不接受任意 JSON。

迁移 `20260718_0007` 新增 `execution_attempts`、`task_actions` 和 `tasks.current_attempt_id`。Attempt 以 `(task_id, attempt_number)` 去重，部分唯一索引保证每个 Task 最多一个非终态 Attempt；终态必须有完成时间，重试只能创建新序号。Action 以 `(execution_attempt_id, ordinal)` 去重，明确区分 planned/authorized/prepared/dispatched/verified 等副作用阶段与 pending/succeeded/failed/cancelled/outcome_uncertain 结果；数据库拒绝阶段、结果和完成时间矛盾。Task→Attempt→Action 全链路使用包含 Task 与 Installation 的复合外键，不能把其他任务或安装实例的子资源挂入当前链路。

`installations` 表保存 UUIDv4 主键、唯一 32 字节 Ed25519 公钥、`active`/`revoked` 状态、正数 revision、创建/更新时间和吊销时间。数据库约束拒绝状态与吊销时间矛盾、倒序时间、非法 UUID 版本、重复公钥和非 32 字节公钥；revision 更新必须在语句中携带旧值作为 CAS 条件。

`DemoBootstrapGrant` 的 claims 由离线 Ed25519 私钥签名为 `atb1` 凭据；Control Plane 只配置 32 字节公钥并验证签名，不持有签发私钥。claims 有效期最多 7 天，绑定一个规范 `DemoEnvironmentId`，唯一 `BootstrapPurpose` 为 `installation.register`。服务端签发最长 5 分钟且不超过 bootstrap 到期时间的一次性 challenge，绑定环境、bootstrap SHA-256 指纹和设备公钥；App 用设备 Ed25519 私钥签名后，PostgreSQL 在同一事务中锁定 challenge、验证证明、创建 Installation、签发初始设备凭据并标记消费。批次次数、撤销和审计仍由 C10-06 实现，不在本 API 中伪造。

长期设备凭据格式为 `atdc1.<credential-id>.<256-bit-secret>`，只在初始签发或轮换成功时返回一次。`device_credentials` 表只保存 SHA-256 摘要、正数版本、精确 `device.session.exchange` scope 和 `active`/`rotated`/`revoked` 历史；每个 Installation 由部分唯一索引限制为一个 active 版本。轮换和吊销先锁 Installation、再锁凭据并常量时间核对摘要；旧版本、错误秘密、未知凭据和已吊销 Installation 统一返回不回显输入的认证失败。

当前长期凭据可调用 `POST /api/v1/device-sessions` 换取 `atds1.<session-id>.<256-bit-secret>`，响应禁止缓存。Session 固定 5 分钟寿命并允许客户端时钟最多落后 30 秒，只能精确选择 `app.control-plane` 或 `executor.connect` 一项能力。`device_sessions` 表只保存摘要及 Installation、父凭据 ID、父凭据版本的复合绑定；认证使用 `[not_before, expires_at)` 半开边界，父凭据轮换/吊销或 Installation 撤销后既有 Session 立即失效。

服务器运维侧使用 `automation-tool-revoke-installation --installation-id ... --expected-revision ...` 原子吊销一个 Installation；命令在单事务中更新 Installation revision、active 长期凭据和全部 Session，未知/重复/stale revision/并发失败不会回显目标。App 业务路由统一依赖 `require_current_installation_access` 校验 `app.control-plane` Session 并取得强类型 Installation scope；`GET /api/v1/installations/current` 是首个消费者，后续任务路由不得相信客户端自报 scope 或复制一套认证。

`WS /api/v1/executors/connect` 只接受唯一 `automation-tool.executor.v1` 子协议和 `executor.connect` Session；认证后立即从长连接 scope 擦除原始 Authorization Header。升级后第一帧必须是正式 Python parser 验证的 `executor.hello`，并把连接绑定到 Installation、Executor、协议版本、Executor 版本、平台、架构和独立 `ExecutorConnectionId`；后续 I2-13 阶段只接受同一身份的 heartbeat。连接每秒重新读取 PostgreSQL 认证状态，Session、父凭据或 Installation 失效会以固定 4401 关闭，冒充、协议错误、Hello 超时和内部失败使用固定关闭码与不泄密文案。Uvicorn 固定使用 `websockets-sansio`，并在传输层把消息限制为 32 KiB。

真实网络验收在 `backend/` 执行 `uv run python ../scripts/run_i2_13_acceptance.py`。脚本后台启动隔离 PostgreSQL 和真实 Uvicorn，经正式 REST 换票/吊销端点与标准 WebSocket 客户端验证子协议、超大帧、冒充、在线吊销和旧 Session 重连拒绝，结束后回收服务、端口、容器、网络和卷；不启动桌面 App。

真实测试版 Tauri App 已通过正式 Rust 网络桥消费 Health、Installation 注册/访问、设备凭据轮换/吊销和 Session 换票端点。Rust 从 App 私有目录加载设备私钥和长期凭据，执行签名与凭据注入，React 不接触任何秘密；I2-14 纵向验收以隐藏窗口连接真实 FastAPI 与隔离 PostgreSQL，再由服务端 CLI 吊销并验证同一 App 进入独立失效诊断及最终数据库状态。

## 本地命令

在 `backend/` 目录执行：

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run automation-tool-export-openapi --output ../contracts/openapi/control-plane.v1.json --check
uv run automation-tool-export-executor-schema --output ../contracts/protocol/executor-v1.schema.json --check
```

后端 Pydantic/FastAPI 是跨端 DTO 的唯一来源。路由契约变化后先去掉 `--check` 重新导出快照，再到 `frontend/` 执行 `pnpm generate:api`；提交前两侧都必须通过各自漂移检查。

先在仓库根目录启动开发数据库：

```bash
cp .env.example .env
openssl rand -hex 32
# 把随机值分别填入两个数据库密码，并把开发库密码同步到 DATABASE_URL
docker compose up --detach --wait postgres-dev
```

再在 `backend/` 目录升级数据库并启动本地 Control Plane：

```bash
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env automation-tool-control-plane
```

注册 API 默认关闭；Demo/本地注册联调必须同时提供下列公开部署配置，缺一或格式非法时启动 fail closed。公钥使用无 padding 的 canonical base64url，离线签发私钥不得进入服务器环境、仓库或日志：

```bash
AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID=demo-cn-1
AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY=<32-byte-ed25519-public-key-base64url>
```

服务只绑定 `127.0.0.1:8765`。启动时配置缺失会 fail closed；数据库断开时 Health 返回可重试的结构化 `503 dependency_unavailable`。当前端点为：

- `GET http://127.0.0.1:8765/api/v1/health`
- `GET http://127.0.0.1:8765/api/v1/version`
- `POST http://127.0.0.1:8765/api/v1/installations/registration-challenges`
- `POST http://127.0.0.1:8765/api/v1/installations`
- `GET http://127.0.0.1:8765/api/v1/installations/current`
- `POST http://127.0.0.1:8765/api/v1/device-credentials/rotations`
- `POST http://127.0.0.1:8765/api/v1/device-credentials/revocations`
- `POST http://127.0.0.1:8765/api/v1/device-sessions`
- `WS ws://127.0.0.1:8765/api/v1/executors/connect`

迁移回滚验证（只对明确的测试数据库执行）：

```bash
AUTOMATION_TOOL_DATABASE_URL='postgresql+asyncpg://...test...' uv run alembic downgrade base
AUTOMATION_TOOL_DATABASE_URL='postgresql+asyncpg://...test...' uv run alembic upgrade head
```

两套数据库必须使用两次独立生成的随机密码。开发库持久化到命名卷；测试库使用 tmpfs，不与开发库共享用户、密码、数据库或数据目录。停止服务使用 `docker compose down`，明确需要删除本地开发数据时才额外使用 `--volumes`。自动化测试会启动随机端口上的隔离测试库并在结束后删除其容器、网络和卷。

项目固定使用 uv 管理的 Python 3.12；不要使用 macOS 自带的 Python 3.9，也不要另外维护 `requirements.txt`、Pipenv 或 Poetry 锁文件。
