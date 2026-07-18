"""Create short-lived digest-only device sessions.

Revision ID: 20260718_0005
Revises: 20260718_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0005"
down_revision: str | None = "20260718_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create sessions with an exact parent credential binding and bounded lifetime."""
    op.create_unique_constraint(
        "uq_device_credentials_binding",
        "device_credentials",
        ["id", "installation_id", "version"],
    )
    op.create_table(
        "device_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("secret_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_device_sessions_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "credential_version > 0",
            name="ck_device_sessions_credential_version_positive",
        ),
        sa.CheckConstraint(
            "capability in ('app.control-plane', 'executor.connect')",
            name="ck_device_sessions_capability",
        ),
        sa.CheckConstraint(
            "octet_length(secret_digest) = 32",
            name="ck_device_sessions_secret_digest_length",
        ),
        sa.CheckConstraint(
            "not_before <= created_at "
            "and created_at - not_before <= interval '30 seconds' "
            "and expires_at > created_at "
            "and expires_at <= created_at + interval '5 minutes'",
            name="ck_device_sessions_time_window",
        ),
        sa.CheckConstraint(
            "revoked_at is null or revoked_at >= created_at",
            name="ck_device_sessions_revocation_time",
        ),
        sa.ForeignKeyConstraint(
            ["device_credential_id", "installation_id", "credential_version"],
            [
                "device_credentials.id",
                "device_credentials.installation_id",
                "device_credentials.version",
            ],
            name="fk_device_sessions_credential_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_device_sessions"),
        sa.UniqueConstraint(
            "secret_digest",
            name="uq_device_sessions_secret_digest",
        ),
    )
    op.create_index(
        "ix_device_sessions_installation_expiry",
        "device_sessions",
        ["installation_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove short-lived sessions and their parent binding key."""
    op.drop_index(
        "ix_device_sessions_installation_expiry",
        table_name="device_sessions",
    )
    op.drop_table("device_sessions")
    op.drop_constraint(
        "uq_device_credentials_binding",
        "device_credentials",
        type_="unique",
    )
