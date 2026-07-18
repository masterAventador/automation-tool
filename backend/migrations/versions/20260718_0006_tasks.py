"""Create Installation-scoped Task state with revision CAS support.

Revision ID: 20260718_0006
Revises: 20260718_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0006"
down_revision: str | None = "20260718_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tasks with exact Installation binding and lifecycle constraints."""
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'draft'"),
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
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_tasks_id_uuid_v4",
        ),
        sa.CheckConstraint("revision > 0", name="ck_tasks_revision_positive"),
        sa.CheckConstraint(
            "status in ('draft', 'validating', 'awaiting_device', "
            "'awaiting_platform_login', 'discovering_targets', "
            "'awaiting_confirmation', 'queued', 'running', 'paused', "
            "'awaiting_human', 'cancelling', 'succeeded', "
            "'partially_succeeded', 'failed', 'cancelled', 'outcome_uncertain')",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_tasks_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_tasks_installation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.UniqueConstraint(
            "id",
            "installation_id",
            name="uq_tasks_binding",
        ),
    )
    op.create_index(
        "ix_tasks_installation_updated",
        "tasks",
        ["installation_id", "updated_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove Task persistence while preserving Installation authentication state."""
    op.drop_index("ix_tasks_installation_updated", table_name="tasks")
    op.drop_table("tasks")
