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
from collections import Counter
from pathlib import Path

_DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "docs/local-video-editing-roadmap.md"

_SECTION = re.compile(r"^### (\d+\.\d+) .*?（(\d+) 项）$", re.MULTILINE)
_TASK_ROW = re.compile(r"^\| (LE-\d+) \|", re.MULTILINE)
_TOTAL = re.compile(r"^- 任务总数：(\d+)$", re.MULTILINE)
_NOT_STARTED = re.compile(r"^- ⬜ 未开始：(\d+)$", re.MULTILINE)
_DONE = re.compile(r"^- ✅ 已完成：(\d+)$", re.MULTILINE)
_PENDING_ACCEPT = re.compile(r"^- 🔍 待验收：(\d+)$", re.MULTILINE)
_IN_FLIGHT = re.compile(r"^- 🧪 RED / 🚧 实现中：(\d+)$", re.MULTILINE)

# 表格「当前状态」列里出现的字面值。RED 与实现中在表格里是两个不同状态，但
# 进度区把它们合并声明成同一个「🧪 RED / 🚧 实现中」桶。
_STATUS_NOT_STARTED = "⬜ 未开始"
_STATUS_RED = "🧪 RED"
_STATUS_IN_PROGRESS = "🚧 实现中"
_STATUS_PENDING_ACCEPT = "🔍 待验收"
_STATUS_DONE = "✅ 已完成"
_KNOWN_ROW_STATUSES = {
    _STATUS_NOT_STARTED,
    _STATUS_RED,
    _STATUS_IN_PROGRESS,
    _STATUS_PENDING_ACCEPT,
    _STATUS_DONE,
}


def _fail(message: str) -> None:
    print(f"FAIL: {message}")


def _iter_task_rows(text: str) -> list[tuple[str, str]]:
    """Return (task_id, 当前状态) for every task row line in the ledger."""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = _TASK_ROW.match(line)
        if match is None:
            continue
        fields = [field.strip() for field in line.strip().split("|")[1:-1]]
        rows.append((match.group(1), fields[-1]))
    return rows


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

    task_rows = _iter_task_rows(text)
    all_rows = len(task_rows)
    if declared_total != all_rows:
        problems.append(f"任务总数写 {declared_total}，全文实际 {all_rows} 行")
    if declared_total != declared_by_section:
        problems.append(
            f"任务总数写 {declared_total}，各小节标题相加为 {declared_by_section}"
        )

    declared_counts: dict[str, int] = {}
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
        declared_counts[label] = int(match.group(1))
        status_total += declared_counts[label]
    if status_total != declared_total:
        problems.append(f"各状态相加为 {status_total}，与任务总数 {declared_total} 不符")

    # 与 check_embedded_browser_video_roadmap.py::validate_summary() 同一思路：
    # 用 Counter 统计表格里每行真实的「当前状态」，逐项核对进度区声明的数字是否
    # 真的匹配表格内容——只检查总数相加对不上远远不够，声明的数字本身可能和
    # 表格完全脱节（真实事故：VE 线 8 行全标 ✅ 已完成，但产品路径从未装配）。
    unknown_statuses = sorted(
        {status for _, status in task_rows} - _KNOWN_ROW_STATUSES
    )
    if unknown_statuses:
        problems.append(f"表格里出现未知状态：{unknown_statuses}")

    actual_status_counter = Counter(status for _, status in task_rows)
    actual_counts = {
        "已完成": actual_status_counter[_STATUS_DONE],
        "待验收": actual_status_counter[_STATUS_PENDING_ACCEPT],
        "RED/实现中": (
            actual_status_counter[_STATUS_RED] + actual_status_counter[_STATUS_IN_PROGRESS]
        ),
        "未开始": actual_status_counter[_STATUS_NOT_STARTED],
    }
    for label, declared_count in declared_counts.items():
        actual_count = actual_counts[label]
        if declared_count != actual_count:
            problems.append(
                f"「{label}」声明 {declared_count}，表格里实际 {actual_count} 行"
            )

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
