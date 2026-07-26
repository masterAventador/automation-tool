# RESEARCH：演示机就绪度实测（Gatekeeper 拦截 + 资源占用峰值）

> 日期：2026-07-26
> 状态：调研实测，**未改任何生产代码、未提交、未重建正式包**
> 触发：`docs/development/PLAN-control-plane-delivery.md` §8 的第 1 条（Gatekeeper 未实测）
> 与第 7 条（目标机器情况未查证）是下周演示关键路径上最大的两个未知
> 被测物：`.local/eb-16/clean/cargo-target/release/bundle/dmg/自动化运营工具_0.1.0.dmg`
> （546 MiB，HEAD `e9ca4503` 构建，sha256 `8b7b99cc…b172b1e5`，只读使用，未重建）

---

## 0. 结论先行

| 问题 | 实测结论 |
| --- | --- |
| **Gatekeeper 会不会拦** | **会拦，而且拦得死。**首次双击直接被拒，弹窗**只有「完成」和「移到废纸篓」两个按钮，没有「仍要打开」出口** |
| **拦几次** | **用户可见的拦截只有 1 次**（外层 `.app`）。DMG 挂载不拦；内置 Chromium / 执行器 / ffmpeg / Node 不会各弹一次——但这一条是**推断，不是实测**，见 §1.6 |
| **能不能手工放行** | 能，但**必须事后进系统设置**，不能在弹窗里放行。领导本人做不了，必须我们预置时做完（§1.7） |
| **24 GB 内存够不够** | **够，而且余量充足。**实测整条视频链路并发峰值 **2.4 GiB**，后端栈（PostgreSQL + Control Plane）合计 **0.13 GiB**。全栈最坏估算约 **9～11 GiB**，24 GB 不是瓶颈（§2.6） |
| **真正的瓶颈** | 不是内存，是 **①Gatekeeper 签名/公证**，②**如果用 Docker 跑 PostgreSQL**，虚拟机开销会吃掉几个 GB 且完全可以避免（§2.4） |

**一句话**：内存不用担心；**下周能不能演，取决于 Gatekeeper 那一关有没有在交付前由我们亲手过掉。**

---

## 1. 任务一：Gatekeeper 实测

### 1.1 测试方法（关键：必须人为加 quarantine）

本机构建的产物**天生没有 `com.apple.quarantine` 扩展属性**，直接双击一定放行，测出来的结果是假的。实测确认原始 DMG 的属性只有：

```text
com.apple.FinderInfo: deviddsk
com.apple.diskimages.recentcksum: …
com.apple.provenance:
```

因此把 DMG 拷贝到临时目录，清空属性后按 Safari 下载的格式人为打上隔离属性：

```bash
cp <原DMG> /tmp/probe/自动化运营工具_0.1.0.dmg
xattr -c /tmp/probe/自动化运营工具_0.1.0.dmg
xattr -w com.apple.quarantine "0081;$(printf %x $(date +%s));Safari;" /tmp/probe/自动化运营工具_0.1.0.dmg
```

拷贝后 sha256 与原件一致（`8b7b99cc…`），**原件全程未被修改**（测试结束复查属性与校验和均未变）。

本机环境：**Apple M5 Max / 18 核 / 128 GB / macOS 26.4.1 (25E253) / arm64**。
Gatekeeper 处于开启状态（`spctl --status` → `assessments enabled` + `developer id enabled`）。

### 1.2 逐环节结果

| 环节 | 结果 | 证据 |
| --- | --- | --- |
| **① 双击挂载 DMG** | ✅ **不拦**，约 2 秒挂载完成，无任何提示 | `open <dmg>` 后 `/Volumes/自动化运营工具` 出现；挂载参数含 `quarantine` 标志 |
| **② 从 DMG 拖到「应用程序」** | ✅ **不拦**，Finder 正常复制 | 用 Finder `duplicate` 复制（等价用户拖拽），1.1 GB / 3427 文件复制成功 |
| **③ 隔离属性是否传播** | ⚠️ **全量传播**。**3427 个文件全部**带上 `com.apple.quarantine: 0081;…;Safari;` | 逐文件统计，命中数 = 文件总数 = 3427 |
| **④ 首次打开 .app** | ❌ **被拦，无法打开** | 见 §1.3 弹窗原文；系统写入 `/var/db/SystemPolicyConfiguration/.LastGKReject` |
| **⑤ 拦截是否可绕过** | ❌ 弹窗内**没有任何放行出口** | 只有「完成」「移到废纸篓」两个按钮 |

**结论：DMG 和拖拽都顺畅，卡死在最后一步——双击打开。** 这是最糟糕的失败位置：领导会以为装好了，点开才发现用不了。

### 1.3 拦截弹窗原文（实测截图转录）

```text
未打开“自动化运营工具”

Apple 无法验证“自动化运营工具”是否包含可能危害
Mac 安全或泄漏隐私的恶意软件。

              [ 完成 ]   [ 移到废纸篓 ]
```

- 「移到废纸篓」是**蓝色主按钮**（默认高亮），「完成」是次要按钮；
- **没有「仍要打开」/「打开」选项**——这是 macOS 15 以后的新行为，旧版那种「右键 → 打开」的简易绕过已经不存在；
- 危险点：**默认高亮的是「移到废纸篓」**。领导如果习惯性回车，会直接把 1.1 GB 的 App 删进废纸篓。

系统侧留痕（`plutil -p /var/db/SystemPolicyConfiguration/.LastGKReject`）：

```text
"DirectApproval"     => false
"IsExecutionPath"    => true
"PromptType"         => 6
"XProtectMalwareType"=> 0
"Timestamp"          => 2026-07-26 03:03:15 +0000
```

`XProtectMalwareType = 0` 说明**不是**被判定成恶意软件，纯粹是「无法验证开发者」。

### 1.4 `spctl` 评估结论

| 对象 | `spctl -a -vvv` 结果 |
| --- | --- |
| DMG（`-t open` / `-t install`） | `rejected` — `source=no usable signature` |
| `.app`（`-t exec`） | `rejected`（`assessment:verdict = false`） |
| 内置 Chromium `.app` | `code has no resources but signature indicates they must be present` |
| 冻结执行器 `automation-tool-executor` | `rejected` |
| `media-toolchain/bin/ffmpeg` | `rejected` |
| `motion-video-worker/runtime/node` | `rejected (the code is valid but does not seem to be an app)`<br>**`origin=Developer ID Application: Node.js Foundation (HX7739G8FX)`** |

注意最后一条：**`node` 是唯一签名合格的**（上游 Node.js 基金会的 Developer ID），它被 `rejected` 只是因为「不是 App 包」，不是签名问题。

### 1.5 `codesign` 签名全景（288 个 Mach-O 二进制逐个扫描）

外层 `.app`：

```text
Identifier=com.aventador.automationtool
Format=app bundle with Mach-O thin (arm64)
CodeDirectory v=20400 … flags=0x2(adhoc) …
Signature=adhoc
TeamIdentifier=not set
Sealed Resources version=2 rules=13 files=2784
```

`codesign --verify --deep --strict` → `valid on disk` + `satisfies its Designated Requirement`，
即**签名结构本身是完整有效的，只是身份不被 Apple 认可**。

包内 288 个 Mach-O 可执行文件的签名分布：

| 签名类型 | 数量 | 分布 |
| --- | ---: | --- |
| `flags=0x2(adhoc)`，`TeamIdentifier=not set` | 273 | material-video-worker 190、local-executor 82、App 主程序 1 |
| `flags=0x20002(adhoc, linker-signed)` | 14 | 内置 Chromium 12（主程序 + Framework + 4 个 Helper + 3 个 dylib + crashpad 等）、ffmpeg、ffprobe |
| `flags=0x10000(runtime)`，**Developer ID** | **1** | `motion-video-worker/package/runtime/node`（Node.js Foundation，HX7739G8FX，带时间戳，启用 hardened runtime） |

**两个对后续公证工作影响很大的发现：**

1. **主 App 没有启用 hardened runtime**（`flags` 只有 `0x2`，缺 `0x10000`），也**没有任何 entitlements**（`codesign -d --entitlements -` 无输出）。
   而**公证的前置条件就是 hardened runtime**。也就是说，就算 Developer ID 证书明天到手，
   **也不是「换个签名身份重签一次」就能公证**——构建脚本要加 `--options runtime`，
   还要为内置 Chromium 补 JIT / unsigned-executable-memory 之类的 entitlements，再逐个重签 287 个二进制。
   `PLAN` §7.1 把 T4 估成 0.5～2 天，**这个估算偏乐观**；
2. **287/288 是 ad-hoc**，全部需要换成 Developer ID 重签。唯一不用动的是上游的 `node`。

### 1.6 嵌套组件会不会各弹一次 —— ⚠️ 这是推断，不是实测

**先说清楚：这一条我没有实测。** 实测需要放行后真正启动 App 并跑完整链路，
而本轮被要求停止一切会弹图形窗口的操作，因此下面是**基于静态证据的推断**。

**推断结论：用户可见的拦截只有 1 次（外层 App），嵌套组件不会各弹一次。**

四条静态依据：

| # | 依据 | 性质 |
| --- | --- | --- |
| 1 | 三处子进程启动点全部是 **Rust `Command::new(...)` 直接 exec**，不走 LaunchServices：<br>`executor_manager.rs:712`（执行器）、`local_video_orchestrator.rs:1232`（视频 Worker）、`lib.rs:669`（ffmpeg）。内置 Chromium 由 Playwright 以 Python `subprocess` 拉起，同样不走 LaunchServices | **实测**（源码检索） |
| 2 | §1.3 那个「未打开…」弹窗是 **LaunchServices 打开 App 包**时产生的（`PromptType=6`）。`posix_spawn`/`execve` 出来的子进程不经过这条用户确认流程 | 机制推断 |
| 3 | 288 个嵌套二进制**全部在外层 `.app` 的签名密封范围内**（`Sealed Resources … files=2784`，`--verify --deep --strict` 通过）。Gatekeeper 是按**最外层 bundle** 做评估的 | **实测** |
| 4 | 隔离属性是**整包一致**的（3427 个文件同一个 `0081;…;Safari;` 值），清除时 `xattr -dr` 一次性全清，不存在「逐个组件残留」 | **实测** |

**但必须承认的不确定性**：我确实观察到一个矛盾信号——从终端直接 exec 主二进制时，
进程起来了（PID 存活）但常驻内存只有 32 KB，同时系统写了一条 Gatekeeper 拒绝记录。
这个状态到底是「已放行正常启动」还是「被挂起未初始化」，**没有查清**。

**因此：§1.6 的结论必须在目标机器上用一次真实启动来验证，不能只靠本文。**
具体验证方法见 §3。

### 1.7 手工放行的确切步骤 —— ⚠️ 系统设置路径未逐步点击验证

有两条路，**强烈建议走第二条**。

#### 路线 A：系统设置放行（领导可能自己撞上的路径）

弹窗里没有出口，必须事后去系统设置。步骤：

| 步 | 操作 | 界面文案 |
| --- | --- | --- |
| 1 | 先双击一次 App，让它被拒（**这一步不能省**，系统要先记下这次拒绝才会显示放行按钮） | 「未打开"自动化运营工具"」→ 点**「完成」**（**千万别点「移到废纸篓」**） |
| 2 |  菜单 → 系统设置 | — |
| 3 | 左栏 → **隐私与安全性** | — |
| 4 | 右侧滚到最下面的 **安全性** 区块 | 「"自动化运营工具"已被阻止使用，因为它来自身份不明的开发者。」 |
| 5 | 点该行右侧 **「仍要打开」** | — |
| 6 | Touch ID 或输入登录密码 | — |
| 7 | 再弹一次确认框，点 **「打开」** | — |

> **诚实标注**：第 2～7 步的按钮文案和层级**我没有真的点过**（本轮被要求停止 GUI 操作）。
> 支撑证据是：系统确实写了 `/var/db/SystemPolicyConfiguration/.LastGKReject`
> （`DirectApproval=false`），而这个文件正是「系统设置里显示『仍要打开』按钮」所依赖的记录。
> 换句话说，**放行入口一定存在**，但**具体几步、文案是否逐字如上，需要在目标机器上核对**。

#### 路线 B：命令行一次清干净（**推荐我们预置时用这条**）

```bash
xattr -dr com.apple.quarantine /Applications/自动化运营工具.app
```

- 一条命令清掉全部 **3427 个文件**的隔离属性（传播范围已实测）；
- 清掉之后 LaunchServices **根本不会去问 Gatekeeper**，所以不存在「重启后又被拦」；
- 比路线 A 更彻底：路线 A 是否会递归清除嵌套文件的隔离属性，**我没有验证**。

**两条路的共同前提**：App 必须**先放到最终位置再放行**。
放行记录是跟着这个路径和这份签名走的，**放行后再移动或重装 App 会重新被拦**。
所以给领导的说明里必须写死一条：**不要移动、不要重命名、不要重新安装**。

### 1.8 对下周演示的直接影响

| 事项 | 结论 |
| --- | --- |
| 领导能不能自己装 | **不能。**必须我们预置时把 App 装好、放行好、验证好 |
| 能不能通过微信/邮件把 DMG 发给他自己装 | **不能。**这样传过去一定带隔离属性，他会撞上 §1.3 那个弹窗，而且默认按钮是删除 |
| 放行后重启还有效吗 | 路线 B 有效（属性已物理清除）。路线 A **未验证** |
| 有没有 Developer ID 就能解决 | 能，但**不是 0.5 天的活**。缺 hardened runtime + entitlements + 287 个二进制重签，见 §1.5 |
| 最保险的做法 | 预置时用路线 B 清属性 → 完整跑一遍链路 → **重启电脑再跑一遍** → 之后不要动 App |

---

## 2. 任务二：资源占用峰值实测

### 2.1 方法与边界

- **视频链路**：按 `docs/development/FIX-material-worker-audio-assets.md` §3 的做法，
  向**正式包里那份**冻结 Worker（`.app/Contents/Resources/material-video-worker/package/…`，
  sha256 `556f7f2b…`）的 stdin 写一条与 `local_video_orchestrator.rs::write_bootstrap` 同构的
  bootstrap，读回 ready 事件，再用**正式包里那份内置 Chromium**（无头）按真实用户路径操作它拉起的 WebUI；
- **后端栈**：隔离 Compose 项目 `automation-tool-demo-readiness-probe`，
  磁盘卷（**不是** `postgres-test` 那种 tmpfs，tmpfs 会把数据算进内存，测出来是假的），
  端口 55432，跑完 35 个 Alembic 迁移，再用正式入口 `automation-tool-control-plane` 起服务；
- **保护措施**：Control Plane 的 `local_app()` 会写 App 私有目录，因此**全程使用临时 HOME**。
  实测 bootstrap 授权文件落在临时 HOME 里，真实的
  `~/Library/Application Support/com.aventador.automationtool`（含抖音登录态）**675 条目未变**；
- **采样**：`ps` 每 0.5 秒扫全表，按进程树 + 命令模式跟踪，记录每进程 RSS 峰值与逐时刻合计。

⚠️ **两个必须声明的偏差**：

1. **本机是 Apple M5 Max / 18 核 / 128 GB，目标机是 M4 Air / 约 10 核 / 24 GB。**
   内存数字可直接对照，**耗时不能**——见 §2.5；
2. 测量期间**同一台机器上有另一个会话在并行工作**（它自己的 Worker 稳态约 232 MiB）。
   下面的数字已按 PID 归属拆分，父会话的占用**未计入**本次结论。

### 2.2 视频制作链路（真实成片，已验证产物）

**成片是真的**，不是"跑通了"：

```text
ffprobe outputs/material-result.mp4
  index=0  codec_name=h264  codec_type=video  width=1080  height=1920
  index=1  codec_name=aac   codec_type=audio
  duration=6.700000   size=214429

subtitle
  1  00:00:00,100 --> 00:00:01,950   演示机资源占用实测
  2  00:00:02,325 --> 00:00:06,112   这条视频用于测量峰值内存与渲染耗时
```

任务观测文件（Rust 桥实际读取的那份）：

```json
{"status":"succeeded","progressPercent":100,"outputFile":"material-result.mp4",
 "renderJobId":"d1f4c491-1fed-492c-8a8f-98c522a2745f","revision":9,
 "workerTaskId":"d0855517-5d60-42b9-8023-2f4955fb8412","failureCode":null}
```

**各进程常驻内存峰值：**

| 进程 | 峰值 RSS | 说明 |
| --- | ---: | --- |
| ffmpeg #1 | **513.1 MiB** | 渲染期 **4 个 ffmpeg 并发** |
| ffmpeg #2 | 478.8 MiB | |
| ffmpeg #3 | 417.8 MiB | |
| ffmpeg #4 | 359.1 MiB | |
| material-video-worker（WebUI 子进程） | **461.9 MiB** | Streamlit + moviepy，实际干活的那个 |
| 内置 Chromium 主进程 | 237.8 MiB | 本次用它驱动 WebUI |
| 内置 Chromium renderer ×2 | 306.3 / 95.0 MiB | |
| 内置 Chromium gpu | 155.4 MiB | |
| 内置 Chromium utility ×3 | 99.5 / 90.0 / 73.8 MiB | |
| 内置 Chromium crashpad ×2 | 9.5 / 9.4 MiB | |
| material-video-worker（主进程） | 39.5 MiB | 只做协议转发 |
| **各进程峰值简单相加** | **3347.4 MiB** | 上界，实际不会同时到峰 |
| **实测同一时刻并发峰值** | **2443.2 MiB** | ← **这个才是真正要看的数** |

其中内置 Chromium 全家合计约 **1077 MiB**。

**渲染耗时：15.0 秒**（从点「生成视频」到观测文件 `status=succeeded`），
含 TTS 配音、字幕生成、3 段素材拼接、1080×1920 编码。

### 2.3 后端栈（PostgreSQL + Control Plane）

| 组件 | 常驻内存 | 说明 |
| --- | ---: | --- |
| PostgreSQL 18.4（跑完 35 个迁移，空闲） | **32.4 MiB** | `docker stats` 容器口径 |
| Control Plane（`automation-tool-control-plane`，uvicorn） | **93.5 MiB** | Python 进程本体 |
| （`uv run` 包装进程） | 30.8 MiB | **生产不存在**——PyInstaller 打包后没有这一层 |
| **后端栈合计** | **≈ 126 MiB** | |

健康检查实测通过：`{"status":"ok","service":"control-plane","version":"0.1.0"}`，2 秒内就绪。

**这一项完全不构成压力。** PLAN §3 出路一（预置本机 PostgreSQL）在内存上毫无风险。

### 2.4 ⚠️ 但如果用 Docker 跑 PostgreSQL，账要重算

本机 Docker 走的是 Colima（Apple Virtualization.framework）。宿主机侧实测：

```text
41147.8 MiB  com.apple.Virtualization.VirtualMachine
26044.7 MiB  com.apple.Virtualization.VirtualMachine
  165.7 MiB  limactl hostagent
   74.7 MiB  limactl usernet
```

虚拟机被配置了 **31.28 GiB** 内存上限——**在一台 24 GB 的机器上根本配不出来**。

**结论：演示机绝对不要用 Docker 跑 PostgreSQL。**
PostgreSQL 自己只要 32 MiB，套一层虚拟机要几个 GB 起步，纯属白送。
这条**独立地印证了 PLAN §3「出路一（本机原生 PostgreSQL）」是对的**，
而且理由比 PLAN 里写的「领导不用装 Docker」更硬：**24 GB 机器上装 Docker 跑这个栈是资源灾难。**

### 2.5 磁盘占用

| 项目 | 体积 |
| --- | ---: |
| DMG 安装包 | **546 MiB**（装完可删） |
| 安装后 `.app` | **1159 MiB** / 3427 文件 |
| ├ material-video-worker | 467 MiB |
| ├ embedded-browser（Chromium 149.0.7827.55） | 345 MiB |
| ├ local-executor | 177 MiB |
| ├ motion-video-worker | 108 MiB |
| └ media-toolchain（ffmpeg/ffprobe） | 43 MiB |
| PostgreSQL 数据目录（35 个迁移后的空库） | **49 MiB** |
| **单次视频工作区** | **143 MiB** ← 注意：**每做一条视频就留一份** |
| 单条成片 | 214 KiB |
| App 私有目录（含抖音登录态） | 65 MiB |
| **预置完成后基线占用** | **≈ 1.3 GiB** |

**要提醒的一条**：每个视频任务的私有工作区 143 MiB **不会自动消失**。
演示前反复排练 20 次就是 2.9 GiB。预置和排练后记得清理任务目录。

### 2.6 24 GB 够不够 —— 结论

**够，而且余量很充足。**

最坏情况（视频渲染 + 浏览器 RPA 同时进行）的合计估算：

| 组成 | 内存 | 来源 |
| --- | ---: | --- |
| macOS 26 系统基线 | 4～6 GiB | 常识区间，**未实测** |
| App 壳（Tauri + WKWebView） | 0.2～0.5 GiB | **未实测**，见 §3 |
| PostgreSQL（原生） | 0.03 GiB | **实测** |
| Control Plane | 0.10～0.15 GiB | **实测**（PyInstaller 后略增） |
| 冻结执行器 | 0.15～0.3 GiB | **未实测**，见 §3 |
| 内置 Chromium（抖音页，比本次的 Streamlit 页重） | 1.5～2 GiB | 本次 Streamlit 页实测 1.08 GiB，按 1.5～2 倍估 |
| 视频渲染链路 | 2.4 GiB | **实测** |
| **合计** | **≈ 8.4～11.4 GiB** | |

**24 GB 下剩余 12～16 GiB，不紧张。** 判断依据：

- 唯一的大户是视频渲染的 2.4 GiB，且它是**短时峰值**（本机 15 秒）；
- 后端栈只有 0.13 GiB，可以忽略；
- **没有任何一项接近让 24 GB 吃紧的量级。**

**演示时要避开的事：**

1. **不要装 Docker/Colima 跑 PostgreSQL**（§2.4）——这是唯一能把 24 GB 吃穿的做法；
2. **渲染视频时别同时开别的大程序**——不是因为内存，是因为 **CPU**。
   渲染期有 4 个 ffmpeg 并发，M4 Air 只有约 10 核且**无风扇**，
   同时开 Chrome 一堆标签页 / IDE / 视频会议会明显拖慢渲染；
3. **腾出至少 5 GiB 磁盘余量**，并在排练后清理视频任务工作区（每次 143 MiB）。

### 2.7 ⚠️ 耗时不能直接搬到目标机

本次 **15.0 秒**是在 **M5 Max / 18 核**上测的。目标机是 **M4 Air / 约 10 核 / 被动散热**：

- 渲染是 CPU 密集且并发 4 个 ffmpeg，核数少接近线性变慢，**预计 30～50 秒**；
- M4 Air 无风扇，**持续负载会降频**。单条 6 秒视频不至于触发，但连续渲染多条会；
- **这个数字必须在目标机上实测一次**，不要拿本文的 15 秒对客户承诺。

---

## 3. 我没能验证的部分（逐条）

按重要性排序：

1. **放行之后 App 到底能不能完整跑起来——没验证。**
   本轮只验证到「被拦截」，没有走完「放行 → 启动 → 五份资源全部拉起」。
   §1.6「嵌套组件不会各弹一次」是**静态推断**。
   **这是剩下最大的未知，也是最该先做的一件事**：在第二台 Mac 上装一次、用路线 B 放行、
   把内置 Chromium、执行器、ffmpeg、Node 四条子进程全部实际拉起一遍。半小时能测完。
2. **系统设置的放行路径（§1.7 路线 A）没有逐步点击验证。**
   放行入口确定存在（`.LastGKReject` 已写入），但按钮文案与步数需在目标机核对。
3. **App 壳（Tauri）自身内存没测。** 本轮被要求不启动带界面的 App。
   §2.6 里的 0.2～0.5 GiB 是估计值。可用隐藏窗口的测试配置补测，但那会重写 `frontend/dist`，本轮明确禁止。
4. **冻结执行器的运行态内存没测。** 直接启动会因缺少握手 bootstrap 立即退出
   （实测输出 `Local Executor bootstrap is rejected`），需要 Control Plane 配合完成握手才能测到稳态。
5. **内置 Chromium 打开抖音真实页面的内存没测。** 本次测的是本机 Streamlit 页（1.08 GiB）。
   抖音页面重得多，§2.6 里按 1.5～2 倍估的。
6. **动效成片（Node Worker）链路没跑。** 只确认了 `node` 二进制是 Developer ID 签名 + hardened runtime。
   它的渲染沙箱内存与耗时未测。
7. **macOS 版本差异没覆盖。** 本机 26.4.1；目标机「可能略旧」。
   Gatekeeper 在 macOS 15/26 之间行为一致，但**如果目标机是 macOS 14 或更早，
   「右键 → 打开」的旧绕过可能还在**，那样反而更容易放行。目标机的确切版本值得先问清楚。
8. **没在真正的第二台 Mac 上测过。** 全部用人为打隔离属性在本机模拟。
   这个模拟对 Gatekeeper 是等价的（判定依据就是隔离属性 + 签名），
   但**不覆盖**目标机可能有的 MDM 策略、FileVault 差异、更严的安全设置。
   已知目标机无 MDM 管控，风险较低。

---

## 4. 资源清理复查

本轮所有临时资源均已清理并复查：

| 路径 | 状态 |
| --- | --- |
| 挂载的 DMG | ✅ 已卸载，`/Volumes/` 只剩 `Macintosh HD` |
| `/Applications/自动化运营工具.app` 测试副本 | ✅ 已删除 |
| 临时 DMG 拷贝与截图 | ✅ 已删除 |
| 隔离 Compose 项目 `automation-tool-demo-readiness-probe` | ✅ 容器 / 卷 / 网络全部 `down --volumes` 移除，复查无残留 |
| 端口 55432 / 8765 | ✅ 已释放 |
| 本次启动的 Worker / Chromium / Control Plane 进程 | ✅ 全部退出，无残留 |
| 其他项目的 Docker 资源（`agent-platform-*` 13 个容器） | ✅ **未触碰**，运行中数量不变 |
| **原始 DMG** | ✅ **未修改**，sha256 仍为 `8b7b99cc…b172b1e5`，无隔离属性 |
| **`.local/eb-16/clean` 产物** | ✅ 只读使用，无隔离属性，内容未变 |
| **`~/Library/Application Support/com.aventador.automationtool`（抖音登录态）** | ✅ **675 条目未变**；测试全程使用临时 HOME；另已做只读备份 |
