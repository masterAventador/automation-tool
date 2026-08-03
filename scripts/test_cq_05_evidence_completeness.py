#!/usr/bin/env python3
"""CQ-05 确定性测试：87 行任务与证据文件必须对得上。

CQ-05 的完成条件里写着"Roadmap 状态与 87 个独立证据文件一致后才能完成"。

「一致」有两个方向，缺一不可：

- **每个已激活的任务都要有证据文件**——AV-04 的门禁查过这一条；
- **每个证据文件都要对应一个真实存在的任务**——这一条没人查。一个任务被改名、
  拆分或撤销后，它的证据文件会留在原地，读的人以为那还是一项在做的工作。

未激活的任务（`⬜ 未开始`、`⏸ 后置`）没有证据文件是正常的：它们还没开工。
把这种情况判成缺失，会逼人为没做的事先写一个空台账。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cq_05_evidence_completeness import (  # noqa: E402
    ArchitectureCompletenessRejected,
    EvidenceCompletenessRejected,
    require_specialized_architecture,
    require_evidence_matches_roadmap,
)

ROADMAP = """# 专项

| ID | 任务 | 依赖 | 当前状态 |
| --- | --- | --- | --- |
| AA-01 | 甲 | — | ✅ 已完成 |
| AA-02 | 乙 | AA-01 | 🔍 待验收 |
| AA-03 | 丙 | AA-02 | ⬜ 未开始 |
| AA-04 | 丁 | — | ⏸ 后置 |
"""


class EvidenceCompletenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="cq05-evidence-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.roadmap = self.base / "roadmap.md"
        self.roadmap.write_text(ROADMAP, encoding="utf-8")
        self.evidence = self.base / "development"
        self.evidence.mkdir()

    def write_evidence(self, *task_ids: str) -> None:
        for task_id in task_ids:
            (self.evidence / f"{task_id}.md").write_text(f"# {task_id}\n", encoding="utf-8")

    def test_activated_tasks_with_evidence_pass(self) -> None:
        self.write_evidence("AA-01", "AA-02")
        require_evidence_matches_roadmap(self.roadmap, self.evidence)

    def test_an_activated_task_without_evidence_is_refused(self) -> None:
        self.write_evidence("AA-01")
        with self.assertRaises(EvidenceCompletenessRejected):
            require_evidence_matches_roadmap(self.roadmap, self.evidence)

    def test_an_unstarted_task_needs_no_evidence(self) -> None:
        # 没开工的任务不该被逼着先写一个空台账。
        self.write_evidence("AA-01", "AA-02")
        self.assertFalse((self.evidence / "AA-03.md").exists())
        require_evidence_matches_roadmap(self.roadmap, self.evidence)

    def test_an_orphan_evidence_file_is_refused(self) -> None:
        # 任务被改名或撤销后，它的证据文件会留在原地，读的人以为那还是在做的工作。
        self.write_evidence("AA-01", "AA-02", "AA-99")
        with self.assertRaises(EvidenceCompletenessRejected):
            require_evidence_matches_roadmap(self.roadmap, self.evidence)

    def test_non_task_documents_are_not_orphans(self) -> None:
        # FIX-*/NOTE-* 这类文件不是任务台账，不参与配对。
        self.write_evidence("AA-01", "AA-02")
        (self.evidence / "FIX-something.md").write_text("# 修复", encoding="utf-8")
        (self.evidence / "NOTE-something.md").write_text("# 记录", encoding="utf-8")
        (self.evidence / "windows-evidence-checklist.md").write_text("# 清单", encoding="utf-8")
        require_evidence_matches_roadmap(self.roadmap, self.evidence)

    def test_another_roadmaps_task_family_is_outside_this_check(self) -> None:
        # docs/development 是共享目录；PC/UI 等任务由自己的 Roadmap 管，不能被本专项误报。
        self.write_evidence("AA-01", "AA-02", "PC-01", "UI-01")
        require_evidence_matches_roadmap(self.roadmap, self.evidence)

    def test_an_empty_roadmap_is_refused(self) -> None:
        # 扫不到任何任务时必须拒绝：那时这次检查什么都没证明。
        empty = self.base / "empty.md"
        empty.write_text("# 没有任务表\n", encoding="utf-8")
        with self.assertRaises(EvidenceCompletenessRejected):
            require_evidence_matches_roadmap(empty, self.evidence)


class ArchitectureCompletenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="cq05-architecture-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.frontend = self.base / "frontend.md"
        self.backend = self.base / "backend.md"

    def write_complete_architecture(self) -> None:
        self.frontend.write_text(
            """# 前端架构
### 6.6 内置浏览器、Browser Use 与页面租约
Tauri/Rust 验证资源；BrowserSurfaceLease 约束 CDP 接管；React 看不到 Profile。
### 6.7 两种视频制作链路
TauriMaterialVideoStudioGateway 调用 LocalVideoOrchestrator，以 RenderJob 导入 Artifact。
""",
            encoding="utf-8",
        )
        self.backend.write_text(
            """# 后端架构
### 8.4 Browser Use 受控执行边界
browser_use 使用 temporary Profile，operations 只能在 BrowserSurfaceLease 下经 CDP 交接。
### 17.1 两种视频制作执行链
material_montage_v1 与 motion_composition_v1 经 LocalVideoOrchestrator 写入
VideoJobWorkspaceStore。
""",
            encoding="utf-8",
        )

    def test_all_four_architecture_sections_pass(self) -> None:
        self.write_complete_architecture()
        require_specialized_architecture(self.frontend, self.backend)

    def test_a_missing_section_is_refused(self) -> None:
        self.write_complete_architecture()
        self.frontend.write_text("# 前端架构\n", encoding="utf-8")
        with self.assertRaises(ArchitectureCompletenessRejected):
            require_specialized_architecture(self.frontend, self.backend)

    def test_a_heading_without_required_architecture_facts_is_refused(self) -> None:
        self.write_complete_architecture()
        self.backend.write_text(
            """# 后端架构
### 8.4 Browser Use 受控执行边界
以后再写。
### 17.1 两种视频制作执行链
以后再写。
""",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ArchitectureCompletenessRejected, "BrowserSurfaceLease"
        ):
            require_specialized_architecture(self.frontend, self.backend)

    def test_repository_architecture_covers_the_specialized_runtime(self) -> None:
        require_specialized_architecture(
            ROOT / "docs/frontend-architecture.md",
            ROOT / "docs/backend-architecture.md",
        )


if __name__ == "__main__":
    unittest.main()
