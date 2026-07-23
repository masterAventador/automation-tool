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

### 2. IM 线冻结 Worker（IM-02/03/04 的 Windows 原生候选）

```bash
uv python install 3.11.15
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build        # cargo 测试需要 frontend/dist
python scripts/run_im_04_acceptance.py
python scripts/run_im_05_acceptance.py
```

通过后：IM-02、IM-03 的「Windows 原生候选」缺口闭合；IM-04 剩余缺口只剩完整
WebUI 正式入口（IM-05 承接）。

### 3. VF-04 媒体工具链（FFmpeg/ffprobe 供应链）

```bash
scripts/build_video_media_toolchain.sh windows-x86_64 frontend/src-tauri/resources/media-toolchain
python scripts/check_video_media_toolchain.py \
  --candidate frontend/src-tauri/resources/media-toolchain --target windows-x86_64
```

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

### 5. EB-04 Windows 浏览器构建暂存（新任务，可交给 Windows 机器上的会话执行）

EB-03 已在 `contracts/browser/embedded-chromium-staging.v1.json` 预登记
`windows-x86_64` 目标（官方下载地址 `.../cft/149.0.7827.55/win64/chrome-win64.zip`，
`buildable=false` 待翻转）。流程与 EB-03 对称：下载归档→锁 SHA-256 进契约→
`buildable=true`→复用 `scripts/build_embedded_chromium_staging.py` 与测试→
新增 Windows 验收脚本（离线启动探针）→按台账激活/闭环。

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
