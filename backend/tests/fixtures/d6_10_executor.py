"""Acceptance-only Executor for the D6-10 single-active discovery journey."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from automation_tool.executor import cli as executor_cli
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    DouyinDiscoveryOperationState,
)
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

_BUSY_SIGNAL_ENVIRONMENT = "AUTOMATION_TOOL_D610_BUSY_SIGNAL"
_OBSERVATIONS_ENVIRONMENT = "AUTOMATION_TOOL_D610_OBSERVATIONS"
_BUSY_SIGNAL_CONTENT = b"observed"
_GATE_TIMEOUT_SECONDS = 120


def _controlled_path(environment: str) -> Path:
    source = os.environ.get(environment)
    if source is None:
        raise RuntimeError("D6-10 controlled path is unavailable")
    path = Path(source)
    if not path.is_absolute() or path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeError("D6-10 controlled path is invalid")
    return path


def _record(event: str) -> None:
    document = (
        json.dumps(
            {"event": event},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    descriptor = os.open(
        _controlled_path(_OBSERVATIONS_ENVIRONMENT),
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(descriptor, document)
    finally:
        os.close(descriptor)


class AcceptanceDouyinDiscoveryOperation:
    """Hold one real command until the competing UI proves Installation busy."""

    def __init__(self, **_: Any) -> None:
        pass

    def run(
        self,
        payload: Any,
        *,
        cancellation_requested: Any,
    ) -> DouyinDiscoveryExecutionResult:
        signal = _controlled_path(_BUSY_SIGNAL_ENVIRONMENT)
        deadline = time.monotonic() + _GATE_TIMEOUT_SECONDS
        _record("discovery_waiting_for_busy_ui")
        while True:
            if cancellation_requested():
                raise RuntimeError("D6-10 acceptance discovery was cancelled")
            try:
                content = signal.read_bytes()
            except FileNotFoundError:
                content = None
            if content is not None:
                if content != _BUSY_SIGNAL_CONTENT:
                    raise RuntimeError("D6-10 busy UI signal is invalid")
                signal.unlink()
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("D6-10 busy UI signal did not arrive")
            time.sleep(0.05)
        _record("busy_ui_observed")

        candidates = tuple(
            DouyinCandidate(
                platform_target_id=f"acceptance-author-{index}",
                summary=DouyinCandidateSummary(
                    display_name=f"验收目标 {index}",
                    public_handle=f"acceptance_{index}",
                ),
                source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
                page_revision=payload.page_revision,
            )
            for index in (1, 2)
        )
        _record("discovery_completed")
        return DouyinDiscoveryExecutionResult(
            state=DouyinDiscoveryOperationState.COMPLETED,
            evidence="candidates_extracted",
            page_revision=payload.page_revision,
            candidates=candidates,
        )


def main() -> None:
    vars(executor_cli)["ProductionDouyinDiscoveryOperation"] = AcceptanceDouyinDiscoveryOperation
    executor_cli.main()


if __name__ == "__main__":
    main()
