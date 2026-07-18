"""Add Installation-scoped Task creation idempotency.

Revision ID: 20260718_0010
Revises: 20260718_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0010"
down_revision: str | None = "20260718_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill existing Tasks and enforce new creation idempotency."""
    op.add_column(
        "tasks",
        sa.Column("creation_idempotency_key", sa.String(), nullable=True),
    )
    op.execute(
        "update tasks set creation_idempotency_key = 'legacy:' || id::text "
        "where creation_idempotency_key is null"
    )
    op.alter_column("tasks", "creation_idempotency_key", nullable=False)
    op.create_check_constraint(
        "ck_tasks_creation_idempotency_key",
        "tasks",
        "creation_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
    )
    op.create_unique_constraint(
        "uq_tasks_creation_idempotency",
        "tasks",
        ["installation_id", "creation_idempotency_key"],
    )


def downgrade() -> None:
    """Remove creation idempotency without removing Task records."""
    op.drop_constraint("uq_tasks_creation_idempotency", "tasks", type_="unique")
    op.drop_constraint("ck_tasks_creation_idempotency_key", "tasks", type_="check")
    op.drop_column("tasks", "creation_idempotency_key")
