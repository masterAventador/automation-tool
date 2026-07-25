# 桌面 E2E 全量实跑记录（2026-07-26）

> 性质：一次性执行记录，不是任务台账，不改任何任务状态。
>
> 触发：2026-07-26 凌晨合入 16 个提交（消除构建期分叉、补视频运行时装配、补设备注册路径、
> 零件区止损、法务页改造等），全部验证止于源码与单测层，**一个桌面 E2E 都没跑过**。
>
> 本次目标是拿到真实结果，不是让它变绿。以下所有"通过/失败"都来自实际执行输出。

## 0. 结论摘要

| 项 | 数 |
| --- | --- |
| `frontend/e2e-tauri/` spec 总数 | 50 |
| WDIO 配置数 | 42 |
| **本次拿到 spec 级真实判定的 spec** | **15** |
| ├ 通过 | 7 |
| └ 失败 | 8 |
| 驱动脚本跑了但 App 没起到 spec 阶段 | 4 |
| 端口竞速假失败，从未构建/启动 | 20 |
| 因驱动脚本仍依赖已废弃路径注入而未执行 | 2（BM-08、IM-05） |
| 因 `bail: 1` 未轮到执行 | 2（video-creation-methods、motion-style-catalog） |
| Windows 专属，macOS 不可执行 | 1 |

通过的 7 个：`workbench`、`diagnostic-export`、`model-service`、`update-download`、
`update-installation`、`update-ui`、`startup-environment`。

失败的 8 个：`browser-settings`、`control-plane`、`executor-lifecycle`、`task-creation`、
`platform-session`、`platform-session-reuse`、`mvp-user-journey`、`video-studio`。

**最重要的结论：桌面 E2E 的主力层（`control-plane-e2e` 那 28 个配置）不是今晚坏的，
而是 2026-07-22 起就已经跑不起来，07-24 又叠加了第二个阻断原因。** 今晚的 16 个提交
只对视频线（`video-studio-e2e`）造成了新的可执行性损失，且那是把原本被短路掩盖的
真实缺失暴露出来，方向正确。

## 1. 用例清单与归属

50 个 spec 由 42 个 WDIO 配置驱动，配置又由 `scripts/run_*_acceptance.py` 或
`frontend/package.json` 的 `test:*` 脚本驱动。全部配置共用同一个二进制
`frontend/src-tauri/target/debug/automation-tool-desktop`，因此**只能串行执行**，
切换 feature 会触发重编译。

按 Cargo feature 归并，42 个配置只对应 **5 种构建**：

| 构建 | feature | 前端入口（`vite.config.ts` 按 mode 换 entry） | 配置数 |
| --- | --- | --- | --- |
| 桌面 UI | `desktop-e2e` | `src/test-tauri-main.tsx`（**桩启动检查**） | 7 |
| 控制面 | `control-plane-e2e` | `src/test-control-plane-main.ts` → 真实 `main.tsx` | 28 |
| 视频线 | `video-studio-e2e` | `src/test-production-main.ts` → 真实 `main.tsx` | 1（5 个 spec） |
| 账号会话 | `desktop-test-driver` | 同上 | 1 |
| 账号管理 | `control-plane-e2e,desktop-test-driver` | 同上 | 1 |

### 1.1 spec → 配置 → 驱动脚本

| spec | 配置 | 驱动 |
| --- | --- | --- |
| workbench | `wdio.conf.ts` | `pnpm test:tauri`（无 Python 驱动） |
| workbench-control | `wdio.workbench.conf.ts` | `run_t3_16_acceptance.py` |
| workbench-metrics | `wdio.workbench-metrics.conf.ts` | `run_h8_14_acceptance.py` |
| control-plane | `wdio.control-plane.conf.ts` | `run_i2_09_acceptance.py` |
| control-plane-recovery | `wdio.control-plane-recovery.conf.ts` | `run_h8_06_acceptance.py` |
| executor-lifecycle | `wdio.executor-lifecycle.conf.ts` | `run_e4_14_acceptance.py` |
| executor-crash-recovery | `wdio.executor-crash-recovery.conf.ts` | `run_h8_05_acceptance.py` |
| app-crash-recovery | `wdio.app-crash-recovery.conf.ts` | `run_h8_04_acceptance.py` |
| network-recovery | `wdio.network-recovery.conf.ts` | `run_h8_07_acceptance.py` |
| system-resume | `wdio.system-resume.conf.ts` | `run_h8_08_acceptance.py` |
| startup-environment | `wdio.startup-environment.conf.ts` | `run_h8_16e_acceptance.py` |
| mvp-user-journey | `wdio.mvp-user-journey.conf.ts` | `run_h8_16f_acceptance.py` |
| diagnostic-export | `wdio.diagnostic-export.conf.ts` | `run_h8_13_acceptance.py` |
| browser-settings | `wdio.browser-settings.conf.ts` | `run_b5_04_acceptance.py` |
| platform-session | `wdio.platform-session.conf.ts` | `run_b5_13_acceptance.py` |
| platform-session-reuse | `wdio.platform-session-reuse.conf.ts` | `run_b5_15_acceptance.py` |
| default-profile-isolation | `wdio.default-profile-isolation.conf.ts` | `run_b5_16_acceptance.py` |
| installation-revocation | `wdio.installation-revocation.conf.ts` | `run_i2_14_acceptance.py` |
| account-management | `wdio.account-management.conf.ts` | `run_u9_06_acceptance.py` |
| account-session | `wdio.account-session.conf.ts` | `pnpm test:account-session-tauri`（无 Python 驱动） |
| publishing | `wdio.publishing.conf.ts` | `pnpm test:publishing-tauri`（无 Python 驱动） |
| model-service | `wdio.model-service.conf.ts` | `run_vf_05_acceptance.py` |
| task-creation | `wdio.task-creation.conf.ts` | `run_t3_06_acceptance.py` |
| task-query | `wdio.task-query.conf.ts` | `run_t3_07_acceptance.py` |
| task-event-stream | `wdio.task-event-stream.conf.ts` | `run_t3_12_acceptance.py` |
| task-control | `wdio.task-control.conf.ts` | `run_t3_13` / `run_h8_01` |
| task-termination | `wdio.task-termination.conf.ts` | `run_t3_14` / `run_h8_02` |
| task-projection | `wdio.task-projection.conf.ts` | `run_t3_15_acceptance.py` |
| task-create-form | `wdio.task-create-form.conf.ts` | `run_t3_17_acceptance.py` |
| task-run | `wdio.task-run.conf.ts` | `run_t3_18` / `run_h8_03` |
| task-lifecycle | `wdio.task-lifecycle.conf.ts` | `run_t3_19_acceptance.py` |
| task-restart | `wdio.task-restart.conf.ts` | `run_t3_20_acceptance.py` |
| task-discovery | `wdio.task-discovery.conf.ts` | `run_d6_10_acceptance.py` |
| task-target-preview | `wdio.task-target-preview.conf.ts` | `run_d6_11_acceptance.py` |
| task-target-preview-ui | `wdio.task-target-preview-ui.conf.ts` | `run_d6_12_acceptance.py` |
| update-policy | `wdio.update-policy.conf.ts` | `pnpm test:h8-19-app`（无 Python 驱动） |
| update-download | `wdio.update-download.conf.ts` | `run_h8_20_acceptance.py` |
| update-installation | `wdio.update-installation.conf.ts` | `run_h8_21_acceptance.py` |
| update-ui | `wdio.update-ui.conf.ts` | `run_h8_21` / `run_h8_22` |
| update-macos-package | `wdio.update-macos-package.conf.ts` | `run_h8_22_macos_package_acceptance.py` |
| update-windows-package | `wdio.update-windows-package.conf.ts` | `run_h8_22_windows_package_acceptance.py` |
| video-studio / video-creation-methods / motion-style-catalog | `wdio.video-studio.conf.ts` | `run_vf_06_acceptance.py` |
| plain-language-comprehension | 同上 | `run_cq_01_acceptance.py` |
| material-video-webui | 同上 | `run_im_05_acceptance.py` |
| motion-parts-catalog | 同上（`--spec` 覆盖） | `run_bm_15_acceptance.py` |
| motion-video-native | 同上（`--spec` 覆盖） | `run_bm_08_acceptance.py` |
| video-editing | 同上（`--spec` 覆盖） | `run_ve_03_acceptance.py` |
| video-editing-service | 同上（`--spec` 覆盖） | `run_ve_04_acceptance.py` |

注：`wdio.video-studio.conf.ts` 的 `specs` 列 5 条，但 7 个脚本各用 `--spec` 选取自己的子集；
`motion-parts-catalog`、`motion-video-native`、`video-editing`、`video-editing-service`
这 4 个 spec 不在配置的 `specs` 里，只能由脚本 `--spec` 拉起。

## 2. 分批执行结果

### 2.1 Batch A — `desktop-e2e` 自足脚本（7 个，5 过 2 失）

| 脚本 | spec | 结果 |
| --- | --- | --- |
| `run_h8_13_acceptance.py` | diagnostic-export | ✅ 通过 |
| `run_vf_05_acceptance.py` | model-service | ✅ 通过 |
| `run_h8_20_acceptance.py` | update-download | ✅ 通过 |
| `run_h8_21_acceptance.py` | update-installation + update-ui | ✅ 通过 |
| `run_h8_22_acceptance.py` | update-ui | ✅ 通过 |
| `run_b5_04_acceptance.py` | browser-settings | ❌ 失败 |
| `run_h8_22_macos_package_acceptance.py` | update-macos-package | ❌ 失败 |

另外单独执行 `wdio.conf.ts`（`workbench.spec.ts`）通过：

```text
[webkit 605.1.15 macos #0-0] desktop workbench
[webkit 605.1.15 macos #0-0]    ✓ opens the no-login workbench in the real Tauri main window
[webkit 605.1.15 macos #0-0] 1 passing (35ms)
Spec Files:	 1 passed, 1 total (100% completed) in 00:00:00
```

（该配置在临时 `HOME` 下执行，理由见第 6 节。）

### 2.2 Batch B — `control-plane-e2e`（32 个脚本，1 过 31 失）

只有 `run_h8_16e_acceptance.py`（startup-environment）通过，而它通过的原因值得单独记一笔——
该 spec 的断言本身就是"门禁应当挡住"：

```text
✓ keeps the workbench blocked when the embedded browser is missing
```

它是全仓唯一一个按 EB-08 之后的世界写的桌面 spec：既自带编译期动作信任配置，
又直接断言被阻断的诊断页。其余 31 个中：

- **5 个真正跑到了 spec** 并失败；
- **26 个连 App 都没起来**（其中 20 个是端口竞速造成的假失败，见 2.3）。

### 2.3 端口竞速导致的假失败（必须排除，不计入产品结论）

`run_t3_*`、`run_d6_*`、`run_h8_0*`、`run_i2_*`、`run_u9_06` 共 23 个脚本把
Control Plane 端口**硬编码为 8765**，且启动前 `require_control_plane_port_available()` 会硬失败。
串行连跑时上一个脚本的 Control Plane 尚未释放端口，后一个立刻报：

```text
OSError: [Errno 48] Address already in use
RuntimeError: H8-05 requires an unused Control Plane port
```

20 个脚本以这种方式在 1 秒内失败，**从未构建、从未启动 App**。这是执行编排问题，不是产品缺陷，
但它说明这批脚本无法在同一台机器上连续跑，需要脚本间等待端口释放（本次用
`waitport.py 8765` 轮询解决）。

### 2.4 Batch C — 视频线（`video-studio-e2e`）

见第 4 节。

## 3. 失败逐条定位

### 3.1 startup gate 阻断（Batch B 主因，**非今晚引入**）

跑到 spec 的 5 个控制面用例，失败输出完全一致：

```text
Expect $(`h2`) to have text
Expected: "RPA 运营工作台"
Received: "桌面运行环境需要处理"
```

涉及：`control-plane`（i2_09）、`executor-lifecycle`（e4_14）、`task-creation`（t3_06）、
`platform-session`（b5_13）、`platform-session-reuse`（b5_15）。
`default-profile-isolation`（b5_16）与 `installation-revocation`（i2_14）则表现为
"hidden App exited before ready / registration"，同一病根的另一种表现。

用一次性探针 spec 打印被阻断页面的实际文案（探针已删除），拿到 A/B 证据：

**未装内置浏览器时（3 条诊断）：**

```text
桌面运行环境需要处理业务功能保持关闭，处理下面的本机环境问题后重新检查。
重新检查 打开本地修复工具
控制服务不可用 请检查本地服务或网络；诊断不会显示连接凭据或底层异常。
本地执行器动作配置缺失 当前安装包没有完整的动作信任配置，请安装由管理员正式配置的版本。
浏览器组件缺失 当前安装不完整，请重新安装官方客户端；无需也不要单独安装其他浏览器。
```

**装好内置浏览器后（只剩 1 条）：**

```text
桌面运行环境需要处理业务功能保持关闭，处理下面的本机环境问题后重新检查。
重新检查 打开本地修复工具
本地执行器动作配置缺失 当前安装包没有完整的动作信任配置，请安装由管理员正式配置的版本。
```

两个阻断原因、两个引入时间，**都早于今晚**：

| 诊断 | 来源 | 引入提交 | 日期 |
| --- | --- | --- | --- |
| `executor_configuration_required` | `option_env!("AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY")` 等三个**编译期**变量缺失 → `ExecutorActionRuntimeInput::from_compile_time_configuration()` 返回 `Ok(None)` | `199a021 feat: 完成启动环境诊断闭环` | 2026-07-22 |
| `browser_component_missing` | `EmbeddedBrowserAuthority::resolve()` 在资源目录找不到经校验的内置发行物 | `6025ecf feat(embedded-browser): EB-08 启动健康状态迁移为内置组件语义` | 2026-07-24 |

全仓只有 4 个脚本提供编译期动作信任配置：`run_h8_16e`、`run_h8_16f`、`run_p9_03`、`run_p9_04`。
`run_t3_*` / `run_d6_*` / `run_i2_*` / `run_b5_1*` / `run_h8_0*` **一个都没有**，
也没有一个脚本装配内置浏览器发行物。

结论：**控制面桌面 E2E 这一层自 2026-07-22 起就整体跑不起来，07-24 起多了第二道阻断。**
今晚 16 个提交对 `frontend/src/app/startup.ts` 的唯一改动是新增 `installation_conflict`
诊断分支（把 `controlPlaneDiagnostic()` 抽成函数），不改变阻断条件。

### 3.2 spec 断言过期于已删除的 UI（**非今晚引入**）

`run_b5_04_acceptance.py`（browser-settings）：

```text
Expect $(`.browser-settings-card`) to be displayed
Expected: "displayed"
Received: "not displayed"
    at openSettings (frontend/e2e-tauri/browser-settings.spec.ts:10:59)
```

`.browser-settings-card` 现在**只存在于 `src/styles/global.css`**，没有任何组件渲染它。
B5-04 整条用例的前提（在设置页选择受信任的系统浏览器 Google Chrome / Microsoft Edge）
已被 `f34e503 refactor(embedded-browser): EB-10 移除生产浏览器选择链路`（2026-07-24）删除，
且这是产品规则要求的删除（CLAUDE.md 第 5 节：不发现、选择、下载或回退到系统浏览器）。

`run_h8_16f_acceptance.py`（mvp-user-journey）同源：spec 的 `repairTrustedBrowser()`
仍要点 `button=保存浏览器选择` 并期待点完就进工作台。

仍然引用已删除浏览器选择 UI 的 spec 共 3 个：
`browser-settings.spec.ts`、`mvp-user-journey.spec.ts`、`startup-environment.spec.ts`。

**本次没有修改这三个 spec 的断言。** 它们不是"文案过期"，是整段用户路径被产品删除，
应当由所属任务决定是重写还是退役，不是本次执行记录能替代的决定。

### 3.3 打包 App 启动即崩溃（**待定位，可能与今晚有关**）

`run_h8_22_macos_package_acceptance.py`：

```text
SevereServiceError: Failed to start embedded WebDriver for instance 0:
Tauri app exited before the embedded WebDriver server became ready
(code=null, signal=SIGABRT). The app likely crashed during startup.
```

DMG 构建、挂载、签名校验全部通过，**打出来的 `.app` 一启动就 SIGABRT**。
该脚本在 `finally` 里清掉了 bundle，本次未能保留现场再复现，因此**未定位到根因**。
它与 Batch A 其余 5 个 `desktop-e2e` 用例的差别是：只有它跑的是 `--bundles app,dmg`
的真实打包产物，其余跑的是 `--no-bundle` 裸二进制。今晚的
`93a6acf feat(registration): 正式构建补上本机设备注册路径` 恰好只在正式装配路径生效，
是首要嫌疑，但**没有证据，不下结论**。

### 3.4 `test_video_studio_acceptance_scope.py` 断言集合不等（**非今晚引入**）

```text
AssertionError: VF-06 desktop acceptance must keep covering every non-IM-05 spec
from the wdio config;
expected [video-studio, video-creation-methods, motion-style-catalog, plain-language-comprehension],
got [video-studio, video-creation-methods, motion-style-catalog]
```

`plain-language-comprehension.spec.ts` 在 `wdio.video-studio.conf.ts` 的 `specs` 里，
但由 `run_cq_01_acceptance.py` 单独负责，不在 `run_vf_06_acceptance.SPECS` 中。
是这条门禁的期望写错了（它只豁免了 IM-05，没豁免 CQ-01），不是覆盖真的缺了。

## 4. 视频线：今晚唯一的真实可执行性损失

`FIX-startup-gate-build-fork.md` 的「真实边界 2」已经预告了这一点，本次实测确认。

`video-studio-e2e` 构建在今晚之前编译 `check_local_startup_environment` 的**桩实现**，
无条件返回 AppData / Executor / EmbeddedBrowser 三项全部 Ready，启动门禁被整个短路。
`docs/development/VF-06.md`（2026-07-23）记录的"生产 App 正常左侧入口：1 项桌面验收通过"
就是在这个短路下取得的。

今晚 `780abce fix(build): 消除测试构建对生产行为路径的七处分叉` 删掉了这个桩，
于是视频线需要和正式包一样的四项真实前置。本次已按 `run_vf_06_acceptance.py` 自带的
staging 工具补齐其中四项资源：

```text
staged media-toolchain       -> target/debug/media-toolchain
staged motion-video-worker   -> target/debug/motion-video-worker/package
staged material-video-worker -> target/debug/material-video-worker/package
browser                      -> target/debug/embedded-browser
```

（三份视频资源来自 `scripts/prepare_video_runtime.py` 的缓存；内置浏览器发行物为
`.local/eb-16/run/build/embedded-browser` 的只读副本，331 文件 / macos-arm64 / `fail_closed`。）

### 4.1 VF-06 实跑结果：四项资源装好后仍然阻断

```text
Expect $(`h2`) to have text
Expected: "RPA 运营工作台"
Received: "桌面运行环境需要处理"
[webkit 605.1.15 macos #0-0] 1 failing (30.1s)
Spec Files:	 0 passed, 1 failed, 3 total (33% completed) in 00:00:31
```

配置里 `bail: 1`，所以 `video-creation-methods` 与 `motion-style-catalog` 没轮到执行。

对该构建打探针，剩余诊断精确为两条（浏览器组件缺失已消失，证明资源装配确实生效）：

```text
桌面运行环境需要处理业务功能保持关闭，处理下面的本机环境问题后重新检查。
重新检查 打开本地修复工具
控制服务不可用 请检查本地服务或网络；诊断不会显示连接凭据或底层异常。
本地执行器动作配置缺失 当前安装包没有完整的动作信任配置，请安装由管理员正式配置的版本。
```

**据此修正 `FIX-startup-gate-build-fork.md` 对四项前置的描述。** 它列的第二项是
"`app_data/local-executor/package/`：签名的执行器包"，但实测挡住的不是执行器包，
而是**编译期**的动作信任配置三元组
（`AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY` /
`AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS` /
`AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT`）——
`startup_environment_state()` 第一步就是
`ExecutorActionRuntimeInput::from_compile_time_configuration()`，
返回 `Ok(None)` 时直接 `ConfigurationRequired`，**根本不会走到校验已安装包那一步**。
也就是说：装执行器包不够，`pnpm build:tauri:video-studio-test` 必须带着这三个环境变量编译。

所以 VF-06 要跑起来，实际还差三件事：编译期动作信任配置、可达的 Control Plane、
以及（在前两项满足后才会暴露的）签名执行器包。本次没有替它补这套 harness——
那是新建验收基础设施，属于 VF-06 自己的任务范围，不是一次执行记录该顺手发明的东西。

## 5. 未执行及原因

| 用例 | 原因 |
| --- | --- |
| `update-windows-package` | Windows 专属，本机是 macOS |
| `motion-video-native`（BM-08） | 驱动脚本仍在设置今晚已废弃的 `AUTOMATION_TOOL_BM08_*` 路径注入变量；其确定性取消窗口依赖给浏览器包一层 `sleep 3` 的 shell 包装，在单一构建路径下无法成立，需要重新设计取消窗口机制 |
| `material-video-webui`（IM-05） | 同上，仍在设置已无人读取的 `AUTOMATION_TOOL_IM05_WORKER` |
| 任何需要真实抖音账号的最终状态验证 | 本次不触碰真实平台。没有任何 spec 引用 `douyin.com`，因此这一条实际未构成阻塞，但真实平台最终状态验收仍未做 |

## 6. 安全与清理

- **真实登录态未被触碰。** `~/Library/Application Support/com.aventador.automationtool`
  在开跑前已整目录备份（64 MB），`embedded-browser-profiles/douyin/df1c89f0-…` 与
  `current-douyin-profile-v1` 完好。
- **`tauri.test.conf.json` 没有 `identifier`**，会继承 `tauri.conf.json` 的真实
  `com.aventador.automationtool`，即 `pnpm test:tauri` 默认写真实 App 数据目录。
  本次该配置在临时 `HOME` 下执行以隔离。**这是一个应当修掉的隐患**：其余 41 个
  e2e 配置都有独立 identifier 且 `visible=false`，只有它没有。
- `run_p9_03` / `run_p9_06` / `run_p9_07` 硬编码真实 `APP_IDENTIFIER`，本次**没有执行**。
- `.local/eb-16/` 全程只读，未写入。
- 端口：Control Plane 8765 与 PostgreSQL 5433 开跑前确认空闲；
  未终止、未接管任何来源不明的进程（本机另有 `agent-platform-dev-*` 占用 5432，全程未动）。
- Docker：脚本使用 `automation-tool-local` 专属 compose project name 与
  各任务独立的库名/用户名（如 `automation_tool_t306`）。
- 进程：跑完复查无本次遗留的 `automation-tool-desktop` / wdio / tauri-driver 进程；
  Docker 无 `automation-tool` 容器残留；8765 与 5433 均已释放。
- 临时探针 spec `frontend/e2e-tauri/_tmp-diagnostics.spec.ts` 已删除，
  `e2e-tauri/` 回到 50 个文件。
- 本次装进 `target/debug/` 的内置浏览器与三份视频资源**已全部移除**，
  避免留下一个"看不见但会改变启动门禁结果"的本机状态——这正是本文件所描述问题的成因。
  需要复现第 4 节时按该节命令重新装配即可。
- 本次创建的隔离 App 数据目录（`i209acceptance` / `t306acceptance` / `vf06acceptance`）已删除；
  早于本次的 `pb07acceptance`（07-25 22:06）与 `u904acceptance`（07-23）未动。
- 未 `git add`、未 `commit`、未修改任何产品代码或 spec 断言。
  工作树内 `frontend/src/styles/global.css` 等三处改动属于并行会话，本次未触碰。

## 7. 对"今晚哪些改动破坏了哪些用例"的判断

| 判断 | 依据 |
| --- | --- |
| **控制面 E2E 整层失败与今晚无关** | 两个阻断诊断分别引入于 07-22（`199a021`）和 07-24（`6025ecf`）；今晚对 `startup.ts` 的改动只新增 `installation_conflict` 分支 |
| **B5-04 / H8-16F 的 UI 断言过期与今晚无关** | 浏览器选择链路删除于 07-24 `f34e503`（EB-10） |
| **视频线失去可执行环境确实是今晚造成的** | `780abce` 删除 `video-studio-e2e` 的启动门禁桩；这是把长期被掩盖的资源缺失暴露出来，方向正确，代价是验收需要补齐四项真实前置 |
| **打包 App SIGABRT 尚未定位** | 现场已被脚本清理，未复现；`93a6acf` 只在正式装配路径生效，是嫌疑但无证据 |
| **今晚没有让任何原本能跑通的控制面用例变得不能跑** | 那 28 个配置在今晚之前就已经被启动门禁挡住 |

真正的系统性问题不是今晚的 16 个提交，而是：**桌面 E2E 这一层在 07-22 之后就没有人整体跑过，
27 份任务台账没有一份记录过自己所在的构建把启动门禁关掉了或绕过了。**
