# FIX 动效零件选择接入真实渲染

> 状态：**⛔ 未修复，停在方案**（调查完成；继续做需要主对话先决定产品语义，见第 6 节）
>
> 日期：2026-07-26
>
> 提交：本文件所在提交（**只有本文件，零生产代码改动**）
>
> 触发：`docs/development/completed-task-wiring-audit-20260726.md` 第 3.4 节断点 B——
> 用户在「视频制作 → 品牌动效成片 → 动效零件」勾选的零件对成片零影响。

## 0. 三句话结论

1. **渲染 Worker 不需要改协议，但它也不会"消费零件"**——它是一个与内容无关的截屏器，
   只认 `entryHtml` + `allowedAssets` + `frameCount`，把 `composition.html` 逐帧截下来。
   画面长什么样 100% 由**谁写这个 HTML** 决定。
2. **今天没有任何代码能把一个零件 id 变成分镜里的像素。** 不在 Rust，不在 Worker，
   不在 AI 编排代理，也不在 BM-16 验收。所以"把字段传下去"之后渲染仍然会忽略它——
   正是任务里警告的那种半成品。
3. 这不是"DTO 漏了一个字段"，是**整条设计上消费零件的链路（一句话自动制作）根本没接进 App**，
   与审计里 VE / Browser Use 是同一形态。补哪一条链、零件在固定模板里到底该长什么样，
   是产品决策。

## 1. 链路逐段实测：每一段现在传的是什么

| 段 | 位置 | 现在传什么 | 有没有零件 |
| --- | --- | --- | --- |
| 界面状态 | `VideoStudio.tsx:753` | `motionPartSelections`：`readonly (readonly string[])[]`，按段索引 | 有 |
| 传给目录组件 | `VideoStudio.tsx:902-906` | `selections` / `onSelectionsChange` | 有（只用于展示与勾选） |
| 段数变化时同步 | `VideoStudio.tsx:768-776` | 随段数 resize，避免新段的选择被静默丢弃 | 有 |
| **提交构造** | `VideoStudio.tsx:801-820` | `creationMode/subject/stylePresetId/primaryColor/secondaryColor/secondsPerBeat/beats/logo` | **无** |
| 前端 DTO | `material-video-studio-gateway.ts:30-40` | 同上 8 个字段 | **无** |
| Tauri 网关校验 | `platform/tauri/material-video-studio-gateway.ts:139-160` | 同上 | **无** |
| Rust DTO | `motion_video_studio.rs:125-136` | 同上，且 `deny_unknown_fields` | **无** |
| 工作区写盘 | `motion_video_studio.rs:573-637` | `SCRIPT.json` / `STORYBOARD.json` / `frame.md` / `style-freeze.json` / `composition.html` / `renderjob.json` | **无** |
| 唯一写进工作区的资产 | `motion_video_studio.rs:567-571` | 只有用户上传的 logo | **无** |
| 沙箱请求 | `lib.rs:497-507` | `work` 目录 + `composition.html` + `allowed_assets`（只可能含 logo） | **无** |
| Worker 渲染 | `worker.mjs:1089-1144` | 逐帧 `seek(t)` + `Page.captureScreenshot` | **无** |

`motionPartSelections` 全仓库只有 `VideoStudio.tsx:753` 与 `:904` 两处引用，实测：

```text
$ grep -rn "motionPartSelections" frontend/src/
frontend/src/features/video-studio/VideoStudio.tsx:753:  const [motionPartSelections, setMotionPartSelections] = useState<
frontend/src/features/video-studio/VideoStudio.tsx:904:                  selections={motionPartSelections}
```

顺带一个会咬人的细节：Rust DTO 带 `deny_unknown_fields`。**只在 TypeScript 侧加一个
`partSelections` 字段而不同步 Rust，不是"没生效"，是整个提交会被反序列化拒绝**，
用户会看到"品牌动效任务暂时无法提交"。半吊子改法比现状更糟。

## 2. 关键判断：Worker 能不能消费零件

必须把三件事分开，否则会得到相反的结论。

### 2.1 Worker 对内容完全无知（所以它不构成障碍）

`worker.mjs` 全文没有"风格"也没有"零件"的概念。它做的是：

- `validSandboxSpec`（`:294-319`）只校验 `workspace/entryHtml/allowedAssets/frameCount` 与四项预算；
- `resolveSandboxWorkspace`（`:326-374`）把入口和每个 asset 解析成工作区内的真实普通文件；
- 渲染循环（`:1089-1144`）对 `window.__timelines` 逐帧 `seek(t)` 再截屏。

**结论：任何最终落进 `composition.html`（内联标记 + 内联/本地脚本样式）的东西都会被渲染出来，
不需要动 Worker 协议。** 12 套整体风格今天就是这么"生效"的——`manual_composition()`
（`motion_video_studio.rs:1001-1068`）把颜色和风格名拼进 HTML，Worker 对此一无所知。

### 2.2 但 Worker 硬拒绝子文档（iframe 路线被封死）

`worker.mjs:884-899`：

```js
const allowedFileRequest = (url, isDocument) => {
  ...
  return isDocument ? path === resolved.entryReal : allowedPaths.has(path);
};
```

`Fetch.requestPaused` 处理（`:903-918`）里 `isDocument = params.resourceType === "Document"`。
所以：

- **子资源**（JS / CSS / 图片 / 字体）只要在 `allowedAssets` 里就放行；
- **子文档**（iframe）路径不等于入口文件，一律 `Fetch.failRequest` + `blockedNavigations += 1`。

134 项零件每一项都是**独立的整页 HTML**（`contracts/quality/motion-catalog.v1.json`，
其中 104 项是单 HTML 文件，最大一项 67 个文件，全部 286 个文件共 27.4 MiB）。
"把零件页面 iframe 进分镜"这条最直觉的路线**在当前 Worker 安全边界下不可能**，
除非改 `allowedFileRequest` 的文档规则——那就是扩展 Worker 协议，按任务要求不做。

留下的可行路线只有一条：**由某个"合成器"把零件的标记与脚本内联进 `composition.html`，
把它的样式/素材/依赖作为本地 asset 声明。** 这条不需要改 Worker。

### 2.3 这个"合成器"不存在——任何地方都没有

我逐处核对过：

| 位置 | 有没有"把零件插进分镜"的能力 | 证据 |
| --- | --- | --- |
| Rust 固定模板 | 没有。`manual_composition()` 是一段写死的单布局模板 | `motion_video_studio.rs:1001-1068` |
| Worker | 没有。见 2.1 | `worker.mjs` 全文 |
| BM-16 逐项渲染 | **没有**。它把**每一项零件当作一个独立 RenderJob 的 entryHtml 单独渲染**，不是插进某条片子 | `scripts/run_bm_16_acceptance.py:190-235`：`entries[0]` 作 entry，`files + shared` 作 allowlist，循环 134 次 |
| AI 编排代理 | 没有。零件只是模型在 `STORYBOARD.json` 里**声明**的 id；画面来自模型自己写的 `composition_html`，代理**根本没把零件源码给模型**，只给了 134 个 id 的列表 | `tools/motion-authoring/motion_authoring_agent.py:372-415`（校验 `catalog_parts` ∈ 锁定 id 集）、`:1025-1029`（提示词只列 id） |

也就是说，**"零件生效"这件事在整个仓库里从来没有实现过**，包括标记为 ✅ 的 BM-12/13/14
和 🔍 待验收的 BM-15/BM-16。BM-16 的"全部 134 项逐项预览/渲染"验的是"每项自己能渲染出来"，
不是"每项能被插进成片"。

## 3. 现状其实是"设计如此"，不是"忘了接线"

两处白纸黑字：

- `MotionPartsCatalog.tsx:72-74` 的界面原文：
  > 自动制作会按分镜自动选用零件，这里可以逐段查看并手工覆盖；**提交固定模板手工制作时不使用零件选用**。
- `docs/development/BM-15.md:86-88`：
  > App 端覆盖结果目前保存在草稿状态并在界面持续生效；App 内"一句话自动制作"尚未接入编排代理，
  > **覆盖结果注入真实 RenderJob 的端到端链路属后续 App 接入与 BM-16 验收范围**。

设计上消费零件的是**一句话自动制作**。而这条链路在 App 里**完全不存在**：

- `MotionAuthoringAgent` 在 `tools/motion-authoring/` 下，全仓库 grep 其消费方，
  Rust / TypeScript / backend **零命中**，只有 `scripts/run_bm_05_acceptance.py`、
  `scripts/run_bm_15_acceptance.py`、`scripts/test_motion_authoring_agent.py` 三个脚本；
- 唯一的动效提交命令是 `submit_motion_video_draft`（`lib.rs:395`），
  唯一合法的 `creation_mode` 是 `manual_template_v1`（`motion_video_studio.rs:164`）；
- 「新建视频」页对「品牌动效成片」只有一个「固定模板手工制作」卡片，
  那段"视频需求"输入框其实只是在编辑 `subject`（`VideoStudio.tsx:247-276`）。

**所以用户看到的真实情况是：唯一能走通的制作路径，恰好是被声明为"不使用零件"的那一条；
而使用零件的那条路径在 App 里点不到。** 那句免责说明埋在一段说明文字中间，
而页面其余部分（标签页可用、134 项可筛选、可勾选、"自动推荐"可点、"已选 N 项"实时更新）
全都在反向暗示它有用。审计说"界面已经在骗用户"这个判断成立，只是病因不是漏字段。

## 4. `releaseRoot` 那条是否阻塞

**不阻塞"把字段传下去"，但阻塞任何忠实的零件渲染。** 三点事实：

1. `contracts/video/motion-catalog-release.v1.json:6` 的 `releaseRoot` 是 `.local/motion-catalog-release`，
   `.gitignore:16` 忽略 `.local/`，**本机该目录不存在**（`ls .local/` 只有
   `eb-16 / embedded-browser-video-studio / offline-motion-deps / secrets / video-runtime` 等）；
2. `scripts/release_assembly.py:152-172` 的 `VIDEO_RUNTIME_RESOURCES` 只有 media-toolchain、
   motion-video-worker、material-video-worker 三项，**没有 catalog**；
3. 渲染沙箱默认断网（`worker.mjs:822-823` 把 http/https/ws 全指向死代理），
   零件的 HTML/JS/素材必须**先复制进 RenderJob 工作区**才可能被引用。

即：忠实实现（用上游那 134 份真实实现）需要先把 27.4 MiB 的零件源码 + 离线依赖
装进安装包并复制进工作区。这是一条独立的、有包体积后果的装配任务
（注意 `FIX-video-runtime-release-assembly.md` 已经记录当前包体积**超过 700 MiB 上限**，
dmg 尚未产出，再加 27.4 MiB 会让这个问题更紧）。

顺带量一下资产上限，方案讨论会用到：`SANDBOX_ASSETS_MAXIMUM = 128`
（`worker.mjs:48` 与 `local_video_orchestrator.rs:60`，`:257` 校验）。
按 BM-16 的算法（本项文件 + 共享 offline-deps），共享部分约 24 个文件——这是从
`motion-catalog-release.v1.json` 的 `generated.fileCount: 310` 减去清单里 286 个零件文件**推算**的，
不是实测（本机没有 release 产物）。单项典型因此约 25 个 asset，
一段选 16 项、10 段全选的极端情况会撞上限——**上限不是当前的阻塞点，但方案里必须写清楚 asset 预算怎么算。**

## 5. 为什么我没有直接动手实现

任务给的分支是「Worker 不支持 → 停在方案；支持 → 打通并证明生效」。
按第 2 节，Worker 协议不需要改，但**缺的不是协议，是合成器和零件源码**。
在这种状态下"打通"只有两种做法，两种都要求我替产品做决定：

- **要么**自己发明一套"零件在固定模板里长什么样"的视觉实现——那是凭 134 个中文名
  重新实现 134 个效果（其中 5 个名字连 `FIX-motion-parts-catalog-localization.md`
  自己都登记为"语义把握不足"），并且**直接推翻界面上那句"固定模板手工制作不使用零件选用"**；
- **要么**只把选择写进 `STORYBOARD.json` / `renderjob.json` 而画面不变——那正是
  任务明令禁止的「字段传过去了」，而且比现状更坏：会产生"冻结摘要变了 = 生效了"的假证据。

两条都不该由子代理单方面决定，所以停在方案。**未写任何生产代码，因此没有 RED/GREEN 环节**——
TDD 铁律约束的是"写生产代码前必须先有失败测试"，本次没有生产代码。

## 6. 可选方案（需要主对话决策）

| 方案 | 做什么 | 代价 | 副作用 |
| --- | --- | --- | --- |
| **A. 装配 + 内联合成器** | 把 catalog release 装进包 → Rust 按选择把零件文件复制进工作区 → 写一个把零件标记/脚本内联进 `composition.html` 并接到统一 `window.__timelines` 的合成器 | 最大。装配（含包体积上限冲突）+ 合成器（134 份独立 GSAP 页面合成到一条可 seek 时间轴）+ asset 预算 + 确定性回归 | 唯一"忠实"的方案；BM-16 的确定性门禁要重跑 |
| **B. 接 AI 一句话链路** | 给 App 加"一句话自动制作"入口 → Tauri 命令 → `MotionAuthoringAgent` → 模型写 composition | 大。要给 Python 代理找运行位置（Local Executor？）、模型服务凭据、失败矩阵 | 符合 BM-15 的设计意图；但零件仍只是"给模型的提示"，不是真实现，用户选了不等于画面里一定有 |
| **C. 在固定模板里自研零件效果** | 按分类/按项在 Rust 模板里实现有限的效果集 | 中。但要改界面那句话、要和 BM-11 锁定目录的语义对齐 | 用户选 A 零件得到的是我们自研的近似效果，**必须在界面说清楚**，否则是换了个更小的谎 |
| **D. 先止损** | 在固定模板路径下把零件选择明确标为不参与本次制作（置灰/移出标签页/显著提示），等 A 或 B | 小 | 不给用户新能力，但立刻停止误导；可与 A/B 并行 |

我的技术判断（**不是产品决定**）：D 立刻做、B 是设计意图、A 是唯一忠实终态、C 最容易再造一个谎。
A 与 B 不互斥——A 提供"零件真能出现在画面里"的能力，B 提供"谁来决定用哪些零件"。

## 7. 基线验证（真实输出，本次全部实跑）

改动为零，跑这些是为了确认当前树的基线，以及给后续实现一个可对照的起点。

```text
cd frontend/src-tauri && cargo test
  passed: 328   failed: 0                                    EXIT=0
  tests/motion_video_studio.rs：7 passed（7 条全绿，与 FIX-motion-video-duration 记录一致）

cd frontend && npx vitest run src/features/video-studio
  Test Files 6 passed (6)   Tests 44 passed (44)              EXIT=0

cd frontend && npx tsc -b --force                             EXIT=0
cd frontend && npx eslint .                                   EXIT=0

python3 scripts/check_user_facing_branding.py
  user-facing branding and plain-language scan passed (51 frontend, 247 native files)   EXIT=0
python3 scripts/check_motion_catalog_ui_projection.py
  check passed: 134 items, 11 categories, labels closed, names fully localized,
  no indicator or URL leakage                                 EXIT=0
```

`npx tsc -b --force` **本次退出码 0**，没有出现任务里预告的 `features/legal/` 报错——
说明并行会话此刻的树是可编译的。

第一次跑 `cargo test` 时我用了 `cargo test | tail -40`，拿到的 `$?` 是 `tail` 的退出码，
不是 `cargo` 的；已重跑并单独捕获退出码，上面 328/0/EXIT=0 是第二次的真实结果。

## 8. 失败矩阵

本次无代码改动，无新增失败路径。记录调查中确认的、**对后续实现有约束力**的既有边界：

| 场景 | 当前行为 | 出处 |
| --- | --- | --- |
| 提交请求带 Rust 不认识的字段 | 整个请求反序列化失败 → 提交被拒 | `motion_video_studio.rs:126` `deny_unknown_fields` |
| 合成里 iframe 引用零件页面 | `AccessDenied` + `blockedNavigations` 计数 | `worker.mjs:884-899, 903-918` |
| 合成引用远程零件资源 | 断网代理 + 请求 allowlist 双重拒绝 | `worker.mjs:822-823, 903-918` |
| 声明的 asset 超过 128 项 | `configuration_invalid`，不进 Worker | `local_video_orchestrator.rs:60, 257` |
| 零件文件不在工作区内 / 是符号链接 | `render_workspace_invalid` | `worker.mjs:326-374` |
| 合成注册不了 `window.__timelines` | `seekableDuration = 0`，逐帧截同一画面（静默退化成静止片） | `worker.mjs:1022-1041, 1097` |

最后一条是给方案 A/C 的提醒：合成器如果把零件的 GSAP 时间轴接错，**不会报错**，
只会产出一条不动的片子——和今天"选了没用"一样是静默失败。

## 9. 真实边界（我没做到的部分）

1. **没有做任何用户路径验收。** 未启动 App、浏览器、Playwright、WDIO、Tauri 构建（任务明令禁止）。
2. **没有渲染过任何帧。** 第 2 节关于 iframe 被拒的结论来自逐行读 `worker.mjs` 的
   `Fetch.requestPaused` 分支，**不是实测**。要落实方案 A 之前应该先用一个最小合成
   实测一次子文档确实被拒（成本很低：一个两文件工作区 + 一次 `_render_once`）。
3. **没有构建过 catalog release。** `.local/motion-catalog-release` 在本机不存在，
   我读的是 `contracts/quality/motion-catalog.v1.json` 的清单（134 项 / 286 文件 / 27.4 MiB）
   和 `scripts/build_motion_catalog_release.py` 的写盘逻辑，**没有实际产物**。
   所以"零件 HTML 内联进合成后能不能正常跑"完全未验证。
4. **没有读 134 项零件的实现。** 上游源码在只读 submodule 里，我没有逐项评估
   "能不能内联"。方案 A 的工作量估计因此是粗估，不是排期依据。
5. **没有核对 Windows。** 全部结论来自 macOS 树上的静态阅读与本机测试。
6. **`docs/development-roadmap.md` 与专项 roadmap 的状态没有动。** BM-12/13/14 仍是 ✅、
   BM-15/16 仍是 🔍。按台账规范，"✅ 已完成但产物从未到达用户机器"应当修正，
   但改状态属于台账决策，且与本次是否实现直接相关，留给主对话与实现任务同一提交处理。

## 10. 清理

未启动 App、浏览器、Worker、Playwright、WDIO、Tauri 构建或任何本地服务；无进程、端口、
容器、临时帧或临时 Profile 需要回收。未触碰 `.local/`、构建缓存、
`~/Library/Application Support/com.aventador.automationtool/`、`vendor/`、`backend/`、
`frontend/src/features/legal/`、`scripts/check_embedded_browser_package.py`、
`contracts/quality/material-video-worker-package.v1.json`。
未 `git add` / `git commit`。临时日志写在会话 scratchpad，未进仓库。

## 11. 文档

| 文件 | 改动 |
| --- | --- |
| 本文件 | 新增（本次唯一改动） |

## 12. 遗留项

| 项 | 状态 | 归属 |
| --- | --- | --- |
| 在 A/B/C/D 之间选定方向 | 待决策 | 主对话 |
| 固定模板路径下零件选择的止损（方案 D） | 未做 | 决策后 |
| catalog release 的装配路径与包体积影响 | 未做 | 与 `FIX-video-runtime-release-assembly.md` 的 700 MiB 上限一并处理 |
| 子文档确实被沙箱拒绝的实测 | 未做 | 方案 A 动手前的第一步 |
| 一句话自动制作的 App 入口 | 未做 | BM-15 遗留项，本次确认它仍然不存在 |
| BM-12/13/14 的 ✅ 状态是否应下调 | 待决策 | 与实现任务同一提交 |
