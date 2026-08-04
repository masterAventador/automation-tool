# FIX：内置浏览器归档收进机器级构建缓存

用户可操作：否

证据类型：分层实现

> 日期：2026-08-04
>
> 提交：本文件所在提交
>
> 类型：独立缺陷修复 + 重复实现收敛（不改任何 roadmap 任务状态）

## 缺陷

摘要锁定的 Chromium 归档此前放在 checkout 内的 `.local/`：

```text
.local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip   171 MB
.local/eb-04-windows/chrome-win64.zip
```

而机器上其余每一样锁定的第三方输入都已经在项目级构建缓存里（`media-toolchain`、
两个视频 Worker、字幕字体、VAD 模型，共 670 MB）。归档留在 checkout 内产生两个后果：

**一、每个消费者都得自己回答「我在主 checkout 还是在 worktree」。** worktree 的
`.local/` 是空的，主 checkout 往上两级又不存在。`FIX-embedded-browser-archive-lookup.md`
记着这个代价：BU-02/EB-06/EB-07 只写了 worktree 那一种，于是在主 checkout 里报
「归档还没下载」而归档就在仓库里。

**二、那次只收敛了 4 个脚本，剩下的各自留着一份私有查找。** 本次实测仍有 6 处硬编码：

| 位置 | 写法 |
| --- | --- |
| `run_eb_03_acceptance.py` | `ROOT.parent.parent / ".local/…"` — 只有 worktree 一种，**主 checkout 里跑不了** |
| `run_eb_05_acceptance.py` | 同上，且 Windows 那条又是 `ROOT / ".local/…"` |
| `run_bm_03_acceptance.py`、`run_bm_04_acceptance.py` | 各一份两目标字典 |
| `run_eb_04_acceptance.py` | `ROOT / ".local/eb-04-windows/…"` |
| `build_release_package.py` | 私有 `_first_existing()` + `_EB_03_CACHE` 常量 |

同一个字面量散落六处，正是「值复用原则」点名的那类定时炸弹。

## RED

新增 `scripts/test_embedded_browser_archives.py`，5 条断言归档落在机器级缓存、不依赖
调用者所在的 checkout、尊重 `AUTOMATION_TOOL_BUILD_CACHE` 覆盖。首次执行：

```text
Ran 5 tests ... FAILED (errors=5)
TypeError: archive_path() missing 1 required positional argument: 'relative'
TypeError: default_archives() missing 1 required positional argument: 'root'
```

两条既有门禁改为表达新意图后也先红：

```text
frontend/tests/eb-05-cross-platform-acceptance.test.mjs
  AssertionError [ERR_ASSERTION]: operator: 'doesNotMatch', expected: /\.local\/eb-04-windows/u
```

## GREEN

`embedded_browser_archives.py` 改为复用 `video_runtime_cache.cache_root()`——即
`media-toolchain` 那些已经在用的同一个跨平台缓存根，归档落在其下的
`embedded-browser-archives/`。两候选查找连同 `build_release_package.py` 的
`_first_existing()` 一并删除：不是把 worktree 那个问题再答一遍，而是让它不再存在。

```text
backend/.venv/bin/python scripts/test_embedded_browser_archives.py    Ran 5 tests, OK
backend/.venv/bin/python scripts/test_desktop_e2e_prerequisites.py    20 checks passed
backend/.venv/bin/python scripts/test_build_release_package.py        Ran 14 tests, OK
backend/.venv/bin/python scripts/check_script_import_symbols.py       通过
backend/.venv/bin/python scripts/check_release_package_wiring.py      6 declared resources, 2 release paths
node --test tests/eb-05-cross-platform-acceptance.test.mjs            pass 1
uvx ruff check（9 个改动文件）                                        仅剩 1 条既有 SIM105
```

15 个改动脚本逐个真实导入通过（`run_eb_04_acceptance` 缺 `psutil`，是既有环境事实，
该脚本只在 Windows 跑；`py_compile` 通过）。

**真实端到端证据**——归档迁移后跑完整 EB-03 验收，不是只看路径解析：

```text
backend/.venv/bin/python scripts/run_eb_03_acceptance.py
  Ran 15 tests, OK
  EB-03 staged browser launched offline: 149.0.7827.55
  EB-03 macOS staging acceptance passed: 328 files, 339257128 bytes, manifest reproducible
```

真实解包、逐文件摘要、真实启动 Chromium、清单可复现。**该脚本此前在主 checkout 里
根本跑不起来**（`ROOT.parent.parent` 解析到 `/Users/aventador/.local/…`），本次顺带修好。

## 真实边界

- 只在 macOS arm64 主 checkout 验证。**Windows 侧的 `chrome-win64.zip` 需要同样迁移**
  到 `%LOCALAPPDATA%\automation-tool-build\embedded-browser-archives\`，在那之前
  Windows 上的 EB-04/EB-16/LE-23/PC-16 会报「归档还没下载」并指向新位置；
- worktree 分支这次不是「保留未验证」而是**删除**：缓存本就跨 checkout 共用，
  `new_worktree.py --no-vendor` 建的树不再需要任何归档拷贝；
- 未改动任何产品代码，只动构建与验收脚本。

## 清理

- 迁移用 `mv`，磁盘上不留第二份；空的 `eb-03-cache/` 目录已删；
- 删掉 `_first_existing()`、`_EB_03_CACHE` 与 5 处 `DEFAULT_ARCHIVES` 字面量字典，
  无遗留死代码（`grep _first_existing` / `_EB_03_CACHE` 零命中）。

## 遗留项

| 项 | 状态 |
| --- | --- |
| Windows 机迁移 `chrome-win64.zip` | ✅ 2026-08-05 已完成。迁移前核对文件 sha256 与契约 `archive_sha256` 逐位一致（`ebc0c2b7…07ab`），按本文档做法用移动、不留第二份，192,511,857 字节落到 `%LOCALAPPDATA%\automation-tool-build\embedded-browser-archives\`，空掉的 `.local\eb-04-windows\` 已删。随后在该机实跑 `ensure_staged_browser("windows-x86_64")`：首次 5.0s → 310 文件 / 435,703,601 字节，二次 0.00s 命中，`verify_distribution` 通过。证据见 `FIX-staged-browser-lookup-and-host-target.md` |
