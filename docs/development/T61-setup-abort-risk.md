# T61 — setup 失败即 abort 的风险面

> 状态：**调研完成，修复未做**。本文件是风险面的完整证据，`docs/demo-sprint-roadmap.md` 只留一行结论。
>
> 结论先行：**演示场景（全新 Mac → 首次启动 → 做视频 → 正常退出 → 再打开）实测不会 abort**，前一条线的高风险判断被推翻。剩余真实窗口只有「删除成片时强退」和硬断电，建议一处四行兜底。

## 一、abort 点分布

**23 个 abort 点**：

- **2 个在 Builder 之前** —— `DeploymentProfile::load().expect()` / `UpdateRuntimeConfiguration::load().expect()`。失败时窗口还没建，用户看到的是「双击没反应」。
- **21 个在 setup 钩子里** —— `lib.rs:4203`–`4338`。这些才是「窗口闪一下就没」。

## 二、诊断页结构上救不了 setup 失败

`tauri-2.11.5/src/app.rs:2521` 实证：窗口先建、setup 后跑。所以

- setup 失败 = 窗口先出现再瞬间消失；
- 主线程被 setup 占着，WebView 的 JS 一行都执行不了；
- `check_local_startup_environment` 的四个入参全是 `tauri::State`，全部在 setup 里才 `manage`。

**那个 fail-closed 诊断页覆盖的是「启动成功之后的运行期降级」，不覆盖启动失败本身。** 这是结构决定的，不是实现没做好。

## 三、概率分档

| 档 | 内容 |
|---|---|
| 近似为零（结构决定） | 多数初始化是路径形状校验 + `DirBuilder.recursive(true).mode(0o700)`；全新机器没有旧状态，解析分支不执行 |
| 近似为零（每台一样） | `executor_verifying_key()` 等读编译期 `option_env!`，T44/T48 启动成功即证明发出去的包里是好的 |
| 已知唯一元凶 | `deployment_profile.rs:260` 的非递归 `create_dir`，只在 `~/Library/Application Support` 不存在时触发，真实账号必然存在 |
| 非零但低（首次） | 磁盘满 / 目录不可写；`ensure_private_file_permissions` 对已存在密钥文件的权限位检查（迁移助理、Time Machine 恢复、iCloud 同步 Library 会造出带 group/other 位的文件） |

## 四、真正随使用增长的是第二次启动

正是演示场景：做完视频 → 关 App → 再打开。

- `VideoJobWorkspaceStore::initialize`（`video_job_workspace.rs:349`）建完目录后跑 `recover_interrupted_imports()?` + `validate_artifact_inventory()?` + `discard_staged_publish_artifacts()?`，App 被强退后任意一步不一致就 abort；
- `AppUpdateCache::validate_disk_state()`（`app_update_cache.rs:361`）：包/断点文件与 manifest 对不上就 abort。**这条在发出去的包里是活的** —— 二进制带 feed URL `https://updates.candidate.invalid/…`（`.invalid` 是永不解析的保留域名，**演示机上点「检查更新」必然失败**）；
- `StoredBrowserDiagnosticSettings.version` 不匹配就 abort → 将来版本号一升，老机器上是**启动即闪退而不是迁移**（已单列为 T63）。

## 五、按演示场景的风险表

代码依据见 `video_job_workspace.rs` / `secure_store.rs` / `app_update_cache.rs`。

| 场景 | 会不会 abort | 依据 |
|---|---|---|
| 全新 Mac 首次启动 | **不会** | 空 `artifacts/` / `publish-staging/` / `update-cache/` 全走平凡分支；4 个 vault 全走「文件不存在→创建」 |
| **做完视频、正常退出、再打开** | **不会** | `import_output` 是 payload `sync_all` → manifest `sync_all` → 临时目录 `sync_directory` → **整目录原子 rename** → 父目录 `sync_directory`。落地后必然齐全。**前一条线的高风险判断被推翻** |
| 做完视频、强退、再打开 | 绝大多数不会，**两个非自愈窗口** | 自愈：`.import-*` 被 `recover_interrupted_imports` 删掉；staging 半成品被 `discard_staged_publish_artifacts` 删掉。**不自愈**：① `delete_artifact` 的 `remove_dir_all` 执行到一半被杀 → 目录只剩一个文件 → **永久 abort**；② 硬断电造成 payload 大小与 manifest 不符 → **永久 abort** |
| 磁盘接近满 | 第二次起不会 | 已初始化的机器 setup 全程不写文件。**但 `ensure_free_space` 要求 1 GiB + 视频大小，不够时导入成片失败** —— 界面报错不是闪退，但演示会挂 |
| Time Machine / 迁移助理恢复的账号 | **会，最高真实概率** | 三条独立路径：密钥文件权限被抬成含 group/other 位（`secure_store.rs:182` **只检查不修复**）；目录 mode 漂移（`validate_private_directory_metadata` 同样只检查不修复，而 `deployment_profile` 和 `secure_store` 对目录却是**强制 chmod 修复**，同一仓库两套策略，已单列为 T66）；祖先目录有软链 |
| Windows 企业域 / AppData 重定向 | **会**（读代码推断，未上机验证） | `browser_profiles_windows.rs:504` 的 `\\?\UNC\` 前缀未剥离；`ensure_no_reparse_components` 拒绝路径上任何 junction。已单列为 T67 |

## 六、没查完的部分（如实记）

- 21 个初始化读到底约 14 个，4 个 vault 只读到顶层；
- `recover_interrupted_imports` / `validate_artifact_inventory` 内部失败条件**未展开，而那是风险最高的一处**；
- Windows 路径完全没看。

## 七、真正的缺陷：策略不一致

`.import-*` 半成品清理继续 ✅、staging 半成品清理继续 ✅、**已落地 artifact 出任何问题直接 abort** ❌。

对自己写的临时垃圾宽容，对自己写的正式产物零容忍到把整个 App 拉崩。

## 八、建议的最小兜底（四行，未做）

把 `validate_artifact_inventory()`（`video_job_workspace.rs:379`）从「启动门禁」降级为「启动清理」：遇到坏 artifact 按 `recover_interrupted_imports` 已有做法删掉或挪进隔离目录，只有清理动作本身失败才 `Err`。`list_artifacts()` 作为运行期 API 的严格语义不动。

换来的是把两个永久砖变成「丢一个视频，App 照常开」。已有测试夹具：`tests/video_job_workspace.rs:114/167/483`。

## 九、演示前零成本预检（不改代码，已同步到检查清单）

1. 磁盘空闲 > 5 GiB；
2. `ls -ld /Users /Users/<用户名> ~/Library ~/Library/Application\ Support` 四行都不是 `l` 开头。

这两条覆盖了上表除强退外的全部剩余风险。
