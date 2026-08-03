# Codex 停止交接（2026-08-03）

> **后续恢复状态（同日更新）**：本文件以下正文是 BM-15 收口时的历史停止快照，
> 不是当前 Git 状态。用户随后要求恢复本地 18 个提交并与周末远端工作对账：原本地历史
> 已保留并推送为 `reconcile/local-18-20260803@f9ac2c42`，验证后以 `--no-ff` 合回并
> 推送 `main@dbd56118`。当前新增待办只有全库 Python 覆盖率债，尚未开始补债实现；
> 精确基线、62 个文件清单与 COV-00～COV-06 执行顺序见
> `docs/development/2026-08-03-backend-coverage-debt-plan.md`。下一台 Codex 应从该文档
> 的 COV-00 开始，不要按下方旧快照继续横向开发 EB/PC/BM 功能。

用户可操作：否
证据类型：文档

> 状态：本会话已按用户要求停止。最后完成并推送的 roadmap 任务是 BM-15；没有启动
> 下一个任务，也没有保留进行中的 RED、人工操作或验收现场。

## 已完成并推送

- BM-15 已在提交 `3fd8bbde` 完成：134 项动效零件均可由模型选择和用户逐镜头覆盖，
  macOS 与 Windows 正式 App 都从正常入口选择此前不可用的 `motion-blur`，使用包内
  Chromium、Worker 与 FFmpeg 生成并播放 H.264 1920×1080 成片。
- BM-15 最终 Review 已修复三个 fragment host 未完整保留分镜文案，以及 `stop` /
  `stop_all` 吞掉 Windows 诊断文件清理失败的问题；随后进行了 31 分钟全改动复查，
  截至结束审查进程未提出新的代码 finding。Review 额外复跑 Chromium 单测时出现一次
  环境性挂起；同一文件此前已独立通过 7/7，双平台正式包验收也已通过，因此没有把
  该挂起写成产品通过
  或另开横向任务。
- EB-17 的描述已在提交 `0114e8c8` 改为只依赖正式包内置 Chromium，不再涉及 Edge
  或其他系统浏览器。
- EB-11 修复已在提交 `cbf16b09` 推送。macOS 正式候选已完成安全注销、用户扫码、
  登录复查、退出与同一 Profile 重启复用；Apple 公证上传尚未得到 staple，Windows
  正式 App 的真实扫码与完整生命周期也未做，所以 roadmap 准确保持 `🔍 待验收`。

## BM-15 正式包证据

### macOS

- 正式 App 正常入口：1 passing。
- H.264 1920×1080，729 帧，24.300 秒；AAC 24.286 秒。
- 镜头为 `motion-blur` 149 帧、`data-chart` 450 帧、`shimmer-sweep` 130 帧。
- 48 个时间采样得到 39 个不同摘要，无静帧拉伸复用。
- 本机证据（不入 Git）：
  `.local/embedded-browser-video-studio/pc-16-evidence/pc16-package-acceptance.json`。

### Windows

- 正式 NSIS 安装包 451,678,524 bytes；安装树审计 134 项、337 个目录文件，内置字体
  7,782,072 bytes。
- 可见正式 App 正常入口：1 passing；H.264 1920×1080，378 帧，12.600 秒；
  AAC 12.580 秒；首镜头 `motion-blur` 135 帧。
- 25 个时间采样全部不同；缺目录、缺 manifest、缺 sentinel 的失败矩阵均封闭拒绝。
- 卸载后安装根、注册表、相关进程、容器、任务和 `debug.log` 均为零；临时目录
  `F:\pc16bm15` 已定向删除。
- 验收快照 worktree：`F:\automation-tool\wt\codex-bm15-20260803`。它只用于保存
  本机不入 Git 的证据，后续开发必须以远端 `main` 的 `3fd8bbde` 或更新提交为准。

## 当前停止点

- `BM-15`：`✅ 已完成`。
- `BM-16`：`🔍 待验收`，仍缺 Windows Authenticode、真实低配机、休眠恢复和跨机像素
  一致性；这些没有横向并入 BM-15。
- `EB-11`：`🔍 待验收`，缺 Apple staple 与 Windows 真实扫码/生命周期证据。
- `EB-12` 及其后续任务：本会话没有启动。
- Shadowrocket 和系统 HTTP/HTTPS 代理未被关闭或修改。
- 本会话结束时不再持有任务、产品进程或等待中的人工操作。

## 新电脑接续

1. 完整阅读用户全局规则、仓库 `CLAUDE.md`、三份架构文档、本文件和
   `docs/embedded-browser-video-studio-roadmap.md`。
2. 获取远端 `main`，确认至少包含 BM-15 提交 `3fd8bbde` 及本交接提交；不要把 Windows
   验收快照 worktree 当作权威源码。
3. 按 roadmap 依赖选择最早可推进项；需要用户在 Windows 扫码的场景可以留待用户可
   配合时补验，不伪造通过，也不要因此横向扩展安全加固或额外产品功能。
4. 每完成一个任务，按仓库规则独立提交并推送。

## 明确停止

本会话已停止，不再继续 roadmap、开发、验收、重构或优化工作。
