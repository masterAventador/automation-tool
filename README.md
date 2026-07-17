# automation-tool

面向运营人员的 Tauri 桌面自动化工具。产品优先级固定为：

```text
RPA 运营 > 内容生产与分发 > AI 员工与工作流
```

当前处于第一期 MVP 实施阶段，已建立 Backend Python 包与质量基线。

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
- 尚未初始化 Frontend、数据库或 Tauri 工程，Control Plane API 也尚未实现；
- 尚未部署任何服务或执行真实社交平台动作。
