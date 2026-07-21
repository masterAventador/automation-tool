"""Enforce one nonterminal execution attempt per Installation.

Revision ID: 20260721_0025
Revises: 20260721_0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0025"
down_revision: str | None = "20260721_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NONTERMINAL_ATTEMPT_VALUES = (
    "'pending', 'offered', 'accepted', 'running', 'paused', 'awaiting_human', 'cancelling'"
)


def upgrade() -> None:
    """Replace Task-local exclusivity with Installation-wide exclusivity."""
    op.drop_index(
        "uq_execution_attempts_one_active_task",
        table_name="execution_attempts",
    )
    op.create_index(
        "uq_execution_attempts_one_active_installation",
        "execution_attempts",
        ["installation_id"],
        unique=True,
        postgresql_where=sa.text(f"status in ({_NONTERMINAL_ATTEMPT_VALUES})"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_execution_attempts_one_active_installation",
        table_name="execution_attempts",
    )
    op.create_index(
        "uq_execution_attempts_one_active_task",
        "execution_attempts",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text(f"status in ({_NONTERMINAL_ATTEMPT_VALUES})"),
    )
