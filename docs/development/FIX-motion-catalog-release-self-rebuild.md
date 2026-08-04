# FIX：动效目录发布树缺失或过期时自动重建

用户可操作：否

证据类型：分层实现

> 日期：2026-08-04
>
> 提交：本文件所在提交
>
> 类型：独立缺陷修复（不改任何 roadmap 任务状态）

## 缺陷

每一样锁定的运行时资源在缓存未命中时都会自己重建——`prepare_video_runtime()` 经
`video_runtime_cache.ensure_cached()` 比对契约 fingerprint，不一致就删掉旧产物、
调 builder、失败时连半成品带 stamp 一起清掉。**只有动效目录发布树是例外**：

```python
if not source.is_dir():
    raise BuildError(f"the release tree is not built at {source}; run this script first")
```

两个后果：

**一、干净机器上第一次出包必然失败**，且失败在一个别的资源都不需要的步骤上。

**二、真正常见的不是「没有」，是「有但过期」。** 2026-08-04 实测：本机那棵树建于
08-01 15:20，用的是旧版 `motion-asset-overlay.v1.json`；之后契约改过、lock 的 pin 也
同步更新了，于是出包死在

```text
motion catalog release check failed: release manifest input pins drifted from the lock
```

这句话读起来像「锁文件坏了」，实际仓库完全自洽——当前 overlay 文件的 SHA-256
`055c7190…` 与 lock 里 pin 的一致，漂的是本地产物。判据只能靠人工去比摘要，而
`ensure_cached` 那套机制本来就是干这个的。

## RED

`scripts/test_motion_catalog_release.py` 新增两条，均先真实失败：

```text
test_stage_for_release_builds_a_missing_release_tree
  BuildError: motion catalog release build failed:
  the release tree is not built at …/release/1.0.0; run this script first

test_stage_for_release_rebuilds_a_stale_release_tree
  （同一条路径，manifest 的 inputs 被改成旧摘要以复现 08-04 那次漂移）
```

第二条刻意复现的是「存在但过期」，因为缺失从来不是有意思的那种情况。

## GREEN

`stage_for_release()` 改成：校验 → 失败就重建一次 → 再校验。树是可复现的构建产物、
不是证据，重建不销毁任何东西；重建后仍失败的才是真失败，照常抛出。

一个必须注意的语言细节：`CheckError` 继承的是 `SystemExit`，**不是 `Exception`**，
`except Exception` 抓不到它。捕获写作 `except (BuildError, OSError, SystemExit)`，
并在代码里注明原因。

上游 staged catalog 是这里合成不出来的构建输入，所以缺它时走 `gate_prerequisites`
的 `require("offline-motion-catalog")` 报出产它的命令——那个模块的存在理由正是
「remedy 由 producer 派生，绝不写在旁边」，避免指错命令。

```text
backend/.venv/bin/python scripts/test_motion_catalog_release.py   10 checks passed
backend/.venv/bin/python scripts/test_gate_prerequisites.py       Ran 16 tests, OK
backend/.venv/bin/python scripts/test_build_release_package.py    Ran 14 tests, OK
backend/.venv/bin/python scripts/check_motion_catalog_release.py  134 items / 337 files
backend/.venv/bin/python scripts/check_release_package_wiring.py  6 resources, 2 release paths
backend/.venv/bin/python scripts/check_script_import_symbols.py   通过
uvx ruff check（两个改动文件）                                     All checks passed
```

**真实端到端证据**——删掉本机那棵树，走默认路径（不是测试注入的临时目录）：

```text
删除前: .local/motion-catalog-release/1.0.0  True
删除后: False
自动重建后 staging: manifest.json 124,740 bytes
发布树已回来: True, 338 files
```

## 真实边界

- 只在 macOS arm64 验证；重建逻辑不含平台分支，Windows 侧走同一条；
- 重建**每次调用最多一次**：重建后校验仍失败会直接抛出，不会成环；
- 只读树的删除需要先恢复写权限（`_make_tree_writable`），这是构建产物只读带来的必要步骤；
- 未改动 `build_release()` 本身，也未改动任何校验规则——漂移仍然会被 `verify_release`
  发现，区别只在于发现之后是重建还是让操作者手工跑。

## 清理

- 测试用临时目录，结束前恢复写权限以便清理，无残留；
- 本机发布树在验证过程中被真实删除并重建，现为 338 文件的正常状态。

## 遗留项

无。
