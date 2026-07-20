"""Create atomic server-side action risk authorization facts.

Revision ID: 20260720_0020
Revises: 20260720_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720_0020"
down_revision: str | None = "20260720_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_task_actions_risk_binding",
        "task_actions",
        ["id", "execution_attempt_id", "task_id", "installation_id", "ordinal"],
    )
    op.create_unique_constraint(
        "uq_task_targets_action_binding",
        "task_targets",
        ["id", "task_id", "installation_id", "ordinal"],
    )
    op.create_table(
        "action_risk_authorizations",
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("effective_minimum_interval_seconds", sa.BigInteger(), nullable=False),
        sa.Column("task_action_limit", sa.BigInteger(), nullable=False),
        sa.Column("daily_action_limit", sa.BigInteger(), nullable=False),
        sa.Column("consecutive_failure_threshold", sa.BigInteger(), nullable=False),
        sa.Column("task_count_after", sa.BigInteger(), nullable=False),
        sa.Column("daily_count_after", sa.BigInteger(), nullable=False),
        sa.Column("authorized_day", sa.Date(), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "platform in ('douyin')",
            name="ck_action_risk_authorizations_platform",
        ),
        sa.CheckConstraint(
            "action in ('browse', 'comment', 'direct_message')",
            name="ck_action_risk_authorizations_action",
        ),
        sa.CheckConstraint(
            "policy_version = 'action-risk-policy.v1'",
            name="ck_action_risk_authorizations_policy_version",
        ),
        sa.CheckConstraint(
            "effective_minimum_interval_seconds between 1 and 3600",
            name="ck_action_risk_authorizations_interval",
        ),
        sa.CheckConstraint(
            "task_action_limit between 1 and 100 "
            "and daily_action_limit between 1 and 9007199254740991 "
            "and consecutive_failure_threshold between 1 and 9007199254740991",
            name="ck_action_risk_authorizations_limits",
        ),
        sa.CheckConstraint(
            "task_count_after between 1 and task_action_limit "
            "and daily_count_after between 1 and daily_action_limit",
            name="ck_action_risk_authorizations_counts",
        ),
        sa.CheckConstraint(
            "authorized_day = (authorized_at at time zone 'UTC')::date",
            name="ck_action_risk_authorizations_utc_day",
        ),
        sa.CheckConstraint(
            "created_at >= authorized_at",
            name="ck_action_risk_authorizations_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "execution_attempt_id", "task_id", "installation_id", "ordinal"],
            [
                "task_actions.id",
                "task_actions.execution_attempt_id",
                "task_actions.task_id",
                "task_actions.installation_id",
                "task_actions.ordinal",
            ],
            name="fk_action_risk_authorizations_action_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_id", "task_id", "installation_id", "ordinal"],
            [
                "task_targets.id",
                "task_targets.task_id",
                "task_targets.installation_id",
                "task_targets.ordinal",
            ],
            name="fk_action_risk_authorizations_target_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("action_id", name="pk_action_risk_authorizations"),
        sa.UniqueConstraint(
            "task_id",
            "platform",
            "action",
            "task_count_after",
            name="uq_action_risk_authorizations_task_count",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "platform",
            "action",
            "authorized_day",
            "daily_count_after",
            name="uq_action_risk_authorizations_daily_count",
        ),
    )
    op.create_index(
        "ix_action_risk_authorizations_scope_time",
        "action_risk_authorizations",
        ["installation_id", "platform", "action", "authorized_at", "action_id"],
        unique=False,
    )
    op.create_index(
        "ix_action_risk_authorizations_task_scope",
        "action_risk_authorizations",
        ["task_id", "platform", "action", "authorized_at", "action_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_action_risk_authorizations_task_scope",
        table_name="action_risk_authorizations",
    )
    op.drop_index(
        "ix_action_risk_authorizations_scope_time",
        table_name="action_risk_authorizations",
    )
    op.drop_table("action_risk_authorizations")
    op.drop_constraint(
        "uq_task_targets_action_binding",
        "task_targets",
        type_="unique",
    )
    op.drop_constraint(
        "uq_task_actions_risk_binding",
        "task_actions",
        type_="unique",
    )
