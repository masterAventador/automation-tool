# 出正式包会破坏本机开发环境（已修）

用户可操作：否

证据类型：分层实现

> 状态：✅ 已完成
>
> 日期：2026-08-04
>
> 提交：本文件所在提交

## 现象

2026-08-04 出完一个正式发布包之后，`npx vitest` 立即失效：

```text
Error: Cannot find module '/…/frontend/node_modules/vitest/vitest.mjs'
```

`frontend/node_modules` 从正常规模掉到只剩 23 项，`.pnpm` 的时间戳正是出包那一刻。
恢复方式是 `CI=true pnpm install --frozen-lockfile`（普通 `pnpm install` 会因为
`ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY` 停下）。

## 机制

`build_release_package.py` 把源码物化成一份一次性快照再从里面执行自己（这一点是对的：
源码在构建中途不可能被改动）。为了不重装依赖，它把三样东西**软链**进快照：
`.local`、`frontend/node_modules`、`backend/.venv`。

pnpm 在快照上下文里穿过那条软链，对**操作者那份真实目录**执行了重建——出包日志第 11 行
就是原话：

```text
Recreating /Users/aventador/sourceCode/automation-tool/frontend/node_modules
```

而 `node_modules/<包名>` 是指向 `.pnpm/…` 的**相对软链**，快照跑完即删（实测出包后
`.local/release/source-snapshot-*` 一个都不剩），链接随之失效。

## 为什么值得修

**代价不在于要重装一次，在于报错完全不指向出包。** 一个刚跑完发布流程的人，看到的是
测试框架找不到自己，而那条命令是半小时前跑的、中间隔着签名和两轮公证。今天就是这么
丢掉时间的：先怀疑 vitest、再怀疑 pnpm、最后才回到出包。

## 修法

`frontend/node_modules` 改用 APFS `clonefile`（`cp -c -p -R`）拷进快照，让构建有自己
那一份；`.local` 与 `backend/.venv` 保持软链——`.local` 本来就是构建的工作目录，写它是
预期行为。

代价实测：465 MB、9.7 秒，与原始共享数据块，占 27 分钟出包的 0.6%。这正是
`scripts/new_worktree.py` 处理 `vendor/` 用的同一招（CLAUDE.md §8.1）。不支持 clone 的
文件系统自动退回普通拷贝——只慢不错。

## RED

`test_node_modules_reaches_the_snapshot_without_exposing_the_operators_copy`，实跑：

```text
AssertionError: True is not false : 快照里的 node_modules 不能是指向操作者那份的软链
```

判据写成**隔离**而不是形态：断言快照那份是独立目录，且往它里面写不影响操作者那份。

## GREEN

```bash
backend/.venv/bin/python scripts/test_build_release_package.py    # Ran 14 tests, OK
backend/.venv/bin/python scripts/test_release_assembly.py         # OK
backend/.venv/bin/python scripts/test_customer_demo_release.py    # OK
backend/.venv/bin/python scripts/test_release_identity.py         # OK
uvx ruff check --config backend/pyproject.toml <两个文件>          # 7 条，与改动前基线相同
```

## 失败矩阵

- clonefile 不受支持（非 APFS）：退回普通 `cp -p -R`；
- 两种拷贝都失败或目标不是目录：`ReleaseFailed`，不静默继续；
- 快照路径被占用：沿用既有的 `dependency path is occupied` 拒绝；
- `.local` / `backend/.venv` 行为不变，仍为软链。

## 正常用户路径验收

不适用——这是构建期基础设施，不新增用户入口。

## 真实边界

**端到端只会在下一次真实出包时才被证明。** 本次只有单元级隔离断言：跑完一次完整发布
之后 `npx vitest` 仍然可用，那一条尚未观测到。下次出包（EB-11 重新扫码前必须重出）
时复核，届时把结果补记在此。

## 清理

测试用临时目录，`tempfile.TemporaryDirectory` 自动回收。

## 文档变化

本文件为新增；`CQ-05.md` 2026-08-04 小节里「已登记，未修」的那条对应本次修复。

## 遗留项

- 端到端复核（见「真实边界」）；
- `.local` 与 `backend/.venv` 仍为软链。前者是有意的；后者目前没有观测到被构建改写，
  若将来 uv 出现同类行为，按同一判据处理。
