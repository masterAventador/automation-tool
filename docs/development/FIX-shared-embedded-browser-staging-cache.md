# FIX：内置浏览器解包收敛成一份机器级缓存

用户可操作：否

证据类型：分层实现

> 日期：2026-08-05
>
> 提交：本文件所在提交
>
> 类型：重复实现收敛（不改任何 roadmap 任务状态）

## 缺陷

两个消费者需要同一样东西——解包并经清单校验的 Chromium 树：

| 消费者 | 落点 |
| --- | --- |
| 桌面 E2E 前置 | `.local/desktop-e2e/embedded-browser/<target>` |
| 发版命令 | 每次出包在工作目录现场解包 |

两边用的是**同一个锁定归档、同一个 `build_staging()`、同一份契约**，所以产物按构造
必然一致——**而它们之间只有"按构造"这一条保障**。同时 171 MB 归档解包成 328 个逐一
摘要的文件并不便宜，发版每跑一次就付一次。

「两份本该相同的副本」正是这个仓库反复付代价的形状。

## RED

新增 `scripts/test_embedded_browser_staging_cache.py`，先失败于模块不存在：

```text
ModuleNotFoundError: No module named 'embedded_browser_staging_cache'
```

**第二条 RED 来自真实验证，不是单元测试。** 共享缓存跑通后，按发版的用法把缓存拷进
临时工作目录再跑发布侧校验，实测被拒：

```text
DistributionRejected: embedded browser distribution rejected: symlink entry drifted
文件数 848（缓存里是 330）
```

`shutil.copytree` 默认跟随符号链接，而 Chrome for Testing 的 framework **本身就是一棵
符号链接树**、清单逐条声明了这些链接。补上回归测试后先红：

```text
AssertionError: 331 != 848 : the copy did not preserve the tree shape
```

## GREEN

`scripts/embedded_browser_staging_cache.py`：以 staging 契约为 key 走 `ensure_cached`，
落在与 `media-toolchain` 并排的机器级缓存。契约里逐 target 钉着 `archive_sha256`，
所以换归档即换 key——key 弱于此都会继续端出上一版浏览器。

两个消费者切过来：发版 `copy_staged_browser(...)`（`symlinks=True`），E2E 前置直接
`ensure_staged_browser(...)`。

**缓存刻意只存签名前的树。** 发版要用 Developer ID 重签每个 Mach-O 并重取清单（清单必须
描述出厂字节），这些都在调用方自己的副本上做，缓存不被就地签名——否则一次发版会给下一个
dev 构建留下一棵签过名的树。

`gate_prerequisites` 的 `produces` 原本要求仓库相对路径，而这份产物属于整台机器，
故放宽为「有意义时相对、机器级产物用绝对」，`missing()` 本就两者皆可
（`REPOSITORY_ROOT / "/abs"` 即 `/abs`），`explain()` 补了同样的容错。

```text
scripts/test_embedded_browser_staging_cache.py    Ran 6 tests, OK
scripts/test_gate_prerequisites.py                Ran 16 tests, OK
scripts/test_desktop_e2e_prerequisites.py         20 checks passed
scripts/test_build_release_package.py             Ran 14 tests, OK
scripts/check_release_package_wiring.py           6 resources, 2 release paths
scripts/check_script_import_symbols.py            通过
uvx ruff check（6 个改动文件）                     仅剩 1 条既有 SIM105
```

**真实端到端证据**：

```text
共享缓存 ~/Library/Caches/automation-tool-build/embedded-browser-macos-arm64
        325 MB / 330 文件，Chromium 主可执行文件在位
发版侧取用   0.11s（原为现场解包 171 MB 归档）
逐文件校验   328 files, 339,257,128 bytes 通过
删掉旧 dev 缓存后 dev 侧解析      → 同一个共享缓存目录，331 文件
embedded_browser_cache() 与实际写入位置一致  → True
```

## 真实边界

- 只在 macOS arm64 验证；Windows 走同一个函数，其 target 条目已声明；
- 缓存不含签名，发版签名在私有副本上进行——这是刻意的边界，不是遗漏；
- `.local/desktop-e2e/embedded-browser`（325 MB）已删除，不再有第二份。

## 清理

- 旧 dev 缓存目录已删；
- `build_release_package.py` 中因此不再使用的 `build_staging` / `sha256_file` 导入已移除。

## 遗留项

无。
