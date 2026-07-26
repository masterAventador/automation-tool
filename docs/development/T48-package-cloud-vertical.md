# T48 正式包 × 云端后端纵向连通验收

> 状态：🛑 已中止（登录链路未验证。方案 A 单次尝试失败，窗口在用户屏幕上可见约 3.5 分钟，
> 按硬性中止条件立即停止，未重试）
>
> 日期：2026-07-26
>
> 提交：本文件所在的 T48 独立提交

## 任务

T44 单独验过「正式包能装能过 Gatekeeper」，C10 系列单独验过「云端后端接口正常」。
**两半从来没有接上过。** 本任务是纯验证任务，不改产品代码：证明 T44 产出的那个签名公证包
能连上 `https://at.xuanbai.tech` 并完成产品账号登录、自动设备绑定。

任何一步失败都如实记录失败点，不得为了让流程通过而修改产品代码。

## 隔离约定（强制）

真实用户目录 `~/Library/Application Support/com.aventador.automationtool/` 里有人工扫码得到的
抖音登录凭据，本任务全程不得读写。正式包的 bundle identifier 与该目录完全同名
（`codesign -dv` → `Identifier=com.aventador.automationtool`），并且 Demo Profile 启动时会对
数据根目录执行 `ensure_private_directory`（`chmod 0700` + 建 `profiles/`），
所以**不隔离就一定会改动真实目录**。

隔离手段：`open --env HOME=<隔离目录>`。可行性先证后用，不靠推断：

| 证据 | 结论 |
| --- | --- |
| `dirs-sys-0.5.0/src/lib.rs` `home_dir()` 先读 `$HOME`，再回落 `getpwuid` | Rust 侧认 `$HOME` |
| `dirs-6.0.0/src/mac.rs` `data_dir() = home_dir()/Library/Application Support` | 数据根跟随 `$HOME` |
| `tauri-2.11.5/src/path/desktop.rs:247` `app_data_dir() = dirs::data_dir()/identifier` | Tauri 不额外解析 |
| 自建 `EnvProbe.app` + `open -g -j --env HOME=... -a` → 落盘 `HOME=/tmp/t48-iso-probe` | LaunchServices 确实透传 `--env` |

`scripts/run_u9_06_acceptance.py` 全程未运行（它会清空真实数据目录）。

## 已完成并取证的步骤

### 1. 正式包的部署 Profile 确实是 Demo（编译期烧进二进制）

`.local/t44-release-verify/cargo-target/release/build/automation-tool-desktop-cc2454d9c6d6b03c/output`
中 build script 注入：

```text
cargo:rustc-env=AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_PAYLOAD=eyJ2ZXJzaW9uIjoi…
cargo:rustc-env=AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_SIGNATURE=tCdOcno8…
cargo:rustc-env=AUTOMATION_TOOL_COMPILED_DEPLOYMENT_PROFILE_VERIFYING_KEY=e1Sitm…
```

payload 解开是：

```json
{"version":"customer-demo-profile.v1","profile":"demo","profileId":"demo-xuanbai",
 "baseUrl":"https://at.xuanbai.tech","allowedHosts":["at.xuanbai.tech"]}
```

并且这三个字面量在**分发出去的那个二进制**里逐字节存在（不是只在构建目录里）：
`.../自动化运营工具.app/Contents/MacOS/automation-tool-desktop` 偏移 `11645364`（payload）、
`11645568`（签名）、`11645654`（公钥）。

据此 `DeploymentProfile::load()` 走 `verify_signed` 分支 → `kind = Demo` →
`requires_product_account()` 为真 → 未登录必须停在登录页。

> 排查记录：最初用 `strings | grep customer-demo-profile` 与 `grep xuanbai` 都查不到，一度怀疑
> 发出去的是 Local Profile 包。实际原因是 profile 只以 base64url 形态存在，明文 JSON 从不
> 出现在二进制里；把 payload 原文拿去精确匹配才定位到。这条误判过程留档，避免下次重走。

### 2. 从 DMG 安装 + Gatekeeper 判定（带 quarantine，含一条未验证项）

DMG 进入本步骤时已带 `com.apple.quarantine: 0083;0;Safari;`（模拟下载），
`sha256 = e6d83d4e0ab47ebf1d4448de1e1bad109fd69ebc83de384b643b77a97d143d14`。

`hdiutil attach -nobrowse -readonly` → `ditto` 到隔离目录，quarantine 随之传播到安装副本
（`com.apple.quarantine: 0283;00000000;;`）。对**带 quarantine 的安装副本**判定：

```text
spctl -a -vvv --type execute → accepted
                               source=Notarized Developer ID
                               origin=Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)
xcrun stapler validate       → The validate action worked!
codesign -dv --verbose=4     → Identifier=com.aventador.automationtool
                               flags=0x10000(runtime)
                               TeamIdentifier=HK56FS93AD
                               Runtime Version=26.5.0
```

#### ❌ 未验证项：Gatekeeper 首次启动同意框没有实拍

判定取证后、启动前执行了 `xattr -dr com.apple.quarantine`。原因是首次启动带 quarantine 的 App
会弹出 Gatekeeper 同意对话框，而本机用户正在使用，本任务禁止抢占前台。

`spctl` 判定用的正是系统自己那套评估，**放行结论可信**；但——

> **「客户双击后在同意框上点一次 Open」这一下人工交互，本任务没有走过，也没有任何历史任务走过。**
> 它离真实客户路径就差这一下，而演示当天客户要做的恰恰就是这一下。

这条**不计入已通过**，已挂到 `docs/demo-preflight-checklist.md` B3，
必须由人在演示机上手工完成，不能用 `spctl` 结论顶替。

### 3. 隔离启动成功，真实目录零改动

```text
open -n -g -j --env HOME=<隔离HOME> -a <隔离HOME>/Applications/自动化运营工具.app
```

- 进程起来并进入 Tauri 事件循环（`sample` 栈顶 `tauri::app::App::run` → `-[NSApplication run]`）；
- WebKit 三件套（WebContent / Networking / GPU）随之拉起，说明 WKWebView 已创建；
- 隔离数据目录按 Demo Profile 落在 `profiles/demo-xuanbai/` 下，且 Rust `setup()` 全程跑完：
  `device-identity-ed25519-v1`、`local-executor/executor-id-v1`、`app-updates/`、
  `embedded-browser-profiles/douyin`、`video-workspaces-v1/` 等全部生成；
- **真实用户目录 `~/Library/Application Support/com.aventador.automationtool/` 的 mtime
  在启动前后均为 `2026-07-26T02:37:42`，且未出现 `profiles/` 子目录。** 抖音 Profile 未被触碰。

### 4. 云端基线（登录前）

`at.xuanbai.tech` 后端 `automation-tool-demo` 库：

| 表 | 登录前 |
| --- | --- |
| `installations` | **0 行** |
| `device_credentials` | **0 行** |
| `users` | 1 行，`active` / revision 1 / credential_version 1 |
| `account_audit_events` | `account.created` 1、`login.failed` 3、`login.succeeded` 5、`session.refreshed` 3、`session.reuse_detected` 3 |

Nginx `access.log` 中来自本机公网 IP 的请求数为 `0`。

## 5. 最有价值的发现：云端从未被真实 App 走通过

单独成段，因为它说明的不只是「本任务还没验」：

`account_audit_events` 里已经有 **5 次 `login.succeeded`**，但 `installations` 表**始终是 0 行**，
`device_credentials` 也是 0 行。

产品设计上，登录成功会复用 I2 设备密钥证明把 Installation 原子绑定到当前账号（U9-05）。
**登录成功 5 次而 Installation 一个都没有，只可能是这 5 次登录都没有经过真实 App 的完整链路**
——它们停在接口层（curl 之类）。

也就是说：**截至本任务，云端后端的所有验证都止步于 HTTP 接口，没有任何一次穿过
「正式包 → Rust 网络桥 → 认证注入 → 设备证明 → 绑定」这条链**。这正是本任务存在的理由，
也正是「两半各自验过、接缝从未验过」的量化证据。

## 6. 方案 A 单次尝试：失败，已按硬性中止条件停止

> **⛔ 本节的两条结论已被 T54 推翻，见 `docs/development/T54-main-window-accessibility.md`。**
> 原始记录保留不改写，但读之前必须知道：
>
> 1. 「正式包的主窗口不向辅助功能暴露」**缺乏证据支撑**。本节给出的"矛盾判据"，
>    在 Chrome、VS Code、ghostty 以及一个 30 行的 AppKit 对照程序上同样"命中"。
>    退化只发生在 `System Events`（AppleScript）这一层——它把真实 role 为 `AXWindow`
>    的元素报成 `AXApplication`，正好就是本节记的那个现象；同一时刻直接调 AX API 一切正常。
>    该退化的触发条件尚未定位（不是"锁屏必现"），详见 T54 §3.1。
> 2. 「窗口在用户屏幕上可见约 3.5 分钟」**也不成立**。那段时间屏幕是锁屏/屏保状态，
>    没有人看见过那个窗口。因此下面 §「给后续排查的复现说明」里那条加粗警告的前提是错的。

### 做了什么

用一个一次性 Swift 程序（`t48launch`）：先起轮询，再 `open -n -g`（后台、不夺焦点，**不带 `-j`**）
启动 App，窗口一进辅助功能树立刻挪到所有显示器之外；同时记录三个时间戳
（窗口进入窗口服务器在屏列表 / 进入辅助功能树 / 挪走完成），好用实测数字回答
「到底可见了多久」，而不是给个保证。

### 结果：失败

```text
displays=0 union=(inf, inf, 0.0, 0.0) parkTarget=(inf, inf)
open rc=0
pid=37769 discoveredAt=+66.1ms
no AXWindow appeared within 90s
```

两处都错了：

1. **`CGGetActiveDisplayList` 在本进程返回 0 个显示器**，所以挪窗目标算成了 `(inf, inf)`
   ——一个无效坐标。后来单独验证：同一进程里 `CGWindowListCopyWindowInfo` **可以**正常工作
   （能列出 176 个窗口），只有显示器枚举拿不到。挪窗目标本该从窗口自身 bounds 推，
   不该依赖显示器枚举。
2. **更根本的一条：窗口根本没有进入辅助功能树，等了 90 秒也没有。**

### 我之前的判断是错的

我原先认定「隐藏（`-j`）导致窗口不进 AX 树」。**这个结论不成立。** 去掉 `-j` 之后，
辅助功能树完全一样地退化：

- `AXWindows` 数组长度 1，但那一项的 role 是 `AXApplication`，不是 `AXWindow`；
- `AXMainWindow` / `AXFocusedWindow` 同样返回 `AXApplication`；
- 从应用元素起 3000 节点广度优先扫描，命中的全是菜单（`AXMenuItem` 2580、`AXMenu` 219、
  `AXMenuBarItem` 154、`AXApplication` 24、`AXMenuBar` 23），**没有任何 `AXWindow` /
  `AXWebArea` / `AXTextField`**，且 `AXApplication` 自嵌套约 24 层。

同一时刻窗口服务器却明确报告窗口存在且在屏：

```text
CGWindow number=51867 layer=0 alpha=1 onscreen=1 bounds={X=960, Y=485, Width=1280, Height=801}
```

**所以这是产品侧的一个事实：正式包的主窗口不向辅助功能暴露。** 它既是无障碍缺陷
（读屏用户无法使用该 App），也意味着任何基于辅助功能的自动化都驱动不了这个包。
根因未定位（Tauri 2.11.5 / tao 在 macOS 26 上的 AX 接线嫌疑最大），**本任务不改产品代码，不深挖**。

#### 给后续排查的复现说明

> ⚠️ **先读这段再动手：用正式包复现这个缺陷，就必然要启动它，也就必然在屏幕上造出一个
> 1280×801 的可见窗口。** 本任务已经为此付出过一次约 3.5 分钟的可见代价，不要重复付第二次。
> 若这台机器上有人在用，先确认对方知情，或改用开发构建（`tauri.dev.conf.json`）复现——
> 缺陷若出在 tao/Tauri 的 AX 接线，开发构建同样能观察到，且开发构建可以配隐藏窗口。

判据不需要专门工具，两条命令对照即可（`<pid>` 为 App 进程）：

- 辅助功能侧：`osascript -e 'tell application "System Events" to tell (first process whose unix id is <pid>) to return {visible, count of windows}'`
  —— 缺陷表现为窗口数 `0`，而 `visible` 为 `true`。
- 窗口服务器侧：用 `CGWindowListCopyWindowInfo` 按 `kCGWindowOwnerPID` 过滤
  —— 同一时刻能看到 `layer=0 onscreen=1` 的真实窗口和 bounds。

**两侧结论矛盾**（窗口服务器有、辅助功能没有）就是这个缺陷。若要看退化结构本身，
从 `AXUIElementCreateApplication(pid)` 取 `kAXWindowsAttribute`，会拿到长度为 1、
但 role 是 `AXApplication` 而非 `AXWindow` 的数组，且沿 `AXChildren` 自嵌套约 24 层。

### 代价：窗口在用户屏幕上可见约 3.5 分钟

这是本次尝试的实际伤害，如实记录：窗口在 `(960, 485)` 尺寸 `1280×801` 真实显示，
从创建到我发现并终止进程约 3.5 分钟（`t48launch` 空等 90 秒 + 事后诊断）。它全程没有夺取焦点
（`open -g` 生效），但确实可见。

失败被发现后**立即终止进程，没有重试、没有调参数再试**，符合约定的硬性中止条件。
终止后复查：该 pid 的在屏窗口数归 0，进程不存在。

### 已排除的替代驱动路径

- **WebKit 远程检查器**：`Cargo.toml` 中 `tauri = { version = "2.11.5", features = [] }`，
  未启用 `devtools`，正式包的 WKWebView 不可检查。这本身是正确的安全姿态（与
  「正式安装包必须确认不含 WebDriver、调试端口」一致），但也堵死了这条路。
- **WebdriverIO**：正式包按规则不含 WebDriver，无法接入。
- **CGEvent 键盘/鼠标注入**：需要 App 处于前台并夺取焦点，与「不打扰用户」直接冲突；
  且在辅助功能树不可用时无法定位控件，只能盲点坐标。

### 下一步该怎么走（留给决策，不自行推进）

1. **人工在演示机上走一遍**：这是唯一不依赖辅助功能树、又是真实用户路径的办法，
   而且顺带把 §2 那条 Gatekeeper 同意框的未验证项一起做掉。成本最低、证据最强。
2. **先修无障碍缺陷再自动化**：定位主窗口不进 AX 树的根因并修复。属于产品改动，超出本任务范围，
   但**这个缺陷本身值得单独立项**——它不只挡住验收，也是真实的无障碍问题。
3. 不建议再试自动化绕路：三条替代路径都已排除，继续试就是在用户屏幕上反复制造可见窗口。

## 尚未验证（全部仍未完成）

- [ ] App 启动后停在**产品账号登录界面**而不是业务工作台
- [ ] 用演示账号真实登录成功
- [ ] 登录后进入业务工作台
- [ ] 至少一次真实认证业务请求打到云端（Nginx access.log + 后端审计双向对齐）
- [ ] 设备自动绑定：`installations` 从 0 行变 1 行且 `owner_user_id` 指向该账号，
      `device_credentials` 出现 1 行
- [ ] Gatekeeper 首次启动同意框上真实点一次 Open（见 §2 未验证项 / 清单 §4.3）

登录从未发生，所以云端三张表在本任务前后**完全没有变化**，可作为「本任务没有污染云端」的证据：

| 表 | 任务开始 | 任务结束 |
| --- | --- | --- |
| `installations` | 0 行 | 0 行 |
| `device_credentials` | 0 行 | 0 行 |
| Nginx 来自本机公网 IP 的请求 | 0 | 0 |

## 收尾状态

- 隔离 HOME 与其中的 App 副本（约 1 GB）已删除；
- DMG 已 `hdiutil detach`，文件内容未变（sha256 仍为 `e6d83d4e…d143d14`），
  quarantine 仍是原来的 `0083;0;Safari;`（挂载额外留下的两个 `com.apple.diskimages.*`
  记账 xattr 属 macOS 行为，不影响内容）；
- App 进程已全部终止，在屏窗口数归 0；
- **真实用户目录 `~/Library/Application Support/com.aventador.automationtool/` 全程 675 个条目
  逐行 diff 一致，mtime 始终 `2026-07-26T02:37:42`，抖音扫码凭据完好**；
- 全程未运行 `scripts/run_u9_06_acceptance.py`，未改动任何产品代码。

## 秘密处理

演示账号口令只从 `root@49.233.213.109:/etc/automation-tool-demo/secrets.json`（0600 root-only）
即时读取，只经 stdin 进入辅助功能驱动程序，不进入 argv、进程表、文件、日志、截图或本文件。
驱动程序对口令只回显长度，不回显内容。
