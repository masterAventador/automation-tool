"""Shared deterministic Executor for the D6-11/D6-12 preview acceptances.

PC-23 deliberately removed the second in-process Executor from D6-10 because
that acceptance now proves App-owned sidecar orchestration. D6-11 and D6-12
still exercise an earlier, narrower boundary: their hidden Apps create the
Task while this deterministic formal Executor supplies two preview candidates.
Keeping that fixture here prevents those runners from importing deleted
D6-10 implementation details.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from uuid import uuid4

from run_i2_13_acceptance import post_json

from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.executor import (
    ExecutorBootstrap,
    ExecutorCommandProcessor,
    ExecutorProcessReporter,
    LocalExecutorProcess,
    LocalSessionAuthenticator,
    RuntimeMetadata,
)
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    DouyinDiscoveryOperationState,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    DouyinDiscoveryCommandPayload,
)


class DeterministicPreviewDiscoveryOperation:
    """Return the two candidates consumed by the preview App journeys."""

    def run(
        self,
        payload: DouyinDiscoveryCommandPayload,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinDiscoveryExecutionResult:
        if cancellation_requested():
            raise RuntimeError("D6 preview acceptance was unexpectedly cancelled")
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
        return DouyinDiscoveryExecutionResult(
            state=DouyinDiscoveryOperationState.COMPLETED,
            evidence="candidates_extracted",
            page_revision=payload.page_revision,
            candidates=candidates,
        )


def executor_session(
    *,
    control_plane_port: int,
    credential: str,
) -> str:
    exchanged = post_json(
        control_plane_port,
        "/api/v1/device-sessions",
        credential,
        payload={"capability": DeviceSessionCapability.EXECUTOR_CONNECT.value},
        expected_status=201,
    )
    session_token = exchanged.get("sessionToken")
    if not isinstance(session_token, str):
        raise RuntimeError(
            "D6 preview Executor Session exchange omitted its opaque token"
        )
    return session_token


def start_executor(
    *,
    control_plane_port: int,
    private_app_data: Path,
    installation_id: InstallationId,
    session_token: str,
    state_directory_name: str = "d6-preview-executor-state",
    thread_name: str = "d6-preview-formal-executor",
) -> tuple[threading.Event, threading.Thread, list[BaseException]]:
    executor_id = str(uuid4())
    state_directory = private_app_data / state_directory_name
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(installation_id),
        executor_id=executor_id,
    )
    processor = ExecutorCommandProcessor(
        ledger=ledger,
        installation_id=str(installation_id),
        executor_id=executor_id,
        discovery_operation=DeterministicPreviewDiscoveryOperation(),
    )
    local_session_token = secrets.token_hex(32)
    bootstrap = ExecutorBootstrap.model_validate(
        {
            "bootstrap_version": "1",
            "websocket_url": (
                f"ws://127.0.0.1:{control_plane_port}/api/v1/executors/connect"
            ),
            "local_session_token": local_session_token,
            "session_token": session_token,
            "installation_id": str(installation_id),
            "executor_id": executor_id,
            "heartbeat_interval_seconds": 1,
            "state_directory": str(state_directory),
        }
    )
    authenticator = LocalSessionAuthenticator(bootstrap.local_session_token)
    reporter = ExecutorProcessReporter(StringIO(), authenticator)
    process = LocalExecutorProcess(
        bootstrap=bootstrap,
        metadata=RuntimeMetadata.detect(),
        reporter=reporter,
        command_processor=processor,
    )
    stop = threading.Event()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            process.run(stop)
        except BaseException as error:
            failures.append(error)
        finally:
            authenticator.close()

    thread = threading.Thread(target=run, name=thread_name, daemon=True)
    thread.start()
    return stop, thread, failures
