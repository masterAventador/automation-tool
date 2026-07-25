# Windows x86_64 补证清单（2026-07-23）

> 背景：按用户决策，本项目不依赖 GitHub Actions；Windows 证据改为在用户的
> Windows 电脑上本机执行补齐。本清单原对应各任务证据文件登记的 Windows 验收项，
> 命令与 `.github/workflows/*.yml` 对应 Job 的步骤一致；登记项已于 2026-07-24
> 全部完成，并由 Windows 机器上的会话把结果写入对应
> `docs/development/<任务ID>.md` 后逐项提交。

## 2026-07-25 两份 Roadmap 最终查漏

本轮重新扫描 `docs/development-roadmap.md` 与
`docs/embedded-browser-video-studio-roadmap.md`，并把每个 Windows/双平台任务与独立
证据文件、Windows 实机记录和当前状态交叉核对。结论如下：

- 主 Roadmap 已完成的 Windows 原生链无遗漏：F1-14，E4-03、E4-05、E4-07～E4-15，
  B5-03～B5-08，P9-02、P9-05 均有对应 Windows 构建、文件系统/进程、真实 App 或安装根
  证据；历史系统浏览器链已由 EB-10 替代，不再是当前包内 Chromium 的发布门禁。
- 专项 Roadmap 已完成的 Windows 补证无遗漏：EB-01、EB-02、EB-04～EB-10、BU-02、
  VF-04、IM-02～IM-05、BM-02～BM-04、BM-14 均有 Windows x86_64 证据。IM-05 的冻结
  Worker、真实 Streamlit、正常 Tauri App/WebView2 与清理已经通过，继续保持 `🔍`
  仅因真实生成/进度/取消/成片需要 IM-08 的外部服务条件，不是 Windows 技术链未验。
- 当前已实现但不能完成正式 Windows 结论的条目只有：H8-22（无 Authenticode；
  macOS 也缺 Developer ID/公证）、P9-04（EB-16 正式资源未装配且无 Authenticode）、
  P9-07（还缺 EB-16 正式包、授权账号和 production 注册链）、P9-09（上述设备与平台
  事实的 14 条汇总）、IM-07/IM-08（还缺正式双平台包和百炼/素材/配音凭据）。每项独立
  证据文件均已记录不能替代的原因、解除条件和正式入口。
- 2026-07-25 当前用户与本机证书库的 Code Signing certificate 数量均为 0；工作区也
  没有百炼、素材站或配音验收配置。普通 `NotSigned` 包、确定性夹具、静态检查和人工
  勾选都不能替代正式 Authenticode、真实账号或真实视频证据。
- EB-16、EB-17、BU-07、BM-16、PB-07/PB-08、CQ-03/CQ-04，以及主 Roadmap 后续
  RW-01/RW-02/RW-13 等仍为 `⬜ 未开始`。它们不是“已经做完但漏了 Windows 验收”的
  条目，本轮按用户要求没有启动。

本次查漏没有把任何外部阻塞项改绿，也没有发现新的、具备全部前置但尚未执行的 Windows
验收命令。

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

### 7. BM-05 受限 MotionAuthoringAgent（路径 containment ✅ 2026-07-25 已完成）

BM-05 的封闭工具面、模型不可信输出处理、失败矩阵、流式传输和真实百炼模型端到端
编排验收（一句话→DESIGN/脚本/分镜→可 seek composition→lint/check/snapshot→提交
RenderJob）已在 macOS 完成，逻辑本身跨平台（纯 Python，无 Rust/Node/浏览器）。
**2026-07-25 Windows 验收结果（Mac 经 SSH 远程发起，Windows 真机执行）**：路径
containment 的 NTFS 语义重验**已完成，并修掉三个真实缺陷**——`a.html:hidden` 写入备用
数据流而审计扫描列不出、`DESIGN.json` 大小写不敏感覆盖已有 `design.json` 而审计仍报原名、
段尾点/空格被 Windows 静默剥离导致两个键塌成一个文件。详见 `docs/development/BM-05.md`
遗留项表格。reparse point 与 8.3 短名经核实无需额外处理（`Path.resolve()` 会解析 junction
并展开短名，随后被 `relative_to` 拦截）。

Windows 侧仍待补：BM-05 生成物真正被逐帧渲染并从正式 App 用户入口纵向验收，随 BM-08
（页面）与 BM-16（生产包冻结）在 Windows 平台一并补齐。

### 8. BM-07 风格微调与冻结 Windows 待补

BM-07 已在 macOS 正式 Tauri App 完成推荐 3 套、展开全部 12 套、品牌色/字体、本地 PNG
Logo、实际文案预览与键盘选择；锁定上游 builder 生成的品牌化 `frame.md` 已经 Schema、
远程/主动内容、字体/Logo 文件签名、路径 containment 和摘要校验，并在两个 RenderJob
私有目录重现相同冻结字节。macOS WebKit WebDriver 不支持 `uploadFile`，验收只能在正式
App WebView 内把真实 PNG 字节构造成 `File` 交给生产文件输入控件，已证明生产
`FileReader` 与 `<img>` 预览链路，但没有覆盖操作系统原生文件选择器。Windows 侧随 BM-08
纵向验收：用原生文件选择器选真实 PNG/JPEG/WebP 和本地字体，确认页面预览、Rust/Worker
提交到 RenderJob 的冻结摘要、NTFS reparse/大小写/短名 containment 与二次打开重现一致。

### 9. BM-08 App 原生编辑、渲染与 Artifact 导入 Windows 待补

BM-08 已在 macOS 真实测试版 Tauri App 完成正式用户入口全链路验收（编辑三段文案、
风格与品牌素材、真实草稿预览、提交、渲染中取消并验证 cancelled checkpoint、恢复路径
重试、真实 Chromium 90 帧渲染 + FFmpeg 编码、App 内解码播放约 3 秒 MP4、删除后
Artifact 与工作副本双清理、ffprobe 与三帧视觉证据），入口为
`python3 scripts/run_bm_08_acceptance.py`。Windows 侧待补：同脚本路径在 Windows 真实
App 重跑（脚本当前对 `os.name == "nt"` 显式拒绝，需补 Windows worker/浏览器包装）；
用原生文件选择器上传 Logo（macOS 只能以真实字节构造 `File` 交给生产输入控件）；
Windows WebView2 对 `data:video/mp4;base64` 的 H.264 解码播放；NTFS
reparse/大小写/短名下 RenderJob 工作区与 Artifact 删除语义。正式双平台安装包链路仍属
BM-16。通过后更新 `docs/development/BM-08.md` 遗留项并评估 BM-05/BM-07/BM-08 三项
`🔍 待验收` 状态闭合。

### 10. BM-16 确定性与正式包 Windows 待补

BM-16 已在 macOS 完成：聚合确定性门禁、锁定发布目录构建与黑盒门禁、12 套风格
冻结+真实渲染、同输入双跑 60 帧逐帧 SHA-256 一致、134 项逐项离线渲染 sweep
（134/134 通过，仅 1 次外部请求被默认断网拦截）与无 URL 输入/抓取入口验证，入口
`python3.12 scripts/run_bm_16_acceptance.py`（需 3.10+）。Windows 侧待补：同脚本
在 Windows 真实内置 Chromium 上重跑（发布目录只读属性用 `FILE_ATTRIBUTE_READONLY`
语义）；双平台正式安装包链路与包内容负面检查（无 WebDriver/调试端口/测试凭据/
测试命令）；跨机确定性比对；低配机与休眠恢复注入。通过后更新
`docs/development/BM-16.md` 遗留项并评估 BM-05/07/08/15/16 五项 `🔍 待验收` 闭合。

### 11. EB-11 登录与 Session Windows 验收（✅ 2026-07-25 已完成）

EB-11 已在 macOS 用 staged 内置 Chromium + 全新 0o700 私有 Profile 完成登录整链
（QR 状态机与人工接管、会话四态探测、重启复用、注销清理）与正式命令面
（douyin.login.open/recheck/logout）验收，入口
`cd backend && uv run pytest tests/integration/test_douyin_login_embedded_browser.py`。
Windows 侧待补：同套测试在 Windows staged 内置 Chromium（EB-04 缓存）上重跑，
Profile 权限语义按 Windows ACL 等价校验。真实扫码另标 🔍 待真实账号，不属 Windows
会话职责。通过后更新 `docs/development/EB-11.md` 遗留项。

### 12. EB-12 搜索/浏览/候选提取迁移 Windows 验收（✅ 2026-07-25 已完成）

EB-12 已在 macOS 把搜索执行、浏览、候选提取、有界滚动与页面漂移诊断五条链路的
集成测试启动来源迁移到 staged 内置 Chromium（6/6，页面对象与失败矩阵零改动）。
Windows 侧待补：同五套测试在 Windows staged 内置 Chromium 上重跑。真实抖音平台
最终状态另标 🔍 待真实账号。通过后更新 `docs/development/EB-12.md` 遗留项。

### 13. EB-13 评论链路迁移 Windows 验收（✅ 2026-07-25 已完成）

EB-13 已在 macOS 把评论页与评论动作集成测试启动来源迁移到 staged 内置 Chromium
（3/3，ActionGate/哈希/单次发送/结果不确定矩阵零改动）。Windows 侧待补：同两套
测试在 Windows staged 内置 Chromium 上重跑。真实抖音评论最终状态另标
🔍 待真实账号。通过后更新 `docs/development/EB-13.md` 遗留项。

### 14. EB-14 私信与恢复链路迁移 Windows 验收（✅ 2026-07-25 已完成）

EB-14 已在 macOS 把私信页、私信动作与 side-effect 恢复集成测试启动来源迁移到
staged 内置 Chromium（4/4，目标校验/频控/单次发送/暂停-取消-紧停/崩溃-重启恢复
矩阵零改动）。Windows 侧待补：同三套测试在 Windows staged 内置 Chromium 上重跑。
真实抖音私信最终状态另标 🔍 待真实账号。通过后更新 `docs/development/EB-14.md`
遗留项。

### 15. EB-15 诊断、人工接管与进程清理 ✅ 已完成（headed 一项仍待补）

EB-15 已在 macOS 完成进程树拆除、Profile 解锁重启、外部强杀 fail-closed 与有界
脱敏诊断、真实 headed 接管窗口五条验收，并修复"崩溃后仍交出死窗口"的真实缺陷
（`_require_running` 增加一次真实往返连通性探测）。入口
`cd backend && uv run pytest tests/integration/test_embedded_browser_lifecycle.py`
（headed 用例需 `AUTOMATION_TOOL_EB15_HEADED=1`）。

**2026-07-25 Windows 验收结果（Mac 经 SSH 远程发起，Windows 真机执行）**：在
`F:\automation-tool`（HEAD `bdc9715`）用 Windows staged 内置 Chromium
（`.local\eb-04-windows\chrome-win64.zip`）真机运行同一入口：
**`4 passed, 1 skipped, EXIT=0`**（11.15s）。四条通过的是进程树完全拆除、Profile
解锁后同一 Profile 干净重启、外部强杀后 fail-closed 并可恢复、诊断有界且不泄漏
Profile/可执行文件/家目录路径；skip 的是有头窗口用例（按静默运行规范显式跳过）。

前置改造（提交 `bdc9715`）：`pgrep -f` 与 `SIGKILL` 在 Windows 不存在，两个操作
下沉到 `backend/tests/integration/conftest.py` 的 `process_ids_matching()` 与
`terminate_process()`——POSIX 走 `pgrep`/`SIGKILL`，Windows 走 CIM 进程表与
`taskkill /F`；匹配串经环境变量传入 PowerShell，避免含空格与反斜杠的路径被引号
规则改写。下沉而非各写一份：PB-05 的发布链路集成测试是第二个调用方。

**仍待补**：有头运营窗口的可见性与人工接管，需要 Windows 上已登录的桌面会话，
且按静默运行规范必须显式开启才能跑。

### 16. CQ-01 普通用户可理解性 Windows 待补

CQ-01 已在 macOS 用隐藏 `video-studio-e2e` 测试 App 按左侧菜单真实路径完成验收：
工作台 → 视频制作（两张制作方式卡片各 10 项必答说明、两者“最适合”互不相同）→
动效零件（选“智能素材成片”时只给出归属说明，选“品牌动效成片”时给出 134 项目录并逐卡
断言中文“适用”说明）→ 制作设置（12 套整体画面风格卡片数为 12）→ 视频剪辑（独立模块、
空状态无 `No data`）→ 设置与诊断/平台状态/新建任务（无未解释行业词、无原始状态码），
入口 `python3.12 scripts/run_cq_01_acceptance.py`（需 3.10+）。Windows 侧待补：同脚本在
Windows 隐藏测试 App 上重跑，重点确认 antd 中文 locale 生效、系统中文字体回退后卡片必答项
与概念区分文案不被截断或换行吞掉；以及正式安装包内同一批文案的显示。此外
`控制服务`、`本机执行器`、`本机安装授权`、`剩余用量`、`客户演示版` 五处改名所影响的
`control-plane-recovery`、`network-recovery`、`task-restart`、`app-crash-recovery`、
`workbench-control`、`workbench-metrics`、`executor-crash-recovery`、`task-run`、
`model-service` 真实 App 用例需在具备真实 Control Plane/PostgreSQL 的环境重跑。
通过后更新 `docs/development/CQ-01.md` 遗留项并评估 `🔍 待验收` 闭合。

**2026-07-25 Windows 验收结果（Mac 经 SSH 远程发起，Windows 真机执行）**：在
`F:\automation-tool`（HEAD `64788fa`）用 Windows staged 内置 Chromium
（`.local\eb-04-windows\chrome-win64.zip`）真机运行：EB-11 `5 passed`；
EB-12/13/14 十个集成文件合计 `13 passed`，两次 `EXIT_CODE=0`。

首轮真机运行暴露的唯一障碍是测试侧的平台语义问题：19 处
`os.stat().st_mode & 0o777 == 0o700` 与 `mkdir(mode=0o700)` 在 Windows 上
不成立（mode 参数被忽略、`st_mode` 恒为 `0o777`）。已抽出
`tests/integration/conftest.py` 的跨平台 helper 修复（提交 `64788fa`）；生产
Profile 的 Windows 私有性由 `browser_profiles_windows.rs` 的受保护 DACL 保证，
EB-09 已单独验收，不受影响。各任务的真实抖音账号验收仍为 `🔍 待真实账号`。
### 17. EB-16 首发安装包与签名 Windows 待补

EB-16 已在 macOS arm64 完成真实正式安装包全链路：真实 `tauri build` 出包、装入唯一
一套内置 Chromium（331 文件 / 359,441,871 bytes）、`.app` 实测 635 文件 /
565,382,086 bytes、`.dmg` 约 257.4 MB、DMG 校验挂载安装、包内容负面检查、内外层签名
核对、包内 Chromium 真跑（149.0.7827.55）、真启动正式包 App（真实 HTTP 启动检查 +
Launch Services 前台注册）、真退出无进程残留、真卸载无残留资源。过程中发现并修复了一个
真实缺陷：**Tauri bundler 复制资源会跟随并丢弃符号链接**，会拆坏 Chrome for Testing 的
macOS Framework 并使上游签名失效；发布装配器改为自行保留符号链接装入并重新封章。

入口（macOS）：

```text
AUTOMATION_TOOL_EB16_LAUNCH_VISIBLE_APP=1 \
  backend/.venv/bin/python scripts/run_eb_16_acceptance.py
python3.12 scripts/test_embedded_browser_package.py
```

Windows 侧待补（需真实 Windows 环境）：

1. 用 `scripts/build_embedded_chromium_staging.py` + `build_embedded_browser_distribution.py`
   暂存 `windows-x86_64` 目标，构建真实 NSIS 安装包（`--bundles nsis`），并把内置浏览器
   按同样方式装入 `embedded-browser/`（Windows 无符号链接问题，但仍要核对逐文件摘要）；
2. 跑 `node frontend/scripts/audit-release-bundle.mjs --platform windows
   --bundle-root <安装目录> --executor-package <安装目录>/local-executor/package`。
   **只需这一条**：审计器自己按平台推导内置浏览器位置（Windows 是 `<包根>/embedded-browser`）
   并探测存在性，**存在就强制**执行
   `scripts/check_embedded_browser_package.py --bundle-root … --target windows-x86_64
   --platform windows` 并要求通过（包内只能有 `chrome-win64` 一套完整 Chromium，不得出现
   `chrome-mac-arm64`/`chrome-mac-x64`、`chromedriver.exe`、`tauri-driver.exe`、
   `msedgedriver.exe`、headless shell 或 `ms-playwright`）。绑定基于**资源存在性**而非
   调用方声明，所以漏传参数不会变成零校验通过——这一点在 Windows 尤其关键，因为
   `chrome-win64` 设计上没有符号链接，整树扫描不会像 macOS 那样"恰好"拒绝。
   `--embedded-browser` 现在只能用来**确认**该位置，传错位置会被拒。
   Windows 上需确保 `python3.12`、`python3` 或 `python` 三者之一在 PATH 上；三者都不可用
   时门禁不可达，审计一律**拒绝**（不会跳过）；
3. 跑 `node frontend/scripts/audit-production-package.mjs --binary <安装目录>/*.exe
   --tauri-config <本次构建实际使用的合并配置>`：确认真实产物内没有 WebDriver、调试
   端口、测试凭据、`*_for_acceptance` 测试命令，且窗口配置不是隐藏测试窗口；
4. 记录 Windows 实测体积（安装包与安装目录），如与 macOS 差异较大需回头校准
   `RELEASE_SIZE_BOUNDS`（当前上下界按 macOS 实测设定：浏览器树 320–420 MiB、整包
   340–700 MiB）；
5. Authenticode 签名（含时间戳）与 SmartScreen 表现——本机无 Windows 代码签名证书，
   属 🔍 待凭据；
6. 首次安装（`currentUser` NSIS）、启动正式包 App、退出后无 `chrome.exe` /
   `automation-tool-executor.exe` 残留进程、卸载后 HKCU 注册表与 LocalAppData 零残留；
7. 跑 `python scripts\test_embedded_browser_package.py`（确定性门禁套件）。该套件的
   macOS 形态 fixture 需要创建符号链接，Windows 无开发者模式/`SeCreateSymbolicLink`
   权限时会自动 skip 相关用例（`_REQUIRES_SYMLINKS`），其余用例——含"内置浏览器存在即
   强制跑摘要门禁"这条关键机制用例——仍会真实执行。记录实际 skip 数；
8. 通过后更新 `docs/development/EB-16.md` 遗留项。

## 注意

- 全程无头模式，不要跑出可见浏览器窗口（真实扫码类验收除外）；
- 每个任务的证据、状态行、代码改动必须同一提交；
- 结束后清理 `$TEMP` 下本次创建的 eb-02-* 等临时目录与浏览器进程。
