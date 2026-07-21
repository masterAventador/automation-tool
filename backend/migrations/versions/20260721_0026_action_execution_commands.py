"""Bind typed action execution commands to durable authorizations.

Revision ID: 20260721_0026
Revises: 20260721_0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260721_0026"
down_revision: str | None = "20260721_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_commands",
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint("ck_task_commands_type", "task_commands", type_="check")
    op.drop_constraint("ck_task_commands_status_coherence", "task_commands", type_="check")
    op.drop_constraint("ck_task_commands_response_coherence", "task_commands", type_="check")
    op.drop_constraint(
        "ck_task_commands_target_confirmation_scope",
        "task_commands",
        type_="check",
    )
    op.create_check_constraint(
        "ck_task_commands_type",
        "task_commands",
        "command_type in ('task.offer', 'task.discover', 'action.execute', "
        "'task.pause', 'task.resume', 'task.cancel', 'task.emergency_stop')",
    )
    op.create_check_constraint(
        "ck_task_commands_status_coherence",
        "task_commands",
        "(status = 'pending' and next_delivery_at is not null "
        "and lease_expires_at is null and delivered_at is null "
        "and acknowledged_at is null and response_message_id is null "
        "and response_type is null) or "
        "(status = 'in_flight' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is not null "
        "and delivered_at is null and acknowledged_at is null "
        "and response_message_id is null and response_type is null) or "
        "(status = 'delivered' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is null "
        "and response_message_id is null and response_type is null) or "
        "(status = 'acknowledged' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is not null "
        "and response_message_id is not null "
        "and response_type in ('task.accept', 'task.control_ack', 'action.accept')) or "
        "(status = 'rejected' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is not null "
        "and response_message_id is not null "
        "and response_type in ('task.reject', 'action.reject')) or "
        "(status = 'expired' and next_delivery_at is null "
        "and lease_expires_at is null and acknowledged_at is null "
        "and response_message_id is null and response_type is null "
        "and (delivered_at is null or delivery_attempts > 0))",
    )
    op.create_check_constraint(
        "ck_task_commands_response_coherence",
        "task_commands",
        "response_type is null or "
        "(command_type in ('task.offer', 'task.discover') "
        "and response_type in ('task.accept', 'task.reject')) or "
        "(command_type = 'action.execute' "
        "and response_type in ('action.accept', 'action.reject')) or "
        "(command_type in ('task.pause', 'task.resume', 'task.cancel', "
        "'task.emergency_stop') and response_type = 'task.control_ack')",
    )
    op.create_check_constraint(
        "ck_task_commands_target_confirmation_scope",
        "task_commands",
        "target_confirmation_message_id is null or "
        "command_type in ('task.offer', 'action.execute')",
    )
    op.create_check_constraint(
        "ck_task_commands_action_uuid_v4",
        "task_commands",
        "action_id is null or (substring(action_id::text from 15 for 1) = '4' "
        "and substring(action_id::text from 20 for 1) in ('8', '9', 'a', 'b'))",
    )
    op.create_check_constraint(
        "ck_task_commands_action_scope",
        "task_commands",
        "(command_type = 'action.execute' and action_id is not null "
        "and target_confirmation_message_id is not null) or "
        "(command_type <> 'action.execute' and action_id is null)",
    )
    op.create_foreign_key(
        "fk_task_commands_action_binding",
        "task_commands",
        "task_actions",
        ["action_id", "execution_attempt_id", "task_id", "installation_id"],
        ["id", "execution_attempt_id", "task_id", "installation_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_task_commands_action",
        "task_commands",
        ["action_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_task_commands_action", "task_commands", type_="unique")
    op.drop_constraint("fk_task_commands_action_binding", "task_commands", type_="foreignkey")
    op.drop_constraint("ck_task_commands_action_scope", "task_commands", type_="check")
    op.drop_constraint("ck_task_commands_action_uuid_v4", "task_commands", type_="check")
    op.drop_constraint(
        "ck_task_commands_target_confirmation_scope",
        "task_commands",
        type_="check",
    )
    op.drop_constraint("ck_task_commands_response_coherence", "task_commands", type_="check")
    op.drop_constraint("ck_task_commands_status_coherence", "task_commands", type_="check")
    op.drop_constraint("ck_task_commands_type", "task_commands", type_="check")
    op.create_check_constraint(
        "ck_task_commands_type",
        "task_commands",
        "command_type in ('task.offer', 'task.discover', 'task.pause', 'task.resume', "
        "'task.cancel', 'task.emergency_stop')",
    )
    op.create_check_constraint(
        "ck_task_commands_status_coherence",
        "task_commands",
        "(status = 'pending' and next_delivery_at is not null "
        "and lease_expires_at is null and delivered_at is null "
        "and acknowledged_at is null and response_message_id is null "
        "and response_type is null) or "
        "(status = 'in_flight' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is not null "
        "and delivered_at is null and acknowledged_at is null "
        "and response_message_id is null and response_type is null) or "
        "(status = 'delivered' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is null "
        "and response_message_id is null and response_type is null) or "
        "(status = 'acknowledged' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is not null "
        "and response_message_id is not null "
        "and response_type in ('task.accept', 'task.control_ack')) or "
        "(status = 'rejected' and delivery_attempts > 0 "
        "and next_delivery_at is null and lease_expires_at is null "
        "and delivered_at is not null and acknowledged_at is not null "
        "and response_message_id is not null and response_type = 'task.reject') or "
        "(status = 'expired' and next_delivery_at is null "
        "and lease_expires_at is null and acknowledged_at is null "
        "and response_message_id is null and response_type is null "
        "and (delivered_at is null or delivery_attempts > 0))",
    )
    op.create_check_constraint(
        "ck_task_commands_response_coherence",
        "task_commands",
        "response_type is null or "
        "(command_type in ('task.offer', 'task.discover') "
        "and response_type in ('task.accept', 'task.reject')) or "
        "(command_type in ('task.pause', 'task.resume', 'task.cancel', "
        "'task.emergency_stop') and response_type = 'task.control_ack')",
    )
    op.create_check_constraint(
        "ck_task_commands_target_confirmation_scope",
        "task_commands",
        "target_confirmation_message_id is null or command_type = 'task.offer'",
    )
    op.drop_column("task_commands", "action_id")


__all__ = ["downgrade", "upgrade"]
