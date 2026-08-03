"""Create durable Aliyun editing submission intents for VE-06 reconciliation.

Revision ID: 20260723_0032
Revises: 20260723_0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0032"
down_revision: str | None = "20260723_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist submission intents so reconciliation survives App restarts."""
    op.create_table(
        "aliyun_editing_intents",
        sa.Column("editing_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("vendor_job_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column(
            "output_artifact_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "state in ('prepared', 'dispatched', 'uncertain')",
            name="ck_aliyun_editing_intents_state",
        ),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'paused', 'cancelling', 'succeeded', "
            "'failed', 'cancelled', 'outcome_uncertain')",
            name="ck_aliyun_editing_intents_status",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_aliyun_editing_intents_request_hash",
        ),
        sa.CheckConstraint(
            "vendor_job_id is null or vendor_job_id ~ '^[A-Za-z0-9-]{8,128}$'",
            name="ck_aliyun_editing_intents_vendor_job_id",
        ),
        sa.CheckConstraint(
            "state <> 'prepared' or (vendor_job_id is null and status = 'queued'"
            " and failure_code is null and cardinality(output_artifact_ids) = 0)",
            name="ck_aliyun_editing_intents_prepared_shape",
        ),
        sa.CheckConstraint(
            "state <> 'uncertain' or (vendor_job_id is null"
            " and status = 'outcome_uncertain' and failure_code is null"
            " and cardinality(output_artifact_ids) = 0)",
            name="ck_aliyun_editing_intents_uncertain_shape",
        ),
        sa.CheckConstraint(
            "state <> 'dispatched' or vendor_job_id is not null",
            name="ck_aliyun_editing_intents_dispatched_shape",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' or (cardinality(output_artifact_ids) > 0"
            " and failure_code is null)",
            name="ck_aliyun_editing_intents_succeeded_facts",
        ),
        sa.CheckConstraint(
            "status = 'succeeded' or status = 'failed'"
            " or (cardinality(output_artifact_ids) = 0 and failure_code is null)",
            name="ck_aliyun_editing_intents_non_terminal_facts",
        ),
        sa.CheckConstraint(
            "status <> 'failed' or (cardinality(output_artifact_ids) = 0"
            " and failure_code is not null)",
            name="ck_aliyun_editing_intents_failed_facts",
        ),
        sa.PrimaryKeyConstraint("editing_job_id", name="pk_aliyun_editing_intents"),
    )
    op.create_index(
        "ux_aliyun_editing_intents_vendor_job_id",
        "aliyun_editing_intents",
        ["vendor_job_id"],
        unique=True,
        postgresql_where=sa.text("vendor_job_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("ux_aliyun_editing_intents_vendor_job_id", table_name="aliyun_editing_intents")
    op.drop_table("aliyun_editing_intents")
