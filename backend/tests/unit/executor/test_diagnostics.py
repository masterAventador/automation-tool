from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from automation_tool.executor.diagnostics import (
    ExecutorRecoveryDiagnostics,
    redact_diagnostic_line,
)


def test_python_redactor_matches_every_shared_executor_diagnostic_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[4]
        / "contracts"
        / "fixtures"
        / "executor-diagnostics-v1.json"
    )
    document = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert document["fixtureVersion"] == "2"
    assert len(document["cases"]) >= 18
    for case in document["cases"]:
        assert redact_diagnostic_line(case["input"]) == case["expected"], case["name"]


def test_recovery_diagnostics_are_fixed_bounded_and_track_browser_recovery() -> None:
    output = StringIO()
    diagnostics = ExecutorRecoveryDiagnostics(output)

    diagnostics.system_suspension_detected()
    diagnostics.command_deadline_expired()
    diagnostics.browser_window_unavailable()
    diagnostics.browser_window_unavailable()
    diagnostics.browser_window_available()
    diagnostics.browser_window_available()
    diagnostics.transport_recovered()

    assert output.getvalue().splitlines() == [
        "executor.recovery system_suspension_detected",
        "executor.recovery command_deadline_expired",
        "executor.recovery browser_window_unavailable",
        "executor.recovery browser_window_recovered",
        "executor.recovery transport_recovered",
    ]
    assert all(len(line.encode("utf-8")) <= 96 for line in output.getvalue().splitlines())


def test_recovery_diagnostics_reject_invalid_output_and_ignore_broken_stderr() -> None:
    class FailingOutput(StringIO):
        def write(self, value: str) -> int:
            raise OSError("private stderr failure")

    for output in (object(), None):
        try:
            ExecutorRecoveryDiagnostics(output)  # type: ignore[arg-type]
        except ValueError as error:
            assert str(error) == "invalid Executor diagnostic output"
        else:  # pragma: no cover - assertion helper
            raise AssertionError("invalid diagnostic output was accepted")

    diagnostics = ExecutorRecoveryDiagnostics(FailingOutput())
    diagnostics.system_suspension_detected()
