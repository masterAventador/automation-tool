"""Durable non-sensitive Douyin Session health reporting."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger, PlatformSessionState
from automation_tool.executor.rpa.douyin.session import DouyinSessionDetector
from automation_tool.protocol import (
    EXECUTOR_PROTOCOL_VERSION,
    MAX_EXECUTOR_SEQUENCE,
    PlatformSessionHealthEnvelope,
)

_MESSAGE_DEADLINE = timedelta(seconds=30)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class DouyinSessionHealthReportRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin session health report is unavailable")


@runtime_checkable
class SessionHealthClock(Protocol):
    def now(self) -> datetime: ...


class SystemSessionHealthClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class DouyinSessionHealthReporter:
    """Turn one real page fact into a persisted epoch and typed wire message."""

    def __init__(
        self,
        *,
        ledger: ExecutorLedger,
        clock: SessionHealthClock | None = None,
        id_source: Callable[[], object] = uuid4,
    ) -> None:
        resolved_clock = SystemSessionHealthClock() if clock is None else clock
        if (
            not isinstance(ledger, ExecutorLedger)
            or not isinstance(resolved_clock, SessionHealthClock)
            or not callable(id_source)
        ):
            raise DouyinSessionHealthReportRejected
        self._ledger = ledger
        self._clock = resolved_clock
        self._id_source = id_source
        self._detector = DouyinSessionDetector()

    def observe(
        self,
        window: BrowserWindow,
        *,
        sequence: int,
        recovered: bool = False,
    ) -> PlatformSessionHealthEnvelope:
        try:
            if (
                not isinstance(window, BrowserWindow)
                or type(sequence) is not int
                or not 1 <= sequence <= MAX_EXECUTOR_SEQUENCE
                or type(recovered) is not bool
            ):
                raise ValueError
            observation = self._detector.check(window)
            state = PlatformSessionState(observation.state.value)
            return self._record(state=state, sequence=sequence, advance_epoch=recovered)
        except Exception:
            raise DouyinSessionHealthReportRejected from None

    def record_logout(self, *, sequence: int) -> PlatformSessionHealthEnvelope:
        try:
            if type(sequence) is not int or not 1 <= sequence <= MAX_EXECUTOR_SEQUENCE:
                raise ValueError
            return self._record(
                state=PlatformSessionState.MISSING,
                sequence=sequence,
                advance_epoch=True,
            )
        except Exception:
            raise DouyinSessionHealthReportRejected from None

    def _record(
        self,
        *,
        state: PlatformSessionState,
        sequence: int,
        advance_epoch: bool,
    ) -> PlatformSessionHealthEnvelope:
        observed_at = self._now()
        message_id = self._new_id()
        correlation_id = self._new_id()
        persisted = self._ledger.record_platform_session(
            platform="douyin",
            state=state,
            observed_at=observed_at,
            advance_epoch=advance_epoch,
        )
        return PlatformSessionHealthEnvelope.model_validate(
            {
                "protocol_version": EXECUTOR_PROTOCOL_VERSION,
                "message_id": message_id,
                "message_type": "platform.session_health",
                "sent_at": observed_at,
                "deadline_at": observed_at + _MESSAGE_DEADLINE,
                "installation_id": self._ledger.installation_id,
                "executor_id": self._ledger.executor_id,
                "correlation_id": correlation_id,
                "idempotency_key": (
                    "platform:douyin:session:"
                    f"{persisted.session_revision}:{_utc_microseconds(observed_at)}"
                ),
                "sequence": sequence,
                "payload": {
                    "platform": persisted.platform,
                    "state": persisted.state,
                    "session_revision": persisted.session_revision,
                    "observed_at": persisted.observed_at,
                },
            }
        )

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC)

    def _new_id(self) -> str:
        value = self._id_source()
        if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
            raise ValueError
        return str(value)


def _utc_microseconds(value: datetime) -> int:
    delta = value - _UNIX_EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


__all__ = [
    "DouyinSessionHealthReportRejected",
    "DouyinSessionHealthReporter",
    "SessionHealthClock",
    "SystemSessionHealthClock",
]
