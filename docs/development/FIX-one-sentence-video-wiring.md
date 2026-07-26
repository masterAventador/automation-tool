# FIX 一句话生成视频：编排与渲染之间的三处缺陷

> 状态：🚧 实现中（第一步：640×360 打通。编排 → 渲染 → 编码 已用真实模型、真实内置
> Chromium、真实 ffmpeg 走通并产出画面真的在动的 MP4；App 内 导入 → 预览 的接线尚未落地）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：客户 Demo 需要「一句话生成视频并在 App 内预览」。编排代理
> `tools/motion-authoring/motion_authoring_agent.py` 早已完整实现（BM-05 已用真实百炼模型验收），
> 逐帧渲染链路也已装进正式包（BM-16、FIX-video-runtime-release-assembly），但两者从未接过一次。

## 缺陷

把两半第一次真正接起来跑，产出了一个**合法但静止**的视频：

```text
[author]  composition compositions/main.html
[render]  captured 180 frames
[render]  distinct frames: 1 / 180
[encode]  brand-motion-result.mp4 (14952 bytes)
[encode]  ffprobe h264 640x360 duration 6.000000 nb_frames 180
```

180 帧逐字节相同。worker 报成功（它确实按要求拍满了帧数），ffmpeg 报成功（它确实编出了
合规的 6 秒 H.264），Artifact 入库也会成功。**每一层都是绿的，用户拿到的是一张静止的蓝色渐变图。**

三组对照实验定位出三个独立缺陷，每一个都足以单独把成片变成静图：

| # | 缺陷 | 证据 |
| --- | --- | --- |
| D1 | 舞台尺寸与捕获视口不一致 | 同一份 composition 加 `transform:scale(0.3333)` 后：25/30 帧不同 |
| D2 | 多个 clip 叠在一起不轮播 | 缩放后的画面里标题和「23%」互相压字 |
| D3 | 资源路径按工作区解析、浏览器按文档目录解析 | 同一份 composition 放到工作区根目录：`blockedRequests` 1→0，1/20 → 16/20 帧不同 |

### D1 舞台尺寸

`workers/motion_composition/worker.mjs:31` 把捕获视口硬编码为 640×360。而编排代理唯一的
构图说明书 —— `vendor/hyperframes/skills/hyperframes-core/references/minimal-composition.md`
（由 `contracts/video/motion-authoring-workflow.v1.json` 摘要锁定）—— 要求
`width: 1920px; height: 1080px`、`data-width="1920"`。

模型完全照做了。于是渲染器只拍到 1920×1080 画布的左上角六分之一，那里只有背景渐变。
`check_composition` 校验合成 id、时间轴、paused、`data-track-index`、`data-duration`，
**唯独不校验画布尺寸**，两边都看不见对方的数字。

### D2 clip 不轮播

参考文档只有一个 clip，从未说明多个 clip 如何分时。模型产出 5 个
`position:absolute; inset:0` 的场景、只有入场动画、没有任何一处让场景退场，
于是五幕永远同时在台上。`check_composition` 只要求 `data-track-index` 在文中出现过。

### D3 资源路径解析口径不一致

代理把合成写到 `compositions/main.html`，而 `allowed_assets` 是**工作区相对**的
`runtime/gsap.min.js`。浏览器按文档自身目录解析，实际请求
`compositions/runtime/gsap.min.js` —— 不在白名单，被沙箱拒绝。GSAP 未定义 →
内联脚本抛异常 → `window.__timelines` 始终为空 → 渲染器无处可 seek → 满帧相同。

`lint_composition` 把原始引用串直接和白名单比较，因此一路绿灯。这是三者中最隐蔽的一个：
它让**任何**合成都变成静图，与画布和 clip 无关。

## RED

```text
python3 scripts/test_motion_authoring_agent.py
  Ran 59 tests — FAILED (failures=6)
  # 6 条全部是「check 接受了它必须拒绝的东西」：canvas_mismatch / missing_canvas /
  # clip_interval_invalid / clip_overlap / clip_coverage / clip_visibility_uncontrolled

python3 scripts/test_motion_authoring_agent.py     （提示词契约单独 RED）
  Ran 62 tests — FAILED (failures=3)
  AssertionError: '640' not found in <首轮提示词>

python3 scripts/test_motion_authoring_agent.py     （D3 单独 RED）
  ImportError: cannot import name 'COMPOSITION_PATH'

cargo test --test motion_video_studio
  error[E0432]: no `rendered_film_is_static` in `motion_video_studio`
  error[E0599]: no variant named `StaticRender` found for enum `MotionRenderFailureCode`

npx vitest run src/platform/tauri/material-video-studio-gateway.test.ts
  Tests 1 failed | 8 passed
  MaterialVideoStudioGatewayError: { code: 'protocol_mismatch' }   ← 网关白名单不认识 static_render

npx vitest run src/features/video-studio/VideoStudio.test.tsx
  Tests 1 failed | 15 passed
  Unable to find an element with the text: /画面自始至终没有变化/
```

条数逐次核对：`test_motion_authoring_agent` 49 → 59（画布与 clip 门禁 10 条）→ 62
（提示词契约 3 条）→ 67（D3 入口相对解析 5 条）；`motion_video_studio.rs` 7 → 13
（静图门禁 6 条）；`material-video-studio-gateway.test.ts` 8 → 9；`VideoStudio.test.tsx` 15 → 16。

## GREEN

```text
python3 scripts/test_motion_authoring_agent.py            Ran 67 tests OK
python3 scripts/run_bm_05_acceptance.py                   BM-05 acceptance passed（真实百炼模型）
cargo test --test motion_video_studio                     13 passed
cargo test --tests -- --test-threads=4                    24 个二进制 / 198 passed / 0 failed
npx vitest run src/features/video-studio \
    src/platform/tauri/material-video-studio-gateway.test.ts   61 passed (7 files)
npx tsc -b                                                OK
```

`cargo test` 默认并行度下 `executor_manager` 有 4 条 `TimedOut`。单独跑
`cargo test --test executor_manager` 19/19 通过，降并行度后全量 198/198 通过 ——
是满负载下的启动超时抖动，不在本次改动触及的模块（本次只改
`motion_video_studio.rs` 与 `lib.rs` 的渲染收尾）。

## 交付

### 三个门禁

1. **静图门禁**（`motion_video_studio::rendered_film_is_static`，接在
   `lib.rs::run_motion_render_job` 编码之前）。逐帧比对摘要，遇到第一帧不同就短路返回；
   全同则判 `MotionRenderFailureCode::StaticRender`，不进编码、不入库。单帧成片永远不判静图；
   帧文件缺失返回 `StorageUnavailable` 而不是给出「静图」结论 —— 不完整的捕获不能被当成已定的判断。
   **这条是三者里最有价值的**：它把这一类缺陷从静默变响亮，与具体成因无关。
2. **画布门禁**（`check_composition` → `canvas_mismatch` / `missing_canvas`）。新增
   `contracts/video/motion-render-canvas.v1.json` 作为捕获视口的唯一声明，worker、编排代理和
   Rust 三边共读。第二步升 720p 时只改这一处数字加 sandbox spec 两个字段。
3. **clip 轮播门禁**（`check_composition` → `clip_interval_invalid` / `clip_overlap` /
   `clip_coverage` / `clip_visibility_uncontrolled`）。要求每个 clip 声明区间、区间首尾相接铺满
   时间轴，且多 clip 时 `.clip` 基础状态隐藏、每个 clip 在时间轴上有自己的显隐控制。

### D3 入口相对解析

`lint_composition` 新增必填 `entry_path`，引用先按文档目录解析再比对白名单（`_resolve_from_entry`），
爬出工作区的引用同样判 `undeclared_asset`。合成落点由 `compositions/main.html` 改为工作区根目录的
`composition.html` —— 与沙箱白名单的锚点一致，也与固定模板早就在用的 `MOTION_COMPOSITION_FILE` 一致。

### 编排契约

门禁如果不告诉模型，就只会把每一轮草稿判掉、白烧修正轮次。`_SYSTEM_RULES`、
`_first_message_contract`、`_fix_message_contract` 三处同步写明：舞台必须正好是捕获视口
（并明说参考资料里的 1920×1080 不适用）、字号按小画布设计、clip 用
`tl.set("#id", { autoAlpha: 1 }, start)` / `autoAlpha: 0` 轮流出退场。

### 用户可见文案

静图失败有自己的说法：「这条成片的画面自始至终没有变化，已经停下来没有生成视频。换一句更具体的
描述重新制作通常就能解决。」原来的「请检查视频组件与磁盘空间」会把用户支去修一个根本没坏的东西。
网关白名单同步接受 `static_render`，否则精确的失败会变成整条快照被拒、在界面上表现为任务凭空消失。

## 真实边界（生产同路径验收）

真实百炼 `qwen3.7-max-2026-06-08` → 正式 `MotionAuthoringAgent.author` → 正式
`lint`/`check`/`snapshot` → 正式 `worker.mjs` 渲染沙箱 + 真实内置 Chromium 149 → 正式 ffmpeg：

```text
[author]  composition composition.html
[author]  allowed assets ['runtime/gsap.min.js']
[render]  captured 180 frames
[render]  distinct frames: 92 / 180
[encode]  brand-motion-result.mp4 (81126 bytes)
[encode]  ffprobe h264 640x360 duration 6.000000 nb_frames 180
```

抽 3 帧核对画面：第 20 帧「本周销售增长报告 / Weekly Sales Growth Report」、第 75 帧
「+23% 环比增长」、第 140 帧「继续保持势头」。三幕依次出场、互不压字、满幅构图。

**尚未验收：** App 内 导入 → 预览 的正常用户路径。编排代理仍在 `tools/` 下，未进执行器包，
`submit_motion_video_brief` 命令与前端 `one_sentence_v1` 制作方式都还没有落地。
因此本任务当前只能是 `🚧 实现中`，不能标完成。

## 失败矩阵

| 场景 | 行为 |
| --- | --- |
| 舞台尺寸与捕获视口不一致 | `canvas_mismatch`，进入有界修正；修不好则拒绝提交 RenderJob |
| 合成未声明舞台 | `missing_canvas`，同上 |
| clip 区间重叠 / 留空 / 缺属性 | `clip_overlap` / `clip_coverage` / `clip_interval_invalid` |
| 多 clip 但无显隐控制 | `clip_visibility_uncontrolled` |
| 资源引用按文档目录解析后不在白名单 | `undeclared_asset`（含爬出工作区的 `../..`） |
| 渲染成功但满帧相同 | `StaticRender`，不进编码、不入库，界面给出针对性文案 |
| 帧文件缺失 | `StorageUnavailable`，不给出「静图」结论 |
| 单帧成片 | 永远不判静图 |
| 画布契约损坏 / 漂移 | `MotionAuthoringRejected`，fail closed |

## 清理

真实验收工作区在 scratchpad 下按运行隔离，帧目录跑完即删；每轮渲染的 Chromium 与 node worker
由 `WorkerSession` 在成功、失败、超时三条路径关闭，收尾核对 `pgrep` 无残留。
密钥仅运行时读自 git-ignored `.local/secrets/bailian-model.json`，从不打印、断言或写入产物。

## 下一步

1. 执行器 sidecar 承载编排代理（一次性子进程，stdin/stdout，密钥不进 argv/env/日志）；
2. `submit_motion_video_brief` Rust 命令 + 工作区 seed 真实 `gsap.min.js`；
3. 前端 `one_sentence_v1` 制作方式（`USAGE_BY_CREATION_MODE` 保持 `browse_only`）；
4. 正式包内正常用户路径验收；
5. 第二步：升 1280×720，sandbox spec 增加 `viewportWidth`/`viewportHeight`。
