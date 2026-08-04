# App 退出交还运营档案：这条路径此前零覆盖（已补）

用户可操作：否

证据类型：分层实现

> 状态：✅ 已完成
>
> 日期：2026-08-04
>
> 提交：本文件所在提交

## 缺口是怎么找出来的

不是靠读代码猜，是靠一次机械扫描：把 `frontend/src-tauri/src/` 里所有
`pub` / `pub(crate)` 函数名，跟全部 `tests/*.rs` 加各文件的 in-src 测试模块做集合差。
**46 个公开函数的名字在任何测试里都没出现过。** `shutdown_for_app_exit` 是其中之一，
逐个核实后它排在最前面——理由见下。

（这个判据是名字级的近似：一个函数可能被间接走到而名字不出现。所以扫描结果只用来
排优先级，每一条仍然单独核实。`shutdown_for_app_exit` 实测确实只出现在 `lib.rs:5046`、
`lib.rs:5944` 和 `app_update_installation.rs:160` 三处生产调用点。）

## 为什么是这一条

它管的是**用户退出 App 时把运营档案的锁交还**。这条路径坏掉的后果不是报错，是
**下一次启动每个抖音按钮都报「档案正被占用」，而这台机器上已经没有任何东西还持有它**，
用户只能靠安全注销脱身。

这不是假想。`executor_platform.rs:789` 的注释记着 PC-25 那次：不杀执行器的安全注销
删 Profile 时撞上自己的锁，b5_13 实测 `profile_in_use` ×7。同一类事故，同一个锁。

而它的防线全在一个容易被"简化"掉的形状里：

```rust
let stop = self.manager.stop().map_err(map_manager_error);
let release = self.release_platform_profile();      // ← 不管上面成没成，都要跑
match (stop, release) {
    (Ok(_), Ok(())) => Ok(()),
    (Err(error), _) | (_, Err(error)) => Err(error),
}
```

改成 `self.manager.stop()?` 再释放，读起来更顺，**顺路径上也完全看不出区别**——
只有执行器真的停不下来的那一次才显现，而那正是最需要它管用的一次。

## 补了什么

`frontend/src-tauri/src/executor_platform.rs` 的 in-src 测试模块，两条：

| 测试 | 前提 | 断言 |
| --- | --- | --- |
| `app_exit_stops_the_executor_and_gives_the_operations_profile_back` | 执行器正常 | 退出返回 Ok、管理器为 `Stopped`、档案可再租 |
| `app_exit_gives_the_profile_back_even_when_the_executor_cannot_be_stopped` | 执行器不告而别 | 退出返回 `ProcessUnavailable`，**档案照样可再租** |

两条都用**真实执行器进程**（开发签名者签名的 Python 桩，包必须真的通过验签才起得来）
和**真实 Profile**，租约由一次真实的发布预检留住——租约只有在命令成功且状态要求留住
浏览器时才存活，用替身就等于自己编造前提。

**判据是「能不能再租一次」，不是那个 `Option` 是不是 `None`。** 这两件事在 PC-25 那次
正好分开过：内存状态看着干净，磁盘上的锁标记还在。所以断言走
`open_douyin_profile(id).try_acquire_owned_lock()`，让真实的锁去回答。

### 让「停不下来」这个前提便宜地成立

第二条需要 `manager.stop()` 失败。现成的做法是让桩忽略 SIGTERM 再等停止超时——
但 `ExecutorPlatformService` 把停止超时写死成 10 秒，一条测试就要 10 秒。

改用 `receive_stop_confirmation` 的另一条失败路径：桩在 stdin 关闭后**直接退出、
不发 `executor.stopped`**，事件通道随即断开，`RecvTimeoutError::Disconnected` →
`ProcessUnavailable`，毫秒级返回。两条测试合计 1.1 秒。

这个形状也更贴近真实：执行器崩过之后用户再退出 App，本来就没人会发那句告别。

## 变异确认非空

每条都实跑确认红，且**红在不同的地方**：

```text
✅ 被抓住  stop()? 提前返回，档案不放   → 只有第二条红（第一条照常绿——正是这个盲区）
✅ 被抓住  根本不释放档案               → 两条都红，报 ProfileInUse
✅ 被抓住  根本不停执行器               → 两条都红，第一条报 Running != Stopped
```

第一条变异的结果最能说明问题：**顺路径测试对它完全无感**。只写正常退出那一条，
等于给这个缺口发了一张通行证。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 正常退出，执行器在跑，档案租着 | 都停都放 | ✅ 本次 |
| 执行器停不下来 | 仍放档案，错误照报 | ✅ 本次 |
| 退出时没有租约 | `release_platform_profile` 取到 `None`，直接 Ok | 已有（`take()` 的空分支） |
| 释放锁本身失败 | 错误沿 `map_profile_error` 返回 | 未单独覆盖，见下 |

## 仍未覆盖

- **释放锁本身失败**（磁盘只读、锁文件被外部删除）：要造这个前提得在测试里破坏锁文件，
  而破坏方式本身就是对实现的假设。登记，不在本次做。
- **`emergency_stop` 里同一个 `match (result, release)` 形状**（`executor_platform.rs:552`）：
  与本次这条同源，同样值得一条错误路径测试。`emergency_stop` 的名字在测试里出现过，
  但走的是不是这条分支没核实。登记。
- 扫描出的另外 45 个未命名函数：本次只处理了风险最高的一个，清单在下节。

## 用投毒把「名字没出现」变成「真的没被走到」

名字扫描只能排优先级。判定必须靠**投毒**：在函数体第一行插
`panic!("MUTANT::<名字>")`，跑一次全量 `cargo test`，看哪个 MUTANT 真的炸出来。

六个候选一次跑完，结果：

| 函数 | 判定 |
| --- | --- |
| `read_rendered_video_artifact` | **假阳性**，3 条测试实际走到 |
| `rollback_committed_smart_edit` | 真无覆盖 |
| `finish_smart_edit_job` | 真无覆盖 |
| `dispatch_submitted_job` | 真无覆盖 |
| `fail_submitted_job` | 真无覆盖 |
| `remove_output` | 真无覆盖 → **本轮补齐** |

读日志时有个坑值得记：`panic!` 让后面的代码不可达，rustc 会为每个被投毒的函数
各打一条 `unreachable expression` 警告，**警告里会原样印出 `MUTANT::<名字>`**。
所以 `grep -c MUTANT` 得到 9，其中 6 条是编译警告、只有 3 条是真的 panic。
按出现次数数会把六个全判成「有覆盖」。

`cached_executable_still_sound` 同样是假阳性，由
`executable_removed_after_caching_is_detected_on_next_resolve` 间接覆盖——它不在这次
投毒批次里，是单独读测试确认的。

`*_for_acceptance` 系列不在候选里：它们由验收驱动在运行期真实走过，只是名字不出现在
测试源码。

## 本轮第二条：`remove_output`（已补）

它的用户可见后果藏在唯一调用点 `motion_video_studio::import_rendered_output` 里：
先导入成片，再删掉工作区里那份原件；**删不掉就把刚导入的 Artifact 也删掉并返回失败**。
一坏，用户什么成片都拿不到，报的还是存储错误，指不到真正的原因。

`frontend/src-tauri/tests/video_job_workspace.rs` 新增两条：

| 测试 | 断言 |
| --- | --- |
| `removing_a_worker_output_leaves_the_imported_artifact_and_reports_a_second_removal` | 原件删掉、`outputs/` 目录还在、**已导入的成片仍可读回原字节**、再删一次报 `NotFound` |
| `remove_output_refuses_escaping_names_links_and_directories_without_touching_the_target` | 逃逸名、软链、目录一律 `PathRejected`，且 outputs 之外的文件一个没少 |

### 诱饵放在逃逸名真正会落到的位置

第一版只断言「返回了 `PathRejected`」，这**不够**：拿掉名字校验后 `../x` 也可能因为
那里恰好没有文件而回 `NotFound`，看着照样像被拦住了。第一次跑变异时看到的正是
`left: NotFound / right: PathRejected`——判据分辨的是错误码，不是「文件还在不在」。

改成把诱饵写到每个逃逸名真正会落到的路径上：`../` 落在工作区目录、`../../` 落在
jobs 目录、绝对路径落在本次临时目录（`Path::join` 遇到绝对路径**整段替换**，名字校验
是那里唯一的防线；不拿系统文件做实验）。改完再跑同一条变异，报的就是
**「`../escape-one.mp4` 必须被拒」——它被接受了，那个文件真的会没**。

### 变异确认非空

```text
✅ 被抓住  去掉文件名校验        → 逃逸名被接受，工作区外的诱饵会被删
✅ 被抓住  去掉软链/目录/不存在判定 → 软链与目录被当成产物
✅ 被抓住  报告成功但什么都不删    → 「原件应当已被删除」
```

## 本轮第三条：`finish_smart_edit_job`（已补）

它管的是 Worker **认账地说提交失败**之后的收尾。那条路上没有别人会做事：

- Worker 因为提交没成功，没有清理自己的私有 job 目录；
- App 这边 `running.smart_edit_job` 还占着，而 `start_smart_edit_job` 见到
  `smart_edit_job.is_some()` 直接拒。

所以一坏，用户看到的是「这次剪辑失败了，而且**再也剪不了第二次**」，同时私有暂存
目录留在磁盘上。这两件事都不会报错，只会表现成「智能剪辑坏了」。

夹具由顺路径的 `smart_edit_worker()` 改一处得到：提交时回认证过的
`worker.smart_edit.failed` + `commit_failed`，并且**不**清理私有目录（提交都没成功，
它本来就不会清）。改完加一条 `assert!(!worker_source.contains("worker.smart_edit.succeeded"))`
守着——替换字符串一旦对不上，测的就是另一件事了，而它照样会绿。

断言按用户拿得到的东西排：提交被拒 → 私有目录还在（前提成立）→ 换个 job 标识收不了
这一个 → 此时第二次剪辑仍被拒 → 收尾 → 目录没了 → **能再剪一次**。

变异确认非空，三条各自红在不同断言：

```text
✅ 被抓住  清目录但不放位置   → 「能再剪一次」那条
✅ 被抓住  放位置但不清目录   → 「私有暂存目录必须被收掉」
✅ 被抓住  不核对是哪个 job   → 「别的 job 不能收这一个」
```

## 本轮第四条：`rollback_committed_smart_edit`（已补）

### 先更正上一版这份文档里的一个错判

上一版写着「不能放宽成 `pub`，因为那四个同族方法是 Tauri Command 从 `lib.rs` 调的，
而它只被 `smart_edit_runtime` 调」。**这条规则实测不成立。** 逐个查调用方：

```text
commit_smart_edit_job          smart_edit_runtime.rs
abort_smart_edit_job           smart_edit_runtime.rs
emergency_stop_smart_edit_job  smart_edit_runtime.rs
finish_smart_edit_job          smart_edit_runtime.rs
start_smart_edit_job           smart_edit_runtime.rs
rollback_committed_smart_edit  smart_edit_runtime.rs
```

**六个方法调用方完全一样，`lib.rs` 一个都没调**，五个是 `pub`、一个是 `pub(crate)`。
所以那个 `pub(crate)` 不是按调用方划分的设计，就是漏了——它是这一族里唯一一个从没被
测试走到的，可见性也是这一族里唯一一个不同的，两件事同源。

这个错判是怎么来的：我按「`pub` 的多半给 Command 用」这个印象推了一条规则，没有去
数调用方。**规则要么有据可查，要么就别当成理由写进文档。**

改成 `pub` 之后 RED 是编译错误（集成测试看不见私有方法），改完 GREEN。

### 补了什么

`frontend/src-tauri/tests/local_video_orchestrator.rs` 两条，用现成的
`material_worker` / `editing_launch` 夹具：

| 测试 | 断言 |
| --- | --- |
| `rolling_back_a_committed_smart_edit_forgets_materials_and_removes_the_durable_directory` | 非 v4 标识、超过 32 个素材一律拒，且**被拒时一个字节都不动**；顺路径清掉耐久目录 |
| `a_material_that_cannot_be_forgotten_does_not_stop_the_rest_of_the_rollback` | 第一个素材忘不掉时，**其余素材照样忘、目录照样收**，最后才报 `ProcessUnavailable` |

补偿代码有个共同特点：**它只在别的地方已经出错时才跑**，所以它自己坏掉没人看得见——
表现出来只是磁盘上慢慢多出一堆没人认领的素材和目录。

三条变异各自被抓住：

```text
✅ 被抓住  循环里改用 ?，第一个失败就返回  → 只有第二条红（顺路径照常绿，又是那个盲区）
✅ 被抓住  不收耐久目录                    → 两条都红
✅ 被抓住  去掉素材标识与长度校验          → 第一条红
```

## 本轮第五条：`local_editing_runtime` 的纯决策（已补）

### 先纠正一个观察错误

扫描时我按 `tests/` 目录грep，得出「这个模块 20 个函数零测试引用」。**不对**：文件末尾
有 in-src 测试模块，`cancel_reconciliation_required` 已被完整覆盖。差点因此写了重复用例。

而这个事实恰好指出正确做法：**取消那条决策已经被抽成纯谓词并测过，失败那条还内联在
`fail_current_job` 里**。补齐对称是这个文件自己立的规矩，不是为测试改结构。

### `dispatch_submitted_job` / `fail_submitted_job` 不补单测，理由

它们是活 Control Plane 之上的**组合根**：从 App 取六份状态，再跑一整条异步流程。
而它们**已经在真实 App 上被走过**——`frontend/e2e-tauri/video-editing.spec.ts` 与
`smart-edit-package.spec.ts` 点「提交剪辑任务」正是走 `lib.rs:3188 → dispatch_submitted_job`，
LE-17 还落了真实 PostgreSQL 行并用 `ffprobe` 核了终态。

给它们造 `mock_builder` + 假 Control Plane 的 harness，断言的大部分会是 harness 自己。
**组合根的正确覆盖层次就是端到端**；单测该覆盖的是它里面那些一次 E2E 只会走到一条分支
的决策表。

### 补了什么

| 测试 | 它防的事 |
| --- | --- |
| `only_an_unfinished_job_is_marked_failed_and_a_failed_one_is_idempotent` | `Failed → Ok(false)` 是**幂等**而不是错误——弄反了，一次重试就把正常路径变成错误路径；已成功/已取消要拒，否则会改掉用户已经看到的结果 |
| `every_local_failure_code_keeps_its_own_meaning_upstream` | 八条映射逐条钉死 + **单射**。编译器保证这张表是全的（无 `_` 分支），保证不了它是对的：把「字体缺失」映成「渲染失败」照样编译过，用户看到的是一条查不下去的原因 |
| `a_job_request_is_parsed_only_from_canonical_identifiers_and_a_usable_revision` | 三段不同上界叠在一起的边界（快照 `u64` → 请求 `u32` → 只收 `1..=i32::MAX`） |

同时把 `fail_current_job` 里内联的状态判断抽成 `failure_reconciliation_required`，
与同文件已有的 `cancel_reconciliation_required` 对称。行为不变，TDD 顺序是先写测试
（RED 是编译错误：函数不存在）再抽。

### 变异确认：第四条第一次跑活下来了，那才是重点

```text
✅ 被抓住  字体缺失映成渲染失败
✅ 被抓住  已失败的任务再收一次改成报错
✅ 被抓住  已成功的任务也允许标失败
❌ 活下来  u32::try_from 换成 as u32     ← 第一次
```

**`as u32` 是截断，不是拒绝**，而我原来选的三个「过大」值截断之后恰好都还是非法的：
`u32::MAX + 1` → 0，`u64::MAX` → `u32::MAX`（> `i32::MAX`），下游的
`VideoWorkerLocalEditingJobRequest::new` 照样拦。于是「上界还检查着吗」这个问题**根本
没被问出来**，三个用例全在问别的。

补上 `u32::MAX as u64 + 6`：截断后是 5，一个**合法**的 revision。有了它，变异下
`parse_job_request` 会**成功**返回 revision 5 —— 一个 revision 高得离谱的任务被当成
第 5 版渲染，全程不报错。加上这一个之后 M4 立刻红。

这正是 `memory/mutation-sets-miss-boundary-endpoints` 记的那条：**「全杀」属实，但集合
不完整。** 选端点时要问的不是「这个值大不大」，而是「**这个值能让变异体给出与原实现
不同的答案吗**」。

## 扫描出的其余项（登记，未做）

| 函数 | 现状 |
| --- | --- |
| `dispatch_submitted_job` / `fail_submitted_job` | 见上：覆盖层次在端到端，不补单测。若将来要补，前置是本 crate 开 `tauri` 的 `test` feature 并定下 App 夹具做法——全仓目前没有任何 `mock_builder` / `mock_app` 用法 |
| `monitor` / `settle_worker_lost` / `find_project` / `load_materials` 等 | 同上，都是活 Control Plane 之上的异步组合 |

## 清理

测试自己起的执行器进程由 `shutdown_for_app_exit` 本身停掉（第二条那个不告而别的桩
由 `force_stop` 收走）；跑完复查 `automation-tool-executor` 进程数为 0、临时 App
数据目录为 0。未新增任何常驻服务。

## 顺带修掉

本模块的 `TemporaryAppData` 用未解析的 `std::env::temp_dir()`。Profile 目录逐级用
`O_NOFOLLOW` 打开，而 macOS 的 `$TMPDIR` 挂在 `/var` 这个符号链接下，未解析时 ELOOP，
报出来是 `UnsafeDirectory`——看着像权限问题，实际是路径没解析。
`tests/browser_profiles.rs` 早就在 canonicalize，本模块此前没用到 Profile 所以一直
没暴露。（该修复随上一条提交 `6056b8d1` 一并落地。）

## 验证

```text
cargo test --lib                            167 passed（原 161）
cargo test --test video_job_workspace        17 passed（原 15）
cargo test --test local_video_orchestrator   46 passed, 3 ignored（原 43）
cargo clippy --lib --tests                  零告警
cargo fmt -- --check                        干净
```

## 文档

- `frontend/src-tauri/src/executor_platform.rs`（App 退出两条测试 + 共用夹具）
- `frontend/src-tauri/tests/video_job_workspace.rs`（`remove_output` 两条测试）
- `frontend/src-tauri/tests/local_video_orchestrator.rs`（`finish_smart_edit_job` 一条、
  `rollback_committed_smart_edit` 两条）
- `frontend/src-tauri/src/local_video_orchestrator.rs`（`rollback_committed_smart_edit`
  改 `pub`，与同族五个方法一致）
- `frontend/src-tauri/src/local_editing_runtime.rs`（抽出
  `failure_reconciliation_required` + 三条决策测试）
- 本文件
