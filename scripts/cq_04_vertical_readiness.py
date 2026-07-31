#!/usr/bin/env python3
"""CQ-04 生产纵向链路的静态装配事实。

凭据文件存在只证明可以认证供应商，不能证明正式 App 会把用户操作交给那个供应商。
独立剪辑曾经正好踩中这个区别；本门禁继续分别检查生产 Tauri Gateway 和原生执行链，
避免后续回退到 sessionStorage 或只留下未被正式入口调用的供应商代码。
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
_PRODUCTION_DISPATCH_FACTS = (
    "spawn_blocking",
    "credential_for_adapter",
    "verified_entrypoint",
    "stage_editing_artifacts",
    "build_video_editing_child_request",
    "run_video_editing_child",
    "import_output",
    "settle_editing_job",
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
    if execution_source is not None:
        execution_text = execution_source.read_text(encoding="utf-8")
        missing_dispatch_facts = [
            fact for fact in _PRODUCTION_DISPATCH_FACTS if fact not in execution_text
        ]
        if missing_dispatch_facts:
            gaps.append(
                "production editing dispatch is missing native facts: "
                + ", ".join(missing_dispatch_facts)
            )
    return tuple(gaps)


__all__ = [
    "VerticalReadinessRejected",
    "video_editing_production_wiring_gaps",
]
