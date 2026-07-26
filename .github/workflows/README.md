# 这些 workflow 当前一条都不会运行

2026-07-26 起，本仓库的 GitHub Actions 已在仓库设置里**禁用**（`actions/permissions` → `enabled: false`）。

## 为什么

它们从来就没运行过。查 `gh run list` 会看到从 2026-07-25 16:50 起每一次都是 failure，5 秒内退出，报错都是同一句：

```
The job was not started because recent account payments have failed
or your spending limit needs to be increased.
```

也就是说这些 workflow 一行代码都没执行过，而多个任务的台账里写着「已接进 CI 门禁」。**一道从未运行过的门禁比没有门禁更危险**——它让人以为有东西在守。

雪上加霜的是本地节奏：推送此前不是每个任务的默认动作，`git rev-list --count origin/main..HEAD` 一度到 38。就算 Actions 能跑，它也看不到那 38 个提交。今天两起「main 编译不过 / 脚本一跑就报错」的事故就是在这段窗口里发生的。

## 门禁现在由谁承担

| 范围 | 在哪跑 |
| --- | --- |
| 跨平台：`tsc -b`、Python 测试、`cargo test`、各 check 脚本 | Windows 验收机（`ssh winbox`）自建 runner |
| macOS 专属：codesign、公证、DMG、Tauri macOS 构建 | 本机，**干净 worktree**，不在工作树 |

硬判据：必须在**从某个提交 checkout 出来的干净树**上跑。今天两起事故本机全都看起来正常，因为未提交的修复正好遮住了问题；跑在工作树里的 pre-commit hook 同样挡不住。

见 `[T43]` 与 `docs/development/T43-commit-gate.md`。

## 这些文件为什么留着

它们是「这个项目该跑哪些门禁、在哪个平台跑」的完整清单，是自建 runner 的需求输入。**不要把它们当成正在生效的保障。**

## 如果以后恢复 Actions

先做两件事，否则会退回到今天这个状态：

1. 确认账单真的能跑起来 —— 推一个提交，看到 job 真的执行完，不是看到 workflow 文件存在；
2. 逐条核对这里定义的检查在自建 runner 上是否已经有了，避免两处重复维护、或者两处都以为对方在管。
