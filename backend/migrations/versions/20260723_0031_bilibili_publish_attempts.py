"""Create Bilibili publish attempts with single-admission archive creation.

Revision ID: 20260723_0031
Revises: 20260723_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0031"
down_revision: str | None = "20260723_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store publish intent, part progress, receipts; never access tokens."""
    op.create_table(
        "bilibili_publish_attempts",
        sa.Column("publish_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("material_file_name", sa.String(length=255), nullable=False),
        sa.Column("material_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("material_duration_seconds", sa.BigInteger(), nullable=False),
        sa.Column("material_sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("tid", sa.BigInteger(), nullable=False),
        sa.Column("tag", sa.String(length=200), nullable=False),
        sa.Column("copyright", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(length=250), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column("no_reprint", sa.BigInteger(), nullable=False),
        sa.Column("upload_type", sa.String(length=1), nullable=False),
        sa.Column("part_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("part_count", sa.BigInteger(), nullable=False),
        sa.Column("has_cover", sa.Boolean(), nullable=False),
        sa.Column("upload_token", sa.String(length=512), nullable=True),
        sa.Column("cover_url", sa.String(length=1024), nullable=True),
        sa.Column("video_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resource_id", sa.String(length=16), nullable=True),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("platform_error_code", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "phase in ('prepared', 'video_uploaded', 'dispatched', "
            "'submitted', 'failed', 'outcome_uncertain')",
            name="ck_bilibili_publish_attempts_phase",
        ),
        sa.CheckConstraint(
            "char_length(request_digest) = 64 and char_length(material_sha256) = 64",
            name="ck_bilibili_publish_attempts_digests",
        ),
        sa.CheckConstraint(
            "material_size_bytes > 0 and material_duration_seconds > 0",
            name="ck_bilibili_publish_attempts_material_bounds",
        ),
        sa.CheckConstraint(
            "copyright in (1, 2) and no_reprint in (0, 1) and tid > 0",
            name="ck_bilibili_publish_attempts_submission_fields",
        ),
        sa.CheckConstraint(
            "(upload_type = '0' and part_size_bytes = 0 and part_count = 0)"
            " or (upload_type = '1' and part_size_bytes > 0 and part_count > 0)",
            name="ck_bilibili_publish_attempts_upload_plan",
        ),
        sa.CheckConstraint(
            "phase <> 'prepared' or (video_uploaded_at is null"
            " and dispatched_at is null and settled_at is null)",
            name="ck_bilibili_publish_attempts_prepared_shape",
        ),
        sa.CheckConstraint(
            "phase <> 'video_uploaded' or (video_uploaded_at is not null"
            " and upload_token is not null and dispatched_at is null and settled_at is null)",
            name="ck_bilibili_publish_attempts_uploaded_shape",
        ),
        sa.CheckConstraint(
            "phase <> 'dispatched' or (video_uploaded_at is not null"
            " and dispatched_at is not null and settled_at is null)",
            name="ck_bilibili_publish_attempts_dispatched_shape",
        ),
        sa.CheckConstraint(
            "phase not in ('submitted', 'failed', 'outcome_uncertain')"
            " or (video_uploaded_at is not null and dispatched_at is not null"
            " and settled_at is not null)",
            name="ck_bilibili_publish_attempts_settled_shape",
        ),
        sa.CheckConstraint(
            "(resource_id is not null) = (phase = 'submitted')",
            name="ck_bilibili_publish_attempts_resource_id_shape",
        ),
        sa.CheckConstraint(
            "((failure_code is not null) = (phase = 'failed'))"
            " and ((platform_error_code is not null) = (phase = 'failed'))",
            name="ck_bilibili_publish_attempts_failure_shape",
        ),
        sa.CheckConstraint(
            "failure_code is null or failure_code in "
            "('invalid_input', 'dependency_unavailable', 'platform_error')",
            name="ck_bilibili_publish_attempts_failure_code",
        ),
        sa.CheckConstraint(
            "cover_url is null or has_cover",
            name="ck_bilibili_publish_attempts_cover_shape",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_bilibili_publish_attempts_time_order",
        ),
        sa.PrimaryKeyConstraint("publish_job_id", name="pk_bilibili_publish_attempts"),
    )
    op.create_table(
        "bilibili_upload_parts",
        sa.Column("publish_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("part_number", sa.BigInteger(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "part_number between 1 and 512",
            name="ck_bilibili_upload_parts_part_number",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_bilibili_upload_parts_size"),
        sa.ForeignKeyConstraint(
            ["publish_job_id"],
            ["bilibili_publish_attempts.publish_job_id"],
            name="fk_bilibili_upload_parts_publish_job_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "publish_job_id",
            "part_number",
            name="pk_bilibili_upload_parts",
        ),
    )


def downgrade() -> None:
    """Remove only the Bilibili publish attempt tables."""
    op.drop_table("bilibili_upload_parts")
    op.drop_table("bilibili_publish_attempts")
