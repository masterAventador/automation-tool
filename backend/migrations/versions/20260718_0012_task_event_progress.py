"""Persist bounded structured progress for Task event consumers.

Revision ID: 20260718_0012
Revises: 20260718_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0012"
down_revision: str | None = "20260718_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add one typed progress value without introducing arbitrary event payloads."""
    op.add_column(
        "task_events",
        sa.Column("progress_percent", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_task_events_progress_percent",
        "task_events",
        "progress_percent is null or "
        "(event_type = 'step.progress' and progress_percent between 0 and 100)",
    )


def downgrade() -> None:
    """Remove the optional structured progress projection without deleting events."""
    op.drop_constraint("ck_task_events_progress_percent", "task_events", type_="check")
    op.drop_column("task_events", "progress_percent")
