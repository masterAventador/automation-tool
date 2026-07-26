# T51 三组手抄清单改由权威来源推导

> 状态：✅ 已完成
>
> 日期：2026-07-26
>
> 提交：`f83c679`（事件名）、`f1513ff`（守卫标记）、`d00b6df`（CLAUDE.md 引文）

## 0. 问题形态

门禁有效性审计查实的唯一一类真问题：三组清单**在驱动里手抄**，权威来源变了之后
清单不会跟着变，而且**落后时没有任何信号**——清单少检查一项和全部检查通过，从外面
看长得一模一样。

| 驱动 | 手抄的东西 | 权威来源 |
| --- | --- | --- |
| `run_d6_11` / `run_d6_12` / `run_h8_16f` | 任务事件名（4/5/7 个） | `TaskEventType` 枚举 + Executor 协议 schema |
| `run_im_06` | 窗口守卫机制标记（7 个） | `material_video_studio_init.js` |
| `run_av_01` | CLAUDE.md 规则原文片段（5 条） | CLAUDE.md |

修法照仓库现成的两个样板：`test_video_studio_acceptance_scope.py`（从 wdio 配置自动发现
spec 全集再比对）和 `commit_gate.discover_sys_path_roots`（每次运行从源码重新推导，声明
落后于代码时门禁自己失败）。**从权威来源读出全集，跟驱动声明的比对，不一致就红。**

三组的比对逻辑本身也必须是推导的，不能变成第四张手抄表。

---

## 1. 任务事件名（`scripts/test_acceptance_event_vocabulary.py`）

> 提交：本小节所在提交

### 1.1 为什么这组是隐患

三个驱动把事件名写成字符串字面量做断言。这些字面量是另一处词表的手抄副本，两边没有
任何连接。而这三个驱动要 Docker、真 PostgreSQL、构建好的 Tauri App 和几分钟墙钟时间，
**不在聚合套件里，也不在 CI 里**：契约里改个事件名，驱动就在断言一个产品不再发出的名字，
唯一能说出这件事的是一次没人执行的运行。

### 1.2 两边都是推导的

| 边 | 推导方式 |
| --- | --- |
| 权威词表 | `TaskEventType` 闭合枚举（项目规则 §3：任务事件以 Python 为唯一来源）+ `contracts/protocol/executor-v1.schema.json` 里 `message_type` 的 const/enum |
| 驱动声明 | glob `scripts/run_*.py`，正则读出源码里的字面量 |

**本文件不含任何事件名清单，也不含任何驱动文件名清单**，所以它不会变成第四份副本；
明天新写的驱动当天就被覆盖。实测：41 个词表名、15 个驱动、18 个在用的名字。

### 1.3 变异检验（四次，两次真的抓出问题）

| 变异 | 结果 |
| --- | --- |
| A 权威侧改名：schema 里 `action.execute` → `action.execute_renamed` | ✅ 红：`run_h8_16f_acceptance.py spells action.execute` |
| B 声明侧改名：驱动写 `task.targets_confirmed_v2` | ❌ **第一次green**——见下 |
| B2 声明侧改名：驱动写 `task.awaiting_approval` | ✅ 红 |
| C 权威侧解析失效：schema 里 `message_type` → `message_kind` | ✅ 红：`only 0 message types were read ... this run proves nothing` |

**变异 B 第一次是绿的，这是本组最值钱的一次检验。** 原正则后缀写成 `[a-z_]+`，
遇到带数字的新名字（`task.targets_confirmed_v2`）直接不匹配，于是这个名字**从扫描里消失**，
门禁少检查一个名字却报告通过——正是它要防的那种失效形态，出现在它自己身上。
改成 `[A-Za-z0-9_.\-]+` 后 B 复跑变红。当前树上宽窄两版扫出的都是同样 18 个名字，无误报。

变异 C 对应 `commit_gate` 的 self-check 思路：权威侧读成空集时，所有声明都会"合法"，
门禁会安静地全绿。所以空集判为**本次运行不可信**，而不是通过。

### 1.4 GREEN 与清理

```
./backend/.venv/bin/python scripts/test_acceptance_event_vocabulary.py
18 event names across 15 acceptance drivers all exist in the 41 name contract vocabulary
```

mypy --strict 通过，ruff（F,E9）通过。每次变异后 `git checkout --` 还原，
`git status` 确认工作区只剩新增文件。因为文件名是 `test_*.py`，
`scripts/run_script_tests.py` 会自动发现它，无需登记。

---

## 2. 窗口守卫标记（`scripts/test_material_studio_guard_declaration.py`）

> 提交：本小节所在提交

### 2.1 为什么这组是隐患

`run_im_06_acceptance.py` 声明 7 个标记，逐个断言它们出现在
`material_video_studio_init.js` 里。**这个方向已经在查，而且很响亮**：脚本里少了某个标记，
驱动直接失败。没人查的是反方向——**脚本长出新守卫，声明不会知道**。

实测差距：脚本跑 7 步守卫流水线、有 3 种失败关闭原因，而声明只覆盖了其中 2 步和 2 种原因。
明天加第 8 个守卫，所有检查照样全绿，覆盖率却比前一天更低。少检查和全部通过，
从外面看长得一模一样。

### 2.2 推导规则

| 单元 | 推导方式 |
| --- | --- |
| 流水线步骤 | `reconcile()` 实际调用的文件级函数 |
| 失败关闭原因 | 传给失败汇聚函数的字面量（含模板前缀 `content_policy_`） |
| 驱动声明 | 从 `run_im_06_acceptance.INITIALIZATION_GUARD_MARKERS` 导入，不在门禁里重抄 |

两个非守卫的管道件**按角色排除，不按名字排除**：失败汇聚函数 = 接收原因字面量的那个函数；
DOM 取值函数 = 单表达式返回 `document.` 成员的那个函数。按角色排除意味着改名不能把管道件
混进守卫集，也不能把守卫挤出去。

**第二份清单**：`material_video_studio.rs` 的 `theme_tests` 用自己的手抄清单钉同一个脚本。
一个来源两份清单必然悄悄分叉，所以门禁要求 Rust 钉的每个标记驱动也必须钉——驱动保持超集，
不允许出现只有一边知道的标记。为此声明补进了 `制作服务设置`、`素材\s*API`、`120_000`。

### 2.3 RED → GREEN

```
RED   ImportError: cannot import name 'INITIALIZATION_GUARD_MARKERS'（驱动没有可读的声明）
RED   AssertionError: ... IM-06 now covers less of the guard than the script implements:
      audit, hasRequiredStructure, initialization_error, installTheme,
      productizeHeaderAndSettings, removeTour
GREEN IM-06 pins all 7 guard steps, all 3 fail-closed reasons and all 6 markers of the Rust list
```

声明从 7 条扩到 16 条，`require_initialization_guard()` 改为遍历该常量，不再内联手抄。

### 2.4 变异检验（五次）

| 变异 | 结果 |
| --- | --- |
| A 声明退回原来的 7 条 | ✅ 红，逐个点名 6 个没被钉住的机制 |
| B 声明把 `installTheme` 写错名 | ✅ 红 |
| C 声明删掉 Rust 也钉的 `120_000` | ✅ 红：`两份清单已经开始分叉` |
| D 声明加一个脚本里没有的标记 | ✅ 红：`那些 pin 对出厂的守卫什么都没断言` |
| E **脚本长出第 8 个守卫** | ✅ 推导集合随之变大 |

变异 E 是本组最关键的一次：`material_video_studio_init.js` 属于另一条线的作业面，不能改，
所以直接把推导函数喂给一份改过的源码字符串验证——往 `reconcile()` 里加 `blockDownloads()`
之后推导集合从 7 变 8（门禁随即会红），把 `structure_timeout` 改名后原因集合跟着改名，
把 `reconcile` 整个改名则触发自检：`has no reconcile pipeline to read; this check would
demand nothing and pass`。**证明这个集合是每次从源码读出来的，不是写死的。**

### 2.5 GREEN 与清理

mypy --strict 对新文件无告警，ruff（F,E9）通过。每次变异后原样还原并复跑，
`git status` 确认工作区只剩本组改动。未触碰 `material_video_studio_init.js`
与 `material_video_studio.rs`（另一条线的作业面），两者都只读。

---

## 3. CLAUDE.md 规则片段（`scripts/test_embedded_browser_baseline_declaration.py`）

> 提交：本小节所在提交

### 3.1 为什么这组是隐患

AV-01 是一条绊线：内置 Chromium 决策写进 CLAUDE.md、ADR 和四份架构文档，
`run_av_01_acceptance.py` 断言这些措辞还在。**针对"规则被删掉"它有效且响亮。**

针对"基线被扩写"它什么都不做。驱动抄了 5 条 CLAUDE.md 片段，其中**落在 11 条强制浏览器
规则里的只有 1 条**。明天加第 12 条规则，或者把没被引用的某条悄悄改弱，AV-01 照样全绿——
它守的从来只是它碰巧抄到的那一句。守 1/14 和守 14/14 在干净的树上给出同一个答案，
这正是没人发现它是哪一种的原因。

### 3.2 推导规则与两个方向

规则集每次从 CLAUDE.md 重读：浏览器章节的 `text` 围栏基线块 + 强制规则条目，
**按标题文字定位而不是按章节号**（章节号会变，锚在 `## 5.` 上会在插入新章节那天悄悄读空）。

| 方向 | 判据 |
| --- | --- |
| 每条规则都被引用 | 没有任何引文落在某条规则里 → 那条规则 AV-01 没在守 |
| 每条引文只命中一条规则 | 一条引文同时命中两条规则 → 它哪条都没钉住 |

第二个方向是实测出来的必要条件：原来的 `内置 Chromium` 同时命中围栏基线块和第一条强制规则，
两条里任意一条被改写它都还在，**读起来像覆盖，实际什么都不保证**。

### 3.3 RED → GREEN

```
RED   AssertionError: '内置 Chromium' matches 2 rules（原声明里的松引文）
RED   AssertionError: AV-01 quotes 1 of the 14 mandatory browser rules in CLAUDE.md,
      so it holds the line on only part of the baseline: （13 条逐条列出）
GREEN AV-01 pins all 14 CLAUDE.md browser rules with 15 quotes
```

声明拆成两个常量：`CLAUDE_MD_BROWSER_RULE_QUOTES`（受门禁约束，15 条逐句引文）和
`CLAUDE_MD_PROCESS_QUOTES`（台账与验收基线，3 条，不属于浏览器规则）。

### 3.4 变异检验（四次）

| 变异 | 结果 |
| --- | --- |
| A 声明退回原来的 5 条片段 | ✅ 红：松引文命中 2 条规则 |
| B 只留原来那条精确引文 | ✅ 红：14 条里只守住 1 条，逐条列出漏掉的 13 条 |
| C **CLAUDE.md 长出第 15 条规则** | ✅ 推导集合 14 → 15，新规则立即进入未引用清单 |
| C2 **把某条规则悄悄改弱**（"浏览器必须默认有界面" → "可见性由运行模式决定"） | ✅ 该条进入未引用清单 |
| D 浏览器章节标题改名 | ✅ 自检：`this check would demand nothing of AV-01 and pass` |

C / C2 / D 走的是"把推导函数喂给改过的文档字符串"——CLAUDE.md 是项目规则本体，
不为了验证门禁去改它。C2 是这组最重要的一次：**改弱一条规则而不是删掉它**，
正是原来那条绊线完全看不见的形态。

### 3.5 GREEN 与清理

`run_av_01_acceptance.py` 实跑通过（`AV-01 embedded-browser architecture baseline
acceptance passed`），mypy --strict 与 ruff（F,E9）通过。变异全部还原后复跑两者均绿，
`git status` 确认没有改到 CLAUDE.md 或其他线的文件。

---

## 4. 三组之外

审计线判定"不该动"的两类本次也确实没动：`run_c10_10` / `run_c10_11` 点名的 pytest 用例
（权威运行者是 pytest，用例改名会响亮失败），以及那些"引用不存在路径"的命中（按需构建的
暂存归档、`run_h8_08` 故意投毒的浏览器发现路径、浏览器 profile 内部路径）。

本次没有发现第四组同类隐患。三个新门禁的文件名都是 `test_*.py`，
`scripts/run_script_tests.py` 按目录自动发现，无需登记，也不存在"新写的门禁没人跑"这条老路。
