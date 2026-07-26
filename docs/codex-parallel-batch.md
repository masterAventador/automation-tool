# codex 并行批次交接单（第二批）

> 面向在**独立分支**上与主线并行工作的 codex。本文件是这批任务的完整输入，读完即可开工。
>
> 日期：2026-07-26 ｜ 分支：`codex/batch-2` ｜ 主线这批只做 T10（正式包全量验收）与文档
>
> 第一批（T22 / T58c / T62 / T63 / T64 / T66a）已交付，谢谢。**本文件已整体替换为新的一批。**

---

## 0. 这批怎么切的

第一批按「文件族」切，有效。这批**切法变了**：主线把整个 Rust 产品代码面让出来，自己只做正式包验收和文档。

理由：T10「从正式签名包跑一遍所有用例」只有主线能做——它要逐条判定失败属于产品缺陷 / 测试过期 / 环境缺失 / 基建问题，而这个判定依赖大量只存在于主对话里的上下文。反过来，产品代码的具体修复是可以完整交付的。

所以你拿到的是**四个完整的文件族**。这批期间**主线不碰任何 `frontend/src-tauri/src/*.rs`**。

**主线这批会碰的**（你别动）：`docs/demo-sprint-roadmap.md`、`docs/development/T10*.md`、`wt/release/` 下的一切。

---

## 1. 硬约束（违反其一，产出即作废）

### 1.1 隔离与环境

```bash
cd /Users/aventador/code/automation-tool
git fetch
git worktree add wt/codex2 -b codex/batch-2 origin/main
cd wt/codex2
```

**建好之后必须做这三步**，否则会踩到主线今天已经踩过的坑：

```bash
(cd backend && uv sync --locked)              # 1. 自己的 venv，不要软链主树的
(cd frontend && pnpm install --frozen-lockfile) # 2. 自己的 node_modules，不要软链
git submodule update --init --recursive        # 3. vendor 必须真实检出，不能软链
```

**这三条各有今天实测的教训**：

- **软链 `backend/.venv` 会毁掉全机器的 Python 结论。** venv 里 `automation_tool` 是 editable 安装，指针写在**单个共享文件** `site-packages/automation_tool.pth` 里；而仓库自己的 `package.json` 有多条 `uv run --project ../backend --locked`（`test:p9-05`、`test:h8-22-windows-package`、`p9-02/04/07`），**这条命令会 sync 并改写那个 .pth**。做过对照实验：跑一次，指针就从主树变成那条线的树。发现时它指着第一批 codex 的树——主树跑的 pytest 在测 codex 的在飞代码，**正在跑的正式包构建也在打包 codex 的后端源码**，只能停掉重来。
- **软链 `node_modules` 会被 pnpm 删掉。** pnpm 11 跑任何 script 前校验依赖，软链必然不匹配，它就决定**先删再装**，只有「没有 TTY」挡住了；而它的报错**主动建议设 `CI=true`**——照做会静默删掉主树那份。**永远不要对 pnpm 设 `CI=true`**；要绕过校验用 `PNPM_CONFIG_VERIFY_DEPS_BEFORE_RUN=false`（只跳校验、不写任何东西）。
- **vendor 不能软链。** `tools/motion-authoring/motion_style_freezer.py:105` 显式 `root.is_symlink()` → 拒绝，`test_release_assembly.py:602`、`check_motion_catalog_release.py:44` 多处同型。这是有意的供应链控制：vendor 的信任建立在「这目录就是那个锁定 commit 的检出」上，软链可指向任何地方。**主线在这条上判断错过一次，是第一批的子代理当场推翻的**——那次推翻是对的。

**两条并行安全规则**：

- **`git submodule update` 不要和别的 worktree 同时跑。** `.git/modules` 是共享的。主线今天并行跑三棵树，撞出主树 submodule 被检出成错误 commit（`95dd03e` 而非锁定的 `b1588e1`）+ index 里 204 条暂存删除，修了三轮才回到锁定状态。**串行跑。**
- **不要设 `CARGO_TARGET_DIR` 指回主树**，会撞 cargo 锁。宁可全量构建。

### 1.2 提交

**一律用 `git commit -m "..." -- <path1> <path2>`。**

git index 是共享可变状态——从 `git add` 到 `git commit` 之间，别的 agent stage 的任何东西都会被你的 commit 带走。实测发生过。

三个陷阱：

- **参数顺序**：写成 `git commit -- <paths> -m "msg"` 会把 `-m` 当成路径。主线今天刚踩过；
- 涉及**重命名或删除**时，新旧两个路径都要列进去，否则 HEAD 里会同时留下两份（本机看正常，全新 checkout 才发现）；
- **新增文件仍必须先 `git add`**（`commit --` 不认未跟踪路径）。

### 1.3 TDD 铁律

项目 `CLAUDE.md` 第 8 节，无例外：先写会失败的测试 → **实际运行看到失败，确认失败原因是你预期的那个** → 最小实现 → 转绿 → 跑受影响回归。

**RED 必须是断言失败，不能是编译失败或 ImportError。** 主线今天犯过：测试导入一个还不存在的常量，拿到 ImportError——那不算 RED，补了桩让它返回空值才拿到真正的断言失败。

### 1.4 两条检查习惯

- **macOS 上没有 `timeout` 也没有 `gtimeout`。** 写 `timeout 300 xxx` 会导致整条管道根本没执行，输出为空、退出码 0，看起来像通过。主线和两个子代理各踩过一次。
- **看到「输出为空 / 没有报错」时，先证明工具真的执行了，再解释这个空。** 今天的核心失效模式是「一个检查报告成功，而它实际只是安静」——安静和成功长得一模一样。也别信管道末尾的退出码：`cmd | tail -60` 的退出码是 `tail` 的，主线因此把 392 个通过的测试数成了 29。

---

## 2. 任务清单

每条给了**我的判断 + 判断依据**。依据是可核实的事实，不是印象。
**你读完代码如果有相反判断，说服我，我不预设立场。**

### 族 A —— 失败时能看见（T69 / T50）｜**优先做这族**

文件面：`frontend/src-tauri/src/lib.rs`、`control_plane.rs`、`Cargo.toml`。主线这批完全不碰。

#### T69 App 全程零日志，出问题时信息为零

**现象**：整个 Tauri App **没有任何日志设施**——没有 `tracing` / `log` / `env_logger` 依赖，零条日志调用。

**依据**：今天主线让用户「跑一下 App 二进制看 stderr」，那条命令**根本不可能有输出**，因为没有任何东西会往 stderr 写。实测，不是推断。

**为什么现在做**：下周要在客户面前演示，演示机是全新 Mac。**一旦出问题我们是瞎的**——没有日志只能靠复现，而现场没有复现的机会。

**边界（这是演示前的改动，务必守住）**：只加**不改变任何现有行为**的一层。

- 允许：加日志依赖、初始化 subscriber、在关键路径加记录点（setup 各阶段、Sidecar 生命周期、Control Plane 请求失败、任务状态转换）；
- **不允许**：改任何控制流、改任何错误处理、改任何 `?` 的传播、把 `expect` 改成别的；
- 日志落到 Tauri `app_data_dir` 下的文件，**带大小与保留期上限**（项目规则第 7 节对截图/Trace 的要求，日志同理）；
- **绝对不能进日志的**：Cookie、Token、平台消息、联系人、页面原文、本机私有路径、设备私钥、产品账号 access/refresh secret（项目规则第 7 节明令）。**写一个测试证明这些不会出现在日志里**——喂一个带 token 的错误进去，断言落盘内容里没有它。
- 需要新依赖可以直接改 `Cargo.toml`（这批归你），但**锁文件改动单独一个提交**，方便主线 review。

#### T50 注销成功但界面报失败

**现象**：5 次复现 4 次。注销实际成功，界面报失败。

**依据**：内层约 5 秒的轮询被包在 60 秒超时里——**不是等不起，是自己先放弃了**。已从演示脚本里摘掉「安全注销」来绕开。

**要做的**：让内层轮询的预算与外层一致，或让它在拿到终态前不提前放弃。修完把演示脚本里摘掉的那一步加回去。

**顺手**：这条和 T69 同文件族。做完看一眼——**加了日志之后这个 bug 是不是更容易定位**？如果是，在台账里写一句，那是 T69 价值的直接证据。

### 族 B —— 启动 abort 的自愈（T61 / T65 / T66b）

文件面：`frontend/src-tauri/src/video_job_workspace.rs` 及其测试。三条同文件同启动路径，必须一起做。

**先读 `docs/development/T61-setup-abort-risk.md`**——完整风险面调研（23 个 abort 点、六场景风险表、代码依据）。本节只写结论。

#### T61 setup 失败即 abort，无兜底

**结论先行**：演示场景（全新 Mac → 首次启动 → 做视频 → 正常退出 → 再打开）**实测不会 abort**，前一条线的高风险判断已被推翻。剩余真实窗口只有两个，都不自愈：

- `delete_artifact` 的 `remove_dir_all` 执行到一半被杀 → 目录只剩一个文件 → **永久 abort**；
- 硬断电造成 payload 大小与 manifest 不符 → **永久 abort**。

**真正的缺陷是策略不一致**：`.import-*` 半成品清理继续 ✅、staging 半成品清理继续 ✅、**已落地 artifact 出任何问题直接 abort** ❌。对自己写的临时垃圾宽容，对自己写的正式产物零容忍到把整个 App 拉崩。

**要做的（约四行）**：把 `validate_artifact_inventory()`（`video_job_workspace.rs:379`）从「启动门禁」降级为「启动清理」——遇到坏 artifact 按 `recover_interrupted_imports` 已有做法删掉或挪进隔离目录，只有**清理动作本身失败**才 `Err`。`list_artifacts()` 作为运行期 API 的严格语义**不要动**。

换来的是把两块永久砖变成「丢一个视频，App 照常开」。已有测试夹具：`tests/video_job_workspace.rs:114/167/483`。

#### T65 `cleanup_expired` 生产代码里没有任何调用方

**现象**：只有 `tests/video_job_workspace.rs:391` 调它。

**我的判断**：30 天保留策略形同虚设，artifacts 单调增长，**启动时的 abort 面随视频数量线性增长**——和 T61 是同一个问题的两端。

**要做的**：接上真实调用方（启动时或任务完成后），写测试证明过期 artifact 真的被清掉。

#### T66b 目录权限只检查不修复

**现象**：`validate_private_directory_metadata`（`video_job_workspace.rs:1299`）对目录**只检查不修复**；而 `deployment_profile` 和 `secure_store` 对目录是**强制 `chmod 0700` 修复**。同一仓库两套策略。

**依据**：你第一批的 T66a 已经把 `secure_store.rs` 的文件那一半改成「发现漂移就修复」。这是同型的另一半。触发场景是 Time Machine / 迁移助理恢复的账号——**演示机是全新 Mac 不触发，但客户机器很可能是迁移过来的**，表现是启动即闪退零提示。

**要做的**：对齐成同样的「发现漂移就修复，只有修复失败才 `Err`」，与你 T66a 的做法保持一致。

### 族 C —— 门禁真的在跑吗（T72 / T73）

文件面：`scripts/`、`deploy/`、`.github/`、`tools/`。主线这批只在 `docs/` 下写东西。

#### T72 门禁执行者的三处空洞

今天全量扫描的副产物，同一类：

1. **`run_script_tests.py` 没被接进任何门禁。** `grep -rn "run_script_tests" .github/` **零命中**。为「守卫没人执行」造的解药，自己没人执行；42 个脚本里 39 个仍靠手敲，被点名的 3 个还是用裸 `python3` 调的——**正是它自己 docstring 警告的写法**。
2. **`deploy/` 下 48 条断言无任何执行者。** `deploy/ingress/test_ingress_config.py`(2 条) 与 `deploy/cloud/test_cloud_deployment.py`(46 条)，pytest 的 `testpaths=["tests"]` 收不到、runner 只 glob `scripts/` 也收不到，唯一调用方式是 `docs/development/T68.md:131` 里一行手敲命令。**实跑是绿的**，所以是潜伏风险不是当前故障。
3. **runner 只对失败脚本打印 stdout（`:145`），通过的直接丢弃。** 于是「跑 50 条断言」和「什么都没跑就 `return 0`」在汇总里**完全一样**。实测 41 个脚本共 400 条检查，**11 个「通过」拿不出任何可数证据**——其中就有 `test_video_studio_acceptance_scope.py`，当初那条红着躺很久的守卫。

   **注意措辞**：子代理断言的不是「它们是空壳」，而是「**无法区分**」。修的方向是让通过也可数（要求脚本报告执行条数，runner 汇总并在为 0 时判失败），不是去猜哪个是空壳。
4. **顺带**：`test_script_test_runner.py:73` 用**和被测实现一模一样的启发式**断言实现符合该启发式，恒真，永远发现不了启发式本身选错环境。

**注意 Actions 的现状**：本仓库 GitHub Actions 已在 2026-07-26 **整体禁用**（账单问题，从来没运行过一行），见 `.github/workflows/README.md`。所以第 1 条**不要接到 Actions 上**——接到 `scripts/commit_gate.py` 的慢档，或一个能在 Windows 验收机上跑的入口。

#### T73（新）测试把文件写进只读的 vendor submodule

**现象**（今天实测）：跑完测试后，

- `vendor/hyperframes` 有 **68 个 `packages/producer/tests/*/output/compiled.html` 被改写**；
- `vendor/moneyprinterturbo` 有 **3 个 `test/resources/*.png.mp4` 被新建**。

**后果**：`scripts/check_third_party_sources.py` 明确拒绝——`hyperframes submodule is dirty; upstream source is read-only`，退出码 1。**任何一次测试跑完，这道发版门禁就会失败。**

**依据**：直接违反项目 `CLAUDE.md` 第 6 节——两个 vendor 只允许作为**只读 Submodule** 存在，禁止在 Submodule 内修改。主线今天为了把状态修回去折腾了三轮：`reset --hard` 清不掉（`.gitattributes` 声明这些走 LFS，而仓库里存的是真实内容，clean 过滤器转换后与 index 对不上），最后靠整目录删除 + `git submodule update --init` 全新检出才干净。

**要做的**：找出哪条链路往 vendor 里写（大概率是调用上游 producer 测试或渲染时把输出目录指向了 vendor 内部），改成写到 `.local/` 下的隔离目录。**然后加一道门禁**：跑完测试后 vendor submodule 必须仍然干净——这道门禁本身要能自证（故意往 vendor 写一个文件，确认它抓得到）。

### 族 D —— 字体权利登记（T40 / T41）

文件面：`contracts/quality/asset-rights-policy.v1.json` 及相关校验脚本。

- **T40**：包内 UTM Kabel KT 字体**权利未判定**；
- **T41**：动效叠加字体未进权利登记表——`big-shoulders-display-latin.woff2` 登记在另一份契约里，不在 `asset-rights-policy`。

**我的判断**：合规项，Demo 本身不阻塞，但**在客户面前演示的包里带着权利未判定的字体是有风险的**，而修的成本很低。

**依据**：项目已有完整的字体权利登记机制（T28 把 148MB 专有字体换成 Noto Sans CJK 时建的，包一级四道闸：缺字体 / 被替换 / 版权行不对 / 缺许可证）。这两个是漏网的。

**要做的**：判定这两个字体的权利状态并登记进 `asset-rights-policy.v1.json`。如果判定结果是「不可再分发」，**那就不能留在包里**，要么换掉要么移除，并说明影响。**判不了的就明确写「无法判定」并说明卡在哪，不要猜一个填进去。**

---

## 3. 起点与交回

**起点**：`origin/main` 最新提交。开工前 `git fetch && git log --oneline -1 origin/main`。

**分支**：`codex/batch-2`

**每个任务一个提交**，提交信息用中文（conventional commit 前缀可保留英文），不加任何 AI 署名。

**每完成一个任务，更新下面「进度回填」那一行**，提交推送。主线读这里，不翻你的分支历史。

**族 A 优先**——它直接决定演示当天出问题时我们看不看得见。

---

## 4. 进度回填（codex 写这里，主线只读）

| 任务 | 状态 | 提交 | RED 证据（看到的失败输出） | 备注 / 反驳 |
|---|---|---|---|---|
| T69 App 零日志 | ⬜ | | | |
| T50 注销界面报失败 | ⬜ | | | |
| T61 artifact 门禁降级为清理 | ⬜ | | | |
| T65 `cleanup_expired` 无调用方 | ⬜ | | | |
| T66b 目录权限只检查不修复 | ⬜ | | | |
| T72 门禁执行者三处空洞 | ⬜ | | | |
| T73 测试写进只读 vendor | ⬜ | | | |
| T40/T41 字体权利登记 | ⬜ | | | |

---

## 附录：两条不要碰的结论

**T26「内置浏览器换成 Chromium 开源构建」——任务标题的结论是反的。** 调研结论是**不换**，完整证据见 `docs/development/PLAN-chromium-replacement.md`：Playwright 1.61.0 在 macOS/Windows 上已停产 Chromium 构建（实测 rev ≥1210 是 404）；所有 Chromium 开源构建不含 H.264/AAC，实测抖音播放器彻底黑屏；体积不降反增且 Windows 超门禁上限；而且 **CfT 在 `sec-ch-ua` 里报的就已经是 `"Chromium"`**，换掉的只是文件名不是被网站看到的身份。唯一白纸黑字的分发禁令只针对 `libwidevinecdm.dylib` 一个文件（20.2 MiB），删掉零功能损失。正确任务是「留在 CfT + 剔除 Widevine」，但那会改变正式包内容、与主线 T10 冲突，**等 T10 收口后再做**。

**T67 Windows UNC/junction 不在这批**，因为 `browser_profiles_windows.rs` 是 `#[cfg(target_os = "windows")]`，在 macOS 上根本不参与编译，做不出「先看到失败的测试」这一步。必须在 Windows 机上做。
