"""Closed, redacted value objects for durable external side-effect state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import RFC_4122, UUID

from automation_tool.protocol import DouyinSearchExposureAction


class SideEffectState(StrEnum):
    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    VERIFIED = "verified"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True, repr=False)
class LocalSideEffect:
    action_id: str
    target_id: str
    execution_attempt_id: str
    task_id: str
    installation_id: str
    executor_id: str
    platform: str
    action: DouyinSearchExposureAction
    idempotency_key: str
    effect_fingerprint: bytes
    state: SideEffectState
    prepared_at: datetime
    dispatched_at: datetime | None
    settled_at: datetime | None
    verification_fingerprint: bytes | None
    revision: int
    replayed: bool

    def __post_init__(self) -> None:
        prepared_at = _canonical_utc(self.prepared_at)
        dispatched_at = _canonical_utc(self.dispatched_at)
        settled_at = _canonical_utc(self.settled_at)
        identifiers = (
            self.action_id,
            self.target_id,
            self.execution_attempt_id,
            self.task_id,
            self.installation_id,
            self.executor_id,
        )
        prepared = (
            self.state is SideEffectState.PREPARED
            and self.revision == 1
            and dispatched_at is None
            and settled_at is None
            and self.verification_fingerprint is None
        )
        dispatched = (
            self.state is SideEffectState.DISPATCHED
            and self.revision == 2
            and dispatched_at is not None
            and settled_at is None
            and self.verification_fingerprint is None
        )
        verified = (
            self.state is SideEffectState.VERIFIED
            and self.revision == 3
            and dispatched_at is not None
            and settled_at is not None
            and type(self.verification_fingerprint) is bytes
            and len(self.verification_fingerprint) == 32
        )
        uncertain = (
            self.state is SideEffectState.UNCERTAIN
            and self.revision == 3
            and dispatched_at is not None
            and settled_at is not None
            and self.verification_fingerprint is None
        )
        if (
            any(not _canonical_uuid_v4(value) for value in identifiers)
            or self.platform != "douyin"
            or not (
                self.action is DouyinSearchExposureAction.COMMENT
                or self.action is DouyinSearchExposureAction.DIRECT_MESSAGE
            )
            or self.idempotency_key != f"action:{self.action_id}"
            or type(self.effect_fingerprint) is not bytes
            or len(self.effect_fingerprint) != 32
            or not isinstance(self.state, SideEffectState)
            or prepared_at is None
            or (dispatched_at is not None and dispatched_at < prepared_at)
            or (settled_at is not None and (dispatched_at is None or settled_at < dispatched_at))
            or not (prepared or dispatched or verified or uncertain)
            or type(self.replayed) is not bool
        ):
            raise _ledger_rejected()
        object.__setattr__(self, "prepared_at", prepared_at)
        object.__setattr__(self, "dispatched_at", dispatched_at)
        object.__setattr__(self, "settled_at", settled_at)

    def __repr__(self) -> str:
        return (
            f"LocalSideEffect(state={self.state.value!r}, revision={self.revision!r}, <redacted>)"
        )


def _canonical_uuid_v4(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except Exception:
        return False
    return parsed.version == 4 and parsed.variant == RFC_4122 and str(parsed) == value


def _canonical_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


def _ledger_rejected() -> Exception:
    from automation_tool.executor.ledger import ExecutorLedgerRejected

    return ExecutorLedgerRejected()


__all__ = ["LocalSideEffect", "SideEffectState"]
