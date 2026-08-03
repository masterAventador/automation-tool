# 全库 Python 覆盖率债盘点与收口计划

用户可操作：否
证据类型：文档

> 状态：实施完成，等最终一次全量测量落定。COV-00 ～ COV-06 全部收口，
> 逐项证据见 `docs/development/COV-0*.md`。本文保留 `main@dbd56118` 的可复现
> 基线、已有测试资产、拆分顺序和最终验收口径。

## 1. 结论先行

当前功能与行为测试是绿的，红的是仓库早已设为 100% 的 Python 全库覆盖率门禁：

- `6,471 passed / 21 skipped / 0 failed`；
- 语句覆盖 `30,867 / 32,215`，缺 `1,348` 行；
- 分支覆盖 `7,511 / 8,186`，缺 `675` 个分支；
- coverage.py 综合口径为 `94.992698%`，剩余 `2,023` 个覆盖点；
- `62` 个生产文件未到 100%；
- `backend/pyproject.toml` 仍保持 `branch = true`、`fail_under = 100`；
- 唯一模块级 `omit` 仍是 Linux 覆盖进程无法导入的
  `executor/windows_acl.py`，不得扩大。

这不是本次分支对账造成的新债。旧基线为 `94.648647%`、63 个文件未满；
本次为 `bilibili_publishing_runtime.py` 新增 42 条测试并独立跑到 100%，全库精确
提升到 `94.992698%`，说明本次合并没有继续扩大债务。

早期文档记录过 Ruff、Mypy、coverage 三项全库红。到 `dbd56118`，Ruff 与 Mypy
已经全绿，仍需系统收口的只有 coverage。那些历史文档保留事故现场，不应被当成
当前门禁状态。

## 2. 范围与非目标

这里的“全库覆盖率债”特指当前 CI 已经执行的
`--cov=automation_tool`，即 `backend/src/automation_tool` 全部 Python 生产代码。

TypeScript、Rust 目前分别有 Vitest/Playwright、Cargo test/Clippy 等行为门禁，但
仓库没有统一的数值覆盖率契约。给这两个语言新增覆盖率体系属于另一项工程治理工作，
不应夹进本轮 Python 补债，更不能因为“全库”二字横向扩展第一版范围。

本轮只允许两类修改：

1. 让已有、真实执行生产代码的测试进入正确的 coverage 收集边界；
2. 为确实未覆盖的可达行为补测试，测试暴露真实缺陷时再做最小生产修复。

不得借补覆盖率新增产品能力、顺手重构无关模块或扩大安全治理范围。

## 3. 可复现基线

### 3.1 提交与环境

- 提交：`dbd561188e07230c2e77e08285b0028944ab3ed6`
- 日期：2026-08-03
- 本机：macOS / Python 3.12.13
- CI 权威平台：`.github/workflows/quality.yml` 的 Ubuntu runner

本机执行：

```bash
cd backend
uv run pytest -q \
  --cov=automation_tool \
  --cov-report=term-missing \
  --cov-report=json:/private/tmp/automation-tool-coverage.json
```

预期在补债完成前返回 `1`，原因只能是：

```text
FAIL Required test coverage of 100.0% not reached. Total coverage: 94.99%
6471 passed, 21 skipped
```

不要用 `--cov-fail-under=0` 把这条命令伪装成绿。JSON 在 coverage 失败时仍会生成，
足够用于逐批比较绝对缺口。

### 3.2 数量基线

| 指标 | 总数 | 已覆盖 | 未覆盖 | 覆盖率 |
|---|---:|---:|---:|---:|
| 语句 | 32,215 | 30,867 | 1,348 | 95.815614% |
| 分支 | 8,186 | 7,511 | 675 | 91.754215% |
| 综合 | 40,401 | 38,378 | 2,023 | 94.992698% |

coverage 报告另列出 1,456 条 excluded line。初步审计结果如下：

- 生产源码显式 `# pragma: no cover` 共 49 处；
- excluded line 数不能与 49 个指令一一对应：函数头或分支上的一个 pragma 会排除整段
  函数/分支；coverage.py 默认规则还会排除 `TYPE_CHECKING` 块和仅含 `...` 的协议体；
- 收口前必须逐处复核 49 个显式 pragma 的理由和真实测试映射；
- 补债期间不得新增 pragma，不得新增 omit，不得放宽 `fail_under`。

### 3.3 债务集中度

| 运行边界 | 文件数 | 缺失覆盖点 | 占全部债务 |
|---|---:|---:|---:|
| 本地/智能剪辑 | 17 | 768 | 37.96% |
| 动效作者链 | 14 | 749 | 37.02% |
| 语音/音频 | 5 | 199 | 9.84% |
| B 站/抖音发布 | 10 | 178 | 8.80% |
| 平台与小尾项 | 16 | 129 | 6.38% |
| 合计 | 62 | 2,023 | 100% |

最大单文件 `motion_authoring/agent.py` 占 445 点，约 22%；前 5 个文件占 896 点，
约 44%；前 10 个占 1,237 点，约 61%。反过来，缺口不超过 12 点的 30 个文件合计
只有 153 点。应先解决集中区，最后一次性清小尾项。

## 4. 已有但未计入后端 coverage 的测试资产

### 4.1 动效 agent：收益已实测

`scripts/test_motion_authoring_agent.py` 有 118 条确定性测试，会真实执行
`automation_tool.executor.motion_authoring`，但 `backend/pyproject.toml` 的
`testpaths = ["tests"]` 不会收集它。

在本次 94.99% 的 `.coverage` 数据上追加运行这 118 条测试，实测：

- 118 条全部通过；
- 全库从 94.992698% 提升到 95.881290%；
- 未覆盖语句从 1,348 降到 1,086；
- 未覆盖分支从 675 降到 578；
- 一次消掉 359 个真实缺口；
- `motion_authoring/agent.py` 从 445 点降到 123 点；
- 动效作者链总债从 749 点降到约 390 点。

因此第一批不应重写这 118 条。正确做法是把测试实现迁到
`backend/tests/unit/executor/` 下成为唯一真源，并让原脚本变成兼容入口。原入口不能
直接删除，因为 `run_bm_15_acceptance.py`、`run_bm_16_acceptance.py` 和多份历史验收
仍调用它；兼容入口应调用同一份 canonical 测试，不能复制一套断言。

### 4.2 素材 worker：有资产，但必须先拆边界

`scripts/test_material_video_worker.py` 当前有 74 条测试，混合了三类职责：

1. `local_editing_worker_process.py`、`smart_edit_worker_process.py` 的生产行为；
2. `workers/material_montage` 的 gateway/WebUI 边界；
3. 冻结包、vendor、平台运行时约束。

它由独立脚本门禁以及 Material video Worker 工作流执行，但不进入 backend coverage。
本机 Codex 沙箱测量时有 5 条因禁止绑定临时 socket 在 bootstrap 处假失败；此前同一
提交的非沙箱聚合门禁为 100 个脚本、1,232 个检查全部通过。因此不能用这次沙箱失败
判定代码红，也不能把这 74 条整体塞进 backend pytest。

迁移时只把第 1 类生产 Executor 行为提取到 `backend/tests`。第 2、3 类继续留在
脚本/平台工作流，通过共享 fixture 或薄兼容入口避免复制。迁移前后都要在 CI 或允许
本地 socket 的宿主环境复跑原 74 条。

### 4.3 不采用的收集方式

- 不把 `testpaths` 改成仓库根目录；这会把打包、Docker、真实浏览器、平台验收混进
  Linux backend job。
- 不让全量 `scripts/run_script_tests.py` 在同一个 coverage 进程里跑；它覆盖 100 个
  异构脚本，依赖多个解释器和平台能力，不是后端单测边界。
- 不保留两份内容相同的测试，只为了让两个门禁都显示绿色。

## 5. 完整缺口清单

下表来自本次 JSON，路径相对 `backend/src/automation_tool`。总缺口等于缺行加缺分支。

### 5.1 本地/智能剪辑：768 点

| 模块 | 缺行 | 缺分支 | 总缺口 |
|---|---:|---:|---:|
| `executor/adaptive_frame_extraction.py` | 121 | 57 | 178 |
| `executor/smart_edit_worker_process.py` | 66 | 41 | 107 |
| `executor/local_editing_worker_process.py` | 64 | 22 | 86 |
| `executor/smart_edit_pipeline.py` | 55 | 22 | 77 |
| `executor/local_material_preview.py` | 47 | 23 | 70 |
| `executor/smart_edit_generation.py` | 35 | 22 | 57 |
| `control_plane/infrastructure/database/material_repository.py` | 26 | 20 | 46 |
| `executor/local_editing_worker.py` | 22 | 17 | 39 |
| `executor/smart_edit_media.py` | 28 | 8 | 36 |
| `executor/material_understanding.py` | 14 | 12 | 26 |
| `control_plane/application/editing_jobs.py` | 11 | 9 | 20 |
| `control_plane/application/materials.py` | 3 | 3 | 6 |
| `control_plane/domain/material.py` | 3 | 3 | 6 |
| `executor/material_probe.py` | 3 | 3 | 6 |
| `control_plane/api/editing_materials.py` | 4 | 0 | 4 |
| `control_plane/api/editing_jobs.py` | 2 | 1 | 3 |
| `control_plane/infrastructure/database/timeline_repository.py` | 0 | 1 | 1 |

### 5.2 动效作者链：749 点（收集已有测试后约 390 点）

| 模块 | 缺行 | 缺分支 | 总缺口 |
|---|---:|---:|---:|
| `executor/motion_authoring/agent.py` | 321 | 124 | 445 |
| `executor/motion_authoring/entry.py` | 42 | 22 | 64 |
| `executor/motion_authoring/voiceover.py` | 38 | 14 | 52 |
| `executor/motion_authoring/part_typography.py` | 20 | 14 | 34 |
| `executor/motion_authoring/authoring_workspace.py` | 18 | 13 | 31 |
| `executor/motion_authoring/part_workspace.py` | 15 | 10 | 25 |
| `executor/motion_authoring/film_assembly.py` | 12 | 12 | 24 |
| `executor/motion_authoring/component_host.py` | 12 | 9 | 21 |
| `executor/motion_authoring/segment_concat.py` | 11 | 8 | 19 |
| `executor/motion_authoring/composition_template.py` | 3 | 9 | 12 |
| `executor/motion_authoring/slot_probe_browser.py` | 7 | 5 | 12 |
| `executor/motion_authoring/part_document.py` | 4 | 2 | 6 |
| `executor/motion_authoring/__init__.py` | 1 | 1 | 2 |
| `executor/motion_authoring/resources.py` | 1 | 1 | 2 |

追加现有 118 条测试后，`__init__.py` 已到 100%；其余精确剩余依次为：agent 123、
entry 55、voiceover 52、typography 34、assembly 24、component host 21、concat 19、
part workspace 18、authoring workspace 13、slot probe 12、composition 11、document 6、
resources 2。

### 5.3 语音/音频：199 点

| 模块 | 缺行 | 缺分支 | 总缺口 |
|---|---:|---:|---:|
| `executor/silero_vad.py` | 51 | 29 | 80 |
| `executor/material_speech_pipeline.py` | 45 | 28 | 73 |
| `executor/material_speech_transcription.py` | 21 | 13 | 34 |
| `executor/material_speech_analysis.py` | 5 | 5 | 10 |
| `executor/script_voiceover.py` | 1 | 1 | 2 |

### 5.4 B 站/抖音发布：178 点

| 模块 | 缺行 | 缺分支 | 总缺口 |
|---|---:|---:|---:|
| `control_plane/api/bilibili_publishing.py` | 30 | 6 | 36 |
| `control_plane/infrastructure/bilibili/token_provider.py` | 25 | 9 | 34 |
| `executor/rpa/douyin/publish_release.py` | 24 | 6 | 30 |
| `executor/rpa/douyin/publish_page.py` | 17 | 5 | 22 |
| `control_plane/infrastructure/database/bilibili_publish_repository.py` | 9 | 4 | 13 |
| `executor/rpa/douyin/publish_artifact.py` | 7 | 6 | 13 |
| `executor/rpa/douyin/publish_preflight.py` | 7 | 4 | 11 |
| `executor/rpa/douyin/search_page.py` | 7 | 3 | 10 |
| `control_plane/bootstrap/bilibili_publishing.py` | 5 | 2 | 7 |
| `control_plane/infrastructure/bilibili/open_api_client.py` | 2 | 0 | 2 |

`bilibili_publishing_runtime.py` 不在此表中；它已在本次对账里达到 100%，不得重复开发。

### 5.5 平台与小尾项：129 点

| 模块 | 缺行 | 缺分支 | 总缺口 |
|---|---:|---:|---:|
| `executor/platform_commands.py` | 28 | 9 | 37 |
| `control_plane/bootstrap/local_provisioning.py` | 13 | 8 | 21 |
| `executor/ledger.py` | 12 | 8 | 20 |
| `executor/__init__.py` | 7 | 5 | 12 |
| `executor/browser_use_safety.py` | 5 | 3 | 8 |
| `control_plane/application/__init__.py` | 4 | 1 | 5 |
| `executor/browser_surface_lease.py` | 2 | 3 | 5 |
| `control_plane/domain/__init__.py` | 2 | 2 | 4 |
| `executor/authentication.py` | 2 | 2 | 4 |
| `executor/cli.py` | 2 | 1 | 3 |
| `control_plane/__init__.py` | 1 | 1 | 2 |
| `executor/browser_runtime.py` | 2 | 0 | 2 |
| `executor/side_effect_ledger.py` | 1 | 1 | 2 |
| `protocol/__init__.py` | 1 | 1 | 2 |
| `control_plane/bootstrap/editing_timelines.py` | 1 | 0 | 1 |
| `executor/windows_candidate.py` | 0 | 1 | 1 |

## 6. 推荐实施顺序

全部工作放在专用分支，例如 `coverage/backend-100`。每个批次可以提交并推送这个分支，
但在全库真正达到 100% 以前不合回 `main`。这样既保留细粒度进度，又不把明知红的
全库门禁继续带进主分支。

### COV-00：锁定 Linux 权威基线与排除项

1. 从最新 `main` 建分支，先在与 quality workflow 相同的 Ubuntu/Python 3.12.13
   环境生成 coverage JSON。
2. 对比本文 macOS 基线；平台差异单独登记，不能把差异算作随机波动。
3. 导出并审查 49 个显式 pragma。能通过 mock、受控子进程或真实 CLI 验收覆盖的，
   删除 pragma 并补测试；确实不可达的防御断言保留原因和上层不变量测试。
4. 确认 `windows_acl.py` 是唯一 omit；不增加第二项。

完成判据：Linux 基线可复现，测试数和缺口绝对数被记录，排除项清单有逐项结论。

### COV-01：对齐已有测试的收集边界

1. 把 `scripts/test_motion_authoring_agent.py` 的 118 条测试实现迁为
   `backend/tests/unit/executor/` 下的 canonical 测试。
2. 原脚本保留薄兼容入口，使 BM-15、BM-16、慢提交门禁继续执行同一批测试。
3. 添加执行型门禁：真实运行兼容入口，证明 canonical 测试的 sentinel 行为被执行且
   报告正数测试数；不能只用源码字符串断言入口路径存在。
4. 拆 `scripts/test_material_video_worker.py`：Executor 生产行为迁入 backend tests，
   worker/vendor/平台边界留在脚本工作流；共享 fixture，不复制断言。
5. 在允许 socket 的宿主或 CI 复跑素材 worker 74 条，确认迁移没有改变原门禁。

已知量化目标：仅动效 118 条收集对齐就应把本机综合覆盖率提升到约 95.881290%，
总债减少 359 点。这个数用于发现收集漂移，不代替各模块最终 100% 的判据。

### COV-02：本地/智能剪辑 768 点

按共享 fixture 分四次收口：

1. 抽帧：`adaptive_frame_extraction.py`，178 点；重点覆盖 ffmpeg 启动/退出、超时、
   输出原子写、补帧选择与清理失败。
2. 子进程：smart/local 两个 worker process，193 点；重点覆盖 JSON 边界、取消、提交、
   重试、诊断码、路径脱敏和状态机非法转移。
3. 流水线：pipeline、preview、generation、media、local worker，279 点；用最小假媒体
   与窄 mock 覆盖每个阶段和失败臂，不启动真实模型服务。
4. 材料与控制面尾项：repository、understanding、materials/editing jobs、probe、
   timeline repository，118 点；数据库分支使用现有隔离 fixture，不用内存假实现替代
   本应验证的事务语义。

每一小批必须让列出的目标模块独立达到语句/分支双 100%，再进入下一批。

### COV-03：动效作者链剩余约 390 点

在 COV-01 收集对齐之后再重新生成 JSON，不能继续按原始 749 点写测试。

建议三批：

1. agent、entry、两个 workspace，约 209 点；复用 scripted model、临时工作区和封闭
   工具面 fixture，补解析拒绝、预算边界、修复轮次、写入失败与清理分支。
2. voiceover、typography，约 86 点；覆盖 TTS 超时/坏音频/时长主宰、字体回退和中文
   排版边界，不调用真实付费服务。
3. assembly、component host、concat、composition、slot probe、document/resources，约
   95 点；浏览器或 FFmpeg 只在现有真实验收需要时启动，普通分支用窄进程接口测试。

不要为了覆盖内部行而复制生产算法到测试里；断言应落在可观察产物、封闭错误码、资源
清理和安全边界上。

### COV-04：语音/音频 199 点

1. Silero VAD 80 点：模型资产缺失/损坏、空音频、阈值边界、推理异常与资源释放。
2. speech pipeline + transcription 107 点：成功、无语音、取消、超时、坏返回、临时文件
   清理和故障码映射。
3. analysis + script voiceover 12 点：清最后的输入和分支边界。

真实模型/真实 TTS 继续由已有 acceptance 拥有；单测只验证适配层契约，不能偷偷访问
网络，也不能把真实故障吞成无声降级。

### COV-05：发布链 178 点

1. B 站 API、token provider、bootstrap、repository/client，共 92 点；覆盖 token
   缺失/过期/刷新、平台拒绝、幂等、数据库冲突和响应脱敏。
2. 抖音 publish release/page/artifact/preflight/search，共 86 点；覆盖页面漂移、上传
   失败、预检拒绝、产物不一致与安全恢复。

复用本轮已经补齐的 PB-07 fixture 和封闭错误词表；不要再开发新发布能力，也不要重复
测试已 100% 的 B 站 runtime。

### COV-06：平台尾项 129 点与最终完整性审计

1. 先清 `platform_commands.py`、local provisioning、ledger 三个较大项，共 78 点。
2. 再以参数化 import/CLI/异常测试清 13 个小文件的 51 点。
3. `windows_candidate.py` 的最后一个分支使用平台值注入或 Windows CI 证据解决；不得
   为它扩大 omit。
4. 重新审计 49 个显式 pragma、唯一 omit 和 coverage 配置 diff。
5. 在 Ubuntu 权威环境与本机各跑一次全库 coverage，均要求 100%、0 missing、
   0 partial；平台专属真实验收按原工作流继续跑。

## 7. 每批执行纪律

### 7.1 RED / GREEN

每个模块先从 JSON 的 `missing_lines` 和 `missing_branches` 定位缺口，写一条能证明真实
行为的失败测试，再做最小修复。若仅新增测试即可转绿，不改生产代码。

目标模块命令模板：

```bash
cd backend
uv run pytest <相关测试文件> \
  --cov=automation_tool.<目标模块> \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=100
```

同一运行边界多个模块可以同批，但报告中每个模块都必须 100%，不能用大模块的已覆盖
行数稀释小模块缺口。

### 7.2 批次回归

每批至少执行：

```bash
cd backend
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest <受影响测试目录>
uv run pytest --cov=automation_tool --cov-report=term-missing \
  --cov-report=json:/private/tmp/automation-tool-coverage.json
```

最终一条在完成前仍会因 100% 阈值返回 1；检查标准是 `passed/skipped` 不退化，且
missing lines、missing branches 都严格下降。不要把这次预期 coverage 红描述成“全绿”。

涉及 `scripts/` 兼容入口时，额外跑：

```bash
backend/.venv/bin/python scripts/test_motion_authoring_agent.py
backend/.venv/bin/python scripts/test_material_video_worker.py
backend/.venv/bin/python scripts/run_script_tests.py
```

素材 worker 的 socket 用例必须在允许本地 socket 的宿主/CI 环境执行；Codex 沙箱的
`Operation not permitted` 不是产品失败。

### 7.3 提交与合并

- 每个 COV 小批独立提交并推送 `coverage/backend-100`，提交正文写明覆盖点变化；
- 不把未满 100% 的中间分支合进 `main`；
- 主线有新提交时及时合入覆盖分支并重测，避免最后一次巨型冲突；
- 全库 100% 后跑快/慢提交门禁，再 `--no-ff` 合回 `main` 并推送；
- 推送后核对本地/远端 SHA 与干净工作区。

## 8. 最终验收判据

以下条件必须同时成立，才可把覆盖率债标为完成：

1. `uv run pytest --cov=automation_tool --cov-report=term-missing` 退出 0；
2. `6,471` 只是本次基线，最终测试数只能因新增测试合理增加，不能通过删测减少；
3. coverage 报告为 100%，missing lines = 0、missing branches = 0、partial branches = 0；
4. Ruff format、Ruff check、Mypy、OpenAPI/Executor schema 检查全绿；
5. 独立脚本聚合门禁全绿，且测试数/检查数为正；
6. 49 个旧 pragma 已逐项审计，没有新增 pragma 或 omit；
7. 没有为了覆盖率调用真实付费模型、真实平台账号或默认浏览器 Profile；
8. 没有残留 `.coverage`、临时媒体、容器、浏览器或后台进程；
9. 专用分支已合回并推送 `main`，本地 `main` 与 `origin/main` SHA 一致。

## 9. 禁止的假收口

- 把 `fail_under` 从 100 降到 95/99；
- 给未覆盖模块新增 `omit`；
- 用 `# pragma: no cover`、`TYPE_CHECKING` 或抽象协议重写来藏可达代码；
- 删除失败分支、吞异常、返回默认成功，只为减少分支数；
- 只跑目标测试后宣称“全库 100%”；
- 把 source-text、快照存在性或 mock 调用次数当成业务行为的唯一证据；
- 重复已有测试，让两个收集器各跑一份相同断言；
- 趁补测试横向开发新能力或做无关架构加固。

本轮真正的完成定义很简单：保留现行严格门禁，用真实、可维护、唯一来源的测试把它从
94.99% 推到 100%，并让主分支第一次能够诚实地通过这条门禁。
