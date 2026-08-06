# FIX：Windows 包写了发布身份，却从来没把它装进去

用户可操作：否

证据类型：分层实现

> 日期：2026-08-05
>
> 提交：本文件所在提交

## 症状

2026-08-05 那次 Windows 出包一路绿灯，退出码 0，产物尺寸对得上，日志里还专门有一行

```text
Release identity written to release-identity.v1.json
```

而装出来的 App 根目录是这样：

```text
%LOCALAPPDATA%\自动化运营工具\
  automation-tool-desktop.exe   embedded-browser\   local-executor\
  material-video-worker\        media-toolchain\    motion-catalog\
  motion-video-worker\          uninstall.exe
```

**没有 `release-identity.v1.json`。** 文件确实被写了——在 `C:\atrel\build\payload\` 里躺着，
335 字节，内容正确。它只是从来没有离开过构建目录。

## 为什么会这样

两件事各自都对，中间没有人把它们连起来：

- `embed_windows_release_identity()` 往 payload 写文件；
- `bundle.resources` 由 `contracts/quality/release-package-resources.v1.json` 派生，
  NSIS 只搬这张表里的东西。

发布身份不是那张表里的资源，于是它不在表里，于是它不被搬。**而这张表的派生本身是对的**
——正是它保证了六棵资源树不会被漏掉，2026-07-26 漏掉三棵视频资源的事故就是它来兜的。

代价是 EB-11 的 Windows 侧从一开始就不可能通过：`WindowsDeviceDriver.read_release_identity`
读的是**装好的** App 根目录下那个文件，用来证明「这个 App 是从这棵源码树出来的」。这条
证据链在包里根本不存在，而出包、验包、门禁没有一处会说出来。

## 为什么不把它加进资源契约

试过这条路，它是错的：契约里每个资源都要在 `bundlerDeclared` 里对 macOS 和 Windows 各表一次态。
发布身份在 macOS 上的载体是 `Info.plist` 里的一个键，**macOS 根本没有这个文件**。写成
`macos: false` 就意味着「macOS 由 assembler 自己装」，于是
`check_the_assembler_installs_what_the_bundler_will_not` 会要求 macOS 装一个它没有的东西。

所以它作为一个具名的、单独的参数传进配置写入器，而不是伪装成第七棵资源树。

## RED

```text
scripts/test_release_assembly.py::ReleaseConfigurationTests
  ::test_the_windows_configuration_ships_the_release_identity
  ::test_a_release_identity_under_another_name_is_refused
→ TypeError: write_windows_release_configuration() got an unexpected keyword
  argument 'release_identity'

scripts/test_build_release_package.py::WindowsReleaseTests
  ::test_a_configuration_that_would_ship_no_release_identity_is_refused
→ AttributeError: module 'build_release_package' has no attribute
  'require_declared_release_identity'
```

## GREEN

```text
scripts/test_release_assembly.py + test_build_release_package.py
  → 53 passed（改动前 50 passed），失败集合逐条相同：11 条，全是本机 macOS 专属用例
scripts/test_run_eb_11_formal_app_acceptance.py + test_pc_16_windows_package_acceptance.py
  → 与 main 基线逐条一致（21 failed，diff 为空）
scripts/check_release_package_wiring.py            → exit 0
scripts/check_release_package_wiring.py --self-test → exit 0（五种变异全部被拒）
```

改动：

| 位置 | 内容 |
| --- | --- |
| `release_identity.PACKAGED_IDENTITY_NAME` | 文件名的唯一定义；构建器、配置写入器、EB-11 runner 三处原本各写一遍字面量 |
| `write_release_configuration(release_identity=…)` | 具名可选参数，落到资源根，**不带尾斜杠** |
| `build_release_package.require_declared_release_identity()` | 出包门禁，写完配置立刻查 |
| `check_release_package_wiring.py` | 按**发布真实形态**写 Windows 配置（带身份），既断言它在、又断言它不被算成资源 |

## 变异确认

把 `write_windows_release_configuration` 里的 `release_identity=release_identity` 改成
`release_identity=None`（即恢复缺陷）：

```text
check_release_package_wiring.py → exit 1
  “the windows release configuration drops the release identity,
   so the installer could not carry release-identity.v1.json”
test_the_windows_configuration_ships_the_release_identity → FAILED
```

两处都是承重的，不是摆设。

## 尾斜杠这一处是查过的，不是猜的

`bundle.resources` 里其他六项的值都以 `/` 结尾（目录语义）。发布身份是单个文件，值不能带
斜杠——这是读 `tauri-utils-2.9.3/src/resources.rs` 确认的，不是试出来的：

- `next_pattern()`：map 的值先过 `resource_relpath()`，再存进 `current_dest`；
- 源是文件（非目录、非 glob）时走 `resource_from_path()` 的 `current_iter is None` 分支：
  `current_dest` 非空就直接 `dest.clone()` 作为目标路径；
- 该文件自己的 TODO 注释写着 `{ "README.md": "./folder/" }` 今天会把文件装成 `folder`
  这个名字——所以尾斜杠在这里是「用它当文件名」，不是「装进这个目录」。

于是 `"…/release-identity.v1.json": "release-identity.v1.json"` 落在资源根，
Windows 的资源根就是安装根目录（契约里 `resourceRoot.windows` 是 `[]`），正是 runner 要读的位置。

## 顺带修掉的：这道门禁在 Windows 上从来跑不起来

`check_release_package_wiring.py` 用系统临时目录（`C:`）当草稿区，而仓库在 `F:`。Windows
两个盘之间没有相对路径，于是 Windows 配置写入器抛
`resource source must be relative to the Tauri root` ——**说的是门禁自己的临时目录，跟它要检查的东西毫无关系**。
本机实测改动前 exit 1，改用仓库内 `.local/` 后 exit 0。

不修的话，这次新加的断言在唯一能出 Windows 包的机器上永远不会执行。

## 正常用户路径验收

**已补**（2026-08-05 晚）。重新出包、静默安装，读安装根目录：

```text
%LOCALAPPDATA%\自动化运营工具\
  automation-tool-desktop.exe   embedded-browser\
  local-executor\               material-video-worker\
  media-toolchain\              motion-catalog\
  motion-video-worker\          uninstall.exe
  release-identity.v1.json      ← 在了

release-identity.v1.json:
  buildId          windows-release
  sourceGitCommit  2b5e4b8737d570f100092650e9e12f5fbb4a644d
  sourceTreeSha256 33b746471055d924…
  target           windows-x86_64
```

出包侧 `bundle.resources` 里这一项是 `release-identity.v1.json`（**没有尾斜杠**，
按设计），安装后落在资源根即安装根目录。EB-11 的 `WindowsDeviceDriver.read_release_identity`
从装好的包里读回来成功：

```text
SignedReleaseIdentity(source_git_commit='2b5e4b87…', source_tree_sha256='33b74647…',
                      executor_build_id='windows-release', target='windows-x86_64',
                      architecture='x86_64', deployment_profile_id='local')
```

出包 `REAL_EXIT=0`，installer 450,952,363 字节，主二进制 27,181,568 字节。

## 读产物又抓到一条：安装用的静默开关根本没送到

安装那一步卡了 50 分钟，从外面看和「正在装一个 450 MB 的包」一模一样。实际是
`installer.exe /S` 经 Git Bash 时被 MSYS 参数转换当成路径改写了，installer 收到一个
不认识的参数就弹了 GUI，然后静静等着——**不报错、不退出**。

改从 PowerShell 启动（`Start-Process -ArgumentList '/S' -Wait`）后 `INSTALL_EXIT=0`。

与本仓已记的两条同类：`nohup cmd > log 2>&1 &` 那次退出码来自包装命令，这次是参数没送到。
判据都一样——**看被测的东西自己在干什么，别看包装层说了什么**；卡住时先看有没有窗口在等人。

## 真实边界

- 已证明：配置会声明它、出包门禁会在缺失时拒绝（两者都经变异确认）、`makensis` 确实把它
  装进安装根目录、runner 从装好的包里读得回来。整条闭合；
- **这个包本身仍不能跑 EB-11**，原因与本条无关：它是 `deploymentProfileId: local` 的包，
  而 EB-11 要求 `demo-*` 的客户 Demo 档案。跑验收要另出一个带部署 Profile 的包；
- macOS 侧未跑：本次改动只在 Windows 分支上生效，macOS 配置写入器的调用形状未变
  （`write_macos_release_configuration` 不传该参数），但本机是 Windows，macOS 未复跑。

## 清理

无新增临时资源。门禁的草稿目录由 `TemporaryDirectory` 自行清理。

## 文档变化

本文件为新增。

## 遗留项

无。装好的包里存在 `release-identity.v1.json` 已于 2026-08-05 晚实测闭合。
