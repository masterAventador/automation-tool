"""Create editing_jobs, and with it the three rules no aggregate can hold.

This is the mutable table of the four: a job moves through six states and its
row is rewritten each time, where `editing_projects`, `materials` and
`timelines` are written once. Everything unusual below follows from that, or
from the fact that no domain object here references another one -- `EditingJob`
holds an `EditingProjectId` and a `TimelineId` and has no way to ask what either
of them stands for.

**The composite foreign key carries two invariants at once.**
`(timeline_id, timeline_revision, project_id)` references
`timelines (timeline_id, revision, project_id)`, and that single key says both
that the revision the job names exists and that the project the job claims is
the project that revision belongs to. `project_id` sits on both rows on
purpose -- reading a job should not require a join -- and a duplicated fact is
precisely one that has to be made to agree. A plain foreign key from
`project_id` to `editing_projects` would be satisfied by *any* stored project,
including the wrong one, so it cannot express the triangle; and comparing the
two in the application is a check that two concurrent callers both pass. The
target is `uq_timelines_revision_project` from 0038, which exists for this and
for nothing else, since PostgreSQL requires a foreign key's referenced columns
to be covered by a unique constraint over exactly those columns and
`pk_timelines` spells only two of the three.

**The unique index is partial, and the `WHERE` is the point.** At most one
render of a revision may be waiting to start, because two callers asking for the
same cut is one duplicate request rather than two pieces of work. Restricting
the index to `status = 'queued'` is what lets the slot free itself: as soon as
the job starts, finishes or is cancelled its row stops matching, and the
revision can be queued again. An unrestricted unique index over the same two
columns would look like a stricter version of the same rule and would in fact be
a different one -- a revision could be rendered exactly once, ever, with no
retry after a failure and no second attempt after a cancellation.

Like the three tables before it this carries types, identity and NOT NULL only,
with **no** check constraints mirroring the domain. `status` and `failure_code`
store enumeration members as text and `failure_code` and `output_artifact_id`
are nullable, so the columns can say "sometimes absent" and nothing more --
which absence belongs to which state, and that `updated_at` never precedes
`created_at`, are statements SQL could hold only in part. A partial set would
suggest the database validates these rows when the guard that actually has to
hold is hydration through `EditingJob`'s constructor.

Revision ID: 20260729_0039
Revises: 20260729_0038
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0039"
down_revision: str | None = "20260729_0038"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "editing_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_revision", sa.Integer, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("output_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("job_id", name="pk_editing_jobs"),
        sa.ForeignKeyConstraint(
            ["timeline_id", "timeline_revision", "project_id"],
            ["timelines.timeline_id", "timelines.revision", "timelines.project_id"],
            name="fk_editing_jobs_timeline_revision",
        ),
    )
    op.create_index(
        "uq_editing_jobs_queued_timeline_revision",
        "editing_jobs",
        ["timeline_id", "timeline_revision"],
        unique=True,
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_table("editing_jobs")
