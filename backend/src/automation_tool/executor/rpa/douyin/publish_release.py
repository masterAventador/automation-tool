"""PB-06: one operator-confirmed Douyin publish, pressed at most once.

PB-05 stops with a filled form and a receipt that names the target account and
digests the exact artifact and text. This module turns that into a post. It
refuses to press unless the operator's confirmation still matches that receipt
and the filled intent, unless this executor still owns the visible surface, and
unless the durable ledger grants this job its single dispatch.

What happened afterwards is never read from the submit page. The works list is
opened as separate evidence, and only a list that names this work exactly once
settles the dispatch as verified. A list that is missing it, names it twice or
cannot be read at all leaves the job ``outcome_uncertain`` for a human, because
the button was already pressed and no local fact can undo that.
"""

from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.action_gate import LocalActionHardPolicy
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.browser_surface_lease import (
    BrowserSurfaceLeaseManager,
    SurfaceLeaseRejected,
)
from automation_tool.executor.browser_use_safety import SideEffectConfirmationGate
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.publish_page import (
    DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION,
    DouyinPublishPage,
    DouyinPublishPageObservation,
    DouyinPublishPageState,
)
from automation_tool.executor.rpa.douyin.publish_preflight import (
    MAX_DOUYIN_PUBLISH_ACCOUNT_CHARACTERS,
    DouyinPublishPreflightIntent,
    DouyinPublishPreflightReceipt,
)
from automation_tool.executor.side_effect_ledger import LocalPublishDispatch, SideEffectState
from automation_tool.protocol.safe_text import (
    contains_control_or_bidi,
    is_sha256_hex,
    is_unsafe_text,
)

DOUYIN_PUBLISH_RELEASE_FLOW_VERSION: Final = "douyin.publish-release.v1"
DOUYIN_PUBLISH_CONFIRMATION_ACTION: Final = "douyin_publish"

_VERIFICATION_FINGERPRINT_DOMAIN: Final = b"automation-tool.douyin.publish-verification.v1\0"
_CLICK_TIMEOUT_MILLISECONDS: Final = 15_000
_WORKS_LIST_TIMEOUT_MILLISECONDS: Final = 30_000


class DouyinPublishReleaseRejected(RuntimeError):
    """The publish release cannot run through the frozen v1 contract."""

    def __init__(self) -> None:
        super().__init__("douyin publish release is unavailable")


class DouyinPublishReleaseState(StrEnum):
    NOT_DISPATCHED = "not_dispatched"
    VERIFIED = "verified"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class DouyinPublishReleaseEvidence(StrEnum):
    STALE_CONFIRMATION = "stale_confirmation"
    SURFACE_LOST = "surface_lost"
    LEDGER_UNAVAILABLE = "ledger_unavailable"
    FORM_NOT_READY = "form_not_ready"
    SUBMIT_CONTROL_DISABLED = "submit_control_disabled"
    PREPARE_UNAVAILABLE = "prepare_unavailable"
    DISPATCH_PERMISSION_REJECTED = "dispatch_permission_rejected"
    DISPATCH_TIMED_OUT = "dispatch_timed_out"
    DISPATCH_UNAVAILABLE = "dispatch_unavailable"
    WORKS_LIST_LOGIN_REQUIRED = "works_list_login_required"
    WORKS_LIST_RISK_CHALLENGE = "works_list_risk_challenge"
    WORKS_LIST_UNAVAILABLE = "works_list_unavailable"
    WORK_NOT_LISTED = "work_not_listed"
    WORK_LISTED_AMBIGUOUSLY = "work_listed_ambiguously"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"
    WORK_LISTED = "work_listed"
    REPLAY_VERIFIED = "replay_verified"
    REPLAY_UNCERTAIN = "replay_uncertain"


_NO_EFFECT_EVIDENCE: Final = frozenset(
    {
        DouyinPublishReleaseEvidence.STALE_CONFIRMATION,
        DouyinPublishReleaseEvidence.SURFACE_LOST,
        DouyinPublishReleaseEvidence.LEDGER_UNAVAILABLE,
    }
)
_PREPARED_EVIDENCE: Final = frozenset(
    {
        # The approval is spent late, after the cheap page checks, so a stale
        # confirmation can surface either before or after the job was recorded.
        DouyinPublishReleaseEvidence.STALE_CONFIRMATION,
        DouyinPublishReleaseEvidence.FORM_NOT_READY,
        DouyinPublishReleaseEvidence.SUBMIT_CONTROL_DISABLED,
        DouyinPublishReleaseEvidence.PREPARE_UNAVAILABLE,
        DouyinPublishReleaseEvidence.SURFACE_LOST,
        DouyinPublishReleaseEvidence.DISPATCH_PERMISSION_REJECTED,
    }
)
_POST_DISPATCH_EVIDENCE: Final = frozenset(
    {
        DouyinPublishReleaseEvidence.DISPATCH_TIMED_OUT,
        DouyinPublishReleaseEvidence.DISPATCH_UNAVAILABLE,
        DouyinPublishReleaseEvidence.WORKS_LIST_LOGIN_REQUIRED,
        DouyinPublishReleaseEvidence.WORKS_LIST_RISK_CHALLENGE,
        DouyinPublishReleaseEvidence.WORKS_LIST_UNAVAILABLE,
        DouyinPublishReleaseEvidence.WORK_NOT_LISTED,
        DouyinPublishReleaseEvidence.WORK_LISTED_AMBIGUOUSLY,
        DouyinPublishReleaseEvidence.VERIFICATION_UNAVAILABLE,
        DouyinPublishReleaseEvidence.REPLAY_UNCERTAIN,
    }
)
_WORKS_LIST_HANDOFF: Final = {
    DouyinPublishPageState.LOGIN_REQUIRED: (DouyinPublishReleaseEvidence.WORKS_LIST_LOGIN_REQUIRED),
    DouyinPublishPageState.RISK_CHALLENGE: (DouyinPublishReleaseEvidence.WORKS_LIST_RISK_CHALLENGE),
}


@dataclass(frozen=True, slots=True, repr=False)
class DouyinPublishConfirmation:
    """What the operator saw and agreed to at the publish critical point.

    The digest covers the artifact identity plus the exact title and body; the
    account name is carried separately because it is a page fact rather than
    something the operator supplied. Both have to still hold at dispatch time,
    otherwise the confirmation would be spent on something else.

    The token is what makes this a confirmation rather than a claim: only the
    critical-point gate mints one, only after the operator was shown that
    account and digest and answered yes, and it can be spent exactly once. An
    account this executor could not read is not confirmable at all, so there is
    no ``None`` here - a publish is never sent to an account we cannot name.
    """

    publish_job_id: str
    content_hash: str
    target_account: str
    dispatch_token: str

    def __post_init__(self) -> None:
        if (
            not _canonical_job_id(self.publish_job_id)
            or not is_sha256_hex(self.content_hash)
            or _unsafe_account(self.target_account)
            or not _canonical_dispatch_token(self.dispatch_token)
        ):
            raise DouyinPublishReleaseRejected

    def __repr__(self) -> str:
        return "DouyinPublishConfirmation(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinPublishReleaseReceipt:
    """The outcome of one publish release, including what is not yet known."""

    publish_job_id: str
    state: DouyinPublishReleaseState
    evidence: DouyinPublishReleaseEvidence
    dispatch_state: SideEffectState | None
    dispatch_revision: int | None
    replayed: bool
    flow_version: str = DOUYIN_PUBLISH_RELEASE_FLOW_VERSION
    selector_version: str = DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION

    def __post_init__(self) -> None:
        no_effect = (
            self.state is DouyinPublishReleaseState.NOT_DISPATCHED
            and self.evidence in _NO_EFFECT_EVIDENCE
            and self.dispatch_state is None
            and self.dispatch_revision is None
            and self.replayed is False
        )
        prepared = (
            self.state is DouyinPublishReleaseState.NOT_DISPATCHED
            and self.evidence in _PREPARED_EVIDENCE
            and self.dispatch_state is SideEffectState.PREPARED
            and self.dispatch_revision == 1
            and self.replayed is False
        )
        verified = (
            self.state is DouyinPublishReleaseState.VERIFIED
            and self.evidence
            in {
                DouyinPublishReleaseEvidence.WORK_LISTED,
                DouyinPublishReleaseEvidence.REPLAY_VERIFIED,
            }
            and self.dispatch_state is SideEffectState.VERIFIED
            and self.dispatch_revision == 3
            and self.replayed is (self.evidence is DouyinPublishReleaseEvidence.REPLAY_VERIFIED)
        )
        uncertain = (
            self.state is DouyinPublishReleaseState.OUTCOME_UNCERTAIN
            and self.evidence in _POST_DISPATCH_EVIDENCE
            and (
                (self.dispatch_state is SideEffectState.DISPATCHED and self.dispatch_revision == 2)
                or (
                    self.dispatch_state is SideEffectState.UNCERTAIN and self.dispatch_revision == 3
                )
            )
            and self.replayed is (self.evidence is DouyinPublishReleaseEvidence.REPLAY_UNCERTAIN)
        )
        if (
            not _canonical_job_id(self.publish_job_id)
            or not isinstance(self.state, DouyinPublishReleaseState)
            or not isinstance(self.evidence, DouyinPublishReleaseEvidence)
            or self.flow_version != DOUYIN_PUBLISH_RELEASE_FLOW_VERSION
            or self.selector_version != DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION
            or type(self.replayed) is not bool
            or not (no_effect or prepared or verified or uncertain)
        ):
            raise DouyinPublishReleaseRejected

    @property
    def published(self) -> bool:
        return self.state is DouyinPublishReleaseState.VERIFIED

    @property
    def circuit_open(self) -> bool:
        return not self.published

    def __repr__(self) -> str:
        dispatch_state = None if self.dispatch_state is None else self.dispatch_state.value
        return (
            "DouyinPublishReleaseReceipt("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"dispatch_state={dispatch_state!r}, "
            f"dispatch_revision={self.dispatch_revision!r}, replayed={self.replayed!r}, "
            f"flow_version={self.flow_version!r}, <redacted>)"
        )


@runtime_checkable
class DouyinPublishReleaseClock(Protocol):
    def now(self) -> datetime: ...


class SystemPublishReleaseClock:
    """The production clock; the ledger rejects anything not UTC-aware."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def __repr__(self) -> str:
        return "SystemPublishReleaseClock()"


class DouyinPublishRelease:
    """Press publish once for one confirmed job, then prove it independently."""

    def __init__(
        self,
        *,
        window: BrowserWindow,
        lease: BrowserSurfaceLeaseManager,
        ledger: ExecutorLedger,
        clock: DouyinPublishReleaseClock,
        policy: LocalActionHardPolicy,
        confirmation_gate: SideEffectConfirmationGate,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(lease, BrowserSurfaceLeaseManager)
            or not isinstance(ledger, ExecutorLedger)
            or not isinstance(clock, DouyinPublishReleaseClock)
            or not isinstance(policy, LocalActionHardPolicy)
            or not isinstance(confirmation_gate, SideEffectConfirmationGate)
        ):
            raise DouyinPublishReleaseRejected
        try:
            # The same durable, monotonically tightening local policy the task
            # actions bind: a publish can never run under a looser interval
            # than whatever this installation has already committed to.
            binding = ledger.bind_action_hard_policy(
                minimum_interval_seconds=int(policy.minimum_interval.total_seconds()),
                task_action_limit=policy.task_action_limit,
            )
        except Exception:
            raise DouyinPublishReleaseRejected from None
        self._page = DouyinPublishPage(window)
        self._lease = lease
        self._ledger = ledger
        self._clock = clock
        self._gate = confirmation_gate
        self._minimum_interval_seconds = binding.minimum_interval_seconds
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinPublishRelease(<redacted>)"

    def run(
        self,
        *,
        receipt: DouyinPublishPreflightReceipt,
        intent: DouyinPublishPreflightIntent,
        confirmation: DouyinPublishConfirmation,
    ) -> DouyinPublishReleaseReceipt:
        if (
            self._executed
            or not isinstance(receipt, DouyinPublishPreflightReceipt)
            or not receipt.ready
            or not isinstance(intent, DouyinPublishPreflightIntent)
            or not isinstance(confirmation, DouyinPublishConfirmation)
        ):
            raise DouyinPublishReleaseRejected
        self._executed = True
        job_id = confirmation.publish_job_id
        if not _confirmation_still_holds(receipt, intent, confirmation):
            return _empty_receipt(job_id, DouyinPublishReleaseEvidence.STALE_CONFIRMATION)
        if not self._surface_owned():
            return _empty_receipt(job_id, DouyinPublishReleaseEvidence.SURFACE_LOST)

        try:
            prepared = self._ledger.prepare_publish_dispatch(
                publish_job_id=job_id,
                content_hash=confirmation.content_hash,
                prepared_at=self._now(),
            )
        except Exception:
            return _empty_receipt(job_id, DouyinPublishReleaseEvidence.LEDGER_UNAVAILABLE)
        replay = _receipt_for_existing(job_id, prepared)
        if replay is not None:
            return replay

        form = self._page.observe()
        if form.state is not DouyinPublishPageState.FORM_READY:
            return _prepared_receipt(job_id, DouyinPublishReleaseEvidence.FORM_NOT_READY)
        try:
            armed = self._page.submit_enabled()
        except Exception:
            return _prepared_receipt(job_id, DouyinPublishReleaseEvidence.FORM_NOT_READY)
        if not armed:
            return _prepared_receipt(job_id, DouyinPublishReleaseEvidence.SUBMIT_CONTROL_DISABLED)
        try:
            submit = self._page.submit_control()
        except Exception:
            return _prepared_receipt(job_id, DouyinPublishReleaseEvidence.PREPARE_UNAVAILABLE)
        # Last check before the irreversible step: a surface handed to the user
        # between the confirmation and here must not be clicked behind them.
        if not self._surface_owned():
            return _prepared_receipt(job_id, DouyinPublishReleaseEvidence.SURFACE_LOST)

        try:
            # Spend the operator's approval before asking for the dispatch, so a
            # ledger refusal can never leave a still-spendable token behind.
            self._gate.consume_dispatch(
                confirmation.dispatch_token,
                content_hash=confirmation.content_hash,
            )
        except Exception:
            return _prepared_receipt(job_id, DouyinPublishReleaseEvidence.STALE_CONFIRMATION)

        try:
            dispatched = self._ledger.begin_publish_dispatch(
                publish_job_id=job_id,
                content_hash=confirmation.content_hash,
                dispatched_at=self._now(),
                minimum_interval_seconds=self._minimum_interval_seconds,
            )
        except Exception:
            return _prepared_receipt(
                job_id, DouyinPublishReleaseEvidence.DISPATCH_PERMISSION_REJECTED
            )
        replay = _receipt_for_existing(job_id, dispatched)
        if replay is not None:
            return replay

        try:
            submit.click(timeout=_CLICK_TIMEOUT_MILLISECONDS)
        except PlaywrightTimeoutError:
            return self._uncertain(
                job_id,
                confirmation,
                DouyinPublishReleaseEvidence.DISPATCH_TIMED_OUT,
                dispatched,
            )
        except Exception:
            return self._uncertain(
                job_id,
                confirmation,
                DouyinPublishReleaseEvidence.DISPATCH_UNAVAILABLE,
                dispatched,
            )

        return self._settle_against_works_list(job_id, intent, confirmation, dispatched)

    def _settle_against_works_list(
        self,
        job_id: str,
        intent: DouyinPublishPreflightIntent,
        confirmation: DouyinPublishConfirmation,
        dispatched: LocalPublishDispatch,
    ) -> DouyinPublishReleaseReceipt:
        try:
            works = self._page.open_works_list(
                timeout_milliseconds=_WORKS_LIST_TIMEOUT_MILLISECONDS
            )
        except Exception:
            return self._uncertain(
                job_id,
                confirmation,
                DouyinPublishReleaseEvidence.WORKS_LIST_UNAVAILABLE,
                dispatched,
            )
        if works.state is not DouyinPublishPageState.WORKS_LIST_READY:
            return self._uncertain(
                job_id,
                confirmation,
                _works_list_evidence(works),
                dispatched,
            )
        try:
            listed = self._page.works_titled(intent.title)
        except Exception:
            return self._uncertain(
                job_id,
                confirmation,
                DouyinPublishReleaseEvidence.WORKS_LIST_UNAVAILABLE,
                dispatched,
            )
        if listed != 1:
            return self._uncertain(
                job_id,
                confirmation,
                DouyinPublishReleaseEvidence.WORK_NOT_LISTED
                if listed == 0
                else DouyinPublishReleaseEvidence.WORK_LISTED_AMBIGUOUSLY,
                dispatched,
            )
        try:
            verified = self._ledger.verify_publish_dispatch(
                publish_job_id=job_id,
                content_hash=confirmation.content_hash,
                verification_fingerprint=publish_verification_fingerprint(
                    confirmation.content_hash
                ),
                verified_at=self._now(),
            )
        except Exception:
            return self._uncertain(
                job_id,
                confirmation,
                DouyinPublishReleaseEvidence.VERIFICATION_UNAVAILABLE,
                dispatched,
            )
        return _dispatch_receipt(
            job_id,
            DouyinPublishReleaseState.VERIFIED,
            DouyinPublishReleaseEvidence.WORK_LISTED,
            verified,
            replayed=False,
        )

    def _uncertain(
        self,
        job_id: str,
        confirmation: DouyinPublishConfirmation,
        evidence: DouyinPublishReleaseEvidence,
        dispatched: LocalPublishDispatch,
    ) -> DouyinPublishReleaseReceipt:
        settled = dispatched
        with suppress(Exception):
            settled = self._ledger.mark_publish_dispatch_uncertain(
                publish_job_id=job_id,
                content_hash=confirmation.content_hash,
                uncertain_at=self._now(),
            )
        return _dispatch_receipt(
            job_id,
            DouyinPublishReleaseState.OUTCOME_UNCERTAIN,
            evidence,
            settled,
            replayed=False,
        )

    def _surface_owned(self) -> bool:
        try:
            self._lease.authorize_playwright_action()
        except SurfaceLeaseRejected:
            return False
        return True

    def _now(self) -> datetime:
        try:
            value = self._clock.now()
            if (
                type(value) is not datetime
                or value.tzinfo is None
                or value.utcoffset() != timedelta(0)
            ):
                raise ValueError
            return value.astimezone(UTC)
        except Exception:
            raise DouyinPublishReleaseRejected from None


def publish_verification_fingerprint(content_hash: str) -> bytes:
    """Bind the settled evidence to the confirmed content and the anchor version."""
    if not is_sha256_hex(content_hash):
        raise DouyinPublishReleaseRejected
    return hashlib.sha256(
        _VERIFICATION_FINGERPRINT_DOMAIN
        + content_hash.encode("ascii")
        + b"\0"
        + DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION.encode("ascii")
        + b"\0work_listed_once"
    ).digest()


def _confirmation_still_holds(
    receipt: DouyinPublishPreflightReceipt,
    intent: DouyinPublishPreflightIntent,
    confirmation: DouyinPublishConfirmation,
) -> bool:
    """The receipt, the filled content and the confirmation must be one thing."""
    return (
        receipt.content_hash == intent.content_hash
        and confirmation.content_hash == receipt.content_hash
        and confirmation.target_account == receipt.target_account
    )


def _works_list_evidence(
    observation: DouyinPublishPageObservation,
) -> DouyinPublishReleaseEvidence:
    return _WORKS_LIST_HANDOFF.get(
        observation.state, DouyinPublishReleaseEvidence.WORKS_LIST_UNAVAILABLE
    )


def _receipt_for_existing(
    job_id: str,
    dispatch: LocalPublishDispatch,
) -> DouyinPublishReleaseReceipt | None:
    if not dispatch.replayed or dispatch.state is SideEffectState.PREPARED:
        return None
    if dispatch.state is SideEffectState.VERIFIED:
        return _dispatch_receipt(
            job_id,
            DouyinPublishReleaseState.VERIFIED,
            DouyinPublishReleaseEvidence.REPLAY_VERIFIED,
            dispatch,
            replayed=True,
        )
    return _dispatch_receipt(
        job_id,
        DouyinPublishReleaseState.OUTCOME_UNCERTAIN,
        DouyinPublishReleaseEvidence.REPLAY_UNCERTAIN,
        dispatch,
        replayed=True,
    )


def _empty_receipt(
    job_id: str,
    evidence: DouyinPublishReleaseEvidence,
) -> DouyinPublishReleaseReceipt:
    return DouyinPublishReleaseReceipt(
        publish_job_id=job_id,
        state=DouyinPublishReleaseState.NOT_DISPATCHED,
        evidence=evidence,
        dispatch_state=None,
        dispatch_revision=None,
        replayed=False,
    )


def _prepared_receipt(
    job_id: str,
    evidence: DouyinPublishReleaseEvidence,
) -> DouyinPublishReleaseReceipt:
    return DouyinPublishReleaseReceipt(
        publish_job_id=job_id,
        state=DouyinPublishReleaseState.NOT_DISPATCHED,
        evidence=evidence,
        dispatch_state=SideEffectState.PREPARED,
        dispatch_revision=1,
        replayed=False,
    )


def _dispatch_receipt(
    job_id: str,
    state: DouyinPublishReleaseState,
    evidence: DouyinPublishReleaseEvidence,
    dispatch: LocalPublishDispatch,
    *,
    replayed: bool,
) -> DouyinPublishReleaseReceipt:
    return DouyinPublishReleaseReceipt(
        publish_job_id=job_id,
        state=state,
        evidence=evidence,
        dispatch_state=dispatch.state,
        dispatch_revision=dispatch.revision,
        replayed=replayed,
    )


def _canonical_dispatch_token(value: object) -> bool:
    """The exact shape `SideEffectConfirmationGate` mints, nothing wider."""
    return is_sha256_hex(value)


def _canonical_job_id(value: object) -> bool:
    from uuid import RFC_4122, UUID

    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except Exception:
        return False
    return parsed.version == 4 and parsed.variant == RFC_4122 and str(parsed) == value


def _unsafe_account(value: object) -> bool:
    return (
        type(value) is not str
        or not value.strip()
        or contains_control_or_bidi(value)
        or is_unsafe_text(value, maximum_characters=MAX_DOUYIN_PUBLISH_ACCOUNT_CHARACTERS)
    )


__all__ = [
    "DOUYIN_PUBLISH_CONFIRMATION_ACTION",
    "DOUYIN_PUBLISH_RELEASE_FLOW_VERSION",
    "DouyinPublishConfirmation",
    "DouyinPublishRelease",
    "DouyinPublishReleaseClock",
    "DouyinPublishReleaseEvidence",
    "DouyinPublishReleaseReceipt",
    "DouyinPublishReleaseRejected",
    "DouyinPublishReleaseState",
    "SystemPublishReleaseClock",
    "publish_verification_fingerprint",
]
