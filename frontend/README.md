# Desktop Frontend

本目录只构建 Tauri 桌面客户端的 React UI 资产。Vite 开发服务器用于本机联调和后续 Playwright UI Harness，不是 Web 产品，也没有静态站点部署入口。

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm typecheck
pnpm build
pnpm check:api
pnpm test:e2e
pnpm check:production-boundaries
pnpm dev
pnpm tauri dev
```

`pnpm dev` 只绑定 `127.0.0.1:1420`。`pnpm tauri dev` 会启动这个本机服务和真实桌面窗口。正式交付必须由 Tauri 打包，不能把 `dist/` 发布为用户入口。

当前 `src-tauri/capabilities/main.json` 不暴露任何 IPC 权限；后续每项原生能力必须随对应任务单独增加最小权限。`src-tauri/app-icon.svg` 是工程占位图标，不代表最终品牌设计。

API DTO 只能由 `../contracts/openapi/control-plane.v1.json` 生成：

```bash
pnpm generate:api
pnpm check:api
```

`src/api/generated/control-plane.ts` 禁止手改。后端契约先由 FastAPI 导出快照，前端再生成并检查逐字漂移。

`harness.html` 和 `src/test-harness/` 只供 Playwright 本机 UI 测试。正式 Vite 构建只以 `index.html` 为入口；`pnpm check:production-boundaries` 会重新构建并扫描产物，若发现 Harness 页面、运行标记或测试 Adapter 标记立即失败。UI Harness 通过只代表 React 交互，不代表 Tauri IPC、Rust、Sidecar 或 RPA 可用。
