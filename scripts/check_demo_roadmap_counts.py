#!/usr/bin/env python3
"""The demo ledger must be countable. Twice it was not, and nothing complained.

`docs/demo-sprint-roadmap.md` is the single source of truth for the demo sprint.
Twice its numbers drifted and were reported to a human as fact:

- once because a task id appeared in two status tables, so every count that
  walked the tables double-counted it;
- once because the section headers carried their own parenthetical counts
  (`### ✅ 视频与内容（10）`) while the section actually held twelve rows. The
  same number lived in two places and only one of them was maintained.

Both drifts were silent. Nothing in the repository could tell the difference
between a ledger that added up and one that did not, so the error surfaced only
when a human asked "how many are left" and got a wrong answer with confidence.

This gate makes the ledger self-checking. It deliberately does NOT match
overview rows to sections by name — the row labels and the header text differ
on purpose (a row reads `T73～T86（T10 那轮挖出的新任务）`, its header reads
`### T10 跑全量与并行验收挖出的新任务（T73～T86）`), and a fuzzy name match
would be one more thing to maintain wrongly. Instead it checks the properties
that hold regardless of naming:

1. every task id appears in exactly one status table;
2. the declared total equals the number of distinct task ids;
3. the declared subtotals sum to the declared total;
4. no section header carries a count of its own — the overview table is the
   only place a count is allowed to live.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPOSITORY_ROOT / "docs/demo-sprint-roadmap.md"

TASK_ROW = re.compile(r"\|\s*\*{0,2}(T\d+[a-z]?)\*{0,2}\s*\|")
HEADING = re.compile(r"^#{2,3}\s+(.*)$")
OVERVIEW_ROW = re.compile(r"\|\s*\*{0,2}(.+?)\*{0,2}\s*\|\s*\*{0,2}(\d+)\*{0,2}\s*\|")
# A count a header must not carry: `（10）` or `（11 项）`. A range like
# `（T73～T86）` names which ids live there and stays legal.
HEADER_COUNT = re.compile(r"[（(]\s*\d+\s*(?:项)?\s*[）)]")

OVERVIEW_TITLE = "进度总览"
SUBTOTAL_PREFIX = "小计"
TOTAL_LABEL = "去重后总计"


def task_ids_by_section(text: str) -> dict[str, list[str]]:
    """Task ids per section, skipping the overview table's own rows."""
    sections: dict[str, list[str]] = {}
    section: str | None = None
    in_overview = False
    for line in text.split("\n"):
        heading = HEADING.match(line)
        if heading:
            section = heading.group(1).strip()
            in_overview = section.startswith(OVERVIEW_TITLE)
            continue
        if in_overview or section is None:
            continue
        found = TASK_ROW.match(line)
        if found:
            sections.setdefault(section, []).append(found.group(1))
    return sections


def overview_numbers(text: str) -> dict[str, int]:
    """The counts the ledger declares about itself."""
    declared: dict[str, int] = {}
    in_overview = False
    for line in text.split("\n"):
        heading = HEADING.match(line)
        if heading:
            in_overview = heading.group(1).strip().startswith(OVERVIEW_TITLE)
            continue
        if not in_overview:
            continue
        row = OVERVIEW_ROW.match(line)
        if row:
            declared[row.group(1).strip()] = int(row.group(2))
    return declared



def _names_the_same_section(overview_label: str, heading: str) -> bool:
    """Whether an overview row and a section heading are the same section.

    Compared on the longest run of CJK/word characters they share rather than
    by equality, because the two are worded differently by design — the
    overview says `冻结区·今晚撞见的技术债`, the heading says `今晚撞见的技术债`.
    """
    stripped = _comparable(heading)
    return bool(stripped) and stripped in _comparable(overview_label)


def _comparable(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value)


def count_discrepancies(text: str) -> list[str]:
    """Every way this ledger fails to add up, stated in full."""
    problems: list[str] = []

    sections = task_ids_by_section(text)
    where_seen: dict[str, list[str]] = {}
    for section, ids in sections.items():
        for task in ids:
            where_seen.setdefault(task, []).append(section)
    for task, sections_holding_it in sorted(where_seen.items()):
        if len(sections_holding_it) > 1:
            problems.append(
                f"{task} 出现在 {len(sections_holding_it)} 张状态表里"
                f"（{'、'.join(sections_holding_it)}）；每个任务号只能出现在一张表里，"
                "否则任何按表统计的数都会重复计数"
            )

    declared = overview_numbers(text)
    distinct = len(where_seen)
    total = declared.get(TOTAL_LABEL)
    if total is None:
        problems.append(f"进度总览里没有「{TOTAL_LABEL}」这一行，无法核对总数")
    elif total != distinct:
        problems.append(f"进度总览声明总计 {total}，实际唯一任务号 {distinct} 个")

    # Each per-section number against the section it names. Without this the
    # arithmetic can be perfect while every line of it is wrong: a count moved
    # out of one section and into another still adds to the same total and
    # still matches the distinct ids. Measured 2026-07-27 — the overview
    # claimed 31 and 41 rows where the file held 29 and 42, and 11 un-closed
    # against an actual 12, and every check above passed.
    for label, declared_rows in declared.items():
        if label.startswith(SUBTOTAL_PREFIX) or label == TOTAL_LABEL:
            continue
        matching = [
            section for section in sections if _names_the_same_section(label, section)
        ]
        if len(matching) != 1:
            # Deliberately silent: the overview and the headings word these
            # differently on purpose, and a fuzzy match would only add a second
            # place to maintain wrong. Unmatched labels are covered by the
            # subtotal and total checks above.
            continue
        actual = len(sections[matching[0]])
        if actual != declared_rows:
            problems.append(
                f"进度总览说「{label}」有 {declared_rows} 项，该小节实际 {actual} 行"
            )

    subtotals = [value for key, value in declared.items() if key.startswith(SUBTOTAL_PREFIX)]
    if total is not None and subtotals and sum(subtotals) != total:
        problems.append(
            f"进度总览的小计相加是 {sum(subtotals)}，与声明的总计 {total} 不一致"
        )

    for line in text.split("\n"):
        heading = HEADING.match(line)
        if heading and HEADER_COUNT.search(heading.group(1)):
            problems.append(
                f"小节标题自带计数：{heading.group(1).strip()}；"
                "同一个数存两处必然漂移，计数只允许写在进度总览表里"
            )

    return problems


def main() -> None:
    text = LEDGER.read_text(encoding="utf-8")
    problems = count_discrepancies(text)
    if problems:
        detail = "\n".join(f"  - {problem}" for problem in problems)
        raise SystemExit(f"{LEDGER.name} 的计数对不上：\n{detail}")
    sections = task_ids_by_section(text)
    distinct = len({task for ids in sections.values() for task in ids})
    print(f"{LEDGER.name} 计数自洽：{len(sections)} 张状态表，{distinct} 个唯一任务号")


if __name__ == "__main__":
    main()
