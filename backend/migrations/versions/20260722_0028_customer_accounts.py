"""Create operations-managed customer accounts and append-only audit.

Revision ID: 20260722_0028
Revises: 20260721_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from automation_tool.control_plane.domain import (
    AccountAuditActorKind,
    AccountAuditEventType,
    AccountStatus,
)

revision: str = "20260722_0028"
down_revision: str | None = "20260721_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("login_name", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text(f"'{AccountStatus.ACTIVE.value}'"),
        ),
        sa.Column(
            "credential_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_users_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "login_name ~ '^[a-z][a-z0-9._-]{2,63}$'",
            name="ck_users_login_name",
        ),
        sa.CheckConstraint(
            "status in (" + ", ".join(f"'{status.value}'" for status in AccountStatus) + ")",
            name="ck_users_status",
        ),
        sa.CheckConstraint(
            "credential_version > 0 and revision > 0",
            name="ck_users_versions_positive",
        ),
        sa.CheckConstraint(
            "(status = 'active' and locked_at is null and disabled_at is null) or "
            "(status = 'locked' and locked_at is not null and disabled_at is null) or "
            "(status = 'disabled' and locked_at is null and disabled_at is not null)",
            name="ck_users_lifecycle_state",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "and (locked_at is null or locked_at between created_at and updated_at) "
            "and (disabled_at is null or disabled_at between created_at and updated_at)",
            name="ck_users_timestamp_order",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("login_name", name="uq_users_login_name"),
        sa.UniqueConstraint("id", "credential_version", name="uq_users_id_credential_version"),
    )
    op.create_table(
        "user_password_credentials",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("pepper_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version > 0",
            name="ck_user_password_credentials_version_positive",
        ),
        sa.CheckConstraint(
            r"password_hash ~ '^\$argon2id\$v=19\$m=65536,t=3,p=4"
            r"\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$'",
            name="ck_user_password_credentials_hash",
        ),
        sa.CheckConstraint(
            "pepper_version > 0",
            name="ck_user_password_credentials_pepper_version_positive",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_user_password_credentials_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_password_credentials_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_password_credentials"),
    )
    op.create_table(
        "account_audit_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("source_fingerprint", sa.LargeBinary(length=32), nullable=True),
        sa.CheckConstraint(
            "substring(event_id::text from 15 for 1) = '4' "
            "and substring(event_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_account_audit_events_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "substring(actor_id::text from 15 for 1) = '4' "
            "and substring(actor_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_account_audit_events_actor_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "event_type in ("
            + ", ".join(f"'{event.value}'" for event in AccountAuditEventType)
            + ")",
            name="ck_account_audit_events_type",
        ),
        sa.CheckConstraint(
            "actor_kind in ("
            + ", ".join(f"'{kind.value}'" for kind in AccountAuditActorKind)
            + ")",
            name="ck_account_audit_events_actor_kind",
        ),
        sa.CheckConstraint(
            "outcome in ('succeeded', 'rejected')",
            name="ck_account_audit_events_outcome",
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9._-]{0,63}$'",
            name="ck_account_audit_events_reason_code",
        ),
        sa.CheckConstraint(
            "request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_account_audit_events_request_id",
        ),
        sa.CheckConstraint(
            "source_fingerprint is null or octet_length(source_fingerprint) = 32",
            name="ck_account_audit_events_source_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            name="fk_account_audit_events_subject_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_account_audit_events"),
    )
    op.execute(
        """
        create function reject_account_audit_mutation() returns trigger
        language plpgsql
        as $$
        begin
            raise exception using
                errcode = '55000',
                message = 'account audit events are append-only';
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger trg_account_audit_events_no_update_delete
        before update or delete on account_audit_events
        for each row execute function reject_account_audit_mutation()
        """
    )
    op.execute(
        """
        create trigger trg_account_audit_events_no_truncate
        before truncate on account_audit_events
        for each statement execute function reject_account_audit_mutation()
        """
    )


def downgrade() -> None:
    op.execute("drop trigger trg_account_audit_events_no_truncate on account_audit_events")
    op.execute("drop trigger trg_account_audit_events_no_update_delete on account_audit_events")
    op.execute("drop function reject_account_audit_mutation()")
    op.drop_table("account_audit_events")
    op.drop_table("user_password_credentials")
    op.drop_table("users")


__all__ = ["downgrade", "upgrade"]
