"""Versioned SQLAlchemy schema metadata for Control Plane persistence."""

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from automation_tool.control_plane.application.task_target_previews import (
    TASK_TARGET_CONFIRMATION_INTENT_VERSION,
)
from automation_tool.control_plane.domain import (
    ACTION_RISK_POLICY_VERSION,
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DOUYIN_SEARCH_EXPOSURE_TEMPLATE,
    MAX_ACTION_RISK_LIMIT,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_MESSAGE_TEMPLATE_CHARACTERS,
    MAX_PROJECT_TITLE_CHARACTERS,
    MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS,
    MAX_SEARCH_KEYWORD_CHARACTERS,
    MAX_TASK_EVENT_SEQUENCE,
    MAX_TASK_INTERVAL_SECONDS,
    MAX_TASK_TARGET_LIMIT,
    MAX_TRANSCRIPT_CHARACTERS,
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    AccountAuditActorKind,
    AccountAuditEventType,
    AccountStatus,
    ActionOutcome,
    ActionRiskPlatform,
    ActionStatus,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    ExecutionAttemptStatus,
    InstallationStatus,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskEventVersion,
    TaskStatus,
)
from automation_tool.protocol import (
    FAILED_ACTION_RESULT_EVIDENCE,
    MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS,
    MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS,
    MAX_DOUYIN_TARGET_ID_CHARACTERS,
    MAX_EXECUTOR_SEQUENCE,
    SUCCESS_ACTION_RESULT_EVIDENCE,
    UNCERTAIN_ACTION_RESULT_EVIDENCE,
    ActionResultEvidence,
    DouyinCandidateSource,
)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("login_name", String(length=64), nullable=False),
    Column(
        "status",
        String(length=16),
        nullable=False,
        server_default=text(f"'{AccountStatus.ACTIVE.value}'"),
    ),
    Column("credential_version", BigInteger(), nullable=False, server_default=text("1")),
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
    Column("locked_at", DateTime(timezone=True), nullable=True),
    Column("lock_expires_at", DateTime(timezone=True), nullable=True),
    Column("disabled_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_users_id_uuid_v4",
    ),
    CheckConstraint(
        "login_name ~ '^[a-z][a-z0-9._-]{2,63}$'",
        name="ck_users_login_name",
    ),
    CheckConstraint(
        "status in (" + ", ".join(f"'{status.value}'" for status in AccountStatus) + ")",
        name="ck_users_status",
    ),
    CheckConstraint(
        "credential_version > 0 and revision > 0",
        name="ck_users_versions_positive",
    ),
    CheckConstraint(
        "(status = 'active' and locked_at is null and lock_expires_at is null "
        "and disabled_at is null) or "
        "(status = 'locked' and locked_at is not null and lock_expires_at > locked_at "
        "and disabled_at is null) or "
        "(status = 'disabled' and locked_at is null and lock_expires_at is null "
        "and disabled_at is not null)",
        name="ck_users_lifecycle_state",
    ),
    CheckConstraint(
        "updated_at >= created_at "
        "and (locked_at is null or locked_at between created_at and updated_at) "
        "and (lock_expires_at is null or lock_expires_at > locked_at) "
        "and (disabled_at is null or disabled_at between created_at and updated_at)",
        name="ck_users_timestamp_order",
    ),
    PrimaryKeyConstraint("id", name="pk_users"),
    UniqueConstraint("login_name", name="uq_users_login_name"),
    UniqueConstraint("id", "credential_version", name="uq_users_id_credential_version"),
)

user_password_credentials = Table(
    "user_password_credentials",
    metadata,
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("version", BigInteger(), nullable=False),
    Column("password_hash", String(length=255), nullable=False),
    Column("pepper_version", BigInteger(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "version > 0",
        name="ck_user_password_credentials_version_positive",
    ),
    CheckConstraint(
        r"password_hash ~ '^\$argon2id\$v=19\$m=65536,t=3,p=4"
        r"\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$'",
        name="ck_user_password_credentials_hash",
    ),
    CheckConstraint(
        "pepper_version > 0",
        name="ck_user_password_credentials_pepper_version_positive",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_user_password_credentials_timestamp_order",
    ),
    ForeignKeyConstraint(
        ["user_id"],
        ["users.id"],
        name="fk_user_password_credentials_user",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("user_id", name="pk_user_password_credentials"),
)

account_audit_events = Table(
    "account_audit_events",
    metadata,
    Column("event_id", UUID(as_uuid=True), nullable=False),
    Column("event_type", String(length=32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor_kind", String(length=16), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("subject_user_id", UUID(as_uuid=True), nullable=True),
    Column("outcome", String(length=16), nullable=False),
    Column("reason_code", String(length=64), nullable=False),
    Column("request_id", String(length=128), nullable=False),
    Column("source_fingerprint", LargeBinary(length=32), nullable=True),
    CheckConstraint(
        "substring(event_id::text from 15 for 1) = '4' "
        "and substring(event_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_account_audit_events_id_uuid_v4",
    ),
    CheckConstraint(
        "substring(actor_id::text from 15 for 1) = '4' "
        "and substring(actor_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_account_audit_events_actor_id_uuid_v4",
    ),
    CheckConstraint(
        "event_type in (" + ", ".join(f"'{event.value}'" for event in AccountAuditEventType) + ")",
        name="ck_account_audit_events_type",
    ),
    CheckConstraint(
        "actor_kind in (" + ", ".join(f"'{kind.value}'" for kind in AccountAuditActorKind) + ")",
        name="ck_account_audit_events_actor_kind",
    ),
    CheckConstraint(
        "outcome in ('succeeded', 'rejected')",
        name="ck_account_audit_events_outcome",
    ),
    CheckConstraint(
        "reason_code ~ '^[a-z][a-z0-9._-]{0,63}$'",
        name="ck_account_audit_events_reason_code",
    ),
    CheckConstraint(
        "request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
        name="ck_account_audit_events_request_id",
    ),
    CheckConstraint(
        "source_fingerprint is null or octet_length(source_fingerprint) = 32",
        name="ck_account_audit_events_source_fingerprint",
    ),
    ForeignKeyConstraint(
        ["subject_user_id"],
        ["users.id"],
        name="fk_account_audit_events_subject_user",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("event_id", name="pk_account_audit_events"),
)

account_session_families = Table(
    "account_session_families",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("credential_version", BigInteger(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("absolute_expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("revocation_reason", String(length=32), nullable=True),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_account_session_families_id_uuid_v4",
    ),
    CheckConstraint(
        "credential_version > 0",
        name="ck_account_session_families_credential_version_positive",
    ),
    CheckConstraint(
        "absolute_expires_at > created_at "
        "and absolute_expires_at <= created_at + interval '30 days' "
        "and (revoked_at is null or revoked_at >= created_at)",
        name="ck_account_session_families_time_order",
    ),
    CheckConstraint(
        "(revoked_at is null and revocation_reason is null) or "
        "(revoked_at is not null and revocation_reason in "
        "('logout', 'refresh_reuse', 'credential_changed', 'recovery', 'account_disabled'))",
        name="ck_account_session_families_revocation_state",
    ),
    ForeignKeyConstraint(
        ["user_id"],
        ["users.id"],
        name="fk_account_session_families_user",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("id", name="pk_account_session_families"),
    UniqueConstraint(
        "id",
        "user_id",
        "credential_version",
        name="uq_account_session_families_binding",
    ),
)

account_session_tokens = Table(
    "account_session_tokens",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("family_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("credential_version", BigInteger(), nullable=False),
    Column("kind", String(length=16), nullable=False),
    Column("secret_digest", LargeBinary(length=32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("replaced_by_id", UUID(as_uuid=True), nullable=True),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_account_session_tokens_id_uuid_v4",
    ),
    CheckConstraint(
        "credential_version > 0",
        name="ck_account_session_tokens_credential_version_positive",
    ),
    CheckConstraint(
        "kind in ('access', 'refresh')",
        name="ck_account_session_tokens_kind",
    ),
    CheckConstraint(
        "octet_length(secret_digest) = 32",
        name="ck_account_session_tokens_secret_digest_length",
    ),
    CheckConstraint(
        "expires_at > created_at and "
        "((kind = 'access' and expires_at <= created_at + interval '10 minutes') or "
        "(kind = 'refresh' and expires_at <= created_at + interval '30 days')) "
        "and (consumed_at is null or consumed_at between created_at and expires_at) "
        "and (revoked_at is null or revoked_at >= created_at)",
        name="ck_account_session_tokens_time_order",
    ),
    CheckConstraint(
        "(kind = 'access' and consumed_at is null and replaced_by_id is null) or "
        "(kind = 'refresh' and ((consumed_at is null and replaced_by_id is null) or "
        "(consumed_at is not null and replaced_by_id is not null and replaced_by_id <> id)))",
        name="ck_account_session_tokens_rotation_state",
    ),
    ForeignKeyConstraint(
        ["family_id", "user_id", "credential_version"],
        [
            "account_session_families.id",
            "account_session_families.user_id",
            "account_session_families.credential_version",
        ],
        name="fk_account_session_tokens_family_binding",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["replaced_by_id"],
        ["account_session_tokens.id"],
        name="fk_account_session_tokens_replaced_by",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    ),
    PrimaryKeyConstraint("id", name="pk_account_session_tokens"),
    UniqueConstraint("secret_digest", name="uq_account_session_tokens_secret_digest"),
)

Index(
    "ix_account_session_tokens_family_kind",
    account_session_tokens.c.family_id,
    account_session_tokens.c.kind,
    account_session_tokens.c.created_at,
)

account_login_rate_limits = Table(
    "account_login_rate_limits",
    metadata,
    Column("scope_kind", String(length=16), nullable=False),
    Column("scope_fingerprint", LargeBinary(length=32), nullable=False),
    Column("window_started_at", DateTime(timezone=True), nullable=False),
    Column("failure_count", BigInteger(), nullable=False),
    Column("blocked_until", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "scope_kind in ('identifier', 'source')",
        name="ck_account_login_rate_limits_scope_kind",
    ),
    CheckConstraint(
        "octet_length(scope_fingerprint) = 32",
        name="ck_account_login_rate_limits_fingerprint_length",
    ),
    CheckConstraint(
        "failure_count >= 0 and failure_count <= 20",
        name="ck_account_login_rate_limits_failure_count",
    ),
    CheckConstraint(
        "updated_at >= window_started_at and (blocked_until is null or blocked_until > updated_at)",
        name="ck_account_login_rate_limits_time_order",
    ),
    PrimaryKeyConstraint(
        "scope_kind",
        "scope_fingerprint",
        name="pk_account_login_rate_limits",
    ),
)

account_recovery_tokens = Table(
    "account_recovery_tokens",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("credential_version", BigInteger(), nullable=False),
    Column("secret_digest", LargeBinary(length=32), nullable=False),
    Column("issued_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b') "
        "and substring(issued_by_actor_id::text from 15 for 1) = '4' "
        "and substring(issued_by_actor_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_account_recovery_tokens_ids_uuid_v4",
    ),
    CheckConstraint(
        "credential_version > 0",
        name="ck_account_recovery_tokens_credential_version_positive",
    ),
    CheckConstraint(
        "octet_length(secret_digest) = 32",
        name="ck_account_recovery_tokens_secret_digest_length",
    ),
    CheckConstraint(
        "expires_at > created_at and expires_at <= created_at + interval '15 minutes' "
        "and (consumed_at is null or consumed_at between created_at and expires_at)",
        name="ck_account_recovery_tokens_time_order",
    ),
    ForeignKeyConstraint(
        ["user_id"],
        ["users.id"],
        name="fk_account_recovery_tokens_user",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("id", name="pk_account_recovery_tokens"),
    UniqueConstraint("secret_digest", name="uq_account_recovery_tokens_secret_digest"),
)

installations = Table(
    "installations",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("device_public_key", LargeBinary(length=32), nullable=False),
    Column("owner_user_id", UUID(as_uuid=True), nullable=True),
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
    ForeignKeyConstraint(
        ["owner_user_id"],
        ["users.id"],
        name="fk_installations_owner_user",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "device_public_key",
        name="uq_installations_device_public_key",
    ),
)

Index("ix_installations_owner_user", installations.c.owner_user_id)

platform_session_health = Table(
    "platform_session_health",
    metadata,
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("platform", String(length=16), nullable=False),
    Column("state", String(length=16), nullable=False),
    Column("session_revision", BigInteger(), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "platform = 'douyin'",
        name="ck_platform_session_health_platform",
    ),
    CheckConstraint(
        "state in ('healthy', 'expired', 'missing', 'risk', 'unknown')",
        name="ck_platform_session_health_state",
    ),
    CheckConstraint(
        "session_revision > 0",
        name="ck_platform_session_health_revision_positive",
    ),
    CheckConstraint(
        "updated_at >= observed_at",
        name="ck_platform_session_health_time_order",
    ),
    ForeignKeyConstraint(
        ["installation_id"],
        ["installations.id"],
        name="fk_platform_session_health_installation_id",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "platform",
        name="pk_platform_session_health",
    ),
)

platform_session_gates = Table(
    "platform_session_gates",
    metadata,
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("platform", String(length=16), nullable=False),
    Column("state", String(length=16), nullable=False),
    Column("session_revision", BigInteger(), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("platform = 'douyin'", name="ck_platform_session_gates_platform"),
    CheckConstraint("state = 'blocked'", name="ck_platform_session_gates_state"),
    CheckConstraint(
        "session_revision > 0",
        name="ck_platform_session_gates_revision_positive",
    ),
    ForeignKeyConstraint(
        ["installation_id"],
        ["installations.id"],
        name="fk_platform_session_gates_installation_id",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "platform",
        name="pk_platform_session_gates",
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

account_installation_binding_challenges = Table(
    "account_installation_binding_challenges",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
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
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_account_binding_challenges_id_uuid_v4",
    ),
    CheckConstraint(
        "octet_length(device_public_key) = 32",
        name="ck_account_binding_challenges_device_key_length",
    ),
    CheckConstraint(
        "octet_length(proof_hash) = 32",
        name="ck_account_binding_challenges_proof_hash_length",
    ),
    CheckConstraint(
        "expires_at > created_at and expires_at <= created_at + interval '5 minutes'",
        name="ck_account_binding_challenges_expiry",
    ),
    CheckConstraint(
        "(consumed_at is null and installation_id is null) or "
        "(consumed_at is not null and installation_id is not null "
        "and consumed_at >= created_at and consumed_at < expires_at)",
        name="ck_account_binding_challenges_consumption_state",
    ),
    ForeignKeyConstraint(
        ["user_id"],
        ["users.id"],
        name="fk_account_binding_challenges_user",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["installation_id"],
        ["installations.id"],
        name="fk_account_binding_challenges_installation",
        ondelete="CASCADE",
    ),
    PrimaryKeyConstraint("id", name="pk_account_binding_challenges"),
)

Index(
    "ix_account_binding_challenges_user_expiry",
    account_installation_binding_challenges.c.user_id,
    account_installation_binding_challenges.c.expires_at,
)

Index(
    "uq_account_binding_challenges_pending_device",
    account_installation_binding_challenges.c.user_id,
    account_installation_binding_challenges.c.device_public_key,
    unique=True,
    postgresql_where=account_installation_binding_challenges.c.consumed_at.is_(None),
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
    Column("creation_idempotency_key", String(), nullable=False),
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
        "creation_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
        name="ck_tasks_creation_idempotency_key",
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
    UniqueConstraint(
        "installation_id",
        "creation_idempotency_key",
        name="uq_tasks_creation_idempotency",
    ),
)

Index(
    "ix_tasks_installation_updated",
    tasks.c.installation_id,
    tasks.c.updated_at,
    tasks.c.id,
)

douyin_search_exposure_definitions = Table(
    "douyin_search_exposure_definitions",
    metadata,
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column(
        "template",
        String(length=64),
        nullable=False,
        server_default=text(f"'{DOUYIN_SEARCH_EXPOSURE_TEMPLATE}'"),
    ),
    Column("search_keyword", String(), nullable=False),
    Column("action", String(length=32), nullable=False),
    Column("message_template", String(), nullable=True),
    Column("target_limit", BigInteger(), nullable=False),
    Column("minimum_interval_seconds", BigInteger(), nullable=False),
    Column("maximum_interval_seconds", BigInteger(), nullable=False),
    Column(
        "preview_required",
        Boolean(),
        nullable=False,
        server_default=text("true"),
    ),
    Column(
        "final_confirmation_required",
        Boolean(),
        nullable=False,
        server_default=text("true"),
    ),
    CheckConstraint(
        f"template = '{DOUYIN_SEARCH_EXPOSURE_TEMPLATE}'",
        name="ck_douyin_search_exposure_template",
    ),
    CheckConstraint(
        f"char_length(search_keyword) between 1 and {MAX_SEARCH_KEYWORD_CHARACTERS} "
        f"and octet_length(search_keyword) <= {MAX_SEARCH_KEYWORD_CHARACTERS * 4} "
        "and btrim(search_keyword) = search_keyword "
        "and search_keyword !~ '[[:cntrl:]]'",
        name="ck_douyin_search_exposure_keyword",
    ),
    CheckConstraint(
        "action in ("
        + ", ".join(f"'{action.value}'" for action in DouyinSearchExposureAction)
        + ")",
        name="ck_douyin_search_exposure_action",
    ),
    CheckConstraint(
        "(action = 'browse' and message_template is null) or "
        "(action in ('comment', 'direct_message') and message_template is not null)",
        name="ck_douyin_search_exposure_message_presence",
    ),
    CheckConstraint(
        "message_template is null or ("
        f"char_length(message_template) between 1 and {MAX_MESSAGE_TEMPLATE_CHARACTERS} "
        f"and octet_length(message_template) <= {MAX_MESSAGE_TEMPLATE_CHARACTERS * 4} "
        "and btrim(message_template) = message_template "
        "and message_template !~ '[[:cntrl:]]' "
        "and lower(message_template) not like '%bearer %' "
        "and lower(message_template) not like '%file://%' "
        "and lower(message_template) !~ "
        "'(access[_-]?token|api[_-]?key|authorization|cookie|credential|password|"
        "private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)"
        "[[:space:]]*[:=]' "
        "and btrim(replace(message_template, '{{target_display_name}}', '')) <> '' "
        "and replace(message_template, '{{target_display_name}}', '') !~ '[{}]')",
        name="ck_douyin_search_exposure_message_safe",
    ),
    CheckConstraint(
        f"target_limit between 1 and {MAX_TASK_TARGET_LIMIT}",
        name="ck_douyin_search_exposure_target_limit",
    ),
    CheckConstraint(
        "minimum_interval_seconds between 1 and "
        f"{MAX_TASK_INTERVAL_SECONDS} and maximum_interval_seconds between "
        f"minimum_interval_seconds and {MAX_TASK_INTERVAL_SECONDS}",
        name="ck_douyin_search_exposure_interval",
    ),
    CheckConstraint(
        "preview_required and final_confirmation_required",
        name="ck_douyin_search_exposure_mandatory_confirmation",
    ),
    ForeignKeyConstraint(
        ["task_id", "installation_id"],
        ["tasks.id", "tasks.installation_id"],
        name="fk_douyin_search_exposure_task_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("task_id", name="pk_douyin_search_exposure_definitions"),
    UniqueConstraint(
        "task_id",
        "installation_id",
        name="uq_douyin_search_exposure_binding",
    ),
)

task_targets = Table(
    "task_targets",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("ordinal", BigInteger(), nullable=False),
    Column("platform_target_id", String(length=MAX_DOUYIN_TARGET_ID_CHARACTERS), nullable=False),
    Column("dedupe_key", String(length=50), nullable=False),
    Column("display_name", String(length=MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS), nullable=False),
    Column(
        "public_handle",
        String(length=MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS),
        nullable=True,
    ),
    Column("source", String(length=32), nullable=False),
    Column("page_revision", BigInteger(), nullable=False),
    Column("disposition", String(length=32), nullable=False),
    Column("policy_version", String(length=64), nullable=False),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_task_targets_id_uuid_v4",
    ),
    CheckConstraint(
        f"ordinal between 1 and {MAX_TASK_TARGET_LIMIT}",
        name="ck_task_targets_ordinal_range",
    ),
    CheckConstraint(
        "platform_target_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'",
        name="ck_task_targets_platform_target_id",
    ),
    CheckConstraint(
        "dedupe_key ~ '^atdck1_[A-Za-z0-9_-]{43}$'",
        name="ck_task_targets_candidate_key",
    ),
    CheckConstraint(
        f"char_length(display_name) between 1 and {MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS} "
        f"and octet_length(display_name) <= {MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS * 4} "
        "and btrim(display_name) = display_name "
        "and display_name !~ '[[:cntrl:]]' "
        "and lower(display_name) not like '%bearer %' "
        "and lower(display_name) not like '%file://%' "
        "and lower(display_name) not like '%data:%;base64,%' "
        "and lower(display_name) !~ "
        "'(access[_-]?token|api[_-]?key|authorization|cookie|credential|password|"
        "private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)"
        "[[:space:]]*[:=]'",
        name="ck_task_targets_display_name",
    ),
    CheckConstraint(
        "public_handle is null or public_handle ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'",
        name="ck_task_targets_public_handle",
    ),
    CheckConstraint(
        "source in (" + ", ".join(f"'{source.value}'" for source in DouyinCandidateSource) + ")",
        name="ck_task_targets_source",
    ),
    CheckConstraint(
        f"page_revision between 1 and {MAX_EXECUTOR_SEQUENCE}",
        name="ck_task_targets_page_revision_range",
    ),
    CheckConstraint(
        "disposition in ("
        + ", ".join(f"'{value.value}'" for value in DouyinCandidateDisposition)
        + ")",
        name="ck_task_targets_disposition",
    ),
    CheckConstraint(
        f"policy_version = '{DOUYIN_CANDIDATE_POLICY_VERSION}'",
        name="ck_task_targets_policy_version",
    ),
    CheckConstraint(
        "created_at >= evaluated_at",
        name="ck_task_targets_time_order",
    ),
    ForeignKeyConstraint(
        ["task_id", "installation_id"],
        ["tasks.id", "tasks.installation_id"],
        name="fk_task_targets_task_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("id", name="pk_task_targets"),
    UniqueConstraint(
        "id",
        "task_id",
        "installation_id",
        name="uq_task_targets_binding",
    ),
    UniqueConstraint(
        "id",
        "task_id",
        "installation_id",
        "page_revision",
        name="uq_task_targets_preview_binding",
    ),
    UniqueConstraint(
        "task_id",
        "installation_id",
        "ordinal",
        name="uq_task_targets_task_ordinal",
    ),
)

Index(
    "ix_task_targets_installation_task_page",
    task_targets.c.installation_id,
    task_targets.c.task_id,
    task_targets.c.page_revision,
    task_targets.c.ordinal,
    task_targets.c.id,
)

Index(
    "ix_task_targets_installation_history",
    task_targets.c.installation_id,
    task_targets.c.dedupe_key,
    task_targets.c.evaluated_at,
)

task_target_exclusions = Table(
    "task_target_exclusions",
    metadata,
    Column("target_id", UUID(as_uuid=True), nullable=False),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("page_revision", BigInteger(), nullable=False),
    Column("excluded_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "substring(target_id::text from 15 for 1) = '4' "
        "and substring(target_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_task_target_exclusions_target_uuid_v4",
    ),
    CheckConstraint(
        f"page_revision between 1 and {MAX_EXECUTOR_SEQUENCE}",
        name="ck_task_target_exclusions_page_revision_range",
    ),
    ForeignKeyConstraint(
        ["target_id", "task_id", "installation_id", "page_revision"],
        [
            "task_targets.id",
            "task_targets.task_id",
            "task_targets.installation_id",
            "task_targets.page_revision",
        ],
        name="fk_task_target_exclusions_preview_binding",
        ondelete="CASCADE",
    ),
    PrimaryKeyConstraint("target_id", name="pk_task_target_exclusions"),
)

Index(
    "ix_task_target_exclusions_installation_task_page",
    task_target_exclusions.c.installation_id,
    task_target_exclusions.c.task_id,
    task_target_exclusions.c.page_revision,
)

task_target_confirmations = Table(
    "task_target_confirmations",
    metadata,
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("page_revision", BigInteger(), nullable=False),
    Column("selection_task_revision", BigInteger(), nullable=False),
    Column("confirmed_task_revision", BigInteger(), nullable=False),
    Column("selected_target_count", BigInteger(), nullable=False),
    Column("action", String(length=32), nullable=False),
    Column("message_template", String(), nullable=True),
    Column("intent_version", String(length=64), nullable=False),
    Column("intent_fingerprint", LargeBinary(length=32), nullable=False),
    Column("source_message_id", UUID(as_uuid=True), nullable=False),
    Column("source_idempotency_key", String(), nullable=False),
    Column("source_fingerprint", LargeBinary(length=32), nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint(
        f"page_revision between 1 and {MAX_EXECUTOR_SEQUENCE}",
        name="ck_task_target_confirmations_page_revision_range",
    ),
    CheckConstraint(
        "selection_task_revision > 0 and confirmed_task_revision = selection_task_revision + 1",
        name="ck_task_target_confirmations_revision_order",
    ),
    CheckConstraint(
        f"selected_target_count between 1 and {MAX_TASK_TARGET_LIMIT}",
        name="ck_task_target_confirmations_selected_count",
    ),
    CheckConstraint(
        "action in ("
        + ", ".join(f"'{action.value}'" for action in DouyinSearchExposureAction)
        + ")",
        name="ck_task_target_confirmations_action",
    ),
    CheckConstraint(
        "(action = 'browse' and message_template is null) or "
        "(action in ('comment', 'direct_message') and message_template is not null)",
        name="ck_task_target_confirmations_message_presence",
    ),
    CheckConstraint(
        "message_template is null or ("
        f"char_length(message_template) between 1 and {MAX_MESSAGE_TEMPLATE_CHARACTERS} "
        f"and octet_length(message_template) <= {MAX_MESSAGE_TEMPLATE_CHARACTERS * 4} "
        "and btrim(message_template) = message_template "
        "and message_template !~ '[[:cntrl:]]' "
        "and lower(message_template) not like '%bearer %' "
        "and lower(message_template) not like '%file://%' "
        "and lower(message_template) !~ "
        "'(access[_-]?token|api[_-]?key|authorization|cookie|credential|password|"
        "private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)"
        "[[:space:]]*[:=]' "
        "and btrim(replace(message_template, '{{target_display_name}}', '')) <> '' "
        "and replace(message_template, '{{target_display_name}}', '') !~ '[{}]')",
        name="ck_task_target_confirmations_message_safe",
    ),
    CheckConstraint(
        f"intent_version = '{TASK_TARGET_CONFIRMATION_INTENT_VERSION}'",
        name="ck_task_target_confirmations_intent_version",
    ),
    CheckConstraint(
        "octet_length(intent_fingerprint) = 32",
        name="ck_task_target_confirmations_intent_fingerprint_length",
    ),
    CheckConstraint(
        "substring(source_message_id::text from 15 for 1) = '4' "
        "and substring(source_message_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_task_target_confirmations_message_uuid_v4",
    ),
    CheckConstraint(
        "source_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
        name="ck_task_target_confirmations_idempotency_key",
    ),
    CheckConstraint(
        "octet_length(source_fingerprint) = 32",
        name="ck_task_target_confirmations_fingerprint_length",
    ),
    CheckConstraint(
        "created_at >= confirmed_at",
        name="ck_task_target_confirmations_time_order",
    ),
    ForeignKeyConstraint(
        ["task_id", "installation_id"],
        ["tasks.id", "tasks.installation_id"],
        name="fk_task_target_confirmations_task_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("task_id", name="pk_task_target_confirmations"),
    UniqueConstraint(
        "installation_id",
        "source_message_id",
        name="uq_task_target_confirmations_source_message",
    ),
    UniqueConstraint(
        "installation_id",
        "source_idempotency_key",
        name="uq_task_target_confirmations_source_idempotency",
    ),
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
    "uq_execution_attempts_one_active_installation",
    execution_attempts.c.installation_id,
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
    Column("evidence_code", String(length=64), nullable=True),
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
        "and outcome = 'pending' and evidence_code is null and finished_at is null) or "
        "(status = 'verified' and outcome in ('succeeded', 'failed') "
        "and evidence_code is not null and finished_at is not null) or "
        "(status = 'cancelled' and outcome = 'cancelled' "
        f"and evidence_code = '{ActionResultEvidence.ACTION_CANCELLED.value}' "
        "and finished_at is not null) or "
        "(status = 'outcome_uncertain' and outcome = 'outcome_uncertain' "
        "and evidence_code is not null and finished_at is not null)",
        name="ck_task_actions_result_coherence",
    ),
    CheckConstraint(
        "evidence_code is null or "
        "(outcome = 'succeeded' and evidence_code in ("
        + ", ".join(f"'{value.value}'" for value in sorted(SUCCESS_ACTION_RESULT_EVIDENCE, key=str))
        + ")) or (outcome = 'failed' and evidence_code in ("
        + ", ".join(f"'{value.value}'" for value in sorted(FAILED_ACTION_RESULT_EVIDENCE, key=str))
        + ")) or (outcome = 'cancelled' and evidence_code = "
        f"'{ActionResultEvidence.ACTION_CANCELLED.value}') or "
        "(outcome = 'outcome_uncertain' and evidence_code in ("
        + ", ".join(
            f"'{value.value}'" for value in sorted(UNCERTAIN_ACTION_RESULT_EVIDENCE, key=str)
        )
        + "))",
        name="ck_task_actions_evidence_coherence",
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

task_actions.append_constraint(
    UniqueConstraint(
        task_actions.c.id,
        task_actions.c.execution_attempt_id,
        task_actions.c.task_id,
        task_actions.c.installation_id,
        task_actions.c.ordinal,
        name="uq_task_actions_risk_binding",
    )
)

task_targets.append_constraint(
    UniqueConstraint(
        task_targets.c.id,
        task_targets.c.task_id,
        task_targets.c.installation_id,
        task_targets.c.ordinal,
        name="uq_task_targets_action_binding",
    )
)

Index(
    "ix_task_actions_installation_task",
    task_actions.c.installation_id,
    task_actions.c.task_id,
    task_actions.c.execution_attempt_id,
    task_actions.c.ordinal,
)

action_risk_authorizations = Table(
    "action_risk_authorizations",
    metadata,
    Column("action_id", UUID(as_uuid=True), nullable=False),
    Column("target_id", UUID(as_uuid=True), nullable=False),
    Column("execution_attempt_id", UUID(as_uuid=True), nullable=False),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("ordinal", BigInteger(), nullable=False),
    Column("platform", String(length=32), nullable=False),
    Column("action", String(length=32), nullable=False),
    Column("policy_version", String(length=64), nullable=False),
    Column("effective_minimum_interval_seconds", BigInteger(), nullable=False),
    Column("task_action_limit", BigInteger(), nullable=False),
    Column("daily_action_limit", BigInteger(), nullable=False),
    Column("consecutive_failure_threshold", BigInteger(), nullable=False),
    Column("task_count_after", BigInteger(), nullable=False),
    Column("daily_count_after", BigInteger(), nullable=False),
    Column("authorized_day", Date(), nullable=False),
    Column("authorized_at", DateTime(timezone=True), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint(
        "platform in (" + ", ".join(f"'{platform.value}'" for platform in ActionRiskPlatform) + ")",
        name="ck_action_risk_authorizations_platform",
    ),
    CheckConstraint(
        "action in ("
        + ", ".join(f"'{action.value}'" for action in DouyinSearchExposureAction)
        + ")",
        name="ck_action_risk_authorizations_action",
    ),
    CheckConstraint(
        f"policy_version = '{ACTION_RISK_POLICY_VERSION}'",
        name="ck_action_risk_authorizations_policy_version",
    ),
    CheckConstraint(
        f"effective_minimum_interval_seconds between 1 and {MAX_TASK_INTERVAL_SECONDS}",
        name="ck_action_risk_authorizations_interval",
    ),
    CheckConstraint(
        f"task_action_limit between 1 and {MAX_TASK_TARGET_LIMIT} "
        f"and daily_action_limit between 1 and {MAX_ACTION_RISK_LIMIT} "
        f"and consecutive_failure_threshold between 1 and {MAX_ACTION_RISK_LIMIT}",
        name="ck_action_risk_authorizations_limits",
    ),
    CheckConstraint(
        "task_count_after between 1 and task_action_limit "
        "and daily_count_after between 1 and daily_action_limit",
        name="ck_action_risk_authorizations_counts",
    ),
    CheckConstraint(
        "authorized_day = (authorized_at at time zone 'UTC')::date",
        name="ck_action_risk_authorizations_utc_day",
    ),
    CheckConstraint(
        "created_at >= authorized_at",
        name="ck_action_risk_authorizations_time_order",
    ),
    ForeignKeyConstraint(
        ["action_id", "execution_attempt_id", "task_id", "installation_id", "ordinal"],
        [
            "task_actions.id",
            "task_actions.execution_attempt_id",
            "task_actions.task_id",
            "task_actions.installation_id",
            "task_actions.ordinal",
        ],
        name="fk_action_risk_authorizations_action_binding",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["target_id", "task_id", "installation_id", "ordinal"],
        [
            "task_targets.id",
            "task_targets.task_id",
            "task_targets.installation_id",
            "task_targets.ordinal",
        ],
        name="fk_action_risk_authorizations_target_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("action_id", name="pk_action_risk_authorizations"),
    UniqueConstraint(
        "action_id",
        "installation_id",
        "platform",
        "action",
        name="uq_action_risk_authorizations_result_binding",
    ),
    UniqueConstraint(
        "task_id",
        "platform",
        "action",
        "task_count_after",
        name="uq_action_risk_authorizations_task_count",
    ),
    UniqueConstraint(
        "installation_id",
        "platform",
        "action",
        "authorized_day",
        "daily_count_after",
        name="uq_action_risk_authorizations_daily_count",
    ),
)

action_risk_results = Table(
    "action_risk_results",
    metadata,
    Column("action_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("platform", String(length=32), nullable=False),
    Column("action", String(length=32), nullable=False),
    Column("outcome", String(length=32), nullable=False),
    Column("consecutive_failures_after", BigInteger(), nullable=False),
    Column("consecutive_failure_threshold", BigInteger(), nullable=False),
    Column("circuit_open_after", Boolean(), nullable=False),
    Column("triggered_handoff", Boolean(), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint(
        "platform in (" + ", ".join(f"'{platform.value}'" for platform in ActionRiskPlatform) + ")",
        name="ck_action_risk_results_platform",
    ),
    CheckConstraint(
        "action in ("
        + ", ".join(f"'{action.value}'" for action in DouyinSearchExposureAction)
        + ")",
        name="ck_action_risk_results_action",
    ),
    CheckConstraint(
        "outcome in ('succeeded', 'failed')",
        name="ck_action_risk_results_outcome",
    ),
    CheckConstraint(
        f"consecutive_failures_after between 0 and {MAX_ACTION_RISK_LIMIT} "
        f"and consecutive_failure_threshold between 1 and {MAX_ACTION_RISK_LIMIT}",
        name="ck_action_risk_results_limits",
    ),
    CheckConstraint(
        "(outcome = 'failed' and consecutive_failures_after > 0) or "
        "(outcome = 'succeeded' and "
        "(consecutive_failures_after = 0 or circuit_open_after))",
        name="ck_action_risk_results_failure_count",
    ),
    CheckConstraint(
        "not triggered_handoff or (outcome = 'failed' and circuit_open_after "
        "and consecutive_failures_after >= consecutive_failure_threshold)",
        name="ck_action_risk_results_handoff",
    ),
    CheckConstraint(
        "created_at >= observed_at",
        name="ck_action_risk_results_time_order",
    ),
    ForeignKeyConstraint(
        ["action_id", "installation_id", "platform", "action"],
        [
            "action_risk_authorizations.action_id",
            "action_risk_authorizations.installation_id",
            "action_risk_authorizations.platform",
            "action_risk_authorizations.action",
        ],
        name="fk_action_risk_results_authorization",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("action_id", name="pk_action_risk_results"),
    UniqueConstraint(
        "action_id",
        "installation_id",
        "platform",
        "action",
        name="uq_action_risk_results_scope_binding",
    ),
)

action_failure_circuits = Table(
    "action_failure_circuits",
    metadata,
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("platform", String(length=32), nullable=False),
    Column("action", String(length=32), nullable=False),
    Column("consecutive_failures", BigInteger(), nullable=False),
    Column("circuit_open", Boolean(), nullable=False),
    Column("revision", BigInteger(), nullable=False),
    Column("last_action_id", UUID(as_uuid=True), nullable=False),
    Column("opened_by_action_id", UUID(as_uuid=True), nullable=True),
    Column("opened_at", DateTime(timezone=True), nullable=True),
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
        "platform in (" + ", ".join(f"'{platform.value}'" for platform in ActionRiskPlatform) + ")",
        name="ck_action_failure_circuits_platform",
    ),
    CheckConstraint(
        "action in ("
        + ", ".join(f"'{action.value}'" for action in DouyinSearchExposureAction)
        + ")",
        name="ck_action_failure_circuits_action",
    ),
    CheckConstraint(
        f"consecutive_failures between 0 and {MAX_ACTION_RISK_LIMIT} and revision > 0",
        name="ck_action_failure_circuits_counters",
    ),
    CheckConstraint(
        "(circuit_open and consecutive_failures > 0 "
        "and opened_by_action_id is not null and opened_at is not null) or "
        "(not circuit_open and opened_by_action_id is null and opened_at is null)",
        name="ck_action_failure_circuits_open_state",
    ),
    CheckConstraint(
        "updated_at >= created_at "
        "and (opened_at is null or (opened_at >= created_at and updated_at >= opened_at))",
        name="ck_action_failure_circuits_time_order",
    ),
    ForeignKeyConstraint(
        ["installation_id"],
        ["installations.id"],
        name="fk_action_failure_circuits_installation",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["last_action_id", "installation_id", "platform", "action"],
        [
            "action_risk_results.action_id",
            "action_risk_results.installation_id",
            "action_risk_results.platform",
            "action_risk_results.action",
        ],
        name="fk_action_failure_circuits_last_result",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["opened_by_action_id", "installation_id", "platform", "action"],
        [
            "action_risk_results.action_id",
            "action_risk_results.installation_id",
            "action_risk_results.platform",
            "action_risk_results.action",
        ],
        name="fk_action_failure_circuits_open_result",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint(
        "installation_id",
        "platform",
        "action",
        name="pk_action_failure_circuits",
    ),
)

Index(
    "ix_action_risk_results_observed",
    action_risk_results.c.observed_at,
    action_risk_results.c.action_id,
)

Index(
    "ix_action_risk_authorizations_scope_time",
    action_risk_authorizations.c.installation_id,
    action_risk_authorizations.c.platform,
    action_risk_authorizations.c.action,
    action_risk_authorizations.c.authorized_at,
    action_risk_authorizations.c.action_id,
)

Index(
    "ix_action_risk_authorizations_task_scope",
    action_risk_authorizations.c.task_id,
    action_risk_authorizations.c.platform,
    action_risk_authorizations.c.action,
    action_risk_authorizations.c.authorized_at,
    action_risk_authorizations.c.action_id,
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
    Column("source_idempotency_key", String(), nullable=False),
    Column("source_fingerprint", LargeBinary(length=32), nullable=False),
    Column("progress_percent", BigInteger(), nullable=True),
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
        "source_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
        name="ck_task_events_source_idempotency_key",
    ),
    CheckConstraint(
        "octet_length(source_fingerprint) = 32",
        name="ck_task_events_source_fingerprint_length",
    ),
    CheckConstraint(
        "progress_percent is null or "
        "(event_type = 'step.progress' and progress_percent between 0 and 100)",
        name="ck_task_events_progress_percent",
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
    UniqueConstraint(
        "installation_id",
        "source_idempotency_key",
        name="uq_task_events_source_idempotency",
    ),
)

Index(
    "ix_task_events_installation_task_sequence",
    task_events.c.installation_id,
    task_events.c.task_id,
    task_events.c.sequence,
)

task_commands = Table(
    "task_commands",
    metadata,
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("execution_attempt_id", UUID(as_uuid=True), nullable=False),
    Column("sequence", BigInteger(), nullable=False),
    Column("command_type", String(length=32), nullable=False),
    Column("target_confirmation_message_id", UUID(as_uuid=True), nullable=True),
    Column("action_id", UUID(as_uuid=True), nullable=True),
    Column("task_event_sequence_baseline", BigInteger(), nullable=True),
    Column(
        "status",
        String(length=16),
        nullable=False,
        server_default=text(f"'{TaskCommandStatus.PENDING.value}'"),
    ),
    Column("idempotency_key", String(), nullable=False),
    Column("revision", BigInteger(), nullable=False, server_default=text("1")),
    Column(
        "delivery_attempts",
        BigInteger(),
        nullable=False,
        server_default=text("0"),
    ),
    Column("next_delivery_at", DateTime(timezone=True), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("delivered_at", DateTime(timezone=True), nullable=True),
    Column("acknowledged_at", DateTime(timezone=True), nullable=True),
    Column("response_message_id", UUID(as_uuid=True), nullable=True),
    Column("response_type", String(length=32), nullable=True),
    Column("deadline_at", DateTime(timezone=True), nullable=False),
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
        "substring(message_id::text from 15 for 1) = '4' "
        "and substring(message_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_task_commands_message_uuid_v4",
    ),
    CheckConstraint(
        "substring(correlation_id::text from 15 for 1) = '4' "
        "and substring(correlation_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_task_commands_correlation_uuid_v4",
    ),
    CheckConstraint(
        "response_message_id is null or ("
        "substring(response_message_id::text from 15 for 1) = '4' "
        "and substring(response_message_id::text from 20 for 1) in ('8', '9', 'a', 'b'))",
        name="ck_task_commands_response_uuid_v4",
    ),
    CheckConstraint(
        "target_confirmation_message_id is null or ("
        "substring(target_confirmation_message_id::text from 15 for 1) = '4' "
        "and substring(target_confirmation_message_id::text from 20 for 1) "
        "in ('8', '9', 'a', 'b'))",
        name="ck_task_commands_target_confirmation_uuid_v4",
    ),
    CheckConstraint(
        "action_id is null or ("
        "substring(action_id::text from 15 for 1) = '4' "
        "and substring(action_id::text from 20 for 1) in ('8', '9', 'a', 'b'))",
        name="ck_task_commands_action_uuid_v4",
    ),
    CheckConstraint(
        f"sequence between 1 and {MAX_TASK_EVENT_SEQUENCE}",
        name="ck_task_commands_sequence_range",
    ),
    CheckConstraint(
        "command_type in (" + ", ".join(f"'{command.value}'" for command in TaskCommandType) + ")",
        name="ck_task_commands_type",
    ),
    CheckConstraint(
        "status in (" + ", ".join(f"'{status.value}'" for status in TaskCommandStatus) + ")",
        name="ck_task_commands_status",
    ),
    CheckConstraint(
        "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
        name="ck_task_commands_idempotency_key",
    ),
    CheckConstraint("revision > 0", name="ck_task_commands_revision_positive"),
    CheckConstraint(
        "delivery_attempts >= 0",
        name="ck_task_commands_delivery_attempts_nonnegative",
    ),
    CheckConstraint(
        "deadline_at > created_at and updated_at >= created_at "
        "and (next_delivery_at is null or "
        "(next_delivery_at >= created_at and next_delivery_at < deadline_at)) "
        "and (lease_expires_at is null or "
        "(lease_expires_at > updated_at and lease_expires_at <= deadline_at)) "
        "and (delivered_at is null or "
        "(delivered_at >= created_at and delivered_at <= deadline_at)) "
        "and (acknowledged_at is null or "
        "(delivered_at is not null and acknowledged_at >= delivered_at "
        "and acknowledged_at <= deadline_at))",
        name="ck_task_commands_time_order",
    ),
    CheckConstraint(
        "(status = 'pending' and next_delivery_at is not null "
        "and lease_expires_at is null and delivered_at is null "
        "and acknowledged_at is null and response_message_id is null "
        "and response_type is null) or "
        "(status = 'in_flight' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is not null "
        "and delivered_at is null and acknowledged_at is null "
        "and response_message_id is null and response_type is null) or "
        "(status = 'delivered' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is null "
        "and response_message_id is null and response_type is null) or "
        "(status = 'acknowledged' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is not null "
        "and response_message_id is not null "
        "and response_type in ('task.accept', 'task.control_ack', 'action.accept')) or "
        "(status = 'rejected' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is not null "
        "and response_message_id is not null "
        "and response_type in ('task.reject', 'action.reject')) or "
        "(status = 'expired' and next_delivery_at is null "
        "and lease_expires_at is null and acknowledged_at is null "
        "and response_message_id is null and response_type is null "
        "and (delivered_at is null or delivery_attempts > 0))",
        name="ck_task_commands_status_coherence",
    ),
    CheckConstraint(
        "response_type is null or "
        "(command_type in ('task.offer', 'task.discover') "
        "and response_type in ('task.accept', 'task.reject')) or "
        "(command_type = 'action.execute' "
        "and response_type in ('action.accept', 'action.reject')) or "
        "(command_type in ('task.pause', 'task.resume', 'task.cancel', "
        "'task.emergency_stop') and response_type = 'task.control_ack')",
        name="ck_task_commands_response_coherence",
    ),
    CheckConstraint(
        "target_confirmation_message_id is null or "
        "command_type in ('task.offer', 'action.execute')",
        name="ck_task_commands_target_confirmation_scope",
    ),
    CheckConstraint(
        "(command_type = 'action.execute' and action_id is not null "
        "and target_confirmation_message_id is not null) or "
        "(command_type <> 'action.execute' and action_id is null)",
        name="ck_task_commands_action_scope",
    ),
    CheckConstraint(
        f"(command_type = 'task.offer' and task_event_sequence_baseline between 0 and "
        f"{MAX_TASK_EVENT_SEQUENCE - 1}) or "
        "(command_type <> 'task.offer' and task_event_sequence_baseline is null)",
        name="ck_task_commands_offer_event_baseline_scope",
    ),
    ForeignKeyConstraint(
        ["execution_attempt_id", "task_id", "installation_id"],
        [
            "execution_attempts.id",
            "execution_attempts.task_id",
            "execution_attempts.installation_id",
        ],
        name="fk_task_commands_attempt_binding",
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
        name="fk_task_commands_action_binding",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("message_id", name="pk_task_commands"),
    UniqueConstraint(
        "execution_attempt_id",
        "sequence",
        name="uq_task_commands_attempt_sequence",
    ),
    UniqueConstraint(
        "installation_id",
        "idempotency_key",
        name="uq_task_commands_idempotency",
    ),
    UniqueConstraint(
        "installation_id",
        "response_message_id",
        name="uq_task_commands_response_message",
    ),
    UniqueConstraint("action_id", name="uq_task_commands_action"),
)

Index(
    "ix_task_commands_outbox_due",
    task_commands.c.status,
    task_commands.c.next_delivery_at,
    task_commands.c.deadline_at,
    task_commands.c.message_id,
)
Index(
    "ix_task_commands_installation_task_created",
    task_commands.c.installation_id,
    task_commands.c.task_id,
    task_commands.c.created_at,
    task_commands.c.message_id,
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

bilibili_publish_attempts = Table(
    "bilibili_publish_attempts",
    metadata,
    Column("publish_job_id", UUID(as_uuid=True), nullable=False),
    Column("phase", String(length=20), nullable=False),
    Column("request_digest", String(length=64), nullable=False),
    Column("material_file_name", String(length=255), nullable=False),
    Column("material_size_bytes", BigInteger(), nullable=False),
    Column("material_duration_seconds", BigInteger(), nullable=False),
    Column("material_sha256", String(length=64), nullable=False),
    Column("title", String(length=80), nullable=False),
    Column("tid", BigInteger(), nullable=False),
    Column("tag", String(length=200), nullable=False),
    Column("copyright", BigInteger(), nullable=False),
    Column("description", String(length=250), nullable=True),
    Column("source", String(length=200), nullable=True),
    Column("no_reprint", BigInteger(), nullable=False),
    Column("upload_type", String(length=1), nullable=False),
    Column("part_size_bytes", BigInteger(), nullable=False),
    Column("part_count", BigInteger(), nullable=False),
    Column("has_cover", Boolean(), nullable=False),
    Column("upload_token", String(length=512), nullable=True),
    Column("cover_url", String(length=1024), nullable=True),
    Column("video_uploaded_at", DateTime(timezone=True), nullable=True),
    Column("dispatched_at", DateTime(timezone=True), nullable=True),
    Column("settled_at", DateTime(timezone=True), nullable=True),
    Column("resource_id", String(length=16), nullable=True),
    Column("failure_code", String(length=32), nullable=True),
    Column("platform_error_code", BigInteger(), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "phase in ('prepared', 'video_uploaded', 'dispatched', "
        "'submitted', 'failed', 'outcome_uncertain')",
        name="ck_bilibili_publish_attempts_phase",
    ),
    CheckConstraint(
        "char_length(request_digest) = 64 and char_length(material_sha256) = 64",
        name="ck_bilibili_publish_attempts_digests",
    ),
    CheckConstraint(
        "material_size_bytes > 0 and material_duration_seconds > 0",
        name="ck_bilibili_publish_attempts_material_bounds",
    ),
    CheckConstraint(
        "copyright in (1, 2) and no_reprint in (0, 1) and tid > 0",
        name="ck_bilibili_publish_attempts_submission_fields",
    ),
    CheckConstraint(
        "(upload_type = '0' and part_size_bytes = 0 and part_count = 0)"
        " or (upload_type = '1' and part_size_bytes > 0 and part_count > 0)",
        name="ck_bilibili_publish_attempts_upload_plan",
    ),
    CheckConstraint(
        "phase <> 'prepared' or (video_uploaded_at is null"
        " and dispatched_at is null and settled_at is null)",
        name="ck_bilibili_publish_attempts_prepared_shape",
    ),
    CheckConstraint(
        "phase <> 'video_uploaded' or (video_uploaded_at is not null"
        " and upload_token is not null and dispatched_at is null and settled_at is null)",
        name="ck_bilibili_publish_attempts_uploaded_shape",
    ),
    CheckConstraint(
        "phase <> 'dispatched' or (video_uploaded_at is not null"
        " and dispatched_at is not null and settled_at is null)",
        name="ck_bilibili_publish_attempts_dispatched_shape",
    ),
    CheckConstraint(
        "phase not in ('submitted', 'failed', 'outcome_uncertain')"
        " or (video_uploaded_at is not null and dispatched_at is not null"
        " and settled_at is not null)",
        name="ck_bilibili_publish_attempts_settled_shape",
    ),
    CheckConstraint(
        "(resource_id is not null) = (phase = 'submitted')",
        name="ck_bilibili_publish_attempts_resource_id_shape",
    ),
    CheckConstraint(
        "((failure_code is not null) = (phase = 'failed'))"
        " and ((platform_error_code is not null) = (phase = 'failed'))",
        name="ck_bilibili_publish_attempts_failure_shape",
    ),
    CheckConstraint(
        "failure_code is null or failure_code in "
        "('invalid_input', 'dependency_unavailable', 'platform_error')",
        name="ck_bilibili_publish_attempts_failure_code",
    ),
    CheckConstraint(
        "cover_url is null or has_cover",
        name="ck_bilibili_publish_attempts_cover_shape",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_bilibili_publish_attempts_time_order",
    ),
    PrimaryKeyConstraint("publish_job_id", name="pk_bilibili_publish_attempts"),
)

bilibili_publish_reconciliations = Table(
    "bilibili_publish_reconciliations",
    metadata,
    Column("publish_job_id", UUID(as_uuid=True), nullable=False),
    Column("outcome", String(length=16), nullable=False),
    Column("resource_id", String(length=16), nullable=True),
    Column("archive_state", BigInteger(), nullable=True),
    Column("failure_code", String(length=32), nullable=True),
    Column("last_checked_at", DateTime(timezone=True), nullable=True),
    Column("settled_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "outcome in ('pending', 'published', 'rejected', 'failed')",
        name="ck_bilibili_publish_reconciliations_outcome",
    ),
    CheckConstraint(
        "(settled_at is not null) = (outcome <> 'pending')",
        name="ck_bilibili_publish_reconciliations_settled_shape",
    ),
    CheckConstraint(
        "(failure_code is not null) = (outcome = 'failed')",
        name="ck_bilibili_publish_reconciliations_failure_shape",
    ),
    CheckConstraint(
        "failure_code is null or failure_code in "
        "('invalid_input', 'dependency_unavailable', 'platform_error')",
        name="ck_bilibili_publish_reconciliations_failure_code",
    ),
    CheckConstraint(
        "outcome not in ('published', 'rejected')"
        " or (resource_id is not null and archive_state is not null)",
        name="ck_bilibili_publish_reconciliations_resolved_shape",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_bilibili_publish_reconciliations_time_order",
    ),
    ForeignKeyConstraint(
        ["publish_job_id"],
        ["bilibili_publish_attempts.publish_job_id"],
        name="fk_bilibili_publish_reconciliations_publish_job_id",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint("publish_job_id", name="pk_bilibili_publish_reconciliations"),
)

Index(
    "ix_bilibili_publish_reconciliations_resource_id",
    bilibili_publish_reconciliations.c.resource_id,
)

bilibili_upload_parts = Table(
    "bilibili_upload_parts",
    metadata,
    Column("publish_job_id", UUID(as_uuid=True), nullable=False),
    Column("part_number", BigInteger(), nullable=False),
    Column("size_bytes", BigInteger(), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "part_number between 1 and 512",
        name="ck_bilibili_upload_parts_part_number",
    ),
    CheckConstraint("size_bytes > 0", name="ck_bilibili_upload_parts_size"),
    ForeignKeyConstraint(
        ["publish_job_id"],
        ["bilibili_publish_attempts.publish_job_id"],
        name="fk_bilibili_upload_parts_publish_job_id",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint(
        "publish_job_id",
        "part_number",
        name="pk_bilibili_upload_parts",
    ),
)

editing_projects = Table(
    "editing_projects",
    metadata,
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("title", String(length=MAX_PROJECT_TITLE_CHARACTERS), nullable=False),
    Column("output_width", Integer(), nullable=False),
    Column("output_height", Integer(), nullable=False),
    Column("output_fps", Integer(), nullable=False),
    # `EditingProject`'s font-key pattern admits at most 64 characters.
    Column("caption_font_key", String(length=64), nullable=False),
    Column("caption_font_px", Integer(), nullable=False),
    Column("caption_stroke_px", Integer(), nullable=False),
    Column("caption_line_spacing", Double(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("project_id", name="pk_editing_projects"),
)

editing_project_installations = Table(
    "editing_project_installations",
    metadata,
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("installation_id", UUID(as_uuid=True), nullable=False),
    ForeignKeyConstraint(
        ["project_id"],
        ["editing_projects.project_id"],
        name="fk_editing_project_installations_project",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["installation_id"],
        ["installations.id"],
        name="fk_editing_project_installations_installation",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint(
        "project_id",
        name="pk_editing_project_installations",
    ),
)

Index(
    "ix_editing_project_installations_installation_project",
    editing_project_installations.c.installation_id,
    editing_project_installations.c.project_id,
)

materials = Table(
    "materials",
    metadata,
    Column("material_id", UUID(as_uuid=True), nullable=False),
    Column("kind", String(length=16), nullable=False),
    # Nullable because the domain says so, not because the value is optional
    # paperwork: an image has no duration and audio has no frame size, and both
    # are refused if they carry one. The column cannot express which absence
    # goes with which kind, so it permits all four and `Material` decides.
    Column("duration_ms", Integer(), nullable=True),
    Column("width", Integer(), nullable=True),
    Column("height", Integer(), nullable=True),
    # Fixed width, because a SHA-256 hex digest is exactly 64 characters. Note
    # that `CHAR` is `bpchar`: it blank-pads anything shorter and compares
    # ignoring trailing blanks. Neither touches a digest written through the
    # repository -- there is nothing to pad -- but a row arriving any other way
    # comes back padded, and hydration refuses it because spaces are not hex.
    Column("content_digest", CHAR(length=64), nullable=False),
    Column("has_audio", Boolean(), nullable=False),
    Column("audio_loudness_lufs", Double(), nullable=True),
    Column("has_speech", Boolean(), nullable=False),
    # The three JSONB columns are NOT NULL: "no speech" is an empty array, not
    # an absent one, which keeps `[]` and NULL from both meaning nothing.
    # PostgreSQL never looks inside these, so their shape is entirely on
    # hydration -- see the migration's docstring.
    Column("speech_segments_ms", JSONB(), nullable=False),
    Column("speech_transcript", String(length=MAX_TRANSCRIPT_CHARACTERS), nullable=True),
    Column("shot_boundaries_ms", JSONB(), nullable=False),
    Column("ai_description", String(length=MAX_DESCRIPTION_CHARACTERS), nullable=True),
    Column("ai_tags", JSONB(), nullable=False),
    Column("description_source", String(length=16), nullable=False),
    # The one genuinely optional value here: a material nobody has described
    # yet, and one whose description a person wrote, both leave this NULL.
    Column("described_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("material_id", name="pk_materials"),
    # "The same file must not be imported twice" is the half of the rule the
    # domain cannot hold: `Material` only knows a digest's format, never what
    # else is stored. Two callers hashing the same file concurrently both find
    # nothing and both proceed, so the refusal has to be here.
    UniqueConstraint("content_digest", name="uq_materials_content_digest"),
)

timelines = Table(
    "timelines",
    metadata,
    Column("timeline_id", UUID(as_uuid=True), nullable=False),
    Column("revision", Integer(), nullable=False),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("duration_ms", Integer(), nullable=False),
    # The whole cut as one document: tracks, their clips, and a clip's incoming
    # transition. Not split into a clips table because a revision is an
    # immutable snapshot that the renderer reads whole, and nothing in this
    # release queries across clips. PostgreSQL never looks inside it, so its
    # shape is entirely hydration's problem -- see the migration's docstring.
    Column("tracks", JSONB(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # Composite, because a revision is a snapshot rather than a version counter
    # on one mutable row: every revision of a timeline is its own row and none
    # of them is ever updated. This is also what refuses a second write of the
    # same revision, whoever is racing.
    PrimaryKeyConstraint("timeline_id", "revision", name="pk_timelines"),
    ForeignKeyConstraint(
        ["project_id"],
        ["editing_projects.project_id"],
        name="fk_timelines_project",
    ),
    # A superkey of the primary key, so it refuses nothing the primary key would
    # not have refused already. **Its only reason to exist is to be the target
    # of `editing_jobs`'s composite foreign key**, which is what makes "a job's
    # project is the project its timeline belongs to" a structural fact rather
    # than an application check two concurrent callers can both pass. PostgreSQL
    # requires a foreign key's target columns to be covered by a unique
    # constraint spelling exactly those columns, and the primary key spells only
    # two of the three. Dropping this as redundant would silently remove that
    # invariant's only enforcement.
    UniqueConstraint("timeline_id", "revision", "project_id", name="uq_timelines_revision_project"),
)

editing_jobs = Table(
    "editing_jobs",
    metadata,
    Column("job_id", UUID(as_uuid=True), nullable=False),
    # Carried here as well as on the timeline. The redundancy is deliberate --
    # every read of a job needs its project without a join -- and it is exactly
    # why the two have to be made to agree; see the foreign key below.
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("timeline_id", UUID(as_uuid=True), nullable=False),
    Column("timeline_revision", Integer(), nullable=False),
    # Wide enough for every member of the two enumerations with room to spare.
    # The domain owns the values; a unit test asserts the longest member of each
    # still fits, so a width narrowed below what the domain can produce fails
    # there rather than as a truncation on whichever job hits it first.
    Column("status", String(length=16), nullable=False),
    # Both nullable because most states carry neither, which is all a column can
    # say. *Which* absence belongs to which state -- a succeeded job has an
    # artifact and no failure code, a failed one the reverse, every other state
    # neither -- is `EditingJob._validate_facts_match_status`, and hydration is
    # where a stored row has to meet it.
    Column("failure_code", String(length=32), nullable=True),
    Column("output_artifact_id", UUID(as_uuid=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("job_id", name="pk_editing_jobs"),
    # One key holding two rules that no domain object can hold, because no
    # aggregate here references another: that the revision a job names really
    # exists, and that the project it claims is the project that revision
    # belongs to. A plain foreign key on `project_id` alone would be satisfied
    # by any stored project, including the wrong one, and an application check
    # comparing the two is one that two concurrent callers both pass.
    #
    # The target is `uq_timelines_revision_project`, which exists for this and
    # nothing else: PostgreSQL requires a foreign key's referenced columns to be
    # covered by a unique constraint spelling exactly those columns, and
    # `pk_timelines` covers only two of the three. The order here matches that
    # constraint's declared order, and a reference in any other order is one
    # PostgreSQL refuses to create.
    ForeignKeyConstraint(
        ["timeline_id", "timeline_revision", "project_id"],
        ["timelines.timeline_id", "timelines.revision", "timelines.project_id"],
        name="fk_editing_jobs_timeline_revision",
    ),
)

# At most one render of a revision may be waiting to start. Two callers asking
# to render the same cut is a duplicate request rather than two pieces of work,
# and looking for an existing one before inserting is a check both of them pass.
#
# **The predicate is load-bearing, not a refinement.** Without it this would be
# a plain unique index and a revision could be rendered exactly once ever: a
# failed render could not be retried and a cancelled one could not be resumed,
# because the finished row would still occupy the slot. Restricting it to queued
# rows is what makes the slot free itself as soon as the job starts, finishes or
# is cancelled.
Index(
    "uq_editing_jobs_queued_timeline_revision",
    editing_jobs.c.timeline_id,
    editing_jobs.c.timeline_revision,
    unique=True,
    postgresql_where=editing_jobs.c.status == "queued",
)

__all__ = [
    "account_audit_events",
    "account_installation_binding_challenges",
    "account_login_rate_limits",
    "account_recovery_tokens",
    "account_session_families",
    "account_session_tokens",
    "action_risk_authorizations",
    "bilibili_publish_attempts",
    "bilibili_publish_reconciliations",
    "bilibili_upload_parts",
    "device_credentials",
    "device_sessions",
    "douyin_search_exposure_definitions",
    "editing_jobs",
    "editing_projects",
    "execution_attempts",
    "installation_registration_challenges",
    "installations",
    "materials",
    "metadata",
    "platform_session_gates",
    "platform_session_health",
    "task_actions",
    "task_commands",
    "task_events",
    "task_targets",
    "tasks",
    "timelines",
    "user_password_credentials",
    "users",
]
