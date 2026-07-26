# codex 并行批次交接单（第三批）

> 第二批（`cf6e5a7 merge: 合并 Codex 并行批次 2`，112 个文件）已合入 main，谢谢。**本文件已整体替换为新的一批。**
>
> 日期：2026-07-26 ｜ 主线同时在跑五条线。切法仍然是**文件面不重叠**，不是按优先级。

---

## 0. 先说第二批留下的一个真回归（主线已修，你不用再动，但请看一眼）

第二批合并后跑最终门禁，**三层红**：

```
eslint    ✖ 2 errors                      ← 这条是主线自己的（T86 测试文件里两个未使用常量）
backend   ImportError: cannot import name 'CHROMIUM_CONTRACT'
          from 'run_bm_08_acceptance'     ← 整个 integration 收集失败
scripts   同一个 ImportError              ← 54 条里挂 1 条
```

根因：`0e41d59` 为 T78 重写 `run_bm_08_acceptance.py` 时删掉了模块级的 `CHROMIUM_CONTRACT`、`DEFAULT_ARCHIVES`、`_first_existing`，**但两个导入方没跟上**——`backend/tests/integration/conftest.py:191` 与 `scripts/run_bm_16_acceptance.py:28`。

更值得注意的是：被删的 `_first_existing` 正是 `07975d8 fix(bm-08): 验收脚本 Chromium 缓存路径兼容主仓库与 worktree 双布局` 专门加的。EB-03 缓存写在主检出的 `.local` 下，而 `wt/<task>` worktree 在它下面两层，两个根都要试。**删掉它等于把那次修复一起删了。**

主线的修法不是把常量抄回去，而是搬进 `build_embedded_chromium_staging.py`——两个导入方本来就都为 `load_staging_contract` 依赖它，一处定义服务所有调用方，不再由某个验收脚本"顺便拥有"。

**这件事本身就是下面 C4 的由来。**

---

## 1. 硬约束（每条都出过事故）

1. **每棵 worktree 必须独立**：`python3 scripts/new_worktree.py <名字> [--no-vendor]`。
   **禁止把 `backend/.venv` 或 `frontend/node_modules` 软链到别的树**——`uv run --project` 会改写共享的 `site-packages/automation_tool.pth`（实测后果：主树 pytest 在测另一棵树的在飞代码，同时进行的正式包构建也在打包别人的源码）；pnpm 11 跑任何 script 前校验依赖，软链必然不匹配，它会**先删再装**，而且报错时**建议你设 `CI=true`**——照做就静默删掉主树那份。
2. **建完树先 `git switch -c <分支名>`**。脚本目前留下游离 HEAD，不建分支主线按分支名合不到你的提交（这条主线正在修，修好前请手动执行）。
3. **TDD 不可跳过**：先写测试 → **实际运行看到断言失败**（必须是断言失败，不是 import error / 文件不存在这种顺带红）→ 最小实现 → 实际运行通过。
4. **提交** `git commit -m "..." -- <明确路径>`，不要 `git add -A`；message 用中文，不加 Co-Authored-By 之类署名。
5. **不要改 `docs/demo-sprint-roadmap.md`**——多线同时改必冲突，台账由主线单点更新。你负责写 `docs/development/<任务ID>.md`。
6. **别碰这些面**（主线五条线各自占着）：`frontend/src/`、`frontend/e2e/`、`frontend/src-tauri/src/` 里更新相关的 Rust（`app_updates.rs` / `app_update_coordinator.rs` / `app_update_policy.rs` / `lib.rs`）、`contracts/` 下更新相关的 JSON。`scripts/` 主线也在动，**只有 C3/C4/C5 明确授权你改指定文件**，其余请绕开。

你这批的主战场：`backend/`、`deploy/`、`workers/`、`frontend/e2e-tauri/`、`docs/development/`。

---

## C1 — 补齐第二批的证据文件（**先做这条**）

第二批做了 19 个任务，`docs/development/` 下**一个证据文件都没有**。核实过：那次合并只动了 3 个 docs 文件（`codex-parallel-batch.md`、`customer-demo-operations-runbook.md`、`customer-demo-post-demo-cleanup.md`）。

这违反 `CLAUDE.md` §2.1：每个小任务必须有独立的 `docs/development/<任务ID>.md`，记录日期、提交、RED、GREEN、失败矩阵、真实边界、清理和文档证据，**并与代码变更在同一提交完成**。

按提交逐条补，一个任务一个文件：

| 提交 | 任务 | 提交 | 任务 |
|---|---|---|---|
| `d5e5111` 剔除 Widevine | T26 | `006078c` 复核登录态投影 | T77 |
| `d28f819` 统一执行器包资源查找 | T24 | `0e41d59`+`9325219` 视频桌面验收启动链 | T78 |
| `6197eba` 抖音注销投影超时 | T50 | `98ac834`+`2a3b293`+`92d0367` 隔离浏览器依赖 | T45 |
| `090e756` 有界隐私安全桌面日志 | T69 | `1660d21` 剔除权利未决 UTM 字体 | T40 |
| `050dfc9` 脚本与部署门禁执行证据 | T72 | `eb861c2` 登记 Big Shoulders 字体 | T41 |
| `32f8b55` 隔离只读 vendor 测试写入 | T73 | `b08fb48` 损坏视频产物启动自愈 | T61 |
| `18ed7fc` 验收缓存跟随执行器输入 | T74 | `f8cc1c1` 启动时清理过期数据 | T65 |
| `f3c6d00` 保留执行器构建失败诊断 | T75 | `00eb82f` 自动修复视频目录权限 | T66b |
| `fb6d122` 验收加载生产组合入口 | T76 | `801772e` 演示后退场清单 | T38 |

**写实话，不要补一份漂亮的事后叙述。** 当时没先跑 RED 就写「未按 TDD，事后补测试」；没做真实验收就标 `🔍 待验收` 并写清缺什么。这份台账唯一的用途是让人知道**现在到底能不能发版**——一份诚实的记录比一份好看的有用得多。

另外回答一个主线需要知道的问题：**T79（124 个验收驱动无聚合执行器、48 个只被读源码从不执行）你这批做了吗？** `scripts/test_video_studio_startup_gate_drivers.py` 新增 749 行看着相关，但主线没核实。做了就写证据，没做就明说。

---

## C4 — 让「删掉别人 import 的名字」当场失败，而不是合并之后才炸

**授权你改 `scripts/`（新增检查脚本 + 其测试）。** 这是上面那个回归的根因防线。

现状：`scripts/` 下脚本互相 import（`build_release_package.py` 一口气 import 了四个），`backend/tests/` 也 import `scripts/` 里的名字。但**没有任何东西检查这些跨文件导入是否解析得了**——`run_bm_08_acceptance.py` 少了两个名字，要等到 pytest 收集期整包炸掉才被发现，而那时它已经合进 main。

做一道能在提交前跑完的检查：**每一个跨模块导入的名字，在被导入的模块里必须真实存在。**

判据（重要）：

- 检查要**能真的红**——写完后故意删掉一个被 import 的名字，确认它报错。这一步是必须的，不是可选的自证；
- 不要只做正则文本匹配就宣称覆盖。本仓库已经吃过「绿的是源码长这样，不是能跑通」的亏（T79 那 48 个驱动正是这样）；
- 检查自身必须被聚合执行器收进去（`scripts/run_script_tests.py` 会 glob `scripts/test_*.py`）；
- **不要 import 到副作用**——有些脚本 import 即执行重活。想清楚怎么规避（`ast` 静态解析 + 只比对模块顶层符号，可能比真 import 更合适），把你的取舍写进 docstring。

顺带把这个也纳进来：**请核实第二批里还有没有别的地方因为同一次重写丢了行为**——不是丢了名字，是丢了行为。名字缺失会炸，行为缺失不会。`_first_existing` 就是后者，它没让任何测试变红。

---

## C5 — 核实那 48 条断言现在真的被执行

**授权你改 `deploy/`，以及 `scripts/` 里与聚合执行器相关的部分。**

T72 的第 ② 项说：`deploy/ingress/test_ingress_config.py`(2 条) 与 `deploy/cloud/test_cloud_deployment.py`(46 条) 共 **48 条断言无任何执行者**——pytest 的 `testpaths=["tests"]` 收不到，runner 只 glob `scripts/` 也收不到，唯一调用方式是文档里一行手敲命令。

你的 `050dfc9` 声称补齐了执行证据。**请核实它现在是不是真的被执行了。** 判据不是「代码里接上了」，而是：**跑一次聚合门禁，能不能从输出里数出这 48 条确实跑了。**

这是本仓库反复吃亏的形状：「跑了 50 条断言」和「什么都没跑就 return 0」在汇总里长得一模一样。runner 现在已经会打印 checks 数（实测 `all 54 script tests passed (674 checks)`），请确认这 48 条计入其中；数不出来就补上。

---

## C2 — T57b：按 T57 调研结论执行 e2e 入口的并 / 修 / 废

T57 调研已完成，结论**推翻了「整族长期未运行」这个前提**（见台账「✅ 验收基础设施与门禁」T57 行）。现在执行调研得出的处置。

面：`frontend/e2e-tauri/`、WebdriverIO 配置、相关 npm script。**不要碰 `frontend/e2e/`**（Playwright，主线在用）。

判据：**每一个保留下来的入口都必须有确定的执行者**；废弃的要真删干净——`CLAUDE.md` 代码删除规范要求直接删除而不是注释掉，并 grep 清理所有引用（配置、npm script、文档提及）。

---

## C3 — T25：视频线 WDIO 验收补齐真实资源前置

与你刚做的 T78 相邻但不是同一件事：视频线的 WebdriverIO 验收需要真实资源（内置 Chromium、执行器包、视频运行时）才能跑，现在缺前置声明。

`scripts/gate_prerequisites.py`（T80 引入）已经把「门禁 → 产物 → 生产者」做成单点声明，**提示信息从 `producer` 字段生成，所以指错就是命令错**。沿用它，只加声明、不动它的结构——这是 `scripts/` 下授权你碰的文件之一。

---

## C6 — backend 侧失败矩阵审计（**先出清单，不要动手改**）

`CLAUDE.md` §9 要求：每个跨进程、跨层或有外部副作用的任务，开发前必须覆盖适用的失败矩阵（非法状态转换、重复请求、多实例竞争、Sidecar 崩溃 / 超时 / 版本不匹配、断网、平台超时、幂等与结果不确定、磁盘满、权限拒绝、敏感信息泄漏、重启恢复……）。

审计 `backend/` 侧现有能力，找出哪些缺覆盖，出清单到 `docs/development/C6-backend-failure-matrix-audit.md`，按「这条缺失在真实用户场景下会怎样」排序。

要求：每条给出**具体文件与行号**，以及你判定「没覆盖」的依据（在哪个测试文件里搜的、搜的什么关键词、零命中）。**不要写「建议加强错误处理」这类无法验证的条目。** 主线看完清单再决定修哪些。

---

## 完成后

每条：相关包测试通过 + `docs/development/<任务ID>.md` 写完 + 中文提交。合并前告诉主线分支名，**台账由主线更新**。

**如果你判断某条的前提不成立，直接说前提不成立并给出证据，不要为了有产出而硬做。** 第二批 T77 那次判断是对的（前端投影与权威态其实自洽），省掉了一次无用改动——这种否定结论同样是交付。
