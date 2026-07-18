# Desktop Frontend

本目录只构建 Tauri 桌面客户端的 React UI 资产。Vite 开发服务器用于本机联调和后续 Playwright UI Harness，不是 Web 产品，也没有静态站点部署入口。

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm typecheck
pnpm build
pnpm check:api
pnpm test:ui
pnpm test:rust
pnpm test:tauri
pnpm test:layers
pnpm check:production-boundaries
pnpm dev
pnpm tauri dev
```

`pnpm dev` 只绑定 `127.0.0.1:1420`。`pnpm tauri dev` 会启动这个本机服务和真实桌面窗口。正式交付必须由 Tauri 打包，不能把 `dist/` 发布为用户入口。

当前 `src-tauri/capabilities/main.json` 不暴露任何 IPC 权限；后续每项原生能力必须随对应任务单独增加最小权限。`src-tauri/app-icon.svg` 是工程占位图标，不代表最终品牌设计。

设备身份和长期设备凭据只由 Rust 管理。正式 App 首启使用系统 CSPRNG 生成 Ed25519 私钥，并保存到 Tauri `app_data_dir` 下的固定 App 私有文件；长期 `atdc1` 凭据使用同一存储边界。目录在 Unix 为 `0700`、文件为 `0600`，写入使用同目录临时文件、落盘同步和原子替换；Windows 使用当前用户 AppData 继承的私有 ACL。React、Tauri Command、普通配置文件和 `localStorage` 均没有密钥或长期凭据读写面，也不调用 macOS Keychain 或 Windows Credential Manager，因此不会产生系统钥匙串授权提示。已存在的 32 字节私钥只复用不轮换，凭据可替换和删除；符号链接、非法权限、内容损坏、存储拒绝或随机源失败均 fail closed。`desktop-e2e` 构建只使用不落盘的临时身份，App 私有存储由 Rust 行为测试和正式 Tauri 启动验收覆盖。

正式 `TauriControlPlaneTransport` 只调用注册过的 `check_control_plane_health` Command；Task 服务端状态由 `TauriTaskProjectionSource` 通过固定 `get_task_snapshot`、`list_task_snapshots` 和 `stream_task_projection_events` 消费。请求由 Rust `reqwest` 客户端从固定 local origin 发出，封闭 operation allowlist 覆盖当前 Installation 访问探针、Installation challenge/complete、凭据轮换/吊销、两类 Session 换票，以及 Task 创建、列表、详情、事件 SSE 和 pause/resume/cancel/emergency-stop，禁止 React 传入任意 URL、Header 或 bearer。Task 快照严格校验 `status/revision/lastEventSequence`、UTC 时间、降序稳定性和 opaque cursor；SSE 校验 request ID、响应头、连续序号、封闭版本/类型/状态、UUIDv4、UTC 时间、结构化进度和资源上限，并在 Rust 每解析一条事件时立即推送 Tauri Channel。TanStack Query 每次连接/恢复先读权威快照，Zod 与纯 Reducer 再做重复去重、缺口回拉和未知版本/类型有限降级；不建立 WebView EventSource 或第二事实源。T3-17 再把创建能力接入受约束表单，T3-16/T3-18 再消费投影和控制能力构建页面。请求禁止系统代理和重定向；设备签名及凭据注入只在 Rust 内完成。

API DTO 只能由 `../contracts/openapi/control-plane.v1.json` 生成：

```bash
pnpm generate:api
pnpm check:api
```

`src/api/generated/control-plane.ts` 禁止手改。后端契约先由 FastAPI 导出快照，前端再生成并检查逐字漂移。

Executor v1 使用 `src/api/protocol/executor-envelope.ts` 的 Zod 判别联合和 `src-tauri/src/executor_protocol.rs` 的 Rust 解析器。两者与 Python `parse_executor_message` 回放 `../contracts/fixtures/executor-v1/` 的同一组 6 个 valid、25 个 invalid 原始 wire 文件；未知字段/类型、重复 key、非规范 UUIDv4、非 UTC 或微秒倒序 deadline、超出 JavaScript 安全整数的 sequence、任务作用域混淆、资源滥用和敏感 payload 都 fail closed。TypeScript/Rust 解析错误只返回固定公开信息，不反射原始输入；I2-13 已在 Control Plane 侧让真实 WebSocket 帧经过 Python 正式入口，E4-02/E4-12 再接入 Local Executor 进程，React 只消费公开投影。

`harness.html` 和 `src/test-harness/` 只供 Playwright 本机 UI 测试。正式 Vite 构建只以 `index.html` 为入口；`pnpm check:production-boundaries` 会重新构建并扫描产物，若发现 Harness 页面、运行标记或测试 Adapter 标记立即失败。UI Harness 通过只代表 React 交互，不代表 Tauri IPC、Rust、Sidecar 或 RPA 可用。

四层桌面测试命令分别是：`pnpm test:unit`（Vitest）、`pnpm test:ui`（Playwright UI Harness）、`pnpm test:rust`（Rust）和 `pnpm test:tauri`（真实 Tauri + WebdriverIO）；`pnpm test:layers` 顺序执行全部层级。`test:tauri` 只构建带 `desktop-e2e` Cargo 特性、测试专用前端入口和内联测试 Capability 的 debug App；正常构建仍保持 `withGlobalTauri=false`，正式 Cargo 依赖树不启用 WDIO 插件，生产资产扫描也拒绝 WDIO 标记。所有自动化 Tauri 构建都通过测试专用配置把主窗口设为 `visible=false`，在后台运行且不抢占用户前台；正式 `tauri.conf.json` 不包含这个覆盖，产品窗口正常可见。

I2-09 的生产同路径纵向验收必须从仓库根的 Python 3.12/uv 后端环境执行：

```bash
cd backend
uv run python ../scripts/run_i2_09_acceptance.py
```

脚本会使用随机密码和端口启动隔离 PostgreSQL、执行正式 Alembic 链、启动真实 FastAPI，再由隐藏的真实测试版 Tauri App 经正式 Rust 桥完成 Health → 注册 → App Session → 轮换 → Executor Session → 吊销。最后同时核对 App 私有目录和 PostgreSQL 最终状态，并回收服务、容器、卷及精确隔离 App 数据目录；直接运行 HTTP 客户端或 UI Harness 不能替代该验收。

I2-14 的 Installation 吊销纵向验收同样必须从 uv 后端环境执行 `uv run python ../scripts/run_i2_14_acceptance.py`。它会验证测试配置的主窗口为 `visible=false`，再由隐藏真实 App 完成注册和受保护访问，由服务器运维 CLI 原子吊销 Installation，最后让同一 App 从正式启动入口进入“当前安装实例已失效”；长期凭据只保留在隔离 `app_data_dir` 私有文件中且不经 React/IPC 返回，结束后精确清理。

T3-06 的创建任务纵向验收执行 `uv run python ../scripts/run_t3_06_acceptance.py`。它先校验专用 Tauri 配置只有一个 `visible=false` 主窗口，再由隐藏真实 App 经正式 Rust 客户端完成 Installation 注册、两次 App Session 换票和同键创建/重放；最终核对 App 私有身份/凭据权限、PostgreSQL 只有一条 draft Task，结束后清理隔离 App 数据、Uvicorn、容器、卷和端口。直接 HTTP、TestClient、Mock 或 UI Harness 不能替代该验收。

T3-07 的任务查询纵向验收执行 `uv run python ../scripts/run_t3_07_acceptance.py`。脚本先预置另一个 Installation 的 Task，再由唯一 `visible=false` 真实 App 经正式 Rust 客户端完成注册、创建三个自有 Task、2+1 游标分页、详情读取和跨 Installation 不可见检查；最终核对两个 Installation、四条 Task、七张正式 App Session、App 私有身份/凭据权限及预置数据未被改动，结束后精确清理。直接 HTTP、TestClient、Mock 或 UI Harness 不能替代该验收。

T3-12 的事件流纵向验收执行 `uv run python ../scripts/run_t3_12_acceptance.py`。唯一 `visible=false` 真实 App 从私有 vault 换取 App Session，在 Executor 事件到达前经正式 Rust 客户端建立 SSE，读取序号 1、2 后主动断开，再以新 Session 和 `Last-Event-ID: 2` 续拉 3、4、5，核对 progress 50 与终态关闭；FakeExecutor 同时经正式 Session/WebSocket 产生持久事件。验收回收 App 数据、Uvicorn、PostgreSQL 容器/卷和端口，不弹窗、不抢焦点；直接 HTTP、TestClient、Mock 或 UI Harness 不能替代该产品入口证据。

T3-13 的暂停/恢复纵向验收执行 `uv run python ../scripts/run_t3_13_acceptance.py`。唯一 `visible=false` 真实 App 经正式 Rust `pause_task`/`resume_task` 写入命令，并通过同一 Rust SSE 客户端等待 `task.paused`/`task.resumed`；HOLD FakeExecutor 从正式 Session/WebSocket 处理 offer、pause、resume。验收核对命令 sequence 1/2/3 全部 acknowledged、事件 sequence 1..4、Task 最终 running/revision 5，并回收 App 私有测试目录、Uvicorn、PostgreSQL 容器/卷和端口，全程不弹窗、不抢焦点。

T3-14 的取消/紧停纵向验收执行 `uv run python ../scripts/run_t3_14_acceptance.py`。唯一 `visible=false` 真实 App 依次创建两个 Task，并从正式 Rust `cancel_task`/`emergency_stop_task` 发出请求；同一 HOLD FakeExecutor 从正式 Session/WebSocket 分别回报 `task.cancelled` 与 `task.outcome_uncertain`。验收核对每个 Task 的 offer/control 命令均 acknowledged、事件 sequence 1..3、最终 revision 5，并清理 App 私有目录、服务、容器、卷和端口，全程不弹窗、不抢焦点。

T3-15 的 Query/事件投影纵向验收执行 `uv run python ../scripts/run_t3_15_acceptance.py`。唯一 `visible=false` 真实 App 在 WebView 中调用正式 TypeScript source：先用 TanStack Query 经 Rust 拉取带水位的 Task 快照，再从同一 Rust SSE 通过 Tauri Channel 实时接收事件，由正式 Reducer 收敛 sequence 1..5 到 succeeded。FakeExecutor 经正式 Session/WebSocket 产生事件；验收核对最终 revision/watermark、App 私有身份/凭据权限，并清理 App、服务、容器、卷和端口，全程不弹窗、不抢焦点。

`@wdio/tauri-service 1.2.0` 的发布清单仍将 `@wdio/native-utils` 固定在缺少其已调用导出的 2.4.0，因此 `pnpm-workspace.yaml` 通过官方依赖 override 固定到已提供该导出的 2.5.0；未修改任何第三方源码。当前 embedded provider 的成功测试仍会输出两条上游诊断噪声：误检查外部 `tauri-driver`，以及会话销毁后清理空 mock；两者不影响真实 WKWebView 会话和测试结果，项目不会因此安装未使用的外部驱动。

CI 在 `quality.yml` 的 Ubuntu Frontend/Rust job 重放契约、单测、Lint、类型、UI Harness、生产边界，以及默认、`desktop-e2e`、`control-plane-e2e` 三种 Cargo 配置检查；`desktop.yml` 再用 GitHub Hosted macOS/Windows 构建 production debug binary，并运行后台隐藏窗口的真实 Tauri 冒烟。两条工作流都只有 `contents: read`，不读取 secret、不上传安装包，也不执行发布或部署。
