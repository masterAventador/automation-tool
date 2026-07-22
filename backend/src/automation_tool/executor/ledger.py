"""Private SQLite command ledger for the packaged Local Executor."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final, cast
from uuid import RFC_4122, UUID

from automation_tool.executor.side_effect_ledger import LocalSideEffect, SideEffectState
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_CLOCK_SKEW,
    ACTION_RESULT_EVIDENCE_VERSION,
    ActionAuthorizationClaims,
    DouyinSearchExposureAction,
    PlatformSessionState,
    TaskActionCommandEnvelope,
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCommandEnvelope,
    TaskDiscoveryCompletedEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

EXECUTOR_LEDGER_FILE_NAME: Final = "executor-ledger.sqlite3"
_SCHEMA_VERSION: Final = 7
_MAX_OUTBOX_BATCH: Final = 1000
_MAX_PENDING_OUTBOX_ENTRIES: Final = 1000
_MAX_PENDING_OUTBOX_BYTES: Final = 16 * 1024 * 1024
_MAX_CROSS_RUNTIME_SEQUENCE: Final = 2**53 - 1
_MAX_LOCAL_ACTION_INTERVAL_SECONDS: Final = 3600
_MAX_LOCAL_TASK_ACTIONS: Final = 100
_MAX_UNRESOLVED_SIDE_EFFECTS: Final = 100

_SIDE_EFFECT_SELECT: Final = """
    SELECT s.action_id, a.target_id, a.execution_attempt_id, a.task_id,
           a.installation_id, a.executor_id, a.platform, a.action,
           a.idempotency_key, s.effect_fingerprint, s.state,
           s.prepared_at, s.dispatched_at, s.settled_at,
           s.verification_fingerprint, s.revision, a.deadline_at
    FROM executor_side_effects s
    LEFT JOIN executor_action_admissions a ON a.action_id = s.action_id
"""


class ExecutorLedgerRejected(RuntimeError):
    """The durable local command state cannot be used safely."""

    def __init__(self) -> None:
        super().__init__("Local Executor ledger is unavailable")


class ExecutorActionAdmissionLimitReason(StrEnum):
    EMERGENCY_STOP = "emergency_stop"
    MINIMUM_INTERVAL = "minimum_interval"
    TASK_ACTION_LIMIT = "task_action_limit"


class ExecutorActionAdmissionLimited(ExecutorLedgerRejected):
    def __init__(self, reason: ExecutorActionAdmissionLimitReason) -> None:
        if not isinstance(reason, ExecutorActionAdmissionLimitReason):
            raise ExecutorLedgerRejected
        self.reason = reason
        RuntimeError.__init__(self, "Local Executor action admission is limited")


class AttemptCheckpointState(StrEnum):
    RECEIVED = "received"
    RUNNING = "running"
    PAUSED = "paused"
    TERMINAL = "terminal"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


@dataclass(frozen=True, slots=True)
class LocalPlatformSession:
    platform: str
    state: PlatformSessionState
    session_revision: int
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            self.platform != "douyin"
            or not isinstance(self.state, PlatformSessionState)
            or type(self.session_revision) is not int
            or self.session_revision <= 0
            or not isinstance(self.observed_at, datetime)
            or self.observed_at.utcoffset() != UTC.utcoffset(self.observed_at)
        ):
            raise ExecutorLedgerRejected

    @property
    def circuit_open(self) -> bool:
        return self.state is not PlatformSessionState.HEALTHY


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    message_id: str
    idempotency_key: str
    task_id: str
    attempt_id: str
    sequence: int
    message_type: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class AttemptCheckpoint:
    task_id: str
    attempt_id: str
    last_command_sequence: int
    last_event_sequence: int
    state: AttemptCheckpointState
    revision: int


@dataclass(frozen=True, slots=True)
class PendingTaskControl:
    command: TaskCommandEnvelope
    checkpoint_revision: int
    next_event_sequence: int
    outcome_uncertain: bool = False

    def __post_init__(self) -> None:
        expected_state = self.command.message_type in {
            "task.pause",
            "task.resume",
            "task.cancel",
            "task.emergency_stop",
        }
        if (
            not expected_state
            or type(self.checkpoint_revision) is not int
            or self.checkpoint_revision <= 0
            or type(self.next_event_sequence) is not int
            or not 1 <= self.next_event_sequence <= _MAX_CROSS_RUNTIME_SEQUENCE
            or type(self.outcome_uncertain) is not bool
            or (
                self.outcome_uncertain
                and self.command.message_type not in {"task.cancel", "task.emergency_stop"}
            )
            or (self.command.message_type == "task.emergency_stop" and not self.outcome_uncertain)
        ):
            raise ExecutorLedgerRejected

    @property
    def event_type(self) -> str:
        if self.command.message_type == "task.pause":
            return "task.paused"
        if self.command.message_type == "task.resume":
            return "task.resumed"
        if self.command.message_type == "task.emergency_stop":
            return "task.outcome_uncertain"
        return "task.outcome_uncertain" if self.outcome_uncertain else "task.cancelled"


type InboundTaskCommand = (
    TaskCommandEnvelope | TaskDiscoveryCommandEnvelope | TaskActionCommandEnvelope
)
type OutboundExecutorMessage = (
    TaskCommandResultEnvelope
    | TaskEventEnvelope
    | TaskDiscoveryBatchEnvelope
    | TaskDiscoveryCompletedEnvelope
)


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    message: OutboundExecutorMessage
    source_message_id: str
    replayed: bool


@dataclass(frozen=True, slots=True, repr=False)
class LocalActionAdmission:
    action_id: str
    target_id: str
    execution_attempt_id: str
    task_id: str
    installation_id: str
    executor_id: str
    platform: str
    action: DouyinSearchExposureAction
    idempotency_key: str
    authorization_fingerprint: bytes
    authorized_at: datetime
    deadline_at: datetime
    admitted_at: datetime
    task_action_ordinal: int
    replayed: bool

    def __post_init__(self) -> None:
        if (
            not _is_canonical_uuid_v4(self.action_id)
            or not _is_canonical_uuid_v4(self.target_id)
            or not _is_canonical_uuid_v4(self.execution_attempt_id)
            or not _is_canonical_uuid_v4(self.task_id)
            or not _is_canonical_uuid_v4(self.installation_id)
            or not _is_canonical_uuid_v4(self.executor_id)
            or self.platform != "douyin"
            or not isinstance(self.action, DouyinSearchExposureAction)
            or self.idempotency_key != f"action:{self.action_id}"
            or type(self.authorization_fingerprint) is not bytes
            or len(self.authorization_fingerprint) != 32
            or _canonical_utc(self.authorized_at) is None
            or _canonical_utc(self.deadline_at) is None
            or _canonical_utc(self.admitted_at) is None
            or not self.authorized_at < self.deadline_at
            or not self.admitted_at < self.deadline_at
            or type(self.task_action_ordinal) is not int
            or not 1 <= self.task_action_ordinal <= _MAX_LOCAL_TASK_ACTIONS
            or type(self.replayed) is not bool
        ):
            raise ExecutorLedgerRejected

    def __repr__(self) -> str:
        return "LocalActionAdmission(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalActionEmergencyStop:
    engaged: bool
    revision: int
    changed_at: datetime | None

    def __post_init__(self) -> None:
        if (
            type(self.engaged) is not bool
            or type(self.revision) is not int
            or self.revision < 0
            or (self.revision == 0 and (self.engaged or self.changed_at is not None))
            or (
                self.revision > 0
                and (self.changed_at is None or _canonical_utc(self.changed_at) is None)
            )
        ):
            raise ExecutorLedgerRejected

    def __repr__(self) -> str:
        return (
            "LocalActionEmergencyStop("
            f"engaged={self.engaged!r}, revision={self.revision!r}, <redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class LocalActionHardPolicyBinding:
    minimum_interval_seconds: int
    task_action_limit: int

    def __post_init__(self) -> None:
        if (
            type(self.minimum_interval_seconds) is not int
            or not 1 <= self.minimum_interval_seconds <= _MAX_LOCAL_ACTION_INTERVAL_SECONDS
            or type(self.task_action_limit) is not int
            or not 1 <= self.task_action_limit <= _MAX_LOCAL_TASK_ACTIONS
        ):
            raise ExecutorLedgerRejected

    def __repr__(self) -> str:
        return "LocalActionHardPolicyBinding(<redacted>)"


class ExecutorLedger:
    """Own the fixed-schema command, checkpoint, and outbound replay ledger."""

    def __init__(
        self,
        *,
        state_directory: Path,
        installation_id: str,
        executor_id: str,
    ) -> None:
        try:
            if not isinstance(state_directory, Path):
                raise ValueError
            self._installation_id = _canonical_uuid_v4(installation_id)
            self._executor_id = _canonical_uuid_v4(executor_id)
            self._state_directory = state_directory.absolute()
            self._prepare_private_directory()
            self._database_path = self._state_directory / EXECUTOR_LEDGER_FILE_NAME
            self._prepare_private_database_file()
            self._migrate_and_bind_identity()
        except Exception:
            raise ExecutorLedgerRejected from None

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def installation_id(self) -> str:
        return self._installation_id

    @property
    def executor_id(self) -> str:
        return self._executor_id

    def transport_connected(self) -> bool:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT network_connected FROM executor_action_guard WHERE singleton_id = 1"
                ).fetchone()
            if row not in {(0,), (1,)}:
                raise ValueError
            return bool(row[0])
        except Exception:
            raise ExecutorLedgerRejected from None

    def set_transport_connected(self, connected: bool) -> bool:
        try:
            if type(connected) is not bool:
                raise ValueError
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT network_connected FROM executor_action_guard WHERE singleton_id = 1"
                ).fetchone()
                if row not in {(0,), (1,)}:
                    raise ValueError
                if bool(row[0]) is connected:
                    connection.commit()
                    return False
                updated = connection.execute(
                    "UPDATE executor_action_guard SET network_connected = ? "
                    "WHERE singleton_id = 1 AND network_connected = ?",
                    (int(connected), int(not connected)),
                )
                if updated.rowcount != 1:  # pragma: no cover - row is locked above
                    raise ValueError
                connection.commit()
                return True
        except Exception:
            raise ExecutorLedgerRejected from None

    def bind_action_hard_policy(
        self,
        *,
        minimum_interval_seconds: int,
        task_action_limit: int,
    ) -> LocalActionHardPolicyBinding:
        """Persist local limits monotonically so no later caller can loosen them."""

        try:
            proposed = LocalActionHardPolicyBinding(
                minimum_interval_seconds=minimum_interval_seconds,
                task_action_limit=task_action_limit,
            )
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT minimum_interval_seconds, task_action_limit
                    FROM executor_action_policy WHERE singleton_id = 1
                    """
                ).fetchone()
                if row is None:
                    raise ValueError
                if row == (None, None):
                    effective = proposed
                    connection.execute(
                        """
                        UPDATE executor_action_policy
                        SET minimum_interval_seconds = ?, task_action_limit = ?
                        WHERE singleton_id = 1
                        """,
                        (effective.minimum_interval_seconds, effective.task_action_limit),
                    )
                else:
                    if row[0] is None or row[1] is None:
                        raise ValueError
                    current = LocalActionHardPolicyBinding(
                        minimum_interval_seconds=cast(int, row[0]),
                        task_action_limit=cast(int, row[1]),
                    )
                    effective = LocalActionHardPolicyBinding(
                        minimum_interval_seconds=max(
                            current.minimum_interval_seconds,
                            proposed.minimum_interval_seconds,
                        ),
                        task_action_limit=min(
                            current.task_action_limit,
                            proposed.task_action_limit,
                        ),
                    )
                    if effective != current:
                        connection.execute(
                            """
                            UPDATE executor_action_policy
                            SET minimum_interval_seconds = ?, task_action_limit = ?
                            WHERE singleton_id = 1
                            """,
                            (
                                effective.minimum_interval_seconds,
                                effective.task_action_limit,
                            ),
                        )
                connection.commit()
                return effective
        except Exception:
            raise ExecutorLedgerRejected from None

    def get_action_admission(self, action_id: str) -> LocalActionAdmission | None:
        try:
            canonical_action_id = _canonical_uuid_v4(action_id)
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT action_id, target_id, execution_attempt_id, task_id,
                           installation_id, executor_id, platform, action,
                           idempotency_key, authorization_fingerprint,
                           authorized_at, deadline_at, admitted_at,
                           task_action_ordinal
                    FROM executor_action_admissions
                    WHERE action_id = ?
                    """,
                    (canonical_action_id,),
                ).fetchone()
            return None if row is None else _action_admission(row, replayed=False)
        except Exception:
            raise ExecutorLedgerRejected from None

    def admit_action(
        self,
        *,
        claims: ActionAuthorizationClaims,
        authorization_fingerprint: bytes,
        admitted_at: datetime,
        minimum_interval_seconds: int,
        task_action_limit: int,
    ) -> LocalActionAdmission:
        """Atomically enforce the local latch, interval, task cap, and exact replay."""

        try:
            canonical_admitted_at = _canonical_utc(admitted_at)
            if (
                not isinstance(claims, ActionAuthorizationClaims)
                or str(claims.installation_id) != self._installation_id
                or str(claims.executor_id) != self._executor_id
                or type(authorization_fingerprint) is not bytes
                or len(authorization_fingerprint) != 32
                or canonical_admitted_at is None
                or canonical_admitted_at + ACTION_AUTHORIZATION_CLOCK_SKEW < claims.authorized_at
                or canonical_admitted_at >= claims.deadline_at
                or type(minimum_interval_seconds) is not int
                or not 1 <= minimum_interval_seconds <= _MAX_LOCAL_ACTION_INTERVAL_SECONDS
                or type(task_action_limit) is not int
                or not 1 <= task_action_limit <= _MAX_LOCAL_TASK_ACTIONS
            ):
                raise ValueError
            encoded_admitted_at = _encode_utc(canonical_admitted_at)
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                stop = _action_emergency_stop(
                    cast(
                        sqlite3.Row | tuple[object, ...],
                        connection.execute(
                            """
                            SELECT engaged, revision, changed_at
                            FROM executor_action_guard WHERE singleton_id = 1
                            """
                        ).fetchone(),
                    )
                )
                if stop.engaged:
                    raise ExecutorActionAdmissionLimited(
                        ExecutorActionAdmissionLimitReason.EMERGENCY_STOP
                    )
                rows = connection.execute(
                    """
                    SELECT action_id, target_id, execution_attempt_id, task_id,
                           installation_id, executor_id, platform, action,
                           idempotency_key, authorization_fingerprint,
                           authorized_at, deadline_at, admitted_at,
                           task_action_ordinal
                    FROM executor_action_admissions
                    WHERE action_id = ? OR idempotency_key = ?
                    """,
                    (str(claims.action_id), str(claims.idempotency_key)),
                ).fetchall()
                if rows:
                    if len(rows) != 1 or not _same_action_admission(
                        rows[0], claims, authorization_fingerprint
                    ):
                        raise ValueError
                    connection.commit()
                    return _action_admission(rows[0], replayed=True)
                latest = connection.execute(
                    """
                    SELECT admitted_at FROM executor_action_admissions
                    WHERE installation_id = ? AND platform = ? AND action = ?
                    ORDER BY admitted_at DESC, action_id DESC LIMIT 1
                    """,
                    (self._installation_id, claims.platform, claims.action.value),
                ).fetchone()
                if latest is not None:
                    latest_at = _decode_utc(latest[0])
                    if canonical_admitted_at < latest_at:
                        raise ValueError
                    if canonical_admitted_at - latest_at < timedelta(
                        seconds=minimum_interval_seconds
                    ):
                        raise ExecutorActionAdmissionLimited(
                            ExecutorActionAdmissionLimitReason.MINIMUM_INTERVAL
                        )
                task_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM executor_action_admissions
                        WHERE task_id = ? AND platform = ? AND action = ?
                        """,
                        (str(claims.task_id), claims.platform, claims.action.value),
                    ).fetchone()[0]
                )
                if task_count >= task_action_limit:
                    raise ExecutorActionAdmissionLimited(
                        ExecutorActionAdmissionLimitReason.TASK_ACTION_LIMIT
                    )
                task_action_ordinal = task_count + 1
                connection.execute(
                    """
                    INSERT INTO executor_action_admissions (
                        action_id, target_id, execution_attempt_id, task_id,
                        installation_id, executor_id, platform, action,
                        idempotency_key, authorization_fingerprint,
                        authorized_at, deadline_at, admitted_at,
                        task_action_ordinal
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(claims.action_id),
                        str(claims.target_id),
                        str(claims.execution_attempt_id),
                        str(claims.task_id),
                        str(claims.installation_id),
                        str(claims.executor_id),
                        claims.platform,
                        claims.action.value,
                        str(claims.idempotency_key),
                        authorization_fingerprint,
                        _encode_utc(claims.authorized_at),
                        _encode_utc(claims.deadline_at),
                        encoded_admitted_at,
                        task_action_ordinal,
                    ),
                )
                connection.commit()
            return LocalActionAdmission(
                action_id=str(claims.action_id),
                target_id=str(claims.target_id),
                execution_attempt_id=str(claims.execution_attempt_id),
                task_id=str(claims.task_id),
                installation_id=str(claims.installation_id),
                executor_id=str(claims.executor_id),
                platform=claims.platform,
                action=claims.action,
                idempotency_key=str(claims.idempotency_key),
                authorization_fingerprint=authorization_fingerprint,
                authorized_at=claims.authorized_at,
                deadline_at=claims.deadline_at,
                admitted_at=canonical_admitted_at,
                task_action_ordinal=task_action_ordinal,
                replayed=False,
            )
        except ExecutorActionAdmissionLimited:
            raise
        except Exception:
            raise ExecutorLedgerRejected from None

    def get_side_effect(self, action_id: str) -> LocalSideEffect | None:
        try:
            canonical_action_id = _canonical_uuid_v4(action_id)
            with closing(self._connect()) as connection:
                row = connection.execute(
                    _SIDE_EFFECT_SELECT + " WHERE s.action_id = ?",
                    (canonical_action_id,),
                ).fetchone()
            return None if row is None else _side_effect(row, replayed=False)
        except Exception:
            raise ExecutorLedgerRejected from None

    def list_unresolved_side_effects(self, *, limit: int) -> tuple[LocalSideEffect, ...]:
        try:
            if type(limit) is not int or not 1 <= limit <= _MAX_UNRESOLVED_SIDE_EFFECTS:
                raise ValueError
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    _SIDE_EFFECT_SELECT
                    + " WHERE s.state != 'verified'"
                    + " ORDER BY s.prepared_at, s.action_id LIMIT ?",
                    (limit,),
                ).fetchall()
            return tuple(_side_effect(row, replayed=False) for row in rows)
        except Exception:
            raise ExecutorLedgerRejected from None

    def list_crash_recovery_side_effects(self, *, limit: int) -> tuple[LocalSideEffect, ...]:
        """List action facts whose non-terminal Attempt may need crash reconciliation."""

        try:
            if type(limit) is not int or not 1 <= limit <= _MAX_UNRESOLVED_SIDE_EFFECTS:
                raise ValueError
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    _SIDE_EFFECT_SELECT
                    + " JOIN executor_attempt_checkpoints p"
                    + " ON p.attempt_id = a.execution_attempt_id"
                    + " WHERE p.state IN ('running', 'paused', 'outcome_uncertain')"
                    + " ORDER BY s.prepared_at, s.action_id LIMIT ?",
                    (limit,),
                ).fetchall()
            return tuple(_side_effect(row, replayed=False) for row in rows)
        except Exception:
            raise ExecutorLedgerRejected from None

    def get_side_effect_recovery_event(self, action_id: str) -> TaskEventEnvelope | None:
        """Return the one durable recovery projection for an Action, if present."""

        try:
            canonical_action_id = _canonical_uuid_v4(action_id)
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT envelope FROM executor_outbox
                    WHERE idempotency_key = ?
                    """,
                    (f"executor:recovery:{canonical_action_id}",),
                ).fetchone()
            if row is None:
                return None
            message = _parse_outbound(str(row[0]))
            if (
                not isinstance(message, TaskEventEnvelope)
                or message.message_type not in {"step.completed", "task.outcome_uncertain"}
                or message.payload.get("action_id") != canonical_action_id
                or message.payload.get("evidence_version") != ACTION_RESULT_EVIDENCE_VERSION
            ):
                raise ValueError
            return message
        except Exception:
            raise ExecutorLedgerRejected from None

    def initial_task_command(self, attempt_id: str) -> TaskCommandEnvelope:
        """Load the original task.offer used as the recovery event source."""

        try:
            canonical_attempt_id = _canonical_uuid_v4(attempt_id)
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT envelope FROM executor_commands
                    WHERE attempt_id = ? AND message_type = 'task.offer' AND sequence = 1
                    """,
                    (canonical_attempt_id,),
                ).fetchall()
            if len(rows) != 1:
                raise ValueError
            message = parse_executor_message(str(rows[0][0]))
            if not isinstance(message, TaskCommandEnvelope) or message.message_type != "task.offer":
                raise ValueError
            return message
        except Exception:
            raise ExecutorLedgerRejected from None

    def prepare_side_effect(
        self,
        *,
        action_id: str,
        effect_fingerprint: bytes,
        prepared_at: datetime,
    ) -> LocalSideEffect:
        """Persist exact effect intent before any platform dispatch is permitted."""

        try:
            canonical_action_id = _canonical_uuid_v4(action_id)
            canonical_prepared_at = _canonical_utc(prepared_at)
            _require_fingerprint(effect_fingerprint)
            if canonical_prepared_at is None:
                raise ValueError
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    _SIDE_EFFECT_SELECT + " WHERE s.action_id = ?",
                    (canonical_action_id,),
                ).fetchone()
                if existing is not None:
                    effect = _side_effect(existing, replayed=True)
                    if effect.effect_fingerprint != effect_fingerprint:
                        raise ValueError
                    connection.commit()
                    return effect
                admission = connection.execute(
                    """
                    SELECT action_id, action, admitted_at, deadline_at
                    FROM executor_action_admissions WHERE action_id = ?
                    """,
                    (canonical_action_id,),
                ).fetchone()
                if (
                    admission is None
                    or admission[1]
                    not in {
                        DouyinSearchExposureAction.COMMENT.value,
                        DouyinSearchExposureAction.DIRECT_MESSAGE.value,
                    }
                    or canonical_prepared_at < _decode_utc(admission[2])
                    or canonical_prepared_at >= _decode_utc(admission[3])
                ):
                    raise ValueError
                if _action_emergency_stop(
                    cast(
                        sqlite3.Row | tuple[object, ...],
                        connection.execute(
                            """
                            SELECT engaged, revision, changed_at
                            FROM executor_action_guard WHERE singleton_id = 1
                            """
                        ).fetchone(),
                    )
                ).engaged:
                    raise ValueError
                connection.execute(
                    """
                    INSERT INTO executor_side_effects (
                        action_id, effect_fingerprint, state, prepared_at,
                        dispatched_at, settled_at, verification_fingerprint, revision
                    ) VALUES (?, ?, 'prepared', ?, NULL, NULL, NULL, 1)
                    """,
                    (
                        canonical_action_id,
                        effect_fingerprint,
                        _encode_utc(canonical_prepared_at),
                    ),
                )
                row = connection.execute(
                    _SIDE_EFFECT_SELECT + " WHERE s.action_id = ?",
                    (canonical_action_id,),
                ).fetchone()
                effect = _side_effect(
                    cast(sqlite3.Row | tuple[object, ...], row),
                    replayed=False,
                )
                connection.commit()
                return effect
        except Exception:
            raise ExecutorLedgerRejected from None

    def begin_side_effect_dispatch(
        self,
        *,
        action_id: str,
        effect_fingerprint: bytes,
        dispatched_at: datetime,
    ) -> LocalSideEffect:
        """Atomically grant at most one caller permission to perform the effect."""

        try:
            canonical_action_id = _canonical_uuid_v4(action_id)
            canonical_dispatched_at = _canonical_utc(dispatched_at)
            _require_fingerprint(effect_fingerprint)
            if canonical_dispatched_at is None:
                raise ValueError
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    _SIDE_EFFECT_SELECT + " WHERE s.action_id = ?",
                    (canonical_action_id,),
                ).fetchone()
                current = _side_effect(
                    cast(sqlite3.Row | tuple[object, ...], row),
                    replayed=False,
                )
                if current.effect_fingerprint != effect_fingerprint:
                    raise ValueError
                if current.state is not SideEffectState.PREPARED:
                    connection.commit()
                    return _side_effect(
                        cast(sqlite3.Row | tuple[object, ...], row),
                        replayed=True,
                    )
                dispatch_blocked = connection.execute(
                    """
                    SELECT 1
                    FROM executor_commands c
                    JOIN executor_attempt_checkpoints p ON p.attempt_id = c.attempt_id
                    WHERE c.attempt_id = ?
                      AND (
                        p.state IN ('terminal', 'outcome_uncertain')
                        OR (
                          c.sequence = p.last_command_sequence
                          AND c.message_type IN (
                            'task.pause', 'task.cancel', 'task.emergency_stop'
                          )
                          AND p.state IN ('running', 'paused')
                        )
                      )
                    LIMIT 1
                    """,
                    (current.execution_attempt_id,),
                ).fetchone()
                guard = connection.execute(
                    """
                    SELECT engaged, revision, changed_at, network_connected
                    FROM executor_action_guard WHERE singleton_id = 1
                    """
                ).fetchone()
                if guard is None:
                    raise ValueError
                if (
                    canonical_dispatched_at < current.prepared_at
                    or canonical_dispatched_at >= _decode_utc(cast(tuple[object, ...], row)[16])
                    or dispatch_blocked is not None
                    or _action_emergency_stop(cast(tuple[object, ...], tuple(guard[:3]))).engaged
                    or guard[3] != 1
                ):
                    raise ValueError
                updated = connection.execute(
                    """
                    UPDATE executor_side_effects
                    SET state = 'dispatched', dispatched_at = ?, revision = 2
                    WHERE action_id = ? AND state = 'prepared' AND revision = 1
                    """,
                    (_encode_utc(canonical_dispatched_at), canonical_action_id),
                )
                if updated.rowcount != 1:  # pragma: no cover - locked row cannot drift
                    raise ValueError
                changed = connection.execute(
                    _SIDE_EFFECT_SELECT + " WHERE s.action_id = ?",
                    (canonical_action_id,),
                ).fetchone()
                effect = _side_effect(
                    cast(sqlite3.Row | tuple[object, ...], changed),
                    replayed=False,
                )
                connection.commit()
                return effect
        except Exception:
            raise ExecutorLedgerRejected from None

    def verify_side_effect(
        self,
        *,
        action_id: str,
        effect_fingerprint: bytes,
        verification_fingerprint: bytes,
        verified_at: datetime,
    ) -> LocalSideEffect:
        return self._settle_side_effect(
            action_id=action_id,
            effect_fingerprint=effect_fingerprint,
            target_state=SideEffectState.VERIFIED,
            verification_fingerprint=verification_fingerprint,
            settled_at=verified_at,
        )

    def mark_side_effect_uncertain(
        self,
        *,
        action_id: str,
        effect_fingerprint: bytes,
        uncertain_at: datetime,
    ) -> LocalSideEffect:
        return self._settle_side_effect(
            action_id=action_id,
            effect_fingerprint=effect_fingerprint,
            target_state=SideEffectState.UNCERTAIN,
            verification_fingerprint=None,
            settled_at=uncertain_at,
        )

    def _settle_side_effect(
        self,
        *,
        action_id: str,
        effect_fingerprint: bytes,
        target_state: SideEffectState,
        verification_fingerprint: bytes | None,
        settled_at: datetime,
    ) -> LocalSideEffect:
        try:
            canonical_action_id = _canonical_uuid_v4(action_id)
            canonical_settled_at = _canonical_utc(settled_at)
            _require_fingerprint(effect_fingerprint)
            if target_state is SideEffectState.VERIFIED:
                _require_fingerprint(verification_fingerprint)
            elif (
                target_state is not SideEffectState.UNCERTAIN
                or verification_fingerprint is not None
            ):
                raise ValueError
            if canonical_settled_at is None:
                raise ValueError
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    _SIDE_EFFECT_SELECT + " WHERE s.action_id = ?",
                    (canonical_action_id,),
                ).fetchone()
                current = _side_effect(
                    cast(sqlite3.Row | tuple[object, ...], row),
                    replayed=False,
                )
                if current.effect_fingerprint != effect_fingerprint:
                    raise ValueError
                if current.state is target_state:
                    if current.verification_fingerprint != verification_fingerprint:
                        raise ValueError
                    connection.commit()
                    return _side_effect(
                        cast(sqlite3.Row | tuple[object, ...], row),
                        replayed=True,
                    )
                if (
                    current.state is not SideEffectState.DISPATCHED
                    or current.dispatched_at is None
                    or canonical_settled_at < current.dispatched_at
                ):
                    raise ValueError
                updated = connection.execute(
                    """
                    UPDATE executor_side_effects
                    SET state = ?, settled_at = ?, verification_fingerprint = ?, revision = 3
                    WHERE action_id = ? AND state = 'dispatched' AND revision = 2
                    """,
                    (
                        target_state.value,
                        _encode_utc(canonical_settled_at),
                        verification_fingerprint,
                        canonical_action_id,
                    ),
                )
                if updated.rowcount != 1:  # pragma: no cover - locked row cannot drift
                    raise ValueError
                changed = connection.execute(
                    _SIDE_EFFECT_SELECT + " WHERE s.action_id = ?",
                    (canonical_action_id,),
                ).fetchone()
                effect = _side_effect(
                    cast(sqlite3.Row | tuple[object, ...], changed),
                    replayed=False,
                )
                connection.commit()
                return effect
        except Exception:
            raise ExecutorLedgerRejected from None

    def get_action_emergency_stop(self) -> LocalActionEmergencyStop:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT engaged, revision, changed_at
                    FROM executor_action_guard WHERE singleton_id = 1
                    """
                ).fetchone()
            return _action_emergency_stop(cast(sqlite3.Row | tuple[object, ...], row))
        except Exception:
            raise ExecutorLedgerRejected from None

    def engage_action_emergency_stop(self, *, changed_at: datetime) -> LocalActionEmergencyStop:
        try:
            canonical_changed_at = _canonical_utc(changed_at)
            if canonical_changed_at is None:
                raise ValueError
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                stopped = self._engage_action_latch_in_connection(
                    connection,
                    changed_at=canonical_changed_at,
                )
                connection.commit()
                return stopped
        except Exception:
            raise ExecutorLedgerRejected from None

    def clear_action_emergency_stop(
        self,
        *,
        expected_revision: int,
        changed_at: datetime,
    ) -> LocalActionEmergencyStop:
        try:
            canonical_changed_at = _canonical_utc(changed_at)
            if (
                type(expected_revision) is not int
                or expected_revision <= 0
                or canonical_changed_at is None
            ):
                raise ValueError
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = _action_emergency_stop(
                    cast(
                        sqlite3.Row | tuple[object, ...],
                        connection.execute(
                            """
                            SELECT engaged, revision, changed_at
                            FROM executor_action_guard WHERE singleton_id = 1
                            """
                        ).fetchone(),
                    )
                )
                if (
                    not current.engaged
                    or current.revision != expected_revision
                    or current.changed_at is None
                    or canonical_changed_at <= current.changed_at
                ):
                    raise ValueError
                updated = connection.execute(
                    """
                    UPDATE executor_action_guard
                    SET engaged = 0, revision = revision + 1, changed_at = ?
                    WHERE singleton_id = 1 AND revision = ? AND engaged = 1
                    """,
                    (_encode_utc(canonical_changed_at), current.revision),
                )
                if updated.rowcount != 1:  # pragma: no cover - same locked revision selected above
                    raise ValueError
                connection.commit()
                return LocalActionEmergencyStop(
                    engaged=False,
                    revision=current.revision + 1,
                    changed_at=canonical_changed_at,
                )
        except Exception:
            raise ExecutorLedgerRejected from None

    def receive_command(self, command: InboundTaskCommand) -> CommandReceipt:
        return self._receive_command(command, emergency_stop_changed_at=None)

    def receive_task_emergency_stop(
        self,
        command: TaskCommandEnvelope,
        *,
        changed_at: datetime,
    ) -> CommandReceipt:
        if command.message_type != "task.emergency_stop":
            raise ExecutorLedgerRejected
        return self._receive_command(command, emergency_stop_changed_at=changed_at)

    def _receive_command(
        self,
        command: InboundTaskCommand,
        *,
        emergency_stop_changed_at: datetime | None,
    ) -> CommandReceipt:
        try:
            self._require_command_identity(command)
            canonical_emergency_stop_at = _canonical_utc(emergency_stop_changed_at)
            if (command.message_type == "task.emergency_stop") != (
                canonical_emergency_stop_at is not None
            ):
                raise ValueError
            envelope = _stored_command_envelope(command)
            fingerprint = _command_intent_fingerprint(command)
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT message_id, idempotency_key, task_id, attempt_id,
                           sequence, message_type, intent_sha256
                    FROM executor_commands
                    WHERE message_id = ? OR idempotency_key = ?
                    """,
                    (str(command.message_id), str(command.idempotency_key)),
                ).fetchall()
                if rows:
                    if len(rows) != 1 or bytes(rows[0][6]) != fingerprint:
                        raise ValueError
                    if canonical_emergency_stop_at is not None:
                        self._engage_emergency_stop_in_connection(
                            connection,
                            attempt_id=str(command.execution_attempt_id),
                            changed_at=canonical_emergency_stop_at,
                        )
                    connection.commit()
                    return _command_receipt(rows[0], replayed=True)

                checkpoint = connection.execute(
                    """
                    SELECT task_id, last_command_sequence, last_event_sequence, state, revision
                    FROM executor_attempt_checkpoints
                    WHERE attempt_id = ?
                    """,
                    (str(command.execution_attempt_id),),
                ).fetchone()
                if checkpoint is None:
                    if command.sequence != 1 or command.message_type in {
                        "task.pause",
                        "task.resume",
                        "task.cancel",
                        "task.emergency_stop",
                        "action.execute",
                    }:
                        raise ValueError
                    connection.execute(
                        """
                        INSERT INTO executor_attempt_checkpoints (
                            attempt_id, task_id, last_command_sequence,
                            last_event_sequence, state, revision
                        ) VALUES (?, ?, 1, 0, ?, 1)
                        """,
                        (
                            str(command.execution_attempt_id),
                            str(command.task_id),
                            AttemptCheckpointState.RECEIVED.value,
                        ),
                    )
                else:
                    required_control_states = {
                        "task.pause": (AttemptCheckpointState.RUNNING,),
                        "task.resume": (AttemptCheckpointState.PAUSED,),
                        "task.cancel": (
                            AttemptCheckpointState.RUNNING,
                            AttemptCheckpointState.PAUSED,
                        ),
                        "task.emergency_stop": (
                            AttemptCheckpointState.RUNNING,
                            AttemptCheckpointState.PAUSED,
                        ),
                        "action.execute": (AttemptCheckpointState.RUNNING,),
                    }.get(command.message_type)
                    if (
                        str(checkpoint[0]) != str(command.task_id)
                        or command.sequence != int(checkpoint[1]) + 1
                        or (
                            required_control_states is not None
                            and str(checkpoint[3])
                            not in {state.value for state in required_control_states}
                        )
                    ):
                        raise ValueError
                    connection.execute(
                        """
                        UPDATE executor_attempt_checkpoints
                        SET last_command_sequence = ?, revision = revision + 1
                        WHERE attempt_id = ? AND revision = ?
                        """,
                        (
                            command.sequence,
                            str(command.execution_attempt_id),
                            int(checkpoint[4]),
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO executor_commands (
                        message_id, idempotency_key, intent_sha256, envelope,
                        task_id, attempt_id, sequence, message_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(command.message_id),
                        str(command.idempotency_key),
                        fingerprint,
                        envelope,
                        str(command.task_id),
                        str(command.execution_attempt_id),
                        command.sequence,
                        command.message_type,
                    ),
                )
                if canonical_emergency_stop_at is not None:
                    self._engage_emergency_stop_in_connection(
                        connection,
                        attempt_id=str(command.execution_attempt_id),
                        changed_at=canonical_emergency_stop_at,
                    )
                connection.commit()
                return CommandReceipt(
                    message_id=str(command.message_id),
                    idempotency_key=str(command.idempotency_key),
                    task_id=str(command.task_id),
                    attempt_id=str(command.execution_attempt_id),
                    sequence=command.sequence,
                    message_type=command.message_type,
                    replayed=False,
                )
        except Exception:
            raise ExecutorLedgerRejected from None

    @staticmethod
    def _engage_emergency_stop_in_connection(
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        changed_at: datetime,
    ) -> None:
        ExecutorLedger._engage_action_latch_in_connection(
            connection,
            changed_at=changed_at,
        )
        connection.execute(
            """
            UPDATE executor_side_effects
            SET state = 'uncertain', settled_at = ?, revision = revision + 1
            WHERE state = 'dispatched'
              AND dispatched_at <= ?
              AND action_id IN (
                SELECT action_id FROM executor_action_admissions
                WHERE execution_attempt_id = ?
              )
            """,
            (_encode_utc(changed_at), _encode_utc(changed_at), attempt_id),
        )
        if (
            connection.execute(
                """
                SELECT 1
                FROM executor_side_effects s
                JOIN executor_action_admissions a ON a.action_id = s.action_id
                WHERE a.execution_attempt_id = ? AND s.state = 'dispatched'
                LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()
            is not None
        ):
            raise ValueError

    @staticmethod
    def _engage_action_latch_in_connection(
        connection: sqlite3.Connection,
        *,
        changed_at: datetime,
    ) -> LocalActionEmergencyStop:
        current = _action_emergency_stop(
            cast(
                sqlite3.Row | tuple[object, ...],
                connection.execute(
                    """
                    SELECT engaged, revision, changed_at
                    FROM executor_action_guard WHERE singleton_id = 1
                    """
                ).fetchone(),
            )
        )
        if current.engaged:
            return current
        if current.changed_at is not None and changed_at <= current.changed_at:
            raise ValueError
        updated = connection.execute(
            """
            UPDATE executor_action_guard
            SET engaged = 1, revision = revision + 1, changed_at = ?
            WHERE singleton_id = 1 AND revision = ? AND engaged = 0
            """,
            (_encode_utc(changed_at), current.revision),
        )
        if updated.rowcount != 1:
            raise ValueError
        return LocalActionEmergencyStop(
            engaged=True,
            revision=current.revision + 1,
            changed_at=changed_at,
        )

    def get_checkpoint(self, attempt_id: str) -> AttemptCheckpoint | None:
        try:
            canonical_attempt_id = _canonical_uuid_v4(attempt_id)
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT task_id, attempt_id, last_command_sequence,
                           last_event_sequence, state, revision
                    FROM executor_attempt_checkpoints
                    WHERE attempt_id = ?
                    """,
                    (canonical_attempt_id,),
                ).fetchone()
            return None if row is None else _checkpoint(row)
        except Exception:
            raise ExecutorLedgerRejected from None

    def has_received_task_emergency_stop(self) -> bool:
        try:
            with closing(self._connect()) as connection:
                return (
                    connection.execute(
                        """
                        SELECT 1 FROM executor_commands
                        WHERE message_type = 'task.emergency_stop'
                        LIMIT 1
                        """
                    ).fetchone()
                    is not None
                )
        except Exception:
            raise ExecutorLedgerRejected from None

    def get_platform_session(self, platform: str) -> LocalPlatformSession | None:
        try:
            _require_platform(platform)
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT platform, state, session_revision, observed_at
                    FROM executor_platform_sessions
                    WHERE platform = ?
                    """,
                    (platform,),
                ).fetchone()
            return None if row is None else _platform_session(row)
        except Exception:
            raise ExecutorLedgerRejected from None

    def record_platform_session(
        self,
        *,
        platform: str,
        state: PlatformSessionState,
        observed_at: datetime,
        advance_epoch: bool = False,
    ) -> LocalPlatformSession:
        """Persist one page-derived state without allowing an implicit recovery."""

        try:
            _require_platform(platform)
            if (
                not isinstance(state, PlatformSessionState)
                or not isinstance(observed_at, datetime)
                or observed_at.utcoffset() is None
                or type(advance_epoch) is not bool
            ):
                raise ValueError
            canonical_observed_at = observed_at.astimezone(UTC)
            encoded_observed_at = _encode_utc(canonical_observed_at)
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT platform, state, session_revision, observed_at
                    FROM executor_platform_sessions
                    WHERE platform = ?
                    """,
                    (platform,),
                ).fetchone()
                if row is None:
                    revision = 1
                    connection.execute(
                        """
                        INSERT INTO executor_platform_sessions (
                            platform, state, session_revision, observed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (platform, state.value, revision, encoded_observed_at),
                    )
                else:
                    current = _platform_session(row)
                    if canonical_observed_at < current.observed_at:
                        raise ValueError
                    if canonical_observed_at == current.observed_at:
                        if not advance_epoch and state is current.state:
                            connection.commit()
                            return current
                        raise ValueError
                    if (
                        not advance_epoch
                        and current.circuit_open
                        and state is PlatformSessionState.HEALTHY
                    ):
                        raise ValueError
                    revision = (
                        current.session_revision + 1 if advance_epoch else current.session_revision
                    )
                    connection.execute(
                        """
                        UPDATE executor_platform_sessions
                        SET state = ?, session_revision = ?, observed_at = ?
                        WHERE platform = ? AND session_revision = ?
                        """,
                        (
                            state.value,
                            revision,
                            encoded_observed_at,
                            platform,
                            current.session_revision,
                        ),
                    )
                connection.commit()
            return LocalPlatformSession(
                platform=platform,
                state=state,
                session_revision=revision,
                observed_at=canonical_observed_at,
            )
        except Exception:
            raise ExecutorLedgerRejected from None

    def compare_and_set_checkpoint(
        self,
        *,
        attempt_id: str,
        expected_revision: int,
        state: AttemptCheckpointState,
        last_event_sequence: int,
    ) -> AttemptCheckpoint:
        try:
            canonical_attempt_id = _canonical_uuid_v4(attempt_id)
            if (
                type(expected_revision) is not int
                or expected_revision <= 0
                or not isinstance(state, AttemptCheckpointState)
                or type(last_event_sequence) is not int
                or not 0 <= last_event_sequence <= _MAX_CROSS_RUNTIME_SEQUENCE
            ):
                raise ValueError
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE executor_attempt_checkpoints
                    SET state = ?, last_event_sequence = ?, revision = revision + 1
                    WHERE attempt_id = ? AND revision = ?
                      AND last_event_sequence <= ?
                    """,
                    (
                        state.value,
                        last_event_sequence,
                        canonical_attempt_id,
                        expected_revision,
                        last_event_sequence,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError
                row = connection.execute(
                    """
                    SELECT task_id, attempt_id, last_command_sequence,
                           last_event_sequence, state, revision
                    FROM executor_attempt_checkpoints
                    WHERE attempt_id = ?
                    """,
                    (canonical_attempt_id,),
                ).fetchone()
                connection.commit()
                return _checkpoint(cast(sqlite3.Row | tuple[object, ...], row))
        except Exception:
            raise ExecutorLedgerRejected from None

    def pending_task_controls(self, *, limit: int) -> tuple[PendingTaskControl, ...]:
        """Return latest cooperative controls whose checkpoint event is still pending."""

        try:
            if type(limit) is not int or not 1 <= limit <= _MAX_OUTBOX_BATCH:
                raise ValueError
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT c.envelope, p.revision, p.last_event_sequence,
                           CASE
                             WHEN c.message_type = 'task.emergency_stop' THEN 1
                             WHEN c.message_type = 'task.cancel' AND EXISTS (
                               SELECT 1
                               FROM executor_side_effects uncertain_effect
                               JOIN executor_action_admissions uncertain_action
                                 ON uncertain_action.action_id = uncertain_effect.action_id
                               WHERE uncertain_action.execution_attempt_id = c.attempt_id
                                 AND uncertain_effect.state = 'uncertain'
                             ) THEN 1
                             ELSE 0
                           END AS outcome_uncertain
                    FROM executor_commands c
                    JOIN executor_attempt_checkpoints p ON p.attempt_id = c.attempt_id
                    WHERE c.sequence = p.last_command_sequence
                      AND (
                        (c.message_type = 'task.pause' AND p.state = 'running')
                        OR (c.message_type = 'task.resume' AND p.state = 'paused')
                        OR (
                          c.message_type = 'task.cancel'
                          AND p.state IN ('running', 'paused')
                        )
                        OR (
                          c.message_type = 'task.emergency_stop'
                          AND p.state IN ('running', 'paused')
                        )
                      )
                      AND EXISTS (
                        SELECT 1 FROM executor_outbox acknowledgement
                        WHERE acknowledgement.source_message_id = c.message_id
                          AND json_extract(
                            acknowledgement.envelope, '$.message_type'
                          ) = 'task.control_ack'
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM executor_outbox projected
                        WHERE projected.source_message_id = c.message_id
                          AND (
                            (c.message_type = 'task.pause' AND json_extract(
                              projected.envelope, '$.message_type'
                            ) = 'task.paused')
                            OR (c.message_type = 'task.resume' AND json_extract(
                              projected.envelope, '$.message_type'
                            ) = 'task.resumed')
                            OR (c.message_type = 'task.cancel' AND json_extract(
                              projected.envelope, '$.message_type'
                            ) IN ('task.cancelled', 'task.outcome_uncertain'))
                            OR (c.message_type = 'task.emergency_stop' AND json_extract(
                              projected.envelope, '$.message_type'
                            ) = 'task.outcome_uncertain')
                          )
                      )
                      AND (
                        c.message_type NOT IN ('task.pause', 'task.cancel')
                        OR NOT EXISTS (
                          SELECT 1
                          FROM executor_side_effects s
                          JOIN executor_action_admissions a ON a.action_id = s.action_id
                          WHERE a.execution_attempt_id = c.attempt_id
                            AND s.state = 'dispatched'
                        )
                      )
                    ORDER BY c.rowid
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            pending: list[PendingTaskControl] = []
            for row in rows:
                parsed = parse_executor_message(cast(str, row[0]))
                if not isinstance(parsed, TaskCommandEnvelope):
                    raise ValueError
                pending.append(
                    PendingTaskControl(
                        command=parsed,
                        checkpoint_revision=cast(int, row[1]),
                        next_event_sequence=cast(int, row[2]) + 1,
                        outcome_uncertain=bool(row[3]) if row[3] in (0, 1) else cast(bool, row[3]),
                    )
                )
            return tuple(pending)
        except Exception:
            raise ExecutorLedgerRejected from None

    def complete_task_control(
        self,
        *,
        source_message_id: str,
        expected_checkpoint_revision: int,
        event: TaskEventEnvelope,
    ) -> OutboxEntry | None:
        """Atomically project one acknowledged control at its safe local checkpoint."""

        try:
            canonical_source_id = _canonical_uuid_v4(source_message_id)
            if (
                type(expected_checkpoint_revision) is not int
                or expected_checkpoint_revision <= 0
                or not isinstance(event, TaskEventEnvelope)
            ):
                raise ValueError
            self._require_outbound_identity(event)
            envelope = _canonical_message(event)
            fingerprint = hashlib.sha256(envelope.encode("utf-8")).digest()
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT c.task_id, c.attempt_id, c.correlation_id,
                           c.message_type, c.sequence, p.state, p.revision,
                           p.last_command_sequence, p.last_event_sequence
                    FROM executor_commands c
                    JOIN executor_attempt_checkpoints p ON p.attempt_id = c.attempt_id
                    WHERE c.message_id = ?
                    """,
                    (canonical_source_id,),
                ).fetchone()
                if row is None:
                    raise ValueError
                command_type = cast(str, row[3])
                allowed_states = {
                    "task.pause": {AttemptCheckpointState.RUNNING},
                    "task.resume": {AttemptCheckpointState.PAUSED},
                    "task.cancel": {
                        AttemptCheckpointState.RUNNING,
                        AttemptCheckpointState.PAUSED,
                    },
                    "task.emergency_stop": {
                        AttemptCheckpointState.RUNNING,
                        AttemptCheckpointState.PAUSED,
                    },
                }.get(command_type)
                allowed_event_types = {
                    "task.pause": {"task.paused"},
                    "task.resume": {"task.resumed"},
                    "task.cancel": {"task.cancelled", "task.outcome_uncertain"},
                    "task.emergency_stop": {"task.outcome_uncertain"},
                }.get(command_type)
                if allowed_states is None or allowed_event_types is None:
                    raise ValueError
                existing = connection.execute(
                    """
                    SELECT envelope, source_message_id
                    FROM executor_outbox
                    WHERE source_message_id = ?
                      AND json_extract(envelope, '$.message_type') IN (
                        'task.paused', 'task.resumed',
                        'task.cancelled', 'task.outcome_uncertain'
                      )
                    """,
                    (canonical_source_id,),
                ).fetchone()
                if existing is not None:
                    existing_message = _parse_outbound(cast(str, existing[0]))
                    if (
                        not isinstance(existing_message, TaskEventEnvelope)
                        or existing_message.message_type not in allowed_event_types
                        or existing_message.message_type != event.message_type
                        or str(existing_message.task_id) != str(row[0])
                        or str(existing_message.execution_attempt_id) != str(row[1])
                        or str(existing_message.correlation_id) != str(row[2])
                        or existing_message.sequence != int(row[8])
                        or str(event.task_id) != str(existing_message.task_id)
                        or str(event.execution_attempt_id)
                        != str(existing_message.execution_attempt_id)
                        or str(event.correlation_id) != str(existing_message.correlation_id)
                        or event.sequence != existing_message.sequence
                    ):
                        raise ValueError
                    connection.commit()
                    return OutboxEntry(
                        message=existing_message,
                        source_message_id=cast(str, existing[1]),
                        replayed=True,
                    )
                acknowledged = connection.execute(
                    """
                    SELECT 1 FROM executor_outbox
                    WHERE source_message_id = ?
                      AND json_extract(envelope, '$.message_type') = 'task.control_ack'
                    LIMIT 1
                    """,
                    (canonical_source_id,),
                ).fetchone()
                if (
                    acknowledged is None
                    or str(row[0]) != str(event.task_id)
                    or str(row[1]) != str(event.execution_attempt_id)
                    or str(row[2]) != str(event.correlation_id)
                    or event.message_type not in allowed_event_types
                    or int(row[4]) != int(row[7])
                    or str(row[5]) not in {state.value for state in allowed_states}
                    or int(row[6]) != expected_checkpoint_revision
                    or event.sequence != int(row[8]) + 1
                ):
                    raise ValueError
                if command_type in {"task.pause", "task.cancel"}:
                    dispatched = connection.execute(
                        """
                        SELECT 1
                        FROM executor_side_effects s
                        JOIN executor_action_admissions a ON a.action_id = s.action_id
                        WHERE a.execution_attempt_id = ? AND s.state = 'dispatched'
                        LIMIT 1
                        """,
                        (str(row[1]),),
                    ).fetchone()
                    if dispatched is not None:
                        connection.commit()
                        return None
                if command_type == "task.pause":
                    target_state = AttemptCheckpointState.PAUSED
                    expected_event_type = "task.paused"
                elif command_type == "task.resume":
                    target_state = AttemptCheckpointState.RUNNING
                    expected_event_type = "task.resumed"
                elif command_type == "task.emergency_stop":
                    target_state = AttemptCheckpointState.OUTCOME_UNCERTAIN
                    expected_event_type = "task.outcome_uncertain"
                else:
                    uncertain = connection.execute(
                        """
                        SELECT 1
                        FROM executor_side_effects s
                        JOIN executor_action_admissions a ON a.action_id = s.action_id
                        WHERE a.execution_attempt_id = ? AND s.state = 'uncertain'
                        LIMIT 1
                        """,
                        (str(row[1]),),
                    ).fetchone()
                    if uncertain is None:
                        target_state = AttemptCheckpointState.TERMINAL
                        expected_event_type = "task.cancelled"
                    else:
                        target_state = AttemptCheckpointState.OUTCOME_UNCERTAIN
                        expected_event_type = "task.outcome_uncertain"
                if event.message_type != expected_event_type:
                    raise ValueError
                _require_pending_outbox_capacity(connection, (envelope,))
                current_state = str(row[5])
                updated = connection.execute(
                    """
                    UPDATE executor_attempt_checkpoints
                    SET state = ?, last_event_sequence = ?, revision = revision + 1
                    WHERE attempt_id = ? AND revision = ?
                      AND state = ? AND last_command_sequence = ?
                    """,
                    (
                        target_state.value,
                        event.sequence,
                        str(row[1]),
                        expected_checkpoint_revision,
                        current_state,
                        int(row[4]),
                    ),
                )
                if updated.rowcount != 1:  # pragma: no cover - locked row cannot drift
                    raise ValueError
                next_ordinal = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM executor_outbox"
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO executor_outbox (
                        ordinal, message_id, idempotency_key, intent_sha256,
                        envelope, source_message_id, delivered
                    ) VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        next_ordinal,
                        str(event.message_id),
                        str(event.idempotency_key),
                        fingerprint,
                        envelope,
                        canonical_source_id,
                    ),
                )
                connection.commit()
                return OutboxEntry(
                    message=event,
                    source_message_id=canonical_source_id,
                    replayed=False,
                )
        except Exception:
            raise ExecutorLedgerRejected from None

    def enqueue_outbox(
        self,
        *,
        source_message_id: str,
        message: OutboundExecutorMessage,
    ) -> OutboxEntry:
        try:
            canonical_source_id = _canonical_uuid_v4(source_message_id)
            self._require_outbound_identity(message)
            envelope = _canonical_message(message)
            fingerprint = hashlib.sha256(envelope.encode("utf-8")).digest()
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT message_id, idempotency_key, envelope,
                           source_message_id, intent_sha256
                    FROM executor_outbox
                    WHERE message_id = ? OR idempotency_key = ?
                    """,
                    (str(message.message_id), str(message.idempotency_key)),
                ).fetchall()
                if rows:
                    if (
                        len(rows) != 1
                        or bytes(rows[0][4]) != fingerprint
                        or str(rows[0][3]) != canonical_source_id
                    ):
                        raise ValueError
                    parsed = _parse_outbound(str(rows[0][2]))
                    connection.commit()
                    return OutboxEntry(
                        message=parsed,
                        source_message_id=str(rows[0][3]),
                        replayed=True,
                    )
                source = connection.execute(
                    """
                    SELECT task_id, attempt_id, correlation_id
                    FROM executor_commands
                    WHERE message_id = ?
                    """,
                    (canonical_source_id,),
                ).fetchone()
                if source is None or (
                    str(source[0]) != str(message.task_id)
                    or str(source[1]) != str(message.execution_attempt_id)
                    or str(source[2]) != str(message.correlation_id)
                ):
                    raise ValueError
                _require_pending_outbox_capacity(connection, (envelope,))
                next_ordinal = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM executor_outbox"
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO executor_outbox (
                        ordinal, message_id, idempotency_key, intent_sha256,
                        envelope, source_message_id, delivered
                    ) VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        next_ordinal,
                        str(message.message_id),
                        str(message.idempotency_key),
                        fingerprint,
                        envelope,
                        canonical_source_id,
                    ),
                )
                connection.commit()
                return OutboxEntry(
                    message=message,
                    source_message_id=canonical_source_id,
                    replayed=False,
                )
        except Exception:
            raise ExecutorLedgerRejected from None

    def commit_side_effect_recovery(
        self,
        *,
        action_id: str,
        expected_checkpoint_revision: int,
        event: TaskEventEnvelope,
    ) -> OutboxEntry:
        """Atomically align a settled side effect, checkpoint, and recovery outbox event."""

        try:
            canonical_action_id = _canonical_uuid_v4(action_id)
            if (
                type(expected_checkpoint_revision) is not int
                or expected_checkpoint_revision <= 0
                or not isinstance(event, TaskEventEnvelope)
                or event.message_type not in {"step.completed", "task.outcome_uncertain"}
                or str(event.idempotency_key) != f"executor:recovery:{canonical_action_id}"
                or event.payload.get("action_id") != canonical_action_id
                or event.payload.get("evidence_version") != ACTION_RESULT_EVIDENCE_VERSION
            ):
                raise ValueError
            self._require_outbound_identity(event)
            envelope = _canonical_message(event)
            fingerprint = hashlib.sha256(envelope.encode("utf-8")).digest()
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT envelope, source_message_id
                    FROM executor_outbox WHERE idempotency_key = ?
                    """,
                    (f"executor:recovery:{canonical_action_id}",),
                ).fetchone()
                if existing is not None:
                    replay = _parse_outbound(str(existing[0]))
                    if (
                        not isinstance(replay, TaskEventEnvelope)
                        or replay.message_type != event.message_type
                        or replay.payload != event.payload
                    ):
                        raise ValueError
                    connection.commit()
                    return OutboxEntry(
                        message=replay,
                        source_message_id=str(existing[1]),
                        replayed=True,
                    )
                row = connection.execute(
                    """
                    SELECT a.task_id, a.execution_attempt_id, a.installation_id,
                           a.executor_id, a.action, s.state,
                           p.last_event_sequence, p.state, p.revision,
                           c.message_id, c.correlation_id
                    FROM executor_side_effects s
                    JOIN executor_action_admissions a ON a.action_id = s.action_id
                    JOIN executor_attempt_checkpoints p
                      ON p.attempt_id = a.execution_attempt_id
                    JOIN executor_commands c
                      ON c.attempt_id = a.execution_attempt_id
                     AND c.message_type = 'task.offer' AND c.sequence = 1
                    WHERE s.action_id = ?
                    """,
                    (canonical_action_id,),
                ).fetchone()
                if row is None:
                    raise ValueError
                expected_evidence = (
                    "recovery_unconfirmed"
                    if event.message_type == "task.outcome_uncertain"
                    else (
                        "comment_confirmed"
                        if str(row[4]) == DouyinSearchExposureAction.COMMENT.value
                        else "message_confirmed"
                    )
                )
                expected_side_effect_state = (
                    SideEffectState.UNCERTAIN.value
                    if event.message_type == "task.outcome_uncertain"
                    else SideEffectState.VERIFIED.value
                )
                if (
                    str(row[0]) != str(event.task_id)
                    or str(row[1]) != str(event.execution_attempt_id)
                    or str(row[2]) != str(event.installation_id)
                    or str(row[3]) != str(event.executor_id)
                    or str(row[5]) != expected_side_effect_state
                    or event.payload.get("evidence") != expected_evidence
                    or int(row[6]) + 1 != event.sequence
                    or str(row[7])
                    not in {
                        AttemptCheckpointState.RUNNING.value,
                        AttemptCheckpointState.PAUSED.value,
                    }
                    or int(row[8]) != expected_checkpoint_revision
                    or str(row[10]) != str(event.correlation_id)
                ):
                    raise ValueError
                _require_pending_outbox_capacity(connection, (envelope,))
                checkpoint_state = (
                    AttemptCheckpointState.OUTCOME_UNCERTAIN.value
                    if event.message_type == "task.outcome_uncertain"
                    else str(row[7])
                )
                updated = connection.execute(
                    """
                    UPDATE executor_attempt_checkpoints
                    SET state = ?, last_event_sequence = ?, revision = revision + 1
                    WHERE attempt_id = ? AND revision = ?
                    """,
                    (
                        checkpoint_state,
                        event.sequence,
                        str(row[1]),
                        expected_checkpoint_revision,
                    ),
                )
                if updated.rowcount != 1:  # pragma: no cover - same locked revision selected above
                    raise ValueError
                next_ordinal = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM executor_outbox"
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO executor_outbox (
                        ordinal, message_id, idempotency_key, intent_sha256,
                        envelope, source_message_id, delivered
                    ) VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        next_ordinal,
                        str(event.message_id),
                        str(event.idempotency_key),
                        fingerprint,
                        envelope,
                        str(row[9]),
                    ),
                )
                connection.commit()
                return OutboxEntry(
                    message=event,
                    source_message_id=str(row[9]),
                    replayed=False,
                )
        except Exception:
            raise ExecutorLedgerRejected from None

    def commit_outcome(
        self,
        *,
        source_message_id: str,
        expected_checkpoint_revision: int,
        checkpoint_state: AttemptCheckpointState,
        last_event_sequence: int,
        messages: tuple[OutboundExecutorMessage, ...],
    ) -> tuple[OutboxEntry, ...]:
        """Atomically advance one Attempt checkpoint and append its complete outbound batch."""

        try:
            canonical_source_id = _canonical_uuid_v4(source_message_id)
            if (
                type(expected_checkpoint_revision) is not int
                or expected_checkpoint_revision <= 0
                or not isinstance(checkpoint_state, AttemptCheckpointState)
                or type(last_event_sequence) is not int
                or not 0 <= last_event_sequence <= _MAX_CROSS_RUNTIME_SEQUENCE
                or type(messages) is not tuple
                or not 1 <= len(messages) <= 100
            ):
                raise ValueError
            prepared: list[tuple[OutboundExecutorMessage, str, bytes]] = []
            message_ids: set[str] = set()
            idempotency_keys: set[str] = set()
            for message in messages:
                self._require_outbound_identity(message)
                envelope = _canonical_message(message)
                fingerprint = hashlib.sha256(envelope.encode("utf-8")).digest()
                message_id = str(message.message_id)
                idempotency_key = str(message.idempotency_key)
                if message_id in message_ids or idempotency_key in idempotency_keys:
                    raise ValueError
                message_ids.add(message_id)
                idempotency_keys.add(idempotency_key)
                prepared.append((message, envelope, fingerprint))

            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                source = connection.execute(
                    """
                    SELECT task_id, attempt_id, correlation_id
                    FROM executor_commands
                    WHERE message_id = ?
                    """,
                    (canonical_source_id,),
                ).fetchone()
                if source is None:
                    raise ValueError
                for message, _envelope, _fingerprint in prepared:
                    if (
                        str(source[0]) != str(message.task_id)
                        or str(source[1]) != str(message.execution_attempt_id)
                        or str(source[2]) != str(message.correlation_id)
                    ):
                        raise ValueError
                if (
                    connection.execute(
                        "SELECT 1 FROM executor_outbox WHERE source_message_id = ? LIMIT 1",
                        (canonical_source_id,),
                    ).fetchone()
                    is not None
                ):
                    raise ValueError
                checkpoint = connection.execute(
                    """
                    SELECT last_event_sequence
                    FROM executor_attempt_checkpoints
                    WHERE attempt_id = ? AND revision = ?
                    """,
                    (str(source[1]), expected_checkpoint_revision),
                ).fetchone()
                if checkpoint is None or not 0 <= int(checkpoint[0]) <= last_event_sequence:
                    raise ValueError
                _require_pending_outbox_capacity(
                    connection,
                    tuple(envelope for _message, envelope, _fingerprint in prepared),
                )
                updated = connection.execute(
                    """
                    UPDATE executor_attempt_checkpoints
                    SET state = ?, last_event_sequence = ?, revision = revision + 1
                    WHERE attempt_id = ? AND revision = ?
                    """,
                    (
                        checkpoint_state.value,
                        last_event_sequence,
                        str(source[1]),
                        expected_checkpoint_revision,
                    ),
                )
                if updated.rowcount != 1:  # pragma: no cover - same locked revision selected above
                    raise ValueError
                next_ordinal = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM executor_outbox"
                    ).fetchone()[0]
                )
                for offset, (message, envelope, fingerprint) in enumerate(prepared):
                    connection.execute(
                        """
                        INSERT INTO executor_outbox (
                            ordinal, message_id, idempotency_key, intent_sha256,
                            envelope, source_message_id, delivered
                        ) VALUES (?, ?, ?, ?, ?, ?, 0)
                        """,
                        (
                            next_ordinal + offset,
                            str(message.message_id),
                            str(message.idempotency_key),
                            fingerprint,
                            envelope,
                            canonical_source_id,
                        ),
                    )
                connection.commit()
            return tuple(
                OutboxEntry(
                    message=message,
                    source_message_id=canonical_source_id,
                    replayed=False,
                )
                for message, _envelope, _fingerprint in prepared
            )
        except Exception:
            raise ExecutorLedgerRejected from None

    def outbox_for_command(self, source_message_id: str) -> tuple[OutboxEntry, ...]:
        try:
            canonical_source_id = _canonical_uuid_v4(source_message_id)
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT envelope, source_message_id
                    FROM executor_outbox
                    WHERE source_message_id = ?
                    ORDER BY ordinal
                    """,
                    (canonical_source_id,),
                ).fetchall()
            return tuple(
                OutboxEntry(
                    message=_parse_outbound(str(row[0])),
                    source_message_id=str(row[1]),
                    replayed=True,
                )
                for row in rows
            )
        except Exception:
            raise ExecutorLedgerRejected from None

    def requeue_delivered_outbox(self) -> int:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                updated = connection.execute(
                    "UPDATE executor_outbox SET delivered = 0 WHERE delivered = 1"
                )
                connection.commit()
                return updated.rowcount
        except Exception:
            raise ExecutorLedgerRejected from None

    def pending_outbox(self, *, limit: int) -> tuple[OutboxEntry, ...]:
        try:
            if type(limit) is not int or not 1 <= limit <= _MAX_OUTBOX_BATCH:
                raise ValueError
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT envelope, source_message_id
                    FROM executor_outbox
                    WHERE delivered = 0
                    ORDER BY ordinal
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return tuple(
                OutboxEntry(
                    message=_parse_outbound(str(row[0])),
                    source_message_id=str(row[1]),
                    replayed=True,
                )
                for row in rows
            )
        except Exception:
            raise ExecutorLedgerRejected from None

    def mark_outbox_delivered(self, message_id: str) -> bool:
        try:
            canonical_message_id = _canonical_uuid_v4(message_id)
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE executor_outbox SET delivered = 1
                    WHERE message_id = ? AND delivered = 0
                    """,
                    (canonical_message_id,),
                )
                if cursor.rowcount == 1:
                    connection.commit()
                    return True
                exists = connection.execute(
                    "SELECT delivered FROM executor_outbox WHERE message_id = ?",
                    (canonical_message_id,),
                ).fetchone()
                if exists != (1,):
                    raise ValueError
                connection.commit()
                return False
        except Exception:
            raise ExecutorLedgerRejected from None

    def _require_command_identity(self, command: InboundTaskCommand) -> None:
        if not isinstance(
            command,
            (TaskCommandEnvelope, TaskDiscoveryCommandEnvelope, TaskActionCommandEnvelope),
        ) or (
            str(command.installation_id) != self._installation_id
            or str(command.executor_id) != self._executor_id
        ):
            raise ValueError

    def _require_outbound_identity(self, message: OutboundExecutorMessage) -> None:
        if not isinstance(
            message,
            (
                TaskCommandResultEnvelope,
                TaskEventEnvelope,
                TaskDiscoveryBatchEnvelope,
                TaskDiscoveryCompletedEnvelope,
            ),
        ) or (
            str(message.installation_id) != self._installation_id
            or str(message.executor_id) != self._executor_id
        ):
            raise ValueError

    def _prepare_private_directory(self) -> None:
        self._reject_linked_ancestors(self._state_directory)
        self._state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._reject_linked_ancestors(self._state_directory)
        metadata = self._state_directory.stat()
        self._validate_private_directory_metadata(metadata)
        _validate_windows_private_acl(self._state_directory)

    def _prepare_private_database_file(self) -> None:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self._database_path, flags, 0o600)
        try:
            if os.name != "nt":  # pragma: no branch - mutually exclusive native platform path
                cast(Callable[[int, int], None], vars(os)["fchmod"])(descriptor, 0o600)
            self._validate_private_database_metadata(os.fstat(descriptor))
        finally:
            os.close(descriptor)

    def _connect(self) -> sqlite3.Connection:
        before_directory = self._secure_directory_identity()
        before_database = self._secure_database_identity()
        connection = sqlite3.connect(self._database_path, timeout=5, isolation_level=None)
        try:
            after_directory = self._secure_directory_identity()
            after_database = self._secure_database_identity()
            if after_directory != before_directory or after_database != before_database:
                raise ValueError
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except Exception:
            connection.close()
            raise

    def _secure_directory_identity(self) -> tuple[int, int]:
        metadata = self._state_directory.stat()
        self._validate_private_directory_metadata(metadata)
        _validate_windows_private_acl(self._state_directory)
        return _identity(metadata)

    def _secure_database_identity(self) -> tuple[int, int]:
        metadata = self._database_path.lstat()
        self._validate_private_database_metadata(metadata)
        _validate_windows_private_acl(self._database_path)
        return _identity(metadata)

    @staticmethod
    def _validate_private_directory_metadata(metadata: os.stat_result) -> None:
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(metadata):
            raise ValueError
        if os.name != "nt" and (
            metadata.st_uid != cast(Callable[[], int], vars(os)["getuid"])()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ValueError

    @staticmethod
    def _validate_private_database_metadata(metadata: os.stat_result) -> None:
        if not stat.S_ISREG(metadata.st_mode) or _is_reparse_point(metadata):
            raise ValueError
        if os.name != "nt" and (
            metadata.st_uid != cast(Callable[[], int], vars(os)["getuid"])()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ValueError

    @staticmethod
    def _reject_linked_ancestors(path: Path) -> None:
        current = path
        existing: list[Path] = []
        while True:
            existing.append(current)
            if current.parent == current:
                break
            current = current.parent
        for candidate in reversed(existing):
            try:
                metadata = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
                raise ValueError

    def _migrate_and_bind_identity(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                _migrate_v1(connection)
                version = 1
            if version == 1:
                _migrate_v2(connection)
                version = 2
            if version == 2:
                _migrate_v3(connection)
                version = 3
            if version == 3:
                _migrate_v4(connection)
                version = 4
            if version == 4:
                _migrate_v5(connection)
                version = 5
            if version == 5:
                _migrate_v6(connection)
                version = 6
            if version == 6:
                _migrate_v7(connection)
                version = 7
            if version != _SCHEMA_VERSION:
                raise ValueError
            identity = connection.execute(
                """
                SELECT installation_id, executor_id
                FROM executor_identity WHERE singleton_id = 1
                """
            ).fetchone()
            if identity is None:
                connection.execute(
                    """
                    INSERT INTO executor_identity (singleton_id, installation_id, executor_id)
                    VALUES (1, ?, ?)
                    """,
                    (self._installation_id, self._executor_id),
                )
            elif identity != (self._installation_id, self._executor_id):
                raise ValueError
            connection.commit()


def _migrate_v1(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE executor_identity (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            installation_id TEXT NOT NULL,
            executor_id TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE executor_attempt_checkpoints (
            attempt_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            last_command_sequence INTEGER NOT NULL CHECK (last_command_sequence >= 1),
            last_event_sequence INTEGER NOT NULL CHECK (last_event_sequence >= 0),
            state TEXT NOT NULL CHECK (
                state IN ('received', 'running', 'paused', 'terminal', 'outcome_uncertain')
            ),
            revision INTEGER NOT NULL CHECK (revision >= 1)
        )
        """,
        """
        CREATE TABLE executor_commands (
            message_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_sha256 BLOB NOT NULL CHECK (length(intent_sha256) = 32),
            envelope TEXT NOT NULL,
            task_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 1),
            message_type TEXT NOT NULL CHECK (
                message_type IN (
                    'task.offer', 'task.discover', 'task.pause', 'task.resume',
                    'task.cancel', 'task.emergency_stop'
                )
            ),
            correlation_id TEXT GENERATED ALWAYS AS (
                json_extract(envelope, '$.correlation_id')
            ) STORED,
            UNIQUE (attempt_id, sequence),
            FOREIGN KEY (attempt_id) REFERENCES executor_attempt_checkpoints(attempt_id)
        )
        """,
        """
        CREATE TABLE executor_outbox (
            ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal >= 1),
            message_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_sha256 BLOB NOT NULL CHECK (length(intent_sha256) = 32),
            envelope TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            delivered INTEGER NOT NULL CHECK (delivered IN (0, 1)),
            FOREIGN KEY (source_message_id) REFERENCES executor_commands(message_id)
        )
        """,
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 1")


def _migrate_v2(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE executor_platform_sessions (
            platform TEXT PRIMARY KEY CHECK (platform = 'douyin'),
            state TEXT NOT NULL CHECK (
                state IN ('healthy', 'expired', 'missing', 'risk', 'unknown')
            ),
            session_revision INTEGER NOT NULL CHECK (session_revision >= 1),
            observed_at TEXT NOT NULL
        )
        """
    )
    connection.execute("PRAGMA user_version = 2")


def _migrate_v3(connection: sqlite3.Connection) -> None:
    """Extend the closed command vocabulary without weakening existing replay rows."""

    connection.execute("ALTER TABLE executor_outbox RENAME TO executor_outbox_v2")
    connection.execute("ALTER TABLE executor_commands RENAME TO executor_commands_v2")
    connection.execute(
        """
        CREATE TABLE executor_commands (
            message_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_sha256 BLOB NOT NULL CHECK (length(intent_sha256) = 32),
            envelope TEXT NOT NULL,
            task_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 1),
            message_type TEXT NOT NULL CHECK (
                message_type IN (
                    'task.offer', 'task.discover', 'task.pause', 'task.resume',
                    'task.cancel', 'task.emergency_stop'
                )
            ),
            correlation_id TEXT GENERATED ALWAYS AS (
                json_extract(envelope, '$.correlation_id')
            ) STORED,
            UNIQUE (attempt_id, sequence),
            FOREIGN KEY (attempt_id) REFERENCES executor_attempt_checkpoints(attempt_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO executor_commands (
            message_id, idempotency_key, intent_sha256, envelope,
            task_id, attempt_id, sequence, message_type
        )
        SELECT message_id, idempotency_key, intent_sha256, envelope,
               task_id, attempt_id, sequence, message_type
        FROM executor_commands_v2
        """
    )
    connection.execute(
        """
        CREATE TABLE executor_outbox (
            ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal >= 1),
            message_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_sha256 BLOB NOT NULL CHECK (length(intent_sha256) = 32),
            envelope TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            delivered INTEGER NOT NULL CHECK (delivered IN (0, 1)),
            FOREIGN KEY (source_message_id) REFERENCES executor_commands(message_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO executor_outbox (
            ordinal, message_id, idempotency_key, intent_sha256,
            envelope, source_message_id, delivered
        )
        SELECT ordinal, message_id, idempotency_key, intent_sha256,
               envelope, source_message_id, delivered
        FROM executor_outbox_v2
        """
    )
    connection.execute("DROP TABLE executor_outbox_v2")
    connection.execute("DROP TABLE executor_commands_v2")
    connection.execute("PRAGMA user_version = 3")


def _migrate_v4(connection: sqlite3.Connection) -> None:
    """Add an installation-local action gate without storing signed tokens."""

    connection.execute(
        """
        CREATE TABLE executor_action_guard (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            engaged INTEGER NOT NULL CHECK (engaged IN (0, 1)),
            revision INTEGER NOT NULL CHECK (revision >= 0),
            changed_at TEXT,
            CHECK (
                (revision = 0 AND engaged = 0 AND changed_at IS NULL)
                OR (revision >= 1 AND changed_at IS NOT NULL)
            )
        )
        """
    )
    connection.execute(
        """
        INSERT INTO executor_action_guard (singleton_id, engaged, revision, changed_at)
        VALUES (1, 0, 0, NULL)
        """
    )
    connection.execute(
        """
        CREATE TABLE executor_action_policy (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            minimum_interval_seconds INTEGER,
            task_action_limit INTEGER,
            CHECK (
                (minimum_interval_seconds IS NULL AND task_action_limit IS NULL)
                OR (
                    minimum_interval_seconds BETWEEN 1 AND 3600
                    AND task_action_limit BETWEEN 1 AND 100
                )
            )
        )
        """
    )
    connection.execute(
        """
        INSERT INTO executor_action_policy (
            singleton_id, minimum_interval_seconds, task_action_limit
        ) VALUES (1, NULL, NULL)
        """
    )
    connection.execute(
        """
        CREATE TABLE executor_action_admissions (
            action_id TEXT PRIMARY KEY CHECK (length(action_id) = 36),
            target_id TEXT NOT NULL CHECK (length(target_id) = 36),
            execution_attempt_id TEXT NOT NULL CHECK (length(execution_attempt_id) = 36),
            task_id TEXT NOT NULL CHECK (length(task_id) = 36),
            installation_id TEXT NOT NULL CHECK (length(installation_id) = 36),
            executor_id TEXT NOT NULL CHECK (length(executor_id) = 36),
            platform TEXT NOT NULL CHECK (platform = 'douyin'),
            action TEXT NOT NULL CHECK (action IN ('browse', 'comment', 'direct_message')),
            idempotency_key TEXT NOT NULL UNIQUE,
            authorization_fingerprint BLOB NOT NULL
                CHECK (length(authorization_fingerprint) = 32),
            authorized_at TEXT NOT NULL,
            deadline_at TEXT NOT NULL,
            admitted_at TEXT NOT NULL,
            task_action_ordinal INTEGER NOT NULL
                CHECK (task_action_ordinal BETWEEN 1 AND 100),
            UNIQUE (task_id, platform, action, task_action_ordinal)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX ix_executor_action_admissions_scope_time
        ON executor_action_admissions (installation_id, platform, action, admitted_at DESC)
        """
    )
    connection.execute("PRAGMA user_version = 4")


def _migrate_v5(connection: sqlite3.Connection) -> None:
    """Add the redacted write-ahead ledger for non-repeatable platform effects."""

    connection.execute(
        """
        CREATE TABLE executor_side_effects (
            action_id TEXT PRIMARY KEY CHECK (length(action_id) = 36),
            effect_fingerprint BLOB NOT NULL CHECK (length(effect_fingerprint) = 32),
            state TEXT NOT NULL CHECK (
                state IN ('prepared', 'dispatched', 'verified', 'uncertain')
            ),
            prepared_at TEXT NOT NULL,
            dispatched_at TEXT,
            settled_at TEXT,
            verification_fingerprint BLOB,
            revision INTEGER NOT NULL,
            CHECK (
                (
                    state = 'prepared' AND revision = 1
                    AND dispatched_at IS NULL AND settled_at IS NULL
                    AND verification_fingerprint IS NULL
                )
                OR (
                    state = 'dispatched' AND revision = 2
                    AND dispatched_at IS NOT NULL AND dispatched_at >= prepared_at
                    AND settled_at IS NULL AND verification_fingerprint IS NULL
                )
                OR (
                    state = 'verified' AND revision = 3
                    AND dispatched_at IS NOT NULL AND dispatched_at >= prepared_at
                    AND settled_at IS NOT NULL AND settled_at >= dispatched_at
                    AND verification_fingerprint IS NOT NULL
                    AND length(verification_fingerprint) = 32
                )
                OR (
                    state = 'uncertain' AND revision = 3
                    AND dispatched_at IS NOT NULL AND dispatched_at >= prepared_at
                    AND settled_at IS NOT NULL AND settled_at >= dispatched_at
                    AND verification_fingerprint IS NULL
                )
            ),
            FOREIGN KEY (action_id) REFERENCES executor_action_admissions(action_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX ix_executor_side_effects_recovery
        ON executor_side_effects (state, prepared_at, action_id)
        """
    )
    connection.execute("PRAGMA user_version = 5")


def _migrate_v6(connection: sqlite3.Connection) -> None:
    """Block new platform dispatch while the authenticated transport is offline."""

    connection.execute(
        """
        ALTER TABLE executor_action_guard
        ADD COLUMN network_connected INTEGER NOT NULL DEFAULT 1
        CHECK (network_connected IN (0, 1))
        """
    )
    connection.execute("PRAGMA user_version = 6")


def _migrate_v7(connection: sqlite3.Connection) -> None:
    """Accept typed actions while retaining only a redacted command projection."""

    connection.execute("ALTER TABLE executor_outbox RENAME TO executor_outbox_v6")
    connection.execute("ALTER TABLE executor_commands RENAME TO executor_commands_v6")
    connection.execute(
        """
        CREATE TABLE executor_commands (
            message_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_sha256 BLOB NOT NULL CHECK (length(intent_sha256) = 32),
            envelope TEXT NOT NULL,
            task_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 1),
            message_type TEXT NOT NULL CHECK (
                message_type IN (
                    'task.offer', 'task.discover', 'task.pause', 'task.resume',
                    'task.cancel', 'task.emergency_stop', 'action.execute'
                )
            ),
            correlation_id TEXT GENERATED ALWAYS AS (
                json_extract(envelope, '$.correlation_id')
            ) STORED,
            UNIQUE (attempt_id, sequence),
            FOREIGN KEY (attempt_id) REFERENCES executor_attempt_checkpoints(attempt_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO executor_commands (
            message_id, idempotency_key, intent_sha256, envelope,
            task_id, attempt_id, sequence, message_type
        )
        SELECT message_id, idempotency_key, intent_sha256, envelope,
               task_id, attempt_id, sequence, message_type
        FROM executor_commands_v6
        """
    )
    connection.execute(
        """
        CREATE TABLE executor_outbox (
            ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal >= 1),
            message_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_sha256 BLOB NOT NULL CHECK (length(intent_sha256) = 32),
            envelope TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            delivered INTEGER NOT NULL CHECK (delivered IN (0, 1)),
            FOREIGN KEY (source_message_id) REFERENCES executor_commands(message_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO executor_outbox (
            ordinal, message_id, idempotency_key, intent_sha256,
            envelope, source_message_id, delivered
        )
        SELECT ordinal, message_id, idempotency_key, intent_sha256,
               envelope, source_message_id, delivered
        FROM executor_outbox_v6
        """
    )
    connection.execute("DROP TABLE executor_outbox_v6")
    connection.execute("DROP TABLE executor_commands_v6")
    connection.execute("PRAGMA user_version = 7")


def _require_pending_outbox_capacity(
    connection: sqlite3.Connection,
    envelopes: tuple[str, ...],
) -> None:
    pending = connection.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(length(CAST(envelope AS BLOB))), 0)
        FROM executor_outbox WHERE delivered = 0
        """
    ).fetchone()
    pending_entries, pending_bytes = cast(tuple[int, int], pending)
    added_bytes = sum(len(envelope.encode("utf-8")) for envelope in envelopes)
    if (
        pending_entries + len(envelopes) > _MAX_PENDING_OUTBOX_ENTRIES
        or pending_bytes + added_bytes > _MAX_PENDING_OUTBOX_BYTES
    ):
        raise ValueError


def _canonical_uuid_v4(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    parsed = UUID(value)
    if parsed.version != 4 or parsed.variant != RFC_4122 or str(parsed) != value:
        raise ValueError
    return value


def _is_canonical_uuid_v4(value: object) -> bool:
    try:
        return _canonical_uuid_v4(value) == value
    except Exception:
        return False


def _canonical_message(
    message: InboundTaskCommand | OutboundExecutorMessage,
) -> str:
    return json.dumps(
        message.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _stored_command_envelope(command: InboundTaskCommand) -> str:
    if not isinstance(command, TaskActionCommandEnvelope):
        return _canonical_message(command)
    return json.dumps(
        {
            "correlation_id": str(command.correlation_id),
            "message_type": command.message_type,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _command_intent_fingerprint(command: InboundTaskCommand) -> bytes:
    intent = command.model_dump(mode="json", exclude={"message_id", "sent_at"})
    encoded = json.dumps(
        intent,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _command_receipt(row: sqlite3.Row | tuple[object, ...], *, replayed: bool) -> CommandReceipt:
    return CommandReceipt(
        message_id=str(row[0]),
        idempotency_key=str(row[1]),
        task_id=str(row[2]),
        attempt_id=str(row[3]),
        sequence=cast(int, row[4]),
        message_type=str(row[5]),
        replayed=replayed,
    )


def _checkpoint(row: sqlite3.Row | tuple[object, ...]) -> AttemptCheckpoint:
    return AttemptCheckpoint(
        task_id=str(row[0]),
        attempt_id=str(row[1]),
        last_command_sequence=cast(int, row[2]),
        last_event_sequence=cast(int, row[3]),
        state=AttemptCheckpointState(str(row[4])),
        revision=cast(int, row[5]),
    )


def _require_platform(value: object) -> None:
    if value != "douyin" or type(value) is not str:
        raise ValueError


def _encode_utc(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


def _decode_utc(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    if _encode_utc(parsed) != value:
        raise ValueError
    return parsed


def _require_fingerprint(value: object) -> None:
    if type(value) is not bytes or len(value) != 32:
        raise ValueError


def _side_effect(row: sqlite3.Row | tuple[object, ...], *, replayed: bool) -> LocalSideEffect:
    return LocalSideEffect(
        action_id=cast(str, row[0]),
        target_id=cast(str, row[1]),
        execution_attempt_id=cast(str, row[2]),
        task_id=cast(str, row[3]),
        installation_id=cast(str, row[4]),
        executor_id=cast(str, row[5]),
        platform=cast(str, row[6]),
        action=DouyinSearchExposureAction(cast(str, row[7])),
        idempotency_key=cast(str, row[8]),
        effect_fingerprint=cast(bytes, row[9]),
        state=SideEffectState(cast(str, row[10])),
        prepared_at=_decode_utc(row[11]),
        dispatched_at=None if row[12] is None else _decode_utc(row[12]),
        settled_at=None if row[13] is None else _decode_utc(row[13]),
        verification_fingerprint=(None if row[14] is None else cast(bytes, row[14])),
        revision=cast(int, row[15]),
        replayed=replayed,
    )


def _platform_session(row: sqlite3.Row | tuple[object, ...]) -> LocalPlatformSession:
    observed_at = datetime.fromisoformat(str(row[3]).replace("Z", "+00:00"))
    return LocalPlatformSession(
        platform=str(row[0]),
        state=PlatformSessionState(str(row[1])),
        session_revision=cast(int, row[2]),
        observed_at=observed_at,
    )


def _action_admission(
    row: sqlite3.Row | tuple[object, ...],
    *,
    replayed: bool,
) -> LocalActionAdmission:
    return LocalActionAdmission(
        action_id=str(row[0]),
        target_id=str(row[1]),
        execution_attempt_id=str(row[2]),
        task_id=str(row[3]),
        installation_id=str(row[4]),
        executor_id=str(row[5]),
        platform=str(row[6]),
        action=DouyinSearchExposureAction(str(row[7])),
        idempotency_key=str(row[8]),
        authorization_fingerprint=cast(bytes, row[9]),
        authorized_at=_decode_utc(row[10]),
        deadline_at=_decode_utc(row[11]),
        admitted_at=_decode_utc(row[12]),
        task_action_ordinal=cast(int, row[13]),
        replayed=replayed,
    )


def _same_action_admission(
    row: sqlite3.Row | tuple[object, ...],
    claims: ActionAuthorizationClaims,
    authorization_fingerprint: bytes,
) -> bool:
    existing = _action_admission(row, replayed=False)
    return (
        existing.action_id == str(claims.action_id)
        and existing.target_id == str(claims.target_id)
        and existing.execution_attempt_id == str(claims.execution_attempt_id)
        and existing.task_id == str(claims.task_id)
        and existing.installation_id == str(claims.installation_id)
        and existing.executor_id == str(claims.executor_id)
        and existing.platform == claims.platform
        and existing.action is claims.action
        and existing.idempotency_key == str(claims.idempotency_key)
        and existing.authorization_fingerprint == authorization_fingerprint
        and existing.authorized_at == claims.authorized_at
        and existing.deadline_at == claims.deadline_at
    )


def _action_emergency_stop(
    row: sqlite3.Row | tuple[object, ...],
) -> LocalActionEmergencyStop:
    changed_at = None if row[2] is None else _decode_utc(row[2])
    return LocalActionEmergencyStop(
        engaged=bool(row[0]) if row[0] in (0, 1) else cast(bool, row[0]),
        revision=cast(int, row[1]),
        changed_at=changed_at,
    )


def _parse_outbound(source: str) -> OutboundExecutorMessage:
    parsed = parse_executor_message(source)
    if not isinstance(
        parsed,
        (
            TaskCommandResultEnvelope,
            TaskEventEnvelope,
            TaskDiscoveryBatchEnvelope,
            TaskDiscoveryCompletedEnvelope,
        ),
    ):
        raise ValueError
    return parsed


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _validate_windows_private_acl(path: Path) -> None:
    if os.name == "nt":
        from automation_tool.executor.windows_acl import validate_private_acl

        validate_private_acl(path)


def _is_reparse_point(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


__all__ = [
    "EXECUTOR_LEDGER_FILE_NAME",
    "AttemptCheckpoint",
    "AttemptCheckpointState",
    "CommandReceipt",
    "ExecutorActionAdmissionLimitReason",
    "ExecutorActionAdmissionLimited",
    "ExecutorLedger",
    "ExecutorLedgerRejected",
    "LocalActionAdmission",
    "LocalActionEmergencyStop",
    "LocalActionHardPolicyBinding",
    "LocalPlatformSession",
    "LocalSideEffect",
    "OutboxEntry",
    "PendingTaskControl",
    "PlatformSessionState",
    "SideEffectState",
]
