# T31 复核：打包 App 是否仍然启动闪退

> 状态：✅ 已完成（纯复核任务，未改产品代码）
>
> 结论：**H8-22 记录的那个闪退，在当前可交付的签名正式包上不复现。**
>
> 日期：2026-07-26
>
> 提交：本文件所在提交

## 1. 要复核什么

`H8-22` 期间打出的 App 启动即 SIGABRT，根因当时定位为 **bundle 缺 `Contents/Resources` 目录**
（见 `docs/development/desktop-e2e-remaining-specs.md` §4）。此后正式包装配流程重写、签名公证接入、
视频运行时资源补齐、字体更换。本任务只回答一个问题：**这个缺陷现在还在不在。**

复核对象是唯一可交付产物：

```text
.local/t44-release-verify/cargo-target/release/bundle/dmg/自动化运营工具_0.1.0.dmg
sha256 = e6d83d4e0ab47ebf1d4448de1e1bad109fd69ebc83de384b643b77a97d143d14
```

该摘要与 T44、T48 记录的完全一致，文件未被任何人改动。
`.local/eb-16/{clean,run}/` 下两个包没有 `release-package.json`，更没有 `gatekeeper` 字段，
按判据不可交付，不在复核范围。

## 2. 结论一：可交付包上不复现

### 2.1 结构证据 — `Contents/Resources` 存在且完整

直接挂载 DMG（`hdiutil attach -nobrowse -readonly`）看**发出去的那个 `.app`**：

```text
Contents/
  ├── Info.plist
  ├── MacOS/automation-tool-desktop
  ├── Resources/          ← H8-22 崩溃时整个不存在的就是它
  │     ├── embedded-browser        343M
  │     ├── local-executor          177M
  │     ├── material-video-worker   357M
  │     ├── media-toolchain          42M
  │     └── motion-video-worker     107M
  ├── CodeResources
  └── _CodeSignature/
```

三份逐文件摘要清单也都在：
`Resources/embedded-browser/distribution-manifest.v1.json`、
`Resources/local-executor/package/executor-manifest.v1.json`、
`Resources/media-toolchain/manifest.json`。
同一次挂载顺带核对 `spctl -a -vvv --type execute` → `accepted / Notarized Developer ID`。
挂载点 `/private/tmp/t31-dmg-verify-$$` 已 detach 并 `rmdir`，`mount` 中无残留。

### 2.2 装配证据 — 正式链路结构上不可能产出无 Resources 的包

`scripts/release_configuration.py:130` 的 `write_macos_release_configuration()` 固定把
`local-executor` 声明进 `bundle.resources`，而 `write_release_configuration()` 在 `sources`
与 `bundler_declared_resources("macos")` 不一致时直接 `ReleaseConfigurationRejected`。
只要走这条路，Tauri bundler 就必然建出 `Contents/Resources`；其余四份由
`scripts/release_assembly.py` 再装进成品 `.app`。

也就是说，H8-22 那个形态（**一个不含任何资源声明的 `.app`**）在正式发版路径上写不出来。

### 2.3 运行证据 — 这个产物今天被真实启动过，没有崩溃

系统崩溃报告是不会说谎的旁证。`~/Library/Logs/DiagnosticReports/` 中今天全部 8 份
`automation-tool-desktop-*.ips`：

| 时间 | codeSigningID | 产物 | 归属 |
| --- | --- | --- | --- |
| 04:26 / 09:49 / 09:56×2 / 09:57 | `…automationtool.h822macacceptance` | `Automation Tool H822 Mac Acceptance.app` | H8-22 验收包，即已知缺陷本体 |
| 13:00:15 / 13:00:35 / 13:00:55 | `com.aventador.automationtool` | `/Users/USER/*/自动化运营工具.app` | T44 启动探针第一轮，见 §3 |

**13:09 编出最终二进制、13:14 成型 `.app`、13:19 打出 DMG；此后到现在（16:xx）一份崩溃报告都没有。**
而这段时间里这个产物被真实启动过至少两次：T44 §「外层 Tauri App」第二轮存活 12s 以上；
T48 §3 从 DMG 安装后启动，进入 Tauri 事件循环、拉起 WebKit 三件套、
`setup()` 全程跑完并生成 `device-identity-ed25519-v1`、`local-executor/executor-id-v1`、
`embedded-browser-profiles/douyin`、`video-workspaces-v1/` 等全部目录。

**本任务因此没有再启动一次正式包**：重复启动会在用户屏幕上再造一次 1280×801 的窗口，
而它换不来任何新证据。

## 3. 13:00 那三次崩溃是什么（不是新缺陷）

崩溃栈与 H8-22 完全同形：

```text
tao::…::app_delegate::did_finish_launching
  → core::panicking::panic_cannot_unwind      ← panic 发生在 extern "C" 回调里，不能 unwind
  → std::panicking::panic_with_hook → abort   ← SIGABRT
```

`parentProc = python`，对应 T44 的启动探针。T44 自己记了这一轮的现象：

> 空的隔离 HOME → 签名包与**未签名对照组**都 panic `Failed to setup app: … deployment profile is invalid`，
> 退出码相同（-6）。

根因可以在代码里定死，不用猜：`DeploymentProfile::load()` 只读编译期 `option_env!`，
与 HOME 无关；真正失败的是 `prepare_data_directory()` →
`ensure_private_directory()`（`deployment_profile.rs:252`），它用的是
**`fs::create_dir`（非递归）**。一个空的隔离 HOME 里没有 `Library/Application Support`，
父目录不存在 → `create_dir` 失败 → `DeploymentProfileError` → setup 报错 → abort。

**客户机上不会出现这一条**：真实 macOS 账号的 `~/Library/Application Support` 在建账号时就有。
这三次崩溃是探针把 HOME 指向空目录造出来的，不是产品在真实环境下的行为。

## 4. 仍然存在、但本任务不修的东西

复核到的两件事如实登记，都不改代码（本任务范围是"确认还在不在"，不是"顺手改"）：

1. **`resource_dir()` 那条 `?` 还在原地**（`frontend/src-tauri/src/lib.rs:4239` 附近，
   `EmbeddedBrowserAuthority::new` 的入参）。任何用现有 `tauri*.conf.json` 打出的
   `.app` 都不声明 `bundle.resources`（全部 47 个配置里只有正式发版路径在运行时注入），
   所以 H8-22 那个触发形态**依然一碰就中**。它只影响验收包，不影响交付包。
2. **`setup()` 整体是"失败即 abort"**，产品本来设计的 fail-closed 启动诊断页在这条路径上根本没机会渲染。
   任何 setup 内的意外失败，用户看到的都是闪退而不是任何说明。
   演示机是一台从没跑过本 App 的 M4 Air，这条属于**兜底缺失**，不是已知会触发的缺陷。

## 5. 真实边界

- 本任务证明的是：**当前签名正式包不会因为缺 `Contents/Resources` 而闪退**，
  且该产物今天被真实启动过、没有产生任何崩溃报告；
- 本任务**没有**证明：客户在自己机器上首次双击（含 Gatekeeper 同意框）后一切正常
  —— 那条仍是 `docs/demo-preflight-checklist.md` B3 的人工待办；
- 本任务**没有**在演示机（M4 Air / macOS 26）上跑过任何东西。

## 6. 清理

- DMG 只读挂载后已 detach，挂载点已删除，DMG 内容与 sha256 未变；
- `~/Library/Application Support/com.aventador.automationtool/` 全程未读写；
- 未运行 `scripts/run_u9_06_acceptance.py`；
- 未启动任何 App、浏览器或后台服务；未改动任何产品代码。
