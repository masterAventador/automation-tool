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

正式 `TauriControlPlaneTransport` 只调用注册过的 `check_control_plane_health` Command；请求由 Rust `reqwest` 客户端从固定 local origin 发出。Rust 网络层的封闭 operation allowlist 还覆盖 Installation challenge/complete、凭据轮换/吊销和两类 Session 换票，禁止 React 传入 URL、路径、Header 或 bearer。请求禁止系统代理和重定向，具有固定连接/总超时、64 KiB 响应上限、规范 UUIDv4 关联 ID、严格 JSON DTO 与 `no-store` 校验；设备签名及凭据注入只在 Rust 内完成。

API DTO 只能由 `../contracts/openapi/control-plane.v1.json` 生成：

```bash
pnpm generate:api
pnpm check:api
```

`src/api/generated/control-plane.ts` 禁止手改。后端契约先由 FastAPI 导出快照，前端再生成并检查逐字漂移。

`harness.html` 和 `src/test-harness/` 只供 Playwright 本机 UI 测试。正式 Vite 构建只以 `index.html` 为入口；`pnpm check:production-boundaries` 会重新构建并扫描产物，若发现 Harness 页面、运行标记或测试 Adapter 标记立即失败。UI Harness 通过只代表 React 交互，不代表 Tauri IPC、Rust、Sidecar 或 RPA 可用。

四层桌面测试命令分别是：`pnpm test:unit`（Vitest）、`pnpm test:ui`（Playwright UI Harness）、`pnpm test:rust`（Rust）和 `pnpm test:tauri`（真实 Tauri + WebdriverIO）；`pnpm test:layers` 顺序执行全部层级。`test:tauri` 只构建带 `desktop-e2e` Cargo 特性、测试专用前端入口和内联测试 Capability 的 debug App；正常构建仍保持 `withGlobalTauri=false`，正式 Cargo 依赖树不启用 WDIO 插件，生产资产扫描也拒绝 WDIO 标记。所有自动化 Tauri 构建都通过测试专用配置把主窗口设为 `visible=false`，在后台运行且不抢占用户前台；正式 `tauri.conf.json` 不包含这个覆盖，产品窗口正常可见。

I2-09 的生产同路径纵向验收必须从仓库根的 Python 3.12/uv 后端环境执行：

```bash
cd backend
uv run python ../scripts/run_i2_09_acceptance.py
```

脚本会使用随机密码和端口启动隔离 PostgreSQL、执行正式 Alembic 链、启动真实 FastAPI，再由隐藏的真实测试版 Tauri App 经正式 Rust 桥完成 Health → 注册 → App Session → 轮换 → Executor Session → 吊销。最后同时核对 App 私有目录和 PostgreSQL 最终状态，并回收服务、容器、卷及精确隔离 App 数据目录；直接运行 HTTP 客户端或 UI Harness 不能替代该验收。

`@wdio/tauri-service 1.2.0` 的发布清单仍将 `@wdio/native-utils` 固定在缺少其已调用导出的 2.4.0，因此 `pnpm-workspace.yaml` 通过官方依赖 override 固定到已提供该导出的 2.5.0；未修改任何第三方源码。当前 embedded provider 的成功测试仍会输出两条上游诊断噪声：误检查外部 `tauri-driver`，以及会话销毁后清理空 mock；两者不影响真实 WKWebView 会话和测试结果，项目不会因此安装未使用的外部驱动。

CI 在 `quality.yml` 的 Ubuntu Frontend/Rust job 重放契约、单测、Lint、类型、UI Harness、生产边界，以及默认、`desktop-e2e`、`control-plane-e2e` 三种 Cargo 配置检查；`desktop.yml` 再用 GitHub Hosted macOS/Windows 构建 production debug binary，并运行后台隐藏窗口的真实 Tauri 冒烟。两条工作流都只有 `contents: read`，不读取 secret、不上传安装包，也不执行发布或部署。
