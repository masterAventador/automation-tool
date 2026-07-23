"""Create the Bilibili publish reconciliation table (PB-04).

Revision ID: 20260723_0033
Revises: 20260723_0032
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0033"
down_revision: str | None = "20260723_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the monotonic reconciliation outcome per publish attempt."""
    op.create_table(
        "bilibili_publish_reconciliations",
        sa.Column("publish_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("resource_id", sa.String(length=16), nullable=True),
        sa.Column("archive_state", sa.BigInteger(), nullable=True),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome in ('pending', 'published', 'rejected', 'failed')",
            name="ck_bilibili_publish_reconciliations_outcome",
        ),
        sa.CheckConstraint(
            "(settled_at is not null) = (outcome <> 'pending')",
            name="ck_bilibili_publish_reconciliations_settled_shape",
        ),
        sa.CheckConstraint(
            "(failure_code is not null) = (outcome = 'failed')",
            name="ck_bilibili_publish_reconciliations_failure_shape",
        ),
        sa.CheckConstraint(
            "failure_code is null or failure_code in "
            "('invalid_input', 'dependency_unavailable', 'platform_error')",
            name="ck_bilibili_publish_reconciliations_failure_code",
        ),
        sa.CheckConstraint(
            "outcome not in ('published', 'rejected')"
            " or (resource_id is not null and archive_state is not null)",
            name="ck_bilibili_publish_reconciliations_resolved_shape",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_bilibili_publish_reconciliations_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["publish_job_id"],
            ["bilibili_publish_attempts.publish_job_id"],
            name="fk_bilibili_publish_reconciliations_publish_job_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("publish_job_id", name="pk_bilibili_publish_reconciliations"),
    )
    op.create_index(
        "ix_bilibili_publish_reconciliations_resource_id",
        "bilibili_publish_reconciliations",
        ["resource_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bilibili_publish_reconciliations_resource_id",
        table_name="bilibili_publish_reconciliations",
    )
    op.drop_table("bilibili_publish_reconciliations")
