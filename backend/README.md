# Backend

同一个 Python 包包含可独立部署的 Control Plane 和始终运行在用户电脑上的 Local Executor；两者只能通过 `automation_tool.protocol` 的稳定协议协作，不能互相导入内部实现。

当前已建立包与质量基线，以及 Control Plane 应用工厂、lifespan 和统一错误处理；尚未提供服务器启动命令、Health/Version 或业务路由。

## 本地命令

在 `backend/` 目录执行：

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

项目固定使用 uv 管理的 Python 3.12；不要使用 macOS 自带的 Python 3.9，也不要另外维护 `requirements.txt`、Pipenv 或 Poetry 锁文件。
