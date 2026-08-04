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

## 扫描出的其余高风险项（登记，未做）

按「坏掉之后用户会怎样」排的，不是按数量：

| 函数 | 坏掉的后果 |
| --- | --- |
| `rollback_committed_smart_edit` | 剪辑回滚失效，用户素材可能停在半提交状态 |
| `cached_executable_still_sound` | 内置浏览器缓存的完整性判据失守（供应链边界） |
| `dispatch_submitted_job` / `fail_submitted_job` | 剪辑任务派发与失败终态 |
| `read_rendered_video_artifact` / `remove_output` | 成片读取与删除 |

`*_for_acceptance` 系列不在此列：它们由验收驱动在运行期真实走过，只是名字不出现在
测试源码里。

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
cargo test --lib                      164 passed
cargo clippy --lib --tests            零告警
cargo fmt -- --check                  干净
```

## 文档

- `frontend/src-tauri/src/executor_platform.rs`（两条测试 + 共用夹具）
- 本文件
