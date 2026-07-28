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
