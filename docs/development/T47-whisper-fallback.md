# T47 字幕兜底：堵住演示现场的 1.5GB 静默下载

> 状态：✅ 已完成（第一步：兜底不再联网下载）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：包里带了 `faster_whisper` + `ctranslate2` 但没带模型，字幕兜底一旦触发就会
> 去 HuggingFace 拉 `large-v3`（约 1.5GB），演示现场表现为界面长时间转圈。

## 1. 现状核实

### 1.1 兜底怎么触发

`vendor/moneyprinterturbo/app/services/task.py:215-224`：

```python
if subtitle_provider == "edge":
    voice.create_subtitle(...)
    if not os.path.exists(subtitle_path):
        subtitle_fallback = True
        logger.warning("subtitle file not found, fallback to whisper")

if subtitle_provider == "whisper" or subtitle_fallback:
    subtitle.create(audio_file=audio_file, subtitle_file=subtitle_path)
```

`subtitle_provider` 默认是 `edge`（`config.example.toml`，我们的私有配置也照抄），
所以只有一条触发路径：**Edge 没能写出 srt 时自动转 Whisper**，无条件、不问用户。

### 1.2 转过去之后发生什么

`app/services/subtitle.py:27-37`：先找 `{root_dir}/models/whisper-large-v3/model.bin`，
找不到就把 `model_path` 退化成模型名 `"large-v3"` 交给 `WhisperModel`，
由 faster-whisper 经 huggingface_hub 去线上取。

包里**确实没有任何模型**：`material-video-worker.spec` 没有打包 `models/` 目录，
本任务加了用例守着这条（`test_package_still_ships_no_speech_model`）。
所以在用户机器上这一步必然走线上下载。

`large-v3` 的 `model.bin` 约 1.5GB。下载期间界面只有一个转圈，
用户完全不知道背后在干什么；国内网络下大概率超时或极慢。

**这是一条静默、无上限、用户不知情的网络路径，出现在渲染中途。**

## 2. 修法与理由

### 2.1 选定：给 WebUI 子进程加 HuggingFace 官方离线开关

`workers/material_montage/webui_runtime.py` 把子进程环境变量收敛成
`CHILD_ENVIRONMENT`，其中加一条 `HF_HUB_OFFLINE=1`。

渲染就发生在这个子进程里，所以这一条覆盖整条兜底路径。效果：
模型不在本地缓存时 `WhisperModel(...)` **立即抛错**，上游
`subtitle.py:38-46` 的 `except Exception` 接住、记日志、`return None`，
渲染继续走完，成片没有字幕——**不联网、不下载、不卡住**。

理由：

- `HF_HUB_OFFLINE` 是 huggingface_hub **自己的公开开关**，不是改上游源码、
  不是 Monkey Patch，符合「优先用公开扩展点」；
- 改动落在我们自己的启动层，一行环境变量，不动主链路逻辑，
  不改包内容、不改依赖清单、不改能力声明，因此**不影响另一条工作线正在跑的正式包验收**；
- 顺带把该进程里**任何**别的 HuggingFace 下载也一起堵死，而 Worker 本来就不该有；
- 只影响 HF，不影响 Edge TTS 等必须联网的正常功能。

### 2.2 为什么没有做成「明确报错」

「字幕生成失败就明确报错」需要改上游 `task.py` 的控制流——那是只读 Submodule，
项目规则禁止改。上游拿到 `None` 之后自己的行为就是记日志继续，
在不碰上游源码的前提下能达到的最好结果就是：**快速失败 + 成片照常产出（无字幕）**，
而不是静默下 1.5GB。

### 2.3 更彻底的一步（未做，需要拍板）

真正该做的是**根本不打包 `faster_whisper`**：上游 `subtitle.py:6-9` 本来就写了
`try: from faster_whisper import WhisperModel / except ImportError: WhisperModel = None`，
`create()` 在 `None` 时直接记一条 warning 返回空。也就是说**不打包这个依赖，
就是上游自己支持的降级路径**，比环境变量更干净，而且能砍掉约 115 MiB
（`faster-whisper` + `ctranslate2`，见 `docs/development/FIX-material-worker-package-size.md`），
对 511MB 的包很可观。

没有在本任务做，原因是它会动到：`build.excludedModules`、`dependencies.required`、
依赖数量断言、`probe.requiredCapabilities` 里的 `subtitle_transcription`、
`worker_main.RUNTIME_MODULES`、`gateway` 的能力清单——**这是主链路和包契约的改动，
而另一条工作线此刻正在同一份正式包上跑素材成片链路验收**，必须等它收工并经协调后再做，
还需要一次完整的 PyInstaller 重建来验收。建议单独立任务。

## 3. TDD 证据

### RED

```text
$ python3 -m pytest scripts/test_material_video_worker.py -k SubtitleFallback -q
..F
FAILED ...::test_webui_child_cannot_start_a_hidden_model_download
  AssertionError: None != '1'
1 failed, 2 passed, 50 deselected
```

断言失败，不是导入或编译失败。另外两条同时立住了前提事实：
上游确实还有这条兜底、包里确实没有模型。

### GREEN

```text
$ python3 -m pytest scripts/test_material_video_worker.py -q
52 passed, 1 skipped in 0.12s
```

## 4. 真实验收（A/B，各用一个全新的空缓存）

在锁定运行环境里，对 faster-whisper 真正会去取的仓库
`Systran/faster-whisper-large-v3` 做对照。为了不真的拉 1.5GB，
只请求 `config.json`——**能不能联网取到**这件事和文件大小无关：

```text
today  NETWORK REACHED in 2.00s -> .../models--Systran--faster-whisper-large-v3/snapshots/edaa852e...
fix    REFUSED in 0.10s -> LocalEntryNotFoundError: Cannot find an appropriate cached snapshot
       folder for the specified revision on the local disk and outgoing traffic has been disabled.
```

再直接走产品实际调用的构造函数，同样是全新空缓存：

```text
WhisperModel refused in 0.07s -> LocalEntryNotFoundError
```

即：**修改前会真的出网去取模型；修改后 0.07 秒失败，一个字节都不下载。**
上游 `subtitle.create()` 用 `except Exception` 接住这个异常，记日志后返回 `None`，
渲染继续。

**没验到的一步**：没有在重新冻结的包和正式 `.app` 里跑一遍完整成片并人为制造
Edge 字幕失败来触发兜底。理由同 T32/T46：另一条工作线正在同一份正式包上验收，
本次不重建包。环境变量在 `start_webui` 里注入，源码路径与冻结路径是同一段代码。

## 5. 失败矩阵

| 情况 | 期望行为 | 证据 |
| --- | --- | --- |
| Edge 字幕失败、机器无模型缓存 | 0.1 秒内失败，不下载，成片照常产出（无字幕） | 上面 A/B |
| 机器上碰巧有 HF 缓存 | 正常加载并出字幕（离线开关只挡下载，不挡缓存） | huggingface_hub 语义 |
| 有人把离线开关去掉 | `test_webui_child_cannot_start_a_hidden_model_download` 变红 | 用例已建 |
| 以后往包里塞了模型 | `test_package_still_ships_no_speech_model` 变红，强制一起复核 | 用例已建 |
| 上游改掉兜底写法 | `test_upstream_still_falls_back_to_a_downloaded_model` 变红 | 用例已建 |
| Edge TTS 等必须联网的功能 | 不受影响，开关只作用于 HuggingFace | 变量语义 |

## 6. 清理

- A/B 用的三个临时 HF 缓存目录用完即删；
- 唯一落盘过的网络内容是一个 `config.json`（几 KB），已随缓存目录删除；
- 未启动任何常驻服务，未改 `.local/`。
