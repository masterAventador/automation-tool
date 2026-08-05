# 交接：Windows 出包与 EB-11 Windows 验收

用户可操作：否

证据类型：文档

> 日期：2026-08-05
>
> 提交：本文件所在提交

**这份是给上下文压缩之后的自己看的。** 只写继续干活需要的东西，不复述已完成任务的细节
——那些在各自的证据文件里。

## 分支与提交

工作全部在 `windows-release`，从 `main` 拉出，已 rebase 到 `4ca53fdd5` 之上。

| 提交 | 内容 |
| --- | --- |
| `a3a1a476` | 离线目录收进机器缓存 + 删重复 `DEFAULT_ARCHIVES` + 缓存根一致性门禁 |
| `23a454a5` | `--platform windows` 从拒绝改为真实出包 |
| `08f977d4` | EB-11 runner 平台适配层 + 三处 Windows 缺陷 |
| `1a053d33` | 路径预算门禁 + 执行器清单校验路径 |
| `dfc14718` | `build-id` 按平台派生 |
| `dbfc4e7f` | WebView2 可访问性查证 |

`a3a1a476` 里混进了在途的 Windows 改动和 EB-18 撤销（当时用了 `git add -A scripts/`），
用户已知，未拆。

## 现在能做什么

`build_release_package.py --platform windows` **连续两次干净跑通**：

```text
backend\.venv\Scripts\python.exe scripts\build_release_package.py \
  --platform windows --work-dir C:\atrel
→ REAL_EXIT=0，10 分 27 秒
  安装包 450,917,908 字节 / 主二进制 27,181,568 字节
  内嵌 commit 与 HEAD 逐位一致，Authenticode 实测 NotSigned
```

**工作目录必须短。** `require_windows_path_budget` 会在构建前拒绝超长的——`.local/release-windows`
（41 字符）会被拒，`C:\atrel`（8）通过。原因见该函数上方注释。

## 下一步：EB-11 的 Windows runner

`scripts/run_eb_11_formal_app_acceptance.py` 已有 `DeviceDriver` 适配层：
`MacosDeviceDriver` 保持原行为，`WindowsDeviceDriver` 按能力逐项到位，未实现的各自具名报错。
**已实现**：`read_release_identity`（读 `release-identity.v1.json`）。

四项待办，按建议顺序：

### 1. UI 驱动（最大未知已解，只剩实现）

`FINDING-webview2-accessibility-on-windows.md` 实测确认：**必须由 runner 在启动 App 时设
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--force-renderer-accessibility`**，否则 UIA 树里
只有两个外壳 Pane，按钮和文字一个都读不到。加了之后 Document/Text/Button 全出现且带
`IsEnabled`。

**尚未实测 `InvokePattern.Invoke()`**——只证明了能读，没证明能按。这是下一步第一件事。

可用的探测脚本在 scratchpad（会随会话清掉，必要时重写，内容见该查证文件）。

`IsEnabled` 是 UIA 原生属性，所以 macOS 那个「禁用元素照样返回成功」的坑在 Windows 不存在，
不要照搬那套绕法。

### 2. 证明浏览器打开的是哪个 Profile

macOS 用 `lsof -nP -Fn -p <pids>`。Windows 要走句柄枚举（`NtQuerySystemInformation` /
handle 表 + `GetFinalPathNameByHandle`），纯实现，无设计疑问。

### 3. 证明已删 Profile 的 file id 再无名称

macOS 用保留的目录句柄 + `fcntl F_GETPATH`。Windows 对应 NTFS file id +
`GetFinalPathNameByHandle`，纯实现。

### 4. 对运行中 PID 验 Authenticode —— **需要用户拍板，不要自行决定**

macOS 对活 PID 做 `codesign` 动态验签。**本机没有 Authenticode 证书**
（`BLOCKERS-2026-08-04.md` 记录两处证书库均为 0），`Get-AuthenticodeSignature` 只会返回
`NotSigned`。

这道门是降级成「实测记录状态」，还是别的形态，涉及「这个 Windows 包到底能宣称什么」——
属于产品主张，必须问用户，不得自行降级后当成验过。

## 环境现状

| 项 | 状态 |
| --- | --- |
| 正式包已安装 | `%LOCALAPPDATA%\自动化运营工具`，保留供扫码验收 |
| 出包产物 | `C:\atrel`（可随时删，重跑约 10.5 分钟） |
| 机器级缓存 | `%LOCALAPPDATA%\automation-tool-build`，1.36 GB，浏览器归档与解包树、离线动效目录均已迁入 |
| 探测进程 | 已清零 |

## 这一路踩过的坑，别再踩

1. **后台跑长命令时不要在命令后面拼东西取退出码。** `cmd > log 2>&1; echo "exit=$?"` 里
   harness 看到的是 `echo` 的 0，构建已经死在 traceback 里还报成功。判定要读日志里由
   `code=$?` 紧跟被测命令捕获的那个值；
2. **探路脚本的路径前缀要和真实场景一样长。** 用 `.local/eb18-clone-probe/` 探 `MAX_PATH`
   通过，真实快照路径直接爆——同一段代码，两个结论；
3. **不要用 PowerShell 改源码。** `Set-Content -Encoding utf8` 会加 BOM，让变异确认红在
   `SyntaxError` 上而不是红在被测性质上。用编辑工具；
4. **门禁常量要量不要猜。** 路径预算第一版猜 218，放行了已实测失败的路径。一个放行了已知
   失败输入的预算不是预算；
5. **跑通一次不等于对。** 第一次完整跑通 exit 0，去读产物才发现 `buildId` 写着
   `macos-release`。出包必须核对产物内容，不只看退出码；
6. **提交前逐个文件看 diff。** `git add -A scripts/` 卷进了不该进那个 commit 的改动。

## 正常用户路径验收

不适用——交接文档。

## 真实边界

Windows 出包已两次跑通并核对产物；EB-11 Windows 验收**一步都没走**，runner 的四项观测手段
只完成了适配层骨架与发布身份读取。macOS 侧本轮改动均未复跑。

## 清理

无待清理资源；探测进程已终止，正式包安装为有意保留。

## 文档变化

本文件为新增。

## 遗留项

见上文四项，以及第 4 项需要用户决策。
