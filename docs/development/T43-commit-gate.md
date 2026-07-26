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

## 5. 触发方式

**结论：`pre-push` 挂快档，且不自动安装。**

理由：

- 快档 **6–7 秒**，挂在推送前不会把工作流卡死；而按现在「每个任务默认推送」的节奏，
  推送前正好是每次改动出门的唯一必经点。
- **不挂 `pre-commit`**：本地连续提交很频繁，每次 7 秒会让人想绕过；
  更重要的是 `pre-commit` 跑在**工作树**上——那正是被遮蔽的那个对象，
  挡不住今天这两个缺陷。`pre-push` 拿到的是**将要存在于远端的那个 sha**，
  正是必须能构建的东西。

用法：

```bash
# 显式跑
backend/.venv/bin/python scripts/commit_gate.py <commit>

# git pre-push 协议（stdin 读 <local ref> <local sha> <remote ref> <remote sha>）
backend/.venv/bin/python scripts/commit_gate.py --pre-push
```

`commits_to_gate()` 只取 local sha，并跳过删除分支时的全零 sha。

### 5.1 为什么不由脚本自动装 hook

1. **`.git/hooks/pre-push` 已被 git-lfs 占用**，直接覆盖会破坏 LFS；
   必须链式调用，而链式写法要根据本机 LFS 版本决定，属本机事实。
2. **hook 不随仓库分发**，装在谁的机器上是各人的选择，不该由一次任务替所有人决定。
3. 它会**阻塞推送**——这是工作流层面的改变，应当由人明确启用。

建议的启用方式（人工执行一次，与 git-lfs 共存）：

```sh
# .git/hooks/pre-push 中，在既有 git-lfs 那行之前插入：
backend/.venv/bin/python scripts/commit_gate.py --pre-push || exit 1
```

绕过（应当罕见并说明理由）：`git push --no-verify`。

## 6. Windows 半边：Node 不是快档的阻塞项

复核结论**修正了先前的判断**：

| 项 | 验收机 | 项目要求 | 结论 |
| --- | --- | --- | --- |
| pnpm | 11.9.0 | `>=11 <12` | 一致，且与 CI 相同 |
| Python | 系统 3.14.0 | `>=3.12,<3.13` | **无关**——`uv sync --locked` 自带 3.12.13 解释器 |
| rustc | 1.96.1 | — | 可用 |
| Node | **v22.20.0** | `engines: >=24 <27` | 低于下限，但**不阻塞快档**（见下） |

Node 之所以不阻塞快档：仓库**没有 `.npmrc`**，因此 `engine-strict` 是 pnpm 的默认值
`false`——`engines` 只警告不拒绝；而快档只跑 `tsc`，TypeScript 自身的要求是
`node >=14.17`。

但**慢档必须对齐**：vitest、playwright、Tauri 构建的行为与 Node 版本相关，
在与生产不同的运行时上验收，正是本项目吃过亏的那类错误。
建议在做慢档之前用 `winget` 装 Node 26.x 对齐 CI。**本次未安装**——
那是对一台共享验收机的系统改动，应当由人明确同意后再做。

## 7. 未做

1. **hook 未安装**（有意，理由见 §5.1）；`--pre-push` 模式本身已实现并实跑。
2. **Windows 上未实跑门禁。** 取码方式建议用 `git bundle` + ssh 推到机上裸库，
   绕开 GitHub 凭据与计费问题（Actions 自 07-25 16:50 起因计费每次 5 秒内失败）；
   未实现。
3. **未在验收机安装 Node 26**（有意，见 §6）。
4. **macOS 专属半边（codesign / DMG / Tauri 构建）未纳入**，那属慢档。
5. **慢档整体未实现**（pytest、vitest、cargo test、playwright、打包）。
6. 门禁只在 macOS 上跑过。
