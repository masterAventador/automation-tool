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
    EvidenceCompletenessRejected,
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

    def test_an_empty_roadmap_is_refused(self) -> None:
        # 扫不到任何任务时必须拒绝：那时这次检查什么都没证明。
        empty = self.base / "empty.md"
        empty.write_text("# 没有任务表\n", encoding="utf-8")
        with self.assertRaises(EvidenceCompletenessRejected):
            require_evidence_matches_roadmap(empty, self.evidence)


if __name__ == "__main__":
    unittest.main()
