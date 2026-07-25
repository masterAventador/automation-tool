# FIX：内置浏览器归档路径在主 checkout 里找不到

> 日期：2026-07-25
>
> 提交：本文件所在提交
>
> 类型：独立缺陷修复（不改任何 roadmap 任务状态）

## 缺陷

`scripts/run_bu_02_acceptance.py`、`run_eb_06_acceptance.py`、`run_eb_07_acceptance.py`
把 macOS arm64 的锁定归档默认路径写成 `ROOT.parent.parent / .local/…`。

这个写法本身有来历：验收脚本既可能在主 checkout 里跑，也可能在 `wt/<任务>` worktree 里跑，
而 worktree 的路径是 `<项目根>/wt/<名字>`，往上两级正好回到主 checkout 的 `.local/` 缓存。
问题是这三个早期脚本**只写了 worktree 那一种**，于是在主 checkout 里跑时解析成
`/Users/aventador/.local/…`——那里什么都没有，脚本报"归档还没下载"，而归档就在仓库里。

后来的 `run_bm_08_acceptance.py` 和 `run_eb_16_acceptance.py` 各自补了一份
"两个位置都找"的 `_first_existing`，所以不受影响；代价是同一段查找逻辑在仓库里有两份，
早期三个脚本一份都没有。

## RED

```text
python3 scripts/run_bu_02_acceptance.py
BU-02 acceptance failed: locked archive not downloaded yet:
  /Users/aventador/.local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip
```

实际运行、真实失败，失败原因正是这条缺陷（归档存在于
`.local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip`）。

## GREEN

新增 `scripts/embedded_browser_archives.py`：三个目标的归档相对路径各一处定义，
`archive_path()` 先看当前 checkout 再看上两级，两处都没有时返回**主 checkout** 的路径——
这样报错信息指向操作者应该下载到的位置，而不是 worktree 兜底路径。

四个脚本改用它（`run_eb_16_acceptance.py` 与 `run_bm_08_acceptance.py` 本轮没动，
见「遗留项」）：

```text
python3 scripts/run_bu_02_acceptance.py
  BU-02 dual-mode acceptance passed: isolated executable_path + random loopback CDP
  takeover on 149.0.7827.55 (macos-arm64)
python3 scripts/run_eb_06_acceptance.py
  EB-06 acceptance passed: Rust resolver verified the real staged macos-arm64 distribution
python3 scripts/run_eb_07_acceptance.py
  EB-07 acceptance passed: macos-arm64 authority resolve + executor launch request accepted
python3 scripts/run_bu_07_acceptance.py
  BU-07 attack matrix passed against the real locked browser (149.0.7827.55, macos-arm64)
uv run ruff check --ignore RUF001 scripts/embedded_browser_archives.py scripts/run_bu_02_acceptance.py \
  scripts/run_eb_06_acceptance.py scripts/run_eb_07_acceptance.py scripts/run_bu_07_acceptance.py
python3 scripts/check_embedded_browser_video_roadmap.py
```

四个验收脚本都是真实运行到底（真实暂存、真实 Chromium、真实 cargo 测试），不是只看路径解析。

## 真实边界

只在 macOS arm64 主 checkout 里验证过。worktree 那一条分支这次没有实际跑过——
它是原来就有的行为，本次改动保留了它，但没有新增证据。

## 清理

验收脚本各自在临时目录暂存浏览器、结束时终止进程树；本次未新增常驻服务。

## 遗留项

| 项 | 状态 |
| --- | --- |
| `run_eb_16_acceptance.py` / `run_bm_08_acceptance.py` 收敛到同一处查找 | 未做：本轮它们正被并行的正式包构建占用，改动会打断运行 |
