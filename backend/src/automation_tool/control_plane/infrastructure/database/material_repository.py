"""PostgreSQL storage for the local editing material library."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, insert, select, update
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
    InvalidMaterialModel,
    Material,
    MaterialId,
    MaterialKind,
)

from .hydration import normalise_timestamp
from .schema import materials
from .session import Database

# A refused or timed-out connection surfaces as an `OSError`, not a
# `SQLAlchemyError`: it comes out of asyncio's connect call, and the asyncpg
# dialect only wraps asyncpg's own exceptions. `session.py` and five other
# repositories catch the same pair for the same reason.
_CONNECTION_FAILURES = (OSError, SQLAlchemyError)


def _segment_pairs(value: object) -> object:
    """Turn a JSON array of pairs back into the tuples the domain declares.

    Measured against PostgreSQL 18.4 with asyncpg 0.31.0: SQLAlchemy's asyncpg
    dialect registers a JSON codec, so a JSONB column arrives already parsed --
    a JSON array as `list`, an object as `dict`, never as text. Nested arrays
    therefore come back as lists of lists, and `Material` declares tuples of
    tuples, so something has to convert.

    Shaped deliberately like `normalise_timestamp`: convert only what is already
    the right shape and hand everything else on untouched. Converting first
    would raise a bare `TypeError` from inside this module on a number, and
    iterating a `dict` would quietly hand back a tuple of its keys -- neither of
    which is the domain's error or one of this module's, and the second of which
    would be a plausible-looking value built out of nonsense.
    """
    if isinstance(value, list) and all(isinstance(item, list) for item in value):
        return tuple(tuple(item) for item in value)
    return value


def _sequence(value: object) -> object:
    """The same rule for the two flat JSON arrays: `ai_tags`, `shot_boundaries_ms`.

    Nesting is not unwrapped here on purpose. `[[0]]` becomes a tuple holding a
    list, which the domain refuses because a shot boundary must be an `int` --
    the right answer for a row that is wrong, rather than a repair that invents
    what was meant.
    """
    if isinstance(value, list):
        return tuple(value)
    return value


def _hydrate(row: RowMapping) -> Material:
    """Rebuild a material by constructing it, so a stored row is re-validated.

    Nothing in the table stops a row the domain would refuse -- least of all in
    the three JSONB columns, which PostgreSQL does not inspect at all -- and
    rows arrive from migrations, fixtures and hand-run statements as well as
    from `save`. Going through the constructor makes every one of them meet the
    rules a caller meets. `InvalidMaterialModel` then propagates rather than
    being translated: a row the domain rejects is bad data, not a repository
    failure, and the caller has to be able to tell those apart.

    This is the one place in the codebase outside `material.py` that is allowed
    to build a `Material` from parts, and a structural test enforces that by
    name -- everything else has to go through `with_ai_description` or
    `with_user_description`, so that a describe pass cannot overwrite what a
    person wrote. Reconstituting a stored row is not that operation.

    The three parses share one `except`. `InvalidResourceId` and an unknown
    enumeration member are both `ValueError`, and all three mean the same thing
    to a caller: this row is not a material. Leaving the raw string on the
    object instead is the failure LE-04 recorded on `EditingJobStatus` --
    `kind is MaterialKind.AUDIO` is silently `False` for the string `"audio"`,
    and that is the unsafe direction.
    """
    try:
        material_id = MaterialId.parse(row["material_id"])
        kind = MaterialKind(row["kind"])
        description_source = DescriptionSource(row["description_source"])
    except ValueError:
        raise InvalidMaterialModel from None
    return Material(
        material_id=material_id,
        kind=kind,
        duration_ms=cast(int | None, row["duration_ms"]),
        width=cast(int | None, row["width"]),
        height=cast(int | None, row["height"]),
        content_digest=cast(str, row["content_digest"]),
        has_audio=cast(bool, row["has_audio"]),
        audio_loudness_lufs=cast(float | None, row["audio_loudness_lufs"]),
        has_speech=cast(bool, row["has_speech"]),
        speech_segments_ms=cast(
            tuple[tuple[int, int], ...], _segment_pairs(row["speech_segments_ms"])
        ),
        speech_transcript=cast(str | None, row["speech_transcript"]),
        shot_boundaries_ms=cast(tuple[int, ...], _sequence(row["shot_boundaries_ms"])),
        ai_description=cast(str | None, row["ai_description"]),
        ai_tags=cast(tuple[str, ...], _sequence(row["ai_tags"])),
        description_source=description_source,
        described_at=cast(datetime | None, normalise_timestamp(row["described_at"])),
    )


def _column_values(material: Material) -> dict[str, object]:
    """The full row, with the domain's tuples spelled as the lists JSON has."""
    return {
        "material_id": material.material_id.uuid,
        "kind": material.kind.value,
        "duration_ms": material.duration_ms,
        "width": material.width,
        "height": material.height,
        "content_digest": material.content_digest,
        "has_audio": material.has_audio,
        "audio_loudness_lufs": material.audio_loudness_lufs,
        "has_speech": material.has_speech,
        "speech_segments_ms": [list(segment) for segment in material.speech_segments_ms],
        "speech_transcript": material.speech_transcript,
        "shot_boundaries_ms": list(material.shot_boundaries_ms),
        **_description_values(material),
    }


def _description_values(material: Material) -> dict[str, object]:
    """The four columns a describe pass is allowed to move, and no others.

    Shared with `update_description` so that "which columns are the description"
    is written once: a fifth column added to one and not the other would be
    stored on insert and silently dropped on every update after it.
    """
    return {
        "ai_description": material.ai_description,
        "ai_tags": list(material.ai_tags),
        "description_source": material.description_source.value,
        "described_at": material.described_at,
    }


class SqlAlchemyMaterialRepository:
    """Write-once material rows, apart from the four description columns."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise MaterialPersistenceUnavailable
        self._database = database

    async def save(self, material: Material) -> None:
        """Insert one material, leaving any existing row untouched.

        There is no lookup before the insert -- not for the identifier and not
        for the digest. That would let two callers importing the same file both
        find nothing and both proceed. The primary key and the unique index are
        what refuse the second one, and they refuse it whoever is racing.
        """
        if not isinstance(material, Material):
            raise MaterialDataRejected
        try:
            async with self._database.session() as session:
                await session.execute(insert(materials).values(**_column_values(material)))
        except IntegrityError:
            raise MaterialAlreadyRegistered from None
        except _CONNECTION_FAILURES:
            raise MaterialPersistenceUnavailable from None
        except Exception:
            # Authentication and authorisation failures are neither of the
            # above. Measured on asyncpg 0.31.0:
            #
            #   InvalidPasswordError -> InvalidAuthorizationSpecificationError
            #     -> PostgresError -> PostgresMessage -> Exception
            #   InsufficientPrivilegeError -> SyntaxOrAccessError -> PostgresError -> ...
            #   InvalidCatalogNameError -> PostgresError -> ...
            #   TooManyConnectionsError -> InsufficientResourcesError -> PostgresError -> ...
            #
            # Only the third sits directly under `PostgresError`; the others
            # arrive through an intermediate class, which is why matching on any
            # single named base would miss some of them. None of the four has
            # `OSError` or `SQLAlchemyError` anywhere on its MRO, and their
            # messages name the role and the database, so without this tail they
            # reach the caller verbatim. The same tail guards every method here.
            raise MaterialPersistenceUnavailable from None

    async def get(self, material_id: MaterialId) -> Material:
        if not isinstance(material_id, MaterialId):
            raise MaterialDataRejected
        row = await self._row(materials.c.material_id == material_id.uuid)
        if row is None:
            raise MaterialNotFound
        return _hydrate(row)

    async def find_by_digest(self, content_digest: str) -> Material | None:
        """Answer whether this exact content is already stored.

        `None` means "not stored", which is why the argument's type is checked
        first: a caller handing over something that is not text would otherwise
        get `None` back, read it as "safe to import", and store the same file
        twice. The digest's *format* is the domain's business and is not checked
        here -- a well-formed digest nobody has stored and a malformed one both
        correctly answer `None`.
        """
        if not isinstance(content_digest, str):
            raise MaterialDataRejected
        row = await self._row(materials.c.content_digest == content_digest)
        return None if row is None else _hydrate(row)

    async def update_description(self, material: Material) -> None:
        """Rewrite the four description columns, unless a person owns them.

        `Material.with_ai_description` returns the material unchanged when the
        description came from the user -- but it decides that from the snapshot
        in the caller's hand, and a snapshot goes stale. Load a material while
        its description is still the model's, let the user write theirs, and the
        object that describe pass is holding still says `AI`. Every method call
        in that sequence is the sanctioned one; no test of behaviour and no
        structural guard sees anything wrong; and the user's words are gone.

        So the refusal is a predicate inside the UPDATE, for the same reason
        `save` leans on the primary key rather than looking first: reading the
        row and then deciding has the identical defect one level down, where two
        describe passes both read `ai` and both proceed. Only the database sees
        one statement at a time.

        The predicate is attached for an AI-sourced write and left off for a
        user-sourced one -- a person rewriting their own description must always
        be allowed, and applying the guard to every update is the obvious way to
        over-fix this.

        `rowcount == 0` then has two meanings, and the follow-up read is a
        best-effort attempt to say which. It is **not** the second half of the
        guard: the protection decision was made in full by the UPDATE, in one
        statement, before this read runs at all.

        Sharing a transaction with the UPDATE does not make the two answers
        exhaustive, and an earlier version of this comment claimed it did.
        Measured against this database: `transaction_isolation` is
        `read committed`, so every statement takes a *fresh* snapshot. Another
        connection that deletes the row and commits between the two statements
        makes it invisible to the read -- same transaction or not. The shared
        transaction saves a trip through the pool; that is all it buys.

        Both answers are safe inside that window: a row that really was deleted
        is `MaterialNotFound`, and a row still present and owned by the user is
        `MaterialDescriptionProtected`. Only the label on a concurrently deleted
        row can come out wrong, and both labels tell the caller the same thing --
        stop. Collapsing the two would not be safe: it would tell a caller to
        stop retrying a material that exists, and leave LE-06 answering 404
        where 409 is correct.

        There is no `IntegrityError` clause, unlike `save`: none of these four
        columns carries a constraint that an UPDATE could violate.
        `SQLAlchemyError` would catch one anyway if that ever stopped being true.
        """
        if not isinstance(material, Material):
            raise MaterialDataRejected
        statement = (
            update(materials)
            .where(materials.c.material_id == material.material_id.uuid)
            .values(**_description_values(material))
        )
        if material.description_source is DescriptionSource.AI:
            statement = statement.where(
                materials.c.description_source != DescriptionSource.USER.value
            )
        try:
            async with self._database.session() as session:
                # `AsyncSession.execute` is declared as returning `Result`, and
                # `rowcount` lives on the `CursorResult` a DML statement really
                # hands back. The cast records that rather than reaching through
                # an `Any`, so a SQLAlchemy release that changes it fails here.
                result = cast("CursorResult[Any]", await session.execute(statement))
                matched = result.rowcount != 0
                stored = (
                    None
                    if matched
                    else (
                        await session.execute(
                            select(materials).where(
                                materials.c.material_id == material.material_id.uuid
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except _CONNECTION_FAILURES:
            raise MaterialPersistenceUnavailable from None
        except Exception:
            raise MaterialPersistenceUnavailable from None
        if matched:
            return
        if stored is None:
            raise MaterialNotFound
        raise MaterialDescriptionProtected

    async def _row(self, condition: ColumnElement[bool]) -> RowMapping | None:
        """Read at most one row, with hydration deliberately left to the caller.

        Hydrating in here would put `Material.__post_init__` inside the `try`,
        where the catch-all tail would swallow a domain rejection and report it
        as an unavailable database -- turning "this stored row is broken" into
        "try again later", which is both wrong and unfixable by retrying.
        """
        try:
            async with self._database.session() as session:
                return (
                    (await session.execute(select(materials).where(condition)))
                    .mappings()
                    .one_or_none()
                )
        except _CONNECTION_FAILURES:
            raise MaterialPersistenceUnavailable from None
        except Exception:
            raise MaterialPersistenceUnavailable from None


__all__ = ["SqlAlchemyMaterialRepository"]
