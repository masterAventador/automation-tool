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

T3-06 已在同一正式 Rust Control Plane client 中加入封闭的创建 Task operation；T3-17 已以 `create_douyin_search_exposure_task` 固定 Command 接入窄任务表单。React 只能提交 `douyin.search_exposure.v1` 的明确字段，Zod 先校验，Rust 再校验安全文本、动作/消息关系、数量、间隔和强制确认，然后自行从 App 私有 vault 换取 `app.control-plane` Session 并注入受限幂等键。WebView 不接触 bearer、Header 或任意 URL。完成证据来自 `visible=false` 真实 Tauri App 点击表单并核对 PostgreSQL 最终定义，而不是浏览器 Harness 或直接 HTTP。

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
├── task-create/            # 抖音任务表单和目标预览
├── task-runs/              # 快照、事件、控制和结果
├── platform-sessions/      # 抖音登录态、重新登录和清理
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

Executor v1 的 TypeScript 正式入口是 `src/api/protocol/executor-envelope.ts` 的 `parseExecutorMessage`；Rust 正式入口是 `src-tauri/src/executor_protocol.rs` 的 `parse_executor_message`。两者不从 UI、IPC 或网络输入推断类型，而是与 Python 权威模型共同回放 `contracts/fixtures/executor-v1` 的原始 UTF-8 wire：判别类型、任务作用域、UUIDv4、UTC 微秒 deadline、安全整数、重复 key、资源上限和 payload 隐私规则均 fail closed，失败只返回固定错误。解析器当前不暴露新 Tauri Command；I2-13 已在 Control Plane 网络入口复用 Python 正式 parser，E4-02 已让独立 Local Executor 发送 Hello/Heartbeat，E4-12 已沿同一 WebSocket 接入持久无副作用任务回放，React 只消费经过边界验证的公开投影。

当前 FastAPI OpenAPI 3.1 快照固定在 `contracts/openapi/control-plane.v1.json`，系统 operationId 为 `getSystemHealth`、`getSystemVersion`。`frontend/scripts/openapi.mjs` 使用锁定的 `openapi-typescript` 从快照机械生成 `src/api/generated/control-plane.ts`，`--check` 在系统临时目录重新生成并逐字比较；生成文件禁止手改。

业务组件只处理统一 `AppError`，不直接判断 Axios、Rust 或 FastAPI 的原始异常。

### 4.5 Platform 层

`PlatformAdapter` 至少覆盖：

```ts
interface PlatformAdapter {
  getCapabilities(): Promise<PlatformCapabilities>
  getExecutorStatus(): Promise<ExecutorStatus>
  restartExecutor(): Promise<void>
  requestHumanHandoff(input: HandoffInput): Promise<void>
  emergencyStop(input: EmergencyStopInput): Promise<StopResult>
  selectFiles(input: FileSelectionInput): Promise<SelectedFile[]>
  revealFile(input: RevealFileInput): Promise<void>
  exportDiagnostics(input: DiagnosticsExportInput): Promise<ExportResult>
  showNotification(input: NotificationInput): Promise<void>
  openExternalUrl(input: ExternalUrlInput): Promise<void>
}
```

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

第一期没有产品用户账号和登录 UI，但 Demo 云端仍需认证：

- App 首次启动生成安装实例 ID 和设备密钥；
- 受控 Demo 安装包通过限时、限环境的 bootstrap 授权注册一个安装实例；
- 后端签发可撤销的设备凭据；
- Rust 使用设备凭据换取短期访问能力；
- 用户无感进入工作台；
- bootstrap 凭据不能用于业务 API，Demo 结束后可吊销；
- 这只是 Demo 安全边界，不冒充完整用户账号体系。

如果无法安全提供 bootstrap 或设备注册，Demo 后端必须限制在受控网络，不能退化成匿名公网写接口。

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

E4-07 已实现 `executor_manager.rs` 固定生命周期。Manager 的受信装配项只有包根、E4-05 verifier 和 60 秒内的 start/stop timeout；每次 start 都先复验完整目录，再从 Manifest 精确入口无参数 spawn，唯一 stdin 写入 E4-06 bootstrap 后关闭。stdout reader 只接受 4096 字节内的严格 healthy/stopped JSON+LF 并验证事件 proof；stderr 由 E4-10 的独立限界脱敏模块处理。一个 Mutex 使 start/status/stop 线性化，8 路并发 start 只产生一个子进程，超时/坏证明/Drop 都强制回收直接子进程。当前没有 Tauri Command 或 React API；E4-13/E4-14 才装配固定桌面入口。macOS 已从公开 Rust Manager 原入口跑通真实 signed PyInstaller Executor→Uvicorn→Heartbeat→停止，Windows 原生仍待 runner。

E4-08 的监管仍在同一个 Manager：调用方必须显式提供最大重启次数、monitor interval 和 restart delay，模块再以 8 次/60 秒硬上限约束；MVP 预算为 2。唯一 supervisor thread 通过 channel 唤醒和有界轮询观察 Child，Mutex 内状态机为 running/restarting/stopped。Unix 只对 signal crash、Windows 只计划对负 NT 异常码重启；正常/固定失败退出、显式 stop、坏包或启动认证失败直接 stopped。恢复前重新执行完整包验证并生成新的本机会话，公开状态只增加 `restartCount`。显式 stop 会先从状态机移除 running/pending，Drop 先关闭/join supervisor，因此不会与后台线程形成复活竞态；E4-09 已把直接 Child 清理扩展成完整进程树。

E4-09 把完整进程树所有权收回同一个 `RunningExecutor`，不引入第二 Manager。Unix `CommandExt::process_group(0)` 在 exec 前创建独立 PGID，强制清理只向该负 PGID 发 `SIGKILL`，`ESRCH` 作为已清理处理；正常 stop 仍只向主进程发 `SIGTERM` 以取得认证 stopped proof，但主进程退出后必须再次终止组内剩余后代再 join reader。Windows 进程以 `CREATE_SUSPENDED` 启动，在任何业务代码运行前配置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`、挂入 Job Object 并恢复初始线程；配置、挂载或恢复失败均关闭 Job/终止 suspended child。启动/停止超时、显式 stop、异常退出准备重启和 Manager Drop 都走同一树清理原语。macOS 已用真实签名进程和忽略 `SIGTERM` 的孙进程验证全部边界；Windows 原生行为仍因 runner 计费限制保持待验收。当前仍无 task invoke API，因此 E4-09 的“挂起”指进程生命周期挂起；任务副作用超时与 `OUTCOME_UNCERTAIN` 归 E4-12/后续 RPA 执行层。

E4-10 将所有代次共享的 `ExecutorDiagnostics` 放在 Manager Core，重启不会创建第二日志源。stderr reader 用 `BufRead::fill_buf/consume` 在输入阶段限制捕获，不会因未换行恶意输出无界分配；超长和非法 UTF-8 行只产生固定 `[TRUNCATED]`/`[REDACTED]`。其余行先清除控制/Bidi 字符，再按三端共享 fixtures 依次移除认证 Header、Bearer、设备/本机会话 envelope、64 位本机令牌、平台 Cookie、敏感 JSON/assignment、URL userinfo/全部 query、file/data URL 和私有路径；最终以 200 行、单行 4096 bytes、总计 64 KiB 三重上限滚动淘汰。公开 `diagnostics()` 只克隆安全内存，不提供原始数据、持久化或 WebView 命令；E4-13 才通过固定 PlatformAdapter 展示。macOS 真实 signed 子进程 stderr 已通过，Windows 实包仍待 runner。

E4-11 仍不新增第二 Manager 或 WebView API。`ExecutorLaunchConfiguration` 持有经过绝对路径/长度/组件校验的 `state_directory: PathBuf`；后续 E4-13 必须从 Tauri 自身解析的 `app_data_dir` 派生固定 Executor 子目录，React 不能提交路径。Rust 只把它放入受限 stdin bootstrap，Python CLI 在任何网络连接前完成 `executor-ledger.sqlite3` v1 迁移和 Installation/Executor 身份绑定。该数据库属于 Executor 的本机恢复边界，不是 Control Plane 副本：只保存正式协议命令身份/意图指纹、Attempt checkpoint 和 outbox，不保存 Session、Cookie、密钥、浏览器 Profile 或任意 App 配置，也不调用系统钥匙串。

E4-12 没有给 WebView 增加通用命令通道：Rust Manager 仍只监管正式 Python Executor，由 Python 在 Control Plane WebSocket 内消费 `task.offer` 并从同一 SQLite 精确重放 ACK/Event。macOS 已从公开 Manager 原入口两次启动 signed PyInstaller 产物验证同一状态目录恢复；React 不读取账本、不提交路径，也不能直接调用 Executor。E4-13/E4-14 再通过固定 PlatformAdapter 与隐藏 App 验收 `app_data_dir` 装配。

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

当前 F1-13 基线使用 `@wdio/tauri-service 1.2.0` embedded provider：`pnpm test:tauri` 构建带 `desktop-e2e` Cargo 特性的 debug App，并在真实 macOS WKWebView 中验证无登录工作台和 `main` 原生窗口。WDIO Rust/前端插件、`withGlobalTauri=true` 和测试 Capability 只存在于测试配置对应的构建；测试 Capability 以内联对象提供，不能放入 production 默认扫描的 `capabilities/` 目录。正常 Cargo 依赖树不启用两个可选 WDIO crate，生产 Vite 构建扫描测试标记并 fail closed。所有自动化 Tauri 配置（包括 T3-15 Task 投影、T3-16 工作台、T3-19 生命周期与 T3-20 重启验收）都把唯一测试主窗口固定为 `visible=false`，自动化 App 只在后台运行且不抢焦点；production `tauri.conf.json` 保持窗口可见。

I2-04 起，`desktop-e2e` 特性在真实 App 进程内生成不持久化的临时 Ed25519 身份，避免通用桌面冒烟污染开发机或 CI 的正式 App 数据。I2-08 另以正式、非 `desktop-e2e` Tauri 入口解析隔离测试标识的 `app_data_dir`，验证私钥文件首次创建、重启复用、权限和无长期凭据初始状态；Rust 测试再覆盖凭据写入、替换、删除及故障矩阵。临时身份不能替代正式 App 私有存储验收。

`pnpm test:layers` 固定按 Vitest/契约、Playwright UI Harness、Rust、WebdriverIO 真实桌面四层执行。通用桌面冒烟证明真实 App、WKWebView、测试 IPC 插件和窗口查询可用；I2-09 验证认证纵向链路，T3-19 验证完整任务交互，T3-20 的 `scripts/run_t3_20_acceptance.py` 再让同一隐藏 App 保持运行，真实停止 Control Plane、整页刷新显示不可用、以同一 PostgreSQL 重启服务并点击“重新检查”，最终从工作台/详情读取 Executor 重连后的取消终态。这些通用证据本身不证明 Local Executor、外部运营浏览器或 RPA；E4-07/E4-08 已用独立 Rust/真实进程链验证 Manager 生命周期和重启预算，隐藏 App、完整进程树、外部浏览器与平台最终状态仍分别由 E4-09/E4-14 和 Wave 5～7 补自己的验收。

## 14. 构建和配置

- `local` 构建连接开发机本地 Control Plane；
- `demo` 构建连接指定 HTTPS Demo API；
- baseUrl 和允许域名进入签名构建 Profile；
- Demo bootstrap 授权以限时、限环境方式注入，不写入源码或普通 Vite 环境变量；
- 正式构建只包含 Tauri 入口，不发布 Vite 静态站点；
- Local Executor 随目标平台安装包构建、签名和版本锁定；
- App、Executor 和 Control Plane 建立明确兼容矩阵与最小支持版本。

## 15. 禁止事项

- 禁止建设、部署或对外交付 Web 版；
- 禁止业务页面直接导入 Tauri API；
- 禁止 React 获取长期安装实例凭据、平台 Cookie 或 Executor 会话令牌；
- 禁止 Feature 自己读取或拼接 `baseUrl`；
- 禁止 Tauri 提供任意 URL、任意文件或任意命令代理；
- 禁止用 UI Harness 通过替代真实 Tauri/RPA/微信验收；
- 禁止把远程社交平台网页嵌入 WebView；
- 禁止分别维护 local/demo 页面或 API 逻辑。
