#!/usr/bin/env python3
"""Keep `docs/product-completion-roadmap.md` honest about where the work is.

That file is the single source of status for the product-completion line, and
it is maintained by hand. The failure it is exposed to is not hypothetical: on
the other ledger three tasks were finished — the server rows, the files on
disk and the rendered video all existed — while the table still read "waiting
on the user", and that sentence was recited to the product owner as fact. **A
ledger nobody can check is worse than no ledger, because it is believed.**

What this refuses, all of it shared with the specialized gate through
`roadmap_ledger.py`: a duplicated id, a dependency naming a task that does not
exist, a self-dependency, more than one active task, a row whose cell carries a
raw newline (markdown stops rendering it as a table, so a row that reads fine in
a diff is silently no longer a row — both PC-07 and PC-13 were written that
way), a completion record appended to the ledger instead of the per-task file,
and a task past `⬜ 未开始` with no evidence file.

What it deliberately does not check: a fixed inventory. This line grows as gaps
are found, so `PC-15` appearing tomorrow is normal rather than drift — the
inventory check belongs to the specialized ledger, whose 87 tasks were scoped up
front.

Evidence format is deliberately lighter here than on the specialized ledger: the
depth requirement for a user-facing task already has its own gate in
`check_acceptance_evidence_depth.py`, and duplicating it would give two
definitions of "enough evidence".
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from roadmap_ledger import (  # noqa: E402
    EVIDENCE_STATUSES,
    LedgerError,
    TaskRow,
    expect_failure,
    fail,
    parse_task_rows,
    read_text,
    require_evidence_files,
    require_no_completion_records,
    require_single_active_task,
)

DEFAULT_LEDGER: Final = REPOSITORY_ROOT / "docs/product-completion-roadmap.md"
DEFAULT_EVIDENCE_ROOT: Final = REPOSITORY_ROOT / "docs/development"

TASK_ID: Final = re.compile(r"\bPC-\d{2}\b")
TASK_ROW: Final = re.compile(r"^\| (PC-\d{2}) \|")
DATE_LINE: Final = re.compile(r"^> 日期：\d{4}-\d{2}-\d{2}$", re.MULTILINE)


def inspect_evidence(task_id: str, status: str, evidence: str) -> None:
    """The minimum that makes an evidence file usable by someone else.

    Deliberately short. Whether the acceptance ran deep enough is judged by
    `check_acceptance_evidence_depth.py`, which reads the same files; asserting
    it again here would create a second, drifting definition.
    """
    if DATE_LINE.search(evidence) is None:
        fail(f"{task_id} evidence has no canonical date line (> 日期：YYYY-MM-DD)")
    if f"> 状态：**{status.split(' ', 1)[-1]}**" not in evidence:
        fail(f"{task_id} evidence does not state the ledger status {status!r}")
    # Numbered or not: the requirement is that a reader can find what else the
    # task changed, not that the sections are numbered a particular way.
    if re.search(r"^##+ (?:\d+[.、]\s*)?文档", evidence, flags=re.MULTILINE) is None:
        fail(f"{task_id} evidence has no documentation section")
    if len(evidence.encode("utf-8")) > 65_536:
        fail(f"{task_id} evidence exceeded 64 KiB")


def validate(ledger: str, evidence_root: Path) -> dict[str, TaskRow]:
    require_no_completion_records(ledger)
    if "docs/development/PC-nn.md" not in ledger:
        fail("the ledger must point at the per-task evidence convention")
    rows = parse_task_rows(ledger, row_pattern=TASK_ROW, id_pattern=TASK_ID)
    require_single_active_task(rows)
    require_evidence_files(rows, evidence_root, inspect=inspect_evidence)
    return rows


def run_self_test(ledger: str, evidence_root: Path) -> None:
    """Prove the refusals still refuse, against the real ledger."""
    first = next(line for line in ledger.splitlines() if TASK_ROW.match(line))
    expect_failure(
        "duplicate task", lambda: validate(f"{ledger}\n{first}\n", evidence_root)
    )
    expect_failure(
        "completion record inline", lambda: validate(f"{ledger}\n## GREEN\n", evidence_root)
    )
    split_row = ledger.replace(first, first.replace(" | ", " |\n| ", 1), 1)
    expect_failure("row split across lines", lambda: validate(split_row, evidence_root))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    arguments = parser.parse_args()

    try:
        ledger = read_text(arguments.ledger)
        rows = validate(ledger, arguments.evidence_root)
        if arguments.ledger == DEFAULT_LEDGER:
            run_self_test(ledger, arguments.evidence_root)
    except LedgerError as error:
        print(f"product completion ledger check failed: {error}", file=sys.stderr)
        return 1

    counted = {status: 0 for status in {row.status for row in rows.values()}}
    for row in rows.values():
        counted[row.status] += 1
    with_evidence = sum(row.status in EVIDENCE_STATUSES for row in rows.values())
    print(
        f"product completion ledger is valid: {len(rows)} tasks, "
        f"{with_evidence} with evidence, "
        + ", ".join(f"{status} {count}" for status, count in sorted(counted.items()))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
