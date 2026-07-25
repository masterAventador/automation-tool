# FIX control-plane 桌面 E2E 层的启动门禁前置

> 状态：🔍 待验收（32 个驱动全部能真实启动到自己的被测对象，实跑 16 过 16 败；
> 失败项属各自任务范围，本次不改断言、不改产品代码）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/desktop-e2e-run-20260726.md` 实跑证明 `control-plane-e2e` 那一层
> （32 个驱动脚本）自 2026-07-22 起整层跑不起来：App 一启动就被启动门禁挡在诊断页，
> 27 份任务台账没有一份记录过这件事。

## 缺陷

三条独立缺陷叠加，任何一条单独存在都足以让这一层全灭。

### 1. 编译期动作信任配置缺失（`executor_configuration_required`，2026-07-22 `199a021`）

`executor_platform.rs:385 startup_environment_state()` 的**第一步**是

```rust
match ExecutorActionRuntimeInput::from_compile_time_configuration() {
    Ok(Some(_)) => {}
    Ok(None) => return ExecutorStartupState::ConfigurationRequired,
    ...
}
```

三个值来自 `option_env!`，只有 `tauri build` 时带上环境变量才会被编译进二进制。全仓只有 4 个脚本供了它们
（`run_h8_16e` / `run_h8_16f` / `run_p9_03` / `run_p9_04`），其余 28 个驱动**一个都没有**。

**这一步在验证已安装执行器包之前。** `FIX-startup-gate-build-fork.md` 初版把「签名执行器包」列为第二项
前置，方向不对：包装好了也没用，编译命令必须带那三个变量。该文件已在本次一并更正。

### 2. 内置浏览器发行物从未装配（`browser_component_missing`，2026-07-24 `6025ecf`）

EB-08 把浏览器健康状态改成由内置发行物验证决定。`EmbeddedBrowserAuthority::resolve()` 在 App 资源目录
（`--no-bundle` 调试构建下就是 `frontend/src-tauri/target/debug/`）找经 EB-05 逐文件摘要验证的发行物。
**没有任何驱动脚本装配过它**，于是每个 App 都判 `component_missing`，工作台不挂载。

### 3. Control Plane 端口硬编码 8765（执行编排缺陷）

23 个驱动写死 `CONTROL_PLANE_PORT = 8765`，启动前 `require_control_plane_port_available()` 直接硬失败。
串行连跑时上一个进程的监听套接字还在 `TIME_WAIT`，后一个立刻：

```text
OSError: [Errno 48] Address already in use
```

2026-07-26 那次实跑里 **20 个脚本以这种方式在 1 秒内失败，从未构建、从未启动 App**。

## 方案：一处知识，32 个调用点

新增 `scripts/desktop_e2e_prerequisites.py`，把「这一层的 App 要跑起来需要什么」收在一个模块里；
每个驱动只留调用，不留抄写。

| 能力 | 函数 | 解决 |
| --- | --- | --- |
| 四个编译期 `option_env!` 输入一次性注入 | `startup_gate_environment(env, control_plane_port=…)` | 缺陷 1 |
| 项目专属端口段内预检取空闲端口，进程内唯一 | `reserve_control_plane_port()` | 缺陷 3 |
| 从锁定归档构建一次并缓存的内置浏览器发行物 | `build_embedded_browser_cache()` | 缺陷 2 |
| 校验后装配到 App 真正读取的资源目录 | `stage_embedded_browser()` | 缺陷 2 |
| **显式**移除发行物（H8-16E 的被测对象就是被挡住的页面） | `remove_staged_embedded_browser()` | 缺陷 2 |
| 缓存一次的签名 PyInstaller 执行器包并安装 | `install_signed_executor_package()` | 门禁第三项 |
| 驱动实际调用的单一入口 | `prepare_startup_gate(private_app_data, …)` | 全部 |
| 从 `package.json` 派生本层驱动清单 | `control_plane_e2e_drivers()` | 防复发门禁的输入 |

### 端口如何处理

- 端口段 `18765–18864`：`automation-tool` 专属、可追溯（`1` + 原 `8765`），避开本仓已公开的
  8765 / 1420 / 5432 / 5433，也避开驱动交给 PostgreSQL 与 WebDriver 的临时端口；
- `port_is_free()` **不设** `SO_REUSEADDR`：上一轮还没走完 `TIME_WAIT` 的端口算被占用，直接跳到下一个，
  不去和它抢；
- 被占用的端口一律跳过，**从不终止、从不接管**任何进程——占用者可能属于别的项目；
- 整段都被占用时明确失败并要求先停掉上一轮验收，不做静默降级；
- 每个进程只预留一次：`run_h8_01` 从 `run_t3_13` import `CONTROL_PLANE_PORT`，两处必须是同一个值；
- 端口同时写进 `AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN`——Rust Transport 的 local origin 是
  `option_env!`，构建时不给就仍然指向 8765。

`run_b5_13` / `run_b5_15` / `run_b5_16` / `run_e4_14` / `run_h8_16e` / `run_h8_16f` 原本就用
`unused_loopback_port()` 自取端口并写进 origin，从来没有参与过这次的端口冲突，本次**没有**改它们的端口方案，
只补了它们缺的动作信任配置与内置浏览器。

### 32 个驱动分成三类接线

| 类别 | 数量 | 改了什么 |
| --- | --- | --- |
| 写死 8765、自建隔离环境 | 23（`run_t3_*` / `run_d6_*` / `run_i2_*` / `run_h8_04`–`08` / `run_h8_14` / `run_u9_06`） | 端口改为 `reserve_control_plane_port()`；本地端口守卫改为委托；`isolated_environment()` 返回值过一遍 `startup_gate_environment()`；`main()` 加 `prepare_startup_gate()` |
| 已自取端口并写好 origin | 6（`run_b5_13` / `run_b5_15` / `run_b5_16` / `run_e4_14` / `run_h8_16e` / `run_h8_16f`） | 只补动作信任配置（`h8_16e` / `h8_16f` 自带则不覆盖）与内置浏览器 |
| 从上游驱动 import 端口与环境 | 3（`run_h8_01` / `run_h8_02` / `run_h8_03`） | 只加 `prepare_startup_gate()` |

`run_h8_16e` 传 `embedded_browser=False`：它的被测对象**就是**「浏览器组件缺失」的诊断页，
装上浏览器反而会让它失去意义。这一条必须显式写出来，因为资源目录跨轮次留存。

### 内置浏览器如何装配

`run_vf_06_acceptance.stage_video_runtime()` 是先例，`desktop-e2e-run-20260726.md` 也用同样的做法消除过
`browser_component_missing`。本次把它做成可复用能力：

1. `.local/desktop-e2e/embedded-browser/<target>/` 缓存一次（从 EB-03 锁定归档构建，344 MB / 331 文件）；
2. 每次装配前后都跑 `verify_distribution`，摘要不符直接拒绝并清掉半成品；
3. 资源目录里已有且仍然通过校验就不重复拷贝（否则每个驱动多花几十秒拷 344 MB）；
4. 缓存放在 `target/` **之外**——放里面会被 `cargo clean` 悄悄改变启动门禁的结果，那正是本文件描述的事故形态。

## RED

```text
backend/.venv/bin/python scripts/test_desktop_e2e_prerequisites.py
ModuleNotFoundError: No module named 'desktop_e2e_prerequisites'
```

模块建好、驱动尚未接线时，防复发门禁点名全部 32 个驱动：

```text
AssertionError: these drivers build a control-plane-e2e App without the shared startup
gate preparation: run_b5_13_acceptance.py, run_b5_15_acceptance.py, ... run_u9_06_acceptance.py
```

## GREEN

```text
backend/.venv/bin/python scripts/test_desktop_e2e_prerequisites.py
  ok  check_the_startup_gate_environment_supplies_every_compile_time_input
  ok  check_the_startup_gate_environment_does_not_mutate_the_caller
  ok  check_reserved_control_plane_ports_stay_inside_the_project_range
  ok  check_every_control_plane_driver_goes_through_the_shared_preparation
  ok  check_no_control_plane_driver_hardcodes_the_shared_port
  ok  check_staging_rejects_a_browser_tree_that_fails_verification
  ok  check_a_missing_cache_names_the_step_that_builds_it
  ok  check_removing_the_staged_browser_leaves_no_distribution
  ok  check_the_resource_root_is_where_the_acceptance_app_actually_runs
  ok  check_the_derived_driver_set_matches_the_packaged_builds
  desktop e2e prerequisite checks passed (10 checks)

backend/.venv/bin/python -m ruff check scripts/ --select F,E9
  （本次改动的 32 个驱动 0 错；run_ve_04_acceptance.py 的 1 项为本次之前既有）
```

## 防复发门禁

`scripts/test_desktop_e2e_prerequisites.py`，驱动清单由 `frontend/package.json` 派生而非手工维护：

| 断言 | 拦的形态 |
| --- | --- |
| `check_every_control_plane_driver_goes_through_the_shared_preparation` | 新增或改写的驱动绕过共享前置 |
| `check_no_control_plane_driver_hardcodes_the_shared_port` | 端口写死回归 |
| `check_the_derived_driver_set_matches_the_packaged_builds` | 清单退化成手工维护 |
| `check_staging_rejects_a_browser_tree_that_fails_verification` | 未验证的浏览器树被静默装进资源目录 |
| `check_a_missing_cache_names_the_step_that_builds_it` | 缺缓存时报错不指路 |
| `check_removing_the_staged_browser_leaves_no_distribution` | 依赖上一轮残留状态而不是显式声明 |
| `check_the_resource_root_is_where_the_acceptance_app_actually_runs` | 装进没人读的目录 |
| `check_the_startup_gate_environment_supplies_every_compile_time_input` | 只补一部分编译期输入 |

## 实跑结果

2026-07-26 在 macOS 上串行跑完全部 32 个驱动，随后对失败项做了第二轮复跑以区分「稳定失败」
与「负载下的偶发」。取每个驱动的最好一次结果：

| | 数 |
| --- | --- |
| 驱动总数 | 32 |
| **通过** | **16** |
| **失败** | **16** |
| 仍被启动门禁挡住的 | **0**（唯一一处「桌面运行环境需要处理」出现在 H8-16F，而那正是它 spec 期望的起点） |

**通过（16）**：H8-01、H8-02、H8-04、H8-05、H8-06、H8-07、H8-08、H8-14、H8-16E、
I2-09、I2-14、T3-06、T3-07、T3-14、T3-17、U9-06。

对照 2026-07-26 那次实跑：这一层当时 32 个驱动里只有 1 个（H8-16E，且它断言的就是被挡住）通过，
20 个因端口竞速根本没构建。

### 失败逐条定位（16）

断言一行未改，产品代码一行未改。以下全部是启动门禁不再拦路之后暴露出来的下游失败。

| 驱动 | 失败点 | 事实 |
| --- | --- | --- |
| B5-13 | spec 内 | 工作台已挂载、「平台状态」页已渲染；点「打开登录处理」后 120 秒内没有任何页面事实。用的是真实 Executor + 真实 `douyin.com` |
| B5-15 | spec 内 | 同一处。它用的是 `automation-tool-executor-b515.spec` 冻结 Executor，把探测 URL 路由到**本地伪造页面**，所以与抖音站点改版无关 |
| B5-16 | spec 内 | 同一处；等不到扫码事实，App 在外部审计握手前退出 |
| D6-10 | spec 内 | 「竞争任务应显示 Installation busy」未出现 |
| D6-11 | 驱动事后断言 | spec **通过**；驱动查 `execution_attempts` 时 `NoResultFound`——任务有了，当前尝试行没有 |
| D6-12 | 驱动事后断言 | 同 D6-11 |
| E4-14 | 驱动事后断言 | 「Executor ledger did not migrate to v7」 |
| H8-03 | 驱动事后断言 | 「local action emergency latch is not durable」 |
| H8-16F | spec 内 | `.browser-settings-card label.ant-radio-wrapper` 不显示。**这是 EB-10（`f34e503`）删除浏览器选择链路留下的过期用例**：spec 第一步 `repairTrustedBrowser()` 要点「保存浏览器选择」，该按钮在 `frontend/src/` 已无任何渲染处，只剩 `global.css` 里的样式。整段用户路径被产品规则删除，属退役/重写决定，本次未改断言 |
| T3-12 | spec 内 | `core.invoke` 返回错误对象（spec 打印为 `Error: [object Object]`） |
| T3-13 | spec 内 | 同上 |
| T3-15 | spec 内 | `task-projection.spec.ts:43` `assert.strictEqual` 得到 `false !== true` |
| T3-16 | 驱动 seeding | `seed_attempt_and_offer` → `TaskCommandDeliveryRejected`。第一轮更早一步失败在工作台渲染出「工作台数据暂时不可用」 |
| T3-18 | spec 内 | 「Latest Task run text: …」渲染断言未收敛 |
| T3-19 | spec 内 | `core.invoke` 错误对象，App 在创建任务前退出 |
| T3-20 | spec 内 | 同上 |

`Error: [object Object]` 是 spec 把 Tauri Command 的错误结构直接抛出的结果，看不到 `code`。
定位这四个（T3-12/13/19/20）需要 spec 打印错误码，那是各自任务的事，本次没有改 spec。

### 两个执行层面的发现

**1. 负载下有偶发。** H8-04、T3-17、U9-06 在第一轮失败、第二轮通过；T3-17 单独跑也通过。
第一轮期间本机同时有另一个会话在跑 `cargo test`。这三个按最好结果计入通过，但说明这一层
在满负载下不稳定。

**2. 部分驱动会遗留孤儿进程，进而卡死自己的下一次运行。**
`run_t3_16` 与 `run_t3_18` 结束后留下 `ppid=1` 的 `wdio run … .conf.ts` 及其
`automation-tool-desktop` 子进程。孤儿 App 会重新写出隔离 App 数据目录，于是同一驱动的下一次运行
在 0.4 秒内失败在 `Refusing to reuse an existing … App data directory`——第二轮的 T3-16 与 T3-19
就是这么挂的，与产品无关。本次手工清理了这些孤儿并复跑；**没有修**这些驱动的进程树清理，
那是各自任务的收尾职责。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 编译期动作信任配置缺失 | App 判 `executor_configuration_required`，工作台不挂载 | 缺陷 1；`startup_gate_environment` 四项一起注入 |
| 只注入部分编译期输入 | 门禁断言逐项检查四个键 | `test_desktop_e2e_prerequisites` |
| Control Plane 端口被别的进程占用 | 跳到本项目段内下一个空闲端口，不终止占用者 | `reserve_control_plane_port` |
| 端口段整段被占用 | 明确失败并要求先停上一轮，不静默降级 | `reserve_control_plane_port` |
| 上一轮端口仍在 `TIME_WAIT` | 视为被占用并跳过（不设 `SO_REUSEADDR`） | `port_is_free` |
| 同一进程内多个驱动互相 import | 只预留一次，端口值一致 | `check_reserved_control_plane_ports_stay_inside_the_project_range` |
| 浏览器缓存不存在 | 拒绝并点名构建它的脚本 | `check_a_missing_cache_names_the_step_that_builds_it` |
| 浏览器缓存被篡改 / 摘要不符 | 拒绝，且不在资源目录留下半成品 | `check_staging_rejects_a_browser_tree_that_fails_verification` |
| 资源目录里已有的树失效 | 删掉重装，不继承坏状态 | `stage_embedded_browser` |
| 驱动的被测对象就是「组件缺失」页 | 必须显式 `embedded_browser=False`，不靠上一轮残留 | `run_h8_16e` |
| 新增驱动绕过共享前置 | 集合断言点名 | `check_every_control_plane_driver_goes_through_the_shared_preparation` |

## 真实边界

1. **本次只修「跑得起来」，没有修任何用例失败。** 断言一行没改，产品代码一行没改，
   没有跳过任何用例。启动门禁不再是拦路虎之后暴露出来的失败，全部按原样记录在「实跑结果」。
2. **二进制里带着另一个会话正在改的 Rust 代码。** 本次实跑期间
   `frontend/src-tauri/src/{lib.rs,material_video_studio.rs,local_video_orchestrator.rs}`
   处于另一个并行会话的工作树修改中（`git status` 可证）。所有 App 都由这份工作树编译而来，
   因此本次结果不能等同于某个具体提交的结果。
3. **`publishing` 这一配置没有驱动脚本。** `wdio.publishing.conf.ts` 也是
   `control-plane-e2e` 构建，但只由 `pnpm test:publishing-tauri` 拉起，没有 Python 驱动来起
   Control Plane / PostgreSQL / bootstrap，因此它不在这次的 32 个驱动里，也没有被本次修复覆盖。
   给它补一个驱动是新建验收基础设施，属于 PB 系列任务自身的范围。
4. **Windows 侧完全未执行。** 本机是 macOS；`LOCKED_BROWSER_ARCHIVES` 也只登记了两个 macOS 目标。
5. **动作信任配置用的是签入的开发 fixture 公钥**，与 H8-16E/H8-16F/P9-03/P9-04 一致，不是发布密钥。
6. **共享执行器包按 build id 缓存**。缓存命中只检查签名清单与签名文件存在，不重新验签；
   验签仍由 App 的 `validate_installed_package()` 在每次启动时做。
7. **本层的 `reserve_control_plane_port()` 假设串行执行。** 同一台机器上并行跑两个驱动会各自
   预留同一个端口段的首个空闲端口——真正并行需要给每个实例加隔离标识，本次没有做。

## 清理

- **`~/Library/Application Support/com.aventador.automationtool/`（用户扫码取得的抖音登录态）
  全程未被触碰。** 本层 32 个驱动全部使用带任务后缀的独立 identifier（`t306acceptance` 等），
  开跑前后该目录条目数一致（675）；`embedded-browser-profiles/douyin/df1c89f0-…` 完好。
- 每个驱动自己的隔离 App 数据目录在其 `finally` 中删除；中途手动停过一次运行器，
  当时残留的 `b516acceptance` 目录与 `automation-tool-b516-98882` compose 项目已手工清理。
- Docker：每个驱动使用 `automation-tool-<任务>-<pid>` 专属 compose project name，跑完
  `docker ps --filter name=automation-tool` 为空；本机其他项目（`agent-platform-dev-*`）全程未动。
- 端口：Control Plane 使用 18765–18864 段内空闲端口，PostgreSQL 与 WebDriver 仍由驱动自取；
  未终止、未接管任何进程。
- 浏览器与 App 进程：运行器在每个驱动结束后核对 `automation-tool-desktop` / `tauri-driver` / `wdio`
  进程，逐条记录在 `layer-results.json` 的 `residue` 字段。
- 缓存：`.local/desktop-e2e/`（内置浏览器 344 MB + 执行器包 177 MB）保留，供后续复跑；
  已被 `.gitignore` 的 `.local/` 覆盖。
- **`target/debug/embedded-browser` 收尾时删除。** 留着能省下每轮 344 MB 拷贝，但它会让任何
  没有接线的入口（例如直接 `pnpm test:publishing-tauri`）在一个自己没有声明过的状态下启动——
  正是本文件描述的事故形态。每个驱动开跑时会从缓存重新装配（约 10 秒）。
- 收尾时手工清理了 `run_t3_16` / `run_t3_18` 遗留的孤儿 wdio + App 进程树，以及它们重新写出的
  `t316acceptance` / `t318acceptance` / `t319acceptance` App 数据目录。
- `.local/eb-16/` 全程未读未写。

## 文档

- `scripts/desktop_e2e_prerequisites.py`（新增）
- `scripts/test_desktop_e2e_prerequisites.py`（新增，10 项）
- 32 个 `scripts/run_*_acceptance.py` 驱动（接线，无业务逻辑改动）
- `docs/development/FIX-startup-gate-build-fork.md`（更正前置清单顺序）
- 本文件

## 遗留项

| 项 | 状态 |
| --- | --- |
| `wdio.publishing.conf.ts` 缺 Python 驱动，无法在本层跑起来 | 未做，需要 PB 系列任务自建驱动 |
| 视频线（`video-studio-e2e`）复用本模块补齐同样的四项前置 | 未做，见 `FIX-startup-gate-build-fork.md` 遗留项 |
| `mvp-user-journey.spec.ts` 仍点击 EB-10 已删除的「保存浏览器选择」 | **本次没有改断言**，属 H8-16F 的重写/退役决定 |
| `AUTOMATION_TOOL_B515_PAGE_STATE` 由驱动写入但 `src/` 没有任何读者 | 该值实际由 `backend/tests/fixtures/b5_15_executor.py` 冻结包读取，不是废弃注入，但值得在 B5-15 任务里复核 |
| `run_t3_16` / `run_t3_18` 结束后遗留 `ppid=1` 的 wdio + App 进程树，卡死自己的下一次运行 | 未修，属各自任务的收尾职责；本次手工清理并复跑 |
| T3-12/13/19/20 的 spec 把 Tauri 错误对象直接抛出，看不到 `code` | 未改 spec，定位这四条需要各自任务先让错误码可见 |
| 并行跑多个驱动时的端口/资源隔离标识 | 未做，本层目前只支持串行 |
| Windows 目标的内置浏览器缓存 | 未做，`LOCKED_BROWSER_ARCHIVES` 只有两个 macOS 目标 |
