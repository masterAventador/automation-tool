#!/usr/bin/env python3
"""PC-11 boundary tests for the product-completion ledger gate.

Why this ledger needs a gate of its own
---------------------------------------
`docs/product-completion-roadmap.md` is hand-maintained, and the failure it is
exposed to has already happened once on the other ledger: three tasks were
finished — the server rows, the files on disk, the rendered video all existed —
while the table still read "waiting on the user", and that sentence was
recited to the product owner as fact. A ledger nobody can check is worse than
no ledger, because it is believed.

The checks here are the ones that hold regardless of what the tasks are about:
an id appears once, a dependency names a task that exists, at most one task is
active, an active or finished task has exactly one evidence file, and the
roadmap itself does not accumulate completion records (that is what turns a
ledger into a changelog nobody can read a remaining count out of).

Shared with the specialized ledger on purpose
---------------------------------------------
`scripts/roadmap_ledger.py` holds the judgements both ledgers make; only the
conventions that genuinely differ — the id shape, the evidence format, whether
a fixed inventory exists — stay in the two gates. Copying the logic instead
would leave two drifting definitions of "one active task".
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check_product_completion_roadmap.py"
LEDGER = ROOT / "docs/product-completion-roadmap.md"
EVIDENCE_ROOT = ROOT / "docs/development"


def run_check(ledger: Path = LEDGER, evidence: Path = EVIDENCE_ROOT):
    return subprocess.run(
        [sys.executable, str(CHECK), "--ledger", str(ledger), "--evidence-root", str(evidence)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )


def expect_failure(name: str, ledger_text: str) -> None:
    with tempfile.TemporaryDirectory(prefix="automation-tool-pc11-test-") as temporary:
        path = Path(temporary) / "ledger.md"
        path.write_text(ledger_text, encoding="utf-8")
        result = run_check(path)
        assert result.returncode != 0, f"{name}: tampered ledger must fail"
        assert "product completion ledger check failed" in result.stderr, (
            f"{name}: {result.stderr}"
        )


def a_row(text: str, task_id: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f"| {task_id} |"))


def set_status(text: str, task_id: str, status: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"| {task_id} |"):
            fields = [field.strip() for field in line.strip().split("|")[1:-1]]
            fields[4] = status
            lines[index] = "| " + " | ".join(fields) + " |"
            return "\n".join(lines) + "\n"
    raise AssertionError(f"no row for {task_id}")


def an_unfinished_task(text: str) -> str:
    """A task whose evidence file does not claim completion.

    Used to build the "ledger says done, evidence says otherwise" tamper. The
    gate compares the two, so the tamper only bites on a task that has not
    finished yet — and which task that is changes as the work proceeds.
    """
    for line in text.splitlines():
        if not line.startswith("| PC-"):
            continue
        fields = [field.strip() for field in line.strip().split("|")[1:-1]]
        task_id, status = fields[0], fields[4]
        if status == "✅ 已完成":
            continue
        evidence = EVIDENCE_ROOT / f"{task_id}.md"
        if evidence.is_file() and "> 状态：**已完成**" not in evidence.read_text(encoding="utf-8"):
            return task_id
    raise AssertionError(
        "every task is finished, so this tamper can no longer be built; "
        "replace it with one that does not depend on unfinished work"
    )


def main() -> int:
    assert CHECK.is_file(), "scripts/check_product_completion_roadmap.py is missing"
    assert LEDGER.is_file(), "docs/product-completion-roadmap.md is missing"

    green = run_check()
    assert green.returncode == 0, f"the real ledger must pass: {green.stderr}"

    text = LEDGER.read_text(encoding="utf-8")

    expect_failure("duplicate task id", f"{text}\n{a_row(text, 'PC-01')}\n")

    two_active = set_status(text, "PC-01", "🧪 RED")
    two_active = set_status(two_active, "PC-02", "🚧 实现中")
    expect_failure("two active tasks", two_active)

    # A row whose cell carries a raw newline stops being a table row at all —
    # the renderer breaks it and the parser sees the wrong field count. This is
    # not hypothetical: both PC-07 and PC-13 were written that way.
    broken = text.replace(a_row(text, "PC-05"), a_row(text, "PC-05").replace(" | ", " |\n| ", 1), 1)
    expect_failure("row split across lines", broken)

    unknown_dependency = text.replace(a_row(text, "PC-05"), a_row(text, "PC-05").replace(
        "| PC-03 |", "| PC-99 |", 1
    ), 1)
    expect_failure("dependency names a task that does not exist", unknown_dependency)

    expect_failure("completion record appended to the ledger", f"{text}\n## RED\n")

    # The table says done while the evidence file says otherwise: the shape of
    # the defect this whole gate exists for.
    #
    # The target is *derived*, not named. Naming one (it used to be PC-06) makes
    # this case rot on the day that task legitimately finishes: the tamper then
    # agrees with its own evidence file, the gate correctly stays green, and the
    # self-test fails for a reason that has nothing to do with the gate. Which
    # is exactly what happened on 2026-07-29.
    expect_failure(
        "finished task whose evidence does not say so",
        set_status(text, an_unfinished_task(text), "✅ 已完成"),
    )

    print("product completion ledger tests passed")
    print("executed checks: 6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
