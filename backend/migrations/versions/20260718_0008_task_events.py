"""Create versioned Task events and snapshot event watermark.

Revision ID: 20260718_0008
Revises: 20260718_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0008"
down_revision: str | None = "20260718_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_SEQUENCE = 9007199254740991


def upgrade() -> None:
    """Create a lossless, Installation-scoped event timeline."""
    op.add_column(
        "tasks",
        sa.Column(
            "last_event_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_tasks_last_event_sequence_range",
        "tasks",
        f"last_event_sequence between 0 and {_MAX_SEQUENCE}",
    )

    op.create_table(
        "task_events",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "event_version",
            sa.String(length=8),
            server_default=sa.text("'1.0'"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("task_revision", sa.BigInteger(), nullable=False),
        sa.Column("task_status", sa.String(length=32), nullable=False),
        sa.Column(
            "execution_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("safe_message", sa.String(), nullable=True),
        sa.CheckConstraint(
            f"sequence between 1 and {_MAX_SEQUENCE}",
            name="ck_task_events_sequence_range",
        ),
        sa.CheckConstraint(
            "event_version = '1.0'",
            name="ck_task_events_version",
        ),
        sa.CheckConstraint(
            "event_type in ('task.created', 'task.validation_started', "
            "'task.validation_failed', 'task.awaiting_platform_login', "
            "'task.awaiting_confirmation', 'task.started', 'step.started', "
            "'step.progress', 'step.completed', 'step.failed', "
            "'task.awaiting_human', 'task.paused', 'task.resumed', "
            "'task.cancelling', 'task.cancelled', 'task.completed', "
            "'task.partially_completed', 'task.failed', 'task.outcome_uncertain')",
            name="ck_task_events_type",
        ),
        sa.CheckConstraint(
            "task_revision > 0",
            name="ck_task_events_task_revision_positive",
        ),
        sa.CheckConstraint(
            "task_status in ('draft', 'validating', 'awaiting_device', "
            "'awaiting_platform_login', 'discovering_targets', "
            "'awaiting_confirmation', 'queued', 'running', 'paused', "
            "'awaiting_human', 'cancelling', 'succeeded', "
            "'partially_succeeded', 'failed', 'cancelled', 'outcome_uncertain')",
            name="ck_task_events_task_status",
        ),
        sa.CheckConstraint(
            "source_message_id is null or ("
            "substring(source_message_id::text from 15 for 1) = '4' "
            "and substring(source_message_id::text from 20 for 1) "
            "in ('8', '9', 'a', 'b'))",
            name="ck_task_events_source_message_uuid_v4",
        ),
        sa.CheckConstraint(
            "action_id is null or execution_attempt_id is not null",
            name="ck_task_events_action_requires_attempt",
        ),
        sa.CheckConstraint(
            "recorded_at >= occurred_at",
            name="ck_task_events_time_order",
        ),
        sa.CheckConstraint(
            "safe_message is null or ("
            "char_length(safe_message) between 1 and 1024 "
            "and octet_length(safe_message) <= 4096 "
            "and safe_message !~ '[[:cntrl:]]' "
            "and lower(safe_message) not like '%bearer %' "
            "and lower(safe_message) not like '%file://%' "
            "and lower(safe_message) not like '%data:%;base64,%' "
            "and lower(safe_message) !~ "
            "'(access[_-]?token|api[_-]?key|authorization|cookie|credential|password|"
            "private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)"
            "[[:space:]]*[:=]')",
            name="ck_task_events_safe_message",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "installation_id"],
            ["tasks.id", "tasks.installation_id"],
            name="fk_task_events_task_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_attempt_id", "task_id", "installation_id"],
            [
                "execution_attempts.id",
                "execution_attempts.task_id",
                "execution_attempts.installation_id",
            ],
            name="fk_task_events_attempt_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "execution_attempt_id", "task_id", "installation_id"],
            [
                "task_actions.id",
                "task_actions.execution_attempt_id",
                "task_actions.task_id",
                "task_actions.installation_id",
            ],
            name="fk_task_events_action_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("task_id", "sequence", name="pk_task_events"),
        sa.UniqueConstraint(
            "installation_id",
            "source_message_id",
            name="uq_task_events_source_message",
        ),
    )
    op.create_index(
        "ix_task_events_installation_task_sequence",
        "task_events",
        ["installation_id", "task_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the event timeline while retaining Task and execution records."""
    op.drop_index(
        "ix_task_events_installation_task_sequence",
        table_name="task_events",
    )
    op.drop_table("task_events")
    op.drop_constraint(
        "ck_tasks_last_event_sequence_range",
        "tasks",
        type_="check",
    )
    op.drop_column("tasks", "last_event_sequence")
