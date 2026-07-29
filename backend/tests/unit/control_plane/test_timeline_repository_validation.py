"""Fail-closed branches around the PostgreSQL timeline repository.

These cover what a real database cannot be made to produce on demand: a row that
is missing, a row malformed in a way the columns permit, and a session that
fails in each of the ways the driver can fail. Behaviour against a live
PostgreSQL is in the integration suite.

`tracks` is one JSONB document holding the whole cut -- tracks, their clips, and
a clip's incoming transition -- and PostgreSQL does not look inside it at any
depth. So most of this file is about documents the column accepts and the domain
does not, at each of those three levels.

Every malformed case is wrapped in a document that would be **valid** if the
malformed part were accepted. That is deliberate and it is what makes these
assertions worth anything: a rejection test whose wrapper is itself illegal
passes no matter what the code under test does. Concretely, the row's
`duration_ms` always matches where the track really ends, and a transition is
always on the *second* visual clip, because a transition on the first has
nothing to overlap and the track refuses it whatever its shape.
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest

# `asyncpg` ships no stubs. The import is here rather than behind a local
# equivalent because the point of the test below is what the real driver's
# exception inherits from -- a hand-rolled stand-in would assert nothing.
import asyncpg  # type: ignore[import-untyped]  # isort: skip
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.timelines import (
    TimelineDataRejected,
    TimelineNotFound,
    TimelinePersistenceUnavailable,
    TimelineProjectMissing,
    TimelineRevisionAlreadyStored,
)
from automation_tool.control_plane.domain import (
    EditingProjectId,
    InvalidTimelineModel,
    MaterialId,
    TaskId,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)
from automation_tool.control_plane.infrastructure.database import Database, hydration
from automation_tool.control_plane.infrastructure.database import (
    timeline_repository as repository_module,
)

# Distinctive enough that a leak into a message or traceback cannot be mistaken
# for anything else, and cannot pass by coincidence.
UNREACHABLE_URL = (
    "postgresql+asyncpg://le05_leaked_user:le05_leaked_password@127.0.0.1:1/le05_leaked_db"
)
LEAKED_TOKENS = (
    "le05_leaked_user",
    "le05_leaked_password",
    "le05_leaked_db",
    # What the underlying socket error says. `raise ... from None` suppresses the
    # context rather than clearing it, so this -- not `__context__ is None` -- is
    # what catches the suppression being dropped.
    "Connect call failed",
    "ConnectionRefusedError",
)

CREATED_AT = datetime(2026, 7, 29, 3, 21, 45, 123_456, tzinfo=UTC)
SHANGHAI = timezone(timedelta(hours=8))

# One material per clip that has a source, so a conversion handing back the same
# identifier for every clip could not pass unnoticed.
MATERIAL_ONE = MaterialId.new()
MATERIAL_TWO = MaterialId.new()
MATERIAL_THREE = MaterialId.new()
MATERIAL_FOUR = MaterialId.new()
MATERIAL_FIVE = MaterialId.new()

DURATION_MS = 13_200
CAPTION_ONE = "今天我们去露营"
CAPTION_TWO = "第二段字幕"


class FailingSessionScope:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def __aenter__(self) -> object:
        raise self.failure

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class FailingSessions:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def begin(self) -> FailingSessionScope:
        return FailingSessionScope(self.failure)


class StubResult:
    """Just enough of a `Result` for `.mappings().one_or_none()`."""

    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    def mappings(self) -> StubResult:
        return self

    def one_or_none(self) -> RowMapping | None:
        return self._row


class StubSession:
    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    async def execute(self, _statement: object) -> StubResult:
        return StubResult(self._row)


class StubSessionScope:
    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    async def __aenter__(self) -> StubSession:
        return StubSession(self._row)

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class StubSessions:
    def __init__(self, row: RowMapping | None) -> None:
        self._row = row

    def begin(self) -> StubSessionScope:
        return StubSessionScope(self._row)


def unreachable_database() -> Database:
    return Database.from_url(UNREACHABLE_URL, connect_timeout_seconds=0.05)


def make_timeline(
    timeline_id: TimelineId | None = None,
    project_id: EditingProjectId | None = None,
    *,
    revision: int = 1,
) -> Timeline:
    """The cut every assertion here starts from, built through the domain.

    Deliberately not minimal. It carries one of every shape the JSON document
    has to survive: a clip with a source window, a clip without one (a still
    image, which has no stretch of a source to take and therefore no level), an
    incoming transition, two levels including a whole number, and two caption
    clips whose text is the thing they carry instead of a material.
    """
    return Timeline(
        timeline_id=timeline_id or TimelineId.new(),
        project_id=project_id or EditingProjectId.new(),
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
                        # A whole number, on purpose. JSON keeps `-6.0` and `-6`
                        # apart, PostgreSQL hands the first back as a `float`
                        # and the second as an `int`, and the domain requires a
                        # `float`. A serialiser normalising this to `-6` would
                        # store a row nothing could load again.
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
    text: object = None,
    gain_db: object = None,
    transition_in: object = None,
) -> dict[str, object]:
    """A clip as it really sits in the column, written out by hand.

    Every key is spelled here rather than derived from the repository's own
    serialiser: a test that asked the code under test what shape it writes would
    agree with any shape it wrote.
    """
    return {
        "clip_id": clip_id,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "source_material_id": source_material_id,
        "source_in_ms": source_in_ms,
        "source_out_ms": source_out_ms,
        "text": text,
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
                clip_document("c-one", 500, 2_500, text=CAPTION_ONE),
                clip_document("c-two", 3_200, 2_000, text=CAPTION_TWO),
            ],
        },
    ]


def hydration_row(**overrides: object) -> RowMapping:
    """A row shaped the way asyncpg really hands one back.

    `tracks` is a `list` of `dict`s rather than the tuples the domain declares:
    measured against PostgreSQL 18.4, SQLAlchemy's asyncpg dialect parses JSONB
    before the repository sees it, so a JSON array arrives as `list` and a JSON
    object as `dict`, all the way down. Writing tuples here would test a
    conversion that never has to happen.
    """
    values: dict[str, object] = {
        "timeline_id": TimelineId.new().uuid,
        "revision": 1,
        "project_id": EditingProjectId.new().uuid,
        "duration_ms": DURATION_MS,
        "tracks": track_documents(),
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return cast(RowMapping, values)


# One clip that is legal on its own and legal as a whole one-clip visual track.
# Every track-level and clip-level case below is built so that accepting the
# malformed part would leave a timeline the domain accepts -- which is what
# makes the rejection attributable to the malformed part.
def valid_clip() -> dict[str, object]:
    return clip_document("v-one", 0, 6_000, source_material_id=str(MATERIAL_ONE))


VALID_CLIP_DURATION_MS = 6_000


def track_row(track: object, duration_ms: int = VALID_CLIP_DURATION_MS) -> RowMapping:
    """A row holding exactly one track document, whatever shape it is in."""
    return hydration_row(duration_ms=duration_ms, tracks=[track])


def one_track_row(clips: list[object], duration_ms: int = VALID_CLIP_DURATION_MS) -> RowMapping:
    """A row whose single picture lane holds exactly these clips."""
    return track_row({"track_id": "visual", "kind": "visual", "clips": clips}, duration_ms)


def transition_row(transition: object) -> RowMapping:
    """Two visual clips, the transition on the second one.

    A transition on the *first* clip of a picture lane is refused by the track
    no matter how well formed it is -- there is no outgoing clip for it to
    overlap. Putting these cases there would make every one of them pass for a
    reason having nothing to do with the document's shape.
    """
    return hydration_row(
        duration_ms=10_200,
        tracks=[
            {
                "track_id": "visual",
                "kind": "visual",
                "clips": [
                    clip_document("v-one", 0, 6_000, source_material_id=str(MATERIAL_ONE)),
                    clip_document(
                        "v-two",
                        5_200,
                        5_000,
                        source_material_id=str(MATERIAL_TWO),
                        transition_in=transition,
                    ),
                ],
            }
        ],
    )


def test_repository_refuses_a_database_it_does_not_own() -> None:
    with pytest.raises(TimelinePersistenceUnavailable):
        repository_module.SqlAlchemyTimelineRepository(cast(Database, object()))


@pytest.mark.asyncio
async def test_repository_refuses_foreign_argument_types() -> None:
    """Identifiers and revisions are checked before a statement is built.

    A bare UUID and a sibling identifier both carry exactly the value the column
    would accept, so leaving the check to the database would silently compare
    them. `revision` is checked with `type(...) is not int` rather than
    `isinstance`, because `True` is an `int` and would otherwise be looked up as
    revision 1 -- the domain spells its own integer checks the same way.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyTimelineRepository(database)
        timeline = make_timeline()
        with pytest.raises(TimelineDataRejected):
            await repository.save(cast(Timeline, object()))
        with pytest.raises(TimelineDataRejected):
            await repository.get(cast(TimelineId, timeline.timeline_id.uuid), 1)
        with pytest.raises(TimelineDataRejected):
            await repository.get(cast(TimelineId, TaskId.new()), 1)
        with pytest.raises(TimelineDataRejected):
            await repository.get(timeline.timeline_id, cast(int, "1"))
        with pytest.raises(TimelineDataRejected):
            await repository.get(timeline.timeline_id, cast(int, True))
        with pytest.raises(TimelineDataRejected):
            await repository.latest_revision(cast(TimelineId, timeline.timeline_id.uuid))
        with pytest.raises(TimelineDataRejected):
            await repository.latest_revision(cast(TimelineId, TaskId.new()))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_unreachable_database_is_refused_without_leaking_the_connection() -> None:
    """A refused connection is refused without the connection string in it.

    Measured against a real PostgreSQL: `asyncpg` raises `ConnectionRefusedError`
    out of asyncio's connect call, and the SQLAlchemy dialect does not wrap it,
    because it is not one of asyncpg's own exceptions -- so it is an `OSError`
    and not a `SQLAlchemyError`.

    That classification is recorded here as a third-party fact, **not** as this
    module's load-bearing guard. Every `try` in the repository ends in an
    `except Exception` tail, so an `OSError` would be answered identically with
    the `_CONNECTION_FAILURES` clause deleted; the clause is kept as the shape
    shared across seven repositories, several of which have no tail and do
    depend on it. What this test pins is the outcome: `Unavailable`, and no host,
    port, user or password anywhere in the rendered traceback.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyTimelineRepository(database)
        timeline = make_timeline()
        with pytest.raises(TimelinePersistenceUnavailable) as saved:
            await repository.save(timeline)
        with pytest.raises(TimelinePersistenceUnavailable) as loaded:
            await repository.get(timeline.timeline_id, 1)
        with pytest.raises(TimelinePersistenceUnavailable) as latest:
            await repository.latest_revision(timeline.timeline_id)
        for captured in (saved, loaded, latest):
            rendered = "".join(traceback.format_exception(captured.value))
            for token in LEAKED_TOKENS:
                assert token not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_database_error_is_refused_without_leaking_its_message() -> None:
    """The sentinel is `le05_...` rather than a plain word, and that matters.

    The obvious spelling of this test hides the message inside
    `SQLAlchemyError("private database failure")` and asserts that `"private"`
    does not appear. But `traceback.format_exception` renders a `File "..."`
    line for every frame, so that assertion also matches any *path* containing
    the word -- and on macOS a temporary directory is under `/private/tmp`.
    Measured: running this file from a clone in a scratchpad turned it red with
    nothing leaking at all, which is a false alarm arriving precisely when the
    signal is being relied on. A sentinel that cannot occur by coincidence is
    the rule stated at the top of this file; this is that rule applied.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyTimelineRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(SQLAlchemyError("le05_leaked_database_failure")),
        )
        timeline = make_timeline()
        with pytest.raises(TimelinePersistenceUnavailable) as saved:
            await repository.save(timeline)
        with pytest.raises(TimelinePersistenceUnavailable) as loaded:
            await repository.get(timeline.timeline_id, 1)
        with pytest.raises(TimelinePersistenceUnavailable) as latest:
            await repository.latest_revision(timeline.timeline_id)
        for captured in (saved, loaded, latest):
            rendered = "".join(traceback.format_exception(captured.value))
            assert "le05_leaked_database_failure" not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_authentication_failure_is_refused_without_leaking_the_role() -> None:
    """The catch-all tail, on the exception class a live server really raises.

    Measured on asyncpg 0.31.0, `InvalidPasswordError`'s MRO is

        InvalidPasswordError -> InvalidAuthorizationSpecificationError
        -> PostgresError -> PostgresMessage -> Exception -> BaseException

    -- note the intermediate class, and note that `TooManyConnectionsError`
    arrives via `InsufficientResourcesError` instead, so the four classes in
    this family do not share a single direct base. What they do share is the
    `PostgresError` spine and the absence of `OSError` and `SQLAlchemyError`.

    Both assertions below record third-party facts rather than this module's
    behaviour: with the catch-all tail present, an exception's position relative
    to `OSError` and `SQLAlchemyError` changes nothing about the answer it gets
    here. They are worth keeping because an asyncpg release restructuring this
    hierarchy should fail here and send someone back to re-read the reasoning --
    and because the repositories that have no tail *do* rest on it. What this
    test pins for this module is the outcome: `Unavailable`, with the role
    absent from the rendered traceback even though the driver's message names it.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyTimelineRepository(database)
        failure = asyncpg.exceptions.InvalidPasswordError(
            'password authentication failed for user "le05_leaked_user"'
        )
        assert not isinstance(failure, OSError | SQLAlchemyError)
        assert asyncpg.exceptions.PostgresError in type(failure).__mro__
        object.__setattr__(database, "_sessions", FailingSessions(failure))
        timeline = make_timeline()
        with pytest.raises(TimelinePersistenceUnavailable) as saved:
            await repository.save(timeline)
        with pytest.raises(TimelinePersistenceUnavailable) as loaded:
            await repository.get(timeline.timeline_id, 1)
        with pytest.raises(TimelinePersistenceUnavailable) as latest:
            await repository.latest_revision(timeline.timeline_id)
        for captured in (saved, loaded, latest):
            rendered = "".join(traceback.format_exception(captured.value))
            assert "le05_leaked_user" not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


def integrity_error(sqlstate: object, detail: str) -> IntegrityError:
    """An `IntegrityError` carrying the `sqlstate` a real driver would set.

    Measured shape: SQLAlchemy keeps the driver's exception on `.orig`, and the
    asyncpg adapter puts PostgreSQL's five-character SQLSTATE on it. `23505` and
    `23503` are the two this table can produce today.
    """

    class DriverError(Exception):
        pass

    original = DriverError(detail)
    if sqlstate is not None:
        original.sqlstate = sqlstate  # type: ignore[attr-defined]
    return IntegrityError("insert into timelines", None, original)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("23505", TimelineRevisionAlreadyStored),
        ("23503", TimelineProjectMissing),
        # Anything else a constraint could raise -- NOT NULL is `23502`, CHECK is
        # `23514`. Neither can come from this table today, and neither gets
        # better on a retry, so the answer is the one that says "fix the data"
        # rather than the one that says "try again later".
        ("23502", TimelineDataRejected),
        ("23514", TimelineDataRejected),
        (None, TimelineDataRejected),
    ],
    ids=["unique", "foreign-key", "not-null", "check", "no-sqlstate"],
)
async def test_each_integrity_violation_gets_its_own_answer(
    sqlstate: object, expected: type[Exception]
) -> None:
    """One `IntegrityError` class, three things a caller has to do about it.

    A duplicate revision means this cut is already stored and the caller must
    pick a new revision. A foreign key violation means the project the timeline
    names is not there -- a different resource and a different answer, which
    LE-06 has to turn into 409 and 404 respectively. Retrying either one
    unchanged never succeeds, which is what separates both from "unavailable".

    The SQLSTATE is the only thing that tells them apart: both arrive as
    `IntegrityError` with the constraint name buried in a driver message that
    must not be re-raised, because PostgreSQL's DETAIL line quotes the offending
    key values.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyTimelineRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(integrity_error(sqlstate, "Key (le05-private-detail) ...")),
        )
        with pytest.raises(expected) as captured:
            await repository.save(make_timeline())
        rendered = "".join(traceback.format_exception(captured.value))
        assert "le05-private-detail" not in rendered
        assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_integrity_error_without_a_driver_exception_is_still_refused() -> None:
    """`IntegrityError.orig` can be `None`, measured.

    `getattr(None, "sqlstate", None)` is `None`, so the same fall-through
    applies -- but only if the lookup tolerates the missing object at all.
    Reaching through `error.orig.sqlstate` would raise `AttributeError` from
    inside the repository, which is neither the domain's error nor one of this
    module's.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyTimelineRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(IntegrityError("insert into timelines", None, None)),  # type: ignore[arg-type]
        )
        with pytest.raises(TimelineDataRejected):
            await repository.save(make_timeline())
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_missing_revision_is_not_found_and_a_present_one_hydrates() -> None:
    """Both post-query branches of `get` and `latest_revision`, without a server.

    The two differ in what "no row" means -- an error and an answer
    respectively -- so the pair is asserted rather than one standing in for the
    other. A `latest_revision` that raised would make "this timeline has no
    revisions yet" indistinguishable from a failure.

    `save` returning normally is here too, because the unit layer's coverage is
    measured on its own: without it the one path through `save` that a caller
    actually takes would be covered only by the integration suite.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyTimelineRepository(database)

        object.__setattr__(database, "_sessions", StubSessions(None))
        with pytest.raises(TimelineNotFound):
            await repository.get(TimelineId.new(), 1)
        assert await repository.latest_revision(TimelineId.new()) is None
        await repository.save(make_timeline())

        timeline = make_timeline()
        object.__setattr__(
            database,
            "_sessions",
            StubSessions(
                hydration_row(
                    timeline_id=timeline.timeline_id.uuid,
                    project_id=timeline.project_id.uuid,
                )
            ),
        )
        assert await repository.get(timeline.timeline_id, 1) == timeline
        assert await repository.latest_revision(timeline.timeline_id) == timeline
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_serialisation_failure_is_not_reported_as_an_unavailable_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the row is not database work and must not borrow its failures.

    `_row` keeps `_hydrate` outside its `try` for exactly this reason, and says
    so: a catch-all that swallows the row-building step turns "this code is
    broken" into "try again later", which is wrong and unfixable by retrying.
    `save` had the same shape in reverse -- `_column_values` sat *inside* the
    try, so a serialiser that raised would have been reported as an unavailable
    database and retried forever.

    Nothing can trigger it today: every value the serialiser produces is a JSON
    native, and the timeline it reads from is already validated. That is an
    argument for the guard being cheap, not for leaving the failure mode wired
    the dangerous way -- and a field added to the domain is exactly how "nothing
    can trigger it today" stops being true.

    Letting it propagate is the point. A broken serialiser is not one of the
    five outcomes a caller can act on; masking it as one of them is how it would
    stay hidden.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyTimelineRepository(database)
        object.__setattr__(database, "_sessions", StubSessions(None))

        class SerialiserFailure(Exception):
            pass

        def explode(_timeline: Timeline) -> dict[str, object]:
            raise SerialiserFailure("le05_leaked_serialiser_detail")

        monkeypatch.setattr(repository_module, "_column_values", explode)
        with pytest.raises(SerialiserFailure):
            await repository.save(make_timeline())
    finally:
        await database.close()


def test_hydration_rebuilds_the_whole_tree_as_the_domain_declares_it() -> None:
    """Tuples all the way down, and every leaf parsed back into its own type.

    Equality between two hydrated objects would agree with itself whatever the
    container type, and `(TimelineTrack(...),) == [{...}]` is False, so anything
    comparing a hydrated timeline against a constructed one already depends on
    this. The types are asserted directly so that a conversion which stopped
    happening fails here rather than somewhere far away.
    """
    timeline = make_timeline()
    hydrated = repository_module._hydrate(
        hydration_row(
            timeline_id=timeline.timeline_id.uuid,
            project_id=timeline.project_id.uuid,
        )
    )
    assert hydrated == timeline
    assert type(hydrated.tracks) is tuple
    visual = hydrated.tracks[0]
    assert type(visual) is TimelineTrack
    assert visual.kind is TimelineTrackKind.VISUAL
    assert type(visual.clips) is tuple
    assert type(visual.clips[0]) is TimelineClip
    assert visual.clips[0].source_material_id == MATERIAL_ONE
    transition = visual.clips[1].transition_in
    assert type(transition) is TimelineTransition
    assert transition.kind is TransitionKind.FADE
    assert transition.duration_ms == 800
    # The still image: no window, therefore no level, and no stretch to take.
    assert visual.clips[2].source_in_ms is None
    assert visual.clips[2].gain_db is None
    narration = hydrated.tracks[1]
    assert narration.kind is TimelineTrackKind.NARRATION
    assert type(narration.clips[1].gain_db) is float
    assert narration.clips[1].gain_db == -6.0
    caption = hydrated.tracks[2]
    assert caption.kind is TimelineTrackKind.CAPTION
    assert caption.clips[0].text == CAPTION_ONE
    assert caption.clips[0].source_material_id is None


def test_the_wrappers_the_rejection_cases_use_are_themselves_accepted() -> None:
    """The control for every parametrised rejection below.

    Each of those wraps one malformed part in a document that is otherwise
    legal, and asserts a refusal. If the wrapper were itself illegal -- an
    off-by-one duration, a transition with nothing to overlap -- every case
    would pass without the malformed part ever mattering. So both wrappers are
    hydrated here intact, and both have to succeed.
    """
    whole = repository_module._hydrate(one_track_row([valid_clip()]))
    assert len(whole.tracks) == 1
    assert whole.tracks[0].clips[0].clip_id == "v-one"

    overlapped = repository_module._hydrate(transition_row({"kind": "fade", "duration_ms": 800}))
    incoming = overlapped.tracks[0].clips[1].transition_in
    assert type(incoming) is TimelineTransition
    assert incoming.duration_ms == 800

    # The caption wrapper the malformed-identifier cases use. Without this, a
    # change to the caption rules could make that wrapper illegal on its own and
    # all four of those cases would keep passing while testing nothing.
    captioned = repository_module._hydrate(caption_with_material_row(None))
    assert captioned.tracks[1].clips[0].text == CAPTION_ONE
    assert captioned.tracks[1].clips[0].source_material_id is None

    # The narration wrapper, likewise, for the level cases below.
    levelled = repository_module._hydrate(narration_row(-6.0))
    assert type(levelled.tracks[1].clips[0].gain_db) is float


@pytest.mark.parametrize(
    "stored",
    ["lower-third", "Visual", "", None, 7, True],
    ids=["unknown", "wrong-case", "empty", "null", "number", "boolean"],
)
def test_an_unrecognised_enumeration_value_is_refused_by_the_parser_itself(
    stored: object,
) -> None:
    """Pinned at the function, because no row can pin it.

    The obvious test -- store `"lower-third"` as a track's kind and watch the
    row be refused -- passes whatever this function does. Hand the raw string
    back instead of refusing and `TimelineTrack.__post_init__` rejects it on
    `not isinstance(self.kind, TimelineTrackKind)`; the row is still refused,
    the reason is no longer this function. Measured: a mutation that changes
    only the fall-through leaves both layers green.

    That is the same shape as the material-identifier gap below, with one
    difference: there, a caption clip exists where losing the value hydrates
    successfully, so a row could carry the assertion. Here every consumer
    isinstance-checks the member, so no stored row can tell the two apart and
    the only honest place to assert it is the parser.
    """
    with pytest.raises(InvalidTimelineModel):
        hydration.enumeration_member(TimelineTrackKind, stored, InvalidTimelineModel)
    with pytest.raises(InvalidTimelineModel):
        hydration.enumeration_member(TransitionKind, stored, InvalidTimelineModel)


def test_the_enumeration_parser_returns_the_member_not_its_text() -> None:
    """The other side, so the refusals above are not a parser that only fails.

    Identity rather than equality: a `StrEnum` member compares equal to its own
    text, so `== "visual"` would hold for the bare string this exists to reject.
    """
    parsed = hydration.enumeration_member(TimelineTrackKind, "visual", InvalidTimelineModel)
    assert parsed is TimelineTrackKind.VISUAL
    assert (
        hydration.enumeration_member(TransitionKind, "fade", InvalidTimelineModel)
        is TransitionKind.FADE
    )


def test_the_serialiser_and_the_reader_agree_on_every_key() -> None:
    """One key set, or a field added to the domain is silently dropped.

    The reader refuses a document whose keys are not exactly the domain's
    fields; the writer produces those keys. Both sides read them off
    `dataclasses.fields` rather than a hand-kept list, so a new field on
    `TimelineClip` cannot leave the writer storing something the reader then
    refuses -- or worse, leave the reader quietly dropping it.
    """
    timeline = make_timeline()
    documents = repository_module._column_values(timeline)["tracks"]
    assert documents == track_documents()
    for track in cast(list[dict[str, object]], documents):
        assert set(track) == repository_module._TRACK_KEYS
        for clip in cast(list[dict[str, object]], track["clips"]):
            assert set(clip) == repository_module._CLIP_KEYS
            transition = clip["transition_in"]
            if transition is not None:
                assert set(cast(dict[str, object], transition)) == (
                    repository_module._TRANSITION_KEYS
                )


@pytest.mark.parametrize(
    ("column", "stored"),
    [
        ("timeline_id", UUID(int=0)),
        ("timeline_id", "not-a-uuid"),
        ("project_id", UUID(int=0)),
        ("project_id", None),
    ],
    ids=["timeline-nil", "timeline-text", "project-nil", "project-null"],
)
def test_hydration_refuses_an_identifier_the_column_would_accept(
    column: str, stored: object
) -> None:
    """A `uuid` column takes every version; these identifiers take only v4.

    The integration suite proves such a row really does land in the table; this
    proves the parse failure is translated instead of surfacing as
    `InvalidResourceId`, which nothing about this repository declares.
    """
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(hydration_row(**{column: stored}))


@pytest.mark.parametrize(
    "stored",
    [
        {"not": "an array"},
        "[]",
        7,
        None,
        [],
        ["visual"],
        [{"track_id": "visual", "kind": "visual", "clips": []}, "second"],
    ],
    ids=[
        "object",
        "text",
        "number",
        "null",
        "empty",
        "text-elements",
        "one-element-not-an-object",
    ],
)
def test_hydration_refuses_a_tracks_document_that_is_not_a_list_of_objects(
    stored: object,
) -> None:
    """The conversion turns only what is already the right shape.

    Converting first would raise a bare `TypeError` from inside this module on a
    number, and iterating a `dict` would quietly hand back a tuple of its keys --
    neither of which is the domain's error or one of this module's, and the
    second of which would be a plausible-looking value built out of nonsense.
    """
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(hydration_row(tracks=stored))


@pytest.mark.parametrize(
    "track",
    [
        {"track_id": "visual", "kind": "visual"},
        {"track_id": "visual", "kind": "visual", "clips": [valid_clip()], "speed": 2},
        {"track_id": "visual", "kind": "visual", "clip": [valid_clip()]},
        {"track_id": "visual", "kind": "visual", "clips": {"first": valid_clip()}},
        {"track_id": "visual", "kind": "visual", "clips": "one clip"},
        {"track_id": "visual", "kind": "visual", "clips": ["v-one"]},
        {"track_id": "visual", "kind": "lower-third", "clips": [valid_clip()]},
        {"track_id": "visual", "kind": None, "clips": [valid_clip()]},
        {"track_id": None, "kind": "visual", "clips": [valid_clip()]},
    ],
    ids=[
        "clips-missing",
        "extra-key",
        "clips-misspelled",
        "clips-an-object",
        "clips-text",
        "clips-of-text",
        "kind-unknown",
        "kind-null",
        "track-id-null",
    ],
)
def test_hydration_refuses_a_track_document_of_the_wrong_shape(track: object) -> None:
    """An unknown key is refused rather than dropped on the floor.

    That is what the `extra-key` case is for, and it is the one case here that
    only the key-set check catches: every other shape fails at the constructor
    anyway. A document carrying `speed` is a document written by something this
    code does not understand, and loading it would hand back an object that
    silently means something else than what was stored.
    """
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(track_row(track))


@pytest.mark.parametrize(
    "clip",
    [
        valid_clip() | {"speed": 2},
        {"clip_id": "v-one", "start_ms": 0, "duration_ms": 6_000},
        valid_clip() | {"start_ms": "0"},
        valid_clip() | {"source_material_id": "not-a-uuid"},
        valid_clip() | {"source_material_id": str(UUID(int=0))},
        valid_clip() | {"source_material_id": 7},
        valid_clip() | {"source_material_id": str(MATERIAL_ONE).upper()},
        # Neither half present and both present are the two ways a clip can fail
        # to say what it is: it carries a material or a caption, never neither
        # and never both.
        valid_clip() | {"source_material_id": None},
        valid_clip() | {"text": "字幕"},
        # A level needs a stretch of a source to apply to, and one end of a
        # source window is no window at all.
        valid_clip() | {"gain_db": -6.0},
        valid_clip() | {"source_in_ms": 0},
    ],
    ids=[
        "extra-key",
        "keys-missing",
        "start-text",
        "material-not-a-uuid",
        "material-nil-uuid",
        "material-number",
        "material-upper-case",
        "neither-material-nor-text",
        "both-material-and-text",
        "level-without-a-window",
        "half-a-window",
    ],
)
def test_hydration_refuses_a_clip_document_of_the_wrong_shape(clip: object) -> None:
    """Same rule one level down, and the same reason for the `extra-key` case.

    A clip is where the silent-loss risk is worst: `.get()` on a document that
    spells a key differently yields `None`, and `None` is a legal value for four
    of these nine fields. A misspelled `gain_db` would hydrate into a perfectly
    valid-looking object that has quietly lost a level.
    """
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(one_track_row([clip]))


def caption_with_material_row(source_material_id: object) -> RowMapping:
    """A caption clip that also names a material, next to a legal picture lane.

    The picture lane is there because the aggregate refuses a timeline without
    one, and the caption clip carries text *and* a material because that is the
    only combination where "the identifier could not be parsed" and "there is no
    identifier" lead to different answers.
    """
    return hydration_row(
        duration_ms=VALID_CLIP_DURATION_MS,
        tracks=[
            {"track_id": "visual", "kind": "visual", "clips": [valid_clip()]},
            {
                "track_id": "caption",
                "kind": "caption",
                "clips": [
                    clip_document(
                        "c-one",
                        0,
                        2_000,
                        source_material_id=source_material_id,
                        text=CAPTION_ONE,
                    )
                ],
            },
        ],
    )


def narration_row(gain_db: object) -> RowMapping:
    """A narration clip carrying a level, next to a legal picture lane.

    The clip parametrisation above runs on a picture lane, where a level of
    *any* type is illegal -- the picture lane carries no sound of its own. So an
    integer level asserted there would be refused for a reason having nothing to
    do with its type. A narration clip is where a level is required, and
    therefore the only place its type can be the thing under test.
    """
    return hydration_row(
        duration_ms=VALID_CLIP_DURATION_MS,
        tracks=[
            {"track_id": "visual", "kind": "visual", "clips": [valid_clip()]},
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
                        gain_db=gain_db,
                    )
                ],
            },
        ],
    )


@pytest.mark.parametrize(
    "gain_db",
    [-6, 0, 12, -60, "-6.0", True],
    ids=["whole-number", "zero", "upper-bound", "lower-bound", "text", "boolean"],
)
def test_hydration_refuses_a_level_stored_as_anything_but_a_float(gain_db: object) -> None:
    """JSON keeps `-6` and `-6.0` apart, and only one of them is a level.

    Measured against PostgreSQL 18.4: a JSON integer comes back as an `int` and
    a JSON real as a `float`, and `TimelineClip` requires a `float`. The write
    side is covered -- the stored document is asserted to hold a `float`, and a
    mutation that normalises whole numbers to integers is killed by it. This is
    the *read* side, which had nothing: hydration's whole premise is that rows
    also arrive from migrations, fixtures and hand-run statements, and a person
    typing `"gain_db": -6` into one of those is the likeliest way this ever
    happens.

    Both ends of the allowed range are here as integers on purpose. `-60` and
    `12` are the boundary values the domain accepts as floats, so a guard that
    checked only the range would pass them and only the type check refuses them.
    `True` is included because `isinstance(True, float)` is `False` but
    `isinstance(True, int)` is `True` -- a boolean must not be read as a level
    whichever way the check is spelled.
    """
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(narration_row(gain_db))


@pytest.mark.parametrize(
    "gain_db", [-6.0, -3.5, -60.0, 12.0], ids=["whole", "fractional", "lowest", "highest"]
)
def test_a_level_stored_as_a_float_hydrates_including_at_both_bounds(gain_db: float) -> None:
    """The accepting side, so the refusals above cannot be vacuous."""
    hydrated = repository_module._hydrate(narration_row(gain_db))
    level = hydrated.tracks[1].clips[0].gain_db
    assert type(level) is float
    assert level == gain_db


@pytest.mark.parametrize(
    "source_material_id",
    ["not-a-uuid", str(UUID(int=0)), 7, str(MATERIAL_ONE).upper()],
    ids=["not-a-uuid", "nil-uuid", "number", "upper-case"],
)
def test_a_material_identifier_that_cannot_be_parsed_is_not_read_as_absent(
    source_material_id: object,
) -> None:
    """ "Unparseable" and "not there" are different, and only this shape shows it.

    Added because a mutation proved the four malformed-identifier cases in the
    list above were passing for the wrong reason. Make `_material_id` swallow
    `InvalidResourceId` and answer `None`, and every one of them stays green:
    those clips carry no text, so a clip that loses its material is refused
    anyway -- by the rule that a clip must carry a material *or* a caption,
    never neither. The refusal was real; the reason had stopped being the
    identifier.

    A caption clip carrying text is the case that separates them. Losing its
    material turns it into a perfectly ordinary caption clip, so a repository
    that quietly answered `None` would hydrate a stored media reference away and
    hand back a timeline nobody ever stored. Refusing the row is the only honest
    answer to "this identifier is not one".
    """
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(caption_with_material_row(source_material_id))


@pytest.mark.parametrize(
    "transition",
    [
        {"kind": "fade"},
        {"kind": "fade", "duration_ms": 800, "easing": "linear"},
        {"kind": "cut", "duration_ms": 800},
        {"kind": None, "duration_ms": 800},
        {"kind": "fade", "duration_ms": "800"},
        {"kind": "fade", "duration_ms": 0},
        "fade",
        800,
        [],
    ],
    ids=[
        "duration-missing",
        "extra-key",
        "kind-unknown",
        "kind-null",
        "duration-text",
        "duration-zero",
        "text",
        "number",
        "array",
    ],
)
def test_hydration_refuses_a_transition_document_of_the_wrong_shape(
    transition: object,
) -> None:
    """A hard cut is `None`, not a transition named "cut".

    The domain deliberately has no `CUT` member -- two spellings of one state is
    how they drift apart -- so a stored `"cut"` is a broken row, not a third
    kind of edit and not something to quietly map onto "no transition".
    """
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(transition_row(transition))


def test_hydration_normalises_a_stored_timestamp_to_utc() -> None:
    """A zero offset is not the same thing as UTC.

    The domain accepts any timezone whose offset is zero, so it cannot be the
    thing that guarantees `tzinfo is UTC`; and a row carrying a non-zero offset
    -- as here -- the domain would refuse outright. Normalising at hydration is
    what makes the loaded object's timezone a fact rather than a driver detail.
    """
    shanghai_noon = datetime(2026, 7, 29, 11, 21, 45, 123_456, tzinfo=SHANGHAI)
    hydrated = repository_module._hydrate(hydration_row(created_at=shanghai_noon))
    assert hydrated.created_at.tzinfo is UTC
    assert hydrated.created_at == CREATED_AT


@pytest.mark.parametrize(
    "created_at",
    [
        # Naive: the domain refuses these outright. Normalising before the
        # constructor would instead reinterpret it in the host's timezone --
        # moving the instant by the host's offset and handing back a perfectly
        # valid-looking object. The guard has to run first.
        datetime(2026, 7, 29, 3, 21, 45, 123_456),
        "2026-07-29T03:21:45.123456+00:00",
        123,
        # `timelines.created_at` is NOT NULL, so unlike `materials.described_at`
        # a NULL here can only be a broken row. T2's handoff flagged this as the
        # one expectation that has to be re-decided per column, never copied.
        None,
    ],
    ids=["naive", "text", "number", "null"],
)
def test_hydration_refuses_a_timestamp_it_cannot_trust(created_at: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(hydration_row(created_at=created_at))


@pytest.mark.parametrize(
    "revision",
    [0, -1, "1", 1.0, None, True],
    ids=["zero", "negative", "text", "float", "null", "boolean"],
)
def test_hydration_refuses_a_revision_the_column_would_accept(revision: object) -> None:
    """Revisions start at 1, and `integer` has no opinion about that.

    `True` is in the list because `isinstance(True, int)` is `True` in Python: a
    stored boolean would sail through an `isinstance` check and be read as
    revision 1. The domain spells this as `type(...) is not int`, and hydration
    inherits that by handing the value straight to the constructor.
    """
    with pytest.raises(InvalidTimelineModel):
        repository_module._hydrate(hydration_row(revision=revision))


@pytest.mark.parametrize("revision", [1, 2_147_483_647], ids=["first", "int32-max"])
def test_hydration_accepts_a_revision_at_either_end_of_the_column(revision: int) -> None:
    """The other side of the boundary, so the refusals above are not vacuous.

    The upper value is the largest an `integer` column holds. The domain sets no
    upper bound at all, which is a mismatch this task registers rather than
    papers over -- see the leftover note in `LE-05.md`.
    """
    assert repository_module._hydrate(hydration_row(revision=revision)).revision == revision
