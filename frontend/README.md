# Desktop Frontend

本目录只构建 Tauri 桌面客户端的 React UI 资产。Vite 开发服务器用于本机联调和后续 Playwright UI Harness，不是 Web 产品，也没有静态站点部署入口。

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm typecheck
pnpm build
pnpm dev
```

`pnpm dev` 只绑定 `127.0.0.1:1420`。正式交付必须由 Tauri 打包，不能把 `dist/` 发布为用户入口。
