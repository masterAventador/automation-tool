# FIX：锁定离线目录收进机器缓存，并补上缓存根的一致性门禁

用户可操作：否

证据类型：分层实现

> 日期：2026-08-05
>
> 提交：本文件所在提交
>
> 类型：重复实现收敛 + 独立缺陷修复（不改任何 roadmap 任务状态）

## 三件事

### 一、`build_embedded_chromium_staging` 里指向旧位置的重复实现（真实故障）

`FIX-browser-archives-into-machine-cache.md` 把归档搬进机器缓存、重写了
`embedded_browser_archives.py` 并删掉 `build_release_package.py` 的 `_first_existing()`。
但 `build_embedded_chromium_staging.py` 原样留着一份：同名 `_first_existing()`、同样两候选、
同样两条 `.local/` 字面量，注释还写着「the primary checkout's `.local`」。

**它不是死代码。** 五个调用方读它的 `DEFAULT_ARCHIVES`：`conftest.py`、`run_bm_16`、
`run_eb_16`、`run_le_22`、`run_pc_16_macos`，其中三个还 `.resolve(strict=True)`。归档真的
搬走之后，它们从「报归档没下载」变成在**没人写的路径上抛 `FileNotFoundError`**。

修复：删掉那份实现，改为 `DEFAULT_ARCHIVES = default_archives()`。

```text
修复前 windows-x86_64 -> F:\...\.local\eb-04-windows\chrome-win64.zip      不存在
修复后 windows-x86_64 -> %LOCALAPPDATA%\...\embedded-browser-archives\...   exists=True
       .resolve(strict=True) 由 FileNotFoundError 变为通过
五个调用方逐个真实 import：全部 ok
```

### 二、锁定离线动效目录收进机器缓存

它是**构建输入**——按摘要从 jsdelivr / gstatic / cdnjs 拉的锁定依赖，412 文件 / 39.1 MiB，
与 Chromium 归档、media-toolchain 同类，却留在 checkout 的 `.local`，于是每棵 worktree 要么
重下要么找不到（`.local` 不随 worktree 带过去，CLAUDE.md §8.1 记着那天的代价）。

契约 `layout.catalogRoot` 由 `.local/offline-motion-deps/catalog` 改为缓存条目名
`offline-motion-catalog`，新增唯一解析函数 `build_offline_motion_catalog.catalog_root()`，
六处各自拼接的地方全部改为调用它。

**由它生成的发布树刻意留在 `.local`**：那是**输出**，`commit_gate` 的 slow tier 会用本次提交的
代码重建它，共享会让一次运行的输出变成另一次的输入——正是
`embedded_browser_staging_cache.py` docstring 明确避免的形状。

`commit_gate._copy_offline_motion_catalog` 随之改为 `_require_offline_motion_catalog`：
不再往隔离 checkout 拷 39 MiB，因为 slow tier 只读它（`_build_slow_motion_release` 的输出
落在 checkout 自己的 `.local`）；原有的「输入缺失」与「树里有链接」两项检查保留。

守护 `test_offline_motion_catalog_location.py` 读 AST 拒绝任何脚本再自行拼接
`catalogRoot` 或写死旧路径，只豁免定义处一个文件名。它当场抓出两处漏网
（`gate_prerequisites` 与我自己写在 docstring 里的旧路径），随后按「docstring 属于说明、
不算代码」收紧。

本机迁移：412 文件 / 39.1 MiB 移入 `%LOCALAPPDATA%\automation-tool-build\offline-motion-catalog`。

### 三、缓存根一致性门禁（顺手补）

同一条「缓存在哪」的规则活在三处：`scripts/video_runtime_cache.py::cache_root` 是正本，
执行器包内 `captions/fonts.py` 与 `silero_vad.py` 各有一份——**重复是正当的**（执行器由
PyInstaller 冻结，`scripts/` 不进包，无法 import），但三者此前**只靠人工保持一致，没有任何
东西能发现它们已经分家**。

`scripts/test_build_cache_root_agreement.py` 覆盖八种环境组合（无覆盖 / 显式覆盖 / `~` 简写 /
空值不算覆盖 / `LOCALAPPDATA` 有无 / `XDG_CACHE_HOME` 有无）逐一比对三份实现，另两条钉住
「`~` 必须展开」与「目录名必须可追溯到 `automation-tool`」。

**变异确认**（用编辑器改，不用 PowerShell——`Set-Content -Encoding utf8` 会加 BOM，会让变异
红在 `SyntaxError` 上而不是红在被测性质上）：

```text
摘掉 silero_vad.py 的 .expanduser()
→ executor/silero_vad.py::_build_cache_root returned the relative path ~\at-probe
→ silero_vad 解析 ~\at-probe，video_runtime_cache 解析 C:\Users\Aventador\at-probe
   The executor would read a directory the build never wrote.
还原 → Ran 3 tests, OK
```

**这条门禁没抓到现行 bug**，三份目前一致。它的价值在下一次编辑，测试的 docstring 已写明，
免得后人误以为它当初拦下过什么。

## GREEN

```text
scripts/test_commit_gate.py                        executed checks: 15
scripts/test_motion_catalog_release.py             executed checks: 10
scripts/test_offline_motion_catalog_location.py    OK
scripts/test_gate_prerequisites.py                 OK
scripts/test_build_cache_root_agreement.py         OK
scripts/test_embedded_browser_archives.py          OK
scripts/test_embedded_browser_staging_cache.py     OK
ruff（本次改动的 8 个文件）                          All checks passed
```

## 失败矩阵

- 归档/目录缺失：`catalog_root()` 只解析不创建，缺失由 `gate_prerequisites` 指出 producer 命令；
- 有人重新自行拼接：AST 守护拒绝，豁免只有一个文件名，可数；
- 三份缓存根分家：一致性门禁按八种环境逐一比对；
- slow tier 写入共享输入：`test_commit_gate` 断言迁移后共享输入内容未变。

## 正常用户路径验收

不适用——构建与门禁脚本，不新增终端用户入口。

## 真实边界

- 只在 Windows 实跑。三处改动都不含平台分支（`cache_root()` 本就分好三平台），**但 macOS 上
  的复跑仍待补**；
- macOS 上原有的 `.local/offline-motion-deps/catalog` 会变成没人读的旧物，首次使用时按
  `gate_prerequisites` 的 producer 重建，与浏览器解包树那次同一形态；
- 契约 `offline-motion-dependencies.v1.json` 改动使 `motion-catalog-release.v1.json` 里 pin 的
  摘要漂移，门禁当场拦下（`offlineDependenciesLock digest pin drifted`），已按新值更新——
  **这道门禁按预期工作**。

## 清理

- 删除 `build_embedded_chromium_staging.py` 的 `_first_existing()`、`_EB_03_CACHE` 与自建
  `DEFAULT_ARCHIVES`；删除 `commit_gate._copy_offline_motion_catalog` 的拷贝逻辑；
- 变异验证已还原，`silero_vad.py` 与 HEAD 一致；
- 本机 `.local/offline-motion-deps/catalog` 已移走，不留第二份。

## 文档变化

本文件新增。

## 遗留项

| 项 | 状态 |
| --- | --- |
| macOS 机迁移 `offline-motion-deps/catalog` 并复跑 | 待办（该机不在手边） |
| `frontend/tests/motion-render-static-frames.test.mjs:222` 的 `RUNTIME_CANDIDATES` 仍指旧路径 | 待办：与 `FIX-staged-browser-lookup-and-host-target.md` 修掉的 `BROWSER_CANDIDATES` 同一形状，找不到即 `t.skip`，属同类「安静跳过」 |
| `build_offline_motion_catalog.py` SIM103、`gate_prerequisites.py` RUF022、`test_commit_gate.py` RUF100 | ✅ 本次一并修掉。三条都是 main 上就有的存量（逐个对 `git show main:` 复核过），留着会让「本文件 ruff 是否干净」失去信号 |
