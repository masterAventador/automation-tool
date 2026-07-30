"""Fail-closed branches around the PostgreSQL material repository.

These cover what a real database cannot be made to produce on demand: a row that
is missing, a row malformed in a way the columns permit, an UPDATE that matched
nothing, and a session that fails in each of the three ways the driver can fail.
Behaviour against a live PostgreSQL is in the integration suite.
"""

from __future__ import annotations

import inspect
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

from automation_tool.control_plane.application.materials import (
    MaterialAlreadyRegistered,
    MaterialDataRejected,
    MaterialDescriptionProtected,
    MaterialNotFound,
    MaterialPersistenceUnavailable,
)
from automation_tool.control_plane.domain import (
    DescriptionSource,
    InstallationId,
    InvalidMaterialModel,
    Material,
    MaterialId,
    MaterialKind,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import Database, materials
from automation_tool.control_plane.infrastructure.database import (
    material_repository as repository_module,
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

DESCRIBED_AT = datetime(2026, 7, 29, 3, 21, 45, 123_456, tzinfo=UTC)
SHANGHAI = timezone(timedelta(hours=8))
DIGEST = "a1b2c3d4" * 8


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
    """Just enough of a `Result` for `.mappings().one_or_none()` and `.rowcount`."""

    def __init__(self, row: RowMapping | None, rowcount: int) -> None:
        self._row = row
        self.rowcount = rowcount

    def mappings(self) -> StubResult:
        return self

    def one_or_none(self) -> RowMapping | None:
        return self._row


class StubSession:
    def __init__(self, row: RowMapping | None, rowcount: int) -> None:
        self._row = row
        self._rowcount = rowcount

    async def execute(self, _statement: object) -> StubResult:
        return StubResult(self._row, self._rowcount)


class StubSessionScope:
    def __init__(self, row: RowMapping | None, rowcount: int) -> None:
        self._row = row
        self._rowcount = rowcount

    async def __aenter__(self) -> StubSession:
        return StubSession(self._row, self._rowcount)

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class StubSessions:
    def __init__(self, row: RowMapping | None, rowcount: int = 1) -> None:
        self._row = row
        self._rowcount = rowcount

    def begin(self) -> StubSessionScope:
        return StubSessionScope(self._row, self._rowcount)


def unreachable_database() -> Database:
    return Database.from_url(UNREACHABLE_URL, connect_timeout_seconds=0.05)


def make_material(material_id: MaterialId | None = None) -> Material:
    return Material(
        material_id=material_id or MaterialId.new(),
        kind=MaterialKind.VIDEO,
        duration_ms=185_000,
        width=1920,
        height=1080,
        content_digest=DIGEST,
        has_audio=True,
        audio_loudness_lufs=-14.5,
        has_speech=True,
        speech_segments_ms=((1_200, 4_800), (6_000, 9_500)),
        speech_transcript="今天我们去露营",
        shot_boundaries_ms=(0, 3_200, 15_000),
        ai_description="一段露营视频",
        ai_tags=("户外", "露营"),
        description_source=DescriptionSource.AI,
        described_at=DESCRIBED_AT,
    )


def hydration_row(**overrides: object) -> RowMapping:
    """A row shaped the way asyncpg really hands one back.

    The three JSONB columns are lists rather than tuples on purpose: measured
    against PostgreSQL 18.4, SQLAlchemy's asyncpg dialect parses JSONB before the
    repository sees it, so a JSON array arrives as `list` and a JSON object as
    `dict`. Writing tuples here would test a conversion that never has to happen.
    """
    values: dict[str, object] = {
        "material_id": MaterialId.new().uuid,
        "kind": "video",
        "duration_ms": 185_000,
        "width": 1920,
        "height": 1080,
        "content_digest": DIGEST,
        "has_audio": True,
        "audio_loudness_lufs": -14.5,
        "has_speech": True,
        "speech_segments_ms": [[1_200, 4_800], [6_000, 9_500]],
        "speech_transcript": "今天我们去露营",
        "shot_boundaries_ms": [0, 3_200, 15_000],
        "ai_description": "一段露营视频",
        "ai_tags": ["户外", "露营"],
        "description_source": "ai",
        "described_at": DESCRIBED_AT,
    }
    values.update(overrides)
    return cast(RowMapping, values)


def test_repository_refuses_a_database_it_does_not_own() -> None:
    with pytest.raises(MaterialPersistenceUnavailable):
        repository_module.SqlAlchemyMaterialRepository(cast(Database, object()))


def test_repository_has_one_required_installation_scoped_api() -> None:
    repository_type = repository_module.SqlAlchemyMaterialRepository

    assert list(inspect.signature(repository_type.save).parameters) == [
        "self",
        "material",
        "installation_id",
    ]
    assert list(inspect.signature(repository_type.get).parameters) == [
        "self",
        "material_id",
        "installation_id",
    ]
    assert list(inspect.signature(repository_type.find_by_digest).parameters) == [
        "self",
        "content_digest",
        "installation_id",
    ]
    assert list(inspect.signature(repository_type.update_description).parameters) == [
        "self",
        "material",
        "installation_id",
    ]
    assert not hasattr(repository_type, "save_for_installation")
    assert not hasattr(repository_type, "get_for_installation")
    assert not hasattr(repository_type, "find_by_digest_for_installation")
    assert not hasattr(repository_type, "update_description_for_installation")
    assert materials.c.installation_id.nullable is False


@pytest.mark.asyncio
async def test_repository_refuses_foreign_argument_types() -> None:
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        installation_id = InstallationId.new()
        material = make_material()
        with pytest.raises(MaterialDataRejected):
            await repository.save(cast(Material, object()), installation_id)
        with pytest.raises(MaterialDataRejected):
            await repository.update_description(cast(Material, object()), installation_id)
        # A bare UUID and a sibling identifier carry exactly the value the column
        # would accept, so the type has to be checked before the statement is
        # built rather than left to the database.
        with pytest.raises(MaterialDataRejected):
            await repository.get(cast(MaterialId, MaterialId.new().uuid), installation_id)
        with pytest.raises(MaterialDataRejected):
            await repository.get(cast(MaterialId, TaskId.new()), installation_id)
        # `find_by_digest` takes text, and `bpchar` comparison silently ignores
        # trailing blanks, so a non-string argument must not reach the statement
        # and be compared as whatever the driver makes of it.
        with pytest.raises(MaterialDataRejected):
            await repository.find_by_digest(cast(str, 12345), installation_id)
        with pytest.raises(MaterialDataRejected):
            await repository.find_by_digest(cast(str, None), installation_id)
        with pytest.raises(MaterialDataRejected):
            await repository.save(
                material,
                cast(InstallationId, object()),
            )
        with pytest.raises(MaterialDataRejected):
            await repository.get(
                material.material_id,
                cast(InstallationId, object()),
            )
        with pytest.raises(MaterialDataRejected):
            await repository.find_by_digest(
                DIGEST,
                cast(InstallationId, object()),
            )
        with pytest.raises(MaterialDataRejected):
            await repository.update_description(
                material,
                cast(InstallationId, object()),
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_unreachable_database_is_refused_without_leaking_the_connection() -> None:
    """A refused connection is an `OSError`, not a `SQLAlchemyError`.

    Measured against a real PostgreSQL: `asyncpg` raises `ConnectionRefusedError`
    out of asyncio's connect call, and the SQLAlchemy dialect does not wrap it,
    because it is not one of asyncpg's own exceptions. A repository catching only
    `SQLAlchemyError` would let the raw socket error escape to the caller.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        material = make_material()
        installation_id = InstallationId.new()
        with pytest.raises(MaterialPersistenceUnavailable) as loaded:
            await repository.get(material.material_id, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as saved:
            await repository.save(material, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as found:
            await repository.find_by_digest(DIGEST, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as updated:
            await repository.update_description(material, installation_id)
        for captured in (loaded, saved, found, updated):
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
    Measured while validating T3: running from a clone in a scratchpad turned
    the identical assertion red with nothing leaking at all, which is a false
    alarm arriving precisely when the signal is being relied on.

    This is not the assertion being loosened. The old spelling happened to catch
    a path leak as well, but by accident rather than by design, and the accident
    cost more than it was worth. `LEAKED_TOKENS` in this file already follows
    the rule a sentinel has to meet: it cannot occur by coincidence.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(SQLAlchemyError("le05_leaked_database_failure")),
        )
        material = make_material()
        installation_id = InstallationId.new()
        with pytest.raises(MaterialPersistenceUnavailable) as loaded:
            await repository.get(material.material_id, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as saved:
            await repository.save(material, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as found:
            await repository.find_by_digest(DIGEST, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as updated:
            await repository.update_description(material, installation_id)
        for captured in (loaded, saved, found, updated):
            assert "le05_leaked_database_failure" not in "".join(
                traceback.format_exception(captured.value)
            )
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

    The absence is what the repository's clause ordering rests on, and it is
    asserted first below. The `PostgresError` assertion that follows carries no
    weight for the catch-all -- it records a third-party fact, so that an
    asyncpg release restructuring this hierarchy fails here and sends someone
    back to re-read the reasoning. The message names the role.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        failure = asyncpg.exceptions.InvalidPasswordError(
            'password authentication failed for user "le05_leaked_user"'
        )
        assert not isinstance(failure, OSError | SQLAlchemyError)
        assert asyncpg.exceptions.PostgresError in type(failure).__mro__
        object.__setattr__(database, "_sessions", FailingSessions(failure))
        material = make_material()
        installation_id = InstallationId.new()
        with pytest.raises(MaterialPersistenceUnavailable) as loaded:
            await repository.get(material.material_id, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as saved:
            await repository.save(material, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as found:
            await repository.find_by_digest(DIGEST, installation_id)
        with pytest.raises(MaterialPersistenceUnavailable) as updated:
            await repository.update_description(material, installation_id)
        for captured in (loaded, saved, found, updated):
            rendered = "".join(traceback.format_exception(captured.value))
            assert "le05_leaked_user" not in rendered
            assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_conflicting_insert_is_already_registered_and_says_no_more() -> None:
    """`IntegrityError` carries the offending key in its `DETAIL` line.

    PostgreSQL answers a duplicate with `Key (content_digest)=(...) already
    exists`, and SQLAlchemy keeps the driver's message on `.orig`. The digest is
    derived from a user's own file, so translating without `from None` would put
    it into every caller's traceback.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        driver_error = Exception("Key (content_digest)=(le05-private-detail) already exists")
        constraint_error = Exception()
        constraint_error.constraint_name = "uq_materials_installation_content_digest"  # type: ignore[attr-defined]
        driver_error.__cause__ = constraint_error
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(
                IntegrityError(
                    "insert into materials",
                    None,
                    driver_error,
                )
            ),
        )
        with pytest.raises(MaterialAlreadyRegistered) as captured:
            await repository.save(make_material(), InstallationId.new())
        rendered = "".join(traceback.format_exception(captured.value))
        assert "le05-private-detail" not in rendered
        assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_scoped_insert_only_calls_known_unique_constraints_a_duplicate() -> None:
    """A missing owner is bad server data, not a duplicate material.

    PostgreSQL reports primary-key and unique-index failures with the same
    IntegrityError wrapper as a foreign-key failure. Only the two names whose
    meaning is actually "already registered" may become the public 409.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(
                IntegrityError(
                    "insert into materials",
                    None,
                    Exception("private unknown integrity failure"),
                )
            ),
        )

        with pytest.raises(MaterialDataRejected) as captured:
            await repository.save(
                make_material(),
                InstallationId.new(),
            )

        assert "private unknown integrity failure" not in "".join(
            traceback.format_exception(captured.value)
        )
        assert captured.value.__cause__ is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_missing_row_is_not_found_and_a_present_row_hydrates() -> None:
    """Both post-query branches of `get` and `find_by_digest`, without a server.

    `get` and `find_by_digest` differ in what "no row" means -- an error and an
    answer respectively -- so the pair is asserted rather than one standing in
    for the other.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        installation_id = InstallationId.new()

        object.__setattr__(database, "_sessions", StubSessions(None))
        with pytest.raises(MaterialNotFound):
            await repository.get(MaterialId.new(), installation_id)
        assert await repository.find_by_digest(DIGEST, installation_id) is None

        material = make_material()
        object.__setattr__(
            database,
            "_sessions",
            StubSessions(hydration_row(material_id=material.material_id.uuid)),
        )
        assert await repository.get(material.material_id, installation_id) == material
        assert await repository.find_by_digest(DIGEST, installation_id) == material
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_updating_a_description_that_matched_no_row_is_not_found() -> None:
    """An UPDATE affecting nothing is the caller's question answered "no".

    Without this branch the method returns normally, the caller believes the
    description was stored, and the only trace is a row that never changed --
    the shape of silent failure this whole module exists to avoid.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        installation_id = InstallationId.new()
        object.__setattr__(database, "_sessions", StubSessions(None, rowcount=0))
        with pytest.raises(MaterialNotFound):
            await repository.update_description(make_material(), installation_id)

        object.__setattr__(database, "_sessions", StubSessions(None, rowcount=1))
        await repository.update_description(make_material(), installation_id)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_row_the_predicate_refused_is_told_apart_from_a_row_that_is_gone() -> None:
    """Nothing matched, and the two reasons for that need different answers.

    Both arrive as `rowcount == 0`. Reporting either as `MaterialNotFound`
    would tell a describe pass to stop retrying a material that is sitting
    right there, and would tell the REST layer above to answer 404 where the
    honest answer is 409 -- somebody already owns this field.

    The follow-up read is best-effort, not the second half of the guard -- the
    protection decision is already complete when it runs. Measured: the
    connection is at `read committed`, so each statement takes its own snapshot,
    and a row deleted and committed by another connection between the UPDATE and
    the read is invisible to the read even though both sit in one transaction.
    Sharing the transaction saves a pool checkout and nothing more.

    Both answers are safe in that window; only which of the two gets reported
    can be raced, and both tell the caller to stop.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        object.__setattr__(
            database,
            "_sessions",
            StubSessions(hydration_row(description_source="user"), rowcount=0),
        )
        with pytest.raises(MaterialDescriptionProtected):
            await repository.update_description(make_material(), InstallationId.new())
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_user_written_description_is_not_sent_through_the_predicate() -> None:
    """Covers the other side of the branch that builds the statement.

    The predicate exists to stop an AI pass overwriting a person; applying it
    to the person's own edit would refuse the one update that must always be
    allowed. Whether the WHERE clause is really absent is asserted against a
    real database in the integration suite -- what this pins is that the branch
    exists and that the ordinary path through it succeeds.
    """
    database = unreachable_database()
    try:
        repository = repository_module.SqlAlchemyMaterialRepository(database)
        object.__setattr__(database, "_sessions", StubSessions(None, rowcount=1))
        await repository.update_description(
            make_material().with_user_description("用户写的"),
            InstallationId.new(),
        )
    finally:
        await database.close()


def test_hydration_refuses_an_identifier_of_the_wrong_uuid_version() -> None:
    """The nil UUID is a valid `uuid` value and an invalid `MaterialId`.

    The integration suite proves such a row really does land in the table; this
    proves the parse failure is translated instead of surfacing as
    `InvalidResourceId`, which nothing about this repository declares.
    """
    with pytest.raises(InvalidMaterialModel):
        repository_module._hydrate(hydration_row(material_id=UUID(int=0)))


@pytest.mark.parametrize(
    ("column", "stored"),
    [
        ("kind", "gif"),
        ("kind", None),
        ("kind", 3),
        ("description_source", "robot"),
        ("description_source", None),
    ],
    ids=["kind-unknown", "kind-null", "kind-number", "source-unknown", "source-null"],
)
def test_hydration_refuses_an_enumeration_value_it_does_not_recognise(
    column: str, stored: object
) -> None:
    """A stored string is not an enumeration member until something parses it.

    Leaving the raw text on the object is the failure LE-04 recorded on
    `EditingJobStatus`: `is_terminal` answered `False` for a bare string, which
    reads as "still running" and is the unsafe direction. `MaterialKind` has the
    same exposure through `kind is MaterialKind.AUDIO` comparisons, which a
    string silently loses. So the parse happens at this boundary, and a value
    that is not a member is a broken row rather than a new kind of material.
    """
    with pytest.raises(InvalidMaterialModel):
        repository_module._hydrate(hydration_row(**{column: stored}))


@pytest.mark.parametrize(
    ("column", "stored"),
    [
        ("speech_segments_ms", {"not": "an array"}),
        ("speech_segments_ms", "[[1200, 4800]]"),
        ("speech_segments_ms", 1_200),
        ("speech_segments_ms", [1_200, 4_800]),
        ("speech_segments_ms", [[1_200, 4_800, 9_000]]),
        ("speech_segments_ms", [[4_800, 1_200]]),
        ("ai_tags", {"first": "户外"}),
        ("ai_tags", "户外"),
        ("ai_tags", [1, 2]),
        ("shot_boundaries_ms", {"first": 0}),
        ("shot_boundaries_ms", [[0]]),
        ("shot_boundaries_ms", [15_000, 3_200]),
    ],
    ids=[
        "segments-object",
        "segments-text",
        "segments-number",
        "segments-flat",
        "segments-triples",
        "segments-reversed",
        "tags-object",
        "tags-text",
        "tags-numbers",
        "boundaries-object",
        "boundaries-nested",
        "boundaries-descending",
    ],
)
def test_hydration_refuses_a_json_document_of_the_wrong_shape(column: str, stored: object) -> None:
    """JSONB is a document the database never looks inside.

    The conversion back to tuples has to behave like the timestamp guard: turn
    only what is already the right shape, and hand everything else to the
    constructor untouched. Converting first would raise a bare `TypeError` from
    inside the repository on a number or a string -- neither the domain's error
    nor one of this module's -- and iterating a `dict` would quietly produce a
    tuple of its keys.
    """
    with pytest.raises(InvalidMaterialModel):
        repository_module._hydrate(hydration_row(**{column: stored}))


def test_hydration_turns_json_arrays_into_the_tuples_the_domain_declares() -> None:
    """Equality between two hydrated objects would agree with itself either way.

    `Material` declares tuples, and `((1200, 4800),) == [[1200, 4800]]` is
    False, so anything comparing a hydrated material against a constructed one
    already depends on this. The types are asserted directly so that a
    conversion which stopped happening fails here rather than somewhere far away
    where a list gets mutated.
    """
    hydrated = repository_module._hydrate(hydration_row())
    assert hydrated.speech_segments_ms == ((1_200, 4_800), (6_000, 9_500))
    assert type(hydrated.speech_segments_ms) is tuple
    assert type(hydrated.speech_segments_ms[0]) is tuple
    assert hydrated.ai_tags == ("户外", "露营")
    assert type(hydrated.ai_tags) is tuple
    assert hydrated.shot_boundaries_ms == (0, 3_200, 15_000)
    assert type(hydrated.shot_boundaries_ms) is tuple


def test_hydration_accepts_empty_json_arrays() -> None:
    """`[]` and SQL NULL are different answers, and only one of them is stored.

    All three JSONB columns are NOT NULL, so "no speech" is an empty array
    rather than an absent value -- and an empty tuple is what the domain
    requires for a material with `has_speech` false. A conversion that treated
    the empty case as "nothing to convert" would hand the list straight through
    and be refused.
    """
    hydrated = repository_module._hydrate(
        hydration_row(
            has_audio=False,
            audio_loudness_lufs=None,
            has_speech=False,
            speech_segments_ms=[],
            speech_transcript=None,
            shot_boundaries_ms=[],
            ai_description=None,
            ai_tags=[],
            described_at=None,
        )
    )
    assert hydrated.speech_segments_ms == ()
    assert hydrated.shot_boundaries_ms == ()
    assert hydrated.ai_tags == ()


def test_hydration_normalises_a_stored_timestamp_to_utc() -> None:
    """A zero offset is not the same thing as UTC.

    The domain accepts any timezone whose offset is zero, so it cannot be the
    thing that guarantees `tzinfo is UTC`; and a row carrying a non-zero offset
    -- as here -- the domain would refuse outright. Normalising at hydration is
    what makes the loaded object's timezone a fact rather than a driver detail.
    """
    shanghai_noon = datetime(2026, 7, 29, 11, 21, 45, 123_456, tzinfo=SHANGHAI)
    hydrated = repository_module._hydrate(hydration_row(described_at=shanghai_noon))
    assert hydrated.described_at is not None
    assert hydrated.described_at.tzinfo is UTC
    assert hydrated.described_at == DESCRIBED_AT


@pytest.mark.parametrize(
    "overrides",
    [
        {"ai_description": None, "ai_tags": [], "description_source": "ai"},
        {"ai_description": "我自己写的描述", "ai_tags": [], "description_source": "user"},
    ],
    ids=["never-described", "written-by-the-user"],
)
def test_a_null_described_at_is_an_ordinary_value_and_hydrates_as_none(
    overrides: dict[str, object],
) -> None:
    """The one case T1's handoff says must not be copied, in the other direction.

    `editing_projects.created_at` is NOT NULL, so T1's parametrised timestamp
    test lists `None` among the values it refuses. `materials.described_at` is
    nullable -- a material nobody has described yet, and one whose description a
    person wrote, both store NULL there. Copying T1's expectation would demand
    that an ordinary row be rejected; worse, a repository copied from T1 along
    with it would agree, and the pair would pass while the feature was broken.

    `normalise_timestamp` needs no change for this: it already returns anything
    that is not an aware datetime untouched, which for `None` means the
    constructor decides. What differs is what the constructor then says.
    """
    hydrated = repository_module._hydrate(hydration_row(described_at=None, **overrides))
    assert hydrated.described_at is None


@pytest.mark.parametrize(
    "described_at",
    [
        # Naive: the domain refuses these outright. Normalising before the
        # constructor would instead reinterpret it in the host's timezone --
        # moving the instant by the host's offset and handing back a perfectly
        # valid-looking object. The guard has to run first.
        datetime(2026, 7, 29, 3, 21, 45, 123_456),
        "2026-07-29T03:21:45.123456+00:00",
        123,
    ],
    ids=["naive", "text", "number"],
)
def test_hydration_refuses_a_timestamp_it_cannot_trust(described_at: object) -> None:
    """Nullable does not mean "anything goes".

    `None` is legal here and has its own test above; these three are not, and
    normalising before validating would turn the last two into a bare
    `AttributeError` from inside the repository -- which is neither the domain's
    error nor one of this module's.
    """
    with pytest.raises(InvalidMaterialModel):
        repository_module._hydrate(hydration_row(described_at=described_at))
