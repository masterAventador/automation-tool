# automation-tool

面向运营人员的 Tauri 桌面自动化工具。产品优先级固定为：

```text
RPA 运营 > 内容生产与分发 > AI 员工与工作流
```

当前处于第一期 MVP 实施阶段。Wave 1 工程闭环已完成，正在实施安装实例认证与 Control Plane/Executor 通道。

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
- 服务端运维 CLI 可用 revision CAS 原子吊销 Installation、active 长期凭据与全部 Session；`GET /api/v1/installations/current` 和可复用业务守卫强制 `app.control-plane` scope，未来创建任务 API 已在台账中强制依赖该守卫；
- Frontend 已锁定 React、TypeScript、Vite 和 Ant Design，严格类型、Lint、冻结安装与生产资产构建通过；Vite 仅绑定 loopback 且仓库没有 Web 部署入口；
- Tauri v2 已具备真实 macOS 主窗口、生产 CSP、零 IPC 权限 Capability、桌面图标与 Cargo 锁文件，Rust/Clippy/无 bundle 构建通过；
- Tauri 首启设备身份已在 Rust 内生成 Ed25519 密钥：私钥和长期设备凭据只进入 `app_data_dir` 下由 Rust 管理的 App 私有文件，不进入 React、Tauri IPC、`localStorage` 或普通配置，也不调用系统钥匙串；
- App 打开后直接进入 RPA 运营工作台壳；Control Plane 不可用与 Installation 已吊销分别显示脱敏诊断和重试状态，页面不存在产品登录或注册入口；
- BaseUrl Profile 使用 Zod fail closed：local 固定为 `127.0.0.1:8765`，demo 强制 HTTPS 且主机必须精确命中构建允许列表；
- ControlPlaneTransport 已接入正式 Tauri IPC/Rust 网络桥：生产入口由真实 App 发起 Health 请求；Rust 侧以固定 origin、封闭 operation allowlist、禁止重定向/代理、请求与响应大小/时间上限和关联 ID 调用 Control Plane，不暴露任意 URL 代理；
- Installation 注册、长期凭据轮换/吊销和两类短期 Session 已通过测试版真实 Tauri App → 正式 Rust 桥 → 真实 FastAPI/PostgreSQL 纵向验收；设备私钥、Bootstrap、长期凭据和短期票据全程留在 Rust，React/IPC 响应只得到公开结果；
- FastAPI OpenAPI 3.1 快照与 `openapi-typescript` DTO 已覆盖 Health/Version、Installation 注册/访问、设备凭据生命周期和短期 Session 交换，后端/前端分别具备确定性漂移检查；
- Playwright UI Harness 已覆盖工作台、服务不可用和重试恢复；正式 `dist/` 扫描证明不包含 Harness 页面或测试 Adapter；
- 桌面端已建立 Vitest、Playwright、Rust、WebdriverIO 四层统一门禁；WebdriverIO 使用 embedded provider 在真实 macOS Tauri/WKWebView 中验证无登录工作台和原生窗口标签，测试插件只由 `desktop-e2e` 特性启用；
- GitHub Actions 已建立 Backend、Frontend、Rust 三路质量门禁，以及 macOS/Windows 真实桌面构建与 Tauri 冒烟矩阵；所有第三方 Action 固定完整提交 SHA，工作流只读且不发布、不部署；
- installation、executor、task、execution attempt、action 和 artifact 已使用六种不可混用的规范 UUIDv4 领域类型；
- Task 纯领域状态机已锁定 16 个状态、5 个无出边终态和全部显式转换；256 个状态对均已穷举，取消先进入 `CANCELLING`、取消/完成竞态按最终事实收敛，`OUTCOME_UNCERTAIN` 不可从执行前阶段伪造；
- `tasks` 已具备 PostgreSQL/Alembic 持久化、Task UUIDv4、Installation scope、状态/revision/时间约束和仓储 CAS；只允许 active Installation 创建，跨 Installation 查询/更新不可见，并发旧 revision 只有一个赢家；
- `execution_attempts`、`task_actions` 与 `tasks.current_attempt_id` 已形成 Task/Installation 复合绑定；每个 Task 只有一个非终态 Attempt、重试序号不可重复，每个 Attempt 内 Action ordinal 唯一，Action 阶段与结果确定性由数据库一致性约束锁定；
- Executor v1 Envelope 已建立 Pydantic 判别联合：24 种生命周期/任务命令/回执/事件精确分型，显式 `1.0` 版本、规范 UUIDv4、UTC deadline、幂等键、正序号和受限安全 payload 均 fail closed；
- Executor v1 Draft 2020-12 Schema 已从 Pydantic 确定性导出；Python、Rust、TypeScript 正式解析器共同回放 6 个 valid、25 个 invalid 公共 fixtures，并对结构、deadline、隐私和资源边界给出一致结论；
- `WS /api/v1/executors/connect` 已通过真实 Uvicorn 网络边界接入 `executor.connect` 短期 Session：精确子协议、Installation/Executor/运行时版本绑定、独立连接 ID、32 KiB 传输上限、周期重认证和吊销断连均 fail closed；
- Demo Bootstrap 已建立最多 7 天、精确环境绑定、只允许 installation 注册的 fail-closed 能力模型，不能作为业务 API 凭据；
- 任务等业务 API、Local Executor 进程和 RPA 功能尚未实现；
- 尚未部署任何服务或执行真实社交平台动作。
