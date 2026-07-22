"""Production boundary for one server-authorized Douyin action command."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import Protocol, cast, runtime_checkable

from automation_tool.executor.action_authorization import ActionAuthorizationExpectation
from automation_tool.executor.action_gate import (
    ActionGateLimited,
    ActionGateRejected,
    ExecutorActionGate,
)
from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserWindow,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.action_result import (
    DouyinActionResultFact,
    browse_action_result,
    comment_action_result,
    direct_message_action_result,
    recovered_action_result,
)
from automation_tool.executor.rpa.douyin.browse import (
    DouyinBrowseExecution,
    DouyinBrowseExecutionObservation,
    DouyinBrowseExecutionState,
)
from automation_tool.executor.rpa.douyin.comment_action import (
    DouyinCommentActionExecution,
    DouyinCommentActionIntent,
    DouyinCommentActionReceipt,
)
from automation_tool.executor.rpa.douyin.direct_message_action import (
    DouyinDirectMessageActionExecution,
    DouyinDirectMessageActionIntent,
    DouyinDirectMessageActionReceipt,
)
from automation_tool.executor.rpa.douyin.page_version import douyin_user_profile_url
from automation_tool.executor.rpa.douyin.profile_page import DouyinProfilePage
from automation_tool.executor.rpa.douyin.side_effect_recovery import DouyinSideEffectRecovery
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    ActionMessageTemplate,
    ActionResultEvidence,
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    DouyinSearchExposureAction,
    IdempotencyKey,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    TaskActionCommandEnvelope,
)

_NAVIGATION_TIMEOUT_MILLISECONDS = 30_000
_ACTION_TIMEOUT_MILLISECONDS = 15_000


class DouyinActionOperationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Douyin action operation is unavailable")


@runtime_checkable
class DouyinActionOperation(Protocol):
    """Execute one typed action command and return one closed result fact."""

    def run(self, command: TaskActionCommandEnvelope) -> DouyinActionResultFact: ...


class DouyinActionOperationClock(Protocol):
    def now(self) -> datetime: ...


class _Runtime(Protocol):
    def start(self, request: BrowserLaunchRequest) -> None: ...

    def primary_window(self) -> BrowserWindow: ...

    def close(self) -> None: ...


class _BrowseExecution(Protocol):
    def run(
        self,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinBrowseExecutionObservation: ...


class _CommentExecution(Protocol):
    def run(
        self,
        *,
        token: str,
        intent: DouyinCommentActionIntent,
    ) -> DouyinCommentActionReceipt: ...


class _DirectMessageExecution(Protocol):
    def run(
        self,
        *,
        token: str,
        intent: DouyinDirectMessageActionIntent,
    ) -> DouyinDirectMessageActionReceipt: ...


class _ProfilePage(Protocol):
    def first_video_entry(self) -> object: ...


class _Page(Protocol):
    def goto(self, url: str, *, wait_until: str, timeout: float) -> object: ...


def _default_comment(
    window: BrowserWindow,
    gate: ExecutorActionGate,
    ledger: ExecutorLedger,
    clock: DouyinActionOperationClock,
) -> DouyinCommentActionExecution:
    return DouyinCommentActionExecution(
        window=window,
        action_gate=gate,
        ledger=ledger,
        clock=clock,
    )


def _default_direct_message(
    window: BrowserWindow,
    gate: ExecutorActionGate,
    ledger: ExecutorLedger,
    clock: DouyinActionOperationClock,
) -> DouyinDirectMessageActionExecution:
    return DouyinDirectMessageActionExecution(
        window=window,
        action_gate=gate,
        ledger=ledger,
        clock=clock,
    )


class ProductionDouyinActionOperation:
    """Authorize, navigate, execute, settle, and close one action locally."""

    def __init__(
        self,
        *,
        ledger: ExecutorLedger,
        action_gate: ExecutorActionGate,
        browser_authority: BrowserLaunchAuthority,
        clock: DouyinActionOperationClock,
        runtime_factory: Callable[[], _Runtime] = BrowserRuntime,
        browse_factory: Callable[[BrowserWindow, DouyinCandidate], _BrowseExecution] = (
            DouyinBrowseExecution
        ),
        comment_factory: Callable[
            [BrowserWindow, ExecutorActionGate, ExecutorLedger, DouyinActionOperationClock],
            _CommentExecution,
        ] = _default_comment,
        direct_message_factory: Callable[
            [BrowserWindow, ExecutorActionGate, ExecutorLedger, DouyinActionOperationClock],
            _DirectMessageExecution,
        ] = _default_direct_message,
        profile_page_factory: Callable[[BrowserWindow], _ProfilePage] = DouyinProfilePage,
    ) -> None:
        if (
            not isinstance(ledger, ExecutorLedger)
            or not isinstance(action_gate, ExecutorActionGate)
            or not isinstance(browser_authority, BrowserLaunchAuthority)
            or not callable(getattr(clock, "now", None))
            or not all(
                callable(factory)
                for factory in (
                    runtime_factory,
                    browse_factory,
                    comment_factory,
                    direct_message_factory,
                    profile_page_factory,
                )
            )
        ):
            raise DouyinActionOperationRejected
        self._ledger = ledger
        self._action_gate = action_gate
        self._browser_authority = browser_authority
        self._clock = clock
        self._runtime_factory = runtime_factory
        self._browse_factory = browse_factory
        self._comment_factory = comment_factory
        self._direct_message_factory = direct_message_factory
        self._profile_page_factory = profile_page_factory

    def __repr__(self) -> str:
        return "ProductionDouyinActionOperation(<redacted>)"

    def run(self, command: TaskActionCommandEnvelope) -> DouyinActionResultFact:
        if not isinstance(command, TaskActionCommandEnvelope):
            raise DouyinActionOperationRejected
        expected = _expectation(command)
        try:
            self._action_gate.admit(
                token=command.payload.signed_authority,
                expected=expected,
            )
        except ActionGateLimited:
            return _failure(command, ActionResultEvidence.LOCAL_SAFETY_LIMIT)
        except ActionGateRejected:
            return _failure(command, ActionResultEvidence.ADMISSION_REJECTED)

        if command.payload.action is not DouyinSearchExposureAction.BROWSE:
            try:
                effect = self._ledger.get_side_effect(str(command.payload.action_id))
                if effect is not None and effect.state is not SideEffectState.PREPARED:
                    return recovered_action_result(
                        DouyinSideEffectRecovery.without_page_context(
                            ledger=self._ledger,
                            clock=self._clock,
                        ).run(action_id=command.payload.action_id)
                    )
            except Exception:
                return _failure(command, ActionResultEvidence.EXECUTOR_REPORTED_FAILURE)

        runtime: _Runtime | None = None
        try:
            candidate = _candidate(command)
            with self._browser_authority.lease() as request:
                runtime = self._runtime_factory()
                runtime.start(request)
                window = runtime.primary_window()
                if command.payload.action is DouyinSearchExposureAction.BROWSE:
                    return browse_action_result(
                        action_id=command.payload.action_id,
                        target_id=command.payload.target_id,
                        observation=self._browse_factory(window, candidate).run(
                            cancellation_requested=lambda: False
                        ),
                    )
                if command.payload.action is DouyinSearchExposureAction.COMMENT:
                    return self._comment(command, expected, candidate, window)
                return self._direct_message(command, expected, candidate, window)
        except Exception:
            return _failure(command, ActionResultEvidence.PAGE_UNAVAILABLE)
        finally:
            if runtime is not None:
                with suppress(Exception):
                    runtime.close()

    def _comment(
        self,
        command: TaskActionCommandEnvelope,
        expected: ActionAuthorizationExpectation,
        candidate: DouyinCandidate,
        window: BrowserWindow,
    ) -> DouyinActionResultFact:
        browse = self._browse_factory(window, candidate).run(cancellation_requested=lambda: False)
        if browse.state is not DouyinBrowseExecutionState.COMPLETED:
            return browse_action_result(
                action_id=command.payload.action_id,
                target_id=command.payload.target_id,
                observation=browse,
            )
        entry = self._profile_page_factory(window).first_video_entry()
        click = getattr(entry, "click", None)
        if not callable(click):
            raise DouyinActionOperationRejected
        click(timeout=_ACTION_TIMEOUT_MILLISECONDS)
        intent = DouyinCommentActionIntent(
            authorization=expected,
            message_template=ActionMessageTemplate(
                source=cast(str, command.payload.message_template)
            ),
            target_summary=candidate.summary,
        )
        receipt = self._comment_factory(
            window,
            self._action_gate,
            self._ledger,
            self._clock,
        ).run(token=command.payload.signed_authority, intent=intent)
        return comment_action_result(receipt)

    def _direct_message(
        self,
        command: TaskActionCommandEnvelope,
        expected: ActionAuthorizationExpectation,
        candidate: DouyinCandidate,
        window: BrowserWindow,
    ) -> DouyinActionResultFact:
        page = cast(_Page, window.playwright_page)
        page.goto(
            douyin_user_profile_url(candidate.platform_target_id),
            wait_until="domcontentloaded",
            timeout=_NAVIGATION_TIMEOUT_MILLISECONDS,
        )
        intent = DouyinDirectMessageActionIntent(
            authorization=expected,
            message_template=ActionMessageTemplate(
                source=cast(str, command.payload.message_template)
            ),
            target_summary=candidate.summary,
        )
        receipt = self._direct_message_factory(
            window,
            self._action_gate,
            self._ledger,
            self._clock,
        ).run(token=command.payload.signed_authority, intent=intent)
        return direct_message_action_result(receipt)


def _expectation(command: TaskActionCommandEnvelope) -> ActionAuthorizationExpectation:
    payload = command.payload
    return ActionAuthorizationExpectation(
        action_id=ProtocolActionId(str(payload.action_id)),
        target_id=ProtocolTargetId(str(payload.target_id)),
        execution_attempt_id=ProtocolExecutionAttemptId(str(command.execution_attempt_id)),
        task_id=ProtocolTaskId(str(command.task_id)),
        installation_id=ProtocolInstallationId(str(command.installation_id)),
        executor_id=ProtocolExecutorId(str(command.executor_id)),
        platform="douyin",
        action=payload.action,
        idempotency_key=IdempotencyKey(str(command.idempotency_key)),
    )


def _candidate(command: TaskActionCommandEnvelope) -> DouyinCandidate:
    payload = command.payload
    return DouyinCandidate(
        platform_target_id=payload.platform_target_id,
        summary=DouyinCandidateSummary(
            display_name=payload.display_name,
            public_handle=payload.public_handle,
        ),
        source=DouyinCandidateSource(payload.source),
        page_revision=payload.page_revision,
    )


def _failure(
    command: TaskActionCommandEnvelope,
    evidence: ActionResultEvidence,
) -> DouyinActionResultFact:
    return DouyinActionResultFact(
        action_id=command.payload.action_id,
        target_id=command.payload.target_id,
        message_type="step.failed",
        evidence=evidence,
    )


__all__ = [
    "DouyinActionOperation",
    "DouyinActionOperationClock",
    "DouyinActionOperationRejected",
    "ProductionDouyinActionOperation",
]
