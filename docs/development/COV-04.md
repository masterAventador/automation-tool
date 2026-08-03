# COV-04 语音与音频链 199 点

用户可操作：否
证据类型：分层实现

> 状态：✅ 已完成。199 → **0**。
> 上游计划：`docs/development/2026-08-03-backend-coverage-debt-plan.md`
> 前置：[COV-03](COV-03.md)
> 分支：`coverage/backend-100`

## 1. 起点核对

COV-03 收口后重新测量语音与音频链，精确缺口 **199** 点。逐模块：

| 模块 | 起点 | 现在 |
|---|---:|---:|
| `material_speech_pipeline.py` | 73 | **0** |
| `silero_vad.py` | 46 | **0** |
| `material_speech_analysis.py` | 31 | **0** |
| `script_voiceover.py` | 27 | **0** |
| `material_speech_transcription_adapter.py` | 22 | **0** |

测量口径与前三项一致：对基线 JSON 做 `missing_lines` / `missing_branches`
的集合运算，不看「测试通过」。

```bash
cd backend
uv run --frozen pytest tests/unit tests/contract -q -p no:randomly \
  --cov=automation_tool.executor.material_speech_pipeline \
  --cov-report=json:/tmp/sp.json --cov-fail-under=0
```

## 2. 真实边界

### 2.1 存根测不出命令行合法性

`silero_vad` 与 PCM 抽取都要拼真实的 `ffmpeg` 命令行。这一环沿用既有教训：
只有让真二进制跑一遍，才知道参数顺序、`-f s16le`、采样率这些东西是不是真的
成立；存根返回成功不构成证据。

### 2.2 TOCTOU 只能从打开它的那一步下钩子

PCM 文件在「打开 → 量长度 → 逐块读 → 收尾复核」之间有四个可被顶替的时刻。
这些窗口只能通过 hook `os.read` / `os.fstat` / `os.close` 制造，断言落在
「拒绝了」而不是「读到了什么」。

### 2.3 三处「描述符可能为 None」的清理分支实际不可能

`_extract_pcm`、`_detect_speech_segments`、`_pcm_batches` 各有一段

```python
finally:
    if descriptor is not None:
        with suppress(OSError):
            os.close(descriptor)
```

打开失败时 `_reject()` 直接抛，异常沿 `finally` 向上走——**永远走不到那条
「条件为假、继续往下」的弧**；打开成功时描述符必然非 None。也就是说这个
条件在正常路径恒真、在异常路径不产生该分支弧。

处理方式不是加豁免，是让不变量变成结构：

- `_extract_pcm` 把「打开」和「取 stat」拆成两个 `try`，第二个的 `finally`
  无条件关闭——此处描述符必然已绑定；
- 另两处把 `_open_stable_pcm(...)` 挪出受保护体（它自己就把 OSError 转成
  拒绝，挪出去不改变任何调用方看到的结果），`finally` 同样无条件关闭；
- `_open_stable_pcm` 的 `except MaterialSpeechRejected` 分支加断言：能抛到
  这里的只有打开之后的那几项检查，描述符必然是开着的。

### 2.4 `if confirmed` 在那个位置恒真

`_aggregate_probability_evidence` 里，未确认的候选在 619 行就被丢掉并
`continue`，静音计数根本不会累加。等走到 630 行时 `confirmed` 必然为真——
这是一段死条件，直接删掉并说明理由，而不是补一条造不出来的用例。

### 2.5 批次生成器空转会自己拒绝

`if not transcripts: _reject()` 走不到：`_pcm_batches` 在 `if not yielded`
时先拒绝，异常从 `for` 里抛出，根本到不了这一行。改成断言（本轮第九处
「不可达 → 断言」转换），并在注释里写清依据。

## 3. 失败矩阵

| 场景 | 结果 |
|---|---|
| PCM 提前读空 / 超出声明尺寸 | 拒绝 |
| 描述符前后身份漂移 | 拒绝 |
| 关闭后路径被顶替 | 拒绝 |
| ffmpeg 起不来 / 非零退出 | 拒绝 |
| VAD 概率不是有限的 0..1 浮点 | 拒绝 |
| 转写不是非空且已 strip 的字符串 | 拒绝 |
| 转写总长超上限 | 拒绝 |
| 工作区清理失败 | 吞掉，不影响结果 |

## 4. 清理

临时工作区在 `finally` 里 `shutil.rmtree`，`suppress(OSError)`；用例结束后
不留 PCM、wav 或 ffmpeg 子进程。

## 5. 证据

- 提交：`19305aa0`、`90937c96`、`6d3ce646`、`86032e39`、`811946b3`
- 收口测量：`material_speech_pipeline` / `silero_vad` /
  `material_speech_analysis` / `script_voiceover` /
  `material_speech_transcription_adapter` 的
  `missing_lines` 与 `missing_branches` 均为 `[]`
- 全量：见 [COV-06](COV-06.md) §5 的最终一次 `pytest tests` 记录
