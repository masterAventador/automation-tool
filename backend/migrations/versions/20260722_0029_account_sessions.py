"""Create opaque account sessions, recovery tokens and login throttles.

Revision ID: 20260722_0029
Revises: 20260722_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0029"
down_revision: str | None = "20260722_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_users_lifecycle_state", "users", type_="check")
    op.drop_constraint("ck_users_timestamp_order", "users", type_="check")
    op.add_column(
        "users",
        sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_users_lifecycle_state",
        "users",
        "(status = 'active' and locked_at is null and lock_expires_at is null "
        "and disabled_at is null) or "
        "(status = 'locked' and locked_at is not null and lock_expires_at > locked_at "
        "and disabled_at is null) or "
        "(status = 'disabled' and locked_at is null and lock_expires_at is null "
        "and disabled_at is not null)",
    )
    op.create_check_constraint(
        "ck_users_timestamp_order",
        "users",
        "updated_at >= created_at "
        "and (locked_at is null or locked_at between created_at and updated_at) "
        "and (lock_expires_at is null or lock_expires_at > locked_at) "
        "and (disabled_at is null or disabled_at between created_at and updated_at)",
    )
    op.create_table(
        "account_session_families",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_account_session_families_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "credential_version > 0",
            name="ck_account_session_families_credential_version_positive",
        ),
        sa.CheckConstraint(
            "absolute_expires_at > created_at "
            "and absolute_expires_at <= created_at + interval '30 days' "
            "and (revoked_at is null or revoked_at >= created_at)",
            name="ck_account_session_families_time_order",
        ),
        sa.CheckConstraint(
            "(revoked_at is null and revocation_reason is null) or "
            "(revoked_at is not null and revocation_reason in "
            "('logout', 'refresh_reuse', 'credential_changed', 'recovery', "
            "'account_disabled'))",
            name="ck_account_session_families_revocation_state",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_account_session_families_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_session_families"),
        sa.UniqueConstraint(
            "id",
            "user_id",
            "credential_version",
            name="uq_account_session_families_binding",
        ),
    )
    op.create_table(
        "account_session_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("secret_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_account_session_tokens_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "credential_version > 0",
            name="ck_account_session_tokens_credential_version_positive",
        ),
        sa.CheckConstraint(
            "kind in ('access', 'refresh')",
            name="ck_account_session_tokens_kind",
        ),
        sa.CheckConstraint(
            "octet_length(secret_digest) = 32",
            name="ck_account_session_tokens_secret_digest_length",
        ),
        sa.CheckConstraint(
            "expires_at > created_at and "
            "((kind = 'access' and expires_at <= created_at + interval '10 minutes') or "
            "(kind = 'refresh' and expires_at <= created_at + interval '30 days')) "
            "and (consumed_at is null or consumed_at between created_at and expires_at) "
            "and (revoked_at is null or revoked_at >= created_at)",
            name="ck_account_session_tokens_time_order",
        ),
        sa.CheckConstraint(
            "(kind = 'access' and consumed_at is null and replaced_by_id is null) or "
            "(kind = 'refresh' and ((consumed_at is null and replaced_by_id is null) or "
            "(consumed_at is not null and replaced_by_id is not null and replaced_by_id <> id)))",
            name="ck_account_session_tokens_rotation_state",
        ),
        sa.ForeignKeyConstraint(
            ["family_id", "user_id", "credential_version"],
            [
                "account_session_families.id",
                "account_session_families.user_id",
                "account_session_families.credential_version",
            ],
            name="fk_account_session_tokens_family_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["account_session_tokens.id"],
            name="fk_account_session_tokens_replaced_by",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_session_tokens"),
        sa.UniqueConstraint(
            "secret_digest",
            name="uq_account_session_tokens_secret_digest",
        ),
    )
    op.create_index(
        "ix_account_session_tokens_family_kind",
        "account_session_tokens",
        ["family_id", "kind", "created_at"],
        unique=False,
    )
    op.create_table(
        "account_login_rate_limits",
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_count", sa.BigInteger(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_kind in ('identifier', 'source')",
            name="ck_account_login_rate_limits_scope_kind",
        ),
        sa.CheckConstraint(
            "octet_length(scope_fingerprint) = 32",
            name="ck_account_login_rate_limits_fingerprint_length",
        ),
        sa.CheckConstraint(
            "failure_count >= 0 and failure_count <= 20",
            name="ck_account_login_rate_limits_failure_count",
        ),
        sa.CheckConstraint(
            "updated_at >= window_started_at "
            "and (blocked_until is null or blocked_until > updated_at)",
            name="ck_account_login_rate_limits_time_order",
        ),
        sa.PrimaryKeyConstraint(
            "scope_kind",
            "scope_fingerprint",
            name="pk_account_login_rate_limits",
        ),
    )
    op.create_table(
        "account_recovery_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("secret_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("issued_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b') "
            "and substring(issued_by_actor_id::text from 15 for 1) = '4' "
            "and substring(issued_by_actor_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_account_recovery_tokens_ids_uuid_v4",
        ),
        sa.CheckConstraint(
            "credential_version > 0",
            name="ck_account_recovery_tokens_credential_version_positive",
        ),
        sa.CheckConstraint(
            "octet_length(secret_digest) = 32",
            name="ck_account_recovery_tokens_secret_digest_length",
        ),
        sa.CheckConstraint(
            "expires_at > created_at and expires_at <= created_at + interval '15 minutes' "
            "and (consumed_at is null or consumed_at between created_at and expires_at)",
            name="ck_account_recovery_tokens_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_account_recovery_tokens_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_recovery_tokens"),
        sa.UniqueConstraint(
            "secret_digest",
            name="uq_account_recovery_tokens_secret_digest",
        ),
    )


def downgrade() -> None:
    op.drop_table("account_recovery_tokens")
    op.drop_table("account_login_rate_limits")
    op.drop_index(
        "ix_account_session_tokens_family_kind",
        table_name="account_session_tokens",
    )
    op.drop_table("account_session_tokens")
    op.drop_table("account_session_families")
    op.drop_constraint("ck_users_lifecycle_state", "users", type_="check")
    op.drop_constraint("ck_users_timestamp_order", "users", type_="check")
    op.drop_column("users", "lock_expires_at")
    op.create_check_constraint(
        "ck_users_lifecycle_state",
        "users",
        "(status = 'active' and locked_at is null and disabled_at is null) or "
        "(status = 'locked' and locked_at is not null and disabled_at is null) or "
        "(status = 'disabled' and locked_at is null and disabled_at is not null)",
    )
    op.create_check_constraint(
        "ck_users_timestamp_order",
        "users",
        "updated_at >= created_at "
        "and (locked_at is null or locked_at between created_at and updated_at) "
        "and (disabled_at is null or disabled_at between created_at and updated_at)",
    )


__all__ = ["downgrade", "upgrade"]
