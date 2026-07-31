#!/usr/bin/env python3
"""CQ-04 生产纵向链路的静态装配事实。

凭据文件存在只证明可以认证供应商，不能证明正式 App 会把用户操作交给那个供应商。
独立剪辑当前正好踩中这个区别：服务 Gateway 已有，但用户项目/作业 Gateway 仍是
sessionStorage 实现。CQ-04 必须把两件事分开报告。
"""

from __future__ import annotations

import re
from pathlib import Path

_LOCAL_GATEWAY = "createLocalVideoEditingGateway(window.sessionStorage)"
_EXPECTED_FAILURE = re.compile(
    r'it\.fails\(\s*["\']videoEditingGateway is handed a real Tauri gateway["\']'
)
_REAL_TAURI_ASSIGNMENT = re.compile(
    r"const\s+videoEditingGateway\s*=\s*new\s+Tauri[A-Za-z0-9_]*Gateway\s*\("
)
_FAIL_CLOSED_SUBMISSION = re.compile(
    r"pub fn submit_editing_job[\s\S]{0,1600}"
    r"VideoEditingWorkspaceErrorCode::EditingServiceUnavailable"
)


class VerticalReadinessRejected(RuntimeError):
    """无法从正式生产入口得出装配事实。"""


def video_editing_production_wiring_gaps(
    main_source: Path,
    production_wiring_test: Path,
    execution_source: Path | None = None,
) -> tuple[str, ...]:
    """返回正式独立剪辑装配缺口；空元组才表示生产 wiring 已闭合。"""
    sources = (main_source, production_wiring_test) + (
        () if execution_source is None else (execution_source,)
    )
    missing = [
        path
        for path in sources
        if not path.is_file()
    ]
    if missing:
        raise VerticalReadinessRejected(
            "vertical readiness rejected: missing production source(s): "
            + ", ".join(str(path) for path in missing)
        )

    main_text = main_source.read_text(encoding="utf-8")
    test_text = production_wiring_test.read_text(encoding="utf-8")
    gaps: list[str] = []
    if _LOCAL_GATEWAY in main_text:
        gaps.append(
            "production App still constructs the sessionStorage editing gateway"
        )
    if _EXPECTED_FAILURE.search(test_text):
        gaps.append(
            "production wiring still marks the real Tauri editing gateway as expected failure"
        )
    if _REAL_TAURI_ASSIGNMENT.search(main_text) is None:
        gaps.append("production App constructs no real Tauri videoEditingGateway")
    if execution_source is not None and _FAIL_CLOSED_SUBMISSION.search(
        execution_source.read_text(encoding="utf-8")
    ):
        gaps.append(
            "production editing submission still fails closed before provider dispatch"
        )
    return tuple(gaps)


__all__ = [
    "VerticalReadinessRejected",
    "video_editing_production_wiring_gaps",
]
