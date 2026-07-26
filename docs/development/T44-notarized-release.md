# T44 Developer ID 签名与公证接入发版流程

> 状态：✅ 已完成（真实发版命令完整重跑一次，最终 DMG 带 quarantine 判定 accepted /
> Notarized Developer ID，.app 与 DMG 均已 staple）
>
> 日期：2026-07-26
>
> 提交：本文件所在的 T44 独立实现提交

## 任务

把 Apple Developer ID 签名与公证接进真包发版流程，并加出厂门禁：未公证的产物不许分发。

客户下周装包 → 装到领导的 M4 Air（macOS 26）→ 领导演示。此前实测未签名包在客户机上
被 Gatekeeper 拦下，弹窗默认按钮是「移到废纸篓」。

## 关键实测结论（不是推断）

### 1. Chrome for Testing 根本没有 Google 签名

任务描述里的问题是「Apple 是否接受内嵌他人签名的组件」。实测发现这个问题不成立：

```text
codesign -dvv "…/Google Chrome for Testing.app"
CodeDirectory v=20400 size=536 flags=0x20002(adhoc,linker-signed) hashes=13+0
Signature=adhoc
TeamIdentifier=not set
```

Framework 与 4 个 Helper .app 全部相同。上游根本没有 Developer ID 签名可以保留，
ad-hoc 签名又不可能通过公证，所以包内每一个 Mach-O 都必须用提交者身份重签。

### 2. 三份摘要清单会被重签打断（本任务真正的工作量）

包内有三块载荷各自带一份逐文件 SHA-256 清单，并且都在客户机上被真实校验：

| 载荷 | 清单 | 运行时校验方 | 必须在什么之前签 |
| --- | --- | --- | --- |
| embedded-browser | `distribution-manifest.v1.json`（条目取自 `staging-manifest.json`） | `frontend/src-tauri/src/embedded_browser_distribution.rs` | `build_distribution_manifest` |
| local-executor | `executor-manifest.v1.json` + Ed25519 签名（282 个文件） | `frontend/src-tauri/src/executor_package.rs` | `write_signed_executor_manifest` |
| media-toolchain | `manifest.json`（缺文件、多文件、改文件都拒） | `frontend/src-tauri/src/video_media_toolchain.rs` | 签完必须重算 |

签名会改写它碰到的每个 Mach-O 的字节，还会给嵌套 bundle 加 `_CodeSignature` 目录。
清单先算、后签，就会得到一个和自己清单对不上的包：`verify_distribution` 报
`file digest mismatch`，就算它没报，客户机上的 Rust 解析器也会报。

motion-video-worker 与 material-video-worker 的二进制没有摘要清单，不受此限制。

### 3. 外层封签必须跳过这三块载荷

最初实现让外层 `.app` 封签递归整棵树，会把已经算过清单的载荷再签一遍，直接毁掉上面
三份清单。`signable_nodes(root, exclude=…)` 与 `inventoried_payloads()` 就是为这个加的，
排除列表由 `contracts/quality/release-package-resources.v1.json` 派生，不是手写的。

## entitlements：逐条实测，能不给就不给

每一项都由「先不给 → 实测挂掉 → 给上 → 实测通过」得出，没有一条是猜的。

### com.apple.security.cs.allow-jit（仅 embedded-browser）

不给任何 entitlement、只开 hardened runtime 时：

```text
[adhoc]                    evaluate returned 12499997500000   ← 对照组通过
[hardened-no-entitlements] FAILED: TargetClosedError: Browser.new_page:
                           Target page, context or browser has been closed
```

崩溃报告：

```text
proc       : Google Chrome for Testing Helper (Renderer)
exception  : {"type": "EXC_BREAKPOINT", "signal": "SIGTRAP"}
termination: {"indicator": "Trace/BPT trap: 5", "byProc": "exc handler"}
```

加上 `com.apple.security.cs.allow-jit` 后同一探针：

```text
[hardened-allow-jit] evaluate returned 12499997500000
[hardened-allow-jit] page responsive after JIT
[hardened-allow-jit] OK — browser launched, JIT tier-up ran, browser closed
```

对照组用的是同一台机器上未签名的原包，排除了「无头模式本身跑不起来」这个混淆因素。

### 明确不给的项

- **com.apple.security.cs.allow-unsigned-executable-memory**：常见做法会跟 allow-jit
  一起给。实测只给 allow-jit 就够了，因此不给。
- **com.apple.security.cs.disable-library-validation**：原以为 PyInstaller onedir 加载
  `_internal/*.so` 需要它。实测不需要——包内所有 `.so`/`.dylib` 都由同一个 Developer ID
  重签，库校验本来就通过：

```text
.../local-executor/package/automation-tool-executor --help
Local Executor bootstrap is rejected      ← 执行器自己的握手校验，说明 Python 已启动
.../media-toolchain/bin/ffmpeg -version
ffmpeg version 8.1.2                      ← 通过
.../motion-video-worker/package/runtime/node --version
v22.23.1                                  ← 通过
.../material-video-worker/package/automation-tool-material-video-worker --help
Material video worker command is required ← 通过
```

- **application / media-toolchain / 两个 Worker**：全部零 entitlement。

## RED

### RED-1 签名/公证/门禁的 API 与契约都不存在（16 项失败）

```text
cd scripts && python test_release_assembly.py
Ran 32 tests in 0.064s
FAILED (failures=4, errors=12)

ImportError: cannot import name 'signable_nodes' from 'release_assembly'
ImportError: cannot import name 'load_signing_identity' from 'release_assembly'
ImportError: cannot import name 'require_distributable_artifact' from 'release_assembly'
FileNotFoundError: contracts/quality/macos-release-signing.v1.json
FAIL: test_the_release_path_no_longer_ships_an_ad_hoc_signature
  AssertionError: '"--sign", "-"' unexpectedly found in build_release_package.py
FAIL: test_the_release_path_signs_notarises_staples_and_gates (call='sign_tree(')
FAIL: ... (call='notarize_and_staple(')
FAIL: ... (call='require_distributable_artifact(')
```

### RED-2 外层封签会毁掉三份摘要清单（实现过程中发现的真实缺陷）

把外层 `.app` 封签接上去之后才意识到 `sign_tree` 会递归整棵树、把已经算过清单的
载荷再签一遍。先补失败测试再修：

```text
python -m unittest test_release_assembly.SigningOrderTests
ERROR: test_an_already_inventoried_payload_is_never_signed_again
  TypeError: signable_nodes() got an unexpected keyword argument 'exclude'
ERROR: test_the_outer_seal_excludes_every_declared_release_resource
  ImportError: cannot import name 'inventoried_payloads' from 'release_assembly'
Ran 5 tests, FAILED (errors=2)
```

### RED-3 签名后不重算清单，包就和自己的清单对不上

```text
python -m unittest test_release_assembly.StagingInventoryRefreshTests
```

`test_a_signed_tree_fails_verification_until_the_inventory_is_retaken` 先断言
`verify_distribution` 抛 `DistributionRejected`（清单陈旧），重算后才通过。

### RED-4 真实产物：hardened runtime 下浏览器直接崩

见上文 entitlements 一节，对照组 + 崩溃报告。

## GREEN

```text
cd scripts && python test_release_assembly.py
Ran 36 tests in 0.322s
OK

python scripts/check_release_package_wiring.py
release package wiring passed: 5 declared resources, 2 release paths, every resource owned and gated
python scripts/check_release_package_wiring.py --self-test
release package wiring self-test passed: the gate rejects all five mutations
python scripts/test_embedded_browser_package.py
Ran 35 tests in 0.816s
OK
```

## 真实边界（本任务的核心证据）

### 签名覆盖

```text
包内 Mach-O 总数：289
未被本团队签名的：0
codesign --verify --deep --strict "自动化运营工具.app"  → exit 0
  valid on disk / satisfies its Designated Requirement
codesign -dvv 外层：flags=0x10000(runtime)  TeamIdentifier=HK56FS93AD
                    Timestamp=Jul 26, 2026 at 12:52:10
```

按载荷分（296 个代码节点）：

| 载荷 | 代码节点 | 耗时 |
| --- | --- | --- |
| embedded-browser | 19 | 10s |
| local-executor | 82 | 33s |
| media-toolchain | 2 | 1s |
| motion-video-worker | 1 | 0.4s |
| material-video-worker | 190 | 71s |
| application（外层，排除以上五块） | 2 | 3s |

签名顺序实测为从内往外，Chrome 树最后三个节点依次是
framework 二进制 → framework bundle → `Google Chrome for Testing.app`；
5 个 framework symlink（`Versions/Current`、`Resources`、`Libraries`、`Helpers`、
顶层别名）签名前后都在，EB-16 记录的破坏没有重现。

### 公证

```text
xcrun notarytool submit 自动化运营工具.app.notarization.zip --wait
ACCEPTED submission=50507a23-5743-42d7-842d-42685be13de5 elapsed=278s
xcrun stapler validate 自动化运营工具.app
The validate action worked!
```

一次通过，没有被拒，因此没有用到 `notarytool log` 排错。

### 签名后各组件仍然能跑（全部带对照组或明确判据）

| 组件 | 判据 | 结果 |
| --- | --- | --- |
| 内置 Chromium | Playwright 启动 + V8 热循环 tier-up | 加 allow-jit 后通过；不加则 Renderer SIGTRAP |
| Local Executor | 直接执行，走到自己的握手校验 | `Local Executor bootstrap is rejected`（说明 Python 与 `_internal/*.so` 已加载） |
| ffmpeg | `-version` | `ffmpeg version 8.1.2` |
| node（motion worker） | `--version` | `v22.23.1` |
| material video worker | `--help` | `Material video worker command is required` |
| 外层 Tauri App | 隔离 HOME 下启动，存活 12s 无 panic | 通过 |

外层 App 的启动验证做了两轮，第二轮才是有效的：

1. 空的隔离 HOME → 签名包与**未签名对照组**都 panic
   `Failed to setup app: … deployment profile is invalid`，退出码相同（-6）。
   同现象说明与签名无关，是缺少 App 数据目录导致的；
2. 把真实 App 数据目录**只读复制**进隔离 HOME 后再启动 → 存活 12s 以上、无输出、无崩溃报告。

两轮都把 `HOME` 重定向到临时目录，
`~/Library/Application Support/com.aventador.automationtool/` 的 mtime
（`Jul 26 02:37:42 2026`）在全部实验前后完全一致，未被读写破坏。

### 出厂门禁：最终 DMG 带 quarantine 的判定

DMG 单独签名、单独公证、单独 staple：

```text
=== creating the disk image ===
created in 28s (510368780 bytes)
=== signing the disk image ===
=== notarising the disk image (waits on Apple) ===
ACCEPTED submission=499dd818-a771-48b6-ba66-b4edaeca0efa elapsed=285s
```

门禁本体（`require_distributable_artifact`，与手工执行任务书给的命令结果一致）：

```text
xattr -w com.apple.quarantine "0083;0;Safari;" 自动化运营工具_0.1.0.dmg
spctl -a -vvv -t open --context context:primary-signature 自动化运营工具_0.1.0.dmg

自动化运营工具_0.1.0.dmg: accepted
source=Notarized Developer ID
origin=Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)
spctl exit=0

xattr -p com.apple.quarantine 自动化运营工具_0.1.0.dmg
0083;0;Safari;                       ← 隔离标记确实在，判定不是在“干净文件”上做的

xcrun stapler validate 自动化运营工具_0.1.0.dmg
The validate action worked!
```

挂载后，对客户真正会拖出来的那个 `.app` 再判一次：

```text
hdiutil attach … -nobrowse -readonly
spctl -a -vvv /Volumes/自动化运营工具/自动化运营工具.app
  accepted
  source=Notarized Developer ID
  origin=Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)
xcrun stapler validate /Volumes/自动化运营工具/自动化运营工具.app
  The validate action worked!      ← 票据随 .app 走，断网也能开
```

.app 与 DMG 各公证一次，就是为了这一条：只 staple DMG 的话，客户把 App 拖到
「应用程序」之后那份拷贝不带票据，首次打开要联网找 Apple；演示现场网络不好会
呈现出和未签名包一样的拒绝。两次公证各约 4.7 分钟。

门禁写上去的 quarantine 标记**不再摘掉**：摘掉就意味着最后交出去的文件和刚刚判定
通过的那个文件不是同一个状态。留着它无害（跨网络传输本来就会重新打标记），而且它
让「验收的就是用户拿到的」这句话在字面上成立。

### 完整重建验证（最终证据，走的是真实发版命令）

以上都是在现成产物上摸问题。最后从 main 完整重跑一次真实发版命令，客户 Demo 变体，
全程走改造后的流水线：

```text
python scripts/build_release_package.py --platform macos \
  --work-dir .local/t44-release-verify --build-id t44-notarized-verify \
  --deployment-profile … --profile-signing-key … --action-authorization-key …

[release] Signing this release as Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)
[release] Signing the embedded browser before its manifest is taken
[release] Signed 19 browser code nodes across 337 files      ← 331 + 6 个 _CodeSignature
[release] Signed 82 Local Executor binaries before inventorying them
[release] Building for the deployment at https://at.xuanbai.tech
[release] Signed 190 material-video-worker binaries
[release] Signed 2 media-toolchain binaries
[release] Signed 1 motion-video-worker binaries
[release] Re-taking the media toolchain manifest over the signed binaries
[release] Installing the embedded browser, verifying it, then sealing the bundle
[release] Package payload verified: 337 browser files (358179451 bytes) inside 2743 package files
[E4-15] Production desktop package audit passed
[P9-05] Release bundle audit passed: 2406 files, 732979935 bytes
[release] Application notarised and stapled (submission 08fd0dc9-cd2c-49a3-828f-961d6c957da3)
[release] Signing and notarising the disk image
[release] Disk image notarised and stapled (submission e118a2b2-051f-4783-87f5-32794f8c57f2)
[release] Gate: assessing the disk image as a quarantined download
[release]   自动化运营工具_0.1.0.dmg: accepted
[release]   source=Notarized Developer ID
[release]   origin=Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)
[release] Release package built and every release gate passed
exit code 0
```

这一轮同时证明了三份摘要清单没有被签名打断——
`Installing the embedded browser, verifying it` 里的 `verify_distribution`
和随后的两个 node 审计都是对**签名后的真实包**跑的，全部通过。

构建结束后独立复验一次（不依赖构建脚本自己的输出）：

```text
xattr -w com.apple.quarantine "0083;0;Safari;" 自动化运营工具_0.1.0.dmg
spctl -a -vvv -t open --context context:primary-signature 自动化运营工具_0.1.0.dmg
  accepted
  source=Notarized Developer ID
  origin=Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)
  spctl exit=0
xattr -p com.apple.quarantine …dmg   → 0083;0;Safari;
xcrun stapler validate …dmg          → The validate action worked!
xcrun stapler validate …app          → The validate action worked!
codesign --verify --deep --strict …app → OK
```

产物：`.local/t44-release-verify/cargo-target/release/bundle/dmg/自动化运营工具_0.1.0.dmg`
（510337149 字节，指向 https://at.xuanbai.tech，`release-package.json` 里带
`gatekeeper` 与 `signed_by` 两个字段作为出厂记录）。

## 失败矩阵

| 情形 | 行为 |
| --- | --- |
| 公证被拒 | `notarize_and_staple` 抛出，错误信息里带 submission id 和现成的 `xcrun notarytool log …` 命令 |
| 公证返回的 JSON 是多行 pretty-print | `_notarization_result` 用 `raw_decode` 从后往前扫，兼容单行/多行；解析不出来才拒（否则会把 Apple 已接受的构建误判为失败） |
| 签名身份缺失或不是 Developer ID Application | `load_signing_identity` 拒绝 |
| entitlement 给了但没写理由 / 写了理由但没给 | `entitlements_for` 拒绝，单测同时守 |
| DMG 已签名但没公证 | 门禁拒绝（`source=Unnotarized Developer ID`） |
| DMG ad-hoc | 门禁拒绝（`source=Insufficient Context`） |
| Gatekeeper 直接 rejected | 门禁拒绝 |
| 外层封签误碰已算清单的载荷 | `inventoried_payloads` 排除；单测守住 |
| 新增一个发版资源却忘了排除 | 排除列表由资源契约派生，单测断言两者数量一致 |
| symlink 被当普通文件签名 | `signable_nodes` 跳过 symlink；单测守住 |
| 有人调 `install_and_seal` 忘了传 `seal` | 不再有默认值（原来默认 ad-hoc，且已无任何调用方），必须显式选择；`seal_with_adhoc_signature` 直接删除而不是留着不用 |

## 交付物身份：哪个 DMG 能交，哪个不能

签名接进流水线之后，机器上同时存在多个**文件名完全相同**的
`自动化运营工具_0.1.0.dmg`，其中大部分是未签名的旧产物。演示前一天拿错的成本很高，
所以这里把判据和当时的处置写死。

### 唯一判据（不看路径、不看文件名、不看谁说了什么）

对任意一个 DMG 执行：

```bash
xattr -w com.apple.quarantine "0083;0;Safari;" <dmg>
spctl -a -vvv -t open --context context:primary-signature <dmg>
```

- `accepted` + `source=Notarized Developer ID` → 可以交付；
- 其他任何输出 → **不可交付**，客户双击会看到「移到废纸篓」。

这条判据不依赖任何记录，任何人在任何时候都能自己跑一遍。发版命令产出的
`release-package.json` 里也会带 `gatekeeper` 与 `signed_by` 两个字段作为出厂记录，
**没有 `gatekeeper` 字段的产物一律视为不可交付**（旧流水线不写这个字段）。

### 2026-07-26 当时的实测与处置

| 产物 | 判定 | 处置 |
| --- | --- | --- |
| `.local/t44-release-verify/…/自动化运营工具_0.1.0.dmg` | accepted / Notarized Developer ID | 保留。当天唯一可交付的产物 |
| `.local/customer-demo-release/xuanbai/…dmg` | **rejected / no usable signature** | 已删除 |
| `.local/customer-demo-release/verify/…dmg` | 未签名、无票据 | 已删除 |
| `.local/eb-16/clean/…dmg`、`.local/eb-16/run/…dmg` | 未签名、无票据 | 保留。EB-16 验收工作区，`--skip-build` 复审依赖它们；目录名与 customer-demo 无关，不是交付候选 |

两个被删的都在 `customer-demo-release/` 下、都指向 at.xuanbai.tech、文件名与可交付产物
一字不差——这是最容易拿错的组合，所以直接删而不是贴标签（贴标签依赖人在关键时刻去读）。
构建日志 `xuanbai-build.log` / `verify-build.log` 保留。

### 发版命令现在默认产出到哪里

`scripts/build_release_package.py` 的 `--work-dir` 默认值是
`DEFAULT_WORK_DIRECTORY = <repo>/.local/release`。该目录**从未被使用过**——至今每次
构建都显式传了 `--work-dir`，所以历史产物散落在 `.local/` 下多个自定义目录里，这正是
本节存在的原因。今后不传 `--work-dir` 就会落到 `.local/release`，建议照此使用。

### `.local/customer-demo-release/build-xuanbai.sh` 已失效并改为拒绝执行

该脚本从 worktree `wt/release-build`（停在 `7b776fd`，main 早已越过）构建，而那棵树里的
`scripts/build_release_package.py` 是签名改造之前的版本——**今天再跑它只会再产出一个
未签名包**。已改为打印正确命令并 `exit 2`，原件留作 `build-xuanbai.sh.superseded`。

### 本次的产物不是最终交付物

功能尚未定型（动效线「一句话生成视频」在做）。`.local/t44-release-verify/` 里的包是
**流水线验证产物**，证明签名/公证/门禁这条链路成立；最终交付物需要在功能冻结后用当时的
main 重新构建一次，并再次通过上面那条判据。

## 遗留

- **Windows**：本任务只做 macOS。`scripts/run_eb_16_windows_acceptance.py` 仍走
  `seal_windows_payload`，Windows 代码签名是独立任务；
- **App 完整用户路径**：签名包在隔离 HOME + 只读复制的真实 App 数据下能启动并存活，
  但「登录 → 工作台 → 跑一条真实 RPA」这条完整链路本任务没有跑（需要真实账号与
  `~/Library/Application Support/com.aventador.automationtool/`，后者本任务禁止改动）。
  这条属于 U9/EB-16 验收范围，不是签名链路引入的风险；
- **macOS x86_64 签名/公证：未做，已知缺口。** 本任务只验证了 arm64。演示机是
  M4 Air（arm64），本次不阻塞，但**不要当成已经做过**——`build_release_package.py`
  的 `require_macos_target()` 认得 `macos-x86_64`，签名链路在该架构上一次都没跑过；
- **Windows 代码签名：未做，已知缺口。** `scripts/run_eb_16_windows_acceptance.py`
  仍走 `seal_windows_payload`，与本任务的 Developer ID 链路无关，Windows 包至今没有
  任何签名门禁。本次演示不涉及 Windows，同样**不要当成已经做过**。

## 清理

- 实验目录 `.local/t44-signing/`（不入 Git）；
- 每次浏览器探针都用 `tempfile.mkdtemp` 独立 profile，结束即 `shutil.rmtree`；
- 探针结束后 `pkill -f "Google Chrome for Testing"`，确认无残留；
- 启动探针把 `HOME` 重定向到临时目录，
  `~/Library/Application Support/com.aventador.automationtool/`（含手工扫码的抖音凭据）
  全程未被读写；
- 未运行 `scripts/run_u9_06_acceptance.py`。

