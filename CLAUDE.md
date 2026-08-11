# 自动化运营工具项目规则

本项目当前是 **Demo 阶段**：以尽快交付可用功能为第一目标。不做防御性加固、预防性抽象、
流程门禁和验收台账；功能能在真实 App 里跑通、单元测试绿，就可以提交。

## 1. 项目结构

```text
automation-tool/
├── frontend/                 # React + TypeScript + Vite + Tauri v2
│   └── src-tauri/            # Rust 原生桥接、权限、Sidecar 生命周期和打包
├── backend/                  # Python：Control Plane（FastAPI）+ Local Executor
├── contracts/                # OpenAPI、事件 Schema、跨语言契约与资源锁
├── docs/                     # 架构与产品文档
├── scripts/                  # 构建、打包、部署脚本
├── workers/                  # 视频制作 Worker（随包分发）
├── vendor/                   # 第三方源码只读 Submodule（锁定 commit）
└── .local/                   # 本地运行数据，不进 Git
```

架构详情见 `docs/project-structure.md`、`docs/frontend-architecture.md`、
`docs/backend-architecture.md`，改动涉及对应领域时再读。

## 2. 技术架构要点

- **App**：Tauri v2 桌面客户端（唯一产品形态，无 Web 版）。React + TS + Vite + Ant Design；
  TanStack Query 管服务端状态，Zustand 管客户端状态；业务页面不直接 import `@tauri-apps/*`，
  统一走 `PlatformAdapter`。
- **Control Plane**：Python FastAPI 独立进程，PostgreSQL（本机 Docker）。产品 API、任务、事件。
- **Local Executor**：Python，跑在用户电脑，负责 Playwright RPA、浏览器 Profile、本地文件。
  以 Tauri Sidecar 随 App 打包，用户不用装 Python。
- **契约**：Pydantic 是 REST/事件/执行协议的唯一来源，改协议后重新生成，不手写第二份。
- **浏览器**：App 内置与 Playwright 版本匹配的 Chromium + App 私有运营 Profile + 可见可接管
  的运营窗口。不自动化用户日常浏览器，不读取用户浏览器 Cookie。
- **任务恢复**：任务快照是恢复的权威来源，事件只构建前端投影；外部副作用带幂等键。

## 3. 必须守住的底线（仅此三条）

1. **不做平台风控绕过**：验证码、滑块、风险提示一律暂停转人工；不做 stealth、指纹伪装、
   验证码识别。
2. **密钥与隐私**：Cookie、Token、平台消息、设备私钥不进日志、不进错误响应、不进
   `localStorage`；长期凭据只放 Tauri `app_data_dir` 下 Rust 管理的文件。
3. **vendor/ 只读**：两个 Submodule 锁定 commit，不在里面改代码；升级单独做。

## 4. 开发方式

- 新逻辑配单元测试（backend: pytest `tests/unit`；frontend: vitest；Rust: cargo test）。
  改哪个模块跑哪个模块的测试即可，不要求每次全量。
- 常用命令：
  - 前端：`pnpm dev` / `pnpm typecheck`（必须 `tsc -b`，`tsc --noEmit -p` 是空转）/
    `pnpm test` / `pnpm tauri:dev`
  - 后端：`backend/.venv/bin/python -m pytest tests/unit -q`；服务 `uv run` 启动
  - 打包：`pnpm release:package`（macOS；Windows 在 winbox 上跑同一脚本 `--platform windows`）
- 本地服务（Postgres 等）用 `compose.yaml` 起，用完关掉。
- 起本地服务前确认端口空闲；不接管来路不明的进程。
- 并行开发需要隔离时用 `python3 scripts/new_worktree.py <名称> [提交]` 建 worktree
  （放在 `wt/` 下），它会正确处理 vendor 和 LFS；不需要 vendor 的加 `--no-vendor`。
- 每块工作完成后提交并推送，commit message 用中文。
