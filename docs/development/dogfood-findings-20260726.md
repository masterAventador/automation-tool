# 2026-07-26 正式包试用发现清单

> 来源：用户在 macOS 正式包（`.local/eb-16/run/.../自动化运营工具.app`）上按真实路径逐页试用。
>
> 状态：收集中，待逐条修复。每条修完在此登记提交号。

## 1. 视频制作完全不可用（真实缺陷，高优先级）

**现象**

| 位置 | 报错 |
| --- | --- |
| 智能素材成片 | 本机视频制作服务暂时无法启动，请稍后重试。 |
| 品牌动效成片 → 预览 | 本机渲染组件暂时不可用，请到设置与诊断检查组件。 |

**根因**

正式包 `Contents/Resources/` 只有 `embedded-browser` 与 `local-executor`，而生产代码要找三样：

| 生产代码要找 | 出处 | 缺失后果 |
| --- | --- | --- |
| `Resources/media-toolchain/`（ffmpeg、ffprobe） | `video_media_toolchain.rs` `TOOLCHAIN_DIRECTORY` | `render_unavailable` |
| `Resources/motion-video-worker/package` | `lib.rs` `motion_runtime_paths` 生产分支 | 品牌动效无法渲染 |
| `Resources/material-video-worker/package` | `material_video_studio.rs` `worker_executable` | `process_unavailable` |

`tauri.conf.json` 的 `bundle` 段只有 `"active": true`，**没有 `resources` 声明**——这三样没有任何生产装配路径。

**为什么测试全绿**

`lib.rs:339` 与 `material_video_studio.rs:511` 都有 `#[cfg(feature = "video-studio-e2e")]` 分支：测试构建从
`AUTOMATION_TOOL_BM08_WORKER` / `_BROWSER` / `_FFMPEG` / `AUTOMATION_TOOL_IM05_WORKER` 环境变量读路径，
由验收脚本现场构建好再指过去。BM/IM 线每个任务的渲染都真跑通了，跑的是这条分支；生产分支从来没人走过。

**与 EB-16 的关系**

同一个病。EB-16 已经暴露过一次「内置浏览器是验收脚本装进去的、常规构建产出的包不含浏览器」，
当时加了 `scripts/release_assembly.py` 修掉浏览器那一份，**没有回头查还有没有别的资源同样如此**。
三样里漏了三样。

**修复方向**：扩展 `release_assembly.py` 覆盖这三份资源，并加生产装配完整性门禁。

## 1b. 媒体工具链构建脚本在 macOS 上直接失败（同一病根的第三种形态）

重建 ffmpeg 时暴露：`scripts/build_video_media_toolchain.sh:104`
`FFMPEG_STATIC_LINK_FLAGS[*]: unbound variable`。macOS 自带 bash 3.2，`set -u` 下空数组的
`[*]` 展开被判为未绑定；macOS 分支正是把该数组留空。

VF-04 台账记载「macOS arm64 已通过源码构建」并附 ffmpeg/ffprobe SHA-256，该记录当时属实。
但 `FFMPEG_STATIC_LINK_FLAGS` 是 2026-07-24 补 Windows 支持时新增的变量，**新增后只在 Windows
验证，未回 macOS 重跑**，因此台账中 macOS 那条证据实际早于该改动。

**修复**：改用 `${FFMPEG_STATIC_LINK_FLAGS[*]:-}`。已验证空数组展开为空串、非空数组
（`-static -static-libgcc`）原样保留，Windows 行为零变化。

**共同病根**：三处（EB-16 浏览器、本轮三份视频资源、本条跨平台脚本）都是只验证「本次改动的那条路径」，
未验证「改动之后整体是否仍然成立」。

## 2. 第三方软件声明页太显眼（体验问题，不能整页删）

**用户诉求**：产品里第三方库名已全部换成中文，这页却把上游项目名全列出来，希望去掉。

**不能整页删的理由**

| 组件 | 许可证 | 强制要求 |
| --- | --- | --- |
| 智能素材成片上游 | MIT | 分发物必须带版权声明与许可证全文 |
| 品牌动效成片上游 | Apache-2.0 | 必须带 LICENSE、NOTICE 与变更说明 |
| ffmpeg | GPLv3 | 必须带许可证全文**和完整对应源码**（包内 `source/ffmpeg-8.1.2.tar.xz` 即为此） |

删除后继续分发构成许可证违规，GPLv3 一方有权要求停止分发。

**建议做法**

- 从左侧主导航移除（当前在 `WorkbenchShell.tsx:68`，与「视频制作」「任务记录」同级）；
- 挪到「设置与诊断」页底部一行小字链接，标题改为「开源软件许可」；
- 页面内容只留法定必需项（名称、许可证、版权、源码获取方式），删除展示性技术描述。

**用户决策（2026-07-26）**：按建议降权——从主导航移除，挪到「设置与诊断」页最底部一行小字链接，
标题改为「开源软件许可」。整页删除方案作废。

**已修复（2026-07-26）**：见 `docs/development/FIX-open-source-notice-demotion.md`。

## 2b. 品牌动效成片时长与段数完全写死（真实产品缺陷）

**现象**：「脚本与分镜」页提示「每段 1 秒」，且固定三段，产出成片总长 3 秒。

**代码事实**

| 位置 | 写死的值 |
| --- | --- |
| `motion_video_studio.rs:26` | `MOTION_DURATION_SECONDS: u32 = 3` |
| `motion_video_studio.rs:160` | `self.beats.len() != 3` → 段数非 3 直接 `draft_invalid()` 拒绝提交 |
| `motion_video_studio.rs:355` | `"durationSeconds": 1` 字面量 |
| `motion_video_studio.rs:810` | `data-duration="3"` 字面量，与 `MOTION_DURATION_SECONDS` 未共用常量（违反值复用规范） |
| `VideoStudio.tsx:343` | 界面文案「每段 1 秒」硬编码 |

**问题**：3 秒成片没有实际使用价值；用户无法设置段数与每段时长，想做 5 段会被校验拒绝。

**修复方向**：段数与每段时长改为用户可配（带合理上下限与总时长上限），四处硬编码收敛到单一来源，
界面文案随配置变化。需要同步核对渲染沙箱预算（`motion-render-sandbox-budget.v1.json`）是否按时长换算。

**已修复（2026-07-26）**：见 `docs/development/FIX-motion-video-duration-configurable.md`。
实际写死处比上表多三处：合成脚本的 `Math.min(2.999/2, …)`、ffmpeg 的 `-frames:v 90`（会静默把
成片截断回 3 秒）、以及渲染沙箱固定申报的 `60/60` 墙钟与 CPU 预算。沙箱预算核对结论：
**确实与时长脱钩**，20 秒成片必然被判定为卡死；已改为按帧数推导。段数 1–10、每段 1–10 秒、
总长上限 20 秒（由 `SANDBOX_FRAMES_MAXIMUM = 600` 与 30 fps 硬约束推出），
默认 3 段 × 4 秒。尚缺正式包用户路径验收。

## 2c. 动效零件「自动推荐」实为按目录顺序取前三（真实缺陷）

**现象**：无论段落文字写什么，每段推荐的都是同样三项（Clip Wipe / Editorial Emphasis / Emoji Pop）。

**代码事实**（`frontend/src/features/video-studio/motion-parts-catalog.ts:69-104`）

1. `CATEGORY_KEYWORDS` 是 9 条**纯中文**关键词正则，英文与技术词（ragflow、AI、RAG 等）一条都命中不了；
2. 未命中时按 `beatIndex % 3` 在 `FALLBACK_CATEGORIES` 三项间轮换；
3. 选定分类后 `candidates.slice(0, 3)` —— 取该分类在目录中**排序最前的三项**，与段落内容无关。

**后果**：同一分类下永远返回同样三个零件，134 项中其余项除手动挑选外永不出现。命中关键词与否只影响
分类，不影响"取前三"这个行为。

代码注释说明模型侧自动选择在 authoring agent 中；用户未配置视频创作模型密钥时走的就是这条本地兜底，
因此实际体验即为上述固定结果。

**修复方向**：兜底推荐至少要按段落文本与零件语义做匹配打分而非目录顺序；关键词表需覆盖英文与技术词；
段与段之间应有实质差异。

**已修复（2026-07-26）**：见 `docs/development/FIX-motion-parts-catalog-localization.md`。

## 2d. 134 个动效零件名未做中文映射（体验问题）

零件目录直接显示上游英文原名（Clip Wipe、Editorial Emphasis、Emoji Pop 等）。按 CLAUDE.md 第 6 节，
用户可见文案应使用 `contracts/quality/user-facing-terminology.v1.json` 的通俗映射。134 项均未映射。

**已修复（2026-07-26）**：134 项全部改为显式中文名，生成器与门禁强制"无 ASCII 字母 + 不重复"。
见 `docs/development/FIX-motion-parts-catalog-localization.md`。

## 2e. 零件卡片上的冗余标签（用户要求删除）

`frontend/src/features/video-studio/MotionPartsCatalog.tsx`

| 行 | 内容 | 问题 |
| --- | --- | --- |
| 132 | `<Tag color="blue">{part.category}</Tag>` | 分类名（如「产品与案例展示」）。顶部已有分类筛选下拉框，卡片内重复；且与 137 行「适用：产品功能与案例呈现」表达同一件事 |
| 133 | `有官方在线预览` | 指上游官网存在该零件在线演示页。App 内无法跳转，用户无可执行动作；「官方」一词易引出上游项目追问，与品牌边界规则冲突 |
| 138 | `来源：文字已本地化` | 内部治理信息，用户无需知晓零件文案是否经过本地化（本轮未获用户明确指示，一并登记） |

**用户决策（2026-07-26）**：132、133 两行删除。138 行待确认。

同一文件 112 行标题「动效零件目录（134 项）」中的项数用户要求去掉；该数字是硬编码字面量，
目录增减时不会同步，本身也是缺陷。

**已修复（2026-07-26）**：132、133 行与标题项数已删除，138 行按用户决策保留；`officialPreview`
在前端已无消费方，从 UI 投影契约与前端接口一并下架（治理层的逐项标记与计数保留）。
见 `docs/development/FIX-motion-parts-catalog-localization.md`。

## 3. 三份密钥未配置（非缺陷，配置问题）

用户提供的密钥都在仓库 `.local/secrets/` 下，那是自动化验收脚本读取位置；App 只读自己的私有数据目录，
两者不互通是正确设计（产品不应读取开发仓库路径）。

| 项 | 实际状况 |
| --- | --- |
| 文案模型密钥 | App 内存的是 2026-07-23 某次开发留下的另一把（38 字符），非用户提供的那把（116 字符）→ 连接测试 401 |
| 视频创作模型密钥 | 从未保存 → 新建视频页提示「当前没有调用视频创作模型」（该提示为设计内的无 AI 手工流程） |
| 阿里云剪辑 AK/Secret | `editing-services` 目录为空 → 从未配置 |

已核实：用户提供的密钥对 `qwen3.7-max-2026-06-08`、`deepseek-v4-pro`、`glm-5.2` 均返回 HTTP 200；
`is_valid_api_key` 长度上限 256，116 字符可保存；剪辑服务设置页已正常装配。

**处理**：由用户在设置界面自行填写，同时作为这三个界面的真实用户路径验收。

## 4. VE 剪辑装配缺口（已知规划漏项）

`main.tsx` 使用 `createLocalVideoEditingGateway(window.sessionStorage)`，`frontend/src/platform/tauri/`
下没有真实剪辑工作台网关。VE-04～VE-08 交付的领域层与 Provider 实现（已用真实阿里云凭据验证）
未接入 Control Plane API。

VE-08 台账遗留项写明「随相应装配任务与 CQ-04 端到端验收闭合」，但该装配任务在专项 87 项中不存在。

**处理**：需在专项 Roadmap 新立装配任务。
