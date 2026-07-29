"""LE-05 T2: materials on a real PostgreSQL.

Every assertion reads the database back, either through the repository or
through raw Core statements that bypass it. A repository that only ever reads
its own writes proves nothing about what actually landed -- and this table is
the first one in the schema with JSONB columns, so what the driver hands back
for them is a measured fact here rather than an assumption.
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from alembic_head import HEAD_REVISION
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.materials import (
    MaterialAlreadyRegistered,
    MaterialNotFound,
    MaterialPersistenceUnavailable,
)
from automation_tool.control_plane.domain import (
    MAX_DESCRIPTION_CHARACTERS,
    MAX_TAG_CHARACTERS,
    MAX_TAGS,
    MAX_TRANSCRIPT_CHARACTERS,
    DescriptionSource,
    InvalidMaterialModel,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.infrastructure.database import Database, materials
from automation_tool.control_plane.infrastructure.database.material_repository import (
    SqlAlchemyMaterialRepository,
)

# Carrying microseconds, so a timestamp column that silently truncates is caught.
DESCRIBED_AT = datetime(2026, 7, 29, 3, 21, 45, 123_456, tzinfo=UTC)
LATER = datetime(2026, 7, 29, 9, 2, 3, 456_789, tzinfo=UTC)

# Two digests, both 64 lowercase hex characters and sharing no prefix. The pair
# exists so that a violation-injection test can move `material_id` and
# `content_digest` independently: with one shared digest across every fixture,
# a uniqueness guard on the wrong column refuses the same rows as the right one
# and the test cannot tell which constraint did the refusing. T1 lost a mutant
# exactly that way.
DIGEST_ONE = "a1b2c3d4" * 8
DIGEST_TWO = "9f8e7d6c" * 8

TRANSCRIPT = "今天我们去露营"
DESCRIPTION = "一段露营视频"

PREVIOUS_REVISION = "20260729_0036"

# (data_type, is_nullable, character_maximum_length) straight out of
# `information_schema`. Nullability is part of the shape rather than an
# afterthought: hydration reads every one of these columns, and the four that
# are nullable are nullable because the domain says so -- `described_at` most of
# all, since NULL there is an ordinary value and not a broken row.
EXPECTED_COLUMNS = {
    "material_id": ("uuid", "NO", None),
    "kind": ("character varying", "NO", 16),
    "duration_ms": ("integer", "YES", None),
    "width": ("integer", "YES", None),
    "height": ("integer", "YES", None),
    "content_digest": ("character", "NO", 64),
    "has_audio": ("boolean", "NO", None),
    "audio_loudness_lufs": ("double precision", "YES", None),
    "has_speech": ("boolean", "NO", None),
    "speech_segments_ms": ("jsonb", "NO", None),
    "speech_transcript": ("character varying", "YES", 100_000),
    "shot_boundaries_ms": ("jsonb", "NO", None),
    "ai_description": ("character varying", "YES", 2_000),
    "ai_tags": ("jsonb", "NO", None),
    "description_source": ("character varying", "NO", 16),
    "described_at": ("timestamp with time zone", "YES", None),
}
EXPECTED_CONSTRAINTS = {"pk_materials", "uq_materials_content_digest"}

# The SQLAlchemy type each column is declared with in `schema.py`. T1 compared
# only names and widths and registered the rest as a known gap: pasting
# `nullable=True` onto a NOT NULL column left the whole suite green. Four of
# this table's columns are genuinely nullable, so drift in either direction is a
# live risk here rather than a theoretical one.
EXPECTED_TABLE_TYPES = {
    "material_id": "UUID",
    "kind": "String",
    "duration_ms": "Integer",
    "width": "Integer",
    "height": "Integer",
    "content_digest": "CHAR",
    "has_audio": "Boolean",
    "audio_loudness_lufs": "Double",
    "has_speech": "Boolean",
    "speech_segments_ms": "JSONB",
    "speech_transcript": "String",
    "shot_boundaries_ms": "JSONB",
    "ai_description": "String",
    "ai_tags": "JSONB",
    "description_source": "String",
    "described_at": "DateTime",
}


def forged_identifier(value: UUID) -> MaterialId:
    """A `MaterialId` holding a UUID its constructor would never accept.

    `MaterialId(UUID(int=0))` raises, so a stored row whose `material_id` is not
    a v4 UUID cannot be addressed through the normal constructor at all.
    Building the instance without it is what lets a test reach such a row; the
    row itself gets there through a plain INSERT, which the `uuid` column takes
    happily. Subclassing is not an option -- the class is `@final`.
    """
    identifier = object.__new__(MaterialId)
    object.__setattr__(identifier, "_value", value)
    return identifier


def make_material(
    material_id: MaterialId,
    *,
    content_digest: str = DIGEST_ONE,
    duration_ms: int = 185_000,
) -> Material:
    return Material(
        material_id=material_id,
        kind=MaterialKind.VIDEO,
        duration_ms=duration_ms,
        width=1920,
        height=1080,
        content_digest=content_digest,
        has_audio=True,
        audio_loudness_lufs=-14.5,
        has_speech=True,
        speech_segments_ms=((1_200, 4_800), (6_000, 9_500)),
        speech_transcript=TRANSCRIPT,
        shot_boundaries_ms=(0, 3_200, 15_000),
        ai_description=DESCRIPTION,
        ai_tags=("户外", "露营"),
        description_source=DescriptionSource.AI,
        described_at=DESCRIBED_AT,
    )


def row_values(material_id: UUID, **overrides: object) -> dict[str, object]:
    """The exact column payload `save` is expected to write.

    The three JSONB values are spelled as lists because that is what comes back
    out of the column -- measured, not assumed: SQLAlchemy's asyncpg dialect
    registers a JSON codec, so a JSONB array arrives already parsed as a `list`
    and a JSONB object as a `dict`, never as text. The same lists serialise
    identically on the way in, so one literal serves both directions.
    """
    values: dict[str, object] = {
        "material_id": material_id,
        "kind": "video",
        "duration_ms": 185_000,
        "width": 1920,
        "height": 1080,
        "content_digest": DIGEST_ONE,
        "has_audio": True,
        "audio_loudness_lufs": -14.5,
        "has_speech": True,
        "speech_segments_ms": [[1_200, 4_800], [6_000, 9_500]],
        "speech_transcript": TRANSCRIPT,
        "shot_boundaries_ms": [0, 3_200, 15_000],
        "ai_description": DESCRIPTION,
        "ai_tags": ["户外", "露营"],
        "description_source": "ai",
        "described_at": DESCRIBED_AT,
    }
    values.update(overrides)
    return values


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(materials))


async def stored_row(database: Database, material_id: UUID) -> dict[str, object]:
    async with database.session() as session:
        row = (
            (await session.execute(select(materials).where(materials.c.material_id == material_id)))
            .mappings()
            .one()
        )
    return dict(row)


async def insert_row(database: Database, material_id: UUID, **overrides: object) -> None:
    async with database.session() as session:
        await session.execute(insert(materials).values(**row_values(material_id, **overrides)))


@pytest.mark.asyncio
async def test_saved_material_lands_as_typed_columns_and_hydrates_back_equal(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        material_id = MaterialId.new()
        material = make_material(material_id)

        await repository.save(material)

        row = await stored_row(database, material_id.uuid)
        assert row == row_values(material_id.uuid)
        # The JSONB codec fact, pinned. A driver or dialect change that starts
        # handing back the raw document as text would otherwise turn every
        # hydration into a domain rejection at run time and nothing here would
        # have said why.
        segments = row["speech_segments_ms"]
        assert type(segments) is list
        assert type(cast(list[object], segments)[0]) is list
        assert type(row["ai_tags"]) is list
        assert type(row["shot_boundaries_ms"]) is list
        assert type(row["audio_loudness_lufs"]) is float
        assert type(row["has_audio"]) is bool
        # A 64-character digest occupies the fixed-width column exactly, so
        # nothing is padded onto it here. The padding that `CHAR` does apply to
        # shorter values has its own test below.
        assert row["content_digest"] == DIGEST_ONE
        described_at = row["described_at"]
        assert isinstance(described_at, datetime)
        assert described_at.tzinfo is UTC

        loaded = await repository.get(material_id)
        assert loaded == material
        # Equality alone would not catch this: `((1200, 4800),) == [[1200, 4800]]`
        # is False, but a dataclass comparing two hydrated objects would agree
        # with itself whatever the container type. The domain declares tuples.
        assert type(loaded.speech_segments_ms) is tuple
        assert type(loaded.speech_segments_ms[0]) is tuple
        assert type(loaded.ai_tags) is tuple
        assert type(loaded.shot_boundaries_ms) is tuple
        assert loaded.kind is MaterialKind.VIDEO
        assert loaded.description_source is DescriptionSource.AI
        assert loaded.described_at is not None
        assert loaded.described_at.tzinfo is UTC
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_digest_shorter_than_the_column_is_blank_padded_and_then_refused(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """`CHAR(64)` is `bpchar`, and `bpchar` pads rather than complains.

    Measured: storing `'short'` yields `'short'` followed by 59 spaces, and
    `= 'short'` still matches it, because blank-padded comparison ignores the
    trailing spaces. Neither fact endangers a digest written by the repository
    -- the domain's pattern admits exactly 64 lowercase hex characters, so there
    is nothing to pad and nothing to trim. It matters for rows that arrive any
    other way: the padded value is not hex, so hydration refuses it, and this
    test says so rather than leaving the column's behaviour undocumented.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        material_id = MaterialId.new()
        await insert_row(database, material_id.uuid, content_digest="short")

        stored = (await stored_row(database, material_id.uuid))["content_digest"]
        assert stored == "short" + " " * 59
        with pytest.raises(InvalidMaterialModel):
            await repository.get(material_id)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_missing_material_and_a_repeated_identifier_are_refused_differently(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The two refusals are distinguishable, and neither is "unavailable".

    A caller that cannot tell "already there" from "not there" from "the
    database is down" cannot decide whether retrying is safe, and the REST layer
    above cannot choose between 409, 404 and 503.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        material_id = MaterialId.new()

        with pytest.raises(MaterialNotFound):
            await repository.get(material_id)

        await repository.save(make_material(material_id))
        # The second material carries a different digest, so only the primary
        # key can be what refuses it.
        with pytest.raises(MaterialAlreadyRegistered):
            await repository.save(make_material(material_id, content_digest=DIGEST_TWO))

        # A rejected duplicate must not be an upsert in disguise.
        assert (await stored_row(database, material_id.uuid))["content_digest"] == DIGEST_ONE
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_second_material_with_the_same_digest_is_refused_without_leaking_it(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Identical content under a fresh identifier is still a duplicate.

    "The same file must not be imported twice" is the half of the rule the
    domain cannot hold: `Material` only knows the digest's format, never what
    else is stored. So the uniqueness lives in the table, and the repository's
    job is to translate its refusal without carrying the digest -- which is
    derived from a user's own file -- into whatever the caller logs.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        await repository.save(make_material(MaterialId.new()))

        with pytest.raises(MaterialAlreadyRegistered) as captured:
            await repository.save(make_material(MaterialId.new(), content_digest=DIGEST_ONE))

        rendered = "".join(traceback.format_exception(captured.value))
        assert DIGEST_ONE not in rendered
        assert captured.value.__cause__ is None
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_both_unique_constraints_are_enforced_by_postgresql_itself(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Violation injection, one row per constraint, plus one row for neither.

    A repository that looked the row up before inserting would pass both
    duplicate tests above while still racing two concurrent callers. These
    inserts never touch the repository, so what they prove is that the database
    -- not a Python branch -- refuses.

    Each conflicting row moves exactly one of the two dimensions, so the pair
    tells the constraints apart: dropping the primary key leaves the second case
    red and the first green, and dropping the unique index does the reverse. The
    third row moves both and must be accepted, which is what stops a
    too-broad constraint from passing the first two by refusing everything.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        first_id = MaterialId.new()
        second_id = MaterialId.new()
        await insert_row(database, first_id.uuid, content_digest=DIGEST_ONE)

        with pytest.raises(IntegrityError) as repeated_id:
            await insert_row(database, first_id.uuid, content_digest=DIGEST_TWO)
        with pytest.raises(IntegrityError) as repeated_digest:
            await insert_row(database, second_id.uuid, content_digest=DIGEST_ONE)

        # 23505 is unique_violation; the constraint name is what says which of
        # the two refused, and swapping the two constraints' columns would leave
        # the sqlstate identical.
        assert getattr(repeated_id.value.orig, "sqlstate", None) == "23505"
        assert "pk_materials" in str(repeated_id.value.orig)
        assert getattr(repeated_digest.value.orig, "sqlstate", None) == "23505"
        assert "uq_materials_content_digest" in str(repeated_digest.value.orig)

        await insert_row(database, second_id.uuid, content_digest=DIGEST_TWO)
        assert await stored_material_ids(database) == {first_id.uuid, second_id.uuid}
    finally:
        await reset_data(database)
        await database.close()


async def stored_material_ids(database: Database) -> set[UUID]:
    async with database.session() as session:
        return {
            cast(UUID, value)
            for value in (await session.scalars(select(materials.c.material_id))).all()
        }


@pytest.mark.asyncio
async def test_find_by_digest_answers_both_ways(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The de-duplication gate: an answer of `None` has to mean "not stored".

    Two rows are present for the hit, so a lookup that ignored its argument and
    returned whatever came first would be caught.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        assert await repository.find_by_digest(DIGEST_ONE) is None

        first = make_material(MaterialId.new(), content_digest=DIGEST_ONE)
        second = make_material(MaterialId.new(), content_digest=DIGEST_TWO)
        await repository.save(first)
        await repository.save(second)

        assert await repository.find_by_digest(DIGEST_ONE) == first
        assert await repository.find_by_digest(DIGEST_TWO) == second
        assert await repository.find_by_digest("0" * 64) is None
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_update_description_rewrites_four_columns_and_no_others(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Probing facts survive a description pass; a missing row is not a no-op.

    The whole row is compared afterwards, not just the four columns that should
    have moved. An UPDATE that also rewrote `speech_segments_ms` or reset
    `content_digest` would pass a narrower assertion.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        material_id = MaterialId.new()
        material = make_material(material_id)
        await repository.save(material)

        written_by_user = material.with_user_description("我自己写的描述")
        await repository.update_description(written_by_user)

        assert await stored_row(database, material_id.uuid) == row_values(
            material_id.uuid,
            ai_description="我自己写的描述",
            ai_tags=[],
            description_source="user",
            described_at=None,
        )
        assert await repository.get(material_id) == written_by_user

        # And the AI direction, so the method is not accidentally specific to
        # the shape `with_user_description` happens to produce.
        rewritten = make_material(material_id).with_ai_description(
            "模型看到的描述", ("夜景", "延时"), LATER
        )
        await repository.update_description(rewritten)
        assert await repository.get(material_id) == rewritten

        with pytest.raises(MaterialNotFound):
            await repository.update_description(make_material(MaterialId.new()))
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_update_description_ignores_the_probing_facts_it_is_handed(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """The controlled part of "controlled update" needs a material that disagrees.

    The test above hands over an object whose other fields already match the
    stored row, so an UPDATE writing all sixteen columns would produce exactly
    the same row and pass. Verified by mutation: switching the statement to the
    full column set left that test green.

    A material carrying the same identifier and different probing facts is what
    tells the two apart. It is not a hypothetical shape either -- an object
    hydrated from one row, re-probed after a file was replaced on disk, and then
    passed here for its description is precisely how a digest would get
    overwritten while every other test stayed green.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        material_id = MaterialId.new()
        await repository.save(make_material(material_id))

        disagreeing = Material(
            material_id=material_id,
            kind=MaterialKind.AUDIO,
            duration_ms=60_000,
            width=None,
            height=None,
            content_digest=DIGEST_TWO,
            has_audio=True,
            audio_loudness_lufs=-23.0,
            has_speech=False,
            speech_segments_ms=(),
            speech_transcript=None,
            shot_boundaries_ms=(),
            ai_description="模型看到的描述",
            ai_tags=("夜景",),
            description_source=DescriptionSource.AI,
            described_at=LATER,
        )
        await repository.update_description(disagreeing)

        assert await stored_row(database, material_id.uuid) == row_values(
            material_id.uuid,
            ai_description="模型看到的描述",
            ai_tags=["夜景"],
            description_source="ai",
            described_at=LATER,
        )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_the_longest_values_the_domain_accepts_still_fit_the_columns(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """Ties the domain's three text limits to the real column widths.

    `ai_description` and `speech_transcript` are `varchar` sized from the same
    constants the domain enforces, and `schema.py` imports them, so those two
    are references rather than copies -- but the migration spells the numbers
    out, because a migration is frozen history and must not import a constant
    that can move under it. Widening the domain without widening the column
    turns a clean validation error into a `StringDataRightTruncation` at insert
    time, on the two inputs a user and a model control.

    Tags live in JSONB and have no column width, so what the last assertions pin
    for them is that a full set survives the round trip rather than being
    truncated somewhere in the encoder.

    The text is CJK, so the description is 2,000 characters and 6,000 bytes.
    PostgreSQL counts `varchar` in characters; a column counting bytes fails
    here.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    longest_description = "描" * MAX_DESCRIPTION_CHARACTERS
    longest_transcript = "话" * MAX_TRANSCRIPT_CHARACTERS
    longest_tags = tuple(
        f"{index:02d}" + "标" * (MAX_TAG_CHARACTERS - 2) for index in range(MAX_TAGS)
    )
    try:
        await reset_data(database)
        material_id = MaterialId.new()
        material = Material(
            material_id=material_id,
            kind=MaterialKind.VIDEO,
            duration_ms=185_000,
            width=1920,
            height=1080,
            content_digest=DIGEST_ONE,
            has_audio=True,
            audio_loudness_lufs=-14.5,
            has_speech=True,
            speech_segments_ms=((1_200, 4_800),),
            speech_transcript=longest_transcript,
            shot_boundaries_ms=(0,),
            ai_description=longest_description,
            ai_tags=longest_tags,
            description_source=DescriptionSource.AI,
            described_at=DESCRIBED_AT,
        )

        await repository.save(material)
        assert await repository.get(material_id) == material
        row = await stored_row(database, material_id.uuid)
        assert row["ai_description"] == longest_description
        assert row["speech_transcript"] == longest_transcript
        assert row["ai_tags"] == list(longest_tags)

        with pytest.raises(InvalidMaterialModel):
            make_material(MaterialId.new()).with_user_description(longest_description + "描")
        with pytest.raises(InvalidMaterialModel):
            Material(
                material_id=MaterialId.new(),
                kind=MaterialKind.AUDIO,
                duration_ms=185_000,
                width=None,
                height=None,
                content_digest=DIGEST_TWO,
                has_audio=True,
                audio_loudness_lufs=None,
                has_speech=True,
                speech_segments_ms=((0, 1),),
                speech_transcript=longest_transcript + "话",
                shot_boundaries_ms=(),
                ai_description=None,
                ai_tags=(),
                description_source=DescriptionSource.AI,
                described_at=None,
            )
    finally:
        await reset_data(database)
        await database.close()


# Each row is one the table accepts and the domain refuses. `Material` has no
# nested value objects, so unlike T1 there is no "which layer rejected it"
# question -- every one of these is refused by `__post_init__` itself. What the
# list is chosen for instead is coverage of the shapes a check constraint could
# never express, plus the three JSONB columns, whose contents SQL does not look
# inside at all.
REFUSED_ROWS: dict[str, dict[str, object]] = {
    "kind-not-a-member": {"kind": "gif"},
    "description-source-not-a-member": {"description_source": "robot"},
    "digest-not-hex": {"content_digest": "z" * 64},
    "digest-upper-case": {"content_digest": DIGEST_ONE.upper()},
    "segments-not-an-array": {"speech_segments_ms": {"not": "an array"}},
    "segments-not-pairs": {"speech_segments_ms": [[1_200, 4_800, 9_000]]},
    "segments-scalar-elements": {"speech_segments_ms": [1_200]},
    "segments-text-elements": {"speech_segments_ms": [["1200", "4800"]]},
    "segments-reversed": {"speech_segments_ms": [[4_800, 1_200]]},
    "segments-negative-start": {"speech_segments_ms": [[-5, 4_800]]},
    "segments-past-the-end": {"speech_segments_ms": [[1_200, 999_999]]},
    "segments-overlapping": {"speech_segments_ms": [[1_200, 4_800], [4_000, 9_500]]},
    "shot-boundaries-not-ascending": {"shot_boundaries_ms": [15_000, 3_200]},
    "shot-boundaries-nested": {"shot_boundaries_ms": [[3_200]]},
    "tags-not-an-array": {"ai_tags": {"first": "户外"}},
    "tags-repeated": {"ai_tags": ["户外", "户外"]},
    "tags-without-a-description": {"ai_description": None, "described_at": None},
    "user-source-with-a-described-at": {"description_source": "user", "ai_tags": []},
    "loudness-out-of-range": {"audio_loudness_lufs": -80.0},
    "silent-but-loud": {"has_audio": False},
    "video-without-a-duration": {"duration_ms": None},
    "video-without-a-frame-size": {"width": None},
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
    from `save`, and JSONB in particular is a document the database never
    inspects. Hydrating through the constructor is what makes every one of them
    meet the rules a caller meets.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        material_id = MaterialId.new()
        await insert_row(database, material_id.uuid, **overrides)
        with pytest.raises(InvalidMaterialModel):
            await repository.get(material_id)
    finally:
        await reset_data(database)
        await database.close()


# Both rows are legal and both leave `described_at` NULL, which is the whole
# point: T1's `created_at` is NOT NULL, so every one of its timestamp cases was
# a rejection. Copying that shape here would demand that an ordinary row be
# refused -- and if the repository were copied along with the test, both halves
# would agree and the suite would stay green while the feature was broken.
NULL_DESCRIBED_AT_ROWS: dict[str, dict[str, object]] = {
    "never-described": {
        "ai_description": None,
        "ai_tags": [],
        "description_source": "ai",
        "described_at": None,
    },
    "written-by-the-user": {
        "ai_description": "我自己写的描述",
        "ai_tags": [],
        "description_source": "user",
        "described_at": None,
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides", list(NULL_DESCRIBED_AT_ROWS.values()), ids=list(NULL_DESCRIBED_AT_ROWS)
)
async def test_a_null_described_at_hydrates_as_none(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    overrides: dict[str, object],
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        material_id = MaterialId.new()
        await insert_row(database, material_id.uuid, **overrides)

        loaded = await repository.get(material_id)
        assert loaded.described_at is None
        assert loaded.ai_description == overrides["ai_description"]
        assert loaded.description_source.value == overrides["description_source"]
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_a_stored_identifier_of_the_wrong_uuid_version_is_refused(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A `uuid` column accepts every version; `MaterialId` accepts only v4."""
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await reset_data(database)
        nil_uuid = UUID(int=0)
        await insert_row(database, nil_uuid)
        assert await stored_material_ids(database) == {nil_uuid}

        with pytest.raises(InvalidMaterialModel):
            await repository.get(forged_identifier(nil_uuid))
        with pytest.raises(InvalidMaterialModel):
            await repository.find_by_digest(DIGEST_ONE)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_wrong_credentials_are_refused_without_leaking_the_identity(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    """A refused login is neither an `OSError` nor a `SQLAlchemyError`.

    A refused *connection* is an `OSError`. A refused *login* is an
    `asyncpg.exceptions.InvalidPasswordError`, and its message names the role.
    Measured MRO on asyncpg 0.31.0:

        InvalidPasswordError -> InvalidAuthorizationSpecificationError
        -> PostgresError -> PostgresMessage -> Exception -> BaseException

    `SQLAlchemyError` appears nowhere on it, and neither does `OSError`. All
    four public methods are checked, because a catch-all tail missing from one
    of them leaks from that one alone.
    """
    alembic_runner(postgresql_url, "upgrade", "head")
    url = make_url(postgresql_url)
    role = url.username
    assert role is not None
    database = Database.from_url(
        url.set(password="le05_wrong_password").render_as_string(hide_password=False)
    )
    try:
        repository = SqlAlchemyMaterialRepository(database)
        material = make_material(MaterialId.new())
        with pytest.raises(MaterialPersistenceUnavailable) as loaded:
            await repository.get(material.material_id)
        with pytest.raises(MaterialPersistenceUnavailable) as saved:
            await repository.save(material)
        with pytest.raises(MaterialPersistenceUnavailable) as found:
            await repository.find_by_digest(DIGEST_ONE)
        with pytest.raises(MaterialPersistenceUnavailable) as updated:
            await repository.update_description(material)
        for captured in (loaded, saved, found, updated):
            rendered = "".join(traceback.format_exception(captured.value))
            assert role not in rendered
            assert "password authentication failed" not in rendered
            assert captured.value.__cause__ is None
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
                            "where table_schema = 'public' and table_name = 'materials'"
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
                        "'public.materials'::regclass"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        # `schema.py` and the migration each declare these separately, and
        # nothing else compares them: every other test here reads either the
        # migrated database or this file's own constants, so narrowing the Table
        # alone would leave the suite green.
        declared = {
            column.name: (column.type.__class__.__name__, column.nullable)
            for column in materials.columns
        }
        assert declared == {
            name: (EXPECTED_TABLE_TYPES[name], shape[1] == "YES")
            for name, shape in EXPECTED_COLUMNS.items()
        }
        declared_widths = {
            column.name: getattr(column.type, "length", None) for column in materials.columns
        }
        assert declared_widths == {name: shape[2] for name, shape in EXPECTED_COLUMNS.items()}

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            removed = await session.scalar(text("select to_regclass('public.materials')"))
        assert removed is None
    finally:
        alembic_runner(postgresql_url, "upgrade", "head")
        await database.close()
