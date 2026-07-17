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

当前工作台壳已实现 ready、checking、unavailable 三态和安全重试。F1-08 通过注入式 `StartupCheck` 验证 UI 行为，默认组合只代表桌面壳可渲染，不声称后端在线；F1-10 已建立 Transport 适配缝，I2-09 接入正式 Rust 网络桥后替换默认组合，禁止临时让 WebView 直接请求 Control Plane。

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

当前 `src/schemas/base-url-profile.ts` 是 UI/配置侧的运行时 Schema：local 只接受无凭据、无路径的精确 `http://127.0.0.1:8765`；demo 只接受无凭据、无路径、无非标准端口的 HTTPS origin，并与构建提供的主机允许列表做规范化后精确匹配。任何非法输入只返回固定配置错误，不回显 URL 内容。Rust Transport 在 F1-10 仍须独立执行同等或更强校验，不能因为 React 已校验就信任页面输入。

### 5.2 Transport

正式 Tauri 使用 Rust `ControlPlaneTransport`：

- Rust 读取受控 `baseUrl`；
- 安装实例长期私钥或设备凭据保存在系统安全存储，不进入 React、`localStorage` 或普通配置；
- Rust 为请求附加短期访问凭据和关联 ID；
- Rust 只允许调用从 OpenAPI operation ID 生成的允许列表，不提供任意 URL 代理；
- SSE/事件流由 Rust 建立并通过 Tauri Channel 传给 React；
- 网络断开时关闭旧流，恢复后先拉任务快照，再从最后序号继续订阅。

测试专用 UI Harness 使用同一个 TypeScript `ControlPlaneTransport` 接口，但可直接通过 Axios 访问本机测试后端。它不能进入正式包。

当前 F1-10 契约只暴露业务需要的 `checkHealth`，不接受 URL 或任意 operation。正式 `TauriControlPlaneTransport` 在 Rust 网络桥完成前固定返回可重试的安全 unavailable；测试 Harness 只委托显式注入的 health handler，缺失 handler 时 fail closed。所有底层异常进入启动页前收敛为 `unavailable`，不反射原始 message。F1-11 生成 DTO 后扩展 operation 类型，I2-09 才实现真实 Rust 网络调用。

当前 Playwright 入口固定为 `harness.html`，支持显式 available、unavailable、flaky 健康投影，只在 Vite 测试服务存在。正式 Vite 仍以 `index.html` 为唯一入口；构建后扫描 `dist/`，拒绝 `harness.html`、Harness runtime 字符串和测试 Transport 标记。扫描器本身用干净/污染临时目录回归，不能静默失效。

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
- 通过 stdin bootstrap 传入一次性会话令牌和必要配置；
- 监管 stdout/stderr、健康、超时、崩溃和重启预算；
- 限界保存脱敏诊断；
- App 退出、注销、任务取消和紧停时清理完整进程树；
- 把本地状态和人工接管信号映射成稳定前端模型。

Executor 连接本机或云端 Control Plane 时使用独立设备通道；React 不持有该通道凭据。

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

Playwright 使用真实本地测试 Control Plane 和受控 Executor Adapter，但不能证明真实浏览器或微信可用。

### 13.3 Rust 测试

- baseUrl Profile 校验；
- 安装实例凭据安全存储；
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

当前 F1-13 基线使用 `@wdio/tauri-service 1.2.0` embedded provider：`pnpm test:tauri` 构建带 `desktop-e2e` Cargo 特性的 debug App，并在真实 macOS WKWebView 中验证无登录工作台和 `main` 原生窗口。WDIO Rust/前端插件、`withGlobalTauri=true` 和测试 Capability 只存在于 `tauri.test.conf.json` 对应的测试构建；测试 Capability 以内联对象提供，不能放入 production 默认扫描的 `capabilities/` 目录。正常 Cargo 依赖树不启用两个可选 WDIO crate，生产 Vite 构建扫描 `wdioTauri`、WDIO IPC 和 WebDriver 端口标记并 fail closed。

`pnpm test:layers` 固定按 Vitest/契约、Playwright UI Harness、Rust、WebdriverIO 真实桌面四层执行。当前最小桌面冒烟证明真实 App、WKWebView、测试 IPC 插件和窗口查询可用，不证明后续 Control Plane 网络桥、Local Executor、外部运营浏览器或 RPA 可用；这些能力必须在对应任务新增自己的桌面用例。

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
