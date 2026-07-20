# 自动化运营工具前端架构

> 状态：当前项目实现基线
> 建立日期：2026-07-18
> 适用范围：React UI、Tauri/Rust 桌面壳、Control Plane 通信和本地执行器桥接

## 1. 建设目标

前端只交付一个 Tauri 桌面客户端，用户打开 App 后直接进入 RPA 运营工作台。

它需要同时处理三类能力：

1. 产品 UI：任务创建、目标预览、运行详情、结果、设置和诊断；
2. 网络控制面：连接开发机本地或 Demo 云端 FastAPI；
3. 本机原生能力：监管 Local Executor、打开外部浏览器、文件、通知、窗口和紧急停止。

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

第一期没有认证路由守卫。启动成功后固定进入 `/workbench`；后端不可用时进入可恢复的连接故障页，而不是登录页。

App 启动边界已实现 ready、checking、unavailable、revoked 四态和安全重试；ready 后进入正式工作台页面。F1-08 保留注入式 `StartupCheck` 用于孤立 UI 测试；生产 `main.tsx` 已组合正式 `TauriControlPlaneTransport`、`TauriTaskProjectionSource` 与 `TauriWorkbenchGateway`。真实 WebView 只 invoke 固定 Rust Command，Rust 先检查 Control Plane Health；若 App 私有目录已有长期凭据，还会换取 `app.control-plane` Session 并请求当前 Installation 访问探针。未注册 App 仍直接进入工作台；精确 401 才进入“当前安装实例已失效”，网络/服务/协议故障仍进入普通不可用诊断。禁止让 WebView 直接请求 Control Plane。

Wave 9 的 P9-06/P9-07 将替换“未注册 App 直接进入工作台”的临时行为：启动组合根在健康检查后先判断本机凭据，缺失时由 Rust 自动创建限时设备申请并持有 opaque 轮询秘密，React 只消费公开配对码、申请/到期时间和封闭状态投影。授权页必须可见展示提交中、等待审批、已批准、已拒绝、已过期和连接失败，提供安全重试或重新申请；批准前不挂载工作台业务路由，批准后由 Rust 完成 I2 两步设备证明并自动切换 ready。刷新、App 重启、断网和审批竞态都从服务端权威申请恢复，不能靠逐个业务按钮报错表达未授权。该段是明确规划边界，当前实现仍以上一段四态为准。

T3-06 已在同一正式 Rust Control Plane client 中加入封闭的创建 Task operation；T3-17 已以 `create_douyin_search_exposure_task` 固定 Command 接入窄任务表单。React 只能提交 `douyin.search_exposure.v1` 的明确字段，Zod 先校验，Rust 再校验安全文本、动作/消息关系、数量、间隔和强制确认，然后自行从 App 私有 vault 换取 `app.control-plane` Session 并注入受限幂等键。WebView 不接触 bearer、Header 或任意 URL。完成证据来自 `visible=false` 真实 Tauri App 点击表单并核对 PostgreSQL 最终定义，而不是浏览器 Harness 或直接 HTTP。

D6-03 将表单和 Gateway 的关键词约束收敛到同一个 `douyinSearchKeywordSchema`，并导出只读 `80` 字符/`100` 目标上限给表单控件；长度使用 `Array.from` 的 Unicode code point 语义，与 Python `len`、Rust `chars().count()` 和 PostgreSQL `char_length` 一致，不再用 UTF-16 code unit 误拒非 BMP 文本。C0/C1/DEL、Bidi、首尾空白和安全文本违规在表单调用 Gateway 前显示固定校验错误，生产 Gateway 与 Rust 仍做第二、第三次复验；React 没有 trim、截断或自动改写用户输入。

A7-05 以 `douyinActionMessageTemplateSchema` 将评论/私信文案收紧为固定文案或只含 `{{target_display_name}}` 的封闭模板。表单直接复用 Gateway Schema，未知、带空格、表达式式或畸形花括号在 IPC 前拒绝；Gateway 再复验 500 Unicode code point、非空字面和安全文本，Rust 在序列化/HTTP 前做同等 fail-closed 检查。当前路径只持久模板原文，不在 WebView/Rust 渲染目标数据，不调用 LLM，也没有评论/私信发送代码。

A7-06 让目标预览公开 DTO 同时携带 action、原始 message template 和独立 `confirmationRevision`。最终确认弹窗打开时冻结 page/revision/action/template/count 五项审阅事实；后台 Query 或事件导致的新预览不会替换已打开弹窗的提交参数。Rust 仍只发送固定确认 operation，并在目标预览专用错误映射中把后端冲突保留为 `request_rejected`；React 只据此显示“目标列表已变化”并回拉，其他协议/传输异常继续脱敏。唯一隐藏 App 已实际发出旧 revision、收到真实后端拒绝，再审阅新版本后成功确认。

T3-07 在该 Rust client 中增加 `ListTasks` 与 `GetTask` 两个封闭 operation。列表只允许固定 `/api/v1/tasks`、`1..100` limit 和长度受限的 canonical Base64URL cursor；详情先把 Task ID 验证为规范 UUIDv4 才构造固定路径。T3-15 将既有数据库水位加入同一公开快照，当前 DTO 为 taskId/status/revision/lastEventSequence/createdAt/updatedAt；Rust 复核 16 态枚举、正 revision、安全水位、UTC 时间、列表降序及 cursor 形状，跨 Installation 详情只得到统一拒绝。生产 React 只通过窄 `TauriTaskProjectionSource` 消费，不存在通用 URL/请求代理。

T3-12 在同一个正式 Rust client 中增加 `StreamTaskEvents`：路径只能由规范 Task UUID 构造，Rust 自行从 App 私有 vault 换取 `app.control-plane` Session 并注入 Bearer，支持标准 `Last-Event-ID`，限制单连接 512 KiB、单帧 64 KiB 和验收用有界停止数。解析器要求 `text/event-stream`、匹配 request ID、`no-store/no-transform`、禁代理缓冲，以及唯一 id/event/data 字段；公开 DTO 再核对连续安全整数序号、`1.0` 版本、封闭事件/Task 状态、UUIDv4、UTC 时间、进度与消息边界。React/IPC 不接触 Session、Header 或原始 SSE 文本。T3-15 已在同一解析循环逐条调用回调并推送 Tauri Channel，不等整条 SSE 结束，也没有重新用 WebView EventSource。

T3-13 又在相同 Rust client 中加入固定 `PauseTask`/`ResumeTask` operation。调用者只能提供规范 Task UUID 和受限幂等键；Rust 自行换 App Session、发送空 JSON、构造 `/pause` 或 `/resume` 固定路径，并只接受 202 创建或 200 重放。公开命令对象必须通过 Command/Task/Attempt UUIDv4、跨运行时安全 sequence、精确 command type、封闭 outbox status、正 revision 和 UTC deadline 校验。T3-18 已把两种操作接入正式运行详情，仍不向 React 暴露任意 operation、Header 或 bearer。

T3-14 继续在同一 Rust client 增加固定 `CancelTask`/`EmergencyStopTask` operation，沿用规范 Task UUID、受限幂等键、App 私有 vault 换票和严格公开 Command 解析；固定路径只能是 `/cancel` 与 `/emergency-stop`，仍只接受 202/200。T3-16 已接全局紧停，T3-18 已接运行详情的二次确认控制；WebView 不新增通用 URL、Header、bearer 或原始响应入口。

T3-15 建立正式 Task 投影边界：`TauriTaskProjectionSource` 只 invoke 固定快照、列表和事件 Channel Command，Rust 从私有 vault 换票并返回精确公开 DTO；`taskProjectionKeys`/Query options 管理服务端快照，纯 Reducer 以 status/revision/lastEventSequence 为权威。水位内事件直接去重；下一序号缺口、Task/revision 回退、未知版本/类型或畸形 DTO 只进入 `refresh_required`，先失效并回拉 Query 快照再续订；连续不兼容超过有界预算进入 `degraded`，不从事件名猜状态。正常 SSE 限时关闭也先读新快照再续订，不计作协议降级。唯一 `visible=false` App 已从 WebView 正式 TypeScript source 经 Rust/真实后端/FakeExecutor 收敛 sequence 1..5 到 succeeded。

T3-16 将投影接入正式 RPA 工作台：`Workbench` 展示 Control Plane/Executor 状态、今日任务指标、当前任务和最近任务，较新的实时快照会覆盖列表旧状态。`TauriWorkbenchGateway` 只允许固定工作台状态与紧停 Command；全局紧停必须二次确认，同一 Task 的不确定重试复用幂等键，提交后回拉列表/详情/运行状态，最终状态仍只认 Executor 事件。运行状态轮询设置为隐藏窗口继续执行，满足后台 App 验收。唯一 `visible=false` App 已从页面点击紧停，经正式 Rust/后端/Outbox/FakeExecutor ACK 收敛到 `outcome_uncertain`；长期凭据仍只在 `app_data_dir`，不使用系统钥匙串。

T3-18 将工作台 Task 入口接到正式 `TaskRunDetails`。页面先读 TanStack Query 权威快照，再从持久事件起点通过同一 Rust SSE/Tauri Channel 重放并跟随时间线；只投影明确的进度、step 与 `actionId` 事实，缺少目标或平台证据时显示空态。`TauriTaskRunControlGateway` 暴露四个窄方法，对应四个固定 Rust Command；按钮按权威状态启停，取消/紧停二次确认，不确定重试在同 revision 复用幂等键，提交回执不会冒充 Executor 已执行。事件畸形、错 Task 或缺口会 fail closed 并要求显式重载。唯一 `visible=false` App 已真实点击四类控制并经后端与 HOLD FakeExecutor 收敛最终事实。

### 4.2 Feature 层

第一期 Feature：

```text
features/
├── workbench/              # 运营总览和当前任务
├── task-create/            # 抖音任务表单
├── task-runs/              # 快照、目标预览、事件、控制和结果
├── platform-sessions/      # 抖音服务端健康、本机处理与后续安全注销
├── diagnostics/            # 后端、Executor、浏览器和权限诊断
└── settings/               # 本地保留、浏览器选择和诊断导出
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

当前 FastAPI OpenAPI 3.1 快照固定在 `contracts/openapi/control-plane.v1.json`，系统 operationId 为 `getSystemHealth`、`getSystemVersion`。`frontend/scripts/openapi.mjs` 使用锁定的 `openapi-typescript` 从快照机械生成 `src/api/generated/control-plane.ts`，`--check` 在系统临时目录重新生成并逐字比较；生成文件禁止手改。

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

### 5.3 无登录页面下的安装实例认证

第一期没有产品用户账号和登录 UI，但 Demo 云端仍需认证。I2 已实现设备身份、bootstrap challenge、长期凭据与短期 Session；P9-06/P9-07 在此基础上补齐陌生设备的可见申请和后台审批：

- App 首次启动生成稳定设备身份和设备密钥；
- 缺少长期凭据时，Rust 自动创建限时设备申请，只把公开配对码、申请时间、到期时间和封闭状态投影给 React；opaque 轮询秘密不进入 React、IPC 响应、localStorage、日志或普通配置；
- 设备授权页展示提交中、等待审批、已批准、已拒绝、已过期和连接失败，允许对可恢复失败安全重试，对拒绝/过期明确重新申请；
- 后台认证运维入口按配对码与设备公钥摘要批准或拒绝；批准使用离线签名 bootstrap 绑定该申请的一次注册，Control Plane 不持有离线签发私钥，也不持久化原 token；
- Rust 仅在服务端权威状态为已批准时进入既有两步 challenge，用本机设备私钥证明后保存可撤销设备凭据；
- 批准前不进入工作台且所有业务 Command fail closed，批准后自动进入工作台；日常重启已有有效凭据时继续打开即用；
- bootstrap 或申请授权不能调用业务 API，Demo 结束后可吊销；
- 这只是安装实例安全边界，不冒充完整用户账号体系。

如果无法安全提供 bootstrap、审批授权或设备注册，Demo 后端必须限制在受控网络，不能退化成匿名公网写接口。

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

E4-12 没有给 WebView 增加通用命令通道：Rust Manager 仍只监管正式 Python Executor，由 Python 在 Control Plane WebSocket 内消费 `task.offer` 并从同一 SQLite 精确重放 ACK/Event。macOS 已从公开 Manager 原入口两次启动 signed PyInstaller 产物验证同一状态目录恢复；React 不读取账本、不提交路径，也不能直接调用 Executor。

E4-13 新增唯一 `executor_platform.rs` 组合根。Tauri setup 只从 `app.path().app_data_dir()` 派生 `local-executor/package`、`local-executor/state` 和 `executor-id-v1`；稳定 Executor UUIDv4 使用既有 App 私有原子存储，Unix 目录/文件为 `0700/0600`。重启时 Rust 依次换取 `app.control-plane` Session、校验当前 Installation，再换取独立 `executor.connect` Session 并启动 Manager；React 只能调用四个无参数 Command，不能传 URL、Session、路径、包根或身份。`emergency_stop_executor` 是本机完整进程树硬停止，与 T3-16/T3-18 的业务 Task 协作式紧停严格分离。E4-14 已从唯一 `visible=false` App 的诊断页面完成 WebView→IPC→Control Plane→signed Executor→退出清理的 macOS 生产同路径验收。

E4-14 专用配置只在 `control-plane-e2e` 编译中允许 runner 注入规范动态 loopback origin，并只在该特性注册真实 OS crash/hang 与正常退出验收 Command；默认和 `desktop-e2e` 构建没有这些入口。真实链路发现首个健康心跳为 15 秒而原启动预算为 10 秒，现将 App 组合根启动预算固定为 30 秒。生产 Tauri event loop 在 `RunEvent::ExitRequested` 或 `RunEvent::Exit` 上显式调用唯一 Platform service 停止 Executor，Manager Drop 仍是兜底，不能依赖测试驱动杀进程触发析构。验收核对 App 私有稳定 UUID、SQLite identity/版本迁移、Unix 权限和凭据不入库；所有服务、进程、端口与数据均按本次专属标识清理。

B5-13 在同一组合根增加平台状态纵向链路。`get_douyin_platform_session` 由 Rust 自行换 `app.control-plane` Session 并调用固定 `/api/v1/platform-sessions/douyin`；`open_douyin_login`/`recheck_douyin_login` 在 Executor 停止时先按 E4-13 原路径自动启动，再从 `BrowserSettingsService` 重新发现受信浏览器、从 `BrowserProfileStore.current_douyin_profile()` 取得稳定 App 私有 Profile 并持有 owned lease。Manager 在现有 stdin/stdout 上发送/验签动作，不创建第二 Executor 或第二浏览器 Manager；生产 headed，只有 B5-13 专用 `visible=false`、唯一标识的 `control-plane-e2e` 构建硬编码 headless。真实验收从页面点击出发并在 App 正常退出后确认 signed Executor、Chrome、Profile 锁和隔离服务全部清理。

B5-14 在同一 Gateway 增加无参数 `logout_douyin_session`。页面使用明确二次确认且 pending 时禁用重复提交；Rust 先调用服务端 logout prepare 持久阻断，再通过唯一 Manager 紧停 Executor/浏览器树并释放 lease，随后由 `BrowserProfileStore` 删除 current Douyin Profile。删除只在稳定平台目录句柄下完成，原目录先原子改名为唯一 tombstone、复验同一目录 identity 后删除；重试可续删 tombstone，symlink/reparse、双目录、活跃锁或 identity 漂移一律拒绝。之后 Rust 重启 signed Executor，发送不含任何路径/headless 字段的 `douyin.logout.complete`，并只把重新查询到的服务端 `missing` 投影返回页面。

B5-15 用同一个生产 Gateway/Command 证明 App、Executor 与浏览器生命周期可以全部重建而不更换 Profile。已有健康 Profile 作为本机首个事实时建立 revision 1，不再因 `recovered=true` 被错误拒绝；第二次健康启动才递增到 revision 2。验收配置固定 `visible=false` 且 BrowserRuntime 固定 headless，四轮之间只允许 App/Executor/context 退出重建，current marker 与 Profile device/inode 必须不变；过期页面进入扫码，风险页面进入人工接管。确定性页面属于单独签名的测试 Executor，不进入正式 package spec 或生产配置，真实账号双重启证据仍单独标记待补。

B5-16 继续复用生产 `BrowserProfileStore.current_douyin_profile()`→owned lease→本机认证命令→Python `launch_persistent_context(request.profile_directory)`，没有第二个 Profile 解析入口。专用隐藏 App 在扫码状态保持无头 Chrome 活跃，WDIO 只通过临时 ready/release 文件协调外层审计；外层读取 OS 进程参数和 `lsof`，要求唯一 `--user-data-dir` 与实际打开文件都只落在 App 私有 current Profile，并拒绝 Chrome/Edge 默认 User Data。生产 Rust/Python/TypeScript 源码另由递归契约拒绝默认路径、Cookie 和 storage-state API；Profile 目录与 UUID不进入 WebView、日志或验收输出。

D6-10 在既有 Rust `ControlPlaneClient` 增加唯一 `StartTaskDiscovery` operation，并在 Tauri 注册固定 `start_task_discovery(task_id,idempotency_key)` Command。Rust 自行从 App 私有 vault 换取 `app.control-plane` Session，只向固定 `/api/v1/tasks/{task_id}/discoveries` 发 POST，严格解析 task/command/attempt/status/revision/watermark/UTC deadline；WebView 不能提交关键词、Candidate、Cookie、浏览器/Profile 路径或任意 URL。D6-10 的 `control-plane-e2e` 专用 Command 只用于 hidden App 纵向验收，不进入默认构建。

D6-11 继续复用同一个 `ControlPlaneClient`，以 `GetTaskTargetPreview`、`ReplaceTaskTargetExclusions`、`ConfirmTaskTargetPreview` 三个封闭 operation 和同名固定 Tauri Command 调用 task-scoped API。游标、page revision、task revision、Target UUIDv4、排除集合与幂等键均在 TypeScript、Rust 和后端逐层复验；Rust 只返回公开摘要、封闭来源/策略原因、选择状态、计数和确认时间，拒绝未知字段、乱序、跨 Task、无确认的后确认状态或确认后的预确认状态。`task-target-preview-source.ts` 是正式 PlatformAdapter source；测试配置固定 `visible=false`，生产配置和默认构建没有验收入口。

D6-12 把该正式 source 注入 `App → WorkbenchShell → TaskRunDetails`，没有新增 HTTP 或通用 IPC。`TaskTargetPreviewPanel` 只在当前 Task 等待确认或已收到确认事实时加载最多 100 个目标，展示最小摘要、固定来源、计划执行/用户排除/策略拦截计数，以及 `eligible/本任务重复/30 天内已触达/黑名单` 封闭标记；Target UUID 只作为受控 Mutation 参数，不渲染。单选、全部取消和恢复全部都用完整排除集合、当前 page/task revision 与同意图稳定幂等键调用正式 source；过期 revision 自动回拉，未知错误不显示底层文本，空选择禁止确认。确认使用明确二次确认，成功后回拉任务快照/列表并等待权威事件。`scripts/run_d6_12_acceptance.py` 由独立 `visible=false` App 在真实页面取消第二个目标并确认，经正式 TypeScript source、IPC、Rust、Uvicorn/PostgreSQL 验证最终 `queued`、selection revision 和连续事件；准备命令只注册/发现测试 Task，不代替三次用户页面 API 调用。

E4-15 把测试隔离从源码约束扩展到实际 release 字节。`build.rs` 在 `PROFILE=release` 时先验证编译期 `AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY` 是 canonical 32 字节、有效且非弱 Ed25519 公钥，失败发生在 `tauri_build::build()` 之前且错误不回显输入；公开开发 fixture key 只存在于 debug 分支。生产二进制审计同时读取无默认特性的 Cargo 依赖树、正式 Tauri 配置和 Vite 资产，拒绝 WDIO/WebDriver、验收 Command、测试 origin/标识/Sidecar、Harness、开发验证公钥和 1420 调试端口，并要求实际制品包含预期发布公钥。

真实 release 审计最初发现 `tauri.conf.json` 的 `devUrl`/devCSP 即使 release 不使用仍会进入二进制，因此现已拆到只由 `pnpm tauri:dev` 显式合并的 `tauri.dev.conf.json`。自动化配置继续只用于各自 `--config` 测试构建；正式配置保持唯一可见主窗口、`withGlobalTauri=false`、唯一 `main` Capability 与生产 CSP。E4-15 临时 release target 每次唯一且结束删除，不启动 App、不绑定端口。

B5-01 已冻结外部浏览器会话的迁移边界。当前 Profile 只能从 Tauri `app_data_dir/browser-profiles/douyin/<canonical UUIDv4 profile_id>` 派生，不能由 React、服务端、平台账号文本或任意路径输入决定；B5-05 负责私有权限、symlink/reparse point 与稳定 identity，B5-06/B5-07 负责跨进程单实例锁和真实 headed 浏览器资源所有权。登录健康只由真实页面检测产生 `missing/healthy/expired/risk/unknown`，只有 `healthy` 关闭熔断；等待扫码/确认和人工接管是本地平台工作流，不是 automation-tool 产品登录。

旧 `SocialOperationsRuntime`、进程内账号表、`EncryptedCookieVault`、`.cookie-key`、`SOC1`、tenant/RBAC/Entitlement 全部不迁移。浏览器持久 Profile 是 Cookie/站点数据的唯一来源，React、Tauri IPC、Executor 账本和 Control Plane 都没有 Cookie 导入导出接口。B5-14 注销必须先持久熔断并阻止新任务，安全停止关联动作、关闭浏览器并释放 Profile 锁，最后才定向删除目标目录和递增 `session_revision`；停止失败或最终副作用不确定时保留 Profile 并进入可诊断/`OUTCOME_UNCERTAIN` 状态。

B5-02 新增 Rust `browser_discovery.rs`，但暂不增加 Tauri Command。macOS 固定扫描 `/Applications` 下正式 Chrome/Edge，以 Security.framework 验证所有架构、嵌套代码和精确 vendor designated requirement，并固定 Bundle signing identifier、Developer Team 与 `Contents/MacOS` 主入口；不执行 `codesign` 子进程、不解析 Info.plist 后自行猜测可信度。返回对象保存 App/入口 dev+inode，`revalidate_macos_browser` 在后续启动前要求标准路径未变并重新验签。React、服务端和用户设置只能在 B5-04 选择受支持枚举，永远不能提交可执行路径。

B5-04 将该信任根收口到两个无路径 Tauri Command：`get_browser_settings` 返回固定浏览器枚举和当前选择，`select_browser` 只接受 `google_chrome` / `microsoft_edge`，并在写入前重新执行当前平台真实发现；路径、签名 requirement、证书、identity 和 AppData 根均不序列化到 WebView。`BrowserSettingsService` 只在 Tauri setup 中从 `app.path().app_data_dir()` 构造，选择以 canonical v1 JSON 原子写入 `settings/browser-selection-v1`，缺失、损坏、非 canonical、已卸载或未受信浏览器均 fail closed，绝不回退到用户提供路径。设置页只渲染 Rust 返回的可用枚举，没有文本框或文件选择器。

`scripts/run_b5_04_acceptance.py` 使用专属 `com.aventador.automationtool.b504acceptance` AppData 和启动前检查过的动态 loopback WebDriver 端口，运行唯一 `visible=false` Tauri App；真实页面经正式 `TauriPlatformAdapter` 保存本机受信浏览器，刷新 WebView 后再次从正式 Command 读回。runner 同时核对 canonical 文件内容、Unix `0700/0600` 权限、UI/IPC 不含路径，finally 只删除本次 AppData、恢复生产 Vite 资产并确认端口释放；该验收不启动 Backend、Executor 进程、运营浏览器或用户 Profile。

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
- 外部 Chrome/Edge 启动与人工接管；
- 文件、诊断、紧急停止和错误恢复；
- macOS/Windows 分别冒烟。

当前 F1-13 基线使用 `@wdio/tauri-service 1.2.0` embedded provider：`pnpm test:tauri` 构建带 `desktop-e2e` Cargo 特性的 debug App，并在真实 macOS WKWebView 中验证无登录工作台和 `main` 原生窗口。WDIO Rust/前端插件、`withGlobalTauri=true` 和测试 Capability 只存在于测试配置对应的构建；测试 Capability 以内联对象提供，不能放入 production 默认扫描的 `capabilities/` 目录。正常 Cargo 依赖树不启用两个可选 WDIO crate，生产 Vite 构建扫描测试标记并 fail closed。所有自动化 Tauri 配置（包括 T3-15 Task 投影、T3-16 工作台、T3-19 生命周期、T3-20 重启与 E4-14 Executor 生命周期验收）都把唯一测试主窗口固定为 `visible=false`，自动化 App 只在后台运行且不抢焦点；production `tauri.conf.json` 保持窗口可见。

I2-04 起，`desktop-e2e` 特性在真实 App 进程内生成不持久化的临时 Ed25519 身份，避免通用桌面冒烟污染开发机或 CI 的正式 App 数据。I2-08 另以正式、非 `desktop-e2e` Tauri 入口解析隔离测试标识的 `app_data_dir`，验证私钥文件首次创建、重启复用、权限和无长期凭据初始状态；Rust 测试再覆盖凭据写入、替换、删除及故障矩阵。临时身份不能替代正式 App 私有存储验收。

`pnpm test:layers` 固定按 Vitest/契约、Playwright UI Harness、Rust、WebdriverIO 真实桌面四层执行。通用桌面冒烟证明真实 App、WKWebView、测试 IPC 插件和窗口查询可用；I2-09 验证认证纵向链路，T3-19 验证完整任务交互，T3-20 的 `scripts/run_t3_20_acceptance.py` 再让同一隐藏 App 保持运行，真实停止 Control Plane、整页刷新显示不可用、以同一 PostgreSQL 重启服务并点击“重新检查”，最终从工作台/详情读取 Executor 重连后的取消终态。E4-14 的 `scripts/run_e4_14_acceptance.py` 则从隐藏 App 诊断页驱动正式 signed Executor 启停、崩溃恢复、挂起超时和 App 退出清理。这些证据仍不证明外部运营浏览器或真实平台 RPA，相关最终状态由 Wave 5～7 各自验收。

## 14. 构建和配置

- `local` 构建连接开发机本地 Control Plane；
- `demo` 构建连接指定 HTTPS Demo API；
- baseUrl 和允许域名进入签名构建 Profile；
- Demo bootstrap 授权以限时、限环境方式注入，不写入源码或普通 Vite 环境变量；
- 正式构建只包含 Tauri 入口，不发布 Vite 静态站点；
- 正式配置不含 dev URL/devCSP；release 必须注入经构建脚本验证的 Executor 公钥并通过实际二进制审计；
- Local Executor 随目标平台安装包构建、签名和版本锁定；
- App、Executor 和 Control Plane 建立明确兼容矩阵与最小支持版本。

H8-18～H8-22 的自动更新采用“官方安装底座 + 自有通用策略层”：使用官方 `tauri-plugin-updater` 完成平台包解析、签名验证、下载和安装原语（<https://v2.tauri.app/plugin/updater/>），但不让业务页面直接依赖插件。Rust `AppUpdater` 统一封装启动检查、有界周期检查和用户“检查更新”三个触发源，并持久化可选更新的立即安装/暂不安装/跳过版本，以及不可跳过的强制更新策略。下载缓存始终只保留当前候选，新版本原子替换旧包；强更在下载完成后的下次 App 启动直接进入安装，可选更新继续走同一提示流程。该层只依赖版本、平台、签名、发布策略和安装状态，不引用抖音、任务、客户或其他业务概念，以便跨项目复用。

## 15. 禁止事项

- 禁止建设、部署或对外交付 Web 版；
- 禁止业务页面直接导入 Tauri API；
- 禁止 React 获取长期安装实例凭据、平台 Cookie 或 Executor 会话令牌；
- 禁止 Feature 自己读取或拼接 `baseUrl`；
- 禁止 Tauri 提供任意 URL、任意文件或任意命令代理；
- 禁止用 UI Harness 通过替代真实 Tauri/RPA/微信验收；
- 禁止把远程社交平台网页嵌入 WebView；
- 禁止分别维护 local/demo 页面或 API 逻辑。
