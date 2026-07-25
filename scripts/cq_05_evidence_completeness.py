#!/usr/bin/env python3
"""CQ-05：Roadmap 的任务行与 `docs/development/` 的证据文件必须双向对得上。

AV-04 的门禁查的是"已激活任务有没有证据文件"。反方向没人查：一个任务被改名、拆分或
撤销之后，它的证据文件会留在原地，读的人以为那还是一项在做的工作——台账于是开始说谎，
而且是那种没人会发现的谎。

未激活的任务（`⬜ 未开始`、`⏸ 后置`）没有证据文件是正常的：它们还没开工。把这种情况
判成缺失，只会逼人为没做的事先写一个空台账，那比没有台账更糟。
"""

from __future__ import annotations

import re
from pathlib import Path

_TASK_ROW = re.compile(
    r"^\|\s*([A-Z]{2}-\d{2})\s*\|.*\|\s*([⬜🧪🚧🔍✅⏸][^|]*)\|\s*$", re.M
)
_EVIDENCE_NAME = re.compile(r"^([A-Z]{2}-\d{2})\.md$")
_NOT_ACTIVATED = ("⬜", "⏸")


class EvidenceCompletenessRejected(RuntimeError):
    """任务行与证据文件对不上。"""


def _reject(message: str) -> None:
    raise EvidenceCompletenessRejected(f"evidence completeness rejected: {message}")


def require_evidence_matches_roadmap(roadmap: Path, evidence_directory: Path) -> tuple[int, int]:
    """双向核对，返回（任务行数，已激活任务数）。"""
    rows = _TASK_ROW.findall(roadmap.read_text(encoding="utf-8"))
    if not rows:
        _reject(f"{roadmap} lists no task row — this check would prove nothing")

    activated = {
        task_id for task_id, status in rows if not status.strip().startswith(_NOT_ACTIVATED)
    }
    declared = {task_id for task_id, _ in rows}

    present = {
        match.group(1)
        for path in evidence_directory.iterdir()
        if path.is_file() and (match := _EVIDENCE_NAME.match(path.name))
    }

    missing = sorted(activated - present)
    if missing:
        _reject(f"activated tasks without an evidence file: {missing}")

    orphans = sorted(present - declared)
    if orphans:
        _reject(
            f"evidence files with no task row in the roadmap: {orphans} — "
            "a renamed, split or withdrawn task leaves its ledger behind"
        )
    return len(rows), len(activated)


__all__ = [
    "EvidenceCompletenessRejected",
    "require_evidence_matches_roadmap",
]
