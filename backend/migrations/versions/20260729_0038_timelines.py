"""Create timelines, the immutable-revision half of the editing library.

`editing_project_timelines` gives each project exactly one immutable timeline
identity. `project_id` is the primary key and `timeline_id` is unique; the first
save claims that identity in the same transaction that writes revision 1.
Concurrent first saves therefore cannot create two lineages for one project.

Every `timelines` row is one revision of that cut, and no row is ever updated.
That is why the primary key is composite -- `(timeline_id, revision)` -- rather
than a surrogate key with a version column: a revision is a snapshot, revisions
of the same timeline coexist, and the second insert of a revision has to be
refused rather than merged.

`tracks` is one JSONB document holding a four-level tree: the timeline's tracks,
each track's clips, and a clip's incoming transition. It is not split into clip
and transition tables, and that is a decision rather than a shortcut. A revision
is immutable and the renderer reads it whole; nothing in this release queries
across clips or joins them to anything; and a clip table would add two joins to
every read while buying back nothing. What matters is the consequence:
**PostgreSQL does not look inside this column at any depth.** `jsonb` will
happily accept an object where the array of tracks belongs, a clip missing half
its fields, a level stored as a whole number, a transition naming a kind that
does not exist, or a picture lane with a hole in the middle of it. So every one
of those shapes is refused by `Timeline.__post_init__` when the row is hydrated,
and the repository rebuilds the tree through the domain's own constructors
without converting anything that is not already the right shape.

The composite `fk_timelines_project_timeline` makes every revision name the
identity claimed for its project. A plain project foreign key would prove only
that the project exists while still allowing a second timeline id.

The Timeline table also carries a superkey most likely to be mistaken for
clutter:

* `pk_timelines`, described above;
* `uq_timelines_revision_project` -- **a superkey of the primary key, which
  therefore refuses nothing the primary key had not already refused.** It is not
  redundant and it must not be dropped. It exists to be the *target* of the
  composite foreign key `editing_jobs` declares in 0039:
  `(timeline_id, timeline_revision, project_id)` referencing
  `(timeline_id, revision, project_id)`. PostgreSQL requires a foreign key's
  referenced columns to be covered by a unique constraint over exactly those
  columns, and the primary key covers only two of the three. That composite
  reference is what makes "an editing job's project is the project its timeline
  belongs to" a fact the database holds -- a plain single-column foreign key
  cannot express the triangle, and an application-layer check on it is one two
  concurrent callers both pass.

Like `editing_projects` (0036) and `materials` (0037), this table carries types,
identity and NOT NULL only, with **no** check constraints mirroring the domain's
bounds. The bounds belong to `timeline.py`, and SQL could only ever hold a
subset of them: that a picture lane runs end to end without a gap, that a
transition may not overlap more of the outgoing clip than that clip has left
after an earlier transition already ate into it, and that the declared
`duration_ms` equals where the picture lane really ends are all statements about
a tree in a document. A partial set of constraints would suggest the database
validates these rows when the guard that actually has to hold is hydration
through the constructor.

Revision ID: 20260729_0038
Revises: 20260729_0037
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0038"
down_revision: str | None = "20260729_0037"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "editing_project_timelines",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("project_id", name="pk_editing_project_timelines"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["editing_projects.project_id"],
            name="fk_editing_project_timelines_project",
        ),
        sa.UniqueConstraint(
            "timeline_id",
            name="uq_editing_project_timelines_timeline",
        ),
        sa.UniqueConstraint(
            "project_id",
            "timeline_id",
            name="uq_editing_project_timelines_project_timeline",
        ),
    )
    op.create_table(
        "timelines",
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("tracks", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("timeline_id", "revision", name="pk_timelines"),
        sa.ForeignKeyConstraint(
            ["project_id", "timeline_id"],
            [
                "editing_project_timelines.project_id",
                "editing_project_timelines.timeline_id",
            ],
            name="fk_timelines_project_timeline",
        ),
        sa.UniqueConstraint(
            "timeline_id", "revision", "project_id", name="uq_timelines_revision_project"
        ),
    )


def downgrade() -> None:
    op.drop_table("timelines")
    op.drop_table("editing_project_timelines")
