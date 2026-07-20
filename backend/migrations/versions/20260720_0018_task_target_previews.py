"""Persist target exclusions and snapshot confirmations.

Revision ID: 20260720_0018
Revises: 20260720_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720_0018"
down_revision: str | None = "20260720_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_TYPES_WITH_PREVIEW = (
    "event_type in ('task.created', 'task.validation_started', "
    "'task.validation_failed', 'task.awaiting_platform_login', "
    "'task.discovery_started', 'task.awaiting_confirmation', "
    "'task.target_selection_updated', 'task.targets_confirmed', 'task.started', "
    "'step.started', 'step.progress', 'step.completed', 'step.failed', "
    "'task.awaiting_human', 'task.paused', 'task.resumed', 'task.cancelling', "
    "'task.cancelled', 'task.completed', 'task.partially_completed', "
    "'task.failed', 'task.outcome_uncertain')"
)

_EVENT_TYPES_WITHOUT_PREVIEW = (
    "event_type in ('task.created', 'task.validation_started', "
    "'task.validation_failed', 'task.awaiting_platform_login', "
    "'task.discovery_started', 'task.awaiting_confirmation', 'task.started', "
    "'step.started', 'step.progress', 'step.completed', 'step.failed', "
    "'task.awaiting_human', 'task.paused', 'task.resumed', 'task.cancelling', "
    "'task.cancelled', 'task.completed', 'task.partially_completed', "
    "'task.failed', 'task.outcome_uncertain')"
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_task_targets_preview_binding",
        "task_targets",
        ["id", "task_id", "installation_id", "page_revision"],
    )
    op.create_table(
        "task_target_exclusions",
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_revision", sa.BigInteger(), nullable=False),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "substring(target_id::text from 15 for 1) = '4' and "
            "substring(target_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_task_target_exclusions_target_uuid_v4",
        ),
        sa.CheckConstraint(
            "page_revision between 1 and 9007199254740991",
            name="ck_task_target_exclusions_page_revision_range",
        ),
        sa.ForeignKeyConstraint(
            ["target_id", "task_id", "installation_id", "page_revision"],
            [
                "task_targets.id",
                "task_targets.task_id",
                "task_targets.installation_id",
                "task_targets.page_revision",
            ],
            name="fk_task_target_exclusions_preview_binding",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("target_id", name="pk_task_target_exclusions"),
    )
    op.create_index(
        "ix_task_target_exclusions_installation_task_page",
        "task_target_exclusions",
        ["installation_id", "task_id", "page_revision"],
    )
    op.create_table(
        "task_target_confirmations",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_revision", sa.BigInteger(), nullable=False),
        sa.Column("selection_task_revision", sa.BigInteger(), nullable=False),
        sa.Column("confirmed_task_revision", sa.BigInteger(), nullable=False),
        sa.Column("selected_target_count", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_idempotency_key", sa.String(), nullable=False),
        sa.Column("source_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "page_revision between 1 and 9007199254740991",
            name="ck_task_target_confirmations_page_revision_range",
        ),
        sa.CheckConstraint(
            "selection_task_revision > 0 and confirmed_task_revision = selection_task_revision + 1",
            name="ck_task_target_confirmations_revision_order",
        ),
        sa.CheckConstraint(
            "selected_target_count between 1 and 100",
            name="ck_task_target_confirmations_selected_count",
        ),
        sa.CheckConstraint(
            "substring(source_message_id::text from 15 for 1) = '4' and "
            "substring(source_message_id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_task_target_confirmations_message_uuid_v4",
        ),
        sa.CheckConstraint(
            "source_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
            name="ck_task_target_confirmations_idempotency_key",
        ),
        sa.CheckConstraint(
            "octet_length(source_fingerprint) = 32",
            name="ck_task_target_confirmations_fingerprint_length",
        ),
        sa.CheckConstraint(
            "created_at >= confirmed_at",
            name="ck_task_target_confirmations_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "installation_id"],
            ["tasks.id", "tasks.installation_id"],
            name="fk_task_target_confirmations_task_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("task_id", name="pk_task_target_confirmations"),
        sa.UniqueConstraint(
            "installation_id",
            "source_message_id",
            name="uq_task_target_confirmations_source_message",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "source_idempotency_key",
            name="uq_task_target_confirmations_source_idempotency",
        ),
    )
    op.drop_constraint("ck_task_events_type", "task_events", type_="check")
    op.create_check_constraint("ck_task_events_type", "task_events", _EVENT_TYPES_WITH_PREVIEW)


def downgrade() -> None:
    op.drop_constraint("ck_task_events_type", "task_events", type_="check")
    op.create_check_constraint("ck_task_events_type", "task_events", _EVENT_TYPES_WITHOUT_PREVIEW)
    op.drop_table("task_target_confirmations")
    op.drop_index(
        "ix_task_target_exclusions_installation_task_page",
        table_name="task_target_exclusions",
    )
    op.drop_table("task_target_exclusions")
    op.drop_constraint(
        "uq_task_targets_preview_binding",
        "task_targets",
        type_="unique",
    )
