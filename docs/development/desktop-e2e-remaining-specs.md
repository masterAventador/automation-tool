# 桌面 E2E：control-plane 层之外的 21 个 spec 清点与实跑

> 性质：一次性执行记录，不是任务台账，不改任何任务状态、不改任何断言、不改任何产品代码。
>
> 日期：2026-07-26
>
> 触发：`docs/development/FIX-control-plane-e2e-prerequisites.md` 与
> `FIX-control-plane-e2e-remaining-failures.md` 把 `control-plane-e2e` 那一层的 32 个驱动
> 跑起来了（现状 24 过 / 8 败）。**那一层只覆盖 50 个 spec 里的 29 个**，其余 21 个
> 从来没有被这两轮碰过。本文件是这 21 个的清点与实跑。
>
> 并行边界：全程另有一个会话在同一台机器上修 control-plane 层剩余失败
> （T3-15/16/18、D6-10、H8-16F）。本次未触碰这些驱动、未触碰 `mvp-user-journey.spec.ts`。

## 0. 结论摘要

| 项 | 数 |
| --- | --- |
| `frontend/e2e-tauri/` spec 总数 | 50 |
| control-plane 层 32 个驱动覆盖的 spec | 29 |
| **本次范围：其余 spec** | **21** |
| ├ 实跑通过 | **9** |
| ├ 实跑失败（spec 内断言） | **2** |
| ├ 实跑失败（启动门禁挡住，工作台未挂载） | **3** |
| ├ 未执行：`bail: 1`，同一门禁未轮到 | 2 |
| ├ 未执行：驱动自身前置失败，没走到 spec | 2 |
| ├ 未执行：驱动仍依赖已废弃注入 | 2 |
| └ 未执行：Windows 专属 | 1 |

**通过（9）**：`workbench`、`diagnostic-export`、`model-service`、`update-download`、
`update-installation`、`update-ui`、`update-policy`、`account-session`、`publishing`。

**失败（2）**：`browser-settings`（EB-10 删除的 UI，同 2026-07-26 记录）、
`update-macos-package`（打包 App 启动即 SIGABRT，本次拿到崩溃栈，见 §4）。

**两条与既有文档不一致、本次实测更正**：

1. `publishing` **能跑，而且全过**。`FIX-control-plane-e2e-prerequisites.md` 遗留项写它
   「缺 Python 驱动，无法在本层跑起来」——实际上它的 spec 明确不走工作台，只经 IPC 调
   Rust 发布 Command，`pnpm test:publishing-tauri` 单独就能跑完 5 条断言。
2. `tauri.test.conf.json` 已经有独立 identifier（`…uiharness`），
   `desktop-e2e-run-20260726.md` §6 记的「它会写真实 App 数据目录」这个隐患已被修掉。

## 1. 归属清点：50 = 29 + 21

`control-plane-e2e` 层的 32 个驱动（`desktop_e2e_prerequisites.control_plane_e2e_drivers()`
从 `frontend/package.json` 派生）对应 **29 个 wdio 配置 / 29 个 spec**——32 与 29 的差是
三个配置各有两个驱动（`task-control` = T3-13+H8-01，`task-run` = T3-18+H8-03，
`task-termination` = T3-14+H8-02）。

剩下的 21 个 spec 及其归属：

| # | spec | wdio 配置 | Cargo feature | 驱动 | 本机可跑 |
| --- | --- | --- | --- | --- | --- |
| 1 | workbench | `wdio.conf.ts` | `desktop-e2e` | `pnpm test:tauri`（无 Python 驱动） | 是 |
| 2 | diagnostic-export | `wdio.diagnostic-export.conf.ts` | `desktop-e2e` | `run_h8_13_acceptance.py` | 是 |
| 3 | model-service | `wdio.model-service.conf.ts` | `desktop-e2e` | `run_vf_05_acceptance.py` | 是 |
| 4 | update-download | `wdio.update-download.conf.ts` | `desktop-e2e` | `run_h8_20_acceptance.py` | 是 |
| 5 | update-installation | `wdio.update-installation.conf.ts` | `desktop-e2e` | `run_h8_21_acceptance.py` | 是 |
| 6 | update-ui | `wdio.update-ui.conf.ts` | `desktop-e2e` | `run_h8_21` / `run_h8_22` | 是 |
| 7 | update-policy | `wdio.update-policy.conf.ts` | `desktop-e2e` | `pnpm test:h8-19-app`（无 Python 驱动） | 是 |
| 8 | browser-settings | `wdio.browser-settings.conf.ts` | `desktop-e2e` | `run_b5_04_acceptance.py` | 是 |
| 9 | update-macos-package | `wdio.update-macos-package.conf.ts` | `desktop-e2e` + 真实 `.app`/`.dmg` | `run_h8_22_macos_package_acceptance.py` | 是 |
| 10 | account-session | `wdio.account-session.conf.ts` | `desktop-test-driver` | `pnpm test:account-session-tauri`（无 Python 驱动） | 是 |
| 11 | publishing | `wdio.publishing.conf.ts` | `control-plane-e2e` | 无（`pnpm test:publishing-tauri`） | 是 |
| 12 | update-windows-package | `wdio.update-windows-package.conf.ts` | — | `run_h8_22_windows_package_acceptance.py` | **否（Windows 专属）** |
| 13 | video-studio | `wdio.video-studio.conf.ts` | `video-studio-e2e` | `run_vf_06_acceptance.py` | 需四项前置 |
| 14 | video-creation-methods | 同上 | `video-studio-e2e` | `run_vf_06_acceptance.py` | 需四项前置 |
| 15 | motion-style-catalog | 同上 | `video-studio-e2e` | `run_vf_06_acceptance.py` | 需四项前置 |
| 16 | plain-language-comprehension | 同上 | `video-studio-e2e` | `run_cq_01_acceptance.py` | 需四项前置 |
| 17 | material-video-webui | 同上 | `video-studio-e2e` | `run_im_05_acceptance.py` | 驱动废弃注入 |
| 18 | motion-parts-catalog | 同上（`--spec` 覆盖） | `video-studio-e2e` | `run_bm_15_acceptance.py` | 需四项前置 |
| 19 | motion-video-native | 同上（`--spec` 覆盖） | `video-studio-e2e` | `run_bm_08_acceptance.py` | 驱动废弃注入 |
| 20 | video-editing | 同上（`--spec` 覆盖） | `video-studio-e2e` | `run_ve_03_acceptance.py` | 需四项前置 |
| 21 | video-editing-service | 同上（`--spec` 覆盖） | `video-studio-e2e` | `run_ve_04_acceptance.py` | 需四项前置 |

第 18–21 这 4 个 spec 不在 `wdio.video-studio.conf.ts` 的 `specs` 列表里，只能由驱动
`--spec` 拉起，因此**从来没有被任何一次「跑全部配置」的动作覆盖过**。本次是其中
`video-editing`、`motion-parts-catalog`、`video-editing-service` 三个的**首次真实执行**。

## 2. 执行编排

42 个配置共用同一个 `frontend/src-tauri/target/debug/automation-tool-desktop`，切 feature
就整体重编译，因此只能串行；本次按 feature 分批以减少重编译：

```text
批 D1  desktop-e2e        6 个 Python 驱动
批 D2  desktop-e2e ×3 → desktop-test-driver → control-plane-e2e
批 D3  video-studio-e2e   4 个驱动
```

每一批开跑前和每个驱动开跑前都等 `pgrep -f "wdio run"` 与 `pgrep -f "tauri build"` 为空，
避免和并行会话抢同一个二进制；从不终止、从不接管任何进程。

## 3. 逐条实跑结果

### 3.1 通过（9）

| spec | 驱动 | 输出 |
| --- | --- | --- |
| diagnostic-export | `run_h8_13_acceptance.py` | `✓ exports only after the user confirms in 设置与诊断` |
| model-service | `run_vf_05_acceptance.py` | 1 passing |
| update-download | `run_h8_20_acceptance.py` | 1 passing |
| update-installation + update-ui | `run_h8_21_acceptance.py` | 通过 |
| update-ui | `run_h8_22_acceptance.py` | 通过 |
| workbench | `pnpm test:tauri` | `✓ opens the no-login workbench in the real Tauri main window` |
| update-policy | `pnpm test:h8-19-app` | `✓ initializes the production policy service in isolated AppData` |
| account-session | `pnpm test:account-session-tauri` | `✓ keeps the workbench unmounted and returns only a safe native snapshot` |
| publishing | `pnpm test:publishing-tauri` | **5 passing** |

`publishing` 的 5 条：读真实桥的平台可用性、拒绝不可用平台的发布、拒绝产品不支持的平台、
拒绝没人在等的审批、不向操作者泄漏平台实现机制。

**关于 `publishing` 更正既有文档**：`FIX-control-plane-e2e-prerequisites.md` 的遗留项写
「缺 Python 驱动来起 Control Plane / PostgreSQL / bootstrap，因此跑不起来」。
实测不成立——该 spec 的文件头注释本来就写明它**不走工作台**：

```text
Not covered here: clicking through to the page in the real App. This debug build
stops at the startup environment gate ("桌面运行环境需要处理") ... The navigation
path is covered by the Playwright UI Harness instead.
```

它全程只经 IPC 调 Rust 发布 Command，不需要 Control Plane，也不需要工作台挂载，
`pnpm test:publishing-tauri` 单独就跑完了。它不需要一个新驱动。

### 3.2 失败：spec 内断言（2）

**`browser-settings`（`run_b5_04_acceptance.py`）** — 与 2026-07-26 完全一致，未变化：

```text
Error: Expect $(`.browser-settings-card`) to be displayed
Expected: "displayed"   Received: "not displayed"
    at openSettings (frontend/e2e-tauri/browser-settings.spec.ts:10:59)
1 failing (30.1s)
```

整段用户路径（在设置页选择受信任的系统浏览器）已由 EB-10 `f34e503` 删除，且那是
CLAUDE.md 第 5 节要求的删除。属退役/重写决定，**本次未改断言**。

**`update-macos-package`（`run_h8_22_macos_package_acceptance.py`）** — 见下一节，本次定位到根因。

### 3.3 失败：启动门禁挡住工作台（3）

| spec | 驱动 | 输出 |
| --- | --- | --- |
| video-studio | `run_vf_06_acceptance.py` | `Expected: "RPA 运营工作台"` / `Received: "桌面运行环境需要处理"` |
| video-editing | `run_ve_03_acceptance.py` | `Can't call click on element with selector "//li[…'工作台']" because element wasn't found` |
| motion-parts-catalog | `run_bm_15_acceptance.py` | 同上 |

`video-editing` 与 `motion-parts-catalog` 是**首次真实执行**（它们不在任何配置的 `specs` 里，
只能由驱动 `--spec` 拉起，历次「跑全部配置」都跳过了它们）。两条都停在同一处：工作台没挂载，
左侧导航根本不存在。

`video-studio` 的 `bail: 1` 使 `video-creation-methods` 与 `motion-style-catalog` 未轮到执行
（`Spec Files: 0 passed, 1 failed, 3 total (33% completed)`）。

原因与 `FIX-startup-gate-build-fork.md` / `FIX-control-plane-e2e-prerequisites.md` 记的一致：
**9 个视频线 spec 的 7 个驱动没有一个接 `scripts/desktop_e2e_prerequisites.py`**，实测确认：

```text
$ grep -l "startup_gate_environment\|prepare_startup_gate" scripts/run_{vf_06,ve_03,ve_04,bm_15,bm_08,cq_01,im_05}_acceptance.py
（无匹配）
```

它们缺的是同一套东西——编译期动作信任配置三元组、可达的 Control Plane、签名执行器包、
内置浏览器发行物。`control-plane-e2e` 那一层已经把这四项收敛进 `desktop_e2e_prerequisites`，
**视频线复用它是现成的路**，但那是给这 7 个驱动接线，属 VF/VE/BM/IM/CQ 各自任务范围，
不是一次执行记录该顺手发明的东西。本次没有替它们接。

### 3.4 未执行：驱动自身前置失败，没走到 spec（2）

**`plain-language-comprehension`（`run_cq_01_acceptance.py`）** —— 死在一个与 E2E 无关的门禁上：

```text
AssertionError: clean synthetic tree: expected pass, got user-facing branding check failed:
configured scan root does not exist: frontend/src-tauri/src
    scripts/test_user_facing_branding.py:116
```

这是**既有缺陷，与本次和 E2E 都无关**，单独跑也一样：

```text
$ backend/.venv/bin/python scripts/test_user_facing_branding.py
AssertionError: clean synthetic tree: expected pass, got …
```

原因：`contracts/quality/user-facing-terminology.v1.json:231` 的 `staticScan.roots` 在
`405db35`（CQ-02 扩到 Rust 与后端）加进了 `frontend/src-tauri/src`，而
`test_user_facing_branding.py:63` 的 `base_contract()` 只把 roots 覆盖成
`["frontend/index.html", "frontend/src"]`，合成树里 `frontend/src-tauri/` 下只造了
`tauri.conf.json`、没有 `src/`，于是检查器对**自己的合成树**报「扫描根不存在」。
修它属 CQ-01/CQ-02 范围，本次未修。

**`video-editing-service`（`run_ve_04_acceptance.py`）** —— 死在真实对象存储前置：

```text
real OSS: bucket location matches oss-cn-beijing
real OSS: production staging plan produced deduplicated contract keys
VE-04 staging prefix does not contain exactly the two test objects
```

Rust 侧 7 项设置用例全过（`test result: ok. 7 passed`），随后驱动去真实 OSS 校验暂存前缀，
对象数不符即失败，spec 未执行。**这次执行对真实对象存储发起了读请求**（驱动自带凭据，
非本次引入）；未写入、未删除任何对象。

### 3.5 未执行：驱动仍依赖已废弃注入（2）

`material-video-webui`（IM-05）与 `motion-video-native`（BM-08）。实测确认这两个驱动仍在设置
`AUTOMATION_TOOL_IM05_WORKER` / `AUTOMATION_TOOL_BM08_*`，而 `frontend/src` 与
`frontend/src-tauri/src` 里**没有任何读者**：

```text
$ grep -l "AUTOMATION_TOOL_BM08_\|AUTOMATION_TOOL_IM05_WORKER" scripts/run_*.py
scripts/run_bm_08_acceptance.py
scripts/run_im_05_acceptance.py
$ grep -rn "AUTOMATION_TOOL_BM08_\|AUTOMATION_TOOL_IM05_WORKER" frontend/src frontend/src-tauri/src
（无匹配）
```

与 2026-07-26 的判断一致，未变化。跑它们只会得到一个由过期注入造成的假失败，本次未跑。

### 3.6 未执行：Windows 专属（1）

`update-windows-package`。本机是 macOS。

## 4. `update-macos-package` 的 SIGABRT：本次定位到根因

`desktop-e2e-run-20260726.md` §3.3 记录了这条失败但「现场已被脚本清理，未复现，未定位」。
本次复现并定位。

### 现象

```text
SevereServiceError: Failed to start embedded WebDriver for instance 0:
Tauri app exited before the embedded WebDriver server became ready
(code=null, signal=SIGABRT). The app likely crashed during startup.
```

### 定位过程

1. **崩溃报告**（`~/Library/Logs/DiagnosticReports/automation-tool-desktop-2026-07-26-094953.ips`）
   给出触发线程栈——不是原生崩溃，是 **Rust panic 在一个不能 unwind 的 ObjC 回调里**：

   ```text
   abort
   std::panicking::panic_with_hook
   core::panicking::panic_cannot_unwind
   tao::platform_impl::platform::app_delegate::did_finish_launching
   -[NSApplication _sendFinishLaunchingNotification]
   ```

2. 驱动在 `finally` 里删 bundle，所以**开跑同时轮询 `target/debug/bundle/macos/`，
   在删除前把 `.app` 整份拷进临时目录**，再直接运行它拿 stderr：

   ```text
   thread 'main' panicked at tauri-2.11.5/src/app.rs:1425:11:
   Failed to setup app: error encountered during setup hook: unknown path
   ```

3. `unknown path` 是 `tauri::Error::UnknownPath`。检查 bundle 内容：

   ```text
   Automation Tool H822 Mac Acceptance.app/Contents/
     ├── Info.plist
     ├── MacOS/automation-tool-desktop
     └── _CodeSignature/CodeResources
   ```

   **`Contents/Resources` 整个不存在。**

4. 决定性实验（在**拷贝**上做，不动产品代码）：建一个空的 `Contents/Resources`，
   重新 ad-hoc 签名，跑同一个二进制：

   ```text
   $ mkdir -p "…/Contents/Resources" && codesign --force --deep --sign - "…app"
   $ "…/Contents/MacOS/automation-tool-desktop"
   [WDIO-FRONTEND][INFO] [WDIO][Frontend] Tauri ready - setting up backend-log listener
   → 进程正常存活（4 秒后仍在运行），setup 成功
   ```

### 结论

`frontend/src-tauri/src/lib.rs:3842-3848` 的 setup hook：

```rust
app.manage(embedded_browser_authority::EmbeddedBrowserAuthority::new(
    app.path().resource_dir()
        .map_err(|error| Box::new(error) as Box<dyn std::error::Error>)?,
    …
));
```

`resource_dir()` 在 macOS 上要求 `Contents/Resources` **真实存在**（它做 canonicalize），
而 H8-22 的验收 App 不声明任何 `bundle.resources`，Tauri 就不建这个目录。于是：

```text
resource_dir() → Err(UnknownPath) → `?` → setup hook 报错
→ Tauri panic → panic 发生在 extern "C" ObjC 回调中，不能 unwind → abort (SIGABRT)
```

`--no-bundle` 的调试构建里 `resource_dir()` 返回 `target/debug`（存在），所以其余 41 个配置
全都碰不到这条路径；EB-16 的正式包带着 5 份资源，`Contents/Resources` 必然存在，
**正式发布链路不受影响**。只有「打出一个不含任何资源的 `.app`」这一种形态会触发。

引入时间是 EB-08 `6025ecf`（2026-07-24）把浏览器健康状态改成由内置发行物验证决定时，
`desktop-e2e-run-20260726.md` 怀疑的 `93a6acf` 不是原因。

**本次未修**。它有两种修法（让 startup 容忍资源目录缺失并落到既有的
`browser_component_missing` 诊断页；或让 H8-22 的验收配置产出资源目录），
选哪一种是 H8-22 / EB-08 的决定。但有一点值得单独提请注意：
**产品的 fail-closed 设计本意是显示启动诊断页，而这条路径绕过了它，直接 abort，
用户看到的是应用闪退而不是任何说明。**

## 5. 与既有文档的两处更正

| 文档 | 原记载 | 本次实测 |
| --- | --- | --- |
| `FIX-control-plane-e2e-prerequisites.md` 遗留项 | `wdio.publishing.conf.ts` 缺 Python 驱动，无法跑起来，需 PB 系列自建驱动 | **能跑，5 条全过**，且按 spec 设计根本不需要 Control Plane 或工作台 |
| `desktop-e2e-run-20260726.md` §6 | `tauri.test.conf.json` 没有 identifier，会写真实 App 数据目录，是应当修掉的隐患 | 已修：identifier = `com.aventador.automationtool.uiharness`，`visible=false` |
| `desktop-e2e-run-20260726.md` §3.3 | 打包 App SIGABRT 未定位，`93a6acf` 是嫌疑 | 已定位（§4），与 `93a6acf` 无关 |

## 6. 安全与清理

- **用户扫码取得的抖音登录态全程未被触碰。**
  `~/Library/Application Support/com.aventador.automationtool` 开跑前后一致：

  ```text
  entries=675   inode=8941483   current-douyin-profile-v1=df1c89f0-8418-4336-95e8-e38e1a5fae35
  embedded-browser-profiles/douyin/ = 272 条目
  ```

  本次跑的每个配置都用带任务后缀的独立 identifier。
- 本次创建的 `…uiharness`（workbench）与 `…h822macacceptance`（崩溃定位）App 数据目录已删除；
  `…vf06acceptance` 由驱动自己删除；`…u904acceptance`（account-session）与
  `…pb07acceptance` 早于本次存在，按「不动不确定归属的状态」保留。
- 临时拷贝的 `.app` 与截图/日志临时目录已删除。
- `.local/eb-16/` 全程只读（任务一用它做真实产物验收），未写入。
- 浏览器/App/驱动进程：跑完 `pgrep -f "wdio run|tauri build|automation-tool-desktop|tauri-driver"` 为空。
- Docker：`docker ps --filter name=automation-tool` 为空；本机其他项目容器未触碰。
- `target/debug/bundle/{macos,dmg}` 由 H8-22 驱动自己清空，只剩 Tauri 自带的 `bundle_dmg.sh`。
- 未 `git add`、未 `commit`、未改任何断言、未改任何产品代码。

## 7. 仍未做的

| 项 | 原因 |
| --- | --- |
| 视频线 9 个 spec 的真实结论 | 7 个驱动都没接 `desktop_e2e_prerequisites`；接线属各自任务范围 |
| `update-windows-package` | Windows 专属，本机 macOS |
| `update-macos-package` 的修复 | 根因已定位，修法是 H8-22 / EB-08 的决定 |
| `browser-settings` / `mvp-user-journey` 的退役或重写 | 产品已删除被测路径，属 B5-04 / H8-16F 的决定 |
| `test_user_facing_branding.py` 的合成树修复 | 属 CQ-01 / CQ-02 范围 |
| IM-05 / BM-08 驱动的废弃注入清理 | 属各自任务范围 |
| 任何真实抖音账号的最终状态验证 | 本次不触碰真实平台 |

## 8. 顺带发现：两条 Node 契约用例被 control-plane 层接线打破（非本次造成）

跑 `node --test frontend/tests/*.test.mjs` 时有 2 条失败（224 过 / 2 败）：

```text
✖ B5-16 proves the hidden App launches only its private persistent Profile
  AssertionError: The input did not match the regular expression /browser-profiles/u
✖ E4-14 drives the signed Executor lifecycle through one isolated hidden App
  AssertionError: The input did not match the regular expression /!= \(7,\):/
```

两条都是**钉住验收驱动源码文本**的契约用例，断言对象分别是
`scripts/run_b5_16_acceptance.py` 与 `scripts/run_e4_14_acceptance.py`。
这两个驱动在 `2893ea7`（32 个驱动接共享启动前置）与 `748b3b6`（修 control-plane 层失败）
里被改写，被钉住的那两段文本随之消失，契约用例没跟着更新。

**与本次无关**：两个驱动在本次工作树中未被修改（`git status` 可证），
本次也没有碰 `frontend/tests/` 下这两个文件。属 B5-16 / E4-14 各自任务的跟进项。
