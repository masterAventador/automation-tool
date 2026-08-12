"""Console entry for the independently packaged Local Executor process."""

from __future__ import annotations

import signal
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from types import FrameType
from typing import BinaryIO, TextIO

from automation_tool.executor.action_authorization import (
    ActionAuthorizationVerificationRejected,
)
from automation_tool.executor.action_gate import (
    ActionGateRejected,
    ExecutorActionGate,
    LocalActionHardPolicy,
)
from automation_tool.executor.action_operation import (
    DouyinActionOperationRejected,
    ProductionDouyinActionOperation,
)
from automation_tool.executor.authentication import (
    LocalSessionAuthenticationRejected,
    LocalSessionAuthenticator,
)
from automation_tool.executor.bootstrap import ExecutorBootstrapRejected, read_executor_bootstrap
from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_runtime import BrowserRuntime
from automation_tool.executor.command_processor import (
    ExecutorCommandProcessor,
    ExecutorCommandRejected,
)
from automation_tool.executor.crash_recovery import (
    ExecutorCrashRecoveryCoordinator,
    ExecutorCrashRecoveryRejected,
)
from automation_tool.executor.diagnostics import ExecutorRecoveryDiagnostics
from automation_tool.executor.discovery_operation import ProductionDouyinDiscoveryOperation
from automation_tool.executor.ledger import ExecutorLedger, ExecutorLedgerRejected
from automation_tool.executor.platform_commands import (
    DouyinLoginCommandOperation,
    DouyinPublishPreflightCommandOperation,
    PlatformCommandRejected,
    PlatformCommandRouter,
    PlatformCommandWorker,
)
from automation_tool.executor.rpa.douyin.health import DouyinSessionHealthReporter
from automation_tool.executor.runtime import (
    ExecutorProcessRejected,
    ExecutorProcessReporter,
    LocalExecutorProcess,
    RuntimeMetadata,
)


@contextmanager
def stop_signal_event() -> Iterator[threading.Event]:
    """Install process-local stop handlers and restore the caller's handlers."""

    stop = threading.Event()
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    break_signal = getattr(signal, "SIGBREAK", None)
    previous_break = signal.getsignal(break_signal) if isinstance(break_signal, int) else None

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if isinstance(break_signal, int):
        signal.signal(break_signal, request_stop)
    try:
        yield stop
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        if isinstance(break_signal, int) and previous_break is not None:
            signal.signal(break_signal, previous_break)


def build_platform_command_router(
    *,
    ledger: ExecutorLedger,
    browser_authority: BrowserLaunchAuthority,
    local_outbox: Queue[object],
    runtime_factory: Callable[[], BrowserRuntime],
) -> PlatformCommandRouter:
    """Assemble the production command router shared by the executor and its tests.

    ``local_outbox`` is drained straight onto the Control Plane socket and only
    accepts protocol envelopes. Publishing has no Control Plane message of its
    own yet, so it publishes nothing there and exposes its last outcome through
    ``latest_receipt()``, the pending critical-point summary through
    ``latest_approval()`` and the dispatch outcome through ``latest_release()``;
    PB-07 adds the App-facing projection of all three.
    """
    return PlatformCommandRouter(
        login=DouyinLoginCommandOperation(
            health_reporter=DouyinSessionHealthReporter(ledger=ledger),
            outbound=local_outbox,
            browser_authority=browser_authority,
            runtime_factory=runtime_factory,
        ),
        publish=DouyinPublishPreflightCommandOperation(
            ledger=ledger,
            browser_authority=browser_authority,
            runtime_factory=runtime_factory,
        ),
    )


def _fixed_error(error: TextIO, message: str) -> None:
    try:
        binary_error = getattr(error, "buffer", None)
        if binary_error is not None:
            binary_error.write((message + "\n").encode("utf-8"))
            binary_error.flush()
            return
        error.write(message + "\n")
        error.flush()
    except Exception:
        pass


def run_executor(stdin: BinaryIO, stdout: TextIO, stderr: TextIO) -> int:
    """Run one Executor lifetime and map every failure to a fixed exit contract."""

    try:
        bootstrap = read_executor_bootstrap(stdin)
    except ExecutorBootstrapRejected:
        _fixed_error(stderr, "Local Executor bootstrap is rejected")
        return 2
    return run_executor_with_bootstrap(bootstrap, stdin, stdout, stderr)


def run_executor_with_bootstrap(
    bootstrap: object,
    stdin: BinaryIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run one Executor lifetime from an already-parsed bootstrap document."""

    try:
        authenticator = LocalSessionAuthenticator(bootstrap.local_session_token)
        try:
            recovery_diagnostics = ExecutorRecoveryDiagnostics(stderr)
            ledger = ExecutorLedger(
                state_directory=Path(bootstrap.state_directory),
                installation_id=str(bootstrap.installation_id),
                executor_id=str(bootstrap.executor_id),
            )
            if bootstrap.local_emergency_stop:
                ledger.engage_action_emergency_stop(changed_at=datetime.now(UTC))
            browser_authority = BrowserLaunchAuthority()
            metadata = RuntimeMetadata.detect()
            if bootstrap.action_runtime is not None:
                action_policy = LocalActionHardPolicy(
                    minimum_interval=timedelta(
                        seconds=bootstrap.action_runtime.minimum_interval_seconds
                    ),
                    task_action_limit=bootstrap.action_runtime.task_action_limit,
                )
            else:
                action_policy = LocalActionHardPolicy(
                    minimum_interval=timedelta(seconds=5),
                    task_action_limit=20,
                )
            action_gate = ExecutorActionGate(
                ledger=ledger,
                policy=action_policy,
                clock=metadata,
            )
            action_operation = ProductionDouyinActionOperation(
                ledger=ledger,
                action_gate=action_gate,
                browser_authority=browser_authority,
                clock=metadata,
                runtime_factory=lambda: BrowserRuntime(
                    diagnostics=recovery_diagnostics,
                ),
            )
            command_processor = ExecutorCommandProcessor(
                ledger=ledger,
                installation_id=str(bootstrap.installation_id),
                executor_id=str(bootstrap.executor_id),
                discovery_operation=ProductionDouyinDiscoveryOperation(
                    ledger=ledger,
                    browser_authority=browser_authority,
                    runtime_factory=lambda: BrowserRuntime(
                        diagnostics=recovery_diagnostics,
                    ),
                    capture_successful_diagnostics=bootstrap.capture_successful_diagnostics,
                ),
                action_operation=action_operation,
            )
            if bootstrap.crash_recovery:
                ExecutorCrashRecoveryCoordinator(
                    ledger=ledger,
                    clock=metadata,
                ).run()
            local_outbox: Queue[object] = Queue()
            reporter = ExecutorProcessReporter(stdout, authenticator)
            platform_worker = PlatformCommandWorker(
                input_stream=stdin,
                authenticator=authenticator,
                operation=build_platform_command_router(
                    ledger=ledger,
                    browser_authority=browser_authority,
                    local_outbox=local_outbox,
                    runtime_factory=lambda: BrowserRuntime(
                        diagnostics=recovery_diagnostics,
                    ),
                ),
                result_writer=reporter.platform_command_result,
            )
            process = LocalExecutorProcess(
                bootstrap=bootstrap,
                metadata=metadata,
                reporter=reporter,
                command_processor=command_processor,
                local_outbox=local_outbox,
                diagnostics=recovery_diagnostics,
            )
            with stop_signal_event() as stop:
                worker_failed = threading.Event()

                def run_platform_worker() -> None:
                    try:
                        platform_worker.run(stop)
                    except PlatformCommandRejected:
                        worker_failed.set()
                        stop.set()

                worker = threading.Thread(
                    target=run_platform_worker,
                    name="automation-tool-platform-command-worker",
                    daemon=True,
                )
                worker.start()
                process.run(stop)
                if worker_failed.is_set():
                    raise PlatformCommandRejected
        finally:
            authenticator.close()
    except (
        ExecutorLedgerRejected,
        ActionAuthorizationVerificationRejected,
        ActionGateRejected,
        DouyinActionOperationRejected,
        ExecutorCommandRejected,
        ExecutorCrashRecoveryRejected,
        ExecutorProcessRejected,
        LocalSessionAuthenticationRejected,
        PlatformCommandRejected,
    ):
        _fixed_error(stderr, "Local Executor process is unavailable")
        return 1
    return 0


# The one-shot authoring run is a different process shape from the executor
# itself: it answers one request and exits, so it is selected by argument rather
# than by anything in the bootstrap the long-lived process reads.
AUTHOR_MOTION_ARGUMENT = "--author-motion"


def main() -> None:
    buffered_stdin = sys.stdin.buffer
    input_stream = getattr(buffered_stdin, "raw", buffered_stdin)
    if sys.argv[1:] == [AUTHOR_MOTION_ARGUMENT]:
        from automation_tool.executor.motion_authoring import (
            serve_one_motion_authoring_request,
        )

        raise SystemExit(serve_one_motion_authoring_request(input_stream, sys.stdout))
    raise SystemExit(run_executor(input_stream, sys.stdout, sys.stderr))


__all__ = ["AUTHOR_MOTION_ARGUMENT", "main", "run_executor", "stop_signal_event"]
