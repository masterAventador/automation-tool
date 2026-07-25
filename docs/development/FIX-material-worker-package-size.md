# FIX 智能素材成片 Worker 的包体裁剪与发布体积上限重定

> 状态：🔍 待验收（裁剪、门禁与冻结包真实成片已验证；缺一次完整重建后的正式包用户路径验收）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：补上视频运行时的生产装配路径（`2e9a3a6`）后重建正式包，出厂门禁拒绝：
>
> ```text
> release package rejected: release package is outside the release size bounds
> ```

## 缺陷

`scripts/check_embedded_browser_package.py` 的 `RELEASE_SIZE_BOUNDS.max_package_bytes` 是 700 MiB。
该数值只按「内置 Chromium + 执行器」推导，`2e9a3a6` 把三份视频运行时装进包以后已经失真。

实测 macOS arm64 正式包（`.local/eb-16/.../自动化运营工具.app`）：

| Resources 内的资源 | 体积 |
| --- | ---: |
| `embedded-browser`（Chrome for Testing 149） | 343.0 MiB |
| `local-executor` | 176.1 MiB |
| `material-video-worker` | **640.5 MiB** |
| `motion-video-worker` | 107.9 MiB |
| `media-toolchain` | 42.1 MiB |
| App 壳、WebView 资源、图标、清单 | 21.6 MiB |
| **合计** | **1331.1 MiB**（3603 个文件） |

单纯把上限调高会掩盖真正的问题：640.5 MiB 的智能素材成片 Worker 里有一批**产品任何路径都走不到**的模块。
所以先裁剪，再按裁剪后的实测重新推导上限。

## 每个 ≥10 MiB 组件的核实结论

核实方法不是看目录名，而是三条实证：①在锁定环境里 import 整个上游 `app` 包看谁被真正加载；
②用 meta path finder **屏蔽候选模块**后跑真实 WebUI 并真实成片；③按 `uv tree` 确认引入者。

| 组件 | 体积 | 谁引入 | 产品是否走到 | 结论 |
| --- | ---: | --- | --- | --- |
| `upstream/resource/fonts` | 141.8 MiB | 上游资源 | **走到**，字幕字体默认 `MicrosoftYaHeiBold.ttc` | 保留（另见「未处理」） |
| `pyarrow` | 112.8 MiB | `streamlit` 硬依赖 | **走不到**。streamlit 1.59 只在 dataframe/Arrow/图表路径惰性 import；上游 WebUI 明确不用 dataframe（`webui/Main.py:687` 注释说明改用行内动作） | **裁掉** |
| `onnxruntime` | 59.1 MiB | `faster-whisper` | 只在 Silero VAD 转写时惰性加载 | 保留（见「未处理」） |
| `upstream/resource/songs` | 55.3 MiB | 上游资源 | **走到**，背景音乐可选「随机背景音乐」 | 保留 |
| `imageio_ffmpeg` | 46.9 MiB | `moviepy` | **走到**。其中 46.9 MiB 是它自带的 ffmpeg 二进制，当前正是产品实际使用的那份 | 保留（见「未处理」） |
| `av` | 44.1 MiB | `faster-whisper` | 随 `faster_whisper` 在 `app.services.subtitle` 顶层被 import | 保留（见「未处理」） |
| `streamlit` | 26.7 MiB | 产品自身 | **走到**。`material_video_studio.rs:153` `with_web_ui()`，Tauri 窗口直接指向这个 Streamlit WebUI | 保留 |
| `pandas` | 18.4 MiB | `streamlit` 硬依赖 | **走到**。屏蔽后页面直接 `ImportError`：`streamlit/time_util.py:61 time_to_seconds` → `st.fragment(run_every=...)`（任务管理区） | **保留（实测证伪了"可裁"的推测）** |
| `libpython3.11.dylib` | 16 MiB | CPython | 走到 | 保留 |
| `PIL` | 11.5 MiB | `moviepy` | 走到（字幕合成） | 保留 |
| `cryptography` | 10.8 MiB | TLS 依赖链 | 走到 | 保留 |
| `altair` | 1.8 MiB | `streamlit` | 走不到（无任何 `st.*_chart`） | **裁掉** |
| tcl/tk（`_tcl_data`、`_tk_data`、`tcl9`、`libtcl9*.dylib`） | 6.6 MiB | PyInstaller 的 tkinter hook | 走不到（Worker 无任何桌面对话框） | **裁掉**（排除 `tkinter`/`_tkinter` 后 hook 不再触发） |

**推翻的两个初判**：

1. 「产品不走上游 WebUI，streamlit 可以裁」——**错**。产品的智能素材成片窗口就是上游 Streamlit WebUI，
   `material_video_studio.rs` 用 `WebviewUrl::External` 指向 `127.0.0.1:<port>/studio-<capability>/`。
   streamlit、streamlit-tour 及其运行时依赖全部是主链路。
2. 「pandas 是 streamlit 传递依赖，可以一起裁」——**错**。屏蔽 pandas 后页面首屏就崩，见上表。

## RED

新增排除模块门禁（`scripts/test_material_video_worker.py`，6 → 10 个用例）：

```text
ERROR: test_candidate_carrying_an_excluded_module_is_rejected
AttributeError: module 'build_material_video_worker_candidate' has no attribute
'assert_excluded_modules_absent'

ERROR: test_candidate_without_excluded_modules_is_accepted
AttributeError: module 'build_material_video_worker_candidate' has no attribute
'assert_excluded_modules_absent'

ERROR: test_contract_declares_the_excluded_modules
AttributeError: module 'build_material_video_worker_candidate' has no attribute
'excluded_modules'

FAIL: test_spec_reads_the_excluded_modules_from_the_contract
AssertionError: 'excludedModules' not found in '# -*- mode: python ...  excludes=[], ...'

Ran 10 tests — FAILED (failures=1, errors=3, skipped=1)
```

新增体积上限推导门禁（`scripts/test_embedded_browser_package.py`，32 → 34 个用例）：

```text
FAIL: test_release_size_bounds_admit_the_declared_production_payload
AssertionError: 734003200 not greater than or equal to 1271922688

Ran 34 tests — FAILED (failures=1)
```

`734003200` = 当时的 700 MiB 上限，`1271922688` = 声明的 1213 MiB 生产负载。

## GREEN

```text
python3 scripts/test_material_video_worker.py     Ran 10 tests  OK (skipped=1)
python3 scripts/test_embedded_browser_package.py  Ran 34 tests  OK
python3 scripts/check_third_party_sources.py      third-party source locks, licenses,
                                                  rights policy and SBOM are valid
python3 scripts/check_user_facing_branding.py     user-facing branding and plain-language
                                                  scan passed (51 frontend, 247 native files)
python3 scripts/check_embedded_browser_video_roadmap.py
                                                  specialized roadmap status and per-task
                                                  evidence are valid
```

用例条数逐次核对：`test_material_video_worker` 6 → 10；`test_embedded_browser_package` 32 → 34
（原 `test_release_size_bounds_are_a_real_gate` 里那条写死的 1200 MiB 上界被删掉，改由
`test_release_size_bounds_admit_the_declared_production_payload` 按声明负载 ±10% 双向夹住，
不再是拍脑袋的魔法数字）。

排除门禁对**改动前的真实候选**（`~/Library/Caches/automation-tool-build/material-video-worker`）确实拦得住：

```text
智能素材成片本机服务包被拒绝：候选仍包含产品用不到的模块：altair,pyarrow
```

## 交付

### 契约（`contracts/quality/material-video-worker-package.v1.json`）

`build.excludedModules` 一处声明产品走不到的模块，附 `excludedModulesReason`。
PyInstaller spec 与出厂审计都从这里读，不维护第二份清单。

### 打包（`workers/material_montage/material-video-worker.spec`）

`excludes=[]` → `excludes=excluded_modules`，清单来自契约。

### 审计（`scripts/build_material_video_worker_candidate.py`）

- `excluded_modules(contract)`：读取并校验契约里的排除清单，缺失或非法一律拒绝；
- `assert_excluded_modules_absent(candidate, contract)`：候选里只要还有 `_internal/<模块>` 目录或
  `_internal/<模块>.*` 扩展文件就拒绝，并点名是哪几个；
- 该检查接进 `audit_candidate()`，与「混入 RPA Executor」「缺许可证清单」同级失败关闭。

契约变了缓存键就变（`prepare_video_runtime.py` 的 `ensure_cached` 按契约摘要取键），
下一次正式构建会自动重建 Worker，不需要手工清缓存。

### 体积上限（`scripts/check_embedded_browser_package.py`）

新增 `RELEASE_PAYLOAD_PARTS_MIB`，把「单架构正式包由哪几份资源组成、各自多大」写成代码里的声明，
每一项都带实测字节数注释。上限不再手填，而是从这张表推导：

```text
343 (Chromium) + 177 (执行器) + 520 (素材成片 Worker) + 108 (动效 Worker)
  + 43 (媒体工具链) + 22 (App 壳与前端资源)  =  1213 MiB
max_package_bytes = 1330 MiB  ≈ 声明负载 + 9.6% 余量（测试夹在 +10% 以内）
```

浏览器上下界（320–420 MiB）不变，仍然把「一个包里塞两套架构」按重量挡掉。
包体上限留 10% 而不是更宽：多出一份浏览器（+343）、一份执行器（+177）或一份视频 Worker（+108 起）
都会立刻越界。

## 裁剪前后对比

| | 裁剪前 | 裁剪后 | 差 |
| --- | ---: | ---: | ---: |
| Worker 文件数 | 2972 | 2137 | −835 |
| Worker 体积 | 671,565,906 B（640.5 MiB） | 542,112,270 B（517.0 MiB） | **−129,453,636 B（−123.5 MiB，−19.3%）** |
| 冷启动探针 | 0.32 s（预热后） | 0.32 s（预热后） | 无变化 |
| 锁定依赖数 | 112 | 112 | 无变化（只裁产物，不动锁文件） |
| 正式包合计 | 1,395,796,684 B（1331.1 MiB） | 1,266,343,048 B（1207.7 MiB，推算） | −123.5 MiB |

构建器审计原文：

```text
智能素材成片本机服务候选已通过：2137 files, 542112270 bytes, startup 13.148s,
Python 3.11.15, 112 dependencies
```

（`startup 13.148s` 是 PyInstaller 刚写完 2137 个文件后的冷盘首跑；预热后 `--probe` 稳定在 0.32 s，
与裁剪前一致。契约上限 30 s。）

裁剪后逐项复查：`pyarrow`、`altair`、`pydeck`、`tkinter`、`_tkinter`、`_tcl_data`、`_tk_data`、
`tcl9`、`libtcl9.0.dylib`、`libtcl9tk9.0.dylib` 全部消失；`pandas`、`streamlit`、`moviepy`、
`imageio_ffmpeg`、`av`、`onnxruntime`、`numpy`、`PIL`、`upstream` 全部在位。

## 真实成片验收（裁剪后的冻结包，正常用户路径）

用 `2137` 文件的**冻结候选**按生产方式启动：向可执行文件 stdin 写一条与 Rust 桥完全相同的
bootstrap（`enableWebUi: true`、64 位十六进制会话令牌、`renderBrowser: null`），读回 ready 事件：

```text
READY_EVENT={"authenticationProof":"atvwp1.fUawZrBUU8KKM73kRB668hzdXVEnyYctHufGZQdVsOE",
"event":"worker.ready","port":60679,"protocolVersion":"1.0","scriptModelId":null,
"webUiAuthenticationProof":"atvwp1.5VevwfUvgmSZQ7rKz11Ss1VgD0nMpk7b_zpo7j5Kz50",
"webUiPath":"studio-aiLRNNrWK4P90H2ELtpAEYnwo6_df787L1WAod7Q9qc","webUiPort":60604,
"workerKind":"python","workerVersion":"1.3.2"}
```

再用无头浏览器按真实用户路径操作这个 WebUI：关闭引导 → 粘贴文案 → 视频来源切「本地文件」→
上传三段用打包 ffmpeg 现造的 1080×1920 素材 → 点「生成视频」。Worker 内部完整链路原文：

```text
app.services.task:start - start task: 9589c5c3-1180-4698-8b56-f716ace38872, stop_at: video
app.services.task:generate_audio - no custom audio file provided, using TTS to generate audio.
app.services.voice:azure_tts_v1 - start, voice name: zh-CN-XiaoxiaoNeural, try: 1
app.services.voice:azure_tts_v1 - completed, output file: .../audio.mp3
app.services.task:generate_subtitle - ## generating subtitle, provider: edge
app.services.voice:_write_subtitle_items - completed, subtitle file created: .../subtitle.srt,
                                           duration: 7.487
app.services.task:get_video_materials - ## preprocess local materials
app.services.video:combine_videos - audio duration: 8.06 seconds
app.services.video:_prioritize_unique_source_clips - prioritized unique video materials,
                                                     sources: 3, primary clips: 3
app.services.video:combine_videos - concatenating 3 clips with ffmpeg
app.services.video:combine_videos - video combining completed
app.services.video:generate_video - generating video: 1080 x 1920
app.services.video:generate_video -   ⑤ font: ./resource/fonts/MicrosoftYaHeiBold.ttc
app.services.task:start - task 9589c5c3-1180-4698-8b56-f716ace38872 finished, generated 1 videos.
__main__:_render_generation_controls - 视频生成完成
```

产品侧最终状态（Rust 桥实际读取的两处）：

```text
outputs/material-render-job-observation.json
{"failureCode":null,"outputFile":"material-result.mp4","progressPercent":100,
 "renderJobId":"5692eb19-49b1-49c8-9870-1e751da3a8ab","revision":9,"schemaVersion":1,
 "status":"succeeded","subject":"裁剪后的本机视频服务依然可用。这条视频由冻结包内的完整链路真实生成。",
 "workerTaskId":"9589c5c3-1180-4698-8b56-f716ace38872"}

ffprobe outputs/material-result.mp4
  codec_name=h264   codec_type=video   width=1080   height=1920
  codec_name=aac    codec_type=audio
  duration=8.070000 size=471689

subtitle.srt
  1
  00:00:00,100 --> 00:00:03,025
  裁剪后的本机视频服务依然可用
```

另外还在冻结包上走了「任务管理」浮层与「设置 → 大模型设置」对话框（Base Url、模型名称、
测试模型连接均正常渲染），没有出现任何 `ImportError`。

### 裁剪前的对照实验

在锁定的 uv 环境里用 meta path finder 屏蔽 `pyarrow,altair,tkinter,_tkinter,pydeck` 后，
走同一条 WebUI 用户路径，同样产出真实成片（9.23 s、1080×1920、h264+aac、528,703 B、含中文字幕）。
先证明「屏蔽后仍能成片」，再动打包配置，避免拿冻结构建当试验场。

同一实验把 `pandas` 一并屏蔽时，页面首屏即失败：

```text
ImportError: BLOCKED-BY-TEST: pandas
  streamlit/time_util.py:61 in time_to_seconds
  webui/Main.py:1055 in _render_task_manager_entry
```

这是 pandas 留在包里的直接依据。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 契约没声明排除清单 | 构建拒绝「排除模块契约缺失」 | 合成 |
| 契约清单里混入空串或非字符串 | 构建拒绝「排除模块契约无效」 | 合成 |
| 候选仍带排除模块目录 | 拒绝并点名模块 | 合成 + **裁剪前的真实候选** |
| 候选带排除模块的扩展文件（`_tkinter.*`） | 同上（glob 覆盖） | 合成 |
| 候选干净 | 通过 | 合成 |
| spec 又把排除清单写死 | 测试拒绝（断言 spec 读契约、且不含 `excludes=[]`） | 合成 |
| 包体超过声明负载 +10% | 出厂门禁拒绝 | 合成（既有 `test_oversized_package_is_rejected`） |
| 声明负载漏列某份资源 | 测试拒绝（键集合断言） | 合成 |
| 上限被改到脱离负载（过大或过小） | 测试双向夹住 | 合成 |
| 裁掉了运行时真需要的模块 | 冻结包真实成片验收会暴露 | **真实成片** |
| 契约变更后缓存复用旧产物 | 缓存键含契约摘要，自动重建 | 既有 `test_video_runtime_cache` |

## 未处理（需要产品/法务决定，不擅自裁）

按「不确定就不裁」原则，下面三项**没有动**，但都已核实清楚，合计还有约 300 MiB：

### 1. faster-whisper 全家（`onnxruntime` 59.1 + `av` 44.1 + `ctranslate2` 4.5 + `hf_xet` 6.8 ≈ 115 MiB）

- 引入者：`faster-whisper 1.1.0`（`uv tree` 直连依赖 `av`、`ctranslate2`、`huggingface-hub`、
  `onnxruntime`、`tokenizers`）。注意 `tokenizers` 同时被 `litellm` 需要，不能跟着一起裁；
- 产品可达性：`config.example.toml` 的 `subtitle_provider = "edge"`，WebUI **没有**任何切到 whisper 的入口
  （`grep subtitle_provider vendor/moneyprinterturbo/webui/` 无命中）。whisper 只在 edge 字幕失败时作为
  `subtitle_fallback` 触发；触发后 `subtitle.create()` 要找 `{root_dir}/models/whisper-large-v3/model.bin`，
  **包里没有这个模型**，于是退化成向 HuggingFace 下载 large-v3（约 3 GB）；
- 为什么不裁：`worker_main.py` 的 `runtime_probe()` 声明能力 `subtitle_transcription`，契约
  `probe.requiredCapabilities` 也列了它。裁掉模块而保留 `copy_metadata`，探针照样通过——那等于让门禁说谎。
  **这是一次产品能力取舍**（要不要保留一条离线不可用、在线会静默下载 3 GB 的字幕转写回退），不是打包优化，
  应该由产品决定后连同契约能力声明一起改；
- 顺带记一笔：现状下这条回退路径在用户机器上会**未经同意下载约 3 GB**，本身值得单独评估。

### 2. `upstream/resource/fonts` 141.8 MiB —— 体积之外还有许可证问题

```text
STHeitiLight.ttc          55.8 MiB   Apple 华文黑体（随 macOS 分发的系统字体）
STHeitiMedium.ttc         55.8 MiB   同上
MicrosoftYaHeiNormal.ttc  19.7 MiB   微软雅黑
MicrosoftYaHeiBold.ttc    16.9 MiB   微软雅黑（产品默认字幕字体）
其余 4 个开源字体          0.6 MiB
```

四个 `.ttc` 合计 148 MiB，都是 Apple / 微软的**专有字体**，随正式安装包再分发大概率没有授权。
这同时踩到项目 CLAUDE.md §6 的「资产权利清单」。
替换成可再分发的开源中文字体（思源黑体 Regular/Bold 约 16 MiB）可省约 130 MiB，
但会改变默认字幕外观，属于产品可见变更，不在本次范围。

### 3. `imageio_ffmpeg` 自带的 ffmpeg 二进制 46.9 MiB —— **不能现在裁，因为环境变量没接上**

排查结论与最初设想相反：

```text
frontend/src-tauri/src/video_media_toolchain.rs:186
    pub fn intelligent_material_environment(&self) -> BTreeMap<&'static str, &Path> {
        BTreeMap::from([("IMAGEIO_FFMPEG_EXE", self.ffmpeg.as_path())])
    }
```

全仓库搜索 `intelligent_material_environment` 只有两处命中：定义本身，和
`frontend/src-tauri/tests/video_media_toolchain.rs:95` 的单元测试。
**没有任何生产调用点**——`local_video_orchestrator.rs` 的 `spawn_worker()` 只在 Windows 隔离模式下改
`SystemRoot`/`HOME` 等，`VideoWorkerLaunch` 也没有注入自定义环境变量的能力。

于是上游 `app/utils/utils.py:get_ffmpeg_binary()` 的解析顺序在生产上实际是：

```text
1. IMAGEIO_FFMPEG_EXE      → 未设置，跳过
2. shutil.which("ffmpeg")  → 命中用户机器上的系统 ffmpeg
3. imageio_ffmpeg 自带     → 兜底
```

也就是说：**装了 Homebrew ffmpeg 的用户，产品当前会去用他系统里那份**，与项目「不发现、不选择、
不回退到系统组件」的原则相冲突；而且这份 ffmpeg 版本、编译选项、许可证都不在产品的声明范围内。
删掉包里那 46.9 MiB 之前必须先把 `IMAGEIO_FFMPEG_EXE` 真正接到 Worker 启动上
（`VideoWorkerLaunch` 加环境变量字段 → `spawn_worker` 注入 → 调用方传 `intelligent_material_environment()`），
这需要改 `local_video_orchestrator.rs` 与 `material_video_studio.rs` 并重新做 Rust 与桌面端验收，
本次没有做。**建议按缺陷单独立项**，修完可再省 46.9 MiB，并顺带消除「同一个包里两份未声明 ffmpeg」的
许可证不一致。

## 仍缺的验收

- 本次只在 macOS arm64 上裁剪与验证。Windows 的 `expectedInstalledDistributionCountByTarget` 是 115
  （多 `colorama`/`watchdog`/`win32_setctime`），排除清单跨平台通用，但 Windows 侧的体积与真实成片
  **未验证**；
- 未重建完整正式包做端到端出厂门禁复跑。`.local/eb-16` 里那个包用的仍是裁剪前的缓存产物
  （合计 1331.1 MiB，仍会被新上限拒绝）；契约摘要已变，下一次构建会自动重建 Worker 后落到
  1207.7 MiB。**正式包上的门禁通过与用户入口成片，留给下一次完整重建验收**；
- 本次未修改 `~/Library/Caches/automation-tool-build/` 下的缓存产物，裁剪后的候选留在会话临时目录，
  不影响正在进行的构建。
