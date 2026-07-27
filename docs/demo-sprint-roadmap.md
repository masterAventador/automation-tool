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
| ✅ 生产装配与出厂门禁 | 30 |
| ✅ 云端与交付 | 9 |
| ✅ 视频与内容 | 32 |
| ✅ 验收基础设施与门禁 | 44 |
| ❌ 查证不成立（观察真、结论错，无需修） | 7 |
| **小计：已收口** | **122** |
| 一、Demo 前必须收口 | 3 |
| T73～T100（T10 那轮挖出的新任务） | 2 |
| 冻结区·今晚撞见的技术债 | 5 |
| 冻结区·原有待办 | 3 |
| 冻结区·T101/T90b 自报的新窗口 | 0 |
| **小计：未收口** | **13** |
| **去重后总计** | **135** |

**Demo 前要收口的是 3 项**：**T7 要你动手**（Windows GUI，前置 T53 已就绪），**我这边是 T10 与新挖出的 T109**（抖音重新检查按钮，已派线）。
其余 12 项都不挡演示：3 项来自 T10 那轮（T78 缺云凭据、T79 没做、T90b 缺正式包目视），9 项冻结到 Demo 之后。

> 这几个数由 `scripts/check_demo_roadmap_counts.py` 守着，改完台账跑一次即可。**但它守不住这段散文里的数字**——上面这行以前就漂过。

---

## 当前下一步

1. **从当前 HEAD 出签名包** → 跑最终全量回归 → **在那个包上复验用户路径**。这才是 T10 的定义本身：验收必须发生在你实际拿到的产物上。**磁盘上现在没有一个能演的包**：`/Applications` 里那个主二进制是 07-26 20:42 的，之后 `frontend/src` + `src-tauri/src` 又合入 40 笔；20:52 那个 DMG 挂载后卷里只有 `.app`、**没有 `Applications` 符号链接**（T84 的修复 22:42 才进，晚于那个包）。实测证据见 `docs/development/T70.md`。
2. ~~完全退出 App 再打开，看「平台状态」还是不是「登录正常」~~ **07-27 用户已做，结果是「登录正常」，T6 收口。** 同一次操作**挖出 T109**：被动展示对了，但「打开登录处理」「我已处理，重新检查」两个按钮一点必报「暂时无法读取抖音登录状态」。已派线。
3. 其余按下表顺序。**新发现一律记进冻结区，不派线去做。** Demo 前不再发散。

> **07-27 T103 全量对账。** 未收口从 40 条核到 **15 条**（随后 T70 收口、T101/T90b 又报 3 个新窗口，现为 17）。**25 条早就做完了，只是台账不知道**：最久的是 T62（07-26 18:45 修好）和 T66a（18:46），到 07-27 01:09 对账为止各挂了约 **6 小时 25 分**；批量最大的一笔是 codex 第二批 19 条（23:17 合并，挂约 2 小时）。反方向的错（台账写着做完、实际没做）**一条都没有**。逐条证据、判据与查证中发现的产品缺陷见 `docs/development/T103.md`。
>
> **07-26 22:40 台账纠偏。** T68、T48、T5 三条此前挂着「等你做」，实际早已完成，证据一直在磁盘和服务端：
> `installations` 已从文档记录的 0 行变为 5 行且全部绑定账号；`device-credential-v1` 与最早那行逐秒吻合；三份密钥 20:22 就位并被那次成功出片真实使用。
> **这三条是「复述台账」而不是「去查事实」造成的误报**——台账写什么就说什么，而台账本身落后于现实。判据：**说某项待办之前，先找到它此刻的证据在哪。** 找不到证据的「未完成」和找不到证据的「已完成」一样不可信。
>
> **同一形状在 07-26 一天内至少发生四次**：T63/T64、codex 第二批 19 条、主线 T66a/T62/T22、夜间 T84/T85/T86。**代码合了、台账没回写**是这批任务唯一的系统性失效模式。台账里甚至出现过自相矛盾：T67 的「并行」列写着「✅ 已合并」，同一行却留在未收口表里，**没有任何东西会报错**——计数门禁守的是「数加不加得上」，守不住「这一行自己说的两句话对不对得上」。

---

## 一、Demo 前必须收口

「并行」列回答的是：**这一项能不能开一条独立分支（比如交给 codex）跟我同时做而不打架。**
判据不是优先级高低，而是**文件面是否与我正在改的重叠**——今晚已经实证过，低优先级任务照样牵出核心缺陷，按优先级切会切在错误的地方。

| ID | 任务 | 状态 | 并行 | 说明 |
|---|---|---|---|---|
| T10 | 从正式签名包跑一遍所有用例 | 🔍 待你走用户路径 | ❌ 不可 | **07-27 09:29 正式包已产出**：`wt/release/.local/release/cargo-target/release/bundle/dmg/自动化运营工具_0.1.0.dmg`，**469 MB**，`EXIT=0`，全部发版门禁通过。**逐项验过（主线亲自跑，不只信日志）**：DMG 自身 Gatekeeper `accepted` / Notarized Developer ID、公证票据 validate 通过；挂载后卷里有 `.app` **和 `Applications` 拖拽链接**（T84 的修复在位）；**卷内那个 `.app` 单独验**——`codesign --deep --strict` valid on disk / satisfies its Designated Requirement、Gatekeeper `accepted`、**公证票据 validate 通过**（单独验卷内 `.app` 是因为客户拖出去的是它，票据只贴在 DMG 上的话那个副本还得联网找 Apple 才能开）；卷内五类资源齐全：内置浏览器 336 / 执行器 293 / 素材成片 Worker 2107 / media-toolchain 8 / 动效 Worker 4 个文件。**出包共四次**：两次死在 T127（我引入的），一次死在 `xcrun failed: HTTPClientError.connectTimeout`（瞬时网络，事后实测立刻能连上），第四次成功。**仍待验收**：在这个包上走真实用户路径（安装 → 登录 → 一句话 → 出片 → 播放）——**那一步必须真人操作 App，主线做不了也不假装做**。T78 / T90b / T92 缺的是同一样东西 |
| T7 | Windows GUI 三项验收 | 👤 | — | 依赖 T53（**已就绪**）。你切到 Windows 上做 |
| T120 | **T7 的「三项」出处已查到，待你确认** | 👤 确认 | 从来没有任何文档列出过这三项（T116 查遍 roadmap 全部历史版本、开发路线图、交接单）。**用户 07-27 08:25 明确表示自己也不记得**，所以不能再靠回忆。**去仓库找依据**：`CLAUDE.md:209` 写着「**macOS 和 Windows 的正式包、权限和内置 Chromium 链路分别验收**」——**这三样就是「三项」的出处**，而且与 macOS 侧已完成的三件逐一对应。落到 Windows 上的可执行形式：①**正式包**——`run_eb_16_windows_acceptance.py`（真实 x86_64 NSIS 首发包）；②**权限**——T82 修过的提权会话 ACL（`apply_private_acl` 只设 DACL 而校验要求 owner 是 TokenUser，管理员会话里整块失效），需在真实 GUI 会话里复验；③**内置 Chromium 链路**——包内 Chromium 能起、能被产品用。**仓库里 Windows 专属驱动只有两个**（另一个是 `run_h8_22_windows_package_acceptance.py`，属更新链路），所以第三项没有现成驱动，要在 GUI 会话里人工走。**前置已全部就绪**：T53（Node/hook）、T119·T123（工具链已恢复并真跑通）、T122/T124（Windows 侧测试可移植性）。**等你确认这三项是否就是你当初的意思** |

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

### T10 跑全量与并行验收挖出的新任务（T73～T100）

> 这批全部产生于 T10 那一轮：五条并行验收线 + 一条 Windows 线 + 包审计两轮。
> 编号从 T73 起，**T82 归 Windows 提权 ACL**（`docs/development/T82.md` 已占用），生成耗时那条顺延为 T83。
> T84～T86 是包审计第二轮点出的演示日问题，**都是「演示当天客户会看见」而不是「代码不干净」**。
> **T80/T81/T82/T83 早已移出；T73～T77、T84～T86 经 T103 逐条查证已完成，同批移出**——留在这里会让「未收口」虚高，那正是这份台账要治的病。

| ID | 任务 | 状态 | 归属 | 依据 |
|---|---|---|---|---|
| T78 | 视频线 8 驱动 / 9 spec 全卡启动门禁 | 🔍 待验收 | codex | 共享 `video_studio_startup_harness` 已负责隔离环境、内置浏览器、签名 Executor、唯一 Compose project 的隔离 PostgreSQL、正式 Alembic 链与真实 Control Plane。**7 条真实本机桌面驱动全绿**（VF-06 / BM-06 / BM-08 / BM-15 / CQ-01 / IM-05 / VE-03，真实隐藏 Tauri + 真实 IPC + 真实 PG）。**VE-04 缺阿里云凭据，8/8 未闭环**，因此保持 🔍 不标完成。合并后回归 `036c267` 已修（`0e41d59` 删掉被两个模块 import 的共享常量）。见 `docs/development/T78.md` |
| T90b | **把失败原因区分带到 Rust** | 🔍 待验收 | `model-error-codes` | **已合并**（`8de19ba`）。分类表落在 `contracts/video/motion-authoring-refusal.v1.json`，**Python 与 Rust 都从它读、不留第二份**：`entry.py:53` 与 `motion_video_studio.rs:36` 的 `include_str!` 指向同一份。错误码按传输层/超时/拒绝分开，不再把「连不上」说成「你的描述做不出来」。**缺正式签名包目视**，故 🔍。三处自报缺口已入账为 T106、T107（另一处见 T105），见 `docs/development/T90b.md` 第八节 |

### 「能在 App 里生成出一个视频」这条链路的收口状态

> 优先级判据：**用户能不能装上包、打开、输一句话、拿到一个能播的视频。** 不在这条链路上的一律不算 P0，哪怕它是真缺陷。
>
> **演示形态已定：走品牌动效成片（hyperframes）那条**，不走素材成片。理由：外部依赖 1 个 vs 4 个串联；生成侧已实测产出会动的 mp4 而素材线一次都没走通过；渲染全在本机无现场网络风险；不需要你去申请任何东西。代价是成片风格是文字动效 / 数据可视化，不是带实拍素材的口播视频。

| 序 | 环节 | 状态 | 证据 |
|---|---|---|---|
| 1 | 装上包、Gatekeeper 放行 | ✅ | 带 quarantine 判 `accepted / Notarized Developer ID`，已装到 `/Applications` |
| 1.5 | 产品账号登录 | ✅ | **07-27 01:22 用户已在正式签名包里登过**。两条互不相干的痕迹对上：App 私有目录 `profiles/demo-xuanbai/product-account-session-v1` 428 字节、创建于 01:22（只看存在性与时间，未读内容）；T109 排查抖音按钮时独立做的 mtime 取证记着「01:22:56 登录产品账号 → 01:24:50 执行器起」。**这一行此前写着「等你登一次」是错的**——今晚第三次撞见台账在「还没做」这个方向落后于现实 |
| 2 | App 启动到工作台 | ✅ | `control-plane-e2e` 上实测挂载；正式包 setup 全程跑完 |
| 3 | 设置页存模型密钥 | ✅ | 正式表单存真实密钥且页面不回显 |
| 4 | 输入一句话、空描述被拒 | ✅ | `getValue` 断言描述确实进表单；空描述被人话拒绝且不产生任务 |
| 5 | 点击提交 → 命令开始执行 | ✅ | 真实 App：点击到提交落地 3 秒 |
| 6 | 编排子进程跑完 → 拿到合成 | ✅ | 真实 App：编排 178 秒返回合成 |
| 7 | 渲染出会动的 mp4 | ✅ | 真实 App 任务：360 帧、12.000 秒、h264 640×360，静图门禁放行 |
| 8 | App 内预览播放 | ✅ | 真实 App：`<video>` + base64 data URL 解码播放，`duration≈12s`、`currentTime>0`、无 `error` |

**环节 5–8 全部实证走通**，总耗时 3 分 34 秒，证据 `docs/development/T36-oneshot-video.md` 与 `.local/embedded-browser-video-studio/t36-evidence/t36-one-sentence.mp4`。
剩下的只有「在正式签名包上重跑」（T10）——1.5 已于 07-27 01:22 由用户在正式包里完成。

---

## 二、冻结到 Demo 之后

> 这些都是真问题，但**没有一个挡着下周演示**。Demo 前不再派线，只在此登记。
>
> 「并行」列同上：✅ 表示文件面与我 Demo 前要动的地方不重叠，可以另开分支同时做。

### 今晚撞见的技术债

| ID | 任务 | 并行 | 一句话 |
|---|---|---|---|
| T92 | **动效合成 HTML 挪回本机模板** | 🔍 待验收 | **提速属实但没到「几十秒」，我的前提错了**：T83 那条「耗时 ∝ 模型写的字节」讲的是**线路字节（含流式推理内容）**，不是正文字节；正文砍掉 3.2 倍，耗时只快 **2.4 倍**，差额是**推理时间，它不随正文缩短**——这是模型侧的地板。背靠背交替跑 **28 次真实调用**：模型秒中位 87.0 → 36.9，最坏 116.7 → 57.4，方差从 63 秒收到 23 秒，应答字节中位 7745 → 2429，一次通过率 9/11 → **10/10**。**放弃的表达力逐条写明**：任意 CSS 与 GSAP 编排（接受，12 套锁定风格本就承诺统一观感）、临时发明的组件（部分损失）、任意 SVG/图表（真损失，但 18 次观测从未出现）、`design.typography` 降级为说明字段、用户 Logo 入画（**零损失**，`lib.rs:746` 一直发空数组）。**一个关键设计判断**：不能只删 HTML——屏幕文案只存在于 `composition_html` 里，`SCRIPT.json` 是大纲、`STORYBOARD.json` 的 `purpose` 是导演备注，都不能直接上屏，所以分镜必须新增版式字段，否则成片退化成五张大纲卡。**途中查出并修掉一个真问题**：第一轮模板版通过率反而更低（4/7），查真实应答发现 `beat_id` 返回整数、`script.beats` 返回对象数组，**都是提示词没说清楚却全部以「请换一句更具体的描述」到达用户**；补两句提示词后 10/10。T86 静止画面防护实测有牙（剥掉模板自己的 tween，真实 Chromium 报 `render_static_frames`）。**主线复核**：合并后执行器 1198 条通过——**其中一条真红是两条线各自都对、合到一起才错**：T106 新增的落盘失败用例带着 `composition_html` 第四个键，而 T92 把首答契约收成恰好三键，于是断言差点在错误的理由上通过，已修夹具并记下原因。**07-27 04:06 主线实测补强**：在真实 App（T36 驱动，真实模型 + 本机模板 + 内置 Chromium）上重跑整条一句话生成链路，`EXIT=0`，产出 h264 640×360 / 12.000 秒 / 360 帧的真片；WebdriverIO 段 **1 分 28 秒**，上一次记录是整条 3 分 34 秒——**这是 T92 提速在完整链路上的第一次印证**，此前只有模型侧的数。同一次也证明了 T110 让 Worker 强制要求 `cancelMarker` 之后，**产品里所有构造渲染请求的地方都跟上了**（分层测试证明不了这件事）。**仍待验收**：正式签名包里的用户路径没跑；端到端 120 秒 → 约 70 秒那个数仍是算的；出包后随用户路径一并复验。**同一次复验要顺带改掉界面上那个写死的耗时预期**（T113 查出）：`VideoStudio.tsx:101` 的 `MOTION_AUTHORING_MEASURED = {typicalSeconds: 124, longestSeconds: 178}` 没跟着变，界面仍说「通常 2 分钟左右，最长约 3 分钟」。**不能凭 T92 的数字直接换**——旧常量量的是**编排整段墙钟**（T83 实测 136–178 秒），T92 量的是**模型秒**，口径不同；凭猜换一个数比留着旧数更糟（旧数偏保守，只是提示晚，不误导）。正式包那次跑通会产出真实的端到端耗时，用那个数改。**下一个可摘的果子**：`catalog_parts` 占着 15 个 id 的输出成本与 134 个 id 的提示词成本、18 次里 1 次的拒绝率而对画面零影响，子代理没动是对的——`FIX-motion-parts-selection-wiring.md` 写着这条链的产品语义还没定 |
| T115 | **扫码阶段强退 App，代价是重新扫码** | 👤 决策 | T114 查实并明确没擅自改。弃置租约时 **flock 其实早被内核释放**，磁盘上那个 `active` 只是陈旧旗子；但产品要求显式恢复，而唯一的恢复出口「安全注销」会**删掉整个档案**，于是代价是丢掉登录态、重新扫码。**这是有意的 fail-closed 设计**（`killed_lock_holder_preserves_marker_and_requires_explicit_recovery` 钉死了它），不是 bug，所以子代理没动它是对的。**但演示现场撞得到**：产品在「等扫码」时主动把用户支开，正是此刻 App 持着租约；此时强退就要重扫。可选：提供一种不删档案的确认式恢复（判据是它不能把「另一个进程真在用」也一并放行）。**做不做、什么形态，等你定** |
| T128 | **公证结果不被复用，每次重跑都要重走** | 🤖 | 三次出包实测：第一次已成功公证（提交 `97f10884`），第二次仍从 `Notarising the application bundle` 从头走。**每次重跑的时间成本是「等 Apple」而不是「构建」**，而那不由我们控制。叠加 T112（构建期取字体）与本次撞到的 `xcrun failed: HTTPClientError.connectTimeout`——**已有两条独立网络依赖打断过出包，且都在很靠后的位置**。两次实测都证明是瞬时抖动（事后立刻能连上 Apple、curl 200），但**演示当天临场重新出包时这是真实风险** |
| T112 | **出包在构建期依赖 GitHub raw，国内网络下会打断构建** | 🤖 | **07-27 03:15 真实撞上**：预建视频运行时时 `subtitle_font_assets.py:407` 取 `https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/LICENSE` 报 `SSL: UNEXPECTED_EOF_WHILE_READING`，整条 `prepare_video_runtime.py` 退出 1。**判为瞬时而非阻断**：紧接着沙盒内外各 curl 一次，**两次都 HTTP 200、1.3 秒**，重试即通过。来源是 T28 的设计（把 148 MB 专有字体换成 Noto CJK，按摘要构建期下载、不进 Git），本身是对的；问题是**这条网络依赖落在出包的关键路径上，而演示当天在国内网络**。风险面：临场需要重新出包时，这一步可能直接把构建打断，且错误发生在很靠后的位置（三个产物已建了两个）。可选做法：把字体与许可证纳入按摘要校验的本机缓存（同 `.local/offline-motion-deps` 那种形态）、或加有限次重试与镜像源。**没有当场改**：演示前不动出包路径是本轮的既定原则，且已知重试可用 |
| T130 | **动作授权私钥两端各生成一把，且没有任何东西会发现** | 🤖 | **07-27 11:54 在真实云主机上实测撞到**：服务端持有的私钥派生出 `V-k9YXpP…`，而当时已发出去的包里编译的是 `gfJEBEJ9s-…`，**两端对不上，包装上去也签不出它接受的动作授权**。根因是 `deploy_cloud_demo.py:394` 把这把密钥和其它密钥一视同仁——缺失时 `secrets.token_bytes(32)` 随机生成，而它是**唯一一把两端都要用的密钥**（服务端签发、App 用包内公钥验签）。`FIX-customer-demo-package-build.md` 要求的「同一份文件既喂构建又投递服务端」这一步从来没人执行过，**首台 Mac 出的那个 469 MB 包对这台服务器同样无效**。**最险的是它完全不报错**：health、version、账号登录、容器健康检查全绿。**本次已做**：服务端换成运维保管的那把（改前备份 `secrets.json.bak-20260727T115450`，私钥只走 stdin），重新投递并重启，容器内 `/run/secrets` 派生公钥与包内公钥逐位一致；`action_risk_authorizations`/`action_risk_results` 均为 0 条，换密钥零影响；投递方法与核对方法写进 `deploy/cloud/README.md` §5。**未做、也是这条的价值所在**：防复发门禁——部署器应当在启动前比对「服务端私钥派生的公钥」与「本次要服务的包所声明的公钥」，不一致即拒绝部署，而不是靠人记得投递 |

### 原有待办

| ID | 任务 | 并行 | 为什么冻结 |
|---|---|---|---|
| T4 | 补 VE 剪辑装配任务 | ❌ 需设计 | 大工程，Demo 不演。你此前明确困惑过「剪辑不需要上传素材吗」。07-27 复核：无相关提交 |
| T19 | 动效零件接 AI 一句话制作链路（方案 B） | ❌ 需设计 | 大工程。07-27 复核：无相关提交 |
| T37 | 四条合规决策 | 👤 | 你已定：Demo 后处理 |

### T101 与 T90b 自报的新窗口

> 这三条**不是派单要求修的，是那两条线自己在证据文件里如实登记的缺口**。全部冻结到 Demo 之后，此处只做登记。
> 编号从 T105 起（T104 已被内置 Chromium 的 JIT 门禁占用）。

| ID | 任务 | 并行 | 一句话 |
|---|---|---|---|

---

## 三、已收口（无需再管）

> **本节 07-27 由 T103 全量对账扩充。** 每条新移入的都在代码或测试里独立核过，
> 「证据」写的是核出来的东西（文件行号 / 测试名与输出 / 提交号及它实际改了什么），**不是抄 `docs/development/*.md` 的状态行**——那些也是索引不是证据。

### ❌ 查证不成立 —— 观察是真的，结论是错的

| ID | 原判断 | 实际 |
|---|---|---|
| T77 | B5-13 前端投影与权威态不一致 | **前提已证伪，无需修。** 云端线拿到逐字节响应：权威态是 `{"platform":"douyin","state":"unknown","observedAt":null}`（恰好 57 字节），**不是 `missing`**；`unknown` 从 Python 到 Rust 到 Zod 到 UI 完全自洽，界面显示「尚未确认」。B5-13 的症状只在一个被双侧守死、当前不可达的组合下发生。**T103 复核**：`006078c` 未改 `frontend/src` 任何一行——这是有效的否定性交付，不是没做 |
| T58a | 点击后一分钟命令没开始 | 那 15 分钟是 `browser.waitUntil` 自己的 900 秒预算——它把条件抛出的异常当「还没满足」，跑满预算才用**最后一次**的异常 reject（用装的那份 `Timer` 实测过）。命令早就跑完并失败了，所以取样才看到主线程空闲、无子进程、工作区已删。**真缺陷是另一个**：提交命令声明为同步，全程占用主线程，已 TDD 修掉（`b6cc046`） |
| T54 | 主窗口不向辅助功能暴露 | 是 **macOS 锁屏行为**。判据被推翻：Chrome / VS Code / ghostty 同一时刻 AX 全是 0。连带更正：「窗口可见 3.5 分钟」不成立，那段时间屏幕锁着 |
| T46 | 上游品牌名在产品窗口顶部 | **产品路径不复现**（那是绕开产品窗口直连 Streamlit 才能看到的）。仍补了静态门禁覆盖内嵌 WebUI |
| T31 | H8-22 打包 App 闪退 | **不复现**。四条证据：结构 / 装配 / 运行 / 崩溃报告归因 |
| T94 | 视频剪辑「时间轴编辑」「预览」两页签也塌陷 | **没塌**（`0aa9c9e`）。1280×800 实测外层卡片 992/992、内层容器 942/942，**每个像素都用满**。两条结构性原因：这两页以 `Card`（块级，自己就填满列）开场而不是会收缩的 `Space`；内层 `Space` 是 `.ant-card-body` 直接子元素，正好被 T88 自己那条 `.video-editing-panel` 规则覆盖——**T88 其实已经修好了它们，却写了一条说自己没修的注释**。本次未加任何 CSS。顺带修掉真缺陷：Harness 建不出剪辑项目的根因是**一个漏传的 prop**（`test-harness/main.tsx` 从没传 `videoEditingGateway`，回落到永远抛错的桩），生产一直传的那个实现只需要浏览器——Harness 长期比产品能力更弱且没有理由。用「删掉再跑」证明而非读代码 |
| T83 | 编排 136～178 秒产出只有 9 个字段，慢得不合理 | **我的前提错了。** 第一次应答是**四个键**，第四个 `composition_html` 是一整份 standalone 动效 HTML（含 CSS 与完整 GSAP 时间轴），实测输出约 3200 token、**HTML 占 70%**；本机模板只提供 `runtime/gsap.min.js` 一个文件。把计时器套在代理自带的 `model_call` 注入点上实测：往返 **1 次**、修复轮 **0 次**（8/8）、**`localSeconds = 0.01`**（lint+check+snapshot+落盘共 10 毫秒）、首字节 40 毫秒。**最干净的一刀**：73 token 提示词、零产品代码，模型自己跑 **178 秒**，比产品的真实编排还慢。下行速率是常数 4497–4652 B/s（波动 3%），耗时严格正比于模型这次写了多少字节。**慢在模型写得多，不在我们**。可动的杠杆见 T92（冻结） |

### ✅ 生产装配与出厂门禁

| ID | 关键结论 |
|---|---|
| T63 | **升级路径「一升就砖」**（`e47a73d`）。**三处点名位置里两处早已不成立**——`git blame` 显示 `ee5ecbf fix(t63)` 07-26 19:11 已在 main 上，但它的文档改动只有交接单一行，**台账没回写、证据文件没留**，于是这条在冻结区挂到 07-27。今天第二次同一形状。关于「逐字节相同」那道校验：它挡不住任何刻意改动——文件在 App 私有目录，能写它的人同样能写成规范形式（`serde_json` 输出确定、字段序由结构体定、无空白），**是减速带不是边界**，代价却是序列化库升级或字段增删任一都让存量文件不匹配。真正在守的是 `validate_schema` 的全部不变量、`observe_release` 每次从 feed 重新推导身份、安装前重算 sha256 + minisign 验签。本轮修掉五处同族 abort（诊断设置版本未知/损坏、紧急停止记录读不懂、策略 schema 未知即回滚、文档损坏），全部改为退回安全默认 + 重写 + **记日志**——补上原改法缺的那半，否则会把一份被改过的文件悄悄洗成规范形式，痕迹就没了 |
| T64 | **`AppUpdateCache` 两个非自愈的强退窗口**（`5e1352b`）。同样早已修过（`cfb2a0a` 07-26 19:14）且同样没回写台账。**两条都写了测试去证伪**，其中把台账描述的中间态（package 已换、manifest 未存）逐字造出来那条 **RED 第一次跑就是绿的**——这是否定结论的证据不是推断。本轮收掉同函数内两处：缓存文件权限过宽（Time Machine 还原的典型结果）→ 删除；缓存路径上是软链 → **unlink 而不是 abort**——unlink 只摘链接不碰目标，abort 反而把链接和一个打不开的 App 一起留给用户。`cargo test` 45 个二进制 428 条通过。**真实验收待补**：feed URL 是 `.invalid`，「真的升级一次并中途强退」验不了，没用单测冒充 |
| T89 | **原生层把「更新未启用」洗成 failed**（`b6dce90`）。新增 `UpdateState::Disabled` 把它搬出 `failed`，而不是在 `failed` 内部挖中性豁免——**在 `failed` 内部开洞正是上一次事故的成因**：T85 那个 `configuration_invalid → 中性` 顺手把**真实的**配置失败（`UpdateRuntimeConfiguration::load()` 拒绝 endpoint）也一起降级了，本次已恢复红色。协调器锁中毒复用已有的 `Storage/StorageUnavailable`——写路径 `set_state()` 遇到同一种故障本来就报它，读写两侧对同一次故障说同一句话，不发明第七个码。诚实标注：协调器那把锁**目前没有可达的中毒路径**（临界区只有一次 clone+赋值），是潜在缺陷不是现行缺陷，但一旦发生就说谎 |
| T44 | **正式包接上 Developer ID 签名与公证**。296 个代码节点签名、289 个 Mach-O 全本团队签名 0 例外、entitlements 只加 `allow-jit`（有对照实验）。判据：带 quarantine 判定 `accepted` |
| T1 | 正式包补装三份视频运行时资源。病根：验收验的是「功能能不能跑通」，不是「用户拿到的那个包能不能跑通」 |
| T82 | **以管理员身份运行时内置浏览器 Profile 子系统整块失效**（`a440336`）。`apply_private_acl` 只设 DACL（owner 传 `null_mut()`），而 `verify_private_acl_parts` 要求 `owner == TokenUser`；提权会话里 `TokenOwner` 是 `BUILTIN\Administrators` → `EqualSid` 失败 → `UnsafeDirectory`。双向证明：提权 20/20 失败、非提权 12/12 + 8/8 通过。**修创建端不修校验端**——owner 天然持有 `WRITE_DAC`，接受 Administrators 等于让机器上每个管理员都能改写它保护的东西。见 `docs/development/T82.md` |
| T111 | **三个视频运行时产物的构建缓存键都缺输入**（`1be10b8`）。**比立项时判断的更严重——不是只有被点名那一条**：`material-video-worker` 缺 4 项（含 `workers/material_montage/` 整个源码树，正是 T32 修了却没进用户那个包的直接原因；还有 spec 直接 import 的 `subtitle_font_assets.py`，它决定字体字节）、`motion-video-worker` 缺 2 项（含钉死 gsap 摘要的 `offline-motion-dependencies.v1.json`——**重新 pin 一个 gsap 版本旧缓存照样被复用**）、`media-toolchain` 缺 1 项（构建最后一步写 `manifest.json` 的脚本，而发版正是拿这个文件去校验包）。**门禁不止做内容摘要**：内容摘要只能抓「已在键里的文件被改」，抓不到「驱动长出一个新输入而没人加进键」——事故当初正是后者，那个新文件从来没被摘要过。新门禁读每个构建驱动自身，把它文本里提到的每个真实仓库路径拿出来，要求要么被该产物的缓存键覆盖、要么写进豁免表**并附「它为什么改不动产物」的理由**。抓不到的三类（拼出来的路径、间接 import、vendor 内部变化）如实登记，没假装覆盖。TDD 第一轮发现三条用例是**空转**——旧实现拒绝一切目录，`assertRaises` 会因为「拒绝了」而通过而理由完全不对，改成 `assertRaisesRegex` 才转红。**主线复核**：22+9 条自跑通过；独立变异往构建驱动加一条未声明输入，门禁 rc=1 并精确点名该路径。⚠️ **发版影响**：修好之后三条产物的键全变，每台机器要一次性重跑三个构建，其中 ffmpeg 从源码编译，出包耗时明显变长；且三者共用每机一份的缓存目录、`ensure_cached` 会先 `rmtree` 再建，**出包期间不得并发跑这些构建** |
| T13 | 建立真正的生产构建路径与必需资源门禁，单一声明源 `contracts/quality/release-package-resources.v1.json` |
| T21 | 唯一产包路径不在任何自动门禁里 → 独立发版命令 `scripts/build_release_package.py` |
| T33 | 正式包需在干净工作树重建 |
| T35 | 包审计读共享 dist 而非构建时嵌入的产物 |
| T42 | 包审计 Python 夹具落后于 mjs 新增能力。根治：从 mjs 导出的 `requiredDistributionMarkers` 读，不再抄第二份 |
| T39 | 消除发版与开发环境的构建期分叉。登录界面此前只存在于 `customer-demo` 这个 Vite mode，正式包里整个被 tree-shake |
| T55 | 账号命令没进 desktop-e2e handler。修接线错误但不拆掉设备凭据那条**有意的**安全边界 |
| T59 | 前端入口里的第八处构建期分叉：`startup.ts:44` 的 `desktopShellStartupCheck` 无条件返回 ready，`single_build_path.rs` 只读 Rust 源码看不见它。**5 个入口全过是因为它们的前端根本不跑门禁** |
| T69 | **App 全程零日志，演示机上一旦出问题我们是瞎的**（`090e756`）。原 Tauri App 无 tracing / log / env_logger 依赖、零条日志调用。现新增 `frontend/src-tauri/src/app_logging.rs`（28 KB）：**固定事件白名单** + AppData 私有落盘，单文件 1 MiB、最多 8 个、保留 7 天，异步有界队列满时**丢日志而不阻塞业务**；只记固定事件和封闭字段，不写原始错误详情。RED 是真红——`sensitive error detail reached the desktop log: Cookie=session-cookie`，证明的是「直接记录错误对象会泄漏 Cookie」而不只是「没有日志文件」。**T103 复核接线**：`lib.rs:2` 声明模块，`:4259` 初始化，`:4262/4266/4270/4322` 与 `:1313/1326/1350` 已接进 setup、Control Plane、Executor/Sidecar 生命周期。软链替换日志目录或文件被拒且不触碰外部目标。**遗留**：尚无一次正式 App 故障后从真实 AppData 导出安全日志的取证记录 |
| T61 | **坏视频产物让 App 永久起不来**。`video_job_workspace.rs:349` 的 `initialize()` 现在依次调 `recover_interrupted_imports()`、`cleanup_invalid_artifacts()`、`cleanup_expired()`、`discard_staged_publish_artifacts()`；只有清理本身失败才阻断启动，运行期 `list_artifacts()` 仍严格 fail closed。**T103 实跑 `cargo test --test video_job_workspace` → 14 passed / 0 failed**，其中 `startup_discards_corrupt_artifacts_while_runtime_listing_stays_strict` 正是这条。调研见 `docs/development/T61-setup-abort-risk.md`（它推翻了前一条线「演示场景会 abort」的高风险判断）。**遗留**：尚无正式安装 App 的损坏产物重启验收，属 T10 写面 |
| T65 | **`cleanup_expired` 生产代码里没有调用方**，30 天保留策略形同虚设。现由 `video_job_workspace.rs:380` 在启动自愈后按当前 UTC 执行。**T103 实跑**：`startup_removes_expired_retained_workspaces_without_manual_cleanup` 通过（同上 14/14）。与 T61/T66b 同一条启动路径、同一批修 |
| T66a | **文件权限只检查不修复**（`secure_store.rs` 那半）。Time Machine / 迁移助理恢复的账号会造出带 group/other 位的密钥文件 → 永久闪退。现 `:195` `libc::fchmod(fd, 0o600)`，`:199-204` 修复后复核 dev/ino/mode 拒绝替换竞态；`ensure_private_file_permissions`(:226) 委托给它。提交 `e208b64`(07-26 18:46)、`0f3c060`(19:12) 消除竞态。**修好后在冻结区躺了约 6 小时 25 分**，与 T62 并列本批积压最久 |
| T66b | **目录权限只检查不修复**（`video_job_workspace.rs` 那半）。现 `:1355` `libc::fchmod(dirfd, 0o700)` 并清 setgid/sticky，`:1358-1361` 复核 dev/ino/mode。**T103 实跑**：`startup_repairs_migrated_private_directory_permissions` 通过（同上 14/14）。**遗留**：Windows ACL 漂移不由这条 Unix 实现覆盖 |
| T67 | **Windows 企业域 AppData 重定向会启动即闪退**。`browser_profiles_windows.rs:1049` 现在 `stripped.strip_prefix("unc\\")`，把 `\\?\UNC\server\share` 还原成 `\\server\share`；测试 `:1324 a_unc_path_and_its_final_path_form_share_one_key` 钉住。**已上机验证**：`docs/development/T67.md` 记录 Windows 提权实机 RED（左右值逐字贴出）→ GREEN `lib 9/9`。**台账原写「读代码推断，未上机验证」，这句是错的，本次更正**；同一行的「并行」列早已写着「✅ 已合并」却仍留在未收口表里，是台账内部自相矛盾 |
| T22 | **自动更新在可发布包里从未配置**。`dabd226`（07-26 19:16，9 文件 +273）新增 `build.rs:89 require_release_update_configuration()`：release 构建要么显式 `AUTOMATION_TOOL_UPDATE_DISABLED=1` 且不给 endpoint/公钥，要么必须给通过 https / 三个占位符 / 保留主机名 / 无凭据片段校验的 endpoint + 公钥，否则 `panic!`。**「未配置」从事故变成被门禁约束、必须显式声明的一等公民配置**——这正是 T85 能把它渲染成中性状态的前提 |
| T24 | **执行器包根按 `debug_assertions` 分叉**，违反单一构建路径。`d28f819` 统一到 `resource_dir()/local-executor/package`，桌面验收也把测试用签名执行器装到同一布局。**T103 复核**：`grep -n debug_assertions frontend/src-tauri/src/lib.rs` **零命中** |
| T26 | ~~内置浏览器换成 Chromium 开源构建~~ → **剔除 Widevine CDM**。**任务标题的结论是反的，调研结论是不换**：Playwright 已停产 mac/win 的 Chromium 构建（实测 404）；开源构建不含 H.264/AAC，实测抖音播放器彻底黑屏；而 CfT 在 `sec-ch-ua` 里报的本就是 `"Chromium"`。**唯一白纸黑字的分发禁令只针对 `libwidevinecdm.dylib`**（20.2 MiB）。`d5e5111` 落地：`build_embedded_chromium_staging.py:379,398` 产出 `exclusions` 写进 staging manifest，`build_embedded_browser_distribution.py:185-198` 记 `widevine_excluded_remaining_cft_terms_unresolved`，`check_embedded_browser_package.py:102` 预算按剔除后校准。**遗留**：CfT 整体许可仍是明确的待法律确认边界，不在本任务范围 |
| T40 | **包内 UTM Kabel KT 字体权利未判定**。`1660d21` 没有猜许可，而是登记为 `NOASSERTION` / `undetermined` / `deny`：`asset-rights-policy.v1.json:123` `font-utm-kabel-kt` 带 `rightsBlocker`（字体只写 "Free for everyone"，无许可证名与正文、无商用/再分发/嵌入授权）；`material-video-worker-package.v1.json:42` 把 `fonts/UTM Kabel KT.ttf` 列入 `excludedUpstreamResourceFiles` **物理排除**，五个字面全部换成 OFL 字体。**该字体权利本身仍是「无法确认」，不是已获授权** |
| T41 | **动效叠加字体未进权利登记表**。`eb861c2` 把实际随资源使用的 `big-shoulders-display-latin.woff2` 绑定到精确 SHA-256：`asset-rights-policy.v1.json:161-167` `font-big-shoulders-display`，`license: OFL-1.1`，`sourceUrl` 指向 BM-12 锁定的 commit。条件分发（保留版权与许可证、不得单独售卖、衍生继续遵守 OFL）一并登记 |
| T45 | **Control Plane 镜像被打进 playwright**（约 50MB）。`98ac834`/`2a3b293`/`92d0367` 把它移进默认启用的 `executor` 依赖组：`backend/pyproject.toml:49-50`、`:54 default-groups = ["dev","executor"]`；`backend/Dockerfile:29 uv sync --locked --no-dev --no-group executor --no-editable`。本机开发与执行器仍直接可用，镜像不再带它。**遗留**：没有留下真实镜像的逐层内容清单 |
| T84 | **DMG 里没有 `/Applications`，客户当场装不了**。`179a720`（07-26 22:42）：`build_release_package.py:379` `(staging / "Applications").symlink_to("/Applications")`，`:381-394` 改用 `hdiutil create -srcfolder <staging>` 对暂存目录成像而不是对裸 `.app`。**修不在 `tauri.conf.json`**——构建跑的是 `tauri build --bundles app`，Tauri 的 DMG bundler 根本不执行，`bundle.macOS.dmg` 从不被读；`docs/development/T84.md` 记录了这个否定结论。**注意该证据文件写于 22:39，比修复早三分钟，读它会以为没修**。磁盘上 20:52 那个 DMG 缺链接是因为它早于修复，**T10 必须重新出包** |
| T85 | **更新中心不用点就常显红字**（`ff9f660`）。根因不是 UI 写错：「发布构建显式关闭更新」是 `build.rs` 支持的一等公民配置（客户 Demo 包正是这样构建），却被原生层报成 `failed / configuration_invalid`，而组件挂载即轮询，**不需要用户点任何按钮**就渲染红色。改法是表现层新增单一分类函数 `failurePresentation`，`statusText` 与 `stateColor` 共同引用：`configuration_invalid` → 中性「此版本未启用自动更新」，`transport_unavailable` → 提示色。**派单时写死的边界被遵守**：验签失败、安装失败、存储不可用、清单被拒仍是红，**default 分支兜底为红，新增失败码不会被静默降级**。未改状态机、契约与任何 `.rs` |
| T129 | **执行器候选里的 framework 当场被拒，不再是二十分钟后死在 codesign**（已修）。第二台 Mac 出包死在 Developer ID 签名：`bundle format is ambiguous (could be app or framework)`——那台 `backend/.venv` 建在 Homebrew 的 framework 布局 `python@3.12` 上，PyInstaller 照着打出 `Python.framework`，而 `copytree(symlinks=False)`（**有意**，候选审计禁止 symlink）把它的符号链接实体化成了真实目录。**判据不靠猜 codesign 的内部逻辑**：拿真实 Mach-O 实测五种形状，只有两种签得过——`Versions/Current` 是符号链接的真 framework（需要审计明令禁止的 symlink），以及恰好被当成老式 bundle 的那种（等于把 framework 签成了别的东西）；含事故形状在内的其余三种直接失败。**两个要求不可兼得，因此这份载荷里的 framework 不可能既可签又可入**，审计现在点名拒绝并直接给出修法（换 uv 托管的 standalone CPython）。**同时删掉 `_prune_redundant_framework_binaries()`**——它为「把 framework 修到能签」而存在，前提既已被推翻，留着就是死代码。真实验证双向：刚出包那份 293 文件产物过审计不误伤，同一份塞进真实 Homebrew framework 后被拒。新机器搭建见 `docs/macos-release-machine-setup.md` |

### ✅ 云端与交付

| ID | 关键结论 |
|---|---|
| T18 | **控制服务云端部署**。`https://at.xuanbai.tech` 真实可用。中途逮到两个真缺陷：重复部署必崩（首次全绿、第二次才炸）、AppleDouble 污染 Alembic。重新部署 31 秒 |
| T17 | 云端 Demo bootstrap 可注册无账号 Installation |
| T14 | 正式构建缺设备注册路径 |
| T68 | **产品账号登录已在正式 App 里走通**。根因是边界 nginx 用 `$request_id` 覆盖了 App 送的 `x-request-id`，App 比对回显不一致 → `ProtocolInvalid`，**登录第一个请求就失败**，所以 challenge 与 logout 从未发出。修复已部署。**07-26 服务端实测收口**：`installations` 从文档记录的 0 行变为 5 行、全部 `active` 且 `owner_user_id` 非空。见 `docs/development/T68.md` |
| T48 | **设备绑定已在真实链路成立**。本机 `profiles/demo-xuanbai/device-credential-v1` 创建于 20:19，与 `installations` 最早那行 `12:19:17+00` 逐秒吻合。**5 行不是「密钥每次重生成」**：多出来的来自各自独立 bundle identifier 的验收构建（例如 `…t36acceptance` 的设备身份 mtime 22:05，对应 `14:05:24+00` 那行），demo Profile 那把自 17:25 起未变。包里地址、Gatekeeper 放行、隔离启动此前均已验 |
| T5 | **三份密钥已在签名包的 demo Profile 里就位**并被真实使用：`model-service-video-creative-v1`、`model-service-script-v1`、`video-editing-service-aliyun-v1`，均写于 20:22；你后来那次成功出片就是用它们跑的，比单独验收更强。演示当天只需确认百炼额度仍可用 |
| T109 | **抖音「重新检查」两个按钮一点必报错**（`eb1a244`）。**派单假设被推翻**：不是 sidecar 没握手。用正式包里那个执行器二进制直接驱动生产链路（按 `executor_bootstrap.rs` 格式手算 HMAC 下发 `douyin.login.recheck`）——空档案 4.4 秒回 `awaiting_scan`，用户真实已登录档案 1.8 秒回 `healthy`，执行器全程未退出；签名没有改写 Chromium 字节（337 文件摘要逐一复算全对）、档案锁 0 字节即已释放。磁盘 mtime 还原那次操作：执行器起过、租约取过又放了，但 Chromium 的 `Local State`/`Last Version`/`Default/` 全部仍停在 21:35 ⇒ **浏览器根本没启动**；再往里是哪一支磁盘上什么都没有，**因为产品没写下来——这就是缺陷本身**。三层各丢一部分：`catch{}` 丢掉整个错误、`safeInvoke` 把 Rust 已放上线的 `{code,message,retryable}` 压成 `transport_unavailable`、`map_profile_error` 把可恢复的「档案待恢复」压成通用 `storage_unavailable`——一句文案原本覆盖 **15 种**原因，其中至少两种重试一万次也没用。按 T85 的 `failurePresentation` 做法分三类。**同路径逮到真缺陷**：App 持租约被强杀会在磁盘留 `active` 标记，此后两个按钮**永久失败**，唯一出路「安全注销」原本被翻译成「请稍后重试」——很可能正是用户撞上的那一条。**这条自报的「覆盖空洞」随后被 T114 推翻**：`e2e-tauri/platform-session-reuse.spec.ts` 的 `restart` 相位干的正是「显示登录正常 → 点重新检查」，由 `run_b5_15_acceptance.py` 驱动四次真实 App 生命周期，引入于 `c7ecd1a`（**2026-07-20，早于本任务七天**）；登录事实只在外部平台边界伪造，App、Rust 桥、内置 Chromium、档案存储全是真的。**主线照抄了这个错误结论没去核实，是同一类错误**。那个 spec 里还留着一句注释写明页面会把注销失败吞成一句通用文案——**有人在 E2E 里绕过了这个吞错误问题却没回来修页面**。**待补**：真实用户路径验收需正式包 + 真实已登录账号，主线在新包上复验；若仍失败，界面现在会直接报出故障代码，一步定位 |
| T6 | **抖音扫码登录与重启恢复整条走通**。扫码实测通过、服务端 `state = healthy`；落盘那层 T103 已证（App 未运行时读磁盘，`profiles/demo-xuanbai/embedded-browser-profiles/douyin/739b9297-…/Default/Cookies` 45 KB，64 条 cookie）。**最后一件由用户在正式签名包上走完**：完全退出 App 再打开，平台状态仍是「登录正常」——真实用户路径，代替不了。**换新包不用重扫**，档案按 bundle identifier `com.aventador.automationtool` 定位，构建之间不变。**这次验收顺带挖出 T109**：被动展示对了，但「打开登录处理」「我已处理，重新检查」两个按钮一点必报错——那是独立缺陷，不回退本条。顶层 `current-douyin-profile-v1` 仍指向污染档案 `df1c89f0`，不在 demo 链路上，未清理——产品自管目录不手工动 |
| T38 | **演示后回收清单**（`801772e`）。新增 `docs/customer-demo-post-demo-cleanup.md`，把业务冻结、账号与 Session 停用、设备凭据吊销、本机数据、PostgreSQL、对象存储、云资源、证据保留和双人复核写成可执行清单，并从演示运维手册链接过去；明确区分「删除」「吊销」「停止计费」和「保留审计证据」，只允许登记凭据 ID / 用途 / 指纹，**禁止读取或抄录密钥值**。纯文档任务，证据文件如实写明「无代码 RED」而不是事后编一次从未运行的失败测试。**遗留**：尚未在一次真实客户演示退场中逐项执行 |

### ✅ 视频与内容

| ID | 关键结论 |
|---|---|
| T106 | **五个内部编程错误不再被说成「你的描述太抽象」**（`7e8ad98`）。**派单前提被纠正：是五个不是四个**——`agent_tools_require_an_authoringworkspace` 同型，`entry.py:459` 是 `MotionAuthoringTools` 唯一生产调用点且总传上一行刚校验过的 workspace，主线独立复核成立。新增 `executor_defect` 类报 `authoring_crashed`。**没有并进已有的 `app_request_invalid`**，尽管今天两者错误码相同：类名就是子进程写在线上的 `status`，把接线错误标成「请求无效」等于把假话写进 App 唯一据以推理的那个字段，而且合并之后不可逆。测试用真实构造器传真实的错参数而不是打桩，所以契约里抄错一个 token 会让模块**直接拒绝加载**（主线变异验证过，比断言失败更严）。Rust 侧同时驱动 `rejected` 与 `executor_defect`——只测新状态会是假绿，因为 Rust 的 `_ =>` 兜底本来就覆盖它 |
| T107 | **模型静默等待时长下沉到契约**（`00e5e93`）。前提复核成立：`agent.py:277` 是唯一定义。新增 `contracts/video/motion-authoring-model-call.v1.json`，Python 在 import 时读、TypeScript 直接 import 同一份 JSON——**用的是强形式**（每语言不留字面量），而不是另一种只靠正则守着两份副本的做法。界面文案因此从「超过允许的最长等待时间」变成「连续 6 分没有再返回内容」，并做了变异检验：把契约改成 174，执行器与界面同时变成「2 分 54 秒」。**同一任务里逮到一个打包缺口**：`agent.py` 在 import 时读它，所以缺这个文件的执行器不是超时才失败而是**起不来**，而且只对用户发生（源码检出永远有这个文件）。已加进 PyInstaller spec 与桌面 E2E 打包输入清单，各带测试 |
| T108 | **智能素材成片窗口整页点不动，并按产品配色重做外观**（`cadb047`/`783a984`，用户截图报的）。**CSP 假设被双重证伪**：Tauri 2.11.5 只对 `tauri://` 协议返回的资源附加 CSP（`protocol/tauri.rs:182`），本窗口是外部 URL，官方注释也写明这条链路对外部 URL 不生效；运行层实测 `cspViolations: []`、websocket 只有 `OPEN`。**没动 `tauri.conf.json` 一个字符**。真因是上游引导 `driver.js`：`webui/Main.py:1209` 每个会话首帧调 `tour.start()`，它走 `components.v2` 直接跑在主文档（不是 iframe），给 body 加 `.driver-active`，其样式 `*{pointer-events:none}` 关掉整页，只放行高亮元素和气泡——**而我们自己的注入脚本把气泡与遮罩 `display:none` 了，用户没有任何办法结束引导，类名永不摘除**。WebUI 每次换随机端口即新 origin，`localStorage` 每次为空，所以每次打开必然复现。旧脚本里那句 `.driver-active-element{pointer-events:auto}` 说明此前有人撞上过，但只放行了那一个元素。主题改在窗口一侧定外观（`.theme(Theme::Light)`）：根因是内嵌 WebUI 启动时按 `prefers-color-scheme` 定配色并固定，CSS 层的 `color-scheme` 改不动媒体查询结果。**真实窗口验收**：本机系统是深色而窗口内 `prefersLight` 为真（决定性对照，不是恰好蒙对），`driverActive: false`、`pointer-events: auto`，并在窗口内点击输入框键入文字读回成功。**「好不好看」没写成断言**，没编造永不会红的用例。连带查出 T111 |
| T114 | **租约被弃置这一格：产品必然会撞到、以前没人管**（`409478d`）。**立项前提被推翻了一半**：T109 自报的「healthy 后重启再点没覆盖」是错的，`platform-session-reuse.spec.ts` 的 `restart` 相位七天前就在做这件事。**真正没人管的是另一格，而且不需要登录态**：`executor_platform.rs:621` 只在 `state == "healthy"` 时归还档案租约——也就是说**凡是停在 `awaiting_scan` 的登录，App 都故意继续持有租约**，而那正是产品主动把用户支开去扫码的时刻。此刻非正常退出，磁盘留下 `{"state":"active"}`，之后两个按钮**永久失败**，唯一出路「安全注销」还会**删掉整个档案要求重扫**。`browser_profile_locks.rs` 只钉住了「存储层会留下这个标记」，**从没有测试证明正式 App 会走到这一步、界面会说什么、以及那条唯一出路真的走得通**——最后这条是本次新增的产品事实。**RED 用变异取得**：用例第一次跑就通过（说明 T109 的修复在真实 App 上确实生效），从没红过的用例证明不了自己在测什么，遂把 T109 那处映射改回修复前，整套转红且失败信息直接带着页面渲染的 `故障代码：storage_unavailable`；变异已还原，交付物与 GREEN 同一份代码，**未改任何产品代码**。标记不是造的——由正式 App 被 SIGKILL 时自己写下，runner 只做核对。**没有复现用户那一次**：T109 取证显示用户档案锁当时是 0 字节（已释放），所以用户撞到的是同一条路上的另一支，等复发时由 T109 加的故障代码直接报出来。**仅 macOS arm64，Windows 未验**。**留下的产品问题**：弃置租约时 flock 其实早被内核释放，标记只是陈旧旗子，而「显式恢复 = 丢登录态重扫码」这个代价是否合理，值得单独立项 |
| T110 | **取消不再抢先写终态，标记文件名收成单一来源**（`ad38d67`/`c9c0e3f`）。**立项时我判「窗口只有一帧、危害有限」，被推翻**：真问题是**会丢用户已经做好的片子**。`advance()` 在快照已是终态时**静默 `return Ok(current)`**，而 `cancel()` 抢先写了终态 `Cancelled`；若 FFmpeg 恰在此刻成功退出，产物仍会被导入，随后那句 `advance(Succeeded, 100, 产物)` 撞上提前返回、**不报错地什么都不写**——MP4 在磁盘上、快照 `artifactId` 为 `null`、成片页按 `artifactId !== null` 过滤，于是这支片子**列不出、播不了、删不掉，还继续占配额**。改法：`cancel()` 写非终态 `Cancelling`，终态由执行器线程写。**同一仓库里 `material_video_studio::cancel()` 早就是这个写法**，两个 studio 此前互相矛盾；`publish_workspace.rs` 也有同原则的用例。界面即时显示「正在取消」而不是等 2 秒轮询，也没有靠 React 内存记（`VideoStudio.tsx:1286` 的注释写明切页会卸载）。②标记文件名不再两端各写一份：契约 → Rust 读 → 随渲染请求作为 `cancelMarker` 下发，**Worker 自己不再有意见**，两端都失败关闭；没有用「两端各读同一份 JSON」是因为 Worker 包只装 `runtime/node` + `app/worker.mjs`，把契约拖进打包正是另一处门禁在防的事。**主线复核**：`advance` 的提前返回逐行确认属实；`cargo test --test motion_video_studio` 18 条通过，含那条丢片子的复现用例 |
| T105 | **取消之后侧边栏不再替一条已经不存在的运行说话**（`39b8d07`）。**「单槽 message 要不要改结构」这个前提被推翻**：能表达「哪条任务、什么终态」的结构 T101 就加好了（`ownJobs[].outcome`），缺的是侧边栏一直没去读它。`motionRunAttention` 的 in-flight 判据 `pending !== null` 或 `message !== null` 是 T91 那个布尔函数的原样继承，写它时 store 里确实没有别的东西记得住结果，T101 之后就过期了。改成 `motionRunNeedsWatch(current)`，**一行，没有新增 dismiss、没有新增结构，而且比原来更严**（原来「有条提示在」就算在跑）。**同一行修掉方向相反的第二张脸**：关掉页面上那条可关闭的 info 提示，会让一条还在跑、还在轮询的渲染从侧边栏整个消失——比取消那半更坏。`failed` 分支继续读 message 不是没收拾干净：编排失败时任务压根还不存在（T91 查证过快照写在编排返回之后），任何以任务为键的结构都装不下它。**主线复核**：独立复跑 41 条通过；变异验证把该行改回旧写法，**正好 3 条新用例红、38 条老用例绿**。剩余窗口另立 T110 |
| T71 | **背景音乐：决策已定，本期不做**。用户 07-27 拍板「暂时先不做，后面再说，先去掉就行」。所以这条不是待办而是已定的取舍：不采买、不自制 BGM，**执行落在 T32**（把做不到的三个选项从界面移除，判据是可访问性树条目 2 → 0）。合规上本可做（已有 4 个自制、权利全 `true` 的 `music_sfx`），但那 4 个是 1–2 秒音效不是 BGM，凑不出成片配乐 |
| T99 | **antd 折叠控件把英文状态词吞进可访问名**。用 `ariaSnapshot()` 实测 **20 棵无障碍树**（八个顶层页面 + 法务页折叠/展开 + 运行中任务的工作台 + 运行详情 + 剪辑页签 + 发布流程 + 三种启动门禁），从不靠读 JSX 判断。找到 5 个控件在 2 个文件里——**其中 `Workbench.tsx` 不在派单里，是它自己扫出来的**。根因钉到 `antd/es/collapse/Collapse.js:69-72`。**最值得记的一条：这个缺陷早就被发现、被写下、被绕过了**——`e2e/workbench-home.spec.ts:83` 与 `Workbench.test.tsx:189` 都带着「header 的可访问名是 `<折叠状态> 诊断信息`」这句注释加一个宽松正则，**它被当成环境事实记下来，而不是当成缺陷修掉**。两个框架默认值都做了实证：同一份被污染的渲染上，Playwright 非 exact 命中 1，Testing Library 报找不到——这个不对称正是 e2e 一直绿而单测需要正则的原因。视觉一致性是量出来的：stash 掉修复测 antd 基线，transform、尺寸、类名修复前后逐项一致，只有 aria 属性不同，没碰任何 CSS。`exact: true` 没无差别铺开——e2e 有 121 个带名字的 `getByRole`，只收紧了那 2 个纯粹为绕过本缺陷而存在的站点 |
| T100 | **视频剪辑页在生产尺寸下只差 7px 放不下整页**。**「调一处 margin 就能消掉」这个前提被实测推翻**：这一页没有 margin 可调，每个能压的地方都有写下来的理由——`.ant-empty` 的间距上方明确记着视频制作曾在这里写过 `margin-block: 70px` 后来删掉（理由是「删掉而不是重新调，antd 自己的间距才是本产品其余部分用的」），再写一条就是把刚删的加回来。改法是删掉卡片头「新建剪辑项目」——屏幕上连着四行里这四个字出现 6 次，卡片头是唯一不提供新信息的那个，占 56px；**这不是发明的取舍**，`VideoStudio.tsx` 里同名卡片头因完全相同的理由被删过且有测试守着。1280×800 溢出 7px→0（余量 48px）。**如实登记未修的一项**：创建失败时错误 Alert 带 40+16=56px，比 48px 余量多 8px 仍溢出——它先按算术推断「不溢出」，去量发现是错的 |
| T91b | **渲染阶段失败切页后不再静默**（`7c5d5d6`）。没把 `VideoStudio` 那条 2 秒轮询整个搬上去——`refresh()` 同时喂着任务卡、进度条、成片列表和取消按钮，那是页面数据，搬它意味着外壳持有页面数据、组件改受控。新增独立监视器 `motion-run-watch.ts` 只回答一个问题：**这条本会话启动的片子是不是还欠一个结果**，`VideoStudio.tsx` 一行未改。定时器门控在纯函数 `motionRunNeedsWatch(state)`，本会话没提交过东西就一个定时器都不建；随被看着的任务全部到终态而自己停。**轮询自己失败时显示写着「未知」的角标**（悬停「读不到视频制作进度，去看看」），连续 3 次约 15 秒才翻牌——不复用「失败」（那会断言这里没人知道的事），不退回蓝点（那会把要修的谎装回去）。清理旗子那条用例是实现后补的，**故做了变异验证**：删掉 `if (stopped) return;` 后它报 `expected 'error' to be 'info'` |
| T88b | **屏效清单剩余项**（`ui-backlog`）。扫了 `src/` 全部 **135 个类名，23 个没有任何规则**，逐个实测后**只有 2 个真的错了**，其余 21 个是多余钩子（`Card`、`<section>`、块级 `<li>` 本来就填满父容器）。修的两条与 T88 第 1 条同根：写了 `className` 而 `global.css` 里从没写规则，**不会让构建/tsc/eslint 失败，只在真实窗口尺寸下看得见**。① 设置页「视频剪辑服务」凭据表单只占卡片 942px 中的 **358px（38%）**，而它正上方结构逐层相同的「模型服务」是满宽的；② 视频剪辑片段行「转场」是**全 App 唯一一个裸 `<select>`**（19px 高、Arial、方角），一行五个控件里有一个明显不是同一个产品的。用 antd CSS 变量写而没重抄圆角与字体栈——**抄一遍就等于以后改主题它悄悄不跟着变**。另证伪两条：`.overview-grid` 实测 1008px 挤在 992px 里看着像溢出，但八页 × 两档视窗 `scrollWidth` 恒等于 `clientWidth`；全站零文字裁切。合并后做了**有牙检查**（摘掉 CSS 七条全红、装回全绿） |
| T90 | **模型服务用不了却说成「你的描述做不出来」**（`e1e18a1`）。两层漏斗：`lib.rs:660` 子进程非零退出时只要 stdout 是合法拒绝文档就一律判 `authoring_refused`；`agent.py:1299` **建连失败与读超时都是 `OSError`，被同一个 `except`** 收成同一句。**真 socket 双路实测**：连接被拒 0.01 秒、读超时 3.01 秒，内部原因字符串逐字相同。顺带撞见更宽的漏斗口——`vendor` 缺文件这种纯打包缺陷，界面同样说「请换一句更具体的描述」。**未做到「连不上说连不上、超时说超时」**：`MotionVideoStudioError` 只有 `{code, retryable}`，补齐要动 Rust，接续任务 T90b |
| T91 | **失败在侧边栏冒充「正在进行中」**（`f98461e`）。三种猜测都不对：编排失败时任务**压根还不存在**（快照是编排成功后才写）。而失败注入报告点名的 `setSubmitMessage` 在 HEAD 上已不存在——`a109178` 已修，**那次注入跑的是 22:04 之前的构建**。真正剩的洞是 `motionRunNeedsAttention()` 只返回布尔，失败与进行中共用同一个无文字蓝点，唯一那句文字是悬停可见的 `title="视频制作正在进行中"`——**标记在，只是它在说着相反的话**。等待期补了实测参照（中位数约 2 分钟）。剩余静默窗口见 T91b |
| T93 | **工作台首页把调试字段当正文、最近任务是裸 UUID**（`ec17ae0`）。`Revision` 与「事件水位」判为**降级不删**，三条依据：`CLAUDE.md` 4.4 那条主张约束的是**行为**（恢复来源、状态转换、幂等），全文没要求把计数器渲染到界面；`TaskRunDetails.tsx:522-525` 本来就在展示这两个字段，佐证界面没消失只是挪到操作者去查的那页；全仓 `grep 事件水位` 只有那两处命中，**没有任何测试或验收依赖它出现在工作台上**。折进默认收起的诊断区，完整 Task ID 一并放进去。裸 UUID 那半独立核实后确认无法小改——解析 OpenAPI 契约得 `TaskResponse` 只有六个字段，`searchKeyword` 只在 `TaskCreateRequest` 里且落在独立定义表未 join，`taskSnapshotSchema` 是 `.strict()` 后端多返回也会被拒。改用 `createdAt` 且**精确到秒而非分**——同一分钟内建两个任务会产出两行一模一样的文字，而真实用户一分钟内提交两次完全可能。改后首页正文里已无任何 UUID |
| T95 | **视频制作第一步的「选择」按钮原本在折叠线以下**（`e7d0f9c`）。1280×800 实测：两个按钮底边在 y=1075/1098，折叠线 800，要往下滚 275px 才够得着，**首屏内可按下的动作数为 0**——客户看到的第一屏是一面没有任何动作的文字墙。把说明表整块从按钮之前搬到之后，按钮底边升到 589，首屏动作数 2。960×640 最小窗口余 29px，1440×900 与 1920×1080 均在首屏内，四个尺寸无横向滚动。整页高度不变（1240）——**重排顺序，没有删内容、没动文案配色**。留了三条待判断（「新建视频」四字一屏出现两次、说明表要不要折叠、空状态定高），见 `docs/development/T95.md` |
| T95b | **第一步收进一屏**（`f9503ae`，用户批准三条）。1280×800 实测整页 **1240px（要滚 504px）→ 736px 一屏无滚动**；「新建视频」从一屏两次减为一次；说明表默认收起（每张卡摘要仍常驻，折进去的是选择时才逐项对照的比较表）。1440×900 与 1920×1080 也是一屏；**960×640 最小窗口仍需滚 180px，如实记录未达成**。`min-height: 330px` 的查证结论是**不是防跳动**：`git log -L` 显示它自诞生起未改过，唯一出现在 `d331d90`「建立视频制作页面骨架」，而那版 `VideoStudio` **不接 props、无 gateway、无 useEffect、六个页签五个是静态占位**——那一刻不存在任何内容加载，不可能有加载跳动要防。按随手写的定高删除 |
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
| T50 | **注销成功但界面报失败**（`6197eba`，5 次复现 4 次）。病因与台账描述逐字对应：内层权威态轮询是 `for _ in 0..100` × 50 ms = **5 秒**，却包在 60 秒的外层预算里——**不是等不起，是自己先放弃了**。改为 `tokio::time::timeout(DOUYIN_LOGOUT_PROJECTION_TIMEOUT /* 60s */, async { loop { … } })`，内外层预算对齐，未改注销控制流也未放宽终态要求。**T103 实跑** `frontend/tests/platform-session-logout-acceptance.test.mjs` → 1 pass / 0 fail。**遗留**：修改后尚无真实抖音账号的注销最终状态验收，**演示脚本可以把「安全注销」放回去了，但要先补那次真实验收** |
| T86 | **动效成片静默产出静止画面**（`0862e62`）。**已经真实发生过一次**：随包示范（`vendor/hyperframes` 的 `minimal-composition.md:12`）用的是 CDN 版 gsap 而渲染沙箱硬离线，模型最省事的「移除远程引用」方式就是删掉 script 标签保留 gsap 调用——这份合成通过全部静态检查，渲染时 gsap 未定义、`seekableDuration` 为 0，Worker 跳过逐帧 seek 却照常截图，**把同一张首屏截满 frameCount 次并报 complete**。修法两侧：出口 `worker.mjs` 判 `render_static_frames`（**逐帧与首帧字节比对**而非首/中/末采样——走完整数个循环的动画三帧采样可能相同却全程在动）；入口 `agent.py` 的 `check_composition` 新增 `missing_animation_runtime`，只拒绝「调用 gsap + 无任何 script src + 无内联定义」这个精确的不可运行形态。vendor 只读不能改，因此在提示词里点名覆盖那个 CDN 示范。**出口那道更值钱——它不依赖我们猜到全部失败原因**。真实 worker 进程 + 真实内置 Chromium 149 端到端复现并修复 |
| T101 | **好消息也要送到 + 重启恢复查证**（`61f9d72`/`2f56129`）。①**已修**：成片做好而用户在别的页面时，侧边栏标记仍是「正在进行中」——新增第五种 attention（绿色角标「完成」，悬停「视频制作做好了，去看成片」）。这与 T91/T91b 是同一形状、符号相反。②**查证完成，本轮拒绝实现**，理由不是代价大而是**方案不闭环**：「这条片子用户看过没有」在原理上不可能来自渲染快照，只加 `startedAt` 会让历史上每条成片在每次开 App 时都弹一遍「已经做好了」，**比现在坏**。据此**更正 T91 的登记**：那条把「App 重启后不恢复」写成一个原生侧任务，那个描述是不完整的。剩余窗口另立 T105 |

### ✅ 验收基础设施与门禁

| ID | 关键结论 |
|---|---|
| T119 | **Windows 上出不了包：没有可用的 C 编译器** —— 已由 T123 恢复。**入账时我写成「要不要装工具链、等你定」，那个判断是错的**：线索在 T116 记录里就有（`.local\vf04-msys2` 目录、安装器、07-24 的 `ffmpeg.exe` 都还在），**它本来就是这么装的，是环境退化不是从没有过**，不需要用户拍板。证据与恢复步骤见下一行 T123 |
| T123 | **Windows 的 media toolchain 构建能力已恢复**（`5d5b5b0`）。**真跑了一次：`rc=0`、3 分 14 秒、从空缓存起、产物 48.8 MB**（`ffmpeg.exe` 19,269,632 字节、`ffprobe.exe` 19,109,888 字节）。三条独立佐证：产物在**没有 MSYS2 的普通 PowerShell** 里 `-version` rc=0（证明 `-static` 生效）、`check_video_media_toolchain.py --target windows-x86_64` rc=0（完整能力矩阵 + 两条真实 H.264 编码）、二次运行 0 秒命中缓存。**结论比「装回来了」更有价值：机器上什么都没坏、什么都不缺。** MSYS2 完整在位（1171 MB、114 个包），`gcc 16.1.0 Rev5` 与 07-24 旧产物 `BUILD-INFO.txt` 里的编译器逐字相同，pacman 日志把当初装法完整还原。**本次没装任何新东西、一个字节都没下载**——退化的只是「怎么调用它」从没被任何地方记下来：它装在 `.local\` 下，不写注册表、不进系统 PATH，除非有人显式指向，任何进程都看不见。**主线那条线索指对方向但落点偏了**：Git for Windows 的 bash **也**把 `MSYSTEM` 设成 `MINGW64`，能走过 `build_video_media_toolchain.sh:28` 的守卫再倒在 `No working C compiler found.`——**守卫检查的是「我在哪种 shell 里」，而它真正要保证的是「我有没有 C 编译器」**；改它会让每台机器（含 macOS）缓存全失效重编 ffmpeg，故**只记录不修**，与 T111 的教训一致。**红线守住**：新产物与旧产物大小逐字节相同而 SHA-256 不同（builder 的 `mktemp` 路径被编进 `configuration:` 串），这正是「真的新编了一次」的证据；旧产物只被读过 hash，没有以任何方式进入缓存。**踩到并记录了三个坑**，其中一个是静默成功同族：MSYS2 的 `usr\bin\cmd`（无扩展名 bash shim）会顶掉 PowerShell 的 `cmd.exe`，程序压根不启动而 `$LASTEXITCODE` **保持旧值**——拿到「rc=0 但一秒没跑」，是看 0.13 秒的时间戳才发现的。**T121 的修复立刻回本**：那次真构建死在 `curl: (35) TLS`，能看见这个原因正是因为诊断修复已在检出上 |
| T79 | **验收驱动归属门禁已建立**（`f12bceb`，07-27 01:48，codex）。1294 行：`acceptance_driver_ownership.v1.json` 归属声明 + 555 行检查器 + 360 行测试。**主线复核**：门禁自跑 rc=0，且 `test_acceptance_driver_ownership.py` 已在 `run_script_tests.py` 里跑着（11 项检查），而脚本聚合是全量回归第七层——**所以它真的进了执行链**，不是又一个没人跑的门禁（那正是 T72 修过的形状）。**台账此前一直挂 ⬜「没做」，是错的**：主线整晚用 `git rev-list main..origin/main` 判断「codex 合了没有」，而那个数字回答的是「远端有没有我还没拉的东西」；主线一直在 codex 的工作之上往前推，所以它**永远是 0，与 codex 做没做完全无关**。正确的查法是「main 上有没有匹配任务号的提交，而台账还挂着未收口」 |
| T58c | **拒绝原因不原样转发，决策已定**（`475bee5`，07-27 01:49，codex）。**结论：原样转发 0 条。** 依据：那些是机器 token，而把「这句话太抽象」转给用户等于**产品替模型编造理由，而不是转发**。若以后产品确实需要这种反馈，必须另立封闭协议（Rust 只转发已知码，未知/冲突/超长/额外字段统一落到固定失败文案）。**它诚实交代了没有 RED**：为了形式制造一个失败测试，会要求先实现一条本任务明确否决的自由文本通道——决策类任务这样处理是对的 |
| T113 | **演示前清单与运维手册按今晚改动重核**（`bbc062b`）。**又有 9 条不成立、2 条从来没有**。三条最要紧：①**磁盘上当时一个可发布的包都没有**——清单引用的 20:52 那个 DMG 已不存在，`bundle/dmg/` 整个目录不在，02:04 那次构建停在公证，比上一轮记录的更差；②**运维手册幕 2 有一句要当着客户说的台词现在是假的**——「模型还要把整套画面动效一起写出来，光这一项就占它输出的七成」，T92 之后模型根本不写画面，**而这句正是 T70 上一轮刚补进去的**：上一轮补进来的依据，这一轮就成了要当众说的假话；③B7 的失败指引整段作废并新增一个原来完全没有的状态——App 持档案租约被强杀后两个按钮永久失败，**唯一出路「安全注销」恰在「不要演示」名单里**，写清了现场处理顺序（别当着客户处理）与预防办法。新补两条现场事实：**出包前先解锁屏幕**、**构建期取字体会瞬时失败需重试**。顺带查证：T111 那个「改了源码进不了包」的形状对编排提速**没有同类洞**——Executor 包缓存键把 `backend/src` 整棵树逐文件摘要。**这条合并后我忘了改状态，挂在未收口区约两小时**——正是台账规则里「验收通过第一件事就是更新台账」，我自己违反了 |
| T57b | **e2e 入口的并/修/废执行完毕**（`8dddb62`）。当初的阻塞理由「交接单禁止改 `scripts/`」**已不成立**——codex 第四批六条全部落地（主线逐条查过文件而非查指标）。三个入口各自判定：**B5-04 `browser-settings` 判废并真删**——被测的用户路径本身已被产品删除（EB-10 按 `CLAUDE.md` §5 移除整条系统浏览器选择链路），四条独立证据里最有力的是 `startup-environment.spec.ts:49` **现在反向断言那张卡片不存在**，与它直接矛盾，而 `single_build_path.rs` 的豁免注释逐字写着这个入口应随验收一起死；**修它等于把被规则禁止的页面重做一遍**。删除范围含 runner、wdio 配置、spec、Tauri 配置、Vite mode、桩入口、3 个 npm script、3 条死 CSS 与 Rust 侧豁免。**H8-21 判「并然后废」**：两条 spec 共用同一 runner、同一构建、同一更新源、同样三个场景，全部外层证据都在 runner，唯一差别是决策走 `core.invoke` 还是走点击——而 §8 明说直接 Command 调用只能是分层证据；四处不重复的断言已并入。**H8-20 判修**：它是全仓**唯一**制造「下载被截断 → 断点续传 → 缓存收敛」的验收，属失败矩阵两项，没有别处覆盖。**新增门禁扫描指向已删除入口的引用**——原门禁只遍历现存入口，看不见悬空的 runner，正是 T57b 当初担心的「确定性悬空」；RED 实测捕获三条本会遗漏的引用。**这条合并后主线又忘了改状态**，挂在未收口区约一小时——今晚第二次犯同一个错（第一次是 T113） |
| T127 | **出包死在最后一步：`bundle/dmg/` 父目录没人建**（`dba7381`，已修，用户 09:10 授权）。三次出包里**两次死在这一行**：`create_disk_image` 的 `tempfile.TemporaryDirectory(dir=output.parent)` 要求 `bundle/dmg/` 已存在，报 `FileNotFoundError: .../bundle/dmg/tmpXXXX`。**是我 07-27 重构时的疏漏**：那句 `mkdir` 原本就在这里，把成像挪进 `fill_disk_image` 时它跟着搬走了，而 `fill_disk_image` 跑在这个暂存目录之后。`tauri build --bundles app` 只产出 `bundle/macos/`——**Tauri 的 DMG 打包器根本不执行**——所以 `bundle/dmg/` 永远不会被构建创建。**手工建目录实测无效**：08:31 建的，`tauri build` 08:36:41 整体重建 `bundle/` 时抹掉。测试断言源码里 `mkdir` 出现在 `TemporaryDirectory` 之前而非调用函数——走到那一行要先公证一个真包，每次断言四十分钟加一次往返 Apple；**变异验证**：把 `mkdir` 挪到临时目录之后，测试精确报出位置颠倒 |
| T125 | **desktop-e2e 六个验收补齐启动门禁前置，四个真跑通**（`a0ab077`）。此前它们自 `fb6d122`（07-26 21:16）起整族停在 spec 第一行「桌面运行环境需要处理」——那次改动**方向是对的**（消除测试构建分叉，旧写法让 workbench 断言恒真），缺的只是前置装配。**四个可跑驱动整条通过、退出码 0**，不只是越过门禁：`update-download`、`diagnostic-export`、`update-ui`（三场景）、`model-service`。**两条判定是这条任务的核心，都不是照抄现成做法**：①**控制面不能改端口**——`configured_local_control_plane_origin()` 只在 `control-plane-e2e` 下是 `option_env!`（`control_plane.rs:481-489`，**主线复核属实**），这六个用 `desktop-e2e`，编译进去的就是产品默认 `127.0.0.1:8765`；所以加的是**占住产品原点**的 harness，而不是往六个驱动各抄一段 `prepare_startup_gate`（那只覆盖前三项）；**也没有为绕开而换 feature**——`app_update_installation.rs` 四处以 `not(control-plane-e2e)` 分支，换了会**换掉正在被测的那段代码**。②**不需要数据库**——门禁只发 `/health` 与 `/version`，`api/system.py:62-71` 在 `database is None` 时跳过连接探测（**主线复核属实**）；用生产 `create_app(database=None)` 起进程内 uvicorn，并由一个**先于实现就绿**的用例钉住完整契约，理由是**返回 `200 {}` 的桩骗得过朴素探针却照样让门禁关着**。**两个打包驱动登记为带书面理由的豁免**：更新器跑到一半会把整个 `.app` 换掉，浏览器与 Executor 必须进 `bundle.resources`；构建后往安装副本里塞会让第一个场景过、`verify-installed` 在更新后的包上挂掉——**正是本任务要终结的假绿**；门禁会在它们一旦满足条件时报错要求删豁免。**途中被既有门禁抓到一次**（`sys.path.insert` 多出一个未声明的 MYPYPATH 根），子代理主动写进记录说这证明那套脚本测试真的在管事 |
| T124 | **两个夹具改为按平台派生产物名**（`679b59a`）。**单一来源本来就有**：`release-package-resources.v1.json` → `release_assembly._VideoResource.required_for(platform)`，**生产侧三个消费者一直在读它，唯二没读的就是这两个夹具**——它们把 `platform` 写成了常量。所以没有新建来源；新建正好制造出这个仓库反复在修的「同一个事实两份」。**断言一条没放宽**，守的性质原样保留，只是「哪一种拼写」改为派生。**Windows 是真验了、不是待复验**：winbox 同机同检出，先跑 HEAD 再跑本任务版本——`test_prepare_video_runtime` 从 rc=1（**9 项中 3 项失败**）到 rc=0（10 项全过），`test_video_studio_runtime_staging` 从 rc=1 到 rc=0；失败原文逐字是 `runtime/node.exe is missing or empty`，与 T123 的判断（**含 3/9 这个数字**）吻合。覆盖双向：新用例在 macOS 上跑 `windows` 分支、在 Windows 上跑 `macos` 分支。**今晚 T117 加的那道自守检查在这里立刻回本**——同族地加进本文件后，第一时间抓到子代理自己新写的检查没登记；没有它，那一跑会打印「9 checks passed」然后干净退出。**只登记不改**（都属 codex C10）：两处手抄的 `node.exe if os.name == "nt"` 映射，其中一处还有独立理由——它在 `MOTION_WORKER_INPUTS` 缓存键里，动它会让**每台机器**重编一次 Worker。另记一条 Windows 坑：**PowerShell 的 `>` 重定向写的是 UTF-16LE**，读回来必须先 `iconv`，否则看到逐字符加空格的乱码 |
| T122 | **冻结产物的最小环境，两条前提被实测推翻**（`a2df24c`）。**推翻一：PATH 取值根本不是那个红的原因**——在 winbox 上跑真实 PyInstaller onedir 产物的完整矩阵：换成正确的 `System32` 取值但**不给 `SystemRoot`，一样红**（`WinError 10106` = `WSAEPROVIDERFAILEDINIT`）；给了 `SystemRoot`，连 `.;C:\bin` 都绿。**真正致命的是 `env={"PATH": ...}` 这个写法本身**：交给子进程的环境里只有 PATH，而 Winsock 通过 `%SystemRoot%` 解析服务提供程序目录。**所以该共享的是「环境」而不是「PATH 串」**。**推翻二**：POSIX 上 `os.defpath` 实测是 `/bin:/usr/bin`，**没有前导空项**，POSIX 侧本来就没缺陷——主线复核确认，是主线说错了。**也推翻了派单的第三个猜测**「构建里跑的是 PyInstaller」：PyInstaller 那次 `run` 根本没传 `environment`，`probe_environment()` 只跑已冻结的候选，三处四个调用点跑的都是冻结产物，是同一个事实，因此可以共享——而 `LANG`/`NO_PROXY` 是素材 Worker 自己的关切，留在原处叠加、没塞进共享值。Windows 取值**没有另起答案**，采用 `run_p9_07_acceptance.py:526` 已经在同一台机器上跑着的那一份；两个根都缺时 **raise 而不是猜** `C:\Windows`。守卫按 AST 推导而非手抄三处清单，禁止 `scripts/` 与 `backend/tests/` 再读 `os.defpath`。**主线复核**：11 项自跑通过；变异往 `run_p9_01` 塞一处 `os.defpath`，守卫精确点名；合并后脚本层 61 个测试 / 765 项检查全绿。**待 Windows 复验**：取值本身已在 winbox 实测，但 `test_pyinstaller_bundle.py` 本身没在 Windows 跑过（需要那边能出 Executor 包），素材 Worker 那处仍撞不到，卡在 T119 缺 C 编译器 |
| T121 | **构建失败的诊断被进度条挤掉真实原因**（`1ab2892`，已修）。Windows 验收机上 media toolchain 构建失败，操作者拿到的是**八行 curl 进度条**，而真实原因 `No working C compiler found.` 就在同一个流里稍靠上，只能靠手工重跑 builder 找回来。**机理不是「尾部太短」**：`str.splitlines()` 把回车也当换行，所以一行原地重绘的进度条会变成一行一次重绘——**实测 20 次重绘裂成 22 行**，无论尾部取 8 行还是 80 行，装的都只有进度条。新增 `process_diagnostics.builder_diagnostic`：只按 `\n` 切、每行只保留最后一个回车段（终端上真正显示的那一段）、两个流都渲染。**没有复用 `run_e4_07_acceptance.py` 里那个近似函数**，因为它正是 codex 第四批 C7 在改的文件，动它会重演上一批「删掉符号害得两个导入方崩掉」；两者收敛作为明账写进了提交信息 |
| T118 | **门禁执行器自己的 backend 层写死 POSIX venv 布局**（`093c30c`，已修）。九层里只有它把路径写进自己，其余用 `sys.executable` 或 PATH 上的工具。Windows 上直接跑聚合器会给一个与产品无关的红 backend 层——**自己的失败和要守的东西长得一样的门禁，会教人学会无视它**。测试写歪过一次并被实验推翻：先写的「没有任何层的命令以 `.venv` 开头」对修复和缺陷同样失败，因为**派生值与它替掉的字面量长得一模一样，差别在来源，而来源在字符串里看不见**。改成平台派生两分支各断言 + 解释器在本机真实存在；变异验证过 |
| T117 | **内置浏览器缓存过期把 36 个验收驱动全堵死，堵了 6 小时没人知道**（`73687e7`）。**主线跑一句话生成端到端时撞上的**：codex 的 T26（`d5e5111`，07-26 **21:29**）改了暂存契约剔除 Widevine，而本机缓存是当天 **05:00** 建的、`exclusions` 为 `null`，验证正确拒绝。**真问题是它只拒绝、不重建、也不说该跑什么**——`desktop_e2e_prerequisites.py` 里暂存**目标**验证失败会 `rmtree` 后重建（自愈），**源缓存**验证失败直接抛（死路）；一边能自愈一边不能。调用它的驱动有 **36 个**，从 21:29 起全部卡死，**而且没有任何东西报告**——正是 T79 早就登记的「这些驱动不在任何门禁里」。现已在拒绝信息中点名 `build_embedded_browser_cache`。**顺带修掉写这条测试时踩到的同族缺陷**：`test_desktop_e2e_prerequisites.py` 的 `CHECKS` 是**手工维护的元组**，函数写在 `main()` 之后就跑零次，而输出仍是漂亮的「16 checks passed」——计数是推导的不会漂，**但成员资格是手抄的**（T51/T62 同族）。新增自守检查要求每个 `check_*` 都在 `CHECKS` 里；变异验证：摘掉一条登记，它精确点名该函数。18 项通过 |
| T53 | **Windows 验收机的慢档门禁前置补齐**。07-27 实测收口：`node --version` → **v26.4.0**（此前 v22.20.0，低于 `engines >=24 <27`）、`npm 11.17.0`、`F:\automation-tool\.git\hooks\pre-push` 存在且内容正确、它调的 `backend\.venv\Scripts\python.exe` 也在（Python 3.12.13）——**四项逐条查过，不是「装完就算」**。快档本来就不需要（`tsc` 只要 `>=14.17`），慢档必须对齐。**挂 pre-push 而非 pre-commit**：门禁要挡的是「提交内容与工作树显示的不一致」，而 pre-commit 跑在那棵工作树上，检查的正是被遮蔽的那一份。装的过程有两个本机坑：PowerShell 的引号会吃掉 `#!/bin/sh`，得走 base64；.NET 的当前目录不跟 shell 的 `cd` 走，路径必须写绝对 |
| T87 | **这份台账自己数不清自己**。两次漂移都被当成事实报给了人：一次是同一个任务号出现在两张表里，按表统计必然重复计数；一次是小节标题自带计数（`### ✅ 视频与内容（10）`）而实际有 12 行——**同一个数存两处，只维护了一处**。两次都没有任何东西会报错。新增 `scripts/check_demo_roadmap_counts.py`：任务号只许出现在一张表、总计必须等于唯一号数、小计必须加得上总计、**标题一律不许自带计数**（范围如 `（T73～T86）` 仍合法）。故意不按名字匹配总览行与小节——两者措辞本就不同，模糊匹配只会多一个维护错的地方。**未覆盖：正文散文里的数字**（如「其余 35 项」），那类只能靠人 |
| T102 | **UI Harness 端口写死 1420，并行 worktree 随机互相踩**（`ddf4d7d`）。三处写死：`playwright.config.ts` 的 `baseURL` 与 `webServer.url`、`vite.config.ts` 的 `server.port`。07-27 六条线同时活着，一条三次撞上「1420 already used」——**失败落在后启动的那条身上，看起来像用例 flaky 而不是端口写死**。改为按检出路径派生：主树仍是 1420（习惯不变），每棵 worktree 各拿各的；确定性而非随机——随机端口会让「那个服务是谁的」无法回答。给不合法覆盖值直接报错而不是回落：悄悄起在别处正是本仓库反复吃亏的形状。实测十二棵活着的树两两不撞（14206～14596），主树 Playwright 66 passed |
| T98 | **跑门禁的东西自己会谎报**（`f054250`）。原先每次现写一段 shell：`(cd frontend && pnpm lint 2>&1 | tail -5); echo "rc=$?"`——**管道之后的 `$?` 是 `tail` 的退出码，恒为 0**。07-26 那份日志因此对 eslint、backend、scripts 三层都写着 `rc=0`，而三层全红（eslint 两个 error，后两者因同一个 ImportError 死在收集期）。**那次差一步就被当成「绿了，出包」**，唯一拦住它的是失败恰好打印了肉眼能注意到的字。这正是本仓库反复吃亏的形状——检查报告通过是因为它根本没看——出现在**跑检查的东西里**。改为每层独立子进程、保留真实退出码，汇总由退出码推导而不是写在旁边；起不到的命令算失败不算跳过；一层都没跑也算失败 |
| T96 | **所有 Playwright 用例跑在 720 高的视窗里而配置声称 800**（`ea0bae8`）。project 级 `use: {...devices["Desktop Chrome"]}` 覆盖顶层 viewport。生产窗口真实值读自 `tauri.conf.json`：**1280×800，最小 960×640**（Tauri v2 的 width/height 是 webview inner size，与 Playwright viewport 直接可比）。**既有像素断言在 720/800 两档各实测一遍：没有一条失效、没有一条需要收紧。** 两个「选择」按钮那条余量 131→211px 确实变松，但折叠线本就该是客户真实拿到的那条，真正紧的边界由 960×640 那组承担（余 29px）——分工不是缺陷。`test.use` 钉子删两留一：与共享配置逐字相同的两个**留着有害**（将来有人再铺回 `devices` 预设，钉住的文件照常绿、没钉的悄悄缩回 720，正是本次事故的原形）。新增 `e2e/production-window.ts` 从 `tauri.conf.json` 读尺寸，**基准与生产窗口不再是两个能各自漂移的数字** |
| T97 | **`new_worktree.py` 默认留游离 HEAD**（`1ec57f9`）。`CLAUDE.md` §8.1 规定 worktree 只能由这个脚本创建，而它默认 detach、只在传 `--branch` 时建分支。**07-26 两条子代理线接连把提交落在游离 HEAD 上**：一条自己发现并 `git switch -c`，另一条只能按裸 SHA 合并。没有任何 ref 指向的提交还会被 git 回收——**「默认 detach」不是中性默认，是一种安静丢工作的方式**。改为默认用 worktree 名字建分支，`--detach` 供真正只读的树退出。真实验收：用改后的脚本建树，`git worktree list` 显示 `[verify-branch]` 而非 `(detached HEAD)`，验完已删 |
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
| T72 | **门禁执行者的三处空洞**（`050dfc9`）。三处逐条收口，**T103 全部实测复核**：① `run_script_tests.py` 没被接进任何门禁 → `commit_gate.py:521` 从被验 commit 的 checkout 执行它；② `deploy/` 下 48 条断言无执行者 → `discover()` 改为 `scripts/test_*.py` 加 `deploy/**/test_*.py` 的 rglob 派生，**实跑 `discover()` 得 56 条含那两个 deploy 文件**，直接执行得 `Ran 46 tests OK` 与 `Ran 2 tests OK`；③ 通过脚本的 stdout 被丢弃，「跑 50 条断言」与「什么都没跑就 return 0」长得一样 → `:303-313` 改为解析可数执行摘要，`returncode==0 且 checks==0` **判失败**，`aggregate_success()` 还要求非空且总数为正。④ 原先 `test_script_test_runner.py:73` 那个用与实现相同子串判据的恒真元测试**已被重写**为 `check_discovery_includes_deploy_tests`——造临时树放三个文件、比对发现集合，不再是子串启发式 |
| T73 | **测试把文件写进只读的 vendor submodule**（`32f8b55`）。跑完测试后 hyperframes 有 68 个 `output/compiled.html` 被改写、moneyprinterturbo 有 3 个 `.mp4` 被新建，`check_third_party_sources.py` 判整棵树为脏 → **任何一次测试跑完，发版门禁就失败**。新增 `scripts/run_vendor_tests.py`：从本地对象创建 `.local/vendor-tests` 隔离 checkout，不下载、不写共享 `.git/modules`；真实 vendor 在运行前后都校验 clean 且 HEAD 等于锁定 revision。`commit_gate.py:47` 导入 `extract_archive`/`materialize_repository`，慢档按被验 commit 物化。Windows 无 clonefile 时走 archive fallback 产生真实目录，**不造软链** |
| T74 | **执行器包缓存键是常量，34 个驱动永远拿不到新执行器**（`18ed7fc`）。缓存键现为 `desktop_e2e_prerequisites.py:439` 的 `f"{build_id}-inputs-v1-{executor_package_input_digest(...)}"`，摘要函数 `:419-430` 逐文件把 backend 源码、PyInstaller spec、锁文件、协议和只读资源喂进 sha256；归档解析 `:120-121` 改用 `archive_path()`，不再硬编码 `REPOSITORY_ROOT/.local`，**从任何 worktree 跑都不再第一步死**。**读代码时注意**：`SHARED_EXECUTOR_BUILD_ID` 这个常量仍在 `:93`，但它现在只是键的前缀不是键本身——只看那一行会误判成没修。**遗留**：没有逐一实跑 34 个调用方，所以不写成「34 条验收都拿到了新执行器」 |
| T75 | **另一处吞掉 PyInstaller 输出**（`f3c6d00`）。`run_e4_07_acceptance.py:197` 新增 `_completed_process_diagnostic()`，`:244` 在 PyInstaller 非零退出时 `raise RuntimeError(f"E4-07 PyInstaller build failed\n{diagnostic}")`，两个流各保留最后 20 行、空输出用固定诊断避免又回到沉默失败；E4-09/E4-10 与 Windows candidate 同批覆盖。**T103 发现同函数内还剩一处未修**：`:272` 的打包签名失败分支仍是裸 `raise RuntimeError("E4-07 package signing failed")`，`capture_output=True` 抓走的两个流全丢——与 `ce45efd`、本条同族，修法现成（同文件 `:197`）。**已记入 `docs/development/T103.md` §4.2，未修** |
| T76 | **`desktop-e2e` 前端入口让 workbench 断言恒真**（`fb6d122`）。旧入口注入无条件 ready 的启动桩且不注入生产的 17 个 gateway，`test:tauri` 与 `test:h8-19-app` **37 毫秒通过**。现 `frontend/src/test-tauri-main.tsx` 共 3 行，第 3 行 `void import("./main")`，保留受控桌面测试 Adapter 后直接加载生产组合根。**T103 实跑** `node --test frontend/tests/desktop-e2e-production-entry.test.mjs` → 1 pass / 0 fail。**该任务的完成定义是「测试不撒谎」，不是「工作台在所有环境已可用」**——修好后真实 Tauri 在环境未备时如实停在启动门禁，那正是恒真桩消失的证据 |
| T62 | **`run_script_tests.py` docstring 写死 "37 self-contained test scripts"**（`cc24156`，07-26 18:45）。代码本身是 glob 推导的没有功能问题，但这个会静默落后的散文计数**就写在那份讲 "discovery is derived, never curated" 的文档里**。**T103 复核**：`git log -S "37 self-contained"` 确认该字串已被移除，当前 docstring 无任何计数（实际脚本数已 54） |
| T25 | **视频线 WDIO 验收补齐真实资源前置**。逐项核对后只剩一个独立前置：signed Executor、隔离 PostgreSQL、Control Plane 与编译期启动配置由 `video_studio_startup_harness()` 每次运行自产；BM-08 与 IM-05 所需的视频 Worker / media toolchain 由各自驱动自产——**T103 复核**：`run_im_05_acceptance.py:22-23,149` 与 `run_bm_08_acceptance.py:16` 都 `from prepare_video_runtime import install/prepare` 并实际调用。见 `docs/development/T25.md` |
| T70 | **演示前检查清单与 Runbook 逐条重核**（`87d7381`）。两份文件写于 07-26 下午，当晚到 07-27 凌晨 `frontend/src` 与 `src-tauri/src` 又合入 **40 笔**提交。逐条核完：A/B/C 三段原有 **23 条可执行检查里 11 条已不成立或不够用**，全部改对；补 6 处，其中 A8（挂载 DMG 确认有拖拽目标）是新增整条，清单 23 → 24 条；D 段 10 条待确认里 **4 条已有答案**移进已结案，另新增 3 条真没做的；Runbook **8 处**旧行为讲解词、点击路径和时间编排改对，含**一处幕号写错**（§5 路径 D 把「平台状态」说成幕 4，实在幕 2）。**头号结论：现在磁盘上没有一个可以拿去演示的包**（见「当前下一步」第 1 条）。方法上有意避免「用读代码推断布局」——凡涉及放不放得下、多宽、默认开不开，一律跑 Playwright 实测。**这份 `docs/development/T70.md` 是「逐条去核而不是照抄」的范本** |
| T103 | **未收口任务全量对账**。台账声称还剩 40 项，**逐条查证后实为 15 项**（随后 T101/T90b 又自报 3 个新窗口，现 17）：**25 条早就做完了，只是台账不知道**，最久积压约 6 小时 25 分（T62、T66a），批量最大的一笔是 codex 第二批 19 条。**反方向的错（台账写着做完、实际没做）一条都没有**——已收口区抽查未发现虚报。判据是「说某一项的状态之前，先找到它此刻的证据在哪」：不抄 `docs/development/*.md` 的状态行（那 19 份是 `aef241f` 事后补录的，也是索引不是证据），每条 `✅` 都回代码或测试独立核过，最强的一档是**把台账描述的缺陷条件造出来跑，看它还复不复现**（T61/T65/T66b 就是这么定的）。顺带查明 codex 第二批 19 条：**15 条真做完、T79 没做且自己承认、T77 是有效的否定性交付、T78 部分完成**；那批补录整体诚实——19 份全部主动写明「未与原代码同提交」，9 份在状态行就带 `🔍` 缺口，**没有一份把「代码改了」写成「链路验了」**。查证中发现的 5 个产品缺陷与台账风险见 `docs/development/T103.md` §4 |

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
