# LE-05 数据库迁移与仓储 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。
> 每个 task 一个新实现子代理 + spec 符合性审查 + `pr-review-toolkit:code-reviewer` 质量审查，
> 两轮独立调用，过审才进下一个 task。

**目标：** 把 LE-02/03/04 的四个聚合（`Material`、`Timeline`、`EditingProject`、`EditingJob`）
落进真实 PostgreSQL——迁移、SQLAlchemy 仓储、三条跨聚合根不变式由数据库结构强制。

**架构：** 沿用既有形状——`schema.py` Core Table + Alembic 链式迁移 +
`SqlAlchemy*Repository`（async `Database` 会话）+ `managed_test_postgres` 真实 PG 集成测试。
不引入 ORM 模型类、不引入第二套会话管理。

## 0. 已锁死的决策（实施不再重议；要推翻必须先给实测依据）

1. **不带 `installation_id`。** 被删的云剪辑三张表就没有（`20260728_0035` 的 downgrade
   原文可查），P9 是本地单设备 MVP（CLAUDE.md §2），剪辑数据没有跨设备归属问题。
2. **四张表、四个迁移、一 task 一迁移**：`20260729_0036_editing_projects` →
   `0037_materials` → `0038_timelines` → `0039_editing_jobs`，链式 `down_revision`
   逐个指向前者。每个迁移必须有能真跑的 `downgrade()`（house 规则，`0035` 是范例）。
3. **`timelines` 的主键是 `(timeline_id, revision)`**——修订是快照，同一 `timeline_id`
   每个 revision 一行，行内容不可 UPDATE。另加 **`UNIQUE(timeline_id, revision, project_id)`**
   ——它是主键的超键，看似冗余，**存在的唯一理由是给 `editing_jobs` 的复合外键当靶子**
   （见决策 5），注释里要写明这一点，防止后人当垃圾清掉。
4. **`tracks` 存 JSONB 快照**（含嵌套 clip/transition），不拆 clip 表。理由：Timeline
   是不可变修订快照，渲染器整棵读取，P9 没有任何「跨行查 clip」的用户故事；拆表是
   预测性抽象。序列化/反序列化必须**过域构造器**（`Timeline(...)` 全量校验），
   不许绕过——水合出来的对象要重新经过 `__post_init__` 的全部拒绝逻辑。
5. **三条跨聚合根不变式全部由数据库结构强制**，仓储层只负责把 `IntegrityError`
   翻译成模块异常：
   - **① + ② 用一条复合外键**：`editing_jobs (timeline_id, timeline_revision, project_id)
     REFERENCES timelines (timeline_id, revision, project_id)`。它同时保证
     「timeline_revision 真实存在」（②）与「job.project_id == 该 timeline 的 project_id」
     （①）——普通单列外键管不住这个三角，正是 LE-04 终审说的那件事；
   - **③ 用部分唯一索引**：`CREATE UNIQUE INDEX ... ON editing_jobs (timeline_id,
     timeline_revision) WHERE status = 'queued'`。
   - 三条各自要有**违例注入测试**：手工 INSERT 违例行，断言 PG 拒绝
     （`IntegrityError`），**而不是只测仓储方法不产生违例**——后者是「门禁绿 ≠ 门禁在守」。
6. **枚举一律存 `.value` 字符串、水合时在仓储边界解析回枚举。** LE-04 第 3 条账：
   `is_terminal` 对裸字符串静默返回 `False`（「仍在运行」，不安全方向）——所以水合
   出来的 `EditingJob.status` 必须是 `EditingJobStatus` 成员，且有用例钉住
   「库里一个非法状态字符串 → 仓储抛模块异常，而不是水合出一个带裸字符串的对象」。
7. **`materials.content_digest` 加 UNIQUE 约束**，仓储提供 `find_by_digest`。域层只管
   格式（SHA-256 hex），「同内容不重复入库」这半边由 DB 唯一性承担。重复登记的
   仓储行为：抛模块异常（带 digest 不带路径），不做 upsert——素材是否复用由调用方决定。
8. **仓储 house 风格**：构造器 isinstance 守卫；入参 isinstance 守卫；不写 SQL 字符串，
   全走 Core 表达式。异常映射按下面这套形状，**`from None` 每一条都是必须的**——LE-07
   实测过异常链把私有路径带进日志：

   ```python
   except IntegrityError:        raise <模块>AlreadyRegistered from None
   except (OSError, SQLAlchemyError): raise <模块>Unavailable from None
   except Exception:             raise <模块>Unavailable from None
   ```

   > **本条原文写的是「`except SQLAlchemyError: raise <模块>Rejected from None`」，
   > 由 T1 实测推翻，2026-07-29 就地更正。** 两处错：
   >
   > - **捕获面不够。** 连接被拒是 asyncio 的 `ConnectionRefusedError`（`OSError`），
   >   认证失败是 asyncpg 的 `InvalidPasswordError`（`PostgresError` → `Exception`），
   >   两者都不是 `SQLAlchemyError` 子类，会带着 host:port 或角色名穿过去。house 里
   >   `(OSError, SQLAlchemyError)` 有五处先例（`task_event_convergence_repository.py:723`
   >   等），catch-all 尾巴的先例是 `bilibili_publish_repository.py:158-163`；
   > - **范例选错了。** `workbench_metrics_repository.py` 是只读投影，没有重复键路径，
   >   所以它一个 `Rejected` 就够用；带 `save` 的仓储照抄它，会把「已存在」「不存在」
   >   「库挂了」压成同一个异常，调用方无法判断能否重试，LE-06 也没法分 409/404/503。
   >   带写入的仓储按 `customer_account_repository.py:162-165` 的两分法，结构对齐
   >   `customer_accounts.py`：一个私有 `_XxxFailure` 基类，`message` 写成类属性，
   >   `__init__` 不收参数（于是 `raise X("detail")` 是 TypeError 而不是泄漏），四个
   >   具体类各覆盖自己的 `message`。**语义名对齐，字面名不必**——T1 用的是
   >   `AlreadyRegistered` 而不是 `customer_accounts.py` 的 `AlreadyExists`，取
   >   `InstallationAlreadyRegistered` 的先例；「登记过了」比「存在」更贴近仓储在说的事。
9. **Material 描述保护的结构性边界测试**（台账明令）：AST 测试禁止 `material.py` 之外
   的模块对 `Material` 调 `dataclasses.replace(` 或直接构造 `Material(...)`。
   **样板 `test_shipped_package_boundary.py` 不在本分支**（台账 LE-05 行已登记），
   从零写：读 AST、按名字匹配 `Call` 节点，测试文件自身与 `material.py` 豁免。
   仓储水合是合法构造点——把水合函数放进 `material.py`（或以显式豁免登记，二选一，
   实施时按侵入性小的定，写明理由）。
10. **时间戳全部 `TIMESTAMP WITH TIME ZONE`**，域层已保证 UTC aware；水合后 tzinfo
    必须是 UTC（有用例）。

## Global Constraints

- TDD 铁律：先写失败测试、实跑看红、最小实现、实跑看绿；集成测试的红必须来自
  **真实 PG**（`managed_test_postgres`），不是内存替身（CLAUDE.md §4.2：数据库从第一天
  就是 PostgreSQL）。
- 边界纪律（当天四次事故沉淀）：凡引入有界的东西（长度上限、区间、跨字段比较），
  两端端点用例 + 两端越界一格用例 + **两个方向**的越界一格变异实跑；跨维夹具必须
  两维有区分度；拒绝夹具贴边界。
- 变异盲区补查（LE-09 T4a 沉淀）：变异只能测「存在的代码」；每个第三方调用（这里主要是
  SQLAlchemy/psycopg 的异常面）要问一遍「失败时它到底抛什么」，列一遍夹具没覆盖的
  畸形输入形状（非法枚举串、越界 JSONB、NULL 混入）。
- 单元层覆盖率 100%/branch（`fail_under = 100`）；集成测试不计覆盖率但必须真跑。
- 端口与资源：`managed_test_postgres` 已按项目隔离规范管理 compose 实例，不另起。
- 提交：中文、`feat(le-05):`/`test(le-05):` 前缀、逐文件 `git add`、每 task 一提交、
  同提交内更新 `docs/development/LE-05.md`。
- 台账三份副本：本线只改 smart-edit 副本；不碰另两条线的文件。

## Tasks

### T1 `editing_projects` 表 + 迁移 0036 + 仓储

表：`project_id UUID PK`、`title TEXT NOT NULL`、`output_width/output_height/output_fps
INTEGER NOT NULL`、`caption_font_key TEXT NOT NULL`、`caption_font_px/caption_stroke_px
INTEGER NOT NULL`、`caption_line_spacing DOUBLE PRECISION NOT NULL`、`created_at TIMESTAMPTZ
NOT NULL`。值对象拍平成列（两个值对象都是纯标量，JSONB 反而丢类型）。

仓储 `SqlAlchemyEditingProjectRepository`：`save(project)`（重复 project_id → 模块异常，
不 upsert）、`get(project_id) -> EditingProject`（缺行 → 模块异常）。

RED 清单：真实 PG 落库后 SELECT 断言逐列值；get 水合出的对象与存入的**相等**（域相等性）；
缺行拒绝；重复 save 拒绝；水合经过域构造器（库里手工塞一行 `font_px=0` 的非法行 →
get 抛域异常而不是返回对象）；非 `EditingProjectId` 入参拒绝；`alembic upgrade head` +
`downgrade -1` 往返干净。

### T2 `materials` 表 + 迁移 0037 + 仓储 + AST 边界测试

表：`material_id UUID PK`、`kind TEXT`、`duration_ms/width/height INTEGER NULL`、
`content_digest CHAR(64) NOT NULL UNIQUE`、`has_audio/has_speech BOOLEAN`、
`audio_loudness_lufs DOUBLE PRECISION NULL`、`speech_segments_ms JSONB NOT NULL`、
`speech_transcript TEXT NULL`、`shot_boundaries_ms JSONB NOT NULL`、
`ai_description TEXT NULL`、`ai_tags JSONB NOT NULL`、`description_source TEXT NOT NULL`、
`described_at TIMESTAMPTZ NULL`。

仓储：`save`、`get`、`find_by_digest(digest) -> Material | None`、
`update_description(material)`（描述三字段 + described_at 的受控更新，其余列不动）。

RED 清单：落库/水合往返相等（含 `speech_segments_ms` 嵌套元组——JSONB 回来是 list，
水合必须转回 tuple 并过域校验）；digest 重复 → 模块异常且消息不含路径；
`find_by_digest` 命中/不命中；库里非法 kind 字符串 → 水合拒绝；AST 边界测试
（决策 9，含「测试自己能抓到自己」的自证：临时造一个违例模块字符串喂给检查函数，
断言它红——不是只对现状绿）。

### T3 `timelines` 表 + 迁移 0038 + 仓储

表：`timeline_id UUID`、`revision INTEGER`、`project_id UUID NOT NULL
REFERENCES editing_projects`、`duration_ms INTEGER NOT NULL`、`tracks JSONB NOT NULL`、
`created_at TIMESTAMPTZ NOT NULL`；`PRIMARY KEY (timeline_id, revision)`；
`UNIQUE (timeline_id, revision, project_id)`（决策 3 的靶子，注释写明用途）。

仓储：`save(timeline)`（同 `(timeline_id, revision)` 重复 → 模块异常——修订不可覆写）、
`get(timeline_id, revision)`、`latest_revision(timeline_id) -> Timeline | None`。

RED 清单：往返相等（tracks 全树，含 transition、gain、text clip 各形态至少一份）；
修订不可覆写；`latest_revision` 取最大修订、空表 None；水合过域构造器（库里手工塞
一行 track 顺序非法的 JSONB → get 抛域异常；这条同时证明「JSONB 不是信任边界」）；
project_id 引用不存在的项目 → 外键拒绝（违例注入）。

### T4 `editing_jobs` 表 + 迁移 0039 + 仓储 + 三条不变式

表：`job_id UUID PK`、`project_id UUID NOT NULL`、`timeline_id UUID NOT NULL`、
`timeline_revision INTEGER NOT NULL`、`status TEXT NOT NULL`、`failure_code TEXT NULL`、
`output_artifact_id UUID NULL`、`created_at/updated_at TIMESTAMPTZ NOT NULL`；
复合外键与部分唯一索引按决策 5。

仓储：`save(job)`（新建）、`update(job)`（按 job_id 全列更新，`updated_at` 必须前进——
乐观时序，旧 `updated_at` 覆盖新值要拒）、`get(job_id)`。

RED 清单（不变式三条全部走**违例注入**）：
- ① 手工 INSERT 一行 `project_id` 与 timeline 归属不符的 job → `IntegrityError`；
- ② 手工 INSERT 指向不存在 revision 的 job → `IntegrityError`；
- ③ 同 `(timeline_id, revision)` 第二个 QUEUED → `IntegrityError`；**但一个 QUEUED +
  一个 SUCCEEDED 共存必须接受**（部分索引的 WHERE 是承重的，两侧都要钉）；
- 库里非法 status 字符串 → 水合拒绝（决策 6 的钉子）；
- `update` 的时序拒绝：两端端点（相等的 `updated_at` 算前进还是拒绝——**定死：拒绝**，
  幂等重放靠状态机不靠仓储）+ 越界一格；
- 往返相等（六状态 × 事实字段组合抽代表）。

### T5 收口

`docs/development/LE-05.md` 补齐（RED/GREEN/失败矩阵/
违例注入的实跑输出/变异清单）；台账 LE-05 →（集成测试全绿则）`✅ 已完成`——LE-05 是
纯后端，按 CLAUDE.md §8「后端端点可在服务端任务完成真实 PostgreSQL 门禁」，正式 App
纵向验收归 LE-06/LE-17 登记；全量 `pytest -q` + 门禁脚本；计数脚本。

`alembic_head.py` **不需要对齐版本号**：它用 `ScriptDirectory.get_heads()` 从已提交的
迁移脚本解析，并在头数不等于 1 时报错。原文要求「对齐 `0039`」与该文件现状不符，
T1 实测后删除。

**T5 的 final gate 必须跑整个 `tests/integration` 目录，不许挑子集。** 依据是结构而不是
某一次的红：`conftest.py` 的 `postgresql_url` 是 `scope="session"` 的单个实例，而大量
用例在开头 `delete()` 共享表（`installations`、`tasks` 等）——同一个库、跨文件互删，
换一个子集组合就换一套前置数据。所以子集全绿只说明「这批一起跑没事」，不构成回归证据。
（T1 期间审查者报告过某子集 8 failed 并判为既有互扰；T1 实跑的两个子集均全绿、未复现，
故此处不引用那个数字——上面的结构理由本身已经足够。）

## 失败矩阵（§9 适用行）

| 场景 | 落点 |
| --- | --- |
| 重复请求（重复 save/重复 QUEUED） | T1/T3/T4 |
| 非法状态转换的持久化面（非法枚举串水合） | T1/T3/T4 |
| 多实例竞争（并发两个 QUEUED 靠部分唯一索引，不靠应用层检查） | T4 |
| 断连/库不可达 | 各仓储 `SQLAlchemyError → Rejected from None` 用例 |
| 敏感信息泄漏（异常消息不含连接串/路径） | 各 task 一条渲染断言 |
