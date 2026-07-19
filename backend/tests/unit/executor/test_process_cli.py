from __future__ import annotations

import json
import signal
import subprocess
import sys
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path

import pytest

from automation_tool.executor import cli
from automation_tool.executor.authentication import LocalSessionAuthenticationRejected
from automation_tool.executor.runtime import LocalExecutorProcess

INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174003"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"
LOCAL_SESSION_TOKEN = "02" * 32


def source(
    *,
    websocket_url: str,
    state_directory: Path,
    token: str = "private-session",
) -> bytes:
    return (
        json.dumps(
            {
                "bootstrap_version": "1",
                "websocket_url": websocket_url,
                "local_session_token": LOCAL_SESSION_TOKEN,
                "session_token": token,
                "installation_id": INSTALLATION_ID,
                "executor_id": EXECUTOR_ID,
                "heartbeat_interval_seconds": 1,
                "state_directory": str(state_directory),
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def test_cli_maps_bootstrap_and_process_failures_to_fixed_exit_contracts(tmp_path: Path) -> None:
    output = StringIO()
    error = StringIO()
    assert cli.run_executor(BytesIO(b"private-invalid\n"), output, error) == 2
    assert output.getvalue() == ""
    assert error.getvalue() == "Local Executor bootstrap is rejected\n"

    output = StringIO()
    error = StringIO()
    assert (
        cli.run_executor(
            BytesIO(
                source(
                    websocket_url="ws://127.0.0.1:9/api/v1/executors/connect",
                    state_directory=tmp_path / "executor-state",
                )
            ),
            output,
            error,
        )
        == 1
    )
    assert output.getvalue() == ""
    assert error.getvalue() == "Local Executor process is unavailable\n"
    assert "private" not in error.getvalue().lower()

    class FailingError(StringIO):
        def write(self, value: str) -> int:
            raise OSError(value)

    assert cli.run_executor(BytesIO(b"invalid\n"), StringIO(), FailingError()) == 2


def test_cli_returns_success_after_the_runtime_stops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(LocalExecutorProcess, "run", lambda _self, _stop: None)

    status = cli.run_executor(
        BytesIO(
            source(
                websocket_url="ws://127.0.0.1:8765/api/v1/executors/connect",
                state_directory=tmp_path / "executor-state",
            )
        ),
        StringIO(),
        StringIO(),
    )

    assert status == 0


def test_cli_collapses_local_authenticator_failure_to_the_fixed_process_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def reject_authenticator(_token: object) -> object:
        raise LocalSessionAuthenticationRejected

    monkeypatch.setattr(cli, "LocalSessionAuthenticator", reject_authenticator)
    output = StringIO()
    error = StringIO()

    status = cli.run_executor(
        BytesIO(
            source(
                websocket_url="ws://127.0.0.1:8765/api/v1/executors/connect",
                state_directory=tmp_path / "executor-state",
            )
        ),
        output,
        error,
    )

    assert status == 1
    assert output.getvalue() == ""
    assert error.getvalue() == "Local Executor process is unavailable\n"


def test_signal_scope_sets_one_event_and_restores_process_handlers() -> None:
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)

    with cli.stop_signal_event() as stop:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert stop.is_set()

    assert signal.getsignal(signal.SIGINT) == previous_int
    assert signal.getsignal(signal.SIGTERM) == previous_term


def test_main_uses_binary_stdin_and_exits_with_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    stdin = TextIOWrapper(BytesIO(b"private-invalid\n"), encoding="utf-8")
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    with pytest.raises(SystemExit) as captured:
        cli.main()

    assert captured.value.code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "Local Executor bootstrap is rejected\n"


def test_executor_package_module_uses_the_formal_cli_entry() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "automation_tool.executor"],
        input=b"",
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == b"Local Executor bootstrap is rejected\n"
