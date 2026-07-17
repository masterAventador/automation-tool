# Backend

同一个 Python 包包含可独立部署的 Control Plane 和始终运行在用户电脑上的 Local Executor；两者只能通过 `automation_tool.protocol` 的稳定协议协作，不能互相导入内部实现。

当前已建立包与质量基线，以及 Control Plane 应用工厂、lifespan、统一错误处理和 Health/Version API；尚未提供业务路由。

## 本地命令

在 `backend/` 目录执行：

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

启动本地 Control Plane：

```bash
uv run automation-tool-control-plane
```

服务只绑定 `127.0.0.1:8765`，当前端点为：

- `GET http://127.0.0.1:8765/api/v1/health`
- `GET http://127.0.0.1:8765/api/v1/version`

启动本地开发和测试数据库：

```bash
cp .env.example .env
openssl rand -hex 32
docker compose up --detach --wait
```

把两次独立生成的随机值分别填入 `.env` 的开发库和测试库密码。开发库持久化到命名卷；测试库使用 tmpfs，不与开发库共享用户、密码、数据库或数据目录。停止服务使用 `docker compose down`，明确需要删除本地开发数据时才额外使用 `--volumes`。

项目固定使用 uv 管理的 Python 3.12；不要使用 macOS 自带的 Python 3.9，也不要另外维护 `requirements.txt`、Pipenv 或 Poetry 锁文件。
