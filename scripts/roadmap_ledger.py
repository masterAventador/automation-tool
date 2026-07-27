#!/usr/bin/env python3
"""The judgements every task ledger in this repository makes, in one place.

Two ledgers exist — `docs/embedded-browser-video-studio-roadmap.md` for the 87
specialized tasks and `docs/product-completion-roadmap.md` for the product
completion line — and both are hand-maintained markdown tables that get recited
to a human as fact. The failure they share has already happened: three tasks
were finished, with the server rows and the rendered file to prove it, while
the table still read "waiting on the user" and that sentence was repeated as
truth.

What lives here is what does not depend on which ledger it is: an id appears
once, a dependency names a task that exists, at most one task is active, an
active or finished task has exactly one evidence file, and the ledger does not
accumulate completion records. What stays in the two gates is what genuinely
differs — the id shape, whether a fixed inventory exists, and the evidence
format, which is not the same document in the two lines and should not be
forced to be.

Copying these five checks into a second gate instead would leave two drifting
definitions of "one active task", which is the same class of defect the checks
are here to catch.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

STATUSES: Final[tuple[str, ...]] = (
    "⬜ 未开始",
    "🧪 RED",
    "🚧 实现中",
    "🔍 待验收",
    "✅ 已完成",
    "⏸ 后置",
)
ACTIVE_STATUSES: Final[frozenset[str]] = frozenset({"🧪 RED", "🚧 实现中"})
EVIDENCE_STATUSES: Final[frozenset[str]] = ACTIVE_STATUSES | {"🔍 待验收", "✅ 已完成"}

# A ledger records where a task got to. A completion record is the story of how
# it got there, and once those start landing in the table nobody can read a
# remaining count out of it any more — which is the only reason the table
# exists.
COMPLETION_RECORD_MARKERS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^##+ .*完成记录", re.MULTILINE),
    re.compile(r"^##+ .*完成证据", re.MULTILINE),
    re.compile(r"^## (?:RED|GREEN)$", re.MULTILINE),
    re.compile(r"^> 提交：", re.MULTILINE),
)

TASK_ROW_FIELDS: Final = 5


class LedgerError(ValueError):
    """Raised when a ledger contradicts itself or its evidence."""


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    dependencies: str
    status: str


def fail(message: str) -> None:
    raise LedgerError(message)


def read_text(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        fail(f"required regular file is missing: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"file is not UTF-8: {path}: {error}")
        raise AssertionError from None  # pragma: no cover


def parse_task_rows(
    ledger: str,
    *,
    row_pattern: re.Pattern[str],
    id_pattern: re.Pattern[str],
    known_ids: frozenset[str] | None = None,
) -> dict[str, TaskRow]:
    """Read the status table, refusing anything that is not a well-formed row.

    A cell holding a raw newline is the interesting case: markdown stops
    rendering it as a table and the field count no longer matches, so a row
    that looks fine in a diff is silently no longer a row. Both ledgers have
    been written that way by accident.

    `known_ids` is the closed inventory when a ledger has one; when it does not,
    dependencies are resolved against the ids the table itself declares.
    """
    rows: dict[str, TaskRow] = {}
    for line in ledger.splitlines():
        match = row_pattern.match(line)
        if match is None:
            continue
        fields = [field.strip() for field in line.strip().split("|")[1:-1]]
        if len(fields) != TASK_ROW_FIELDS:
            fail(
                f"task row must have {TASK_ROW_FIELDS} fields, found {len(fields)}"
                f" — a cell carrying a raw newline stops being a table row: {line[:60]}"
            )
        task_id = match.group(1)
        if task_id in rows:
            fail(f"duplicate task row: {task_id}")
        status = fields[4]
        if status not in STATUSES:
            fail(f"{task_id} has unsupported status: {status}")
        rows[task_id] = TaskRow(
            task_id=task_id, dependencies=fields[3], status=status
        )
    if not rows:
        fail("the ledger declares no tasks")

    resolvable = known_ids if known_ids is not None else frozenset(rows)
    for task_id, row in rows.items():
        dependency_ids = set(id_pattern.findall(row.dependencies))
        unknown = dependency_ids - resolvable
        if unknown:
            fail(f"{task_id} has unknown dependencies: {sorted(unknown)}")
        if task_id in dependency_ids:
            fail(f"{task_id} depends on itself")
    return rows


def require_single_active_task(rows: dict[str, TaskRow]) -> None:
    active = sorted(row.task_id for row in rows.values() if row.status in ACTIVE_STATUSES)
    if len(active) > 1:
        fail(f"only one task may be RED/in progress: {active}")


def require_no_completion_records(ledger: str) -> None:
    if any(pattern.search(ledger) for pattern in COMPLETION_RECORD_MARKERS):
        fail("completion evidence must not be appended to the ledger")


def require_evidence_files(
    rows: dict[str, TaskRow],
    evidence_root: Path,
    *,
    inspect: Callable[[str, str, str], None] | None = None,
) -> None:
    """Every task past `⬜ 未开始` must have exactly one readable evidence file.

    `inspect` receives (task_id, status, text) so each ledger can hold its own
    evidence format without that format leaking into the shared rule.
    """
    if not evidence_root.is_dir() or evidence_root.is_symlink():
        fail("evidence root must be a regular directory")
    for task_id, row in sorted(rows.items()):
        if row.status not in EVIDENCE_STATUSES:
            continue
        evidence = read_text(evidence_root / f"{task_id}.md")
        if inspect is not None:
            inspect(task_id, row.status, evidence)


def expect_failure(name: str, action: Callable[[], object]) -> None:
    """Prove a check still refuses what it claims to refuse."""
    try:
        action()
    except LedgerError:
        return
    fail(f"self-test expected failure but passed: {name}")


__all__ = [
    "ACTIVE_STATUSES",
    "COMPLETION_RECORD_MARKERS",
    "EVIDENCE_STATUSES",
    "STATUSES",
    "TASK_ROW_FIELDS",
    "LedgerError",
    "TaskRow",
    "expect_failure",
    "fail",
    "parse_task_rows",
    "read_text",
    "require_evidence_files",
    "require_no_completion_records",
    "require_single_active_task",
]
