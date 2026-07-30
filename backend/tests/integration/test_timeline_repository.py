"""LE-05 T3: timelines on a real PostgreSQL.

Every assertion reads the database back, either through the repository or
through raw Core statements that bypass it. A repository that only ever reads
its own writes proves nothing about what actually landed.

Two things make this table different from the two before it. `tracks` is a
single JSONB document holding a four-level tree -- timeline, track, clip,
transition -- that PostgreSQL never looks inside at any depth. And the primary
key is composite, `(timeline_id, revision)`, because a revision is an immutable
snapshot: rows are inserted and never updated, and the second insert of a
revision has to be refused rather than merged.

Rows here reference `editing_projects`, so every test that stores a timeline
stores a project first, through T1's repository rather than a raw INSERT. Clean
up runs in the same order reversed -- timelines before projects -- because the
foreign key has no ON DELETE action and the other LE-05 integration files delete
`editing_projects` wholesale in their own fixtures.
"""

from __future__ import annotations

import asyncio
import secrets
import traceback
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from alembic_head import HEAD_REVISION
from conftest import AlembicRunner
from sqlalchemy import ForeignKeyConstraint, delete, insert, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.schema import ColumnCollectionConstraint

from automation_tool.control_plane.application.timelines import (
    TimelineDataRejected,
    TimelineNotFound,
    TimelinePersistenceUnavailable,
    TimelineProjectMissing,
    TimelineRevisionAlreadyStored,
)
from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    InstallationId,
    InvalidTimelineModel,
    MaterialId,
    OutputSpec,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)

# From the module rather than the package: LE-03 deliberately keeps the two
# length bounds off `domain/__init__.py`'s public surface, and nothing outside
# this file needs them yet. Widening that surface for a test's convenience would
# be a change to LE-03 made as a side effect of this task.
from automation_tool.control_plane.domain.timeline import (
    MAX_TIMELINE_DURATION_MS,
    MIN_TIMELINE_DURATION_MS,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    editing_project_timelines,
    editing_projects,
    installations,
    timelines,
)
from automation_tool.control_plane.infrastructure.database.editing_project_repository import (
    SqlAlchemyEditingProjectRepository,
)
from automation_tool.control_plane.infrastructure.database.timeline_repository import (
    SqlAlchemyTimelineRepository,
)

# Carrying microseconds, so a timestamp column that silently truncates is caught.
CREATED_AT = datetime(2026, 7, 29, 3, 21, 45, 123_456, tzinfo=UTC)
OWNER = InstallationId.parse("00000000-0000-4000-8000-000000000001")

MATERIAL_ONE = MaterialId.new()
MATERIAL_TWO = MaterialId.new()
MATERIAL_THREE = MaterialId.new()
MATERIAL_FOUR = MaterialId.new()
MATERIAL_FIVE = MaterialId.new()

DURATION_MS = 13_200
CAPTION_ONE = "今天我们去露营"
CAPTION_TWO = "第二段字幕"

PREVIOUS_REVISION = "20260729_0037"

EXPECTED_COLUMNS = {
    "timeline_id": ("uuid", "NO", None),
    "revision": ("integer", "NO", None),
    "project_id": ("uuid", "NO", None),
    "duration_ms": ("integer", "NO", None),
    "tracks": ("jsonb", "NO", None),
    "created_at": ("timestamp with time zone", "NO", None),
}
EXPECTED_CONSTRAINTS = {
    "pk_timelines",
    "uq_timelines_revision_project",
    "fk_timelines_project_timeline",
}
EXPECTED_IDENTITY_COLUMNS = {
    "project_id": ("uuid", "NO", None),
    "timeline_id": ("uuid", "NO", None),
}
EXPECTED_IDENTITY_CONSTRAINTS = {
    "pk_editing_project_timelines",
    "fk_editing_project_timelines_project",
    "uq_editing_project_timelines_timeline",
    "uq_editing_project_timelines_project_timeline",
}

EXPECTED_TABLE_TYPES = {
    "timeline_id": "UUID",
    "revision": "Integer",
    "project_id": "UUID",
    "duration_ms": "Integer",
    "tracks": "JSONB",
    "created_at": "DateTime",
}

# What `schema.py` declares, as opposed to what the migration built. The two are
# written separately and drift silently: `migrations/env.py` points
# `target_metadata` at this metadata, so anything the database has and this does
# not is what the next `--autogenerate` offers to drop. Column *order* is part
# of the value because the composite foreign key T4 declares has to name these
# columns in this order.
EXPECTED_TABLE_CONSTRAINTS = {
    "pk_timelines": ("PrimaryKeyConstraint", ["timeline_id", "revision"]),
    "fk_timelines_project_timeline": (
        "ForeignKeyConstraint",
        ["project_id", "timeline_id"],
    ),
    "uq_timelines_revision_project": (
        "UniqueConstraint",
        ["timeline_id", "revision", "project_id"],
    ),
}

# The superkey exists for exactly one reader: `editing_jobs` (T4) points a
# composite foreign key at it, which is the only way to hold "a job's project is
# the project its timeline belongs to". Its columns are pinned here so that
# renaming or re-columning it fails in this task rather than in that one.
SUPERKEY_NAME = "uq_timelines_revision_project"
SUPERKEY_COLUMNS = ["timeline_id", "revision", "project_id"]
FOREIGN_KEY_PROBE_TABLE = "le05_t3_composite_fk_probe"


def forged_identifier(value: UUID) -> TimelineId:
    """A `TimelineId` holding a UUID its constructor would never accept.

    `TimelineId(UUID(int=0))` raises, so a stored row whose `timeline_id` is not
    a v4 UUID cannot be addressed through the normal constructor at all.
    Building the instance without it is what lets a test reach such a row; the
    row itself gets there through a plain INSERT, which the `uuid` column takes
    happily. Subclassing is not an option -- the class is `@final`.
    """
    identifier = object.__new__(TimelineId)
    object.__setattr__(identifier, "_value", value)
    return identifier


def make_project(project_id: EditingProjectId) -> EditingProject:
    return EditingProject(
        project_id=project_id,
        title="夏日露营 第一集",
        output=OutputSpec(width=1280, height=720, fps=30),
        caption_style=CaptionStyle(
            font_key="source-han-sans", font_px=48, stroke_px=3, line_spacing=1.25
        ),
        created_at=CREATED_AT,
    )


def make_timeline(
    timeline_id: TimelineId,
    project_id: EditingProjectId,
    *,
    revision: int = 1,
) -> Timeline:
    """The cut every round trip starts from, built through the domain.

    Deliberately not minimal: it carries one of every shape the JSON document
    has to survive -- a clip with a source window, a still image with no window
    and therefore no level, an incoming transition, two levels one of which is a
    whole number, and caption clips whose text is what they carry instead of a
    material.
    """
    return Timeline(
        timeline_id=timeline_id,
        project_id=project_id,
        revision=revision,
        duration_ms=DURATION_MS,
        tracks=(
            TimelineTrack(
                track_id="visual",
                kind=TimelineTrackKind.VISUAL,
                clips=(
                    TimelineClip(
                        clip_id="v-one",
                        start_ms=0,
                        duration_ms=6_000,
                        source_material_id=MATERIAL_ONE,
                        source_in_ms=1_000,
                        source_out_ms=7_000,
                        text=None,
                        gain_db=None,
                        transition_in=None,
                    ),
                    TimelineClip(
                        clip_id="v-two",
                        start_ms=5_200,
                        duration_ms=5_000,
                        source_material_id=MATERIAL_TWO,
                        source_in_ms=0,
                        source_out_ms=5_000,
                        text=None,
                        gain_db=None,
                        transition_in=TimelineTransition(kind=TransitionKind.FADE, duration_ms=800),
                    ),
                    TimelineClip(
                        clip_id="v-three",
                        start_ms=10_200,
                        duration_ms=3_000,
                        source_material_id=MATERIAL_THREE,
                        source_in_ms=None,
                        source_out_ms=None,
                        text=None,
                        gain_db=None,
                        transition_in=None,
                    ),
                ),
            ),
            TimelineTrack(
                track_id="narration",
                kind=TimelineTrackKind.NARRATION,
                clips=(
                    TimelineClip(
                        clip_id="n-one",
                        start_ms=0,
                        duration_ms=4_000,
                        source_material_id=MATERIAL_FOUR,
                        source_in_ms=200,
                        source_out_ms=4_200,
                        text=None,
                        gain_db=-3.5,
                        transition_in=None,
                    ),
                    TimelineClip(
                        clip_id="n-two",
                        start_ms=5_000,
                        duration_ms=2_500,
                        source_material_id=MATERIAL_FIVE,
                        source_in_ms=0,
                        source_out_ms=2_500,
                        text=None,
                        gain_db=-6.0,
                        transition_in=None,
                    ),
                ),
            ),
            TimelineTrack(
                track_id="caption",
                kind=TimelineTrackKind.CAPTION,
                clips=(
                    TimelineClip(
                        clip_id="c-one",
                        start_ms=500,
                        duration_ms=2_500,
                        source_material_id=None,
                        source_in_ms=None,
                        source_out_ms=None,
                        text=CAPTION_ONE,
                        gain_db=None,
                        transition_in=None,
                    ),
                    TimelineClip(
                        clip_id="c-two",
                        start_ms=3_200,
                        duration_ms=2_000,
                        source_material_id=None,
                        source_in_ms=None,
                        source_out_ms=None,
                        text=CAPTION_TWO,
                        gain_db=None,
                        transition_in=None,
                    ),
                ),
            ),
        ),
        created_at=CREATED_AT,
    )


def clip_document(
    clip_id: str,
    start_ms: object,
    duration_ms: object,
    *,
    source_material_id: object = None,
    source_in_ms: object = None,
    source_out_ms: object = None,
    text_value: object = None,
    gain_db: object = None,
    transition_in: object = None,
) -> dict[str, object]:
    """A clip as it really sits in the column, written out by hand.

    Spelled here rather than borrowed from the repository's own serialiser: a
    test that asked the code under test what shape it writes would agree with
    any shape it wrote.
    """
    return {
        "clip_id": clip_id,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "source_material_id": source_material_id,
        "source_in_ms": source_in_ms,
        "source_out_ms": source_out_ms,
        "text": text_value,
        "gain_db": gain_db,
        "transition_in": transition_in,
    }


def track_documents() -> list[dict[str, object]]:
    """The `tracks` document matching `make_timeline`, key for key."""
    return [
        {
            "track_id": "visual",
            "kind": "visual",
            "clips": [
                clip_document(
                    "v-one",
                    0,
                    6_000,
                    source_material_id=str(MATERIAL_ONE),
                    source_in_ms=1_000,
                    source_out_ms=7_000,
                ),
                clip_document(
                    "v-two",
                    5_200,
                    5_000,
                    source_material_id=str(MATERIAL_TWO),
                    source_in_ms=0,
                    source_out_ms=5_000,
                    transition_in={"kind": "fade", "duration_ms": 800},
                ),
                clip_document("v-three", 10_200, 3_000, source_material_id=str(MATERIAL_THREE)),
            ],
        },
        {
            "track_id": "narration",
            "kind": "narration",
            "clips": [
                clip_document(
                    "n-one",
                    0,
                    4_000,
                    source_material_id=str(MATERIAL_FOUR),
                    source_in_ms=200,
                    source_out_ms=4_200,
                    gain_db=-3.5,
                ),
                clip_document(
                    "n-two",
                    5_000,
                    2_500,
                    source_material_id=str(MATERIAL_FIVE),
                    source_in_ms=0,
                    source_out_ms=2_500,
                    gain_db=-6.0,
                ),
            ],
        },
        {
            "track_id": "caption",
            "kind": "caption",
            "clips": [
                clip_document("c-one", 500, 2_500, text_value=CAPTION_ONE),
                clip_document("c-two", 3_200, 2_000, text_value=CAPTION_TWO),
            ],
        },
    ]


def row_values(timeline_id: UUID, project_id: UUID, **overrides: object) -> dict[str, object]:
    """The exact column payload `save` is expected to write."""
    values: dict[str, object] = {
        "timeline_id": timeline_id,
        "revision": 1,
        "project_id": project_id,
        "duration_ms": DURATION_MS,
        "tracks": track_documents(),
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return values


async def reset_data(database: Database) -> None:
    """Revisions, identities, then projects: none cascades on delete."""
    async with database.session() as session:
        await session.execute(delete(timelines))
        await session.execute(delete(editing_project_timelines))
        await session.execute(delete(editing_projects))


async def store_project(database: Database, project_id: EditingProjectId) -> None:
    """Through T1's repository, not a raw INSERT -- the production path."""
    async with database.session() as session:
        exists = await session.scalar(
            select(installations.c.id).where(installations.c.id == OWNER.uuid)
        )
        if exists is None:
            await session.execute(
                insert(installations).values(
                    id=OWNER.uuid,
                    device_public_key=secrets.token_bytes(32),
                )
            )
    await SqlAlchemyEditingProjectRepository(database).save(
        make_project(project_id),
        OWNER,
    )


async def stored_row(database: Database, timeline_id: UUID, revision: int) -> dict[str, object]:
    async with database.session() as session:
        row = (
            (
                await session.execute(
                    select(timelines).where(
                        timelines.c.timeline_id == timeline_id,
                        timelines.c.revision == revision,
                    )
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


async def insert_row(
    database: Database,
    timeline_id: UUID,
    project_id: UUID,
    **overrides: object,
) -> None:
    async with database.session() as session:
        await session.execute(
            postgresql_insert(editing_project_timelines)
            .values(project_id=project_id, timeline_id=timeline_id)
            .on_conflict_do_nothing()
        )
        await session.execute(
            insert(timelines).values(**row_values(timeline_id, project_id, **overrides))
        )


async def insert_unclaimed_row(
    database: Database,
    timeline_id: UUID,
    project_id: UUID,
) -> None:
    async with database.session() as session:
        await session.execute(
            insert(timelines).values(**row_values(timeline_id, project_id))
        )


async def stored_keys(database: Database) -> set[tuple[UUID, int]]:
    async with database.session() as session:
        rows = (await session.execute(select(timelines.c.timeline_id, timelines.c.revision))).all()
    return {(cast(UUID, row[0]), cast(int, row[1])) for row in rows}


@pytest.mark.asyncio
async def test_saved_timeline_lands_as_typed_columns_and_hydrates_back_equal(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        await store_project(database, project_id)
        timeline_id = TimelineId.new()
        timeline = make_timeline(timeline_id, project_id)

        await repository.save(timeline, OWNER)

        row = await stored_row(database, timeline_id.uuid, 1)
        assert row == row_values(timeline_id.uuid, project_id.uuid)
        # The JSONB codec facts, pinned all four levels down. A driver or
        # dialect change that started handing the document back as text would
        # otherwise turn every hydration into a domain rejection at run time,
        # and nothing here would have said why.
        tracks = cast(list[object], row["tracks"])
        assert type(tracks) is list
        visual = cast(dict[str, object], tracks[0])
        assert type(visual) is dict
        clips = cast(list[object], visual["clips"])
        assert type(clips) is list
        assert type(clips[0]) is dict
        assert type(cast(dict[str, object], clips[1])["transition_in"]) is dict
        # A whole-number level has to survive as a JSON float. PostgreSQL keeps
        # `-6.0` and `-6` apart and hands the second back as an `int`, which the
        # domain refuses outright -- so a serialiser normalising this would
        # store a row that could never be loaded again.
        narration = cast(dict[str, object], tracks[1])
        second = cast(dict[str, object], cast(list[object], narration["clips"])[1])
        assert type(second["gain_db"]) is float
        assert second["gain_db"] == -6.0
        created_at = row["created_at"]
        assert isinstance(created_at, datetime)
        assert created_at.tzinfo is UTC

        loaded = await repository.get(timeline_id, 1, OWNER)
        assert loaded == timeline
        # Equality alone would not catch this: a dataclass comparing two
        # hydrated objects agrees with itself whatever the container type. The
        # domain declares tuples at every level.
        assert type(loaded.tracks) is tuple
        assert type(loaded.tracks[0]) is TimelineTrack
        assert type(loaded.tracks[0].clips) is tuple
        assert type(loaded.tracks[0].clips[0]) is TimelineClip
        assert type(loaded.tracks[0].clips[1].transition_in) is TimelineTransition
        assert loaded.tracks[0].kind is TimelineTrackKind.VISUAL
        assert loaded.tracks[0].clips[0].source_material_id == MATERIAL_ONE
        assert loaded.tracks[2].clips[0].text == CAPTION_ONE
        assert type(loaded.tracks[1].clips[1].gain_db) is float
        assert loaded.created_at.tzinfo is UTC
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_stored_revision_cannot_be_overwritten(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A revision is a snapshot, so the second insert is refused, never merged.

    The stored row is compared afterwards, because a refusal that had silently
    upserted would still raise nothing and still leave the caller believing the
    original was intact.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        await store_project(database, project_id)
        timeline_id = TimelineId.new()

        with pytest.raises(TimelineNotFound):
            await repository.get(timeline_id, 1, OWNER)

        await repository.save(make_timeline(timeline_id, project_id), OWNER)
        with pytest.raises(TimelineRevisionAlreadyStored):
            await repository.save(make_timeline(timeline_id, project_id), OWNER)

        assert await stored_row(database, timeline_id.uuid, 1) == row_values(
            timeline_id.uuid, project_id.uuid
        )
        # A different revision of the same timeline is an ordinary new row.
        await repository.save(make_timeline(timeline_id, project_id, revision=2), OWNER)
        assert await stored_keys(database) == {(timeline_id.uuid, 1), (timeline_id.uuid, 2)}
        # ... and a revision nobody stored is still missing, so the composite
        # lookup is not ignoring half its argument.
        with pytest.raises(TimelineNotFound):
            await repository.get(timeline_id, 3, OWNER)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_the_primary_key_is_enforced_by_postgresql_itself(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Violation injection: the refusal is the database's, not a Python branch.

    A repository that looked the row up before inserting would pass the test
    above while still letting two concurrent callers both find nothing and both
    proceed. These inserts never touch the repository.

    Only the primary key can be attributed this way. The superkey unique
    constraint covers a *superset* of its columns, so every row that violates
    the superkey violates the primary key too -- measured: PostgreSQL reports
    `pk_timelines` for a row that breaks both. That constraint therefore gets an
    existence-and-shape assertion of its own rather than an injected row.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        first_project = EditingProjectId.new()
        second_project = EditingProjectId.new()
        await store_project(database, first_project)
        await store_project(database, second_project)
        timeline_id = TimelineId.new()
        other_timeline = TimelineId.new()
        await insert_row(database, timeline_id.uuid, first_project.uuid)

        # The exact same revision is refused by the primary key.
        with pytest.raises(IntegrityError) as repeated:
            await insert_row(database, timeline_id.uuid, first_project.uuid)
        assert getattr(repeated.value.orig, "sqlstate", None) == "23505"
        assert "pk_timelines" in str(repeated.value.orig)

        # One revision moved on the same lineage, and one wholly separate
        # project-lineage pair: both must be accepted.
        await insert_row(database, timeline_id.uuid, first_project.uuid, revision=2)
        await insert_row(database, other_timeline.uuid, second_project.uuid)
        assert await stored_keys(database) == {
            (timeline_id.uuid, 1),
            (timeline_id.uuid, 2),
            (other_timeline.uuid, 1),
        }
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_one_project_and_one_timeline_id_can_form_only_one_identity(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Both halves of the one-to-one identity and its revision foreign key."""
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        first_project = EditingProjectId.new()
        second_project = EditingProjectId.new()
        first_timeline = TimelineId.new()
        second_timeline = TimelineId.new()
        await store_project(database, first_project)
        await store_project(database, second_project)
        async with database.session() as session:
            await session.execute(
                insert(editing_project_timelines).values(
                    project_id=first_project.uuid,
                    timeline_id=first_timeline.uuid,
                )
            )

        with pytest.raises(IntegrityError) as second_lineage:
            async with database.session() as session:
                await session.execute(
                    insert(editing_project_timelines).values(
                        project_id=first_project.uuid,
                        timeline_id=second_timeline.uuid,
                    )
                )
        assert "pk_editing_project_timelines" in str(second_lineage.value.orig)

        with pytest.raises(IntegrityError) as shared_identity:
            async with database.session() as session:
                await session.execute(
                    insert(editing_project_timelines).values(
                        project_id=second_project.uuid,
                        timeline_id=first_timeline.uuid,
                    )
                )
        assert "uq_editing_project_timelines_timeline" in str(shared_identity.value.orig)

        with pytest.raises(IntegrityError) as unclaimed_revision:
            await insert_unclaimed_row(
                database,
                second_timeline.uuid,
                first_project.uuid,
            )
        assert "fk_timelines_project_timeline" in str(unclaimed_revision.value.orig)
        assert await stored_keys(database) == set()
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_first_saves_commit_one_identity_and_one_revision(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        await store_project(database, project_id)
        first = make_timeline(TimelineId.new(), project_id)
        second = make_timeline(TimelineId.new(), project_id)
        start = asyncio.Event()

        async def save_after_start(timeline: Timeline) -> None:
            await start.wait()
            await repository.save(timeline, OWNER)

        first_save = asyncio.create_task(save_after_start(first))
        second_save = asyncio.create_task(save_after_start(second))
        start.set()
        outcomes = await asyncio.gather(first_save, second_save, return_exceptions=True)

        assert sum(outcome is None for outcome in outcomes) == 1
        conflicts = [
            outcome for outcome in outcomes if isinstance(outcome, TimelineRevisionAlreadyStored)
        ]
        assert len(conflicts) == 1
        async with database.session() as session:
            identities = (await session.execute(select(editing_project_timelines))).all()
            revisions = (await session.execute(select(timelines))).all()
        assert len(identities) == 1
        assert len(revisions) == 1
        assert identities[0].project_id == project_id.uuid
        assert revisions[0].project_id == project_id.uuid
        assert revisions[0].timeline_id == identities[0].timeline_id
        assert revisions[0].revision == 1
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_timeline_naming_a_project_nobody_stored_is_refused(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The foreign key, through the repository and by injection.

    A timeline pointing at a project that is not there is the caller's mistake
    and no retry fixes it, so it is neither "already stored" nor "unavailable".
    It gets its own answer because LE-06 has to say 404 about a *different*
    resource than the one that was asked for, and telling it apart from a
    duplicate revision is the only way it can.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        absent_project = EditingProjectId.new()
        timeline_id = TimelineId.new()

        with pytest.raises(TimelineProjectMissing) as refused:
            await repository.save(make_timeline(timeline_id, absent_project), OWNER)
        # PostgreSQL's DETAIL line quotes the key it could not find, which is
        # the project identifier the caller handed over.
        rendered = "".join(traceback.format_exception(refused.value))
        assert str(absent_project) not in rendered
        assert refused.value.__cause__ is None
        assert await stored_keys(database) == set()

        with pytest.raises(IntegrityError) as injected:
            await insert_row(database, timeline_id.uuid, absent_project.uuid)
        assert getattr(injected.value.orig, "sqlstate", None) == "23503"
        assert "fk_editing_project_timelines_project" in str(injected.value.orig)

        # The same timeline lands once the project it names exists, so the
        # refusal above was about the reference and nothing else.
        await store_project(database, absent_project)
        await repository.save(make_timeline(timeline_id, absent_project), OWNER)
        assert await stored_keys(database) == {(timeline_id.uuid, 1)}
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_the_superkey_can_carry_the_composite_foreign_key_t4_needs(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """`UNIQUE (timeline_id, revision, project_id)` is not redundant paperwork.

    It is a superkey of the primary key, so it refuses nothing the primary key
    would not have refused, and no injected row can demonstrate it. What it is
    for is being the *target* of `editing_jobs`'s composite foreign key in T4 --
    which is how "a job's project is the project its timeline belongs to"
    becomes a structural fact instead of an application-layer check that two
    concurrent callers can both pass.

    So this asserts the property rather than the name: a real composite foreign
    key is declared against those three columns here and has to be accepted.
    PostgreSQL refuses one with "there is no unique constraint matching given
    keys" when the target is missing or spans different columns, which is
    exactly the failure T4 would otherwise discover. The catalogue assertions
    that follow pin the name and column order so T4's migration can refer to it.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            columns = list(
                await session.scalars(
                    text(
                        "select column_name from information_schema.key_column_usage "
                        "where table_schema = 'public' and table_name = 'timelines' "
                        f"and constraint_name = '{SUPERKEY_NAME}' order by ordinal_position"
                    )
                )
            )
            kind = await session.scalar(
                text(
                    "select constraint_type from information_schema.table_constraints "
                    "where table_schema = 'public' and table_name = 'timelines' "
                    f"and constraint_name = '{SUPERKEY_NAME}'"
                )
            )
        # Column *order* is pinned, not just membership: a unique constraint on
        # the same three columns in another order would still serve the foreign
        # key, but T4's migration has to spell the reference in this order and
        # the two have to be written down somewhere that fails when they drift.
        assert columns == SUPERKEY_COLUMNS
        assert kind == "UNIQUE"

        try:
            async with database.session() as session:
                await session.execute(
                    text(
                        f"create table {FOREIGN_KEY_PROBE_TABLE} ("
                        " probe_id uuid primary key,"
                        " timeline_id uuid not null,"
                        " timeline_revision integer not null,"
                        " project_id uuid not null,"
                        " constraint fk_le05_t3_probe foreign key"
                        " (timeline_id, timeline_revision, project_id)"
                        " references timelines (timeline_id, revision, project_id))"
                    )
                )
        finally:
            async with database.session() as session:
                await session.execute(text(f"drop table if exists {FOREIGN_KEY_PROBE_TABLE}"))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_latest_revision_takes_the_highest_and_ignores_other_timelines(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Three ways this can be wrong, and all three are covered here.

    Answering `None` for a timeline that has revisions; answering with the row
    that happens to come first rather than the highest revision; and answering
    with some *other* timeline's revision, which is the "ignores its argument"
    failure T2 pinned on `find_by_digest`. The second timeline deliberately
    carries a higher revision than the first, so a lookup dropping its filter
    returns the wrong object rather than coincidentally the right one.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        other_project = EditingProjectId.new()
        await store_project(database, project_id)
        await store_project(database, other_project)
        timeline_id = TimelineId.new()
        other_timeline = TimelineId.new()

        assert await repository.latest_revision(project_id, OWNER) is None

        # Inserted out of order, so "the highest" cannot be confused with
        # "the last one written" or "the first one read".
        await repository.save(make_timeline(timeline_id, project_id, revision=2), OWNER)
        await repository.save(make_timeline(timeline_id, project_id, revision=1), OWNER)
        await repository.save(make_timeline(timeline_id, project_id, revision=3), OWNER)
        await repository.save(
            make_timeline(other_timeline, other_project, revision=9),
            OWNER,
        )

        latest = await repository.latest_revision(project_id, OWNER)
        assert latest is not None
        assert latest.revision == 3
        assert latest.timeline_id == timeline_id
        assert latest == make_timeline(timeline_id, project_id, revision=3)

        other = await repository.latest_revision(other_project, OWNER)
        assert other is not None
        assert other.revision == 9
        assert other.timeline_id == other_timeline

        assert await repository.latest_revision(EditingProjectId.new(), OWNER) is None
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_reads_and_writes_are_scoped_to_the_project_owner(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        timeline_id = TimelineId.new()
        other_owner = InstallationId.new()
        await store_project(database, project_id)
        await repository.save(make_timeline(timeline_id, project_id), OWNER)

        with pytest.raises(TimelineNotFound):
            await repository.get(timeline_id, 1, other_owner)
        assert await repository.latest_revision(project_id, other_owner) is None
        with pytest.raises(TimelineProjectMissing):
            await repository.save(
                make_timeline(timeline_id, project_id, revision=2),
                other_owner,
            )
        assert await stored_keys(database) == {(timeline_id.uuid, 1)}
    finally:
        await reset_data(database)
        await database.close()


def visual_clip(
    clip_id: str, start_ms: int, duration_ms: int, **overrides: object
) -> dict[str, object]:
    return clip_document(
        clip_id, start_ms, duration_ms, source_material_id=str(MATERIAL_ONE), **overrides
    )


def audio_clip(clip_id: str, start_ms: int, duration_ms: int) -> dict[str, object]:
    return clip_document(
        clip_id,
        start_ms,
        duration_ms,
        source_material_id=str(MATERIAL_FOUR),
        source_in_ms=0,
        source_out_ms=duration_ms,
        gain_db=-3.5,
    )


def visual_track(clips: list[object], track_id: str = "visual") -> dict[str, object]:
    return {"track_id": track_id, "kind": "visual", "clips": clips}


# Rows the column accepts and the domain refuses, grouped by the level that is
# the *only* one able to see the problem. Each is built so that removing the one
# offending property would leave a timeline the domain accepts -- otherwise the
# case would pass without the property under test ever mattering.
#
# `duration_ms` moves with each case because the aggregate requires it to equal
# where the picture lane ends. Getting that wrong would make every case below
# pass for that reason instead of its own.
REFUSED_ROWS: dict[str, dict[str, object]] = {
    # --- only the track can see these: they need a neighbour to compare with.
    "picture-lane-gap": {
        "duration_ms": 3_000,
        "tracks": [
            visual_track([visual_clip("v-one", 0, 1_000), visual_clip("v-two", 2_000, 1_000)])
        ],
    },
    "transition-eats-the-whole-previous-clip": {
        "duration_ms": 3_000,
        "tracks": [
            visual_track(
                [
                    visual_clip("v-one", 0, 1_000),
                    visual_clip(
                        "v-two", 0, 3_000, transition_in={"kind": "fade", "duration_ms": 1_000}
                    ),
                ]
            )
        ],
    },
    "second-transition-eats-the-same-stretch-twice": {
        "duration_ms": 9_000,
        "tracks": [
            visual_track(
                [
                    visual_clip("v-one", 0, 5_000),
                    visual_clip(
                        "v-two", 2_000, 4_000, transition_in={"kind": "fade", "duration_ms": 3_000}
                    ),
                    visual_clip(
                        "v-three",
                        4_000,
                        5_000,
                        transition_in={"kind": "dissolve", "duration_ms": 2_000},
                    ),
                ]
            )
        ],
    },
    "audio-clips-out-of-order": {
        "duration_ms": 8_000,
        "tracks": [
            visual_track([visual_clip("v-one", 0, 8_000)]),
            {
                "track_id": "narration",
                "kind": "narration",
                "clips": [audio_clip("n-one", 4_000, 2_000), audio_clip("n-two", 1_000, 1_000)],
            },
        ],
    },
    "repeated-clip-id-within-a-track": {
        "duration_ms": 2_000,
        "tracks": [
            visual_track([visual_clip("v-one", 0, 1_000), visual_clip("v-one", 1_000, 1_000)])
        ],
    },
    "caption-clip-carrying-a-material": {
        "duration_ms": 6_000,
        "tracks": [
            visual_track([visual_clip("v-one", 0, 6_000)]),
            {
                "track_id": "caption",
                "kind": "caption",
                "clips": [clip_document("c-one", 0, 2_000, source_material_id=str(MATERIAL_TWO))],
            },
        ],
    },
    "narration-clip-without-a-level": {
        "duration_ms": 6_000,
        "tracks": [
            visual_track([visual_clip("v-one", 0, 6_000)]),
            {
                "track_id": "narration",
                "kind": "narration",
                "clips": [
                    clip_document(
                        "n-one",
                        0,
                        2_000,
                        source_material_id=str(MATERIAL_FOUR),
                        source_in_ms=0,
                        source_out_ms=2_000,
                    )
                ],
            },
        ],
    },
    # --- only the aggregate root can see these: they span tracks, or compare a
    # track against the timeline's own declared length.
    "duration-does-not-match-the-picture-lane": {
        "duration_ms": 7_000,
        "tracks": [visual_track([visual_clip("v-one", 0, 6_000)])],
    },
    "no-picture-lane-at-all": {
        "duration_ms": 2_000,
        "tracks": [
            {
                "track_id": "caption",
                "kind": "caption",
                "clips": [clip_document("c-one", 0, 2_000, text_value=CAPTION_ONE)],
            }
        ],
    },
    "two-tracks-on-the-same-lane": {
        "duration_ms": 6_000,
        "tracks": [
            visual_track([visual_clip("v-one", 0, 6_000)]),
            visual_track([visual_clip("w-one", 0, 6_000)], track_id="second-visual"),
        ],
    },
    "two-tracks-sharing-an-identifier": {
        "duration_ms": 6_000,
        "tracks": [
            visual_track([visual_clip("v-one", 0, 6_000)]),
            {
                "track_id": "visual",
                "kind": "caption",
                "clips": [clip_document("c-one", 0, 2_000, text_value=CAPTION_ONE)],
            },
        ],
    },
    "a-clip-running-past-the-end-of-the-timeline": {
        "duration_ms": 6_000,
        "tracks": [
            visual_track([visual_clip("v-one", 0, 6_000)]),
            {
                "track_id": "caption",
                "kind": "caption",
                "clips": [clip_document("c-one", 5_000, 2_000, text_value=CAPTION_ONE)],
            },
        ],
    },
    # --- shapes SQL could not express at any level.
    "no-tracks-at-all": {"duration_ms": 6_000, "tracks": []},
    "duration-below-the-domain-minimum": {
        "duration_ms": MIN_TIMELINE_DURATION_MS - 1,
        "tracks": [visual_track([visual_clip("v-one", 0, MIN_TIMELINE_DURATION_MS - 1)])],
    },
    # No clip may end past `MAX_TIMELINE_DURATION_MS` either, so a picture lane
    # really ending at 600_001 cannot be built at all -- which is the point: the
    # aggregate's own bound is what refuses this, and it is checked before the
    # rule that the declared length match where the picture lane ends.
    "duration-above-the-domain-maximum": {
        "duration_ms": MAX_TIMELINE_DURATION_MS + 1,
        "tracks": [visual_track([visual_clip("v-one", 0, MAX_TIMELINE_DURATION_MS)])],
    },
    "revision-below-the-first": {"revision": 0},
    "negative-revision": {"revision": -1},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", list(REFUSED_ROWS.values()), ids=list(REFUSED_ROWS))
async def test_rows_the_domain_would_refuse_are_refused_at_hydration(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    overrides: dict[str, object],
) -> None:
    """The stored row is input, not truth.

    Rows arrive from migrations, fixtures and hand-run statements as well as
    from `save`, and `tracks` in particular is a document the database never
    inspects at any depth. Hydrating through the constructor is what makes every
    one of them meet the rules a caller meets -- including the rules no single
    clip and no single track can check on its own.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        await store_project(database, project_id)
        timeline_id = TimelineId.new()
        revision = cast(int, overrides.get("revision", 1))
        await insert_row(database, timeline_id.uuid, project_id.uuid, **overrides)

        with pytest.raises(InvalidTimelineModel):
            await repository.get(timeline_id, revision, OWNER)
        with pytest.raises(InvalidTimelineModel):
            await repository.latest_revision(project_id, OWNER)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duration_ms",
    [MIN_TIMELINE_DURATION_MS, MAX_TIMELINE_DURATION_MS],
    ids=["shortest", "longest"],
)
async def test_a_timeline_at_either_end_of_the_allowed_length_round_trips(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    duration_ms: int,
) -> None:
    """Both endpoints, so the two rejections one step outside are not vacuous.

    The domain's bounds and the `integer` column have to agree about these: a
    limit widened in one place and not the other turns a clean validation error
    into a driver failure at insert time.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        await store_project(database, project_id)
        timeline_id = TimelineId.new()
        timeline = Timeline(
            timeline_id=timeline_id,
            project_id=project_id,
            revision=1,
            duration_ms=duration_ms,
            tracks=(
                TimelineTrack(
                    track_id="visual",
                    kind=TimelineTrackKind.VISUAL,
                    clips=(
                        TimelineClip(
                            clip_id="v-one",
                            start_ms=0,
                            duration_ms=duration_ms,
                            source_material_id=MATERIAL_ONE,
                            source_in_ms=None,
                            source_out_ms=None,
                            text=None,
                            gain_db=None,
                            transition_in=None,
                        ),
                    ),
                ),
            ),
            created_at=CREATED_AT,
        )

        await repository.save(timeline, OWNER)
        assert await repository.get(timeline_id, 1, OWNER) == timeline
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_revision_past_the_column_is_refused_rather_than_stored(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The domain sets no upper bound on a revision; `integer` sets one at 2^31.

    Measured: asyncpg refuses the argument before the statement is sent, with
    `DataError: value out of int32 range`, which SQLAlchemy wraps as a plain
    `DBAPIError` -- a `SQLAlchemyError` but not an `IntegrityError`. So it comes
    back as "unavailable", which tells the caller to retry something that can
    never succeed.

    This test pins the behaviour rather than endorsing it. Two billion revisions
    of one cut is not reachable by incrementing, so nothing is being fixed here;
    the mismatch between an unbounded domain field and a bounded column is
    registered as a leftover in `LE-05.md`. **If this assertion ever goes red,
    the bound was closed and that note should be deleted, not the test.**
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        await store_project(database, project_id)
        timeline_id = TimelineId.new()

        # The largest revision the column holds is stored without complaint.
        await repository.save(
            make_timeline(timeline_id, project_id, revision=2**31 - 1),
            OWNER,
        )
        assert (await repository.get(timeline_id, 2**31 - 1, OWNER)).revision == 2**31 - 1

        with pytest.raises(TimelinePersistenceUnavailable) as refused:
            await repository.save(
                make_timeline(timeline_id, project_id, revision=2**31),
                OWNER,
            )
        assert refused.value.__cause__ is None
        assert "int32" not in "".join(traceback.format_exception(refused.value))
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_stored_identifier_of_the_wrong_uuid_version_is_refused(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A `uuid` column accepts every version; `TimelineId` accepts only v4."""
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTimelineRepository(database)
    try:
        await reset_data(database)
        project_id = EditingProjectId.new()
        await store_project(database, project_id)
        nil_uuid = UUID(int=0)
        await insert_row(database, nil_uuid, project_id.uuid)
        assert await stored_keys(database) == {(nil_uuid, 1)}

        with pytest.raises(InvalidTimelineModel):
            await repository.get(forged_identifier(nil_uuid), 1, OWNER)
        with pytest.raises(InvalidTimelineModel):
            await repository.latest_revision(project_id, OWNER)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_wrong_credentials_are_refused_without_leaking_the_identity(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A real server refusing a real login, against all three public methods.

    A refused *connection* is an `OSError`. A refused *login* is an
    `asyncpg.exceptions.InvalidPasswordError`, and its message names the role.
    Measured MRO on asyncpg 0.31.0:

        InvalidPasswordError -> InvalidAuthorizationSpecificationError
        -> PostgresError -> PostgresMessage -> Exception -> BaseException

    `SQLAlchemyError` appears nowhere on it, and neither does `OSError` -- a
    third-party fact recorded rather than a distinction this module rests on,
    since the `except Exception` tail would answer identically either way.

    What is load-bearing here is that **all three methods** are exercised: the
    tail is written once per `try`, and one missing it leaks from that method
    alone while the other two stay clean.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    url = make_url(postgresql_url)
    role = url.username
    assert role is not None
    database = Database.from_url(
        url.set(password="le05_wrong_password").render_as_string(hide_password=False)
    )
    try:
        repository = SqlAlchemyTimelineRepository(database)
        timeline = make_timeline(TimelineId.new(), EditingProjectId.new())
        with pytest.raises(TimelinePersistenceUnavailable) as saved:
            await repository.save(timeline, OWNER)
        with pytest.raises(TimelinePersistenceUnavailable) as loaded:
            await repository.get(timeline.timeline_id, 1, OWNER)
        with pytest.raises(TimelinePersistenceUnavailable) as latest:
            await repository.latest_revision(timeline.project_id, OWNER)
        for captured in (saved, loaded, latest):
            rendered = "".join(traceback.format_exception(captured.value))
            assert role not in rendered
            assert "password authentication failed" not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_repository_refuses_arguments_before_it_reaches_the_database(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A bare UUID carries exactly the value the column would compare against."""
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        repository = SqlAlchemyTimelineRepository(database)
        with pytest.raises(TimelineDataRejected):
            await repository.get(cast(TimelineId, TimelineId.new().uuid), 1, OWNER)
        with pytest.raises(TimelineDataRejected):
            await repository.get(TimelineId.new(), cast(int, "1"), OWNER)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_migration_creates_the_declared_shape_and_drops_it(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = {
                cast(str, row["column_name"]): (
                    cast(str, row["data_type"]),
                    cast(str, row["is_nullable"]),
                    row["character_maximum_length"],
                )
                for row in (
                    await session.execute(
                        text(
                            "select column_name, data_type, is_nullable, "
                            "character_maximum_length from information_schema.columns "
                            "where table_schema = 'public' and table_name = 'timelines'"
                        )
                    )
                )
                .mappings()
                .all()
            }
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.timelines'::regclass"
                    )
                )
            )
            identity_columns = {
                cast(str, row["column_name"]): (
                    cast(str, row["data_type"]),
                    cast(str, row["is_nullable"]),
                    row["character_maximum_length"],
                )
                for row in (
                    await session.execute(
                        text(
                            "select column_name, data_type, is_nullable, "
                            "character_maximum_length from information_schema.columns "
                            "where table_schema = 'public' "
                            "and table_name = 'editing_project_timelines'"
                        )
                    )
                )
                .mappings()
                .all()
            }
            identity_constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.editing_project_timelines'::regclass"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert identity_columns == EXPECTED_IDENTITY_COLUMNS
        assert identity_constraints >= EXPECTED_IDENTITY_CONSTRAINTS
        # `>=` rather than `==`, which is one-directional: it catches a
        # constraint this file expects and the database lacks, and **not** a
        # constraint the database has and `schema.py` has never heard of -- the
        # second being exactly the autogenerate drift these assertions exist to
        # catch. T4's equivalent is compared for equality over
        # `contype in ('p','f','u','c')`; tightening this one belongs with the
        # task that owns `timelines`, and is registered as a leftover in
        # `LE-05.md` rather than changed from another task's fix round.
        assert constraints >= EXPECTED_CONSTRAINTS
        # `schema.py` and the migration each declare all of this separately, and
        # this assertion is the only thing comparing the two: every other test
        # in this file reads either the migrated database or this file's own
        # constants, so narrowing the `Table` alone would leave them all green.
        declared = {
            column.name: (column.type.__class__.__name__, column.nullable)
            for column in timelines.columns
        }
        assert declared == {
            name: (EXPECTED_TABLE_TYPES[name], shape[1] == "YES")
            for name, shape in EXPECTED_COLUMNS.items()
        }
        # The constraints, not just the columns -- and this is not decoration.
        # `migrations/env.py` sets `target_metadata` to exactly this metadata,
        # so a constraint present in the database but missing from `schema.py`
        # is drift that the *next* `alembic revision --autogenerate` proposes
        # dropping. Measured: with only the column comparison above, deleting
        # the superkey, the foreign key, or half the primary key from
        # `schema.py` left the whole suite green -- and T4 is the next task to
        # run autogenerate, against the very constraint it depends on.
        # `Table.constraints` is typed as holding the `Constraint` base, which
        # declares no `.columns`. Every concrete constraint class in SQLAlchemy
        # 2.0.51 does have one, so this narrowing never actually fires today; it
        # is asserted rather than written as an `isinstance` filter so that a
        # constraint kind without one would surface here instead of being
        # quietly skipped. What does the work below is the dictionary
        # comparison.
        declared_constraints: dict[str, tuple[str, list[str]]] = {}
        for constraint in timelines.constraints:
            assert isinstance(constraint, ColumnCollectionConstraint), constraint
            assert constraint.name is not None
            declared_constraints[str(constraint.name)] = (
                type(constraint).__name__,
                list(constraint.columns.keys()),
            )
        assert declared_constraints == EXPECTED_TABLE_CONSTRAINTS
        # The foreign key's target as well, since its columns alone say nothing
        # about what it points at.
        assert [
            element.target_fullname
            for constraint in timelines.constraints
            if isinstance(constraint, ForeignKeyConstraint)
            for element in constraint.elements
        ] == [
            "editing_project_timelines.project_id",
            "editing_project_timelines.timeline_id",
        ]

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            removed = await session.scalar(text("select to_regclass('public.timelines')"))
            removed_identity = await session.scalar(
                text("select to_regclass('public.editing_project_timelines')")
            )
        assert removed is None
        assert removed_identity is None
    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
        await database.close()
