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
