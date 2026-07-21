"""Persist action results and consecutive-failure circuits.

Revision ID: 20260721_0023
Revises: 20260720_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260721_0023"
down_revision: str | None = "20260720_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_action_risk_authorizations_result_binding",
        "action_risk_authorizations",
        ["action_id", "installation_id", "platform", "action"],
    )
    op.create_table(
        "action_risk_results",
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("consecutive_failures_after", sa.BigInteger(), nullable=False),
        sa.Column("consecutive_failure_threshold", sa.BigInteger(), nullable=False),
        sa.Column("circuit_open_after", sa.Boolean(), nullable=False),
        sa.Column("triggered_handoff", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "platform in ('douyin')",
            name="ck_action_risk_results_platform",
        ),
        sa.CheckConstraint(
            "action in ('browse', 'comment', 'direct_message')",
            name="ck_action_risk_results_action",
        ),
        sa.CheckConstraint(
            "outcome in ('succeeded', 'failed')",
            name="ck_action_risk_results_outcome",
        ),
        sa.CheckConstraint(
            "consecutive_failures_after between 0 and 9007199254740991 "
            "and consecutive_failure_threshold between 1 and 9007199254740991",
            name="ck_action_risk_results_limits",
        ),
        sa.CheckConstraint(
            "(outcome = 'failed' and consecutive_failures_after > 0) or "
            "(outcome = 'succeeded' and "
            "(consecutive_failures_after = 0 or circuit_open_after))",
            name="ck_action_risk_results_failure_count",
        ),
        sa.CheckConstraint(
            "not triggered_handoff or (outcome = 'failed' and circuit_open_after "
            "and consecutive_failures_after >= consecutive_failure_threshold)",
            name="ck_action_risk_results_handoff",
        ),
        sa.CheckConstraint(
            "created_at >= observed_at",
            name="ck_action_risk_results_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "installation_id", "platform", "action"],
            [
                "action_risk_authorizations.action_id",
                "action_risk_authorizations.installation_id",
                "action_risk_authorizations.platform",
                "action_risk_authorizations.action",
            ],
            name="fk_action_risk_results_authorization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("action_id", name="pk_action_risk_results"),
        sa.UniqueConstraint(
            "action_id",
            "installation_id",
            "platform",
            "action",
            name="uq_action_risk_results_scope_binding",
        ),
    )
    op.create_index(
        "ix_action_risk_results_observed",
        "action_risk_results",
        ["observed_at", "action_id"],
        unique=False,
    )
    op.create_table(
        "action_failure_circuits",
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("consecutive_failures", sa.BigInteger(), nullable=False),
        sa.Column("circuit_open", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("last_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opened_by_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
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
            "platform in ('douyin')",
            name="ck_action_failure_circuits_platform",
        ),
        sa.CheckConstraint(
            "action in ('browse', 'comment', 'direct_message')",
            name="ck_action_failure_circuits_action",
        ),
        sa.CheckConstraint(
            "consecutive_failures between 0 and 9007199254740991 and revision > 0",
            name="ck_action_failure_circuits_counters",
        ),
        sa.CheckConstraint(
            "(circuit_open and consecutive_failures > 0 "
            "and opened_by_action_id is not null and opened_at is not null) or "
            "(not circuit_open and opened_by_action_id is null and opened_at is null)",
            name="ck_action_failure_circuits_open_state",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "and (opened_at is null or (opened_at >= created_at and updated_at >= opened_at))",
            name="ck_action_failure_circuits_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_action_failure_circuits_installation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_action_id", "installation_id", "platform", "action"],
            [
                "action_risk_results.action_id",
                "action_risk_results.installation_id",
                "action_risk_results.platform",
                "action_risk_results.action",
            ],
            name="fk_action_failure_circuits_last_result",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_action_id", "installation_id", "platform", "action"],
            [
                "action_risk_results.action_id",
                "action_risk_results.installation_id",
                "action_risk_results.platform",
                "action_risk_results.action",
            ],
            name="fk_action_failure_circuits_open_result",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "installation_id",
            "platform",
            "action",
            name="pk_action_failure_circuits",
        ),
    )


def downgrade() -> None:
    op.drop_table("action_failure_circuits")
    op.drop_index("ix_action_risk_results_observed", table_name="action_risk_results")
    op.drop_table("action_risk_results")
    op.drop_constraint(
        "uq_action_risk_authorizations_result_binding",
        "action_risk_authorizations",
        type_="unique",
    )
