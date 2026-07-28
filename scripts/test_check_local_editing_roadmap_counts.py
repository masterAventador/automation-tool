"""The counts guard must fail when the ledger's numbers drift."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "check_local_editing_roadmap_counts.py"
_LEDGER = (
    Path(__file__).resolve().parents[1] / "docs/local-video-editing-roadmap.md"
)

# These read the ledger's *current* counts instead of a hardcoded snapshot: a
# literal like "- ✅ 已完成：1" goes stale the next time a task finishes, and a
# stale literal that no longer matches silently turns `.replace()` into a
# no-op — the "broken" fixture ends up identical to the real, valid ledger,
# and the test that meant to prove the checker rejects a mismatch instead
# proves nothing. Deriving from the live value keeps these tests meaningful
# as the ledger's own progress moves forward.
_DONE_COUNT = re.compile(r"^- ✅ 已完成：(\d+)$", re.MULTILINE)
_NOT_STARTED_COUNT = re.compile(r"^- ⬜ 未开始：(\d+)$", re.MULTILINE)
_IN_FLIGHT_COUNT = re.compile(r"^- 🧪 RED / 🚧 实现中：(\d+)$", re.MULTILINE)
_NOT_STARTED_ROW = re.compile(r"^\| LE-\d+ \|.*\| ⬜ 未开始 \|$", re.MULTILINE)


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
        _LEDGER.read_text(encoding="utf-8").replace("任务总数：24", "任务总数：99"),
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


def test_status_sum_mismatch_fails(tmp_path: Path) -> None:
    """声明的四个状态计数加起来必须等于任务总数。"""
    broken = tmp_path / "roadmap.md"
    broken.write_text(
        _LEDGER.read_text(encoding="utf-8").replace(
            "- 🔍 待验收：0", "- 🔍 待验收：5"
        ),
        encoding="utf-8",
    )
    result = _run(broken)
    assert result.returncode != 0
    assert "各状态相加" in result.stdout + result.stderr


def test_two_in_flight_fails(tmp_path: Path) -> None:
    """同一时间最多一个任务处于 RED 或实现中。"""
    text = _LEDGER.read_text(encoding="utf-8")

    not_started_rows = _NOT_STARTED_ROW.findall(text)
    assert len(not_started_rows) >= 2, "需要至少两行「⬜ 未开始」才能构造本场景"
    for row in not_started_rows[:2]:
        text = text.replace(row, row[: -len("⬜ 未开始 |")] + "🚧 实现中 |", 1)

    in_flight_match = _IN_FLIGHT_COUNT.search(text)
    not_started_match = _NOT_STARTED_COUNT.search(text)
    assert in_flight_match is not None
    assert not_started_match is not None
    # 同步声明的进度计数，让「相加对不上」与「跨表核对」两类检查都保持通过，
    # 只让「同一时间最多一个任务处于 RED/实现中」这一条单独触发。
    text = _IN_FLIGHT_COUNT.sub(
        f"- 🧪 RED / 🚧 实现中：{int(in_flight_match.group(1)) + 2}", text
    )
    text = _NOT_STARTED_COUNT.sub(
        f"- ⬜ 未开始：{int(not_started_match.group(1)) - 2}", text
    )

    broken = tmp_path / "roadmap.md"
    broken.write_text(text, encoding="utf-8")
    result = _run(broken)
    assert result.returncode != 0
    assert "同一时间最多一个任务" in result.stdout + result.stderr


def test_status_cross_check_mismatch_fails(tmp_path: Path) -> None:
    """声明的状态计数即使总和对了，也必须真的匹配表格里每行的当前状态。

    回归用例：这正是复现过的真实事故——VE 线整条 8 行都标 ✅ 已完成，而
    产品路径其实没装配。只检查总和相加对不上远远不够，声明的数字本身可能和
    表格内容完全脱节。
    """
    broken = tmp_path / "roadmap.md"
    text = _LEDGER.read_text(encoding="utf-8")
    assert _DONE_COUNT.search(text) is not None
    assert _NOT_STARTED_COUNT.search(text) is not None
    text = _DONE_COUNT.sub("- ✅ 已完成：24", text)
    text = _NOT_STARTED_COUNT.sub("- ⬜ 未开始：0", text)
    broken.write_text(text, encoding="utf-8")
    result = _run(broken)
    assert result.returncode != 0
    assert "已完成" in result.stdout + result.stderr
