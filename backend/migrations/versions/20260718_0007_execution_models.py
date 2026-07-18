"""Create scoped execution attempts, task actions, and current-attempt binding.

Revision ID: 20260718_0007
Revises: 20260718_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0007"
down_revision: str | None = "20260718_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NONTERMINAL_ATTEMPT_VALUES = (
    "'pending', 'offered', 'accepted', 'running', 'paused', 'awaiting_human', 'cancelling'"
)
_TERMINAL_ATTEMPT_VALUES = (
    "'succeeded', 'partially_succeeded', 'failed', 'cancelled', "
    "'rejected', 'expired', 'outcome_uncertain'"
)


def upgrade() -> None:
    """Create exact Task/Installation-bound execution persistence."""
    op.create_table(
        "execution_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_execution_attempts_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_execution_attempts_number_positive",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_execution_attempts_revision_positive",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'offered', 'accepted', 'running', 'paused', "
            "'awaiting_human', 'cancelling', 'succeeded', 'partially_succeeded', "
            "'failed', 'cancelled', 'rejected', 'expired', 'outcome_uncertain')",
            name="ck_execution_attempts_status",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "and (started_at is null or started_at >= created_at) "
            "and (finished_at is null or finished_at >= coalesce(started_at, created_at)) "
            "and updated_at >= coalesce(finished_at, started_at, created_at)",
            name="ck_execution_attempts_time_order",
        ),
        sa.CheckConstraint(
            f"(status in ({_TERMINAL_ATTEMPT_VALUES}) and finished_at is not null) or "
            f"(status in ({_NONTERMINAL_ATTEMPT_VALUES}) and finished_at is null)",
            name="ck_execution_attempts_terminal_time",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "installation_id"],
            ["tasks.id", "tasks.installation_id"],
            name="fk_execution_attempts_task_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_attempts"),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "installation_id",
            name="uq_execution_attempts_binding",
        ),
        sa.UniqueConstraint(
            "task_id",
            "attempt_number",
            name="uq_execution_attempts_task_number",
        ),
    )
    op.create_index(
        "uq_execution_attempts_one_active_task",
        "execution_attempts",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text(f"status in ({_NONTERMINAL_ATTEMPT_VALUES})"),
    )
    op.create_index(
        "ix_execution_attempts_installation_updated",
        "execution_attempts",
        ["installation_id", "updated_at", "id"],
        unique=False,
    )

    op.add_column(
        "tasks",
        sa.Column("current_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tasks_current_attempt_binding",
        "tasks",
        "execution_attempts",
        ["current_attempt_id", "id", "installation_id"],
        ["id", "task_id", "installation_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "task_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "execution_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'planned'"),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
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
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_task_actions_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name="ck_task_actions_ordinal_positive",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_task_actions_revision_positive",
        ),
        sa.CheckConstraint(
            "status in ('planned', 'authorized', 'prepared', 'dispatched', "
            "'verified', 'cancelled', 'outcome_uncertain')",
            name="ck_task_actions_status",
        ),
        sa.CheckConstraint(
            "outcome in ('pending', 'succeeded', 'failed', 'cancelled', 'outcome_uncertain')",
            name="ck_task_actions_outcome",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "and (finished_at is null or "
            "(finished_at >= created_at and updated_at >= finished_at))",
            name="ck_task_actions_time_order",
        ),
        sa.CheckConstraint(
            "(status in ('planned', 'authorized', 'prepared', 'dispatched') "
            "and outcome = 'pending' and finished_at is null) or "
            "(status = 'verified' and outcome in ('succeeded', 'failed') "
            "and finished_at is not null) or "
            "(status = 'cancelled' and outcome = 'cancelled' "
            "and finished_at is not null) or "
            "(status = 'outcome_uncertain' and outcome = 'outcome_uncertain' "
            "and finished_at is not null)",
            name="ck_task_actions_result_coherence",
        ),
        sa.ForeignKeyConstraint(
            ["execution_attempt_id", "task_id", "installation_id"],
            [
                "execution_attempts.id",
                "execution_attempts.task_id",
                "execution_attempts.installation_id",
            ],
            name="fk_task_actions_attempt_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_actions"),
        sa.UniqueConstraint(
            "id",
            "execution_attempt_id",
            "task_id",
            "installation_id",
            name="uq_task_actions_binding",
        ),
        sa.UniqueConstraint(
            "execution_attempt_id",
            "ordinal",
            name="uq_task_actions_attempt_ordinal",
        ),
    )
    op.create_index(
        "ix_task_actions_installation_task",
        "task_actions",
        ["installation_id", "task_id", "execution_attempt_id", "ordinal"],
        unique=False,
    )


def downgrade() -> None:
    """Remove execution persistence without removing Task records."""
    op.drop_index("ix_task_actions_installation_task", table_name="task_actions")
    op.drop_table("task_actions")
    op.drop_constraint("fk_tasks_current_attempt_binding", "tasks", type_="foreignkey")
    op.drop_column("tasks", "current_attempt_id")
    op.drop_index(
        "ix_execution_attempts_installation_updated",
        table_name="execution_attempts",
    )
    op.drop_index(
        "uq_execution_attempts_one_active_task",
        table_name="execution_attempts",
    )
    op.drop_table("execution_attempts")
