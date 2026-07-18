"""Create typed Douyin search exposure Task definitions.

Revision ID: 20260718_0013
Revises: 20260718_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0013"
down_revision: str | None = "20260718_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add one explicit versioned template without an arbitrary JSON definition."""
    op.create_table(
        "douyin_search_exposure_definitions",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "template",
            sa.String(length=64),
            server_default=sa.text("'douyin.search_exposure.v1'"),
            nullable=False,
        ),
        sa.Column("search_keyword", sa.String(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("message_template", sa.String(), nullable=True),
        sa.Column("target_limit", sa.BigInteger(), nullable=False),
        sa.Column("minimum_interval_seconds", sa.BigInteger(), nullable=False),
        sa.Column("maximum_interval_seconds", sa.BigInteger(), nullable=False),
        sa.Column(
            "preview_required",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "final_confirmation_required",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "template = 'douyin.search_exposure.v1'",
            name="ck_douyin_search_exposure_template",
        ),
        sa.CheckConstraint(
            "char_length(search_keyword) between 1 and 80 "
            "and octet_length(search_keyword) <= 320 "
            "and btrim(search_keyword) = search_keyword "
            "and search_keyword !~ '[[:cntrl:]]'",
            name="ck_douyin_search_exposure_keyword",
        ),
        sa.CheckConstraint(
            "action in ('browse', 'comment', 'direct_message')",
            name="ck_douyin_search_exposure_action",
        ),
        sa.CheckConstraint(
            "(action = 'browse' and message_template is null) or "
            "(action in ('comment', 'direct_message') and message_template is not null)",
            name="ck_douyin_search_exposure_message_presence",
        ),
        sa.CheckConstraint(
            "message_template is null or ("
            "char_length(message_template) between 1 and 500 "
            "and octet_length(message_template) <= 2000 "
            "and btrim(message_template) = message_template "
            "and message_template !~ '[[:cntrl:]]' "
            "and lower(message_template) not like '%bearer %' "
            "and lower(message_template) not like '%file://%' "
            "and lower(message_template) !~ "
            "'(access[_-]?token|api[_-]?key|authorization|cookie|credential|password|"
            "private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)"
            "[[:space:]]*[:=]')",
            name="ck_douyin_search_exposure_message_safe",
        ),
        sa.CheckConstraint(
            "target_limit between 1 and 100",
            name="ck_douyin_search_exposure_target_limit",
        ),
        sa.CheckConstraint(
            "minimum_interval_seconds between 1 and 3600 "
            "and maximum_interval_seconds between minimum_interval_seconds and 3600",
            name="ck_douyin_search_exposure_interval",
        ),
        sa.CheckConstraint(
            "preview_required and final_confirmation_required",
            name="ck_douyin_search_exposure_mandatory_confirmation",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "installation_id"],
            ["tasks.id", "tasks.installation_id"],
            name="fk_douyin_search_exposure_task_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "task_id",
            name="pk_douyin_search_exposure_definitions",
        ),
        sa.UniqueConstraint(
            "task_id",
            "installation_id",
            name="uq_douyin_search_exposure_binding",
        ),
    )


def downgrade() -> None:
    """Remove typed definitions while leaving their parent Tasks intact."""
    op.drop_table("douyin_search_exposure_definitions")
