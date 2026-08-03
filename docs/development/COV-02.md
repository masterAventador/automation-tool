# COV-02 本地/智能剪辑 768 点

用户可操作：否
证据类型：分层实现

> 状态：🚧 实现中。第 1 批 `adaptive_frame_extraction.py` 已消除 75/178；
> 其余三批未开始。
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

### 2.5 第 1 批剩余 103 点

| 函数 | 剩余 |
|---|---:|
| `_open_output_workspace` | 26 |
| `extract_adaptive_frame_candidates` | 10 |
| `_read_final_frame` / `_collect_bounded_ffmpeg` | 8 / 8 |
| `close` / `_current_path` / `_write_exclusive_frame` | 5 / 5 / 5 |
| `read_adaptive_frame_artifacts` / `_uniformly_sample` / `_parse_supplement_frame` / `_timestamp_from_name` | 4 each |
| 其余 11 个小项 | 2 或 1 |

## 3. 待办

- 第 1 批剩余 103 点（主要是 `_OutputWorkspace` 的独占写、fsync、identity 复验）；
- 第 2 批 smart/local 两个 worker process 193 点；
- 第 3 批 pipeline/preview/generation/media/local worker 279 点；
- 第 4 批 material 与控制面尾项 118 点。

每小批必须让目标模块独立达到语句/分支双 100% 再进入下一批。
