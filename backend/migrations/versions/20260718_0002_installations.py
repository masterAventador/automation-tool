"""Create installation identity and revocation state.

Revision ID: 20260718_0002
Revises: 20260718_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0002"
down_revision: str | None = "20260718_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the installation table and fail-closed state constraints."""
    op.create_table(
        "installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_public_key", sa.LargeBinary(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "octet_length(device_public_key) = 32",
            name="ck_installations_device_public_key_length",
        ),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_installations_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_installations_revision_positive",
        ),
        sa.CheckConstraint(
            "(status = 'active' and revoked_at is null) "
            "or (status = 'revoked' and revoked_at is not null)",
            name="ck_installations_revocation_state",
        ),
        sa.CheckConstraint(
            "status in ('active', 'revoked')",
            name="ck_installations_status",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at and (revoked_at is null or revoked_at >= created_at)",
            name="ck_installations_timestamp_order",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_installations"),
        sa.UniqueConstraint(
            "device_public_key",
            name="uq_installations_device_public_key",
        ),
    )


def downgrade() -> None:
    """Remove installation persistence while preserving the baseline revision."""
    op.drop_table("installations")
