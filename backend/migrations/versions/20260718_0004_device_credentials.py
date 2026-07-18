"""Create versioned digest-only device credentials.

Revision ID: 20260718_0004
Revises: 20260718_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0004"
down_revision: str | None = "20260718_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create constrained credential history with one active version per installation."""
    op.create_table(
        "device_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("secret_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_device_credentials_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_device_credentials_version_positive",
        ),
        sa.CheckConstraint(
            "scope = 'device.session.exchange'",
            name="ck_device_credentials_scope",
        ),
        sa.CheckConstraint(
            "octet_length(secret_digest) = 32",
            name="ck_device_credentials_secret_digest_length",
        ),
        sa.CheckConstraint(
            "status in ('active', 'revoked', 'rotated')",
            name="ck_device_credentials_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' and revoked_at is null and replaced_by_id is null) or "
            "(status = 'revoked' and revoked_at is not null and replaced_by_id is null) or "
            "(status = 'rotated' and revoked_at is not null and replaced_by_id is not null "
            "and replaced_by_id <> id)",
            name="ck_device_credentials_lifecycle_state",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at and (revoked_at is null or revoked_at >= created_at)",
            name="ck_device_credentials_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_device_credentials_installation_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["device_credentials.id"],
            name="fk_device_credentials_replaced_by_id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_device_credentials"),
        sa.UniqueConstraint(
            "installation_id",
            "version",
            name="uq_device_credentials_installation_version",
        ),
        sa.UniqueConstraint(
            "secret_digest",
            name="uq_device_credentials_secret_digest",
        ),
    )
    op.create_index(
        "uq_device_credentials_active_installation",
        "device_credentials",
        ["installation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    """Remove credential state while preserving registrations and installations."""
    op.drop_index(
        "uq_device_credentials_active_installation",
        table_name="device_credentials",
    )
    op.drop_table("device_credentials")
