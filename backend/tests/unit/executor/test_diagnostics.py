from __future__ import annotations

import json
from pathlib import Path

from automation_tool.executor.diagnostics import redact_diagnostic_line


def test_python_redactor_matches_every_shared_executor_diagnostic_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[4]
        / "contracts"
        / "fixtures"
        / "executor-diagnostics-v1.json"
    )
    document = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert document["fixtureVersion"] == "1"
    assert len(document["cases"]) >= 14
    for case in document["cases"]:
        assert redact_diagnostic_line(case["input"]) == case["expected"], case["name"]
