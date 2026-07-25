# FIX-sandbox-cpu-budget 完成证据

> 状态：✅ 已完成
>
> 日期：2026-07-25
>
> 提交：本文件所在的独立修复提交（分支 `t6-sandbox-cpu`）
>
> 来源：BM-16 Windows 真机验收登记的「剩余阻塞（一项）」——134 项 sweep 在整机被前序
> 渲染压满后某一项触发 `render_resource_exceeded`；局部探针已证明同一项单独渲染在同一
> 预算下通过（`cpu=280 memory=2048 -> PASSED`），故不是渲染缺陷。

## 原契约为什么是错的量纲

`workers/motion_composition/worker.mjs` 用同一个常量约束两个不同的物理量：

```javascript
const SANDBOX_SECONDS_MAXIMUM = 300;
...
&& boundedInteger(value.maxDurationSeconds, 1, SANDBOX_SECONDS_MAXIMUM)
&& boundedInteger(value.maxCpuSeconds, 1, SANDBOX_SECONDS_MAXIMUM)
```

`frontend/src-tauri/src/local_video_orchestrator.rs` 复制了同一份判定。两处都把
**墙钟秒**（一维时间）和 **CPU 秒**（时间 × 并行度）当成同一个量。

CPU 秒在 `sampleProcessGroup` 里是按整棵浏览器进程树累加的（POSIX `/bin/ps` 的
`pgid=,rss=,time=`；Windows `Win32_Process` 的 `KernelModeTime + UserModeTime`），所以
一次占用 N 核的渲染每 1 墙钟秒会累计约 N CPU 秒。共用一个 300 会同时错在两端：

- **长墙钟端误杀**：16 核满载 20 墙钟秒就是 320 CPU 秒。一个声明 120 秒墙钟预算的
  渲染，实际在 CPU 维度上大约 19 秒就被判越界——墙钟预算被 CPU 上限**悄悄替换**了，
  这不是任何人声明过的契约。BM-16 的 sweep 正是这样被判成 `render_resource_exceeded`。
- **短墙钟端形同虚设**：1 秒墙钟预算可以合法声明 300 CPU 秒。任何宿主都不可能在
  1 墙钟秒内消耗 300 CPU 秒（需要 300 倍并行），所以那条 CPU 保护**永远不会触发**，
  是一条死约束。仓库里就有这样的用例——`test_sandbox_isolation_flags_and_wall_timeout`
  原本用 `maxDurationSeconds=1` 搭配默认的 `maxCpuSeconds=20`。

## 新 CPU 上限的取值依据

新契约（`contracts/video/motion-render-sandbox-budget.v1.json`）：

| 项 | 值 | 语义 |
| --- | --- | --- |
| `wallClockSecondsMaximum` | 300（不变） | 卡死保护。挂起的渲染在 `maxDurationSeconds` 被杀，语义未变。 |
| `cpuParallelismMaximum` | 8 | 一次渲染在其墙钟预算内**可声明的最高平均核占用**。 |
| CPU 秒可接受区间 | `[1, maxDurationSeconds × 8]` | 取代原来的 `[1, 300]`。 |
| CPU 秒绝对上限 | 2400（= 300 × 8） | 墙钟已被限在 300，故绝对量仍然有界。 |

为什么 8 仍然是**有效约束**而不是「调大让它过」：

1. **它把守的是设备负载，不是卡死**。卡死由墙钟保证。CPU 上限的独立价值是限制一次
   渲染最多能从整机抽走多少 CPU 时间——这是墙钟单独做不到的（120 秒墙钟的渲染在
   32 核机器上可以烧掉 3840 CPU 秒 = 64 分钟机器时间）。把上限写成 `墙钟 × P`，
   被声明的东西才是明确的：**这次渲染平均最多占 P 个核**。
2. **在验收机上可达，因此会真的触发**。BM-16 的 Windows 验收机是 16 核，物理上限是
   `墙钟 × 16`；ceiling 落在物理上限的一半，真正的并行失控（页面无限 spawn worker /
   GPU 进程）会在墙钟预算耗尽之前先撞上 CPU 上限。
3. **对正常渲染有余量但不夸张**。BM-16 实测一项 640×360 无头合成渲染的平均核占用约
   2–3 核，8 核的 ceiling 留了约 3 倍余量。
4. **它是收紧而不是放宽**。凡墙钟预算 < 37.5 秒（300 ÷ 8）的规格，新上限一律**低于**
   旧的 300。上面那条 1 秒墙钟 / 300 CPU 秒的死约束现在直接被拒。放宽只发生在长墙钟
   预算上，而那一端旧数字本来就是错的。
5. **判定逻辑没有放松**。`sample.cpuSeconds > spec.maxCpuSeconds` 立即强杀整个进程组的
   执行路径一行未改，只有「什么样的预算可以被声明」这条准入规则改了。

## 单点定义

契约 JSON 是唯一事实源，五处镜像由 `frontend/tests/motion-render-sandbox-budget.test.mjs`
机械比对（读源码抽常量，任何一处漂移即 fail）：

- `workers/motion_composition/worker.mjs`（判定方）
- `frontend/src-tauri/src/local_video_orchestrator.rs`（Rust 同构判定）
- `tools/motion-authoring/motion_authoring_agent.py`（BM-05 提交方）
- `scripts/test_motion_video_render_sandbox.py`（边界测试独立声明）
- 契约测试同时断言调用方**不得**自造 CPU 数字：`run_bm_16_acceptance.py` 不许再有
  `RENDER_CPU_BUDGET_SECONDS = <literal>`，authoring agent 不许再有 `min(<literal>, ...)`。

收敛掉的硬编码：

- `scripts/run_bm_16_acceptance.py`：删掉 `RENDER_CPU_BUDGET_SECONDS = 280`，改为
  `budget_seconds * SANDBOX_CPU_PARALLELISM_MAXIMUM`（短渲染 120→960，确定性 180→1440）。
- `tools/motion-authoring/motion_authoring_agent.py`：删掉两处字面量 `300` 和魔法系数
  `10`，改为 `wall_seconds` 与 `wall_seconds * SANDBOX_CPU_PARALLELISM_MAXIMUM`。
  顺带修掉一处**原本就越界**的提交：6 秒时长的作品原先提交 `wall=6, cpu=60`，在新契约下
  ceiling 是 48，旧公式产出的规格会被 Worker 判 `render_sandbox_invalid`。

## RED

三层各自先跑出失败，失败输出原文如下。

### 1. Worker 沙箱边界（Python）

```
$ backend/.venv/bin/python -c "... test_sandbox_cpu_budget_scales_with_the_wall_clock_budget ..."
Traceback (most recent call last):
  File "<string>", line 10, in <module>
  File ".../scripts/test_motion_video_render_sandbox.py", line 522, in test_sandbox_cpu_budget_scales_with_the_wall_clock_budget
    expect_sandbox_failure(
  File ".../scripts/test_motion_video_render_sandbox.py", line 316, in expect_sandbox_failure
    assert event["reasonCode"] == reason, event
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: {'authenticationProof': 'atvwp1.9QLkrsx5aUxyjthqTdmRgTj0gyjg8TBlXw1oA5WRXFQ', 'event': 'worker.render.failed', 'jobId': '7d444840-9dc0-41a2-bcd4-e15b02a4c51e', 'protocolVersion': '1.0', 'reasonCode': 'render_sandbox_invalid', 'workerKind': 'node', 'workerVersion': '0.7.68'}
```

即 `maxDurationSeconds=120, maxCpuSeconds=480`（4 核平均占用）这条合法的多核预算被旧契约
判成 `render_sandbox_invalid`——线上现象的最小复现。

同一次全量运行还暴露旧契约在另一端的松：

```
$ backend/.venv/bin/python scripts/test_motion_video_render_sandbox.py
  File ".../scripts/test_motion_video_render_sandbox.py", line 395, in test_sandbox_rejects_invalid_spec
    expect_sandbox_failure(
AssertionError: {..., 'reasonCode': 'render_protocol_invalid', ...}
```

`maxDurationSeconds=20, maxCpuSeconds=161` 期望被拒，旧契约却放行并真的去启动了浏览器
（于是拿到的是后续的 `render_protocol_invalid` 而不是 `render_sandbox_invalid`）。

### 2. Rust 同构判定

```
$ cargo test --manifest-path frontend/src-tauri/Cargo.toml --test local_video_orchestrator render_sandbox_scales
running 1 test
test render_sandbox_scales_the_cpu_budget_with_the_wall_clock_budget ... FAILED

---- render_sandbox_scales_the_cpu_budget_with_the_wall_clock_budget stdout ----

thread 'render_sandbox_scales_the_cpu_budget_with_the_wall_clock_budget' (86034509) panicked at tests/local_video_orchestrator.rs:627:9:
cpu=480 inside a 120s wall budget must be accepted

test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 19 filtered out; finished in 0.00s
```

### 3. 跨语言契约测试

```
$ node --test tests/motion-render-sandbox-budget.test.mjs
✖ the render sandbox CPU budget contract has one definition in every language (1.562792ms)
✖ the CPU ceiling is derived from the wall-clock budget, never shared with it (1.252917ms)
ℹ tests 2
ℹ pass 0
ℹ fail 2

  Error: ENOENT: no such file or directory, open '.../contracts/video/motion-render-sandbox-budget.v1.json'
```

### 4. BM-05 提交方（先写断言，把修复回滚成旧公式验证确实会红）

```
$ backend/.venv/bin/python scripts/test_motion_authoring_agent.py
FAIL: test_submission_cpu_budget_stays_inside_the_sandbox_contract (__main__.AgentAuthoringTests....)
Traceback (most recent call last):
  File ".../scripts/test_motion_authoring_agent.py", line 515, in test_submission_cpu_budget_stays_inside_the_sandbox_contract
    self.assertLessEqual(spec["maxCpuSeconds"], wall_seconds * parallelism)
AssertionError: 60 not less than or equal to 48

Ran 49 tests in 0.030s
FAILED (failures=1)
```

## GREEN

```
$ backend/.venv/bin/python scripts/test_motion_video_render_sandbox.py
PASS test_sandbox_rejects_invalid_spec
PASS test_sandbox_rejects_workspace_violations
PASS test_sandbox_null_render_browser_refuses_without_discovery
PASS test_sandbox_cpu_budget_scales_with_the_wall_clock_budget
PASS test_sandbox_forged_or_tampered_command_is_ignored
PASS test_sandbox_rejects_chromium_major_mismatch
PASS test_sandbox_isolation_flags_and_wall_timeout
PASS test_sandbox_cpu_budget_kills_the_process_group
PASS test_sandbox_memory_budget_kills_the_process_group
BM-04 render sandbox boundary tests passed

$ backend/.venv/bin/python scripts/test_motion_video_worker.py
BM-02 Node Worker rejection tests passed

$ backend/.venv/bin/python scripts/test_motion_video_render_adapter.py
（10 项全 PASS）BM-03 render adapter boundary tests passed

$ backend/.venv/bin/python scripts/test_motion_authoring_agent.py
Ran 49 tests in 0.029s
OK

$ cd frontend && node --test tests/*.test.mjs
ℹ tests 214
ℹ pass 214
ℹ fail 0

$ cargo test --manifest-path frontend/src-tauri/Cargo.toml --test local_video_orchestrator
test result: ok. 20 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 5.21s

$ backend/.venv/bin/ruff check --ignore RUF001 --config backend/pyproject.toml \
    scripts/test_motion_video_render_sandbox.py scripts/run_bm_16_acceptance.py \
    scripts/test_motion_authoring_agent.py tools/motion-authoring/motion_authoring_agent.py
Found 3 errors.   # 三条全部与 HEAD 基线逐条一致，均非本次引入

$ cargo fmt --manifest-path frontend/src-tauri/Cargo.toml -- --check -l
frontend/src-tauri/src/video_job_workspace.rs        # 本次未触碰
frontend/src-tauri/tests/motion_video_studio.rs      # 本次未触碰
```

## 失败矩阵

| 情形 | 期望 | 覆盖 |
| --- | --- | --- |
| `maxCpuSeconds = 0` | `render_sandbox_invalid` | Worker 测试（既有用例保留） |
| `maxCpuSeconds` 非整数 / 字符串 | `render_sandbox_invalid` | 既有 `boundedInteger` 用例 |
| `wall=1, cpu=8`（恰好等于 ceiling） | 通过校验 | Worker + Rust 新用例 |
| `wall=1, cpu=9`（ceiling+1） | `render_sandbox_invalid` | Worker + Rust 新用例 |
| `wall=1, cpu=300`（旧契约放行的死约束） | `render_sandbox_invalid` | Worker + Rust 新用例 |
| `wall=30, cpu=300` | `render_sandbox_invalid` | Worker + Rust 新用例 |
| `wall=120, cpu=480`（线上误杀场景） | 通过校验 | Worker + Rust 新用例 |
| `wall=120, cpu=961`（ceiling+1） | `render_sandbox_invalid` | Worker + Rust 新用例 |
| `wall=300, cpu=2400`（绝对上限） | 通过校验 | Worker + Rust 新用例 |
| `wall=20, cpu=161`（默认规格的 ceiling+1） | `render_sandbox_invalid` | `test_sandbox_rejects_invalid_spec` 新增项 |
| `maxDurationSeconds` 越界（0 / 301） | `render_sandbox_invalid`，且不会去计算 CPU ceiling | 既有用例 + `&&` 短路顺序 |
| CPU 真的超限（busy 浏览器） | `render_resource_exceeded` + 强杀整个进程组 | `test_sandbox_cpu_budget_kills_the_process_group`（判定路径未改，回归通过） |
| 内存超限 | `render_resource_exceeded` | `test_sandbox_memory_budget_kills_the_process_group` |
| 墙钟挂起 | `render_timeout` | `test_sandbox_isolation_flags_and_wall_timeout`（规格改为 `wall=1, cpu=8`） |
| 校验拒绝时浏览器是否被启动 | 从不启动、无 `frames/`、无 RenderJob 目录残留 | 新用例断言 decoy 未被调用 + `assert_workspace_untouched` |
| 五处常量任意一处漂移 | 契约测试 fail | `frontend/tests/motion-render-sandbox-budget.test.mjs` |
| 调用方重新硬编码 CPU 数字 | 契约测试 fail | 同上，`doesNotMatch` 两条 |
| BM-05 提交越界预算 | 单测 fail | `test_submission_cpu_budget_stays_inside_the_sandbox_contract` |

## 正常用户路径验收

不适用（分层证据说明）：本次改的是 BM-04 沙箱规格的准入区间，没有新增或改变任何用户
可见入口。用户从正式 App 制作动效视频的路径由 BM-08 / BM-16 承担，本修复要闭合的正是
BM-16 Windows 侧登记的那条阻塞——**该端到端复验尚未执行**，见下方真实边界。

## 真实边界（本次没有覆盖什么）

- **没有跑 BM-16 端到端 sweep 复验**。本次只做到「契约层已修复且被测试锁死」。原始现象
  （整机压满后某一项 `render_resource_exceeded`）要真正判定消失，必须在 **Windows 16 核
  真机**上重跑 `python scripts\run_bm_16_acceptance.py` 的 134 项 sweep。本会话没有
  Windows 机器可用，也没有在 macOS 上跑完整 sweep（需现场暂存 435MB 锁定 Chromium 归档）。
  在该复验完成前，BM-16 的这条阻塞只能记为「已按根因修复，待真机复验」，不能记为已验证消失。
- **Windows 与 POSIX 的 CPU 统计差异：只覆盖了契约层，没有覆盖平台采样层**。
  两条采样实现（POSIX `ps -axo time=`，Windows `Win32_Process` 的 100ns 计数）本次一行未改，
  因此不存在新增的平台差异；但本次新增的所有用例都在 macOS 上执行，**Windows 上的
  `test_motion_video_render_sandbox.py` 未重跑**。该文件的 Windows 分支
  （`write_windows_sandbox_fake` / CIM 采样）此前由 BM-04 验收覆盖，新增用例只走规格校验、
  不触碰采样代码，但严格说仍缺 Windows 侧复跑证据。
- **`cpuParallelismMaximum = 8` 是策略值，不是实测最优值**。依据是 BM-16 记录的「约 2–3 核
  平均占用」和 16 核验收机这两个事实，没有做核数扫描实验。若将来出现常态超过 8 核平均占用
  的合法渲染，应作为独立任务重新评估这个数并同步契约，而不是就地调大。
- **仓库既有问题未夹带修复**（最小改动原则）：`cargo clippy --lib --tests -- -D warnings`
  在 HEAD 上就已有 5 条错误（`tests/local_video_orchestrator.rs` 的 385/574/589 三处
  `.err().expect()`、497 处 complex type），`cargo fmt --check` 在 `video_job_workspace.rs`
  与 `tests/motion_video_studio.rs` 上也已不干净，ruff 有 3 条既有告警。本次改动不新增任何
  一条，也没有顺手修，需要时另开任务。

## 清理

- 本次所有测试只启动 shell/Python 假浏览器与 `renderBrowser: null` 路径，**没有启动过真实
  Chromium**；测试自带的 `wait_for_process_exit` 与 `render_job_directories` 断言全部通过。
- 运行结束核对：系统临时目录无 `automation-tool-renderjob-` 残留；无 `fake-browser` /
  `sandbox-fake` 进程存活。`pgrep -f chrome` 计数为 14，经逐条核对全部是本会话之外的
  `agent-browser-chrome-*` profile 与 VS Code Helper，非本次创建，按规则不清理。
- worktree 内为编译 Rust 而复制的 `frontend/dist/` 与 `git submodule update --init
  vendor/hyperframes` 均为本地构建/测试依赖，被 `.gitignore` 与 gitlink 覆盖，未进入提交。

## 文档变化

- 新增本证据文件与 `contracts/video/motion-render-sandbox-budget.v1.json`、
  `frontend/tests/motion-render-sandbox-budget.test.mjs`；
- `docs/development/windows-evidence-checklist.md` 第 10 节「Windows 侧剩余阻塞（一项）」
  改写为「根因已修复，待 Windows 真机复验」；
- `docs/development/BM-16.md` 同段同步改写，避免两份不一致的阻塞描述；
- 未改动 `docs/development-roadmap.md` 与专项 Roadmap 的任务状态：BM-16 仍是 `🔍 待验收`，
  因为真机复验尚未完成。
