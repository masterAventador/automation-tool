# COV-02 本地/智能剪辑 768 点

用户可操作：否
证据类型：分层实现

> 状态：✅ **已收口**。四批 768 点全部消除，17 个模块均达语句/分支双 100%，
> 已由含 integration 的全量运行（6974 passed / 21 skipped）验证。
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

## 4. 第 3 批：流水线五模块（279 → 0）

| 模块 | 基线 | 结果 |
|---|---:|---:|
| `smart_edit_pipeline.py` | 77 | **0** |
| `local_material_preview.py` | 70 | **0** |
| `smart_edit_generation.py` | 57 | **0** |
| `local_editing_worker.py` | 39 | **0** |
| `smart_edit_media.py` | 36 | **0** |

五个模块各自连跑三次稳定在 100.00%。

### 4.1 只存在于「两步之间」的分支怎么测

预览模块有几处窗口从外面进不去：文件批准了、拿到描述符时世界已经变了。这类分支唯一
的确定性入口是让 `fstat` 直接给出那个答案，并**按 inode 精确匹配到被测文件**——否则会
打到注册表自己读文档的那次调用上，答出一个注册表原因，根本到不了被测分支。

顺带踩到 fd 号复用：预览的描述符关掉之后，注册表紧接着打开文档拿到的正是同一个号。
「只拒绝这个 fd」于是变成连注册表一起拒绝。改成只拒绝一次。

### 4.2 域模型自己会拦，所以测不出「下游还验一遍」

生成模块的 dataclass 都在 `__post_init__` 里验形状，而下游「不信任传进来的东西、自己
再验一遍」这件事，用工厂造出来的样本证明不了——工厂本来就不会产出非法值。所以有一个
显式的 `_corrupt` 辅助，用 `__new__` 加 `object.__setattr__` 组装出工厂不会产出的对象。
被证明「不够用」的正是那个构造函数，用它造样本等于什么也没测。

### 4.3 第三处不可达分支改成断言

`smart_edit_pipeline.prepare` 里的 `if current.ai_description is None: _reject()` 走不到：
`_needs_understanding` 为假只可能是描述已经存在（域模型拒绝「用户来源但没有描述」），
为真时上面那步要么产出了描述要么已经拒绝。

`safe_preview_content_type` 里 ftyp 分支的 `if kind is VIDEO` 假侧同理——IMAGE 在上面
已返回或被拒，AUDIO 是上一个 `if`。

两处都按 §2.6 的先例改成 `assert`：断言行每次都执行因而可覆盖，语义仍是防御，且真出
问题时是响亮失败而不是静默走偏。**全程没有新增 `pragma: no cover`。**

## 5. 第 4 批：素材与控制面尾项（118 点）

| 模块 | 基线 | 结果 |
|---|---:|---:|
| `material_understanding.py` | 26 | **0** |
| `application/editing_jobs.py` | 20 | **0** |
| `application/materials.py` | 6 | **0** |
| `material_probe.py` | 6 | **0** |
| `domain/material.py` | 6 | **0** |
| `api/editing_materials.py` | 4 | **0** |
| `api/editing_jobs.py` | 3 | **0** |
| `material_repository.py` | 46 | **0** |
| `timeline_repository.py` | 1 | **0** |

### 5.1 第四次「断言通过但被测路径没执行」

`domain/material.py` 有三条既有用例——语音窗口的顺序、空/倒置、超出时长——都没传
`speech_transcript`。转写文本的校验排在段落循环**之前**，所以三条实际都在
`material.py:66` 因「没有转写文本」被拒，从没走到各自断言的规则。三条全是绿的。

用给 `_reject` 打点的方式确认过：不传时来自 line 66，传了才走到 line 212。修法是抽
一个 `_speech` 辅助把这个字段一并给全，并在 docstring 里写清它为什么不能省。

本轮四次同类问题，前三次是本次新写的测试，这次是既有的。**共同点是只看测试通过与否
一次也发现不了**，只有缺口数字不降才暴露。

### 5.2 从 HTTP 进不去的那一层

`EditingJobService.reconcile` 的校验从接口层进不去——请求模型先把它们挡了——但本地
调度器是直接调这个 service 的，中间没有 Pydantic。所以新建
`test_editing_job_reconciliation.py` 直接对 service 测，并在文件开头写明这一点，免得
后来的人把这些「看起来冗余」的检查删掉。

游标那两条值得单独记：一条是解码后字节相同但拼写不同的 base64（末位字符带未使用的
低位），另一条是 base64 规范但 JSON 文档不规范（键后多一个空格）。游标是服务端发出的
不透明令牌，第二种拼写就是服务端没发过的那种。

### 5.3 素材仓储：能用替身的和只能用真库的

`material_repository.py` 的失败分支分两类。类型闸、驱动异常兜底、`IntegrityError` 的
约束名提取（asyncpg 把它放在异常上，SQLAlchemy 有时再包一层放到 `__cause__`，两种形状
都要读）、以及智能剪辑写回的业务分支，都用替身覆盖——写回在一个事务里跑多条语句并按
每条的返回分叉，所以替身按调用顺序应答，一条一个答案。

有两个子条件**从存在的命令构造不出来**：写回声明 `user` 归属时既不能带模型标签也不能带
描述时间戳（域模型两者都拒），所以 USER 分支里比较这两项的那两个词永远为假。测试里保留
了可构造的两项并注明原因，没有为了凑覆盖去放宽域模型。

### 5.4 第四处不可达分支改成断言

`timeline_repository.save` 的 `if reference_values:` 假侧走不到：时间线必须有画面轨
（`Timeline.__post_init__`），画面片段必须命名素材（`TimelineClip.__post_init__`），
所以引用列表恒非空。改成 `assert reference_values`——判据与前三处一致：断言行每次都
执行因而可覆盖，语义仍是防御，且放宽任一域规则时会响亮失败，而不是悄悄存下一条
「素材无人记录」的修订。

## 6. 收口

四批 768 点全部消除。最终验证：`pytest tests`（含 integration，真实 PostgreSQL）
**6974 passed / 21 skipped**，本运行边界 17 个模块的 `missing_lines` 与
`missing_branches` 均为空。`ruff` / `ruff format` / `mypy`(626 files) 全绿。

全程**没有新增 `pragma: no cover`、没有新增 `omit`、没有降低 `fail_under`**。
五处不可达分支改成了断言（§2.6、§4.3、§5.4，以及素材仓储里写回时间戳的那处），
判据一致：那条 `if` 的假侧在任何输入下都到不了，而断言行每次都执行因而可覆盖，
语义仍是防御，且规则一放宽就是响亮失败而不是静默走偏。

每小批必须让目标模块独立达到语句/分支双 100% 再进入下一批。
