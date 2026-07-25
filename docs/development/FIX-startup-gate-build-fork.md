# FIX 测试构建对启动门禁与运行时资源解析的短路

> 状态：🔍 待验收（构建期分叉已消除，三种构建与全量 Rust 测试通过；视频线 WDIO 验收因此失去可运行环境，见「真实边界」）
>
> 日期：2026-07-26
>
> 提交：分两处。`lib.rs` 的分叉 1-5 已被主会话的 `74fb282` 一并带入（本任务未自行提交，是主会话
> 打包提交时扫进去的）；分叉 6（`material_video_studio.rs`）、分叉 7（`PublishWorkspaceState`）、
> 防复发门禁与本文件仍在工作树，待主会话把关后提交。
>
> 触发：用户在 macOS 正式包上试用，视频制作、视频剪辑、抖音登录全部不可用，而全部自动化验收长期全绿。
> 承接 `FIX-video-runtime-release-assembly.md` 与 `pending-acceptance-audit-20260726.md` 第 4.2 / 6-② 节。

## 任务

把 `frontend/src-tauri/` 里不属于「单一构建路径规范」三类允许差异的构建期分叉全部消除，并加防复发门禁。

允许的三类差异（`~/.claude/CLAUDE.md`）：

1. 测试驱动的挂载（WebDriver、自动化端口——安全规则要求正式包不得包含）；
2. 窗口可见性、日志级别等不影响业务路径的运行参数；
3. 指向隔离实例的配置值，**但读取配置的代码路径必须同一条**。

「无条件返回就绪」和「按构建改变去哪里找文件」都不属于其中任何一类。

## 缺陷

启动门禁是生产中**唯一**会因为运行时资源缺失而拦住用户、不挂载工作台的机制。`video-studio-e2e`
构建把它整个短路，同时又把资源解析改成读环境变量。两条叠加的后果是：

> 视频线的测试构建对「资源到底在不在包里」这件事完全免疫。

而 `frontend/src/test-production-main.ts` 只是 `import "@wdio/tauri-plugin"` 后转发到真实
`main.tsx`——前端跑的确实是生产装配，所以短路只能发生在 Rust 侧，也确实发生在 Rust 侧。27 份任务台账
没有一份记录过自己跑的构建把启动门禁关掉了。

## 分叉全清单

`frontend/src-tauri/src/` 共 **77 处** `cfg(...feature...)`（`grep -rn 'cfg(feature = ' src/` 得 69 处，
加上 8 处复合谓词中的 `cfg(any(...))` / `cfg(all(...))`）。按「是否属于三类允许差异」分类：

### 违规，必须消除（7 处 → 已全部消除）

| # | 位置 | 当前行为 | 违规原因 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | `lib.rs:785` `check_local_startup_environment`（`video-studio-e2e`） | **无条件返回 AppData/Executor/EmbeddedBrowser 三项全部 Ready** | 不属于任何一类；短路唯一的资源缺失拦截点 | 删除桩，生产实现改为无条件编译 |
| 2 | `lib.rs:1499` `check_control_plane_health`（`video-studio-e2e`） | **无条件返回 `status: "available"`**，不发任何请求 | 同上；宣告一个从未联系过的服务 | 删除桩与 `VideoStudioAcceptanceHealth`；`desktop-e2e` 变体走真实 `check_health()` |
| 3 | `lib.rs:329/331` `MotionWorkerSource` | 枚举按 feature 分出 `Executable` / `Package` 两个变体 | 为分叉 4 服务 | 删除枚举，`MotionRuntimePaths.worker_package` 直接持 `PathBuf` |
| 4 | `lib.rs:339/364` `motion_runtime_paths` | 从 `AUTOMATION_TOOL_BM08_WORKER` / `_BROWSER` / `_FFMPEG` / `_CHROMIUM_MAJOR` 读路径 | **按构建改变去哪里找可执行程序** | 删除环境变量分支，所有构建走资源目录 + 内置浏览器授权 + `VideoMediaToolchain` |
| 5 | `lib.rs:409/419` `motion_worker_launch` | match 臂按 feature 分出裸可执行 / `bundled_node` 两条启动路径 | 同上 | 收敛为单条 `bundled_node` |
| 6 | `material_video_studio.rs:511` `worker_executable` | `AUTOMATION_TOOL_IM05_WORKER` 覆盖资源目录解析 | 同上 | 删除覆盖 |
| 7 | `lib.rs:1180` `PublishWorkspaceState` | 类型按 feature 门控，`app.manage()` 却无条件调用 | 导致 `video-studio-e2e` 组合**根本编译不过**（HEAD 即如此） | 类型改为无条件声明 |

### 允许（1）测试驱动的挂载

| 位置 | 说明 |
| --- | --- |
| `lib.rs:3862` `desktop-test-driver` | 挂 `tauri-plugin-wdio` / `-webdriver`。安全规则要求正式包不含，只能是构建差异；由 `frontend/scripts/audit-production-package.mjs` 的禁止串检查覆盖 |
| `executor_manager.rs:633/645/655`、`1080/1090/1100/1113/1158/1163` | `inject_crash_for_acceptance` / `inject_hang_for_acceptance` / `inject_raw_diagnostic_for_acceptance` 及其 unix / windows / 其余三种实现。**追加**能力，且注入的是失败不是成功 |
| `executor_platform.rs:436/677/684` | 上述注入器的同名透传 |
| `lib.rs` 约 40 处 `*_for_acceptance` 命令（`control-plane-e2e`） | 追加的验收准备 Tauri Command，全部驱动**真实** `ControlPlaneClient`，不替换任何生产路径 |

### 允许（3）指向隔离实例的配置值

| 位置 | 说明 |
| --- | --- |
| `control_plane.rs:480/486` `configured_local_control_plane_origin` | 只有端口值不同，两个变体都流经同一个 `validated_loopback_origin` |
| `lib.rs` 各 `*_BOOTSTRAP_TOKEN` / `*_ENVIRONMENT_ID`（`std::env::var`） | 隔离实例的引导凭据，只被验收专用命令读取 |
| `executor_platform.rs:865/870` `executor_verifying_key` | debug 用签入的开发 fixture 公钥，release 由打包流水线提供；`build.rs:63` 在 release 强制存在且为有效非弱 Ed25519 |
| `app_update_coordinator.rs:575/580` 更新配置 | 同上，`build.rs:86` 在 release 强制 HTTPS / 占位符 / Minisign 公钥可解析 |
| `lib.rs:893/904` `export_diagnostics` | `AUTOMATION_TOOL_H813_EXPORT_DIRECTORY` 改的是**导出目的地**，不是依赖查找位置，无法掩盖资源缺失。已登记进防复发门禁的评审白名单，附理由 |

### 边界项：保留但已登记（不是「返回就绪」，但确实是行为分叉）

| 位置 | 说明 |
| --- | --- |
| `lib.rs:999` / `1473` `restart_executor` | 纯 UI 的 `desktop-e2e` 构建没有控制面连接材料，其变体返回 `Err(operation_unavailable)`。方向安全——它拒绝，不假装成功 |
| `lib.rs:1483` / `1508` `check_control_plane_health` | `desktop-e2e` 构建用临时身份、没有生产凭据保险箱，跑不了 installation-access 检查。**两个变体都发真实 `check_health()` 请求**，门禁强制这一点 |
| `lib.rs:3838` 临时身份 vs 生产身份 | `desktop-e2e` 用 `initialize_ephemeral_identity()`。属于凭据隔离，但读取路径确实不同一条。**未消除**，见「遗留项」 |
| `lib.rs:3868/3901/3965` 三份 `invoke_handler` 命令表 | `desktop-e2e` 构建注册的命令集是生产的子集。**未消除**，见「遗留项」 |
| `lib.rs:3819/3822` 执行器包根（`debug_assertions`） | debug 从 `app_data/local-executor/package` 找，release 从 `Resources/local-executor/package` 找。**这是同一病根的第二实例**，见「遗留项」 |

## 关于 `control-plane-e2e` 那一批的结论

**没有找到第二个「无条件返回就绪」。** 这不是抽样结论，是全量结构化扫描的结论：

```text
按 (文件, 函数名, cfg 谓词) 全量提取 src/*.rs 的 fn 定义，取所有被同名定义遮蔽的条目：

  control_plane.rs::configured_local_control_plane_origin  （配置值，允许 3）
  executor_manager.rs::inject_abnormal_process_exit        （按 OS 分实现，同一 feature）
  executor_manager.rs::suspend_process_for_acceptance      （同上）
  lib.rs::check_local_startup_environment                  ← 违规 1
  lib.rs::check_control_plane_health                       ← 违规 2
  lib.rs::restart_executor                                 （返回 Err，方向安全）
```

`control-plane-e2e` 的约 60 处里，**没有一处遮蔽生产函数**。它们全是新增的
`*_for_acceptance` 命令与 `any(not(desktop-e2e), control-plane-e2e)` 形态的
「生产 ∪ 控制面验收」条件编译。后一种谓词在 release 下求值为真，即**生产代码**——不是测试分支。

## RED

新增 `frontend/src-tauri/tests/single_build_path.rs`，7 项，全部先失败：

```text
cargo test --test single_build_path

---- no_source_file_relocates_a_runtime_dependency_through_the_environment ----
  src/lib.rs reads AUTOMATION_TOOL_BM08_WORKER
  src/lib.rs reads AUTOMATION_TOOL_BM08_BROWSER
  src/lib.rs reads AUTOMATION_TOOL_BM08_FFMPEG
  src/lib.rs reads AUTOMATION_TOOL_BM08_CHROMIUM_MAJOR
  src/material_video_studio.rs reads AUTOMATION_TOOL_IM05_WORKER

---- runtime_dependencies_resolve_from_the_package_in_every_build ----
  lib.rs::motion_runtime_paths branches on a Cargo feature, so a test build
  resolves the dependency from somewhere the release never looks

---- every_control_plane_health_variant_performs_the_real_request ----
  a check_control_plane_health variant gated by #[cfg(feature = "video-studio-e2e")]
  never calls check_health, so its build reports a Control Plane it has not contacted

---- the_startup_gate_is_compiled_identically_in_every_build ----
  check_local_startup_environment has 2 definitions; the startup gate is the only
  thing that stops a user whose install is missing a resource
  left: 2   right: 1

---- test_builds_never_declare_the_local_environment_ready_without_probing ----
  lib.rs::check_local_startup_environment declares AppDataStartupState::Ready
  lib.rs::check_local_startup_environment declares ExecutorStartupState::Ready
  lib.rs::check_local_startup_environment declares EmbeddedBrowserStartupState::Ready
  lib.rs::check_control_plane_health declares status: "available"

---- production_functions_carry_no_unreviewed_inline_feature_branch ----
  left  含 lib.rs::motion_runtime_paths, lib.rs::motion_worker_launch,
        material_video_studio.rs::worker_executable
  right 只含四项已评审条目

---- every_feature_forked_function_is_reviewed ----
  left 多出 lib.rs::check_local_startup_environment

test result: FAILED. 0 passed; 7 failed
```

`cargo build --features video-studio-e2e` 也是 RED，且**在 HEAD 上就是 RED**（分叉 7）：

```text
error[E0425]: cannot find function, tuple struct or tuple variant
              `PublishWorkspaceState` in this scope
    --> src/lib.rs:3742:24
note: found an item that was configured out
    --> src/lib.rs:1181:12
1180 | #[cfg(any(not(feature = "desktop-e2e"), feature = "control-plane-e2e"))]
```

新增 `scripts/test_video_studio_runtime_staging.py`，6 项，先失败：

```text
python3 scripts/test_video_studio_runtime_staging.py
ImportError: cannot import name 'EMBEDDED_BROWSER_MANIFEST' from 'run_vf_06_acceptance'
```

### RED 的自检

- 条数逐次核对：`single_build_path` RED 7 项 / GREEN 7 项，`runtime_staging` RED 6 项 / GREEN 6 项，
  `cargo test` 全量 321 → **328**（+7），无静默漏收集；
- 每条失败原因都被逐条读过，确认指向本次要修的那件事，不是别的原因；
- 门禁初版把 `#[cfg(not(feature = "*-e2e"))]` 也判成测试代码（生产变体被误报）。已改为**按 release 求值
  cfg 谓词**（features 全关、其余原子全开）后重跑 RED，误报消失，失败条目只剩真正的测试专用变体。

## GREEN

```text
cargo test --test single_build_path
  running 7 tests
  test no_source_file_relocates_a_runtime_dependency_through_the_environment ... ok
  test runtime_dependencies_resolve_from_the_package_in_every_build ... ok
  test the_startup_gate_is_compiled_identically_in_every_build ... ok
  test every_control_plane_health_variant_performs_the_real_request ... ok
  test test_builds_never_declare_the_local_environment_ready_without_probing ... ok
  test production_functions_carry_no_unreviewed_inline_feature_branch ... ok
  test every_feature_forked_function_is_reviewed ... ok
  test result: ok. 7 passed; 0 failed; 0 ignored

cargo test                                    passed=328 failed=0 ignored=6
cargo build --features video-studio-e2e       Finished `dev` profile in 6.76s
cargo build --features control-plane-e2e      Finished `dev` profile in 3.07s
cargo build --features desktop-e2e            Finished `dev` profile in 10.59s
cargo build --release                         Finished `release` profile in 13.35s
npx tsc -b --force                            OK（本次改动落地后即时执行）
npx eslint .                                  OK（复跑仍为 0）

python3 scripts/test_video_studio_runtime_staging.py
  ok  check_the_resource_root_is_where_the_acceptance_app_actually_runs
  ok  check_staging_installs_every_resource_where_the_release_reads_it
  ok  check_incomplete_staging_is_rejected_and_leaves_nothing_behind
  ok  check_an_empty_required_file_is_rejected
  ok  check_restaging_replaces_a_previous_run
  ok  check_a_missing_embedded_browser_names_the_provisioning_step
  video studio runtime staging checks passed (6 checks)
```

**`npx tsc -b --force` 的复跑现在是红的，但与本次改动无关。** 本次未改任何 TypeScript。
复跑时全部报错落在并行会话正在编辑中的两个文件：

```text
src/features/legal/third-party-software/third-party-software-notice.ts
src/features/legal/third-party-software/third-party-software-notice.test.ts
```

（`licenseText` → `licenseTextId` 重构进行到一半。这两个文件的修改时间是 03:15:27 / 03:16:01，
本次改动最后一次落盘是 03:11:32，中间那次 `tsc -b --force` 是绿的。）`npx eslint .` 复跑仍为 0。

`cargo build --release` 需要打包流水线密钥（`build.rs` 的 fail-closed 门禁）。本次用签入的开发
fixture 签名者公钥与 `run_p9_04_acceptance.py` 的更新配置常量满足门禁，仅用于让 release profile 编译，
**不产出可分发包**。

## 防复发门禁

`frontend/src-tauri/tests/single_build_path.rs` 读本 crate 源码，随 `cargo test` 一起跑
（先例：`scripts/test_release_assembly.py` 读脚本源码、`frontend/src/app/production-wiring.test.ts`
读 `main.tsx` 源码）：

| 断言 | 拦的形态 |
| --- | --- |
| `test_builds_never_declare_the_local_environment_ready_without_probing` | 任何**只有测试构建才编译**的函数体里出现 `AppDataStartupState::Ready` / `ExecutorStartupState::Ready` / `EmbeddedBrowserStartupState::Ready` / `status: "available"` |
| `runtime_dependencies_resolve_from_the_package_in_every_build` | `motion_runtime_paths` / `motion_worker_launch` / `worker_executable` 出现 feature 门控、内联 `cfg(feature`、或 `env::var` |
| `no_source_file_relocates_a_runtime_dependency_through_the_environment` | 五个已废弃的路径注入环境变量以任何形式回到 `src/` |
| `production_functions_carry_no_unreviewed_inline_feature_branch` | 生产函数**新增或删除**内联测试 feature 分支，与评审白名单不符即失败 |
| `every_feature_forked_function_is_reviewed` | 出现新的「同名函数按 feature 编译成两份」，与评审白名单不符即失败 |
| `every_control_plane_health_variant_performs_the_real_request` | 任何 `check_control_plane_health` 变体不调 `check_health()` |
| `the_startup_gate_is_compiled_identically_in_every_build` | 启动门禁出现第二份定义、被 feature 门控、或丢掉三项真实探测中的任何一项 |

两张白名单（`REVIEWED_INLINE_FEATURE_BRANCHES`、`REVIEWED_FEATURE_FORKED_FUNCTIONS`）每条都写了
「为什么这条不是产品行为分叉」。集合相等断言意味着**增删都失败**，新分叉必须经过一次自觉决定。

门禁按 release 语义求值 cfg 谓词，因此
`any(not(feature = "desktop-e2e"), feature = "control-plane-e2e")` 被正确判为生产代码而非测试代码。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 测试构建声明启动环境就绪却不探测 | 门禁点名函数与具体标记 | `single_build_path` |
| 测试构建声明 Control Plane 可用却不发请求 | 门禁点名 cfg 谓词 | `single_build_path` |
| 启动门禁出现第二份定义 | 门禁报出定义数 | `single_build_path` |
| 启动门禁丢掉任一项真实探测 | 门禁点名丢掉的探测 | `single_build_path` |
| 运行时依赖解析重新读环境变量 | 门禁点名文件与变量 | `single_build_path` |
| 新增未评审的构建期分叉 | 集合相等断言失败并打印差集 | `single_build_path` |
| `video-studio-e2e` / `control-plane-e2e` / `desktop-e2e` / release 四种构建编不过 | 四条构建命令逐条执行 | 本文件 GREEN 段 |
| staging 树缺任意一份视频资源 | 拒绝并点名，且删除本次写入的全部树 | `test_video_studio_runtime_staging` |
| 必需文件存在但大小为零 | 拒绝并点名（生产解析器正是死在这里） | `test_video_studio_runtime_staging` |
| 重复装配到上一轮残留上 | 先清理再装，不继承陈旧资源 | `test_video_studio_runtime_staging` |
| 验收 App 没有内置浏览器发行物 | 拒绝并点名构建它的脚本 | `test_video_studio_runtime_staging` |
| 验收 App 的资源目录假设与 wdio 配置脱节 | 断言失败并点名实际目录 | `test_video_studio_runtime_staging`（已做变异验证：把常量改成 `target/release` 后该断言确实失败，不是空断言） |

## 真实边界

1. **没有走任何用户路径。** 本次不启动 App、不跑 Playwright / WebdriverIO（任务约束）。证据止于
   「四种构建编得过 + 328 项 Rust 测试通过 + 源码门禁拒绝分叉」。
2. **视频线的 WDIO 验收现在跑不起来，这是本次修复的直接后果，不是回归。** 消除分叉后，
   `video-studio-e2e` 构建与正式包走同一条解析路径，于是它需要一个**真实完整**的运行环境：
   - `target/debug/embedded-browser/`：经 EB-05 逐文件摘要验证的发行物（否则 `authority.resolve()` 失败，
     启动门禁判 `browser_component_missing`，工作台不挂载，全部 spec 失败）；
   - `app_data/local-executor/package/`：签名的执行器包（否则 `executor_unavailable`）；
   - 可达的 Control Plane（否则 `control_plane_unavailable`）；
   - `target/debug/{media-toolchain,motion-video-worker/package,material-video-worker/package}`。

   第四项已提供工具：`run_vf_06_acceptance.stage_video_runtime()` / `require_staged_video_runtime()` /
   `require_staged_embedded_browser()`，配 6 项测试。前三项**没有做**。
3. **`run_bm_08_acceptance.py` 与 `run_im_05_acceptance.py` 未改动。** 它们仍在设置现在没人读的
   `AUTOMATION_TOOL_BM08_*` / `AUTOMATION_TOOL_IM05_WORKER`。**没有半改**是刻意的：
   BM-08 的确定性取消窗口靠 `_write_browser_wrapper` 给浏览器包一层 `sleep 3` 的 shell 包装，
   而生产路径要求浏览器来自经验证的内置发行物——这层包装在单一构建路径下**无法成立**，
   需要另一种制造取消窗口的机制。这是重新设计，不是机械替换，在无法运行验收的前提下改它只会掩盖问题。
4. **`lib.rs` 工作树内含并行会话对 `run_motion_render_job` / `encode_motion_video` 的改动**，本次未触碰，
   也未验证其正确性。
5. **Windows 侧完全未执行。**

## 发现的其他红灯（非本次引入，未修）

| 项 | 事实 |
| --- | --- |
| `cargo build --features video-studio-e2e` 在 HEAD 上编译失败 | 分叉 7。意味着视频线验收当前**根本构建不出 App**。本次顺带修好 |
| `python3 scripts/test_video_studio_acceptance_scope.py` 在 HEAD 上失败 | `wdio.video-studio.conf.ts` 的 specs 含 `plain-language-comprehension.spec.ts`，`run_vf_06_acceptance.SPECS` 只有 3 条，断言集合不等。与本次改动无关 |
| `tests/material_video_gateway.rs:187` 静默跳过 | `frozen_worker_starts_real_web_ui_only_inside_the_task_workspace` 在 `AUTOMATION_TOOL_IM05_WORKER` 缺席时 `eprintln!` + `return`，报告为 ok。属于「全绿但没跑」形态，测试侧夹具，本次未动 |

## 清理

未启动 App、浏览器、Control Plane、数据库或任何常驻服务，无需清理进程。未新增临时文件。
未触碰 `~/Library/Application Support/com.aventador.automationtool/`、`.local/`、
`~/Library/Caches/automation-tool-build/`。构建产物落在 `frontend/src-tauri/target/`（已被 `.gitignore` 覆盖）。

## 文档

- `frontend/src-tauri/src/lib.rs`（消除分叉 1-5、7）
- `frontend/src-tauri/src/material_video_studio.rs`（消除分叉 6）
- `frontend/src-tauri/tests/single_build_path.rs`（新增，7 项）
- `scripts/run_vf_06_acceptance.py`（新增 staging 与前置校验工具，验收流程行为未变）
- `scripts/test_video_studio_runtime_staging.py`（新增，6 项）
- 本文件

## 遗留项

| 项 | 状态 |
| --- | --- |
| 为视频线验收提供真实内置浏览器 / 执行器包 / Control Plane，重跑 BM/IM/VF/CQ 全套 | 未做，本项完成的前提 |
| BM-08 确定性取消窗口在单一构建路径下的替代机制 | 未做，需重新设计 |
| `lib.rs:3819/3822` 执行器包根按 `debug_assertions` 分叉（debug 找 app_data、release 找 Resources） | **未消除。** 与本次同型：没有任何 debug 构建验证过 release 的查找位置 |
| `desktop-e2e` 构建的临时身份与子集命令表 | 未消除，需要 desktop-e2e 也具备生产凭据保险箱 |
| `model_service_settings.rs:473` / `video_editing_service_settings.rs:448` 的 release 更严分支 | 未消除；风险低但 release 分支天然测不到 |
| `frontend/vite.config.ts` 按 mode 替换 HTML 入口 | 未评估。视频线走 `test-production-main.ts` → 真实 `main.tsx`，本次不受影响；其余 mode 未查 |
| `scripts/test_video_studio_acceptance_scope.py` 在 HEAD 上失败 | 未修，非本次引入 |
