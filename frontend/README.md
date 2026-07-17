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

设备身份由 `src-tauri/src/device_identity.rs` 在 Rust 进程内管理。正式 App 首启使用系统 CSPRNG 生成 Ed25519 私钥，并以二进制 Secret 保存到 macOS Keychain 或 Windows Credential Manager；React、Tauri Command、普通配置文件和 `localStorage` 均没有私钥读写面。已存在的 32 字节私钥只复用不轮换，存储损坏、权限拒绝或随机源失败均 fail closed。`desktop-e2e` 构建只使用不落盘的临时身份，真实系统安全存储另由 Rust 平台测试完成往返和删除。

API DTO 只能由 `../contracts/openapi/control-plane.v1.json` 生成：

```bash
pnpm generate:api
pnpm check:api
```

`src/api/generated/control-plane.ts` 禁止手改。后端契约先由 FastAPI 导出快照，前端再生成并检查逐字漂移。

`harness.html` 和 `src/test-harness/` 只供 Playwright 本机 UI 测试。正式 Vite 构建只以 `index.html` 为入口；`pnpm check:production-boundaries` 会重新构建并扫描产物，若发现 Harness 页面、运行标记或测试 Adapter 标记立即失败。UI Harness 通过只代表 React 交互，不代表 Tauri IPC、Rust、Sidecar 或 RPA 可用。

四层桌面测试命令分别是：`pnpm test:unit`（Vitest）、`pnpm test:ui`（Playwright UI Harness）、`pnpm test:rust`（Rust）和 `pnpm test:tauri`（真实 Tauri + WebdriverIO）；`pnpm test:layers` 顺序执行全部层级。`test:tauri` 只构建带 `desktop-e2e` Cargo 特性、测试专用前端入口和内联测试 Capability 的 debug App；正常构建仍保持 `withGlobalTauri=false`，正式 Cargo 依赖树不启用 WDIO 插件，生产资产扫描也拒绝 WDIO 标记。

`@wdio/tauri-service 1.2.0` 的发布清单仍将 `@wdio/native-utils` 固定在缺少其已调用导出的 2.4.0，因此 `pnpm-workspace.yaml` 通过官方依赖 override 固定到已提供该导出的 2.5.0；未修改任何第三方源码。当前 embedded provider 的成功测试仍会输出两条上游诊断噪声：误检查外部 `tauri-driver`，以及会话销毁后清理空 mock；两者不影响真实 WKWebView 会话和测试结果，项目不会因此安装未使用的外部驱动。

CI 在 `quality.yml` 的 Ubuntu Frontend/Rust job 重放契约、单测、Lint、类型、UI Harness、生产边界和两种 Cargo 特性检查；`desktop.yml` 再用 GitHub Hosted macOS/Windows 构建 production debug binary，并运行真实 Tauri 冒烟。两条工作流都只有 `contents: read`，不读取 secret、不上传安装包，也不执行发布或部署。
