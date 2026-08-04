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

## 扫描出的其余高风险项（登记，未做）

| 函数 | 坏掉的后果 | 为什么这轮没做 |
| --- | --- | --- |
| `rollback_committed_smart_edit` | 智能剪辑补偿失效：生成的旁白素材与 `generated-materials/<job>` 目录双双泄漏 | 见下「为什么不是顺手就能补」 |
| `dispatch_submitted_job` / `fail_submitted_job` | 剪辑任务派发与失败终态；后者坏掉意味着失败的任务**永远停在进行中** | 两者都吃 `tauri::AppHandle`。本 crate 没有开 `tauri` 的 `test` feature，全仓也没有任何 `mock_builder` / `mock_app` 用法——要补就得先加这项 dev 依赖并定下 App 夹具的做法 |

### 为什么 `rollback_committed_smart_edit` 不是顺手就能补

它是 `pub(crate)`，集成测试看不见；而 in-src 测试模块里没有 Worker 夹具——那套
（签名的 Python Worker、`TemporaryWorker`、`editing_launch`）整个住在
`tests/local_video_orchestrator.rs` 里。三条路都不是白拿的：

1. **放宽成 `pub`**：它的四个同族方法（`commit` / `abort` / `emergency_stop` /
   `finish`）确实都是 `pub`。但看清楚区别——那四个是 Tauri Command 从 `lib.rs` 调的，
   而它只被 `smart_edit_runtime` 调，跟同样是 `pub(crate)` 的 `smart_edit_job_owner`、
   `worker_uses_script_model`、`local_editing_job_owner` 同一类。**按调用方划分是对的，
   为了测试把它挪到另一类不对。**
2. **把夹具抄进 in-src**：约 250 行，从此两份要跟着协议一起改。
3. **下沉成共享夹具**：`#[cfg(test)]` 的模块集成测试看不见，要让两边都用就得开
   feature——那正好撞上「单一构建路径」不许用 feature 改变产品行为的那条。

所以这不是「还没排上」，是**一个需要先定的设计选择**。挑哪条路都要单开一个任务。

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
cargo test --lib                            164 passed
cargo test --test video_job_workspace        17 passed（原 15）
cargo test --test local_video_orchestrator   44 passed, 3 ignored（原 43）
cargo clippy --lib --tests                  零告警
cargo fmt -- --check                        干净
```

## 文档

- `frontend/src-tauri/src/executor_platform.rs`（App 退出两条测试 + 共用夹具）
- `frontend/src-tauri/tests/video_job_workspace.rs`（`remove_output` 两条测试）
- `frontend/src-tauri/tests/local_video_orchestrator.rs`（`finish_smart_edit_job` 一条测试）
- 本文件
