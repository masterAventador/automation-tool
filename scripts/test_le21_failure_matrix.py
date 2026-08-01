#!/usr/bin/env python3
"""LE-21: keep every required local-editing failure bound to executable evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/quality/local-video-editing-failure-matrix.v1.json"

EXPECTED = (
    ("material-disappeared", "素材消失", "source_changed"),
    ("disk-full", "磁盘满", "resource_exhausted"),
    ("permission-denied", "权限拒绝", "resource_exhausted"),
    ("render-timeout", "渲染超时", "timed_out"),
    ("process-killed", "进程被杀", "recovered_once"),
    ("cancel-race", "取消竞争", "cancelled_once"),
    ("app-exit-recovery", "App 退出恢复", "resumed_without_redispatch"),
)
ALLOWED_RUNNERS = {"cargo-test", "pytest"}
ALLOWED_PREFIXES = ("backend/tests/", "frontend/src-tauri/tests/")


def load_contract() -> dict:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_matrix_names_every_required_failure_and_fixed_outcome_once() -> None:
    document = load_contract()
    assert set(document) == {"schemaVersion", "version", "roadmapTask", "scenarios"}
    assert document["schemaVersion"] == 1
    assert document["version"] == "local-video-editing.failure-matrix.v1"
    assert document["roadmapTask"] == "LE-21"
    assert [
        (item["id"], item["name"], item["expectedOutcome"])
        for item in document["scenarios"]
    ] == list(EXPECTED)
    assert all(
        set(item) == {"id", "name", "expectedOutcome", "evidence"}
        for item in document["scenarios"]
    )


def test_every_evidence_anchor_is_an_executable_test_inside_the_repository() -> None:
    for scenario in load_contract()["scenarios"]:
        evidence = scenario["evidence"]
        assert isinstance(evidence, list) and evidence
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            assert set(item) == {"runner", "path", "anchor"}
            assert item["runner"] in ALLOWED_RUNNERS
            path_text = item["path"]
            relative = PurePosixPath(path_text)
            assert not relative.is_absolute() and ".." not in relative.parts
            assert path_text.startswith(ALLOWED_PREFIXES)
            path = ROOT.joinpath(*relative.parts)
            assert path.is_file()
            assert path.suffix == (".py" if item["runner"] == "pytest" else ".rs")
            anchor = item["anchor"]
            assert isinstance(anchor, str) and re.fullmatch(r"[a-z][a-z0-9_]+", anchor)
            source = path.read_text(encoding="utf-8")
            if item["runner"] == "cargo-test":
                assert not source.lstrip().startswith("#![cfg(")
            declaration = (
                f"def {anchor}" if item["runner"] == "pytest" else f"fn {anchor}"
            )
            assert declaration in source
            key = (path_text, anchor)
            assert key not in seen
            seen.add(key)


def test_matrix_reaches_both_render_and_native_lifecycle_boundaries() -> None:
    scenarios = {item["id"]: item for item in load_contract()["scenarios"]}
    for scenario_id in (
        "material-disappeared",
        "disk-full",
        "permission-denied",
        "render-timeout",
    ):
        assert {item["runner"] for item in scenarios[scenario_id]["evidence"]} >= {
            "pytest"
        }
    for scenario_id in ("process-killed", "cancel-race", "app-exit-recovery"):
        assert {item["runner"] for item in scenarios[scenario_id]["evidence"]} >= {
            "cargo-test"
        }


def main() -> int:
    tests = [
        value for name, value in sorted(globals().items()) if name.startswith("test_")
    ]
    for test in tests:
        test()
    print("LE-21 failure matrix contract tests passed")
    print(f"executed checks: {len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
