#!/usr/bin/env python3
"""Refuse a completed user-facing task whose evidence stops at "it responded".

Why this exists
---------------
An acceptance can end at "the window opens and the button is clickable" and
still read as finished. T108 is the worked example: it proved the material-video
window was interactive through a real Tauri window and stopped there, so the
ledger said done while "can it actually produce a video" stayed unknown. The
product owner found out by opening the packaged App.

Why not a weak-word blacklist
-----------------------------
Rewording "clicked successfully" into "element present with correct state"
would pass a blacklist while proving exactly as little. What separates a real
acceptance is that it names something a reader can independently go and check:
a file with a size or digest, an `ffprobe`/`stat` reading, a database row, an id
the platform handed back, an exit code from a named log. None of those can be
written down without having run the thing.

Why declarations rather than inference
--------------------------------------
Layered work — a Page Object, an audit, a failure-matrix table — legitimately
has no such artefact. Guessing which is which from the title is how a gate
starts emitting noise and gets switched off, so each file says what it is.

Why an exemption list
---------------------
390 evidence files predate this rule. Turning them all red at once would bury
the signal; the list freezes the past and lets everything new be held to the
rule. `test_acceptance_evidence_depth.py` refuses an exemption naming a file
that no longer exists, so the list cannot quietly become a blanket permission.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT: Final = ROOT / "docs/development"
CONTRACT_PATH: Final = ROOT / "contracts/quality/acceptance-evidence-depth.v1.json"

_DECLARATION: Final = re.compile(r"^用户可操作：\s*(是|否)\s*$", re.MULTILINE)
_KIND: Final = re.compile(r"^证据类型：\s*(决策|查证|分层实现|文档)\s*$", re.MULTILINE)

# Something a reader can go and verify without taking the author's word for it.
# Each pattern names a shape that cannot be produced without having run the
# thing: a measured artefact, a stored row, an identifier a real system issued.
_TERMINAL_ANCHORS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bffprobe\b|\bffmpeg\b", re.IGNORECASE),
    re.compile(r"\b[0-9]{2,5}\s*[×x]\s*[0-9]{2,5}\b"),  # measured frame or image size
    re.compile(r"\bsha256\b|\b[0-9a-f]{40,64}\b", re.IGNORECASE),  # digest
    re.compile(r"\b[0-9][0-9,\.]*\s*(?:字节|bytes|KB|MB|GB|MiB|GiB)\b"),
    re.compile(r"\bEXIT=[0-9]+|\brc=[0-9]+|退出码"),
    re.compile(r"\bpsql\b|\bSELECT\b|\((?:[0-9]+ rows?)\)|数据库(?:里|中)(?:的)?[一那]?行"),
    re.compile(r"\bstat\b|\bmtime\b|创建于|落盘|写入.*?(?:路径|目录|文件)"),
    re.compile(r"作品\s*ID|artifact\s*id|submission\s+[0-9a-f-]{8,}", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:秒|帧|fps)\b"),  # a measured duration or frame count
)


class EvidenceProblem:
    NO_DECLARATION: Final = "no-declaration"
    NO_KIND: Final = "no-kind"
    NO_TERMINAL_ANCHOR: Final = "no-terminal-anchor"


@dataclass(frozen=True, slots=True)
class Problem:
    path: str
    kind: str
    detail: str


def load_contract() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def has_terminal_anchor(text: str) -> bool:
    return any(pattern.search(text) for pattern in _TERMINAL_ANCHORS)


def audit_text(name: str, text: str) -> list[Problem]:
    """Judge one evidence document; an empty list means it satisfies the rule."""
    declaration = _DECLARATION.search(text)
    if declaration is None:
        return [
            Problem(
                name,
                EvidenceProblem.NO_DECLARATION,
                "缺少「用户可操作：是/否」声明，无法判断该用哪条验收标准",
            )
        ]
    if declaration.group(1) == "否":
        if _KIND.search(text) is None:
            return [
                Problem(
                    name,
                    EvidenceProblem.NO_KIND,
                    "声明为非用户可操作，但没有写「证据类型：决策/查证/分层实现/文档」",
                )
            ]
        return []
    if not has_terminal_anchor(text):
        return [
            Problem(
                name,
                EvidenceProblem.NO_TERMINAL_ANCHOR,
                "用户可操作功能的证据里没有任何可外部核对的终态"
                "（产物尺寸/摘要、ffprobe 读数、数据库行、平台返回的 ID、退出码等）",
            )
        ]
    return []


def audit_repository(root: Path, *, exemptions: set[str]) -> list[Problem]:
    problems: list[Problem] = []
    for path in sorted(root.glob("*.md")):
        if path.name in exemptions:
            continue
        problems.extend(audit_text(path.name, path.read_text(encoding="utf-8")))
    return problems


def main() -> int:
    contract = load_contract()
    exemptions = set(contract["exemptions"])  # type: ignore[arg-type]
    problems = audit_repository(EVIDENCE_ROOT, exemptions=exemptions)
    checked = sum(
        1 for path in EVIDENCE_ROOT.glob("*.md") if path.name not in exemptions
    )
    for problem in problems:
        print(f"{problem.path}: {problem.kind}: {problem.detail}", file=sys.stderr)
    if problems:
        print(f"{len(problems)} evidence documents rejected", file=sys.stderr)
        return 1
    print(f"acceptance evidence depth: {checked} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
