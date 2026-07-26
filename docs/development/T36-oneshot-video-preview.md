# T36 一句话生成视频的 App 内闭环与本地预览

> 状态：🚧 部分完成 —— 成片「去发布」交接已实现并通过分层门禁；**一句话生成链路与其真实
> App 用户路径验收被作业面边界挡住，未完成、不 claim**。
>
> 日期：2026-07-26
>
> 提交：本文件所在提交
>
> 触发：客户 Demo 底线是「一句话生成视频能做完，并且可以本地预览」。
> `docs/development/FIX-one-sentence-video-wiring.md` 已经把生成侧打通（真实模型编排 →
> 真实内置 Chromium 逐帧渲染 → 真实 ffmpeg 编码，180 帧里 92 帧不同），并明确写下
> 「App 内 导入 → 预览 的接线尚未落地」。本任务承接那条「下一步」。

## 摸底结论（先查现状，不假设）

| 问题 | 事实 | 出处 |
| --- | --- | --- |
| App 内能不能播本机 mp4？ | **能，而且早就在跑。** `ArtifactPage` 已有 `<video>` 播放器，源来自 `readMotionArtifact` 返回的 base64 data URL | `frontend/src/features/video-studio/VideoStudio.tsx` |
| 预览要不要动 `asset:` 协议 / CSP / capability？ | **不要。** 生产 CSP 已声明 `media-src 'self' data: blob:`，data URL 播放不需要文件系统权限，也不需要新增 Tauri capability | `frontend/src-tauri/tauri.conf.json` |
| 预览有没有真实 App 验收过？ | **有。** BM-08 桌面 E2E 在真实 Tauri App 里点「播放成片」，断言 `readyState`、`duration`、`currentTime` 并把解出的字节写成证据 mp4 | `frontend/e2e-tauri/motion-video-native.spec.ts` |
| 「一句话」提交入口存在吗？ | **不存在。** 全仓库没有 `submit_motion_video_brief`、没有 `one_sentence_v1`；生产与三套 e2e 的 `invoke_handler` 里只有 `submit_motion_video_draft`（固定模板手工制作） | `frontend/src-tauri/src/lib.rs` |
| 编排代理在哪？ | 仍在 `tools/motion-authoring/motion_authoring_agent.py`，没有进执行器包，App 无法调用 | `tools/` |
| 成片能不能送去发布？ | 接收端齐了（`WorkbenchShell` 持 `selectedVideo`、`PublishWorkspace` 渲染它、Rust 按 `artifactId` 取件），**发起端那个按钮缺席** | `production-wiring.test.ts` 的 `it.fails` |

所以本任务真正能在作业面内完成的，是最后一行：成片页「去发布」。

## RED

```text
cd frontend && npx vitest run src/features/video-studio/VideoStudio.test.tsx \
                             src/app/production-wiring.test.ts

  × hands the finished-videos page a way to send one on to publishing 1ms
      Error: VideoStudio is never given a publish handoff
  × hands a finished video on to publishing 1084ms
      TestingLibraryElementError: Unable to find role="button" and name "发布季度增长"

  Test Files  2 failed (2)
       Tests  2 failed | 29 passed | 1 expected fail (32)
```

两条 RED 分别盯住同一个洞的两端：组件里没有按钮，装配里没有把回调传下去。
`production-wiring.test.ts` 那条原本写成 `it.fails`（把现状留在 CI 里而不是备忘录里），
本次改回常规 `it`，它立刻转红——这就是它当初留下来的用途。

## GREEN

```text
cd frontend && npx vitest run src/features/video-studio src/app/production-wiring.test.ts
  Test Files  7 passed (7)        Tests 67 passed | 1 expected fail (68)

cd frontend && npx vitest run
  Test Files  59 passed (59)      Tests 483 passed | 1 expected fail (484)

cd frontend && npx tsc -b         exit 0

python3 scripts/check_user_facing_branding.py
  user-facing branding and plain-language scan passed (51 frontend, 250 native files)
python3 scripts/check_embedded_browser_video_roadmap.py
  specialized roadmap status and per-task evidence are valid
python3 scripts/cq_04_ledger_honesty.py       exit 0
```

剩下那 1 条 `expected fail` 是既有的 `videoEditingGateway`，与本次改动无关。

## 交付

### 成片页「去发布」

- `VideoStudio` 新增可选 `onPublishArtifact`；成片卡片上出现「去发布」，无障碍名称
  `发布{片名}`。**可选**是有意的：没有接收端的外壳不该凭空多出一个点了没反应的按钮，
  为此单独留了一条用例守住。
- 两种制作方式的成片都放行。Rust 侧两条导入路径写的都是 `role = "rendered_video"`，
  `stage_publishable_artifact` 认的也是这一个角色，所以只放行其中一种没有依据。
- 交接内容是 `SelectedVideo { artifactId, videoSummary }`，类型直接从
  `features/publishing/PublishWorkspace` 引入，不另立一份结构相同的定义。
  `videoSummary` 拼成「片名 · 制作方式」，制作方式名从 `VIDEO_CREATION_METHODS` 里查，
  不再手写一遍——发布页要把这句话回显给用户确认发的是哪一条，它和用户点过的那张卡片
  必须永远一致。
- `WorkbenchShell` 新增 `publishSelectedVideo`：**记录选中 + 切页是同一个动作**。
  只记录不切页，用户会停在成片页不知道发生了什么；只切页不记录，用户会落在一个仍然说
  「还没有选定要发布的视频」的发布页上。它是既有 `chooseAnotherVideo` 的返程。

## 真实边界（生产同路径验收）

**本次没有取得真实 App 用户路径验收证据，原因是作业面边界，不是"来不及"。** 如实登记：

已经具备的真实证据（既有，非本次产生）：真实 Tauri App 里从正常菜单进「视频制作」、编辑、
提交真实渲染、在 App 内播放解码出的 mp4——这条由 `motion-video-native.spec.ts` +
`scripts/run_bm_08_acceptance.py` 覆盖，隔离 App 标识 `com.aventador.automationtool.vf06acceptance`、
隐藏窗口。**本次改动没有触碰这条链路上的任何一环。**

本次改动缺的那半条，卡在两处：

1. **唯一能挂载工作台的 e2e 构建里没有发布命令。** `video-studio-e2e` 展开成
   `desktop-e2e`，该 `invoke_handler` 不含 `get_publish_workspace`，所以在那个 App 里点
   「去发布」会落到「暂时读不到发布状态」，看不到「待发布视频」卡片。带发布命令的是
   `control-plane-e2e`，要用它就得新增 `src-tauri/tauri.*.conf.json`、`e2e-tauri/*.spec.ts`
   和 `wdio.*.conf.ts`——三者都在本次作业面之外。
2. **本地还没有可用的动效 Worker 包。** `motion_runtime_paths` 已按单一构建路径规范改成
   只从资源目录解析（`AUTOMATION_TOOL_BM08_*` 那套构建期分叉已被
   `tests/single_build_path.rs` 明令禁止），而 `target/debug/` 下只有 `embedded-browser`，
   没有 `motion-video-worker/package`、没有 `media-toolchain`，因此现在直接跑真实渲染必然
   `render_unavailable`。补装配要动 `scripts/`，同样在作业面之外。

据此本任务标 🚧，不标完成。补验收依赖已写在下面「未完成」。

## 失败矩阵

| 场景 | 行为 |
| --- | --- |
| 外壳没有接收端（未装配、测试外壳） | 不渲染「去发布」，而不是渲染一个点了没反应的按钮 |
| 同一制作方式下多条成片 | 按钮无障碍名称带片名（`发布{片名}`），不会误发另一条 |
| 两种制作方式的成片 | 都能送去发布；两者角色同为 `rendered_video`，发布端取件判据一致 |
| 成片在选中之后、发布之前被删掉 | 既有行为不变：Rust `stage_publishable_artifact` 返回 `NotFound`，让用户重新挑，而不是报存储故障 |
| 用户选错了要换一条 | 既有 `chooseAnotherVideo` 先清掉选择再回成片页，不会留着旧选择 |
| 正在忙（busy） | 按钮禁用，避免连点造成选择在切页途中被改写 |

## 清理

本次只改前端源码与测试，没有启动浏览器、模拟器、渲染进程或本地服务，因此无进程、无
Profile、无临时文件需要回收。未运行 `scripts/run_u9_06_acceptance.py`，未触碰
`~/Library/Application Support/com.aventador.automationtool/`。

## 未完成（下一条工作线的输入）

按依赖顺序：

1. **执行器承载编排代理**：把 `tools/motion-authoring/motion_authoring_agent.py` 做成执行器
   一次性子进程（stdin/stdout，密钥不进 argv/env/日志）。
2. **`submit_motion_video_brief` Rust 命令**：接一句话 Brief，跑编排 → lint/check/snapshot →
   提交 RenderJob；工作区 seed 真实 `gsap.min.js`。
3. **前端 `one_sentence_v1` 制作方式**：网关加一个方法，「新建视频」页把那句话真正提交出去，
   进度沿用既有 `motionJobs` 轮询，成片沿用既有播放器——预览这一半不需要再造。
4. **本地视频运行时装配**：`media-toolchain` 与 `motion-video-worker/package` 进
   `target/debug/`，否则本机跑不了真实渲染。
5. **真实 App 用户路径验收**：需要一个同时具备视频命令与发布命令的构建（`control-plane-e2e`
   系），走完 一句话 → 进度 → 播放预览 → 去发布 → 发布页显示待发布视频。
