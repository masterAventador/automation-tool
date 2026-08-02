"""The counts guard must fail when the ledger's numbers drift."""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "check_local_editing_roadmap_counts.py"
_LEDGER = Path(__file__).resolve().parents[1] / "docs/local-video-editing-roadmap.md"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_local_editing_roadmap_counts import (  # noqa: E402
    MAX_CONCURRENT_WORK_LINES,
    _TASK_ROW_CELLS,
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
_PENDING_ACCEPT_COUNT = re.compile(r"^- 🔍 待验收：(\d+)$", re.MULTILINE)
_MUTABLE_TASK_ROW = re.compile(
    r"^(?P<prefix>\| LE-\d+ \|.*\| )"
    r"(?P<status>⬜ 未开始|🔍 待验收|✅ 已完成)"
    r"(?P<suffix> \|)$",
    re.MULTILINE,
)
_TASK_ROW = re.compile(r"^\| LE-\d+ \|.*\|$", re.MULTILINE)
_DECLARED_STATUS = {
    "⬜ 未开始": (_NOT_STARTED_COUNT, "- ⬜ 未开始："),
    "🔍 待验收": (_PENDING_ACCEPT_COUNT, "- 🔍 待验收："),
    "✅ 已完成": (_DONE_COUNT, "- ✅ 已完成："),
}


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
    """声明的四个状态计数加起来必须等于任务总数。

    待验收计数从活台账里读出来再 +5，不写死——这条曾硬编码替换「待验收：0」，
    LE-09 收口把真值改成 1 之后替换成了 no-op，夹具与真台账相同、测试反而红。
    正是本文件头注释警告的那种时效腐烂，只是当时漏改了这一条。
    """
    broken = tmp_path / "roadmap.md"
    text = _LEDGER.read_text(encoding="utf-8")
    pending_match = _PENDING_ACCEPT_COUNT.search(text)
    assert pending_match is not None
    inflated = int(pending_match.group(1)) + 5
    broken.write_text(
        _PENDING_ACCEPT_COUNT.sub(f"- 🔍 待验收：{inflated}", text),
        encoding="utf-8",
    )
    result = _run(broken)
    assert result.returncode != 0
    assert "各状态相加" in result.stdout + result.stderr


def _with_in_flight(text: str, wanted: int) -> str:
    """Return the ledger with exactly `wanted` rows in flight, counts kept consistent.

    The progress counts are adjusted alongside the rows so that the "sums do not
    add up" and "cross-check" rules stay satisfied and only the concurrency rule
    can fire.
    """
    in_flight_match = _IN_FLIGHT_COUNT.search(text)
    assert in_flight_match is not None
    added = wanted - int(in_flight_match.group(1))
    assert added >= 0

    candidates = list(_MUTABLE_TASK_ROW.finditer(text))
    assert len(candidates) >= added, "需要足够多的非在途任务行才能构造本场景"
    changed = Counter(match.group("status") for match in candidates[:added])
    for match in candidates[:added]:
        replacement = match.group("prefix") + "🚧 实现中" + match.group("suffix")
        text = text.replace(match.group(0), replacement, 1)

    for status, removed in changed.items():
        pattern, prefix = _DECLARED_STATUS[status]
        count_match = pattern.search(text)
        assert count_match is not None
        text = pattern.sub(f"{prefix}{int(count_match.group(1)) - removed}", text)

    return _IN_FLIGHT_COUNT.sub(f"- 🧪 RED / 🚧 实现中：{wanted}", text)


def test_one_in_flight_per_work_line_passes(tmp_path: Path) -> None:
    """每条工作线各带一个在途任务是台账明文授权的，不该被判红。"""
    ledger = tmp_path / "roadmap.md"
    ledger.write_text(
        _with_in_flight(_LEDGER.read_text(encoding="utf-8"), MAX_CONCURRENT_WORK_LINES),
        encoding="utf-8",
    )
    result = _run(ledger)
    assert result.returncode == 0, result.stdout + result.stderr


def test_more_in_flight_than_work_lines_fails(tmp_path: Path) -> None:
    """超过授权的并行线数就没有工作线认领它，必须判红。"""
    ledger = tmp_path / "roadmap.md"
    ledger.write_text(
        _with_in_flight(_LEDGER.read_text(encoding="utf-8"), MAX_CONCURRENT_WORK_LINES + 1),
        encoding="utf-8",
    )
    result = _run(ledger)
    assert result.returncode != 0
    assert "并行工作线" in result.stdout + result.stderr


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


def test_a_task_row_that_lost_its_dependency_cell_fails(tmp_path: Path) -> None:
    """删掉「依赖」格，每一个计数都还是对的——所以计数守不住这件事。

    `_iter_task_rows` 取 `fields[-1]`，少一格时「当前状态」正好滑进「依赖」
    的位置，状态照样读得到，小节计数、总数、状态分布全部依旧一致。这不是
    假想：`d1dcd8b` 就是这样把 LE-05、LE-09、LE-10 三行的依赖整格删掉的，
    而门禁全绿。台账是「任务定义、**依赖**和当前下一步的唯一事实源」，
    当时 LE-05 恰恰是「当前下一步」，它的依赖格是空的。
    """
    text = _LEDGER.read_text(encoding="utf-8")
    row = next(
        candidate.group(0)
        for candidate in _TASK_ROW.finditer(text)
        if len(candidate.group(0).strip("|").split("|")) == _TASK_ROW_CELLS
    )
    cells = row.strip("|").split("|")
    maimed = "|" + "|".join(cells[:-2] + cells[-1:]) + "|"

    broken = tmp_path / "roadmap.md"
    broken.write_text(text.replace(row, maimed), encoding="utf-8")

    result = _run(broken)
    assert result.returncode != 0
    assert "格" in result.stdout + result.stderr


def test_an_unescaped_pipe_inside_a_cell_fails(tmp_path: Path) -> None:
    """裸竖线在 GFM 里照样切格，代码跨度不豁免——所以它也是列数缺陷。

    与上一条同一个守卫的另一侧：`| LE-03 | ... `int | None` ... |` 渲染出来
    是六列，多出来的那格是文字被劈开的碎片。多一列和少一列都说明这一行的
    格子边界不是作者以为的那样。
    """
    text = _LEDGER.read_text(encoding="utf-8")
    row = next(
        candidate.group(0)
        for candidate in _TASK_ROW.finditer(text)
        if len(candidate.group(0).strip("|").split("|")) == _TASK_ROW_CELLS
    )
    cells = row.strip("|").split("|")
    split_open = "|" + "|".join([*cells[:1], "`int | None`", *cells[1:]]) + "|"

    broken = tmp_path / "roadmap.md"
    broken.write_text(text.replace(row, split_open), encoding="utf-8")

    result = _run(broken)
    assert result.returncode != 0
    assert "格" in result.stdout + result.stderr
