"""Create durable Task command outbox and acknowledgement state.

Revision ID: 20260718_0009
Revises: 20260718_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0009"
down_revision: str | None = "20260718_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_SEQUENCE = 9007199254740991


def upgrade() -> None:
    """Create a scoped, retryable outbox without storing arbitrary payloads."""
    op.create_table(
        "task_commands",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "execution_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("command_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column(
            "revision",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "delivery_attempts",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("next_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "response_message_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("response_type", sa.String(length=32), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
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
            "substring(message_id::text from 15 for 1) = '4' "
            "and substring(message_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_task_commands_message_uuid_v4",
        ),
        sa.CheckConstraint(
            "substring(correlation_id::text from 15 for 1) = '4' "
            "and substring(correlation_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_task_commands_correlation_uuid_v4",
        ),
        sa.CheckConstraint(
            "response_message_id is null or ("
            "substring(response_message_id::text from 15 for 1) = '4' "
            "and substring(response_message_id::text from 20 for 1) in ('8', '9', 'a', 'b'))",
            name="ck_task_commands_response_uuid_v4",
        ),
        sa.CheckConstraint(
            f"sequence between 1 and {_MAX_SEQUENCE}",
            name="ck_task_commands_sequence_range",
        ),
        sa.CheckConstraint(
            "command_type in ('task.offer', 'task.pause', 'task.resume', "
            "'task.cancel', 'task.emergency_stop')",
            name="ck_task_commands_type",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'in_flight', 'delivered', "
            "'acknowledged', 'rejected', 'expired')",
            name="ck_task_commands_status",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
            name="ck_task_commands_idempotency_key",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_task_commands_revision_positive",
        ),
        sa.CheckConstraint(
            "delivery_attempts >= 0",
            name="ck_task_commands_delivery_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "deadline_at > created_at and updated_at >= created_at "
            "and (next_delivery_at is null or "
            "(next_delivery_at >= created_at and next_delivery_at < deadline_at)) "
            "and (lease_expires_at is null or "
            "(lease_expires_at > updated_at and lease_expires_at <= deadline_at)) "
            "and (delivered_at is null or "
            "(delivered_at >= created_at and delivered_at <= deadline_at)) "
            "and (acknowledged_at is null or "
            "(delivered_at is not null and acknowledged_at >= delivered_at "
            "and acknowledged_at <= deadline_at))",
            name="ck_task_commands_time_order",
        ),
        sa.CheckConstraint(
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
            name="ck_task_commands_status_coherence",
        ),
        sa.CheckConstraint(
            "response_type is null or "
            "(command_type = 'task.offer' and response_type in ('task.accept', 'task.reject')) or "
            "(command_type in ('task.pause', 'task.resume', 'task.cancel', "
            "'task.emergency_stop') and response_type = 'task.control_ack')",
            name="ck_task_commands_response_coherence",
        ),
        sa.ForeignKeyConstraint(
            ["execution_attempt_id", "task_id", "installation_id"],
            [
                "execution_attempts.id",
                "execution_attempts.task_id",
                "execution_attempts.installation_id",
            ],
            name="fk_task_commands_attempt_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("message_id", name="pk_task_commands"),
        sa.UniqueConstraint(
            "execution_attempt_id",
            "sequence",
            name="uq_task_commands_attempt_sequence",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "idempotency_key",
            name="uq_task_commands_idempotency",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "response_message_id",
            name="uq_task_commands_response_message",
        ),
    )
    op.create_index(
        "ix_task_commands_outbox_due",
        "task_commands",
        ["status", "next_delivery_at", "deadline_at", "message_id"],
        unique=False,
    )
    op.create_index(
        "ix_task_commands_installation_task_created",
        "task_commands",
        ["installation_id", "task_id", "created_at", "message_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove command outbox state while preserving tasks and events."""
    op.drop_index(
        "ix_task_commands_installation_task_created",
        table_name="task_commands",
    )
    op.drop_index("ix_task_commands_outbox_due", table_name="task_commands")
    op.drop_table("task_commands")
