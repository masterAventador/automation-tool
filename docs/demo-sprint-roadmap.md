# 客户 Demo 冲刺台账

> 本文件是 2026-07-25/26 大返工与客户 Demo 冲刺这批任务的**唯一状态源**。会话中断后从这里恢复。
>
> 与 `docs/development-roadmap.md`（产品主线 EB/VF/BM/U9）和 `docs/embedded-browser-video-studio-roadmap.md`（内置浏览器与视频专项）并列，不互相双写状态。本批用 `T<n>` 编号，逐条证据在 `docs/development/` 下对应文件。
>
> **每个任务号在本文件中只出现在一张状态表里。** 同一个号以前在多节重复出现，是「数不清还剩多少」的直接原因。

---

## 进度总览

> **数任务只能按小节归属数，不能 grep。** 已完成区的表格没有状态列，用状态符号统计会把 32 行判成「其他」——而且不报错。这条已经吃过一次亏。

| 小节 | 数量 |
|---|---:|
| ✅ 生产装配与出厂门禁 | 11 |
| ✅ 云端与交付 | 6 |
| ✅ 视频与内容 | 13 |
| ✅ 验收基础设施与门禁 | 17 |
| ❌ 查证不成立（观察真、结论错，无需修） | 5 |
| **小计：已收口** | **52** |
| 一、Demo 前必须收口 | 9 |
| T73～T95（T10 那轮挖出的新任务） | 15 |
| 冻结区·今晚撞见的技术债 | 12 |
| 冻结区·原有待办 | 10 |
| **小计：未收口** | **46** |
| **去重后总计** | **98** |

**T10 那一轮把任务从 74 涨到 98**——二十四条新的全部来自「真的去跑一遍」，其中 T80/T81/T82 已在同一轮修完合并，T83 已查证结论。

**Demo 前要收口的是 9 项**，其余 37 项冻结或已派给 codex（见 `docs/codex-parallel-batch.md`）。

> 这三个数由 `scripts/check_demo_roadmap_counts.py` 守着，改完台账跑一次即可。**但它守不住这段散文里的数字**——上面这两行以前就漂过。

---

## 当前下一步

1. **等 codex 合并** → 跑最终全量回归 → 从那个 HEAD 出签名包（约一个半小时，大头是公证）→ **在那个包上复验用户路径**。最后一步才是 T10 的定义本身：验收必须发生在你实际拿到的产物上。现在这个包缺 UI 修复、缺 DMG 的 `Applications` 链接、缺 07-26 夜里合的四条。
2. **👉 你现在就能做（30 秒）：完全退出 App 再打开，看「平台状态」还是不是「登录正常」。** 这验的是抖音登录态有没有落盘，是 T6 唯一未闭环的一项，与换不换包无关。
3. 其余按下表顺序。**新发现一律记进冻结区，不派线去做。** Demo 前不再发散。

> **07-26 22:40 台账纠偏。** T68、T48、T5 三条此前挂着「等你做」，实际早已完成，证据一直在磁盘和服务端：
> `installations` 已从文档记录的 0 行变为 5 行且全部绑定账号；`device-credential-v1` 与最早那行逐秒吻合；三份密钥 20:22 就位并被那次成功出片真实使用。
> **这三条是「复述台账」而不是「去查事实」造成的误报**——台账写什么就说什么，而台账本身落后于现实。判据：**说某项待办之前，先找到它此刻的证据在哪。** 找不到证据的「未完成」和找不到证据的「已完成」一样不可信。

---

## 一、Demo 前必须收口

「并行」列回答的是：**这一项能不能开一条独立分支（比如交给 codex）跟我同时做而不打架。**
判据不是优先级高低，而是**文件面是否与我正在改的重叠**——今晚已经实证过，低优先级任务照样牵出核心缺陷，按优先级切会切在错误的地方。

| ID | 任务 | 状态 | 并行 | 说明 |
|---|---|---|---|---|
| T10 | 从正式签名包跑一遍所有用例 | 🚧 | ❌ 不可 | 本轮目标本身。与 T57b 的 e2e 重组直接冲突，必须我做。**已修四条真红**（见下「T10 已抓到的」）；正式包正在 `wt/release` 出，四条扫描线并行 |
| T72 | 门禁执行者的三处空洞 | ⬜ | ✅ 可 | 全量扫描的副产物，同一类：① `run_script_tests.py` **没被接进任何门禁**（`grep -rn run_script_tests .github/` 零命中）——为「守卫没人执行」造的解药，自己没人执行；② `deploy/ingress/test_ingress_config.py`(2 条) 与 `deploy/cloud/test_cloud_deployment.py`(46 条) 共 **48 条断言无任何执行者**，pytest 的 `testpaths=["tests"]` 收不到、runner 只 glob `scripts/` 也收不到，唯一调用方式是 `docs/development/T68.md:131` 里一行手敲命令（实跑是绿的，属潜伏风险）；③ runner 只对**失败**脚本打印输出（`:145`），通过的 stdout 直接丢弃，于是「跑 50 条断言」和「什么都没跑就 return 0」在汇总里长得一模一样——实测 41 个脚本共 400 条检查，**11 个「通过」数不出任何执行证据**，其中 `test_video_studio_acceptance_scope.py` 正是当初红着躺了很久那条。另 `test_script_test_runner.py:73` 的元测试用与实现完全相同的子串判据去断言实现，恒真 |
| T69 | App 全程零日志 | ⬜ | ❌ 不可 | 无 tracing / log / env_logger 依赖，零条日志调用。**演示机上一旦出问题，我们是瞎的**（今天就吃过亏：我让你跑二进制看 stderr，那条命令根本不可能有输出）。Demo 前只加「不改任何行为」的一层 |
| T61 | setup 失败即 abort，无兜底（含 T66b、T65） | ⬜ | ❌ 不可 | 调研已完成，**紧迫性下降**：「做完视频→正常退出→再打开」经导入链路核实是安全的，前一条线的高风险判断被推翻。剩余真实窗口只有「删成片时强退」和硬断电。建议一处四行兜底，不动 setup 结构。**T66b（目录权限只检查不修复）和 T65（`cleanup_expired` 无调用方）都在 `video_job_workspace.rs` 同一条启动路径上，并进来一起改。** 全部证据见 `docs/development/T61-setup-abort-risk.md` |
| T70 | 演示前检查清单补完 | 🚧 | ⚠️ 半 | §2、§5.1 已实测贴输出；§3 等最终 DMG，§7 等 T36 定型 |
| T6 | 抖音扫码登录与后续链路 | 🔍 待验收 | — | **扫码已完成并实测通过**，服务端记录 `state = healthy`。本机只读核对：指针 `current-douyin-profile-v1` 指向产品自建的 `739b9297-…`，档案目录 21:35 更新；此前我手工拷进去的污染档案 `df1c89f0` 已不在链路上。**换新包不用重扫**——档案在 `app_data_dir` 下按 bundle identifier `com.aventador.automationtool` 定位，构建之间不变。**只剩一件**：完全退出 App 再打开，确认仍是「登录正常」（预检清单 §375 第二步，验的是登录态落盘，不是当时那一刻通没通）。遗留两处旧档案（顶层 `embedded-browser-profiles/douyin/df1c89f0`、`browser-profiles/douyin/d2265434`）不在链路上，未清理——产品自管目录不手工动 |
| T53 | Windows 验收机装 Node + pre-push hook | 👤 | — | 当前 v22.20.0 低于 `engines >=24 <27`，快档门禁不需要（`tsc` 只要 `>=14.17`）但**慢档必须对齐**。`.git/hooks/pre-push` 已被 git-lfs 占用，需人工插入共存。挂 pre-push 而非 pre-commit——后者跑在工作树上，而工作树正是被遮蔽的那个对象 |
| T7 | Windows GUI 三项验收 | 👤 | — | 依赖 T53。你已授权装 Node |
| T71 | 要不要补背景音乐 | 👤 决策 | — | 合规上无障碍（已有 4 个自制、权利全 `true` 的 `music_sfx`），但那 4 个是 1–2 秒音效不是 BGM。**做不做 / 几首 / 什么风格等你定** |

### T10 已抓到并修掉的（跑全量的直接产出）

| 提交 | 问题 | 判定 |
|---|---|---|
| `e4b546e` | 编排代理在 `889cf9e` 搬走后，**4 处引用还指着旧路径**。一处响亮（`.mjs` 直接 open 文件 ENOENT，`pnpm test` 全红），**三处躺在契约的 `definedIn` 里一声不吭**——指向不存在路径的契约管辖的是空集，和管辖一切的长得一模一样 | 产品外的事实源缺陷。补 `check_contract_declared_paths.py`。两种自动推导都不可用（形状规则误报 1462 条、自扩展 key 推导误报 474 条，因为 `path` 是包内相对、`healthPath` 是 URL 路径、`excludedGlobs` 是通配符），只能手工 key 集 + 门禁自报覆盖范围 |
| `ce45efd` | 发版构建停在 `_run_pyinstaller`，只给一句「被拒绝」。`capture_output=True` 抓走 PyInstaller 输出后 `raise _reject()` 全丢掉，**构建失败零诊断** | 真缺陷。真实原因（缺一个 vendored 文件）是手工重跑同一条命令才拿到的，构建机上没人能这么做 |
| `dd735dc` | 全量扫描报 **216 条集成失败，一条真的都没有**。夹具 shell 出裸 `alembic` 靠 PATH 解析，而 `.venv/bin/python -m pytest`（完全正常的跑法）不会把 `.venv/bin` 放进 PATH | 基建问题伪装成产品大面积崩溃。改 `sys.executable -m alembic`，用「有 docker、无 venv/bin」的精确复现条件验证 16 条集成用例全过 |
| `c084f2d` | `upstream-name-leak.spec.ts:77/86` 各卡 30 秒超时点「发布到抖音」 | 测试过期，产品不动。`PublishWorkspace.tsx:215` 的 `disabled={busy \|\| !publishable}` 与 `ui-harness.spec.ts:139` 的 `toBeDisabled()` 断言两处坐实「填完文案前禁用」是有意行为；leak spec 从没填过 |

**跑全量之外，我自己的并行设置也炸了两次**，记在这里因为下次一定还会遇到：

1. **软链 `backend/.venv` 到主树 = 所有并行线的 Python 结论作废。** venv 里 `automation_tool` 是 editable 安装，指针在**单个共享文件** `site-packages/automation_tool.pth` 里；而仓库自己的 `package.json` 有多条 `uv run --project ../backend --locked`（`test:p9-05`、`test:h8-22-windows-package`、`p9-02/04/07`），**这条命令会 sync 并改写它**。做过对照实验定性（跑一次，`.pth` 从 `<root>/backend/src` 变成 `<root>/wt/release/backend/src`），不是谁违规。发现时它指着 `wt/codex`——主树 pytest 在测 codex 的在飞代码，**正在跑的正式包构建也在打包 codex 的后端源码**。已改为每棵树 `uv sync --locked`，四棵共 10.5 秒、依赖零重下。
2. **软链 `node_modules` 差点被 pnpm 删掉。** pnpm 11 跑任何 script 前校验依赖，软链必然不匹配，它就决定**先删再装**，只有「没有 TTY」挡住了；而它的报错**建议设 `CI=true`**，照做就静默删掉主树那份、三条线一起报废。已改为每棵树独立 `pnpm install`，单树 3.3 秒。

判据：软链安不安全**不看体积，看有没有正常命令会去改写它**。`vendor/*` 只读 submodule 可以软链（先核对各 worktree gitlink 一致），`.venv` 和 `node_modules` 不行。

### T10 跑全量与并行验收挖出的新任务（T73～T95）

> 这十四条全部产生于 T10 那一轮：五条并行验收线 + 一条 Windows 线 + 包审计两轮。
> 编号从 T73 起，**T82 归 Windows 提权 ACL**（`docs/development/T82.md` 已占用），生成耗时那条顺延为 T83。
> T84～T86 是包审计第二轮点出的演示日问题，07-26 夜间三条独立 worktree 并行修，**都是「演示当天客户会看见」而不是「代码不干净」**。
> T90～T91 来自稳定性线的失败注入，**是演示当天客户会看见的产品缺陷，不是测试问题**。
> T80/T81/T82 已修完合并、T83 已查证结论，四条都已移出本表——留在这里会让「未收口」虚高。

| ID | 任务 | 状态 | 归属 | 依据 |
|---|---|---|---|---|
| T73 | 测试把文件写进只读的 vendor submodule | ⬜ | codex | 跑完测试后 hyperframes 有 68 个 `output/compiled.html` 被改写、moneyprinterturbo 有 3 个 `.mp4` 被新建，`check_third_party_sources.py` 判整棵树为脏并拒绝 → **任何一次测试跑完，这道发版门禁就失败**。违反 CLAUDE.md 第 6 节。修复难点：`reset --hard` 清不掉（`.gitattributes` 声明走 LFS 而仓库存的是内容，clean 过滤器转换后与 index 对不上），只能整目录删除 + 全新检出 |
| T74 | 执行器包缓存键是常量，34 个驱动永远拿不到新执行器 | ⬜ | codex | `desktop_e2e_prerequisites.py:75` 的 `SHARED_EXECUTOR_BUILD_ID` 是常量字符串，执行器源码不参与缓存键。**云端 E2E 线用 A/B 坐实**：旧缓存包 63 秒后 `exit=70`，重建包（只花 9 秒）132 秒成功，直接跑源码也成功。同文件 `:90` 还硬编码 `REPOSITORY_ROOT/.local/` 而不用 `archive_path()`，导致那 34 个驱动**从任何 worktree 跑都在第一步死** |
| T75 | 另一处吞掉 PyInstaller 输出（主线漏修） | ⬜ | codex | `run_e4_07_acceptance.py:226` 的 `build_signed_executor` 同样 `capture_output=True` 后丢弃。`ce45efd` 只修了 `macos_candidate.py` 的 `_run_pyinstaller`，是另一个函数 |
| T76 | `desktop-e2e` 前端入口让 workbench 断言恒真 | ⬜ | codex | `test:tauri` 与 `test:h8-19-app` **37 毫秒通过**。`test-tauri-main.tsx` 注入的 `desktopShellStartupCheck` 无条件返回 ready 且**不注入生产的 17 个 gateway**，于是 `workbench.spec.ts` 断言恒真，而它是该 spec 唯一执行者。对照组：`model-service-e2e` 的 `test-production-main.ts` 直接 `import("./main")` |
| T77 | ~~B5-13 前端投影与权威态不一致~~ → **前提已证伪** | 🔽 降级 | codex | 云端线拿到逐字节响应：权威态是 `{"platform":"douyin","state":"unknown","observedAt":null}`（恰好 57 字节），**不是 `missing`**。而 `unknown` 从 Python 到 Rust 到 Zod 到 UI 完全自洽，界面显示「尚未确认」。B5-13 描述的症状只在一个**被双侧守死、当前不可达**的组合下发生。**不是现网 bug**，改为「确认该链路对 unknown 的处理正确」即可 |
| T78 | 视频线 7 驱动 / 8 spec 全卡启动门禁 | ⬜ | codex | `780abce` 拆桩后驱动没跟上，`prepare_startup_gate` 的 34 个调用者里这 7 个一个都没有。`T36-oneshot-video-preview.md:116` 记了但说「5 个 spec」，实测 8 个。依赖 T74 |
| T79 | 124 个验收驱动无聚合执行器，48 个只被读源码不被执行 | ⬜ | codex | 按「引用」与「执行」分开判：被 npm script 点名 37、被真 subprocess 执行 4、**只被读源码从不执行 48**、**零执行者 35**。那 48 个最隐蔽：`.test.mjs` / `test_*.py` 只 `readFile` 驱动源码做文本断言，**绿的是「源码长这样」不是「能跑通」**——`run_t3_12` 在读者下全绿、真执行时 exit=1。`run_vf_01_acceptance.py` 全仓零命中 |
| T90 | **模型侧三种失败都说同一句话，而且是错的** | ⬜ | **我** | 失败注入实测：模型连不上 → 2 秒后「判定这次描述做不出来…请换一句更具体的描述后重试」；模型卡死不断 → **363 秒后一字不差的同一句**。两种都不是用户的描述有问题，而产品让用户去改句子。演示当天讲解人会照着这句话去改文案，越改越错 |
| T91 | **提交后切页 = 永远没有任何提示**（静默失败） | ⬜ | **我** | 任务第 4 秒就失败了，切到别的页面再回来，列表显示「还没有真实制作任务」，**第 12 分钟仍然什么都不说**；全屏关键词扫描全阴性（做不出来/超时/出错/失败/不可用/无法 全 no）。另：等待期间只有一个转圈的按钮——`setSubmitMessage(null)` 清空后不写新提示，那句「已提交」在 `.then()` 里，比字面意思晚一到两分钟。**演示当天最便宜的规避：提交后不要切页** |
| T93 | **工作台首页把调试字段当正文，最近任务列表是裸 UUID** | ⬜ | **我** | 首页最大那张「当前任务」卡的正文实测是 `Task ID: a8ed7b07-…` / `Revision: 3` / `事件水位: 2`；「最近任务」每行的可点标题是**裸 UUID**，没有任务名、关键词或时间。**客户第一眼看到的就是它。** 两半分开决定：`Revision` 与「事件水位」移走是小改动，但可能是有意佐证「任务快照是权威来源」这条架构主张，属产品判断；**UUID 换人话做不到小改**——`TaskSnapshot` 只有 `taskId/status/revision/lastEventSequence/createdAt/updatedAt`，**没有标题字段**，要显示关键词得先动后端契约（退一步可显示 `createdAt`） |
| T94 | **视频剪辑「时间轴编辑」「预览」两页签很可能同样塌陷，但未实测** | ⬜ | **我** | 它们用的 `.video-editing-track` / `.video-editing-clip` 等同样在 `global.css` 里没有任何规则，按 T88 查明的同一机制应当同样收缩。**没验证的原因是 UI Harness 的 gateway 建不出剪辑项目**，两个页签渲染不出来。T88 故意没把它们带进修复范围，理由写在 CSS 注释里——**改一个看不见的东西比不改更危险**。要收口需先让 harness 能造出草稿项目 |
| T95 | **视频制作第一步的「选择」按钮在折叠线以下 243px** | ⬜ | **我** | 1280×800 下按钮在 y=1043，要滚约 340px 才看得到。**客户看到的第一屏是一面没有任何可点动作的文字墙。** 不是小改动，需要重排第一步的信息密度，取舍见清单 |
| T84 | DMG 里没有 `/Applications`，客户当场装不了 | 🚧 | `dmg-install` | 实测解析 `tauri.conf.json`，`bundle` 段的完整内容就是 `{"active": true}`——**没有任何 DMG 定制**。挂载后用户看到一个孤立的 App 图标，没有可以拖进去的目标。演示当天客户拿到包的第一个动作就卡在这里。修配置 + 门禁测试由该线做，**挂载核对（`hdiutil attach` 看有没有 `Applications -> /Applications`）由我在最终出包时做** |
| T85 | 更新中心不用点就常显红字 | 🚧 | `update-ui` | 正式包审计里目视到的现象。代码事实：`AppUpdateCenter.tsx` 的 `stateColor()` **只有 `failed` 一个分支返回红**，所以红字必然来自状态机进了 `failed`；而状态机**根本没有「未配置 / 已关闭」这种状态**；组件 `useEffect` 挂载即轮询 `getState()`，**不需要用户点任何按钮**。演示时客户第一眼看到红字。派单时写死了边界：**不许为了消红把真实失败一起藏掉**——那是本项目反复吃亏的「静默成功」 |
| T86 | 动效成片可能静默产出静止画面 | 🚧 | `motion-still` | **已经真实发生过一次。** 随包示范里出现 CDN 版 gsap，而渲染沙箱硬离线；模型照抄一个 `<script src="https://...">`，沙箱加载不到，**页面不报错、逐帧渲染照常跑完、产出每帧相同的静止视频**——链路全绿，成片是废的。`agent.py:731` 已提到离线路径 `compositions/runtime/gsap.min.js`，说明这条路存在，但示范与校验是否对齐要查证。修法两侧：入口拦外部 URL，出口检测「首帧/中帧/末帧全同」。**出口那道更值钱——它不依赖我们猜到全部失败原因** |

### 「能在 App 里生成出一个视频」这条链路的收口状态

> 优先级判据：**用户能不能装上包、打开、输一句话、拿到一个能播的视频。** 不在这条链路上的一律不算 P0，哪怕它是真缺陷。
>
> **演示形态已定：走品牌动效成片（hyperframes）那条**，不走素材成片。理由：外部依赖 1 个 vs 4 个串联；生成侧已实测产出会动的 mp4 而素材线一次都没走通过；渲染全在本机无现场网络风险；不需要你去申请任何东西。代价是成片风格是文字动效 / 数据可视化，不是带实拍素材的口播视频。

| 序 | 环节 | 状态 | 证据 |
|---|---|---|---|
| 1 | 装上包、Gatekeeper 放行 | ✅ | 带 quarantine 判 `accepted / Notarized Developer ID`，已装到 `/Applications` |
| 1.5 | 产品账号登录 | 🔍 | T68 —— 修复已上线，**等你登一次** |
| 2 | App 启动到工作台 | ✅ | `control-plane-e2e` 上实测挂载；正式包 setup 全程跑完 |
| 3 | 设置页存模型密钥 | ✅ | 正式表单存真实密钥且页面不回显 |
| 4 | 输入一句话、空描述被拒 | ✅ | `getValue` 断言描述确实进表单；空描述被人话拒绝且不产生任务 |
| 5 | 点击提交 → 命令开始执行 | ✅ | 真实 App：点击到提交落地 3 秒 |
| 6 | 编排子进程跑完 → 拿到合成 | ✅ | 真实 App：编排 178 秒返回合成 |
| 7 | 渲染出会动的 mp4 | ✅ | 真实 App 任务：360 帧、12.000 秒、h264 640×360，静图门禁放行 |
| 8 | App 内预览播放 | ✅ | 真实 App：`<video>` + base64 data URL 解码播放，`duration≈12s`、`currentTime>0`、无 `error` |

**环节 5–8 全部实证走通**，总耗时 3 分 34 秒，证据 `docs/development/T36-oneshot-video.md` 与 `.local/embedded-browser-video-studio/t36-evidence/t36-one-sentence.mp4`。
剩下的只有 1.5（等你登录）和「在正式签名包上重跑」（T10）。

---

## 二、冻结到 Demo 之后

> 这些都是真问题，但**没有一个挡着下周演示**。Demo 前不再派线，只在此登记。
>
> 「并行」列同上：✅ 表示文件面与我 Demo 前要动的地方不重叠，可以另开分支同时做。

### 今晚撞见的技术债

| ID | 任务 | 并行 | 一句话 |
|---|---|---|---|
| T63 | **三处「版本号一升就砖」，会同时打死所有老机器** | 🤖 codex | `executor_platform.rs:354` 诊断设置版本无迁移路径；`app_update_policy.rs:288` 发布通道名变更即 abort；`app_update_policy.rs:282` 要求存量文件**重新序列化后与磁盘字节逐字节相同**（字段顺序或序列化行为任何变化即 abort）。本次演示不可能触发（单一构建单一通道），但**第一次真正发版升级时会同时打死所有老机器** |
| T64 | `AppUpdateCache` 有两个非自愈的强退窗口 | 🤖 codex | `download()` 尾段 351-357：`atomic_replace` 之后、`save_cache_manifest` 之前被杀 → package 有而 manifest 无 → 永久 abort；`partial_manifest.delete()` 之前被杀 → 同型。**本包里是死的**（feed URL 是 `.invalid` 保留域，永不解析），但**换成真 feed URL 就变成「更新到一半被强退 = 砖」的定时炸弹** |
| T92 | **把动效 HTML 从模型输出挪回本机模板** | 🤖 | T83 查明耗时严格正比于模型写的字节数，而 `composition_html` 占输出 70%。改成本机模板 + 模型只填结构化字段，提交到完成有望从两分多钟降到几十秒。**这是产品改动，演示前不动** |
| T66a | 文件权限只检查不修复（`secure_store.rs` 那半） | 🤖 codex | `ensure_private_file_permissions`（`secure_store.rs:182`）对**文件**只检查不修复，而同文件对**目录**却是强制 `chmod 0700` 修复——同一个文件里两套策略。Time Machine / 迁移助理恢复的账号会造出带 group/other 位的密钥文件 → 永久闪退 |
| T66b | 目录权限只检查不修复（`video_job_workspace.rs` 那半） | ❌ 我做 | `validate_private_directory_metadata`（`video_job_workspace.rs:1299`）同型，但**和 T61 在同一个文件同一条启动路径上**，并入 T61 一起改 |
| T67 | Windows 企业域 AppData 重定向会启动即闪退 | ✅ 已合并 | `browser_profiles_windows.rs:504` 的 `normalized_path_key` 只剥 `\\?\` 前缀，**不处理 `\\?\UNC\`**；文件夹重定向到 UNC 共享或 SUBST 映射盘时 `final_path != normalized_path_key` → abort。另 `ensure_no_reparse_components` 拒绝路径上任何 junction（把 AppData 搬到 D 盘留 junction 是常见场景）。**读代码推断，未上机验证** |
| T65 | `cleanup_expired` 生产代码里没有任何调用方 | ❌ 与 T61 同文件 | 只有 `tests/video_job_workspace.rs:391` 调。30 天保留策略形同虚设，artifacts 单调增长，**启动时的 abort 面随视频数量线性增长** |
| T50 | 注销成功但界面报失败 | 🤖 codex | 5 次复现 4 次。内层 ~5 秒轮询包在 60 秒超时里——**不是等不起，是自己先放弃了**。已从演示脚本摘掉「安全注销」 |
| T58c | 拒绝原因要不要转发给用户 | 🤖 codex | 静态核查已确定**能拿到且不需要读 stderr**：`entry.py` 13 处 `_reject` 与 `agent.py` 全部拒绝消息都是固定字面量，唯二插值是结构标签和门禁码闭集，`MotionBrief` 越界消息也不回显 brief 原文。但转发要改共用 error wire，与「细节不进 wire」的决定冲突，代价写在台账里等决策 |
| T62 | `run_script_tests.py` docstring 写死 "37 self-contained test scripts" | 🤖 codex | 实际已 41。代码本身是 glob 推导的没有功能问题，但这个会静默落后的散文计数**就写在那份讲 "discovery is derived, never curated" 的文档里** |
| T45 | Control Plane 镜像被打进 playwright | ❌ 碰 `uv.lock` | 约 50MB。代码层守住了 CLAUDE.md 4.2，打包层破了 |
| T57b | 按 T57 调研结论执行 e2e 入口的并/修/废 | ❌ 与 T10 冲突 | **并**：VF-06 / BM-15 / VE-03 / VE-04 搬到 control-plane-e2e（+BM-06、CQ-01 并入）。**废**：B5-04（产品 UI 已删）、`workbench.spec.ts`、三条 0-click 的 update spec。**重新设计**：BM-08、IM-05。另注：`video-studio-e2e` 这个 feature 已不门控任何一行产品代码 |

### 原有待办

| ID | 任务 | 并行 | 为什么冻结 |
|---|---|---|---|
| T4 | 补 VE 剪辑装配任务 | ❌ 需设计 | 大工程，Demo 不演。你此前明确困惑过「剪辑不需要上传素材吗」 |
| T19 | 动效零件接 AI 一句话制作链路（方案 B） | ❌ 需设计 | 大工程 |
| T22 | 自动更新在可发布包里从未配置 | ⚠️ 与 T63/T64 同文件 | Demo 不需要 |
| T24 | 执行器包根按 `debug_assertions` 分叉 | ✅ 可 | |
| T25 | 视频线 WDIO 验收补齐真实资源前置 | ❌ 与 T57b 重叠 | |
| T26 | ~~内置浏览器换成 Chromium 开源构建~~ → **剔除 Widevine CDM** | ❌ 与 T10 冲突 | **任务标题的结论是反的，调研结论是不换**：Playwright 已停产 mac/win 的 Chromium 构建（实测 404）；所有 Chromium 开源构建不含 H.264/AAC，实测**抖音播放器彻底黑屏**；体积不降反增且 Windows 超门禁上限；而且 **CfT 在 `sec-ch-ua` 里报的就已经是 `"Chromium"`**，换掉的只是文件名不是被网站看到的身份。**唯一白纸黑字的分发禁令只针对一个文件**：macOS 版 CfT 里的 `libwidevinecdm.dylib`（20.2 MiB），LICENSE 明文禁止无协议分发；实测删掉它零功能损失、零门禁风险。正确任务是「留在 CfT + 剔除 Widevine」，1～2 人日而非 10～15。**因会改变正式包内容，须等 T10 收口后做。** 见 `docs/development/PLAN-chromium-replacement.md` |
| T37 | 四条合规决策 | 👤 | 你已定：Demo 后处理 |
| T40 | 包内 UTM Kabel KT 字体权利未判定 | ⚠️ 共享契约 | 合规，Demo 后。改 `contracts/quality/asset-rights-policy` 需独占 |
| T41 | 动效叠加字体未进权利登记表 | ⚠️ 共享契约 | 同上。`big-shoulders-display-latin.woff2` 登记在另一份契约里 |
| T38 | 演示后回收清单 | ✅ 可（纯文档） | 演示后才用 |

---

## 三、已收口（42 项，无需再管）

### ❌ 查证不成立 —— 观察是真的，结论是错的

| ID | 原判断 | 实际 |
|---|---|---|
| T58a | 点击后一分钟命令没开始 | 那 15 分钟是 `browser.waitUntil` 自己的 900 秒预算——它把条件抛出的异常当「还没满足」，跑满预算才用**最后一次**的异常 reject（用装的那份 `Timer` 实测过）。命令早就跑完并失败了，所以取样才看到主线程空闲、无子进程、工作区已删。**真缺陷是另一个**：提交命令声明为同步，全程占用主线程，已 TDD 修掉（`b6cc046`） |
| T54 | 主窗口不向辅助功能暴露 | 是 **macOS 锁屏行为**。判据被推翻：Chrome / VS Code / ghostty 同一时刻 AX 全是 0。连带更正：「窗口可见 3.5 分钟」不成立，那段时间屏幕锁着 |
| T46 | 上游品牌名在产品窗口顶部 | **产品路径不复现**（那是绕开产品窗口直连 Streamlit 才能看到的）。仍补了静态门禁覆盖内嵌 WebUI |
| T31 | H8-22 打包 App 闪退 | **不复现**。四条证据：结构 / 装配 / 运行 / 崩溃报告归因 |
| T83 | 编排 136～178 秒产出只有 9 个字段，慢得不合理 | **我的前提错了。** 第一次应答是**四个键**，第四个 `composition_html` 是一整份 standalone 动效 HTML（含 CSS 与完整 GSAP 时间轴），实测输出约 3200 token、**HTML 占 70%**；本机模板只提供 `runtime/gsap.min.js` 一个文件。把计时器套在代理自带的 `model_call` 注入点上实测：往返 **1 次**、修复轮 **0 次**（8/8）、**`localSeconds = 0.01`**（lint+check+snapshot+落盘共 10 毫秒）、首字节 40 毫秒。**最干净的一刀**：73 token 提示词、零产品代码，模型自己跑 **178 秒**，比产品的真实编排还慢。下行速率是常数 4497–4652 B/s（波动 3%），耗时严格正比于模型这次写了多少字节。**慢在模型写得多，不在我们**。可动的杠杆见 T92（冻结） |

### ✅ 生产装配与出厂门禁

| ID | 关键结论 |
|---|---|
| T44 | **正式包接上 Developer ID 签名与公证**。296 个代码节点签名、289 个 Mach-O 全本团队签名 0 例外、entitlements 只加 `allow-jit`（有对照实验）。判据：带 quarantine 判定 `accepted` |
| T1 | 正式包补装三份视频运行时资源。病根：验收验的是「功能能不能跑通」，不是「用户拿到的那个包能不能跑通」 |
| T82 | **以管理员身份运行时内置浏览器 Profile 子系统整块失效**（`a440336`）。`apply_private_acl` 只设 DACL（owner 传 `null_mut()`），而 `verify_private_acl_parts` 要求 `owner == TokenUser`；提权会话里 `TokenOwner` 是 `BUILTIN\Administrators` → `EqualSid` 失败 → `UnsafeDirectory`。双向证明：提权 20/20 失败、非提权 12/12 + 8/8 通过。**修创建端不修校验端**——owner 天然持有 `WRITE_DAC`，接受 Administrators 等于让机器上每个管理员都能改写它保护的东西。见 `docs/development/T82.md` |
| T13 | 建立真正的生产构建路径与必需资源门禁，单一声明源 `contracts/quality/release-package-resources.v1.json` |
| T21 | 唯一产包路径不在任何自动门禁里 → 独立发版命令 `scripts/build_release_package.py` |
| T33 | 正式包需在干净工作树重建 |
| T35 | 包审计读共享 dist 而非构建时嵌入的产物 |
| T42 | 包审计 Python 夹具落后于 mjs 新增能力。根治：从 mjs 导出的 `requiredDistributionMarkers` 读，不再抄第二份 |
| T39 | 消除发版与开发环境的构建期分叉。登录界面此前只存在于 `customer-demo` 这个 Vite mode，正式包里整个被 tree-shake |
| T55 | 账号命令没进 desktop-e2e handler。修接线错误但不拆掉设备凭据那条**有意的**安全边界 |
| T59 | 前端入口里的第八处构建期分叉：`startup.ts:44` 的 `desktopShellStartupCheck` 无条件返回 ready，`single_build_path.rs` 只读 Rust 源码看不见它。**5 个入口全过是因为它们的前端根本不跑门禁** |

### ✅ 云端与交付

| ID | 关键结论 |
|---|---|
| T18 | **控制服务云端部署**。`https://at.xuanbai.tech` 真实可用。中途逮到两个真缺陷：重复部署必崩（首次全绿、第二次才炸）、AppleDouble 污染 Alembic。重新部署 31 秒 |
| T17 | 云端 Demo bootstrap 可注册无账号 Installation |
| T14 | 正式构建缺设备注册路径 |
| T68 | **产品账号登录已在正式 App 里走通**。根因是边界 nginx 用 `$request_id` 覆盖了 App 送的 `x-request-id`，App 比对回显不一致 → `ProtocolInvalid`，**登录第一个请求就失败**，所以 challenge 与 logout 从未发出。修复已部署。**07-26 服务端实测收口**：`installations` 从文档记录的 0 行变为 5 行、全部 `active` 且 `owner_user_id` 非空。见 `docs/development/T68.md` |
| T48 | **设备绑定已在真实链路成立**。本机 `profiles/demo-xuanbai/device-credential-v1` 创建于 20:19，与 `installations` 最早那行 `12:19:17+00` 逐秒吻合。**5 行不是「密钥每次重生成」**：多出来的来自各自独立 bundle identifier 的验收构建（例如 `…t36acceptance` 的设备身份 mtime 22:05，对应 `14:05:24+00` 那行），demo Profile 那把自 17:25 起未变。包里地址、Gatekeeper 放行、隔离启动此前均已验 |
| T5 | **三份密钥已在签名包的 demo Profile 里就位**并被真实使用：`model-service-video-creative-v1`、`model-service-script-v1`、`video-editing-service-aliyun-v1`，均写于 20:22；你后来那次成功出片就是用它们跑的，比单独验收更强。演示当天只需确认百炼额度仍可用 |

### ✅ 视频与内容

| ID | 关键结论 |
|---|---|
| T88 | **UI 屏效系统排查**（`0d10956`），14 条清单见 `docs/development/T88-ui-density-sweep.md`，只修「客观是错的、不需要审美判断」的三处。① `VideoEditingWorkbench` 在 JSX 里写了 16 个 `video-editing-*` 类名而 `global.css` 里**一条规则都没有**，antd `Space` 是 `inline-flex` 会收缩，1280×800 下页签只占内容列 992px 中的 **568px**——项目里其它每个功能都声明了这条，只有它整体漏掉；② 视频剪辑与作品发布的标题行间距是 **0px**（其余六页 20px，这个值在 CSS 里被复制了七份，这两页是漏写的那两个），而它们首块内容都是带彩色边框的 `Alert`。**基准改用 1280×800**——那是 `tauri.conf.json` 声明的生产窗口默认尺寸，客户双击图标后的实际尺寸，比原定参考尺寸更小，多条严重程度因此变化。**第一轮 GREEN 是假的**：12 条全绿而页面实看仍错（卡片撑到 992px 但卡片*里面*的 `Space` 仍收缩成 518px，比修之前更难看），补断言后第二轮才真绿——由它自己实看抓到 |
| T36 | **整条一句话生成链路收口**。真实 App 用户路径通过，3m34s，成片 360 帧 / 12.000 秒并在 App 内播放 |
| T58 | 编排子进程非零退出 —— **是过期的构建产物，不是产品缺陷**。`ensure_signed_executor_package` 的缓存键是一个常量字符串，执行器源码不参与，所以验收一直在跑一个早于 `--author-motion` 入口的包；它把编排请求当 bootstrap 读，exit 2、stdout 空 → `authoring_crashed`。`run_t36_acceptance.py` 现在起跑前先探这个包，不合格就重建 |
| T28 | **把 148MB 专有字体换成开源字体**。Noto Sans CJK，构建期按摘要下载不进 Git。包一级四道闸：缺字体 / 被替换 / 版权行不对 / 缺许可证 |
| T47 | 字幕兜底会现场下载 1.5GB whisper 模型 → `HF_HUB_OFFLINE=1`。A/B：修改前 2.00s 真出网，修改后 0.10s 被拒零字节 |
| T32 | 背景音乐三个选项全部等价于无音乐。不改 vendor，用注入层移除控件。判据：可访问性树条目 2 → 0 |
| T16 | 裁剪素材成片 Worker 647MB 打包体积 → 降到 353 MiB |
| T27 | 两条视频线的 ffmpeg 都会回退到用户系统组件 |
| T56 | 一句话入口把片长写死 12 秒且不提示。原文案还写着「最长 20 秒」，是误导。改后明示 12 秒**并给出去处** |
| T2 | 品牌动效段数与每段时长改为用户可配 |
| T8 | 动效零件自动推荐与中文名映射 |
| T29 | 零件区在固定模板路径下明确标为不参与 |
| T20 | 发布页永远没有视频可发 → 成片页补上「去发布」 |

### ✅ 验收基础设施与门禁

| ID | 关键结论 |
|---|---|
| T87 | **这份台账自己数不清自己**。两次漂移都被当成事实报给了人：一次是同一个任务号出现在两张表里，按表统计必然重复计数；一次是小节标题自带计数（`### ✅ 视频与内容（10）`）而实际有 12 行——**同一个数存两处，只维护了一处**。两次都没有任何东西会报错。新增 `scripts/check_demo_roadmap_counts.py`：任务号只许出现在一张表、总计必须等于唯一号数、小计必须加得上总计、**标题一律不许自带计数**（范围如 `（T73～T86）` 仍合法）。故意不按名字匹配总览行与小节——两者措辞本就不同，模糊匹配只会多一个维护错的地方。**未覆盖：正文散文里的数字**（如「其余 35 项」），那类只能靠人 |
| T60 | **渲染沙箱的安全断言在静默跳过**。9 处 `#[test]` 读不到验收环境变量就裸 `return`，libtest 对空跑的 body 打印 `1 passed; 0 failed`，而六条驱动判断测试跑没跑的唯一依据正是这个字符串——**空转逐字满足断言**。受影响的包括「渲染沙箱隔离恶意 HTML」「锁定版 Chromium 启动」「包内 Node 运行时协议」。改为 `#[ignore] + expect(...)`，驱动加 `--ignored` 与 `expect_summary` 实际执行条数断言（`0e53086`） |
| T43 | **没有任何门禁挡住 main 编译不过**。两起同型事故。定性两次修正，最终查明 GitHub Actions 因账单从未运行。已停用 Actions 并写 README；`scripts/commit_gate.py` 快档 6 秒，**每次运行植入已知缺陷自证** |
| T34 | **桌面 E2E 剩余失败逐条判定**。「4 条失败」两重都不成立：测自被遮蔽的树 + 重跑后只有 2 条稳定。D6-10 是**测试期望过时**不是产品缺陷；B5-13 是真缺陷（见 T50）。排除了「产品启动不稳」：56 次运行 0 新增崩溃报告 |
| T51 | 三组手工抄的清单改由权威来源推导。**变异检验抓出门禁自身的失效**：事件名正则 `[a-z_]+` 遇到带数字的新名字会让它从扫描里消失、门禁少检查一项却报通过 |
| T52 | 聚合执行者抓到的第一个真红 → 判定为**测试过期**（证据链完整）：越界在 `renderVerify()` 的 `--version` 探针与无头浏览器共用 1 秒预算，而假浏览器是刚写出的新文件，macOS 首次 exec 扫描吃掉 45%~64%。**没有放宽断言、没有加 skip** |
| T49 | 门禁有效性审计两轮。第二轮证明业务验收层**没有同类病**——「这一层没问题」本身是有价值的产出 |
| T57 | 纯 desktop-e2e 入口去留调研完成。**推翻了「整族长期未运行」这个前提**（执行部分见 T57b） |
| T30 | control-plane 层 28 个 E2E 配置自 07-22 起全部阻断 |
| T12 | 消除测试构建对启动门禁的短路 |
| T23 | 装配核对测试加强 |
| T11 | 审计 51 项已完成任务的生产装配真实性 |
| T9 | 排查全部待验收任务缺什么 |
| T15 | 法务页缺 ffmpeg GPLv3 条目与许可证全文 |
| T3 | 第三方软件声明页降权到设置页底部 |
| T80 | **四处门禁在干净树上跑不起来**（`2cbb325`/`def5837`/`5fc876a`）。B1 缺 offline 目录、B2 缺 `frontend/dist`（有传染性，`test:layers` 必断）、B3 缺 worker package 且**补救指路指错脚本**、B4 缺 EB-16 正式包。新增 `scripts/gate_prerequisites.py` 把「门禁→产物→生产者」单点声明，**提示信息从 `producer` 字段生成**，所以指错就是命令错。顺带修 `build_offline_motion_catalog.py` 的 `fetch()` 零重试——锁文件声明 71 个下载产物，零重试在干净机器上近乎必然失败 |
| T81 | **VF-04 自检只可能在 Windows 通过**（`2cbb325`）。`check_video_media_toolchain.py:539` 在 POSIX 造符号链接、Windows 造目录联接，而 `finally: linked.rmdir()` 对符号链接必然 `ENOTDIR`。**崩溃在 `finally`，断言本身已经通过了**——所以 macOS/Linux 从写下那天起就是红的 |

---

## 四、这一轮学到的（新会话请先读）

今晚所有事故归结为**同一种失效模式换了不同的衣服**：

**形态一 — 共享的可变状态产出不可信的证据**：工作树遮蔽坏提交 / 共享 git index 卷走别人的文件 / `.git/worktrees` 注册 / 模块在调查中途被搬走。

**形态二 — 一个检查报告成功，而它实际只是安静**：`--ignore-missing-imports` 把未解析 import 变成 `Any` / 14 个孤儿测试没人跑 / 6 个 CI workflow 因账单从未运行 / 包审计只问否定问题 / macOS 没有 `timeout` 命令导致管道没执行却退出 0。

据此形成的硬规矩：

- 判据永远指向**从提交提取出来的树**，不指向任何活的工作树、index 或本机状态
- **每道门禁必须能自证**：每次运行植入已知缺陷，抓不到就判自己失败
- 看到「输出为空 / 没有报错」时，**先证明工具真的执行了**
- 门禁要**明说自己不检查什么**——最危险的时刻不是抓不到问题，是别人以为它抓得到
- 覆盖清单必须从权威来源自动发现，手抄的清单落后时没有信号
- 并行多代理时用 `git commit -- <paths>`（重命名/删除要新旧路径都列）；跑门禁用 `git archive` 而非 `git worktree add`
- 拿到因果结论先问**有没有对照组**——「隐藏导致窗口不进 AX 树」就是个没有对照组的观察，据此做的决定是错的
- **按文件面切并行，不按优先级切**——低优先级任务照样牵出核心缺陷（T57 挖出前端桩分叉、T60 挖出验收空转），按优先级切会切在错误的地方
