"""Bind every REST-created editing project to one owning Installation.

`editing_projects` predates its REST surface and intentionally contains only the
editing aggregate. Authentication alone is not authorization: without this
relation, any valid App Session could list every project stored by a shared
Control Plane. The relation keeps ownership outside the domain object while
making it structural in PostgreSQL:

* `project_id` is the primary key, so one project cannot have two owners;
* both sides are restrictive foreign keys, so neither identity can be invented;
* the API repository inserts the project and this row in one transaction;
* reads join this table before applying the stable project-page ordering.

There is no update path. Moving a project between Installations would be a new
product operation with its own authorization and audit contract, not an UPDATE
hidden inside project creation.

Revision ID: 20260730_0040
Revises: 20260729_0039
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0040"
down_revision: str | None = "20260729_0039"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "editing_project_installations",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["editing_projects.project_id"],
            name="fk_editing_project_installations_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_editing_project_installations_installation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "project_id",
            name="pk_editing_project_installations",
        ),
    )
    op.create_index(
        "ix_editing_project_installations_installation_project",
        "editing_project_installations",
        ["installation_id", "project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_editing_project_installations_installation_project",
        table_name="editing_project_installations",
    )
    op.drop_table("editing_project_installations")
