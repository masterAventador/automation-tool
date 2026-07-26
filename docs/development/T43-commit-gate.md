# T43 提交门禁

> 状态：🚧 实现中（快档已实现并实跑通过；Windows 半边与触发方式未接线）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交

## 1. 为什么要有它

2026-07-26 当天两个缺陷进了 `main`，本机检查全部通过：

| 缺陷 | 形态 | 现有检查为何看不见 |
| --- | --- | --- |
| `c0cc760` | 改了发布口类型，漏改两个消费方，`main` 40 分钟编译不过 | 作者工作区里有未提交的修复，本机 `tsc -b` 因此通过 |
| `run_bm_05_acceptance.py` | 调 `lint_composition` 少传 keyword-only 的 `entry_path` | `[tool.mypy] files = ["src","tests"]` 不含 `scripts/`；`ruff --select F,E9` 无类型推断 |

两者的共同点是**验证对象错了**：验的是工作树，而工作树上有别人的、或自己没提交的改动。
CI 里其实有 `pnpm typecheck`，但 `git rev-list --count origin/main..HEAD` 当时是 35，
**最近 35 个提交一个都没推送**，门禁躺在 `git push` 的另一侧；且账号计费异常，
GitHub Actions 从 07-25 16:50 起每次 5 秒内失败，**推了也跑不起来**。

结论：门禁不能依赖远端，必须在本地、按提交跑。

## 2. 判据

**唯一硬判据：在从某个提交提取出来的树上跑，绝不读工作树。**

实现用 `git archive`，不用 `git worktree add`：本仓多方并行，
`git worktree add` 会改 `.git/worktrees` 这个**共享可变状态**。当天三起事故
（工作树遮蔽、共享 index 把别人的文件卷进提交、模块在定位过程中被搬走）
根源都是共享可变状态。`git archive` 只读对象库，把这一整类风险从「小心使用」
降级成「结构上不可能」。

唯一允许进入被检查树的 commit 外内容是 `frontend/node_modules`（软链）——
它是可从已提交锁文件重建的构建产物。**任何源码都不允许**。

## 3. 快档做什么

`scripts/commit_gate.py <commit>`，实跑 **7 秒**：

| 检查 | 内容 |
| --- | --- |
| `self-check` | 往被检查树里植入一个已知缺陷，断言能被抓到 |
| `typescript` | `npx tsc -b --pretty false` |
| `python` | mypy 覆盖 `scripts/`，MYPYPATH 取自**被检查的那个提交** |

### 3.1 self-check 为什么必须有

`--ignore-missing-imports` 会把解析不到的 import 变成 `Any`，
而对 `Any` 的调用永远不报错。所以 **MYPYPATH 配错时，mypy 不会报错，它会安静**，
而安静和成功长得一模一样。实测踩过一次：`tools/motion-authoring` 已被 `889cf9e`
搬走，同一份探针从"应当报错"变成 `Success: no issues found`。

因此每次运行都先植入一个「少传必需关键字参数」的探针并断言抓得到，
抓不到就直接判定**本次运行不可信**，而不是报告通过。

### 3.2 阻塞哪些错误码：按实测定，不按语感定

`scripts/` 在配好 MYPYPATH 后的现状（实测）：

```text
Found 282 errors in 53 files (checked 207 source files)
  79 attr-defined    74 arg-type      32 index      29 union-attr
  21 call-overload   13 operator       8 return      8 misc  …
   0 call-arg
```

| 码 | 数量 | 处置 | 理由 |
| --- | --- | --- | --- |
| `call-arg` | **0** | **阻塞** | 今天第二起事故就是这个码；基线为空，可以直接以「必须干净」上线 |
| `attr-defined` | 79 | 记录不阻塞 | 同族缺陷，但基线不为空 |
| `arg-type` | 74 | 记录不阻塞 | 同上 |
| 其余 | 89 | 记录不阻塞 | 多为标注噪音 |

**这一条纠正了一个先前的提案**：把 `arg-type` 与 `attr-defined` 一并阻塞听起来更严，
但那会让门禁**上线即红 153 条**，变成一个跟所有并行工作线撞车的大重构，
结局多半是被关掉。`BLOCKING_ERROR_CODES` 的成员资格是**实测属性**，
由 `check_python_baseline_is_clean_for_blocking_codes` 持续守住：
哪天某个码清零了，就可以加进来。

### 3.3 MYPYPATH 取自提交

`discover_sys_path_roots()` 扫描被检查树里 `scripts/*.py` 的 `sys.path.insert`，
重新推导根目录，而不是信一份手写常量。当前推导结果：

```text
backend/src   scripts   tools/browser-use-contract
tools/motion-authoring   workers/material_montage
```

**已知且写明的洞**：`run_bu_02_acceptance.py` / `run_bu_07_acceptance.py`
从环境变量（`BU02_HARNESS_DIR`、`BU07_*`）插入运行期才存在的目录，静态检查覆盖不到。
由 `DYNAMIC_SYS_PATH_MARKERS` 显式排除——是命名的洞，不是静默的洞。

## 4. RED / GREEN

RED：`scripts/test_commit_gate.py` 先写，运行得到
`ModuleNotFoundError: No module named 'commit_gate'`。

GREEN：

```text
backend/.venv/bin/python scripts/test_commit_gate.py
  ok   check_mypy_path_covers_every_static_sys_path_insert
  ok   check_every_declared_root_exists
  ok   check_gate_judges_the_commit_not_the_working_tree
  ok   check_gate_detects_an_injected_typescript_defect
  ok   check_gate_detects_an_injected_python_defect
  ok   check_python_baseline_is_clean_for_blocking_codes
  ok   check_checkout_is_removed_after_use
  commit gate checks passed (7 checks)          9.9 秒

backend/.venv/bin/python scripts/commit_gate.py HEAD
  ok self-check / ok typescript / ok python
  commit gate passed on 4596447                 7.0 秒
```

其中 `check_gate_judges_the_commit_not_the_working_tree` 是本门禁的存在理由本身：
它在工作树里写一个语法错误的文件，断言提取出来的树里**没有**它。

## 5. 未做

1. **触发方式未接线。** 7 秒的代价可以挂 `pre-push`；但 `pre-push` 是可绕过的
   （`--no-verify`），且本仓 `.git/hooks` 现由 git-lfs 占用，需与之共存。
   建议：`pre-push` 调用快档 + 保留显式命令供随时手动跑。**待定，未实现。**
2. **Windows 半边未接线。** 已确认验收机工具链：`pnpm 11.9.0`（与 CI 一致）、
   `uv` 已装（`uv sync --locked` 自带 3.12.13 解释器，**系统 Python 3.14 无关**）、
   `rustc 1.96.1`；**唯一缺口是 Node v22.20.0**，低于 `engines` 的 `>=24 <27`。
   取码方式建议用 `git bundle` / ssh 推到机上裸库，绕开 GitHub 凭据与计费问题；
   未实现。
3. **macOS 专属半边（codesign / DMG / Tauri 构建）未纳入**，那属慢档。
4. **慢档整体未实现**（pytest、vitest、cargo test、playwright、打包）。
5. 门禁只在 macOS 上跑过。
