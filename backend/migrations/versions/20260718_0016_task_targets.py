"""Create Installation-scoped Task target preview rows.

Revision ID: 20260718_0016
Revises: 20260718_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0016"
down_revision: str | None = "20260718_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist a bounded preview snapshot without arbitrary page payloads."""
    op.create_table(
        "task_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("platform_target_id", sa.String(length=128), nullable=False),
        sa.Column("dedupe_key", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("public_handle", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("page_revision", sa.BigInteger(), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_task_targets_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "ordinal between 1 and 100",
            name="ck_task_targets_ordinal_range",
        ),
        sa.CheckConstraint(
            "platform_target_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'",
            name="ck_task_targets_platform_target_id",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^atdck1_[A-Za-z0-9_-]{43}$'",
            name="ck_task_targets_candidate_key",
        ),
        sa.CheckConstraint(
            "char_length(display_name) between 1 and 80 "
            "and octet_length(display_name) <= 320 "
            "and btrim(display_name) = display_name "
            "and display_name !~ '[[:cntrl:]]' "
            "and lower(display_name) not like '%bearer %' "
            "and lower(display_name) not like '%file://%' "
            "and lower(display_name) not like '%data:%;base64,%' "
            "and lower(display_name) !~ "
            "'(access[_-]?token|api[_-]?key|authorization|cookie|credential|password|"
            "private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)"
            "[[:space:]]*[:=]'",
            name="ck_task_targets_display_name",
        ),
        sa.CheckConstraint(
            "public_handle is null or "
            "public_handle ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'",
            name="ck_task_targets_public_handle",
        ),
        sa.CheckConstraint(
            "source = 'general_search_author'",
            name="ck_task_targets_source",
        ),
        sa.CheckConstraint(
            "page_revision between 1 and 9007199254740991",
            name="ck_task_targets_page_revision_range",
        ),
        sa.CheckConstraint(
            "disposition in "
            "('eligible', 'duplicate_in_task', 'duplicate_in_history', 'blacklisted')",
            name="ck_task_targets_disposition",
        ),
        sa.CheckConstraint(
            "policy_version = 'douyin.candidate-policy.v1'",
            name="ck_task_targets_policy_version",
        ),
        sa.CheckConstraint(
            "created_at >= evaluated_at",
            name="ck_task_targets_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "installation_id"],
            ["tasks.id", "tasks.installation_id"],
            name="fk_task_targets_task_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_targets"),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "installation_id",
            name="uq_task_targets_binding",
        ),
        sa.UniqueConstraint(
            "task_id",
            "installation_id",
            "ordinal",
            name="uq_task_targets_task_ordinal",
        ),
    )
    op.create_index(
        "ix_task_targets_installation_task_page",
        "task_targets",
        ["installation_id", "task_id", "page_revision", "ordinal", "id"],
    )
    op.create_index(
        "ix_task_targets_installation_history",
        "task_targets",
        ["installation_id", "dedupe_key", "evaluated_at"],
    )


def downgrade() -> None:
    """Remove target previews while leaving their parent Tasks intact."""
    op.drop_table("task_targets")
