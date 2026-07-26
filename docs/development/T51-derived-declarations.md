# T51 三组手抄清单改由权威来源推导

> 状态：🚧 实现中
>
> 日期：2026-07-26
>
> 提交：见每组小节

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
