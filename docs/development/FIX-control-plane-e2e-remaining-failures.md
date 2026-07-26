# FIX control-plane 桌面 E2E 层剩余失败

> 状态：🔍 待验收（13 条剩余失败中 **8 条已实跑通过**；3 条定位到确切原因但未修，
> 1 条只定位了一半，1 条属产品决策）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/FIX-control-plane-e2e-prerequisites.md` 补齐启动前置、
> `FIX-platform-login-e2e-failures.md` 修好登录链路之后，这一层还剩 13 个驱动失败。

## 结论先说

13 条失败底下是 **6 条独立原因**。与上一轮不同，**这一轮没有生产缺陷**：
四条是驱动夹具落后于 2026-07-22 落地的产品规则，一条是上一轮已修但没复跑，
一条是夹具与 App 真实本机执行器抢同一份平台会话状态。产品代码一行未改。

| # | 原因 | 分类 | 影响的驱动 | 本次 |
| --- | --- | --- | --- | --- |
| 1 | 驱动播种 `task.offer` 时不建目标确认，被生产「未确认副作用投递守卫」拒绝 | 测试过期 | T3-12 / T3-13 / T3-15 / T3-16 / T3-19 / T3-20 | 已修 |
| 2 | 目标确认让 Task revision 前进一格，六个驱动/两个 spec 冻结着旧数字 | 测试过期 | T3-12 / T3-13 / T3-15 / T3-16 / T3-19 / T3-20 | 已修 |
| 3 | 确认目标会释放发现尝试，驱动仍按 `current_attempt_id` 找它 | 测试过期 | D6-11 / D6-12 | 已修 |
| 4 | 夹具同时播种两个活跃执行尝试，违反 Installation 单活任务约束 | 测试过期 | T3-18 | 已修（该条另有未定位失败） |
| 5 | 上一轮把执行器账本版本改成产品常量后未复跑 | 已解决 | E4-14 / H8-03 | 复跑通过 |
| 6 | App 自己的真实本机执行器把驱动播种的平台健康覆盖成 `missing`，第二次发现被平台守卫（而不是忙检查）拒绝 | 环境/夹具 | D6-10 | 未修（见下） |

外加 H8-16F：被测用户路径已被 EB-10 整段删除，属产品决策，本次按要求未动断言。

## 逐条定位

### 原因 1：`task.offer` 播种缺目标确认（测试过期）

`SqlAlchemyTaskCommandRepository.enqueue` 自 `1a45ffe`（未确认副作用投递守卫）起，
对带有抖音搜索曝光定义的 Task 拒绝没有匹配 `task_target_confirmations` 的 `task.offer`：

```text
task_command_repository.py:314  raise TaskCommandDeliveryRejected
```

这条守卫是刻意的：目标没被人确认过，副作用就不许下发到平台。

App 通过生产 `create_task` 建的每个验收 Task 都带这份定义，所以自己插尝试再入队 offer 的驱动
必须同时播种确认。落地时 `run_t3_14` / `run_h8_01`–`run_h8_06` / `run_t3_18` 六个驱动跟进了，
**另外六个没有**，于是它们在 App 阶段全过之后死在自己的夹具里，看起来像产品回归。

`run_t3_13` 的 spec 里那句注释——「Target confirmation establishes revision 2 before the four
executor events」——写的正是这条规则；断言早就更新了，只有驱动没跟上。

**修法**：六个驱动改为引用 `run_t3_14_acceptance` 的 `seed_task_confirmation` +
`seed_attempt_and_offer`（T3-12 / T3-13 / T3-15 各自那份私有 `seed_attempt_and_offer` 副本一并删除）。

同时改造了共享的 `seed_task_confirmation`：**确认的动作与文案从 Task 自己的
`douyin_search_exposure_definitions` 行读取**，不再写死一份 `comment` + 中文模板。
T3-19 / T3-20 的 Task 由 App 的新建表单创建，表单默认动作是 `browse`、根本没有文案，
写死的夹具对它们永远过不了守卫的动作/文案比对。

### 原因 2：目标确认使 Task revision 前移一格（测试过期）

守卫要求 `confirmation.confirmed_task_revision == tasks.revision`，因此播种确认的 Task
在 offer 入队时是 revision 2 而不是 1。此后每条事件 +1，终止类控制命令（取消 / 紧停）
在入队瞬间把 Task 投影为 `cancelling` 并额外 +1。

冻结旧数字的地方：`run_t3_11.wait_for_convergence`（6）、`run_t3_13`（5）、`run_t3_15`（6）、
`run_t3_16`（5）、`run_t3_19`（7 / 6）、`run_t3_20`（4 / 5）、
`frontend/e2e-tauri/task-projection.spec.ts`（`finalRevision` 6）。

**修法**：全部改为从单一事实推导——
`run_t3_14.CONFIRMED_TASK_REVISION`（确认建立的 revision）
`+ 终止投影数` `+ 事件条数`；`run_t3_11` 新增 `CONVERGED_EVENT_COUNT` 与
`task_revision_baseline` 参数，`run_t3_11` 自己（不属本层、Task 无曝光定义）继续用基线 1。
`task-projection.spec.ts` 的 `finalRevision` 由 6 改为 7，并写清为什么不可能更低。

### 原因 3：确认目标释放发现尝试（测试过期）

`task_target_preview_repository._append_preview_event(clear_current_attempt=True)` 在
`task.targets_confirmed` 时把 `tasks.current_attempt_id` 置空——这是刻意的：
`action_execution_orchestration_repository` 只在 `current_attempt_id IS NULL` 时才准入
动作执行尝试。

D6-11 / D6-12 的驱动仍按 `execution_attempts.id == task.current_attempt_id` 找那条尝试，
于是 spec 全过之后驱动自己抛 `NoResultFound`。

**修法**：按 `task_id` + `installation_id` 定位该 Task 唯一的执行尝试，并**补一条更强的断言**
——确认之后 `current_attempt_id` 必须为空。断言没有放宽，是把产品规则写进了用例。

### 原因 4：一个 Installation 只能有一个活跃执行尝试（测试过期）

`35e1e0b`（Installation 单活任务）加了
`uq_execution_attempts_one_active_installation`。T3-18 的夹具一次性给两个 Task 各插一个
`accepted` 尝试：

```text
UniqueViolationError: duplicate key value violates unique constraint
"uq_execution_attempts_one_active_installation"
```

**修法**：紧停 Task 的 offer 改由后台线程在 `wait_for_idle_installation()` 之后播种——
即 UI 把第一个 Task 驱动到终态、Installation 空出唯一活跃槽位之后。

### 原因 5：执行器账本版本（上一轮已修，本次复跑确认）

`FIX-platform-login-e2e-failures.md` 把五个驱动冻结的账本版本号改成产品常量但没有复跑。
本次复跑：**E4-14 通过、H8-03 通过**，无需任何新改动。

### 原因 6：真实本机执行器把夹具播种的平台健康改写掉（环境/夹具，未修）

D6-10 的 spec 期望第二个任务点「开始目标发现」时看到「当前设备已有任务正在运行」。
实测点下去之后 App **直接跳到了「平台状态」页**。

**第一层证据**（临时探针，已删除）：直接调用 `start_task_discovery` 得到

```json
{"code":"operation_unavailable","message":"native command error:operation_unavailable","retryable":false}
```

不是 `installation_busy`。服务端 `SqlAlchemyTaskDiscoveryRepository.start` 的守卫顺序是：

```text
1. Installation 状态
2. platform_session_gates / platform_session_health   ← 命中就 409 task_discovery_rejected
3. 幂等键
4. 活跃执行尝试                                        ← 命中才是 423 installation_task_active
```

第 2 步在第 4 步之前，所以这一轮根本没走到忙检查。前端
`task-discovery-gateway.ts` 把 409 对应的 `operation_unavailable` 映射成
`discovery_rejected`，`TaskRunDetails` 对它的处理就是 `onPlatformLoginRequired()`——跳到平台状态页。

**第二层证据**（驱动新增的 `report_platform_gate_state()`，失败时打印）：

```text
[D6-10] platform health=[{'platform':'douyin','state':'missing','session_revision':1}]
        gates=[]
        attempts=[{...,'status':'failed'}, {...,'status':'failed'}]
```

驱动的 `seed_healthy_platform()` 写的是 `healthy`，失败时却是 `missing`；`gates` 为空。
也就是说 **App 自己的真实本机执行器**（启动前置现在会安装并由 App 拉起签名执行器包）
接管了发现命令，用没有抖音登录态的内置 Chromium 去检查，如实把平台会话报成 `missing`
并把两次发现尝试都置为 `failed`。第一次发现能过，是因为它跑在执行器上报之前。

所以这不是产品缺陷：没有登录态就不该允许目标发现，服务端和前端的行为都对。
**是 D6-10 的夹具假设自己独占 `platform_session_health` 与任务命令**，而启动前置补齐之后
App 侧多了一个真实执行器与之竞争。

**未修的理由**：让夹具与真实执行器共存有多种做法（不给这个驱动装执行器包、
把平台会话按生产路径做成真的健康、或让驱动的正式执行器先抢占连接），
选哪一种是 D6-10 自身的验收设计决定，不该在这里替它定。

### H8-16F：被测用户路径已被删除（产品决策）

```text
Expect $(`.browser-settings-card label.ant-radio-wrapper`) to be displayed
Received: "not displayed"
```

`mvp-user-journey.spec.ts` 第一步 `repairTrustedBrowser()` 要点「保存浏览器选择」。
EB-10（`f34e503`）按产品规则删掉了整条浏览器选择链路，`frontend/src/` 里已无任何渲染处。

**建议（这是产品决策，本次未改断言）**：把 `repairTrustedBrowser()` 整段从 spec 删掉，
其余 MVP 用户旅程（建任务 → 登录 → 发现 → 排除 → 确认 → 执行 → 结果）原样保留并重新验收。
理由：EB-10 之后「修复受信浏览器」这个用户动作在产品里不存在了，它不是被换了个位置，
是被内置 Chromium 取代了；保留它只能证明一个已经没有的页面。删掉之后 H8-16F 仍然覆盖
首期唯一 RPA 闭环的完整用户路径，覆盖面没有实质损失。
**不建议**保留断言并把浏览器选择 UI 加回来——那与 CLAUDE.md 第 5 节「不发现、不选择、
不回退系统浏览器」直接冲突。

## 顺带修掉的执行级缺陷：孤儿进程树

`pnpm test:*-tauri` 是一条链：pnpm → WebdriverIO → `tauri-driver` → App。
驱动的 `finally` 只对 pnpm 发信号，其余全部被 init 收养；孤儿 App 会把驱动刚删掉的隔离
App 数据目录重新写出来，于是**同一个驱动的下一次运行在 0.4 秒内死在
「Refusing to reuse an existing … App data directory」**。

`run_h8_03` 早就有一份正确的进程组终止实现（含 Windows `taskkill /T /F`）。本次把它上提到
`desktop_e2e_prerequisites.terminate_app_process_tree()`，删掉 h8_03 的本地副本，
本层所有会启动 App 的驱动改为「自建会话启动 + 整树终止」。

## RED

### 1. 未确认投递守卫（`scripts/test_desktop_e2e_prerequisites.py`）

```text
backend/.venv/bin/python scripts/test_desktop_e2e_prerequisites.py
AssertionError: these drivers enqueue a task.offer for an App-created Task without
seeding the confirmation the production guard requires: run_t3_12_acceptance.py,
run_t3_13_acceptance.py, run_t3_15_acceptance.py, run_t3_16_acceptance.py,
run_t3_19_acceptance.py, run_t3_20_acceptance.py
```

### 2. 进程树终止（同一文件）

```text
AssertionError: these drivers spawn an acceptance App without starting it in its own
session and stopping the whole tree through terminate_app_process_tree: ...
```

### 3. 驱动本身就是 RED

原因 2/3/4 的失败由驱动实跑直接暴露，证据见「实跑结果」；本次还把三处
「invalid」错误文案改成会打印实际观测值（`run_t3_11` / `run_t3_13` / `run_t3_15` /
`run_t3_16`），否则这一层的每次失败都要靠再跑一轮才知道差在哪。

## GREEN

```text
backend/.venv/bin/python scripts/test_desktop_e2e_prerequisites.py
  ok  ...（14 项，新增 2 项）
  desktop e2e prerequisite checks passed (14 checks)

backend/.venv/bin/python -m ruff check scripts/ --select F,E9
  仅 run_ve_04_acceptance.py 的 1 项，为本次之前既有

cd frontend/src-tauri && cargo test
  全绿（含 doc-tests；无 FAILED）

cd frontend/src-tauri && cargo test --test single_build_path
  test result: ok. 7 passed; 0 failed

cd backend && uv run --locked pytest tests/unit -q
  3166 passed, 1 skipped in 41.89s

cd frontend && npx vitest run
  Test Files  59 passed (59)
  Tests  462 passed | 1 expected fail (463)
```

## 实跑结果

本次共跑 15 个驱动、51 轮，全部串行。**13 条目标失败里 8 条通过、5 条仍失败。**
另有 T3-14 / H8-01 两个本来就绿的驱动，因为共享夹具和进程树终止被改动而一并复跑，
用来确认没有把已经通过的用例改坏。

| | 数 |
| --- | --- |
| 本次目标失败 | 13 |
| **修好并实跑通过** | **8** |
| 仍失败 | 5 |
| 回归复跑（本来就绿，改动波及） | 2，全部仍通过 |

取每个驱动的最终结果：

| 驱动 | 结果 | 说明 |
| --- | --- | --- |
| T3-12 | ✅ 通过 | 确认播种 + 收敛 revision 基线 |
| T3-13 | ✅ 通过 | 同上 |
| T3-19 | ✅ 通过 | 同上（spec 先于驱动通过） |
| T3-20 | ✅ 通过 | 同上 |
| D6-11 | ✅ 通过 | 尝试定位方式 + `current_attempt_id` 释放断言 |
| D6-12 | ✅ 通过 | 同上 |
| E4-14 | ✅ 通过 | 上一轮账本常量修复的复跑确认 |
| H8-03 | ✅ 通过 | 同上 |
| T3-15 | ❌ 仍失败 | spec 通过；驱动断言设备会话数 3 → 实测 4（详见下） |
| T3-16 | ❌ 仍失败 | spec 通过；驱动断言 `executor.connect` 恰好 1 → 实测 2 |
| T3-18 | ❌ 仍失败 | 单活尝试冲突已修；工作台始终不出现「运行中」，未定位 |
| D6-10 | ❌ 仍失败 | 原因 6，未修 |
| H8-16F | ❌ 仍失败 | 被删 UI，属产品决策 |
| T3-14 | ✅ 通过（回归） | 共享 `seed_task_confirmation` 改为读定义之后复跑 |
| H8-01 | ✅ 通过（回归） | 同上 + 进程树终止接线 |

上表中 T3-12 / T3-13 / T3-19 / T3-20 / D6-11 / D6-12 在进程树终止改动**之后**又整体复跑过一轮，
八个驱动连跑全绿，没有出现上一轮记录的负载偶发。

### T3-15 / T3-16：冻结的设备会话计数

两条都只差一个断言，且 spec 已经通过。

```text
T3-15: ['app.control-plane' x4, 'executor.connect']        断言写死 x3 + 1
T3-16: ['app.control-plane' x16, 'executor.connect' x2]     断言写死 executor.connect 恰好 1
```

「能力集合」这一半（不得出现计划外的能力）两边都通过，失败的是**次数**。
这个次数等于该轮 App 实际发出的 Control Plane 调用数，会随 UI 轮询时机浮动
（T3-16 的 16 次就是工作台轮询的结果）。

**本次没有改这两个数字**：把 3 改成 4、把 1 改成 2 只是让它今天通过，而这个计数本身
是否应该被断言、应该断言成什么，是 T3-15 / T3-16 各自任务的设计决定；在没弄清第 4 个
App 会话和第 2 个执行器会话分别由谁发起之前改数字，等于猜一个原因然后改代码。

### T3-18：单活约束已修，另有一条未定位失败

夹具冲突修好之后 T3-18 第一次跑到了 spec 内部，失败在

```text
task-run.spec.ts:133  waitForRenderedText(controlled, emergency, "本机执行器在线", "运行中")
Latest Task run text: … 累计任务2 … 状态草稿 Revision1 … 最近任务 <id>草稿 <id>草稿
```

两个 Task 在工作台上一直是「草稿 / Revision 1」，而同一份投影里动作结果
（成功动作 1 / 失败动作 1 / 动作结果待确认 1）已经在了——也就是说
`seed_target_results` 落了库，`seed_attempt_and_offer` 却没有把 Task 推到 `queued`，
或者工作台读到的是更早的快照。**没有拿到确切原因，本次不猜。**
已在驱动里加了播种后的 offer 状态打印，供下一轮定位。

这正是上一轮记录的 T3-18 原始症状（「Latest Task run text: … 渲染断言未收敛」），
之前被更早的 `IntegrityError` 掩盖。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 驱动播种 `task.offer` 但不建确认 | 门禁点名驱动 | `check_every_app_created_task_offer_seeds_the_production_confirmation` |
| 新驱动私有复制 offer 播种、漏掉确认 | 同上（按符号名判定，不按调用形态） | 同上 |
| Task 的动作/文案不是 `comment` + 中文模板 | 确认从 Task 自己的定义读取 | `seed_task_confirmation` |
| 目标确认再次改变 revision 语义 | 断言从 `CONFIRMED_TASK_REVISION` 推导，改一处即可 | 六个驱动 + `task-projection.spec.ts` |
| 终止类命令的 `cancelling` 投影 | 显式计入 revision 表达式并写明理由 | T3-16 / T3-20 / T3-19 |
| 确认后 `current_attempt_id` 被释放 | 断言必须为空（更强，不是放宽） | D6-11 / D6-12 |
| 同一 Installation 两个活跃尝试 | 第二个 offer 等 Installation 空闲后再播种 | `wait_for_idle_installation` |
| App 进程链被半途终止 | 自建会话 + 整树终止，孤儿不再写回 App 数据目录 | `terminate_app_process_tree` + 门禁 |
| Windows 上没有进程组 | 走 `taskkill /PID /T /F` | 同上（本次未在 Windows 实跑） |
| 驱动断言失败但看不出差在哪 | 四处 `invalid` 文案改为打印实际观测值 | T3-11 / T3-13 / T3-15 / T3-16 |

## 真实边界

1. **D6-10 的产品缺陷没有修。** 已确定拒绝来自平台会话守卫而不是忙检查，但没有拿到
   「是 gate 行还是 health 行」这一层证据，因此没有动产品代码。
2. **T3-18 的第二条失败没有定位。** 单活尝试冲突确实修好了（它现在能跑进 spec），
   但工作台不出现「运行中」的原因未知。
3. **T3-15 / T3-16 的设备会话计数断言没有改。** 见上。
4. **H8-16F 一行断言未改。** 退役还是重写是产品决策。
5. **B5-13 未跑。** 需要真实 `douyin.com`，按约束不触碰。
6. **Windows 侧完全未执行。** 本机是 macOS；`terminate_app_process_tree` 的
   `taskkill` 分支只在代码层与 `run_h8_03` 原实现对齐，未实跑。
7. **进程树终止接线波及 21 个驱动，本次只复跑了其中 10 个**
   （T3-12/13/14/15/16/18/19/20、D6-10/11/12、H8-01——去重后 12 个，其中 10 个通过）。
   剩下 B5-13、B5-15、B5-16、E4-14、H8-02、H8-03、H8-14、H8-16E、H8-16F、I2-14 没有复跑；
   该改动只影响失败路径与进程会话，但「没跑过就是没验证」。
8. **二进制里带着并行会话的改动。** 实跑期间 `.local/eb-16/` 另有会话在做正式包；
   `frontend/dist` 被本层的测试构建反复覆盖（开跑前它已经是 `control-plane-e2e` 模式，
   收尾时按原字节还原）。
9. **一次 `cargo test` 被本会话误杀。** 第一次跑到 `publish_workspace` 测试二进制时
   误判为挂起并终止；重跑一次后全绿，记录在此以免把这次终止读成产品问题。

## 清理

- **`~/Library/Application Support/com.aventador.automationtool/`（用户扫码取得的抖音登录态）
  全程未读未写。** 本次所有驱动使用带任务后缀的独立 identifier；开跑前后该目录递归条目数
  一致（675），`embedded-browser-profiles/douyin/df1c89f0-…` 完好。
- 清理了上一轮遗留的 `…t319acceptance` / `…t320acceptance` 两个隔离 App 数据目录
  （它们让 T3-19 / T3-20 在 0.4 秒内失败）；`…pb07acceptance` / `…u904acceptance`
  属于其他会话，未动。
- Docker：每个驱动使用 `automation-tool-<任务>-<pid>` 专属 compose project；收尾
  `docker ps --filter name=automation-tool` 为空，本机其他项目未动。
- 端口：Control Plane 使用 18765–18864 段内空闲端口；未终止、未接管任何进程。
- 进程：每轮驱动之间只清理本项目 debug 构建的 `automation-tool-desktop`、
  `wdio run wdio.*.conf.ts` 与 `tauri-driver`；`.local/eb-16/` 的正式包进程全程未动。
- 定位期间加在 `task-event-stream.spec.ts` / `task-discovery.spec.ts` 里的探针已全部删除；
  `task-event-stream.spec.ts` 与 HEAD 逐字节一致。
- `frontend/dist` 收尾时按开跑前的字节还原；`frontend/src-tauri/target/debug/embedded-browser`
  按前两份 FIX 文档的规则删除，`.local/desktop-e2e/` 缓存保留供复跑。
- 收尾核对：`wdio` / `tauri-driver` / `automation-tool-desktop` / `automation-tool-executor` /
  `Chrome for Testing` 进程全空；`docker ps --filter name=automation-tool` 为空；
  隔离 App 数据目录只剩不属于本次的 `…pb07acceptance` / `…u904acceptance`。
- `.local/eb-16/` 全程未写；该会话的正式包进程从未被本次的清理命中
  （清理只匹配 `frontend/src-tauri/target/debug/automation-tool-desktop`、
  `wdio run wdio.*.conf.ts` 与 `tauri-driver`）。

## 文档

- `scripts/desktop_e2e_prerequisites.py`（新增 `terminate_app_process_tree`）
- `scripts/test_desktop_e2e_prerequisites.py`（12 → 14 项）
- `scripts/run_t3_11_acceptance.py`、`run_t3_12`、`run_t3_13`、`run_t3_14`、`run_t3_15`、
  `run_t3_16`、`run_t3_18`、`run_t3_19`、`run_t3_20`
- `scripts/run_d6_10_acceptance.py`、`run_d6_11`、`run_d6_12`
- 本层其余会启动 App 的驱动（仅进程树终止接线）
- `frontend/e2e-tauri/task-projection.spec.ts`、`task-discovery.spec.ts`
- 本文件

## 遗留项

| 项 | 状态 |
| --- | --- |
| D6-10：平台会话守卫与 Installation 忙检查共用一个错误码 | 未修，需要先拿到 gate/health 证据 |
| T3-18：工作台不出现「运行中」 | 未定位 |
| T3-15 / T3-16：冻结的设备会话计数断言 | 未改，需要各自任务决定断言什么 |
| H8-16F：`repairTrustedBrowser()` 点击已删除的 UI | 未改，属产品决策 |
| B5-13：需要真实 `douyin.com` | 未跑 |
| 本层另外 19 个驱动的进程树终止改动 | 未复跑 |
| Windows 侧全部 | 未执行 |
</content>
