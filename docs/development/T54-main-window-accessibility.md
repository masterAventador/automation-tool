# T54 复核：正式包主窗口"不向辅助功能暴露"

> 状态：✅ 已完成（纯复核任务，未改产品代码）
>
> 结论：**这不是产品缺陷。** T48 §6 的观测是在**锁屏状态**下做的；锁屏时
> 本机上**每一个** App 都不向辅助功能暴露窗口。判据本身不成立，产品不需要修。
>
> 日期：2026-07-26
>
> 提交：本文件所在提交

## 1. 被复核的结论

`docs/development/T48-package-cloud-vertical.md` §6 记录：

```text
AXWindows 那一项 role 是 AXApplication 而不是 AXWindow
AXApplication 自嵌套 24 层，3000 节点广度优先扫描全是菜单（AXMenuItem 2580 个）
全树没有任何 AXWindow / AXWebArea / AXTextField，90 秒都没出现
```

同一时刻窗口服务器却报 `CGWindow number=51867 layer=0 alpha=1 onscreen=1`。
T48 据此判定「正式包的主窗口不向辅助功能暴露」，并推出两条影响：挡住 AX 自动化、
**读屏用户无法使用本 App**。

T48 给后续排查留下的判据是这对矛盾：辅助功能侧窗口数 0 且 `visible` 为 true，
窗口服务器侧同时看得到 `layer=0 onscreen=1` 的真实窗口。

## 2. 复核方法：先验判据，再验产品

没有直接去启动正式包（那会重复付一次可见窗口的代价）。先问一个更便宜的问题：
**这条判据用在明显没有无障碍缺陷的 App 上，会不会也"成立"？**

会。而且是全部成立。

### 2.1 对照组一：本机正在运行的成熟 App

同一时刻，逐个查窗口服务器与辅助功能两侧（`CGWindowListCopyWindowInfo` 按
`kCGWindowOwnerPID` 过滤 / `System Events` 的 `count of windows`）：

| App | 窗口服务器 | 辅助功能 |
| --- | --- | --- |
| ghostty | 25 个窗口，含 `layer=0 onscreen=1` | **0** |
| Google Chrome | 11 个窗口，含 `layer=0 onscreen=1` | **0** |
| Visual Studio Code | 6 个窗口，含 `layer=0 onscreen=1` | **0** |
| Finder / WeChat / sublime_merge / Activity Monitor | — | **0** |

`role of UI elements` 也复现了 T48 描述的退化形状，例如 ghostty：
`AXApplication, AXApplication, AXMenuBar` —— **AXApplication 自嵌套在成熟 App 上一样出现**。

Chrome 与 VS Code 都是无障碍支持完善的产品。判据在它们身上"成立"，说明判据无效。

### 2.2 对照组二：30 行 AppKit 程序

为排除"是不是这台机器上所有 Electron/WebView 类 App 都有问题"，另写了一个最小对照：
`NSApplication` + 一个 `NSWindow`，`alphaValue = 0` 保证用户看不见，
`orderFrontRegardless()` 保证不抢焦点，打成最小 `.app` 后 `open -n -g` 启动。

```text
CGWindow num=51929 layer=0 onscreen=1 alpha=0 bounds={400×332}
System Events → {name=AXProbeControl, visible=true, count of windows=0}
role of UI elements → AXApplication, AXMenuBar
```

一个只有一个 NSWindow、不可能有无障碍缺陷的程序，同样"命中缺陷"。

## 3. 根因：观测发生在锁屏期间

`loginwindow` 的锁屏状态切换（`log show --predicate 'process == "loginwindow" AND
eventMessage CONTAINS "setNotifySharedSpace: com.apple.sessionagent.screenIsLocked"'`）：

```text
2026-07-26 13:04:22  → 1   锁屏
2026-07-26 16:09:27  → 0   解锁
2026-07-26 16:27:01  → 1   锁屏（当前状态，CGSSessionScreenIsLocked = 1）
```

把三条时间线对上：

| 时刻 | 锁屏 | 辅助功能侧窗口数 |
| --- | --- | --- |
| 13:19 DMG 生成 → 13:26～13:39 T48 四次启动 App → 14:19:39 T48 台账提交 | **锁屏中**（13:04–16:09） | T48 实测：0，90s 未出现 |
| 16:19～16:20（本任务，解锁窗口内） | 否 | ghostty **2**、Chrome **1** |
| 16:27 之后（本任务，再次锁屏） | **是** | ghostty 0、Chrome 0、Code 0、AppKit 对照组 0 |

同一台机器、同一批 App，**只有锁屏状态变了，辅助功能侧的窗口数就在 2/1 与 0/0 之间来回**。

T48 的四次启动（`runningboardd` 日志中 13:26:22～13:39:28，pid 5678 / 6635 / 10500 / 11529）
和它的台账提交（14:19:39）全部落在 13:04:22–16:09:27 这个锁屏区间内。

**结论：macOS 锁屏时不向辅助功能客户端暴露任何 App 的 AXWindow；窗口服务器不受影响。
T48 观测到的"矛盾"就是这个系统行为，不是本产品的属性。**

## 4. 对 T48 的两处更正

1. **「正式包的主窗口不向辅助功能暴露」不成立**，「读屏用户完全用不了这个 App」也就没有依据。
   本产品在这一点上和 Chrome / VS Code 表现一致。
2. **「窗口在用户屏幕上可见约 3.5 分钟」也不成立**。那段时间（13:26–13:39）屏幕处于锁屏/屏保状态，
   屏幕上显示的是锁屏界面，没有任何人看见过那个窗口。T48 因此设下的
   「不要付第二次可见代价」的警告，前提是错的。

已在 T48 文件对应位置加了指向本文件的更正块，不改写它的原始记录。

## 5. 怎么重新验（给后续）

先查状态，再查产品 —— 顺序反了就会重走 T48 的老路：

```bash
# 1. 先确认屏幕没锁；返回 1 就别做任何辅助功能测量
swift -e 'import CoreGraphics
print((CGSessionCopyCurrentDictionary() as? [String:Any])?["CGSSessionScreenIsLocked"] ?? "nil")'

# 2. 再拿一个已知良好的 App 当基线；这一步返回 0 就是环境问题，不是被测产品的问题
osascript -e 'tell application "System Events" to return count of windows of ¬
  (first process whose name is "Google Chrome")'
```

两步都过了，再去测本产品才有意义。

## 6. 仍未做的

- **解锁状态下对本产品的正向确认没有做。** 判据被推翻不等于正向证明；
  要拿到"本 App 的窗口确实进 AX 树"这条正证据，需要在屏幕解锁时启动一次 App 并查询。
  这需要用户在机器前，且会产生一个可见窗口，本任务按约定没有自行决定。
  代价约 15 秒，建议并入下一次有人在机器前的操作里顺手做掉。
- 顺带存疑（不属本任务，未验证）：`docs/development/EB-16.md:233` 记的
  「`screencapture` 只能拿到桌面壁纸」也发生在同类环境里。壁纸-only 是缺少「屏幕录制」权限的
  典型表现，但值得在解锁状态下复查一次再下结论。

## 7. 真实边界与清理

- 本任务**没有启动过正式包**，没有构建任何东西，没有改动任何产品代码；
- 对照实验用的两个 Swift 探针只写在会话临时目录，未进入仓库；
  探针窗口 `alphaValue = 0` 且 `open -g` 不夺焦点；
  唯一一次可见性事故是第一版未打包探针的 400×332 窗口在屏幕左下角存在约 2 秒
  —— 当时屏幕处于锁屏状态，无人可见，随后所有版本都改为 alpha 0；
- 探针进程已全部退出（`pgrep -f AXProbeControl` 为空），临时 pid 文件已删除；
- `~/Library/Application Support/com.aventador.automationtool/` 全程未读写；
- 未运行 `scripts/run_u9_06_acceptance.py`；未触碰其他线在跑的 Docker 资源与构建。
