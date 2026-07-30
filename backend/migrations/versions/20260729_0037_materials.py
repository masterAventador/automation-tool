"""Create materials, the imported-source-file half of the editing library.

Three columns hold JSONB documents -- `speech_segments_ms`,
`shot_boundaries_ms` and `ai_tags` -- and none of them is a shortcut around
modelling. They are homogeneous sequences of scalars owned entirely by one row,
never joined against and never queried by element; a side table for each would
add three joins to every read of a material and buy nothing back. What matters
is that **PostgreSQL does not look inside them**: `jsonb` will accept an object
where an array belongs, a three-element segment, a negative start, or a
transcript's worth of strings in the shot-boundary column. So every one of those
shapes is refused by `Material.__post_init__` when the row is hydrated, and the
repository converts a JSON array back into the tuples the domain declares
without converting anything that is not already the right shape.

Like `editing_projects` (0036), this table carries types, identity and NOT NULL
only, with **no** check constraints mirroring the domain's bounds -- and for the
same two reasons. The bounds belong to `material.py`; and SQL could only ever
hold a subset of them, since `speech_segments_ms` must not overrun
`duration_ms`, an image must have no duration at all, and a description written
by a person must carry no `described_at`. A partial set of constraints would
suggest the database validates rows when the guard that actually has to hold is
hydration through the constructor. **This is the opposite of what 0032 and 0034
did**, and deliberately so: those tables had no domain object behind them, so
their constraints were the only validation there was.

Three things do belong in the schema, because no domain object can see both sides:

* `pk_materials`, so a repeated identifier is refused rather than merged;
* `fk_materials_installation`, so a REST-visible material belongs to a real
  Installation while pre-REST internal rows retain a NULL namespace;
* partial unique indexes, so the same file cannot be imported twice inside one
  Installation while another Installation learns nothing from that digest.
  `Material` knows a digest's format and nothing about what else is stored, so
  the refusal has to be structural.

`content_digest` is `char(64)` rather than `varchar`: a SHA-256 hex digest has
exactly one length. The trade-off is `bpchar` semantics -- a shorter value is
blank-padded on the way in and compares equal ignoring trailing blanks -- which
never touches a digest written through the repository, because the domain admits
only 64 lowercase hex characters. A row inserted any other way comes back padded
and is refused at hydration, since a space is not a hex digit.

Three limits are spelled out here and enforced from a constant elsewhere:
`ai_description` at 2000 and `speech_transcript` at 100000 match
`MAX_DESCRIPTION_CHARACTERS` and `MAX_TRANSCRIPT_CHARACTERS`, which `schema.py`
imports. A migration is a frozen historical record and must not import a
constant that can later change underneath it, so the duplication is structural
rather than sloppy -- widening the domain without widening the column turns a
clean validation error into a `StringDataRightTruncation` at insert time, and an
integration test pins the two together by round-tripping the longest value the
domain accepts.

Revision ID: 20260729_0037
Revises: 20260729_0036
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0037"
down_revision: str | None = "20260729_0036"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "materials",
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("content_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("has_audio", sa.Boolean, nullable=False),
        sa.Column("audio_loudness_lufs", sa.Double, nullable=True),
        sa.Column("has_speech", sa.Boolean, nullable=False),
        sa.Column("speech_segments_ms", postgresql.JSONB, nullable=False),
        sa.Column("speech_transcript", sa.String(length=100000), nullable=True),
        sa.Column("shot_boundaries_ms", postgresql.JSONB, nullable=False),
        sa.Column("ai_description", sa.String(length=2000), nullable=True),
        sa.Column("ai_tags", postgresql.JSONB, nullable=False),
        sa.Column("description_source", sa.String(length=16), nullable=False),
        sa.Column("described_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_materials_installation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("material_id", name="pk_materials"),
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
    op.drop_table("materials")
