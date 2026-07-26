# codex 并行批次交接单

> 面向在**独立分支**上与主线并行工作的 codex。本文件是这批任务的完整输入，读完即可开工。
>
> 日期：2026-07-26 ｜ 基线提交：见下文「起点」 ｜ 主线负责人正在做 T68 / T10 / T61 / T69 / T70

---

## 0. 为什么是这 6 个任务

划分依据不是「优先级高低」，是**文件面是否与主线重叠**。

主线昨夜的实测教训：低优先级任务照样牵出核心缺陷（T57 挖出前端桩分叉、T60 挖出验收空转），
**按优先级切会切在错误的地方**。所以这批是按「文件族」切的。

被**排除**在外的任务，以及排除的具体理由（不是「不重要」）：

| 排除的 | 理由 |
|---|---|
| T50 注销界面报失败 | 落在 `control_plane.rs` / `lib.rs`，主线 T69 要在同一批文件里加日志 |
| T65 `cleanup_expired` 无调用方、T66b 目录权限 | 都在 `video_job_workspace.rs`，与主线 T61 同文件同启动路径 |
| T67 Windows UNC/junction | `browser_profiles_windows.rs` 是 `#[cfg(target_os = "windows")]`，**在 macOS 上根本不参与编译**，做不出「先看到失败的测试」这一步。必须在 Windows 机上做 |
| T45 Control Plane 镜像进 playwright | 要动 `uv.lock`，全局锁文件 |
| T10 / T57b | 与主线本轮目标直接冲突 |
| T26 内置浏览器 | 见文末附录，结论与任务标题相反，且改动会改变正式包内容，与主线 T10 冲突 |

---

## 1. 硬约束（违反其一，产出即作废）

### 1.1 隔离

**不要在主工作树上工作。** 主线正在同一棵树上构建 Tauri App 与正式包。

昨夜实测过两种污染，靠「划定文件白名单」完全挡不住：

1. **测试结论被污染** —— 桌面 E2E 线测出一片红，实际是另一条线正在改一个 `.tsx` 且处于 TS 编译不过的状态，所有 driver 在 `beforeBuildCommand` 就失败。作业面没重叠，但**整棵树能不能编译是共享状态**；
2. **交付产物被污染** —— 要交客户的正式包如果在这棵树上构建，会把别人没写完的代码打进去。所有门禁都会绿，因为混进去的是半成品而不是缺东西。

**worktree 已经建好了，直接进去开工：**

```bash
cd /Users/aventador/code/automation-tool/wt/codex
git status          # 应显示 On branch codex/resilience-batch，基线 7055041
```

- 分支 `codex/resilience-batch`，基线 `7055041`（= 当时的 `origin/main`）；
- `frontend/node_modules` 与 `backend/.venv` 已软链到主树，**不要在里面跑 `pnpm install` 或 `uv sync`**——那会改到主树共享的那一份。缺依赖先停下报告；
- **不要设 `CARGO_TARGET_DIR` 指回主树 target** 省构建时间——主树上有 cargo 在跑，会撞锁。宁可全量构建（第一次会久，正常）；
- `wt/` 已在 `.git/info/exclude`，不会污染共享 `.gitignore`。

**为什么不是「随便找个目录 clone 一份」**：worktree 与主树共享 object database，你的提交主线 `git log codex/resilience-batch` 直接就能看到，不需要额外配 remote。

### 1.2 提交

**一律用 `git commit -- <path1> <path2>`，不要 `git add` + `git commit`。**

理由：git index 是共享可变状态。昨夜实测到——一条线 `git add` 完自己的文件后，另一条线把某个文件重命名 staged 进同一 index，前者的 commit 就把那个重命名一起提交了。`git commit -- <paths>` 绕过 index，只提交显式列出的路径。

两个陷阱：

- 涉及**重命名或删除**时，新旧两个路径都要列进去，否则 HEAD 里会同时留下新旧两份文件（本机工作树看起来正常，全新 checkout 才会发现）；
- **新增文件仍必须先 `git add`**（`commit --` 不认未跟踪路径）。

### 1.3 不许碰的文件

| 文件 | 归属 |
|---|---|
| `docs/demo-sprint-roadmap.md` | 主线独占。**你的状态写进本文件末尾的「进度回填」一节**，主线负责合并 |
| `frontend/src-tauri/src/lib.rs` | 主线 T69 正在加日志 |
| `frontend/src-tauri/src/control_plane.rs` | 同上 |
| `frontend/src-tauri/src/video_job_workspace.rs` | 主线 T61 |
| `frontend/src-tauri/Cargo.toml` | 主线 T69 要加日志依赖。**你需要新依赖时先停下报告，不要自己加** |
| `uv.lock` / `package-lock.json` | 全局锁文件 |
| `docs/development-roadmap.md` | 另一条产品主线 |

**碰到必须动这些文件才能推进的情况：停下，把原因写进回填区，不要绕过。**

### 1.4 TDD 铁律

来自项目 `CLAUDE.md` 第 8 节，无例外：

1. 先写能证明目标行为、**且当前会失败**的测试；
2. **实际运行**，亲眼看到失败，确认失败原因是你预期的那个（不是编译错、不是路径错）；
3. 写满足行为的最小实现；
4. 运行确认转绿；
5. 重构后跑受影响回归。

一条附加要求：**RED 必须表现为断言失败，不能是编译失败。** 靠「引用还不存在的符号」制造 RED 会让整棵树在那段时间坏掉。

### 1.5 两条从昨夜事故里长出来的检查习惯

- **macOS 上没有 `timeout` 命令。** 写 `timeout 300 xxx` 会导致整条管道根本没执行，输出为空、退出码 0，看起来像通过。主线和一个子代理各踩过一次。用 `gtimeout` 或不用；
- **看到「输出为空 / 没有报错」时，先证明工具真的执行了，再解释这个空。** 昨夜的核心失效模式就是「一个检查报告成功，而它实际只是安静」——安静和成功长得一模一样。

---

## 2. 任务清单

每条都给了**我的判断 + 判断依据**。依据是可核实的事实，不是印象。
**你读完代码如果有相反判断，说服我，我不预设立场。** 昨夜子代理推翻过主线四个错误判断，每次都是因为写出了依据而不只是结论。

### 族 A —— 自动更新与版本迁移（T63 / T64 / T22）

这三条同属一个文件族，放一起做避免互相踩。

#### T63 三处「版本号一升就砖」，会同时打死所有老机器

**现象**（三处独立，同一形状）：

| 位置 | 行为 |
|---|---|
| `executor_platform.rs:354` | `StoredBrowserDiagnosticSettings.version` 与当前不匹配就 abort，**没有迁移路径** |
| `app_update_policy.rs:288` | 发布通道名变更即 abort |
| `app_update_policy.rs:282` | 要求存量文件**重新序列化后与磁盘字节逐字节相同**——字段顺序或 serde 序列化行为任何变化即 abort |

**我的判断**：这三处都在 setup 钩子里，abort 表现为「窗口闪一下就没」，用户看到的是双击没反应。本次演示不可能触发（单一构建、单一通道、版本号不变），但**第一次真正发版升级时会同时打死所有老机器**——因为老机器磁盘上躺着的正是旧版本写的文件。

**依据**：`tauri-2.11.5/src/app.rs:2521` 实证窗口先建、setup 后跑；主线程被 setup 占着，WebView 的 JS 一行都执行不了，所以**任何前端诊断页在结构上都救不了 setup 失败**。第三处尤其危险：它把「serde 输出稳定」当成了不变量，而那从来不是 serde 的承诺。

**要做的**：给三处都补上迁移路径——读到旧版本时按已知规则升级并回写，只有**升级本身失败**才 `Err`。语义参考同仓库已有的 `recover_interrupted_imports`（对不一致状态先尝试自愈，不是直接拉崩 App）。

**验收**：单元测试要覆盖「磁盘上是 N-1 版本 → 启动 → 自动升到 N 且不 abort」和「磁盘上是无法识别的版本 → 明确报错」。

#### T64 `AppUpdateCache` 有两个非自愈的强退窗口

**现象**：`app_update_cache.rs` `download()` 尾段 351-357——

- `atomic_replace` 之后、`save_cache_manifest` 之前被杀 → package 文件有而 manifest 无 → 下次启动 `validate_disk_state()`（L361）永久 abort；
- `partial_manifest.delete()` 之前被杀 → 同型。

**我的判断**：**这条在当前发出去的包里是死的**——二进制里的 feed URL 是 `https://updates.candidate.invalid/…`，`.invalid` 是 RFC 保留域名永不解析，所以下载路径根本走不到。但**换成真 feed URL 的那一天，它就变成「更新到一半被强退 = 砖」**。

**依据**：`.invalid` 保留域这一条是直接从打包出的二进制里读到的，不是推断。所以这条现在优先级不高、但必须在第一次真发版之前修掉——修的成本现在最低（没有存量用户数据要兼容）。

**要做的**：让 `validate_disk_state()` 对「package 有而 manifest 无」这类可判定的不一致执行清理而非 abort（删掉孤儿 package，下次重新下载），只有清理失败才 `Err`。

#### T22 自动更新在可发布包里从未配置

**现象**：feed URL 是占位符，updater 从未真正配置过。

**我的判断**：Demo 不需要自动更新，所以这条本身不急；但它和 T63/T64 在同一族，一起想清楚比分三次想省事。

**边界（重要）**：**只做契约与配置层，不要接线到 `lib.rs`。** 如果发现必须改 `lib.rs` 才能推进，停下报告——那部分留给主线。

### 族 B —— 权限韧性（T66a）

#### T66a 文件权限只检查不修复

**现象**：`secure_store.rs:182` 的 `ensure_private_file_permissions` 对**文件**只检查权限位、不对不上就报错；而**同一个文件里**对**目录**却是强制 `chmod 0700` 修复。

**我的判断**：同一个模块里两套策略，这是缺陷不是设计。真实触发场景是 **Time Machine 恢复 / 迁移助理 / iCloud 同步 Library**——这些会造出带 group/other 位的密钥文件，用户表现是**启动即闪退，零提示**。

**依据**：这是主线 T61 风险面调研里评出的**最高真实概率**那一档（见 `docs/development/T61-setup-abort-risk.md` 第五节）。演示机是全新 Mac 所以本次不触发，但客户机器很可能是迁移过来的。

**要做的**：把文件权限对齐成和目录一样的「发现漂移就 `chmod` 修复」，只有修复失败才 `Err`。

**边界**：`video_job_workspace.rs:1299` 的 `validate_private_directory_metadata` 是同型问题的另一半，**归主线**（T66b，与 T61 同文件）。你只做 `secure_store.rs` 这半。

### 族 C —— Python 执行器与脚本（T58c / T62）

#### T58c 拒绝原因要不要转发给用户

**现状**：一句话生成被执行器拒绝时，用户只看到一个笼统错误，**拿不到具体原因**。

**我的判断**：能转发，且不需要读 stderr。

**依据**（静态核查已完成）：`entry.py` 的 13 处 `_reject` 与 `agent.py` 的全部拒绝消息都是**固定字面量**；唯二的插值是结构标签和门禁码闭集；`MotionBrief` 越界消息也不回显 brief 原文。所以转发这些字符串不存在泄漏用户输入或本机路径的风险。

**冲突点（这就是它还没做的原因）**：转发要改共用 error wire，与既有的「细节不进 wire」决定冲突。

**要做的**：**不要**去放宽通用 error wire。加一个**专用的、闭集的**拒绝原因字段——只允许承载上述固定字面量集合，用类型或校验把「任意字符串」挡在外面。这样既给了用户可行动的信息，又没有破坏原来那条决定。

**如果你读完代码认为专用字段这条路走不通**，把理由写清楚，我来定。

#### T62 `run_script_tests.py` docstring 写死 "37 self-contained test scripts"

**现象**：实际已 41。代码本身是 glob 推导的，**没有功能问题**。

**我的判断**：这不是凑数任务。这个会静默落后的散文计数**就写在那份讲 "discovery is derived, never curated" 的文档里**——文档自己违反了自己主张的原则。修法是让这个数字也从 glob 推导（或者干脆不写数字）。

**顺手**：如果发现同一份文档里还有别的手抄计数，一并处理。

---

## 3. 起点与交回

**起点**：从 `origin/main` 的最新提交建分支。开工前 `git fetch && git log --oneline -1 origin/main` 确认。

**分支**：`codex/resilience-batch`

**每个任务一个提交**，提交信息用中文（conventional commit 前缀可保留英文），不加任何 AI 署名。

**每完成一个任务，在本文件末尾「进度回填」追加一行**，然后提交推送。主线读这里，不会去翻你的分支历史。

**做完全部或卡住时**：推送分支并在回填区写明状态。主线负责 review 后 `git merge --no-ff` 进 main。

---

## 4. 进度回填（codex 写这里，主线只读）

| 任务 | 状态 | 提交 | RED 证据（看到的失败输出） | 备注 / 反驳 |
|---|---|---|---|---|
| T63 | ⬜ | | | |
| T64 | ⬜ | | | |
| T22 | ⬜ | | | |
| T66a | ⬜ | | | |
| T58c | ⬜ | | | |
| T62 | ✅ 完成 | 本提交 | 防回归断言先捕获 `37`、`14`、`7/14` 与 `three browser_use scripts` 四处手抄计数 | 散文计数已移除；定向检查与 Ruff 通过，完整聚合 runner 仍受既存 `tools/browser-use-contract/.venv` 未挂载阻塞 |

---

## 附录：T26「内置浏览器换成 Chromium 开源构建」为什么不在这批里

**任务标题里的结论是错的，不要照做。** 完整证据见 `docs/development/PLAN-chromium-replacement.md`，摘要：

1. **任务假设的那条路不存在** —— Playwright 1.61.0 在 macOS/Windows 上**已停产 Chromium 构建**，只剩 Chrome for Testing。实测 CDN：`chromium-mac-arm64.zip` 在 rev ≥1210 是 404，rev 1200~1205 是 307 重定向到 CfT 桶；
2. **所有 Chromium 开源构建都不含 H.264/AAC**（两个独立来源交叉验证，是 `proprietary_codecs` 编译开关的差异不是版本差异）。实测抖音播放器**彻底失效**：黑屏、`readyState=0`、`networkState=3`、CDN 反复重试；
3. **体积不降反增**：mac 多 6 MB；Windows 快照 773 MiB（含一个 328 MiB 的 `interactive_ui_tests.exe`），剔掉后仍 445 MiB > 门禁上限 420 MiB；
4. **换过去解决不了根本担心**：实测 **CfT 在 `sec-ch-ua` 里报的就已经是 `"Chromium"`**，`userAgentData.brands` 里没有 "Google Chrome"。换掉的是**品牌文件名**，不是**被网站看到的身份**；
5. **唯一一条白纸黑字的分发禁令只针对一个文件** —— macOS 版 CfT 里的 `libwidevinecdm.dylib`（20.2 MiB），其 LICENSE 明文写着未经与 Google 单独签署协议不得分发。实测删掉它：签名门禁不受影响、H.264/AAC/HEVC 全部保留、抖音正常渲染，且 Windows 版压根没有这个文件。

所以正确的任务是「**留在 CfT，剔除 Widevine CDM**」，1～2 人日而不是 10～15 人日。

**但它也不在这批里**，因为它会改变正式包内容，与主线 T10（在正式签名包上重跑用户路径）直接冲突。等 T10 收口后再做。
