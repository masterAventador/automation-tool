"""Read-only reconciliation for dispatched Douyin comment and message effects."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, cast, runtime_checkable

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.comment_action import (
    comment_action_verification_fingerprint,
)
from automation_tool.executor.rpa.douyin.comment_page import (
    DouyinCommentPage,
    DouyinCommentPageEvidence,
    DouyinCommentPageObservation,
    DouyinCommentPageState,
)
from automation_tool.executor.rpa.douyin.direct_message_action import (
    direct_message_action_verification_fingerprint,
)
from automation_tool.executor.rpa.douyin.direct_message_page import (
    DouyinDirectMessagePage,
    DouyinDirectMessagePageEvidence,
    DouyinDirectMessagePageObservation,
    DouyinDirectMessagePageState,
)
from automation_tool.executor.side_effect_ledger import LocalSideEffect, SideEffectState
from automation_tool.protocol import (
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolTargetId,
)

DOUYIN_SIDE_EFFECT_RECOVERY_VERSION = "douyin.side-effect-recovery.v1"
_FINAL_TIMEOUT_MILLISECONDS = 10_000


class DouyinSideEffectRecoveryRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin side-effect recovery is unavailable")


class DouyinSideEffectRecoveryState(StrEnum):
    NOT_DISPATCHED = "not_dispatched"
    VERIFIED = "verified"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class DouyinSideEffectRecoveryEvidence(StrEnum):
    PREPARED_NOT_DISPATCHED = "prepared_not_dispatched"
    ALREADY_VERIFIED = "already_verified"
    ALREADY_UNCERTAIN = "already_uncertain"
    COMMENT_CONFIRMED = "comment_confirmed"
    MESSAGE_CONFIRMED = "message_confirmed"
    LOGIN_REQUIRED = "login_required"
    DIALOG_BLOCKED = "dialog_blocked"
    MESSAGING_NOT_ALLOWED = "messaging_not_allowed"
    FOLLOW_REQUIRED = "follow_required"
    FINAL_TIMED_OUT = "final_timed_out"
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    PAGE_UNAVAILABLE = "page_unavailable"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"


_UNCONFIRMED_EVIDENCE = frozenset(
    {
        DouyinSideEffectRecoveryEvidence.LOGIN_REQUIRED,
        DouyinSideEffectRecoveryEvidence.DIALOG_BLOCKED,
        DouyinSideEffectRecoveryEvidence.MESSAGING_NOT_ALLOWED,
        DouyinSideEffectRecoveryEvidence.FOLLOW_REQUIRED,
        DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT,
        DouyinSideEffectRecoveryEvidence.PAGE_VERSION_UNKNOWN,
        DouyinSideEffectRecoveryEvidence.CONFLICTING_ANCHORS,
        DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE,
        DouyinSideEffectRecoveryEvidence.VERIFICATION_UNAVAILABLE,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinSideEffectRecoveryReceipt:
    action_id: ProtocolActionId
    target_id: ProtocolTargetId
    action: DouyinSearchExposureAction
    state: DouyinSideEffectRecoveryState
    evidence: DouyinSideEffectRecoveryEvidence
    side_effect_state: SideEffectState
    side_effect_revision: int
    replayed: bool
    recovery_version: str = DOUYIN_SIDE_EFFECT_RECOVERY_VERSION

    def __post_init__(self) -> None:
        prepared = (
            self.state is DouyinSideEffectRecoveryState.NOT_DISPATCHED
            and self.evidence is DouyinSideEffectRecoveryEvidence.PREPARED_NOT_DISPATCHED
            and self.side_effect_state is SideEffectState.PREPARED
            and self.side_effect_revision == 1
            and self.replayed is False
        )
        newly_verified = (
            self.state is DouyinSideEffectRecoveryState.VERIFIED
            and self.evidence
            in {
                DouyinSideEffectRecoveryEvidence.COMMENT_CONFIRMED,
                DouyinSideEffectRecoveryEvidence.MESSAGE_CONFIRMED,
            }
            and self.side_effect_state is SideEffectState.VERIFIED
            and self.side_effect_revision == 3
            and self.replayed is False
        )
        already_verified = (
            self.state is DouyinSideEffectRecoveryState.VERIFIED
            and self.evidence is DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED
            and self.side_effect_state is SideEffectState.VERIFIED
            and self.side_effect_revision == 3
            and self.replayed is True
        )
        newly_uncertain = (
            self.state is DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN
            and self.evidence in _UNCONFIRMED_EVIDENCE
            and (
                (
                    self.side_effect_state is SideEffectState.DISPATCHED
                    and self.side_effect_revision == 2
                )
                or (
                    self.side_effect_state is SideEffectState.UNCERTAIN
                    and self.side_effect_revision == 3
                )
            )
            and self.replayed is False
        )
        already_uncertain = (
            self.state is DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN
            and self.evidence is DouyinSideEffectRecoveryEvidence.ALREADY_UNCERTAIN
            and self.side_effect_state is SideEffectState.UNCERTAIN
            and self.side_effect_revision == 3
            and self.replayed is True
        )
        if (
            type(self.action_id) is not ProtocolActionId
            or type(self.target_id) is not ProtocolTargetId
            or self.action
            not in {
                DouyinSearchExposureAction.COMMENT,
                DouyinSearchExposureAction.DIRECT_MESSAGE,
            }
            or not isinstance(self.state, DouyinSideEffectRecoveryState)
            or not isinstance(self.evidence, DouyinSideEffectRecoveryEvidence)
            or not isinstance(self.side_effect_state, SideEffectState)
            or type(self.side_effect_revision) is not int
            or type(self.replayed) is not bool
            or self.recovery_version != DOUYIN_SIDE_EFFECT_RECOVERY_VERSION
            or not (
                prepared
                or newly_verified
                or already_verified
                or newly_uncertain
                or already_uncertain
            )
        ):
            raise DouyinSideEffectRecoveryRejected

    @property
    def completed(self) -> bool:
        return self.state is DouyinSideEffectRecoveryState.VERIFIED

    @property
    def circuit_open(self) -> bool:
        return not self.completed

    def __repr__(self) -> str:
        return (
            "DouyinSideEffectRecoveryReceipt("
            f"action={self.action.value!r}, state={self.state.value!r}, "
            f"evidence={self.evidence.value!r}, "
            f"side_effect_state={self.side_effect_state.value!r}, "
            f"side_effect_revision={self.side_effect_revision!r}, "
            f"replayed={self.replayed!r}, recovery_version={self.recovery_version!r}, "
            "<redacted>)"
        )


@runtime_checkable
class DouyinSideEffectRecoveryClock(Protocol):
    def now(self) -> datetime: ...


class DouyinSideEffectRecovery:
    """Inspect final page facts without ever redispatching an external effect."""

    def __init__(
        self,
        *,
        window: BrowserWindow,
        ledger: ExecutorLedger,
        clock: DouyinSideEffectRecoveryClock,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(ledger, ExecutorLedger)
            or not isinstance(clock, DouyinSideEffectRecoveryClock)
        ):
            raise DouyinSideEffectRecoveryRejected
        self._window = window
        self._ledger = ledger
        self._clock = clock
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinSideEffectRecovery(<redacted>)"

    def run(self, *, action_id: ProtocolActionId) -> DouyinSideEffectRecoveryReceipt:
        if self._executed or type(action_id) is not ProtocolActionId:
            raise DouyinSideEffectRecoveryRejected
        self._executed = True
        try:
            effect = self._ledger.get_side_effect(str(action_id))
        except Exception:
            raise DouyinSideEffectRecoveryRejected from None
        if effect is None:
            raise DouyinSideEffectRecoveryRejected
        if effect.state is SideEffectState.PREPARED:
            return _receipt(
                effect,
                DouyinSideEffectRecoveryState.NOT_DISPATCHED,
                DouyinSideEffectRecoveryEvidence.PREPARED_NOT_DISPATCHED,
                replayed=False,
            )
        existing = _existing_receipt(effect)
        if existing is not None:
            return existing
        if effect.action is DouyinSearchExposureAction.COMMENT:
            return self._recover_comment(effect)
        return self._recover_message(effect)

    def _recover_comment(self, effect: LocalSideEffect) -> DouyinSideEffectRecoveryReceipt:
        try:
            page = DouyinCommentPage(self._window)
            observation = page.wait_for_final(timeout_milliseconds=_FINAL_TIMEOUT_MILLISECONDS)
        except Exception:
            return self._settle_uncertain(effect, DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE)
        failure = _comment_failure(observation)
        if failure is not None:
            return self._settle_uncertain(effect, failure)
        try:
            page.final_confirmation()
        except Exception:
            return self._settle_uncertain(
                effect, DouyinSideEffectRecoveryEvidence.VERIFICATION_UNAVAILABLE
            )
        return self._verify(
            effect,
            comment_action_verification_fingerprint(effect.effect_fingerprint),
            DouyinSideEffectRecoveryEvidence.COMMENT_CONFIRMED,
        )

    def _recover_message(self, effect: LocalSideEffect) -> DouyinSideEffectRecoveryReceipt:
        try:
            page = DouyinDirectMessagePage(self._window)
            observation = page.wait_for_final(timeout_milliseconds=_FINAL_TIMEOUT_MILLISECONDS)
        except Exception:
            return self._settle_uncertain(effect, DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE)
        failure = _message_failure(observation)
        if failure is not None:
            return self._settle_uncertain(effect, failure)
        try:
            page.final_confirmation()
        except Exception:
            return self._settle_uncertain(
                effect, DouyinSideEffectRecoveryEvidence.VERIFICATION_UNAVAILABLE
            )
        return self._verify(
            effect,
            direct_message_action_verification_fingerprint(effect.effect_fingerprint),
            DouyinSideEffectRecoveryEvidence.MESSAGE_CONFIRMED,
        )

    def _verify(
        self,
        effect: LocalSideEffect,
        verification_fingerprint: bytes,
        evidence: DouyinSideEffectRecoveryEvidence,
    ) -> DouyinSideEffectRecoveryReceipt:
        try:
            verified = self._ledger.verify_side_effect(
                action_id=effect.action_id,
                effect_fingerprint=effect.effect_fingerprint,
                verification_fingerprint=verification_fingerprint,
                verified_at=self._now(),
            )
        except Exception:
            current = self._current(effect)
            existing = _existing_receipt(current)
            if existing is not None:
                return existing
            return self._settle_uncertain(
                current, DouyinSideEffectRecoveryEvidence.VERIFICATION_UNAVAILABLE
            )
        if verified.replayed:
            return _receipt(
                verified,
                DouyinSideEffectRecoveryState.VERIFIED,
                DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED,
                replayed=True,
            )
        return _receipt(
            verified,
            DouyinSideEffectRecoveryState.VERIFIED,
            evidence,
            replayed=False,
        )

    def _settle_uncertain(
        self,
        effect: LocalSideEffect,
        evidence: DouyinSideEffectRecoveryEvidence,
    ) -> DouyinSideEffectRecoveryReceipt:
        try:
            settled = self._ledger.mark_side_effect_uncertain(
                action_id=effect.action_id,
                effect_fingerprint=effect.effect_fingerprint,
                uncertain_at=self._now(),
            )
        except Exception:
            settled = self._current(effect)
            existing = _existing_receipt(settled)
            if existing is not None:
                return existing
        else:
            if settled.replayed:
                return cast(DouyinSideEffectRecoveryReceipt, _existing_receipt(settled))
        return _receipt(
            settled,
            DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN,
            evidence,
            replayed=False,
        )

    def _current(self, fallback: LocalSideEffect) -> LocalSideEffect:
        with suppress(Exception):
            current = self._ledger.get_side_effect(fallback.action_id)
            if current is not None:
                return current
        return fallback

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
            raise DouyinSideEffectRecoveryRejected from None


def _comment_failure(
    observation: DouyinCommentPageObservation,
) -> DouyinSideEffectRecoveryEvidence | None:
    if observation.state is DouyinCommentPageState.CONFIRMED:
        return None
    if observation.state is DouyinCommentPageState.LOGIN_REQUIRED:
        return DouyinSideEffectRecoveryEvidence.LOGIN_REQUIRED
    if observation.state is DouyinCommentPageState.DIALOG_BLOCKED:
        return DouyinSideEffectRecoveryEvidence.DIALOG_BLOCKED
    if observation.state is DouyinCommentPageState.READY:
        return DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT
    return {
        DouyinCommentPageEvidence.REQUIRED_ANCHOR_MISSING: (
            DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT
        ),
        DouyinCommentPageEvidence.PAGE_VERSION_UNKNOWN: (
            DouyinSideEffectRecoveryEvidence.PAGE_VERSION_UNKNOWN
        ),
        DouyinCommentPageEvidence.CONFLICTING_ANCHORS: (
            DouyinSideEffectRecoveryEvidence.CONFLICTING_ANCHORS
        ),
        DouyinCommentPageEvidence.PAGE_UNAVAILABLE: (
            DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE
        ),
    }.get(observation.evidence, DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE)


def _message_failure(
    observation: DouyinDirectMessagePageObservation,
) -> DouyinSideEffectRecoveryEvidence | None:
    if observation.state is DouyinDirectMessagePageState.CONFIRMED:
        return None
    if observation.state is DouyinDirectMessagePageState.LOGIN_REQUIRED:
        return DouyinSideEffectRecoveryEvidence.LOGIN_REQUIRED
    if observation.state is DouyinDirectMessagePageState.DIALOG_BLOCKED:
        return DouyinSideEffectRecoveryEvidence.DIALOG_BLOCKED
    if observation.state is DouyinDirectMessagePageState.PERMISSION_DENIED:
        return (
            DouyinSideEffectRecoveryEvidence.FOLLOW_REQUIRED
            if observation.evidence is DouyinDirectMessagePageEvidence.FOLLOW_REQUIRED
            else DouyinSideEffectRecoveryEvidence.MESSAGING_NOT_ALLOWED
        )
    if observation.state in {
        DouyinDirectMessagePageState.PROFILE_READY,
        DouyinDirectMessagePageState.CONVERSATION_READY,
    }:
        return DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT
    return {
        DouyinDirectMessagePageEvidence.REQUIRED_ANCHOR_MISSING: (
            DouyinSideEffectRecoveryEvidence.FINAL_TIMED_OUT
        ),
        DouyinDirectMessagePageEvidence.PAGE_VERSION_UNKNOWN: (
            DouyinSideEffectRecoveryEvidence.PAGE_VERSION_UNKNOWN
        ),
        DouyinDirectMessagePageEvidence.CONFLICTING_ANCHORS: (
            DouyinSideEffectRecoveryEvidence.CONFLICTING_ANCHORS
        ),
        DouyinDirectMessagePageEvidence.PAGE_UNAVAILABLE: (
            DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE
        ),
    }.get(observation.evidence, DouyinSideEffectRecoveryEvidence.PAGE_UNAVAILABLE)


def _existing_receipt(
    effect: LocalSideEffect,
) -> DouyinSideEffectRecoveryReceipt | None:
    if effect.state is SideEffectState.VERIFIED:
        return _receipt(
            effect,
            DouyinSideEffectRecoveryState.VERIFIED,
            DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED,
            replayed=True,
        )
    if effect.state is SideEffectState.UNCERTAIN:
        return _receipt(
            effect,
            DouyinSideEffectRecoveryState.OUTCOME_UNCERTAIN,
            DouyinSideEffectRecoveryEvidence.ALREADY_UNCERTAIN,
            replayed=True,
        )
    return None


def _receipt(
    effect: LocalSideEffect,
    state: DouyinSideEffectRecoveryState,
    evidence: DouyinSideEffectRecoveryEvidence,
    *,
    replayed: bool,
) -> DouyinSideEffectRecoveryReceipt:
    return DouyinSideEffectRecoveryReceipt(
        action_id=ProtocolActionId(effect.action_id),
        target_id=ProtocolTargetId(effect.target_id),
        action=effect.action,
        state=state,
        evidence=evidence,
        side_effect_state=effect.state,
        side_effect_revision=effect.revision,
        replayed=replayed,
    )


__all__ = [
    "DOUYIN_SIDE_EFFECT_RECOVERY_VERSION",
    "DouyinSideEffectRecovery",
    "DouyinSideEffectRecoveryClock",
    "DouyinSideEffectRecoveryEvidence",
    "DouyinSideEffectRecoveryReceipt",
    "DouyinSideEffectRecoveryRejected",
    "DouyinSideEffectRecoveryState",
]
