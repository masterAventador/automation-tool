#!/usr/bin/env python3
"""CQ-05：Roadmap 的任务行与其任务族证据文件必须双向对得上。

AV-04 的门禁查的是"已激活任务有没有证据文件"。反方向没人查：一个任务被改名、拆分或
撤销之后，它的证据文件会留在原地，读的人以为那还是一项在做的工作——台账于是开始说谎，
而且是那种没人会发现的谎。

`docs/development/` 由多份 Roadmap 共用；只检查当前 Roadmap 已声明的任务族。PC/UI 等由
其他台账声明的任务不能被本专项误报为孤儿，同任务族内不存在的任务仍必须拒绝。

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


class ArchitectureCompletenessRejected(RuntimeError):
    """专项架构章节缺失，或只有标题没有实现事实。"""


def _reject(message: str) -> None:
    raise EvidenceCompletenessRejected(f"evidence completeness rejected: {message}")


def _reject_architecture(message: str) -> None:
    raise ArchitectureCompletenessRejected(
        f"architecture completeness rejected: {message}"
    )


_ARCHITECTURE_REQUIREMENTS = (
    (
        "frontend",
        "### 6.6 内置浏览器、Browser Use 与页面租约",
        ("Tauri/Rust", "BrowserSurfaceLease", "CDP", "React", "Profile"),
    ),
    (
        "frontend",
        "### 6.7 两种视频制作链路",
        (
            "TauriMaterialVideoStudioGateway",
            "LocalVideoOrchestrator",
            "RenderJob",
            "Artifact",
        ),
    ),
    (
        "backend",
        "### 8.4 Browser Use 受控执行边界",
        ("browser_use", "temporary Profile", "operations", "BrowserSurfaceLease", "CDP"),
    ),
    (
        "backend",
        "### 17.1 两种视频制作执行链",
        (
            "material_montage_v1",
            "motion_composition_v1",
            "LocalVideoOrchestrator",
            "VideoJobWorkspaceStore",
        ),
    ),
)


def _section(text: str, heading: str) -> str | None:
    marker = f"{heading}\n"
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    match = re.search(r"^#{2,3}\s", text[start:], flags=re.M)
    end = len(text) if match is None else start + match.start()
    return text[start:end]


def require_specialized_architecture(frontend: Path, backend: Path) -> None:
    """要求专项四条执行边界在主架构文档里有可核对的实现事实。"""
    documents = {
        "frontend": frontend.read_text(encoding="utf-8"),
        "backend": backend.read_text(encoding="utf-8"),
    }
    for document_name, heading, required_facts in _ARCHITECTURE_REQUIREMENTS:
        body = _section(documents[document_name], heading)
        if body is None:
            _reject_architecture(f"{document_name} is missing section {heading!r}")
        missing = [fact for fact in required_facts if fact not in body]
        if missing:
            _reject_architecture(
                f"{document_name} section {heading!r} is missing facts: {missing}"
            )


def require_evidence_matches_roadmap(roadmap: Path, evidence_directory: Path) -> tuple[int, int]:
    """双向核对，返回（任务行数，已激活任务数）。"""
    rows = _TASK_ROW.findall(roadmap.read_text(encoding="utf-8"))
    if not rows:
        _reject(f"{roadmap} lists no task row — this check would prove nothing")

    activated = {
        task_id for task_id, status in rows if not status.strip().startswith(_NOT_ACTIVATED)
    }
    declared = {task_id for task_id, _ in rows}
    declared_families = {task_id.partition("-")[0] for task_id in declared}

    present = {
        match.group(1)
        for path in evidence_directory.iterdir()
        if path.is_file() and (match := _EVIDENCE_NAME.match(path.name))
        and match.group(1).partition("-")[0] in declared_families
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
    "ArchitectureCompletenessRejected",
    "EvidenceCompletenessRejected",
    "require_specialized_architecture",
    "require_evidence_matches_roadmap",
]
