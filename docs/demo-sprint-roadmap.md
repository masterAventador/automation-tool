# 客户 Demo 冲刺台账

> 本文件是 2026-07-25/26 大返工与客户 Demo 冲刺这批任务的**唯一状态源**。会话中断后从这里恢复。
>
> 与 `docs/development-roadmap.md`（产品主线 EB/VF/BM/U9 等任务）和 `docs/embedded-browser-video-studio-roadmap.md`（内置浏览器与视频专项）并列，不互相双写状态。本批任务用 `T<n>` 编号，逐条证据在 `docs/development/` 下对应文件。
>
> 状态标记：`✅ 已完成` / `🚧 进行中` / `⬜ 未开始` / `🧊 冻结到 Demo 后` / `👤 需用户本人操作` / `❌ 查证不成立`

---

## 当前下一步

**会话恢复时先读这一节。**

1. **[T58] 一句话生成在 App 里提交后失败** — 唯一挡着 Demo 的技术问题。已定位到"编排子进程非零退出"（**不是超时**，实测证伪）。下一步：再拆一个码区分「按协议拒绝」（`entry.py` 写 `{"status":"rejected"}` 并 `exit 70`）与「崩溃」（其它非零码），然后重跑。
2. **[T61] setup 失败即 abort 的风险面** — 只调研不改。演示机是全新 Mac，setup 内任何意外失败客户看到的都是闪退零提示。拿到风险面再定要不要在演示前动核心启动路径。
3. 上面两条之外的任何新发现，**记进本文件的冻结区，不要派线去做**。Demo 前不再发散。

---

## 一、P0 — 挡着「在 App 里生成出一个视频」

> 优先级判据：**用户能不能装上包、打开、输一句话、拿到一个能播的视频。** 不在这条链路上的一律不算 P0，哪怕它是真缺陷。
>
> 这条链路当前的断点只有一处：**提交之后**。前面每一环都已实证走通。

| 序 | 环节 | 状态 | 证据 |
|---|---|---|---|
| 1 | 装上包、Gatekeeper 放行 | ✅ | 带 quarantine 判 `accepted / Notarized Developer ID`。**新包正在出**（P0-0） |
| 2 | App 启动到工作台 | ✅ | `control-plane-e2e` 上实测挂载；正式包 setup 全程跑完 |
| 3 | 设置页存模型密钥 | ✅ | 正式表单存真实密钥且页面不回显 |
| 4 | 输入一句话、空描述被拒 | ✅ | `getValue` 断言描述确实进表单；空描述被人话拒绝且不产生任务 |
| 5 | **点击提交 → 命令开始执行** | ❌ | **T58a：点击后至少一分钟命令根本没开始。** 静态分析进行中 |
| 6 | **编排子进程跑完 → 拿到合成** | ❌ | **T58：子进程非零退出。** 不是超时（实测证伪），四码已拆但「拒绝还是崩溃」未取证 |
| 7 | 渲染出会动的 mp4 | ✅ | 分层实证：180 帧 / 114 帧互不相同，静图门禁反向验过拦得住 |
| 8 | App 内预览播放 | ⬜ | 代码就位（`ArtifactPage` 既有播放器 + base64 data URL），**未跑通因为卡在 5/6** |

**所以要改的只有第 5、6 两环，其余都是通的。** 按严重度：先 T58a（命令根本没开始，卡住整条路），再 T58（子进程为什么失败）。

| ID | 任务 | 状态 | 说明 |
|---|---|---|---|
| P0-0 | 用当前 main 出签名公证包 | 🚧 | 手上那个是 7 小时前的代码。产到 `.local/release`，已进入公证阶段 |
| T58a | 点击后一分钟命令没开始 | 🚧 | 打字 0 秒、点击 5 秒，**15 分钟全在提交命令内部**。41s/57s 两次取样：主线程在事件循环、tokio 全 park、无子进程、工作区为空——**park 意味着在等不是在算** |
| T58 | 编排子进程非零退出 | 🚧 | 已拆 `authoring_refused` / `authoring_crashed` 两码可分辨，需重跑取证 |
| T36 | 整条链路收口 | 🚧 | 5、6 两环通了之后跑完 7、8 即闭环 |

## 二、P1 — 生成视频通了之后才轮到

> 这些都**不挡**「生成出一个视频」。放在 P0 通了之后按顺序做。

| ID | 任务 | 状态 | 说明 |
|---|---|---|---|
| T10 | 从正式包跑一遍所有用例 | ⬜ | 这是用户给这一轮定的目标本身，但要等生成视频通了才有意义 |
| T61 | setup 失败即 abort，无兜底 | ⬜ | **风险面已查完，紧迫性下降**（详情见下）。「做完视频→正常退出→再打开」经导入链路核实是**安全的**，前一条线的高风险判断被推翻。剩余真实窗口只有「删除成片时强退」和硬断电。**建议做一处四行兜底**，不动 setup 结构 |
| T48 | 登录 → 设备绑定接缝 | 👤 | 包里地址、Gatekeeper 放行、隔离启动**均已验**；登录链路自动化三条路全排除，需人工走。**不挡生成视频**（本地 Profile 不要求产品账号） |
| T5 | 三份密钥按真实路径填一遍 | 👤 | 存密钥这一环已验通，这条是补正式验收 |
| T6 | 抖音扫码登录与后续链路 | 👤 | **演示前一天必须做掉**，且要确认关掉 App 再打开登录态仍在。跟生成视频无关 |
| — | 演示前检查清单补完 | 🚧 | §2、§5.1 **已实测贴输出**；§3 等最终 DMG，§7 等 T36 定型 |
| T50 | 注销成功但界面报失败 | ⬜ | 已从演示脚本摘掉「安全注销」，不阻塞 |
| T7 | Windows GUI 三项验收 | 👤 | 用户已授权装 Node。依赖 T53 |

### T61 详情：setup abort 风险面（调研已完成，是否修待定）

**23 个 abort 点**：2 个在 Builder 之前（`DeploymentProfile::load().expect()` / `UpdateRuntimeConfiguration::load().expect()`，失败时窗口还没建，表现是"双击没反应"）；21 个在 setup 钩子里（`lib.rs:4203`–`4338`，这些才是闪退）。

**诊断页结构上救不了 setup 失败**（`tauri-2.11.5/src/app.rs:2521` 实证）：窗口先建、setup 后跑 → 失败 = 窗口先出现再瞬间消失；主线程被 setup 占着，WebView 的 JS 一行都执行不了；且 `check_local_startup_environment` 的四个入参全是 `tauri::State`，全部在 setup 里才 `manage`。**那个 fail-closed 诊断页覆盖的是"启动成功之后的运行期降级"，不覆盖启动失败本身。**

**概率分档**：
- *近似为零（结构决定）*：多数初始化是路径形状校验 + `DirBuilder.recursive(true).mode(0o700)`；全新机器没有旧状态，解析分支不执行
- *近似为零（每台一样）*：`executor_verifying_key()` 等读编译期 `option_env!`，T44/T48 启动成功即证明发出去的包里是好的
- *已知唯一元凶*：`deployment_profile.rs:260` 的非递归 `create_dir`，只在 `~/Library/Application Support` 不存在时触发，真实账号必然存在
- *非零但低（首次）*：磁盘满 / 目录不可写；`ensure_private_file_permissions` 对已存在密钥文件的权限位检查（迁移助理、Time Machine 恢复、iCloud 同步 Library 会造出带 group/other 位的文件）

**⚠️ 真正随使用增长的是第二次启动**（正是演示场景：做完视频 → 关 App → 再打开）：
- `VideoJobWorkspaceStore::initialize`（`video_job_workspace.rs:349`）建完目录后跑 `recover_interrupted_imports()?` + `validate_artifact_inventory()?` + `discard_staged_publish_artifacts()?`，App 被强退后任意一步不一致就 abort
- `AppUpdateCache::validate_disk_state()`（`app_update_cache.rs:361`）：包/断点文件与 manifest 对不上就 abort。**这条在发出去的包里是活的**——二进制带 feed URL `https://updates.candidate.invalid/…`（`.invalid` 是永不解析的保留域名，**演示机上点「检查更新」必然失败**）
- `StoredBrowserDiagnosticSettings.version` 不匹配就 abort → 将来版本号一升，老机器上是**启动即闪退而不是迁移**

**没查完（如实记）**：21 个初始化读到底约 14 个，4 个 vault 只读到顶层；`recover_interrupted_imports` / `validate_artifact_inventory` 内部失败条件**未展开，而那是风险最高的一处**；Windows 路径完全没看。

**第二轮补完后的风险表**（按演示场景，代码依据见 `video_job_workspace.rs` / `secure_store.rs` / `app_update_cache.rs`）：

| 场景 | 会不会 abort | 依据 |
|---|---|---|
| 全新 Mac 首次启动 | **不会** | 空 `artifacts/` / `publish-staging/` / `update-cache/` 全走平凡分支；4 个 vault 全走"文件不存在→创建" |
| **做完视频、正常退出、再打开** | **不会** | `import_output` 是 payload `sync_all` → manifest `sync_all` → 临时目录 `sync_directory` → **整目录原子 rename** → 父目录 `sync_directory`。落地后必然齐全。**前一条线的高风险判断被推翻** |
| 做完视频、强退、再打开 | 绝大多数不会，**两个非自愈窗口** | 自愈：`.import-*` 被 `recover_interrupted_imports` 删掉；staging 半成品被 `discard_staged_publish_artifacts` 删掉。**不自愈**：① `delete_artifact` 的 `remove_dir_all` 执行到一半被杀 → 目录只剩一个文件 → **永久 abort**；② 硬断电造成 payload 大小与 manifest 不符 → **永久 abort** |
| 磁盘接近满 | 第二次起不会 | 已初始化的机器 setup 全程不写文件。**但 `ensure_free_space` 要求 1 GiB + 视频大小，不够时导入成片失败**——界面报错不是闪退，但演示会挂 |
| Time Machine / 迁移助理恢复的账号 | **会，最高真实概率** | 三条独立路径：密钥文件权限被抬成含 group/other 位（`secure_store.rs:182` **只检查不修复**）；目录 mode 漂移（`validate_private_directory_metadata` 同样只检查不修复，而 `deployment_profile` 和 `secure_store` 对目录却是**强制 chmod 修复**——同一仓库两套策略）；祖先目录有软链 |
| Windows 企业域 / AppData 重定向 | **会**（读代码推断，未上机验证） | `browser_profiles_windows.rs:504` 的 `\\?\UNC\` 前缀未剥离；`ensure_no_reparse_components` 拒绝路径上任何 junction |

**策略不一致（真正的缺陷）**：`.import-*` 半成品清理继续 ✅、staging 半成品清理继续 ✅、**已落地 artifact 出任何问题直接 abort** ❌。对自己写的临时垃圾宽容，对自己写的正式产物零容忍到把整个 App 拉崩。

**建议的最小兜底（四行，未做）**：把 `validate_artifact_inventory()`（`video_job_workspace.rs:379`）从「启动门禁」降级为「启动清理」——遇到坏 artifact 按 `recover_interrupted_imports` 已有做法删掉或挪进隔离目录，只有清理动作本身失败才 `Err`。`list_artifacts()` 作为运行期 API 的严格语义不动。换来的是把两个永久砖变成「丢一个视频，App 照常开」。已有测试夹具（`tests/video_job_workspace.rs:114/167/483`）。

**演示前零成本预检（不改代码，已同步到检查清单）**：磁盘空闲 > 5 GiB；`ls -ld /Users /Users/<用户名> ~/Library ~/Library/Application\ Support` 四行都不是 `l` 开头。这两条覆盖了表里除强退外的全部剩余风险。

---

## 二、需要用户本人操作或决策

| ID | 事项 | 说明 |
|---|---|---|
| T48 | 演示机上人工走登录链路 | 顺带把「客户双击后在 Gatekeeper 同意框点一次 Open」验掉（清单 §4.3，自动化拍不到）。也顺带做 T54 的正向确认（约 15 秒，会有可见窗口） |
| T53 | Windows 验收机装 Node | 当前 v22.20.0 低于 `engines >=24 <27`。快档门禁不需要（`tsc` 只要 `>=14.17`），**慢档必须对齐**。属对共享机器的系统改动 |
| T53 | 安装 pre-push hook | `.git/hooks/pre-push` 已被 git-lfs 占用，需人工插入共存。挂 pre-push 而非 pre-commit——后者跑在工作树上，而工作树正是被遮蔽的那个对象 |
| T5 | 三份密钥按真实路径填一遍 | 需要正式包。模型密钥已实测可用（HTTP 200） |
| T6 | 抖音扫码登录与后续链路 | 需要正式包 + 真实扫码。演示机数据目录与开发机隔离，**必须演示前一天做掉并确认关掉 App 再打开登录态仍在** |
| — | 申请 Pexels / Pixabay 免费 Key | 不为演示（主推动效线），是为关掉"素材线 Key 之后还有没有第二个阻塞"这个问题 |
| T37 | 四条合规决策 | 用户已定：Demo 后处理 |
| — | 要不要补背景音乐 | 合规上无障碍（已有 4 个自制、权利全 true 的 `music_sfx`），但那 4 个是 1-2 秒音效不是 BGM。做不做、几首、什么风格待定 |

---

## 三、已完成

### 生产装配与出厂门禁

| ID | 任务 | 关键结论 |
|---|---|---|
| T1 | 正式包补装三份视频运行时资源 | 病根：验收验的是"功能能不能跑通"，不是"用户拿到的那个包能不能跑通" |
| T13 | 建立真正的生产构建路径与必需资源门禁 | 单一声明源 `contracts/quality/release-package-resources.v1.json` |
| T21 | 唯一产包路径不在任何自动门禁里 | 独立发版命令 `scripts/build_release_package.py` |
| T33 | 正式包需在干净工作树重建 | |
| T35 | 包审计读共享 dist 而非构建时嵌入的产物 | |
| T42 | 包审计 Python 夹具落后于 mjs 新增能力 | 根治：从 mjs 导出的 `requiredDistributionMarkers` 读，不再抄第二份 |
| T44 | **正式包接上 Developer ID 签名与公证** | 296 个代码节点签名、289 个 Mach-O 全本团队签名 0 例外、entitlements 只加 `allow-jit`（有对照实验）。判据：带 quarantine 判定 `accepted` |
| T39 | 消除发版与开发环境的构建期分叉 | 登录界面此前只存在于 `customer-demo` 这个 Vite mode，正式包里整个被 tree-shake |
| T55 | 账号命令没进 desktop-e2e handler | 修接线错误但不拆掉设备凭据那条**有意的**安全边界 |
| T31 | H8-22 打包 App 闪退 | ❌ **不复现**。四条证据：结构 / 装配 / 运行 / 崩溃报告归因 |

### 云端与交付

| ID | 任务 | 关键结论 |
|---|---|---|
| T17 | 云端 Demo bootstrap 可注册无账号 Installation | |
| T18 | **控制服务云端部署** | `https://at.xuanbai.tech` 真实可用。中途逮到两个真缺陷：重复部署必崩（首次全绿、第二次才炸）、AppleDouble 污染 Alembic。重新部署 31 秒 |
| T14 | 正式构建缺设备注册路径 | |

### 视频与内容

| ID | 任务 | 关键结论 |
|---|---|---|
| T2 | 品牌动效段数与每段时长改为用户可配 | |
| T8 | 动效零件自动推荐与中文名映射 | |
| T16 | 裁剪素材成片 Worker 647MB 打包体积 | 降到 353 MiB |
| T27 | 两条视频线的 ffmpeg 都会回退到用户系统组件 | |
| T28 | **把 148MB 专有字体换成开源字体** | Noto Sans CJK，构建期按摘要下载不进 Git。包一级四道闸：缺字体 / 被替换 / 版权行不对 / 缺许可证 |
| T29 | 零件区在固定模板路径下明确标为不参与 | |
| T32 | 背景音乐三个选项全部等价于无音乐 | 不改 vendor，用注入层移除控件。判据：可访问性树条目 2 → 0 |
| T46 | 上游品牌名在产品窗口顶部 | ❌ **产品路径不复现**（那是绕开产品窗口直连 Streamlit 才能看到的）。仍补了静态门禁覆盖内嵌 WebUI |
| T47 | 字幕兜底会现场下载 1.5GB whisper 模型 | `HF_HUB_OFFLINE=1`。A/B：修改前 2.00s 真出网，修改后 0.10s 被拒零字节 |
| T56 | 一句话入口把片长写死 12 秒且不提示 | 原文案还写着"最长 20 秒"，是误导。改后明示 12 秒**并给出去处** |
| T20 | 发布页永远没有视频可发 | 成片页补上「去发布」 |

### 验收基础设施与门禁

| ID | 任务 | 关键结论 |
|---|---|---|
| T9 | 排查全部待验收任务缺什么 | |
| T11 | 审计 51 项已完成任务的生产装配真实性 | |
| T12 | 消除测试构建对启动门禁的短路 | |
| T23 | 装配核对测试加强 | |
| T30 | control-plane 层 28 个 E2E 配置自 07-22 起全部阻断 | |
| T34 | **桌面 E2E 剩余失败逐条判定** | "4 条失败"两重都不成立：测自被遮蔽的树 + 重跑后只有 2 条稳定。D6-10 是**测试期望过时**不是产品缺陷；B5-13 是真缺陷（见 T50）。排除了"产品启动不稳"：56 次运行 0 新增崩溃报告 |
| T43 | **没有任何门禁挡住 main 编译不过** | 两起同型事故。定性两次修正，最终查明 GitHub Actions 因账单从未运行。已停用 Actions 并写 README；`scripts/commit_gate.py` 快档 6 秒，**每次运行植入已知缺陷自证** |
| T49 | 门禁有效性审计 | 两轮。第一轮定位在门禁管道层，第二轮证明业务验收层**没有同类病**——"这一层没问题"本身是有价值的产出 |
| T15 | 法务页缺 ffmpeg GPLv3 条目与许可证全文 | |
| T3 | 第三方软件声明页降权到设置页底部 | |
| T54 | 主窗口不向辅助功能暴露 | ❌ **是 macOS 锁屏行为，不是产品缺陷**。判据被推翻：Chrome / VS Code / ghostty 同一时刻 AX 全是 0。连带更正："窗口可见 3.5 分钟"不成立，那段时间屏幕锁着 |
| T57 | 纯 desktop-e2e 入口去留 | 调研完成（结论见冻结区）。**推翻了"整族长期未运行"这个前提** |
| T51 | 三组手工抄的清单改由权威来源推导 | 事件名取自 `TaskEventType` 闭合枚举 + Executor schema；守卫标记取自 `reconcile()` 实际调用；CLAUDE.md 引文按标题定位并双向查。**变异检验抓出门禁自身的失效**：事件名正则 `[a-z_]+` 遇到带数字的新名字会让它从扫描里消失、门禁少检查一项却报通过。`docs/development/T51-derived-declarations.md` |
| T52 | 聚合执行者抓到的第一个真红 | 判定为**测试过期**（证据链完整）：产品行为逐条符合契约，越界在 `renderVerify()` 的 `--version` 探针与无头浏览器共用 1 秒预算，而假浏览器是刚写出的新文件，macOS 首次 exec 扫描吃掉 45%~64%。修法是把一次性扫描费提前付掉，**没有放宽断言、没有加 skip**。`docs/development/T52-render-timeout-first-exec.md` |
| T59 | 前端入口里的第八处构建期分叉 | `startup.ts:44` 的 `desktopShellStartupCheck` 无条件返回 ready，`single_build_path.rs` 只读 Rust 源码看不见它。**5 个入口全过是因为它们的前端根本不跑门禁**。已纳入门禁（`e86a77e`） |

---

## 四、冻结到 Demo 之后

> 这些都是真问题，但**没有一个挡着下周演示**。Demo 前不再派线，只在此登记。

### 今晚撞见的技术债

| ID | 任务 | 一句话 |
|---|---|---|
| T60 | 渲染沙箱的安全断言在静默跳过 | `local_video_orchestrator*.rs` 五处裸 `return;`，常规 `cargo test` 一律报 ok。涉及 `real_worker_render_sandbox_isolates_malicious_html` 等。**收敛时可能有未提交改动，需确认** |
| T50 | 注销成功但界面报失败 | 5 次复现 4 次。内层 ~5 秒轮询包在 60 秒超时里——**不是等不起，是自己先放弃了**。已从演示脚本摘掉「安全注销」 |
| T58c | 拒绝原因要不要转发给用户 | 静态核查已确定**能拿到且不需要读 stderr**：`entry.py` 13 处 `_reject` 与 `agent.py` 全部拒绝消息都是固定字面量，唯二插值是结构标签和门禁码闭集，`MotionBrief` 越界消息也不回显 brief 原文。但转发要改共用 error wire，与"细节不进 wire"的决定冲突，代价写在台账里等决策 |
| T62 | `run_script_tests.py` docstring 写死 "37 self-contained test scripts" | 实际已 41。代码本身是 glob 推导的没有功能问题，但这个会静默落后的散文计数**就写在那份讲"discovery is derived, never curated"的文档里** |
| T63 | **三处「版本号一升就砖」，会同时打死所有老机器** | `executor_platform.rs:354` 诊断设置版本无迁移路径；`app_update_policy.rs:288` 发布通道名变更即 abort；`app_update_policy.rs:282` 要求存量文件**重新序列化后与磁盘字节逐字节相同**（字段顺序或序列化行为任何变化即 abort）。本次演示不可能触发（单一构建单一通道），但**第一次真正发版升级时会同时打死所有老机器** |
| T64 | `AppUpdateCache` 有两个非自愈的强退窗口 | `download()` 尾段 351-357：`atomic_replace` 之后、`save_cache_manifest` 之前被杀 → package 有而 manifest 无 → 永久 abort；`partial_manifest.delete()` 之前被杀 → 同型。**本包里是死的**（feed URL 是 `.invalid` 保留域，永不解析），但**换成真 feed URL 就变成「更新到一半被强退 = 砖」的定时炸弹** |
| T65 | `cleanup_expired` 生产代码里没有任何调用方 | 只有 `tests/video_job_workspace.rs:391` 调。30 天保留策略形同虚设，artifacts 单调增长，**启动时的 abort 面随视频数量线性增长** |
| T66 | 目录权限策略两套：一套修复一套只检查 | `deployment_profile` 和 `secure_store` 对**目录**是强制 `chmod 0700` 修复；`validate_private_directory_metadata` 和 `ensure_private_file_permissions` 对目录/文件却**只检查不修复**。所以 Time Machine/迁移助理造成的权限漂移在一半路径上自愈、另一半上是永久闪退 |
| T67 | Windows 企业域 AppData 重定向会启动即闪退 | `browser_profiles_windows.rs:504` 的 `normalized_path_key` 只剥 `\\?\` 前缀，**不处理 `\\?\UNC\`**；文件夹重定向到 UNC 共享或 SUBST 映射盘时 `final_path != normalized_path_key` → abort。另 `ensure_no_reparse_components` 拒绝路径上任何 junction（把 AppData 搬到 D 盘留 junction 是常见场景）。**读代码推断，未上机验证** |
| T45 | Control Plane 镜像被打进 playwright | 约 50MB。代码层守住了 CLAUDE.md 4.2，打包层破了 |
| T57 | desktop-e2e 入口的并/修/废 | **并**：VF-06 / BM-15 / VE-03 / VE-04 搬到 control-plane-e2e（+BM-06、CQ-01 并入）。**废**：B5-04（产品 UI 已删）、`workbench.spec.ts`、三条 0-click 的 update spec。**重新设计**：BM-08、IM-05。另注：`video-studio-e2e` 这个 feature 已不门控任何一行产品代码 |

### 原有待办

| ID | 任务 | 为什么冻结 |
|---|---|---|
| T4 | 补 VE 剪辑装配任务 | 大工程，Demo 不演。用户此前明确困惑过"剪辑不需要上传素材吗" |
| T19 | 动效零件接 AI 一句话制作链路（方案 B） | 大工程 |
| T10 | 正式包全量跑通所有测试 | 等功能定型 |
| T22 | 自动更新在可发布包里从未配置 | Demo 不需要 |
| T24 | 执行器包根按 debug_assertions 分叉 | |
| T25 | 视频线 WDIO 验收补齐真实资源前置 | 与 T57 的"并"重叠 |
| T26 | 内置浏览器换成 Chromium 开源构建 | 用户已定 Demo 后 |
| T7 | 需 Windows GUI 会话的三项验收 | 依赖 T53 |
| T40 | 包内 UTM Kabel KT 字体权利未判定 | 合规，Demo 后 |
| T41 | 动效叠加字体未进权利登记表 | 同上。`big-shoulders-display-latin.woff2` 登记在另一份契约里，不在 `asset-rights-policy` |
| T38 | 演示后回收清单 | 演示后才用 |

---

## 五、这一轮学到的（新会话请先读）

今晚所有事故归结为**同一种失效模式换了不同的衣服**：

**形态一 — 共享的可变状态产出不可信的证据**：工作树遮蔽坏提交 / 共享 git index 卷走别人的文件 / `.git/worktrees` 注册 / 模块在调查中途被搬走。

**形态二 — 一个检查报告成功，而它实际只是安静**：`--ignore-missing-imports` 把未解析 import 变成 `Any` / 14 个孤儿测试没人跑 / 6 个 CI workflow 因账单从未运行 / 包审计只问否定问题 / macOS 没有 `timeout` 命令导致管道没执行却退出 0。

据此形成的硬规矩：

- 判据永远指向**从提交提取出来的树**，不指向任何活的工作树、index 或本机状态
- **每道门禁必须能自证**：每次运行植入已知缺陷，抓不到就判自己失败
- 看到"输出为空 / 没有报错"时，**先证明工具真的执行了**
- 门禁要**明说自己不检查什么**——最危险的时刻不是抓不到问题，是别人以为它抓得到
- 覆盖清单必须从权威来源自动发现，手抄的清单落后时没有信号
- 并行多代理时用 `git commit -- <paths>`（重命名/删除要新旧路径都列）；跑门禁用 `git archive` 而非 `git worktree add`
- 拿到因果结论先问**有没有对照组**——"隐藏导致窗口不进 AX 树"就是个没有对照组的观察，据此做的决定是错的
