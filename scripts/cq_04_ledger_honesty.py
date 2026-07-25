#!/usr/bin/env python3
"""CQ-04：`🔍 待验收` 的任务必须说得出自己缺什么。

AV-04 的 Roadmap 门禁检查字段齐全、计数一致、只有一个活跃任务。它检查不到的是
**内容与状态相符**：一个任务可以字段写全、状态标 `🔍 待验收`，而「遗留项」小节
只有一个空表头——读的人无从判断它离完成还差多远。

## 这条判据的范围是被实测收窄过的

对全部 87 项跑了四轮，每轮都发现是**判据**错而不是台账错：

1. 只认两列表格 → 三列表格（项/状态/归属）整张读成空；
2. 只认表格 → 列表写法（`- 🔍 **待凭据**：…`）整节读成空；
3. 穷举"未做/待凭据/…"这类词判未闭合 → 漏掉没想到的"待加固"；
4. 只认无序列表 → 有序列表（`1. **…**`）和散文段落又读成空。

结论：**判据不该去解析格式**。本仓库的遗留项至少有五种写法，每修一次就冒出一种新的。
所以现在问的是一个格式无关的问题——那一节里有没有实质文字。

同样被收窄掉的还有"已完成的任务不该有遗留项"：本仓库的「遗留项」既写本任务的缺口，
也写下游承接（"BU-04：把租约接进执法点"）。实跑时 `✅ 已完成` 一侧报出 22 处，
几乎全是后者。判据分不清就不判——一条会稳定误报的门禁比没有门禁更糟，它会训练人忽略它。
"""

from __future__ import annotations

import re
from pathlib import Path

COMPLETE = "✅ 已完成"
PENDING = "🔍 待验收"

_HEADING = re.compile(r"^#{1,6}\s")
# 表格骨架：表头分隔行，以及只有列名的表头行。
_TABLE_SKELETON = re.compile(r"^\|[\s\-:|]+\|?\s*$")
_TABLE_HEADER_WORDS = {"项", "状态", "归属", "说明", "备注", "任务"}
_CHINESE = re.compile(r"[一-鿿]")


class LedgerHonestyRejected(RuntimeError):
    """任务状态与它自己的证据内容不符。"""


def _reject(message: str) -> None:
    raise LedgerHonestyRejected(f"ledger honesty rejected: {message}")


def _is_table_header(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(cell in _TABLE_HEADER_WORDS or not cell for cell in cells)


def leftover_substance(evidence: str) -> str:
    """返回「遗留项」小节里去掉表格骨架后剩下的实质文字。

    不关心它写成表格、无序列表、有序列表还是散文——只关心有没有东西。
    """
    lines = evidence.splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines) if line.strip().startswith("## 遗留项")
        )
    except StopIteration:
        return ""
    kept: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if _HEADING.match(stripped):
            break
        if not stripped:
            continue
        if stripped.startswith("|") and (
            _TABLE_SKELETON.match(stripped) or _is_table_header(stripped)
        ):
            continue
        kept.append(stripped)
    text = " ".join(kept)
    return text if _CHINESE.search(text) else ""


def require_status_matches_evidence(task_id: str, status: str, evidence_path: Path) -> None:
    """拒绝 `🔍 待验收` 却说不出缺什么的任务。"""
    if not evidence_path.is_file():
        _reject(f"{task_id} is {status} but has no evidence file at {evidence_path}")
    evidence = evidence_path.read_text(encoding="utf-8")
    if PENDING in status and not leftover_substance(evidence):
        _reject(
            f"{task_id} is marked {PENDING} but its 遗留项 section has no substance — "
            "a reader cannot tell how far it is from done"
        )


__all__ = [
    "COMPLETE",
    "PENDING",
    "LedgerHonestyRejected",
    "leftover_substance",
    "require_status_matches_evidence",
]
