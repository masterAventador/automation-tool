# 发布页视频来源接线（PB-07 剩余范围）

> 日期：2026-07-26
>
> 归属：**PB-07 剩余范围**，不新开任务 ID。`docs/embedded-browser-video-studio-roadmap.md`
> 的 PB-07 行保持 `🔍 待验收`（真实投稿归 PB-08），证据同时追加进
> `docs/development/PB-07.md`。
>
> 方案：`docs/development/PLAN-publish-video-source.md`。方案中有一条推荐解法被实测推翻，
> 见下面第 1 节。

## 起因

`docs/development/completed-task-wiring-audit-20260726.md` 第 3.2 节的两个断点：

- **A**：`main.tsx` 从不传 `selectedVideo`，而 `PublishWorkspace` 要求它有值才渲染发布
  按钮。发布页在正式 App 里永远没有视频可发，PB-01～PB-04 与 BU-02～BU-05 共八项已完成
  任务全部点不到。
- **B**：B站恒为「待配置」，全 App 没有配置入口。

排查这两条时又落出两条更要紧的：**平台参数根本没有进到执行链路**，以及**成片载荷的
文件名不带扩展名，执行器一定会拒**。

## 四条缺陷，各自怎么处理的

### 1. 成片载荷没有扩展名 —— 接线做完照样失败，而三层测试都发现不了

成片落在 `<artifacts>/<artifactId>/payload`，没有任何后缀；执行器
`backend/src/automation_tool/executor/rpa/douyin/publish_artifact.py` 的
`_require_media_type()` 要求**恰好一个** `.mp4`/`.mov` 后缀。把这个路径直接交给执行器，
每一次点「发布」都会 `DouyinPublishArtifactRejected`。

Rust 单测不启动执行器，UI Harness 用受控 Adapter，Python 测试自己造输入文件——三层各自
都绿，只有真机上的操作员会看到它。

**方案文档推荐的解法（硬链接）实测不成立。** 执行器的 `_validate_regular_file()` 要求
`st_nlink == 1`，而硬链接会让**原文件和链接两边**都变成多链接。实测：

| 交给执行器的文件 | 结果 |
| --- | --- |
| `payload`（成片载荷原件，无后缀） | 拒绝 |
| `<artifactId>.mp4`（普通副本，nlink=1） | **接受** |
| 指向副本的硬链接（nlink=2） | 拒绝，**且副本自己也同时变得不可接受** |
| `x.tar.mp4`（两个后缀） | 拒绝 |

所以实现用的是**副本**，不是硬链接：

- `VideoJobWorkspaceStore::stage_publishable_artifact(artifact_id)` 把载荷复制到
  `<app_data>/video-workspaces-v1/publish-staging/<artifactId>.mp4`；
- 连字符 UUID 自身不含点，所以暂存文件名恰好一个后缀；
- 复制沿用既有的 `copy_stable_file` + `ensure_free_space`，并把复制得到的摘要与长度**回
  对 manifest**，中途被替换的载荷会被拒而不是被发布；
- **没有放宽执行器任何一条现有判据**。

暂存是单槽的：每次暂存先清空上一份，发布走到任何终态都清，App 启动时也清一次（崩溃遗留
的整份视频副本不该留在数据目录里）。

### 2. 平台分派硬编码走抖音 —— 翻个开关就会把稿子发错平台

`begin_publish` 原来只用 `platform` 判定「能不能发」，之后一路 `current_douyin_profile()`
→ `PreflightDouyinPublish`。也就是说，一旦 `PublishWorkspace::new(false)` 改成 `true`，
点「发布到B站」会打开**抖音运营档案**执行**抖音发布流程**。

止损（本任务范围）：

- `PublishPlatform::route()` 把「怎么到达」变成平台自己的属性，`match` 穷举，加平台时
  编译器会问；
- `PublishWorkspace::begin()` 改为返回解析后的平台，调用方直接在它上面分派，不再自己
  重新推导；
- `begin_publish` 先分派再动任何东西：`OperationsBrowser` 走原链路，`NotIntegrated`
  直接 settle 成「未发布」并返回 `publish_platform_not_integrated`，**不碰浏览器、不碰
  运营档案**。

**没有**翻 `PublishWorkspace::new(false)`。B站仍按 Roadmap §2.7 属于首期实现范围，
凭据入口、OAuth、服务端可达是独立任务，真实凭据验收归 PB-08。

### 3. 契约 `artifactPath` → `artifactId`

原来 React 传本机绝对路径，`begin_publish` 直接 `PathBuf::from` 零校验；执行器只验
「是个合法的本地 mp4」，不验「是这个 App 产出的成片」。让 React 持有本机绝对路径同时
违反 `CLAUDE.md` §4.1 / §7 与专项 Roadmap §4.1。

现在：

- 命令入参是 `artifact_id: uuid::Uuid`，路径由 store 在 Rust 内解析；
- 可发布集合被收敛为 `role == "rendered_video"` 且 `media_type == "video/mp4"` 的**已登记
  Artifact**；
- 成片被删掉后再发布，得到的是「这条视频已经不在了」（`NotFound` → `publish_video_unavailable`），
  而不是一个存储故障。

顺带修掉 PB-07 自己登记的将就：**`publishJobId` 不再复用 `confirmationId`**。发布任务
标识由桥在 `begin_publish` 里生成并持有，`approve_publish` 从状态里取，页面不再传——页面
手里没有它，也就不可能让两次发布共用一个标识。

### 4. 撒谎文案

「该平台还没有配置发布凭据，**配置后即可使用**」——全 App 没有配置入口，服务端也没有授权
链路，这句话让操作员去找一个不存在的界面。改成：

> 该平台的发布通道还在接入中，暂时不能发布；其他平台不受影响。

契约 `contracts/publishing/publish-workspace.v1.json` 的 `awaitingConfigurationHint`
同步，并加了一条测试断言页面里不再出现「配置后即可使用」。

## 扩展名那条的跨层测试长什么样

`frontend/src-tauri/tests/publish_artifact_handoff.rs`（新增，3 条）。

它**不重写任何一侧的规则**——规则本身正是争议所在。Rust 真的产出文件，真的 Python 边界
真的去判：

```rust
fn executor_verdict(path: &Path) -> String {
    // cwd = backend/，跑 `uv run --locked python -c ...`，
    // 调真实的 open_publish_artifact()，回答 "accepted <media_type> <sha256>" 或 "rejected"
}
```

三条断言：

1. `what_the_bridge_stages_is_what_the_executor_accepts`
   —— 真的 import 一条成片 → `stage_publishable_artifact()` → 把得到的路径交给真实
   Python 边界，断言 `accepted video/mp4 <sha256>`，且这个 sha256 等于 Rust 侧算出来的；
2. `the_stored_artifact_payload_is_refused_by_the_executor`
   —— 把 `<artifacts>/<id>/payload` 直接交过去，断言 `rejected`。**这条就是暂存存在的
   理由**，也是「`artifactPath` 契约会导致每次点击都失败」的证据；
3. `a_hard_link_is_refused_by_the_executor_so_the_staged_copy_must_be_a_copy`
   —— 给暂存副本加一个硬链接，断言链接和副本**双双**变成 `rejected`；删掉链接后副本
   重新被接受。这条把「用硬链接省一次复制」这个显而易见的优化钉死在 CI 里，而不是钉在
   操作员面前。

跑一次约 0.5 秒（`uv run` 约 0.2 秒）。本机缺 `uv` 或 backend 虚拟环境时它会明确报错而
不是静默跳过——会静默跳过的话，这条测试就白写了。

## 成片到发布的完整用户路径

```
视频制作 → 成片 →〔某张成片卡片「去发布」〕   ← 唯一缺口，见下
    → 自动切到左侧「作品发布」
    → 顶部「待发布视频」卡片显示主题与大小，可「换一个」退回成片页
    → 填标题与简介（两项都可读之前，平台按钮 disabled）
    → 平台卡片上出现「发布到抖音」
    → 临界点显示目标账号 / 视频 / 标题 / 简介 → 确认发布
```

没选视频时，发布页不再是个死结：显示「还没有选定要发布的视频」并给一个「去选一条」按钮
直接跳到成片页。

**唯一缺口：成片卡片上的「去发布」按钮没有做**，因为它在
`frontend/src/features/video-studio/VideoStudio.tsx`，本次该目录由另一条工作线占用，
按约束未动。接收端全部就位（Shell 持有选中成片并能清、发布页渲染它、Rust 按
`artifactId` 取件），缺的是发起端那一次回调。补法是在成片卡片里加一个按钮 +
一个可选 prop：

```tsx
// VideoStudio 的 props 增加：
readonly onPublishArtifact?: ((video: SelectedVideo) => void) | undefined;

// 成片卡片里（motionArtifacts / artifacts 两处各一个）：
{onPublishArtifact === undefined ? null : (
  <Button
    type="primary"
    disabled={busy}
    onClick={() =>
      onPublishArtifact({
        artifactId: job.artifactId!,
        videoSummary: `${job.subject} · ${((job.artifactSizeBytes ?? 0) / 1024 / 1024).toFixed(1)} MB`,
      })
    }
  >
    去发布
  </Button>
)}

// WorkbenchShell 的调用点：
<VideoStudio gateway={materialVideoStudioGateway} onPublishArtifact={publishFinishedVideo} />

// 以及 WorkbenchShell 里新增（现在故意没写，因为写了就是死代码）：
const publishFinishedVideo = (video: SelectedVideo) => {
  setSelectedVideo(video);
  setActivePage("publishing");
};
```

这个缺口**写在测试里而不是备忘录里**：`src/app/production-wiring.test.ts` 新增一条
`it.fails("hands the finished-videos page a way to send one on to publishing")`，沿用该
文件已有的 `videoEditingGateway` 先例。按钮补上之后它会因为「预期失败却通过了」而报错，
那时把它移进常规用例即可。

## 失败矩阵

| 场景 | 结果 | 覆盖 |
| --- | --- | --- |
| 载荷无扩展名交给执行器 | 拒绝（暂存副本才是交出去的东西） | 跨层（真实 Python 边界） |
| 用硬链接代替复制 | 链接与副本双双被拒 | 跨层 |
| 暂存副本摘要 / 长度与 manifest 不符 | 拒绝，不发布 | Rust |
| 选中的成片不是成片（role 不对） | `publish_video_not_publishable`，不碰浏览器 | Rust |
| 选中的成片已被删除 | `publish_video_unavailable`，阶段回到已结束 | Rust |
| 非 v4 / 未知 artifactId | 拒绝 | Rust |
| B站（`NotIntegrated`）发起 | 明确「尚未接入」，**不碰浏览器与运营档案** | Rust（单元 + 源码级） |
| 未知平台 | `UnknownPlatform` | Rust |
| 执行器不可用 / 内置浏览器不可用 / 运营档案读不出 | settle 未发布，**并清掉暂存副本** | Rust |
| 预检返回终态、不可确认、文案不可读 | settle，清暂存 | Rust |
| 确认后派发（成功 / 失败 / 结果不明） | 清暂存（点击已经花掉了） | Rust |
| 派发前取消 | 清暂存 | Rust |
| App 崩溃后重启 | 启动时清掉遗留暂存副本，Artifact 不受影响 | Rust |
| 磁盘空间不足 | `ensure_free_space` 先拒，不留半份副本 | Rust（沿用既有配额） |
| 标题 / 简介为空、超长、含控制字符 | 按钮 disabled，请求发不出去 | 前端 |
| 没选视频 | 不渲染文案表单与发布按钮，改为提示 + 「去选一条」 | 前端 |
| 选了视频后「换一个」 | 清空选择并跳回成片页（不是先跳再清） | 前端（Shell 层） |

## 改动清单

**Rust**

- `frontend/src-tauri/src/video_job_workspace.rs`
  —— 新增 `publish-staging` 根目录、`StagedPublishArtifact`、
  `stage_publishable_artifact()`、`discard_staged_publish_artifacts()`；
  `generate_uuid_v4()` 转为 `pub`（全 crate 唯一的 UUIDv4 来源，不另造第二个）。
- `frontend/src-tauri/src/publish_workspace.rs`
  —— 新增 `PublishRoute` 与 `PublishPlatform::route()`；`PublishWorkspace` 新增
  `job_id`，`begin()` 改为收下发布任务标识并返回平台，`settle()` 清空它。
- `frontend/src-tauri/src/lib.rs` —— **改动全部落在 1250～1560 行的发布块内**：
  | 函数 | 行号区间 | 改了什么 |
  | --- | --- | --- |
  | `map_video_workspace_error`（新增） | 1270–1292 | 成片不可发布的原因映射成不泄漏本机路径的错误码 |
  | `begin_publish` | 1321–1492 | 生成发布任务标识；按 `route()` 分派；入参 `artifactPath` → `artifactId`；经 store 暂存；各失败路径清暂存 |
  | `approve_publish` | 1494–1561 | 去掉 `publish_job_id` 入参，改从状态取；派发后清暂存 |
  | `cancel_publish` | 1563–1592 | 注入 store，取消后清暂存 |

  （行号以本次改动落盘后的 `lib.rs` 为准；同期另一条工作线在同文件 500～630 行的
  `MotionRenderStageFailure` / `run_motion_render_job` 也有改动，与本次无重叠。）

  未触碰 `motion_runtime_paths`、`motion_worker_launch`、`submit_motion_video_draft`
  及其邻近代码；未触碰 `PublishWorkspace::new(false)` 那一行。

**契约**

- `contracts/publishing/publish-workspace.v1.json` —— `awaitingConfigurationHint` 改为
  不承诺做不到的事。

**前端**

- `frontend/src/features/publishing/publish-workspace-gateway.ts`
  —— `PublishRequest.artifactPath` → `artifactId` 并去掉 `publishJobId`；
  `PublishApprovalRequest` 只剩 `confirmationId`；新增 `isPublishableCopy()`（与 Rust
  同一条可读性判据，避免开完浏览器才发现文案不可用）；改文案。
- `frontend/src/features/publishing/PublishWorkspace.tsx`
  —— `SelectedVideo` 改为 `{ artifactId, videoSummary }`；新增「待发布视频」卡片、
  标题/简介表单、`onChangeSelection`（「换一个」/「去选一条」共用一个回调）；
  未填文案时按钮 disabled。
- `frontend/src/app/WorkbenchShell.tsx`
  —— Shell 持有选中成片状态（prop 只作初值），新增 `chooseAnotherVideo()`。
- `frontend/src/platform/tauri/publish-workspace-gateway.ts`、
  `frontend/src/test-harness/publishing.ts` —— 跟随契约。

**测试**

- 新增 `frontend/src-tauri/tests/publish_artifact_handoff.rs`（3 条跨层）；
- `frontend/src-tauri/tests/video_job_workspace.rs` 8 → 11；
- `frontend/src-tauri/tests/publish_workspace.rs` 23 → 27；
- `PublishWorkspace.test.tsx`、`WorkbenchShell.test.tsx`、
  `platform/tauri/publish-workspace-gateway.test.ts`、`production-wiring.test.ts`、
  `e2e/ui-harness.spec.ts`、`e2e-tauri/publishing.spec.ts` 跟随并新增。

## 验证

```text
cargo test                              全部 ok，0 失败（新增 publish_artifact_handoff 3 条）
cargo test --test publish_artifact_handoff   3 passed
cargo test --test video_job_workspace       11 passed（原 8）
cargo test --test publish_workspace         27 passed（原 23）
cargo test --test single_build_path          7 passed
uv run --locked pytest tests/unit -q    3166 passed, 1 skipped
npx vitest run                          476 passed | 2 expected fail（原 464 | 1）
node --test tests/*.test.mjs            237 pass, 0 fail
npx playwright test e2e/ui-harness        9 passed
npx eslint .                            退出码 0
scripts/check_user_facing_branding.py   passed
scripts/check_embedded_browser_video_roadmap.py  valid
```

`npx tsc -b --force` 退出码 2，两条错误都在
`src/features/legal/third-party-software/third-party-software-notice.test.ts`，属于同期
另一条工作线正在做的字体许可改动（`subtitle-fonts` / OFL-1.1），与本次改动无关，本次未碰
该文件。本次改动涉及的文件在 tsc 输出里零错误。

## 真实边界

1. **没有真实投稿，也没有碰真实抖音账号。** 用户给的硬约束是「发布一律不得真发布，必须
   走草稿箱」；本次连草稿箱都没走到——需要真实账号的部分**未跑**，归 PB-08。
2. **跨层测试证明的是「执行器会收下这个文件」，不是「抖音会收下这条稿子」。** 它到
   `open_publish_artifact()` 为止，没有打开浏览器，没有上传。
3. **成片页「去发布」按钮未做**（文件被占用），所以正式 App 里发布页目前仍然拿不到成片。
   接收端已全部就位，缺口写在 `production-wiring.test.ts` 的 `it.fails` 里。
4. **真实 Tauri App 桌面验收未跑。** `e2e-tauri/publishing.spec.ts` 已按新契约更新，但
   PB-07 已记录的那条边界仍在：本机没有暂存的执行器包与内置浏览器，debug 构建停在启动
   环境门，工作台不会挂载。
5. **`videoSummary` 仍由页面提供**。它是给操作员看的标签，决定发哪个文件的是
   `artifactId`；但严格说，摘要与文件由两个来源提供，仍可能不一致。收紧它需要让 Rust
   从 manifest 自己拼摘要，本次没做。
6. **B站只做了止损**，凭据入口、OAuth、`BilibiliAccessTokenProvider` 生产实现、FastAPI
   路由都没做，仍是独立任务。

## 清理

Playwright 用 headless（配置默认），9 条跑完自行退出；跨层测试的 `uv run` 是短进程；
Rust 测试的临时 AppData 目录由 `TemporaryRoot::drop` 删除。本次未启动任何常驻本地服务，
未启动 Tauri App，未启动浏览器窗口。
