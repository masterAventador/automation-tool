"""Add durable Executor event intent identity.

Revision ID: 20260718_0011
Revises: 20260718_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0011"
down_revision: str | None = "20260718_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill existing events and enforce scoped replay identity."""
    op.add_column(
        "task_events",
        sa.Column("source_idempotency_key", sa.String(), nullable=True),
    )
    op.add_column(
        "task_events",
        sa.Column("source_fingerprint", sa.LargeBinary(length=32), nullable=True),
    )
    op.execute(
        "update task_events set source_idempotency_key = "
        "'legacy:event:' || task_id::text || ':' || sequence::text "
        "where source_idempotency_key is null"
    )
    op.execute(
        "update task_events set source_fingerprint = "
        "decode(md5(task_id::text || ':' || sequence::text) || "
        "md5(installation_id::text || ':' || event_type), 'hex') "
        "where source_fingerprint is null"
    )
    op.alter_column("task_events", "source_idempotency_key", nullable=False)
    op.alter_column("task_events", "source_fingerprint", nullable=False)
    op.create_check_constraint(
        "ck_task_events_source_idempotency_key",
        "task_events",
        "source_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
    )
    op.create_check_constraint(
        "ck_task_events_source_fingerprint_length",
        "task_events",
        "octet_length(source_fingerprint) = 32",
    )
    op.create_unique_constraint(
        "uq_task_events_source_idempotency",
        "task_events",
        ["installation_id", "source_idempotency_key"],
    )


def downgrade() -> None:
    """Remove replay intent fields without removing event facts."""
    op.drop_constraint("uq_task_events_source_idempotency", "task_events", type_="unique")
    op.drop_constraint("ck_task_events_source_fingerprint_length", "task_events", type_="check")
    op.drop_constraint("ck_task_events_source_idempotency_key", "task_events", type_="check")
    op.drop_column("task_events", "source_fingerprint")
    op.drop_column("task_events", "source_idempotency_key")
