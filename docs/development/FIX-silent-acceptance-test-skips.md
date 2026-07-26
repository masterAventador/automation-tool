# FIX 验收测试在缺少环境时静默报 ok

> 状态：✅ 已完成（macOS 六条驱动实跑通过；Windows 三处与 IM-05 的 Tauri/WDIO 半段见「真实边界」）
>
> 日期：2026-07-26
>
> 提交：见本文件所在提交。
>
> 触发：门禁审计发现 `real_worker_render_sandbox_isolates_malicious_html` 在 **0.00 秒**内"通过"。

## 缺陷

9 处 `#[test]` 在读不到验收环境变量时直接 `return`：

```rust
let Some(browser) = std::env::var_os("BM04_RENDER_BROWSER").map(PathBuf::from) else {
    return;
};
```

libtest 对一个什么都没执行的 body 打印

```text
test result: ok. 1 passed; 0 failed; 0 ignored; ... finished in 0.00s
```

而六条驱动判断"这条测试到底跑没跑"的唯一依据正是

```python
if completed.returncode != 0 or "1 passed; 0 failed" not in completed.stdout:
```

**一个空转逐字满足这个断言。** 受影响的用例名恰好覆盖渲染沙箱隔离恶意 HTML、锁定版
Chromium 启动、包内 Node 运行时协议——即"没有那个前提就等于什么都没验证"的那一类。绿灯
不是弱证据，是假证据。

## 缺陷清单（9 处，比审计单多 1 处）

| # | 文件 | 用例 | 依赖的环境 |
| --- | --- | --- | --- |
| 1 | `local_video_orchestrator.rs` | `bundled_node_candidate_uses_packaged_runtime_and_protocol` | `BM02_PACKAGE_ROOT` |
| 2 | `local_video_orchestrator.rs` | `real_worker_render_verify_launches_the_locked_chromium` | `BM03_RENDER_BROWSER` / `_CHROMIUM_MAJOR` / `_NODE` |
| 3 | `local_video_orchestrator.rs` | `real_worker_render_sandbox_isolates_malicious_html` | `BM04_RENDER_BROWSER` / `_CHROMIUM_MAJOR` / `_NODE` / `_WORKSPACE` |
| 4 | `local_video_orchestrator_windows.rs` | `bundled_node_candidate_uses_packaged_runtime_and_protocol` | `BM02_PACKAGE_ROOT` |
| 5 | `local_video_orchestrator_windows.rs` | `real_worker_render_verify_launches_the_locked_chromium` | `BM03_PACKAGE_ROOT` / `_RENDER_BROWSER` / `_CHROMIUM_MAJOR` |
| 6 | `local_video_orchestrator_windows.rs` | `real_worker_render_sandbox_isolates_malicious_html` | `BM04_PACKAGE_ROOT` / `_RENDER_BROWSER` / `_CHROMIUM_MAJOR` / `_WORKSPACE` |
| 7 | `material_video_gateway.rs` | `frozen_worker_uses_the_authenticated_loopback_gateway_end_to_end` | `AUTOMATION_TOOL_IM03_WORKER` |
| 8 | `material_video_gateway.rs` | `app_script_settings_configure_the_real_frozen_worker_without_public_secrets` | `AUTOMATION_TOOL_IM03_WORKER` |
| 9 | `material_video_gateway.rs` | `frozen_worker_starts_real_web_ui_only_inside_the_task_workspace` | `AUTOMATION_TOOL_IM05_WORKER` |

第 5 处（Windows 的 BM-03 用例）不在审计单上，是本任务的防复发门禁扫出来的。

## 改法

Rust 侧换成仓库自 E4-07 起就在用的写法（`executor_manager_packaged.rs`、
`embedded_browser_distribution.rs` 等 7 个文件共 14 条同型用例）：

```rust
#[ignore = "requires the BM-04 …; run via scripts/run_bm_04_acceptance.py"]
fn real_worker_render_sandbox_isolates_malicious_html() {
    let browser = std::env::var_os("BM04_RENDER_BROWSER")
        .map(PathBuf::from)
        .expect("BM04_RENDER_BROWSER is staged by scripts/run_bm_04_acceptance.py");
```

于是两条路都不再说谎：普通全量跑把它算作 `ignored` 而不是 `passed`；驱动点名跑而环境缺失
时，`expect` 直接 panic。

驱动侧统一加 **`--ignored`**，不是审计单里写的 `--include-ignored`——仓库既有的 8 条驱动
（`run_e4_07/08/09/10`、`run_eb_06/07/16`、`run_bu_02`）用的都是 `--ignored`，两者在这些
调用点等价，跟随既有写法。

`run_im_03/04` 跑的是整个 `material_video_gateway` 二进制且只准备 IM-03 的 Worker，所以还要
`--skip` 掉 IM-05 那条，否则会撞上它自己缺的环境。被 skip 的用例名在三个驱动里出现，抽成
`build_material_video_worker_candidate.WEB_UI_TEST_CASE` 单点定义。

`run_im_03/04/05` 的 `run()` 原本只看退出码，而**一个什么都没选中的 libtest 仍然退出 0**——
即"驱动哪天掉了 `--ignored`"会退回同一类静默绿灯。给 `run()` 加了 `expect_summary`，让这三
条驱动断言实际执行条数（IM-03/04 各 2 条，IM-05 1 条）。

## RED

新增防复发门禁 `frontend/src-tauri/tests/acceptance_gate_honesty.rs`，两条断言：
"没有 `#[test]` 在环境缺失时静默返回"、"点名 `#[ignore]` 用例的驱动必须选中它"。

第一次 RED（未改任何测试前），逐条点名 9 处：

```text
thread 'no_acceptance_test_reports_success_when_its_environment_is_missing' panicked:
these tests return green without running when their environment is absent; …
  local_video_orchestrator.rs:177 …::bundled_node_candidate_uses_packaged_runtime_and_protocol
  local_video_orchestrator.rs:735 …::real_worker_render_verify_launches_the_locked_chromium
  local_video_orchestrator.rs:788 …::real_worker_render_sandbox_isolates_malicious_html
  local_video_orchestrator_windows.rs:83  …::bundled_node_candidate_uses_packaged_runtime_and_protocol
  local_video_orchestrator_windows.rs:123 …::real_worker_render_verify_launches_the_locked_chromium
  local_video_orchestrator_windows.rs:165 …::real_worker_render_sandbox_isolates_malicious_html
  material_video_gateway.rs:83  …::frozen_worker_uses_the_authenticated_loopback_gateway_end_to_end
  material_video_gateway.rs:132 …::app_script_settings_configure_the_real_frozen_worker_without_public_secrets
  material_video_gateway.rs:189 …::frozen_worker_starts_real_web_ui_only_inside_the_task_workspace
```

第二次 RED（只改 Rust 半边、驱动未改）——这正是"改一半"的事故形态，门禁当场抓住 4 条驱动：

```text
thread 'every_driver_naming_an_ignored_test_selects_ignored_tests' panicked:
these drivers select an ignored test that libtest will refuse to run, so the
acceptance run silently executes nothing:
  run_bm_02_acceptance.py names …::bundled_node_candidate_uses_packaged_runtime_and_protocol but passes no --ignored / --include-ignored
  run_bm_03_acceptance.py names …::real_worker_render_verify_launches_the_locked_chromium but passes no …
  run_bm_04_acceptance.py names …::real_worker_render_sandbox_isolates_malicious_html but passes no …
  run_im_05_acceptance.py names …::frozen_worker_starts_real_web_ui_only_inside_the_task_workspace but passes no …
```

## GREEN

```text
cargo test --test acceptance_gate_honesty
test result: ok. 2 passed; 0 failed
```

普通全量跑不再把验收用例算成通过：

```text
cargo test --test local_video_orchestrator
test result: ok. 18 passed; 0 failed; 3 ignored

cargo test --test material_video_gateway
test result: ok. 0 passed; 0 failed; 3 ignored
```

六条驱动在各自正常提供环境的情况下实跑：

| 驱动 | 结果 | 该用例实际耗时 |
| --- | --- | --- |
| `run_bm_02_acceptance.py` | ✅ `1 passed; 0 failed` | 0.03s |
| `run_bm_03_acceptance.py` | ✅ `1 passed; 0 failed` | 2.45s |
| `run_bm_04_acceptance.py` | ✅ `1 passed; 0 failed` | **3.48s**（原为 0.00s） |
| `run_im_03_acceptance.py` | ✅ `2 passed; 0 failed; 1 filtered out` | 3.72s |
| `run_im_04_acceptance.py` | ✅ `2 passed; 0 failed; 1 filtered out` | 3.82s |
| `run_im_05_acceptance.py` | ✅ `1 passed; 0 failed; 2 filtered out`（仅 `require_real_frozen_webui` 半段，见真实边界） | 1.69s |

IM-03/04 全脚本退出码 0，各自现场冻结 2108 files / 369454315 bytes 的真实 PyInstaller 候选。
IM-05 用同一 builder 现场冻结候选后，跑 `require_real_frozen_webui` 里逐字相同的 cargo 调用：

```text
AUTOMATION_TOOL_IM05_WORKER=<候选>/automation-tool-material-video-worker \
cargo test --manifest-path frontend/src-tauri/Cargo.toml --test material_video_gateway --locked \
  frozen_worker_starts_real_web_ui_only_inside_the_task_workspace -- --ignored --exact --test-threads=1
test result: ok. 1 passed; 0 failed; 0 ignored; 2 filtered out; finished in 1.69s
```

`2 filtered out` 与 IM-03/04 的 `1 filtered out` 互为佐证：三条驱动的选择集合正好互补，没有
哪一条在空跑。

BM-03/04 需要 `--archive .local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip`
（脚本内 `DEFAULT_ARCHIVES["macos-arm64"]` 指向 `ROOT.parent.parent`，在本机不存在；台账里
BM-03/04 记录的命令本来也是显式传 `--archive`，属于既有状态，本任务未改）。

## 变异检验

故意让环境缺失，确认对应用例变红而不是变绿：

```text
# 1) 普通跑（无环境、无 --ignored）——旧的谎言已经消失
cargo test --test local_video_orchestrator real_worker_render_sandbox_isolates_malicious_html -- --exact
test real_worker_render_sandbox_isolates_malicious_html ... ignored, requires the BM-04 …
test result: ok. 0 passed; 0 failed; 1 ignored
# ↑ 不再满足驱动的 "1 passed; 0 failed" 断言

# 2) 驱动那样点名跑，但环境全缺
cargo test --test local_video_orchestrator real_worker_render_sandbox_isolates_malicious_html -- --ignored --exact
panicked at tests/local_video_orchestrator.rs:786: BM04_RENDER_BROWSER is staged by scripts/run_bm_04_acceptance.py
test result: FAILED. 0 passed; 1 failed

# 3) 逐个变量都是活的（只补前一个，红在下一行）
BM04_RENDER_BROWSER=/bin/ls …                       → panicked at …:790（BM04_CHROMIUM_MAJOR）
BM04_RENDER_BROWSER=/bin/ls BM04_CHROMIUM_MAJOR=149 … → panicked at …:793（BM04_NODE）

# 4) IM 侧同样
cargo test --test material_video_gateway -- --ignored --skip frozen_worker_starts_real_web_ui_only_inside_the_task_workspace
test result: FAILED. 0 passed; 2 failed; 1 filtered out
```

第 4 条同时证明 `--ignored --skip` 的选择结果确实是 2 条，与驱动新加的
`expect_summary="2 passed; 0 failed"` 对得上。

## 失败矩阵

| 情形 | 期望 | 实际 |
| --- | --- | --- |
| 环境全缺 + 驱动点名跑 | 响亮失败 | ✅ panic，退出码非 0 |
| 环境部分缺 | 响亮失败并指出缺哪个 | ✅ 三个变量各自 panic 在不同行 |
| 普通全量跑（开发机日常） | 记为 ignored，不算通过 | ✅ `3 ignored` |
| 驱动掉了 `--ignored` | 门禁拦住 | ✅ 门禁二 RED；BM 侧还会因 `0 passed` 触发既有断言 |
| 驱动掉了 `--ignored`（IM 侧，libtest 选中 0 条仍退出 0） | 驱动自己拦住 | ✅ 新增 `expect_summary` 断言执行条数 |
| 新写一个同型静默 skip | 门禁拦住 | ✅ 门禁一扫描全部 `tests/*.rs` |
| 被 skip 的用例名与 Rust 端漂移 | 不得静默 | ✅ 名字单点定义；万一漂移会变成"该用例缺环境"的响亮失败 |

## 真实边界

- **Windows 三处（清单 4/5/6）未做编译与运行验证**。本机没有装 Windows target，
  `#![cfg(windows)]` 在 macOS 上编译为空。已用 `rustfmt --check` 证明该文件语法可解析、
  格式合规，且改动与 unix 侧逐字同构、绑定类型未变（`PathBuf` / `u32` 保持原样），但
  "在 Windows 上编译通过"这件事本任务没有证据，需在 Windows 验收机补。
- **IM-05 只验证了 `require_real_frozen_webui` 半段**，也就是本任务实际改到的那一条 cargo
  调用。另一半 `require_normal_app_entry` 要跑 `build:tauri:video-studio-test` 全量 Tauri
  构建 + WDIO；当时另有一条线正在产出正式包，抢构建资源不合适，且
  `FIX-startup-gate-build-fork.md` 已记录视频线 WDIO 验收当前失去可运行环境。该半段与本
  任务改动无关，未跑。
- **同型缺陷还有第 10、11 处，本任务未修**：
  `frontend/src-tauri/tests/video_editing_service_settings_real.rs` 的
  `real_gateway_accepts_production_signature` 与
  `real_gateway_rejects_tampered_secret_with_sanitized_error`。它们把 `std::env::var` 藏在
  `load_real_credentials()` 里，形状相同、后果相同（`VE04_REAL_CREDENTIALS_FILE` 缺失即静默
  报 ok）。没有一起修的原因：修它必须同时改 `scripts/run_ve_04_acceptance.py`，而那条链路
  要真实阿里云 ICE 凭据才能验证"改完还能过"，本任务拿不到——只改一半正是本文件要消灭的
  事故形态。已登记在门禁的 `UNFIXED_SILENT_SKIPS` 里，并且门禁会在它们被修好后强制要求删
  除该条目（陈旧条目直接 RED），不会变成永久豁免。

## 清理

- 本任务未新增临时文件；BM-03/04 的 run_root 由驱动自身 `shutil.rmtree` + `require_no_residue` 清理，实跑后已确认退出码 0。
- IM-03/04 的 PyInstaller 候选建在 `tempfile.TemporaryDirectory` 中，随驱动退出删除。
- 未触碰 `.local/release/`、`frontend/src-tauri/src/lib.rs`、`motion_video_studio.rs`、
  `frontend/src/features/video-studio/`，未触碰
  `~/Library/Application Support/com.aventador.automationtool/`。

## 文档

- 本文件即证据文件。命名用 `FIX-` 前缀，不与 `docs/embedded-browser-video-studio-roadmap.md`
  的 BM/IM 任务行状态双写：本任务不改这些任务的完成状态，只修它们验收链路的诚实性。
- `scripts/cq_05_evidence_completeness.py` 只校验 `XX-00.md` 形状的证据文件，`FIX-*.md` 不在
  其双向核对范围内，本文件不会造成 orphan 误报。
