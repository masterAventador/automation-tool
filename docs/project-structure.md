# 自动化运营工具整体工程结构

> 状态：当前项目实现基线
> 建立日期：2026-07-18
> 适用范围：仓库目录、依赖方向、协议、测试、打包和本地运行数据

## 1. 核心决策

项目采用单一 Git 仓库，只有一个面向用户的交付物：Tauri 桌面客户端。

```text
同一个仓库
├── frontend/    # React UI + Tauri/Rust 桌面壳
└── backend/     # Python 可部署业务后端 + 本地 RPA Executor
```

“前端”不代表另做 Web 产品：

- React 页面运行在 Tauri WebView 中；
- Vite 浏览器模式只用于开发和 Playwright UI Harness；
- 不部署静态网页，不提供用户网页入口；
- 真实浏览器 RPA、微信、系统权限和 Sidecar 生命周期只能由 Tauri 桌面链路完成正式验收。

## 2. 根目录

```text
automation-tool/
├── frontend/                      # 桌面客户端 UI 与 Tauri 原生壳
├── backend/                       # Python 可部署业务后端和本地执行器
├── contracts/                     # 跨 Rust/TypeScript/Python 的生成协议
│   ├── openapi/                   # FastAPI OpenAPI 快照
│   ├── protocol/                  # Executor 等跨进程 JSON Schema 快照
│   ├── events/                    # 任务事件 JSON Schema
│   └── fixtures/
│       └── executor-v1/           # Python/Rust/TypeScript 共用 valid/invalid wire 样例
├── docs/
│   ├── dt-ai-helper-competitive-analysis.md
│   ├── product-plan.md
│   ├── project-structure.md
│   ├── frontend-architecture.md
│   ├── backend-architecture.md
│   ├── development-roadmap.md
│   └── adr/                       # 后续重要架构决策
├── scripts/                       # 跨工程生成、检查、纵向验收和打包脚本
│   ├── run_i2_09_acceptance.py   # 隐藏 Tauri→Rust→FastAPI/PostgreSQL 隔离验收
│   ├── run_i2_13_acceptance.py   # 后台 Uvicorn→WebSocket→PostgreSQL 隔离验收
│   ├── run_i2_14_acceptance.py   # 隐藏 Tauri 吊销诊断与最终状态验收
│   ├── run_t3_06_acceptance.py   # 隐藏 Tauri 幂等创建 Task 纵向验收
│   ├── run_t3_07_acceptance.py   # 隐藏 Tauri Task 分页/详情/scope 纵向验收
│   ├── run_t3_08_acceptance.py   # 后台真实 WebSocket Registry 单活/心跳验收
│   └── run_t3_09_acceptance.py   # 后台 PostgreSQL Outbox→WebSocket 重投/ACK/过期验收
├── .github/
│   └── workflows/                 # macOS/Windows CI 与安装包验证
├── .local/                        # 开发运行数据，必须忽略
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── .editorconfig
├── .gitignore
├── compose.yaml                   # 本地独立 PostgreSQL 开发库与测试库
└── .env.example                   # 只放非敏感示例
```

根目录不放业务源码、数据库、浏览器 Profile、测试截图、真实素材或临时脚本。

## 3. Frontend 目录

```text
frontend/
├── src/
│   ├── app/                       # Provider、路由、布局和错误边界
│   ├── features/
│   │   ├── workbench/             # RPA 运营工作台
│   │   ├── task-create/           # 新建任务与目标预览
│   │   ├── task-runs/             # 运行详情、事件和结果
│   │   ├── platform-sessions/     # 平台登录态和人工接管，不是产品账号中心
│   │   ├── diagnostics/           # 环境、浏览器和 Sidecar 诊断
│   │   ├── settings/              # 本地设置、保留与清理
│   │   ├── content-studio/        # P2 内容生产与素材
│   │   └── ai-workflows/          # P3 AI 员工与工作流
│   ├── components/                # 真正跨 Feature 复用的 UI
│   ├── api/
│   │   ├── generated/             # 由 OpenAPI 生成，禁止手改
│   │   ├── control-plane/         # 窄 Transport 接口与测试 Harness
│   │   ├── protocol/              # Executor v1 Zod 正式解析与公共 fixture 回归
│   │   ├── client.ts              # 后续业务 API 客户端
│   │   ├── events.ts              # 后续实时事件连接与投影
│   │   └── query-client.ts
│   ├── platform/
│   │   ├── tauri/
│   │   │   └── control-plane-transport.ts # 正式 Tauri invoke 适配器
│   │   ├── types.ts               # PlatformAdapter 公共接口
│   │   └── test-harness.ts        # 仅测试构建可用
│   ├── schemas/                   # Zod 运行时校验
│   ├── stores/                    # 少量纯客户端 Zustand 状态
│   ├── styles/
│   ├── test/
│   └── main.tsx
├── e2e/                           # Playwright 测试专用 UI Harness
├── e2e-tauri/                     # WebdriverIO 真实 Tauri E2E
├── src-tauri/
│   ├── src/
│   │   ├── commands/              # 有界 Tauri Command
│   │   ├── executor/              # Local Executor 握手、监管和事件桥
│   │   ├── security/              # Capability、路径和令牌边界
│   │   ├── platform/              # 文件、通知、窗口和系统能力
│   │   ├── control_plane.rs       # 固定 origin、operation allowlist 与凭据注入
│   │   ├── device_identity.rs     # Ed25519 设备身份与 App 私有存储
│   │   ├── device_credentials.rs  # 长期设备凭据的校验、替换与删除
│   │   ├── executor_protocol.rs   # Executor v1 Rust 正式解析与安全失败边界
│   │   ├── secure_store.rs        # app_data_dir 私有文件与原子替换
│   │   ├── lib.rs
│   │   └── main.rs
│   ├── tests/
│   │   └── executor_protocol_fixtures.rs # 回放三端共享原始 wire
│   ├── binaries/                  # 构建产物目录，不提交未签名临时包
│   ├── capabilities/              # 正式最小权限
│   ├── tauri.conf.json
│   ├── tauri.test.conf.json       # 后台隐藏的通用桌面测试配置
│   ├── tauri.control-plane-e2e.conf.json # 后台隐藏的网络桥纵向验收配置
│   ├── tauri.installation-revocation-e2e.conf.json # 后台隐藏的吊销验收
│   ├── tauri.task-creation-e2e.conf.json # 后台隐藏的创建 Task 验收
│   └── tauri.task-query-e2e.conf.json # 后台隐藏的 Task 查询验收
├── public/
├── package.json
├── pnpm-lock.yaml
├── vite.config.ts
├── playwright.config.ts
├── wdio.conf.ts
├── wdio.control-plane.conf.ts
├── wdio.installation-revocation.conf.ts
├── wdio.task-creation.conf.ts
├── wdio.task-query.conf.ts
├── tsconfig.json
└── README.md
```

### 3.1 Frontend 依赖方向

```text
app
 └── features
      ├── components
      ├── api
      ├── platform interface
      └── schemas/stores

Tauri implementation ──implements──> platform interface
Test harness implementation ────────> platform interface
```

规则：

- `features/` 之间通过稳定 ID、公开组件或任务事件协作，不深层导入内部文件；
- 业务页面、业务 Store 和普通 API 代码不得直接依赖 `@tauri-apps/*`；
- `src-tauri/` 不承载运营业务规则，只负责原生权限、Local Executor、系统资源和协议桥接；
- 测试 Harness 只能模拟原生边界，不能被生产构建引用；
- `content-studio` 和 `ai-workflows` 在对应阶段开始前不创建空壳菜单。

## 4. Backend 目录

```text
backend/
├── src/
│   └── automation_tool/
│       ├── control_plane/         # 独立部署的 FastAPI 业务后端
│       │   ├── bootstrap/         # 配置、注册、设备凭据和 Session 依赖装配
│       │   ├── api/               # REST、设备认证、SSE/WebSocket 和错误映射
│       │   ├── application/       # 注册、凭据、任务、配置、内容和工作流用例
│       │   ├── domain/            # 稳定 ID、Task 执行状态、版本事件、快照与 Command 契约
│       │   └── infrastructure/
│       │       ├── database/      # PostgreSQL 注册认证与 Task/Attempt/Action/Event/Command 持久化
│       │       ├── security/      # Bootstrap 签名验证等密码学适配
│       │       ├── events/
│       │       ├── object_storage/
│       │       └── observability/
│       ├── executor/              # 永远运行在用户电脑的执行器
│       │   ├── fake.py            # 无 I/O 场景引擎；复用正式 parser/envelope/幂等规则
│       │   ├── fake_client.py     # 正式 Session WebSocket 的有界联调客户端
│       │   ├── bootstrap/         # Sidecar 入口、握手和生命周期
│       │   ├── application/       # 领取、执行、暂停、取消和上报
│       │   ├── rpa/
│       │   │   ├── base/          # 平台 Adapter、动作和页面契约
│       │   │   ├── browser/       # Playwright、Profile 和页面证据
│       │   │   ├── douyin/        # MVP 抖音实现
│       │   │   ├── xiaohongshu/   # P1.3
│       │   │   ├── kuaishou/      # P1.5
│       │   │   ├── wechat_channels/
│       │   │   └── wechat/        # 按 OS 分实现
│       │   └── infrastructure/
│       │       ├── filesystem/
│       │       ├── browser/
│       │       ├── desktop/
│       │       └── logging/
│       ├── protocol/              # Control Plane ↔ Executor 版本化协议
│       │   ├── executor_envelope.py # v1 判别联合、ID/时限/幂等/序号和安全 payload
│       │   ├── schema.py          # Draft 2020-12 确定性导出与漂移检查
│       │   └── version.py         # 当前与最小/最大兼容版本
│       └── capabilities/
│           ├── content_studio/    # P2 服务端能力
│           └── ai_workflows/      # P3 服务端能力
├── migrations/                    # PostgreSQL/Alembic 迁移
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── rpa/                       # 录制页面样例与受控适配测试
│   └── fixtures/
├── pyproject.toml
├── uv.lock
├── automation-tool-executor.spec  # Local Executor 的 PyInstaller 配置
├── Dockerfile                     # Control Plane 部署镜像
└── README.md
```

T3-09 的具体落点保持分层：`application/task_command_delivery.py` 定义命令记录、投递用例与安全错误；`infrastructure/database/task_command_repository.py` 实现 PostgreSQL enqueue/lease/retry/ACK；`bootstrap/task_commands.py` 只做依赖装配；`api/executor_websocket.py` 仍是唯一网络收发入口。没有另建 dispatcher 数据库、消息队列或第二套协议模型。

T3-10 的 `executor/fake.py` 只依赖共享 `protocol/`，以正式 command envelope 驱动确定性无副作用场景并保存进程内回放账本；`fake_client.py` 只负责正式 WebSocket 传输。它们不能导入 `control_plane/`、RPA Adapter、文件系统、子进程或数据库，也不能替代 Wave 4 正式 Executor 的本机持久幂等账本、生命周期监管和真实平台实现。

### 4.1 Backend 依赖方向

```text
Control Plane API ──> application ──> domain
                           │              ▲
                           ▼              │
                     infrastructure ──────┘

Control Plane ──> protocol <── Executor application ──> RPA ports
                                                        │
                                                        ▼
                                             Executor infrastructure
```

规则：

- Control Plane 不得导入 Playwright、微信桌面实现、平台 Cookie 或用户本机路径；
- Executor 不得实现产品页面、内容/AI 工作流 API 或服务端业务数据库；
- Control Plane `domain/` 不依赖 FastAPI、PostgreSQL 或部署环境；
- 两个进程只通过 `protocol/` 的版本化消息协作，不互相导入内部实现；
- Executor `application/` 编排执行，不直接写页面选择器；
- `rpa/base/` 定义平台契约，各平台实现只影响自身目录；
- 页面选择器和页面版本不得进入任务领域模型；
- 两侧 `infrastructure/` 只实现端口，不承载频控、幂等或状态转换决策；
- P2/P3 能力只依赖公开任务、事件、Artifact 和 Adapter，不反向污染 RPA 核心。

## 5. 前后端契约

```text
Python Pydantic / FastAPI
      │
      ├── OpenAPI ───────────→ contracts/openapi/
      │                            │
      │                            ▼
      │                    TypeScript generated client
      │
      └── Executor JSON Schema → contracts/protocol/
                                      │
                                      ▼
                              共享原始 wire fixtures
                              ├── Python parser
                              ├── Rust parser
                              └── TypeScript parser
```

规则：

- Pydantic 模型是 REST 和事件 wire format 的唯一手写来源；
- TypeScript 类型和客户端从 OpenAPI 生成；
- Rust 只维护进程桥接所需的最小 DTO，并使用公共 fixtures 做一致性测试；
- 所有对象拒绝未知字段，协议版本不匹配时明确失败；
- 破坏性字段变化升级协议版本，不通过“可选字段堆积”偷偷改变语义；
- `contracts/fixtures/` 同时保存有效和无效样例，三种语言必须得到一致结论。

## 6. 进程与发布单元

开发环境：

```text
Tauri App ──HTTP/SSE──> 本机 Control Plane（FastAPI 热更新）
    │                         │
    │                         └── PostgreSQL（本机 Docker）
    │
    └──监管 Local Executor ──受认证通道──> Control Plane
         └── 系统 Chrome/Edge + App 独立运营 Profile
```

客户 Demo：

```text
Tauri App ──HTTPS/SSE──> 云端 Control Plane（同一 Python 包）
    │                         │
    │                         └── 云端 PostgreSQL
    │
    └──监管 Local Executor ──出站设备认证通道──> Control Plane
```

App 使用受控 Profile 配置 `baseUrl`，开发和 Demo 只切换端点、凭据与基础设施，不修改业务源码。

Tauri 正式安装包包含 React WebView 资源、Rust 原生桥接和 PyInstaller `onedir` Local Executor。它不包含：

- Control Plane 或 PostgreSQL；
- Web 前端服务器或公开网页；
- 单独安装的 Python；
- 用户默认 Chrome Profile；
- 测试 WebDriver、测试 Adapter 或真实平台凭据；
- 第一阶段未启用的 AI 中台。

## 7. 本地运行数据

设备侧运行数据使用操作系统 App 私有数据目录，不能写入源码目录：

```text
app-data/
├── device/
│   ├── installation.json
│   └── executor-state.db
├── browser-profiles/
│   └── douyin/default/
├── artifacts/
│   ├── evidence/
│   └── exports/
├── logs/
├── diagnostics/
└── settings.json
```

规则：

- 目录和文件权限按当前用户最小化；
- 浏览器 Profile 不进入普通备份、日志或导出；
- Artifact、日志和诊断数据都有数量、大小和时间上限；
- 数据迁移必须有 schema version、备份或可回滚策略；
- 测试使用临时目录，不能读写真实 App 数据。

## 8. 测试归属

| 测试 | 位置 |
| --- | --- |
| Python 领域和组件单元测试 | `backend/tests/unit/` |
| PostgreSQL、文件、FastAPI 集成测试 | `backend/tests/integration/` |
| REST、事件与跨语言协议 | `backend/tests/contract/`、`contracts/fixtures/` |
| 平台 Adapter 和页面样例 | `backend/tests/rpa/` |
| React 单元/组件测试 | 与 `frontend/src/` 被测文件就近 |
| 测试专用 UI Harness E2E | `frontend/e2e/` |
| Rust 原生单元/集成测试 | `frontend/src-tauri/` 与 `tests/` |
| 真实 Tauri E2E | `frontend/e2e-tauri/` |
| 真实社交平台验收记录 | 对应实施条目和本地受控证据，不提交敏感原件 |

根目录不再创建混合 `tests/`。

## 9. 包管理与生成

- Frontend：pnpm，提交 `pnpm-lock.yaml`；
- Backend：uv，提交 `uv.lock`；
- Rust：Cargo，提交 `Cargo.lock`；
- Local Executor：PyInstaller 按目标操作系统分别构建；
- Control Plane：Docker 构建，开发与 Demo 使用同一 Python 包和迁移；
- 禁止用 npm/yarn/pip requirements 与上述锁文件并存形成第二套来源；
- 生成文件必须通过脚本可重复生成，CI 检查是否漂移。

## 10. 从 agent-platform 复用边界

### 10.1 审计基线和判定方式

R0-12 审计基于旧仓库 `/Users/aventador/code/agent-platform` 的提交
`a01cfc9aa93e87e71b78b73eee3e07a3b9d31061`。只把旧代码视为经过验证的实现样本，不把它作为当前仓库的依赖、Git 子模块或运行时来源。

迁移判定统一为：

- **提取迁移**：保留失败语义和核心算法，在当前仓库先写失败测试后提取；
- **按新契约重写**：旧实现解决的问题仍存在，但公开类型、协议或存储方式已经变化；
- **删除**：与当前架构冲突，不得进入新仓库；
- **延后**：不属于 MVP，只有路线图任务进入时才能重新评估。

旧模块现有 Rust 测试结果为 35 项通过：`browser_session` 9 项、`local_executor` 14 项、`sidecar_security` 12 项。测试通过只证明旧仓库样本可参考，不替代新仓库的 RED/GREEN、macOS/Windows 和正式安装包验收。

### 10.2 `local_executor.rs` 逐项清单

| 旧能力 | 决策 | 当前项目落点 | 迁移要求 |
| --- | --- | --- | --- |
| Unix process group、Windows Job Object | 提取迁移 | `E4-09` | 保留整棵进程树终止、重复停止幂等和挂起调用清理测试；Windows 必须在真实打包产物复验 |
| 生命周期互斥、单实例 Supervisor | 按新契约重写 | `E4-07` | 保留并发启动线性化；改名 `ExecutorManager`，不暴露任意子进程能力 |
| 后台退出检测和最多两次重启 | 提取迁移 | `E4-08` | 重启预算进入显式配置；正常停止、版本不兼容、认证失败和风控停止都不得自动重启 |
| 调用超时并终止不确定进程 | 提取迁移 | `E4-07`、`E4-09` | 使用 Executor v1 类型化消息；外部副作用超时必须进入 `OUTCOME_UNCERTAIN`，不能当普通失败重试 |
| stderr 独立排空、脱敏和内存限界 | 提取迁移 | `E4-10` | 保留单行、行数、总字节上限；补设备凭据、平台 Cookie、URL 查询串和本机路径样例 |
| 256-bit stdin 会话令牌、常量时间比较 | 提取迁移 | `E4-06` | 令牌只能经 stdin bootstrap 传递，不进入 argv、env、日志、错误或响应 |
| 逐行 JSON stdio request/response | 按新契约重写 | `I2-10`～`I2-13`、`E4-02` | stdin 只做本机 bootstrap；任务、命令和事件使用带版本、ID、deadline、幂等键和序号的协议，Executor 主动连接 Control Plane |
| `serde_json::Value` 任意请求 | 删除 | — | 必须换成三语言共享 fixtures 验证的判别联合，未知字段 fail closed |
| `current_exe + --social-operations-sidecar` 自身分叉 | 删除 | — | 当前 Executor 是独立 Python/PyInstaller `onedir` 发布单元，由 Tauri 解析受信资源路径 |
| 硬编码 `CAPABILITY_ID` 和通用 Tauri invoke | 删除 | — | React 只能调用 allowlist 后的 `PlatformAdapter`/ControlPlaneTransport 操作 |
| `run_sidecar_io` 固定 ACK 假执行器 | 删除 | — | 无副作用联调统一使用 `T3-10 FakeExecutor`；不得混入正式 Executor |
| 已验证字节再执行的 TOCTOU 防护思路 | 按新契约重写 | `E4-05`、`E4-07` | 适配 PyInstaller 目录包和平台签名；不能只验证入口单文件后信任其余目录 |

### 10.3 `sidecar_package.rs` 逐项清单

| 旧能力 | 决策 | 当前项目落点 | 迁移要求 |
| --- | --- | --- | --- |
| Ed25519 manifest 签名、SHA-256、大小、平台和架构绑定 | 提取迁移 | `E4-04`、`E4-05` | Manifest 增加协议版本、构建 ID、入口和目录文件清单；任何未知/缺失字段拒绝 |
| 防版本回退 | 提取迁移 | `E4-05` | 使用明确的版本解析规则；已安装版本和 App 允许范围都参与判断 |
| 私有目录、拒绝 symlink、原子 staging/replace | 提取迁移 | `E4-05` | 安装根必须来自 Tauri app data/resource；路径不能由 React 或服务端任意指定 |
| 打开文件后的稳定 identity 检查 | 提取迁移 | `E4-05` | 覆盖验证后替换、目录成员替换和入口被调包；macOS/Windows 分别验证 |
| 凭据、Cookie、URL 和私有路径脱敏规则 | 提取迁移 | `E4-10` | 与 Python Executor 使用同一脱敏 fixtures，避免两端结论漂移 |
| `CrashRecoveryPolicy` | 移位迁移 | `E4-08` | 归入 Executor 监管模块，不留在包验证模块 |
| HTTPS 下载、redirect allowlist、在线安装 | 延后 | MVP 之后单独立项 | MVP 的 Executor 随 App 安装包交付；没有明确更新威胁模型前不做在线 Sidecar 更新器 |
| `reqwest::blocking` 下载器和远程 URL 输入 | 删除 | — | Tauri 不接受 React/Control Plane 下发的任意下载 URL |
| 单文件名 `social-operations-sidecar` | 删除 | — | 改为平台相关 PyInstaller `onedir` manifest，不沿用旧产品命名 |
| 自定义 `x.y.z` 字符串比较 | 按新契约重写 | `E4-05` | 用受维护的语义版本库或严格内部版本类型，并覆盖预发布/非法版本 |

### 10.4 `browser_session.rs` 逐项清单

| 旧能力 | 决策 | 当前项目落点 | 迁移要求 |
| --- | --- | --- | --- |
| App-owned `platform/UUID` Profile 目录 | 提取迁移 | `B5-05` | 根目录改为当前 App 标识；只创建路线图已经启用的平台，不预建五平台目录 |
| UUID 规范化和路径逃逸拒绝 | 提取迁移 | `B5-05` | 使用 `profile_id`，不能用昵称、手机号或平台账号作为目录名 |
| 祖先 symlink 防护和私有权限 | 提取迁移 | `B5-05` | 补目录竞争、权限修复失败和 Windows reparse point 测试 |
| Profile 定向删除 | 按新契约重写 | `B5-14` | 先阻止新任务、停止关联运行并释放锁，再只删除目标 Profile；失败可诊断、可重试 |
| `QrLoginSession` 状态机、revision 和 circuit open | 按新契约重写 | `B5-09`～`B5-12` | 状态来自真实页面检测并持久化到本机账本/Control Plane；风险、验证码必须人工恢复 |
| 进程内 `HashMap` 账号和单 active account | 删除 | — | 账号/Profile 事实不能只在 Tauri 内存；并发由 Profile 锁和任务账本决定 |
| `EncryptedCookieVault` 和 Cookie 导入导出 | 删除 | — | Playwright 持久 Profile 是会话唯一来源；Cookie 不复制到独立文件、不上传 Control Plane |
| 普通文件 `.cookie-key` | 删除 | — | 不再单独保存 Cookie；设备密钥只允许进入 Rust 管理的 `app_data_dir` 固定私有文件，不能形成第二份配置或导出文件 |
| ChaCha20Poly1305 Cookie 文件格式 `SOC1` | 删除 | — | 与当前持久 Profile 方案重复，避免形成第二份登录态 |
| Douyin/Xiaohongshu/Kuaishou/Wechat 等一次性枚举 | 按阶段重写 | `B5-01` 及后续平台任务 | MVP 只实现抖音 Adapter；新增平台必须有独立页面对象、契约和真实验收 |

### 10.5 跨模块保留与明确排除

继续保留并在对应任务重新实现：

- `PlatformAdapter` 的业务隔离规则；
- UI Harness、Rust 集成、Tauri E2E 和正式包审计四层测试体系；
- 版本、幂等、截止时间、事件序号、人工接管、资源清理和脱敏原则。

明确不迁移：

- 产品注册登录、企业、租户、RBAC 和 Entitlement；
- 旧项目的多租户设备注册、远程调度和 `SocialOperationsRuntime` 聚合实现；
- LangGraph、Deep Agents、RAGFlow、LiteLLM 和 AI 中台 Core；
- 旧项目绑定的腾讯云/阿里云部署基线；
- 依赖旧租户、审批或审计模型的业务 API。

实施顺序固定为：先在新仓库建立公开契约和失败测试，再提取最小实现，随后删除旧产品命名与依赖，最后通过当前项目的 macOS/Windows、真实 Control Plane 和正式安装包门禁。禁止直接复制三个旧源文件后再修改。

## 11. 禁止事项

- 禁止增加用户可访问的 Web 产品、Web 部署或第二套页面；
- 禁止把 Python 业务代码放进 `src-tauri/`，或把 Rust 原生实现放进 React Feature；
- 禁止前端手写一套与 Pydantic 不一致的 DTO；
- 禁止页面直接调用 Local Executor 任意端口、任意 URL 或任意命令；
- 禁止 Control Plane 直接操作浏览器、微信、用户文件或操作系统 UI；
- 禁止云端 Demo 暴露匿名写接口，未做用户登录时仍必须校验安装实例/设备凭据；
- 禁止把浏览器选择器散落到 API、任务服务或 React 页面；
- 禁止把 Cookie、数据库、截图、聊天、素材和运行日志提交 Git；
- 禁止为小红书、微信或未来 AI 预建没有用户闭环的空目录和空菜单；
- 禁止为了单仓库强制 TypeScript、Rust 和 Python 共用依赖管理器。
