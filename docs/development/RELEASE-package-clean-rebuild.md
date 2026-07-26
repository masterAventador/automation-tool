# RELEASE 干净工作树上的正式包完整重建

> 状态：✅ 已完成（macOS arm64；不跳步、工作树干净、含可见 App 启动验收）
>
> 日期：2026-07-26
>
> HEAD：`e9ca45033644eb3f7b4a68a258932ef67f0720d2`
> （`test(login): 补平台会话命令的跨层契约用例`）
>
> 触发：`FIX-material-worker-audio-assets.md` 与 `FIX-video-runtime-release-assembly.md` 各自留下一条
> 「仍缺的验收」——上一次出厂用了 `--skip-build` 且构建时工作树不干净，产物不可归因。本次消除这两条限制。

## 本次相对上一次出厂的两点差别

| 项 | 上一次（2026-07-26 05:36） | 本次 |
| --- | --- | --- |
| `--skip-build` | 用了，因此**跳过 `verify_manifest_signature`**（执行器清单签名校验） | **没用**，全流程实跑，签名校验执行 |
| 工作树 | 不干净，另一会话在改 `frontend/src-tauri/src/*.rs`，产物是混合物 | **干净**（构建开始时 `git status --porcelain` 为空） |
| 产物归因 | 不可归因 | 可归因到 `e9ca4503` |

`--skip-build` 会跳过签名校验，依据是 `scripts/run_eb_16_acceptance.py:main()`：该分支把 `private_key`
置为 `None`，而调用点是 `if private_key is not None: verify_manifest_signature(...)`。本次走构建分支，
`private_key` 由 `build_executor_candidate()` 真实产出，校验必然执行。

## 开跑前的环境核对

| 检查 | 结果 |
| --- | --- |
| `git status --porcelain` | 空 |
| `pgrep -fl wdio` / `tauri-driver` / `chromedriver` | 均无 |
| 磁盘可用 | 3.3 TiB |
| 用户 App 数据基线 | 675 条目，inode `8941483`，抖音档案 `df1c89f0-8418-4336-95e8-e38e1a5fae35`（272 条目） |

### 撞到的第一件事：默认工作目录下有活着的 App 进程

PID 4394 是一个**前台 GUI** `automation-tool-desktop`，启动于 02:32:58，`lsof` 显示它打开着
`~/Library/Application Support/com.aventador.automationtool/embedded-browser-profiles/douyin`，
而它的 bundle 路径正是 EB-16 的**默认**工作目录：

```text
.local/eb-16/run/cargo-target/release/bundle/macos/自动化运营工具.app
```

在默认目录重建会把这个进程正在执行的 bundle 直接删掉重写。当时无法区分它是残留进程还是用户正在使用的会话，
按「不杀不确定归属的进程」处理，改用隔离工作目录：

```text
--work-dir /Users/aventador/code/automation-tool/.local/eb-16/clean
```

`--work-dir` 只改变构建树位置，**不跳过任何门禁**，代价是一次冷 Rust 构建。
（事后由主会话确认 4394 是几小时前某次验收的残留，已 SIGTERM 优雅退出；隔离工作目录本身仍是正确做法，保留。）

## 命令与真实输出

### 一、完整构建 + 全部门禁（不跳步）

```text
$ uv run --project backend python scripts/run_eb_16_acceptance.py \
    --work-dir /Users/aventador/code/automation-tool/.local/eb-16/clean

START 2026-07-26 08:17:20  HEAD=e9ca45033644eb3f7b4a68a258932ef67f0720d2
[EB-16] Running the deterministic package gate
Ran 35 tests in 0.865s
OK
[EB-16] Staging the digest-locked macos-arm64 Chromium from chrome-mac-arm64.zip
[EB-16] Building the real signed Local Executor candidate
[EB-16] Building one production-mode .app (no test features)
[EB-16] Preparing the pinned video runtime resources (cached per machine)
[EB-16] Installing the video runtime resources into the built bundle
[EB-16] Video runtime installed: ['material-video-worker', 'media-toolchain', 'motion-video-worker']
[EB-16] Installing the embedded browser, verifying it, then re-sealing
[EB-16] Creating the release disk image from the final App bundle
[EB-16] Built application: .../.local/eb-16/clean/cargo-target/release/bundle/macos/自动化运营工具.app
[EB-16] Built disk image: .../dmg/自动化运营工具_0.1.0.dmg (572189948 bytes)
[EB-16] Package payload verified: 331 browser files (359441871 bytes) inside 2737 package files (1207873055 bytes)
[EB-16] Auditing the built binary, configuration and whole bundle content
[E4-15] Production desktop package audit passed
[P9-05] Release bundle audit passed: 2406 files, 848431184 bytes
[EB-16] Verifying outer and inner code signatures on the built package
[EB-16] Outer ad-hoc signature seals the final bundle and the packaged browser keeps its upstream ad-hoc linker signature
[EB-16] Verifying and mounting the built .dmg, then installing the App
[EB-16] Package payload verified: 331 browser files (359441871 bytes) inside 2737 package files (1207873055 bytes)
[EB-16] Verifying outer and inner code signatures on the built package
[EB-16] Outer ad-hoc signature seals the final bundle and the packaged browser keeps its upstream ad-hoc linker signature
[EB-16] Checking every startup gate input of the installed package
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.27s
[EB-16] Launching the packaged Chromium from the installed App (offline)
[EB-16] Packaged Chromium reported version 149.0.7827.55 and exited cleanly
[EB-16] Skipping the visible App launch phase (set AUTOMATION_TOOL_EB16_LAUNCH_VISIBLE_APP=1 to run it)
[EB-16] Uninstalling the App and checking for residue
[EB-16] EB-16 acceptance passed: one ad-hoc signed macos-arm64 package with 331 browser files
        (359441871 bytes), package 1207873055 bytes, disk image 572189948 bytes
EXIT=0 2026-07-26 08:19:35
```

历时 2 分 15 秒（三份视频运行时命中本机构建缓存，未重建 ffmpeg）。

### 二、可见 App 启动验收（唯一有头环节）

EB-16 默认跳过这一阶段，需显式开启环境变量。这一步会在屏幕上弹出真实产品窗口，直到脚本把它退出。

```text
$ AUTOMATION_TOOL_EB16_LAUNCH_VISIBLE_APP=1 \
  uv run --project backend python scripts/run_eb_16_acceptance.py \
    --work-dir /Users/aventador/code/automation-tool/.local/eb-16/clean --skip-build

[EB-16] Starting isolated PostgreSQL as automation-tool-eb16-<pid>
[EB-16] Applying the production migration chain
INFO  [alembic.runtime.migration] Running upgrade ... -> 20260723_0034,
      Create durable finished-output lineages and artifacts for VE-07.
[EB-16] Starting the local Control Plane on 127.0.0.1:8765
[EB-16] Launching the installed release App from its normal entry point
[EB-16] A real product window will appear on screen until the App is quit
[EB-16] Startup path reached: the packaged App requested ['/api/v1/health', '/api/v1/version']
[EB-16] Launch Services registered the installed App as a GUI application
[EB-16] Quitting the App
[EB-16] Uninstalling the App and checking for residue
[EB-16] EB-16 acceptance passed: ...
EXIT=0 2026-07-26 08:21:18
```

这一趟用 `--skip-build` 是**正确用法**：它复用第一趟刚验证过的同一个 `.app` 与 `.dmg`，只补跑启动阶段。
签名校验已由第一趟完成，本文件开头那条限制不因此复活。

## 可见 App 启动阶段的结论

**App 真的起来了，并且走完了生产启动门禁。** `launch-evidence.json` / `eb-16-acceptance.json` 实录：

| 证据 | 值 | 含义 |
| --- | --- | --- |
| `launch_services_record` | `pid = 32723 type="Foreground"`，bundle path 指向 `clean/installed/自动化运营工具.app` | 从 DMG 安装出来的那一份注册成了前台 GUI App，不是构建树里的副本 |
| `requests` | `["/api/v1/health", "/api/v1/version"]` | 桌面启动门禁经**生产 Rust 网络桥**打到真实 Control Plane（真实 PostgreSQL + 生产迁移链），不是 Mock |
| `exit_code` | `-15` | 收到 SIGTERM 后优雅退出，不是崩溃、不是被 kill -9 |
| `isolated_home_used` | `true` | 全程跑在 `/private/tmp` 下的临时 HOME |
| `first_install_app_data_created` | 15 条，含 `device-identity-ed25519-v1`、`local-executor/executor-id-v1`、`embedded-browser-profiles/douyin` 等 | 隔离 HOME 里是一次**全新安装**的首启形态，证明它没有读用户目录 |
| `user_app_data_untouched` | `{entries: 675, inode: 8941483}` | 脚本自身在启动前后对真实用户目录取指纹并比对，不一致即失败 |

启动后脚本按流程退出 App、卸载、查残留，`require_no_process_matching` 与 Launch Services 注册均已清空。

## 产物

### DMG

```text
路径：/Users/aventador/code/automation-tool/.local/eb-16/clean/cargo-target/release/bundle/dmg/自动化运营工具_0.1.0.dmg
体积：572,189,948 B（545.7 MiB）
```

### `.app` 内五份资源

| Resources 内的资源 | 文件数 | 体积 |
| --- | ---: | ---: |
| `embedded-browser` | 333 | 359,658,199 B（343.0 MiB） |
| `local-executor` | 284 | 184,686,384 B（176.1 MiB） |
| `material-video-worker` | 2108 | 484,123,149 B（461.7 MiB） |
| `motion-video-worker` | 3 | 113,124,957 B（107.9 MiB） |
| `media-toolchain` | 8 | 44,095,804 B（42.1 MiB） |
| 小计 | 2736 | 1,185,688,493 B（1130.8 MiB） |

五份齐全，与 `release_assembly.VIDEO_RUNTIME_RESOURCES` 声明的安装位置一致；
`Video runtime installed: ['material-video-worker', 'media-toolchain', 'motion-video-worker']` 是装配器的实跑输出。

### 整包体积与上限

```text
审计口径整包：2737 文件  1,207,873,055 B  = 1151.9 MiB
声明上限     max_package_bytes = 1270 MiB          → 通过（余量 118 MiB）
浏览器       331 文件  359,441,871 B，上下界 320–420 MiB → 通过
Chromium 版本 149.0.7827.55（从安装后的包离线启动实测）
```

**两套文件数的口径差异已核对，不是异常**：`find -type f` 数整个 `.app` 得 2739 文件 / 1,208,089,383 B，
比审计口径多 2 文件 / 216,328 B。原因在 `check_embedded_browser_package.py:audit_embedded_browser_package()`——
它对浏览器目录不数磁盘文件，而是加 EB-05 清单**已逐文件校验**的 `distribution.verified_files`（331）与
`total_bytes`（359,441,871）。磁盘上浏览器树是 333 文件 / 359,658,199 B，差值 2 文件 / **216,328 B**，
与整包差值**逐字节相等**，两边对上。上一次出厂记录里 2737 与 2736 的关系同理。

### 与上一次产物的差异（归因证据）

五份资源**逐份字节相同**（浏览器、执行器、三份视频运行时都是锁定版本 + 锁定摘要的确定性产物，
且命中同一份本机构建缓存）。整包差 **−387,152 B**（1,208,260,207 → 1,207,873,055），差异全部落在
App 壳与前端资源上——正是上一次被另一会话在途 `frontend/src-tauri/src/*.rs` 改动污染的部分。
本次这 387 KB 的消失，就是「工作树干净」这件事的直接体现。

## 中途撞上的门禁与处理方式

**没有任何门禁拒绝，因此没有放宽任何判据。** 本次未修改一行门禁代码、未修改一行产品代码。
需要记录的是两件绕过去的环境问题：

### 1. 默认工作目录被活进程占用 → 换隔离工作目录

见上文。处理方式是换 `--work-dir`，不是杀进程、也不是跳过步骤。

### 2. `frontend/dist` 竞态：这次没中，但窗口只有 13 秒

上一次出厂在 `audit-production-package.mjs` 被拒（`Production build contains a desktop test marker`），
查明是并发 E2E 每轮以 `desktop-e2e` 模式重写共享的 `frontend/dist`。本次开跑前就发现 dist 是脏的：

```text
08:17 开跑前   index-BmVN2yc3.js  1,413,708 B   含 wdioTauri / plugin:wdio|   ← 07:56 某次测试构建的残留
```

EB-16 的 `tauri build` 会先以生产模式重写 dist，所以这份残留会被自动覆盖。为确认不再被并发写入，
全程记录 dist 变化：

```text
08:17:50  index-Bow_WG7i.js  1,397,917 B   ← EB-16 生产构建产出，两趟审计读的都是它
08:21:31  index-BmVN2yc3.js  1,413,708 B   ← 另一代理的测试构建重新覆盖
```

两趟运行的 `audit-production-package.mjs` 都落在 08:17:50–08:21:31 这段生产 dist 有效期内并通过
（第二趟收尾于 08:21:18，距被覆盖只差 **13 秒**）。也就是说本次结论未被污染，但
`FIX-material-worker-audio-assets.md` 记的那条隐患依然成立且依然未修：
**该审计读的是「运行时刻磁盘上的共享 dist」，不是「构建那一刻嵌进二进制的那一份」**，
在同一工作树并发构建时既可能误报也可能漏报。本次的 13 秒余量是运气，不是设计。建议按缺陷单独立项。

## 工作树归因

构建开始（08:17:20）时 `git status --porcelain` 为空，`.app` 与 `.dmg` 产出于 08:17:20–08:19:35。
收尾时工作树出现一处改动：

```text
 M frontend/e2e-tauri/task-event-stream.spec.ts      mtime 2026-07-26 08:21:13
```

这是另一代理修 control-plane E2E 的在途文件，**产生于产物完成并审计之后**（08:19:35 之前工作树仍为空），
且该文件是 WebdriverIO E2E spec，只被 `frontend/tsconfig.node.json` 引用，不进 Vite 生产构建。
因此本次 DMG 可归因到 **`e9ca4503` + 干净工作树**。

## 失败矩阵（本次实跑覆盖到的）

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 执行器清单签名被篡改或不匹配 | `verify_manifest_signature` 用真实私钥验签 | **本次实跑**（上一次因 `--skip-build` 未跑） |
| 包内混入第二个浏览器 / WebDriver | 审计拒绝 | 门禁实跑通过 |
| 包内混入桌面测试标记 | `audit-production-package.mjs` 拒绝 | 门禁实跑通过 |
| 包内混入发布禁止后缀（`.mp3` 等） | `audit-release-bundle.mjs` 拒绝 | 门禁实跑通过（2406 文件） |
| 三份视频运行时缺失或必需文件为空 | `require_packaged_video_runtime` 拒绝 | 门禁实跑通过 |
| 体积越界（浏览器上下界、整包上限） | 拒绝 | 实测 1151.9 MiB / 上限 1270 MiB |
| 安装后负载与构建产物不一致 | 逐项比对后拒绝 | DMG 挂载安装后四项计数逐项相等 |
| 内外层代码签名缺失或被破坏 | 拒绝 | 构建产物与安装产物各验一次 |
| 从安装包离线启动内置 Chromium | 版本须与清单一致 | 实测 149.0.7827.55，干净退出 |
| 打包 App 首启读用户真实数据目录 | 隔离 HOME 未产生数据即失败；真实目录指纹变化即失败 | **可见 App 实跑** |
| 退出后进程 / Launch Services 残留 | 拒绝 | 实跑通过 |
| 卸载后安装根残留 | 拒绝 | 实跑通过 |

## 清理与残留复查

| 项 | 结果 |
| --- | --- |
| `automation-tool-desktop` / `automation-tool-executor` 进程 | 无残留 |
| Launch Services 注册 | 已清空 |
| 端口 8765 | 已释放 |
| `automation-tool-eb16-*` 容器 / 卷 / 网络 | 无残留 |
| DMG 挂载点 | 无 automation 相关挂载 |
| `installed/`、`mounted-dmg/` | 已被脚本删除，只剩 `build/`、`cargo-target/` 与两份证据 JSON |
| 其他项目的 14 个容器 | 未触碰 |
| `~/Library/Caches/automation-tool-build/` | 未改动，三份缓存均命中复用 |

**抖音登录态复查（跑完后实测）**：

```text
~/Library/Application Support/com.aventador.automationtool   675 条目，inode 8941483   ← 与开跑前基线完全一致
current-douyin-profile-v1                                    df1c89f0-8418-4336-95e8-e38e1a5fae35
embedded-browser-profiles/douyin/                            272 条目
```

除自身比对外，脚本内建的 `user_app_data_untouched` 也独立记录了同一组指纹（675 / inode 8941483）。

## 仍未覆盖的部分

- **Windows 侧全部未验证**。本次只在 macOS arm64。`run_eb_16_windows_acceptance.py` 的装配接线、
  NSIS 对 `bundle.resources` 声明三份视频资源的实际打包行为、Windows 上的体积与真实成片都没跑过；
- **macOS x86_64 未验证**。只做了 arm64 单架构；
- **Developer ID 签名、公证与 Gatekeeper 未做**。本机无 Apple Developer ID，包是 ad-hoc 签名
  （`signingIdentity: "-"`），这三项仍是外部凭据门禁；
- **没有在这个正式包里走视频制作的用户路径**。本次证明的是「五份资源装进去了、包能装能起能连 Control Plane」，
  不是「用户在这个包里点『智能素材成片』能出片」。`FIX-video-runtime-release-assembly.md` 的
  「仍缺：在正式 App 里操作视频制作」依旧未闭环——正式包不含 WebDriver，无法自动化驱动，
  该验收要等构建期分叉消除后在「正式构建 + 仅挂 WebDriver」的形态上做；
- **`audit-production-package.mjs` 的 dist 竞态未修**。本次靠 13 秒余量侥幸避开，判据本身仍读共享目录；
- **三份视频资源仍无逐文件摘要校验**，完整性判据只是「必需文件存在且非空」（浏览器有 EB-05 清单，视频这三份没有）；
- **背景音乐三个选项当前实际都等价于「无背景音乐」**，见 `FIX-material-worker-audio-assets.md`，属产品取舍，未处理。
