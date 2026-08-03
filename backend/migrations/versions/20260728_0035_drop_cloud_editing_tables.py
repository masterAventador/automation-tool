"""Drop the Aliyun cloud-editing tables.

LE-01 removed the cloud-editing route. The two migrations that created these
tables (20260723_0032, 20260723_0034) stay on disk: 0032 sits mid-chain with
0033 pointing at it, so deleting the file would break the chain, and databases
that already ran them would keep the tables regardless.

All three tables dropped here -- `aliyun_editing_intents`, `editing_output_lineages`,
and `editing_output_artifacts` -- were created by the two cloud-editing migrations
(20260723_0032 and 20260723_0034) and had exactly one repository consumer between
them (`aliyun_editing_intent_repository.py`, `editing_output_ledger_repository.py`),
both deleted in Task 2. LE-05's rewritten ledger targets a different domain model
(Material / Timeline / EditingJob with source in/out points) and does not reuse this
shape, so nothing is left reading these tables. `editing_output_artifacts` must be
dropped before `editing_output_lineages`: it carries a foreign key to it, and
dropping the referenced table first fails with `DependentObjectsStillExistError`.

`downgrade()` restores all three tables exactly as `20260723_0032` and
`20260723_0034` created them -- same columns, types, constraints, indexes and
the foreign key -- so this migration stays reversible like the rest of the
chain. Creation order is the reverse of the drop order: `aliyun_editing_intents`
and `editing_output_lineages` first, then `editing_output_artifacts` last, since
it references `editing_output_lineages`.

Revision ID: 20260728_0035
Revises: 20260723_0034
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_0035"
down_revision: str | None = "20260723_0034"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.drop_table("editing_output_artifacts")
    op.drop_table("editing_output_lineages")
    op.drop_table("aliyun_editing_intents")


def downgrade() -> None:
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

    op.create_table(
        "editing_output_lineages",
        sa.Column("editing_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_revision", sa.Integer, nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("provider_contract_verified_at", sa.String(length=10), nullable=False),
        sa.Column(
            "input_artifact_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("cost_source", sa.String(length=16), nullable=False),
        sa.Column("cost_currency", sa.String(length=3), nullable=False),
        sa.Column("cost_billed_minutes", sa.Integer, nullable=False),
        sa.Column("cost_tier_id", sa.String(length=64), nullable=False),
        sa.Column("cost_unit_price_cny", sa.Numeric(12, 4), nullable=False),
        sa.Column("cost_total_cny", sa.Numeric(14, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("timeline_revision >= 1", name="ck_editing_output_lineages_revision"),
        sa.CheckConstraint(
            "provider_id ~ '^[a-z0-9_]{2,64}$'",
            name="ck_editing_output_lineages_provider",
        ),
        sa.CheckConstraint(
            "provider_contract_verified_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'",
            name="ck_editing_output_lineages_verified_at",
        ),
        sa.CheckConstraint(
            "cardinality(input_artifact_ids) >= 1",
            name="ck_editing_output_lineages_inputs",
        ),
        sa.CheckConstraint(
            "cost_source in ('estimated', 'billed')",
            name="ck_editing_output_lineages_cost_source",
        ),
        sa.CheckConstraint("cost_currency = 'CNY'", name="ck_editing_output_lineages_currency"),
        sa.CheckConstraint("cost_billed_minutes >= 1", name="ck_editing_output_lineages_minutes"),
        sa.CheckConstraint(
            "cost_tier_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'",
            name="ck_editing_output_lineages_tier",
        ),
        sa.CheckConstraint(
            "cost_unit_price_cny >= 0", name="ck_editing_output_lineages_unit_price"
        ),
        sa.CheckConstraint(
            "cost_total_cny = cost_unit_price_cny * cost_billed_minutes",
            name="ck_editing_output_lineages_cost_total",
        ),
        sa.PrimaryKeyConstraint("editing_job_id", name="pk_editing_output_lineages"),
    )

    op.create_table(
        "editing_output_artifacts",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("editing_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column("sha256_hex", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_editing_output_artifacts_position"),
        sa.CheckConstraint(
            "kind in ('video', 'cover', 'subtitle', 'metadata')",
            name="ck_editing_output_artifacts_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'video' and media_type in ('video/mp4', 'video/webm'))"
            " or (kind = 'cover' and media_type in ('image/jpeg', 'image/png'))"
            " or (kind = 'subtitle' and media_type in ('text/vtt', 'application/x-subrip'))"
            " or (kind = 'metadata' and media_type = 'application/json')",
            name="ck_editing_output_artifacts_media",
        ),
        sa.CheckConstraint("byte_size >= 1", name="ck_editing_output_artifacts_bytes"),
        sa.CheckConstraint(
            "sha256_hex ~ '^[0-9a-f]{64}$'", name="ck_editing_output_artifacts_sha256"
        ),
        sa.ForeignKeyConstraint(
            ["editing_job_id"],
            ["editing_output_lineages.editing_job_id"],
            name="fk_editing_output_artifacts_lineage",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_editing_output_artifacts"),
        sa.UniqueConstraint(
            "editing_job_id", "position", name="ux_editing_output_artifacts_position"
        ),
    )
    op.create_index(
        "ux_editing_output_artifacts_one_video",
        "editing_output_artifacts",
        ["editing_job_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'video'"),
    )
