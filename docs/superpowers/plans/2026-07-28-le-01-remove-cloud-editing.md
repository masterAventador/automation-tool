# LE-01 删除云剪辑路线 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除阿里云 IMS 云剪辑的全部实现、供应商抽象层、凭据配置界面与 Tauri 命令，为本地 FFmpeg 剪辑腾出干净的地基，并留下防回归守卫。

**Architecture:** 先写守卫测试断言"云剪辑相关模块不存在"（RED），再分五块删除让守卫转绿（GREEN）。删除严格区分"云剪辑供应商专属"与"剪辑工作台本身"——后者由 LE-17 重写，本任务不动。数据库迁移不删文件（会断 alembic 链），改为新增 drop 迁移。

**Tech Stack:** Python 3.12 / pytest / SQLAlchemy / Alembic / PostgreSQL、TypeScript / Vitest / Playwright、Rust / Tauri v2 / cargo test

## Global Constraints

- 工作树 `/Users/aventador/sourceCode/automation-tool/wt/smart-edit`，分支 `smart-edit`，基点 `origin/main`
- **中文 commit message**，conventional 前缀保留英文，冒号后用中文，不加任何 AI 署名
- 每个 Task 独立可提交、可回滚；禁止 `git add -A`，必须逐个文件 `git add`
- 后端测试命令：`cd backend && .venv/bin/python -m pytest`
- 前端测试命令：`pnpm --dir frontend exec vitest run`
- Rust 测试命令：`cd frontend/src-tauri && cargo test`
- 用户可见文案全中文，无未解释术语；改动后须过 `python3 scripts/check_user_facing_branding.py`
- 第三方源门禁须保持退出 0：`python3 scripts/check_third_party_sources.py`
- 本机测试不得弹出窗口，浏览器与 Tauri 一律无头/隐藏窗口运行

---

## File Structure

### 删除（云剪辑供应商专属）

**后端领域层与仓储**

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_provider.py` | 967 | 阿里云 Provider 实现 |
| `backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_staging.py` | 686 | OSS 暂存与费用预估 |
| `backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_output.py` | 557 | 成片导入 |
| `backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_reconciliation.py` | 491 | 回调对账 |
| `backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_callback.py` | 180 | 回调验签 |
| `backend/src/automation_tool/control_plane/domain/video_editing_provider.py` | 302 | Provider 抽象（只剩一条实现时属过度设计） |
| `backend/src/automation_tool/control_plane/domain/video_editing_provider_conformance.py` | 247 | 多供应商一致性套件 |
| `backend/src/automation_tool/control_plane/domain/fake_second_editing_provider.py` | — | 假的第二 Provider |
| `backend/src/automation_tool/control_plane/domain/video_editing.py` | 337 | 领域对象（LE-02～04 重写） |
| `backend/src/automation_tool/control_plane/domain/video_editing_outputs.py` | 256 | 成片登记（LE-02～04 重写） |
| `backend/src/automation_tool/control_plane/infrastructure/database/aliyun_editing_intent_repository.py` | 158 | 阿里云意图仓储 |
| `backend/src/automation_tool/control_plane/infrastructure/database/editing_output_ledger_repository.py` | — | 成片台账仓储（LE-05 重写） |

**后端测试**：`backend/tests/unit/control_plane/domain/` 下 9 个 editing 测试文件、`backend/tests/integration/` 下 2 个、`backend/tests/real_cloud/` 下 4 个 VE 测试。

**前端**

| 文件 | 职责 |
| --- | --- |
| `frontend/src/features/settings/VideoEditingServiceSettings.tsx` | 阿里云凭据配置界面 |
| `frontend/src/features/settings/VideoEditingServiceSettings.test.tsx` | 同上测试 |
| `frontend/src/features/settings/video-editing-service-gateway.ts` | 服务网关接口 |
| `frontend/src/platform/tauri/video-editing-service-gateway.ts` | Tauri 服务网关实现 |
| `frontend/src/platform/tauri/video-editing-service-gateway.test.ts` | 同上测试 |
| `frontend/src/features/video-editing/provider-replaceability.test.tsx` | 供应商可替换性证明 |
| `frontend/e2e-tauri/video-editing-service.spec.ts` | 云剪辑服务配置 E2E |

**Rust**

| 文件 | 职责 |
| --- | --- |
| `frontend/src-tauri/src/video_editing_service_settings.rs` | 凭据存储与连接测试 |
| `frontend/src-tauri/tests/video_editing_service_settings.rs` | 单元测试 |
| `frontend/src-tauri/tests/video_editing_service_settings_real.rs` | 真实凭据测试 |

**契约与脚本**

| 文件 | 职责 |
| --- | --- |
| `contracts/video/aliyun-ims-editing-staging.v1.json` | 阿里云暂存与计费契约 |
| `scripts/run_ve_03_acceptance.py` | VE-03 验收脚本 |
| `scripts/run_ve_04_acceptance.py` | VE-04 验收脚本 |

### 修改

| 文件 | 改动 |
| --- | --- |
| `backend/src/automation_tool/control_plane/infrastructure/database/schema.py:2343-2414` | 删 `aliyun_editing_intents` 表与索引 |
| `backend/src/automation_tool/control_plane/infrastructure/database/schema.py:2416+` | 删 `editing_output_lineages` 表 |
| `frontend/src/main.tsx:20,23,47,50,73,76` | 移除服务网关 import 与注入 |
| `frontend/src/app/WorkbenchShell.tsx:54-55,405-423,474,500` | 移除服务设置页与其 fallback 网关 |
| `frontend/src-tauri/src/lib.rs:35,259-306,4371,4471-4474,4538-4541,4636-4639` | 移除模块声明、4 个 command 定义与 3 处注册 |
| `frontend/src-tauri/tests/acceptance_gate_honesty.rs` | 移除 VE 验收脚本相关条目 |
| `scripts/run_cq_04_acceptance.py` | 移除云剪辑环节 |
| `docs/embedded-browser-video-studio-roadmap.md` | 移除 VE-01～VE-08 八行、9.7 小节、计数修正 |

### 新建

| 文件 | 职责 |
| --- | --- |
| `backend/migrations/versions/20260728_0035_drop_cloud_editing_tables.py` | drop 两张云剪辑表（不删历史迁移） |
| `backend/tests/unit/control_plane/test_cloud_editing_removed.py` | 后端删除守卫 |
| `frontend/src/app/no-cloud-editing.test.ts` | 前端删除守卫 |
| `scripts/check_local_editing_roadmap_counts.py` | 本地剪辑台账计数守卫 |
| `scripts/test_check_local_editing_roadmap_counts.py` | 计数守卫自身的测试 |

### 明确保留（LE-17 重写，本任务不动）

- `frontend/src/features/video-editing/VideoEditingWorkbench.tsx`（工作台 UI）
- `frontend/src/features/video-editing/video-editing-dto.ts`、`video-editing-gateway.ts`、`local-video-editing-gateway.ts`
- `frontend/e2e/video-editing-tabs.spec.ts`、`frontend/e2e-tauri/video-editing.spec.ts`
- `frontend/src/app/production-wiring.test.ts` 中的 `it.fails("videoEditingGateway is handed a real Tauri gateway")` —— 这条已知失败正是 LE-17 的验收信号，删掉就丢了

---

## Task 1: 删除守卫测试（RED）

**Files:**
- Create: `backend/tests/unit/control_plane/test_cloud_editing_removed.py`
- Create: `frontend/src/app/no-cloud-editing.test.ts`

**Interfaces:**
- Consumes: 无
- Produces: 两个守卫测试，Task 2～6 逐步让它们转绿。守卫按**模块可导入性**和**文件存在性**断言，不按字符串搜索，避免注释里提到就误报

- [ ] **Step 1: 写后端守卫测试**

创建 `backend/tests/unit/control_plane/test_cloud_editing_removed.py`：

```python
"""Guard: the Aliyun cloud-editing route is gone and must not come back.

LE-01 removed it. The evidence that made us remove it: VE-01..VE-08 were all
marked done while `control_plane/api/` had no editing route at all, so nothing
users could reach ever called this code. Re-adding a vendor module here means
someone is rebuilding that same unreachable layer.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_REMOVED_DOMAIN_MODULES = (
    "aliyun_ims_editing_provider",
    "aliyun_ims_editing_staging",
    "aliyun_ims_editing_output",
    "aliyun_ims_editing_reconciliation",
    "aliyun_ims_editing_callback",
    "video_editing_provider",
    "video_editing_provider_conformance",
    "fake_second_editing_provider",
)

_REMOVED_DATABASE_MODULES = (
    "aliyun_editing_intent_repository",
    "editing_output_ledger_repository",
)

_DOMAIN_PACKAGE = "automation_tool.control_plane.domain"
_DATABASE_PACKAGE = "automation_tool.control_plane.infrastructure.database"


@pytest.mark.parametrize("module_name", _REMOVED_DOMAIN_MODULES)
def test_cloud_editing_domain_module_is_gone(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"{_DOMAIN_PACKAGE}.{module_name}")


@pytest.mark.parametrize("module_name", _REMOVED_DATABASE_MODULES)
def test_cloud_editing_database_module_is_gone(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"{_DATABASE_PACKAGE}.{module_name}")


def test_schema_declares_no_cloud_editing_tables() -> None:
    from automation_tool.control_plane.infrastructure.database import schema

    table_names = set(schema.metadata.tables)
    assert "aliyun_editing_intents" not in table_names
    assert "editing_output_lineages" not in table_names


def test_aliyun_editing_contract_file_is_gone() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    contract = repository_root / "contracts/video/aliyun-ims-editing-staging.v1.json"
    assert not contract.exists()
```

- [ ] **Step 2: 运行后端守卫，确认失败**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/test_cloud_editing_removed.py -v
```

Expected: FAIL。12 个用例应全部失败——`importlib.import_module` 成功返回而非抛 `ModuleNotFoundError`，`metadata.tables` 里两张表都在，契约文件存在。

- [ ] **Step 3: 写前端守卫测试**

创建 `frontend/src/app/no-cloud-editing.test.ts`：

```typescript
/**
 * Guard: the cloud-editing credential UI and its Tauri bridge are gone.
 *
 * LE-01 removed the Aliyun route. These files configured vendor credentials for
 * a service the product never reached — `main.tsx` handed the workbench a
 * sessionStorage draft gateway and submission always threw. Their return means
 * someone is rebuilding that layer.
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const PROJECT_ROOT = resolve(__dirname, "../..");

const REMOVED_FILES = [
  "src/features/settings/VideoEditingServiceSettings.tsx",
  "src/features/settings/VideoEditingServiceSettings.test.tsx",
  "src/features/settings/video-editing-service-gateway.ts",
  "src/platform/tauri/video-editing-service-gateway.ts",
  "src/platform/tauri/video-editing-service-gateway.test.ts",
  "src/features/video-editing/provider-replaceability.test.tsx",
  "e2e-tauri/video-editing-service.spec.ts",
] as const;

const RETAINED_FILES = [
  "src/features/video-editing/VideoEditingWorkbench.tsx",
  "src/features/video-editing/video-editing-gateway.ts",
  "src/app/production-wiring.test.ts",
] as const;

describe("cloud editing removal", () => {
  it.each(REMOVED_FILES)("%s is gone", (relativePath) => {
    expect(existsSync(resolve(PROJECT_ROOT, relativePath))).toBe(false);
  });

  it.each(RETAINED_FILES)("%s is kept for LE-17 to rewrite", (relativePath) => {
    expect(existsSync(resolve(PROJECT_ROOT, relativePath))).toBe(true);
  });
});
```

- [ ] **Step 4: 运行前端守卫，确认失败**

```bash
pnpm --dir frontend exec vitest run src/app/no-cloud-editing.test.ts
```

Expected: FAIL。7 个 "is gone" 用例失败（文件都还在），3 个 "is kept" 用例通过。

- [ ] **Step 5: 提交守卫测试**

```bash
git add backend/tests/unit/control_plane/test_cloud_editing_removed.py frontend/src/app/no-cloud-editing.test.ts
git commit -m "test(le-01): 先落删除守卫，断言云剪辑模块与凭据界面不存在

守卫按模块可导入性与文件存在性断言，不按字符串搜索，避免注释提到就误报。
同时断言工作台本身与 production-wiring 的已知失败用例必须保留——后者是
LE-17 接线成功的验收信号，删掉就丢了。

当前为 RED：后端 11 个用例全失败，前端 7 个 is-gone 用例失败。"
```

---

## Task 2: 删除后端领域层与测试

**Files:**
- Delete: 12 个后端生产文件、15 个测试文件（清单见 File Structure）
- Test: `backend/tests/unit/control_plane/test_cloud_editing_removed.py`

**Interfaces:**
- Consumes: Task 1 的后端守卫测试
- Produces: `automation_tool.control_plane.domain` 下不再有任何 editing 模块，且该包的 `__init__.py` 不再重新导出它们；守卫新增 `test_parent_packages_still_import`，用例总数 12 → 13；LE-02 将在空白处新建 `material.py` 与 `video_editing.py`

- [ ] **Step 1: 确认这些模块没有外部引用**

```bash
grep -rn "video_editing\|editing_provider\|aliyun_ims_editing\|editing_output\|EditingProject\|EditingJob\|EditingTimeline" backend/src --include='*.py' \
  | grep -v "/domain/aliyun_ims_editing\|/domain/video_editing\|/domain/fake_second_editing\|/database/aliyun_editing_intent\|/database/editing_output_ledger"
```

Expected: **恰好两处**命中，其余为空：

1. `schema.py` 的表定义行 —— Task 3 处理，本任务不动
2. `backend/src/automation_tool/control_plane/domain/__init__.py` 第 151 行与 166 行的两条 `from ... import (...)`，以及 `__all__` 中对应的重新导出条目 —— **本任务 Step 4 处理**

若出现这两者以外的文件，**停下来报 BLOCKED**——说明有本计划未覆盖的引用，需要先补进计划。

已核实这些符号在 domain 包外的真实消费者只有 `aliyun_editing_intent_repository.py` 与 `editing_output_ledger_repository.py`，两者都在本任务的删除清单内，因此清理 `__init__.py` 不会断开任何保留代码。

- [ ] **Step 2: 给守卫加父包健康断言**

删掉子模块会让父包 `__init__.py` 的 import 失败，而**父包坏掉时 `importlib.import_module` 抛的同样是 `ModuleNotFoundError`**——守卫分辨不出「子模块真的没了」和「父包坏了」，8 个模块用例会因为错误的原因转绿。加一条断言堵住这个：

在 `backend/tests/unit/control_plane/test_cloud_editing_removed.py` 中，紧接 `_DATABASE_PACKAGE` 常量定义之后加入：

```python
def test_parent_packages_still_import() -> None:
    """The module guards below assert ModuleNotFoundError. A broken parent
    package raises exactly that too, so without this check those guards could
    go green because the package itself stopped importing — the opposite of
    what they are meant to prove.
    """
    importlib.import_module(_DOMAIN_PACKAGE)
    importlib.import_module(_DATABASE_PACKAGE)
```

- [ ] **Step 3: 删除后端生产文件**

```bash
cd /Users/aventador/sourceCode/automation-tool/wt/smart-edit
git rm backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_provider.py \
       backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_staging.py \
       backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_output.py \
       backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_reconciliation.py \
       backend/src/automation_tool/control_plane/domain/aliyun_ims_editing_callback.py \
       backend/src/automation_tool/control_plane/domain/video_editing_provider.py \
       backend/src/automation_tool/control_plane/domain/video_editing_provider_conformance.py \
       backend/src/automation_tool/control_plane/domain/fake_second_editing_provider.py \
       backend/src/automation_tool/control_plane/domain/video_editing.py \
       backend/src/automation_tool/control_plane/domain/video_editing_outputs.py \
       backend/src/automation_tool/control_plane/infrastructure/database/aliyun_editing_intent_repository.py \
       backend/src/automation_tool/control_plane/infrastructure/database/editing_output_ledger_repository.py
```

- [ ] **Step 4: 清理 `domain/__init__.py` 的重新导出**

删除第 151 行起 `from automation_tool.control_plane.domain.video_editing import (...)` 整段（含其 12 个符号），以及第 166 行起 `from automation_tool.control_plane.domain.video_editing_provider import (...)` 整段（含其 12 个符号）。

同时从 `__all__` 中删除这 24 个符号对应的条目。它们包括（以文件中实际出现的为准）：`EDITING_JOB_TERMINAL_STATUSES`、`MAX_EDITING_PROJECT_TITLE_CHARACTERS`、`MAX_EDITING_SOURCE_ARTIFACTS`、`EditingFailureCode`、`EditingJob`、`EditingJobId`、`EditingJobStateMachine`、`EditingJobStatus`、`EditingProject`、`EditingProjectId`、`EditingTimeline`、`InvalidEditingJobTransition`、`InvalidVideoEditingModel`、`MAX_EDITING_IDEMPOTENCY_KEY_CHARACTERS`、`EditingIdempotencyKey`、`EditingProviderCapabilities`、`EditingProviderErrorCode`，以及 `video_editing_provider` 导出的其余符号。

**逐个核对**：`__all__` 里每删一项，都应能在上面两段被删的 import 中找到来源。若某个符号来自其他仍保留的模块，不要删它。

- [ ] **Step 5: 删除对应测试**

```bash
git rm backend/tests/unit/control_plane/domain/test_aliyun_ims_editing_provider.py \
       backend/tests/unit/control_plane/domain/test_aliyun_ims_editing_staging.py \
       backend/tests/unit/control_plane/domain/test_aliyun_ims_editing_output.py \
       backend/tests/unit/control_plane/domain/test_aliyun_ims_editing_reconciliation.py \
       backend/tests/unit/control_plane/domain/test_aliyun_ims_editing_callback.py \
       backend/tests/unit/control_plane/domain/test_video_editing_provider.py \
       backend/tests/unit/control_plane/domain/test_editing_provider_conformance.py \
       backend/tests/unit/control_plane/domain/test_video_editing.py \
       backend/tests/unit/control_plane/domain/test_video_editing_outputs.py \
       backend/tests/integration/test_aliyun_editing_intent_repository.py \
       backend/tests/integration/test_editing_output_ledger_repository.py \
       backend/tests/real_cloud/test_ve05_aliyun_real_submission.py \
       backend/tests/real_cloud/test_ve06_aliyun_real_reconciliation.py \
       backend/tests/real_cloud/test_ve07_aliyun_real_output_import.py \
       backend/tests/real_cloud/test_ve08_aliyun_real_conformance.py
```

- [ ] **Step 6: 清掉 `__pycache__` 残留，否则守卫会误判模块仍可导入**

```bash
find backend/src backend/tests -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; true
```

- [ ] **Step 7: 运行守卫，模块类用例应转绿**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/test_cloud_editing_removed.py -v
```

Expected: 13 个用例中 11 个 PASS —— 10 个模块用例（8 domain + 2 database）加 `test_parent_packages_still_import`；`test_schema_declares_no_cloud_editing_tables` 与 `test_aliyun_editing_contract_file_is_gone` 仍 FAIL（Task 3、Task 6 处理）。

**`test_parent_packages_still_import` 必须 PASS。** 它红了说明 `__init__.py` 没清干净，此时那 10 个模块用例的绿是假的，不可采信。

- [ ] **Step 8: 运行后端全量单元测试，确认没删断别的**

```bash
cd backend && .venv/bin/python -m pytest tests/unit -q
```

Expected: 除守卫里那 2 个已知 FAIL 外全部通过。若出现 `ImportError` 或 `ModuleNotFoundError`，说明有文件 import 了被删模块而 Step 1 没查出来，**停下来补计划**。

- [ ] **Step 9: 提交**

```bash
git add -u backend/
git add backend/tests/unit/control_plane/test_cloud_editing_removed.py
git commit -m "refactor(le-01): 删除阿里云剪辑领域层、Provider 抽象与全部相关测试

删除 12 个生产文件（约 3000 行）与 15 个测试文件（约 3700 行）。Provider
抽象与一致性套件是为多供应商准备的，只剩本地一条实现时属过度设计，一并
删除；video_editing.py 与 video_editing_outputs.py 由 LE-02 到 LE-04 重写。

schema.py 的两张表与契约文件分别由后续步骤处理，守卫中对应两个用例仍红。"
```

---

## Task 3: 清理数据库 schema 并新增 drop 迁移

**Files:**
- Modify: `backend/src/automation_tool/control_plane/infrastructure/database/schema.py:2343-2460`
- Create: `backend/migrations/versions/20260728_0035_drop_cloud_editing_tables.py`
- Test: `backend/tests/unit/control_plane/test_cloud_editing_removed.py::test_schema_declares_no_cloud_editing_tables`

**Interfaces:**
- Consumes: Task 2 已删除仓储代码
- Produces: `schema.metadata` 不含两张云剪辑表；alembic 链尾变为 `20260728_0035`，LE-05 的新迁移应以它为 `down_revision`

**为什么不删迁移文件**：alembic 链为 `0031 → 0032(aliyun_editing_intents) → 0033(bilibili) → 0034(editing_output_lineages)`。`0032` 位于链中间，`0033` 的 `down_revision` 指向它，删文件会断链；且已执行过迁移的数据库会残留表。迁移是历史记录，只能往前推进。

- [ ] **Step 1: 查看要删除的表定义确切范围**

```bash
sed -n '2340,2465p' backend/src/automation_tool/control_plane/infrastructure/database/schema.py
```

记下 `aliyun_editing_intents = Table(` 起始行到 `editing_output_lineages` 定义结束行（含其后的 `Index(...)` 声明）。

- [ ] **Step 2: 从 schema.py 删除两张表及其索引**

删除 `aliyun_editing_intents` 的 `Table(...)` 定义、其 `Index("ux_aliyun_editing_intents_vendor_job_id", ...)`，以及 `editing_output_lineages` 的 `Table(...)` 定义与其全部 `Index` 声明。保留文件中其他表不动。

- [ ] **Step 3: 运行 schema 守卫用例，确认转绿**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/test_cloud_editing_removed.py::test_schema_declares_no_cloud_editing_tables -v
```

Expected: PASS

- [ ] **Step 4: 新增 drop 迁移**

创建 `backend/migrations/versions/20260728_0035_drop_cloud_editing_tables.py`：

```python
"""Drop the Aliyun cloud-editing tables.

LE-01 removed the cloud-editing route. The two migrations that created these
tables (20260723_0032, 20260723_0034) stay on disk: 0032 sits mid-chain with
0033 pointing at it, so deleting the file would break the chain, and databases
that already ran them would keep the tables regardless.

Revision ID: 20260728_0035
Revises: 20260723_0034
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0035"
down_revision: str | None = "20260723_0034"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.drop_table("editing_output_lineages")
    op.drop_table("aliyun_editing_intents")


def downgrade() -> None:
    raise NotImplementedError(
        "LE-01 dropped the cloud-editing route; recreating these tables would "
        "resurrect a vendor layer no product path reaches."
    )
```

- [ ] **Step 5: 对真实 PostgreSQL 验证迁移**

按项目端口与资源命名规范起隔离实例（**先确认目标端口未被占用**，不得接管来源不明的进程）：

```bash
cd backend && .venv/bin/python -m alembic upgrade head
```

Expected: 迁移成功，`20260728_0035` 成为 head。验证两张表确实消失：

```bash
cd backend && .venv/bin/python -m alembic current
```

Expected: 输出含 `20260728_0035 (head)`

- [ ] **Step 6: 提交**

```bash
git add backend/src/automation_tool/control_plane/infrastructure/database/schema.py \
        backend/migrations/versions/20260728_0035_drop_cloud_editing_tables.py
git commit -m "refactor(le-01): schema 移除两张云剪辑表，新增 0035 drop 迁移

不删 0032 与 0034 迁移文件：0032 位于链中间且 0033 的 down_revision 指向
它，删文件会断链，已迁移过的库也仍会残留表。迁移是历史记录，只能往前推。

downgrade 显式抛错而不是重建表——重建等于让一个产品路径够不着的供应商层
复活。LE-05 的新迁移以 0035 为 down_revision。"
```

---

## Task 4: 删除前端云剪辑服务界面与网关

**Files:**
- Delete: 7 个前端文件（清单见 File Structure）
- Modify: `frontend/src/main.tsx`、`frontend/src/app/WorkbenchShell.tsx`
- Test: `frontend/src/app/no-cloud-editing.test.ts`

**Interfaces:**
- Consumes: Task 1 的前端守卫
- Produces: `WorkbenchShell` 不再接收 `videoEditingServiceGateway` prop；`main.tsx` 只注入 `videoEditingGateway`（仍是 sessionStorage 草稿版，LE-17 换成真网关）

- [ ] **Step 1: 删除文件**

```bash
git rm frontend/src/features/settings/VideoEditingServiceSettings.tsx \
       frontend/src/features/settings/VideoEditingServiceSettings.test.tsx \
       frontend/src/features/settings/video-editing-service-gateway.ts \
       frontend/src/platform/tauri/video-editing-service-gateway.ts \
       frontend/src/platform/tauri/video-editing-service-gateway.test.ts \
       frontend/src/features/video-editing/provider-replaceability.test.tsx \
       frontend/e2e-tauri/video-editing-service.spec.ts
```

- [ ] **Step 2: 从 `main.tsx` 移除服务网关**

删除第 20 行 `import { TauriVideoEditingServiceGateway } ...`、第 47 行 `const videoEditingServiceGateway = new TauriVideoEditingServiceGateway();`、第 73 行 `videoEditingServiceGateway={videoEditingServiceGateway}`。

**保留**第 23 行 `createLocalVideoEditingGateway` import、第 50 行的赋值与第 76 行的传参——工作台本身归 LE-17 处理。

- [ ] **Step 3: 从 `WorkbenchShell.tsx` 移除服务设置页**

删除第 54-55 行两个 import、第 405-423 行 `shellVideoEditingServiceGateway` 常量、第 474 行 prop 类型声明、第 500 行默认参数，以及 JSX 中渲染 `<VideoEditingServiceSettings ... />` 的整段。

**保留**第 70-72 行 `VideoEditingGatewayError` / `VideoEditingGateway` import、第 384-402 行 `shellVideoEditingGateway`、第 691 行 `editingGateway={videoEditingGateway}`。

- [ ] **Step 4: 运行前端守卫，确认全绿**

```bash
pnpm --dir frontend exec vitest run src/app/no-cloud-editing.test.ts
```

Expected: 10 个用例全 PASS（7 个 is-gone + 3 个 is-kept）

- [ ] **Step 5: 运行前端全量测试与类型检查**

```bash
pnpm --dir frontend exec vitest run
pnpm --dir frontend typecheck
pnpm --dir frontend lint
```

Expected: 全部通过。`production-wiring.test.ts` 中 `it.fails("videoEditingGateway is handed a real Tauri gateway")` **应继续保持 it.fails 状态**（它断言的问题由 LE-17 修复）；若它变成非预期通过，说明 Step 2 误删了 `videoEditingGateway` 注入。

- [ ] **Step 6: 提交**

```bash
git add -u frontend/src frontend/e2e-tauri
git commit -m "refactor(le-01): 删除云剪辑凭据界面与 Tauri 服务网关

删除阿里云凭据配置页、服务网关接口与实现、供应商可替换性测试和服务配置
E2E；从 main.tsx 与 WorkbenchShell 摘掉注入点。

工作台本身、video-editing DTO 与网关、production-wiring 的已知失败用例全部
保留，归 LE-17 重写——那条 it.fails 断言的正是「生产组合根拿到的是
sessionStorage 草稿网关」，是接线成功与否的验收信号。"
```

---

## Task 5: 删除 Tauri 命令与凭据存储

**Files:**
- Delete: `frontend/src-tauri/src/video_editing_service_settings.rs`、`frontend/src-tauri/tests/video_editing_service_settings.rs`、`frontend/src-tauri/tests/video_editing_service_settings_real.rs`
- Modify: `frontend/src-tauri/src/lib.rs`、`frontend/src-tauri/tests/acceptance_gate_honesty.rs`

**Interfaces:**
- Consumes: Task 4 已删除前端调用方
- Produces: Tauri 不再注册 `get_video_editing_service_settings`、`configure_video_editing_service`、`clear_video_editing_service`、`test_video_editing_service_connection` 四个 command

- [ ] **Step 1: 删除 Rust 文件**

```bash
git rm frontend/src-tauri/src/video_editing_service_settings.rs \
       frontend/src-tauri/tests/video_editing_service_settings.rs \
       frontend/src-tauri/tests/video_editing_service_settings_real.rs
```

- [ ] **Step 2: 从 `lib.rs` 移除模块与命令**

按行号从**后往前**删，避免前面的删除让后面行号偏移：

1. 第 4636-4639 行：4 个 command 名（第三处注册）
2. 第 4538-4541 行：4 个 command 名（第二处注册）
3. 第 4471-4474 行：4 个 command 名（第一处注册）
4. 第 4371 行附近：`video_editing_service_settings::initialize_production_video_editing_service_settings(...)` 调用
5. 第 259-306 行：4 个 `#[tauri::command]` 函数定义
6. 第 35 行：`pub mod video_editing_service_settings;`

- [ ] **Step 3: 检查验收诚实性测试是否引用了已删脚本**

```bash
grep -n "video_editing\|ve_03\|ve_04" frontend/src-tauri/tests/acceptance_gate_honesty.rs
```

若有条目指向 `run_ve_03_acceptance.py` / `run_ve_04_acceptance.py`，一并移除（脚本本身在 Task 6 删除）。

- [ ] **Step 4: 编译并跑 Rust 测试**

```bash
cd frontend/src-tauri && cargo test
```

Expected: 编译通过，全部测试 PASS。若报 `cannot find function` 或 `unresolved import`，说明 Step 2 有遗漏，按报错行补删。

- [ ] **Step 5: 确认正式包不再声明这些命令**

```bash
grep -rn "video_editing_service" frontend/src-tauri/src/ frontend/src-tauri/capabilities/ 2>/dev/null
```

Expected: 无输出

- [ ] **Step 6: 提交**

```bash
git add -u frontend/src-tauri
git commit -m "refactor(le-01): 移除云剪辑凭据的 Tauri 命令与存储

删除 video_editing_service_settings 模块及其两个测试，从 lib.rs 摘掉模块
声明、4 个 command 定义与 3 处注册，以及生产初始化调用。

这些命令存的是阿里云 AK/SK 与地域配置，云剪辑路线取消后没有任何调用方；
本地剪辑不需要任何供应商凭据。"
```

---

## Task 6: 清理契约、验收脚本与台账

**Files:**
- Delete: `contracts/video/aliyun-ims-editing-staging.v1.json`、`scripts/run_ve_03_acceptance.py`、`scripts/run_ve_04_acceptance.py`
- Modify: `scripts/run_cq_04_acceptance.py`、`docs/embedded-browser-video-studio-roadmap.md`
- Create: `scripts/check_local_editing_roadmap_counts.py`、`scripts/test_check_local_editing_roadmap_counts.py`
- Test: `backend/tests/unit/control_plane/test_cloud_editing_removed.py::test_aliyun_editing_contract_file_is_gone`

**Interfaces:**
- Consumes: Task 1～5 的删除成果
- Produces: `check_local_editing_roadmap_counts.py` 守护 `docs/local-video-editing-roadmap.md` 的任务计数，供后续每个 LE 任务改台账后运行

- [ ] **Step 1: 删除契约与 VE 验收脚本**

```bash
git rm contracts/video/aliyun-ims-editing-staging.v1.json \
       scripts/run_ve_03_acceptance.py \
       scripts/run_ve_04_acceptance.py
```

- [ ] **Step 2: 运行后端守卫，确认最后一个用例转绿**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/control_plane/test_cloud_editing_removed.py -v
```

Expected: 全部 13 个用例 PASS

- [ ] **Step 3: 从 CQ-04 验收脚本移除云剪辑环节**

```bash
grep -n "editing\|剪辑" scripts/run_cq_04_acceptance.py
```

把"送入独立阿里云剪辑"相关步骤改为本地剪辑，或在本任务范围内先移除该环节并留注释指向 LE-22。

- [ ] **Step 4: 从专项台账移除 VE 线**

编辑 `docs/embedded-browser-video-studio-roadmap.md`：

1. 删除 `### 9.7 独立视频剪辑（8 项）` 整个小节（含 VE-01～VE-08 八行表格）
2. 在原位置插入一行说明：

```markdown
### 9.7 独立视频剪辑（已废弃）

VE 线整条废弃，由本地 FFmpeg 剪辑取代，见 `docs/local-video-editing-roadmap.md`。
废弃原因：领域层实现完整但从未装配到产品路径——Control Plane 无剪辑 REST
路由、前端生产组合根注入 sessionStorage 草稿网关、提交固定抛错。证据与实测
数据见 `docs/superpowers/specs/2026-07-28-local-smart-edit-design.md` §1。
```

3. 更新总任务数（原 87 减去 8 项 VE）与各状态计数
4. 移除 `4.5 独立视频剪辑控制面` 中描述 `VideoEditingProvider` Registry 的内容，改为指向新台账
5. 检查 `CQ-01`、`CQ-03`、`CQ-04` 三行中提到"独立视频剪辑"的依赖与描述，把 `VE-08` 依赖改为指向新台账

- [ ] **Step 5: 写计数守卫脚本的测试**

创建 `scripts/test_check_local_editing_roadmap_counts.py`：

```python
"""The counts guard must fail when the ledger's numbers drift."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "check_local_editing_roadmap_counts.py"
_LEDGER = (
    Path(__file__).resolve().parents[1] / "docs/local-video-editing-roadmap.md"
)


def _run(ledger_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--ledger", str(ledger_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_real_ledger_passes() -> None:
    result = _run(_LEDGER)
    assert result.returncode == 0, result.stdout + result.stderr


def test_wrong_total_fails(tmp_path: Path) -> None:
    broken = tmp_path / "roadmap.md"
    broken.write_text(
        _LEDGER.read_text(encoding="utf-8").replace("任务总数：23", "任务总数：99"),
        encoding="utf-8",
    )
    result = _run(broken)
    assert result.returncode != 0
    assert "任务总数" in result.stdout + result.stderr


def test_section_count_mismatch_fails(tmp_path: Path) -> None:
    broken = tmp_path / "roadmap.md"
    broken.write_text(
        _LEDGER.read_text(encoding="utf-8").replace(
            "### 3.4 本地渲染引擎（6 项）", "### 3.4 本地渲染引擎（9 项）"
        ),
        encoding="utf-8",
    )
    result = _run(broken)
    assert result.returncode != 0
    assert "3.4" in result.stdout + result.stderr
```

- [ ] **Step 6: 运行测试确认失败**

```bash
cd /Users/aventador/sourceCode/automation-tool/wt/smart-edit
backend/.venv/bin/python -m pytest scripts/test_check_local_editing_roadmap_counts.py -v
```

Expected: FAIL，`check_local_editing_roadmap_counts.py` 不存在

- [ ] **Step 7: 写计数守卫脚本**

创建 `scripts/check_local_editing_roadmap_counts.py`：

```python
#!/usr/bin/env python3
"""Keep the local-editing ledger's counts honest.

A ledger exists to track progress, so a number that drifts from the table it
summarises is worse than no number at all. This checks three things agree:
each section heading's own count, the table rows under it, and the totals in
the progress section.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "docs/local-video-editing-roadmap.md"

_SECTION = re.compile(r"^### (\d+\.\d+) .*?（(\d+) 项）$", re.MULTILINE)
_TASK_ROW = re.compile(r"^\| (LE-\d+) \|", re.MULTILINE)
_TOTAL = re.compile(r"^- 任务总数：(\d+)$", re.MULTILINE)
_NOT_STARTED = re.compile(r"^- ⬜ 未开始：(\d+)$", re.MULTILINE)
_DONE = re.compile(r"^- ✅ 已完成：(\d+)$", re.MULTILINE)
_PENDING_ACCEPT = re.compile(r"^- 🔍 待验收：(\d+)$", re.MULTILINE)
_IN_FLIGHT = re.compile(r"^- 🧪 RED / 🚧 实现中：(\d+)$", re.MULTILINE)


def _fail(message: str) -> None:
    print(f"FAIL: {message}")


def check(text: str) -> list[str]:
    problems: list[str] = []

    sections = _SECTION.findall(text)
    declared_by_section = sum(int(count) for _, count in sections)

    blocks = re.split(r"^### ", text, flags=re.MULTILINE)[1:]
    for block in blocks:
        heading_match = re.match(r"(\d+\.\d+) .*?（(\d+) 项）", block)
        if heading_match is None:
            continue
        section_id, declared = heading_match.group(1), int(heading_match.group(2))
        actual = len(_TASK_ROW.findall(block))
        if actual != declared:
            problems.append(
                f"小节 {section_id} 标题写 {declared} 项，表里实际 {actual} 行"
            )

    total_match = _TOTAL.search(text)
    if total_match is None:
        problems.append("找不到「任务总数」")
        return problems
    declared_total = int(total_match.group(1))

    all_rows = len(_TASK_ROW.findall(text))
    if declared_total != all_rows:
        problems.append(f"任务总数写 {declared_total}，全文实际 {all_rows} 行")
    if declared_total != declared_by_section:
        problems.append(
            f"任务总数写 {declared_total}，各小节标题相加为 {declared_by_section}"
        )

    status_total = 0
    for pattern, label in (
        (_DONE, "已完成"),
        (_PENDING_ACCEPT, "待验收"),
        (_IN_FLIGHT, "RED/实现中"),
        (_NOT_STARTED, "未开始"),
    ):
        match = pattern.search(text)
        if match is None:
            problems.append(f"找不到「{label}」计数")
            return problems
        status_total += int(match.group(1))
    if status_total != declared_total:
        problems.append(f"各状态相加为 {status_total}，与任务总数 {declared_total} 不符")

    in_flight_match = _IN_FLIGHT.search(text)
    if in_flight_match is not None and int(in_flight_match.group(1)) > 1:
        problems.append("同一时间最多一个任务处于 RED 或实现中")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=_DEFAULT_LEDGER)
    arguments = parser.parse_args()

    text = arguments.ledger.read_text(encoding="utf-8")
    problems = check(text)
    for problem in problems:
        _fail(problem)
    if problems:
        return 1
    print("local editing roadmap counts are consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: 运行测试确认通过**

```bash
backend/.venv/bin/python -m pytest scripts/test_check_local_editing_roadmap_counts.py -v
python3 scripts/check_local_editing_roadmap_counts.py
```

Expected: 3 个测试 PASS；脚本直接运行输出 `local editing roadmap counts are consistent`，退出码 0

- [ ] **Step 9: 更新本地剪辑台账的 LE-01 状态**

编辑 `docs/local-video-editing-roadmap.md`：

1. LE-01 行状态从 `⬜ 未开始` 改为 `✅ 已完成`
2. 进度区：`✅ 已完成：0` → `1`，`⬜ 未开始：23` → `22`
3. §5 当前下一步改为 LE-02

- [ ] **Step 10: 写 LE-01 证据文件**

创建 `docs/development/LE-01.md`，按项目规范（`CLAUDE.md §9.1`）开头声明：

```markdown
# LE-01 删除云剪辑路线

> 用户可操作：否
> 证据类型：分层实现
> 日期：2026-07-28
```

正文记录 RED（守卫测试失败输出）、GREEN（各测试命令与通过计数）、删除清单与行数、真实边界（alembic 链为何不删迁移文件）、清理、文档变化。

- [ ] **Step 11: 跑全量门禁**

```bash
cd backend && .venv/bin/python -m pytest tests/unit -q
pnpm --dir frontend exec vitest run
pnpm --dir frontend typecheck && pnpm --dir frontend lint
cd frontend/src-tauri && cargo test
python3 scripts/check_third_party_sources.py
python3 scripts/check_user_facing_branding.py
python3 scripts/check_local_editing_roadmap_counts.py
python3 scripts/check_embedded_browser_video_roadmap.py
```

Expected: 全部退出 0。`check_embedded_browser_video_roadmap.py` 若因 VE 行移除而报计数不符，按其提示同步修正原台账计数。

- [ ] **Step 12: 提交**

```bash
git add -u contracts scripts docs
git add scripts/check_local_editing_roadmap_counts.py \
        scripts/test_check_local_editing_roadmap_counts.py \
        docs/development/LE-01.md
git commit -m "chore(le-01): 清理云剪辑契约与验收脚本，VE 线从专项台账下线

删除阿里云暂存计费契约与 VE-03、VE-04 验收脚本；专项台账 9.7 小节改为
废弃说明并指向新台账，同步修正总数与状态计数。

新增 check_local_editing_roadmap_counts.py 守护本地剪辑台账：小节标题计数、
表格行数、进度区各状态相加三者必须一致，且同时最多一个任务在 RED 或实现中。
台账的用途是跟踪进度，数字对不上就失去意义，所以由脚本守而不是靠人记。

LE-01 完成，下一步 LE-02 素材库领域对象。"
```

---

## Self-Review

**1. 规格覆盖**

对照台账 LE-01 的交付项逐条核对：

| LE-01 交付项 | 覆盖任务 |
| --- | --- |
| 删除阿里云 IMS 全部生产代码与测试 | Task 2 |
| 删除供应商无关 Provider 抽象层 | Task 2 |
| 2 个迁移 | Task 3（**修正为新增 drop 迁移，不删文件**） |
| 删除 `aliyun-ims-editing-staging.v1.json` | Task 6 |
| 前端剪辑服务设置页与网关 | Task 4 |
| Tauri 4 个 service command | Task 5 |
| 从专项台账移除 VE-01～VE-08 并修正计数 | Task 6 Step 4 |
| 新建 `check_local_editing_roadmap_counts.py` | Task 6 Step 5-8 |
| 保留 `e2e-tauri/video-editing.spec.ts` | Task 1 守卫的 RETAINED_FILES 断言 |
| 全量测试与门禁脚本通过 | Task 6 Step 11 |

无遗漏。

**2. 占位符扫描**

无 TBD/TODO。Task 3 Step 2 与 Task 4 Step 3 描述的是删除操作而非新增代码，故给出精确行号与保留清单而非代码块；Task 6 Step 3 的 CQ-04 脚本改动给了两种可选处理并指明归属任务。

**3. 类型一致性**

守卫测试中的模块名与 Task 2 的 `git rm` 清单逐一对应；前端守卫的 `REMOVED_FILES` 与 Task 4 的 `git rm` 清单逐一对应；`RETAINED_FILES` 与"明确保留"清单一致。drop 迁移的 `down_revision = "20260723_0034"` 与实测链尾一致。

**4. 计划外发现**

- `frontend/src/app/production-wiring.test.ts` 已用 `it.fails` 记录了"生产组合根拿到 sessionStorage 草稿网关"这一缺口，本计划将其列入必须保留，并作为 LE-17 的验收信号
- 设计文档 §7.3 原写"删除 2 个迁移"，经核实 alembic 链后修正为新增 drop 迁移，需同步更新设计文档与台账措辞
