# 查证：Windows 上 WebView2 的可访问性树默认是空的

用户可操作：否

证据类型：查证

> 日期：2026-08-05
>
> 提交：本文件所在提交

## 为什么查这个

EB-11 的 Windows runner 要做的第一件事就是「在正式 App 里按下一个具名按钮」。macOS 走
AppleScript 遍历 AX 树；Windows 只能走 UIAutomation，而**全仓从没有人读过 WebView2 里的
任何一个控件**——`run_p9_02_acceptance.py:42` 只探测了「UIA 运行时存在」。

这一项不成立，后面三项（验签、Profile 归属、inode 无名）全部白做，所以先验它。

## 实测

在刚出的正式包上（`%LOCALAPPDATA%\自动化运营工具`，安装自
`自动化运营工具_0.1.0_x64-setup.exe`）用外部 PowerShell 进程读同一个 PID 的 UIA 树：

| | 默认启动 | 设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--force-renderer-accessibility` |
| --- | ---: | ---: |
| Descendants | 17 | **27** |
| 具名元素 | 2 | **9** |
| 能读到按钮 | 否 | **是** |

默认启动时只有外壳：

```text
Pane  自动化运营工具
Pane  自动化运营工具 - Web 内容
```

加 flag 之后 WebView 内容出现：

```text
Document  enabled=True  自动化运营工具
Text      enabled=True  暂时无法连接业务服务
Button    enabled=True  重新检查
Text      enabled=True  控制服务不可用
```

（界面显示「暂时无法连接业务服务」是因为当时没起控制面，与本结论无关。）

## 结论

**可行，但必须由 runner 在启动那一刻设置那个环境变量。** Chromium 系的可访问性是惰性的：
不检测到 a11y 客户端就不建树，WebView2 继承了这个行为。

**这不改产品。** 环境变量由 runner 设置，出厂的 App 一个字节不变——与 macOS 上 runner
依赖系统 a11y 栈是同一性质，不是给正式包开测试后门。

**一处比 macOS 更好**：`IsEnabled` 是 UIA 的原生属性，直接可读。macOS 那边为这件事踩过坑
——`AXPress` 对禁用元素照样返回成功，两种情况输出完全相同，2026-08-04 专门修过一轮
（`test_press_refuses_a_control_that_is_only_visible_not_enabled`）。Windows 不需要那种绕法。

## 正常用户路径验收

不适用——本文件是查证结果，不新增用户入口。

## 真实边界

- 只证明了「树可读、按钮带 enabled」。**没有证明可以按下去**：`InvokePattern.Invoke()` 尚未
  实测，属于下一步；
- 该 flag 对内置运营 Chromium（Playwright 起的那个）无效，它不是 WebView2；EB-11 需要观测
  的浏览器进程走的是另一套手段（句柄枚举），与本结论无关。

## 清理

探测进程已终止，`Get-Process automation-tool-desktop` 为 0。正式包保留安装以供后续验收。

## 文档变化

本文件为新增。

## 遗留项

| 项 | 状态 |
| --- | --- |
| `InvokePattern.Invoke()` 实测 | 待办 |
| 对运行中 PID 验 Authenticode | 待办，且**本机无证书，这道门必须重新设计**，不能假装验过 |
| 证明浏览器打开的是哪个 Profile（句柄枚举） | 待办 |
| 证明已删 Profile 的 file id 再无名称 | 待办 |
