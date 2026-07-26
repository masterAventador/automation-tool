# T52 渲染超时用例的真红判定

> 状态：✅ 已完成
>
> 日期：2026-07-26
>
> 提交：本文件所在提交

## 1. 起因

聚合执行者 `scripts/run_script_tests.py` 上线首轮抓到一条红：

```
scripts/test_motion_video_render_adapter.py::test_render_timeout_kills_the_browser_process_group
→ IndexError: list index out of range   （read_invocations(record)[-1]）
```

这条用例属于此前 14 个「从 workflow 和 acceptance 脚本都引用不到、`testpaths=["tests"]`
也不收」的孤儿之一，只有人手敲文件名才会跑，所以这个红躺了很久没人知道。

## 2. 判定：测试过期，不是产品缺陷

**判据：失败发生时，产品的行为逐条符合契约；越界发生在用例自己的断言前置条件上。**

用例在 `IndexError` 之前的断言全部通过，这本身就是证据：

| 断言 | 结果 | 说明 |
| --- | --- | --- |
| `elapsed < 10` | 通过 | 超时没有等待挂死的浏览器 |
| `event == "worker.render.failed"` | 通过 | 失败事件如约发出 |
| `reasonCode == "render_timeout"` | 通过 | 失败原因正确 |
| `session.finish() == 0` | 通过 | Worker 干净退出 |

也就是说：**Worker 拿到 `launchTimeoutSeconds: 1`，就在第 1.00 秒杀掉了进程组并如实上报。**
这正是被测契约本身。`IndexError` 出在下一行——用例假设"记录文件里至少有一次调用，
最后一次就是那个挂死的无头浏览器"，而这个前提在失败路径上不成立。

### 2.1 前提为什么会不成立

`renderVerify()`（`workers/motion_composition/worker.mjs:548-564`）在 POSIX 上**先**跑
`--version` 探针，**再**启动无头浏览器，两者共用同一个 `launchTimeoutSeconds` 预算。
用例给的是 schema 允许的最小值 1 秒（`RENDER_TIMEOUT_SECONDS_MINIMUM = 1`）。

而用例的假浏览器是一个**刚写出来的新可执行文件**。macOS 对新文件的首次 exec 要做一次
扫描，本机实测：

```
sample 0: first-exec 641ms  warm-exec 18ms
sample 1: first-exec 501ms  warm-exec 18ms
sample 2: first-exec 574ms  warm-exec 19ms
sample 3: first-exec 568ms  warm-exec 19ms
sample 4: first-exec 448ms  warm-exec 18ms
sample 5: first-exec 465ms  warm-exec 16ms
```

**首次 exec 吃掉 1 秒预算的 45%~64%，第二次只要 18ms。** 机器一忙，这一次性扫描就撑满整
个预算：Worker 在 `--version` 探针还没执行到假浏览器第一行 Python 时就把它杀了，于是

- 无头浏览器**根本没被启动**（用例声称要杀的那个进程不存在）；
- 假浏览器**一行都没跑**，记录文件连创建都没有；
- `read_invocations(record)` 返回 `[]`，`[-1]` 越界。

### 2.2 复现（RED）

空闲时单跑通过，所以先量化余量，再按聚合执行者的并发把余量吃掉：

```
# 空闲：预算 1.00s，实际耗时 1.45~1.61s，两次调用都记到了
round 0: reason=render_timeout elapsed=1.61s invocations=2
round 4: reason=render_timeout elapsed=1.50s invocations=2
```

```
# 16 路并发跑真实用例文件（聚合执行者默认 --jobs 4，加上另外两条在跑的线）
for i in $(seq 1 16); do ./backend/.venv/bin/python scripts/test_motion_video_render_adapter.py & done
→ 16 份全部 exit=1，全部是同一处：
  File ".../test_motion_video_render_adapter.py", line 673,
    in test_render_timeout_kills_the_browser_process_group
    hanging = read_invocations(record)[-1]["pid"]
IndexError: list index out of range
```

诊断探针同时打印出失败瞬间的现场，与推断完全一致：

```
round 1: reason=render_timeout elapsed=1.00s invocations=0
  RECORD MISSING/EMPTY -> IndexError here. raw='<no record file>'
```

`elapsed` 精确等于 1.00s（预算耗尽），`invocations=0`，**记录文件不存在**——证明超时打在
`--version` 探针上，而不是无头浏览器上。

### 2.3 生产不受影响

没有任何生产路径使用 1 秒预算：

| 使用方 | `launchTimeoutSeconds` |
| --- | --- |
| `frontend/src-tauri/src/local_video_orchestrator.rs:1341` | 配置项 `launch_timeout`，受 `RENDER_LAUNCH_TIMEOUT_MINIMUM/MAXIMUM` 约束 |
| `scripts/run_bm_03_acceptance.py:175` | 30 |
| 本用例 | **1**（schema 最小值） |

而且生产装的 Chromium 是安装包里早已存在的文件，不是每次现写的新文件。
所以这是**用例给自己挑了最紧的预算，又让被测对象在预算内替它付一次性扫描费**，
与产品行为无关。

## 3. 改法

1. **把一次性扫描费挪出预算窗口**：新增 `warm_executable()`，在把可执行文件交给 Worker
   之前先自己跑一次 `--version`，然后删掉这次留下的记录。Worker 的 1 秒于是只花在被测行
   为上（两次 exec 都是 18ms 量级的热启动）。
2. **让越界失败变成能读懂的失败**：原来记录为空时报 `IndexError`，看不出发生了什么。改
   成显式断言，直接说明"超时打在浏览器启动之前，本用例什么都没证明"。同时补一条断言，
   要求最后一次调用确实带 `--headless`——用例声称杀的是无头浏览器，就该验明正身。
   Windows 分支同样处理（原来会在空记录上抛解包错误）。

**没有放宽任何断言，没有加 skip。** 空记录仍然是红，只是从越界异常变成了带诊断的断言。

## 4. GREEN

```
# 与 RED 完全相同的 16 路并发
for i in $(seq 1 16); do ./backend/.venv/bin/python scripts/test_motion_video_render_adapter.py & done
→ 16 份全部 exit=0，0 处 Traceback
```

```
# 串行全量
./backend/.venv/bin/python scripts/test_motion_video_render_adapter.py
→ 10 项全 PASS，BM-03 render adapter boundary tests passed
```

## 5. 变异检验

把 `warm_executable(executable, record)` 一行改成注释，其余不动，跑同一条 16 路并发：

```
16 份全部 exit=1，失败形态全部是：
AssertionError: the render timeout fired before the browser was ever launched,
so this test observed no hanging process and proved nothing about killing one
```

两件事同时得证：

- 预热**确实**是让用例转绿的原因（去掉就红回来，16/16）；
- 新断言**确实**在守——而且守出来的话是能直接读懂的诊断，不再是 `IndexError`。

随后原样还原，重跑确认 GREEN。

## 6. 失败矩阵

| 情形 | 行为 |
| --- | --- |
| 超时打在 `--version` 探针（记录为空） | 显式断言失败并说明原因，不再 `IndexError` |
| 最后一次调用不是无头浏览器 | 断言失败并打印实际参数 |
| Windows 记录不足两个 PID | 断言失败并打印实际内容，不再抛解包错误 |
| 预热本身失败 | `subprocess.run(check=True)` 直接抛，不静默继续 |
| 挂死浏览器没被杀掉 | 原有的 5 秒轮询断言不变，仍然红 |

## 7. 真实边界与清理

- 真实边界：真的 Node Worker 进程、真的 `spawn`、真的进程组 `SIGKILL`、真的临时目录。
  假浏览器只替代 Chromium 本身，超时与杀进程链路全是产品代码。
- 清理：并发复现共起过 16×10 个 Worker 会话与假浏览器。收尾核对
  `ps aux | grep automation-tool-renderjob\|fake-browser` 无残留，
  `/tmp` 下无 `automation-tool-renderjob-*` 残留目录（用例自带的 `main()` 尾部断言也覆盖这条）。
- 未触碰另外两条线占用的文件，也未清理 `automation-tool-t36-*` 的任何资源。

## 8. 遗留

`--version` 探针与无头启动共用同一个 `launchTimeoutSeconds` 预算，这是产品设计，本次没有
改动。若将来把两段预算拆开，本用例的预热仍然正确，只是不再是必需。
