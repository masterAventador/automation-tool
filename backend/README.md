# Backend

同一个 Python 包包含可独立部署的 Control Plane 和始终运行在用户电脑上的 Local Executor；两者只能通过 `automation_tool.protocol` 的稳定协议协作，不能互相导入内部实现。

当前已建立包与质量基线、Control Plane 应用工厂、lifespan、统一错误处理、Health/Version API，以及 SQLAlchemy asyncpg/Alembic 数据库基线；尚未提供业务路由。

## 本地命令

在 `backend/` 目录执行：

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

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

服务只绑定 `127.0.0.1:8765`。启动时配置缺失会 fail closed；数据库断开时 Health 返回可重试的结构化 `503 dependency_unavailable`。当前端点为：

- `GET http://127.0.0.1:8765/api/v1/health`
- `GET http://127.0.0.1:8765/api/v1/version`

迁移回滚验证（只对明确的测试数据库执行）：

```bash
AUTOMATION_TOOL_DATABASE_URL='postgresql+asyncpg://...test...' uv run alembic downgrade base
AUTOMATION_TOOL_DATABASE_URL='postgresql+asyncpg://...test...' uv run alembic upgrade head
```

两套数据库必须使用两次独立生成的随机密码。开发库持久化到命名卷；测试库使用 tmpfs，不与开发库共享用户、密码、数据库或数据目录。停止服务使用 `docker compose down`，明确需要删除本地开发数据时才额外使用 `--volumes`。自动化测试会启动随机端口上的隔离测试库并在结束后删除其容器、网络和卷。

项目固定使用 uv 管理的 Python 3.12；不要使用 macOS 自带的 Python 3.9，也不要另外维护 `requirements.txt`、Pipenv 或 Poetry 锁文件。
