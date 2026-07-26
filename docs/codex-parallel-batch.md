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

### 族 E —— 验收基建的真根因（T74）

文件面：`scripts/desktop_e2e_prerequisites.py`。

#### T74（新）执行器包缓存键是常量，34 个驱动永远拿不到新执行器

**现象**：`scripts/desktop_e2e_prerequisites.py:75`

```python
SHARED_EXECUTOR_BUILD_ID: Final = "desktop-e2e-startup-gate"
```

`ensure_signed_executor_package(build_id=SHARED_EXECUTOR_BUILD_ID)` 用它当缓存键，命中就直接返回缓存目录。**执行器源码不参与这个键。** 而 `grep -rln 'desktop_e2e_prerequisites' scripts/` 有 **34 个驱动**。

**后果**：缓存一旦建立，执行器的任何改动就**再也进不了那 34 条验收**。今天已经吃过一次：一句话生成视频的验收连续失败，表现是子进程 exit 2、stdout 空、错误码 `authoring_crashed`——看起来像产品崩溃，实际是**验收装的是一个早于 `--author-motion` 入口的旧执行器包**，它把编排请求当 bootstrap 读了。

**这条为什么还在**：当时的修复只在 `run_t36_acceptance.py` 一个驱动里加了「起跑前先探这个包，不合格就重建」，**根因原封不动**。另外 33 个驱动仍然在这个坑里。

**依据**：`SHARED_EXECUTOR_BUILD_ID` 的定义就在那一行，是常量不是推断；34 这个数字是 `grep -rln` 实测。

**要做的**：把缓存键改成**从执行器实际输入内容推导**（backend 源码树的摘要 + spec 文件 + 相关契约），源码一变键就变、缓存自动失效。参考同仓库已有的做法——项目里多处用「逐文件 SHA-256 清单」做同类事情（`distribution-manifest`、`release-package-resources`）。

**验收要求（重要）**：写一个测试证明**改一行执行器源码后，缓存键会变**。这条不能只靠读代码判断，因为整个问题的性质就是「看起来在刷新，实际没有」。

**注意**：`wt/sweep-desktop` 和 `wt/sweep-suite` 里有主线的扫描线在用这个文件。worktree 是隔离的，你在 `wt/codex` 里改不会影响它们，但**合并时主线会重新验证那两条线的结论**。

**同一个文件里还有一条，一起修**：`desktop_e2e_prerequisites.py:90` **硬编码 `REPOSITORY_ROOT/.local/`**，没有用仓库**专门为此写的** `archive_path()`。后果是**那 34 个驱动从任何 worktree 里跑都在第一步就死**——这是桌面扫描线实测出来的，不是推断。改成走 `archive_path()`。

#### T75（新）另一处吞掉 PyInstaller 输出的地方

`scripts/run_e4_07_acceptance.py:226` 的 `build_signed_executor` **也用 `capture_output=True` 抓走 PyInstaller 输出然后丢掉**，构建失败时零诊断。

**这是主线的漏修**：`ce45efd` 只修了 `backend/src/automation_tool/executor/macos_candidate.py` 的 `_run_pyinstaller`，**这是另一个函数**，桌面扫描线发现的。

**要做的**：同样让它把构建器说的话带出来。**顺便全仓搜一遍还有没有第三处**——`grep -rn 'capture_output=True' scripts/ backend/src/` 然后看哪些在失败分支里把输出丢了。这类「失败时零诊断」在构建机上代价最大。

### 族 G —— 桌面扫描线抓到的产品与测试缺陷（T76 / T77 / T78）

这三条来自主线桌面 E2E 全量扫描（52 个执行者，39 通过 / 13 失败 / 5 未跑，13 条失败归并后只有 6 类根因）。

#### T76（新）`desktop-e2e` 前端入口让整片断言变成恒真

**现象**：`test:tauri` 和 `test:h8-19-app` **在 37 毫秒内通过**。

扫描线原本预测它们会因为没装启动门禁而失败，**预测错了**——然后它去追「为什么绿」，这才是价值所在：

`vite.config.ts` 按 mode 换入口。`desktop-e2e` 模式用的是 `test-tauri-main.tsx`，它注入的 `desktopShellStartupCheck` 是

```ts
async check() { return { status: "ready" }; }
```

**无条件 ready**，从不问 Control Plane 也不问本机环境，并且**完全不注入生产的 17 个 gateway**。

所以 `workbench.spec.ts` 断言「工作台挂载了」在这个构建里**恒真**，而它是该 spec 的**唯一执行者**。

**对照组证明这不是没办法**：`model-service-e2e` 用的 `test-production-main.ts` 是直接 `import("./main")`，那才是正确写法。

**依据补充**：Rust 侧的注释写着「every build compiles exactly this one」，给人一种已经收口的印象——**分叉现在在前端**，Rust 门禁看不见它。主线的 T59 把这个桩纳入了单一构建路径门禁，但**没有改掉入口本身**。

**要做的**：让 `desktop-e2e` 入口走和 `model-service-e2e` 一样的路子（`import("./main")` + 受控测试 Adapter），而不是用一个恒真的桩替代整个启动检查与 gateway 注入。改完 `workbench.spec.ts` 应该真的在测东西——如果它因此变红，那是**真相浮出来**，报告出来别去放宽断言。

#### T77（新）B5-13 前端投影与权威态不一致（疑似产品缺陷）

**现象**：权威态返回 `state: "missing"`，UI 却渲染「暂时无法读取」。

**扫描线的判定**：后端对、前端投影错。

**我的判断**：这是演示路径上用户看得见的错误信息，值得修。但**先自己复核一遍投影逻辑再动手**——扫描线自己标的是「疑似」，不是定案。

**注意**：这条落在前端 `frontend/src/`，与族 A 的 Rust 文件不冲突。

#### T78（新）视频线 7 个驱动 / 8 个 spec 全卡启动门禁

**现象**：视频线的 7 个驱动、8 个 spec **全部卡在启动门禁**。

**已知原因**：`780abce` 拆桩之后驱动没跟上——`prepare_startup_gate` 的 34 个调用者里，这 7 个**一个都没有**。

**已有记录但低估了范围**：`docs/development/T36-oneshot-video-preview.md:116` 记了这件事，但文档说「5 个 spec」，**实测受影响面更大（8 个）**。

**要做的**：给这 7 个驱动补上 `prepare_startup_gate`。改完至少让它们能跑起来——**跑起来之后失败是新信息，跑不起来是没信息**。

**依赖**：这条和 T74（缓存键 + 硬编码路径）在同一批里，建议先做 T74 再做这条，否则你会在一个「执行器包永远是旧的」的地基上判断结果。

### 族 F —— 出厂前该清掉的（T26 / T24 / T45 / T38）

#### T26 剔除内置浏览器里的 Widevine CDM

**先看清楚：任务原标题「换成 Chromium 开源构建」的结论是反的**，完整证据见文末附录和 `docs/development/PLAN-chromium-replacement.md`。**不要换浏览器。**

**要做的是另一件事**：剔除 `libwidevinecdm.dylib`。

**依据**：这是整轮调研里查到的**唯一一条白纸黑字的分发禁令**。macOS 版 Chrome for Testing 内置的

```
chrome-mac-arm64/Google Chrome for Testing.app/Contents/Frameworks/
  Google Chrome for Testing Framework.framework/Versions/149.0.7827.55/
  Libraries/WidevineCdm/
    LICENSE                                            (473 B)
    _platform_specific/mac_arm64/libwidevinecdm.dylib  (20,183,440 B)
```

那份 473 字节的 LICENSE 原文：

> Google LLC and its affiliates ("Google") own all legal right, title and interest in and to the content decryption module software ("Software") … **You may not use, modify, sell, or otherwise distribute the Software without a separate license agreement with Google. The Software is not open source software.**

**现状是它被 `distribution-manifest.v1.json` 逐文件摘要锁定，随 macOS 安装包分发给用户。** 不需要任何法律解读，那句话就是字面意思。

**实测过删掉它的后果**（复制 CfT 树 → 删 `Libraries/WidevineCdm/` → 跑 EB-16 那套校验 + 一次真实抖音访问）：

| 检查 | 结果 |
|---|---|
| 文件数 / 字节 | 331 / 359,441,871 → **328 / 339,257,128**（省 19.2 MiB） |
| `codesign -dv` 可执行文件 | `Identifier`、`adhoc`、`linker-signed` **全部保留** |
| `codesign --verify --strict` | 失败——**但未删改的原始缓存树同样失败、同样报错**（CfT 上游就是 linker-signed、`Sealed Resources=none`），有对照组 |
| 启动 + 打开抖音 | 正常，渲染出与未删改版本相同的 8 个 `data-e2e` |
| `canPlayType` avc1/mp4a/hev1 | **全部仍为 `probably`**（H.264/AAC/HEVC 一个没丢） |

另外实测：**Widevine EME 在删之前就已经不可用**（`NotSupportedError`），也就是说这 20 MiB 在我们的启动方式下压根没被启用——**纯负债，零收益**。Windows 版 CfT 里根本没有这个文件。

**具体动作**（1～2 人日）：

1. `contracts/browser/embedded-chromium-staging.v1.json` 每个 target 增加 `excluded_entry_prefixes`（macOS 两个目标填上面那个 `Libraries/WidevineCdm/` 路径，Windows 填空数组）；`archive_sha256` **不变**（仍然锁原始归档），staging 在解包后按白名单**显式剔除**并把剔除动作写进 `staging-manifest.json`；
2. `scripts/build_embedded_browser_distribution.py` 的 licence block 把 `"redistribution_review": "pending"` 换成有依据的结论——声明已剔除的专有组件清单 + 剩余组件的许可结论；
3. `RELEASE_PAYLOAD_PARTS_MIB["embedded-chromium"]` 343 → **324**（实测 339,257,128 B = 323.6 MiB），连带包上限重算；
4. 重跑 EB-03 / EB-05 / EB-16（macOS arm64 + x86_64）。Windows 无此文件，确认剔除列表为空时行为不变。

**边界**：**只做剔除 Widevine 这一件事。** 品牌与 CfT 的 ToS 定位问题（产物仍叫 `Google Chrome for Testing.app`）是法律判断不是技术判断，用户还没拍板，**不要碰**。

**时机**：主线暂停出包，等你这批做完再出，所以这条现在做正好——下一版包就带上。

#### T24 执行器包根按 `debug_assertions` 分叉

**现象**：执行器包的根路径按 `debug_assertions` 走不同分支。

**我的判断**：这直接踩项目 `CLAUDE.md` 的「单一构建路径规范」——那条规则明令禁止「用编译期 feature / 环境变量 / 构建模式改变**产品去哪里寻找**文件、资源、进程、可执行程序」，并且写着这条规则来自一次真实事故（测试构建从环境变量读依赖路径、生产构建从安装包资源目录读，结果验收长期全绿而用户拿到的包整块功能不可用）。

**依据**：规则里只允许三类差异（测试驱动的挂载、窗口可见性/日志级别、指向隔离实例的配置值），而「去哪里找执行器包」不属于任何一类。

**要做的**：合成一条路径。如果发现确实需要区分，**说清楚它属于允许的哪一类**——说不出来的一律不许留。

#### T45 Control Plane 镜像被打进 playwright

**现象**：约 50MB 的 Control Plane 相关内容被打进了 playwright 的依赖树。

**我的判断**：代码层守住了 `CLAUDE.md` 4.2 的边界（Control Plane 不依赖 Playwright），**打包层破了**。

**边界**：这条要动 `uv.lock`。**这批你独占它**，主线不碰。改完把 `uv.lock` 的改动放在单独一个提交里。

#### T38 演示后回收清单

纯文档。演示结束后要回收/停用哪些东西（云端实例、测试账号、临时凭据、演示数据、Demo Profile），写成可执行的清单。

**依据**：`~/Documents/at-tools-credentials/project-secrets/` 下有演示账号、两个第三方 API key、签名私钥；云端 `at.xuanbai.tech` 上有 Control Plane 与 PostgreSQL。这些演示后该怎么处理需要有个清单，不能靠记忆。

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
| **A** T69 App 零日志 | ✅ | 本提交 | Rust 落盘测试准确失败：`sensitive error detail reached the desktop log: Cookie=session-cookie` | 固定事件白名单覆盖 setup、Control Plane、任务状态与 Sidecar 生命周期；有界异步队列不阻塞业务，单文件 1 MiB、最多 8 个、保留 7 天；T50 的超时现在可由请求失败固定事件直接定位 |
| **A** T50 注销界面报失败 | ✅ | 本提交 | Node 契约准确失败：`the authoritative projection must receive the full outer command budget` | 轮询预算由约 5 秒对齐为 60 秒；仓内 B5-13/B5-14 演示验收已包含安全注销步骤 |
| **B** T61 artifact 门禁降级为清理 | ✅ | 本提交 | 启动恢复断言准确失败：`restarted store: VideoWorkspaceError { code: StorageUnavailable }` | 启动时仅清理损坏/中断删除的 artifact，清理失败才阻断；运行期 `list_artifacts()` 继续严格失败，外部软链目标不受触碰 |
| **B** T65 `cleanup_expired` 无调用方 | ⬜ | | | |
| **B** T66b 目录权限只检查不修复 | ⬜ | | | |
| **C** T72 门禁执行者三处空洞 | ⬜ | | | |
| **C** T73 测试写进只读 vendor | ⬜ | | | |
| **D** T40/T41 字体权利登记 | ⬜ | | | |
| **E** T74 执行器缓存键 + 硬编码 `.local/` | ✅ | 本提交 | 缓存键测试准确失败：`source, spec and contract bytes must each select a different cached package` | 缓存键纳入 backend 源码、spec、锁文件及相关契约/只读资源摘要；浏览器归档统一走 `archive_path()`，T36 失效清理同步指向摘要键 |
| **E** T75 另一处吞掉 PyInstaller 输出 | ✅ | 本提交 | 构建失败测试准确失败：`PyInstaller stdout: missing hidden import` 与 `PyInstaller stderr: build traceback` 均不在异常中 | E4-07 及同类 E4-09/E4-10、Windows candidate 均携带 stdout/stderr 各自最后 20 行，空输出也给固定诊断 |
| **F** T26 剔除 Widevine CDM | ⬜ | | | 唯一有书面禁令的风险 |
| **F** T24 执行器包根按 `debug_assertions` 分叉 | ⬜ | | | |
| **F** T45 Control Plane 镜像进 playwright | ⬜ | | | 独占 `uv.lock` |
| **F** T38 演示后回收清单 | ⬜ | | | 纯文档 |
| **G** T76 `desktop-e2e` 入口让断言恒真 | ⬜ | | | 改完可能变红，那是真相浮出来 |
| **G** T77 B5-13 前端投影与权威态不一致 | ⬜ | | | 先复核再动手 |
| **G** T78 视频线 7 驱动缺 `prepare_startup_gate` | ⬜ | | | 依赖 T74 |

---

## 附录：两条不要碰的结论

**T26「内置浏览器换成 Chromium 开源构建」——任务标题的结论是反的。** 调研结论是**不换**，完整证据见 `docs/development/PLAN-chromium-replacement.md`：Playwright 1.61.0 在 macOS/Windows 上已停产 Chromium 构建（实测 rev ≥1210 是 404）；所有 Chromium 开源构建不含 H.264/AAC，实测抖音播放器彻底黑屏；体积不降反增且 Windows 超门禁上限；而且 **CfT 在 `sec-ch-ua` 里报的就已经是 `"Chromium"`**，换掉的只是文件名不是被网站看到的身份。唯一白纸黑字的分发禁令只针对 `libwidevinecdm.dylib` 一个文件（20.2 MiB），删掉零功能损失。正确任务是「留在 CfT + 剔除 Widevine」，但那会改变正式包内容、与主线 T10 冲突，**等 T10 收口后再做**。

**T67 Windows UNC/junction 不在这批**，因为 `browser_profiles_windows.rs` 是 `#[cfg(target_os = "windows")]`，在 macOS 上根本不参与编译，做不出「先看到失败的测试」这一步。必须在 Windows 机上做。
