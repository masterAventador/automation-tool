# FIX：锁定字体与离线依赖入库，构建不再联网取它们

用户可操作：否

证据类型：分层实现

> 日期：2026-08-04
>
> 提交：本文件所在提交
>
> 类型：独立缺陷修复（不改任何 roadmap 任务状态）

## 缺陷

仓库里被跟踪的字体文件此前只有 1 个：

```text
git ls-files | grep -E "\.(woff2?|ttf|otf|ttc)$"
assets/motion-catalog-overlay/fonts/big-shoulders-display-latin.woff2
```

其余两类每台新机器都要联网重新拉：

| 资产 | 体积 | 来源 |
| --- | --- | --- |
| 字幕字体（Noto CJK / Plangothic，6 文件） | 63 MB | `raw.githubusercontent.com` |
| 动效模板依赖（23 个字体家族 + draco + 脚本，90 文件） | 21 MB | `fonts.gstatic.com`、`cdn.jsdelivr.net`、`www.gstatic.com` |

这些是不可变的静态文件——不像 ffmpeg 要按本机架构编译，也不像 171 MB 的 Chromium
归档那么重。重新下载换不来任何东西，却换来一个真实依赖：**Windows 验收机直连
`fonts.gstatic.com` TLS 握手失败**，它的构建一直靠"在 Mac 上下好再 `scp -r` 传过去"
这个手工前置步骤才成立。

`e0bc7d4d lock rare Han font assets` 那次改的是锁定、校验与重试，动的全是契约、脚本
和测试——**一个字体文件都没入库**，所以"之前已经放本地了"并不成立。

## RED

新增 `scripts/test_committed_locked_assets.py`，3 条，均先失败：

```text
FAIL test_download_root_is_inside_the_repository_and_tracked
  AssertionError: the download root must be a committed directory, not .local/

ERROR test_fonts_build_without_touching_the_network
  SubtitleFontUnavailable: cannot fetch https://raw.githubusercontent.com/notofonts/…
  the build tried to download …
```

第二条注入一个「一被调用就抛异常」的 fetcher，所以它测的是**真的没发请求**，
而不是「请求碰巧成功了」。

## GREEN

**动效依赖**：契约 `layout.downloadRoot` 由 `.local/offline-motion-deps/downloads`
改为 `assets/offline-motion-deps`，90 个文件入库。`verify_downloads()` 一行逻辑没改——
它本来就是「文件在就不下载，然后照样校验 sha256 与字节数」，也本来就有 `--offline`
模式。入库后那条路径自然成立。

**字幕字体**：6 个文件入库到 `assets/subtitle-fonts/`；`ensure_subtitle_fonts` 的取字节
入口改为先读仓库副本、缺失才回源。**校验完全不变**——`verify_font_payload` 在这个入口
外面，仓库副本和下载结果走同一套摘要断言，一个漂了的committed 文件会像损坏的下载一样
被拒绝。

改契约的连锁也一并处理：`motion-catalog-release.v1.json` 的
`inputs.offlineDependenciesLock` pin 跟随更新（旧 `73ef3e94…` → 新 `801ab7e8…`），
发布树重建。这是「锁跟随其锁定对象」，不是绕过门禁——契约是本次有意修改的。

```text
backend/.venv/bin/python scripts/test_committed_locked_assets.py    Ran 3 tests, OK
backend/.venv/bin/python scripts/test_offline_motion_catalog.py     3 checks passed
backend/.venv/bin/python scripts/test_le20_caption_font_assets.py   7 checks passed
backend/.venv/bin/python scripts/check_offline_motion_catalog.py    134 items, 411 files, 0 remote URLs
backend/.venv/bin/python scripts/check_motion_catalog_release.py    134 items, 337 files, 0 remote URLs
backend/.venv/bin/python scripts/check_third_party_sources.py       4 redistributable fonts registered
```

**真实端到端证据**——删掉构建产物，用「缺文件直接报错、绝不下载」的离线模式重建：

```text
rm -rf .local/offline-motion-deps/catalog
backend/.venv/bin/python scripts/build_offline_motion_catalog.py --offline
  offline motion catalog built: 134 items, 411 files, 70 items pending BM-13 asset replacement
```

## 真实边界

- 直接入库，**不引入 LFS**：81 MB 一次性且不可变，不会像构建产物那样每次重建都留一份
  新的；而给主仓库引入 LFS 会把 `new_worktree.py` 绕开的那套 smudge 代价加回来；
- 只在 macOS arm64 验证。**Windows 侧的收益要等那台机 `git pull` 后才兑现**，届时它
  不再需要「Mac 上下好再 scp」这一步；
- Chromium 归档（171 MB）仍不入库，仍需人工下载——体积是它的八倍，且已有明确的
  「locked archive is not downloaded yet」提示；
- 摘要校验一处未放宽：committed 副本与下载走同一套断言。

## 清理

- 旧的 `.local/offline-motion-deps/downloads`（21 MB）已删；主仓库代码零引用
  （只剩两个 worktree 自己的 checkout，与一处历史设计文档的旁注）；
- 机器级 `subtitle-fonts` 缓存保留：它现在从仓库副本构建，不再触网。

## 遗留项

| 项 | 状态 |
| --- | --- |
| Windows 机 pull 后验证不再需要 scp 字体 | 待办，与该机 `chrome-win64.zip` 迁移一并做 |
