"""Create durable finished-output lineages and artifacts for VE-07.

Revision ID: 20260723_0034
Revises: 20260723_0033
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0034"
down_revision: str | None = "20260723_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist finished editing outputs, lineage and cost as write-once rows."""
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


def downgrade() -> None:
    op.drop_index("ux_editing_output_artifacts_one_video", table_name="editing_output_artifacts")
    op.drop_table("editing_output_artifacts")
    op.drop_table("editing_output_lineages")
