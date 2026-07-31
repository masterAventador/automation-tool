# 自动化运营工具前端架构

> 状态：当前项目实现基线
> 建立日期：2026-07-18
> 适用范围：React UI、Tauri/Rust 桌面壳、Control Plane 通信和本地执行器桥接

## 1. 建设目标

前端只交付一个 Tauri 桌面客户端。P9 本地 MVP 打开 App 后直接进入 RPA 运营工作台；客户 Demo 在 U9 账号体系完成后先进入产品登录，认证成功且设备归属有效才进入工作台。

它需要同时处理三类能力：

1. 产品 UI：任务创建、目标预览、运行详情、结果、设置和诊断；
2. 网络控制面：连接开发机本地或 Demo 云端 FastAPI；
3. 本机原生能力：监管 Local Executor、验证并打开 App 内置运营浏览器、文件、通知、窗口和紧急停止。

不建设面向用户的 Web 产品。Vite 浏览器模式只是测试专用 UI Harness。

## 2. 核心技术栈

- React + TypeScript：页面和业务组件；
- Vite：桌面前端构建与测试 Harness；
- Tauri v2：正式桌面容器；
- Rust：原生权限、网络凭据、Local Executor 生命周期和事件桥；
- Ant Design：基础组件、表格、表单、弹窗和状态反馈；
- React Router：桌面端页面路由；
- TanStack Query：Control Plane 服务端状态；
- Zustand：少量纯客户端状态；
- Zod：运行时协议与配置校验；
- Vitest + Testing Library：单元和组件测试；
- Playwright：测试专用 UI Harness E2E；
- WebdriverIO + Tauri Service：真实桌面 E2E。

第一期不引入 Next.js、SSR、SEO、PWA 或 Web 部署配置。

当前 Tauri 基线锁定 CLI 2.11.4 / Rust crate 2.11.5。主窗口只加载 `http://127.0.0.1:1420` 开发服务或安装包内 `dist/`，不配置远程页面；生产 CSP 仅允许自身资源和 Tauri IPC 端点。`main` Capability 当前权限列表为空，后续原生命令必须按任务逐项授权，不能直接扩大为通用文件、Shell 或网络权限。

## 3. 总体架构

```text
React 页面与 Feature
        │
        ├── TanStack Query ──> ControlPlaneTransport
        │                           ├── Tauri/Rust 正式实现
        │                           └── Test Harness 实现
        │
        └── PlatformAdapter ──> Tauri Commands
                                    ├── Local Executor 生命周期
                                    ├── 文件/通知/窗口
                                    └── 紧急停止/人工接管

Tauri/Rust ──HTTPS/SSE──> Python Control Plane
Tauri/Rust ──stdio/受认证 IPC──> Python Local Executor
Tauri/Rust ──只读 Resources──> 已验证的内置 Chromium
```

业务页面不感知当前 Control Plane 在本机还是云端，也不直接感知 Rust、Sidecar 或操作系统。

## 4. 分层与职责

### 4.1 App 层

负责：

- 全局 Provider；
- 路由和布局；
- 主题与设计 Token；
- Query Client；
- `ControlPlaneTransport` 和 `PlatformAdapter` 初始化；
- App 启动诊断；
- 全局 Error Boundary；
- 任务运行时的全局紧急停止入口。

当前 P9 本地 MVP 没有认证路由守卫，启动成功后固定进入 `/workbench`；后端不可用时进入可恢复的连接故障页。U9-04 将为客户 Demo 增加独立登录/恢复路由和业务路由守卫，未登录时不挂载工作台。

App 启动边界已实现 checking、ready、blocked 及兼容 unavailable/revoked 状态和安全重试；ready 后才挂载正式工作台。F1-08 保留注入式 `StartupCheck` 用于孤立 UI 测试；生产 `main.tsx` 组合 `TauriControlPlaneTransport` 与 `TauriStartupEnvironmentGateway`，并行检查 Control Plane、Local Executor、内置浏览器组件和 App 私有数据目录。Control Plane Health 若发现已有长期凭据，还会换取 `app.control-plane` Session 并请求当前 Installation 访问探针；精确 401 单独映射吊销，网络/服务/协议故障映射普通不可用。

本机诊断只经固定 `check_local_startup_environment` Command 返回 exact `{appData,executor,embeddedBrowser}` 封闭枚举。Rust 复用既有 AppData 私有权限、BrowserProfile identity/DACL、内置发行物 Manifest/摘要验证、编译期动作信任配置和 signed Executor verifier；它不会启动 Executor 或浏览器，也不会序列化路径、版本、摘要、PID、凭据、页面内容或底层异常。内置组件缺失、损坏或版本不兼容时，Gate 给出重新安装官方客户端的安全提示并保持工作台封锁，不提供 Chrome/Edge 选择或系统浏览器 fallback；本机修复工具只保留 Executor 诊断等仍有效能力。只有 Control Plane 不可用或 Installation 吊销时不展示无关本机修复入口。真实 WebView 只能 invoke 固定 Rust Command，禁止直接请求 Control Plane。

U9-04 已在客户 Demo Profile 中把产品账号会话门禁放到启动组合根最外层：账号状态未确认、未登录或离线时不挂载 P9 启动检查、诊断工具或业务工作台；登录、恢复、改密、注销和重启 refresh 都经固定 Rust Command，React 只得到安全账号投影。U9-05 再在登录成功后增加账号所属 Installation 门禁，复用设备密钥证明完成自动归属绑定，不生成配对码、不轮询设备审批，也不要求后台逐设备批准。P9 本地 Profile 继续保持上一段四态和无产品登录入口。

T3-06 已在同一正式 Rust Control Plane client 中加入封闭的创建 Task operation；T3-17 已以 `create_douyin_search_exposure_task` 固定 Command 接入窄任务表单。React 只能提交 `douyin.search_exposure.v1` 的明确字段，Zod 先校验，Rust 再校验安全文本、动作/消息关系、数量、间隔和强制确认，然后自行从 App 私有 vault 换取 `app.control-plane` Session 并注入受限幂等键。WebView 不接触 bearer、Header 或任意 URL。完成证据来自 `visible=false` 真实 Tauri App 点击表单并核对 PostgreSQL 最终定义，而不是浏览器 Harness 或直接 HTTP。

D6-03 将表单和 Gateway 的关键词约束收敛到同一个 `douyinSearchKeywordSchema`，并导出只读 `80` 字符/`100` 目标上限给表单控件；长度使用 `Array.from` 的 Unicode code point 语义，与 Python `len`、Rust `chars().count()` 和 PostgreSQL `char_length` 一致，不再用 UTF-16 code unit 误拒非 BMP 文本。C0/C1/DEL、Bidi、首尾空白和安全文本违规在表单调用 Gateway 前显示固定校验错误，生产 Gateway 与 Rust 仍做第二、第三次复验；React 没有 trim、截断或自动改写用户输入。

A7-05 以 `douyinActionMessageTemplateSchema` 将评论/私信文案收紧为固定文案或只含 `{{target_display_name}}` 的封闭模板。表单直接复用 Gateway Schema，未知、带空格、表达式式或畸形花括号在 IPC 前拒绝；Gateway 再复验 500 Unicode code point、非空字面和安全文本，Rust 在序列化/HTTP 前做同等 fail-closed 检查。当前路径只持久模板原文，不在 WebView/Rust 渲染目标数据，不调用 LLM，也没有评论/私信发送代码。

A7-06 让目标预览公开 DTO 同时携带 action、原始 message template 和独立 `confirmationRevision`。最终确认弹窗打开时冻结 page/revision/action/template/count 五项审阅事实；后台 Query 或事件导致的新预览不会替换已打开弹窗的提交参数。Rust 仍只发送固定确认 operation，并在目标预览专用错误映射中把后端冲突保留为 `request_rejected`；React 只据此显示“目标列表已变化”并回拉，其他协议/传输异常继续脱敏。唯一隐藏 App 已实际发出旧 revision、收到真实后端拒绝，再审阅新版本后成功确认。

T3-07 在该 Rust client 中增加 `ListTasks` 与 `GetTask` 两个封闭 operation。列表只允许固定 `/api/v1/tasks`、`1..100` limit 和长度受限的 canonical Base64URL cursor；详情先把 Task ID 验证为规范 UUIDv4 才构造固定路径。T3-15 将既有数据库水位加入同一公开快照，当前 DTO 为 taskId/status/revision/lastEventSequence/createdAt/updatedAt；Rust 复核 16 态枚举、正 revision、安全水位、UTC 时间、列表降序及 cursor 形状，跨 Installation 详情只得到统一拒绝。生产 React 只通过窄 `TauriTaskProjectionSource` 消费，不存在通用 URL/请求代理。

T3-12 在同一个正式 Rust client 中增加 `StreamTaskEvents`：路径只能由规范 Task UUID 构造，Rust 自行从 App 私有 vault 换取 `app.control-plane` Session 并注入 Bearer，支持标准 `Last-Event-ID`，限制单连接 512 KiB、单帧 64 KiB 和验收用有界停止数。解析器要求 `text/event-stream`、匹配 request ID、`no-store/no-transform`、禁代理缓冲，以及唯一 id/event/data 字段；公开 DTO 再核对连续安全整数序号、`1.0` 版本、封闭事件/Task 状态、UUIDv4、UTC 时间、进度与消息边界。React/IPC 不接触 Session、Header 或原始 SSE 文本。T3-15 已在同一解析循环逐条调用回调并推送 Tauri Channel，不等整条 SSE 结束，也没有重新用 WebView EventSource。

T3-13 又在相同 Rust client 中加入固定 `PauseTask`/`ResumeTask` operation。调用者只能提供规范 Task UUID 和受限幂等键；Rust 自行换 App Session、发送空 JSON、构造 `/pause` 或 `/resume` 固定路径，并只接受 202 创建或 200 重放。公开命令对象必须通过 Command/Task/Attempt UUIDv4、跨运行时安全 sequence、精确 command type、封闭 outbox status、正 revision 和 UTC deadline 校验。T3-18 已把两种操作接入正式运行详情，仍不向 React 暴露任意 operation、Header 或 bearer。

T3-14 继续在同一 Rust client 增加固定 `CancelTask`/`EmergencyStopTask` operation，沿用规范 Task UUID、受限幂等键、App 私有 vault 换票和严格公开 Command 解析；固定路径只能是 `/cancel` 与 `/emergency-stop`，仍只接受 202/200。T3-16 已接全局紧停，T3-18 已接运行详情的二次确认控制；WebView 不新增通用 URL、Header、bearer 或原始响应入口。

T3-15 建立正式 Task 投影边界：`TauriTaskProjectionSource` 只 invoke 固定快照、列表和事件 Channel Command，Rust 从私有 vault 换票并返回精确公开 DTO；`taskProjectionKeys`/Query options 管理服务端快照，纯 Reducer 以 status/revision/lastEventSequence 为权威。水位内事件直接去重；下一序号缺口、Task/revision 回退、未知版本/类型或畸形 DTO 只进入 `refresh_required`，先失效并回拉 Query 快照再续订；连续不兼容超过有界预算进入 `degraded`，不从事件名猜状态。正常 SSE 限时关闭也先读新快照再续订，不计作协议降级。唯一 `visible=false` App 已从 WebView 正式 TypeScript source 经 Rust/真实后端/FakeExecutor 收敛 sequence 1..5 到 succeeded。

T3-16 将投影接入正式 RPA 工作台：`Workbench` 展示 Control Plane/Executor 状态、当前任务和最近任务，较新的实时快照会覆盖列表旧状态。H8-14 移除从最近 20 条客户端任务估算的“今日”指标，改由独立 10 秒 Query 调用固定 `get_workbench_metrics`，展示当前 Installation 的累计任务/动作成功、失败、接管和结果不确定事实；Zod 与 Rust 都严格校验 `workbench.metrics.v1`、安全整数、未知字段和分类总数一致性。运行状态继续 1 秒轮询，不被数据库指标查询放大。

`TauriWorkbenchGateway` 只允许固定工作台状态、只读指标与紧停 Command；全局紧停必须二次确认，同一 Task 的不确定重试复用幂等键，提交后回拉列表/详情/运行状态/指标，最终状态仍只认 Executor 事件。两类轮询都允许隐藏窗口继续执行。H8-14 的唯一 `visible=false` App 已从正式页面经 TypeScript gateway、Tauri IPC、Rust 固定 operation、真实 Uvicorn/App Session 和隔离 PostgreSQL 渲染结构化指标，并证明其他 Installation 不可见、读取前后事实不变；长期凭据仍只在 `app_data_dir`，不使用系统钥匙串。

T3-18 将工作台 Task 入口接到正式 `TaskRunDetails`。页面先读 TanStack Query 权威快照，再从持久事件起点通过同一 Rust SSE/Tauri Channel 重放并跟随时间线；只投影明确的进度、step 与 `actionId` 事实，缺少目标或平台证据时显示空态。`TauriTaskRunControlGateway` 暴露四个窄方法，对应四个固定 Rust Command；按钮按权威状态启停，取消/紧停二次确认，不确定重试在同 revision 复用幂等键，提交回执不会冒充 Executor 已执行。事件畸形、错 Task 或缺口会 fail closed 并要求显式重载。唯一 `visible=false` App 已真实点击四类控制并经后端与 HOLD FakeExecutor 收敛最终事实。

H8-16A 在同一详情页接入 strict `TaskDiscoveryGateway` 与 `TauriTaskDiscoveryGateway`。正式组合根只构造一个 Adapter，它只 invoke 固定 `start_task_discovery`；草稿、等待平台登录、等待确认和人工接管状态才显示启动/重新发现，等待登录与接管状态可导航到既有平台状态页。同一 Task revision 的不确定重试复用幂等键，卸载会取消 UI 等待，成功后只失效 Task 详情/列表并等待权威快照和 SSE；响应跨 Task、未知字段或非法 revision/watermark 均 fail closed。D6-10 隐藏 App 验收已从正式工作台进入详情并点击按钮，再经 Rust App Session、真实 Uvicorn/PostgreSQL 与 LocalExecutorProcess 展示候选；feature-gated 准备 Command 只创建 draft Task，不代替页面发起发现。

H8-16B 不新增页面、HTTP 客户端或通用 IPC。Rust 只在固定 `StartTaskDiscovery` operation 的合法 423 JSON/no-store/请求关联响应上产生 `InstallationBusy`，Tauri 映射为 `installation_busy`；TypeScript Adapter 只保留该封闭码和固定公开文案，任务详情显示设备单活冲突并保持竞争 Task 可重试。隐藏 App 从两个草稿 Task 的正式详情按钮制造竞争，看到提示后再让 Executor 收敛赢家，证明错误不是 Mock 或直接 HTTP 伪造。

A7-15 在同一运行详情增加只读 `TaskTargetResultSource`，没有第二个页面或 Web 路由。正式实现只调用固定 `getTaskTargetResults` Tauri Command；Rust 自行换 `app.control-plane` Session、构造 `/api/v1/tasks/{task_id}/target-results` 固定路径并严格校验 Task/Target/Action UUID、ordinal、UTC 时间、封闭状态/evidence 与响应大小，React 不接触 bearer、Header、baseUrl、Executor SQLite 或任意响应。TanStack Query 独立管理 loading/empty/error/retry；Task SSE 前进与控制成功只失效查询并重取 PostgreSQL 权威投影，UI 不从事件 label 或本地控制回执推断目标结果。

页面按 pending/running/succeeded/skipped/failed/outcome_uncertain 展示固定状态标签，并把 `action-result-evidence.v1` 翻译成内置中文摘要；不回显消息正文、页面文本、URL、Profile、Cookie、路径、策略内部字段或错误原文。扩展后的 T3-18 runner 仍使用唯一 `visible=false` App，从现有详情真实发出 TypeScript source → IPC → Rust → Uvicorn/PostgreSQL 请求并核对成功、跳过、失败、不确定；测试准备数据与 FakeExecutor 不替代页面调用，退出后 App/WebdriverIO/服务/端口/Compose/AppData 全部回收。

### 4.2 Feature 层

第一期 Feature：

```text
features/
├── workbench/              # 运营总览和当前任务
├── task-create/            # 抖音任务表单
├── task-runs/              # 快照、目标预览、事件、控制和结果
├── platform-sessions/      # 抖音服务端健康、本机处理与后续安全注销
├── diagnostics/            # 后端、Executor、浏览器和权限诊断
└── settings/               # 本地保留、内置浏览器组件状态和诊断导出
```

规则：

- Feature 内部维护页面、组件、Query、Mutation、Schema 和测试；
- 跨 Feature 通过稳定任务 ID、公开组件、路由或事件投影协作；
- 不允许从其他 Feature 深层导入内部文件；
- P2/P3 未开始前不添加空壳菜单和不可用按钮。

### 4.3 公共组件层

只接受被两个以上 Feature 真实复用的组件，例如：

- `TaskStatusBadge`；
- `EventTimeline`；
- `HumanHandoffCard`；
- `EmergencyStopButton`；
- `DiagnosticResult`；
- `EvidencePreview`。

公共组件不直接发请求，也不导入 Tauri API。

### 4.4 API 层

负责：

- 由 FastAPI OpenAPI 生成 DTO 和 operation ID；
- 将 DTO 转成前端领域模型；
- 统一超时、取消、错误码和关联 ID；
- TanStack Query Key 工厂；
- SSE/事件快照的合并与恢复；
- Zod 校验关键边界。

Executor v1 的 TypeScript 正式入口是 `src/api/protocol/executor-envelope.ts` 的 `parseExecutorMessage`；Rust 正式入口是 `src-tauri/src/executor_protocol.rs` 的 `parse_executor_message`。两者不从 UI、IPC 或网络输入推断类型，而是与 Python 权威模型共同回放 `contracts/fixtures/executor-v1` 的原始 UTF-8 wire：判别类型、任务作用域、UUIDv4、UTC 微秒 deadline、安全整数、重复 key、资源上限和 payload 隐私规则均 fail closed，失败只返回固定错误。B5-12 新增 Executor-scoped `platform.session_health`，payload 只能包含平台、封闭状态、正 revision 和 UTC 观察时间，不能携带 task scope 或任意附加页面/Profile 字段。B5-13 只在 React 侧消费 Control Plane 已验证的 `{platform,state,observedAt}` 公开投影；打开处理/重新检查结果是独立本机 flow 事实，页面不得用它伪造服务端状态或观察时间。I2-13 已在 Control Plane 网络入口复用 Python 正式 parser，E4-02 已让独立 Local Executor 发送 Hello/Heartbeat，E4-12/B5-13 已沿同一 WebSocket 接入持久命令回放与平台投影。

当前 FastAPI OpenAPI 3.1 快照固定在 `contracts/openapi/control-plane.v1.json`，系统 operationId 为 `getSystemHealth`、`getSystemVersion`；U9-03 已加入产品账号登录、refresh、注销、改密和恢复五个固定 operation 及三种用途隔离 Bearer scheme。`frontend/scripts/openapi.mjs` 使用锁定的 `openapi-typescript` 从快照机械生成 `src/api/generated/control-plane.ts`，`--check` 在系统临时目录重新生成并逐字比较；生成文件禁止手改。生成账号 DTO 只表示机器契约已就绪，U9-04 完成 Rust 私有 secret 存储与登录路由前，React 不得直接调用这些 HTTP operation。

业务组件只处理统一 `AppError`，不直接判断 Axios、Rust 或 FastAPI 的原始异常。

### 4.5 Platform 层

Executor 生命周期继续使用 E4-13 的 `PlatformAdapter`：

```ts
interface PlatformAdapter {
  getExecutorStatus(): Promise<ExecutorManagerStatus>
  restartExecutor(): Promise<ExecutorManagerStatus>
  getExecutorDiagnostics(): Promise<readonly string[]>
  emergencyStopExecutor(): Promise<ExecutorManagerStatus>
}
```

平台会话使用独立的窄 `PlatformSessionGateway`，不把页面动作塞进 Executor 通用生命周期接口：

```ts
interface PlatformSessionGateway {
  getDouyinSession(): Promise<PlatformSessionSnapshot>
  openDouyinLogin(): Promise<PlatformSessionAction>
  recheckDouyinLogin(): Promise<PlatformSessionAction>
  logoutDouyinSession(): Promise<PlatformSessionSnapshot>
}
```

`TauriPlatformSessionGateway` 只 invoke 四个固定无参数 Command 并对返回值做 exact Zod 校验。查询经 Rust 固定 Control Plane operation 取得服务端投影；登录两个动作经本机 Rust→signed Executor 处理；注销由 Rust 固定编排服务端 prepare、Executor 紧停、Profile 定向删除、Executor 重启、path-free logout complete 与权威状态轮询。React 只能确认并触发，不能提供 URL、浏览器、Profile、headless、页面事实、revision 或“已登录”布尔值，也不能在本机动作完成前自行改写快照。文件、诊断导出、通知和其他平台能力仍在对应 Wave 按真实需求增加。

强制约束：

- 业务代码不得导入 `@tauri-apps/*`；
- 运行环境判断只能在 Adapter 初始化处；
- 不支持能力返回明确状态，不能静默成功；
- 测试 Harness Adapter 只存在于开发/测试构建；
- 生产构建必须通过静态检查证明不包含测试 Adapter。

## 5. Control Plane 通信

### 5.1 Base URL Profile

只维护配置 Profile，不维护两套客户端代码：

| Profile | Base URL | 用途 |
| --- | --- | --- |
| `local` | `http://127.0.0.1:8765` | 开发、单元集成和本机联调 |
| `demo` | `https://demo-api.<domain>` | 客户 Demo 安装包 |
| `production` | 以后确认 | 正式上线 |

规则：

- `baseUrl` 是公开配置，不是密钥；
- 非 loopback 地址必须使用 HTTPS；
- 正式构建只允许签名配置声明的域名，不能接受页面传入任意 URL；
- 环境切换由构建/运行 Profile 完成，禁止在 Feature 中判断环境；
- Demo 和 local 使用相同 API 版本、OpenAPI 和业务行为。

当前 `src/schemas/base-url-profile.ts` 是 UI/配置侧的运行时 Schema：local 只接受无凭据、无路径的精确 `http://127.0.0.1:8765`；demo 只接受无凭据、无路径、无非标准端口的 HTTPS origin，并与构建提供的主机允许列表做规范化后精确匹配。任何非法输入只返回固定配置错误，不回显 URL 内容。I2-09 的 Rust Transport 使用编译期固定 local origin，完全不接受 React 传入 baseUrl；Demo 签名构建 Profile 接入时仍须在 Rust 侧独立验证允许域名，不能因为 React 已校验就信任页面输入。

### 5.2 Transport

正式 Tauri 使用 Rust `ControlPlaneTransport`：

- Rust 读取受控 `baseUrl`；
- 安装实例长期私钥和设备凭据保存在 Tauri `app_data_dir` 下由 Rust 管理的 App 私有文件，不调用系统钥匙串，也不进入 React、`localStorage` 或普通配置；
- Rust 为请求附加短期访问凭据和关联 ID；
- Rust 只允许调用从 OpenAPI operation ID 生成的允许列表，不提供任意 URL 代理；
- SSE/事件流由 Rust 建立并通过 Tauri Channel 传给 React；
- 网络断开时关闭旧流，恢复后先拉任务快照，再从最后序号继续订阅。

测试专用 UI Harness 只实现 Feature 已有的窄 TypeScript gateway/source 接口；生命周期场景用测试 Adapter 和 `sessionStorage` 模拟任务事实，不访问产品凭据或任意网络 operation。它不能进入正式包，也不能替代正式 App/Rust/后端验收。

当前生产 TypeScript 健康 Transport 只暴露 `checkHealth`；Task 状态另由窄 `TaskProjectionSource` 暴露列表、详情和事件订阅，两者都不接受 URL、Header、凭据或任意 operation。Rust 使用 `reqwest` 从固定 local origin 发起请求，封闭 allowlist 覆盖 Health、Installation/凭据/Session 和固定 Task 路径；请求禁止系统代理与重定向，并严格校验状态、响应头、关联 ID、UUIDv4、UTC 时间、公开快照、事件和分页游标。底层异常只映射固定安全错误，React 不得到原始网络原因或秘密。

设备注册由 Rust 使用 App 私有目录中的生产设备身份签名 challenge，注册响应中的 `atdc1` 直接写入同一 Rust 私有凭据仓。Session 令牌保存在 Rust `Zeroizing` 缓冲，轮换以新值原子替换，吊销后删除；Bootstrap、私钥、长期凭据和 Session 都没有 React/序列化/通用 IPC 读写面。测试 Harness 仍只用于分层 UI 测试，不能替代正式桥。

I2-04 建立的设备密钥边界已在 I2-08 按当前产品决策迁移到统一 App 私有存储：Rust 1.88 基线使用 `ed25519-dalek 3.0.0`、`getrandom 0.4.3` 和 `zeroize 1.9.0` 生成、派生并及时清零临时私钥缓冲；私钥与长期凭据分别使用 `device-identity-ed25519-v1` 和 `device-credential-v1` 固定文件，根目录由正式 Tauri 入口通过 `app.path().app_data_dir()` 解析。Unix 目录/文件权限固定为 `0700`/`0600`，Windows 继承当前用户 AppData ACL；存储拒绝符号链接、非普通文件、超限内容和不安全文件名，写入经同目录独占临时文件、同步及原子替换完成。正式入口只托管公钥和 Rust 凭据仓，不提供序列化、Command 或 React 接口，也不调用系统钥匙串。私钥缺项时首启生成并保存，已有值必须精确为 32 字节；长期凭据必须是 canonical `atdc1` 且允许原子替换和幂等删除。权限拒绝、随机源失败、损坏和非法凭据均 fail closed，并收敛为固定不泄密错误。

当前 Playwright 入口固定为 `harness.html`，支持显式 available、unavailable、flaky、revoked 健康/授权投影，以及 `task-lifecycle` 的创建→暂停→恢复→取消、独立成功和整页刷新恢复，只在 Vite 测试服务存在。正式 Vite 仍以 `index.html` 为唯一入口；构建后扫描 `dist/`，拒绝 `harness.html`、Harness runtime 字符串和测试 Transport 标记。扫描器本身用干净/污染临时目录回归，不能静默失效。

### 5.3 客户 Demo 的产品账号与设备归属

P9 本地 MVP 保留当前无产品登录的 Installation 认证；任何客户 Demo 必须先完成 U9-01～U9-06，并使用以下正式路径：

- 未登录时只挂载产品登录与恢复页面，不渲染工作台、任务路由或业务数据；
- 登录标识和密码只作为当前表单的短生命周期输入传给固定 Rust Command，不写入 localStorage、普通配置、日志或错误；产品 access/refresh secret 始终只由 Rust 私有存储持有，不返回 React；
- Rust 通过固定 Control Plane operation 登录、刷新和注销，React 只接收脱敏账号 ID、展示名、状态和可操作错误；
- 首次登录成功后，Rust 复用 I2 设备密钥证明，把当前 Installation 原子绑定登录账号并取得账号范围内的设备凭据，不使用 bootstrap 配对码、设备轮询或后台逐设备审批；
- Demo 业务 Session 必须同时来源于有效产品账号会话和该账号下有效 Installation；账号停用、全 Session 注销、设备吊销或归属变化任一发生都立即 fail closed；
- 已绑定设备重启时由 Rust 刷新账号会话并复验设备归属；需要重新输入凭据时明确回到登录页，网络失败与账号拒绝不能混成同一错误；
- 产品账号只授权 automation-tool 服务，不读取、上传或同步运营浏览器 Profile、平台 Cookie、微信数据和原始本机证据。

首个 Demo 账号由认证运维入口创建和重置，不开放匿名自注册。组织、租户、RBAC、套餐、计费与跨设备业务数据同步仍在本阶段之外，不能复制旧项目账号服务提前引入。

U9-04 的正式实现由 `features/account-session/`、`platform/tauri/account-session-gateway.ts`、Rust `account_session_vault.rs` 和 `lib.rs` 五个固定 Command 组成。账号 vault 复用 App 私有 secret store，使用单个 `product-account-session-v1` 记录、严格 token/UUID/login/UTC 校验、原子替换和幂等删除；损坏记录删除后回到未登录。refresh、注销、改密或恢复的网络结果不确定不会被自动当成成功，工作台保持 fail closed。账号设备归属仍由 U9-05 实现，U9-04 不伪造 Installation 已绑定。

## 6. Local Executor 桥接

React 不直接连接 Local Executor。链路固定为：

```text
React
  → PlatformAdapter / Tauri Command
  → Rust LocalExecutorManager
  → 受认证 stdio/IPC
  → Python Local Executor
```

Rust 负责：

- 启动已签名、版本匹配的 Executor；
- 通过 stdin bootstrap 传入一次性会话令牌、受控端点/身份和 App 私有 Executor 状态目录；
- 监管 stdout/stderr、健康、超时、崩溃和重启预算；
- 限界保存脱敏诊断；
- App 退出、注销、任务取消和紧停时清理完整进程树；
- 把本地状态和人工接管信号映射成稳定前端模型。

Executor 连接本机或云端 Control Plane 时使用独立设备通道；React 不持有该通道凭据。

E4-05 已实现 `executor_package.rs` 原生 verifier。信任输入只允许来自 Rust 装配层：32 字节 Ed25519 公钥、非通配的 `semver::VersionReq` 和可选已安装版本；React、Tauri Command、Control Plane、argv、运行时环境变量和远程 URL 都没有设置公钥、版本策略或包路径的接口。当前模块不下载、不安装、不启动进程，E4-07 才从 App 自有 resource/app-data 边界装配固定路径和受信公钥。

验证顺序固定为：拒绝根/祖先 symlink → 有界稳定读取 Manifest/签名 → `verify_strict` 验签 → exact-field 反序列化并逐字节重建 canonical JSON → 绑定 manifest 版本、build ID、当前 OS/架构和平台精确入口 → 执行 App 允许范围与已安装版本防降级 → 拒绝目录 symlink/非普通文件并取得排序后的完整 payload 集合 → 以安全打开的文件句柄逐项复算大小/SHA-256 和目录摘要 → 再枚举一次目录确认验证窗口内没有成员增删。打开文件在读前、读后及按路径重开时核对平台稳定 identity；Unix 使用 `O_NOFOLLOW + dev/inode`，Windows 实现使用 reparse-point 打开约束和 volume/file index。失败只返回固定错误码和 `executor package is rejected`，不反射路径、Manifest 或签名。由于进程尚未接入，验证返回值仅是 Rust 内部的版本、build、入口和资源统计，不暴露给 WebView。

E4-06 已实现 `executor_bootstrap.rs` 本机认证原语。它把每次启动的 32 字节本机会话与 Control Plane `executor.connect` Session 分成两个字段，只通过受限 stdin JSON 写入；本机会话的 Rust 内存 Drop 清零，Python 认证器在退出时清零，所有错误与 Debug 输出固定脱敏。Python stdout 不回传令牌，只返回按事件/协议域隔离的 `atlep1` HMAC 证明；Rust 使用常量时间 MAC 校验。该模块本身没有进程、页面、Tauri Command 或 IPC。

E4-07 已实现 `executor_manager.rs` 固定生命周期。Manager 的受信装配项只有包根、E4-05 verifier 和 60 秒内的 start/stop timeout；每次 start 都先复验完整目录，再从 Manifest 精确入口无参数 spawn，唯一 stdin 写入 E4-06 bootstrap 后关闭。stdout reader 只接受 4096 字节内的严格 healthy/stopped JSON+LF 并验证事件 proof；stderr 由 E4-10 的独立限界脱敏模块处理。一个 Mutex 使 start/status/stop 线性化，8 路并发 start 只产生一个子进程，超时/坏证明/Drop 都强制回收直接子进程。E4-13 已从固定 Tauri Command/PlatformAdapter 装配，E4-14 已完成 macOS 隐藏 App 生命周期纵向验收。macOS 与 Windows 均已从公开 Rust Manager 原入口和真实 App 页面两条原入口跑通 signed PyInstaller Executor→Uvicorn→Heartbeat→停止；Windows 另完成 Job Object 整树清理与生命周期复验。

E4-08 的监管仍在同一个 Manager：调用方必须显式提供最大重启次数、monitor interval 和 restart delay，模块再以 8 次/60 秒硬上限约束；MVP 预算为 2。唯一 supervisor thread 通过 channel 唤醒和有界轮询观察 Child，Mutex 内状态机为 running/restarting/stopped。Unix 只对 signal crash、Windows 只计划对负 NT 异常码重启；正常/固定失败退出、显式 stop、坏包或启动认证失败直接 stopped。恢复前重新执行完整包验证并生成新的本机会话，公开状态只增加 `restartCount`。显式 stop 会先从状态机移除 running/pending，Drop 先关闭/join supervisor，因此不会与后台线程形成复活竞态；E4-09 已把直接 Child 清理扩展成完整进程树。

E4-09 把完整进程树所有权收回同一个 `RunningExecutor`，不引入第二 Manager。Unix `CommandExt::process_group(0)` 在 exec 前创建独立 PGID，强制清理只向该负 PGID 发 `SIGKILL`，`ESRCH` 作为已清理处理；正常 stop 仍只向主进程发 `SIGTERM` 以取得认证 stopped proof，但主进程退出后必须再次终止组内剩余后代再 join reader。Windows 进程以 `CREATE_SUSPENDED` 启动，在任何业务代码运行前配置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`、挂入 Job Object 并恢复初始线程；配置、挂载或恢复失败均关闭 Job/终止 suspended child。启动/停止超时、显式 stop、异常退出准备重启和 Manager Drop 都走同一树清理原语。macOS 与 Windows x86_64 均已用真实签名进程及后代进程验证正常退出、超时、崩溃恢复、挂起强停和整树清理。当前仍无 task invoke API，因此 E4-09 的“挂起”指进程生命周期挂起；任务副作用超时与 `OUTCOME_UNCERTAIN` 归 E4-12/后续 RPA 执行层。

E4-10 将所有代次共享的 `ExecutorDiagnostics` 放在 Manager Core，重启不会创建第二日志源。stderr reader 用 `BufRead::fill_buf/consume` 在输入阶段限制捕获，不会因未换行恶意输出无界分配；超长和非法 UTF-8 行只产生固定 `[TRUNCATED]`/`[REDACTED]`。其余行先清除控制/Bidi 字符，再按三端共享 fixtures 依次移除认证 Header、Bearer、设备/本机会话 envelope、64 位本机令牌、平台 Cookie、敏感 JSON/assignment、URL userinfo/全部 query、file/data URL 和私有路径；最终以 200 行、单行 4096 bytes、总计 64 KiB 三重上限滚动淘汰。公开 `diagnostics()` 只克隆安全内存；E4-13 的固定 PlatformAdapter 再次校验行数、字节和控制/Bidi 边界后展示。macOS 与 Windows 真实 signed 子进程 stderr 均已通过限界、脱敏和跨重启复验。

E4-11 仍不新增第二 Manager 或账本 WebView API。`ExecutorLaunchConfiguration` 持有经过绝对路径/长度/组件校验的 `state_directory: PathBuf`；E4-13 已从 Tauri 自身解析的 `app_data_dir` 派生固定 `local-executor/state`，React 不能提交路径。Rust 只把它放入受限 stdin bootstrap，Python CLI 在任何网络连接前完成 `executor-ledger.sqlite3` v1 迁移和 Installation/Executor 身份绑定。该数据库属于 Executor 的本机恢复边界，不是 Control Plane 副本：只保存正式协议命令身份/意图指纹、Attempt checkpoint 和 outbox，不保存 Session、Cookie、密钥、浏览器 Profile 或任意 App 配置，也不调用系统钥匙串。

H8-09 同样不增加 WebView 路径参数、任意文件 Tauri Command 或第二个本机存储根。Python `LocalArtifactStore` 只接收上述 bootstrap 已固定的 `local-executor/state` 和代码内可信 Policy，公开引用只有 UUIDv4、SHA-256、媒体类型、大小与受控相对路径；页面漂移生产调用方已经按 ID 完成解析、枚举和校验读取。当前没有用户可调用的 App/API，因此原调用方验收从正式 Executor command processor 与无头 BrowserRuntime 进入；后续 Tauri 展示只能新增 Artifact ID allowlist 边界，不能把绝对/相对路径或任意媒体类型交给 React。

H8-10 只给既有“设置与诊断”页增加成功任务诊断开关，不提供截图浏览或路径输入。React 经 `get_browser_diagnostic_settings`/`set_capture_successful_diagnostics` 两个固定 Command 读写 exact boolean；Rust 使用 AppData 私有原子存储保存 `local-executor/browser-diagnostic-settings-v1`（POSIX `0600`，不用系统钥匙串），下一次启动 Executor 时把值写入严格 stdin bootstrap。失败任务始终采集，成功任务默认关闭；WebView 不能提交 state 目录、Artifact ID、URL、Profile、媒体类型或截图选项。隐藏 `visible=false` App 已从真实页面开启设置并启动 signed Executor，runner 复核持久值、权限和 bootstrap 兼容。

H8-11 不新增日志页面或任意诊断输入。Rust Manager 仍是 Executor stderr 到 WebView 前的独立信任边界，按公共 fixture v2 清除凭据、Header/Cookie、完整 URL、页面/消息内容、异常原文和私有路径，并执行 200 行、单行 4096 bytes、总计 64 KiB 的内存上限；现有 `get_executor_diagnostics` 只返回这一安全快照。唯一隐藏 App 已从正式页面/Command 读取 hostile fixture 并拒绝每个精确私密值，测试准备 Command 仅在 `control-plane-e2e` 特性存在。例行 Playwright 已在全局配置显式固定无头，跨目录 Node 门禁同时拒绝常规 Python 浏览器用例漏传 `headless=True`。

E4-12 没有给 WebView 增加通用命令通道：Rust Manager 仍只监管正式 Python Executor，由 Python 在 Control Plane WebSocket 内消费 `task.offer` 并从同一 SQLite 精确重放 ACK/Event。macOS 已从公开 Manager 原入口两次启动 signed PyInstaller 产物验证同一状态目录恢复；React 不读取账本、不提交路径，也不能直接调用 Executor。

E4-13 新增唯一 `executor_platform.rs` 组合根。Tauri setup 只从 `app.path().app_data_dir()` 派生 `local-executor/package`、`local-executor/state` 和 `executor-id-v1`；稳定 Executor UUIDv4 使用既有 App 私有原子存储，Unix 目录/文件为 `0700/0600`。重启时 Rust 依次换取 `app.control-plane` Session、校验当前 Installation，再换取独立 `executor.connect` Session 并启动 Manager；React 只能调用四个无参数 Command，不能传 URL、Session、路径、包根或身份。`emergency_stop_executor` 是本机完整进程树硬停止，与 T3-16/T3-18 的业务 Task 协作式紧停严格分离。E4-14 已从唯一 `visible=false` App 的诊断页面完成 WebView→IPC→Control Plane→signed Executor→退出清理的 macOS 生产同路径验收。

E4-14 专用配置只在 `control-plane-e2e` 编译中允许 runner 注入规范动态 loopback origin，并只在该特性注册真实 OS crash/hang 与正常退出验收 Command；默认和 `desktop-e2e` 构建没有这些入口。App 组合根保留 30 秒有界启动预算；Executor 在 Hello、持久 outbox/控制队列和连接闸门完成后立即发送首条健康心跳，持续入站帧也不能推迟该证明。生产 Tauri event loop 在 `RunEvent::ExitRequested` 或 `RunEvent::Exit` 上显式调用唯一 Platform service 停止 Executor，Manager Drop 仍是兜底，不能依赖测试驱动杀进程触发析构。验收核对 App 私有稳定 UUID、SQLite identity/版本迁移、Unix 权限和凭据不入库；所有服务、进程、端口与数据均按本次专属标识清理。

B5-13 在同一组合根增加平台状态纵向链路。`get_douyin_platform_session` 由 Rust 自行换 `app.control-plane` Session 并调用固定 `/api/v1/platform-sessions/douyin`；`open_douyin_login`/`recheck_douyin_login` 在 Executor 停止时先按 E4-13 原路径自动启动，再从 `BrowserSettingsService` 重新发现受信浏览器、从 `BrowserProfileStore.current_douyin_profile()` 取得稳定 App 私有 Profile 并持有 owned lease。Manager 在现有 stdin/stdout 上发送/验签动作，不创建第二 Executor 或第二浏览器 Manager；生产 headed，只有 B5-13 专用 `visible=false`、唯一标识的 `control-plane-e2e` 构建硬编码 headless。真实验收从页面点击出发并在 App 正常退出后确认 signed Executor、Chrome、Profile 锁和隔离服务全部清理。

B5-14 在同一 Gateway 增加无参数 `logout_douyin_session`。页面使用明确二次确认且 pending 时禁用重复提交；Rust 先调用服务端 logout prepare 持久阻断，再通过唯一 Manager 紧停 Executor/浏览器树并释放 lease，随后由 `BrowserProfileStore` 删除 current Douyin Profile。删除只在稳定平台目录句柄下完成，原目录先原子改名为唯一 tombstone、复验同一目录 identity 后删除；重试可续删 tombstone，symlink/reparse、双目录、活跃锁或 identity 漂移一律拒绝。之后 Rust 重启 signed Executor，发送不含任何路径/headless 字段的 `douyin.logout.complete`，并只把重新查询到的服务端 `missing` 投影返回页面。

B5-15 用同一个生产 Gateway/Command 证明 App、Executor 与浏览器生命周期可以全部重建而不更换 Profile。已有健康 Profile 作为本机首个事实时建立 revision 1，不再因 `recovered=true` 被错误拒绝；第二次健康启动才递增到 revision 2。验收配置固定 `visible=false` 且 BrowserRuntime 固定 headless，四轮之间只允许 App/Executor/context 退出重建，current marker 与 Profile device/inode 必须不变；过期页面进入扫码，风险页面进入人工接管。确定性页面属于单独签名的测试 Executor，不进入正式 package spec 或生产配置，真实账号双重启证据仍单独标记待补。

B5-16 继续复用生产 `BrowserProfileStore.current_douyin_profile()`→owned lease→本机认证命令→Python `launch_persistent_context(request.profile_directory)`，没有第二个 Profile 解析入口。专用隐藏 App 在扫码状态保持无头 Chrome 活跃，WDIO 只通过临时 ready/release 文件协调外层审计；外层读取 OS 进程参数和 `lsof`，要求唯一 `--user-data-dir` 与实际打开文件都只落在 App 私有 current Profile，并拒绝 Chrome/Edge 默认 User Data。生产 Rust/Python/TypeScript 源码另由递归契约拒绝默认路径、Cookie 和 storage-state API；Profile 目录与 UUID不进入 WebView、日志或验收输出。

D6-10 在既有 Rust `ControlPlaneClient` 增加唯一 `StartTaskDiscovery` operation，并在 Tauri 注册固定 `start_task_discovery(task_id,idempotency_key)` Command。Rust 自行从 App 私有 vault 换取 `app.control-plane` Session，只向固定 `/api/v1/tasks/{task_id}/discoveries` 发 POST，严格解析 task/command/attempt/status/revision/watermark/UTC deadline；WebView 不能提交关键词、Candidate、Cookie、浏览器/Profile 路径或任意 URL。D6-10 的 `control-plane-e2e` 专用 Command 只用于 hidden App 纵向验收，不进入默认构建。

D6-11 继续复用同一个 `ControlPlaneClient`，以 `GetTaskTargetPreview`、`ReplaceTaskTargetExclusions`、`ConfirmTaskTargetPreview` 三个封闭 operation 和同名固定 Tauri Command 调用 task-scoped API。游标、page revision、task revision、Target UUIDv4、排除集合与幂等键均在 TypeScript、Rust 和后端逐层复验；Rust 只返回公开摘要、封闭来源/策略原因、选择状态、计数和确认时间，拒绝未知字段、乱序、跨 Task、无确认的后确认状态或确认后的预确认状态。`task-target-preview-source.ts` 是正式 PlatformAdapter source；测试配置固定 `visible=false`，生产配置和默认构建没有验收入口。

D6-12 把该正式 source 注入 `App → WorkbenchShell → TaskRunDetails`，没有新增 HTTP 或通用 IPC。`TaskTargetPreviewPanel` 只在当前 Task 等待确认或已收到确认事实时加载最多 100 个目标，展示最小摘要、固定来源、计划执行/用户排除/策略拦截计数，以及 `eligible/本任务重复/30 天内已触达/黑名单` 封闭标记；Target UUID 只作为受控 Mutation 参数，不渲染。单选、全部取消和恢复全部都用完整排除集合、当前 page/task revision 与同意图稳定幂等键调用正式 source；过期 revision 自动回拉，未知错误不显示底层文本，空选择禁止确认。确认使用明确二次确认，成功后回拉任务快照/列表并等待权威事件。`scripts/run_d6_12_acceptance.py` 由独立 `visible=false` App 在真实页面取消第二个目标并确认，经正式 TypeScript source、IPC、Rust、Uvicorn/PostgreSQL 验证最终 `queued`、selection revision 和连续事件；准备命令只注册/发现测试 Task，不代替三次用户页面 API 调用。

E4-15 把测试隔离从源码约束扩展到实际 release 字节。`build.rs` 在 `PROFILE=release` 时先验证编译期 `AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY` 是 canonical 32 字节、有效且非弱 Ed25519 公钥，失败发生在 `tauri_build::build()` 之前且错误不回显输入；公开开发 fixture key 只存在于 debug 分支。生产二进制审计同时读取无默认特性的 Cargo 依赖树、正式 Tauri 配置和 Vite 资产，拒绝 WDIO/WebDriver、验收 Command、测试 origin/标识/Sidecar、Harness、开发验证公钥和 1420 调试端口，并要求实际制品包含预期发布公钥。

真实 release 审计最初发现 `tauri.conf.json` 的 `devUrl`/devCSP 即使 release 不使用仍会进入二进制，因此现已拆到只由 `pnpm tauri:dev` 显式合并的 `tauri.dev.conf.json`。自动化配置继续只用于各自 `--config` 测试构建；正式配置保持唯一可见主窗口、`withGlobalTauri=false`、唯一 `main` Capability 与生产 CSP。E4-15 临时 release target 每次唯一且结束删除，不启动 App、不绑定端口。

### 6.1 AV-01 内置浏览器前端基线

ADR-0001 已替代外部 Chrome/Edge 生产方案。Tauri/Rust 后续只从安装包 Resources 验证并启动与 Playwright 锁定版本匹配的 Chromium；React、普通设置、Control Plane 和任务参数都不能提供浏览器种类或可执行路径。

- 启动门禁与诊断 UI 只展示 `浏览器组件正常`、`浏览器组件损坏`、`浏览器组件版本不兼容` 三类封闭结果及安全修复动作，不显示本机浏览器列表、安装路径、文件选择器或运行时下载按钮。
- 首次使用直接创建全新 App 私有运营 Profile，不提供旧 Profile、旧浏览器选择或 Cookie 迁移入口。可见运营窗口继续支持扫码、验证码/风控暂停和人工接管。
- 正常用户路径验收必须从正式 App 页面启动可见窗口并核对真实浏览器/平台结果；UI Harness、Mock Gateway、直接 invoke 和测试专用页面不能替代。
- 现有 B5 浏览器发现、选择与系统浏览器验收段落保留为已实现历史和迁移输入；EB 系列完成前它们仍描述当前代码，不再定义目标生产架构，也不得成为 fallback。

### 6.2 AV-03 用户品牌与不可信视频内容

- 视频制作页面只显示“智能素材成片”和“品牌动效成片”，消费稳定内部 ID；上游项目名、CLI、原始错误和进程信息不能进入 React DTO、标题、菜单、按钮、加载、错误、无障碍文本、任务或导出。
- 按 ADR-0002，“视频剪辑”是独立左侧菜单入口和独立模块，不并入“视频制作”页面；剪辑页面只消费供应商无关的内部 DTO，阿里云等供应商名称、任务 ID 和原始错误不进入 React。剪辑入口与页面由 VE-03 交付，交付前不得添加空壳菜单。
- `contracts/quality/user-facing-terminology.v1.json` 是中文展示与通俗术语契约。`plainLanguageMappings` 是术语中文说法的**唯一事实源**；`unexplainedIndustryTerms` 只声明哪些术语被强制执行以及执行范围，中文说法一律回查 `plainLanguageMappings`，查不到即 fail closed，不允许出现第二份术语表。契约同时声明两张制作方式卡片必答项和四组概念区分文案。
- `scripts/check_user_facing_branding.py` 扫描正式 UI 源码、Tauri 标题、JSX 文本节点和`aria-label`/`alt`/`title`/`placeholder` 无障碍名称，对渲染文案中的行业词按 `segment`（同句必须给出中文说法）或 `file`（同一源文件内任意位置给出即可）两种范围 fail closed，并校验 134 个动效零件名称旁始终带中文说明。**已知偏差**：`file` 范围以“同一源文件”近似“同一页面”，一个页面由多个组件文件拼成时可能误报，同一文件被多个 Tab 复用时可能漏报；字段因此命名为 `file` 而不是 `page`。独立第三方软件声明页是唯一名称白名单，但不是功能入口。
- 该扫描只是回归门禁；普通用户可理解性的交付证据是 `scripts/run_cq_01_acceptance.py`，它构建隐藏测试 App、按左侧菜单真实页面路径断言用户可见文本，并把每个页面的渲染文本与无障碍名称回灌给同一个 Python 匹配函数判定，行业词规则只有一份实现。
- React 不渲染生成 HTML，也不能直连本机视频 Worker；只通过固定 Gateway 查看脱敏状态、预览和 Artifact。HTML 预览由隔离渲染面生成像素或受控媒体结果。
- 外部模型调用前页面必须说明会离开本机的数据范围；任何密钥、绝对路径、运营 Profile、Cookie、原始 Worker 错误和上游名称都不能进入 WebView。
- 静态扫描和 UI Harness 只能作为分层证据；用户功能仍要从正式 App 正常入口覆盖成功、失败、取消、人工接管、诊断和导出。

### 6.3 VF-02 本地视频 Worker 生命周期

`LocalVideoOrchestrator` 位于 Tauri Rust 边界，分别按 `Python`、`Node` 槽位线性管理两类
视频 Worker；React、Control Plane 和 Worker 都不能自行发现、启动或复用其他进程。
Worker 从标准输入一次性接收新生成的 256-bit 会话令牌，在自身绑定
`127.0.0.1:0` 后返回端口、协议、精确版本和 HMAC 证明。Tauri 随后用同一会话在真实
TCP `/health` 上复验，不把端口、令牌、可执行路径或原始错误暴露给 WebView。

两个 Worker 与 Local Executor 复用 `ManagedProcessTree`：Unix 使用独立进程组，Windows
使用 kill-on-close Job Object。启动失败、证明/版本不匹配、超时、取消、异常退出、恢复
预算耗尽、显式停止和 App 释放都由该所有者清理完整进程树；崩溃恢复先杀后代再等待日志
管道，避免渲染子进程继承句柄后卡死。VF-02 只提供内部生命周期，不注册用户 Command；
后续 IM/BM Adapter 才提供受限业务调用，VF-06 才增加正常用户入口。

### 6.4 VF-03 RenderJob 私有工作区

Tauri 正式组合根持有唯一 `VideoJobWorkspaceStore`，存储根固定在 App 私有数据目录下，
每个 UUIDv4 RenderJob 分配独立 `outputs/checkpoints/work` 目录和稳定目录 identity。只有
受信 Worker Adapter 能取得重新校验后的输出目录；React、Tauri Command、Control Plane、
日志、错误和 Artifact DTO 均不包含绝对路径。

Worker 输出导入时先拒绝越界名称、symlink/reparse、identity 替换、单文件/单任务配额和
剩余空间不足，再以固定大小缓冲区流式复制及计算 SHA-256。payload 与无路径 manifest
先写入私有临时目录并 fsync，最后以一次目录 rename 原子发布；启动时只清理符合本协议
命名和结构的中断临时目录。成片读取使用带剩余字节上限的流式 Reader，不把最高 32 GiB
Artifact 整体载入内存。

checkpoint 使用同目录临时文件、fsync 和原子替换，可在 App 重启后按 Job/名称恢复；
`Keep` 写入保留截止时间，清理只删除已到期且整棵目录复验无链接的工作区，`Delete` 明确
删除工作区。已经原子导入的 Artifact 独立存续，只能按稳定 Artifact ID 显式删除。

### 6.5 VF-04 统一视频媒体工具

Tauri 侧 `VideoMediaToolchain` 只解析 `resource_dir/media-toolchain` 的锁定发行物，逐文件
校验摘要、目标、版本、许可、路径和可执行权限；系统 PATH、用户选择路径和运行时下载都
不是生产来源。同一个已验证 FFmpeg/ffprobe 对通过环境变量分别交给 Python 与 Node Worker；
路径不序列化、不写普通日志，也没有 WebView command。

FFmpeg 8.1.2 与 x264 锁定源码、双平台原生构建、能力矩阵、GPL 对应源码和真实编码烟测
详见 `video-media-toolchain-supply-chain.md`。

### 6.6 内置浏览器、Browser Use 与页面租约

Tauri/Rust 是内置 Chromium 发行物、运营浏览器进程和私有 Profile 的唯一所有者。
`operations` 使用持久 Profile；页面分析等一次性受控执行使用独立的 temporary Profile，
视频逐帧渲染再使用自己的无登录进程。三类进程不能共享 Profile、Context 或启动参数，
React 只消费封闭状态和业务结果，不能取得浏览器路径、Profile 路径、Cookie、CDP 地址或
页面原始内容。

同一运营页面需要在确定性 Playwright 控制与 Browser Use 能力之间交接时，必须经过
`BrowserSurfaceLease`。租约管理器先暂停当前所有者，再签发随机 loopback CDP 接管能力；
接管者断开并释放后才能恢复原所有者。接管失败、过期或连接状态不确定时进入
`reclaim_required`，双方都不能继续操作，只有资源所有者完成进程级回收后才能重新开放
页面。现有发布预检仍由受控 Playwright 页面对象执行；Browser Use 的模型执行能力不得
绕过相同的内容脱敏、一次性高风险确认和页面租约边界。

### 6.7 两种视频制作链路

视频制作页只通过 `TauriMaterialVideoStudioGateway` 提交“智能素材成片”和“品牌动效
成片”，不直连 localhost Worker，也不传递本机路径或密钥。两条链路都由 Tauri/Rust 的
`LocalVideoOrchestrator` 启动并回收受管 Worker，为每次任务创建 UUIDv4 `RenderJob`
私有工作区；完成后只把通过摘要、配额、链接与目录身份复验的结果导入为无路径
`Artifact`，React 仅按稳定 ID 查询进度、预览和导出。

- 智能素材成片由内嵌素材制作 Worker 完成素材理解、脚本和时间轴到本机成片的闭环；
- 品牌动效成片由受控编排、语音合成、Node 动效渲染和 FFmpeg/ffprobe 组合完成，旁白实际
  时长可以拉长画面时间轴；
- 两条链路共享同一套已校验 Chromium 与媒体工具发行物，但 Worker 进程、会话、任务目录
  和 checkpoint 相互隔离，失败、取消、崩溃恢复与 App 退出都由各自受管进程树清理。

### 6.8 独立剪辑设备工作区

T4 第一片把正式 `videoEditingGateway` 从 WebView `sessionStorage` 替身替换为
`TauriVideoEditingGateway`。React 只消费 VE-03 的 provider 中性 DTO，项目、时间线与任务
列表通过六条固定 Command 进入 Rust；返回值在 IPC 两侧都按严格字段和封闭枚举复验，未知
错误不会把路径、凭据或上游响应带回 WebView。

设备侧 `VideoEditingWorkspace` 固定落在 App 私有数据目录，状态写入先生成私有临时文件、
fsync，再原子替换并同步目录；重开 Store 后复验全部项目、Timeline 修订和任务结果形状。
这一层只解决设备持久化与 UI 装配，不冒充云执行：Provider 调度尚未接通时提交明确返回
`editing_service_unavailable`，不得预造 `queued` 作业。后续装配仍遵循 ADR-0002 的单向
依赖：剪辑页面 → Control Plane 剪辑应用层 → Provider Adapter。

B5-01 已冻结原外部浏览器会话的历史迁移边界。当前 Profile 只能从 Tauri `app_data_dir/browser-profiles/douyin/<canonical UUIDv4 profile_id>` 派生，不能由 React、服务端、平台账号文本或任意路径输入决定；B5-05 负责私有权限、symlink/reparse point 与稳定 identity，B5-06/B5-07 负责跨进程单实例锁和真实 headed 浏览器资源所有权。登录健康只由真实页面检测产生 `missing/healthy/expired/risk/unknown`，只有 `healthy` 关闭熔断；等待扫码/确认和人工接管是本地平台工作流，不是 automation-tool 产品登录。

旧 `SocialOperationsRuntime`、进程内账号表、`EncryptedCookieVault`、`.cookie-key`、`SOC1`、tenant/RBAC/Entitlement 全部不迁移。浏览器持久 Profile 是 Cookie/站点数据的唯一来源，React、Tauri IPC、Executor 账本和 Control Plane 都没有 Cookie 导入导出接口。B5-14 注销必须先持久熔断并阻止新任务，安全停止关联动作、关闭浏览器并释放 Profile 锁，最后才定向删除目标目录和递增 `session_revision`；停止失败或最终副作用不确定时保留 Profile 并进入可诊断/`OUTCOME_UNCERTAIN` 状态。

B5-02 新增 Rust `browser_discovery.rs`，但暂不增加 Tauri Command。macOS 固定扫描 `/Applications` 下正式 Chrome/Edge，以 Security.framework 验证所有架构、嵌套代码和精确 vendor designated requirement，并固定 Bundle signing identifier、Developer Team 与 `Contents/MacOS` 主入口；不执行 `codesign` 子进程、不解析 Info.plist 后自行猜测可信度。返回对象保存 App/入口 dev+inode，`revalidate_macos_browser` 在后续启动前要求标准路径未变并重新验签。React、服务端和用户设置只能在 B5-04 选择受支持枚举，永远不能提交可执行路径。

B5-04 将该信任根收口到两个无路径 Tauri Command：`get_browser_settings` 返回固定浏览器枚举和当前选择，`select_browser` 只接受 `google_chrome` / `microsoft_edge`，并在写入前重新执行当前平台真实发现；路径、签名 requirement、证书、identity 和 AppData 根均不序列化到 WebView。`BrowserSettingsService` 只在 Tauri setup 中从 `app.path().app_data_dir()` 构造，选择以 canonical v1 JSON 原子写入 `settings/browser-selection-v1`，缺失、损坏、非 canonical、已卸载或未受信浏览器均 fail closed，绝不回退到用户提供路径。设置页只渲染 Rust 返回的可用枚举，没有文本框或文件选择器。

上一段是 EB-10（`f34e503`，2026-07-24）之前的状态。内置 Chromium 基线要求删除系统浏览器选择，设置页那张卡片随之消失，`scripts/run_b5_04_acceptance.py` 与它的 `wdio.browser-settings.conf.ts`、`browser-settings.spec.ts`、`tauri.browser-settings-e2e.conf.json`、`test-browser-settings-main.tsx` 已在 T57b 一起删除——两次真实桌面全量实跑（07-26）都停在同一处「`.browser-settings-card` not displayed」，那不是文案过期而是被测路径已不存在。现在由 `frontend/tests/browser-settings-boundary.test.mjs` 断言前端不得再出现 `get_browser_settings`/`select_browser`，由 `e2e-tauri/startup-environment.spec.ts` 断言该卡片不再渲染；Rust 模块自身的退役属 ADR-0001/EB 系列。

B5-05 新增纯 Rust `BrowserProfileStore`，由 Tauri setup 从自身 `app.path().app_data_dir()` 管理唯一实例，只固定派生 `browser-profiles/douyin/<canonical UUIDv4>`，Profile ID 由本机 CSPRNG 生成；当前没有 Tauri Command、React DTO、Control Plane 接口或其他平台目录。Unix 逐级使用目录句柄、`openat(O_NOFOLLOW)`、`mkdirat` 和 dev+inode，Windows 固定子目录使用父 HANDLE 相对 `NtCreateFile(FILE_OPEN_REPARSE_POINT)`、volume/file index、最终路径和当前用户 protected DACL。Store/Profile 持有打开的目录身份并在创建、重开与交给后续浏览器前复验；路径被 symlink/reparse、普通文件或 rename 后同名替换时 fail closed。B5-06 只能在该身份上增加跨进程锁，B5-07 才能在持锁后把私有目录交给系统浏览器。

B5-06 已在同一稳定 Profile identity 上加入原生跨进程非阻塞排他锁和崩溃标记：同 Profile 竞争拒绝，不同 Profile 可并行，只有显式释放清除标记，意外退出要求后续恢复流程。B5-07 证明正式 PyInstaller onedir 可携带 Python Playwright driver 而不携带浏览器。B5-08 已在 Python Executor 建立单 context、线程约束、页面/窗口、有界超时和确定关闭接口；macOS 冻结验收从 Rust 受信浏览器复验、私有 Profile 与锁链路完成系统 Chrome 双窗口正常关闭，并按生产 Manager 相同 process-group 语义强杀完整后代树。Windows 继续复用 Executor Job Object、待原生 runner 验收；冻结探针仅用于验收，不是 App Command 或用户功能。B5-09 已在 Python 平台层固定官方 `/user/self` 探测入口和 `healthy/expired/missing/risk/unknown` 页面证据，只有 `healthy` 解除熔断；B5-10 用专用 headed 窗口、异步就绪上限和无参数 `recheck()` 封闭扫码/手机确认/二维码过期状态，B5-11 再把所有外层验证挑战投影为 `handoff_required`。B5-12 由同一 detector 生成最小 `platform.session_health` 并经正式 Executor WebSocket 投影到 Control Plane；B5-13 已让用户从真实平台状态页查询该投影，并通过同一 Executor 打开处理和无参数重新检查；B5-14 已把注销做成服务端持久门闩、唯一 Manager 停机、稳定目录定向删除与 path-free missing 上报的失败关闭链路；B5-15 已从同一页面原入口完成四轮隐藏 App/Executor/Chrome 重建、稳定 Profile identity、过期扫码和风控接管工程验收；B5-16 再用活跃进程树和打开文件证明没有读取默认 Chrome/Edge Profile。React 仍不能接触 Cookie、Profile、路径或页面事实，动作结果不能覆盖服务端快照；真实空白二维码、实际扫码到健康态和生产 flow 的持久 Profile 重开复用已通过，但真实账号的完整 App 双重启仍待补。验证码、滑块和风控窗口只留给用户处理。

## 7. 页面与导航

MVP 导航：

```text
自动化运营工具
├── RPA 运营
│   ├── 工作台
│   ├── 新建任务
│   └── 任务记录
├── 平台状态
└── 设置与诊断
```

不出现：

- 登录、注册、个人中心；
- 企业、成员、角色和套餐；
- Web 管理端；
- 未实现的小红书、微信、内容中心或 AI 空菜单。

## 8. 状态管理

### 8.1 TanStack Query

管理 Control Plane 权威状态：

- 任务列表和任务快照；
- 目标预览；
- 事件历史；
- 结果和服务端配置；
- 安装实例的公开状态；
- Control Plane 健康和版本。

### 8.2 Zustand

只管理纯客户端状态：

- 当前面板开合；
- 未提交任务草稿；
- 诊断页展示偏好；
- 当前本机 Executor 瞬时连接投影。

服务端任务、平台会话和结果不得复制到 Zustand 形成第二事实源。

### 8.3 URL 与组件状态

- 任务 ID、筛选、分页和 Tab 进入 URL；
- 单个弹窗、输入焦点等留在组件；
- 表单草稿只有确有跨页面恢复需要时才进入 Zustand；
- 不把 Cookie、设备凭据或平台登录态放入 URL、Store 或浏览器存储。

## 9. 任务事件投影

事件模型至少包含：

```text
task.created
task.validation_started
task.validation_failed
task.awaiting_platform_login
task.awaiting_confirmation
task.started
step.started
step.progress
step.completed
step.failed
task.awaiting_human
task.paused
task.resumed
task.cancelling
task.cancelled
task.completed
task.partially_completed
task.failed
task.outcome_uncertain
```

前端必须：

- 校验事件版本和单调序号；
- 重复事件去重；
- 检测序号缺口并重新拉快照；
- App 恢复或网络重连时先读快照，再续订；
- 任务终态关闭实时连接；
- 未知新事件受控降级，不猜测状态；
- 任何错误文案只展示服务端或 Executor 生成的脱敏安全消息。

T3-04 已把上述消费边界落为后端数据契约：事件版本精确为 `1.0`，19 种类型使用封闭枚举，sequence 限制在 `1..2^53-1` 且以 `(task_id, sequence)` 唯一；Task 快照携带 `last_event_sequence` 水位。安全消息最多 1024 字符，并在进入持久化前拒绝敏感赋值、Bearer、私有绝对路径、file/data URI、控制与双向字符。T3-15 Reducer 已以服务端 status/revision/last sequence 为权威，并按上述未知版本、未知类型、缺口与回退规则实现受控回拉/降级；事件名不会被用来推断状态。

## 10. 错误模型

统一错误至少区分：

```text
control_plane_unavailable
device_unauthorized
executor_unavailable
platform_login_required
human_handoff_required
validation
conflict
rate_limited
timeout
network
protocol_mismatch
outcome_uncertain
server
unknown
```

字段错误、页面局部错误、全局连接故障和高风险接管分别展示，不能所有失败都弹 Toast。

## 11. 安全基线

- Tauri CSP 默认拒绝远程脚本、远程页面和任意连接；
- WebView 不加载抖音、小红书或微信页面；
- 外部平台始终在独立 Chrome/Edge 窗口打开；
- 长期设备凭据和平台会话不进入 React；
- Ed25519 设备私钥和长期设备凭据只进入 Rust 管理的 `app_data_dir` 固定私有文件；损坏或不可用时 fail closed，不回退到普通配置、React 存储或系统钥匙串；
- Markdown、模型输出和服务端文本按不可信内容处理；
- 外链只允许 `https` 且展示目标域名；
- 文件选择通过 Tauri，后端不能请求任意本机路径；
- 紧急停止必须在运行任务的任意页面可达；
- 生产包不包含测试 WebDriver、测试 Adapter、调试端口和源码映射中的敏感信息。

## 12. 性能与可靠性

- 路由和 P2/P3 重型模块按需加载；
- 事件按小批次合并，避免每个进度事件触发全页重渲染；
- 长列表和事件时间线使用分页或虚拟化；
- 请求支持取消，页面离开后不遗留无界轮询；
- App 页面关闭不等于任务停止；任务状态由 Control Plane 和 Executor 协议决定；
- Executor 或网络故障时 UI 明确展示最后可信快照和数据时间；
- Control Plane 版本与 App 支持范围不匹配时 fail closed 并提示升级。

## 13. 测试策略

### 13.1 Vitest

- DTO 转换和 Zod；
- Query Key 与事件 Reducer；
- 任务状态展示；
- `PlatformAdapter` 和 `ControlPlaneTransport` 契约；
- 错误、重连、序号缺口和结果不确定；
- 生产构建不引用 Test Harness。

### 13.2 Playwright UI Harness

覆盖：

- 打开工作台；
- 创建任务；
- 登录状态投影；
- 目标预览与确认；
- 运行、暂停、恢复、取消、接管和结果；
- 后端断开和恢复；
- 不渲染登录页或未实现菜单。

Playwright 使用受控窄 Adapter 驱动真实 React 页面交互；T3-19 的生命周期 Adapter 将测试状态保存在当前 Harness 会话以验证整页恢复。它不能证明 Tauri IPC、Rust、真实网络、运营浏览器或微信可用，代表流程必须再由隐藏真实 App 从产品入口验收。

### 13.3 Rust 测试

- baseUrl Profile 校验；
- 安装实例凭据安全存储；
- macOS/Windows 的真实 App 私有数据目录往返、权限边界、复用、原子替换、损坏拒绝和清理；
- HTTP operation allowlist；
- SSE 到 Tauri Channel 映射；
- Executor 启停、超时、崩溃、重启预算和进程树清理；
- Capability、CSP、文件和外链限制；
- 正式配置不启用测试驱动。

### 13.4 Tauri E2E

- 真实 App 启动和无登录工作台；
- local/demo Profile 连接；
- Rust 网络桥和事件；
- Local Executor 真实子进程；
- 内置 Chromium 启动、可见窗口与人工接管；
- 文件、诊断、紧急停止和错误恢复；
- macOS/Windows 分别冒烟。

当前 F1-13 基线使用 `@wdio/tauri-service 1.2.0` embedded provider：`pnpm test:tauri` 构建带 `desktop-e2e` Cargo 特性的 debug App，并在真实 macOS WKWebView 中验证无登录工作台和 `main` 原生窗口。WDIO Rust/前端插件、`withGlobalTauri=true` 和测试 Capability 只存在于测试配置对应的构建；测试 Capability 以内联对象提供，不能放入 production 默认扫描的 `capabilities/` 目录。正常 Cargo 依赖树不启用两个可选 WDIO crate，生产 Vite 构建扫描测试标记并 fail closed。所有自动化 Tauri 配置（包括 T3-15 Task 投影、T3-16 工作台、T3-19 生命周期、T3-20 重启与 E4-14 Executor 生命周期验收）都把唯一测试主窗口固定为 `visible=false`，自动化 App 只在后台运行且不抢焦点；production `tauri.conf.json` 保持窗口可见。

I2-04 起，`desktop-e2e` 特性在真实 App 进程内生成不持久化的临时 Ed25519 身份，避免通用桌面冒烟污染开发机或 CI 的正式 App 数据。I2-08 另以正式、非 `desktop-e2e` Tauri 入口解析隔离测试标识的 `app_data_dir`，验证私钥文件首次创建、重启复用、权限和无长期凭据初始状态；Rust 测试再覆盖凭据写入、替换、删除及故障矩阵。临时身份不能替代正式 App 私有存储验收。

`pnpm test:layers` 固定按 Vitest/契约、Playwright UI Harness、Rust、WebdriverIO 真实桌面四层执行。通用桌面冒烟证明真实 App、WKWebView、测试 IPC 插件和窗口查询可用；I2-09 验证认证纵向链路，T3-19 验证完整任务交互，T3-20 的 `scripts/run_t3_20_acceptance.py` 再让同一隐藏 App 保持运行，真实停止 Control Plane、整页刷新显示不可用、以同一 PostgreSQL 重启服务并点击“重新检查”，最终从工作台/详情读取 Executor 重连后的取消终态。E4-14 的 `scripts/run_e4_14_acceptance.py` 则从隐藏 App 诊断页驱动正式 signed Executor 启停、崩溃恢复、挂起超时和 App 退出清理。这些证据仍不证明外部运营浏览器或真实平台 RPA，相关最终状态由 Wave 5～7 各自验收。

所有 WDIO 配置统一展开 `wdioRuntimeArtifacts`：除每次运行唯一且随父进程回收的临时输出目录外，Node 26 下还统一移除跨 Undici dispatcher 不兼容的显式 `Content-Length`，交给实际发送方按 body 重新计算。该兼容层本身不改写 body、URL、认证或响应，并进入严格 TypeScript 与 Node 契约；新增验收配置无需再复制请求 workaround。

## 14. 构建和配置

- `local` 构建连接开发机本地 Control Plane；
- `demo` 构建连接指定 HTTPS Demo API；
- baseUrl 和允许域名进入签名构建 Profile；
- Demo bootstrap 授权以限时、限环境方式注入，不写入源码或普通 Vite 环境变量；
- 正式构建只包含 Tauri 入口，不发布 Vite 静态站点；
- 正式配置不含 dev URL/devCSP；release 必须注入经构建脚本验证的 Executor 公钥并通过实际二进制审计；
- Local Executor 随目标平台安装包构建、签名和版本锁定；
- App、Executor 和 Control Plane 建立明确兼容矩阵与最小支持版本。

H8-18～H8-22 的自动更新采用“官方安装底座 + 自有通用策略层”。H8-18 已锁定官方 Rust `tauri-plugin-updater 2.10.1`，由它完成 macOS/Windows 平台识别、feed 检查和安装原语；H8-20 因官方 `Update::download` 会一次性缓冲且不支持 Range，通用缓存层改用同版本依赖树中的 `reqwest` 和官方底层同款 `minisign-verify 0.2.5` 做可恢复流式下载与验签，不复制平台安装器。当前 Tauri 2.11/Rust 1.96 满足其版本要求。只引入 Rust crate，不引入 `@tauri-apps/plugin-updater` JavaScript binding，也不向 `main` Capability 授予 `updater:default`，因此 React 只能消费 `AppUpdateGateway` 的脱敏状态，不能直接检查任意端点、取得下载 URL/签名或触发安装。

更新 feed 固定使用官方 dynamic server 格式：无更新返回 204；有更新返回规范 SemVer、HTTPS `url`、Minisign `signature`、可选 notes/RFC3339 `pub_date`，并在官方 `Update.raw_json` 的 `update_contract` 扩展中携带版本 1、受限 channel、`optional/forced` policy，以及 `darwin/windows`、`aarch64/x86_64`、SHA-256 和 1 GiB 内字节数。Rust 在把任何数据投影给 UI 前再次拒绝未知字段、响应/插件版本不一致、非 HTTPS、非法平台、非法摘要和不安全文本；URL 与签名永不进入 React 状态。官方默认版本比较保持启用，不允许服务端借更新通道静默降级。

第一期发布目标固定为 macOS `aarch64/x86_64` 的签名、notarized App updater archive，以及 Windows `x86_64/aarch64` 的 Authenticode-signed NSIS per-user installer；不同时维护 MSI 更新链。Windows 安装器采用官方推荐的 `passive` 无交互模式以保留可靠进度和必要的系统安装能力。“强制/静默更新”在产品契约中表示用户不能暂缓或跳过、下次启动直接进入安装，不表示关闭安装器安全提示、绕过系统权限或使用不可靠的 `quiet` 提权。

App 可见状态闭集为 `idle/checking/up_to_date/available/downloading/ready/installing/installation_launched/failed`，检查来源闭集为 `startup/periodic/manual`，用户决策闭集为 `install_now/defer/skip_version`，ready action 闭集为 `prompt/deferred/skipped/suppressed/install_requested/forced`。H8-20 已由 Rust `AppUpdateCoordinator` 统一启动、固定 6 小时周期与“检查更新”入口；15 分钟～24 小时常量边界防止错误配置成忙轮询，原子并发门把检查和决策串行，重叠触发只执行一个官方检查。`AppUpdateCache` 只从 Rust 内的 official `Update` 构造下载源，使用 Range/If-Range、强 ETag、精确 Content-Length/Range 和 20 秒请求上限恢复失败下载；流式 SHA-256 与 Minisign 都通过后才把私有 `candidate.partial` 原子替换为唯一 `candidate.package`。缓存始终只保留当前候选，新版本原子替换旧包；该层只依赖版本、平台、签名、发布策略和安装状态，不引用抖音、任务、客户或其他业务概念，以便跨项目复用。

H8-19 已实现 `UpdatePolicyService`，并在正式 Tauri setup 中以当前 `package_info().version`、固定 stable channel 和当前 AppData 初始化唯一实例。私有 `app-updates/update-policy-v1` 使用规范 JSON schema v1，只保存配置 channel、不会下降的已安装版本下限、最高已观察发布的 `version/channel/policy/target/arch/sha256/sizeBytes` identity、最后一次可选决策和单调 revision；不保存 URL、Minisign signature、notes、本机路径或任何业务数据。存储复用 App 私有原子替换，Unix 固定 `0700/0600`，损坏、未来 schema、symlink、宽权限或写入失败均不推进内存状态。

策略转换固定如下：新的更高版本清除旧暂缓/跳过；`defer` 仅关闭本次提示，下一次启动、周期或手动检查重新观察同一发布时再次提示；`skip_version` 只压制 identity 不变的当前版本；`install_now` 作为待安装意图跨重启保留，直到实际 App 版本达到候选才清除。强制发布不接受任何用户决策；低于版本下限/最高已见版本的发布、同版本换策略/摘要/目标、一次提示上的第二次点击，以及被压制或已请求安装状态下的过期决策均拒绝。H8-20/H8-21 只能消费该策略结果，不能在 scheduler、React 或安装器中复制另一套判断。

H8-21 的安装交接位于 Rust `AppUpdateInstallationCoordinator`。`install_now` 只在当前状态为 `ready/prompt` 时接受；强更或先前已持久化的 `install_requested` 只有在 startup 检查发现 identity 精确匹配的既有缓存时自动安装，因此同一次启动刚下载的强更不会提前执行。安装前从私有 package 重新读取并校验长度、SHA-256 和当前 official response 的 Minisign；随后隐藏窗口、通过 `shutdown_for_app_exit` 停止完整 Executor 树并释放 Profile，最后把内存 bytes 交给 official `Update::install`。Windows 官方路径启动安装器后退出进程；macOS 官方路径替换 App 后由 Tauri restart。预检失败不会停止运行环境；停止或安装失败会恢复窗口并投影固定 `failed/install`，不会暴露包内容、内部错误或路径。H8-22 已用 ad-hoc macOS 实包证明上述替换、重启与失败恢复语义，并准备了 Windows `currentUser` 普通未签名 NSIS 的隔离原路径验收器；Windows 实机事实、Developer ID/notarization 与 Authenticode 仍是发布门禁。

H8-22 的 UI 边界位于 `features/app-updates/AppUpdateCenter.tsx` 与 `platform/tauri/app-update-gateway.ts`。生产组合根唯一构造 `TauriAppUpdateGateway`，经 `App → WorkbenchShell` 注入；更新中心常驻轮询只读状态，使提示不依赖用户先进入设置页，而设置卡片只在“设置与诊断”展示状态、进度与主动检查。Rust `UpdateState` enum 字段固定为 camelCase，使 `downloadedBytes/totalBytes` 与 exact-field Zod 契约一致；用户主动检查或决策持有同一个本地 operation gate，期间轮询直接跳过，防止并发调用原生协调器。可选 `ready/prompt` 才渲染立即安装、稍后提醒和跳过版本；强制 `ready/forced` 使用不可关闭提示且没有用户决策。Zod 还验证 release policy 与 action 一致，未知原生字段、底层错误、URL、签名和路径均不会进入组件。三轮隐藏 App 已验证更新决策与安装交接；专用 `tauri.update-macos-package-e2e.conf.json`/runner 进一步用临时 Minisign、ad-hoc codesign、真实 DMG 和无安装探针 official updater 验证 macOS 覆盖/重启/失败恢复，所有安装根固定在无 symlink 的 `/private/tmp` 并在 finally 清理。Windows 专用配置/WDIO/spec/runner 则固定唯一 `currentUser` product、identifier、binary 和 AppData，从普通 `NotSigned` NSIS 进入同一 production feed/Rust/official updater 路径；外层只在安装后二进制 PE 版本/哈希命中并等待 updater 安装器退出后接受预期断连，同时核对 HKCU 安装版本/路径/卸载记录，每轮由专属卸载器还原。该 runner 尚未在 Windows 实体机执行，因此普通包实体证据、Developer ID/notarization 与 Authenticode 发布验收仍待补。

P9-03 不新增 IPC、Capability 或运行时安装器。debug 仍从 AppData 的 `local-executor/package` 使用开发/验收包；release setup 则由 Tauri `resource_dir()` 固定派生 `.app/Contents/Resources/local-executor/package`，再与 AppData 下的 `local-executor/state` 组成同一个 `ExecutorPlatformService`。两棵目录必须是无 `.`/`..` 的绝对非重叠路径，实际启动前仍由 E4-05 对 Resources 内 Manifest、签名、平台/架构和完整目录做 fail-closed 复验。独立候选配置只选择 `app/dmg`、不锁死发布 identity，并继承生产 `withGlobalTauri=false`、单一 `main` capability、空权限表和 CSP；普通包 runner 才在临时生成配置中强制 ad-hoc。正式 Developer ID Application、公证与 Gatekeeper 分发不由普通候选冒充。

P9-04 沿同一个 release Resources 组合根构建 Windows NSIS，不新增第二个 Executor 路径或安装服务。正式候选覆盖只声明 `targets=["nsis"]` 和 `installMode="currentUser"`，不覆盖 product/identifier/main binary、App、plugin、Capability、CSP、Updater passive 模式或 Windows 签名字段。原生 runner 从 P9-02 候选生成一次性 Manifest，以无测试 Feature release 执行 E4-15 审计；为避免破坏用户可能已有的正式安装，安装阶段才生成唯一隔离 identity，并在非提权进程中核对普通 `NotSigned` installer/App/uninstaller、主二进制版本/哈希、HKCU-only 卸载记录、LocalAppData 根、Resources 清单/Manifest/PE 以及专属卸载零残留。Windows 实机运行与正式 Authenticode 是独立待验收事实。

P9-05 在 E4-15 既有主二进制、生产配置、Vite assets 和无测试 Feature Cargo tree 审计之外，新增最终 bundle 全树边界。`audit-release-bundle.mjs` 只接受 macOS `Contents/Resources/local-executor/package` 或 Windows `local-executor/package`，要求平台入口及 Manifest/签名 metadata 存在；递归读取时拒绝 symlink/特殊文件、20,000 文件/16 GiB 以上包、测试/WDIO/安装探针/1420 origin、开发公钥/测试 Session/private key，以及 Profile/Cookie/SQLite/log/diagnostic/upload/download/material 路径。扫描按 1 MiB 流式分块并保留最大 marker overlap。P9-03 已在真实 build App 和 DMG 挂载副本各通过 304 文件/204,479,153 bytes，P9-04 已在 Windows 卸载前接入同一规则；Windows 原生结果仍待补。

P9-06 不给正式 App 增加测试 Feature、IPC 或运行时配置口。显式设备 runner 消费已经完成 Developer ID 签名、公证 staple 和 Gatekeeper 认可的唯一 DMG，复用 P9-05 完整包审计后复制到不存在的用户级验收 App；生产 identifier/AppData 保持不变，因此任何既有 AppData 都在启动前拒绝。目标 App 环境只保留必要用户变量和系统 `PATH`，移除 Python/虚拟环境/`AUTOMATION_TOOL_*`；外部扫码窗口打开时，OS 进程树必须证明正式 Executor、一个 Chrome/Edge、`app_data_dir/browser-profiles` 和零 Python 后代。人工 checkpoint 只允许授权账号的无写入 browse 旅程，重启后必须观察登录态/任务快照复用和零重复动作；证据是 path-free 的 `0600` JSON，安装和 AppData 可从废纸篓恢复。该入口显式可见且不进入 CI；签名包、真实账号以及本地 Control Plane/首次设备注册链未具备时保持 fail closed。

P9-07 同样不增加测试 Feature/IPC，直接消费发布方给出的 NSIS。PowerShell 只作 OS 证明：`Get-AuthenticodeSignature` 配合在线 `X509Chain`、Code Signing EKU、时间戳和 thumbprint 锁定 installer/App/uninstaller；注册表只允许 HKCU 的固定 DisplayName/InstallLocation/UninstallString，HKLM 任一命中即拒绝。App 在无 Python/开发变量的系统环境启动后，Win32 `GetProcessDpiAwareness/GetDpiForWindow` 要求 per-monitor 与至少 125% 实际 DPI，CIM 进程树要求一个 `IsProcessInJob=true` 的 Executor、一个 Chrome/Edge 和私有 Profile。强停只针对主 App PID，不递归杀树；Executor/浏览器仍存活即证明 Job 清理失败。三次启动分别覆盖真实旅程、普通恢复和强停恢复；卸载必须删程序/HKCU 而保留 AppData，随后由 runner 复验 current-user ACL 并移到回收站。最小证据关闭 ACL 继承且只授权当前用户；入口显式可见，不在 CI 自动运行。

正式构建通过 `AUTOMATION_TOOL_UPDATE_ENDPOINT` 与 `AUTOMATION_TOOL_UPDATE_PUBLIC_KEY` 注入公开发布配置；`build.rs` 在 release Profile 强制 endpoint 为包含一次 `target/arch/current_version` 占位符的 HTTPS URL，并对规范 Base64 包裹的 Minisign 公钥做实际解析。缺失、非 HTTPS、带凭据/fragment、占位符异常或坏公钥均在打包前失败。debug 仅在显式本机环境变量存在时启用更新，证书忽略开关只编译进 `desktop-e2e`；正式 App 不接受运行时端点覆盖。发布私钥始终只属于受控签名环境。

## 15. 禁止事项

- 禁止建设、部署或对外交付 Web 版；
- 禁止业务页面直接导入 Tauri API；
- 禁止 React 获取长期安装实例凭据、平台 Cookie 或 Executor 会话令牌；
- 禁止 Feature 自己读取或拼接 `baseUrl`；
- 禁止 Tauri 提供任意 URL、任意文件或任意命令代理；
- 禁止用 UI Harness 通过替代真实 Tauri/RPA/微信验收；
- 禁止把远程社交平台网页嵌入 WebView；
- 禁止分别维护 local/demo 页面或 API 逻辑。
