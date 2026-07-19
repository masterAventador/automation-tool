"""Create the non-sensitive platform Session health projection.

Revision ID: 20260718_0014
Revises: 20260718_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0014"
down_revision: str | None = "20260718_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store only Installation-scoped platform state, revision, and time."""
    op.create_table(
        "platform_session_health",
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("session_revision", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "platform = 'douyin'",
            name="ck_platform_session_health_platform",
        ),
        sa.CheckConstraint(
            "state in ('healthy', 'expired', 'missing', 'risk', 'unknown')",
            name="ck_platform_session_health_state",
        ),
        sa.CheckConstraint(
            "session_revision > 0",
            name="ck_platform_session_health_revision_positive",
        ),
        sa.CheckConstraint(
            "updated_at >= observed_at",
            name="ck_platform_session_health_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_platform_session_health_installation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "installation_id",
            "platform",
            name="pk_platform_session_health",
        ),
    )


def downgrade() -> None:
    """Remove only the non-sensitive platform health projection."""
    op.drop_table("platform_session_health")
