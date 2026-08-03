# COV-02 本地/智能剪辑 768 点

用户可操作：否
证据类型：分层实现

> 状态：🚧 实现中。第 1 批 `adaptive_frame_extraction.py` 178→**0**、第 2 批两个
> worker process 193→**0**，均连跑三次稳定；第 3、4 批未开始。
> 上游计划：`docs/development/2026-08-03-backend-coverage-debt-plan.md`
> 前置：[COV-00](COV-00.md)、[COV-01](COV-01.md)
> 分支：`coverage/backend-100`

## 1. 起点核对

COV-01 之后全库缺口 1,664。本运行边界 17 个模块合计 **768** 点，与计划 §5.1 的表格
逐模块一致（`adaptive_frame_extraction` 178、`smart_edit_worker_process` 107、
`local_editing_worker_process` 86 ……）。计划的四批拆分沿用。

## 2. 第 1 批：`adaptive_frame_extraction.py`（178 → 103）

### 2.1 先分组，再动手

把 178 点按函数归类后，最大的一块出乎意料：

| 分组 | 点数 | 占比 |
|---|---:|---:|
| Windows 目录句柄四函数 | 60 | 34% |
| `_OutputWorkspace` 类与 `_open_output_workspace` | 51 | 29% |
| 进程控制（bounded ffmpeg / kill / measure） | ~30 | 17% |
| 其余（时间戳解析、采样、读帧） | ~37 | 20% |

### 2.2 Windows 四函数：60 点，用平台值注入

`_open_windows_directory_handle` / `_windows_directory_path` /
`_close_windows_directory_handle` / `_windows_last_error` 此前**连 `os.name != "nt"`
守卫都没执行过**——唯一被计入的行是 `def` 本身。

能在 macOS/Linux 上覆盖它们，靠的是一个先实测过的事实：**`ctypes.wintypes` 在非
Windows 上可以导入**（它是纯 Python 模块，`LPCWSTR`/`DWORD`/`HANDLE`/`LPWSTR`/`BOOL`
全部可用），只有 `CDLL("kernel32.dll")` 会失败。因此 patch `os.name` 与 `ctypes.CDLL`
之后，函数体（argtypes/restype 赋值、buffer 分配、错误判定）全部按原样执行。

这就是计划 §COV-06 对 `windows_candidate.py` 说的「平台值注入」，不是放宽门禁：断言落
在**传给 API 的实参**（`FILE_LIST_DIRECTORY`、`BACKUP_SEMANTICS|OPEN_REPARSE_POINT`）
和**每一种失败信号是否被转成 `OSError`** 上。真实 kernel32 行为仍归 Windows runner。

> 写测试时撞到一个行为：patch `os.name="nt"` 期间 `Path(...)` 会构造 `WindowsPath`
> （`Path.__new__` 按 `os.name` 选实现），块内块外建的同一路径字符串形态不同。测试里
> 已注明并把路径对象移到 patch 外构造。

### 2.3 进程控制：把 COV-00 的 flaky 分支钉死

COV-00 §2.2 记录 `_kill_and_reap` 的 `(1040, 1043)` 是竞态分支——同一份代码连跑三次，
只有一次走到「子进程已自行退出」那一侧。

新测试用固定的 `poll()` 应答替代时序，两条臂各有一条确定性用例，另加「第一次 kill 后
仍超时要再 kill 一次」「kill 撞上进程已消失的 `OSError` 要容忍」「第二次 reap 再超时也
不得抛出」。复跑三次验证：

| 跑次 | `(1040→1043)` 缺失 |
|---|---|
| 1 / 2 / 3 | False / False / False |

不再看调度运气。

文件系统一侧（`_measure_output`、`_workspace_accepts_write`）用真实临时目录而非 mock：
这些断言问的是 `stat` 对非普通文件、对消失的目录报什么，mock 只会把测试自己的假设重述
一遍。

### 2.4 本批结果

用集合运算（基线 `missing` ∩ 新测试 `executed`）精确计量：

```
基线缺口: 178
新覆盖:   57 行 + 18 分支 = 75
剩余:     103
```

新增两个测试文件共 29 条用例，`ruff`/`ruff format`/`mypy`(619 files) 全绿。

### 2.5 收口：178 → 0

继续补三个测试文件后模块达到 100%：

| 文件 | 用例 | 覆盖对象 |
|---|---:|---|
| `..._workspace.py` | 18 | `_OutputWorkspace` 三种命名方式、identity 漂移拒绝、独占写与批量回滚 |
| `..._parsing.py` | 30 | 时间戳解析、候选采样、读回校验、批次元数据拒绝 |
| `..._orchestration.py` | 19 | 工具链失效、源文件中途被换、补帧预算耗尽、测量失败杀子进程 |

最终 `pytest -k adaptive` 150 passed，模块 **100.00%**，连跑三次稳定；
`ruff` / `ruff format` / `mypy`(622 files) 全绿。

### 2.6 两处需要记的东西

**同一个坑踩了两次：patch `os.name="nt"` 会改变 `Path()` 的实现类。**
`Path.__new__` 按 `os.name` 选 `WindowsPath` 还是 `PosixPath`，所以在 patch 内构造的
路径在 macOS 上 `lstat()` 会失败——`_open_output_workspace` 于是在最开头的 identity 探测
就返回了 `None`，**断言照样通过，但验的是完全另一条路径**。第一次是在比较实参时发现
（字符串带反斜杠），第二次是覆盖率显示 Windows 分支根本没执行才发现。两处都已把路径
对象移到 patch 外构造并注明原因。

顺带一提，第二次是 `ruff --fix` 合并 `with` 语句时把问题显影的——但**问题本来就在**，
不是工具引入的；工具只是让它更显眼。

**一处不可达分支改成了断言。** `_globally_uniform_indices` 里的
`if best_previous_cost < 0: continue` 在任何合法输入下都走不到：每一轮填充的 cost 区间
下界，恰好等于下一轮读取的下界，所以运行中的最优值在第一次迭代就已 ≥ 0。它是本模块唯一
无法靠测试覆盖的点。

改成 `assert best_previous_cost >= 0`（同文件已有 `assert final_internal_index >= 0`、
`assert parent_index >= 0` 两处先例）。这样断言行本身每次迭代都执行因而可覆盖，语义仍是
防御，而且真出问题时是响亮失败，而不是静默按 `-1` 计费得出一条错误的选帧路径。**没有新增
`pragma: no cover`。**

## 3. 第 2 批：两个 worker process（193 → 0）

| 模块 | 基线 | 结果 |
|---|---:|---:|
| `smart_edit_worker_process.py` | 107 | **0** |
| `local_editing_worker_process.py` | 86 | **0** |

新增 `test_smart_edit_worker_validation.py`（55）、`test_local_editing_worker_validation.py`
（43 + 61 subtest），并把两个 `..._process.py` 测试各扩到 32 / 14 条。四个文件
连跑三次均为 100.00%。

### 3.1 两条自己写的假阳性

都是「断言绿了，但被测那条路根本没执行」，且**只有覆盖率数据能发现**：

- **用 monkeypatch 把 `_MAX_DOCUMENT_BYTES` 降到 1 来测结果文档超限。** 看着等价，
  实际不是——`_load_object` 拿同一个常量限制*请求*文件，于是运行在读自己的输入时
  就死了，从没到过结果大小那道关。改成真的产出一份超限文档；
- **测「adapter 加载失败要拒绝」时 fixture 用了 catalog 里不存在的 `model_id`。**
  adapter 本来就加载不起来，我 mock 的那个特定失败从未被触及——两条不同的失败路径
  共用了一个断言。改用真实存在的 `model_id` 与 `base_url`，并对着一条既有的通过用例
  核对过。

这正是本轮一律用「基线 `missing` ∩ 新 `executed`」计量、而不是只看测试通过与否的原因：
`pytest` 说通过只证明断言没炸，缺口数字下降才证明那段代码真的被执行。

### 3.2 第三次撞上 `Path` / `os.name`

`patch os.name="nt"` 期间构造的 `Path` 是 `WindowsPath`，`lstat` 与路径比较在 macOS 上
必然失败，被测函数于是在最开头就返回——断言照样通过。§2.6 已记两次，本批是第三次，
三处都已注明原因。

### 3.3 几处值得记的验收形状

- **`_render_failure` 用全映射断言守枚举增长**：`covered == set(VisualRenderExecutionRejection)`，
  将来加一个没人翻译的成员会在这里红，而不是在运行时 `KeyError`；
- **`commit` 回滚失败时不得留在已发布状态**：树搬不回去就必须丢弃，不能两头都在；
- **渲染作业的路径泄漏**：异常里不得出现源路径，`repr` 必须是 `<redacted>`（§7）；
- **产物标识**：非 UUID、nil UUID、v1、非 RFC 4122 变体四种都必须拒绝——渲染已经成功，
  唯一能表达成功的就是一个查得回来的标识。

## 4. 待办

- ~~第 1 批~~ ✅ 已收口；
- ~~第 2 批~~ ✅ 已收口；
- 第 3 批 pipeline/preview/generation/media/local worker 279 点；
- 第 4 批 material 与控制面尾项 118 点。

每小批必须让目标模块独立达到语句/分支双 100% 再进入下一批。
