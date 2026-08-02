# 本地智能剪辑线 Codex 交接（2026-07-31）

用户可操作：否
证据类型：文档

> 本文件是 `smart-edit` 工作线在 LE-16/T2 完整收口后的恢复入口。接手者先核对第 0 节，
> 再从 LE-16/T3 开始；不要重新实现已经完成的 T1/T2，也不要读取
> `docs/handover-2026-07-31-codex.md`，用户已明确说明那份文档不属于本工作线。

## 0. 交接快照

- 工作树：`/Users/aventador/sourceCode/automation-tool/wt/smart-edit`
- 分支：`smart-edit`
- 上游：`origin/smart-edit`
- 代码与台账收口检查点：`3ddcf779376509937ec46cbba1a8e76fd49e68eb`
  （`docs(le-16): 完成片段选择任务台账`）
- 交接前核对：
  - `HEAD == origin/smart-edit == 3ddcf77`
  - `origin/smart-edit...HEAD == 0/0`
  - 工作树无已跟踪或未跟踪改动
  - 仓库内没有残留 `.coverage`
- 当前 `origin/main`：`9fc3aaeb5345a471cc1bc13f2703e6e6af314f18`
- `origin/main` 已经是当前 `HEAD` 的祖先。它由零冲突合并提交
  `2a6321f merge: 同步 main 最新功能` 进入本分支；交接时 main 没有新的内容需要再次
  合并。下一会话仍须重新核对远端事实，不能把本段当成永久结论。
- 本轮没有启动前端、后端、数据库、Docker、浏览器或桌面 App 服务，没有后台进程需要
  接管或清理。

恢复时先执行：

```bash
cd /Users/aventador/sourceCode/automation-tool/wt/smart-edit
cat /Users/aventador/.claude/CLAUDE.md
cat ./CLAUDE.md
git status --short
git rev-parse HEAD origin/smart-edit origin/main
git rev-list --left-right --count origin/smart-edit...HEAD
git merge-base --is-ancestor origin/main HEAD
sed -n '1,280p' docs/development/LE-16.md
sed -n '1,145p' docs/local-video-editing-roadmap.md
```

如果 `origin/main` 已前进，先在一个完整任务检查点按用户要求把 main 合入当前分支；不要
在 T3 写到一半时合并。合并后必须按实际改动范围验收，不能只看“无冲突”。

## 1. 本工作线的强制做法

以下规则同时来自用户、本项目 `CLAUDE.md` 和全局规则，接手后继续执行：

1. 所有交流、台账和提交说明使用中文。
2. 严格 TDD：先写测试并实际看见正确原因的 RED，再写最小生产实现；发现 bug 也必须先补
   红测。测试与实现不得同时落下。
3. 每完成一个 Task 都运行一次 `codex review --commit <提交>`；finding 要么以红测修复，
   要么以实测证据证明不成立，修复后继续复审到无 finding。
4. 每个 Task 只跑本 Task 的专项、受影响回归、Ruff、严格 mypy 和必要快速门禁。正常情况
   下不重复跑整仓或整目录全量；一个功能模块收口时统一跑一次。LE-16 的整目录
   integration 与模块全量明确留到 T5。
5. 单个 Task 如果改公共基础层、跨模块共享契约、迁移或安全边界，确有风险时才额外跑相应
   全量，并在运行前说明原因。
6. 任务通过后，第一件事更新
   `docs/local-video-editing-roadmap.md` 与 `docs/development/<ID>.md`，再总结、提交和
   开始下一个 Task。台账不能滞后。
7. 成块工作完成后默认提交并推送，不需要等待用户再次提醒；只有用户明确说只提交不推送
   或暂不提交时例外。
8. 项目从未部署或发布，不存在历史兼容负担。代码、API、协议、配置、数据库与构建产物
   直接修改唯一现行实现；禁止旧字段、双写、fallback、兼容 shim、回填迁移或降级备份。
9. 本工作树只由这一条工作线使用；不要把其他会话在 main 的提交误判为本分支并发写入。
10. 未经用户明确要求，不启用子代理或额外工作树。
11. 不主动部署；真实云端或正式发布操作仍需用户单独授权。

项目规则与全局“默认提交并推送”没有冲突。项目只是把提交门槛限定为：实现、适用测试、
真实验收、资源清理和台账全部达到当前 Task 的完成定义之后再提交。

## 2. 专项整体进度

唯一状态台账是 `docs/local-video-editing-roadmap.md`。交接时 24 项的计数为：

| 状态 | 数量 | 任务 |
| --- | ---: | --- |
| ✅ 已完成 | 5 | LE-01、LE-02、LE-03、LE-04、LE-13 |
| 🔍 待验收 | 7 | LE-05、LE-06、LE-07、LE-08、LE-09、LE-14、LE-15 |
| 🚧 实现中 | 1 | LE-16（T1、T2 已完成；T3～T5 未开始） |
| ⬜ 未开始 | 11 | LE-10、LE-11、LE-12、LE-17、LE-18、LE-19、LE-20、LE-21、LE-22、LE-23、LE-24 |

计数门禁：

```bash
python3 scripts/check_local_editing_roadmap_counts.py
# local editing roadmap counts are consistent
```

验收证据深度门禁：

```bash
python3 scripts/check_acceptance_evidence_depth.py
# acceptance evidence depth: 48 checks passed
```

LE-16 仍是 `🚧 实现中`，因为专项任务行的完成定义是完整产出 Timeline 草稿；T1/T2 的
完成不会把整条 LE-16 提前标绿。

## 3. 本会话完成的检查点

### 3.1 main 合并

- 合并提交：`2a6321f`
- 父提交：`d08befc`（当时 smart-edit）与 `9fc3aae`（main）
- 冲突：0
- 合入内容主要是品牌动效成片、视觉证据、Tauri 与前端改动，共 36 个文件。
- 合并后执行了相应验证并推送。用户指定忽略的
  `docs/handover-2026-07-31-codex.md` 没有作为本工作线输入。

### 3.2 LE-16/T1 语义匹配

提交链：

| 提交 | 内容 |
| --- | --- |
| `ea772f9` | 实现句子 × 素材完整语义分数矩阵 |
| `2c04b98` | 修复多行描述/转写和百炼响应字段缺失边界 |
| `d08befc` | T1 Review 与台账收口 |

最终事实：

- 完整消费最多 128 句、最多 32 条视觉素材；
- 每 16 句一批，但每批都携带全部候选，最后本地闭包校验完整矩阵；
- 模型只看到临时候选键、句子、描述、标签和允许的转写，不看到 MaterialId、摘要、路径等；
- 本地按分数降序、素材输入顺序决胜，`score >= 60` 才合格；
- `enable_thinking` 默认关闭，但仍是请求级配置；
- T1 最终专项 51 条，第二轮 Review 无 finding。

### 3.3 LE-16/T2 可解码片段选择

提交链：

| 提交 | 内容 |
| --- | --- |
| `d9c507c` | 初版可解码片段选择 |
| `beda123` | 收紧进程边界、零帧证据、区间性能与 UUIDv4 |
| `7ff632f` | 恢复同分素材按输入顺序稳定决胜 |
| `3ddcf77` | 第三轮 Review 无 finding，T2 与 Roadmap 收口 |

三轮 Codex Review：

1. `d9c507c`：一项 P1、两项 P2。
   - Executor 直接导入 Control Plane 领域类型与常量；
   - 零可解码帧无法表达；
   - 4096 个镜头切点 × 4097 个解码区间形成约 1680 万次求交，探针约 `6.103s`。
2. `beda123`：一项 P2。
   - 协议值允许同分候选顺序与素材输入顺序相反，选择器直接迭代会丢掉本地稳定决胜。
3. `7ff632f`：无 finding。
   - Review 独立复跑专项、Ruff、严格 mypy，并穷举最多 6 条素材的全部协议合法并列排列；
     不同分数与同分顺序均符合契约。

## 4. T2 当前实现边界

### 4.1 已实现

- `backend/src/automation_tool/protocol/local_editing.py`
  - 版本：`local-editing.segment-selection.v1`
  - 路径无关的素材、候选分数、句子排名 DTO；
  - UUID 必须是 canonical RFC 4122 UUIDv4；
  - 本地剪辑的素材时长、镜头数、Timeline 时长、句子数、候选数与阈值上限。
- `backend/src/automation_tool/executor/segment_selection.py`
  - `VerifiedDecodableMaterial` 以 `material_id + content_digest` 绑定当前素材字节；
  - VIDEO 必须恰有一份解码证据，IMAGE 不携带解码证据；
  - 空 `intervals=()` 是“容器可读但零帧可解码”的合法事实；
  - 镜头区间与实际可解码区间用双指针线性求交；
  - 每个素材只返回时间上最早、能完整容纳占位时长的窗口；
  - 图片可占任意合法占位时长且不带 source window；
  - `Material.duration_ms` 只做协议形状校验，不用于补出未验证尾帧，也不裁掉超过偏短容器
    声明、但已实际验证可解码的帧；
  - 最终候选按 `(-score, material input index)` 排序。

4096 个镜头边界与 4097 个解码区间的同形探针在修复后约 `0.001986s`，初版约
`6.103s`。

### 4.2 尚未实现，不能误报

1. T2 是纯路径无关选择器。它没有从真实本机源文件调用 FFmpeg/ffprobe 生产
   `VerifiedDecodableMaterial`，也没有从 `MaterialPathRegistry` 解析路径；真实证据生产
   与正式编排接线仍需后续任务负责。
2. T2 还没有从产品入口、Control Plane 命令或 Worker 调用。LE-17/LE-19/LE-22/LE-23
   才负责正式用户路径和安装包纵向验收。
3. `local_editing.py` 的共享上限目前在协议层定义，并由测试与上游领域/执行器常量逐项
   对齐；不要删除这条漂移守卫。
4. 新选择器已不再导入 `automation_tool.control_plane`。但是 T1 的既有
   `executor/semantic_matching.py` 仍直接导入
   `control_plane.domain.material`。这是 T1 已有边界，未在 T2 提交中扩大；T3/T5 做
   正式编排边界时必须重新评估并优先用协议投影，不能把新的 Executor 模块继续绑到
   Control Plane 实现类型。
5. 新协议模块目前按子模块路径直接导入，没有加入 `protocol/__init__.py` 根重导出。
   仓库两种导入形态都存在；没有真实根导入调用方之前不要为了“整齐”预测性扩大
   `__init__` 的冻结包 import 图。

## 5. T2 最终验证证据

第二轮 finding 的 RED：

```bash
UV_CACHE_DIR=/private/tmp/automation-tool-uv-cache \
uv run --project backend pytest -q \
backend/tests/unit/executor/test_segment_selection.py \
-k equal_scores_are_resolved_by_material_input_order_at_selection_boundary
# 1 failed, 51 deselected
```

GREEN 与覆盖率：

```bash
UV_CACHE_DIR=/private/tmp/automation-tool-uv-cache \
uv run --project backend pytest -q \
backend/tests/unit/executor/test_segment_selection.py \
--cov=automation_tool.executor.segment_selection \
--cov=automation_tool.protocol.local_editing \
--cov-branch --cov-report=term-missing --cov-fail-under=100
# 52 passed
# segment_selection.py: 110 statements / 48 branches / 100%
# protocol/local_editing.py: 62 statements / 10 branches / 100%
# total: 172 statements / 58 branches / 100%
```

受影响回归：

```bash
UV_CACHE_DIR=/private/tmp/automation-tool-uv-cache \
uv run --project backend pytest -q \
backend/tests/unit/executor/test_segment_selection.py \
backend/tests/unit/executor/test_semantic_matching.py \
backend/tests/unit/executor/test_script_segmentation.py \
backend/tests/unit/executor/test_script_voiceover.py
# 189 passed
```

静态门禁：

```bash
UV_CACHE_DIR=/private/tmp/automation-tool-uv-cache \
uv run --project backend ruff format --check \
backend/src/automation_tool/executor/segment_selection.py \
backend/src/automation_tool/protocol/local_editing.py \
backend/tests/unit/executor/test_segment_selection.py

UV_CACHE_DIR=/private/tmp/automation-tool-uv-cache \
uv run --project backend ruff check \
backend/src/automation_tool/executor/segment_selection.py \
backend/src/automation_tool/protocol/local_editing.py \
backend/tests/unit/executor/test_segment_selection.py

UV_CACHE_DIR=/private/tmp/automation-tool-uv-cache \
uv run --project backend mypy --strict \
backend/src/automation_tool/executor/segment_selection.py \
backend/src/automation_tool/protocol/local_editing.py \
backend/tests/unit/executor/test_segment_selection.py

git diff --check
# 全部通过
```

本 Task 没有运行 `backend/tests/integration` 整目录，也没有运行 LE-16 模块全量；这是按
用户要求的测试粒度有意延后，不得在后续文档里写成已通过。T5 收口时必须补。

## 6. 下一步：LE-16/T3

T3 只做“有人声素材按自带旁白段落编排”，不要提前把 T4 错误收敛或 T5 最终 Timeline
构造全部塞进来。

### 6.1 已经定死的产品行为

1. 文案句子只分配给 `has_speech=false` 的素材。
2. 有人声素材不消耗句子，不参与无人声素材的句子竞争，不排 TTS narration。
3. 每条有人声视频按素材输入顺序独立占段。
4. source window 必须覆盖主体 `speech_segments_ms`；段时长来自这个原声区间，不来自
   文案、TTS 或容器声明时长。
5. 同一段排 visual、同源 ambient 和 `speech_transcript` caption，三者起点与时长一致。
6. 首期不做说话人分离。
7. 全部素材都有语音是合法的纯原声草稿：未使用的文案/TTS 结果被忽略，不因没有句子分配
   报错。
8. 纯无人声素材自然退化为纯 TTS 旁白草稿。

### 6.2 建议先写的 RED

开工前先更新 `docs/development/LE-16.md` 的 T3 状态和 RED 清单，再写测试。先搜索现有
编排/Timeline 草稿模块，确认能否复用；不要先创建名字相近的第二套模块。

最少应有以下红测：

1. 混合输入中，有人声素材不会出现在任何句子分配结果里，无人声句子仍按 T1/T2 候选消费。
2. 两条有人声素材即使语义分数顺序相反，仍按素材输入顺序形成两个独立原声段。
3. 多个 `speech_segments_ms` 的 source window 覆盖主体首段起点到末段终点，visual、
   ambient、caption 的时间完全一致，且没有 narration。
4. 有人声段使用完整 `speech_transcript` 作为字幕，不制造 TTS 或第二份转写。
5. 全部素材有人声时成功返回纯原声中间草稿；任何 TTS/句子匹配 Adapter 都不应被调用。
6. 纯无人声输入不制造 ambient 原声段，仍保留句子/TTS 编排输入。
7. 重复素材身份、非视频却声明语音、语音段越界/乱序、转写缺失等非法协议值 fail closed，
   错误不含文案、转写、MaterialId 或路径。

首次 RED 的失败原因必须是缺失的目标行为或模块，不能因夹具自身非法、导入错路径或环境
缺依赖而红。

### 6.3 边界建议

- T3 建议先产出路径无关的“段落草稿”中间值；T5 再一次性构造领域 `Timeline`，避免 T3
  偷跑 T5。
- 新 Executor 编排模块继续只消费协议 DTO，不直接导入 Control Plane 领域实现。
- 若 T3 需要扩展 `protocol/local_editing.py`，先写协议非法值红测和版本/上限漂移守卫；
  项目未发布，可直接修改 v1 唯一现行协议及全部调用方，不保留兼容 DTO。
- 每完成 T3，先更新台账、提交推送，再对该提交运行一次 Codex Review；不跑 LE-16
  整目录全量。

## 7. LE-16/T4 已定的四类产品结果

| 场景 | 固定行为 |
| --- | --- |
| 无人声素材少于待分配句子 | 不复用素材、不产部分草稿，返回 `INSUFFICIENT_MATERIALS` |
| 有候选但实际可解码区间都太短 | 依次尝试下一名合格素材；全失败返回 `SOURCE_TOO_SHORT` |
| 某句全部分数低于 60 | 不用最高低分硬配，返回 `NO_RELEVANT_MATERIAL` |
| 全部素材均有人声 | 成功产出纯原声草稿，不调用 TTS、不返回错误 |

固定失败结果不得携带文案、转写、MaterialId、模型请求 ID 或本机路径。同一次生成全有或
全无，不能留下半份 Timeline。T4 的每个错误都先写 RED，并至少覆盖成功回退与最终失败
两条路径。

## 8. LE-16/T5 收口要求

T5 才产出最终 `Timeline` 草稿，并直接经过领域构造器：

1. visual 从 0 开始连续相接，精确结束于 `Timeline.duration_ms`，首期不自动加转场。
2. 无人声段的 visual、narration、caption 起点和时长完全一致。
3. 有人声段的 visual、ambient、caption 起点和时长完全一致，没有 narration clip。
4. VIDEO clip 必须携带等长 source in/out；IMAGE clip 不携带 source window。
5. 直接构造 `TimelineClip`、`TimelineTrack`、`Timeline`，不能复制或绕开领域校验。
6. T5 完成时统一运行：
   - LE-16 T1～T5 全部专项与受影响单元测试；
   - 适用的 `backend/tests/integration` 整目录，而不是任意子集；
   - 覆盖率、Ruff、严格 mypy、路线图计数、证据深度；
   - 捕获力变异；
   - 模块最终 Codex Review。

LE-16 完整任务是否能标 ✅ 仍要按真实用户路径门禁判断；如果只有分层实现、正式 App 接线
仍依赖 LE-19，则按台账规则顶格 `🔍 待验收`，不能只因单元测试全绿而标 ✅。

## 9. LE-16 之后的建议顺序

沿用 `docs/development/2026-07-30-codex-handoff.md` 的拆解和当前 Roadmap 依赖，建议：

1. LE-16/T3 → T4 → T5；
2. LE-10 视频渲染管线（6 个子任务）；
3. LE-11 音频管线（5 个子任务）；
4. LE-12 Worker 生命周期与任务控制（5 个子任务）；
5. LE-17 工作台接真实网关（5 个子任务）；
6. LE-18 素材库界面（6 个子任务）；
7. LE-19 智能剪辑入口（5 个子任务）；
8. LE-24 深度思考开关与实测耗时（4 个子任务）；
9. LE-20 字体必要性重估与装配；
10. LE-21 联合失败矩阵；
11. LE-22 macOS 正式包纵向验收；
12. LE-23 Windows 正式包纵向验收。

每个任务的【先定】决策仍以旧交接与当前 Roadmap 的任务行为准；当前 Roadmap 更新后的事实
优先于旧交接中的历史状态和旧门禁读数。

## 10. 🔍 任务何时可升 ✅

| 任务 | 剩余真实验收依赖 |
| --- | --- |
| LE-05 | LE-17/T5：真实 App 经正式 Rust 网络桥写四表，并核对真实 PostgreSQL 行 |
| LE-06 | LE-17：正式 App 通过真实网关消费剪辑 REST API |
| LE-07 | LE-18/T6：正式 App 导入素材并看到时长、画幅和有无声音 |
| LE-08 | LE-17/LE-19 接入正式用户路径，LE-22/23 核对正式包 |
| LE-09 | LE-10/T4 让字幕 PNG 真进成片，LE-20 完成字体装配，LE-17 接用户路径 |
| LE-14 | LE-17 接正式入口，LE-22/23 核对正式包里的 ONNX Runtime/VAD/ASR 路径 |
| LE-15 | LE-17/LE-19 接正式用户路径，LE-22/23 核对正式包 |

升状态前重新读对应 `docs/development/<ID>.md` 的完成定义并核实真实证据，不得只按本表
机械翻绿。

## 11. 仍有效的陷阱与已失效旧事实

### 11.1 仍有效

1. `backend/tests/integration` 组合不耐受任意子集，需运行时按整目录执行；真实 PostgreSQL
   前先检查端口和本项目专属 Docker/Colima 状态。
2. 媒体工具链缓存可能缺失；素材探测相关测试会要求
   `~/Library/Caches/automation-tool-build/media-toolchain`。按错误提示使用正式准备脚本，
   不建立测试专用 fallback。
3. 变异与破坏性探针必须在隔离副本运行，显式核对 `module.__file__` 指向副本并清
   `__pycache__`；最终以 `git diff` 和工作树状态确认没有变异残留。
4. Roadmap 表格每行必须是 5 个单元格，单元格里的 `|` 写成 `\|`；计数脚本是守卫，
   不能为放行而放宽。
5. 新 worktree 只能用 `python3 scripts/new_worktree.py <名称> [提交]` 建；要基于当前
   工作必须显式传 `HEAD`。
6. 启停 Colima/Docker 前先查当前 profile/context，不停止或接管其他项目/会话资源。
7. 每条守卫都要临时弄坏一次确认会红；变异后必须还原并重跑 GREEN。
8. `UV_CACHE_DIR` 固定使用 `/private/tmp/automation-tool-uv-cache`，避免工作树内缓存污染。

### 11.2 已失效或需要重新实测

1. 旧交接写 `check_user_facing_branding.py` 已知为红；main 合流后该门禁已恢复绿色。
   Roadmap 记录的最近实测为 54 个前端文件、284 个原生文件通过。后续仍需在 LE-19
   保持绿色。
2. 旧交接写验收深度脚本只存在于其他分支；现在
   `scripts/check_acceptance_evidence_depth.py` 已在当前分支，并实跑 48 项通过。
3. Roadmap 仍记录后端全量 mypy 历史基线为 17 errors / 8 files、全量 Ruff 约 98 项；
   这些是 LE-03 时点的历史读数，不是本次 T2 新鲜复测。任务若触及全量门禁，必须重新
   实跑并按文件交集判断，不能只复述旧数字。
4. 旧交接的进度 4 ✅ / 3 🔍 / 17 未开始已经失效；当前以第 2 节的
   5 ✅ / 7 🔍 / 1 🚧 / 11 未开始为准。

## 12. 下一会话最短恢复路线

1. 完整读取全局与项目规则。
2. 核对 Git 三个 ref、ahead/behind、工作树和 main 祖先关系。
3. 只读本交接、`docs/development/LE-16.md`、专项 Roadmap 和本地智能剪辑设计；不要
   批量读取全部历史任务文档，也不要读用户指定忽略的 handover 文档。
4. 确认 Roadmap 当前下一步仍是 LE-16/T3；若其他会话已经改变事实，先查清并修台账。
5. 在 `LE-16.md` 登记 T3 的精确 RED 与状态。
6. 先写并运行 T3 红测；确认失败原因正确后才写生产代码。
7. 完成 T3 专项与受影响快速门禁，更新台账，提交并推送。
8. 对 T3 提交运行 Codex Review，修到无 finding 后再进入 T4。

## 13. 交接终态

- LE-16/T1：✅
- LE-16/T2：✅，第三轮 Review 无 finding
- LE-16/T3～T5：未开始
- 当前分支已包含交接时的最新 `origin/main`
- 代码、任务证据与 Roadmap 已推送
- 没有未提交改动、测试产物、运行服务或后台审查进程
- 下一会话从 T3 RED 开始

