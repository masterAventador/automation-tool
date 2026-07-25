# FIX 智能素材成片 Worker 移除未授权背景音乐资源

> 状态：🔍 待验收（裁剪、门禁、冻结包真实成片、正式包构建与 EB-16 出厂门禁全部通过；
> 但通过那一次用了 `--skip-build` 复用同一 work-dir 里刚构建好的包，跳过了执行器清单签名校验，
> 且构建时工作树被另一个会话并发修改，缺一次工作树独占下的不跳步完整通过记录）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：`FIX-material-worker-package-size.md` 裁剪完模块后重建正式包，出厂门禁在包内容审计处拒绝：
>
> ```text
> node scripts/audit-release-bundle.mjs --bundle-root ...
> Error: Release bundle is rejected
>     at assertSafePath (audit-release-bundle.mjs:151)
> ```

## 缺陷

`frontend/scripts/audit-release-bundle.mjs` 的 `forbiddenSuffixes` 把 `.mp3` 列为发布包禁止后缀，
本意是挡住误打包进来的用户媒体文件。智能素材成片 Worker 随包带了上游的 29 个背景音乐文件
（`_internal/upstream/resource/songs/output0*.mp3`，57,989,121 B / 55.3 MiB），正好撞上这条规则。

按项目原则，撞门禁先查产物，不先放宽门禁。查下来这批音乐**本来就不该随包发布**：

1. **权利不清**。`contracts/quality/third-party-notice-ui.v1.json` 的 `assetRights` 是
   `deniedByDefault: true`、`registeredEntryCount: 0`，并且专门为音乐预留了 `music_sfx` 类别却一条都没登记。
   也就是说本项目的资产权利清单从未批准过任何随包分发的音乐；
2. **可选功能**。字幕、配音、素材合成三条核心链路都不依赖它，去掉后仍能成片；
3. **占体积**。55.3 MiB 是裁剪后 Worker 的 11%，而且 WebUI 每开一次都会把 `resource` 整目录
   `copytree` 到本次任务的私有运行目录（`webui_runtime.py:_prepare_private_project`），
   等于每个渲染任务都多复制一份 55 MiB。

所以按 `FIX-material-worker-package-size.md` 的同一套做法裁掉它：契约声明 → spec 不打包 → 构建器审计失败关闭。

## 上游在没有 songs 目录时的行为核实

**结论：不会崩，优雅降级为「没有背景音乐」。** 三条实证，全部读的是 `vendor/moneyprinterturbo` 只读源码，未做任何修改：

| 入口 | 代码 | 无 songs 时的行为 |
| --- | --- | --- |
| `utils.song_dir()` | `app/utils/utils.py:106` | 目录不存在就 `os.makedirs(d)`。生产上 `webui_runtime.py:231` 把 `utils.root_dir` 重绑到**可写的**任务私有运行目录，所以这次 `makedirs` 一定成功，不会因为包内只读而抛 `PermissionError` |
| 「随机背景音乐」（WebUI 默认值） | `app/services/video.py:530-536` | `glob("*.mp3")` 取空 → `logger.warning("no bgm files found in song directory")` → `return ""` |
| 「自定义背景音乐」（用户填文件名） | `app/services/video.py:510-526` | `file_security.resolve_path_within_directory(..., require_file=True)` 抛 `ValueError` → 被捕获 → 记警告 → `return ""` |
| 消费端 | `app/services/video.py:1163-1166` | `if bgm_file:` 才建 `AudioFileClip`，空字符串直接跳过混音 |
| WebUI 的 `get_all_songs()` | `webui/Main.py:1132` | 全仓库无调用点，是死代码；即使被调用，`os.walk` 遍历不存在的目录也只返回空 |

实测复核见下面「真实成片验收」：默认就是「随机背景音乐」，日志里如实打出
`no bgm files found in song directory: ./resource/songs`，随后成片成功。

## RED

`scripts/test_material_video_worker.py`（10 → 14 个用例）：

```text
ERROR: test_candidate_carrying_an_excluded_upstream_resource_is_rejected
AttributeError: module 'build_material_video_worker_candidate' has no attribute
'assert_excluded_upstream_resources_absent'

ERROR: test_candidate_without_excluded_upstream_resources_is_accepted
AttributeError: module 'build_material_video_worker_candidate' has no attribute
'assert_excluded_upstream_resources_absent'

ERROR: test_contract_declares_the_excluded_upstream_resources
AttributeError: module 'build_material_video_worker_candidate' has no attribute
'excluded_upstream_resources'

FAIL: test_spec_ships_upstream_resources_without_the_excluded_ones
AssertionError: 'excludedUpstreamResources' not found in '# -*- mode: python ...'

Ran 14 tests — FAILED (failures=1, errors=3, skipped=1)
```

`scripts/test_embedded_browser_package.py`（35 个用例，改声明负载后该用例转红）：

```text
FAIL: test_release_size_bounds_admit_the_declared_production_payload
AssertionError: 1394606080 not less than or equal to 1335676108

Ran 35 tests — FAILED (failures=1)
```

`1394606080` = 旧的 1330 MiB 上限，`1335676108` = 新声明负载 1158 MiB 的 110%。
上限不再匹配声明负载，测试立刻拒绝——这正是这张表存在的意义。

## GREEN

```text
python3 scripts/test_material_video_worker.py      Ran 14 tests  OK (skipped=1)
python3 scripts/test_embedded_browser_package.py   Ran 35 tests  OK
```

新门禁对**改动前的真实候选**（`~/Library/Caches/automation-tool-build/material-video-worker`，
2137 文件那一版）确实拦得住：

```text
智能素材成片本机服务包被拒绝：候选仍包含产品不再分发的上游资源：songs
```

## 交付

### 契约（`contracts/quality/material-video-worker-package.v1.json`）

`build.excludedUpstreamResources: ["songs"]`，附 `excludedUpstreamResourcesReason` 写清权利依据。
与已有的 `excludedModules` 并列，spec 与构建器都从这一处读，不维护第二份清单。

### 打包（`workers/material_montage/material-video-worker.spec`）

原来是把整个 `resource` 目录作为一条 data 项打进去：

```python
(str(upstream_root / "resource"), "upstream/resource"),
```

改成逐项枚举、跳过被排除的目录，`resource` 下新增内容仍然自动打包：

```python
for entry in sorted((upstream_root / "resource").iterdir()):
    if entry.name in excluded_upstream_resources:
        continue
    ...
```

### 审计（`scripts/build_material_video_worker_candidate.py`）

- `excluded_upstream_resources(contract)`：读取并校验清单，缺失、空串、非字符串、含 `/` 或 `.`/`..` 一律拒绝
  （目录名而不是路径，避免清单本身变成路径注入面）；
- `assert_excluded_upstream_resources_absent(candidate, contract)`：候选里只要还有
  `_internal/upstream/resource/<名字>` 就拒绝并点名；
- 接进 `audit_candidate()`，与 `assert_excluded_modules_absent` 同级失败关闭。

契约摘要变了缓存键就变，下一次正式构建自动重建 Worker，不需要手工清缓存。

### 体积上限（`scripts/check_embedded_browser_package.py`）

`RELEASE_PAYLOAD_PARTS_MIB` 里 `material-video-worker` 由 520 → **465**（实测 484,123,149 B = 461.7 MiB）。
上限随之从声明负载推导：

```text
343 (Chromium) + 177 (执行器) + 465 (素材成片 Worker) + 108 (动效 Worker)
  + 43 (媒体工具链) + 22 (App 壳与前端资源)  =  1158 MiB
max_package_bytes = 1270 MiB  ≈ 声明负载 + 9.67%（测试夹在 +10% 以内）
```

## 裁剪前后对比

| | 裁剪前 | 裁剪后 | 差 |
| --- | ---: | ---: | ---: |
| Worker 文件数 | 2137 | 2108 | −29 |
| Worker 体积 | 542,112,270 B（517.0 MiB） | 484,123,149 B（461.7 MiB） | **−57,989,121 B（−55.3 MiB，−10.7%）** |
| 声明生产负载 | 1213 MiB | 1158 MiB | −55 MiB |
| 包体上限 | 1330 MiB | 1270 MiB | −60 MiB |
| 实测正式包 | （上一版会被 `.mp3` 直接拒绝） | 1,207,882,639 B（1152.0 MiB，2737 文件） | — |
| 每个渲染任务的私有 `resource` 复制量 | 约 203 MiB | 约 148 MiB | −55 MiB |

裁剪后逐项复查：`_internal/upstream/resource` 只剩 `fonts`、`public`；
`pandas`、`streamlit`、`moviepy`、`imageio_ffmpeg`、`av`、`onnxruntime`、`numpy`、`PIL`、`upstream` 全部在位；
`pyarrow`、`altair`、`pydeck`、`tkinter`、`_tkinter` 依旧不在。
全候选再扫一遍发布包禁止后缀（`.mp3/.mp4/.wav/.m4a/.mov/.mkv/.avi/.webm/.log/.key/.p12/.pfx/.sqlite/.sqlite3/.db-journal/.mobileprovision`）：零命中。

## 真实成片验收（裁剪后的冻结包，正常用户路径，默认选「随机背景音乐」）

按生产方式启动冻结可执行文件：向 stdin 写一条与 `local_video_orchestrator.rs:write_bootstrap`
完全同构的 bootstrap（`enableWebUi: true`、64 位十六进制会话令牌、`renderBrowser: null`、
`assetRoot` 指向 `video-workspaces-v1/jobs/<uuid>/work`），读回 ready 事件：

```text
READY_EVENT={"authenticationProof":"atvwp1.dDlJtL6tcPCk-tIFaH3zqMD-sMz_jVUBLOeRxdNrXow",
"event":"worker.ready","port":56122,"protocolVersion":"1.0","scriptModelId":null,
"webUiAuthenticationProof":"atvwp1.-yPIF7vhrijZRGpx09gqCg7bnhZPFBG0AiWr0S0WgTA",
"webUiPath":"studio-yvIXuIKKzD_irGmnTKVJpXYoj1cMARrEe2osGcI3_DY","webUiPort":56092,
"workerKind":"python","workerVersion":"1.3.2"}
```

私有运行目录里的 `resource` 只剩 `fonts`、`public`——证明打包裁剪一路传导到了任务运行时。

再用无头浏览器按真实用户路径操作这个 WebUI：关闭引导 → 填视频主题与文案 → 视频来源切「本地文件」→
上传三段用随包 ffmpeg 现造的 1080×1920 素材 → 点「生成视频」。
**背景音乐来源保持 WebUI 默认的「随机背景音乐」不动**，专门走降级路径。Worker 内部完整链路原文：

```text
app.services.task:start:333 - start task: e28ccf24-fc79-463b-80a7-5ae6820a107f, stop_at: video
app.services.task:generate_audio:156 - no custom audio file provided, using TTS to generate audio.
app.services.voice:azure_tts_v1:734 - start, voice name: zh-CN-XiaoxiaoNeural, try: 1
app.services.voice:azure_tts_v1:763 - completed, output file: ./storage/tasks/e28ccf24-.../audio.mp3
app.services.task:generate_subtitle:202 - ## generating subtitle, provider: edge
app.services.voice:_write_subtitle_items:1559 - completed, subtitle file created: .../subtitle.srt,
                                                duration: 5.763
app.services.task:get_video_materials:238 - ## preprocess local materials
app.services.video:combine_videos:559 - audio duration: 6.34 seconds
app.services.video:_prioritize_unique_source_clips:134 - prioritized unique video materials,
                                                         sources: 3, primary clips: 3
app.services.video:combine_videos:730 - concatenating 3 clips with ffmpeg
app.services.video:generate_video:962 - generating video: 1080 x 1920
app.services.video:generate_video:981 -   ⑤ font: ./resource/fonts/MicrosoftYaHeiBold.ttc
app.services.video:get_bgm_file:535 - no bgm files found in song directory: ./resource/songs
app.services.task:start:436 - task e28ccf24-fc79-463b-80a7-5ae6820a107f finished, generated 1 videos.
__main__:_render_generation_controls:3173 - 视频生成完成
```

`get_bgm_file` 那条 WARNING 就是本次裁剪的直接证据：默认「随机背景音乐」找不到任何音乐文件，
记一条警告后按「不加背景音乐」继续，**没有异常、没有中断、成片成功**。

产品侧最终状态（Rust 桥实际读取的两处）：

```text
work/.automation-tool-webui/<capability>/material-render-job-observation.json
{"failureCode":null,"outputFile":"material-result.mp4","progressPercent":100,
 "renderJobId":"53c00f79-253a-46c3-b24a-9fcba13e1243","revision":9,"schemaVersion":1,
 "status":"succeeded","subject":"移除随机背景音乐后，本机视频服务依然可以完成一次真实成片。",
 "workerTaskId":"e28ccf24-fc79-463b-80a7-5ae6820a107f"}

ffprobe outputs/material-result.mp4
  index=0  codec_name=h264  codec_type=video  width=1080  height=1920
  index=1  codec_name=aac   codec_type=audio
  duration=6.366667  size=208046

subtitle.srt
  1
  00:00:00,100 --> 00:00:01,900
  移除随机背景音乐后
  2
  00:00:02,200 --> 00:00:05,763
  本机视频服务依然可以完成一次真实成片
```

运行结束后 `resource/songs` 被上游 `song_dir()` 在**可写的任务私有目录**里自动创建为空目录，
没有触发任何权限错误——这条正是「不能直接删目录」的风险点，实测证伪。

## 正式包出厂门禁

`uv run --project backend python scripts/run_eb_16_acceptance.py`（工作目录 `.local/eb-16/run`，
开跑前已删除上一次失败留下的 `build/`）。最终结果：

```text
[EB-16] EB-16 acceptance passed: one ad-hoc signed macos-arm64 package with
331 browser files (359441871 bytes), package 1208260207 bytes, disk image 572717220 bytes
```

| 阶段 | 结果 |
| --- | --- |
| 确定性包门禁（`test_embedded_browser_package.py` 35 用例） | ✅ 通过 |
| 锁定 Chromium 149.0.7827.55 落位 | ✅ 通过 |
| 真实签名 Local Executor 候选构建 | ✅ 通过 |
| 生产模式 `.app` 构建（无测试特性） | ✅ 通过 |
| 三份视频运行时装配 | ✅ `['material-video-worker', 'media-toolchain', 'motion-video-worker']` |
| 内置浏览器逐文件校验并重新签名 | ✅ 通过 |
| DMG 产出 | ✅ `自动化运营工具_0.1.0.dmg`，572,717,220 B（546.2 MiB） |
| 体积上限（浏览器上下界 + 包体上限） | ✅ 331 浏览器文件 359,441,871 B，整包 2737 文件 **1,208,260,207 B（1152.3 MiB）**，在 1270 MiB 上限内 |
| **发布包内容审计（本次缺陷所在）** | ✅ **`[P9-05] Release bundle audit passed: 2406 files, 848818336 bytes`** |
| 生产包边界审计 `audit-production-package.mjs` | ✅ 通过 |
| 内外层代码签名 | ✅ 外层 ad-hoc 封包，内置浏览器保留上游 ad-hoc 链接器签名 |
| DMG 校验、挂载、安装 | ✅ 安装后负载与构建产物逐项相等 |
| 安装包启动门禁输入逐项检查 | ✅ 通过 |
| 从安装包离线启动内置 Chromium | ✅ `149.0.7827.55`，干净退出 |
| 卸载与残留检查（EB-17 干净机） | ✅ 通过 |
| 可见 App 启动阶段 | 默认跳过（需 `AUTOMATION_TOOL_EB16_LAUNCH_VISIBLE_APP=1`） |

正式包内五份资源实测：

| Resources 内的资源 | 文件数 | 体积 |
| --- | ---: | ---: |
| `embedded-browser` | 333 | 359,658,199 B（343.0 MiB） |
| `local-executor` | 284 | 184,686,384 B（176.1 MiB） |
| `material-video-worker` | 2108 | 484,123,149 B（461.7 MiB） |
| `motion-video-worker` | 3 | 113,124,957 B（107.9 MiB） |
| `media-toolchain` | 8 | 44,095,804 B（42.1 MiB） |
| 小计 | 2736 | 1,185,688,493 B（1130.8 MiB） |

包内 `material-video-worker/package/.../_internal/upstream/resource` 只剩 `fonts`、`public`；
整个 `.app` 里 `.mp3/.mp4/.wav/.m4a` 零命中。

## 中途撞上的另一道门禁：`audit-production-package.mjs` 与并发会话抢 `frontend/dist`

前两次完整跑都在这一关被拒：

```text
Error: Production build contains a desktop test marker
    at assertProductionBoundaries (check-production-boundaries.mjs:42)
```

原因不是本次改动，而是 `audit-production-package.mjs` 审计的是**磁盘上当次读取时刻的 `frontend/dist`**，
不是构建时嵌进二进制的那一份，而当时**同一个工作树上另一个会话正在连续跑 WebdriverIO 桌面 E2E**，
每一轮 `pnpm build:tauri:<套件>-test` 都会以 `desktop-e2e` 模式重写 `frontend/dist`：

```text
05:33:0x  EB-16 的 pnpm build 产出 dist/assets/index-Bx7eOxFL.js（1,397,979 B，无 wdio 标记）
          → 这一份被编进 .app
05:33:12  并发会话的 pnpm build:tauri:task-discovery-test 覆盖 dist
          → 变成 index-CuI6uJKc.js（1,413,770 B，含 @wdio/tauri-plugin 客户端代码）
05:33:5x  EB-16 走到内容审计，读到的是被覆盖后的 dist → 拒绝
```

判定依据（四条互相印证）：

- 单独重跑 `pnpm build` 稳定产出 `index-Bx7eOxFL.js`，`wdioTauri`/`plugin:wdio|` **零命中**；
- `@wdio/tauri-plugin` 只被 `src/test-*-main.ts(x)` 四个测试入口 import，生产入口 `src/main.tsx` 不引用；
- 对同一个 `.app` 单独跑发布包内容审计（同样扫 `wdioTauri`/`plugin:wdio|`/`TAURI_WEBDRIVER_PORT`）**通过**，
  证明产物本体没有测试标记，脏的只是磁盘上的共享 `frontend/dist`；
- 实时观察到并发进程 `wdio run wdio.default-profile-isolation.conf.ts`、
  `pnpm build:tauri:task-discovery-test`、`pnpm build:tauri:task-run-test`、
  `pnpm build:control-plane-e2e-assets` 与 `cargo test`，重建 dist 的间隔约 1–2 分钟；
  同时工作树里出现了本任务之外的一批修改文件（含 `frontend/src-tauri/src/*.rs`）。

处理方式：先 `pnpm build` 把 dist 恢复成生产产物，再用 `--skip-build` 复用同一 work-dir 里刚构建好的
`.app` 与 DMG 直接跑完剩余全部门禁，把「重建 dist → 读取 dist」的窗口从约 90 秒压到约 10 秒，一次通过。

**没有为了让门禁通过而放宽门禁**：`forbiddenDesktopTestMarkers`、`audit-production-package.mjs`、
`check-production-boundaries.mjs`、`audit-release-bundle.mjs` 一个字都没改。

顺带记一笔（不在本次范围，建议单独立项）：`audit-production-package.mjs` 用运行时刻的 `frontend/dist`
代表「这次构建的前端产物」，在同一工作树并发构建时既可能误报也可能漏报。
更严的做法是审计构建那一刻的 dist 快照（或直接审计已经嵌进二进制的资源），而不是事后重读共享目录。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 契约没声明排除资源清单 | 构建拒绝「排除上游资源契约缺失」 | 合成 |
| 清单里混入空串、非字符串、`/` 或 `..` | 构建拒绝「排除上游资源契约无效」 | 合成 |
| 候选仍带 `upstream/resource/songs` | 拒绝并点名 | 合成 + **裁剪前的真实候选** |
| 候选只带 `fonts` | 通过 | 合成 |
| spec 又把整个 `resource` 整目录打进去 | 测试拒绝（断言 spec 不含整目录那一行、且读契约） | 合成 |
| 无 songs 时上游 `song_dir()` 在只读位置建目录 | 生产上 `root_dir` 已重绑到可写任务目录，实测成功建空目录 | **真实成片** |
| 无 songs 时选「随机背景音乐」 | 记警告后按无背景音乐继续，成片成功 | **真实成片（默认选项）** |
| 无 songs 时填「自定义背景音乐」文件名 | `resolve_path_within_directory` 抛 `ValueError` → 捕获 → 返回空 → 无背景音乐 | 源码核实 |
| 声明负载与包体上限脱节 | 测试双向夹住（±10%） | 合成 |
| 正式包里还有任何 `.mp3` 等禁止后缀 | 发布包内容审计拒绝 | **真实正式包（本次已通过）** |
| 契约变更后缓存复用旧产物 | 缓存键含契约摘要，自动重建 | **本次实测重建** |

## 仍缺的验收

- **一次工作树独占、不跳步的 EB-16 完整通过**。本次通过用的是 `--skip-build`，它复用同一 work-dir 里
  刚构建好的 `.app` 与 DMG，因此**跳过了 `verify_manifest_signature`（执行器清单签名校验）**，
  其余门禁全部实跑；
- **本次构建的工作树不干净**。构建期间另一个会话在改 `frontend/src-tauri/src/*.rs` 等文件，
  两次构建的包体因此从 1,207,882,639 B 变成 1,208,260,207 B。也就是说这份 DMG 是
  「HEAD + 本次改动 + 另一个会话的在途改动」，不能当成本次改动的纯净发布物；
  正式发布前需要在干净工作树上重建一次；
- 可见 App 启动阶段本次未跑（EB-16 默认跳过，需 `AUTOMATION_TOOL_EB16_LAUNCH_VISIBLE_APP=1`）；
- 本次只在 macOS arm64 上裁剪与验证。Windows 侧的体积与真实成片**未验证**；
- **产品可见后果未处理**：上游 WebUI 的「背景音乐来源」默认仍是「随机背景音乐」，
  现在选它等于没有音乐；「自定义背景音乐」只能读任务私有目录下的 `resource/songs`，
  而该目录随任务创建、随任务销毁，用户没有正常途径往里放文件。也就是说
  **三个背景音乐选项现在实际都等价于「无背景音乐」**。
  这是产品取舍，不是打包问题：要么在产品侧隐藏这组选项，要么补一份有明确授权的音乐包并登记
  `assetRights` 的 `music_sfx`，要么让用户从本机选音频文件。上游 `webui/Main.py` 是只读 Submodule，
  不能在这里改，**建议按缺陷单独立项**；
- `FIX-material-worker-package-size.md` 里登记的三项未处理项（faster-whisper 全家约 115 MiB、
  `resource/fonts` 里 148 MiB 的 Apple/微软专有字体、`imageio_ffmpeg` 自带的 46.9 MiB ffmpeg）
  本次同样没有动。其中专有字体与本次的音乐属于同一类资产权利问题，优先级最高。
