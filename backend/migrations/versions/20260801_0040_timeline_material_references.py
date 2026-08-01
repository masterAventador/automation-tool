"""Make material deletion structurally safe for immutable timelines.

Timeline clips live in JSONB, so PostgreSQL could not previously see their
material identifiers.  A read-then-delete check in the repository would race a
concurrent timeline save.  This migration projects each distinct clip material
into a normalized reference row and protects it with foreign keys.  The
material-owner and project-owner composite keys also prove that a timeline can
only name a material belonging to the same Installation.

Revision ID: 20260801_0040
Revises: 20260729_0039
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0040"
down_revision: str | None = "20260729_0039"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_materials_material_installation",
        "materials",
        ["material_id", "installation_id"],
    )
    op.create_table(
        "timeline_material_references",
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_revision", sa.Integer(), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "timeline_id",
            "timeline_revision",
            "material_id",
            name="pk_timeline_material_references",
        ),
        sa.ForeignKeyConstraint(
            ["timeline_id", "timeline_revision", "project_id"],
            ["timelines.timeline_id", "timelines.revision", "timelines.project_id"],
            name="fk_timeline_material_references_timeline",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "installation_id"],
            ["editing_projects.project_id", "editing_projects.installation_id"],
            name="fk_timeline_material_references_project_owner",
        ),
        sa.ForeignKeyConstraint(
            ["material_id", "installation_id"],
            ["materials.material_id", "materials.installation_id"],
            name="fk_timeline_material_references_material_owner",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_timeline_material_references_installation_material",
        "timeline_material_references",
        ["installation_id", "material_id"],
    )
    op.execute(
        """
        INSERT INTO timeline_material_references (
            timeline_id,
            timeline_revision,
            project_id,
            installation_id,
            material_id
        )
        SELECT DISTINCT
            timeline.timeline_id,
            timeline.revision,
            timeline.project_id,
            project.installation_id,
            (clip.value ->> 'source_material_id')::uuid
        FROM timelines AS timeline
        JOIN editing_projects AS project
          ON project.project_id = timeline.project_id
        CROSS JOIN LATERAL jsonb_array_elements(timeline.tracks) AS track(value)
        CROSS JOIN LATERAL jsonb_array_elements(track.value -> 'clips') AS clip(value)
        WHERE clip.value -> 'source_material_id' IS NOT NULL
          AND jsonb_typeof(clip.value -> 'source_material_id') = 'string'
        """
    )


def downgrade() -> None:
    op.drop_table("timeline_material_references")
    op.drop_constraint(
        "uq_materials_material_installation",
        "materials",
        type_="unique",
    )
