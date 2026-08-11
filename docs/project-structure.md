# 项目结构

> 2026-08-11 起项目转入 Demo 阶段，原门禁/验收/台账体系已整体移除。本文只描述现存结构。

```text
automation-tool/
├── frontend/                      # React + TypeScript + Vite + Tauri v2
│   ├── src/                       # 业务 UI（features/ 按领域分模块）与单元测试（vitest）
│   │   ├── app/                   # 工作台外壳、路由
│   │   ├── features/              # 任务、平台会话、视频工作室、剪辑、发布、法务声明等
│   │   ├── platform/              # PlatformAdapter：Tauri 桥 / 浏览器 Harness 两套实现
│   │   └── test-harness/          # 开发用 UI Harness 入口（非产品入口）
│   ├── scripts/openapi.mjs        # 从后端 OpenAPI 生成 TS 类型
│   └── src-tauri/                 # Rust：命令、权限、Sidecar 生命周期、打包
│       ├── src/                   # 各领域桥接模块（executor、浏览器、视频、更新等）
│       ├── tests/                 # cargo 集成测试
│       ├── tauri.conf.json        # 正式配置
│       ├── tauri.dev.conf.json    # 本地开发配置
│       └── tauri.{macos,windows}-candidate.conf.json  # 打包候选配置（打包脚本引用）
├── backend/                       # Python 3.12 + uv
│   ├── src/automation_tool/
│   │   ├── control_plane/         # FastAPI：产品 API、任务、事件、账号（待替换为统一登录）
│   │   └── executor/              # 本地执行器：Playwright RPA、技能回放、视频、字幕
│   ├── tests/unit/                # 单元测试（唯一保留的后端测试层）
│   ├── automation-tool-executor.spec  # PyInstaller 冻结配置
│   └── compose.yaml 相关          # 本地 PostgreSQL
├── contracts/                     # 跨端契约与资源锁（运行时/构建真实读取的才保留）
│   ├── openapi/ protocol/         # Control Plane API 与执行协议（Pydantic 导出）
│   ├── browser/                   # 内置 Chromium 暂存与兼容性契约
│   ├── browser-use/ publishing/   # Browser Use 与发布契约
│   ├── video/ quality/            # 视频目录、资产权利、打包资源锁
│   └── security/ deployment/      # 仍被代码引用的少量契约
├── workers/                       # 随包分发的视频 Worker（material_montage、motion_composition）
├── scripts/                       # 39 个功能脚本：构建、打包、部署、缓存、worktree
│   ├── build_release_package.py   # 正式打包入口（pnpm release:package）
│   ├── release_assembly.py / release_configuration.py / release_identity.py
│   ├── run_p9_03_acceptance.py / run_p9_04_acceptance.py / run_eb_16_windows_acceptance.py
│   │                              # 名字像验收，实为 mac/Windows 打包函数库，勿删
│   ├── build_embedded_chromium_staging.py / embedded_browser_*.py  # 内置浏览器
│   ├── build_motion_*.py / build_offline_motion_catalog.py         # 动效目录
│   ├── prepare_video_runtime.py / video_runtime_cache.py           # 视频运行时
│   ├── new_worktree.py            # 建 worktree（正确处理 vendor 与 LFS）
│   └── deploy_customer_demo.py / customer_demo_release.py          # Demo 部署
├── tools/                         # browser-use-contract、motion-authoring 辅助工程
├── vendor/                        # 第三方源码只读 Submodule（hyperframes、moneyprinterturbo）
├── docs/                          # 架构、产品、运维文档
└── .local/                        # 本地运行数据与构建产物，不进 Git
```

## 依赖方向

- `frontend/src` 只经 `PlatformAdapter` 触达原生；不直接 import `@tauri-apps/*`
- `backend/control_plane` 不依赖 Tauri、Playwright 或本机路径；`executor` 才碰浏览器和文件
- 契约由 Pydantic 导出（`check:api` / `generate:api`），不手写第二份
- `scripts/` 可以 import 同目录模块与 `backend/src`，反向禁止
