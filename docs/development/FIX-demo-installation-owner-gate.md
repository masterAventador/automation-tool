# FIX 无账号归属的 Installation 仍能访问业务 API

> 状态：🔍 待验收（后端生产同路径已闭环——正式 `create_app()` 部署装配 + 真实 FastAPI + 真实 PostgreSQL，
> local 与 demo 两种配置各跑一遍；桌面端不需要改动，见「正常用户路径验收」。缺的是在**真实云端 Demo 环境**上复验，
> 而该环境至今未部署（路线图「当前下一步」的 C10-08 才是部署任务），因此不改绿。）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：调研正式构建首次设备注册链（`docs/development/PLAN-production-device-registration.md` §1.6）时顺带查出，
> 主对话确认拆成独立任务修复。该 PLAN 的 §4.1 第 11 项与 §5 也把这一条列为「建议拆成独立任务」。

## 缺陷

云端客户 Demo 环境下，**一份泄漏的 bootstrap token 就能注册出无账号归属的 Installation，并调用全部业务 API**。

| 环节 | 出处 | 事实 |
| --- | --- | --- |
| Demo 上线即开着 bootstrap 注册端点 | `deploy/customer-demo/compose.v1.json:44-45`、`scripts/deploy_customer_demo.py:51-52` | `DEMO_ENVIRONMENT_ID` 与 `DEMO_BOOTSTRAP_PUBLIC_KEY` 是**必填**，因此 `registration_service_from_environment` 必然装配 |
| 注册出来的 Installation 可以没有 owner | `infrastructure/database/schema.py:425`、`registration.py` | `installations.owner_user_id` 可空；bootstrap 注册路径根本不写这一列 |
| 业务守卫不看 owner | `api/installation_access.py:57-70` | `require_current_installation_access` 只认证 `app.control-plane` 设备 Session |

结果：`owner_user_id IS NULL` 的 Installation 通过全部 18 个受守卫的业务路由处理函数
（installation_access 1、platform_sessions 2、task_controls 4、task_target_previews 3、
task_discoveries 1、task_target_results 1、task_event_stream 1、tasks 3、workbench 2）。

违反项目规则第 4.2 节：

> 云端客户 Demo 必须使用产品账号认证，并把 Installation/设备凭据绑定到已登录账号；账号或设备任一失效都拒绝业务访问，
> **禁止裸露匿名写接口**。

以及 `docs/backend-architecture.md` §9.2 已写下但当时没有任何代码强制的一句：「既有 bootstrap 仅保留在受控测试和
明确迁移边界，**不能用于客户业务 API**」。

## 调研中改掉了原始描述的两处

**① 缺口不止 HTTP 业务 API，执行器通道同样敞着。**
`require_current_installation_access` 只守 HTTP。Executor WebSocket `/api/v1/executors/connect`
（`api/executor_websocket.py:135`）走的是**另一条**守卫 `ExecutorConnectionService.authorize`，能力为
`executor.connect`。无归属 Installation 换到 `executor.connect` 票据后，同样能连上 WebSocket 并向
`TaskEventConvergenceService`、`TaskDiscoveryConvergenceService`、`PlatformSessionHealthService` 写入数据。
只补 HTTP 守卫等于留了第二个匿名写入口。

两条守卫的唯一公共汇合点是 `SqlAlchemyDeviceSessionRepository.authenticate`——它已经在同一事务里
`SELECT ... FOR UPDATE` 锁 Installation 并读出 `owner_user_id`（用于既有的账号停用判定）。门禁落在这里，一处覆盖两条。

**② 本地开发不是「没有 owner 所以会被打死」，而是「本地根本没有账号体系」。**
`account_session_service_from_environment`（`bootstrap/account_sessions.py:65-84`）在账号 Pepper、Pepper 版本、
账号指纹密钥三项**全缺**时返回 `None`，任一缺失即 fail closed 报错。本地开发三项都不配 → 账号服务不装配 →
`create_app` 连账号绑定服务、账号设备服务都不会建。也就是说：**本地部署里「账号归属」这个概念根本不存在**，
不是「存在但为空」。

（另外核实：`installations.owner_user_id` 的唯一写入方是 U9-05 账号绑定
`account_installation_binding_repository.py:137-182`——新公钥 insert 时直接写 owner，已存在且 owner 为空时认领。
Demo 的 App 首个设备凭据正是由 `login_product_account` → `bind_account_installation` 签发的，天生带 owner，
所以本修复不影响 U9 链路的任何一步。绑定端点本身用**账号 access token** 认证，不经设备 Session，
因此也不会出现「因为没绑定所以认证不过、因为认证不过所以绑不上」的死锁。）

## 判定依据：为什么不是新加一个开关

需要一个能区分「云端 Demo」与「本地单机」的依据，且不得是构建期分叉（全局「单一构建路径规范」）。

| 候选 | 结论 |
| --- | --- |
| 新增 `AUTOMATION_TOOL_REQUIRE_INSTALLATION_OWNER` 开关 | **否决**。为了不打死本地，它必须默认关；于是 Demo 少配一个变量就是静默 fail open——正是本项目反复踩的事故形态 |
| 前端 Vite `MODE === "customer-demo"` | 否决。构建期分叉，且客户端说了不算 |
| 复用 `DEMO_ENVIRONMENT_ID` 是否配置 | 否决。那是 bootstrap 注册的配置，语义是「本部署允许受控注册」，不是「本部署有账号体系」；而且 PLAN 方案 A 打算让本地也配它 |
| **由「本部署是否装配产品账号」推导** | **采纳** |

采纳理由：

1. **归属只能存在于有账号的部署**。账号体系成立 ⇔ 归属可建立 ⇔ 归属必须要求。三者是同一个事实，
   不该拆成两个可以互相矛盾的配置项；
2. **不存在 fail open 的配置组合**。开关方案里「账号开着、归属要求关着」是一个可达状态；推导方案里它**不可表达**；
3. **同一条代码路径**。local 与 demo 走的是同一个 `create_app`、同一个 `build_device_session_service`、
   同一个仓储方法，差的只是配置值——落在全局规范允许的第三类差异（指向隔离实例的配置值）里；
4. **判定值不可省略**。`SqlAlchemyDeviceSessionRepository.__init__` 的 `require_installation_owner`
   **没有默认值**，忘了传就是 `TypeError`、服务建不起来，而不是悄悄放行；非 bool 传值直接
   `ValueError`，配置无法判定时同样拒绝构建；
5. **部署配置零改动**。客户 Demo 已经必填账号三件套，因此上线即自动获得该门禁——不需要运维记住任何新变量，
   也不需要改 `deploy/customer-demo/compose.v1.json` 与 `scripts/deploy_customer_demo.py`。

## RED

### 单元（mock Database，不需要 PostgreSQL）

```text
uv run --locked pytest tests/unit/control_plane/test_device_sessions.py -q

E  TypeError: SqlAlchemyDeviceSessionRepository.__init__() got an unexpected
   keyword argument 'require_installation_owner'                    ×5

E  Failed: DID NOT RAISE DeviceSessionRejected
   （test_configured_product_accounts_make_installation_ownership_mandatory）

7 failed, 30 passed
```

### 集成（真实 PostgreSQL，走正式 `create_app()` 部署装配）

把生产判定临时短路成 `if False and self._require_installation_owner:` 后跑新集成文件：

```text
uv run --locked pytest tests/integration/test_installation_owner_access_gate.py -q

>  assert response.status_code == 401, route
E  AssertionError: /api/v1/installations/current
E  assert 200 == 401
E   +  where 200 = <Response [200 OK]>.status_code

E  Failed: DID NOT RAISE WebSocketDenialResponse
   （test_unowned_installation_cannot_open_the_executor_channel_where_accounts_exist）

2 failed, 4 passed
```

两条 RED 的失败原因正是缺陷本身：装配了产品账号的部署里，`owner_user_id IS NULL` 的 Installation
拿到了业务 API 的 200 和执行器通道的握手成功。

**另外 4 条一开始就是绿的，这是有意的**：它们锁的是「本地部署行为不许变」「已归属账号仍能访问」
「账号停用立刻拒绝」，属于回归护栏，不是缺陷证据。

## GREEN

```text
uv run --locked pytest tests/unit/control_plane/test_device_sessions.py -q
37 passed in 0.28s                       （30 → 37，新增 7 条）

uv run --locked pytest tests/integration/test_installation_owner_access_gate.py -q
6 passed in 9.86s                        （新文件 6 条）

uv run --locked pytest tests/unit -x -q
3151 passed, 1 skipped in 40.78s

uv run --locked pytest tests/integration -x -q
321 passed, 5 skipped in 320.59s (0:05:20)
（5 条 skip 均为既有的「显式可见浏览器 / 真实抖音账号」验收，与本次改动无关）

uv run --locked pytest tests/contract -q
274 passed in 4.11s

uv run --locked ruff check <本次改动的 9 个文件>     All checks passed!
uv run --locked mypy src tests                      本次改动文件 0 error（9 条既有 error 在未触碰的文件里）
python3 scripts/check_embedded_browser_video_roadmap.py   台账门禁通过
```

条数逐次核对：`test_device_sessions.py` 30 → 37；集成新文件 0 → 6；单元全量 3144 → 3151。

## 交付

### 判定值（`bootstrap/app.py`）

```python
resolved_device_session_service = build_device_session_service(
    resolved_database,
    require_installation_owner=resolved_account_session_service is not None,
)
```

唯一一处推导，带注释说明「装配账号即要求归属，且不存在装配账号却允许无归属的配置」。

### 强制点（`infrastructure/database/device_session_repository.py`）

`authenticate` 里紧挨既有账号状态判定：owner 为空且本部署要求归属 → `DeviceSessionRejected`。
仍在同一事务、同一把 `FOR UPDATE` 锁下，不新增查询、不新增往返。

构造参数无默认值 + 非 bool 即 `ValueError("Installation owner requirement is invalid")`。

### 覆盖面

| 入口 | 认证 | 本次是否被门禁覆盖 |
| --- | --- | --- |
| 18 个业务路由处理函数（`app.control-plane`） | `require_current_installation_access` | ✅ |
| Executor WebSocket（`executor.connect`） | `ExecutorConnectionService.authorize` / `reauthorize` | ✅（同一仓储方法） |
| `POST /api/v1/device-sessions` 换票 | 设备凭据 bearer | ❌ 有意保留：换出来的票据在两条守卫上都用不了，不构成访问 |
| `device-credentials` 轮换/吊销 | 设备凭据 bearer | ❌ 有意保留：设备必须始终能吊销自己的凭据 |
| bootstrap 注册两步 | bootstrap token | ❌ 不在本任务范围，见「遗留项」 |
| 账号绑定、账号 Session | 账号 access token | ❌ 不涉及设备 Session |

### 错误面

无新增错误码。HTTP 侧收敛为既有固定 `401 installation_access_denied` /
`Installation access is unavailable`；WebSocket 侧收敛为既有 `403` 拒绝握手。
集成测试逐条断言响应里不含 Installation ID、不含 `owner` 字样。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 无归属 Installation + 装配账号 → 业务 HTTP | 401 固定文案，不回显归属 | 集成（真实 PostgreSQL，2 条路由） |
| 无归属 Installation + 装配账号 → 执行器 WebSocket | 403 拒绝握手 | 集成 |
| 无归属 Installation + 无账号（本地） → 业务 HTTP | 200，行为不变 | 集成 |
| 无归属 Installation + 无账号（本地） → 执行器 WebSocket | 握手成功，行为不变 | 集成 |
| 已归属活跃账号 + 装配账号 | 200 | 集成 + 单元 |
| 归属账号被停用 | 下一次请求即 401（票据未过期也拒绝） | 集成 + 单元 |
| 判定值缺失 | `TypeError`，服务建不起来 → 端点 503，不放行 | 结构性（无默认值） |
| 判定值非 bool | `ValueError`，同上 | 单元 |
| 换票端点在门禁开启时 | 仍 201，但票据在两条守卫上都用不了 | 集成（每条用例都先真实换票） |
| Installation 被吊销 / 凭据被吊销 / 票据过期 / 能力不符 | 既有拒绝路径不变 | 既有集成回归（`test_device_session_lifecycle` / `test_installation_revocation_lifecycle` / `test_executor_websocket_lifecycle`） |
| U9 账号绑定链路 | 不变（绑定端点用账号 token，不经设备 Session） | 既有集成回归（`test_account_installation_binding_lifecycle`） |

## 正常用户路径验收

**桌面端不需要改动，也不需要新的用户路径验收**，理由如下：

- 本地 Profile：本部署不装配账号 → 门禁不生效 → 用户可见行为逐位不变；
- 客户 Demo Profile：App 的第一份设备凭据本来就来自 `login_product_account` → `bind_account_installation`
  （`frontend/src-tauri/src/lib.rs:1601`、`control_plane.rs:639`），Installation 创建时即写入 owner，
  从来不会落到无归属状态。生产 `invoke_handler` 里**没有任何 bootstrap 注册命令**
  （PLAN §1.4：唯一调用方全在 `#[cfg(feature = "control-plane-e2e")]` 下），所以 Demo 的正常用户路径不经过被堵的那条。

真实用户路径证据落在**攻击者路径**上：集成测试模拟的正是「拿到 bootstrap token 的人注册出无归属
Installation 后调业务 API」，全部由真实 FastAPI + 真实 PostgreSQL + 正式 `create_app()` 部署装配执行，
不使用 Mock 服务、不使用 `dependency_overrides`。

## 真实边界

1. **bootstrap 注册端点本身仍开着**。本修复让注册出来的 Installation 变得没用，但没有关掉注册。
   关它属于 PLAN §4.1 阶段五（把两个 DEMO 变量由必填改为禁止），与主对话正在推进的本地注册方案耦合，
   不在本任务范围。
2. **换票端点仍可被无归属 Installation 调用**。每次调用会 `INSERT` 一行 `device_sessions`——
   一个可被泄漏 token 触发的存储增长面，未设速率限制。判为可接受（票据无用、且注册本身已是同等量级的写入面），
   但记录在案。
3. **未验证真实云端 Demo 环境**。本任务没有部署、没有连过真实 Demo；结论基于
   `deploy/customer-demo/compose.v1.json` 必填账号三件套这一静态事实。
4. **本地一旦有人配了账号三件套，本地也会开门禁**。这是设计意图（有账号就该要求归属），
   但意味着「本地想试 U9 登录」的开发者必须先完成绑定才能访问业务 API。文档已写明，未做额外提示。
5. **未改任何 Rust / TypeScript**。按本任务硬性约束执行（多个子代理正在改 `lib.rs`），
   桌面端也确实不需要改，但因此没有跑桌面四层门禁中的后两层。

## 清理

未新增常驻服务。集成测试用的 PostgreSQL 由 `tests/integration/conftest.py` 的
`automation-tool-pytest-<pid>` 专属 Compose project + 随机空闲端口启动，退出时自身回收；
本次运行前已确认目标端口未被占用，未终止、未接管任何其他项目的进程或容器。
测试自身在 `finally` 中删除本次 seed 的 Installation / 凭据 / Session / 账号行。

`ruff format` 曾一次性重排 39 个历史文件，已用 `git checkout --` 全部回退，最终改动只剩本任务的 9 个文件。

## 文档

- `backend/src/automation_tool/control_plane/infrastructure/database/device_session_repository.py`（门禁）
- `backend/src/automation_tool/control_plane/bootstrap/device_sessions.py`（判定值透传）
- `backend/src/automation_tool/control_plane/bootstrap/app.py`（唯一一处推导）
- `backend/tests/unit/control_plane/test_device_sessions.py`（30 → 37）
- `backend/tests/integration/test_installation_owner_access_gate.py`（新增，6 条）
- `backend/tests/integration/test_account_installation_binding_lifecycle.py`、
  `test_device_session_lifecycle.py`、`test_executor_websocket_lifecycle.py`、
  `test_installation_revocation_lifecycle.py`（构造点显式声明本地语义）
- `docs/backend-architecture.md` §9.2（把「bootstrap 不能用于客户业务 API」从声明变成可执行的判定规则）
- 本文件

## 遗留项

| 项 | 状态 |
| --- | --- |
| 关掉客户 Demo 的 bootstrap 注册端点（两个 DEMO 变量由必填改为禁止） | 未做，属 PLAN 阶段五，与本地注册方案同批 |
| 换票端点对无归属 Installation 的写入面限流 | 未做 |
| 真实云端 Demo 环境上的验证 | 未做，无常驻 Demo 环境 |
