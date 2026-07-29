"""Create editing projects, the first of the four local-editing tables.

`OutputSpec` and `CaptionStyle` are flattened into columns rather than stored as
one JSONB document. Both are pure scalars, and JSONB would give back `int` and
`float` as whatever the document happened to encode, losing exactly the types
the domain checks -- while buying nothing, since neither value object is
open-ended or queried by shape.

The table carries types, identity and NOT NULL only. It deliberately has **no**
check constraints mirroring the domain's bounds, and this is not an oversight:

* the bounds belong to `editing_project.py`, which spent LE-04 getting them
  right; a second copy in SQL is a second thing to keep in step;
* SQL could only ever hold a subset of them. `stroke_px * 2 < font_px` and
  `font_px <= output_height` are cross-field, and "trimmed, no control
  characters" is not expressible as a sane check -- so a partial set of
  constraints would suggest the database validates rows when the guard that
  actually has to hold is the repository hydrating through the constructor.

Cross-aggregate invariants are a different matter and do go into the schema:
0038 and 0039 add the composite foreign key and the partial unique index that
no domain object can enforce, because no domain object can see both sides.

Revision ID: 20260729_0036
Revises: 20260728_0035
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0036"
down_revision: str | None = "20260728_0035"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "editing_projects",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("output_width", sa.Integer, nullable=False),
        sa.Column("output_height", sa.Integer, nullable=False),
        sa.Column("output_fps", sa.Integer, nullable=False),
        sa.Column("caption_font_key", sa.String(length=64), nullable=False),
        sa.Column("caption_font_px", sa.Integer, nullable=False),
        sa.Column("caption_stroke_px", sa.Integer, nullable=False),
        sa.Column("caption_line_spacing", sa.Double, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_id", name="pk_editing_projects"),
    )


def downgrade() -> None:
    op.drop_table("editing_projects")
