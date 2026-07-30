# FIX control-plane 桌面 E2E 层最后 5 条失败

> 状态：✅ 已完成（原批次 4 条已修并连续两轮实跑通过；D6-10 已由 PC-23
> 于 2026-07-30 按本文冻结的受控签名 Executor 方案收口，历史 RED 与定性保留如下）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/FIX-control-plane-e2e-remaining-failures.md` 把 13 条失败修到 5 条，
> 剩下 5 条被刻意留下——「再往下就是猜」。本次把这 5 条的原因逐条查清。

## 结论先说

5 条底下是 **4 条独立原因**，其中 **1 条是产品缺陷**（本次按 TDD 修复），
2 条是断言落后于 2026-07-21 / 07-26 的产品行为变化，1 条是被 EB-10 删除的用户动作，
1 条是 2026-07-22 的产品行为变化让该驱动的整套验收架构失效——那条**没有修**。

| # | 驱动 | 确切原因 | 分类 | 本次 |
| --- | --- | --- | --- | --- |
| 1 | T3-15 | `app.control-plane` 会话数 = App 存活时长 × 1 Hz 工作台轮询，不是常量；同一份代码跑两次分别得 3 和 4 | 断言本身不可能成立 | 已改为下界 + 说明 |
| 2 | T3-16 | 第 2 个 `executor.connect` 是 `10c2870`（2026-07-21 离线紧急停止）引入的「紧停后重启本机执行器」 | 断言落后于产品行为 | 已改为 2 + 写明两个来源 |
| 3 | T3-18 | 工作台的任务列表是一次性快照，只有被跟随的那个 Task 会实时更新；真正在运行的 Task 永远显示成草稿 | **产品缺陷** | 先写失败测试再修 |
| 3b | T3-18 | 修好之后 spec 一次通过，只剩驱动事后断言 `executor.connect` 恰好 1——与 #2 同一条产品路径 | 断言落后于产品行为 | 与 T3-16 同样改为 2 |
| 4 | H8-16F | `repairTrustedBrowser()` 点击 EB-10 已删除的浏览器选择 UI；驱动还故意不装内置浏览器 | 被删除的用户动作 | 整段删除 + 驱动装配浏览器 |
| 5 | D6-10 | `b69f463`（2026-07-22）让 `start_task_discovery` 先拉起 App 自己的本机执行器；它 0.2 秒内就把发现判为 `login_required`，忙检查的前提（存在活跃尝试）根本不存在 | 产品行为变化使该验收架构失效 | **未修，见下** |

## 定位方法：请求时间线探针

三条（T3-15 / T3-16 / T3-18）都卡在「某个数字对不上」或「界面不动」，而
`device_sessions` 表和页面文本都不记录是谁发起的调用。本次用一个**临时** ASGI 包装把
Control Plane 的每个请求按单调时钟写进一个文件：

```python
# backend/probe_request_log.py（临时，已删除）
def create_probe_app():
    app = create_app()
    async def wrapped(scope, receive, send):
        _record(scope["method"], scope["path"])   # 或 WS + path
        await app(scope, receive, send)
    return wrapped
```

驱动的 uvicorn 临时改为 `probe_request_log:create_probe_app`。三条的原因都是这份时间线
直接读出来的，不是推断出来的。探针文件与驱动改动收尾时全部还原，`git status` 中不留痕。

> 顺带一条事实：uvicorn 自带的 `--access-log` 在本项目不可用——Control Plane 装了自己的
> 脱敏 access logger，记录的是 `'Control Plane request'` + 结构化 extra，uvicorn 的
> `AccessFormatter` 解不出 5 个占位符，每条请求都会打一段
> `ValueError: not enough values to unpack (expected 5, got 0)`。所以驱动里的
> `--no-access-log` 不是可选项，探针必须自己做。

## 逐条

### 1. T3-15：设备会话数不可能是常量（断言本身错）

产品每次 Control Plane 调用都换一张全新的单能力设备会话——`ControlPlaneClient` 的每个方法
都以 `exchange_device_session(vault, AppControlPlane)` 开头，没有任何缓存。因此
`device_sessions` 里 `app.control-plane` 的行数 = **App 活着的时候发出的 Control Plane 调用数**。

而工作台挂载后有两个后台轮询：

```text
frontend/src/features/workbench/workbench-gateway.ts
  workbenchRuntimeStatusQueryOptions  refetchInterval: 1_000   refetchIntervalInBackground: true
  workbenchMetricsQueryOptions        refetchInterval: 10_000  refetchIntervalInBackground: true
```

T3-15 的 App 在注册后只活 0.4–1.5 秒，1 Hz 轮询因此**可能命中 0 次也可能命中 1 次**。
本次同一份代码连跑两次：

```text
run 1  ['app.control-plane' x3, 'executor.connect']   通过
run 2  ['app.control-plane' x4, 'executor.connect']   失败（断言写死 x3）
```

探针把 run 2 的第 4 个会话钉死了：

```text
…262.773 POST /api/v1/device-sessions   →  262.786 POST /api/v1/tasks
…262.818 POST /api/v1/device-sessions   →  262.826 GET  /api/v1/tasks/{id}
…262.839 POST /api/v1/device-sessions   →  262.847 GET  /api/v1/tasks/{id}/events
…262.969 POST /api/v1/device-sessions   →  262.977 WS   /api/v1/executors/connect   ← FakeExecutor
…263.711 POST /api/v1/device-sessions   →  263.720 GET  /api/v1/workbench/status    ← 1 Hz 轮询
```

**这不是产品做了多余的会话交换**：三次是 T3-15 自己的产品调用（建 Task、取快照、取事件流），
第四次是工作台按设计在轮询。**是「恰好 3 个」这个断言本身不可能稳定成立**——它断言的是一个
时间函数。

**修法**：断言改成「`app.control-plane` ≥ 3、`executor.connect` 恰好 1、不出现任何计划外能力」，
并把上面这份时间线写进代码注释。这不是放宽：原断言里真正有意义的两条（能力集合封闭、
App 不用执行器能力、执行器不用 App 能力）一条没少，去掉的只是那个不可能成立的精确计数。

### 2. T3-16：第 2 个 `executor.connect` 是紧停后的本机执行器重启（断言过期）

探针时间线：

```text
399.931 POST /api/v1/tasks/{id}/emergency-stop        ← spec 点「全局紧急停止 / 确认紧停」
399.958 POST /api/v1/device-sessions
399.965 GET  /api/v1/installations/current            ┐ issue_executor_connection
399.975 POST /api/v1/device-sessions                  ┘ （第 2 个 executor.connect）
403.807 WS   /api/v1/executors/connect                ← 重启后的本机执行器接上来
```

对应产品代码 `lib.rs: reconcile_pending_task_emergency_stop`：紧停命令提交成功后，
**必然**走一次 `issue_executor_connection` + `restart_for_task_emergency_stop`，
把被紧停闩掉的本机执行器带着新会话重新拉起来。

这段路径由 `10c2870`「完成离线紧急停止闭环」在 **2026-07-21** 落地；T3-16 的验收记录日期是
**2026-07-18**。也就是说断言写的时候，紧停之后根本没有执行器重启这一步。

**这是产品行为的合理变化**：紧停必须先停住本机执行器，停完之后不把它拉回来，设备就再也接不了
下一个任务。所以第 2 个会话不是多余的，它是紧停闭环的一半。

**修法**：`executor.connect` 由 1 改为 2，并在代码里写清两个来源分别是谁（驱动自己的
FakeExecutor + 产品的紧停后重启）以及为什么 2026-07-18 时是 1。
`app.control-plane` 在 T3-16 本来就只断言集合不断言计数（实测 16 次全是工作台轮询），
本次显式写明了不断言它的理由。

### 3. T3-18：工作台把正在运行的 Task 显示成草稿（产品缺陷）

#### 先排除播种问题

驱动里加了一个临时 DB 轮询探针（0.5 秒一次，已删除）。**从第一次采样开始，数据库就是对的**，
并且在整个 90 秒断言窗口里一动不动：

```text
tasks=[{emergency: draft,   revision 1, last_event_sequence 0, current_attempt_id None},
       {controlled: running, revision 4, last_event_sequence 2, current_attempt_id …}]
commands=[{controlled: task.offer, acknowledged}]
attempts=[{controlled: running}]
```

offer 被消费了，Task 是 `running`，尝试是 `running`。**播种没有问题。**

#### 界面为什么不动

失败时的页面文本（本次实测）：

```text
… 本机执行器在线 累计任务2 … 当前任务 Task ID c38d46a8…(emergency) 状态草稿 Revision1 事件水位0
  最近任务 c38d46a8…草稿  db056b90…草稿
```

`db056b90` 就是数据库里那个 `running` 的 Task，界面上是「草稿」。请求时间线给出了原因：

```text
GET /api/v1/tasks   —— 整整 90 秒里只出现过 1 次（611.716）
GET /api/v1/workbench/status  —— 89 次
GET /api/v1/workbench/metrics —— 10 次
```

`taskListQueryOptions` 是工作台上**唯一没有刷新的查询**：

```ts
// frontend/src/api/control-plane/task-projections.ts（修复前）
return queryOptions({ queryKey: …, queryFn: …, retry: false, staleTime: 0 });
```

它只在挂载和显式失效时取一次。工作台随后只跟随**一个** Task 的事件流
（`currentTask = tasks.find(t => !TERMINAL_STATUSES.has(t.status))`），本次是列表里排第一的
`emergency` 草稿任务——它没有尝试、没有事件，跟随它得到的投影永远是草稿。于是：

- `当前任务` 卡片显示草稿（跟随的就是草稿任务）；
- `最近任务` 里那个真正在运行的 Task 显示的是 611.716 那一刻的状态，永远不刷新。

**这是产品缺陷，不是夹具问题。** 用户视角：一边跑着任务一边新建了第二个任务（Installation
单活约束允许同时存在一个 draft 和一个 running），工作台从此不再显示那个正在跑的任务的真实状态，
直到它进终态才会因为失效被动刷新一次。工作台三块面板，运行状态 1 Hz、指标 10 s 都在轮询，
唯独「报告任务状态的那块」不刷新。

#### RED → GREEN

RED（修复前实跑，两条都准确失败）：

```text
frontend/src/api/control-plane/task-projections.test.ts
  ✕ keeps polling the Task list so a Task nobody is following stops going stale
    expected undefined to be 1000

frontend/src/features/workbench/Workbench.test.tsx
  ✕ refreshes the recent Task list for a Task the live projection does not cover
    Unable to find an element with the text: 运行中
```

第二条是行为级复现：列表第一次返回两个 draft、之后返回其中一个变成 running，工作台跟随的仍是
另一个 draft——和隐藏 App 里看到的完全同一个形状。

GREEN（最小实现）：把快照查询已有的刷新纪律给列表查询一份，两者共用同一个常量：

```ts
const TASK_PROJECTION_REFETCH_INTERVAL_MS = 1_000;   // 原 TASK_SNAPSHOT_REFETCH_INTERVAL_MS
// taskListQueryOptions 增加
refetchInterval: TASK_PROJECTION_REFETCH_INTERVAL_MS,
refetchIntervalInBackground: true,
```

`refetchIntervalInBackground` 是必需的：验收窗口 `visible=false`，真实用户也会最小化窗口，
没有它后台就不刷新——运行状态查询早就是因为同样的理由这么设的。

**没有动 `currentTask` 的选择逻辑。**「当前任务」跟随列表里第一个非终态 Task（哪怕是草稿）
这件事本身也值得商榷，但那会改变工作台订阅哪个 Task 的事件流，影响面比本缺陷大，
本次只记录不改（见「遗留项」）。

### 4. H8-16F：断言 EB-10 已删除的界面（按主对话决定执行）

`repairTrustedBrowser()` 要求 App 起在「桌面运行环境需要处理」诊断页，点「打开本地修复工具」→
选一个浏览器 → 「保存浏览器选择」。EB-10（`f34e503`）把整条浏览器选择链路删了，
`frontend/src/` 里只剩 `global.css` 的一段样式。

删断言只是一半。驱动那边还写着：

```python
# 修复前
# The journey starts on the blocked diagnostics page and repairs the browser
# from settings, so the component has to be genuinely absent at startup.
prepare_startup_gate(private_app_data, embedded_browser=False, executor_package=False)
```

它**故意不装内置浏览器**，就是为了让 App 起在那个诊断页。EB-10 之后这个状态没有出口：
`resolve_embedded_browser` 只认经逐文件摘要验证的内置发行物，没有发现、没有选择、没有回退，
App 会一直停在诊断页，后面整条 MVP 旅程一步都走不了。

**修法**：spec 删掉 `repairTrustedBrowser()` 整段（函数与调用），旅程从真实运营者的第一屏
「RPA 运营工作台」开始；驱动改为 `prepare_startup_gate(private_app_data, executor_package=False)`
——装内置浏览器，执行器包仍由 H8-16F 自己构建签名（它需要受控页面）。

删掉之后 H8-16F 覆盖的仍是首期唯一 RPA 闭环的完整用户路径：
建任务 → 平台登录 → 目标发现 → 排除 → 确认 → 执行 → 结果渲染 → 隐私断言，一步没少；
少掉的只是一个产品里已经不存在的动作。

### 5. D6-10：产品行为变化让整套验收架构失效（未修，理由在下面）

#### 确切原因

D6-10 的验收架构（2026-07-20，`1bd21cc`）是：**驱动进程内自己跑唯一那个正式
`LocalExecutorProcess`**，注入 `DeterministicDiscoveryOperation`，
「避免日常测试触碰真实账号或弹出浏览器」（D6-10 台账原话）。App 自己没有执行器。

`b69f463`（**2026-07-22**）给 `start_task_discovery` 加了一步：

```rust
async fn start_task_discovery(…, platform: State<'_, ExecutorPlatformService>) -> … {
    ensure_executor_running(&client, &vault, &platform).await…?;   // 新增
    client.start_task_discovery(&vault, &task_id, &idempotency_key).await…
}
```

于是今天点「开始目标发现」时，App **先把自己的本机执行器拉起来并等它上线**，再提交发现命令。
本次实测时间线：

```text
941.973 POST /api/v1/device-sessions  →  941.981 GET /api/v1/installations/current
941.988 POST /api/v1/device-sessions                         ┐ issue_executor_connection
946.499 WS   /api/v1/executors/connect                       ┘ App 自己的执行器上线（4.5 秒）
946.518 POST /api/v1/tasks/{id}/discoveries                  ← 第一次发现，这才提交
946.800 POST /api/v1/tasks/{id}/discoveries                  ← 竞争 Task 的发现，0.28 秒后
```

驱动的正式执行器要等 `wait_for_busy_signal` 之后才启动，而那个信号永远等不到。所以
`task.discover` 全部落到 **App 自己的执行器**手里；它的本地账本里没有抖音会话，
`ProductionDouyinDiscoveryOperation` 立刻返回 `login_required`，服务端据此把尝试置 `failed`、
Task 置 `awaiting_platform_login`。

驱动失败时打印的库内事实印证了这一点：

```text
[D6-10] platform health=[{'platform':'douyin','state':'missing','session_revision':1}]
        gates=[] attempts=[{74516a50…: 'failed'}, {f95fa840…: 'failed'}]
```

**两个尝试都存在、都 failed。** 也就是说竞争 Task 的发现根本没被忙检查拦下——第一个尝试在
0.2 秒内就已经失败、腾出了 Installation 的唯一活跃槽位，**「设备忙」这个前提在用户点第二下的
时候已经不存在了**。

（上一轮记录的「第 2 步平台会话守卫拦下 409」是另一次运行的形态：同一个根因在不同的时序下，
既可能表现为平台守卫拒绝，也可能表现为两个尝试都通过并双双失败。两种形态的共同前因都是
App 自己的执行器接管了发现命令。）

#### 三个候选方向为什么都不成立

- **「让夹具把平台会话也置成 healthy，使真实执行器不覆盖它」**——不成立。App 的执行器判断
  「有没有登录」读的是**它自己进程内的 SQLite 账本**（`ledger.get_platform_session("douyin")`），
  服务端 `platform_session_health` 怎么播种都改不了它的判断；而且就算服务端那行守住 healthy，
  第一个尝试仍然会被它判 `login_required` 而 `failed`，忙检查照样没有前提。
- **「让该驱动不装真实执行器」**——不成立。`ensure_executor_running` 排在提交发现之前，
  没有已安装的执行器包，第一次「开始目标发现」直接报错，连第一个尝试都建不出来。
- **「调整播种顺序」**——不成立，同上：问题不在播种时刻，在于命令被谁接走。

#### 该怎么做（本次的验收设计决策，但未实施）

唯一能在真实执行器在场时仍然验到「竞争 Task 显示 Installation 忙」的设计，是让
**App 自己安装的签名执行器包就是那个确定性执行器**——即本仓已经用过两次的
B5-15 / H8-16F 模式：`backend/tests/fixtures/automation-tool-executor-<任务>.spec` +
一个把 `ProductionDouyinDiscoveryOperation` 换成确定性实现的 fixture 入口，
驱动构建并安装它，然后删掉驱动进程内那个执行器。这样只有一个执行器、不再有连接抢占、
不再有平台健康被覆盖，第一个尝试会正常停在 `discovering_targets`，
竞争点击才会真正撞上忙检查。

**本次没有实施**，理由：这不是改一行断言，而是把 D6-10 的验收架构换成另一套
（新 fixture 模块 + 新 PyInstaller spec + 驱动重写 + 一次分钟级签名构建），
它同时影响共用这个驱动的 H8-16B 断言。把它塞进这次「修最后 5 条」里，
要么做不完，要么做成一个没验证透的半成品——两者都比如实停下更糟。
D6-10 因此**仍然失败**，原因已经查到底，做法已经写死在上面。

## RED / GREEN

### RED（产品缺陷，T3-18）

```text
cd frontend && npx vitest run src/api/control-plane/task-projections.test.ts \
                             src/features/workbench/Workbench.test.tsx
  ✕ keeps polling the Task list so a Task nobody is following stops going stale
      expected undefined to be 1000
  ✕ refreshes the recent Task list for a Task the live projection does not cover
      Unable to find an element with the text: 运行中
  Test Files  2 failed (2)
       Tests  2 failed | 10 passed (12)
```

### GREEN

```text
cd frontend && npx vitest run src/api/control-plane/task-projections.test.ts \
                             src/features/workbench/Workbench.test.tsx
  Test Files  2 passed (2)
       Tests  12 passed (12)

cd frontend && npx vitest run
  Test Files  59 passed (59)
       Tests  464 passed | 1 expected fail (465)      （改前 462 + 1）

cd frontend && npx tsc -b --pretty false      无输出
cd frontend && npx eslint . --max-warnings 0  退出码 0

cd backend && uv run --locked pytest tests/unit -q
  3166 passed, 1 skipped in 42.36s

backend/.venv/bin/python scripts/test_desktop_e2e_prerequisites.py
  desktop e2e prerequisite checks passed (14 checks)

backend/.venv/bin/python -m ruff check scripts/ --select F,E9
  仅 run_ve_04_acceptance.py 的 1 项，本次之前既有

cd frontend/src-tauri && cargo test
  无 FAILED、无 panic（0 条匹配 `test result: FAILED|panicked`）

cd frontend/src-tauri && cargo test --test single_build_path
  test result: ok. 7 passed; 0 failed
```

## 实跑结果

三轮，全部串行，并与另一个会话的批次协作互斥（见「真实边界」第 6 条）。

| | 数 |
| --- | --- |
| 本次目标失败 | 5 |
| **修好并连续两轮实跑通过** | **4** |
| 仍失败（原因已查清，未修） | 1（D6-10） |

| 驱动 | 第 1 轮 | 第 2 轮 | 第 3 轮 | 说明 |
| --- | --- | --- | --- | --- |
| T3-15 | ✅ | — | ✅ | 会话计数改为下界后不再随 App 存活时长翻车 |
| T3-16 | ✅ | — | ✅ | `executor.connect` = 2（FakeExecutor + 紧停后重启） |
| T3-18 | ❌ | ✅ | ✅ | 第 1 轮 **spec 已通过（1 passing, 9.5s）**，只剩驱动事后的会话断言；那是与 T3-16 完全相同的紧停重启，改成 2 之后通过 |
| H8-16F | ✅ | — | ✅ | 完整 MVP 用户旅程 1 passing |
| D6-10 | ❌ | — | ❌ | 两轮症状逐字一致：两个尝试都 `failed`、平台健康 `missing` |

T3-18 值得单独说一句：**产品修复之后，隐藏 App 里的 spec 一次就过了**——
「工作台不显示运行中」这个症状随着任务列表恢复刷新而消失，剩下的是另一条已知原因。
这是产品修复有效的直接证据，不是靠改断言换来的。

D6-10 第 3 轮的库内事实与第 1 轮完全一致，确认这不是负载偶发：

```text
[D6-10] platform health=[{'platform':'douyin','state':'missing','session_revision':1}]
        gates=[] attempts=[{8a6e21f4…:'failed'}, {3a89d63b…:'failed'}]
```

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 设备会话计数随 App 存活时长变化 | 断言改为下界，不再是时间函数 | T3-15 |
| App 用执行器能力 / 执行器用 App 能力 | 仍然按能力计数逐项拦下 | T3-15 / T3-16 |
| 出现计划外的会话能力 | 集合断言保留 | T3-15 / T3-16 / T3-18 / D6-10 |
| 紧停后本机执行器重启 | 计入并写明来源，不再被当成异常 | T3-16 |
| 工作台不跟随的 Task 状态变化 | 列表按 1 Hz 刷新 | `Workbench.test.tsx` 行为用例 |
| 窗口不可见 / 最小化时列表不刷新 | `refetchIntervalInBackground: true` | `task-projections.test.ts` |
| 列表刷新纪律被单独改掉 | 与快照查询共用同一个常量 | `TASK_PROJECTION_REFETCH_INTERVAL_MS` |
| 内置浏览器缺失时进入 MVP 旅程 | 驱动装配发行物，旅程从工作台开始 | H8-16F |
| 用例点击产品里已不存在的用户动作 | 整段删除，不保留「证明一个没有的页面」 | `mvp-user-journey.spec.ts` |
| App 自己的执行器与驱动夹具执行器抢同一个 Installation | **未解决** | D6-10 / H8-16B |

## 真实边界

1. **D6-10 没有修。** 原因查到底了（`b69f463` 之后 App 自己的执行器接管发现命令，
   第一个尝试 0.2 秒内失败，忙检查前提消失），做法也写死了（任务专属签名执行器包），
   但没有实施——那是 D6-10/H8-16B 自己的验收架构改造。
2. **T3-15 的会话计数改成了下界。** 上界不可断言：它等于 App 存活时长 × 1 Hz 轮询。
   本次没有去改产品的「每次调用换一张新会话」设计，也没有给设备会话加缓存——
   那是安全设计选择，不在本次范围。
3. **「当前任务」跟随草稿 Task 的行为没有改。** 见 T3-18 一节末尾。
4. **B5-13 未跑。** 需要真实 `douyin.com`，按约束不触碰真实抖音账号。
5. **Windows 侧完全未执行。** 本机是 macOS。
6. **本次实跑与另一个会话并行。** 同一 Claude 会话的另一个代理在跑 `d1`–`d4` 四个 E2E 批次，
   并且在改 `frontend/scripts/audit-production-package.mjs`、
   `scripts/run_e4_15/eb_16/p9_03/p9_04` 等包审计脚本。两边都会重写共享的
   `frontend/dist` 与 `target/debug` 二进制，因此本次的驱动运行采用与对方相同的
   协作式串行：开跑前轮询 `wdio run` / `tauri build`，有人在跑就等。
   本次结果不能等同于某个具体提交的结果。
   另外：本次**没有执行任何 `git add` / `git commit`**，但对方在 `a04a5fe`
   「包审计的结论绑到被审计的产物」里把本次改过的四个驱动脚本
   （`run_t3_15` / `run_t3_16` / `run_t3_18` / `run_h8_16f`）一并提交了。
   内容与本文件描述一致，已逐条核对；前端四个文件与本文件仍未提交。
7. **探针只覆盖 HTTP/WS 边界。** `platform_session_health` 在 D6-10 里从
   `healthy` 变成 `missing` 这件事，本次没有钉到具体的写入方（执行器上报还是登录复检），
   因为它不改变结论——决定性事实是两个尝试都已 `failed`。这一点如实记在这里。

## 清理

- **`~/Library/Application Support/com.aventador.automationtool/`（用户扫码取得的抖音登录态）
  全程未读未写。** 本次所有驱动使用带任务后缀的独立 identifier；开跑前后该目录递归条目数
  一致（674），`embedded-browser-profiles/douyin/df1c89f0-8418-4336-95e8-e38e1a5fae35` 完好。
- 临时探针全部删除：`backend/probe_request_log.py`（新建后删除）、四个驱动里的
  uvicorn factory / 环境变量改动、`run_t3_18_acceptance.py` 里的 DB 轮询探针。
  `run_d6_10_acceptance.py` 逐字节回到未修改状态。
- **收尾时清掉了一个残留进程**：D6-10 结束后
  `…automationtool.d610acceptance/local-executor/package/automation-tool-executor` 仍在跑。
  它是 App 自己拉起的执行器，驱动的 `finally` 只回收自己启动的东西，回收不到它——
  这正是 D6-10 那条根因的另一种表现。已按路径精确匹配终止，只杀本次 d610 实例。
- 收尾核对：`wdio` / `tauri-driver` / `automation-tool-desktop` / `automation-tool-executor` /
  `Chrome for Testing` 进程全空；`docker ps -a --filter name=automation-tool` 为空；
  隔离 App 数据目录只剩不属于本次的 `…pb07acceptance` / `…u904acceptance`。
- `target/debug/embedded-browser` 按前两份 FIX 文档的规则收尾删除；
  `.local/desktop-e2e/`（内置浏览器 344 MB + 执行器包 177 MB）保留供复跑。
- **`frontend/dist` 没有还原成开跑前的字节**，留在最后一次驱动构建（H8-16F）产出的状态。
  前一轮的「按原字节还原」假设独占，本次不成立：另一个会话正在同一目录反复构建并做包审计，
  把一份更旧的产物盖回去只会把污染变严重。开跑前的备份已留存在本次 scratchpad。
- 端口：Control Plane 使用 18765–18864 段内空闲端口，未终止、未接管任何进程。
- Docker：每个驱动使用 `automation-tool-<任务>-<pid>` 专属 compose project。
- `.local/eb-16/` 全程未读未写；未编辑另一个会话正在修改的任何文件。

## 文档

- `frontend/src/api/control-plane/task-projections.ts`（产品修复）
- `frontend/src/api/control-plane/task-projections.test.ts`（RED）
- `frontend/src/features/workbench/Workbench.test.tsx`（RED，行为级复现）
- `frontend/e2e-tauri/mvp-user-journey.spec.ts`（删除已退役的用户动作）
- `scripts/run_t3_15_acceptance.py`、`scripts/run_t3_16_acceptance.py`、
  `scripts/run_h8_16f_acceptance.py`
- 本文件

## 遗留项

| 项 | 状态 |
| --- | --- |
| D6-10 / H8-16B：改用任务专属签名执行器包 | 未做，原因与做法见上 |
| D6-10 驱动回收不到 App 自己拉起的执行器进程 | 未修，与上一条同源；本次收尾手工清理 |
| `frontend/e2e-tauri/browser-settings.spec.ts` 仍在断言 EB-10 删除的浏览器选择卡片 | 未动，属 B5-04 驱动（`browser-settings-e2e` 构建），不在本层 32 个驱动内 |
| 工作台「当前任务」会跟随一个 draft Task，导致真正在跑的 Task 不被订阅 | 未改，本次只记录 |
| 设备会话每调用一换，工作台 1 Hz 轮询下每秒新增一行 `device_sessions` | 未改，属安全设计选择，但值得单独评估保留期与清理 |
| B5-13：需要真实 `douyin.com` | 未跑 |
| Windows 侧全部 | 未执行 |
