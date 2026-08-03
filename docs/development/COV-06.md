# COV-06 平台尾项 129 点与最终完整性审计

用户可操作：否
证据类型：分层实现

> 状态：✅ 已完成。129 → **0**，全库 2,023 → **0**。
> 上游计划：`docs/development/2026-08-03-backend-coverage-debt-plan.md`
> 前置：[COV-05](COV-05.md)
> 分支：`coverage/backend-100`

## 1. 起点核对

| 模块 | 起点 | 现在 |
|---|---:|---:|
| `executor/platform_commands.py` | 37 | **0** |
| `control_plane/bootstrap/local_provisioning.py` | 21 | **0** |
| `executor/ledger.py` | 20 | **0** |
| `executor/__init__.py` | 12 | **0** |
| `executor/browser_use_safety.py` | 8 | **0** |
| `control_plane/application/__init__.py` | 5 | **0** |
| `executor/browser_surface_lease.py` | 5 | **0** |
| `control_plane/domain/__init__.py` | 4 | **0** |
| `executor/authentication.py` | 4 | **0** |
| `executor/cli.py` | 3 | **0** |
| `protocol/__init__.py` | 2 | **0** |
| `executor/side_effect_ledger.py` | 2 | **0** |
| `executor/browser_runtime.py` | 2 | **0** |
| `control_plane/__init__.py` | 2 | **0** |
| `executor/windows_candidate.py` | 1 | **0** |
| `control_plane/bootstrap/editing_timelines.py` | 1 | **0** |

## 2. 真实边界

### 2.1 五个包的懒加载 `__getattr__` 是有行为的代码

`executor`、`protocol`、`control_plane` 及其 `application` / `domain` 子包都
用 `__getattr__` 按需导入叶子模块——这是让 Python 3.11 视频 Worker 能只装载
自己需要那一支的机制。它们此前一点覆盖都没有，因为所有调用方都直接从叶子
模块导入。

补的是它们对外承诺的三件事：公开名解析后落成普通模块属性（第二次访问不再
走 `__getattr__`）、未公开的名报 `AttributeError`、`__all__` 与被搜模块不
一致时**不静默返回 None**。最后一条用 `monkeypatch.setattr(package,
"_PUBLIC_MODULES", ())` 制造。

### 2.2 平台命令操作此前只有登录那一半

`DouyinPublishPreflightCommandOperation` 的整条发布接缝——预检持窗、呈现
批准、派发帧按一次、按完作废已填表单——没有任何用例。新增
`test_douyin_publish_command_operation.py`，用真实的
`BrowserLaunchAuthority`、真实 `ExecutorLedger`、真实
`SideEffectConfirmationGate` 和一个走完「入口 → 上传 → 表单 → 作品列表」
的页面 Fake 把两帧跑通。

`handle` 里那句 `except PlatformCommandRejected: raise` 被删掉：try 体内没有
任何调用会抛它（浏览器授权与启动各自被就近接住），而它与下面那支产出的
异常类型相同，删掉不改变调用方看到的任何结果。

`handle` 里 `if command.executable_path is None or ...` 这段与模型校验重复，
但 `handle` 是公开方法，模型不是它唯一的入口——用
`PlatformCommand.model_construct(...)` 绕过校验构造一帧，从真实入口证明它
仍然被拒绝且浏览器没有被启动。

### 2.3 本地注册引导的三个平台

`local_app_data_directory()` 按 `sys.platform` 分三支。macOS 上另外两支只能
靠替换模块看到的 `sys` 对象来走（`_PlatformStub`），断言落在**返回的路径**
上而不是真实文件系统行为：Windows 缺 `APPDATA` 拒绝、Linux 有无
`XDG_DATA_HOME` 分别落在哪。

写一半的授权必须被删掉而不是留下——用 `os.fsync` 抛 `OSError` 制造，断言
目录里**什么都不剩**：App 会把一个截断的文件当成完整授权读。

### 2.4 台账里两处走不到的守卫

- `outbox_for_delivery` 的 `if last_ordinal <= 0: raise ValueError`：每页都按
  `ordinal > last_ordinal` 从零开始过滤，schema 又有 `CHECK (ordinal >= 1)`，
  返回行不可能不推进扫描。改成断言——真不推进会在这里死循环，没有可恢复
  状态。
- 同一段的 `delivered not in {0, 1}`：schema 有 `CHECK (delivered IN (0,1))`，
  但这条守卫防的正是**不是这个 schema 写出来的文件**。用
  `PRAGMA ignore_check_constraints = 1` 把行改坏，从真实入口证明扫描会拒绝。

### 2.5 副作用记录的形状闸

`LocalPublishDispatch.__post_init__` 把「哪些字段组合是可能发生过的」写死成
四种（prepared / dispatched / verified / uncertain），此前只有正向用例。补的是
八种不可能的组合：作业号不规范、内容摘要不是摘要、状态不在闭集、准备时刻
无时区、点击早于准备、结算前没有点击、版本号不对、重放标志不是布尔。

## 3. 失败矩阵

| 场景 | 结果 |
|---|---|
| 包里取一个没公开的名 | `AttributeError`，不导入任何叶子模块 |
| `__all__` 里的名没有任何叶子模块提供 | `AttributeError`，不返回 None |
| 发布命令帧绕过模型校验 | 拒绝，浏览器不启动 |
| 浏览器起不来 | 回执，执行器不退出 |
| 关不掉的浏览器 + 调用方要求关闭 | 拒绝（`close()` 抛） |
| 关不掉的浏览器 + 尽力而为 | 吞掉 |
| 契约文件三处都找不到 | 配置错误 |
| Windows 缺 `APPDATA` | 拒绝签发 |
| 授权写一半 | 删掉临时文件，拒绝 |
| 授权超出冻结预算 | 不落盘，拒绝 |
| 借用方失败上报两次 | 幂等，仍需一次回收 |
| 无人借用时上报失败 / 令牌不对 | 拒绝 |
| 派发令牌不是文本 | 拒绝 |
| 台账行被别的 schema 写坏 | 拒绝投递 |

## 4. 清理

新增用例全部使用 `tmp_path` 与内存 Fake；`operating.close()` 在每条用例末尾
调用，浏览器 Fake 的关闭次数被断言。没有新增长期运行的服务或端口。

## 5. 最终完整性审计

### 5.1 权威门禁

`backend/pyproject.toml` 的 `fail_under = 100` 与 `branch = true` 一字未动，
`omit` 仍只有 Linux 覆盖进程无法导入的 `executor/windows_acl.py`。

```bash
cd backend
uv run --frozen pytest tests -q -p no:randomly \
  --cov=automation_tool --cov-report=term-missing
```

```text
Name    Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------
TOTAL   32199      0   8154      0   100%

271 files skipped due to complete coverage.
Required test coverage of 100.0% reached. Total coverage: 100.00%
7327 passed, 21 skipped, 208 subtests passed in 585.12s (0:09:45)
```

- 语句 `32,199 / 32,199`，缺 0；
- 分支 `8,154 / 8,154`，缺 0，部分分支 0；
- 起点 `main@dbd56118` 为 `6,471 passed`、`94.992698%`、`2,023` 个覆盖点。
  测试数只增不减：`6,471 → 7,327`。

### 5.2 既有 `# pragma: no cover` 逐项审计

起点 49 处，本轮**未新增任何一处**，删掉 9 处，现存 **40** 处。

删掉的 9 处是同一形状：跟在返回 `Never` 的 `_reject()` 后面的
`raise AssertionError`，本来就是死语句（`agent.py` 七处、`voiceover.py`
一处）。`authoring_workspace.py` 那一处的 `_reject` 返回类型原本写成 `None`
——那正是断言存在的唯一理由——改成 `Never` 后一并删掉。这与本轮在
`silero_vad.py` 做过的处理同源。

现存 40 处的复核结论：

| 类别 | 处数 | 结论 |
|---|---:|---|
| 台账里 `BEGIN IMMEDIATE` 锁住同一行后的 `rowcount != 1` | 9 | **保留**。行在同一事务里选中并锁住，漂移不可能；它抛的是领域错误而不是 `AssertionError`，转成断言会在 `-O` 下削弱行为 |
| 打包器与 CLI 入口（`macos_candidate` 8、`windows_candidate` 6、`package_manifest` 4、`__main__` 3、`browser_runtime._start_playwright` 1） | 22 | **保留**。由真实子进程与真实出包验收，本来就不走进程内覆盖；把它们拉进覆盖等于在单元测试里跑 PyInstaller |
| B 站发布与对账的防御性 `isinstance` / `else` | 7 | **保留**。解析器已把状态锁成文档化闭集，这些分支守的是解析器被改坏 |
| 诊断码的私有固定调用、动效模板布局的 `else` | 2 | **保留** |

### 5.3 计划 §8 判据逐条核对

| 判据 | 结果 |
|---|---|
| 1. `pytest --cov` 退出 0 | ✅（见 §5.1，无失败、覆盖率要求满足） |
| 2. 测试数只增不减 | ✅ `6,471 → 7,327` |
| 3. missing lines / branches / partial 全为 0 | ✅ |
| 4. Ruff format、Ruff check、Mypy 全绿 | ✅ 630 个文件 |
| 5. 独立脚本聚合门禁 | ✅ `check_acceptance_evidence_depth.py` 74 项通过 |
| 6. 49 处旧 pragma 已逐项审计，无新增 pragma / omit | ✅ 见 §5.2，49 → 40 |
| 7. 未调用真实付费模型、真实平台账号或默认浏览器 Profile | ✅ 新增用例全部是内存 Fake、`tmp_path` 与一条本机 loopback HTTP |
| 8. 无残留 `.coverage`、临时媒体、容器、浏览器或后台进程 | ✅ 每轮结束核对 |
| 9. 合回并推送 `main` | 见 §7 |

### 5.4 本轮对生产代码的改动清单

补覆盖率不允许改产品行为，以下每一处都是「让走不到的路消失」或「让不变量
变成结构」，没有一处改变调用方看到的结果：

| 位置 | 改动 | 依据 |
|---|---|---|
| `platform_commands.handle` | 删掉 `except PlatformCommandRejected: raise` | try 体内没有来源；与下一支产出同类型异常 |
| `publish_artifact.open_publish_artifact` | 同上 | 三个下游助手只抛 `ValueError` |
| `ledger.outbox_for_delivery` | `if last_ordinal <= 0: raise` → 断言 | 分页过滤 + schema `CHECK` 双重保证；真不推进会死循环 |
| `agent.author` 修复轮 | `for ... in (1, 0)` → `while True` + 计数器 | 循环没有正常出口，序列写法留下走不到的耗尽弧 |
| `material_speech_pipeline` ×3 | 把打开挪出受保护体 / 拆成两个 `try` | 「描述符可能为 None」的正常收尾路径不存在 |
| `material_speech_pipeline._open_stable_pcm` | 加断言替代 None 判断 | 能抛到那里的只有打开之后的检查 |
| `_aggregate_probability_evidence` | 删掉恒真的 `if confirmed` | 未确认的候选在上面就被丢掉 |
| `MaterialSpeechAnalyzer` | `if not transcripts: _reject()` → 断言 | 批次生成器空转会自己拒绝 |
| `authoring_workspace._reject` | 返回类型 `None` → `Never` | 与同目录另外两个 `_reject` 对齐，消掉一处死断言 |
| `silero_vad`、`adaptive_frame_extraction` 等九处（前几批） | 不可达 → 断言 | 逐处在对应台账里写明依据 |

## 6. 证据

- 提交：`0e5a0ddb`、`b0e8a367`、`3fecb03a`、`939dad78`、`9dfe55b8` 之后的收尾提交
- 收口测量：上表全部模块的 `missing_lines` 与 `missing_branches` 均为 `[]`
- 权威门禁：见 §5.1

## 7. 合回

见分支 `coverage/backend-100` 合入 `main` 的 merge commit（`--no-ff`），
以及合入后在 `main` 上复跑一次的同一条门禁命令。
