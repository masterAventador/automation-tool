# 查证：Windows 上「这个进程是不是我们这一轮起的」怎么答

用户可操作：否

证据类型：查证

> 日期：2026-08-05
>
> 提交：本文件所在提交

## 为什么查这个

EB-11 剩下的最大一块是进程层：快照、归属判定、正常退出、清零。它卡在一个具体问题上——
**Chromium 的 helper 会被重新挂到别的父进程下**，光靠进程树判断不出它属不属于本轮。

macOS 的答案是启动时塞一个 nonce 进环境变量，事后用 `ps eww` 把目标进程的环境读出来比对
（`process_has_launch_nonce`）。Windows 上有两条路，且**优劣不同**，所以两条都实测，
不凭印象挑。

选错的代价不是「慢一点」：`ProcessRecord` 有 10 处调用点依赖它的形状，选完再换要全改。

## 实测

### 路线一：读目标进程的 PEB（`ps eww` 的直译）

`NtQueryInformationProcess` 取 `PebBaseAddress` → `ProcessParameters`（x64 偏移 `+0x20`）
→ `CommandLine`（`+0x70`）与 `Environment`（`+0x80`，长度在 `+0x3F0`），
`OpenProcess` 只要 `PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ`。

```text
command line: …\python.exe -c "import time; time.sleep(20)" --probe-marker
--probe-marker visible:        True
nonce visible in environment:  True
environment blob chars:        5527
```

同用户进程可读，命令行与环境一次拿全。

### 路线二：Job Object（内核记账的归属）

把 App 放进 Job，之后问 Job 要进程列表（`JobObjectBasicProcessIdList`）。子进程无法退出
Job，重新挂父也带不走它。

```text
AssignProcessToJobObject:   True
QueryInformationJobObject:  True
中间父进程退出后，Job 仍列出：[15632, 12300]
被孤儿化的孙进程仍在册：      True
```

## 结论：走 PEB，Job 记为可选加固

**不是因为 Job 弱**——它的归属主张其实更强，是内核记的账，不靠一个可被复制的字符串。
是因为**命令行无论如何都得读**：

- `USER_DATA_DIRECTORY_PATTERN` 要从浏览器命令行里抠 `--user-data-dir`（两处调用点）；
- `command_runs()` 要判断某进程跑的是不是包里那个可执行文件（四处）。

Job Object **不提供命令行**。所以 PEB 读取是必需的；而它一旦存在，nonce 就顺带白拿，
`ProcessRecord` 的形状一个字段都不用动，10 处调用点全部照旧。

Job 可以以后叠加上去当加固（尤其是「退出后清零」那一步，问 Job 比按 nonce 扫全表更干脆），
**加它不需要改记录形状**——这正是先定这条的意义。

## 真实边界

- 只测了同用户、同架构（x64 → x64）的进程。跨架构（WOW64）读 PEB 偏移不同，本项目不涉及；
- 未测被保护进程 / 提权进程——EB-11 全程以普通用户跑自己起的 App，不涉及；
- **两条路线都只证明了机制可用，都还没接进 runner。** `process_snapshot()` 在 Windows 上
  仍未实现，本文件不改变 EB-11 的状态；
- Job 实测里父进程退出后列表有两项而非一项，没有深究——判据是「孤儿孙进程还在册」，
  这一条成立即可。

## 清理

探测进程已终止：`python`（本项目路径）0 个、`automation-tool-desktop` 0 个、
8765 端口无监听。

## 文档变化

本文件为新增。

## 遗留项

| 项 | 状态 |
| --- | --- |
| 用 PEB 实现 Windows `process_snapshot()` | 待办，本文件的直接下一步 |
| Job Object 加固退出清零 | 可选，不改记录形状，可后加 |
