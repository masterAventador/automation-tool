from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO, StringIO, TextIOWrapper

import pytest
from pydantic import SecretStr

from automation_tool.executor.authentication import LocalSessionAuthenticator
from automation_tool.executor.runtime import (
    ExecutorProcessRejected,
    ExecutorProcessReporter,
    RuntimeMetadata,
)

LOCAL_SESSION_TOKEN = "03" * 32


def authenticator() -> LocalSessionAuthenticator:
    return LocalSessionAuthenticator(SecretStr(LOCAL_SESSION_TOKEN))


def test_runtime_metadata_normalizes_only_supported_packaging_targets() -> None:
    assert RuntimeMetadata.detect(system_name="Darwin", machine_name="arm64") == RuntimeMetadata(
        executor_version="0.1.0",
        platform="macos",
        architecture="arm64",
    )
    assert RuntimeMetadata.detect(system_name="Windows", machine_name="AMD64") == RuntimeMetadata(
        executor_version="0.1.0",
        platform="windows",
        architecture="x86_64",
    )
    assert RuntimeMetadata.detect(system_name="Darwin", machine_name="x86_64") == RuntimeMetadata(
        executor_version="0.1.0",
        platform="macos",
        architecture="x86_64",
    )
    for system_name, machine_name in (
        ("Linux", "x86_64"),
        ("Darwin", "i386"),
        ("Windows", "armv7"),
        ("", "arm64"),
    ):
        with pytest.raises(ExecutorProcessRejected):
            RuntimeMetadata.detect(system_name=system_name, machine_name=machine_name)


def test_reporter_writes_only_fixed_bounded_health_events() -> None:
    output = StringIO()
    reporter = ExecutorProcessReporter(output, authenticator())

    reporter.healthy()
    reporter.stopped()

    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [line["event"] for line in lines] == ["executor.healthy", "executor.stopped"]
    assert all(line["protocolVersion"] == "1.0" for line in lines)
    assert all(line["authenticationProof"].startswith("atlep1.") for line in lines)
    assert lines[0]["authenticationProof"] != lines[1]["authenticationProof"]
    assert all(set(line) == {"authenticationProof", "event", "protocolVersion"} for line in lines)
    assert LOCAL_SESSION_TOKEN not in output.getvalue()


def test_reporter_writes_authenticated_platform_results_and_fails_closed() -> None:
    output = StringIO()
    process_reporter = ExecutorProcessReporter(output, authenticator())

    process_reporter.platform_command_result(
        command_id="123e4567-e89b-42d3-a456-426614174005",
        state="logged_out",
    )

    result = json.loads(output.getvalue())
    assert result["flowVersion"] == "douyin.session-control.v1"
    assert result["state"] == "logged_out"

    class FailingOutput(StringIO):
        def write(self, value: str) -> int:
            raise OSError("private output failure")

    with pytest.raises(ExecutorProcessRejected):
        ExecutorProcessReporter(FailingOutput(), authenticator()).platform_command_result(
            command_id="123e4567-e89b-42d3-a456-426614174005",
            state="logged_out",
        )


def test_reporter_uses_lf_bytes_when_stdout_translates_newlines() -> None:
    raw_output = BytesIO()
    translated_output = TextIOWrapper(raw_output, encoding="utf-8", newline="\r\n")
    reporter = ExecutorProcessReporter(translated_output, authenticator())

    reporter.healthy()

    assert raw_output.getvalue().endswith(b"\n")
    assert not raw_output.getvalue().endswith(b"\r\n")


def test_reporter_and_runtime_fail_closed_on_invalid_dependencies() -> None:
    with pytest.raises(ExecutorProcessRejected):
        ExecutorProcessReporter(object(), authenticator())  # type: ignore[arg-type]
    with pytest.raises(ExecutorProcessRejected):
        ExecutorProcessReporter(StringIO(), object())  # type: ignore[arg-type]
    with pytest.raises(ExecutorProcessRejected):
        RuntimeMetadata(
            executor_version="private-invalid",
            platform="macos",
            architecture="arm64",
        )

    class FailingOutput(StringIO):
        def write(self, value: str) -> int:
            raise OSError("private output failure")

    with pytest.raises(
        ExecutorProcessRejected,
        match=r"^Local Executor process is unavailable$",
    ) as captured:
        ExecutorProcessReporter(FailingOutput(), authenticator()).healthy()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_runtime_messages_use_current_utc_time() -> None:
    metadata = RuntimeMetadata.detect(system_name="Darwin", machine_name="arm64")
    before = datetime.now(UTC)
    observed = metadata.now()
    after = datetime.now(UTC)
    assert before <= observed <= after
