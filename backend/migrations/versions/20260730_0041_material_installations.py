"""Scope REST materials to one owning Installation.

The pre-REST material repository had one global digest namespace. That cannot
be used by an App Session: it would either expose another device's material or
make a duplicate on that device reveal that the other row exists. Descriptions
are mutable as well, so sharing one global row would let one Installation's
user-owned text affect another.

Existing rows remain in the NULL/internal namespace. Two partial unique indexes
retain its old digest rule while giving every non-NULL Installation an
independent digest namespace. API reads always include the owner predicate.

Revision ID: 20260730_0041
Revises: 20260730_0040
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0041"
down_revision: str | None = "20260730_0040"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column(
        "materials",
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_materials_installation",
        "materials",
        "installations",
        ["installation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_materials_content_digest",
        "materials",
        type_="unique",
    )
    op.create_index(
        "uq_materials_unscoped_content_digest",
        "materials",
        ["content_digest"],
        unique=True,
        postgresql_where=sa.text("installation_id IS NULL"),
    )
    op.create_index(
        "uq_materials_installation_content_digest",
        "materials",
        ["installation_id", "content_digest"],
        unique=True,
        postgresql_where=sa.text("installation_id IS NOT NULL"),
    )
    op.create_index(
        "ix_materials_installation_material",
        "materials",
        ["installation_id", "material_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_materials_installation_material",
        table_name="materials",
    )
    op.drop_index(
        "uq_materials_installation_content_digest",
        table_name="materials",
    )
    op.drop_index(
        "uq_materials_unscoped_content_digest",
        table_name="materials",
    )
    op.drop_constraint(
        "fk_materials_installation",
        "materials",
        type_="foreignkey",
    )
    op.drop_column("materials", "installation_id")
    op.create_unique_constraint(
        "uq_materials_content_digest",
        "materials",
        ["content_digest"],
    )
