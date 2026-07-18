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

正式 `TauriControlPlaneTransport` 只调用注册过的 `check_control_plane_health` Command；请求由 Rust `reqwest` 客户端从固定 local origin 发出。Rust 网络层的封闭 operation allowlist 还覆盖当前 Installation 访问探针、Installation challenge/complete、凭据轮换/吊销、两类 Session 换票，以及 Task 创建、固定列表路径和经 UUIDv4 校验的详情路径，禁止 React 传入任意 URL、Header 或 bearer。Task 操作均由 Rust 从 App 私有 vault 换取短期 App Session：创建只接受 201/200 同形快照；查询严格校验公开状态、UTC 时间、降序稳定性和 opaque cursor，详情不能借错误枚举其他 Installation。T3-17 再把创建能力接入受约束表单，T3-15/T3-16 再接查询投影；当前不暴露通用业务 IPC。请求禁止系统代理和重定向，具有固定连接/总超时、64 KiB 响应上限、规范 UUIDv4 关联 ID、严格 JSON DTO 与 `no-store` 校验；设备签名及凭据注入只在 Rust 内完成。未注册 App 仍直接进入工作台；已有长期凭据时启动命令会换取 `app.control-plane` Session 并检查访问，只有精确 401 映射为独立 Installation 失效诊断。

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

`@wdio/tauri-service 1.2.0` 的发布清单仍将 `@wdio/native-utils` 固定在缺少其已调用导出的 2.4.0，因此 `pnpm-workspace.yaml` 通过官方依赖 override 固定到已提供该导出的 2.5.0；未修改任何第三方源码。当前 embedded provider 的成功测试仍会输出两条上游诊断噪声：误检查外部 `tauri-driver`，以及会话销毁后清理空 mock；两者不影响真实 WKWebView 会话和测试结果，项目不会因此安装未使用的外部驱动。

CI 在 `quality.yml` 的 Ubuntu Frontend/Rust job 重放契约、单测、Lint、类型、UI Harness、生产边界，以及默认、`desktop-e2e`、`control-plane-e2e` 三种 Cargo 配置检查；`desktop.yml` 再用 GitHub Hosted macOS/Windows 构建 production debug binary，并运行后台隐藏窗口的真实 Tauri 冒烟。两条工作流都只有 `contents: read`，不读取 secret、不上传安装包，也不执行发布或部署。
