"""Authenticated local platform commands accepted only from the Tauri parent."""

from __future__ import annotations

import threading
import time
from hmac import compare_digest
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from queue import Queue
from types import MappingProxyType
from typing import Annotated, BinaryIO, Final, Literal, Protocol, TextIO, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from automation_tool.executor.authentication import (
    LocalSessionAuthenticationRejected,
    LocalSessionAuthenticator,
)
from automation_tool.executor.browser_authority import (
    BrowserLaunchAuthority,
    BrowserLaunchAuthorityRejected,
    BrowserLaunchLease,
)
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserWindow,
)
from automation_tool.executor.browser_surface_lease import (
    BrowserSurfaceLeaseManager,
    SurfaceLeaseRejected,
)
from automation_tool.executor.rpa.douyin.login import (
    DouyinQrLoginFlow,
    DouyinQrLoginState,
)
from automation_tool.executor.rpa.douyin.publish_artifact import (
    DouyinPublishArtifactRejected,
    open_publish_artifact,
)
from automation_tool.executor.action_gate import LocalActionHardPolicy
from automation_tool.executor.browser_use_safety import (
    SideEffectApproval,
    SideEffectConfirmationGate,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.publish_preflight import (
    DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION,
    DouyinPublishPreflight,
    DouyinPublishPreflightEvidence,
    DouyinPublishPreflightIntent,
    DouyinPublishPreflightReceipt,
    DouyinPublishPreflightRejected,
    DouyinPublishPreflightState,
)
from automation_tool.executor.rpa.douyin.publish_release import (
    DOUYIN_PUBLISH_CONFIRMATION_ACTION,
    DOUYIN_PUBLISH_RELEASE_FLOW_VERSION,
    DouyinPublishConfirmation,
    DouyinPublishRelease,
    DouyinPublishReleaseEvidence,
    DouyinPublishReleaseReceipt,
    DouyinPublishReleaseState,
    SystemPublishReleaseClock,
)
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    MAX_EXECUTOR_SEQUENCE,
    MessageId,
    PlatformSessionHealthEnvelope,
)
from automation_tool.protocol.json_object import decode_bounded_json_object
from automation_tool.protocol.safe_text import contains_control_or_bidi

MAX_PLATFORM_COMMAND_BYTES = 16 * 1024
DOUYIN_QR_LOGIN_FLOW_VERSION = "douyin.qr-login.v2"
DOUYIN_PUBLISH_PREFLIGHT_COMMAND = "douyin.publish.preflight"
DOUYIN_PUBLISH_RELEASE_COMMAND = "douyin.publish.release"
DOUYIN_PUBLISH_DISPATCH_COMMAND = "douyin.publish.dispatch"
PUBLISH_RELEASED_STATE = "publish_released"


class PlatformCommandRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Local platform command is rejected")


class PlatformCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    authentication_proof: Annotated[
        str, Field(alias="authenticationProof", min_length=50, max_length=50)
    ]
    artifact_path: Annotated[str, Field(min_length=1, max_length=4096)] | None = Field(
        default=None, alias="artifactPath"
    )
    command_id: MessageId = Field(alias="commandId")
    command_type: Literal[
        "douyin.login.open",
        "douyin.login.recheck",
        "douyin.logout.complete",
        "douyin.publish.dispatch",
        "douyin.publish.preflight",
        "douyin.publish.release",
    ] = Field(alias="commandType")
    confirmation_id: MessageId | None = Field(default=None, alias="confirmationId")
    description: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    executable_path: Annotated[str, Field(min_length=1, max_length=4096)] | None = Field(
        default=None, alias="executablePath"
    )
    headless: bool | None = None
    profile_directory: Annotated[str, Field(min_length=1, max_length=4096)] | None = Field(
        default=None, alias="profileDirectory"
    )
    protocol_version: Literal["1.0"] = Field(alias="protocolVersion")
    publish_job_id: MessageId | None = Field(default=None, alias="publishJobId")
    title: Annotated[str, Field(min_length=1, max_length=4096)] | None = None

    @field_validator("description", "title")
    @classmethod
    def require_safe_publish_text(cls, value: str | None) -> str | None:
        if value is not None and (contains_control_or_bidi(value) or not value.strip()):
            raise ValueError("invalid publish text")
        return value

    @field_validator("artifact_path", "executable_path", "profile_directory")
    @classmethod
    def require_safe_absolute_path_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value)
        if (
            contains_control_or_bidi(value)
            or not path.is_absolute()
            or path.parent == path
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise ValueError("invalid local path")
        return value

    @model_validator(mode="after")
    def require_command_specific_fields(self) -> PlatformCommand:
        paths = (self.executable_path, self.profile_directory, self.headless)
        content_fields = (self.artifact_path, self.description, self.title)
        if self.command_type == DOUYIN_PUBLISH_DISPATCH_COMMAND:
            # A dispatch acts on the pre-submit state the executor already
            # holds, so it must carry the job and the approval and nothing
            # that could contradict what was filled and confirmed.
            if self.publish_job_id is None or self.confirmation_id is None:
                raise ValueError("dispatch command requires a job and a confirmation")
            if paths != (None, None, None) or any(
                value is not None for value in content_fields
            ):
                raise ValueError("dispatch command must not restate publish content")
            return self
        if self.confirmation_id is not None:
            raise ValueError("only the dispatch command carries a confirmation")
        if self.command_type == DOUYIN_PUBLISH_PREFLIGHT_COMMAND:
            if self.publish_job_id is None or any(
                value is None for value in (*paths, *content_fields)
            ):
                raise ValueError("publish command requires browser identity and content")
            return self
        if self.publish_job_id is not None or any(
            value is not None for value in content_fields
        ):
            raise ValueError("only the publish command carries publish content")
        if self.command_type in {"douyin.logout.complete", DOUYIN_PUBLISH_RELEASE_COMMAND}:
            if paths != (None, None, None):
                raise ValueError("logout command must be path free")
        elif any(value is None for value in paths):
            raise ValueError("login command requires browser identity")
        return self

    def __repr__(self) -> str:
        return "PlatformCommand(<redacted>)"


@runtime_checkable
class PlatformCommandOperation(Protocol):
    def handle(self, command: PlatformCommand) -> str: ...

    def close(self) -> None: ...


@runtime_checkable
class BrowserSurfaceOwningOperation(PlatformCommandOperation, Protocol):
    """An operation that may hold the single operations browser between commands.

    The router must be able to reclaim that surface before a login or logout
    command, so the capability is part of the type contract instead of a
    runtime probe that silently degrades into the M2 deadlock.
    """

    def release_surface(self) -> None: ...


class _BinaryLineReader(Protocol):
    def readline(self, limit: int = -1) -> bytes: ...


class PlatformCommandWorker:
    def __init__(
        self,
        *,
        input_stream: BinaryIO,
        authenticator: LocalSessionAuthenticator,
        operation: PlatformCommandOperation,
        result_output: TextIO | None = None,
        result_writer: Callable[..., None] | None = None,
    ) -> None:
        if (
            not hasattr(input_stream, "readline")
            or not isinstance(authenticator, LocalSessionAuthenticator)
            or not isinstance(operation, PlatformCommandOperation)
            or (result_output is None) == (result_writer is None)
            or (result_output is not None and not hasattr(result_output, "write"))
            or (result_writer is not None and not callable(result_writer))
        ):
            raise PlatformCommandRejected
        self._input = input_stream
        self._authenticator = authenticator
        self._operation = operation
        self._result_output = result_output
        self._result_writer = result_writer

    def run(self, stop: threading.Event) -> None:
        if not isinstance(stop, threading.Event):
            raise PlatformCommandRejected
        try:
            while not stop.is_set():
                source = self._input.readline(MAX_PLATFORM_COMMAND_BYTES + 1)
                if source == b"":
                    break
                command = read_platform_command(_SingleLineStream(source), self._authenticator)
                state = self._operation.handle(command)
                if self._result_writer is not None:
                    self._result_writer(
                        command_id=str(command.command_id),
                        state=state,
                        command_type=command.command_type,
                    )
                else:
                    write_platform_command_result(
                        self._result_output,  # type: ignore[arg-type]
                        self._authenticator,
                        command_id=str(command.command_id),
                        state=state,
                        command_type=command.command_type,
                    )
        except PlatformCommandRejected:
            raise
        except Exception:
            raise PlatformCommandRejected from None
        finally:
            try:
                self._operation.close()
            except Exception:
                if not stop.is_set():
                    raise PlatformCommandRejected from None


class _HealthReporter(Protocol):
    def observe(
        self,
        window: BrowserWindow,
        *,
        sequence: int,
        recovered: bool,
    ) -> PlatformSessionHealthEnvelope: ...

    def record_logout(self, *, sequence: int) -> PlatformSessionHealthEnvelope: ...


class _LoginFlow(Protocol):
    def begin(self) -> object: ...

    def recheck(self) -> object: ...

    def active_window(self) -> BrowserWindow: ...

    def close(self) -> None: ...


class _Runtime(Protocol):
    def start(self, request: BrowserLaunchRequest) -> None: ...

    def close(self) -> None: ...


class DouyinLoginCommandOperation:
    """Own one thread-confined QR flow and queue its non-sensitive health facts."""

    def __init__(
        self,
        *,
        health_reporter: _HealthReporter,
        outbound: Queue[object],
        runtime_factory: Callable[[], _Runtime] = BrowserRuntime,
        flow_factory: Callable[[_Runtime], _LoginFlow] = DouyinQrLoginFlow,  # type: ignore[assignment]
        sequence_source: Callable[[], int] | None = None,
        browser_authority: BrowserLaunchAuthority | None = None,
    ) -> None:
        if (
            not hasattr(health_reporter, "observe")
            or not isinstance(outbound, Queue)
            or not callable(runtime_factory)
            or not callable(flow_factory)
            or (sequence_source is not None and not callable(sequence_source))
            or (
                browser_authority is not None
                and not isinstance(browser_authority, BrowserLaunchAuthority)
            )
        ):
            raise PlatformCommandRejected
        self._health_reporter = health_reporter
        self._outbound = outbound
        self._runtime_factory = runtime_factory
        self._flow_factory = flow_factory
        self._sequence_source = sequence_source or self._next_wall_sequence
        self._browser_authority = browser_authority or BrowserLaunchAuthority()
        self._last_sequence = 0
        self._runtime: _Runtime | None = None
        self._flow: _LoginFlow | None = None
        self._launch_identity: tuple[str, str, bool] | None = None
        self._browser_lease: BrowserLaunchLease | None = None

    def handle(self, command: PlatformCommand) -> str:
        if not isinstance(command, PlatformCommand):
            raise PlatformCommandRejected
        try:
            if command.command_type == "douyin.logout.complete":
                self._close_active()
                self._browser_authority.revoke()
                sequence = self._next_sequence()
                self._outbound.put(self._health_reporter.record_logout(sequence=sequence))
                return "logged_out"
            if (
                command.executable_path is None
                or command.profile_directory is None
                or command.headless is None
            ):
                raise ValueError
            identity = (
                command.executable_path,
                command.profile_directory,
                command.headless,
            )
            if command.command_type == "douyin.login.open":
                self._close_active()
                self._begin(identity)
                flow = self._flow
                if flow is None:
                    raise ValueError
                observation = flow.begin()
            elif command.command_type == "douyin.login.recheck":
                if self._flow is None:
                    self._begin(identity)
                    flow = self._flow
                    if flow is None:
                        raise ValueError
                    observation = flow.begin()
                else:
                    if identity != self._launch_identity:
                        raise ValueError
                    observation = self._flow.recheck()
            else:
                raise ValueError
            state: object = getattr(getattr(observation, "state", None), "value", None)
            if type(state) is not str or state not in {
                "login_required",
                "awaiting_scan",
                "awaiting_confirmation",
                "qr_expired",
                "healthy",
                "handoff_required",
                "unknown",
            }:
                raise ValueError
            sequence = self._next_sequence()
            flow = self._flow
            if flow is None:
                raise ValueError
            message = self._health_reporter.observe(
                flow.active_window(),
                sequence=sequence,
                recovered=state == DouyinQrLoginState.HEALTHY.value,
            )
            self._outbound.put(message)
            if state == DouyinQrLoginState.HEALTHY.value:
                self._close_active()
            return state
        except PlatformCommandRejected:
            raise
        except Exception:
            self._close_active(best_effort=True)
            raise PlatformCommandRejected from None

    def _next_sequence(self) -> int:
        sequence = self._sequence_source()
        if (
            type(sequence) is not int
            or not 1 <= sequence <= MAX_EXECUTOR_SEQUENCE
            or sequence <= self._last_sequence
        ):
            raise ValueError
        self._last_sequence = sequence
        return sequence

    def _begin(
        self,
        identity: tuple[str, str, bool],
    ) -> None:
        runtime = self._runtime_factory()
        executable_path, profile_directory, headless = identity
        request = BrowserLaunchRequest(
            executable_path=Path(executable_path),
            profile_directory=Path(profile_directory),
            headless=headless,
        )
        self._browser_authority.authorize(request)
        lease = self._browser_authority.acquire()
        try:
            runtime.start(lease.request)
            flow = self._flow_factory(runtime)
        except Exception:
            try:
                runtime.close()
            finally:
                lease.close()
            raise
        self._runtime = runtime
        self._flow = flow
        self._launch_identity = identity
        self._browser_lease = lease

    def _next_wall_sequence(self) -> int:
        return max(self._last_sequence + 1, time.time_ns() // 1_000)

    def _close_active(self, *, best_effort: bool = False) -> None:
        flow = self._flow
        runtime = self._runtime
        lease = self._browser_lease
        self._flow = None
        self._runtime = None
        self._launch_identity = None
        self._browser_lease = None
        failed = False
        if flow is not None:
            try:
                flow.close()
            except Exception:
                failed = True
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                failed = True
        if lease is not None:
            try:
                lease.close()
            except Exception:
                failed = True
        if failed and not best_effort:
            raise PlatformCommandRejected

    def close(self) -> None:
        self._close_active()


class PlatformCommandRouter:
    """Dispatch each authenticated command family to its owning operation."""

    def __init__(
        self,
        *,
        login: PlatformCommandOperation,
        publish: BrowserSurfaceOwningOperation,
    ) -> None:
        if not isinstance(login, PlatformCommandOperation) or not isinstance(
            publish, BrowserSurfaceOwningOperation
        ):
            raise PlatformCommandRejected
        self._login = login
        self._publish = publish

    def handle(self, command: PlatformCommand) -> str:
        if not isinstance(command, PlatformCommand):
            raise PlatformCommandRejected
        if command.command_type in _PUBLISH_COMMANDS:
            return self._publish.handle(command)
        # Login and logout own the operations browser next: a pre-submit hold is
        # reclaimed first, otherwise the single browser authority stays locked
        # and the user can never log out or log in again.
        self._publish.release_surface()
        return self._login.handle(command)

    def close(self) -> None:
        failure: Exception | None = None
        for operation in (self._publish, self._login):
            try:
                operation.close()
            except Exception as error:
                failure = error
        if failure is not None:
            raise PlatformCommandRejected

    def __repr__(self) -> str:
        return "PlatformCommandRouter(<redacted>)"


class DouyinPublishPreflightCommandOperation:
    """Own the lease-guarded publish preflight that always stops before submission."""

    def __init__(
        self,
        *,
        ledger: ExecutorLedger,
        runtime_factory: Callable[[], _Runtime] = BrowserRuntime,
        browser_authority: BrowserLaunchAuthority | None = None,
        surface_lease: BrowserSurfaceLeaseManager | None = None,
        publish_policy: LocalActionHardPolicy | None = None,
    ) -> None:
        if (
            not isinstance(ledger, ExecutorLedger)
            or not callable(runtime_factory)
            or (
                browser_authority is not None
                and not isinstance(browser_authority, BrowserLaunchAuthority)
            )
            or (
                surface_lease is not None
                and not isinstance(surface_lease, BrowserSurfaceLeaseManager)
            )
            or (
                publish_policy is not None
                and not isinstance(publish_policy, LocalActionHardPolicy)
            )
        ):
            raise PlatformCommandRejected
        self._runtime_factory = runtime_factory
        self._browser_authority = browser_authority or BrowserLaunchAuthority()
        self._surface_lease = surface_lease or BrowserSurfaceLeaseManager()
        self._ledger = ledger
        self._policy = publish_policy or DEFAULT_PUBLISH_HARD_POLICY
        self._confirmations = SideEffectConfirmationGate()
        self._clock = SystemPublishReleaseClock()
        self._runtime: _Runtime | None = None
        self._browser_lease: BrowserLaunchLease | None = None
        self._window: BrowserWindow | None = None
        self._latest: DouyinPublishPreflightReceipt | None = None
        self._latest_intent: DouyinPublishPreflightIntent | None = None
        self._latest_approval: SideEffectApproval | None = None
        self._latest_release: DouyinPublishReleaseReceipt | None = None

    def __repr__(self) -> str:
        return "DouyinPublishPreflightCommandOperation(<redacted>)"

    def latest_receipt(self) -> DouyinPublishPreflightReceipt | None:
        """The only observation point for the last preflight outcome.

        Every command that returns a state also leaves a receipt here, so a
        caller can always tell a rejected artifact from rejected content from a
        browser that would not start. PB-06 reads the pre-submit receipt from
        here to bind its confirmation; releasing the surface voids it, so a
        *ready* receipt never describes a page that no longer exists. Blocked
        and handoff receipts are kept for reporting after the browser is gone.
        """
        return self._latest

    def latest_approval(self) -> SideEffectApproval | None:
        """The critical-point summary awaiting the operator's answer, if any.

        A ready preflight presents the target account and the content digest
        here; PB-07 projects this to the App. It exists only while the filled
        page it describes exists, so it is voided alongside a ready receipt.
        """
        return self._latest_approval

    def latest_release(self) -> DouyinPublishReleaseReceipt | None:
        """The outcome of the last dispatch, including an uncertain one."""
        return self._latest_release

    def surface_lease(self) -> BrowserSurfaceLeaseManager:
        return self._surface_lease

    def release_surface(self) -> None:
        """Give the operations browser back, voiding only a dispatchable receipt.

        A *ready* receipt describes a filled form on a page that is about to be
        closed, so PB-06 must never see it again, and the approval and intent
        that describe the same page go with it. A blocked or handoff receipt is
        a reason, not a dispatch target, and stays available for reporting.
        """
        if self._latest is not None and self._latest.ready:
            self._latest = None
        self._latest_intent = None
        self._latest_approval = None
        self._close_active(best_effort=True)

    def handle(self, command: PlatformCommand) -> str:
        if (
            not isinstance(command, PlatformCommand)
            or command.command_type not in _PUBLISH_COMMANDS
        ):
            raise PlatformCommandRejected
        if command.command_type == DOUYIN_PUBLISH_RELEASE_COMMAND:
            self.release_surface()
            return PUBLISH_RELEASED_STATE
        if command.command_type == DOUYIN_PUBLISH_DISPATCH_COMMAND:
            return self._dispatch(command)
        self._latest = None
        self._latest_intent = None
        self._latest_approval = None
        self._latest_release = None
        try:
            if (
                command.executable_path is None
                or command.profile_directory is None
                or command.headless is None
                or command.artifact_path is None
                or command.title is None
                or command.description is None
                or command.publish_job_id is None
            ):
                raise ValueError
            try:
                artifact = open_publish_artifact(Path(command.artifact_path))
            except DouyinPublishArtifactRejected:
                return self._blocked(DouyinPublishPreflightEvidence.ARTIFACT_REJECTED)
            try:
                intent = DouyinPublishPreflightIntent(
                    artifact=artifact,
                    title=command.title,
                    description=command.description,
                )
            except DouyinPublishPreflightRejected:
                # The command model bounds are wider than the publish policy.
                return self._blocked(DouyinPublishPreflightEvidence.CONTENT_REJECTED)
            # A previous browser that cannot be closed must not block this command:
            # the user may have closed the visible operations window by hand.
            self._close_active(best_effort=True)
            try:
                # Refuse before starting a browser when another controller owns the surface.
                self._surface_lease.authorize_playwright_action()
                window = self._begin(
                    (command.executable_path, command.profile_directory, command.headless)
                )
            except (BrowserLaunchAuthorityRejected, SurfaceLeaseRejected) as error:
                # Another controller still owns the single operations browser
                # surface; the two causes need different user handling.
                return self._blocked(
                    DouyinPublishPreflightEvidence.BROWSER_BUSY
                    if isinstance(error, BrowserLaunchAuthorityRejected)
                    else DouyinPublishPreflightEvidence.SURFACE_NOT_OWNED
                )
            except Exception:
                # The operations browser would not start - a locked profile from a
                # browser that could not be closed is the common cause. This is a
                # receipt for the user, never a reason to terminate the executor.
                return self._blocked(DouyinPublishPreflightEvidence.BROWSER_UNAVAILABLE)
            self._window = window
            receipt = DouyinPublishPreflight(
                window=window,
                lease=self._surface_lease,
            ).run(intent)
            self._latest = receipt
            if receipt.ready:
                self._latest_intent = intent
                self._present_confirmation(receipt)
            if receipt.state is DouyinPublishPreflightState.BLOCKED:
                # A handoff keeps the visible window open: the user has to finish
                # the captcha, slider, risk check or login in it by hand.
                self._close_active(best_effort=True)
            return _PUBLISH_RESULT_FOR_STATE[receipt.state]
        except PlatformCommandRejected:
            raise
        except Exception:
            self._close_active(best_effort=True)
            raise PlatformCommandRejected from None

    def _present_confirmation(self, receipt: DouyinPublishPreflightReceipt) -> None:
        """Put the target account and content digest in front of the operator.

        An account this executor could not read leaves no approval behind, so
        the publish simply stays undispatchable: we do not ask anyone to
        approve sending a video to an account we cannot name.
        """
        if receipt.target_account is None or receipt.content_hash is None:
            return
        try:
            self._latest_approval = self._confirmations.present(
                action=DOUYIN_PUBLISH_CONFIRMATION_ACTION,
                target_account=receipt.target_account,
                content_hash=receipt.content_hash,
            )
        except Exception:
            self._latest_approval = None

    def _dispatch(self, command: PlatformCommand) -> str:
        """Spend one approval on one click against the page already held open.

        An approval that is gone - already spent, or voided when the surface
        went back to the user - is an ordinary outcome, not a protocol
        violation: the operator pressed publish a moment too late. It is
        reported as *not dispatched* rather than terminating the executor,
        which would hand anyone who can send one frame a way to kill it.
        """
        receipt = self._latest
        intent = self._latest_intent
        approval = self._latest_approval
        window = self._window
        if command.publish_job_id is None or command.confirmation_id is None:
            raise PlatformCommandRejected
        if (
            receipt is None
            or not receipt.ready
            or receipt.target_account is None
            or receipt.content_hash is None
            or intent is None
            or approval is None
            or window is None
            or not compare_digest(approval.confirmation_id, str(command.confirmation_id))
        ):
            return self._not_dispatched(str(command.publish_job_id))
        try:
            confirmation = DouyinPublishConfirmation(
                publish_job_id=str(command.publish_job_id),
                content_hash=receipt.content_hash,
                target_account=receipt.target_account,
                dispatch_token=self._confirmations.authorize_dispatch(
                    approval.confirmation_id,
                    confirmed=True,
                ),
            )
        except Exception:
            return self._not_dispatched(str(command.publish_job_id))
        # One approval, one click: the summary is spent whatever happens next.
        self._latest_approval = None
        try:
            release = DouyinPublishRelease(
                window=window,
                lease=self._surface_lease,
                ledger=self._ledger,
                clock=self._clock,
                policy=self._policy,
                confirmation_gate=self._confirmations,
            ).run(receipt=receipt, intent=intent, confirmation=confirmation)
        except Exception:
            raise PlatformCommandRejected from None
        self._latest_release = release
        if release.state is not DouyinPublishReleaseState.NOT_DISPATCHED:
            # The page was pressed: the filled form is no longer a dispatch
            # target, whatever the works list said about the outcome.
            self._latest = None
            self._latest_intent = None
        return PUBLISH_DISPATCH_RESULT_FOR_STATE[release.state]

    def _not_dispatched(self, publish_job_id: str) -> str:
        """Record and return one publish that never reached the button."""
        receipt = DouyinPublishReleaseReceipt(
            publish_job_id=publish_job_id,
            state=DouyinPublishReleaseState.NOT_DISPATCHED,
            evidence=DouyinPublishReleaseEvidence.STALE_CONFIRMATION,
            dispatch_state=None,
            dispatch_revision=None,
            replayed=False,
        )
        self._latest_release = receipt
        return PUBLISH_DISPATCH_RESULT_FOR_STATE[receipt.state]

    def _blocked(self, evidence: DouyinPublishPreflightEvidence) -> str:
        """Record and return one blocked outcome; a state always has a receipt."""
        receipt = _blocked_publish_receipt(evidence)
        self._latest = receipt
        return _PUBLISH_RESULT_FOR_STATE[receipt.state]

    def _begin(self, identity: tuple[str, str, bool]) -> BrowserWindow:
        executable_path, profile_directory, headless = identity
        request = BrowserLaunchRequest(
            executable_path=Path(executable_path),
            profile_directory=Path(profile_directory),
            headless=headless,
        )
        self._browser_authority.authorize(request)
        lease = self._browser_authority.acquire()
        runtime = self._runtime_factory()
        try:
            runtime.start(lease.request)
            window = cast(BrowserRuntime, runtime).primary_window()
        except Exception:
            try:
                runtime.close()
            finally:
                lease.close()
            raise
        self._runtime = runtime
        self._browser_lease = lease
        return window

    def _close_active(self, *, best_effort: bool = False) -> None:
        runtime = self._runtime
        lease = self._browser_lease
        self._runtime = None
        self._browser_lease = None
        self._window = None
        failed = False
        for closeable in (runtime, lease):
            if closeable is None:
                continue
            try:
                closeable.close()
            except Exception:
                failed = True
        if failed and not best_effort:
            raise PlatformCommandRejected

    def close(self) -> None:
        self._close_active()


_PUBLISH_RESULT_FOR_STATE = {
    DouyinPublishPreflightState.PRE_SUBMIT_READY: "publish_pre_submit_ready",
    DouyinPublishPreflightState.HANDOFF_REQUIRED: "publish_handoff_required",
    DouyinPublishPreflightState.BLOCKED: "publish_blocked",
}

PUBLISH_DISPATCH_RESULT_FOR_STATE = {
    DouyinPublishReleaseState.VERIFIED: "publish_verified",
    DouyinPublishReleaseState.OUTCOME_UNCERTAIN: "publish_outcome_uncertain",
    DouyinPublishReleaseState.NOT_DISPATCHED: "publish_not_dispatched",
}

# The local hard limits a publish runs under. They bind monotonically into the
# ledger, so an installation that already committed to something stricter for
# task actions keeps that stricter value here too.
DEFAULT_PUBLISH_HARD_POLICY = LocalActionHardPolicy(
    minimum_interval=timedelta(seconds=60),
    task_action_limit=100,
)


_PUBLISH_COMMANDS: Final = frozenset(
    {
        DOUYIN_PUBLISH_DISPATCH_COMMAND,
        DOUYIN_PUBLISH_PREFLIGHT_COMMAND,
        DOUYIN_PUBLISH_RELEASE_COMMAND,
    }
)


def _blocked_publish_receipt(
    evidence: DouyinPublishPreflightEvidence,
) -> DouyinPublishPreflightReceipt:
    return DouyinPublishPreflightReceipt(
        state=DouyinPublishPreflightState.BLOCKED,
        evidence=evidence,
    )


_FLOW_VERSION_BY_COMMAND: Final = MappingProxyType(
    {
        "douyin.login.open": DOUYIN_QR_LOGIN_FLOW_VERSION,
        "douyin.login.recheck": DOUYIN_QR_LOGIN_FLOW_VERSION,
        "douyin.logout.complete": "douyin.session-control.v1",
        DOUYIN_PUBLISH_DISPATCH_COMMAND: DOUYIN_PUBLISH_RELEASE_FLOW_VERSION,
        DOUYIN_PUBLISH_PREFLIGHT_COMMAND: DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION,
        DOUYIN_PUBLISH_RELEASE_COMMAND: DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION,
    }
)


def _flow_version_for_command(command_type: str) -> str:
    """Look up the flow contract owned by one command family.

    Deriving it from the result state instead only worked while every state
    belonged to exactly one family, and silently mislabels the frame as soon as
    two families share a state. A default here would rebuild that same defect
    on the command type, so an unregistered family is rejected: a new command
    family must register its flow contract in the same change that adds it.
    """
    flow_version = _FLOW_VERSION_BY_COMMAND.get(command_type) if type(command_type) is str else None
    if flow_version is None:
        raise PlatformCommandRejected
    return flow_version


class _SingleLineStream:
    def __init__(self, source: bytes) -> None:
        self._source = source

    def readline(self, _limit: int = -1) -> bytes:
        source = self._source
        self._source = b""
        return source


def read_platform_command(
    stream: _BinaryLineReader,
    authenticator: LocalSessionAuthenticator,
) -> PlatformCommand:
    try:
        if not isinstance(authenticator, LocalSessionAuthenticator):
            raise ValueError
        source = stream.readline(MAX_PLATFORM_COMMAND_BYTES + 1)
        if (
            type(source) is not bytes
            or not source.endswith(b"\n")
            or len(source) > MAX_PLATFORM_COMMAND_BYTES
        ):
            raise ValueError
        decoded = decode_bounded_json_object(source[:-1], maximum_bytes=MAX_PLATFORM_COMMAND_BYTES)
        command = PlatformCommand.model_validate(decoded)
        if command.command_type == DOUYIN_PUBLISH_DISPATCH_COMMAND:
            assert command.publish_job_id is not None
            assert command.confirmation_id is not None
            authenticator.verify_publish_dispatch_command(
                command_id=str(command.command_id),
                command_type=command.command_type,
                publish_job_id=str(command.publish_job_id),
                confirmation_id=str(command.confirmation_id),
                presented_proof=command.authentication_proof,
            )
        elif command.command_type == DOUYIN_PUBLISH_PREFLIGHT_COMMAND:
            assert command.executable_path is not None
            assert command.profile_directory is not None
            assert command.headless is not None
            assert command.publish_job_id is not None
            assert command.artifact_path is not None
            assert command.title is not None
            assert command.description is not None
            authenticator.verify_publish_command(
                command_id=str(command.command_id),
                command_type=command.command_type,
                executable_path=command.executable_path,
                profile_directory=command.profile_directory,
                headless=command.headless,
                publish_job_id=str(command.publish_job_id),
                artifact_path=command.artifact_path,
                title=command.title,
                description=command.description,
                presented_proof=command.authentication_proof,
            )
        elif command.command_type in {"douyin.logout.complete", DOUYIN_PUBLISH_RELEASE_COMMAND}:
            authenticator.verify_session_command(
                command_id=str(command.command_id),
                command_type=command.command_type,
                presented_proof=command.authentication_proof,
            )
        else:
            assert command.executable_path is not None
            assert command.profile_directory is not None
            assert command.headless is not None
            authenticator.verify_command(
                command_id=str(command.command_id),
                command_type=command.command_type,
                executable_path=command.executable_path,
                profile_directory=command.profile_directory,
                headless=command.headless,
                presented_proof=command.authentication_proof,
            )
        return command
    except (
        AttributeError,
        LocalSessionAuthenticationRejected,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        ValidationError,
    ):
        raise PlatformCommandRejected from None


def write_platform_command_result(
    output: TextIO,
    authenticator: LocalSessionAuthenticator,
    *,
    command_id: str,
    state: str,
    command_type: str,
) -> None:
    import json

    try:
        proof = authenticator.proof_for_command_result(
            command_id=command_id,
            state=state,
        )
        source = json.dumps(
            {
                "authenticationProof": proof,
                "commandId": command_id,
                "event": "platform.command.completed",
                "flowVersion": _flow_version_for_command(command_type),
                "platform": "douyin",
                "protocolVersion": EXECUTOR_PROTOCOL_VERSION,
                "state": state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(source.encode("utf-8")) > 4096:
            raise ValueError
        output.write(source + "\n")
        output.flush()
    except Exception:
        raise PlatformCommandRejected from None


__all__ = [
    "DEFAULT_PUBLISH_HARD_POLICY",
    "DOUYIN_PUBLISH_DISPATCH_COMMAND",
    "PUBLISH_DISPATCH_RESULT_FOR_STATE",
    "DOUYIN_PUBLISH_PREFLIGHT_COMMAND",
    "DOUYIN_PUBLISH_RELEASE_COMMAND",
    "DOUYIN_QR_LOGIN_FLOW_VERSION",
    "MAX_PLATFORM_COMMAND_BYTES",
    "PUBLISH_RELEASED_STATE",
    "BrowserSurfaceOwningOperation",
    "DouyinLoginCommandOperation",
    "DouyinPublishPreflightCommandOperation",
    "PlatformCommand",
    "PlatformCommandOperation",
    "PlatformCommandRejected",
    "PlatformCommandRouter",
    "PlatformCommandWorker",
    "read_platform_command",
    "write_platform_command_result",
]
