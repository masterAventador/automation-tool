# 客户演示前检查清单

> 用途：演示前照着从头跑一遍，全绿才算「能演」。
>
> 初版 2026-07-26；**2026-07-27 逐条重核（T70）**。上一版写于 07-26 下午，当晚到凌晨主线又合了四十笔改产品界面的提交，其中相当一部分直接推翻了清单里写的点击路径、文案和判据。**本次每一条都重新判过一遍**，标 ✅ **实测** 的是重核当天真正跑过并贴了输出的。
>
> 每条末尾用 `〔07-27 改〕` 标出这次变了什么、原来写的是什么。没有这个标记的条目是重核后仍然成立。
>
> **本文件取代 `docs/development/DEMO-preflight-checklist.md`。** 那一份是 T70 的过程稿，§7「功能链路」是空占位。**两份内容冲突时以本文件为准**，并请协调者尽快把旧文件删除或改成一行指向本文件的跳转——留着两份清单，早晚有人拿错那份。（07-27 复查：**那份文件仍然存在**，还没被删。）
>
> 配套文件：`docs/demo-runbook.md`（演示当天的动作脚本与故障预案）。本清单负责「能不能演」，runbook 负责「怎么演」。

---

## 0. 先说这份清单不覆盖什么

一份清单最危险的时刻不是它漏掉了什么，而是别人以为它覆盖了。

- **不覆盖好不好用**。只覆盖「能不能跑起来、会在哪一步停」。文案质量、画面是否贴题、成片好不好看，一律不判断。
- **不覆盖断网**。断网就是演示失败，没有离线降级路径。本清单只验「现在通不通」，验不出「演示中途会不会断」。
- **不覆盖抖音平台侧的变化**。平台改版、风控弹窗、二维码过期都在清单之外，只能现场人工接管。
- **不是回归测试**。它不替代任何门禁，只回答「明天能不能演」。
- **不覆盖 Windows**。本轮演示形态是 macOS 正式包。Windows 侧另行验收（见 D 段）。

---

## 1. 怎么读这份清单

每条格式固定：**做什么 → 命令或点击路径 → 期望看到什么 → 不对时怎么办**。

| 标注 | 含义 |
| --- | --- |
| `[本机]` | 在开发机上做，结论对演示机同样成立（例如包的签名判定——包是同一个文件） |
| `[演示机]` | **必须在演示当天真正要用的那台 Mac 上做**，本机做了不算数 |
| `[服务器]` | 在云端服务器上做 |

**排序原则：越早发现越省事。** 本清单不按模块排，按「这条如果挂了，补救要花多久」排：

| 阶段 | 什么时候做 | 挂了的补救成本 |
| --- | --- | --- |
| **A 段** | 演示前 2–3 天，**还来得及重出包** | 重新构建 + 签名 + 公证，以天计 |
| **B 段** | 演示前一天，在演示机上 | 以小时计（重扫码、重配密钥、清盘） |
| **C 段** | 演示当天开场前 10 分钟 | 只能取消或改脚本 |

**A 段任何一条不过，都不要往 B 段走。** 拿一个坏包去演示机上折腾一整天，是最糟的时间分配。

---

## ⛔ 重核当天的头号结论：**现在磁盘上没有一个可以拿去演示的包**

这一条排在所有检查之前，因为它决定了 A 段大部分条目此刻要不要跑。

现存最新的 DMG 是 `wt/release/.local/release/.../自动化运营工具_0.1.0.dmg`，**07-26 20:52**。它有三个各自独立的问题：

| # | 问题 | 证据 |
| --- | --- | --- |
| 1 | **界面落后 40 笔提交** | 包内可执行文件 07-26 20:42；此后 `frontend/src` 与 `frontend/src-tauri/src` 又有 **40 笔**提交，含视频制作第一步重排、工作台首页、更新中心状态、失败文案、失败可见性、剪辑服务表单宽度 |
| 2 | **挂载后没有可以拖进去的目标** | 实测挂载：卷里只有 `自动化运营工具.app` 一项，**没有 `Applications` 符号链接**。修复在 `179a720`（07-26 22:42），比这个包晚了 110 分钟 |
| 3 | 它是 T84 那条缺陷的**原始现场** | `docs/demo-sprint-roadmap.md` 里 T84 描述的就是这个包 |

**好消息是：真正难的那两件已经不再是未知数。**

- 上一版清单顶着的那个阻塞级 🚩（「带 JIT 修复的新包是否已构建」）**已解决**——`/Applications` 下那个包的两个 `node` 都能求值（A1 有实测输出）；
- 上一版 D4/D5 两条「签名包上从未成功过」**都已被推翻**——签名包上真实出过片（B9 有产物），抖音也真实登录过（B7）。

所以现在缺的只是**再出一次包**：从含 `48f0910` 或更新的提交构建 → 签名公证 → 跑完整个 A 段。**A2 与 A8 是这次新包必须过、而当前包一定不过的两条。**

〔07-27 改〕原来这里挂的是「新包是否已构建」这个未知数；现在未知数没了，换成一件确定要做的事。

---

## A 段 · 还来得及重出包（`[本机]` / `[服务器]`）

### A1 ✅ 包内两个 Node 运行时能真的跑 JavaScript

**这条排第一，因为它是本轮唯一一条「挂了就必须重出包、且所有浅层检查都会骗你」的缺陷。**

2026-07-26 发现：`release_assembly.py` 给包内每个 Mach-O 加 `--options runtime`（硬化运行时）重签，而签名契约只为 `embedded-browser` 声明了 entitlements。两个 `node` 于是带着硬化运行时、没有 `com.apple.security.cs.allow-jit`，**V8 在 `Isolate::Init` 阶段拿不到可写可执行页，直接 abort（exit 133）**。

后果不小：Playwright 起不来 = **全部浏览器 RPA 不可用**（抖音登录、发布都死）；`motion-video-worker` 的 node 同样 133 = **品牌动效成片不可用**，而那正是本次选定的演示线。

**做什么**：把要交付的那个 App 装出来，对包内**每一个** `node` 求值一个表达式。

```bash
APP="/Applications/自动化运营工具.app"          # 换成实际路径
find "$APP/Contents/Resources" -name node -type f -perm +111 | while read -r N; do
  OUT=$("$N" -e 'console.log(1)' 2>&1); RC=$?
  echo "rc=$RC  out=${OUT:0:40}  $N"
done
```

**期望**：找到 **2 个** node，每个都 `rc=0` 且 `out=1`。

**⛔ 不要用 `node --version` 当判据。** 它在 V8 初始化前就打印退出，**坏包也能过**。这正是这个缺陷躲过所有检查的原因。

**✅ 2026-07-27 实测（`/Applications` 下的当前包，即 20:52 那个 DMG 装出来的）**：

```text
rc=0  out=1  /Applications/自动化运营工具.app/Contents/Resources/motion-video-worker/package/runtime/node
rc=0  out=1  /Applications/自动化运营工具.app/Contents/Resources/local-executor/package/_internal/playwright/driver/node
```

门禁脚本同样通过：

```text
$ python3 scripts/check_packaged_javascript_runtimes.py "$APP"
  executing .../local-executor/package/_internal/playwright/driver/node
  executing .../motion-video-worker/package/runtime/node
all 2 packaged JavaScript runtimes evaluate an expression
  11 binaries are granted allow-jit; this gate exercised 2 of them by evaluating an
  expression. The rest — the embedded Chromium among them — are NOT exercised here;
  a JIT grant that never runs is a claim nobody checked.
rc=0
```

**注意脚本自己那句免责声明**：11 个二进制拿到 `allow-jit`，这道门禁只真跑了其中 2 个。**内置 Chromium 的 JIT 没有任何东西验过**——它由 B7 抖音扫码间接验证（浏览器起不来就扫不了码）。

**不对时怎么办**：**没有现场补救，必须重出包。** 修复已提交（`41a19d8`：补 entitlements + 新增 `scripts/check_packaged_javascript_runtimes.py`，已接进 `build_release_package.py` 的审计段，位置在签名之后、公证之前）。

〔07-27 改〕上一版这里顶着一个阻塞级 🚩「新包是否已构建，未知」，并且实测输出贴的是**坏包**（rc=133）。现在贴的是真包的通过输出，🚩 撤销。新包出来后这条要**重跑**——它验的是那一个具体文件。

---

### A2 ⚠️ 交付的到底是哪个包（判据这次换了）

在跑其余 A 段之前先钉死目标。把要发给客户的那个 DMG 记作 `$DMG`，装出来的 App 记作 `$APP`。

**上一版的判据已经不够用了。** 它写的是「修改时间晚于 `41a19d8`（07-26 20:37 那笔 JIT 修复）」。20:52 那个包满足这条，**却仍然落后 40 笔界面提交、并且装不进去**。用一个写死的 commit 当门槛，门槛就会随着仓库前进而自动失效。

**换成不会过期的判据**：包里的可执行文件，必须比仓库里最后一笔会进产品界面的提交更晚。

```bash
ls -l "$APP/Contents/MacOS/automation-tool-desktop"
git -C /Users/aventador/code/automation-tool log -1 \
    --format='%h %ad %s' --date=format:'%m-%d %H:%M' \
    -- frontend/src frontend/src-tauri/src
```

**期望**：第一行的时间**晚于**第二行的时间。

**✅ 2026-07-27 实测（当前包 —— 这是一次失败示范）**：

```text
$ ls -l "$APP/Contents/MacOS/automation-tool-desktop"
-rwxr-xr-x@ 1 aventador admin 21679296 Jul 26 20:42 .../automation-tool-desktop

$ git log -1 --format='%h %ad %s' --date=... -- frontend/src frontend/src-tauri/src
48f0910 07-27 00:27 merge: 合并 T63/T64 升级路径自愈，并纠正台账对五处的描述

$ git log --oneline --since='2026-07-26 20:42' -- frontend/src frontend/src-tauri/src | wc -l
      40
```

**包比最后一笔界面提交早了近 4 小时，中间隔着 40 笔提交。判定：不合格。**

**不对时怎么办**：重出包。**验一个包、发另一个包，等于没验**——本轮已经因此吃过两次亏（第一次是 JIT，第二次就是这个）。

〔07-27 改〕判据从「晚于某个写死的 commit」改成「晚于仓库当前最后一笔界面提交」，并配了失败示范。

---

### A3 ✅ 包内五份资源齐全

生产代码要从 `Contents/Resources/` 找五样东西，缺任何一样对应功能直接不可用。

```bash
ls "$APP/Contents/Resources/"
```

**期望**：恰好这五项，一个不少。

| 资源 | 缺了会怎样 |
| --- | --- |
| `embedded-browser` | 内置 Chromium 没了，浏览器 RPA 全死 |
| `local-executor` | 执行器没了，什么任务都跑不了 |
| `media-toolchain` | ffmpeg/ffprobe 没了，报 `render_unavailable` |
| `motion-video-worker` | **品牌动效成片渲染不了 ← 演示线** |
| `material-video-worker` | 智能素材成片起不来 |

**✅ 2026-07-27 实测**：

```text
embedded-browser   local-executor   material-video-worker
media-toolchain    motion-video-worker
```

五项齐全，无多余项。

**不对时怎么办**：跑 `python3 scripts/check_release_package_wiring.py`，再看 `frontend/scripts/audit-production-package.mjs`。这三份视频资源曾经整体缺失而所有门禁全绿（`tauri.conf.json` 的 `bundle` 段当时根本没有 `resources` 声明），所以**肉眼确认这五个目录名，比相信任何一份报告都可靠**。

---

### A4 ✅ 签名与公证

```bash
xcrun stapler validate "$DMG"
```
**期望**：`The validate action worked!`

```bash
xattr -l "$DMG" | grep quarantine || \
  xattr -w com.apple.quarantine '0083;00000000;Safari;' "$DMG"   # 没有就模拟客户下载
spctl -a -vvv -t open --context context:primary-signature "$DMG"
```
**期望**：`accepted`，且 `source=Notarized Developer ID`。

```bash
codesign --verify --deep --strict --verbose=2 "$APP"
spctl -a -vvv -t execute "$APP"
xcrun stapler validate "$APP"
```
**期望**：`satisfies its Designated Requirement`；`accepted / source=Notarized Developer ID`；`The validate action worked!`

**✅ 2026-07-27 实测（DMG 与 `/Applications` 下的 App，四条全过）**：

```text
$ xattr -l "$DMG" | grep quarantine
com.apple.quarantine: 0083;0;Safari;          ← 隔离属性本来就在，这是客户路径

$ xcrun stapler validate "$DMG"
The validate action worked!

$ spctl -a -vvv -t open --context context:primary-signature "$DMG"
...自动化运营工具_0.1.0.dmg: accepted
source=Notarized Developer ID
origin=Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)

$ codesign --verify --deep --strict --verbose=2 "$APP"
/Applications/自动化运营工具.app: valid on disk
/Applications/自动化运营工具.app: satisfies its Designated Requirement

$ spctl -a -vvv -t execute "$APP"
/Applications/自动化运营工具.app: accepted
source=Notarized Developer ID
origin=Developer ID Application: beijing yizhuangjingjikaifaqu wei (HK56FS93AD)

$ xcrun stapler validate "$APP"
The validate action worked!
```

**⛔ 不要先 `xattr -d` 清掉 quarantine 再验**——那验的是本机特权路径，不是客户会走的路径。上面这次不需要手工加，因为那个 DMG 上的隔离属性一直都在。

**不对时怎么办**：客户双击会看到「无法打开，来自身份不明的开发者」。**这是最难现场补救的一类**，必须重新签名公证。

> **注意**：A4 全绿**不能推出 A1 全绿，也不能推出 A2/A8 全绿**。当前这个包就是活证据——签名、公证、stapler 四项完美，**界面落后 40 笔提交，而且挂载后装不进去**。签名验的是「这个文件没被改过」，不验「这个文件是对的那个」。

〔07-27 改〕补上 `stapler validate "$APP"`（原来只验 DMG）、补上隔离属性的先查后加、四条全部换成真实输出；末尾的注意事项从「A4 不能推出 A1」扩展到「也不能推出 A2/A8」。

---

### A5 ✅ 包内没有测试痕迹

```bash
grep -rl 'TAURI_WEBDRIVER_PORT\|wdioTauri\|automation-tool-test-harness' "$APP" 2>/dev/null | head
```

**期望**：**无输出**。

**✅ 2026-07-27 实测**：无输出，通过。

**不对时怎么办**：出厂包带调试端口是安全问题，重出包。

〔07-27 改〕从「⬜ 从未执行」变成已实测。

---

### A6 ✅ 云端后端活着，且未认证请求被拒

```bash
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 10 https://at.xuanbai.tech/api/v1/health
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 10 https://at.xuanbai.tech/api/v1/account-installations
```

**期望**：第一条 `200`，第二条 `401`。

**✅ 2026-07-27 实测**：

```text
200
401
```

**不对时怎么办**：
- health 不是 200 → 先看服务器上容器是否在跑，再查 DNS/证书。**这条不过，演示直接取消，没有本地降级。**
- 第二条返回 `200` → 裸露的匿名写接口是安全问题，不是演示问题。**停下来报告，不要带着这个上演示。**

〔07-27 改〕上一版只实测了 health，第二条（匿名写接口必须被拒）从没跑过。这次两条都跑了，`401` 符合预期。

---

### A7 ✅ `[服务器]` 演示账号凭据在位

```bash
ssh root@49.233.213.109 'ls -l /etc/automation-tool-demo/secrets.json'
```

**期望**：文件存在，权限 `-rw-------`（0600）。

**✅ 2026-07-27 实测**：

```text
-rw------- 1 root root 1009 Jul 26 18:01 /etc/automation-tool-demo/secrets.json
```

**不对时怎么办**：权限不对就 `chmod 600`；文件不存在就重新下发凭据。

> 地址**写死**不用别名——跑这份清单的人可能不是这台机器，`~/.ssh/config` 里的别名在他那儿不存在。
>
> ⚠️ 这台服务器上还跑着别的业务。**只读这一个文件，不要在上面做任何别的操作。** 本次重核严格遵守了这一条，因此**服务端 `installations` 表的行数没有重新查**，见 B5。

〔07-27 改〕从「⬜ 从未执行」变成已实测。

---

### A8 🆕 挂载 DMG，确认里面有可以拖进去的目标

**这条是新加的，因为它正是当前这个包一定不过的那条。**

客户拿到 DMG 的第一个动作是双击挂载，然后把 App 拖进「应用程序」。如果卷里只有一个孤零零的 App 图标，**没有任何可以拖的目标**，客户当场就卡在这里——而这不是他能自己想明白的事，需要他另开一个访达窗口。

`tauri.conf.json` 里改不了这个：发版跑的是 `tauri build --bundles app`，Tauri 自己的 DMG 打包器根本不执行，`bundle.macOS.dmg` 那些设置一行都不会被读。真正的成像发生在 `scripts/build_release_package.py`，`179a720`（07-26 22:42）把它从「直接给 `hdiutil` 一个裸 `.app`」改成「先 `ditto` 进暂存目录、建 `Applications` 符号链接、再对暂存目录成像」。

```bash
MP=$(mktemp -d /tmp/at-preflight-dmg-XXXX)
hdiutil attach -nobrowse -readonly -mountpoint "$MP" "$DMG" && ls -la "$MP"
hdiutil detach "$MP" -quiet; rmdir "$MP"
```

**期望**：卷里有两项——`自动化运营工具.app`，以及指向 `/Applications` 的符号链接（`ls -la` 里那一行以 `l` 开头，写着 `Applications -> /Applications`）。

**✅ 2026-07-27 实测（当前包 —— 又一次失败示范）**：

```text
$ hdiutil attach -nobrowse -readonly -mountpoint "$MP" "$DMG" && ls -la "$MP"
total 0
drwxr-xr-x@   3 aventador  staff   170 Jul 26 20:47 .
drwxrwxrwt  232 root       wheel  7424 Jul 27 00:23 ..
drwxr-xr-x@   3 aventador  staff   102 Jul 26 20:41 自动化运营工具.app
```

**只有 App，没有 `Applications` 符号链接。判定：不合格。** 修复代码已在主线，缺的只是再出一次包。

**⛔ 不要用「我这台机器上装得上」来代替这条。** 开发机上你会直接把 App 拷进 `/Applications` 或者用命令行装，根本不走拖拽这条路——**客户走的恰恰只有这条路**。

**不对时怎么办**：确认出包用的是 `scripts/build_release_package.py` 且它含 `179a720`；重出包。

〔07-27 新增〕上一版完全没有这条。DMG 挂载后长什么样，之前没有任何一条检查看过。

---

## B 段 · 演示前一天，在演示机上（全部 `[演示机]`）

**本节没有一条能在开发机上代劳。** 演示机的数据目录、登录态、磁盘、网络都是独立的。

> **一条贯穿 B 段的好消息（重核当天确认）：换包不会清掉任何东西。**
> 登录态、三份密钥、抖音档案、彩排成片全部存在
> `~/Library/Application Support/com.aventador.automationtool/profiles/demo-xuanbai/`，
> 这个路径按 **bundle identifier** 定位，**不含版本号，也不在 App 包里面**。
> 装一个新版本的 App 不会碰它。所以 B5/B6/B7/B8 在同一台机器上**只需要做一次**，
> 后面再换包不用重做——但**换包之后 B4 必须重做**（它是「装对包了没有」的探针）。

### B0 先确认「演示机」是哪台、谁来演

**做什么**：书面确认三件事——① 演示当天用哪台 Mac；② 谁操作键盘；③ 那个人懂不懂技术。

**为什么排在 B 段第一**：这三个答案决定了 B 段要不要做、runbook 要写多细。**如果操作者不懂技术、现场没有技术人员，那么「出问题现场救」这个选项不存在**，B 段每一条都必须提前一天做完做实。

> **🚩 仍待确认**：`docs/development/DEMO-preflight-checklist.md` 记载的交付形态是「客户自己装包 → 装到领导的 M4 Air / macOS 26 / 24G → 领导现场演示，演示者不懂技术」。至今没有第二处确认过这个说法。**这两种情况的准备工作量差一倍，必须先确认。** 需要项目负责人确认。
>
> 顺带一提：如果确实是「客户自己装包」，那么 **A8 就从「体验问题」升级成「阻塞问题」**——没有技术人员在旁边，卡在拖拽这一步就没人能往下走。

〔07-27 改〕结论没变，仍未确认；补了它与 A8 的联动。

---

### B1 ✅ 系统、芯片、磁盘（本机已跑，演示机必须重跑）

```bash
sw_vers && uname -m && df -h /
```

**期望**：`arm64`；**可用空间 ≥ 20 GB**。

**判据来源**：包约 510 MB 量级，安装后还要展开内置 Chromium、媒体工具链和两个视频 Worker，加上渲染中间产物与成片。另外 `ensure_free_space` 硬性要求「1 GiB + 视频大小」，不够时**导入成片会失败**——界面报错不是闪退，但演示当场就挂了。20 GB 是留了余量的保守线。

**✅ 2026-07-27 实测（开发机，结论不对演示机成立）**：

```text
ProductName:    macOS
ProductVersion: 26.4.1
BuildVersion:   25E253
arm64
/dev/disk3s1s1  3.6Ti  16Gi  3.2Ti  1%  /
```

**不对时怎么办**：
- 不是 arm64 → 包的架构必须重新确认，macOS 验收是分 arm64 / x86_64 两条线的。
- 磁盘不够 → 清盘。**磁盘满会在渲染中途炸，是最难看的失败形态**（前面全绿、最后一步失败）。

〔07-27 改〕补了开发机实测输出，并明确标注这条的结论**不能**顺延到演示机。

---

### B2 ✅ 两条零成本预检（风险面这次小了一些，但没有消失）

App 的启动 setup 里有大量 abort 点，失败时**窗口先出现再瞬间消失，没有任何提示**，而且**结构上救不了**：Tauri 是先建窗口后跑 setup，setup 占着主线程，WebView 的 JS 一行都执行不了，所以那个 fail-closed 诊断页覆盖不到启动失败本身。

**07-27 更新**：`T63/T64`（`48f0910`）把其中 **6 处**改成了自愈——诊断设置版本未知或 JSON 损坏、待办紧急停止记录损坏、更新策略文档 schema 未知或不变量已破、更新缓存 manifest 读不懂，现在一律退回安全状态并留日志，不再让 App 起不来；顺带把更新缓存文件**权限过宽**和**路径上是软链**这两种也从 abort 改成删除/解链接。

**但对演示最要命的那一处仍然在**：`secure_store.rs` 的 `ensure_private_file_permissions` 对**文件**只检查不修复（`T66a`，未修，冻结在 Demo 之后）。也就是说，**带 group/other 权限位的密钥文件仍然会让 App 启动即闪退、零提示**。而这正是「Time Machine / 迁移助理恢复的账号」最容易造出来的东西。

下面两条覆盖了剩余风险里概率最高的部分，**不改代码、零成本**：

```bash
# 1) 磁盘空闲 > 5 GiB（B1 的 20 GB 已覆盖，这里是下限）
df -h /

# 2) 祖先目录没有软链
ls -ld /Users "/Users/$(whoami)" ~/Library ~/Library/Application\ Support
```

**期望**：四行**都不是 `l` 开头**（不是 symlink）。

**✅ 2026-07-27 实测（开发机）**：

```text
drwxr-xr-x   5 root       admin   160 Apr 15 01:18 /Users
drwxr-x---+ 59 aventador  staff  1888 Jul 27 00:13 /Users/aventador
drwx------@ 90 aventador  staff  2880 Jul 10 00:16 /Users/aventador/Library
drwx------+ 78 aventador  staff  2496 Jul 26 23:08 /Users/aventador/Library/Application Support
```

**不对时怎么办**：出现软链就换一个账号或换一台机器演示。**代码里这条是硬拒绝，没有开关能绕过。**

**演示机上还要当面问一句**：**这台机器是不是从旧机用 Time Machine / 迁移助理恢复过来的？** 是的话，B6 存完密钥后一定要跑一遍 B9 的权限核对——那是唯一能在闪退发生**之前**看见它的地方。

〔07-27 改〕上一版写「23 个 abort 点，其中 21 个在 setup 钩子里」。T63/T64 改掉 6 处后这个数字已经不准，所以不再报数字，改为说清「哪些自愈了、哪一处还在」。Time Machine 那条风险从「值得当面问一句」升级成「问完还要跑 B9 核对」。

---

### B3 首次启动，让 Gatekeeper 放行

**动作**：双击 DMG → **确认卷里有「应用程序」文件夹的别名** → 把 App 拖过去 → **右键点「打开」**（不是双击）→ 观察弹窗。

**期望**：
1. 挂载后看到两个图标：App 和「应用程序」文件夹别名（这是 A8 在演示机上的复验，**A8 不过就不要走到这一步**）；
2. 出现一次「macOS 已验证此 App 不含恶意软件」或直接启动；**不出现「无法打开、来自身份不明的开发者」**。

**不对时怎么办**：看不到「应用程序」别名 → 回到 A8，重出包。看到身份不明提示 → 回到 A4。

> ⚠️ **这一步必须在演示前一天做掉，让 App 完成首次授权，不要留到演示当天现场第一次打开。**
>
> 另注：**「人在同意框上点一次『打开』」这个动作至今没有任何自动化真实走过。** 此前取证时是先 `xattr -dr com.apple.quarantine` 再启动的，所以 `spctl` 的结论虽然可信，**却不能顶替这一下人工交互**。演示当天客户要做的恰恰就是这一下。

〔07-27 改〕在最前面插入了挂载后的目视确认——上一版直接从「拖进应用程序」写起，默认了那个目标存在，而它当时并不存在。

---

### B4 🔁 App 起来并停在正确的位置（这条现在同时是「装对包了没有」的探针）

**动作**：启动 App，观察主界面。

**期望（结构，未变）**：先看到产品账号登录页（Demo Profile 未登录时），登录后进入工作台，左侧导航八项齐全：

```text
工作台 / 新建任务 / 任务记录 / 视频制作 / 视频剪辑 / 作品发布 / 平台状态 / 设置与诊断
```

**不出现白屏，不出现「制作界面暂时不可用」。**

**期望（新包特征，四条目视，任意一条不符就是装了旧包）**：

| # | 在哪看 | 新包应该是什么样 | 旧包是什么样 |
| --- | --- | --- | --- |
| 1 | 工作台首页「最近任务」 | 每行叫「**07-26 21:33:53 的任务**」这种按创建时间的名字 | 一串裸 UUID |
| 2 | 工作台首页 | `Revision`、`事件水位` 折在一个**默认收起**的「**诊断信息**」里 | 直接摊在首页上 |
| 3 | 视频制作 →「新建视频」 | **整页不用滚动**；「新建视频」这四个字在页面上**只出现一次**（页签上）；两张制作方式卡片的「详细说明」**默认收起** | 整页 1240px 要滚；「新建视频」出现两次（页签 + 卡片标题）；说明表默认摊开 |
| 4 | 设置与诊断 →「视频剪辑服务」 | 凭据表单**和上面的「模型服务」一样宽**（满宽） | 塌成卡片的三分之一宽，地域下拉只有 134px |

**为什么把这四条放在这里**：它们**不用登录、不用配密钥、不用等三分钟**，是全流程里最便宜的「我装的是不是那个包」判据。A2 用时间戳判，这四条用眼睛判——**两个判据独立，一起用比只用一个稳**。

**✅ 2026-07-27 实测（UI Harness，Playwright 无头 Chromium，视窗 = 生产窗口尺寸）**：第 1–4 条对应的用例全部通过。

```text
$ pnpm exec playwright test e2e/video-studio-density.spec.ts e2e/workbench-home.spec.ts
  ✓ e2e/video-studio-density.spec.ts:202 「新建视频」在页面上只出现一次
  ✓ e2e/video-studio-density.spec.ts:228 整页不需要滚动
  ✓ e2e/video-studio-density.spec.ts:243 详细说明默认是收起的
  ✓ e2e/video-studio-density.spec.ts:87  「选择品牌动效成片」在首屏之内
  ✓ e2e/workbench-home.spec.ts:68        每一行最近任务都说得出自己是什么时候的
  ✓ e2e/workbench-home.spec.ts:79        诊断信息 still holds the counters an operator needs
  18 passed (6.0s)

$ pnpm exec playwright test e2e/unstyled-class-hooks.spec.ts
  ✓ 视频剪辑服务凭据表单填满它的卡片 @ 1280x800 › 和同一页的模型服务表单一样宽
  ✓ 视频剪辑服务凭据表单填满它的卡片 @ 960x640  › 和同一页的模型服务表单一样宽
  10 passed (9.6s)
```

**这只证明代码是对的，不证明包是对的。** UI Harness 跑的是仓库当前 HEAD 的 React，不是安装包里那份。**演示机上这四条要用眼睛再看一遍。**

**不对时怎么办**：
- 白屏或闪退 → 回到 B2；再查数据目录是否可写（B9）。
- 「制作界面暂时不可用」类提示 → 回到 A3（资源缺失）或 A1（node 跑不了）。
- 上表四条任意一条长得像「旧包」那一列 → **装错包了**，回到 A2。

〔07-27 改〕上一版只检查了八项导航。新增的四条目视特征全部来自 07-26 夜里到 07-27 凌晨合并的界面改动（T93 首页、T95 视频制作第一步、T88b 剪辑服务表单宽度）。

---

### B5 产品账号登录

**动作**：用演示账号登录一次。

- 账号：`xuanbai.demo`
- 密码：**在 `~/Documents/at-tools-credentials/project-secrets/demo-account.txt`**（不要抄进任何文档、聊天或截图）

**期望**：登录成功并进入工作台；设备**自动**绑定当前账号，**不出现配对码、审批或轮询等待**。

**登录成功后在演示机上核对**（只看文件名和权限，不看内容）：

```bash
ls -la ~/Library/Application\ Support/com.aventador.automationtool/profiles/demo-xuanbai/ \
  | grep -E 'product-account-session-v1|device-credential-v1'
```

**期望**：两个文件都在，权限均为 `-rw-------`。

**✅ 2026-07-27 实测（开发机，签名包上真实登录过）**：

```text
-rw-------@  1 aventador  staff   86 Jul 26 20:19 device-credential-v1
-rw-------@  1 aventador  staff  428 Jul 26 21:25 product-account-session-v1
```

**不对时怎么办**：
- 报「暂时无法完成账号操作，请稍后重试」→ 这是 T68 那个已修复的缺陷的原文案（边界 nginx 用 `$request_id` 覆盖了 App 送的 `x-request-id`，App 比对回显不一致判为协议违规，**登录第一个请求就失败**）。修复在服务端、已部署。若又出现，**先查 nginx 配置是否被回滚**，不要怀疑包。
- 其他报错 → 先跑 A6 确认后端活着，再确认 App 连的是 `at.xuanbai.tech` 而不是 loopback。

〔07-27 改〕上一版写「服务端 `installations` 计数应从 0 变 1」。**这句现在是错的**——`docs/demo-sprint-roadmap.md` 07-26 22:40 的台账纠偏记载该表**已有 5 行且全部绑定账号**，所以「从 0 变 1」既不会发生，也没法当判据。而且核对它要登服务器查库，而 A7 明确写着那台机器上只读一个文件。**改成核对本机那两个文件**——它同样能证明「登录成功且设备已绑定」，且不用碰服务器。
>
> ⚠️ 本次重核**没有**重新查服务端 `installations` 的行数（遵守 A7 的只读约束）。上面那个「5 行」是台账里的记录，不是这次的实测。

---

### B6 三份密钥：保存 + 测试连接

**点击路径**：左侧「设置与诊断」→

| 卡片 | 要填什么 | 验证动作 |
| --- | --- | --- |
| 模型服务 → **文案模型服务** | 阿里百炼 API Key | 点「保存配置」→ 再点「**测试连接**」 |
| 模型服务 → **视频创作模型服务** | 阿里百炼 API Key | 同上 |
| **视频剪辑服务** | 阿里云 AccessKey ID + Secret + 地域 | 同上——**这张卡也有「测试连接」按钮** |

**期望**：三处标签都显示「**已配置**」；「测试连接」全部成功。成功时的原文是——

- 模型服务：`连接成功；<额度信息>。`（服务没返回额度时是 `连接成功；服务未返回可用额度。`）
- 视频剪辑服务：`连接成功；访问密钥与所选地域可用。`

**⚠️ 视频创作模型这一份是演示线的硬依赖**——一句话自动制作的文案、分镜、画面全靠它。没配置时点「开始自动制作」会被拒，提示「请先到"设置与诊断"配置视频创作模型服务。」

**顺带确认 B4 第 4 条**：「视频剪辑服务」的凭据表单应该和上面的「模型服务」一样宽。**如果它只占卡片三分之一宽，说明装的是旧包**，不用往下配了，先回 A2。

**不对时怎么办**：
- `authentication_rejected`（密钥未通过阿里百炼验证）→ 换正确的 Key 重存。密钥保存后**不回显**，看不到不等于没存。
- 提示 `当前服务额度已用尽，请在阿里百炼控制台处理。` → 这是额度问题不是密钥问题，**去控制台充值，不要换 Key**。
- 密钥放在开发仓库 `.local/secrets/` 下的**不会被 App 读到**，那是验收脚本的位置，App 只读自己的私有数据目录——这是正确设计，不是 bug。

**怎么确认真的存进去了**（在演示机终端上，只看文件名不看内容）：

```bash
ls -la ~/Library/Application\ Support/com.aventador.automationtool/profiles/demo-xuanbai/model-services/ \
       ~/Library/Application\ Support/com.aventador.automationtool/profiles/demo-xuanbai/editing-services/
```

**期望**：三个文件，权限均为 `-rw-------`：

```text
model-service-script-v1
model-service-video-creative-v1
video-editing-service-aliyun-v1
```

**✅ 2026-07-27 实测（开发机）**：三份齐全，权限全对，**20:22 存入，并被 21:39 那次成功出片真实用掉**（见 B9）。

```text
model-services/
-rw-------@ 1 aventador staff 204 Jul 26 20:22 model-service-script-v1
-rw-------@ 1 aventador staff 212 Jul 26 20:22 model-service-video-creative-v1
editing-services/
-rw-------@ 1 aventador staff 138 Jul 26 20:22 video-editing-service-aliyun-v1
```

〔07-27 改〕三处：① 视频剪辑服务那行从含糊的「保存并按页面提示验证」改成明确的「点测试连接」，并写出两种成功原文；② 补了「额度用尽」这条容易被当成密钥错误的失败；③ 挂上 B4 第 4 条的宽度目视。另：上一版说「三处都显示已配置」时没写标签原文，现已确认就是「已配置 / 未配置」两个词。

---

### B7 抖音扫码登录

**点击路径**：左侧「平台状态」→ 抖音卡片 → 点「**打开登录处理**」→ 在弹出的运营浏览器窗口中扫码 → 手机抖音确认。

**期望**：状态标签变为「**登录正常**」。

**然后必须做第二步**：**完全关掉 App 再打开**，回到「平台状态」，确认仍是「登录正常」、不再要求扫码。

**✅ 已在开发机上真实完成（07-26）**：扫码通过，服务端记录 `state = healthy`；本机指针 `current-douyin-profile-v1` 指向产品自建的档案。

```text
$ cat .../profiles/demo-xuanbai/current-douyin-profile-v1
739b9297-f6c2-47b2-8da5-26451ec80c36

$ ls .../profiles/demo-xuanbai/embedded-browser-profiles/douyin/
739b9297-f6c2-47b2-8da5-26451ec80c36        （07-26 21:35 更新，且只有这一个）
```

**⬜ 仍未做的第二步**：完全退出 App 再打开，确认还是「登录正常」。这验的是**登录态有没有落盘**，不是当时那一刻通没通。**这是整条抖音链路唯一还没闭环的一项**，30 秒就能做完。

**为什么必须提前一天做（前两条仍然成立，第三条已被推翻）**：
1. 演示机的数据目录与开发机隔离，**开发机上登录过完全不算数**；
2. 现场扫码要掏手机、找入口、对二维码，**二维码还会过期**。这是演示最容易翻车的一步；
3. ~~抖音登录态是按 Installation 记在服务端的，本地复制 cookie 文件无效~~ → 前半句仍对（服务端确实按 Installation 记），但**由此推出的「换包要重扫」是错的**：档案存在 `~/Library/Application Support/com.aventador.automationtool/` 下，按 **bundle identifier** 定位，路径里没有版本号，**换一个新构建的包不用重扫**。

**不对时怎么办**：重新扫码。**如果 A1 没过（node 跑不了），这一步必然失败**——界面会报「暂时无法读取抖音登录状态，请稍后重试」，因为浏览器根本起不来。先回去修 A1。

> ⚠️ **关于那两份「会骗人」的旧档案——上一版把位置写错了。**
>
> 上一版说污染档案 `df1c89f0-…` **在** `profiles/demo-xuanbai/embedded-browser-profiles/douyin/` 下。**实测不在那儿**：
>
> ```text
> demo-xuanbai/embedded-browser-profiles/douyin/  →  只有 739b9297-…（产品自建，在链路上）
>
> 两份遗留档案实际在 profile 之外的顶层：
>   com.aventador.automationtool/embedded-browser-profiles/douyin/df1c89f0-8418-…
>   com.aventador.automationtool/browser-profiles/douyin/d2265434-b5b2-…
> ```
>
> 它们都**不在链路上**，也没被清理（产品自管目录不手工动）。判据不变：**不要因为看到某个目录里有 Cookies 就以为抖音已登录**，一律以界面上的「登录正常」为准。

〔07-27 改〕三处：① 补上已完成的实测证据和剩下那一件；② 推翻「换包要重扫」这个会造成无谓返工的说法；③ 修正污染档案的实际路径（上一版指的那个位置里根本没有它）。

---

### B8 ⚠️ 完整彩排一次：一句话 → 成片播放

**这是整份清单里唯一能证明「演示内容真的能跑通」的一条。前面全绿都只证明环境和包是好的。**

**点击路径（与演示当天完全一致 —— 这次改了两步）**：

1. 左侧「**视频制作**」
2. 「**新建视频**」标签页（默认就停在这里）
3. 页面下半部分「选择制作方式」里，点那张卡片上的「**选择品牌动效成片**」按钮
   　→ 按钮上写的是完整的「选择品牌动效成片」，不是光一个「选择」；两张卡片各有一个，别点错
   　→ 点完页面会**自动把「一句话自动制作」卡片滚进视野**
4. 在输入框里写一句描述（**上限 500 字**）
5. 点「**开始自动制作**」
6. **App 自己跳到「制作任务」标签页** —— 不需要你手动切
7. 等待。「制作任务」页会显示一行走字的状态（原文见下）
8. 完成后切到「**成片**」标签页 → 点「**播放成片**」

**期望**：拿到一段**能播放的、画面在动的** mp4。

**等待期界面上的原文**（演示者必须提前知道，否则会以为卡死）：

```text
正在自动编排这条视频 · 已用 1 分 12 秒 · 通常 2 分钟左右，最长约 3 分钟
```

超过实测最长的 178 秒之后，后半句会自己换成：

```text
… · 已经超过实测最长的 3 分钟，可能是视频创作模型服务没有回应
```

**注意后半句是「可能」不是「已经失败」**——这里没有任何东西知道那件事。它只是在说「超出已知范围了，最可能的原因是这个，你可以去查」。

编排跑完、本机渲染开始时，才会出现这条：

```text
已提交一句话自动制作，编排完成，本机渲染开始了。
```

**已知固定值**（界面已明示）：**片长固定 12 秒**（= 默认 3 段 × 每段 4 秒，来自 `contracts/video/motion-storyboard-duration.v1.json`）。一句话入口**不能改片长**，界面会提示需要别的长度请用下面的「固定模板手工制作」。描述上限 **500 字**（`contracts/video/motion-one-sentence-brief.v1.json`）。

**耗时基线（这次换成了有样本量的数字）**：

| 段 | 最小 | **中位数** | **最大** |
| --- | --- | --- | --- |
| 提交 → 完成 | 85 秒 | **124 秒** | **178 秒** |

七次连续运行，**7/7 成功**，七支成片的 `ffprobe` 逐字段完全一致（360 帧 / 12.000 秒 / h264 640×360）。

**但这个最大值不可信，而且是往上不可信**：样本从 6 次加到 7 次，最大值就从 121 → 146 → 178 单调往上抬，**右尾根本没探到底**。所以演示话术**按 3 分钟讲、心里备到 4.5 分钟**。

**耗时到底花在哪（这条影响你怎么回答「怎么这么慢」）**：编排那段里**我们本机总共只花 10 毫秒**（lint + check + snapshot + 落盘），其余全是模型在写——它要吐一整份 standalone 的动效 HTML，**光这一项就占输出的 70%**。下行速率是常数（4497–4652 B/s，波动 3%），所以**耗时严格正比于模型这次写了多少字节**。本机渲染合成那段另算，约 30 秒。

**⚠️ 必须用秒表记下演示机上的实测秒数，写进 `docs/demo-runbook.md`。** 演示机没有基线。

**顺便在彩排时确认两件 runbook 需要的事**：

- [ ] 编排等待那两分钟里，界面显示的状态文字**和上面写的一致吗**？（不一致 = 装了旧包，回 A2）
- [ ] 成片能播放后，**把这一条成片留在机器上**，演示当天作为兜底素材。

**关于「提交后切到别的页面」**：**已经修好了，切走不会丢任务、不会丢句子、也不会丢失败**（`a109178` + `7c5d5d6`）。切走之后如果失败，侧边栏「视频制作」那一项会出现红色的「**失败**」角标；如果连轮询本身都失败，角标显示「**未知**」——它不会假装还在跑。

**但演示当天仍然建议不要切页。** 不是因为有风险，是因为**不切页是零成本的**：留在「制作任务」页对着那个走字的钟讲，效果一样好，还少一个变数。

**✅ 2026-07-27 实测（UI Harness，Playwright 无头）**：切页链路的两条用例都通过。

```text
$ pnpm exec playwright test e2e/video-studio-one-sentence.spec.ts e2e/video-studio-render-failure.spec.ts
  ✓ choosing brand motion brings the one-sentence entry into view
  ✓ a submission and its result survive leaving the page
  ✓ a render that fails while the operator is elsewhere still says so
```

**不对时怎么办**：

| 现象 | 大概率原因 | 动作 |
| --- | --- | --- |
| 提示「请先到"设置与诊断"配置视频创作模型服务。」 | 密钥没存 | 回 B6 |
| 「本机渲染组件暂时不可用」 | `media-toolchain` 或 `motion-video-worker` 缺失 | 回 A3 |
| 任务直接失败、无有效输出 | **`node` 跑不了（exit 133）** | 回 A1，**必须重出包** |
| 报「自动编排没能完成…这不是描述的问题：视频创作模型服务可能连不上…」 | 模型服务侧问题 | **不要去改描述**。回 B6 点「测试连接」，再查网络 |
| 「这条成片的画面自始至终没有变化…」 | 静图门禁拦下，描述太空泛 | 换一句更具体的描述重试 |
| 导入成片失败 | 磁盘不足 1 GiB + 视频大小 | 回 B1 清盘 |

〔07-27 改〕这一条改动最大，共六处：
> ① **点击路径错了两步**——按钮实际叫「选择品牌动效成片」不是「选择」；提交后 App **自己跳到「制作任务」**，上一版写的「切到制作任务标签页看进度」是多余动作。
> ② **上一版引用的提示原文「已提交一句话自动制作，可到"制作任务"查看进度。」在代码里已经不存在了。** 现在等待期显示走字的钟，完成后才出「编排完成，本机渲染开始了」。
> ③ **耗时基线从「3 分 34 秒 = 编排 178 + 渲染 33」改成「中位数 124 秒 / 最长 178 秒 / 7 次全成」。** 上一版那个数字是**单次最坏样本**被当成了典型值——而且当时那次跑的是 debug 构建的 `control-plane-e2e`，不是这条演示链路。
> ④ 补上「右尾没探到底」这个限定。没有它，178 秒会被当成上界，而它不是。
> ⑤ 补上「慢在模型不在我们」的量化依据（本机 10 毫秒 / HTML 占输出 70% / 速率是常数）。
> ⑥ 「切页会不会中断」从一个待确认项变成了已修 + 有用例，但演示建议改成「不切」。
> ⑦ 新增「自动编排没能完成」这一行失败——上一版没有它，而这正是 T90 改掉的那条：**模型服务连不上以前会被说成「你的描述做不出来」**，演示者照着旧文案会去改描述，越改越错。

---

### B9 ✅ 数据目录健康

```bash
ls -la ~/Library/Application\ Support/com.aventador.automationtool/profiles/demo-xuanbai/
```

**期望**：目录存在、当前用户可写，且以下几项齐备（B5–B8 做完之后）：

| 文件/目录 | 说明 |
| --- | --- |
| `product-account-session-v1` | 产品账号已登录 |
| `device-credential-v1` | 设备已绑定 |
| `current-douyin-profile-v1` | 指向当前抖音运营档案 |
| `model-services/` | 两份模型密钥 |
| `editing-services/` | 剪辑密钥 |
| `embedded-browser-profiles/` | 运营浏览器档案 |
| `video-workspaces-v1/artifacts/` | **彩排产出的成片应该在这里，非空** |

**权限期望**：文件均为 `-rw-------`，目录均为 `drwx------`。带 group/other 位会导致**启动即闪退**（见 B2，`T66a` 未修）。

**✅ 2026-07-27 实测（开发机 —— 这次连成片一起验了）**：

```text
drwx------@ 12 aventador staff 384 Jul 26 21:25 .
drwx------@  4 aventador staff 128 Jul 26 17:25 app-updates
-rw-------@  1 aventador staff  36 Jul 26 21:33 current-douyin-profile-v1
-rw-------@  1 aventador staff  86 Jul 26 20:19 device-credential-v1
-rw-------@  1 aventador staff  32 Jul 26 17:25 device-identity-ed25519-v1
drwx------@  3 aventador staff  96 Jul 26 20:22 editing-services
drwx------@  4 aventador staff 128 Jul 26 20:25 embedded-browser-profiles
drwx------@  4 aventador staff 128 Jul 26 17:25 local-executor
drwx------@  4 aventador staff 128 Jul 26 20:22 model-services
-rw-------@  1 aventador staff 428 Jul 26 21:25 product-account-session-v1
drwx------@  5 aventador staff 160 Jul 26 17:25 video-workspaces-v1
```

权限全部为 `-rw-------` / `drwx------`，无一例外。

**成片确实在**——这一条推翻了上一版的 D4「签名包上从未成功生成过一次视频」：

```text
$ cat video-workspaces-v1/artifacts/ee99cc9c-…/manifest.json
{"artifactId":"ee99cc9c-7e58-4e1e-a538-305162d4d8d8",
 "jobId":"448e46df-02f9-4db3-9cfd-56e2005be34c",
 "sha256":"ace0ddf0…","sizeBytes":186778,
 "mediaType":"video/mp4","role":"rendered_video"}

$ ls -la video-workspaces-v1/artifacts/ee99cc9c-…/
-rw-------@ 1 aventador staff    243 Jul 26 21:39 manifest.json
-rw-------@ 1 aventador staff 186778 Jul 26 21:39 payload
```

07-26 21:39，签名包，186 KB 的 `video/mp4`。**演示线在正式包上真实产出过成片。**

> ⛔ **绝对不要在演示机上运行本仓库任何 `scripts/run_*_acceptance.py`**，尤其 `scripts/run_u9_06_acceptance.py`——**它会删掉整个 App 私有数据目录**，包括前一天辛苦扫码的抖音登录态和刚彩排出来的成片。
>
> 具体后果：演示当天早上有人抱着「我验证一下环境」的好意跑了它 → 清单里全绿过的条目在开场后当场失效 → 现场要掏手机重新扫码，而二维码还会过期。
>
> **演示机只用来演示，不用来验证。要验证就在开发机上验。**

〔07-27 改〕补 `current-douyin-profile-v1` 一行；补全部实测输出；用 artifacts 里那个真实成片推翻了上一版 D4。

---

## C 段 · 演示当天开场前 10 分钟

**只做这六件，不要临时改任何东西。**

| # | 做什么 | 期望 | 预计 |
| --- | --- | --- | --- |
| C1 | 在**会场网络**下跑 5 次 health | `200 200 200 200 200` | 1 分钟 |
| C2 | 打开 App，确认停在工作台 | 八项导航齐全，无白屏，最近任务显示的是时间不是 UUID | 1 分钟 |
| C3 | 「平台状态」确认抖音仍「登录正常」 | 不要求扫码 | 1 分钟 |
| C4 | `df -h /` 再看一眼磁盘 | ≥ 20 GB 可用 | 30 秒 |
| C5 | 「视频制作 → 成片」确认彩排那条成片还在、能播 | 能播放 | 1 分钟 |
| C6 | 电源接上、Wi-Fi 稳定、**关闭系统通知（勿扰模式）** | — | 1 分钟 |

C1 的命令：

```bash
for i in 1 2 3 4 5; do
  curl -sS -o /dev/null -w '%{http_code} ' --max-time 10 https://at.xuanbai.tech/api/v1/health
done; echo
```

**C1 是整份清单里最容易被跳过、也最容易翻车的一条：办公室通不代表会场通。**

**任何一条不过就不要按原脚本开始演示**，改走 `docs/demo-runbook.md` §5 的降级路径。

〔07-27 改〕只动了 C2：顺手加一句「最近任务显示的是时间不是 UUID」。这是 B4 那四条目视里最快的一条，白送的一次装错包复查。C 段其余不动——它只有十分钟，加检查项等于把它变成 B 段。

---

## D 段 · 待确认清单

下面每一条都**还不知道答案**，写在这里是为了不让「没查」被当成「查过了」。

**07-27 重核后，上一版十条里有四条已经有答案，从本表移出**（去向见下面「已结案」）。

| # | 待确认的事 | 为什么重要 | 谁去确认 |
| --- | --- | --- | --- |
| D1′ | **含界面修复与 DMG 修复的新包尚未构建** | 当前唯一的阻塞项。见本文件开头那节。代码全在主线，缺的只是再出一次包 + 走完 A 段 | 发版线 |
| D2 | 演示机是哪台、谁操作、懂不懂技术 | 决定 B 段做到多细、runbook 写到多细；也决定 A8 是「体验问题」还是「阻塞问题」 | 项目负责人 |
| D3 | 演示机上一句话生成的**实际耗时** | 开发机中位数 124 秒是 debug 构建 + 开发机网络；演示机无基线 | B8 彩排时实测 |
| D7 | 会场网络能否稳定访问 `at.xuanbai.tech` | 无离线降级 | C1，演示当天 |
| D8 | 是否补背景音乐（T71） | 影响成片观感；当前四个自制音效是 1–2 秒音效不是 BGM | 用户决策 |
| D9 | Windows 侧验收 | 本轮演示形态是 macOS，Windows 另算 | Windows 验收线 |
| D10 | 智能素材成片线可行性 | 素材源 Key（pexels）为空是硬阻塞，上游 `get_api_key` 空列表直接抛错 | 已决定不演（见 runbook §4） |
| **D11** 🆕 | **抖音登录态跨 App 重启是否保住** | B7 唯一未闭环的一项，**30 秒能做完**：完全退出 App 再打开，看还是不是「登录正常」 | 演示机（或现在就在开发机上做一次） |
| **D12** 🆕 | **更新中心在正式包上的目视** | T89 把「未启用更新」从红色 `failed` 挪进中性的 `disabled`，但那条改动**最高只验到 UI Harness**。正式包上它长什么样没人看过 | B4 时顺手看一眼「设置与诊断」 |
| **D13** 🆕 | **侧边栏那个红色「失败」角标会不会被裁掉** | 侧栏宽 232px，Ant Menu 带 `overflow: hidden`。jsdom 和 Playwright 的 `toBeVisible()` **都不判定裁剪**——这是 T91 自己登记的边界 | 正式包上目视 |

### 已结案（上一版的 D1/D4/D5/D6）

| 原编号 | 原来的问题 | 现在的答案 |
| --- | --- | --- |
| D1 | 带 JIT 修复的新包是否已构建/签名/公证 | **已完成**。A1 实测两个 node 全部 `rc=0`，A4 四项签名公证全过。（但该包**另有**两个问题，见 D1′） |
| D4 | 签名包上从未成功生成过一次视频 | **已推翻**。07-26 21:39，签名包，186 KB `video/mp4` 落盘，证据在 B9 |
| D5 | 签名包上抖音登录从未成功过 | **已推翻**。扫码通过，服务端 `state = healthy`，本机档案 `739b9297-…`，证据在 B7 |
| D6 | 生成中能否切走页面再切回 | **能，且已修**（`a109178` + `7c5d5d6`），有 e2e 用例守着。**但演示当天仍建议不切**——零成本的规避 |

### 明确不打算在演示前解决的已知缺陷

写在这里是为了让演示者**认得出它们**，而不是当场以为出了新问题。

| 缺陷 | 会看到什么 | 演示时的处理 |
| --- | --- | --- |
| `T50` 安全注销成功但界面报失败 | 注销其实成功了，界面报失败 | **不演**（runbook §4） |
| `T100` 视频剪辑页比视窗高 7px | 1280×800 下整页出现滚动条 | **不演**（runbook §4） |
| `T99` 第三方软件声明页的折叠控件把英文 `collapsed` 读进可访问名 | 只有屏幕阅读器听得出来 | **不主动打开那一页** |
| `T66a` 密钥文件权限过宽会导致启动即闪退 | 闪退，零提示 | B2 + B9 提前查掉 |

〔07-27 改〕D 段整体重排：四条结案移进「已结案」表并写明答案；新增 D11/D12/D13；新增「明确不打算解决的已知缺陷」一节（上一版这些散落在 runbook §4 里，清单侧看不到）。

---

## E 段 · 本文件的证据分级

| 分级 | 条目 |
| --- | --- |
| **✅ 2026-07-27 已真实执行并贴了输出** | A1（两个 node 求值 + 门禁脚本）、A2（包时间戳 vs 最后一笔界面提交，**判定不合格**）、A3（五份资源）、A4（stapler×2 + spctl×2 + codesign）、A5（无测试痕迹）、A6（200 / 401 两条）、A7（服务器凭据 0600）、A8（挂载后**无** `Applications` 链接，**判定不合格**）、B1（开发机）、B2（四行祖先目录）、B5/B6/B9（文件、权限、成片 manifest）、B7（抖音档案指针与目录）、B4/B8 的界面事实（UI Harness，Playwright 无头，共 28 条用例通过） |
| **📋 命令形式已验证，但结论对演示机不成立** | C1（本机跑过，会场网络必须重跑）、B1（开发机的磁盘和芯片不代表演示机）、B4/B8 的界面事实（Harness 跑的是仓库 HEAD 的 React，**不是安装包里那份**） |
| **⬜ 只能在演示机上验，本机做了不算** | B0、B3（含挂载后的目视与 Gatekeeper 同意框——**这一下至今没有任何自动化真实走过**）、B4（八项导航 + 四条新包目视）、B5–B8 在演示机上的重做、C2–C6 |
| **⬜ 从未执行** | D11（App 重启后抖音登录态）、D12（正式包上的更新中心目视）、D13（侧边栏角标是否被裁） |

**一条从没执行过的检查命令，和一道从没运行过的门禁是同一种东西。** 上面这张表要一直留着，做一条划掉一条，不要让「写在清单里」被当成「验过了」。

**这次重核本身就是这句话的例子**：上一版有 11 条标着「⬜ 从未执行」，其中 A2 和 A8（当时还不存在）一跑就是红的——**而它们红的那两件事，恰恰是客户会第一个撞上的**（装不进去、装进去了是旧界面）。

〔07-27 改〕整表重建。上一版「从未执行」有 11 条，这次跑掉了其中 4 条（A2、A5、A7 + 新增的 A8），剩下的按「只能在演示机上验」和「真的还没做」分开了——这两种在上一版里混在同一格，看不出哪些是能做而没做的。
