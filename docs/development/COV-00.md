# COV-00 锁定权威基线与排除项审计

用户可操作：否
证据类型：查证

> 状态：进行中。排除项审计已完成并逐项给出结论；基线复现数据待本机全量跑完后补入 §2。
> 上游计划：`docs/development/2026-08-03-backend-coverage-debt-plan.md`
> 分支：`coverage/backend-100`（worktree `wt/coverage-100`，基于 `origin/main@5c1387d9`）

## 1. 本任务的边界

COV-00 只做三件事，不写任何补覆盖率的测试：

1. 复现计划记录的基线，确认 `94.992698%` 这个数字在本工作树上可重现；
2. 逐处审计 49 个显式 `# pragma: no cover`，给出每一处的去留结论；
3. 确认 `windows_acl.py` 是唯一模块级 `omit`。

**删除动作不在本任务内。** 经用户确认，pragma 的实际删除与配套测试跟着模块走：
`agent.py`/`voiceover.py`/`authoring_workspace.py` 的 9 处在 COV-03 处理，`ledger.py` 的
9 处在 COV-06 处理，依此类推。这样同一个文件只动一次，且计划里「118 条对齐后升到
95.881290%」这个校验点保持有效——它是在当前 pragma 集合下测出来的，本任务若改动排除项
就会让那个漂移探测器失效。

## 2. 基线复现

命令（在 `wt/coverage-100/backend`）：

```bash
uv run pytest -q --cov=automation_tool --cov-report=term-missing \
  --cov-report=json:<scratchpad>/baseline-aligned.json
```

退出码 1，原因是 `ERROR: Coverage failure: total of 95 is less than fail-under=100`，
没有任何测试失败。**注意本次刻意不接管道**：`cmd | tail` 会让退出码变成 `tail` 的，
按 CLAUDE.md §8.3 那条教训，判定前必须让退出码由被测命令决定。

| 指标 | 计划记录 | 本树实测 |
|---|---|---|
| 测试 | 6,471 passed / 21 skipped | 6,471 passed / 21 skipped ✅ |
| 语句 | 32,215 / 缺 1,348 | 32,215 / 缺 1,348 ✅ |
| 分支 | 8,186 / 缺 675 | 8,186 / 缺 **676**（见 §2.2） |
| 综合 | 94.992698% | 94.990223%（同上） |
| 未满文件 | 62 | 62 ✅ |
| excluded | 1,456 | 1,456 ✅ |

### 2.1 环境对齐：新工作树缺 media-toolchain

首跑得到 `6470 passed / 22 skipped`，比计划少一个通过。原因不是代码：
`tests/integration/test_smart_edit_pipeline_real_media.py` 的 skipif 看的是
`frontend/src-tauri/target/debug/media-toolchain/bin/ffmpeg`，而新建工作树的 Cargo
target 是空的。主树那对二进制（2026-07-27 构建）按 §8.1 的做法用 `cp -c -p -R` clone
过来，0.006 秒、SHA-256 一致，测试随即恢复通过，测试数与计划对齐。

**这属于 §8.1 已记录的坑的同一类**：要不要某个产物，得按测试实际读什么去判，不能按
「这批任务跟它有关吗」去判。不查的话，后面每批比对「passed 不退化」都会带着一个来源
不明的 -1。

### 2.2 已确认的 flaky 分支（必须在 COV-02 消除）

两次全量的**唯一**差异是 `executor/adaptive_frame_extraction.py` 的分支 `(1040, 1043)`：

```python
def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:      # 1040
        with suppress(OSError):     # 1041  进程还活着 → kill
            process.kill()
    try:                            # 1043  进程已自行退出 → 直接 wait
```

`missing_lines` 两次完全相同，1040/1043 两行都是 covered；变的只是**走到哪一侧**。
单跑 `tests/unit/executor/test_adaptive_frame_extraction.py` 三次复现：

| 跑次 | `1040→1041` | `1040→1043` |
|---|---|---|
| 1 | 走到 | 走到 |
| 2 | 走到 | 未走到 |
| 3 | 走到 | 未走到 |

即 `process.poll()` 那一刻子进程是否已经退出，是真实的时序竞争，不是环境差异。

**对本轮的三点影响：**

1. 权威基线是 **2,023～2,024 个缺口**，其中 1 点会漂。计划记录的 2,023 / 94.992698%
   对应 `1040→1043` 恰好走到的那一次；
2. `adaptive_frame_extraction.py` 是 COV-02 的最大单文件（178 点），这个竞态就在里面。
   收口时必须给 `_kill_and_reap` 写**确定性**测试，分别构造「进程仍在运行」与「进程已
   退出」两种状态，而不是依赖调度碰运气；
3. 计划 §8 判据「missing branches = 0」在存在 flaky 分支时无法稳定成立——某次跑到 100%
   不代表下次还是。**建议把最终判据改为「连续两次全量都 100%」**，否则这条门禁合回 main
   之后会随机变红，而那正是本轮要终结的状态。

目前两份全量样本只暴露这 1 个 flaky 分支，但样本量不足以断言只有这一个；COV-06 的最终
审计应把「连续两次全量」本身当作探测手段。

### 2.3 平台差异的已排除项

计划要求「在与 quality workflow 相同的 Ubuntu/Python 3.12.13 环境生成 coverage JSON」。
本机是 macOS，经用户确认先用 macOS 基线开工，Linux 差异在做到平台专属模块时再取。

已核实的一项**非差异**：`.github/workflows/quality.yml` 的 backend job 装依赖用的是
`uv sync --locked --dev`，而 `pyproject.toml` 声明 `default-groups = ["catalog-build",
"dev", "executor"]`。uv 的 `--group`/`--dev` 是**追加**而非排他（`--only-group` 才排他），
本机 `uv sync --locked --dev --dry-run` 对已按默认组安装的 73 包环境报 "no changes"，
证明 CI 与本机装的是同一批依赖。**因此 macOS 与 CI 之间不存在依赖组差异**，平台差异只
可能来自平台专属代码分支（见 §3.4）。

另需注意：CI 的 `uv run pytest --cov=automation_tool --cov-report=term-missing` 没有传
`--cov-fail-under=0`，而 `fail_under = 100`，所以 **quality.yml 的 backend job 当前就是
红的**。这与计划的结论一致，不是本分支引入的。

## 3. 49 处 pragma 逐项结论

按处理方式分五类。计数已核对：9+8+7+6+4+4+3+3+1+1+1+1+1 = 49。

### 3.1 死代码，应直接删除整行（9 处）→ COV-03

`agent.py:94` 的 `_reject` 签名是 `def _reject(message: str) -> Never`。mypy 靠 `Never`
就完成了类型收窄，**它后面跟着的 `raise AssertionError` 是不可达且不必要的**。正确处理
是删掉那一行，而不是想办法覆盖它——给死代码加 pragma 只是把问题藏起来。

| 文件 | 行 | 形态 |
|---|---|---|
| `executor/motion_authoring/agent.py` | 640, 1515, 1583, 2024, 2028, 2220, 2305 | `_reject(...)` 后紧跟 `raise AssertionError` |
| `executor/motion_authoring/voiceover.py` | 127 | 同上 |
| `executor/motion_authoring/authoring_workspace.py` | 34 | 同上 |

删除前需实测 mypy：若某处 `_reject()` 位于有返回值的函数末尾，删掉 `raise` 后 mypy 应
仍认为该路径不可达而不报 missing return。删除后 `ruff format --check`、`ruff check`、
`mypy` 与全量 pytest 必须全绿。

### 3.2 可由受控子进程 / 真实 CLI 覆盖（15 处）→ 各自模块批次

这类是 CLI 入口，计划 §COV-00 第 3 条点名「能通过 mock、受控子进程或真实 CLI 验收覆盖的，
删除 pragma 并补测试」。它们**不是不可达**，只是当前没有测试走这条路。

| 文件 | 行 | 内容 |
|---|---|---|
| `executor/package_manifest.py` | 244, 256, 263, 283 | `_parser` / `_read_signing_key` / `main` / `__main__` |
| `executor/macos_candidate.py` | 392, 399, 416 | `_parser` / `main` / `__main__` |
| `executor/windows_candidate.py` | 312, 319, 336 | `_parser` / `main` / `__main__` |
| `executor/__main__.py` | 3, 5, 6 | 模块入口的 import 与 `main()` 调用 |
| `executor/diagnostics.py` | 61 | `code not in _RECOVERY_DIAGNOSTIC_CODES` 早退 |
| `executor/browser_runtime.py` | 114 | `_start_playwright()` 真正启动 driver |

`executor/__main__.py` 的 3 处尤其可疑：整个文件只有 4 行，却挂了 3 个 pragma。冻结包的
入口正是它，值得单独确认「verified in a child process」这句注释指向哪个测试。

### 3.3 锁内防御断言（17 处）→ COV-05 / COV-06

`ledger.py` 的 9 处全部是同一形态：在 `BEGIN IMMEDIATE` 事务内、对刚刚 `SELECT` 并锁住的
行做 `UPDATE`，然后 `if updated.rowcount != 1: raise ValueError`。行已被锁，理论上不可能
漂移。bilibili 两个文件的 7 处与 `composition_template.py` 的 1 处同理，都是上游已经收窄过
的分支。

| 文件 | 行 |
|---|---|
| `executor/ledger.py` | 358, 854, 961, 1101, 1207, 1309, 2008, 2237, 2361 |
| `control_plane/application/bilibili_archive_publishing.py` | 700, 848, 890, 905 |
| `control_plane/application/bilibili_archive_reconciliation.py` | 108, 514, 629 |
| `executor/motion_authoring/composition_template.py` | 127 |

**结论：保留，但要求上层不变量测试。** 这类断言的价值在于「如果上游收窄被改坏，这里会
炸而不是静默走错」。用 mock 强行把 `rowcount` 改成 0 来覆盖它，测的是 mock 不是行为——
计划 §9 明确把「把 mock 调用次数当成业务行为的唯一证据」列为假收口。正确做法是确认对应的
上层收窄（锁的获取、状态机转换）本身有测试。

### 3.4 平台专属，本机 Linux/CI 不可达（8 处）→ COV-06

| 文件 | 行 | 原因 |
|---|---|---|
| `executor/macos_candidate.py` | 153, 170, 302 | `codesign` 验签 / ad-hoc 重签 / 跑 PyInstaller |
| `executor/windows_candidate.py` | 223 | 跑 PyInstaller |
| `executor/macos_candidate.py` | 293, 382 | OS race 归一化 `except` |
| `executor/windows_candidate.py` | 214, 302 | 同上 |

前 4 处确实需要真实打包 runner，保留。后 4 处的 `except (OSError, ...)` **可以**用 mock
注入 OSError 覆盖，属于 §3.2 那一类，留待 COV-06 判定。

### 3.5 唯一模块级 omit

`pyproject.toml` 的 `[tool.coverage.run] omit = ["src/automation_tool/executor/windows_acl.py"]`
已确认是唯一一项，理由（Linux 覆盖进程无法导入 Win32 DLL）成立。本轮不新增第二项。

## 4. 完成判据对照

| 判据 | 状态 |
|---|---|
| Linux 基线可复现 | 已按用户决策改为「macOS 基线开工，Linux 差异后补」；依赖组非差异已证（§2.3） |
| 测试数与缺口绝对数被记录 | ✅ 6,471/21，缺口 2,023～2,024（含 1 个 flaky，§2.2） |
| 排除项清单有逐项结论 | ✅ 49 处分五类逐项给出结论 |
| `windows_acl.py` 是唯一 omit | ✅ 已确认 |

## 5. 交给后续批次的待办

| 项 | 去向 |
|---|---|
| 9 处 `_reject()` 后的死代码 `raise AssertionError` | COV-03 |
| 15 处 CLI 入口 pragma（含 `__main__.py` 那 3 处存疑） | 各自模块批次 |
| 17 处锁内防御断言 | 保留，COV-05/06 确认上层不变量有测试 |
| 4 处 `except OSError` 打包竞态归一化（可 mock 覆盖） | COV-06 |
| `_kill_and_reap` 时序竞态分支确定性化 | **COV-02（必做）** |
| 最终判据改为「连续两次全量 100%」 | COV-06，待用户确认 |
