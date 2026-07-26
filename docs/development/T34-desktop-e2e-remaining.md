# T34 桌面 E2E 剩余失败逐条判定

> 状态：🔍 待验收（判定完成；两条稳定失败均未修产品代码，按要求只报告）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`docs/development/FIX-control-plane-e2e-final-failures.md` 之后，主对话记录
> control-plane 层「28 通过 / 4 失败」，需要逐条判定是测试过期还是产品缺陷，
> 下周客户演示前要知道哪些会被撞见。

## 0. 结论先说

**那个「4 条失败」的数字不成立，原因有两层。**

第一层：它测自一棵**被未提交改动遮蔽的工作树**。`main` 自 `c0cc760`（11:10）起
`tsc -b` 就编译不过，整层桌面 E2E 一个都构建不起来；主工作树看起来正常，
只因为修复正躺在某条并行工作线的未提交改动里（详见 §4）。

第二层：本轮在隔离 worktree 上实测，5 条失败里**只有 2 条是稳定失败**，
另外 3 条重跑 8 次里通过 7 次或 5 次（详见 §3）。

基线：`aa40c8c`（最后一个确认能编译的提交），隔离 worktree，31 个 driver
（`u9_06` 按约束排除），**26 通过 / 5 失败**。

| 驱动 | 稳定性 | 判定 | 演示风险 |
| --- | --- | --- | --- |
| D6-10 | 4/4 失败 | **测试期望过时**（产品行为变更且新行为正确） | 低 |
| B5-13 | 4 次运行 3 次失败 | **产品缺陷**：注销成功却报失败，未修 | **中高，在演示路径上** |
| H8-07 | 3/8 失败 | 用例预算与产品预算相等，无余量 | 低—中 |
| T3-18 | 1/8 失败 | 一次性连锁，未复现 | 低 |
| T3-15 | 1/8 失败 | 同上 | 低 |

产品代码一行未改。测试面只改了一处：给 B5-13 的注销等待补失败事实打印（§3.2）。

## 1. 基线与隔离

主工作树上同时有三条并行工作线在改代码，其中一条反复处于「改到一半、TS 编译不过」
的状态。11:46 第一次尝试时，`run_d6_10` 在 12 秒内假失败于
`beforeBuildCommand`——报错来自 `VideoStudio.tsx` / `VideoStudio.test.tsx`，
与被测对象无关。

因此改为隔离 worktree：

```text
git worktree add /Users/aventador/code/automation-tool/wt/e2e-diagnosis aa40c8c
```

`wt/` 写进 `.git/info/exclude`（不动共享 `.gitignore`）；独立 `node_modules`、
独立 `backend/.venv`、独立 `frontend/src-tauri/target`；`.local/desktop-e2e` 与
`.local/embedded-browser-video-studio/eb-03-cache` 从主树拷贝（内容按摘要校验，不重建）。
每个 driver 开跑前等 `tauri build` / `wdio run` 为空，从不终止、从不接管任何进程。

**为什么基线不是 HEAD**：见 §4，HEAD 编译不过。

## 2. 稳定失败逐条

### 2.1 D6-10：测试期望过时，产品行为正确

**症状**（4 次运行逐字一致）：

```text
Error: Competing Task did not show Installation busy: …平台状态…请在打开的运营浏览器中扫码登录。
[D6-10] platform health=[{'platform':'douyin','state':'missing','session_revision':1}]
        gates=[] attempts=[{…:'failed'}, {…:'failed'}]
```

**产品侧变更点**（这是判定依据，不是推断）：`b69f463`（2026-07-22）给
`start_task_discovery` 加了一步：

```rust
// frontend/src-tauri/src/lib.rs:2098
ensure_executor_running(&client, &vault, &platform).await…?;
client.start_task_discovery(&vault, &task_id, &idempotency_key).await
```

于是点「开始目标发现」时 App **先拉起自己的本机执行器**。该执行器的本地账本里没有抖音
会话，`ProductionDouyinDiscoveryOperation` 立刻返回 `login_required`，服务端把尝试置
`failed`。第一个尝试 0.2 秒内终结，Installation 的唯一活跃槽位随即空出——
**用户点第二下时，「设备忙」这个前提已经不存在了。**

而 D6-10 的验收架构（`1bd21cc`，2026-07-20）建立在「App 自己没有执行器、
驱动在进程内跑唯一那个执行器并注入确定性 operation」之上。产品变了，架构没跟上。

**为什么产品行为是对的：**

1. 服务端守卫顺序是刻意的——`task_discovery_repository.py:130-148` 先查
   `platform_session_gates` / `platform_session_health`，`health != "healthy"` 直接
   `TaskDiscoveryRejected`；活跃尝试的忙检查排在其后。没有登录态就不许发起目标发现，
   与 `CLAUDE.md` 第 5 节一致。
2. 前端把 409 映射成 `discovery_rejected` → `onPlatformLoginRequired()` → 跳「平台状态」页，
   即「先去处理登录」，对用户是正确指引。
3. **被测的那条规则本身在更低层已经验证过**：
   `backend/tests/integration/test_task_discovery_lifecycle.py:499` 断言 N 个并发启动里
   恰好一个得到 `TaskDiscoveryInstallationBusy`；
   `backend/tests/contract/test_task_discovery_api.py:165` 钉住
   `installation_task_active` 映射。D6-10 独有的价值是端到端用户可见路径，
   而失效的正是那条路径的**前提**，不是规则。

**结论：测试期望过时。** 与既有记录一致——
`FIX-control-plane-e2e-final-failures.md` §5 自己的分类就是
「产品行为变化使该验收架构失效」，而不是产品缺陷。

**未修的原因**：修复需要改 `scripts/run_d6_10_acceptance.py` 与新增
`backend/tests/fixtures/` 执行器 fixture（把 App 自己安装的签名执行器包换成确定性实现，
即本仓 B5-15 / H8-16F 已用两次的模式），两者都在本次作业面之外。

**演示风险：低。** 演示机会有真实抖音登录态，第一个发现会正常停在
`discovering_targets`，竞争点击才会真正撞上忙检查。但**这条端到端路径目前无人验证**，
属「未验证」而非「已知坏」。

### 2.2 B5-13：产品侧真实问题，在演示路径上

**症状**：3 次运行 3 次失败，且是**两种形态**：

| 运行 | 耗时 | 失败点 |
| --- | --- | --- |
| 全量 | 3m13s | spec：`safe logout did not render authoritative missing state` |
| 重跑 1 | 38s | spec **通过（9.6s）**，驱动后置断言：`B5-14 safe logout retained the current Profile marker` |
| 重跑 2 | 3m9s | 同全量 |

**已排除的两个可能：**

1. **不是 schema drift。** `logout_douyin_session` 返回
   `control_plane::PlatformSessionStatus { platform, state, observed_at }`，
   与 `platformSessionSnapshotSchema` 的三个 `.strict()` 键逐字对应，无多余字段。
   （这与 `FIX-platform-login-e2e-failures.md` 缺陷 2 的形态不同。）
2. **不是断言过时。** `PlatformSessions.tsx:13` 仍是 `missing: "需要登录"`，
   spec 等的就是产品当前文案。

**根因（本次用新增的失败事实打印一次拿到，不是推断）：**

给 spec 的注销等待补上失败事实打印后，第 4 次运行给出：

```json
{"authoritativeSession":{"observedAt":"2026-07-26T05:03:05.594233Z",
                         "platform":"douyin","state":"missing"},
 "logoutStillPending":false,"rendersMissing":false,"rendersHealthy":false,
 "rendersUnknown":true,"rendersReadFailure":true}
```

逐条读：

- `authoritativeSession.state == "missing"` —— **服务端权威状态已经注销成功**。
  登出闸、执行器停止、Profile 清理、`CompleteDouyinLogout` 全部生效了。
- `logoutStillPending == false` —— 按钮已恢复可用，命令**已经返回**，没有卡住。
- `rendersReadFailure == true` —— 页面显示的是
  「暂时无法读取抖音登录状态，请稍后重试。」，即 `PlatformSessions.tsx:115-127`
  的 `catch` 分支被触发：**`gateway.logoutDouyinSession()` 抛了异常。**
- `rendersUnknown == true` / `rendersMissing == false` —— 状态卡仍停在旧的「尚未确认」，
  始终没有变成「需要登录」。

**也就是说：注销真的成功了，App 却告诉用户它失败了。**

抛异常的地方在 `logout_douyin_session` 的收尾轮询（`lib.rs:1198-1211`）：

```rust
for _ in 0..100 {
    let snapshot = client.get_douyin_platform_session(&vault).await…?;
    if snapshot.state() == "missing" { return Ok(snapshot); }
    tokio::time::sleep(Duration::from_millis(50)).await;
}
Err(ExecutorPlatformCommandError { code: "timed_out", retryable: true })
```

**100 次 × 50 毫秒 ≈ 5 秒**。权威状态的收敛要经过「执行器重启 → `CompleteDouyinLogout`
→ 服务端投影更新」，实测有时落在 5 秒内（重跑 1 整个 spec 9.6 秒走完、通过），
有时落在 5 秒外——落在外面就返回 `timed_out`，尽管状态紧接着就收敛了。

对照之下，包住这条命令的外层预算是
`executor_manager.rs:28  PLATFORM_COMMAND_TIMEOUT = 60s`。
**内层预算比外层紧一个数量级**，这是问题的形状：不是「等不起」，是自己先放弃了。

第二重问题：`catch` 分支只 `setFailure(true)`，**不重新拉一次权威状态**，
所以页面此后一直停在旧快照 +「暂时无法读取」，直到用户手动重新检查或离开重进。
权威状态明明就在那儿、就是 `missing`。

**这是产品缺陷，不是测试过期。** 与 `FIX-platform-login-e2e-failures.md` 缺陷 2 同类：
底层做成了，App 把成功报成失败。

**产品侧顺序**（供修复参考）：`logout_douyin_session`（`lib.rs:1143`）是

```text
1. prepare_douyin_platform_session_logout   服务端登出闸
2. service.emergency_stop()                 停本机执行器
3. profiles.remove_current_douyin_profile() 删 Profile 与标记   ← 同步、返回前完成
4. issue_executor_connection + restart      重新拉起执行器
5. CompleteDouyinLogout                     要求 state == "logged_out"
6. 轮询 get_douyin_platform_session 最多 100 次
```

第 3 步在命令返回前同步完成，所以「标记还在」不是这一步漏了。而
`current_douyin_profile()`（`browser_profiles.rs:134-154`）在**没有存量标记时会新建
Profile 并写回标记**，它可从 `open_douyin_login` / `recheck`（`lib.rs:1079`）和
`begin_publish`（`lib.rs:1304`）到达。也就是说「注销后不存在 Profile 标记」这个不变量，
产品只在**没有后续动作**时成立；驱动断言的是全局不变量，产品保证的是瞬时状态。

需要说明确定性边界：**我没有证明重跑 1 里究竟是哪一次调用重建了标记**，
这是机制候选而非已证结论。它与上面那条 5 秒预算的主因是两件事。

### 复现步骤

1. 平台状态页，抖音处于已登录（`healthy`）；
2. 点「安全注销」→「确认注销」；
3. 等约 5 秒。

**期望**：状态卡变成「需要登录」。
**实测（4 次里 3 次）**：状态卡停在原状态，页面弹出
「暂时无法读取抖音登录状态，请稍后重试。」，而此时服务端权威状态已经是 `missing`。
再次点「我已处理，重新检查」可以把页面拉回正确状态——**数据没坏，只有这一屏在骗人**。

### 演示风险：中高

「安全注销」是设置/平台状态页上的用户按钮，演示大概率会经过这一屏。
撞上的概率量级：本轮 4 次运行 3 次复现（约 75%），且与机器负载无关——
它取决于注销收敛能否挤进 5 秒。演示机若网络更慢，只会更容易撞上。

后果不是数据错误，是**信任**：客户看到「注销失败」，实际已经注销；
操作者很可能反复点击。

**建议**：演示脚本先不包含「安全注销」。修复方向（成本很低，但在本次作业面之外）：
把收尾轮询的预算提到与外层 `PLATFORM_COMMAND_TIMEOUT` 同量级，
并让失败分支重新拉一次权威状态而不是只 `setFailure(true)`。

## 3. flake 根因判定：不是同一个原因

对三条非稳定失败各重跑 5 次，并在每次开跑前采样 1 分钟负载与外部构建进程数。

### 3.1 数据

| 驱动 | 总计 | 失败 | 失败时负载 | 通过时负载 |
| --- | --- | --- | --- | --- |
| T3-18 | 8 | 1 | 全量那次（未采样） | 2.75 – 3.29 |
| T3-15 | 8 | 1 | 全量那次（未采样） | 2.79 – 3.48 |
| H8-07 | 8 | 3 | 2.78 | 2.58 / 3.10 / 3.22 / **4.03** |

### 3.2 判定

**H8-07 → 偏 (B)，但不是「产品不稳」，是「用例预算等于产品预算，零余量」。**

失败与负载**不相关**：它在观测到的**最高**负载 4.03 通过，在 2.78 失败；
外部构建数为 3 时既通过也失败。失败率约 37%，与机器忙闲无关。

机制侧数字对得上：驱动的 `wait_for_server_recovery`
（`scripts/run_h8_07_acceptance.py:306`）给的期限是 **30 秒**；而执行器自己的重连预算是
`restart_reconnect_attempts=120 × restart_reconnect_delay=250ms`
（`backend/src/automation_tool/executor/runtime.py:234-235`）= **恰好 30 秒**。
用例只有在恢复远未用满自身预算时才可能通过。

需要说明确定性边界：**两个 30 秒相等是强提示，但我没有实测执行器在失败那次真的烧掉了
重连预算**，没有插桩到那一层。

**演示风险：低—中。** 产品行为在设计预算之内：断网后重连最长 30 秒。演示中若网络抖动，
会看到最多半分钟的「恢复中」，可恢复、不致命。**加固建议**（成本可控，但都在
`scripts/` / `backend/`，本次作业面之外）：把用例期限提到产品预算的 2 倍（60 秒），
使其断言「能恢复」而不是「能在恰好等于预算的时间内恢复」。

**T3-18 / T3-15 → 偏一次性连锁，非负载泛化。**

两条都只在全量那一轮失败，且是**相邻的第 4、第 5 个**；此后各 7 次全部通过，
负载区间与失败那次没有系统性差别。失败形态也指向连锁而非产品：
T3-18 是 `WebDriverError: ECONNREFUSED`（App 中途退出），
T3-15 是启动门禁 `桌面运行环境需要处理`——后者是**确定性判定**（校验内置浏览器发行物
与执行器包），负载不改变它的结论，只有被测环境被动过才会变。两个 driver 共享
`target/debug/embedded-browser` 这一份暂存资源。

**排除了「App 崩溃」：** `~/Library/Logs/DiagnosticReports/` 里
`automation-tool-desktop` 的崩溃报告最近一份是 09:57，本轮全量（11:58–12:20）与全部
重跑期间**一份都没有新增**。所以 T3-18 的 App 不是 panic、不是被 OOM 干掉，是自己退出的。

需要说明确定性边界：**我没有复现这次连锁**，因此「谁动了暂存资源」只有指向、没有定论。

**对 (A) / (B) 的总回答：** 这三条不能一并归因。H8-07 与负载无关，是用例设计无余量；
T3-18 / T3-15 是一次性连锁，8 次里只出现 1 次，且不随负载重现。
**没有证据表明产品本身启动不稳**——单跑一个 App 的客户机不会遇到这三种形态。

## 4. main 编译不过，40 分钟无人发现

### 4.1 事实

| 提交 | 时间 | 隔离 worktree 上 `npx tsc -b` |
| --- | --- | --- |
| `aa40c8c` | 10:52 | ✅ exit 0 |
| `c0cc760` | 11:10 | ❌ exit 2，6 个错误 |
| `46d4770` | 11:2x | ❌ 同样 6 个 |

`c0cc760` 把发布口类型改了（`PublishRequest` 的 `publishJobId`+`artifactPath` →
`artifactId`；`PublishApprovalRequest` 只剩 `confirmationId`），同步了 Rust、页面和
`e2e-tauri/publishing.spec.ts`，**漏了两个消费方**：

```text
src/platform/tauri/publish-workspace-gateway.ts(38,31): Property 'publishJobId' does not exist on type 'PublishRequest'.
src/platform/tauri/publish-workspace-gateway.ts(39,31): Property 'artifactPath' does not exist on type 'PublishRequest'.
src/platform/tauri/publish-workspace-gateway.ts(50,31): Property 'publishJobId' does not exist on type 'PublishApprovalRequest'.
```

影响不止 E2E：每个 driver 的构建都是 `tsc -b && vite build`，`tsc` 挂了就
`beforeBuildCommand failed`，**整层桌面 E2E 在 HEAD 上一个都跑不起来**；正式包同理。

### 4.2 为什么没被发现

**不是「没有门禁」，是门禁在够不着的地方，而且够着了也跑不了。**

1. **门禁存在**：`.github/workflows/quality.yml` 在 `push: branches:[main]` 与
   `pull_request` 上跑 `pnpm typecheck`（= `tsc -b --pretty false`）。
2. **它没见过这个提交**：`git ls-remote origin main` = `d8cc304` = 本地 `origin/main`，
   而 `git rev-list --count origin/main..HEAD` = **35**。最近 35 个提交一个都没推送。
   工作节奏是「本地连续提交、稍后再推」，门禁躺在 `git push` 的另一侧。
3. **本机验证验错了对象**：提交者本地 `tsc -b` 是通过的——因为修复就在他工作区里，
   只是没 `git add`。**在工作树上验证，验的不是即将提交出去的那棵树。**

第三点是整件事的核心，也解释了为什么 **pre-commit hook 同样挡不住**：
它跑在工作树里，跟被遮蔽的那次本机验证是同一个对象。

### 4.3 门禁应该长什么样

硬判据只有一条：**在从某个提交 checkout 出来的干净树上跑**，
`git worktree add --detach <tmp> <commit>` 或等价物，不在工作树上跑。

范围必须含 Python。今天第二起事故（`run_bm_05_acceptance.py` 调
`lint_composition` 少传 keyword-only 的 `entry_path`）前端门禁管不到，而
**现有 Python 静态检查也管不到**：`backend/pyproject.toml` 的
`[tool.mypy] files = ["src", "tests"]` **不含 `scripts/`**，
`ruff --select F,E9` 没有类型推断、无法发现缺失关键字参数。
而 `lint_composition` 本身是完整标注的：

```python
# tools/motion-authoring/motion_authoring_agent.py:650
def lint_composition(html: str, *, allowed_assets: frozenset[str],
                     max_bytes: int, entry_path: str) -> LintResult:
```

所以 mypy 只要指过去就能抓到。要指对，`MYPYPATH` 必须覆盖 `scripts/` 里
**全部** `sys.path.insert` 的静态目标（实测扫描结果）：

```text
backend/src   scripts   tools/browser-use-contract
tools/motion-authoring   workers/material_montage
```

**已知且必须写明的洞**：`run_bu_02_acceptance.py` / `run_bu_07_acceptance.py`
从环境变量（`BU02_HARNESS_DIR`、`BU07_HARNESS_DIR` / `_SCRIPTS_DIR` / `_BACKEND_SRC`）
插入运行期才存在的目录，静态检查覆盖不到。这是命名的洞，不是静默的洞。

分两半、两档的具体设计与实现属 `T43-commit-gate`，本文件不展开。

## 5. 共享工作树被并行工作线破坏

**发现时间**：11:46，第一次全量的第 1 个 driver。

**现象**：`run_d6_10` 12 秒内失败，栈顶是「App exited before creating its Task」，
真正原因在更上面——`beforeBuildCommand` 里 `tsc -b` 报
`VideoStudio.test.tsx(501,49)` 与 `VideoStudio.tsx(1012,16)` 两个错误，
都属另一条工作线**当时正在编辑、尚未提交**的文件。

**影响面**：这类污染对**每一个** driver 等价——它们共用
`pnpm build:tauri:* → tsc -b && vite build`。任何一条工作线把前端改到编译不过，
整层 31 个 driver 全部在几十秒内假失败，且栈顶信息与被测对象无关，极易被读成产品回归。

**隔离之后是否消失：是，彻底消失。** worktree 里 31 个 driver + 10 次重跑 + 15 次
flake 探针，共 56 次运行，**没有再出现过一次构建期假失败**；`aa40c8c` 的 `tsc -b`
全程 exit 0。剩下的失败全部落在被测对象自己身上。

**沉淀成规矩的建议**：凡是要对「某个提交」下结论的验证（E2E、门禁、打包、
性能对比），一律在 `git worktree add --detach <项目根>/wt/<名称> <commit>` 出来的
干净树上跑；主工作树只用于开发。代价实测：`pnpm install --frozen-lockfile` 3.3 秒
（有 store 缓存）、`uv sync --locked` 数十秒、Rust 首次全量约 50 秒—数分钟，
之后增量约 3 秒。`.local` 大件（内置 Chromium 发行物 521 MB、Chromium 归档 171 MB）
直接从主树拷贝，不重建。

## 6. 真实边界

1. **两条稳定失败都没有修产品代码**，按要求只报告。
2. **B5-13 主因已定位到代码行**（`lib.rs:1198-1211` 的 5 秒轮询预算），
   但**未实测「把预算调大就通过」**——修改产品代码在本次作业面之外。
   次要形态「Profile 标记重现」仍只到机制候选层，没有实证。
3. **H8-07 的「两个 30 秒相等」没有插桩证实**执行器确实烧满了重连预算。
4. **T3-18 / T3-15 的连锁没有复现**，「谁动了共享暂存资源」无定论。
5. **U9-06 全程未运行**（其驱动会清空真实 App 数据目录，内含用户手工扫码的抖音登录态）。
   因此本轮是 31/32，不是 32/32。
6. **基线是 `aa40c8c` 而不是 HEAD**，因为 HEAD 编译不过。`c0cc760` 之后的发布口类型
   改造、字体替换未纳入本轮验证。
7. **Windows 侧完全未执行**，本机是 macOS。
8. **B5-13 触达真实 `douyin.com`**（headless，仅打开登录页读取二维码状态，
   不做任何第三方骚扰动作）。其耗时在 9.6 秒到 3 分 13 秒之间剧烈波动，
   真实网络是其中一个变量，未能与产品问题完全解耦。

## 7. 清理

- **`~/Library/Application Support/com.aventador.automationtool/` 全程未读未写。**
  本轮所有 driver 使用带任务后缀的独立 identifier；
  收尾核对该目录与另外两个不属于本次的隔离目录（`…pb07acceptance`、`…u904acceptance`）
  均未被触碰。
- Docker：每个 driver 使用 `automation-tool-<任务>-<pid>` 专属 compose project；
  收尾 `docker ps --filter name=automation-tool` 为空；
  机器上其他项目的 `agent-platform-dev-*` 容器全程未动。
- 进程：只终止本 sweep 自己启动的 driver 进程树；从不终止、从不接管其他工作线的
  `tauri build` / `cargo` / 远程 `docker build`。
- `frontend/dist` 开跑前已备份，主树未被本轮改写（全部构建发生在 worktree）。
- worktree `wt/e2e-diagnosis` 保留，供 §2 两条稳定失败的后续定位复用；
  `wt/` 已在 `.git/info/exclude`。

## 8. 文档

- `frontend/e2e-tauri/platform-session.spec.ts`（注销等待补失败事实打印）
- 本文件
