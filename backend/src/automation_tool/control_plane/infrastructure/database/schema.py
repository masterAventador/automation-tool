"""Versioned SQLAlchemy schema metadata for Control Plane persistence."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from automation_tool.control_plane.domain import (
    MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS,
    MAX_TASK_EVENT_SEQUENCE,
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptStatus,
    InstallationStatus,
    TaskEventType,
    TaskEventVersion,
    TaskStatus,
)

metadata = MetaData()

installations = Table(
    "installations",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("device_public_key", LargeBinary(length=32), nullable=False),
    Column(
        "status",
        String(length=16),
        nullable=False,
        server_default=text(f"'{InstallationStatus.ACTIVE.value}'"),
    ),
    Column("revision", BigInteger(), nullable=False, server_default=text("1")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "octet_length(device_public_key) = 32",
        name="ck_installations_device_public_key_length",
    ),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_installations_id_uuid_v4",
    ),
    CheckConstraint("revision > 0", name="ck_installations_revision_positive"),
    CheckConstraint(
        "(status = 'active' and revoked_at is null) "
        "or (status = 'revoked' and revoked_at is not null)",
        name="ck_installations_revocation_state",
    ),
    CheckConstraint(
        "status in ('active', 'revoked')",
        name="ck_installations_status",
    ),
    CheckConstraint(
        "updated_at >= created_at and (revoked_at is null or revoked_at >= created_at)",
        name="ck_installations_timestamp_order",
    ),
    PrimaryKeyConstraint("id", name="pk_installations"),
    UniqueConstraint(
        "device_public_key",
        name="uq_installations_device_public_key",
    ),
)

installation_registration_challenges = Table(
    "installation_registration_challenges",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("environment_id", String(length=64), nullable=False),
    Column("bootstrap_fingerprint", LargeBinary(length=32), nullable=False),
    Column("device_public_key", LargeBinary(length=32), nullable=False),
    Column("proof_hash", LargeBinary(length=32), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("installation_id", UUID(as_uuid=True), nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint(
        "octet_length(bootstrap_fingerprint) = 32",
        name="ck_registration_challenges_bootstrap_fingerprint_length",
    ),
    CheckConstraint(
        "octet_length(device_public_key) = 32",
        name="ck_registration_challenges_device_public_key_length",
    ),
    CheckConstraint(
        "environment_id ~ '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$'",
        name="ck_registration_challenges_environment_id",
    ),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_registration_challenges_id_uuid_v4",
    ),
    CheckConstraint(
        "octet_length(proof_hash) = 32",
        name="ck_registration_challenges_proof_hash_length",
    ),
    CheckConstraint(
        "expires_at > created_at",
        name="ck_registration_challenges_expiry",
    ),
    CheckConstraint(
        "(consumed_at is null and installation_id is null) or "
        "(consumed_at is not null and installation_id is not null "
        "and consumed_at >= created_at and consumed_at < expires_at)",
        name="ck_registration_challenges_consumption_state",
    ),
    ForeignKeyConstraint(
        ["installation_id"],
        ["installations.id"],
        name="fk_registration_challenges_installation_id",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("id", name="pk_registration_challenges"),
)

device_credentials = Table(
    "device_credentials",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("version", BigInteger(), nullable=False),
    Column("scope", String(length=64), nullable=False),
    Column("secret_digest", LargeBinary(length=32), nullable=False),
    Column("status", String(length=16), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("replaced_by_id", UUID(as_uuid=True), nullable=True),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_device_credentials_id_uuid_v4",
    ),
    CheckConstraint("version > 0", name="ck_device_credentials_version_positive"),
    CheckConstraint(
        "scope = 'device.session.exchange'",
        name="ck_device_credentials_scope",
    ),
    CheckConstraint(
        "octet_length(secret_digest) = 32",
        name="ck_device_credentials_secret_digest_length",
    ),
    CheckConstraint(
        "status in ('active', 'revoked', 'rotated')",
        name="ck_device_credentials_status",
    ),
    CheckConstraint(
        "(status = 'active' and revoked_at is null and replaced_by_id is null) or "
        "(status = 'revoked' and revoked_at is not null and replaced_by_id is null) or "
        "(status = 'rotated' and revoked_at is not null and replaced_by_id is not null "
        "and replaced_by_id <> id)",
        name="ck_device_credentials_lifecycle_state",
    ),
    CheckConstraint(
        "updated_at >= created_at and (revoked_at is null or revoked_at >= created_at)",
        name="ck_device_credentials_timestamp_order",
    ),
    ForeignKeyConstraint(
        ["installation_id"],
        ["installations.id"],
        name="fk_device_credentials_installation_id",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["replaced_by_id"],
        ["device_credentials.id"],
        name="fk_device_credentials_replaced_by_id",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    ),
    PrimaryKeyConstraint("id", name="pk_device_credentials"),
    UniqueConstraint(
        "installation_id",
        "version",
        name="uq_device_credentials_installation_version",
    ),
    UniqueConstraint(
        "id",
        "installation_id",
        "version",
        name="uq_device_credentials_binding",
    ),
    UniqueConstraint("secret_digest", name="uq_device_credentials_secret_digest"),
)

Index(
    "uq_device_credentials_active_installation",
    device_credentials.c.installation_id,
    unique=True,
    postgresql_where=device_credentials.c.status == "active",
)

tasks = Table(
    "tasks",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("current_attempt_id", UUID(as_uuid=True), nullable=True),
    Column(
        "last_event_sequence",
        BigInteger(),
        nullable=False,
        server_default=text("0"),
    ),
    Column(
        "status",
        String(length=32),
        nullable=False,
        server_default=text(f"'{TaskStatus.DRAFT.value}'"),
    ),
    Column("revision", BigInteger(), nullable=False, server_default=text("1")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_tasks_id_uuid_v4",
    ),
    CheckConstraint("revision > 0", name="ck_tasks_revision_positive"),
    CheckConstraint(
        f"last_event_sequence between 0 and {MAX_TASK_EVENT_SEQUENCE}",
        name="ck_tasks_last_event_sequence_range",
    ),
    CheckConstraint(
        "status in (" + ", ".join(f"'{status.value}'" for status in TaskStatus) + ")",
        name="ck_tasks_status",
    ),
    CheckConstraint("updated_at >= created_at", name="ck_tasks_timestamp_order"),
    ForeignKeyConstraint(
        ["installation_id"],
        ["installations.id"],
        name="fk_tasks_installation_id",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("id", name="pk_tasks"),
    UniqueConstraint("id", "installation_id", name="uq_tasks_binding"),
)

Index(
    "ix_tasks_installation_updated",
    tasks.c.installation_id,
    tasks.c.updated_at,
    tasks.c.id,
)

_terminal_attempt_values = ", ".join(
    f"'{status.value}'" for status in TERMINAL_EXECUTION_ATTEMPT_STATUSES
)
_nonterminal_attempt_values = ", ".join(
    f"'{status.value}'"
    for status in ExecutionAttemptStatus
    if status not in TERMINAL_EXECUTION_ATTEMPT_STATUSES
)

execution_attempts = Table(
    "execution_attempts",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_number", BigInteger(), nullable=False),
    Column(
        "status",
        String(length=32),
        nullable=False,
        server_default=text(f"'{ExecutionAttemptStatus.PENDING.value}'"),
    ),
    Column("revision", BigInteger(), nullable=False, server_default=text("1")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_execution_attempts_id_uuid_v4",
    ),
    CheckConstraint(
        "attempt_number > 0",
        name="ck_execution_attempts_number_positive",
    ),
    CheckConstraint("revision > 0", name="ck_execution_attempts_revision_positive"),
    CheckConstraint(
        "status in (" + ", ".join(f"'{status.value}'" for status in ExecutionAttemptStatus) + ")",
        name="ck_execution_attempts_status",
    ),
    CheckConstraint(
        "updated_at >= created_at "
        "and (started_at is null or started_at >= created_at) "
        "and (finished_at is null or finished_at >= coalesce(started_at, created_at)) "
        "and updated_at >= coalesce(finished_at, started_at, created_at)",
        name="ck_execution_attempts_time_order",
    ),
    CheckConstraint(
        f"(status in ({_terminal_attempt_values}) and finished_at is not null) or "
        f"(status in ({_nonterminal_attempt_values}) and finished_at is null)",
        name="ck_execution_attempts_terminal_time",
    ),
    ForeignKeyConstraint(
        ["task_id", "installation_id"],
        ["tasks.id", "tasks.installation_id"],
        name="fk_execution_attempts_task_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("id", name="pk_execution_attempts"),
    UniqueConstraint(
        "id",
        "task_id",
        "installation_id",
        name="uq_execution_attempts_binding",
    ),
    UniqueConstraint(
        "task_id",
        "attempt_number",
        name="uq_execution_attempts_task_number",
    ),
)

Index(
    "uq_execution_attempts_one_active_task",
    execution_attempts.c.task_id,
    unique=True,
    postgresql_where=text(f"status in ({_nonterminal_attempt_values})"),
)
Index(
    "ix_execution_attempts_installation_updated",
    execution_attempts.c.installation_id,
    execution_attempts.c.updated_at,
    execution_attempts.c.id,
)

tasks.append_constraint(
    ForeignKeyConstraint(
        [tasks.c.current_attempt_id, tasks.c.id, tasks.c.installation_id],
        [
            execution_attempts.c.id,
            execution_attempts.c.task_id,
            execution_attempts.c.installation_id,
        ],
        name="fk_tasks_current_attempt_binding",
        ondelete="RESTRICT",
        use_alter=True,
    )
)

task_actions = Table(
    "task_actions",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("execution_attempt_id", UUID(as_uuid=True), nullable=False),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("ordinal", BigInteger(), nullable=False),
    Column(
        "status",
        String(length=32),
        nullable=False,
        server_default=text(f"'{ActionStatus.PLANNED.value}'"),
    ),
    Column(
        "outcome",
        String(length=32),
        nullable=False,
        server_default=text(f"'{ActionOutcome.PENDING.value}'"),
    ),
    Column("revision", BigInteger(), nullable=False, server_default=text("1")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_task_actions_id_uuid_v4",
    ),
    CheckConstraint("ordinal > 0", name="ck_task_actions_ordinal_positive"),
    CheckConstraint("revision > 0", name="ck_task_actions_revision_positive"),
    CheckConstraint(
        "status in (" + ", ".join(f"'{status.value}'" for status in ActionStatus) + ")",
        name="ck_task_actions_status",
    ),
    CheckConstraint(
        "outcome in (" + ", ".join(f"'{outcome.value}'" for outcome in ActionOutcome) + ")",
        name="ck_task_actions_outcome",
    ),
    CheckConstraint(
        "updated_at >= created_at "
        "and (finished_at is null or "
        "(finished_at >= created_at and updated_at >= finished_at))",
        name="ck_task_actions_time_order",
    ),
    CheckConstraint(
        "(status in ('planned', 'authorized', 'prepared', 'dispatched') "
        "and outcome = 'pending' and finished_at is null) or "
        "(status = 'verified' and outcome in ('succeeded', 'failed') "
        "and finished_at is not null) or "
        "(status = 'cancelled' and outcome = 'cancelled' and finished_at is not null) or "
        "(status = 'outcome_uncertain' and outcome = 'outcome_uncertain' "
        "and finished_at is not null)",
        name="ck_task_actions_result_coherence",
    ),
    ForeignKeyConstraint(
        ["execution_attempt_id", "task_id", "installation_id"],
        [
            "execution_attempts.id",
            "execution_attempts.task_id",
            "execution_attempts.installation_id",
        ],
        name="fk_task_actions_attempt_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("id", name="pk_task_actions"),
    UniqueConstraint(
        "id",
        "execution_attempt_id",
        "task_id",
        "installation_id",
        name="uq_task_actions_binding",
    ),
    UniqueConstraint(
        "execution_attempt_id",
        "ordinal",
        name="uq_task_actions_attempt_ordinal",
    ),
)

Index(
    "ix_task_actions_installation_task",
    task_actions.c.installation_id,
    task_actions.c.task_id,
    task_actions.c.execution_attempt_id,
    task_actions.c.ordinal,
)

task_events = Table(
    "task_events",
    metadata,
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("sequence", BigInteger(), nullable=False),
    Column(
        "event_version",
        String(length=8),
        nullable=False,
        server_default=text(f"'{TaskEventVersion.V1.value}'"),
    ),
    Column("event_type", String(length=64), nullable=False),
    Column("task_revision", BigInteger(), nullable=False),
    Column("task_status", String(length=32), nullable=False),
    Column("execution_attempt_id", UUID(as_uuid=True), nullable=True),
    Column("action_id", UUID(as_uuid=True), nullable=True),
    Column("source_message_id", UUID(as_uuid=True), nullable=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "safe_message",
        String(),
        nullable=True,
    ),
    CheckConstraint(
        f"sequence between 1 and {MAX_TASK_EVENT_SEQUENCE}",
        name="ck_task_events_sequence_range",
    ),
    CheckConstraint(
        f"event_version = '{TaskEventVersion.V1.value}'",
        name="ck_task_events_version",
    ),
    CheckConstraint(
        "event_type in ("
        + ", ".join(f"'{event_type.value}'" for event_type in TaskEventType)
        + ")",
        name="ck_task_events_type",
    ),
    CheckConstraint(
        "task_revision > 0",
        name="ck_task_events_task_revision_positive",
    ),
    CheckConstraint(
        "task_status in (" + ", ".join(f"'{status.value}'" for status in TaskStatus) + ")",
        name="ck_task_events_task_status",
    ),
    CheckConstraint(
        "source_message_id is null or ("
        "substring(source_message_id::text from 15 for 1) = '4' "
        "and substring(source_message_id::text from 20 for 1) in ('8', '9', 'a', 'b'))",
        name="ck_task_events_source_message_uuid_v4",
    ),
    CheckConstraint(
        "action_id is null or execution_attempt_id is not null",
        name="ck_task_events_action_requires_attempt",
    ),
    CheckConstraint(
        "recorded_at >= occurred_at",
        name="ck_task_events_time_order",
    ),
    CheckConstraint(
        "safe_message is null or ("
        f"char_length(safe_message) between 1 and {MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS} "
        "and octet_length(safe_message) <= 4096 "
        "and safe_message !~ '[[:cntrl:]]' "
        "and lower(safe_message) not like '%bearer %' "
        "and lower(safe_message) not like '%file://%' "
        "and lower(safe_message) not like '%data:%;base64,%' "
        "and lower(safe_message) !~ "
        "'(access[_-]?token|api[_-]?key|authorization|cookie|credential|password|"
        "private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)"
        "[[:space:]]*[:=]')",
        name="ck_task_events_safe_message",
    ),
    ForeignKeyConstraint(
        ["task_id", "installation_id"],
        ["tasks.id", "tasks.installation_id"],
        name="fk_task_events_task_binding",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["execution_attempt_id", "task_id", "installation_id"],
        [
            "execution_attempts.id",
            "execution_attempts.task_id",
            "execution_attempts.installation_id",
        ],
        name="fk_task_events_attempt_binding",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["action_id", "execution_attempt_id", "task_id", "installation_id"],
        [
            "task_actions.id",
            "task_actions.execution_attempt_id",
            "task_actions.task_id",
            "task_actions.installation_id",
        ],
        name="fk_task_events_action_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("task_id", "sequence", name="pk_task_events"),
    UniqueConstraint(
        "installation_id",
        "source_message_id",
        name="uq_task_events_source_message",
    ),
)

Index(
    "ix_task_events_installation_task_sequence",
    task_events.c.installation_id,
    task_events.c.task_id,
    task_events.c.sequence,
)

device_sessions = Table(
    "device_sessions",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("device_credential_id", UUID(as_uuid=True), nullable=False),
    Column("credential_version", BigInteger(), nullable=False),
    Column("capability", String(length=32), nullable=False),
    Column("secret_digest", LargeBinary(length=32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("not_before", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_device_sessions_id_uuid_v4",
    ),
    CheckConstraint(
        "credential_version > 0",
        name="ck_device_sessions_credential_version_positive",
    ),
    CheckConstraint(
        "capability in ('app.control-plane', 'executor.connect')",
        name="ck_device_sessions_capability",
    ),
    CheckConstraint(
        "octet_length(secret_digest) = 32",
        name="ck_device_sessions_secret_digest_length",
    ),
    CheckConstraint(
        "not_before <= created_at "
        "and created_at - not_before <= interval '30 seconds' "
        "and expires_at > created_at "
        "and expires_at <= created_at + interval '5 minutes'",
        name="ck_device_sessions_time_window",
    ),
    CheckConstraint(
        "revoked_at is null or revoked_at >= created_at",
        name="ck_device_sessions_revocation_time",
    ),
    ForeignKeyConstraint(
        ["device_credential_id", "installation_id", "credential_version"],
        [
            "device_credentials.id",
            "device_credentials.installation_id",
            "device_credentials.version",
        ],
        name="fk_device_sessions_credential_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("id", name="pk_device_sessions"),
    UniqueConstraint("secret_digest", name="uq_device_sessions_secret_digest"),
)

Index(
    "ix_device_sessions_installation_expiry",
    device_sessions.c.installation_id,
    device_sessions.c.expires_at,
)

__all__ = [
    "device_credentials",
    "device_sessions",
    "execution_attempts",
    "installation_registration_challenges",
    "installations",
    "metadata",
    "task_actions",
    "task_events",
    "tasks",
]
