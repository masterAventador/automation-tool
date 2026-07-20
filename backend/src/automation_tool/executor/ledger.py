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
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, cast
from uuid import RFC_4122, UUID

from automation_tool.protocol import (
    PlatformSessionState,
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

EXECUTOR_LEDGER_FILE_NAME: Final = "executor-ledger.sqlite3"
_SCHEMA_VERSION: Final = 2
_MAX_OUTBOX_BATCH: Final = 1000
_MAX_CROSS_RUNTIME_SEQUENCE: Final = 2**53 - 1


class ExecutorLedgerRejected(RuntimeError):
    """The durable local command state cannot be used safely."""

    def __init__(self) -> None:
        super().__init__("Local Executor ledger is unavailable")


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


type OutboundExecutorMessage = TaskCommandResultEnvelope | TaskEventEnvelope


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    message: OutboundExecutorMessage
    source_message_id: str
    replayed: bool


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

    def receive_command(self, command: TaskCommandEnvelope) -> CommandReceipt:
        try:
            self._require_command_identity(command)
            envelope = _canonical_message(command)
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
                    if command.sequence != 1:
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
                    if (
                        str(checkpoint[0]) != str(command.task_id)
                        or command.sequence != int(checkpoint[1]) + 1
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

    def _require_command_identity(self, command: TaskCommandEnvelope) -> None:
        if not isinstance(command, TaskCommandEnvelope) or (
            str(command.installation_id) != self._installation_id
            or str(command.executor_id) != self._executor_id
        ):
            raise ValueError

    def _require_outbound_identity(self, message: OutboundExecutorMessage) -> None:
        if not isinstance(message, (TaskCommandResultEnvelope, TaskEventEnvelope)) or (
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
                    'task.offer', 'task.pause', 'task.resume',
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


def _canonical_uuid_v4(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    parsed = UUID(value)
    if parsed.version != 4 or parsed.variant != RFC_4122 or str(parsed) != value:
        raise ValueError
    return value


def _canonical_message(
    message: TaskCommandEnvelope | TaskCommandResultEnvelope | TaskEventEnvelope,
) -> str:
    return json.dumps(
        message.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _command_intent_fingerprint(command: TaskCommandEnvelope) -> bytes:
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


def _platform_session(row: sqlite3.Row | tuple[object, ...]) -> LocalPlatformSession:
    observed_at = datetime.fromisoformat(str(row[3]).replace("Z", "+00:00"))
    return LocalPlatformSession(
        platform=str(row[0]),
        state=PlatformSessionState(str(row[1])),
        session_revision=cast(int, row[2]),
        observed_at=observed_at,
    )


def _parse_outbound(source: str) -> OutboundExecutorMessage:
    parsed = parse_executor_message(source)
    if not isinstance(parsed, (TaskCommandResultEnvelope, TaskEventEnvelope)):
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
    "ExecutorLedger",
    "ExecutorLedgerRejected",
    "LocalPlatformSession",
    "OutboxEntry",
    "PlatformSessionState",
]
