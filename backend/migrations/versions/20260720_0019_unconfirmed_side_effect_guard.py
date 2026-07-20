"""Bind execution offers to one current target confirmation.

Revision ID: 20260720_0019
Revises: 20260720_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720_0019"
down_revision: str | None = "20260720_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_commands",
        sa.Column(
            "target_confirmation_message_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_task_commands_target_confirmation_uuid_v4",
        "task_commands",
        "target_confirmation_message_id is null or ("
        "substring(target_confirmation_message_id::text from 15 for 1) = '4' and "
        "substring(target_confirmation_message_id::text from 20 for 1) "
        "in ('8', '9', 'a', 'b'))",
    )
    op.create_check_constraint(
        "ck_task_commands_target_confirmation_scope",
        "task_commands",
        "target_confirmation_message_id is null or command_type = 'task.offer'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_task_commands_target_confirmation_scope",
        "task_commands",
        type_="check",
    )
    op.drop_constraint(
        "ck_task_commands_target_confirmation_uuid_v4",
        "task_commands",
        type_="check",
    )
    op.drop_column("task_commands", "target_confirmation_message_id")
