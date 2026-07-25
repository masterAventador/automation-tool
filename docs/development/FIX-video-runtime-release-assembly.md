# FIX 视频运行时的生产装配路径与出厂门禁

> 状态：🔍 待验收（装配器与门禁已落地并对真实缺陷包验证有效；缺一次完整重建后的正式包用户路径验收）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：用户在 macOS 正式包上试用，「智能素材成片」报「本机视频制作服务暂时无法启动」、
> 「品牌动效成片 → 预览」报「本机渲染组件暂时不可用」。

## 缺陷

正式包 `Contents/Resources/` 只有 `embedded-browser` 与 `local-executor`。生产代码要找的是另外三样：

| 生产代码读取位置 | 出处 | 缺失后果 |
| --- | --- | --- |
| `Resources/media-toolchain/` | `video_media_toolchain.rs` `TOOLCHAIN_DIRECTORY` | `render_unavailable` |
| `Resources/motion-video-worker/package` | `lib.rs` `motion_runtime_paths` 生产分支 | 品牌动效无法渲染 |
| `Resources/material-video-worker/package` | `material_video_studio.rs` `worker_executable` | `process_unavailable` |

`tauri.conf.json` 的 `bundle` 段只有 `"active": true`，没有 `resources` 声明。这三样**没有任何生产装配路径**。

## 为什么全部验收都是绿的

`lib.rs:329-364` 与 `material_video_studio.rs:511-523` 存在构建期分叉：

- `#[cfg(feature = "video-studio-e2e")]` → 从 `AUTOMATION_TOOL_BM08_WORKER` / `_BROWSER` / `_FFMPEG` /
  `AUTOMATION_TOOL_IM05_WORKER` 环境变量读路径；
- `#[cfg(not(feature = "video-studio-e2e"))]` → 从打包资源目录读。

每个 BM/IM 验收脚本自己现场构建 ffmpeg 与两个 Worker，把路径通过环境变量喂给测试构建，跑完按清理规则删除。
渲染确实真跑通过，跑的是测试分支；**生产分支从未被任何测试或门禁走过**。

这是同一病根的第三次出现：

1. EB-16 内置浏览器——验收脚本装进去的，常规构建产出的包不含浏览器（已修，`release_assembly.py`）；
2. 本次三份视频资源——修浏览器时没有回头排查其余资源；
3. `build_video_media_toolchain.sh` 的 `FFMPEG_STATIC_LINK_FLAGS`——补 Windows 支持时新增，只验 Windows
   未回归 macOS，导致 macOS 上 bash 3.2 `set -u` 直接失败（见下）。

用户据此要求新增全局规则，已落地为 `~/.claude/CLAUDE.md` 的「单一构建路径规范」。

## RED

```text
python3 scripts/test_release_assembly.py
  ImportError: cannot import name 'install_video_runtime' from 'release_assembly'

python3 scripts/test_video_runtime_cache.py
  ModuleNotFoundError: No module named 'video_runtime_cache'

python3 scripts/test_release_assembly.py   （接线测试单独 RED）
  AssertionError: 'install_video_runtime(' not found in
  '...run_eb_16_windows_acceptance.py...'
  Ran 11 tests — FAILED (failures=2)
```

对真实缺陷包跑门禁（这是最有力的一条 RED，被检查的是用户手里那个包）：

```text
release assembly rejected: the bundle carries no media-toolchain at
.local/eb-16/run/cargo-target/release/bundle/macos/自动化运营工具.app/Contents/Resources/media-toolchain
— it was built without the video runtime assembly step
```

## GREEN

```text
python3 scripts/test_release_assembly.py        Ran 11 tests OK   （原 5 → 11）
python3 scripts/test_video_runtime_cache.py     Ran 12 tests OK
```

条数逐次核对：`release_assembly` 5 → 10（新增 5 个装配用例）→ 11（新增 1 个接线用例）。

## 交付

### 装配器（`scripts/release_assembly.py`）

- `VIDEO_RUNTIME_RESOURCES` 一处声明三份资源的 staging 名、安装位置与必需文件。安装位置**不对称**：
  两个 Worker 读 `<name>/package/...`，媒体工具链读 `<name>/...`——这个映射由生产代码决定，声明一次而不是
  在每个调用点重建；
- `require_packaged_video_runtime()` 出厂门禁。不只判目录存在：必需文件缺失或**大小为零**同样拒绝，
  因为生产解析器正是"找得到目录、仍然起不来"；
- `install_video_runtime()` 装入后整体复验，任一环失败删除本次写入的全部树，不留半装状态；
- `resource_directory()` 统一 macOS（`Contents/Resources`）与 Windows（安装根）的差异。

### 构建缓存（`scripts/video_runtime_cache.py` + `prepare_video_runtime.py`）

ffmpeg、两个 Worker 都是锁定版本 + 锁定源码摘要的确定性产物，此前每次验收都重建再删除。

- 缓存位于仓库外的本机稳定路径（macOS `~/Library/Caches/automation-tool-build`，Windows `%LOCALAPPDATA%`，
  其余 `XDG_CACHE_HOME`），可用 `AUTOMATION_TOOL_BUILD_CACHE` 覆盖。**必须在仓库外**：两个 Worker 构建脚本
  本身拒绝写入 checkout，且仓库级清理会扫掉它；
- 缓存键是**锁定契约的摘要**，契约改了就重建，没改就复用；
- 戳记读不出、产物被删、构建失败——三种情况一律重建，绝不把半成品当成品交给装配器。

实测：首次 3 分 53 秒（含 ffmpeg 源码编译），复用 **0.036 秒**。

### 接线（两条正式路径）

| 平台 | 装配位置 | 门禁位置 |
| --- | --- | --- |
| macOS | `install_runtime_resources_and_sign()`——视频运行时先装，浏览器后装，**最后封章**（签名必须覆盖全部资源） | `create_disk_image()`，与浏览器门禁并列 |
| Windows | payload 树上，bundler 之前；并写入 `bundle.resources` 声明 | `build_release_package()` 之前 |

`test_release_assembly.py` 增加接线测试，读两个脚本源码断言装配与门禁都在路径上——「写了装配器但没接进唯一
产包路径」正是浏览器那次首修的漏洞形态，也是 `TauriPublishWorkspaceGateway` 装配缺失的形态。

### 构建脚本跨平台修复

`scripts/build_video_media_toolchain.sh` 的 `${FFMPEG_STATIC_LINK_FLAGS[*]}` 改为 `${...[*]:-}`。

macOS 自带 bash 3.2，`set -u` 下空数组 `[*]` 展开判为未绑定，而 macOS 分支正是把该数组留空。已验证：

```text
旧写法 + 空数组   → /bin/bash: A[*]: unbound variable
新写法 + 空数组   → [x ]
新写法 + 非空数组 → [x -static -static-libgcc]      ← Windows 行为零变化
```

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 包内完全没有视频运行时 | 拒绝并点名缺哪一份 | 合成 + **真实缺陷包** |
| 三份中缺任意一份 | 拒绝并点名该份 | 合成（逐份 subTest） |
| 目录在但必需文件缺失或为空 | 拒绝（生产解析器正是死在这里） | 合成 |
| 装配中途失败 | 本次写入的树全部删除，Resources 保持为空 | 合成 |
| 重复装配到已有树上 | 拒绝，不覆盖 | 合成 |
| 缓存戳记损坏 | 重建，不抛错 | 合成 |
| 缓存产物被删但戳记还在 | 重建 | 合成 |
| 构建失败后再次调用 | 重建，且不残留半成品 | 合成 |
| 锁定契约变更 | 重建 | 合成 |
| 缓存目录落在仓库内 | 测试拒绝 | 合成 |
| Windows 可执行后缀（`.exe`） | 按平台换名校验 | 合成 |

## 正常用户路径验收

**未完成**。装配器与门禁已对真实缺陷包验证有效，但**尚未完整重建一次正式包并在其中操作视频制作功能**。
在完成该验收前，本项不得标记完成。

## 真实边界

1. **没有重建后的正式包证据**。当前证据止于「门禁能拒绝坏包」，缺「装配后的好包能让用户跑通视频制作」。
2. **Windows 侧全部未执行**。`run_eb_16_windows_acceptance.py` 的改动只经过语法检查与源码断言，
   没有在 Windows 主机上跑过；`bundle.resources` 声明三份视频资源后 NSIS 的实际打包行为未验证。
3. **缓存未做并发保护**。两个进程同时 `ensure_cached` 同一资源会互相覆盖。当前所有调用点都是单进程串行，
   但这个前提没有被强制。
4. **缓存不校验产物内容**，只校验锁定契约摘要。产物被外部手工篡改不会被发现——真正的逐文件校验由各自的
   `check_*` 脚本负责，尚未接入缓存路径。
5. **三份资源的完整性判据是「必需文件存在且非空」**，不是逐文件摘要。浏览器那份有 EB-05 Manifest 做逐文件
   校验，视频这三份还没有等价物。

## 清理

缓存目录在仓库外，不进 Git；`.local/video-runtime/` 下的一次性构建日志属于本地运行数据，已被 `.gitignore`
覆盖。未新增常驻服务。本轮启动的 App 会话、Control Plane 与 PostgreSQL 容器已随会话脚本正常收尾。

## 文档

- `scripts/release_assembly.py`（扩展）
- `scripts/test_release_assembly.py`（5 → 11）
- `scripts/video_runtime_cache.py`（新增）
- `scripts/test_video_runtime_cache.py`（新增，12 项）
- `scripts/prepare_video_runtime.py`（新增）
- `scripts/run_eb_16_acceptance.py`、`scripts/run_eb_16_windows_acceptance.py`（接线）
- `scripts/build_video_media_toolchain.sh`（bash 3.2 修复）
- `~/.claude/CLAUDE.md` 新增「单一构建路径规范」（已推送 dotclaude-unified `40d42a1`）
- 本文件

## 遗留项

| 项 | 状态 |
| --- | --- |
| 完整重建正式包并在其中走通视频制作用户路径 | 未做，本项完成的前提 |
| Windows 正式包装配与 NSIS 打包行为验证 | 未做，需 Windows 主机 |
| 三份视频资源的逐文件摘要校验接入装配器 | 未做 |
| 缓存并发保护 | 未做，当前依赖单进程串行的隐含前提 |
| 排查是否还有第四、第五份未装配资源 | 进行中，见 `pending-acceptance-audit-20260726.md` |
