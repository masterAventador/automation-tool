# 交接：Windows 出包与 EB-11 Windows 验收

用户可操作：否

证据类型：文档

> 日期：2026-08-05（第二版，取代 08-05 首版）
>
> 提交：本文件所在提交

**这份是给上下文压缩之后的自己看的。** 只写继续干活需要的东西，不复述已完成任务的细节
——那些在各自的证据文件里。

## 首版错在哪里，先说清楚

首版把 EB-11 的 Windows 缺口写成「四件观测手段」。**那是低估。** 真实缺口是把一个
2500 行、从头到尾按 macOS 写的验收 runner 移过来，而且其中一件不是遗留项而是前置：

- 首版说 Authenticode 是四件之一 → 实际上 EB-11 的**制品验证整条**建立在签名上
  （`codesign --verify` 整包 + `spctl` Gatekeeper + `stapler` 公证 + 活 PID 的 cdhash +
  浏览器的 Developer ID）。**已由用户 2026-08-05 拍板改为摘要绑定**，见下；
- 首版没提：进程快照与归属、启动方式、正常退出、`APP_DATA` 路径、`Info.plist` 身份、
  target 映射。这些同样一件都没有 Windows 实现。

写下这条不是自责，是因为**下一个人会照着交接估工期**，而首版给出的估计是错的。

## 分支与提交

工作全部在 `windows-release`，从 `main` 拉出。

| 提交 | 内容 |
| --- | --- |
| `a3a1a476` | 离线目录收进机器缓存 + 删重复 `DEFAULT_ARCHIVES` + 缓存根一致性门禁 |
| `23a454a5` | `--platform windows` 从拒绝改为真实出包 |
| `08f977d4` | EB-11 runner 平台适配层 + 三处 Windows 缺陷 |
| `1a053d33` | 路径预算门禁 + 执行器清单校验路径 |
| `dfc14718` | `build-id` 按平台派生 |
| `dbfc4e7f` | WebView2 可访问性查证 |
| `ceb8f73d` | 交接（首版） |
| `297dd388` | **出包漏装发布身份**，修 + 门禁 |
| `5efa6373` | Windows UI 三件仪器：读、按、等窗口 |
| `1bae5d93` | 制品改摘要绑定（用户拍板） |
| `ea1a4d4e` | 进程归属两条路线实测，定为读 PEB |
| `c34b44fd` | Windows 进程事实读取模块 |
| `ce381355` | 交接（本文件，第二版） |
| `0179d9be` | 进程层接进 runner（含 `Contents/` 前缀那处沉默假设） |
| `a8c16c16` | 启动、正常退出、强制清理 |
| `5816680e` | 打开的文件审计（`lsof -p` 对应物） |
| `4f007741` | 资源布局与发布目标按宿主派生 |

## 出包：可用，且这次真的完整

```text
backend\.venv\Scripts\python.exe scripts\build_release_package.py \
  --platform windows --work-dir C:\atrel
→ 约 10.5 分钟，安装包 450,917,908 字节
```

**工作目录必须短**（`require_windows_path_budget`）：`.local/release-windows`（41 字符）
会被拒，`C:\atrel`（8）通过。

**`297dd388` 之前出的包都缺 `release-identity.v1.json`**，EB-11 读不到。现在出包会在
`makensis` 之前查一道。**当前装着的那个包是修复前的，缺这个文件**——要跑 EB-11 必须重出。

### 出包时机：必须在所有源码改动之后

EB-11 把包里的 `sourceTreeSha256` 与当前源码树逐位比对
（`run_eb_11_formal_app_acceptance.py:854`，不一致直接判「不是从当前源码树构建的」）。
`docs/development/**` 不进摘要，所以「出包 → 跑验收 → 写台账」是成立的；
但**改一行 runner 代码就作废一个包**。顺序固定：runner 全部改完 → 出包 → 装 → 跑。

## EB-11 Windows：已有什么

`DeviceDriver` 适配层，`MacosDeviceDriver` 保持原行为，`WindowsDeviceDriver` 逐项到位。

| 能力 | 状态 |
| --- | --- |
| `read_release_identity` | ✅ 读安装根的 `release-identity.v1.json` |
| `press` / `visible_ui_text` / `wait_for_window` | ✅ UIA，**实测按得动**（连接计数 1→2） |
| `verify_artifact` | ✅ 摘要绑定，`code_signing` 记录实测 Authenticode 状态 |
| `process_snapshot` / `process_has_launch_nonce` / `packaged_prefix` | ✅ 真实 App 上认领成立 |
| `start_app` / `request_quit` / 强制清理 | ✅ 起停实测，且有对照证明 `request_quit` 承重 |
| `read_process_open_paths` | ✅ 系统句柄表 + `GetFileType` 防挂死 |
| `resource_root` / `expected_release_target` | ✅ 真实安装包上两个清单都在位 |

### 启动 App 必须带这个环境变量

```text
WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--force-renderer-accessibility
```

不带的话 UIA 树里只有两个外壳 Pane，按钮和文字一个都读不到（Chromium 可访问性是惰性的）。
常量已在 `run_eb_11_formal_app_acceptance.py`：`WEBVIEW_ACCESSIBILITY_ENVIRONMENT` /
`WEBVIEW_ACCESSIBILITY_ARGUMENT`。

## EB-11 Windows：还缺什么（按阻塞顺序）

`verify_release_artifact()` 是整条运行的入口，它自上而下第一个卡住的地方就是下一件事。

### 1. `read_identity()` —— 第一个卡点

macOS 读 `Info.plist` 拿 `CFBundleIdentifier` / `CFBundleShortVersionString` /
`CFBundleExecutable`。Windows 没有 plist，建议：

- 可执行文件：`windows_product_binary()`（已有）；
- 版本：主二进制的版本资源（`GetFileVersionInfoW` 的 `ProductVersion`）；
- bundle identifier：**别凭空造**。二进制里编译进了 Tauri 配置，
  `compiled_deployment_profile_root()` 已经在用「扫二进制找一段字节」这个手法，
  同样手法可以断言它确实带着 `APP_IDENTIFIER`。

### 2. 部署 Profile 需要一个 `demo-*` 档案

`compiled_deployment_profile_root()` 要求 `profileId` 匹配 `demo-[a-z0-9-]+`，
并且那段 base64 必须真的出现在主二进制里。**本机跑要么造一个 demo profile 出包，
要么这条门也得换。** 这一条会决定 `--deployment-profile` 参数怎么给，先想清楚再动手。

### 3. `bind_runtime_code_identities()` / `verify_runtime_process_identity()`

macOS 对可执行文件和活 PID 跑 `codesign`。按已决策的摘要绑定，Windows 的对应物是
「进程的映像路径 == 验过的那个可执行文件，且该文件摘要 == 验过的摘要」。
`CodeIdentity` 加一个 `image_sha256` 字段，macOS 字段保持不变、带默认值——
和 `ArtifactFacts` 那次一样的做法。

**弱在哪里要写清楚**：macOS 验的是加载进内存的代码，Windows 验的是磁盘上那个文件
（运行中被映射，不能被替换，但可以被改名）。

### 4. 已删 Profile 的「再无名称」

macOS 保留目录句柄 + `fcntl F_GETPATH`。Windows 用 NTFS file id +
`GetFinalPathNameByHandle`：删除前拿到 file id，删除后确认这个 id 解析不出任何名称。
句柄枚举那一半已经做完了（`windows_processes.open_file_paths`），可以照抄它的
`GetFinalPathNameByHandle` 用法。

### 5. 私有目录判定与目录句柄

- `require_private_directory_identity()` 用 `st_mode == 0o700` 和
  `st_uid == os.geteuid()`。Windows 上 `st_uid` 无意义、`st_mode` 只表达只读位，
  **这条要换成 ACL 检查还是换成别的，是个真问题**，别糊过去；
- `open_absolute_directory()` 用 `os.O_DIRECTORY`（Windows 没有）。
  打开目录句柄要走 `CreateFileW` + `FILE_FLAG_BACKUP_SEMANTICS`
  （`test_windows_processes.py` 里那个握目录的用例就是这么开的，抄它）。

### 6. 最后再放开闸门

`require_device_boundary()` 目前 `if driver.platform != "darwin": raise`，另外要求
`.app` 后缀、`os.geteuid() == 0` 检查。**这三条留到最后改**——在上面五条完成之前放开，
只会让运行死在更深、更难读的地方。

另有两条 macOS 系统浏览器 Profile 路径（`~/Library/Application Support/Google/Chrome`
等），用于「不碰用户日常浏览器」的检查，Windows 要换成 `%LOCALAPPDATA%` 下对应目录。

`APP_DATA` 同理：**要和 Rust 侧 `app_data_dir` 实际用的那个一致，去代码里核，别猜。**

## 制品验证：已决策，别再自行改

**2026-08-05 用户拍板：Windows 用摘要绑定，并明说没有 OS 信任链。**

本机 `Cert:\CurrentUser\My` 与 `Cert:\LocalMachine\My` 的代码签名证书都是 0，
装好的主二进制实测 `NotSigned`。摘要绑定证明「装的就是这棵源码树出的那个包」，
**不证明**「系统信任它」「取摘要前没被改过」。差别写在数据里：签名者字段留空，
`code_signing` 记录实测状态，证据 JSON 多一个 `codeSigning` 键。

不是永久放弃 Authenticode：状态每次实测，有证书那天证据自己会变，有用例守着。

## 这一路踩过的坑，别再踩

1. **后台跑长命令不要在命令后面拼东西取退出码。** `cmd > log 2>&1; echo "exit=$?"` 里
   harness 看到的是 `echo` 的 0；
2. **探路脚本的路径前缀要和真实场景一样长**（`MAX_PATH`）；
3. **不要用 PowerShell 改源码**，`Set-Content -Encoding utf8` 会加 BOM；
4. **门禁常量要量不要猜**（路径预算第一版猜 218，放行了已实测失败的路径）；
5. **跑通一次不等于对**：第一次完整跑通 exit 0，读产物才发现 `buildId` 写着
   `macos-release`；**第二次跑通，读安装目录才发现整个身份文件没进去**；
6. **提交前逐个文件看 diff**；
7. **改了分发就要重查用例还测不测得动**：`press` 改成按宿主分发后，三条 patch
   `apple_script` 的用例在 Windows 上根本不碰被 patch 的函数，其中一条**仍然是绿的**
   ——UIA 对不存在的 pid 同样抛 `AcceptanceFailed`、异常里同样带标签名；
8. **`os.O_NOFOLLOW` 在 Windows 上不存在**，`bundle_tree_digest` 原本无条件用它。

## 正常用户路径验收

不适用——交接文档。

## 真实边界

Windows 出包已多次跑通并核对产物，**但当前装着的包是身份修复之前、走 EB-16 路径出的**
（清单里 `build_id` 是 `eb-16-windows-release`），跑验收前必须重出重装。

EB-11 Windows 验收**一步都没走**。仪器侧已实测可用的部分见上表；`verify_release_artifact`
自上而下第一个卡点是 `read_identity`。macOS 侧本轮改动均未复跑（本机是 Windows），
改动形状一律是「加可选字段 + 委托到原实现」，macOS 代码路径未变。

## 清理

无待清理资源；探测进程已终止（`python` 本项目路径 0、`automation-tool-desktop` 0、
8765 端口无监听），正式包安装为有意保留。

## 文档变化

本文件由 2026-08-05 首版整体重写。

## 遗留项

见上文「还缺什么」五节。制品验证一项已决策，不再是遗留。
