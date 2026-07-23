# Windows x86_64 补证清单（2026-07-23）

> 背景：按用户决策，本项目不依赖 GitHub Actions；Windows 证据改为在用户的
> Windows 电脑上本机执行补齐。本清单对应各任务证据文件登记的待补项，命令与
> `.github/workflows/*.yml` 对应 Job 的步骤一致。执行完成后由 Windows 机器上的
> 会话把结果写入对应 `docs/development/<任务ID>.md` 并提交（一个任务一个提交）。

## 一次性环境准备

1. Git + Git LFS（`git lfs install`），克隆仓库：
   `git clone --recurse-submodules <repo>`（vendor 子模块与 LFS 大文件必须完整）；
2. [uv](https://docs.astral.sh/uv/)（负责 Python 版本与依赖）、Node 22 + pnpm、
   Rust stable（cargo，Tauri 测试需要）、Git Bash（跑 `.sh` 脚本）；
3. ffmpeg：`choco install ffmpeg --yes`；
4. 凭据（仅 IM-04 真实生成复验需要，可选）：按 `docs/credentials-bailian-model.md`
   把 JSON 抄到 `.local/secrets/bailian-model.json`。

## 按优先级执行

### 1. EB-02 共用 Chromium 验证（最优先，解锁 EB-04/EB-05 及后续）

Git Bash 中：

```bash
uv sync --project tools/shared-browser-validation --locked
pnpm --dir tools/shared-browser-validation install --frozen-lockfile
export PLAYWRIGHT_BROWSERS_PATH="$TEMP/eb-02-playwright"
uv run --project tools/shared-browser-validation --locked playwright install --no-shell chromium
# 确认没有第二套浏览器
find "$PLAYWRIGHT_BROWSERS_PATH" -type f \( -name 'headless_shell' -o -name 'chrome-headless-shell*' \) && echo "发现禁止的第二浏览器" || echo OK
uv run --project tools/shared-browser-validation --locked python \
  scripts/validate_shared_chromium.py \
  --browser-root "$PLAYWRIGHT_BROWSERS_PATH" \
  --platform-id windows-x86_64 \
  --artifacts-dir "$TEMP/eb-02-artifacts"
```

通过后：EB-02 → ✅；同时把 `$TEMP/eb-02-artifacts` 的关键输出摘录进 EB-02.md。

### 2. IM 线冻结 Worker（IM-02/03/04 ✅）

```bash
uv python install 3.11.15
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build        # cargo 测试需要 frontend/dist
python scripts/run_im_04_acceptance.py
python scripts/run_im_05_acceptance.py
```

IM-02 已于 2026-07-24 在 Windows 11 x86_64 完成两次原生冻结：4,383 files /
749,943,842 bytes、CPython 3.11.15、115 个锁定分发包、冷启动 2.782 秒；真实
`automation-tool-material-video-worker.exe` 启动、无参数拒绝、许可证清单、上游零写入和
清理均通过。首轮发现并修复了 macOS/Windows 依赖计数差异与 Windows 临时目录重试清理。

IM-03 已于 2026-07-24 用独立 Windows 候选完成生产 Rust 编排器的 ready HMAC、随机
loopback、认证 health/cancel、模型秘密投影和 Job Object 清理。补证发现并修复统一 Rust
bootstrap 新增 `renderBrowser: null` 后 Python 严格字段集未同步的问题；非空值仍拒绝。

IM-04 已于 2026-07-24 连续两次通过 Windows 原生冻结模型适配验收：4,383 files /
749,943,876 bytes、冷启动 2.808 秒；原生模型设置投影、公开状态脱敏、真实进程健康/停止和
上游 `config.toml` 零写入全部通过。真实百炼三个锁定模型调用已由 2026-07-23 的独立凭据
验收补齐。

IM-05 的 Windows 冻结 WebUI 与正常 App 入口也已于 2026-07-24 连续两次通过：最终候选
4,383 files / 749,944,181 bytes、冷启动 2.694 秒，真实 Streamlit、Tauri App 与
Edge/WebView2 150 的 1 个 WDIO spec 全绿。pnpm `.cmd` 解析、任务私有 home/cache/temp 和
`\\?\` 扩展路径兼容问题已修复。IM-05 仍只因 IM-08 承接的外部服务真实生成纵向证据保持待
验收，不再缺 Windows 技术链路。

### 3. VF-04 媒体工具链（✅ 2026-07-24 已完成）

已在 Windows 11 x86_64 的原生 MSYS2 MINGW64 环境从锁定源码构建 FFmpeg 8.1.2 和
x264 `b35605ace3ddf7c1a5d67a2eb553f034aef41d55`。两个程序均为 PE `0x8664`，普通
PowerShell 可直接启动且只导入系统 DLL；`ffmpeg.exe` SHA-256 为
`35824b9ec97389446ac4a1f64a0088ff632d9c7e2dd3ec13f40978c3c31cfc95`，
`ffprobe.exe` SHA-256 为
`7b83be67453c09e7129a4d9806afa1e4713ab1f8b6e454ad2c5466e7628b0494`。

`check_video_media_toolchain.py --candidate ... --target windows-x86_64` 已通过完整能力
矩阵、两条真实 H.264 MP4 编码和 ffprobe 对账；self-test 与
`run_vf_04_acceptance.py`（含 5 项 Rust fail-closed 测试）也全部通过。原生工具前缀、
`.exe` make 目标、相对输出目录、缺失动态运行时和非管理员 symlink 夹具问题均已修复并
建立回归约束。

### 4. BM-02 动效 Node Worker

```bash
python scripts/run_bm_02_acceptance.py
```

### 4.1 BM-03 共用 Chromium 渲染适配（依赖 EB-04 的 Windows 暂存）

EB-04 完成 Windows 暂存后执行：

```bash
python scripts/test_motion_video_render_adapter.py
python scripts/run_bm_03_acceptance.py --archive <EB-04 锁定的 chrome-win64.zip>
```

补证要点：Worker 对无头 Chromium 的 detached 进程组终止在 Windows 上的语义
（`process.kill(-pid)` 不适用，需验证 Job Object/进程树清理路径）、可执行路径
reparse point 校验，以及真实 `chrome.exe` 的 CDP 管道 getVersion/Browser.close
干净退出。通过后更新 `docs/development/BM-03.md` 遗留项。

### 4.2 BM-04 HTML 渲染安全沙箱（依赖 EB-04 的 Windows 暂存）

EB-04 完成 Windows 暂存后执行：

```bash
python scripts/test_motion_video_render_sandbox.py
python scripts/run_bm_04_acceptance.py --archive <EB-04 锁定的 chrome-win64.zip>
```

补证要点：资源上限的进程组采样在 Windows 无 `/bin/ps`，需替换为 Job Object 内存/CPU
统计或等价 API 并重验 CPU/内存超限强杀；detached 进程组终止改用 Windows Job Object；
失效代理 `--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback>` 与子帧
`Target.setAutoAttach` 递归拦截在 Windows 上重验诱饵端口 0 命中；入口/资产路径的
reparse point 越界校验；真实恶意 HTML 的 navigation/download/popup/dialog 拦截与
工作区外机密不可读。通过后更新 `docs/development/BM-04.md` 遗留项。

### 5. EB-04 Windows 浏览器构建暂存（✅ 2026-07-24 已完成）

已从预登记官方地址下载 `chrome-win64.zip`（192,511,857 bytes），真实 SHA-256
`ebc0c2b75e2ea98151a7f18ff47037bfcbab44a8660e79b9ffa6520f9b7607ab` 已写入
`contracts/browser/embedded-chromium-staging.v1.json`，`windows-x86_64.buildable`
已翻为 `true`。`run_eb_04_acceptance.py` 已在 Windows 11 x86_64 完成双暂存可复现
Manifest、308 files / 435,574,347 bytes、PE `0x8664`、Playwright 离线启动
149.0.7827.55 和进程零残留验收。

### 5.1 EB-05 单一浏览器发行物 Manifest（✅ 2026-07-24 已完成）

`run_eb_05_acceptance.py` 已扩展为按宿主选择原生目标，并在 Windows 11 x86_64 对 EB-04
锁定归档完成 308 files / 435,574,347 bytes 的 Manifest 生成与逐文件摘要复验；故意篡改
`chrome.exe` 后默认强锁校验拒绝，恢复后临时树清理。

### 5.2 EB-06 Rust 内置发行物解析与验证（✅ 2026-07-24 已完成）

`run_eb_06_acceptance.py` 已扩展为按宿主选择原生目标与 Rust 测试。在 Windows 11
x86_64 使用 EB-04 锁定归档现场生成 308 files / 435,574,347 bytes 的发行物 Manifest，
生产 Rust `EmbeddedBrowserDistribution::load_for_target` 在 4.63 秒内完成逐文件复验
并解析真实 `chrome.exe`。确定性矩阵同时通过 AMD64 PE `0x8664`、macOS 契约错配拒绝
和非提权 `mklink /J` 创建的真实 NTFS junction 根拒绝；验收结束恢复 fixture 并清理
全部临时发行物。

### 5.3 EB-07 Executor 启动协议迁移（✅ 2026-07-24 已完成）

Windows 11 x86_64 使用 EB-04 锁定归档完成 308 files / 435,574,347 bytes 的真实
Authority 全量解析与缓存复解析（4.53 秒），同一 `chrome.exe` 经生产 Python
`BrowserLaunchRequest.revalidate()` 接受且 repr 不泄露路径。验收真实复现并修复
“首次缓存后发行物根被换成 NTFS junction 仍返回旧路径”的缺口；现在缓存命中前复查
resource/发行物/可执行路径 reparse 边界，异常强制全量验证并固定拒绝。Windows
原生确定性矩阵 8 passed / 1 ignored，临时 junction 与发行物均已清理。

### 5.4 EB-08 启动健康状态迁移（✅ 2026-07-24 已完成）

Windows 11 x86_64 通过隔离隐藏 Tauri App 的正常启动入口验证真实组件缺失态：
正式 IPC 返回 `appData=ready / executor=ready / embeddedBrowser=component_missing`，
页面显示“浏览器组件缺失”和“请重新安装官方客户端；无需也不要单独安装其他浏览器”。
首次 RED 证明旧 WDIO 仍尝试选择系统 Chrome/Edge；修正后打开修复工具只显示执行器
诊断、没有浏览器选择入口，重新检查仍保持工作台封锁。WebdriverIO 1 passing（4.7 秒）；
runner 已清理本次签名 Executor、AppData、App 进程并确认两个隔离端口关闭。

### 5.5 EB-09 全新内置 Profile 契约（✅ 2026-07-24 已完成）

Windows 11 x86_64 实体机会话已完成新 `embedded-browser-profiles` 根的 12 项生命周期
测试和 8 项锁测试：真实 NTFS 私有 DACL、UUIDv4 稳定路径、首次创建/重开复用、
junction/reparse/普通文件替换拒绝、并发创建、跨进程排他、持锁进程强杀恢复、活动
lease 禁删、安全 tombstone 删除和 crash resume 全部通过。

首轮 RED 发现 Windows 删除阶段名被通用安全名称校验误拒、普通锁句柄允许
rename/delete replacement，以及持有后代锁文件句柄时目录不可 rename。生产实现现在
仅接受精确 `.removing-<safe-id>` 内部名称，普通运行锁拒绝 delete sharing；删除流程
关闭句柄前保留 durable active fence，rename 后通过恢复型删除锁清除。原目录与
tombstone 同时存在稳定返回 `RecoveryRequired`，不会因 tombstone 的继承 DACL 提前
改变错误语义。Clippy `-D warnings`、212 项前端契约与专项 Roadmap self-test 均通过。

### 5.6 EB-10 删除生产浏览器选择链路（✅ 2026-07-24 已完成）

Windows 11 x86_64 实体机会话重新运行正式启动环境 runner：独立签名测试 Executor、
隐藏真实 Tauri App 与 WebdriverIO 1/1 通过（4.8 秒）。从 App 正常启动门禁进入
“打开本地修复工具”，确认没有浏览器选择卡片、Google Chrome/Microsoft Edge 选项、
保存选择或当前选择；正式 IPC 只报告内置浏览器组件缺失，并提示重装官方客户端。
WebView2/Edge 仅作为测试驱动承载 App WebView，不是运营浏览器发现或 fallback。

48 文件/307 项 React 测试、typecheck、lint、212 项契约、品牌扫描和专项 Roadmap
self-test 全绿。Runner 清理签名 Executor、隔离 AppData、App 进程及两个临时端口。

### 5.7 BU-02 单一 Chromium 双模式适配（✅ 2026-07-24 已完成）

Windows 11 x86_64 实体机会话使用 EB-04 锁定归档现场暂存 308 文件 /
435,574,347 bytes。同一发行物先由生产 Rust `EmbeddedBrowserAuthority` 完成 Manifest
和逐文件摘要验证（4.60 秒），再由 Browser Use 0.13.6 完成两种真实模式：已验证
`chrome.exe` + fresh Profile 独立启动，以及同一二进制随机 loopback CDP 接管；两次
均命中本机随机端口 fixture，CDP 版本为 `Chrome/149.0.7827.55`。

首轮 RED 修复 POSIX execute-bit 判据在 Windows 拒绝合法 PE 的问题，改为 Windows
`.exe` + `MZ` 入口校验，完整 PE/AMD64/摘要继续由 Rust Authority 保证。第二轮 RED
发现宿主 SOCKS 代理变量会让锁定环境因缺少可选 `socksio` 在启动前失败；harness 现在
大小写无关地剥离 HTTP/HTTPS/ALL/NO_PROXY，同时继续关闭 Cloud、遥测并移除 Cloud
凭据。Windows 外部 CDP 树按本次创建的 PID 定向清理；10 项 harness、Browser Use API
self-test、ruff 与 212 项前端契约全部通过，无临时 Profile、端口或进程残留。

### 6. BM-14 Windows 发布目录构建与只读属性验收

macOS 已完成 134 项离线目录发布合成的全部确定性门禁（构建可复现、逐文件摘要、只读
0444、150 处素材替换与商标指示词零残留）。Windows 侧待补：在 Windows 机器上重跑
`scripts/build_motion_catalog_release.py` + `scripts/check_motion_catalog_release.py`，
验证 NTFS 上的只读属性语义（`stat.S_IWRITE` 位与 `chmod 0444` 映射）、路径大小写与
Unicode 文件名行为，以及聚合摘要与 macOS 结果一致。通过后更新
`docs/development/BM-14.md` 遗留项。

## 注意

- 全程无头模式，不要跑出可见浏览器窗口（真实扫码类验收除外）；
- 每个任务的证据、状态行、代码改动必须同一提交；
- 结束后清理 `$TEMP` 下本次创建的 eb-02-* 等临时目录与浏览器进程。
