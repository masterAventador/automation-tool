"""Persist the exact Task event watermark carried by task.offer.

Revision ID: 20260721_0027
Revises: 20260721_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from automation_tool.control_plane.domain import MAX_TASK_EVENT_SEQUENCE

revision: str = "20260721_0027"
down_revision: str | None = "20260721_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_commands",
        sa.Column("task_event_sequence_baseline", sa.BigInteger(), nullable=True),
    )
    op.execute(
        """
        update task_commands as command
        set task_event_sequence_baseline = coalesce(
            (
                select max(event.sequence)
                from task_events as event
                where event.task_id = command.task_id
                  and event.installation_id = command.installation_id
                  and event.recorded_at <= command.created_at
            ),
            0
        )
        where command.command_type = 'task.offer'
        """
    )
    op.create_check_constraint(
        "ck_task_commands_offer_event_baseline_scope",
        "task_commands",
        "(command_type = 'task.offer' and task_event_sequence_baseline between 0 and "
        f"{MAX_TASK_EVENT_SEQUENCE - 1}) or "
        "(command_type <> 'task.offer' and task_event_sequence_baseline is null)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_task_commands_offer_event_baseline_scope",
        "task_commands",
        type_="check",
    )
    op.drop_column("task_commands", "task_event_sequence_baseline")


__all__ = ["downgrade", "upgrade"]
