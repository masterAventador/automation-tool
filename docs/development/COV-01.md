# COV-01 对齐已有测试的收集边界

用户可操作：否
证据类型：分层实现

> 状态：动效部分（计划 §COV-01 第 1～3 步）已完成并验证；素材 worker 拆分（第 4～5 步）
> 未开始，见 §6。
> 上游计划：`docs/development/2026-08-03-backend-coverage-debt-plan.md`
> 前置：[COV-00](COV-00.md)
> 分支：`coverage/backend-100`（worktree `wt/coverage-100`）

## 1. 要解决什么

`scripts/test_motion_authoring_agent.py` 的 118 条确定性测试真实执行
`automation_tool.executor.motion_authoring`，但 `backend/pyproject.toml` 设了
`testpaths = ["tests"]`，backend 的 coverage 运行从不收集它们——**它们覆盖的 359 个点
被算成了债**。这不是缺测试，是测试站错了地方。

因此本批不写任何新的覆盖率测试，只把已有资产移进收集边界。

## 2. RED

新增 `scripts/test_motion_authoring_collection_boundary.py`，先跑，四条断言按预期失败：

- `test_canonical_tests_are_collected_by_the_backend_pytest_run` — canonical 文件不存在；
- `test_compatibility_entry_really_executes_the_canonical_suite` — sentinel 模块名不在输出里；
- `test_compatibility_entry_holds_no_assertions_of_its_own` — 当时的脚本满是断言；
- `test_canonical_file_no_longer_patches_sys_path` — 文件不存在。

## 3. GREEN

### 3.1 迁移

`git mv scripts/test_motion_authoring_agent.py backend/tests/unit/executor/`，并：

- 删掉 `import sys` 与 `sys.path.insert(0, str(ROOT / "backend/src"))`——在 `backend/tests`
  里 `automation_tool` 已由 venv 的 editable install 提供；
- `ROOT` 的层级从 `parents[1]` 改为 `parents[4]`（vendor 与 contracts 路径依赖它）；
- 7 处 `# noqa: E402` 随之删除，`ROOT` 定义下移到 import 之后。

### 3.2 兼容入口

`scripts/test_motion_authoring_agent.py` 重建为纯转发。它必须继续工作，因为：

- `run_script_tests.py` 的 `discover()` 用 glob 派生 `scripts/test_*.py`，不是手工清单；
- `contracts/video/motion-render-canvas.v1.json` 与 `motion-one-sentence-brief.v1.json`
  的 `enforcedBy` 都指向这个路径；
- BM-15/BM-16 验收与慢提交门禁直接调用它。

**实现细节（踩过）**：`load_tests` 是 unittest 的惯用钩子，但 **pytest 不实现该协议**，
而历史记录显示这个文件两种方式都被调用过（`python scripts/...` 与 `pytest scripts/...`）。
用 `load_tests` 会让 pytest 那条路收集到 0 个测试。因此改为用 `importlib` 按固定模块名
加载 canonical 文件，再把 TestCase 类绑进入口的命名空间——两个 runner 都能发现。

固定模块名 `canonical_motion_authoring_agent_tests` 同时充当 sentinel：它出现在
`unittest -v` 的每个测试名里，是「入口确实执行了 canonical 文件」的证据。

### 3.3 门禁是真门禁（按 §8.3 验证）

计划要求门禁「真实运行兼容入口」而不是「断言入口路径存在」。为确认它不是不可能失败的
断言，故意破坏两次：

| 破坏 | 结果 |
|---|---|
| 入口不再转发（注释掉绑定调用） | `test_compatibility_entry_really_executes_the_canonical_suite` FAIL，输出 `Ran 0 tests` |
| 入口自己定义一个 TestCase | `test_compatibility_entry_holds_no_assertions_of_its_own` FAIL |
| 恢复 | 4 tests OK |

「入口不含断言」这条用 AST 判定而非搜字符串 `class `：后者会因为文档里出现该词而误报，
也会被换个写法绕过。形状不是语义。

## 4. 量化验证：与计划逐字命中

| | 覆盖率 | 缺口 | 测试 |
|---|---|---|---|
| 基线 | 94.990223% | 2,024 | 6,471 passed / 21 skipped |
| COV-01 后 | **95.881290%** | **1,664** | 6,589 passed / 21 skipped |

计划预期 `95.881290%`、总债减少 359。实测减少 **360**——差的 1 点正是 COV-00 §2.2 记录
的那个 flaky 分支（基线是 2,023 还是 2,024 取决于它），**终点 1,664 与百分比逐字一致**。

动效链逐模块也与计划的预测全部吻合：

| 模块 | 基线 | 现在 | 计划预测 |
|---|---:|---:|---:|
| `agent.py` | 445 | 123 | 123 |
| `entry.py` | 64 | 55 | 55 |
| `voiceover.py` | 52 | 52 | 52 |
| `part_typography.py` | 34 | 34 | 34 |
| `film_assembly.py` | 24 | 24 | 24 |
| `component_host.py` | 21 | 21 | 21 |
| `segment_concat.py` | 19 | 19 | 19 |
| `part_workspace.py` | 25 | 18 | 18 |
| `authoring_workspace.py` | 31 | 13 | 13 |
| `slot_probe_browser.py` | 12 | 12 | 12 |
| `composition_template.py` | 12 | 11 | 11 |
| `part_document.py` | 6 | 6 | 6 |
| `resources.py` | 2 | 2 | 2 |
| `__init__.py` | 2 | 0 | 已到 100% |
| **合计** | **749** | **390** | 约 390 |

## 5. 计划未预见的成本：文件首次进入质量门禁

这个文件在 `scripts/` 下时**从未被任何 lint 或类型检查覆盖**——`quality.yml` 的
`ruff`/`mypy` 都以 `working-directory: backend` 运行，`scripts/` 不在范围内。搬进
`backend/tests/` 后它立刻进入 ruff + mypy strict，暴露 **37 处既有问题**（5 ruff + 1 格式
+ 32 mypy，其中 1 处 `B023` 闭包捕获告警）。

这些不是本次引入的，但不修就是让 CI 红。按「不得降低门禁来制造进度」全部修复，没有加
文件级豁免：

- 根因集中——7 个 helper 的返回标注 `dict[str, object]` → `dict[str, Any]` 消掉 22 个
  （测试读的是外部 JSON 契约，`Any` 恰当；运行时断言仍然验证真实值）；
- 2 处**测试故意传错类型**验证拒绝行为（`slot_probe="not-a-probe"`、
  `narrator="not-a-narrator"`）用逐处 `# type: ignore[arg-type]` 标注意图，而不是放宽签名；
- `__exit__` 的返回类型按 mypy 要求改 `Literal[False]`；
- `B023` 加 noqa 并写明理由：`_urlopen` 只在同一次迭代的 `with` 块内被调用，闭包不逃逸，
  late binding 观察不到。

**过程中被 §8.4 救了一次**：用 `replace_all` 批量删 `# noqa: E402` 时，闭合括号被并进了
上一行。语法仍然合法、118 条测试照常全绿，只有逐行看 diff 才发现。已回滚重做。

## 6. 本批未做

计划 §COV-01 第 4～5 步（拆 `scripts/test_material_video_worker.py` 的 74 条、在允许
socket 的宿主复跑）尚未开始，留待 COV-01b。它与动效部分互不依赖。

## 7. 验证命令与结果

```
uv run mypy                     Success: no issues found in 617 source files
uv run ruff check .             All checks passed!
uv run ruff format --check .    659 files already formatted
uv run pytest tests/unit/executor/test_motion_authoring_agent.py    118 passed
backend/.venv/bin/python scripts/test_motion_authoring_agent.py     Ran 118 tests OK
backend/.venv/bin/python -m pytest scripts/test_motion_authoring_agent.py   118 passed
backend/.venv/bin/python scripts/test_motion_authoring_collection_boundary.py   Ran 4 tests OK
uv run pytest --cov=automation_tool    6589 passed / 21 skipped, 95.881290%
```

最后一条按 `fail_under=100` 退出 1，这是补债完成前的预期状态，不是失败。
