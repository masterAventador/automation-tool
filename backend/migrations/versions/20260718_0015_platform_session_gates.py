"""Create the persistent platform Session logout gate.

Revision ID: 20260718_0015
Revises: 20260718_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0015"
down_revision: str | None = "20260718_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_session_gates",
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("session_revision", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("platform = 'douyin'", name="ck_platform_session_gates_platform"),
        sa.CheckConstraint("state = 'blocked'", name="ck_platform_session_gates_state"),
        sa.CheckConstraint(
            "session_revision > 0",
            name="ck_platform_session_gates_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_platform_session_gates_installation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "installation_id",
            "platform",
            name="pk_platform_session_gates",
        ),
    )


def downgrade() -> None:
    op.drop_table("platform_session_gates")
