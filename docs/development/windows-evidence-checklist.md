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
