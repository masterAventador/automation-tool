from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO

import pytest

from automation_tool.executor.runtime import (
    ExecutorProcessRejected,
    ExecutorProcessReporter,
    RuntimeMetadata,
)


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
    reporter = ExecutorProcessReporter(output)

    reporter.healthy()
    reporter.stopped()

    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert lines == [
        {"event": "executor.healthy", "protocolVersion": "1.0"},
        {"event": "executor.stopped", "protocolVersion": "1.0"},
    ]
    assert all(set(line) == {"event", "protocolVersion"} for line in lines)


def test_reporter_and_runtime_fail_closed_on_invalid_dependencies() -> None:
    with pytest.raises(ExecutorProcessRejected):
        ExecutorProcessReporter(object())  # type: ignore[arg-type]
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
        ExecutorProcessReporter(FailingOutput()).healthy()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_runtime_messages_use_current_utc_time() -> None:
    metadata = RuntimeMetadata.detect(system_name="Darwin", machine_name="arm64")
    before = datetime.now(UTC)
    observed = metadata.now()
    after = datetime.now(UTC)
    assert before <= observed <= after
