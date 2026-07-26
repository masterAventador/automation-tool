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

**结论：真实 App 用户路径验收未取得。不是"来不及"，是整条视频线的验收环境当前不可执行。**
下面是实测过程，不是推断。

### 做到哪一步

在 `wt/video-verify` 独立 worktree 上（干净树，避免掺入其他工作线的半成品）：

1. `pnpm install --frozen-lockfile` — OK；
2. `pnpm build:tauri:video-studio-test` — 首次失败，见下节「顺带查出的事故」；补齐后
   **构建成功**（52s，产出 `target/debug/automation-tool-desktop`）；
3. 资源装配：`media-toolchain`、`motion-video-worker/package` 从本机构建缓存
   （`~/Library/Caches/automation-tool-build/`，两者早已构建好）拷进资源目录；
   `embedded-browser` 用 `desktop_e2e_prerequisites.stage_embedded_browser` 正式装配 — OK；
4. 隔离 App 数据目录 `com.aventador.automationtool.vf06acceptance` 重建 + 装签名执行器包 — OK；
5. 跑 `motion-video-native.spec.ts` — **失败在第一步，工作台根本没挂载。**

打出 App 实际页面文字：

```text
桌面运行环境需要处理
业务功能保持关闭，处理下面的本机环境问题后重新检查。
  控制服务不可用
  本地执行器动作配置缺失   当前安装包没有完整的动作信任配置，请安装由管理员正式配置的版本。
```

### 根因（已被仓库自己记录在案）

`docs/development/desktop-e2e-run-20260726.md` 已经写明：`780abce` 删掉了
`video-studio-e2e` 构建里 `check_local_startup_environment` 的桩实现之后，
**整条视频线（5 个 spec）失去了可执行环境**，`motion-video-native`（BM-08）被单列为
「因驱动脚本仍依赖已废弃路径注入而未执行」。要跑起来还差三件事：

1. 编译期动作信任三元组（`AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY` /
   `..._LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS` / `..._LOCAL_ACTION_TASK_LIMIT`）必须在
   `tauri build` 时 `option_env!` 进去；
2. 一个可达的 Control Plane（要 Docker PostgreSQL + uvicorn，每个 control-plane 驱动各自
   起一套，没有可复用的单次调用）；
3. 签名执行器包（本次已装好，但被前两项挡在后面）。

该文件同时写明这套 harness「属于 VF-06 自己的任务范围，不是一次执行记录该顺手发明的东西」。
本次同样不发明它。

### 即便环境修好，本次改动仍有一半验不了

`video-studio-e2e` 展开成 `desktop-e2e`，其 `invoke_handler` **不含**
`get_publish_workspace`。所以在这个构建里点「去发布」只会落到「暂时读不到发布状态」，
看不到「待发布视频」卡片。带发布命令的是 `control-plane-e2e`，要用它得新增
`src-tauri/tauri.*.conf.json`、`e2e-tauri/*.spec.ts` 和 `wdio.*.conf.ts` —— 都在本次作业面之外。

### 顺带查出的事故：main 自 11:10 起编译不过

在干净 worktree 上构建时第一步就挂了，6 个 TS 错误全在本次改动之外：`c0cc760` 把发布口
类型从 `publishJobId`+`artifactPath` 改成 `artifactId`，只同步了 `features/publishing/` 下的
文件，漏了 `platform/tauri/publish-workspace-gateway.ts` 和 `test-harness/publishing.ts`
两个消费方。本机一直看着正常，是因为修复正躺在工作区未提交，把真实状态遮住了 ——
**共享工作树 + 未提交改动，会让 HEAD 坏掉而没人看见。** 已由 `7b776fd` 修复。

本次改动在 `7b776fd` 上复验：`npx tsc -b` exit 0；`npx vitest run` 59 文件 / 481 通过 /
1 条既有 expected fail。

据此本任务标 🚧，不标完成。补验收依赖见下面「未完成」。

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

验收尝试期间启动过一次隐藏窗口的隔离 App 与 tauri-driver，跑完核对 `pgrep` 无残留
（`automation-tool-desktop` / `tauri-driver` / `wdio` 均无进程）。用完删除：
`wt/video-verify` worktree（含其中装配的 Chromium、media-toolchain、Worker 包）、隔离
App 数据目录 `com.aventador.automationtool.vf06acceptance`。`wt/` 已写入
`.git/info/exclude`，不污染共享 `.gitignore`。

未运行 `scripts/run_u9_06_acceptance.py`，全程未触碰
`~/Library/Application Support/com.aventador.automationtool/`（用户手工扫码的抖音凭据在那里）。
未修改 `scripts/`、`src-tauri/`、`main.tsx` 及其他工作线占用的文件。

## 未完成（下一条工作线的输入）

按依赖顺序：

1. **执行器承载编排代理**：把 `tools/motion-authoring/motion_authoring_agent.py` 做成执行器
   一次性子进程（stdin/stdout，密钥不进 argv/env/日志）。
2. **`submit_motion_video_brief` Rust 命令**：接一句话 Brief，跑编排 → lint/check/snapshot →
   提交 RenderJob；工作区 seed 真实 `gsap.min.js`。
3. **前端 `one_sentence_v1` 制作方式**：网关加一个方法，「新建视频」页把那句话真正提交出去，
   进度沿用既有 `motionJobs` 轮询，成片沿用既有播放器——预览这一半不需要再造。
4. **恢复视频线的验收环境（挡在最前面，先做这个）**：编译期动作信任三元组 + 可达
   Control Plane + 签名执行器包，并把 `run_bm_08_acceptance.py` 里已废弃的
   `AUTOMATION_TOOL_BM08_*` 注入和依赖 `sleep 3` 浏览器包装的取消窗口重新设计。
   在这条修好之前，**「本地预览」这个 Demo 底线没有任何可执行的真实 App 证据**——
   注意资源本身不缺：`media-toolchain` 与 `motion-video-worker` 在本机构建缓存里都是现成的，
   拷进资源目录即可，挡路的是启动门禁那三项。
5. **真实 App 用户路径验收**：需要一个同时具备视频命令与发布命令的构建（`control-plane-e2e`
   系），走完 一句话 → 进度 → 播放预览 → 去发布 → 发布页显示待发布视频。
