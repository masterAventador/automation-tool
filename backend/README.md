# Backend

同一个 Python 包包含可独立部署的 Control Plane 和始终运行在用户电脑上的 Local Executor；两者只能通过 `automation_tool.protocol` 的稳定协议协作，不能互相导入内部实现。

当前已建立包与质量基线、Control Plane 应用工厂、lifespan、统一错误处理、Health/Version API、SQLAlchemy asyncpg/Alembic 数据库基线、六类不可混用的稳定资源 ID、Installation 持久化表、无账号 Installation 注册 API 和版本化设备凭据生命周期；尚未提供任务等业务路由。

资源 ID 统一使用规范小写 UUIDv4，并通过 `InstallationId`、`ExecutorId`、`TaskId`、`ExecutionAttemptId`、`ActionId` 和 `ArtifactId` 值对象隔离。外部字符串必须先调用对应类型的 `parse`，新资源调用 `new`；不能把普通字符串、另一类资源 ID 或非 UUIDv4 值直接带入领域层。

`installations` 表保存 UUIDv4 主键、唯一 32 字节 Ed25519 公钥、`active`/`revoked` 状态、正数 revision、创建/更新时间和吊销时间。数据库约束拒绝状态与吊销时间矛盾、倒序时间、非法 UUID 版本、重复公钥和非 32 字节公钥；revision 更新必须在语句中携带旧值作为 CAS 条件。

`DemoBootstrapGrant` 的 claims 由离线 Ed25519 私钥签名为 `atb1` 凭据；Control Plane 只配置 32 字节公钥并验证签名，不持有签发私钥。claims 有效期最多 7 天，绑定一个规范 `DemoEnvironmentId`，唯一 `BootstrapPurpose` 为 `installation.register`。服务端签发最长 5 分钟且不超过 bootstrap 到期时间的一次性 challenge，绑定环境、bootstrap SHA-256 指纹和设备公钥；App 用设备 Ed25519 私钥签名后，PostgreSQL 在同一事务中锁定 challenge、验证证明、创建 Installation、签发初始设备凭据并标记消费。批次次数、撤销和审计仍由 C10-06 实现，不在本 API 中伪造。

长期设备凭据格式为 `atdc1.<credential-id>.<256-bit-secret>`，只在初始签发或轮换成功时返回一次。`device_credentials` 表只保存 SHA-256 摘要、正数版本、精确 `device.session.exchange` scope 和 `active`/`rotated`/`revoked` 历史；每个 Installation 由部分唯一索引限制为一个 active 版本。轮换和吊销先锁 Installation、再锁凭据并常量时间核对摘要；旧版本、错误秘密、未知凭据和已吊销 Installation 统一返回不回显输入的认证失败。I2-07 才会使用该最小 scope 换取短期 Session，I2-08 才会把返回的长期凭据接入 Tauri 系统安全存储。

## 本地命令

在 `backend/` 目录执行：

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run automation-tool-export-openapi --output ../contracts/openapi/control-plane.v1.json --check
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
- `POST http://127.0.0.1:8765/api/v1/device-credentials/rotations`
- `POST http://127.0.0.1:8765/api/v1/device-credentials/revocations`

迁移回滚验证（只对明确的测试数据库执行）：

```bash
AUTOMATION_TOOL_DATABASE_URL='postgresql+asyncpg://...test...' uv run alembic downgrade base
AUTOMATION_TOOL_DATABASE_URL='postgresql+asyncpg://...test...' uv run alembic upgrade head
```

两套数据库必须使用两次独立生成的随机密码。开发库持久化到命名卷；测试库使用 tmpfs，不与开发库共享用户、密码、数据库或数据目录。停止服务使用 `docker compose down`，明确需要删除本地开发数据时才额外使用 `--volumes`。自动化测试会启动随机端口上的隔离测试库并在结束后删除其容器、网络和卷。

项目固定使用 uv 管理的 Python 3.12；不要使用 macOS 自带的 Python 3.9，也不要另外维护 `requirements.txt`、Pipenv 或 Poetry 锁文件。
