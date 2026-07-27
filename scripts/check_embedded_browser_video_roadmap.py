#!/usr/bin/env python3
"""Validate the specialized roadmap and one-file-per-task evidence contract."""

from __future__ import annotations

import argparse
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROADMAP = REPOSITORY_ROOT / "docs/embedded-browser-video-studio-roadmap.md"
DEFAULT_EVIDENCE_ROOT = REPOSITORY_ROOT / "docs/development"
DEFAULT_LEGACY_ROADMAP = REPOSITORY_ROOT / "docs/development-roadmap.md"
GROUP_COUNTS = {
    "AV": 4,
    "EB": 17,
    "BU": 7,
    "VF": 7,
    "IM": 8,
    "BM": 16,
    "VE": 8,
    "PB": 8,
    "SA": 7,
    "CQ": 5,
}
EXPECTED_IDS = {
    f"{prefix}-{number:02d}"
    for prefix, count in GROUP_COUNTS.items()
    for number in range(1, count + 1)
}
# Derived, never typed a second time. The inventory grows when a workstream
# turns out to need a task nobody planned for, and the two places below that
# used to spell the total out are the shape this repository has already been
# bitten by: one gets updated, the other keeps asserting the old number and
# the gate then rejects the very ledger it was meant to protect.
TOTAL_TASKS = len(EXPECTED_IDS)
STATUSES = (
    "⬜ 未开始",
    "🧪 RED",
    "🚧 实现中",
    "🔍 待验收",
    "✅ 已完成",
    "⏸ 后置",
)
ACTIVE_STATUSES = {"🧪 RED", "🚧 实现中"}
EVIDENCE_STATUSES = ACTIVE_STATUSES | {"🔍 待验收", "✅ 已完成"}
TASK_ID_PATTERN = re.compile(r"\b(?:AV|EB|BU|VF|IM|BM|VE|PB|SA|CQ)-\d{2}\b")
TASK_ROW_PATTERN = re.compile(r"^\| ((?:AV|EB|BU|VF|IM|BM|VE|PB|SA|CQ)-\d{2}) \|")
DATE_PATTERN = re.compile(r"^> 日期：\d{4}-\d{2}-\d{2}$", re.MULTILINE)


class RoadmapError(ValueError):
    """Raised when the specialized roadmap contract is invalid."""


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    dependencies: str
    status: str


def fail(message: str) -> None:
    raise RoadmapError(message)


def read_text(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        fail(f"required regular file is missing: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"file is not UTF-8: {path}: {error}")


def parse_task_rows(roadmap: str) -> dict[str, TaskRow]:
    rows: dict[str, TaskRow] = {}
    for line in roadmap.splitlines():
        match = TASK_ROW_PATTERN.match(line)
        if match is None:
            continue
        fields = [field.strip() for field in line.strip().split("|")[1:-1]]
        if len(fields) != 5:
            fail(f"task row must have five fields: {line}")
        task_id = match.group(1)
        if task_id in rows:
            fail(f"duplicate task row: {task_id}")
        status = fields[4]
        if status not in STATUSES:
            fail(f"{task_id} has unsupported status: {status}")
        dependencies = fields[3]
        dependency_ids = set(TASK_ID_PATTERN.findall(dependencies))
        unknown = dependency_ids - EXPECTED_IDS
        if unknown:
            fail(f"{task_id} has unknown dependencies: {sorted(unknown)}")
        if task_id in dependency_ids:
            fail(f"{task_id} depends on itself")
        rows[task_id] = TaskRow(
            task_id=task_id,
            dependencies=dependencies,
            status=status,
        )
    missing = EXPECTED_IDS - set(rows)
    extra = set(rows) - EXPECTED_IDS
    if missing or extra or len(rows) != TOTAL_TASKS:
        fail(
            "task inventory drifted: "
            f"count={len(rows)}, missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return rows


def validate_summary(roadmap: str, rows: dict[str, TaskRow]) -> None:
    declared: dict[str, int] = {}
    for line in roadmap.splitlines():
        fields = [field.strip() for field in line.strip().split("|")[1:-1]]
        if len(fields) != 2 or fields[0] not in STATUSES:
            continue
        status, raw_count = fields
        if status in declared:
            fail(f"duplicate status summary: {status}")
        if not raw_count.isdigit():
            fail(f"status summary is not an integer: {status}")
        declared[status] = int(raw_count)
    if set(declared) != set(STATUSES):
        fail("status summary must declare every allowed status exactly once")
    actual = Counter(row.status for row in rows.values())
    for status in STATUSES:
        if declared[status] != actual[status]:
            fail(
                f"status summary drifted for {status}: "
                f"declared={declared[status]}, actual={actual[status]}"
            )
    if sum(declared.values()) != TOTAL_TASKS:
        fail(f"status summary total must be {TOTAL_TASKS}")


def validate_roadmap_text(roadmap: str) -> dict[str, TaskRow]:
    if len(roadmap.encode("utf-8")) > 131_072 or len(roadmap.splitlines()) > 900:
        fail("specialized roadmap exceeded its lightweight size budget")
    forbidden_completion_markers = (
        re.compile(r"^##+ .*完成记录", re.MULTILINE),
        re.compile(r"^##+ .*完成证据", re.MULTILINE),
        re.compile(r"^## (?:RED|GREEN)$", re.MULTILINE),
        re.compile(r"^> 提交：", re.MULTILINE),
    )
    if any(pattern.search(roadmap) for pattern in forbidden_completion_markers):
        fail("completion evidence must not be appended to the specialized roadmap")
    if "docs/development/<任务ID>.md" not in roadmap:
        fail("specialized roadmap must point to the per-task evidence convention")
    rows = parse_task_rows(roadmap)
    active = [row.task_id for row in rows.values() if row.status in ACTIVE_STATUSES]
    if len(active) > 1:
        fail(f"only one task may be RED/in progress: {active}")
    validate_summary(roadmap, rows)
    return rows


def require_evidence_headings(task_id: str, status: str, evidence: str) -> None:
    required = (
        f"# {task_id} 完成证据",
        f"> 状态：{status}",
        "> 提交：",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
        "## 遗留项",
    )
    missing = [heading for heading in required if heading not in evidence]
    if missing:
        fail(f"{task_id} evidence is missing fields: {missing}")
    if DATE_PATTERN.search(evidence) is None:
        fail(f"{task_id} evidence has no canonical date")
    if re.search(r"^> 提交：\s*\S", evidence, flags=re.MULTILINE) is None:
        fail(f"{task_id} evidence has no commit reference")
    if len(evidence.encode("utf-8")) > 65_536:
        fail(f"{task_id} evidence exceeded 64 KiB")
    if not re.search(r"^## 文档.*$", evidence, flags=re.MULTILINE):
        fail(f"{task_id} evidence has no documentation section")


def validate_evidence(rows: dict[str, TaskRow], evidence_root: Path) -> None:
    if not evidence_root.is_dir() or evidence_root.is_symlink():
        fail("evidence root must be a regular directory")
    specialized_name = re.compile(r"^(?:AV|EB|BU|VF|IM|BM|VE|PB|SA|CQ)-\d{2}\.md$")
    unknown_files = sorted(
        path.name
        for path in evidence_root.iterdir()
        if specialized_name.fullmatch(path.name) and path.stem not in EXPECTED_IDS
    )
    if unknown_files:
        fail(f"unknown specialized evidence files: {unknown_files}")
    for task_id, row in rows.items():
        evidence_path = evidence_root / f"{task_id}.md"
        if row.status not in EVIDENCE_STATUSES:
            continue
        evidence = read_text(evidence_path)
        require_evidence_headings(task_id, row.status, evidence)


def validate_legacy_roadmap(legacy: str) -> None:
    duplicated = [line for line in legacy.splitlines() if TASK_ROW_PATTERN.match(line)]
    if duplicated:
        fail("specialized task rows must not be copied into docs/development-roadmap.md")


def expect_failure(name: str, action: Callable[[], object]) -> None:
    try:
        action()
    except RoadmapError:
        return
    fail(f"self-test expected failure but passed: {name}")


def replace_task_status(roadmap: str, task_id: str, status: str) -> str:
    lines = roadmap.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if not line.startswith(f"| {task_id} |"):
            continue
        fields = [field.strip() for field in line.strip().split("|")[1:-1]]
        fields[4] = status
        lines[index] = "| " + " | ".join(fields) + " |"
        replaced = True
        break
    if not replaced:
        fail(f"self-test cannot find task row: {task_id}")
    return "\n".join(lines) + ("\n" if roadmap.endswith("\n") else "")


def run_self_test(
    roadmap: str,
    rows: dict[str, TaskRow],
    evidence_root: Path,
    legacy: str,
) -> None:
    first_row = next(line for line in roadmap.splitlines() if line.startswith("| AV-01 |"))
    expect_failure("duplicate task", lambda: validate_roadmap_text(f"{roadmap}\n{first_row}\n"))
    completed_count = sum(row.status == "✅ 已完成" for row in rows.values())
    wrong_summary = roadmap.replace(
        f"| ✅ 已完成 | {completed_count} |",
        f"| ✅ 已完成 | {completed_count + 1} |",
        1,
    )
    expect_failure("summary drift", lambda: validate_roadmap_text(wrong_summary))
    two_active = replace_task_status(roadmap, "EB-01", "🧪 RED")
    two_active = replace_task_status(two_active, "EB-02", "🚧 实现中")
    expect_failure("two active tasks", lambda: validate_roadmap_text(two_active))
    expect_failure("inline evidence", lambda: validate_roadmap_text(f"{roadmap}\n## GREEN\n"))
    expect_failure(
        "legacy duplication",
        lambda: validate_legacy_roadmap(f"{legacy}\n| AV-01 | copied | — | ✅ 已完成 |\n"),
    )
    with tempfile.TemporaryDirectory(prefix="automation-tool-av04-self-test-") as temporary:
        copied_root = Path(temporary)
        for task_id, row in rows.items():
            if row.status in EVIDENCE_STATUSES:
                shutil.copyfile(evidence_root / f"{task_id}.md", copied_root / f"{task_id}.md")
        av_01_path = copied_root / "AV-01.md"
        av_01_path.write_text(
            av_01_path.read_text(encoding="utf-8").replace("## 失败矩阵", "## 缺失矩阵", 1),
            encoding="utf-8",
        )
        expect_failure("missing evidence field", lambda: validate_evidence(rows, copied_root))
    print("specialized roadmap gate self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roadmap", type=Path, default=DEFAULT_ROADMAP)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--legacy-roadmap", type=Path, default=DEFAULT_LEGACY_ROADMAP)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()

    roadmap = read_text(arguments.roadmap)
    rows = validate_roadmap_text(roadmap)
    validate_evidence(rows, arguments.evidence_root)
    legacy = read_text(arguments.legacy_roadmap)
    validate_legacy_roadmap(legacy)
    if arguments.self_test:
        run_self_test(roadmap, rows, arguments.evidence_root, legacy)
    print("specialized roadmap status and per-task evidence are valid")


if __name__ == "__main__":
    try:
        main()
    except RoadmapError as error:
        raise SystemExit(f"specialized roadmap check failed: {error}") from error
