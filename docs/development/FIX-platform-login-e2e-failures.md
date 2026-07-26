# FIX 登录链路三个桌面 E2E 失败

> 状态：B5-15 ✅ 实跑通过；B5-16 ✅ 实跑通过；B5-13 🔍 待验收（需要真实
> `douyin.com` 站点，本次按约束未跑）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/FIX-control-plane-e2e-prerequisites.md` 补齐启动门禁前置后，
> control-plane 层 32 个驱动实跑 16 过 16 败，其中 B5-13 / B5-15 / B5-16 是同一症状：
> 工作台已挂载、「平台状态」页已渲染，点「打开登录处理」后 120 秒内拿不到任何页面事实。

## 结论先说

**四条独立缺陷叠加**，其中**第二条是生产缺陷**——正式 App 里用户点「打开登录处理」
一定失败，与测试无关。

| # | 缺陷 | 引入 | 层 | 影响 |
| --- | --- | --- | --- | --- |
| 1 | B5-15 验收 Executor 夹具的构造签名落后于生产装配 | `1bd21cc`（2026-07-20 10:45） | 验收夹具 | 执行器启动即崩溃，B5-15 / B5-16 |
| 2 | **React Gateway 的严格 Schema 落后于 Rust Command 的返回 DTO** | **`c98f57e`（2026-07-25 21:43，PB-07）** | **生产** | **正式 App 的登录/重新检查全部失败** |
| 3 | 三个驱动读 EB-09 已废弃的 Profile 根 | `f34e503` 一线（EB-09，2026-07-24） | 驱动断言 | B5-13 / B5-15 / B5-16 的阶段后置断言 |
| 4 | 五个驱动各自冻结了一份 Executor ledger schema 版本号 | 各自成文时 | 驱动断言 | B5-15 / B5-16 / E4-14 / H8-03 / H8-16F |

**都不是今晚 `93a6acf` / `e94c17f` / `780abce` 引入的。** 这三个提交在本次定位中被逐条排除：
设备注册链路正常（`prepare_platform_session_reuse_for_acceptance` 注册成功、
`issue_executor_connection` 从未报错）；ffmpeg 注入只作用于 `VideoWorkerLaunch`，
不经过 `spawn_executor`；构建期分叉消除不涉及执行器或浏览器解析路径。

## 定位过程

超时 120 秒是**报错**而不是卡住：`PLATFORM_COMMAND_TIMEOUT` 是 60 秒，
`PlatformSessions.run()` 捕获异常后只 `setFailure(true)`，界面换成
「暂时无法读取抖音登录状态，请稍后重试。」——它不在 spec 等待的事实清单里，
于是 `waitUntil` 干等满 120 秒。这一层信息被 UI 吞掉，所以定位分四轮，
每轮往 `platform-session-reuse.spec.ts` 里加一次性探针（全部已删除）。

| 轮 | 探针 | 观察 |
| --- | --- | --- |
| 1 | 直接 `core.invoke("open_douyin_login")` 打印错误对象 | `{"code":"process_unavailable","retryable":true}`；全程 1 秒粒度轮询 `ps`，执行器进程**从未出现** |
| 2 | `get_executor_diagnostics` | `TypeError: AcceptanceDouyinLoginCommandOperation.__init__() got an unexpected keyword argument 'browser_authority'` —— 缺陷 1 |
| 3 | 修完缺陷 1 后重跑 | 执行器起来了、活着；界面仍是同一句错误；诊断为空（执行器没崩） |
| 4 | 失败分支里读 `get_douyin_platform_session` + 重新 open 并测收敛耗时 | 服务端权威投影**在界面报错时已经是 `healthy`**；重新 open 695 ms 返回 healthy、717 ms 收敛。链路完全正常，问题在 React |

第 4 轮排除了 Rust、执行器、内置 Chromium、WebSocket、Control Plane 和数据库，
把范围收敛到 `TauriPlatformSessionGateway` 的解析。

## 缺陷 1：验收 Executor 夹具的构造签名漂移

`backend/tests/fixtures/b5_15_executor.py` 用
`vars(executor_cli)["DouyinLoginCommandOperation"] = AcceptanceDouyinLoginCommandOperation`
替换生产协作者，但它的 `__init__` 只收 `health_reporter` 和 `outbound`。
`1bd21cc` 给生产装配加上 `browser_authority=` 之后，这个 PyInstaller 包**启动即 `TypeError`**，
退出码 1。B5-15 和 B5-16 都用这个夹具（`automation-tool-executor-b515.spec`）。

同目录的 `h8_16f_executor.py` 早已是正确签名，本次按它的既有形状修正 b5_15，
不新造第二种写法。

**为什么没被任何测试拦住：** 这两个夹具各自被打成独立签名二进制，测试套件里没有任何
地方 import 它们。

## 缺陷 2：Gateway 严格 Schema 落后于 Rust 返回 DTO（生产缺陷）

`open_douyin_login` / `recheck_douyin_login` 原样序列化
`executor_bootstrap::LocalPlatformCommandResult`。PB-07 `c98f57e` 为统一发布 DTO
给它加了两个字段：

```rust
pub struct LocalPlatformCommandResult {
    platform: String,
    state: String,
    flow_version: String,
    confirmation_id: Option<String>,   // c98f57e 新增
    target_account: Option<String>,    // c98f57e 新增
}
```

`Option<String>` 没有 `skip_serializing_if`，所以登录结果**一定**带着这两个 `null`：

```json
{"confirmationId":null,"flowVersion":"douyin.qr-login.v2","platform":"douyin",
 "state":"healthy","targetAccount":null}
```

而 `platformSessionActionSchema` 是 `.strict()` 的三键对象，多出来的键直接判
`protocol_mismatch`。于是：**执行器真的打开了浏览器、真的读到了页面事实、
服务端权威状态真的变成了 healthy，用户看到的却只有「暂时无法读取抖音登录状态」。**

自 2026-07-25 21:43 起，正式 App 的「打开登录处理」和「我已处理，重新检查」两个按钮
都是这个下场。这条不是测试问题，是**首期唯一 RPA 闭环的入口坏了**。

修复：Schema 补上两个 `z.string().nullable()`。不放宽 `.strict()`——严格是刻意的协议钉死，
放宽等于把同类漂移永久变成静默。

## 缺陷 3：驱动读 EB-09 已废弃的 Profile 根

EB-09 把 `BrowserProfileStore` 的根从 `browser-profiles` 换成
`embedded-browser-profiles`，并显式声明旧根**不迁移、不读取、不回退**。
`run_b5_13` / `run_b5_15` / `run_b5_16` 三个驱动仍在读旧根，表现是
**四个 App 阶段全过之后**驱动自己在后置断言上炸：

```text
FileNotFoundError: .../com.aventador.automationtool.b515acceptance/
                   browser-profiles/current-douyin-profile-v1
```

## 缺陷 4：五个驱动各冻结一份 ledger schema 版本号

`EXECUTOR_LEDGER_SCHEMA_VERSION` 现在是 8，而驱动里冻着 `(2,)` / `(2,)` / `(7,)` /
`(5,)` / `(7,)`。每次迁移一次性给所有驱动埋雷，且**只在 App 阶段全过之后**才引爆，
看上去像产品回归。本次全部改为引用产品常量。

## 方案

| 缺陷 | 改动 |
| --- | --- |
| 1 | `backend/tests/fixtures/b5_15_executor.py` 接受并转发 `browser_authority`，与 `h8_16f_executor.py` 同形 |
| 2 | `frontend/src/features/platform-sessions/platform-session-gateway.ts` 的 action schema 补 `confirmationId` / `targetAccount`（可为 null，保持 `.strict()`） |
| 3 | `scripts/desktop_e2e_prerequisites.py` 新增 `OPERATIONS_PROFILE_ROOT` / `CURRENT_DOUYIN_PROFILE_FILE` 单点定义，三个驱动改为引用 |
| 4 | 五个驱动改为 `from automation_tool.executor.ledger import EXECUTOR_LEDGER_SCHEMA_VERSION`；E4-14 的报错文案里写死的 `v7` 一并改成不带版本号的说法 |

## RED

### 1. 执行器装配（`backend/tests/unit/executor/test_platform_command_assembly.py`）

新增用例按生产 `build_platform_command_router` 的原样调用装配每个会替换
`executor_cli` 全局的验收入口，夹具清单从 `backend/tests/fixtures/*_executor.py` 派生。

```text
backend/.venv/bin/python -m pytest tests/unit/executor/test_platform_command_assembly.py -x -q
E   TypeError: AcceptanceDouyinLoginCommandOperation.__init__() got an unexpected keyword argument 'browser_authority'
src/automation_tool/executor/cli.py:106: TypeError
FAILED ...::test_acceptance_executor_entrypoints_assemble_the_production_router[b5_15_executor]
1 failed, 2 passed in 0.16s
```

### 2. 跨语言 DTO 接缝（`frontend/tests/platform-session-command-contract.test.mjs`）

新增契约用例从 Rust 源码解析 `LocalPlatformCommandResult` 的序列化字段，
与 Gateway 的 `.strict()` 对象键逐项比对。原有两处单元测试自己手写了同一份过时
文档，所以只能证明前端和自己一致——缺陷正好落在它们看不见的接缝上。

```text
cd frontend && node --test tests/platform-session-command-contract.test.mjs
✖ the local platform command result has one field set in Rust and the gateway
  AssertionError: platformSessionActionSchema drifted from LocalPlatformCommandResult
    actual: [ 'platform', 'state', 'flowVersion' ]
  expected: [ 'platform', 'state', 'flowVersion', 'confirmationId', 'targetAccount' ]
```

### 3 / 4. 层级防复发门禁（`scripts/test_desktop_e2e_prerequisites.py`）

```text
backend/.venv/bin/python scripts/test_desktop_e2e_prerequisites.py
AssertionError: these drivers still read the pre-EB-09 Profile root instead of
OPERATIONS_PROFILE_ROOT: run_b5_13_acceptance.py, run_b5_15_acceptance.py, run_b5_16_acceptance.py

AssertionError: these drivers compare the Executor ledger schema version against their own
frozen literal instead of EXECUTOR_LEDGER_SCHEMA_VERSION: run_b5_15_acceptance.py,
run_b5_16_acceptance.py, run_e4_14_acceptance.py, run_h8_03_acceptance.py, run_h8_16f_acceptance.py
```

## GREEN

```text
backend/.venv/bin/python -m pytest tests/unit/executor -q
  1123 passed, 1 skipped in 36.80s

cd frontend && node --test tests/platform-session-command-contract.test.mjs
  ℹ pass 1  ℹ fail 0

cd frontend && pnpm exec tsc -b
  （无输出）

cd frontend && pnpm exec vitest run
  Test Files  59 passed (59)
  Tests  462 passed | 1 expected fail (463)

backend/.venv/bin/python scripts/test_desktop_e2e_prerequisites.py
  ok  ...（12 项，新增 2 项）
  desktop e2e prerequisite checks passed (12 checks)

backend/.venv/bin/python -m ruff check scripts/ --select F,E9
  仅 run_ve_04_acceptance.py 的 1 项，为本次之前既有
```

### 驱动实跑

```text
backend/.venv/bin/python scripts/run_b5_15_acceptance.py
  [B5-15] Running hidden restart phase: first     → 1 passed (00:00:10)
  [B5-15] Running hidden restart phase: restart   → 1 passed (00:00:04)
  [B5-15] Running hidden restart phase: expired   → 1 passed (00:00:04)
  [B5-15] Running hidden restart phase: risk      → 1 passed (00:00:04)
  [B5-15] Hidden-App Profile reuse and fail-closed handoff acceptance passed

backend/.venv/bin/python scripts/run_b5_16_acceptance.py
  [B5-16] Live Chrome command-line and open-file isolation audit passed
  1 passed, 1 total (100% completed) in 00:00:09
  [B5-16] Hidden-App default Profile isolation acceptance passed
```

修复前同一阶段耗时 2 分 00 秒（`waitUntil` 干等满），修复后 4–10 秒。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 验收夹具替换生产协作者后签名漂移 | 生产装配调用即 `TypeError`，用例点名夹具 | `test_acceptance_executor_entrypoints_assemble_the_production_router` |
| 新增验收 Executor 入口 | 夹具清单从目录派生，自动纳入 | 同上 |
| Rust 返回 DTO 新增字段而 Gateway 未跟 | 契约用例逐字段比对并点名 | `platform-session-command-contract` |
| 有人把 Gateway schema 放宽成非 `.strict()` | 契约用例直接失败并说明理由 | 同上 |
| Rust struct 改掉 `rename_all = "camelCase"` | 契约用例失败（camelCase 推导前提消失） | 同上 |
| 登录结果带 null 的发布字段 | 解析通过，界面只用 `state` | `platform-session-gateway.test.ts` |
| 登录结果带未声明的额外字段 | 仍判 `protocol_mismatch` | 同上 |
| 登录结果缺少发布字段 | 判 `protocol_mismatch`（生产一定带） | 同上 |
| Profile 根再次改名 | 门禁比对 Rust 常量并点名残留驱动 | `check_every_driver_names_the_operations_profile_root_the_app_writes` |
| ledger schema 再次迁移 | 门禁禁止驱动写字面量 | `check_no_driver_pins_its_own_copy_of_the_executor_ledger_schema_version` |
| 执行器进程起不来 | Rust 报 `process_unavailable`，stderr 进 `get_executor_diagnostics` | 本次定位实际走通 |
| 页面事实 healthy 但服务端投影未收敛 | `readPublishedSnapshot` 5 秒预算；实测收敛 717 ms | 本次实测，未改 |

## 真实边界

1. **B5-13 未跑。** 它用生产 Executor spec 直连真实 `douyin.com`，按本次约束不触碰。
   缺陷 2 足以解释它记录的症状——失败发生在 React 解析层，早于任何页面事实渲染，
   与站点返回什么无关；缺陷 3 的旧 Profile 根断言也已修。但**这是推断，不是验收**：
   修复后它能否通过，取决于真实站点的页面分类，必须实跑才能断言。
2. **E4-14 / H8-03 / H8-16F 的 ledger 版本号一并改为产品常量，但本次没有重跑这三个驱动。**
   替换本身是把错误字面量换成唯一事实源，不改变其他断言；这三个任务各自还有本次范围外的
   其他失败（见上一份 FIX 文档）。
3. **`run_e4_07_acceptance.py` 也冻结着 `(2,)`，本次未改。** 它不在
   `control_plane_e2e_drivers()` 派生出的 32 个驱动里，因此不在新门禁的覆盖范围内，
   也无法在本次实跑验证。
4. **二进制里带着另一个会话正在改的 Rust 代码。** 本次实跑期间
   `frontend/src-tauri/src/{lib.rs,material_video_studio.rs,model_service_settings.rs,
   motion_video_studio.rs,video_editing_service_settings.rs}` 以及新增的
   `command_error.rs` 处于并行会话的工作树修改中（`git status` 可证）。
   所有 App 都由这份工作树编译而来，本次结果不能等同于某个具体提交的结果。
5. **`.strict()` 的 action schema 现在要求两个发布字段必须存在。** 依据是 Rust 侧
   `Option<String>` 无条件序列化；若将来给它们加上 `skip_serializing_if`，
   本文件新增的契约用例会立刻失败，届时应同步改成 optional 而不是删掉断言。
6. **`readPublishedSnapshot` 的 5 秒预算没有动。** 实测一次完整登录到服务端投影收敛
   717 ms，当前有近 7 倍余量；但这是本机 loopback 的数据，远程 Control Plane 下需要复核。
7. **本次没有改任何 spec 断言。** 定位期间加过的探针全部删除，
   `frontend/e2e-tauri/platform-session-reuse.spec.ts` 与 HEAD 逐字节一致。

## 清理

- **`~/Library/Application Support/com.aventador.automationtool/`（用户扫码取得的抖音登录态）
  全程未读未写。** B5-15 / B5-16 使用 `…​.b515acceptance` / `…​.b516acceptance` 独立
  identifier；收尾核对 `embedded-browser-profiles/douyin/df1c89f0-8418-4336-95e8-e38e1a5fae35`
  完好，目录递归条目 674。
- 每轮驱动的隔离 App 数据目录由其自身 `finally` 删除；收尾时
  `com.aventador.automationtool.b515acceptance` / `.b516acceptance` 均不存在。
- Docker：`automation-tool-b515-<pid>` / `automation-tool-b516-<pid>` 专属 compose project；
  收尾 `docker ps --filter name=automation-tool` 为空，本机其他项目未动。
- 进程：收尾 `wdio` / `tauri-driver` / `automation-tool-executor` / `Chrome for Testing` 均为空。
- 端口：Control Plane 与 PostgreSQL 全部由驱动自取空闲 loopback 端口；未终止、未接管任何进程。
- 定位期间临时改过 `scripts/run_e4_14_acceptance.py` 的 Control Plane 日志重定向，已 `git checkout` 还原；
  临时日志 `/tmp/at-probe-cp.log`、`/tmp/at-probe-control-plane.log` 已删除。
- `frontend/src-tauri/target/debug/embedded-browser` 收尾时按上一份 FIX 文档的规则删除，
  不让任何未接线的入口在自己没有声明过的状态下启动；`.local/desktop-e2e/` 缓存保留供复跑。
- `.local/eb-16/` 全程未读未写。
- 未触碰 `frontend/src/features/publish/` 与 `frontend/src/main.tsx`。

## 文档

- `backend/tests/fixtures/b5_15_executor.py`
- `backend/tests/unit/executor/test_platform_command_assembly.py`（新增 1 项参数化用例）
- `frontend/src/features/platform-sessions/platform-session-gateway.ts`
- `frontend/src/features/platform-sessions/platform-session-gateway.test.ts`
- `frontend/src/features/platform-sessions/PlatformSessions.test.tsx`
- `frontend/src/platform/tauri/platform-session-gateway.test.ts`
- `frontend/tests/platform-session-command-contract.test.mjs`（新增）
- `scripts/desktop_e2e_prerequisites.py`
- `scripts/test_desktop_e2e_prerequisites.py`（10 → 12 项）
- `scripts/run_b5_13_acceptance.py`、`run_b5_15_acceptance.py`、`run_b5_16_acceptance.py`
- `scripts/run_e4_14_acceptance.py`、`run_h8_03_acceptance.py`、`run_h8_16f_acceptance.py`
- 本文件

## 遗留项

| 项 | 状态 |
| --- | --- |
| B5-13 需要真实 `douyin.com` 实跑复验 | 未做，本次约束不触碰真实站点 |
| E4-14 / H8-03 / H8-16F 改了 ledger 常量但未重跑 | 未做，各自任务还有本次范围外的失败 |
| `run_e4_07_acceptance.py` 仍冻结 `(2,)` | 未做，不在本层 32 个驱动内，新门禁覆盖不到 |
| `run_p9_06_acceptance.py` / `test_p9_07_acceptance.py` 仍引用旧 `browser-profiles` 根 | 未做，属 P9 打包链路，P9-07 台账已登记同一缺口 |
| 其余 13 个失败驱动（D6-10/11/12、T3-12/13/15/16/18/19/20、H8-16F 等） | 未做，属各自任务 |
| 缺陷 2 是否也影响 U9 之后的远程 Control Plane 形态 | 未验，本次只在 loopback 上实测 |
