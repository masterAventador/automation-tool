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

### 1. EB-02 共用 Chromium 验证（✅ 2026-07-24 已完成）

Windows 11 x86_64 实体机会话已按以下命令完成验证：

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

Chromium 149.0.7827.55 / revision 1228 的同一 `chrome.exe` 已同时通过 headed
Playwright、Browser Use `executable_path`、随机 loopback CDP 和独立 HyperFrames
渲染；三套并发 Profile、134 个目录项、12 套风格及字体/媒体/Canvas/WebGL/WebGPU/
透明/横竖屏矩阵全部通过，且没有第二套浏览器或进程残留。证据文件 SHA-256 为
`3bab78042c135143377fc9791ed2ed54cd4aa859a294fa99628e37761cdbb161`，浏览器可执行
文件 SHA-256 为 `b798f9e53a98d29eb7f36f8c409f905d3184780a04d2bcb56989067194784bd1`，
134+12 结果摘要为
`91c6bbc162cfc0cb2ed315cecd359b7ffdd65c3fe3be6f574a1b2e4e8abab8cf`。
`docs/development/EB-02.md` 与专项 Roadmap 已同步为 `✅ 已完成`，并解锁 EB-04/EB-05。

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

### 4. BM-02 动效 Node Worker（✅ 2026-07-24 已完成）

```bash
python scripts/run_bm_02_acceptance.py
```

Windows 11 x86_64 实体机会话下载摘要锁定的 Node 22.23.1 x64 官方归档，构建只含
随包 `node.exe`、许可证和 Worker 的临时候选；空 PATH 下版本、no-command、ready、
认证 health、UUIDv4/HMAC cancel、stop 和进程退出均通过。首轮修复清空环境时漏保留
`SYSTEMROOT/WINDIR` 导致 Node CSPRNG 初始化失败；第二轮发现 Unix-only Rust 目标在
Windows 实际为 0 tests，新增 Windows 原生测试并让 runner 强制要求
`1 passed; 0 failed`。Clippy `-D warnings` 与 212 项前端契约通过；候选、归档、随机
loopback 端口和进程均无残留，上游 submodule 零写入。

### 4.1 BM-03 共用 Chromium 渲染适配（✅ 2026-07-24 已完成）

Windows 11 x86_64 实体机会话执行：

```bash
python scripts/test_motion_video_render_adapter.py
python scripts/run_bm_03_acceptance.py --archive <EB-04 锁定的 chrome-win64.zip>
```

正式 runner 从 EB-04 摘要锁定归档现场暂存 308 个文件，以官方 Node 22.23.1 x64
候选启动生产 Worker，并从 Rust `LocalVideoOrchestrator` 经 CDP 管道验证真实
Chrome for Testing 149.0.7827.55。首轮 RED 发现 Windows GUI 子系统的
`chrome.exe --version` 会启动完整浏览器并挂起，且 `Browser.close` 已应答后根进程仍因
继承管道保持存活；实现改为 Windows 直接以已认证的 CDP `Browser.getVersion` 验证实际
major，收到 `Browser.close` 应答后使用绝对 `taskkill.exe /T /F` 定向清理本次自有树。
10 项 Worker 矩阵、真实 major 错配拒绝、NTFS junction 祖先拒绝、超时父子 PID 清理及
RenderJob/暂存路径零残留全部通过；Rust 两项原生门禁分别为
`1 passed; 0 failed`。

### 4.2 BM-04 HTML 渲染安全沙箱（✅ 2026-07-24 已完成）

Windows 11 x86_64 实体机会话执行：

```bash
python scripts/test_motion_video_render_sandbox.py
python scripts/run_bm_04_acceptance.py --archive <EB-04 锁定的 chrome-win64.zip>
```

正式 runner 从 EB-04 摘要锁定归档现场暂存 308 个文件，以官方 Node 22.23.1 x64
候选启动生产 Worker。Windows 进程树资源采样改为绝对系统 PowerShell +
`Win32_Process` CIM 的 `KernelModeTime`/`UserModeTime`/`WorkingSetSize`，按父 PID
闭包累计；固定 PE 夹具实测 CPU 与内存越界都在墙钟前返回
`render_resource_exceeded` 并清理整树。真实恶意 HTML 经 Rust→Node→Chromium 完成
3 张非空 PNG，失效代理和递归 auto-attach 下诱饵端口 0 命中，工作区外机密未变，
navigation/request/download/popup/dialog 五类计数均非零；Windows file URL 转换、
NTFS junction 工作区/入口/资产越界、墙钟父子 PID 强杀及 RenderJob/暂存路径零残留
全部通过。8 项 BM-04 Worker 矩阵、10 项 BM-03 回归和原生 Rust 纵向测试全绿。

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

### 6. BM-14 Windows 发布目录构建与只读属性验收（✅ 2026-07-24 已完成）

Windows 11 x86_64 实体机会话从锁定 URL 重建 BM-12 暂存，再连续两次运行
`build_motion_catalog_release.py` 并通过 `check_motion_catalog_release.py` 和完整测试。
首轮 RED 发现 Python 文本写入在 Windows 把生成内容转成 CRLF，导致同样 384 文件的
BM-12 聚合摘要漂移；所有生成文本、manifest 和 relock 写入现固定 LF。修正后 BM-12
聚合恢复锁定值 `128ea48c66685b2bbf8f0e8b0afaa9f27440cbeb85154b115d86ff4190336068`，
BM-14 两次都生成 134 items / 310 files / 150 asset replacements / 68 trademark items，
聚合与 macOS 锁定值
`38160d1cc3c17821e6df57036583ac3b22c08dba6a3364396db888d59bd50a63`
完全一致。

Windows 专项断言确认每个产物同时清除 `stat.S_IWRITE` 并带
`FILE_ATTRIBUTE_READONLY`，生成文本零 CRLF；非 ASCII `目录-Ångström/头像-É.svg`
可按原 Unicode 路径回读，大小写变体解析为同一 NTFS 文件。manifest 与构建器新增
casefold 路径碰撞拒绝，非提权 `mklink /J` reparse 夹具被门禁拒绝。第二轮 RED 还发现
clean submodule 的许可证工作树可被 `core.autocrlf` 物化为 CRLF；治理门禁现从锁定 commit
blob 计算许可证摘要，同时继续要求工作树为普通文件且 submodule 完全干净。所有 BM-12/
BM-13/BM-14 门禁、Ruff、212 项前端契约和专项 Roadmap 检查通过，临时发布/依赖树已清理。

## 注意

- 全程无头模式，不要跑出可见浏览器窗口（真实扫码类验收除外）；
- 每个任务的证据、状态行、代码改动必须同一提交；
- 结束后清理 `$TEMP` 下本次创建的 eb-02-* 等临时目录与浏览器进程。
