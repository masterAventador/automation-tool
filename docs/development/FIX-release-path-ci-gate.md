# FIX 唯一产包路径进 CI 门禁 + 包审计断言必需资源

> 状态：✅ 已完成（结构性修复 + 防复发门禁；未产出新的正式包）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/completed-task-wiring-audit-20260726.md` 第 288–296 行
> 「6.1 装配路径本身有两个问题」，以及同一份审计对
> `frontend/scripts/audit-production-package.mjs:112` 的判定
> 「遍历 `bundle.resources` 找违禁串，从不检查必需资源是否声明——空的 `resources` 永远通过」。
>
> 与 `docs/development/FIX-package-audit-artifact-race.md`（同日、`a04a5fe`）共存：
> 那次给审计加了「产物归属」判据，本次给同一个审计加「必需资源」判据，两者互不覆盖。

## 缺陷

两条，同一个根因的两半。

### 一、唯一能产出完整包的路径不是一条命令，也不在任何门禁里

`install_and_seal` / `install_video_runtime` / `require_packaged_*` 的全部非测试调用方，
只有 `scripts/run_eb_16_acceptance.py` 与 `scripts/run_eb_16_windows_acceptance.py` 两个**验收脚本**。
`.github/workflows/desktop.yml` 不跑它们。`frontend/package.json` 里 44 条 `build:tauri:*`
全是 `--debug --no-bundle`，没有一条产出可分发包。

结果：想出一个包，必须跑一整套验收；而没有任何工作流跑那套验收。
于是「装配步骤被改坏 / 少装一份资源」这件事，没有任何自动判据会说话。

### 二、正式包审计只做否定检查，空 `resources` 永远通过

`audit-production-package.mjs` 对 `bundle.resources` 唯一的动作是**找违禁串**：

```js
const bundledPaths = [...stringsIn(configuration?.bundle?.resources), ...];
if (bundledPaths.some(...forbidden...)) throw ...
```

一个空声明和一个完整声明，对它来说**没有区别**。它也从不看构建出来的包里到底有没有那些资源。
所以 2026-07-26 那个缺三份视频运行时的 macOS 包，一路通过了 `[E4-15] Production desktop package audit passed`。

### RED —— 先证明这个洞真实存在

在 `frontend/tests/production-package-audit.test.mjs` 里造一个**真实形状的 `.app`**
（`Contents/MacOS/<binary>` + `Contents/Resources/<五份资源>`），然后分别拿掉资源、清空
`bundle.resources`，要求审计拒绝。改代码之前的实跑：

```text
$ node --test frontend/tests/production-package-audit.test.mjs
ℹ tests 17
ℹ pass 12
ℹ fail 5
✖ P9-05 refuses a production config that declares no bundler resource at all
    AssertionError: Missing expected rejection
✖ P9-05 refuses a package whose video runtime never reached the resource directory
✖ P9-05 refuses a package whose browser or Executor tree is absent
✖ P9-05 refuses a package whose required resource file is present but empty
✖ P9-05 requires every release resource to be declared on Windows
```

条数 11 → 17（新增 6 条），失败 5 条，**失败原因全部是 `Missing expected rejection`**——
不是断言写错，是审计真的接受了这五种包。洞坐实。

## 方案

### 单一声明源：`contracts/quality/release-package-resources.v1.json`

生产 Rust 代码从打包资源目录解析五棵树。此前「哪五棵、装在哪、谁负责装」这件事，
在四个地方各存了一份手抄子集：

| 地方 | 它知道的子集 |
| --- | --- |
| `release_assembly.VIDEO_RUNTIME_RESOURCES` | 3 份视频 |
| `run_eb_16_windows_acceptance.write_release_configuration` | 5 份（手写 `resources` 字典） |
| `run_eb_16_acceptance.write_release_configuration` | 1 份（Executor） |
| `audit-release-bundle.mjs` | Executor 的 3 个文件 |

新增一份契约，声明每份资源的：名字、类别、安装路径、必需文件、Windows 可执行后缀、
**每个平台由谁负责装**（`bundlerDeclared`），以及更强的完整性判据归谁（`verifiedBy`）。

派生方（全部改为读它，不再手抄）：

- `scripts/release_assembly.py` —— `VIDEO_RUNTIME_RESOURCES` 现在由契约里 `category == "video"` 的条目生成；
- `frontend/scripts/audit-production-package.mjs` —— 必需资源断言读它；
- `scripts/release_configuration.py` —— 两个平台的 `bundle.resources` 由它生成；
- `scripts/check_release_package_wiring.py` —— 接线门禁由它推导。

`scripts/test_release_assembly.py` 里有一条测试专门守这件事：
`release_assembly.py` 源码中**不允许再出现** `"media-toolchain"` / `"bin/ffmpeg"` /
`"runtime/node"` / `"automation-tool-material-video-worker"` 这些字面量。

### 一、发版命令：`scripts/build_release_package.py`

```bash
python3 scripts/build_release_package.py --platform macos [--work-dir DIR] [--archive ZIP]
# 或
pnpm --dir frontend release:package
```

它做完整的一趟：staging 锁定摘要的 Chromium → 构建签名 Local Executor →
按契约生成 Tauri 配置 → `tauri build` → 冻结本次 dist → `prepare_video_runtime`
→ `install_video_runtime` → `install_and_seal`（含重新签名）→
`require_packaged_browser` + `require_packaged_video_runtime` → `hdiutil create`
→ 三道审计（`check_embedded_browser_package`、`audit-production-package.mjs`、
`audit-release-bundle.mjs`）→ 校验 Executor 清单签名。

它**不做**验收的事：不安装、不启动、不探活、不卸载。那是 EB-16。

**没有第二套实现。** 原先住在 `run_eb_16_acceptance.py` 里的五个发版步骤
（`stage_browser_distribution`、`build_executor_candidate`、`build_release_package`、
`install_runtime_resources_and_sign`、`create_disk_image`）**移动**到了这个模块，
EB-16 现在 `from build_release_package import ...`，`main()` 的函数体一行没改。
方向是对的：验收脚本包住发版命令，而不是反过来。

Windows 侧**明确拒绝**而不是半实现：

```text
$ python3 scripts/build_release_package.py --platform windows
release failed: the Windows release still runs through
scripts/run_eb_16_windows_acceptance.py; this command builds macOS packages only
```

### 二、审计断言必需资源

`auditProductionPackage` 新增两段判据，都由契约驱动：

1. **声明**：本平台 `bundlerDeclared` 为真的每份资源，必须出现在 `bundle.resources` 的**目的地**里。
   空 `resources` 直接拒。
2. **存在**：包里 `<资源根>/<installedParts>` 必须是真实目录；契约列了 `requiredFiles` 的，
   逐个必须是**非空文件**（目录存在但文件是空壳，正是生产解析器会踩的形态）；
   没列的（浏览器、Executor，各自有更强的门禁）要求目录非空。

**判据绑在产物上，不绑在调用方开关上。** macOS 包从被审计的二进制自己认出来：
`<Name>.app/Contents/MacOS/<binary>` 这个结构只有打好的包才有，`--no-bundle` 的
release 二进制永远不是。所以 macOS 上**不给参数也逃不掉**。
Windows 没有这种结构标记（它的负载就是普通目录），只能显式传 `--package-root`，
而「发版路径必须传」由 `check_release_package_wiring.py` 和
`test_release_assembly.py` 两处守着。

### 三、CI 门禁：`desktop.yml` 新增 `release-wiring` job

**先说清楚 CI 验不了什么**：一次真实 macOS 发版要 340 MB Chromium 归档、
一次 PyInstaller Executor 构建、一次 ffmpeg 构建、两个视频 Worker（其中一个 460 MB），
还要签名。这不是 CI job 能干的事，本次**没有**假装能干。

CI 上真正能判定、且本次接进去的是这些：

| 步骤 | 判定内容 | 能失败吗 |
| --- | --- | --- |
| `check_release_package_wiring.py` | 每份声明资源在每个平台都有明确的 owner（bundler 或 assembler，不能既是又不是）；macOS 上 assembler 装的正好是 bundler 装不了的；每份视频资源都有构建者；两个平台的发版配置**实跑写出来再读回来**，声明集必须等于契约要求；少一份负载会被写入方拒绝；两条发版路径都调装配器、四道门禁并把包给审计看；审计确实读契约而不是重抄 | ✅ 见下 |
| `check_release_package_wiring.py --self-test` | 把发版路径改坏（删掉 `install_video_runtime`、把 `--package-root` 改掉）、把契约改坏（多一份没人装 / 没人声明 / 没人构建的资源），要求门禁**全部拒绝** | ✅ 这就是它的全部作用 |
| `test_release_assembly.py` | 装配器对缺资源、空负载、半装失败回滚的拒绝；单一声明源；发版路径接线 | ✅ |
| `test_embedded_browser_package.py` | 既有确定性包门禁（35 条） | ✅ |
| `node --test production-package-audit.test.mjs release-package-gate.test.mjs` | 上面 RED 里那 6 条 + 发版命令存在性 + CI 自身接线 | ✅ |

job 跑在 `ubuntu-latest`，只要 python3 + node，10 分钟超时，无需 pnpm install
（审计脚本只用 node 内置模块）。

## 真实输出

### RED → GREEN

```text
# 审计侧（改代码前）
$ node --test frontend/tests/production-package-audit.test.mjs
ℹ tests 17   ℹ pass 12   ℹ fail 5        ← 五条 Missing expected rejection

# 审计侧（改代码后）
$ node --test frontend/tests/production-package-audit.test.mjs
ℹ tests 17   ℹ pass 17   ℹ fail 0

# 单一声明源（改代码前）
$ python3 scripts/test_release_assembly.py
ImportError: cannot import name 'RELEASE_RESOURCE_CONTRACT' from 'release_assembly'

# 发版命令 + CI 接线（改代码前）
$ node --test frontend/tests/release-package-gate.test.mjs
ℹ tests 4   ℹ pass 1   ℹ fail 3
  Error: ENOENT: no such file or directory, open '.../scripts/build_release_package.py'

# 全部改完
$ python3 scripts/test_release_assembly.py
Ran 19 tests in 0.057s
OK
$ cd frontend && node --test tests/*.test.mjs
ℹ tests 237   ℹ pass 237   ℹ fail 0
```

### 门禁真的会失败（不是自证，是实跑）

**（a）自检模式**——门禁自己造五种坏形态：

```text
$ python3 scripts/check_release_package_wiring.py --self-test
release package wiring self-test passed: the gate rejects all five mutations
```

**（b）主模式**——往契约里真加一份没人构建的第六份资源，再跑主门禁：

```text
$ python3 scripts/check_release_package_wiring.py
release package wiring rejected: subtitle-font-pack is declared as a packaged
video resource but prepare_video_runtime.py never builds it
EXIT=1
```

> 这一条是本次开发中门禁**第一次抓到自己的漏洞**：第一版门禁对这个变异是**通过**的
> （因为单一声明源会把新资源自动传导给装配器和配置写入方，两边自洽）。
> 于是补了 `check_every_video_resource_has_a_builder`，才有上面的拒绝。

**（c）审计主模式**——用 P9-03 产出的那种真实形状（`.app` 里只有 `local-executor`，
配置用 checked-in 的 `tauri.conf.json`）直接调审计：

```text
REJECTED: Production Tauri config does not declare a required release resource: local-executor
```

### CI 配置有效性

```text
$ actionlint .github/workflows/desktop.yml
ACTIONLINT_EXIT=0

$ python -c "yaml.safe_load(...)"
jobs: ['release-wiring', 'executor-bundle', 'desktop']
  release-wiring -> ubuntu-latest 8 steps
```

`release-wiring` 的 5 条 run 行，逐条按 CI 的形态在本机实跑（工作目录=仓库根）：

```text
python3 scripts/check_release_package_wiring.py              EXIT=0
python3 scripts/check_release_package_wiring.py --self-test  EXIT=0
python3 scripts/test_release_assembly.py                     EXIT=0  (19 tests)
python3 scripts/test_embedded_browser_package.py             EXIT=0  (35 tests)
node --test <两个测试文件>                                     EXIT=0  (21 tests)
```

`act` 本机没有；用 `actionlint`（已装）+ YAML 解析 + 逐条实跑命令替代。
**没有**在真实 GitHub Runner 上跑过这个 job。

## 改动带来的后果（必须知道的一条）

**`scripts/run_p9_03_acceptance.py`（macOS 候选包）现在会被审计拒绝。**

它审计的二进制在 `.app/Contents/MacOS/` 里 → 触发产物侧自动识别 → 要求五份资源齐全 →
而它的包**确实只有 `local-executor`**。拒绝信息就是上面（c）那条。

这是**正确的拒绝，不是回归**：`docs/development/completed-task-wiring-audit-20260726.md`
第 291 行已经写明「P9-03/P9-04 产出的包不含这 5 份资源——这也正是它们仍标 🔍 待验收的原因」。
一个能通过缺资源包的门禁就是不会失败的门禁。P9-03 **不在 CI 里**，所以 main 不会因此变红。

想要改回去只需一行：去掉 `audit-production-package.mjs` 里的 macOS 自动识别
（`macOsBundleContaining`），让判据完全依赖显式 `--package-root`。本次**没有**这么做，
因为调用方开关式的门禁正是 `audit-release-bundle.mjs` 自己注释里点名批评的反模式。

## 失败矩阵（本次实跑覆盖到的）

| 场景 | 结果 |
| --- | --- |
| 正式配置 `bundle.resources` 为空 | 拒绝（`does not declare a required release resource: local-executor`） |
| 包里缺 `media-toolchain` / `motion-video-worker` / `material-video-worker` | 逐个点名拒绝 |
| 包里缺 `embedded-browser` / `local-executor` | 逐个点名拒绝 |
| 必需文件存在但**长度为 0** | 拒绝 |
| Windows 平台少声明任一份资源 | 拒绝 |
| 发版路径不再调用 `install_video_runtime` | 门禁拒绝（self-test） |
| 发版路径不再把包交给审计（`--package-root` 丢失） | 门禁拒绝（self-test） |
| 契约新增一份没人装 / 没人声明 / 没人构建的资源 | 门禁拒绝（self-test + 主模式实跑） |
| 发版配置写入方拿到不完整负载 | `ReleaseConfigurationRejected`，不会写出更小的包 |
| `--no-bundle` 的 E4-15 产物 | 不误判为包，审计行为不变（21 条测试守着） |

## 清理

| 项 | 结果 |
| --- | --- |
| 本次启动的进程 | 只有短命的 `node --test` / `python3` / `actionlint`，无常驻；`pgrep` 无残留 |
| 浏览器 / 模拟器 / Docker | 一个都没起 |
| `frontend/dist` | 未触碰（本次不跑任何构建） |
| `.local/eb-16/` | 未触碰 |
| `~/Library/Application Support/com.aventador.automationtool/` | 未读未写 |
| 临时文件 | 契约备份放 scratchpad，验证脚本用后即删；测试自身用 `mkdtemp` 并 `rm -rf` |
| Git | 未 `add`、未 `commit`（按要求） |

## 仍未覆盖的部分

- **发版命令本身没有实跑过一次完整发版。** 明确被要求不跑 `run_eb_16_acceptance.py`、
  不启动会重写 `frontend/dist` 的构建。已验证的是：模块导入通过、`--help` 正常、
  Windows 分支拒绝正确、被移动的五个步骤在 EB-16 里能解析到、配置写入方的行为被实跑断言。
  **未验证的是：这条命令端到端真的能产出一个可分发的 `.dmg`。** 下一次出厂应该用
  `python3 scripts/build_release_package.py --platform macos` 而不是 EB-16，那一趟就是它的首次真实验收；
- **EB-16 macOS 脚本本身没有重跑过。** 它被改了三处（导入改为委托、`build_executor_candidate`
  多传一个 `build_id`、审计多传 `--package-root`），`main()` 函数体未改。
  已用真实解释器 `import run_eb_16_acceptance` 验证符号全部可解析，但没跑过它；
- **Windows 侧一律未实跑。** `run_eb_16_windows_acceptance.py` 改了两处（配置写入方委托、
  审计多传 `--package-root`），只做了 `py_compile`；本机不是 Windows；
- **Windows 候选包路径（P9-04，在 CI 里）不受新判据保护。** Windows 的包就是一个普通目录，
  没有 `.app` 那种结构标记，无法从产物自动识别；P9-04 也不传 `--package-root`。
  这是一个真实的不对称，记在这里而不是掩盖；
- **`audit-release-bundle.mjs` 未改。** Executor 的 3 个必需文件仍写死在它里面，
  契约里 `local-executor` 的 `requiredFiles` 是空的（只断言目录非空），
  以免制造第二个声明源。把那 3 个文件也收进契约是后续的事；
- **契约没有 JSON Schema。** 结构靠 `test_release_assembly.py` 的形状断言守着，没有正式 schema；
- **视频三份资源仍无逐文件摘要校验**（浏览器有 EB-05 清单，视频这三份只有「必需文件存在且非空」）——
  这条在 `RELEASE-package-clean-rebuild.md` 里已经登记，本次未处理。
