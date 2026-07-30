#!/usr/bin/env python3
"""Guards that a completed user-facing task carries a terminal-state anchor.

The defect this closes has no code signature, which is why nothing caught it:
an acceptance can stop at "the window opens and the button is clickable" and
read as finished. T108 is the worked example — it proved the material-video
window was interactive (`driverActive: false`, `pointerEvents: auto`) through a
real Tauri window, and stopped there. Whether a video could actually be
produced was never tested, so the ledger said done while the capability was
unknown. The reporter of that defect was the product owner, opening the packaged
App and finding it unusable.

The rule cannot be "search for weak words": rewording "clicked successfully"
into "element present with correct state" would pass while proving no more.
What separates a real acceptance is that it names something a reader can go and
check independently — a file with a size or digest, an `ffprobe` reading, a row
in a database, an id the platform handed back. Those cannot be produced without
having actually run the thing.

Layered work legitimately has no such artefact: a Page Object, an audit, a
failure-matrix table. Those declare themselves as such rather than being
guessed at, because guessing from the title is how a gate starts producing
noise and gets switched off.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_acceptance_evidence_depth import (  # noqa: E402
    EvidenceProblem,
    audit_repository,
    audit_text,
    load_contract,
)

_ANCHORED = """# T999 一句话出片

用户可操作：是

## 验收

真实 App 走用户路径出片，`ffprobe` 读产物：h264 1280×720 / 30fps / 90 帧。
"""

_UNANCHORED = """# T999 一句话出片

用户可操作：是

## 验收

打开窗口，元素可见，按钮可点击，接口返回 200，测试 passed。
"""

_LAYERED = """# A7-08 抖音评论 Page Object

用户可操作：否
证据类型：分层实现

## 说明

只提供页面定位，不产生外部副作用。
"""


class EvidenceDepthTests(unittest.TestCase):
    def test_a_user_facing_evidence_without_a_terminal_anchor_is_rejected(self) -> None:
        problems = audit_text("T999.md", _UNANCHORED)
        self.assertTrue(problems)
        self.assertEqual(problems[0].kind, EvidenceProblem.NO_TERMINAL_ANCHOR)

    def test_a_user_facing_evidence_with_a_terminal_anchor_passes(self) -> None:
        self.assertEqual(audit_text("T999.md", _ANCHORED), [])

    def test_layered_work_passes_when_it_declares_what_it_is(self) -> None:
        self.assertEqual(audit_text("A7-08.md", _LAYERED), [])

    def test_an_undeclared_evidence_file_is_rejected(self) -> None:
        problems = audit_text("T999.md", "# T999\n\n## 验收\n\n跑通了。\n")
        self.assertTrue(problems)
        self.assertEqual(problems[0].kind, EvidenceProblem.NO_DECLARATION)

    def test_non_user_facing_work_must_state_its_kind(self) -> None:
        problems = audit_text("T999.md", "# T999\n\n用户可操作：否\n\n没说是哪一类。\n")
        self.assertTrue(problems)
        self.assertEqual(problems[0].kind, EvidenceProblem.NO_KIND)

    def test_the_weak_words_alone_never_count_as_an_anchor(self) -> None:
        """The point of the gate: rewording must not be a way through it."""
        for weak in (
            "元素可见且状态正确。",
            "按钮可点击，交互正常。",
            "接口返回 200，响应结构符合预期。",
            "所有单元测试 passed。",
        ):
            with self.subTest(weak=weak):
                problems = audit_text("T999.md", f"# T\n\n用户可操作：是\n\n{weak}\n")
                self.assertTrue(problems, f"{weak!r} was accepted as a terminal anchor")

    def test_the_exemption_list_may_not_name_a_file_that_is_gone(self) -> None:
        """A stale exemption is how a list becomes a blanket permission."""
        contract = load_contract()
        missing = [
            name
            for name in contract["exemptions"]
            if not (ROOT / "docs/development" / name).is_file()
        ]
        self.assertEqual(missing, [], "exempted evidence files no longer exist")

    def test_exempted_files_are_skipped_but_new_ones_are_not(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "OLD.md").write_text("# OLD\n\n没有声明。\n", encoding="utf-8")
            (root / "NEW.md").write_text("# NEW\n\n没有声明。\n", encoding="utf-8")
            problems = audit_repository(root, exemptions={"OLD.md"})
            self.assertEqual([problem.path for problem in problems], ["NEW.md"])

    def test_the_repository_itself_passes_this_gate(self) -> None:
        problems = audit_repository(
            ROOT / "docs/development", exemptions=set(load_contract()["exemptions"])
        )
        self.assertEqual(
            [f"{problem.path}: {problem.kind}" for problem in problems],
            [],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
