"""PostgreSQL storage for timeline revisions."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Never, cast

from sqlalchemy import Select, insert, select
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
    InvalidResourceId,
    InvalidTimelineModel,
    MaterialId,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)

from .hydration import normalise_timestamp
from .schema import timelines
from .session import Database

# A refused or timed-out connection surfaces as an `OSError`, not a
# `SQLAlchemyError`: it comes out of asyncio's connect call, and the asyncpg
# dialect only wraps asyncpg's own exceptions. `session.py` and six other
# repositories catch the same pair for the same reason.
#
# **In this module that pairing changes no behaviour.** Every `try` here ends in
# an `except Exception` tail answering with the same failure, so deleting this
# clause would be invisible: the tail already covers `OSError` and
# `SQLAlchemyError` alike. It is kept as the shape shared across seven
# repositories -- several of which have no tail, and for those the distinction
# is what stops a raw socket error reaching the caller. Treat the sentence above
# as documenting why the pair exists at all, not as a claim that this file would
# leak without it. Tests naming these classes are pinning the third-party fact,
# not this module's behaviour.
_CONNECTION_FAILURES = (OSError, SQLAlchemyError)

# PostgreSQL's SQLSTATE for the two violations this table can produce. Both
# arrive as one `IntegrityError`, so this is the only thing that tells them
# apart; matching on the constraint name in the driver's message would work too
# but would tie the translation to a string that also carries the offending key
# values, and those must not travel.
_UNIQUE_VIOLATION: Final = "23505"
_FOREIGN_KEY_VIOLATION: Final = "23503"

# The keys a stored document is allowed to have, at each of the three levels.
# Read off the dataclasses rather than written out, so that a field added to the
# domain cannot leave the writer storing something the reader refuses -- or, far
# worse, leave the reader quietly dropping it.
_TRACK_KEYS: Final = frozenset(field.name for field in fields(TimelineTrack))
_CLIP_KEYS: Final = frozenset(field.name for field in fields(TimelineClip))
_TRANSITION_KEYS: Final = frozenset(field.name for field in fields(TimelineTransition))


def _refuse_integrity_violation(error: IntegrityError) -> Never:
    """Turn one exception class into the three answers a caller can act on.

    A duplicate `(timeline_id, revision)` means this revision is stored and a
    revision is write-once, so the caller has to choose another one. A foreign
    key violation means the *project* the timeline names is not there, which is
    a different resource, a different thing to fix, and 404 rather than 409 one
    layer up. Neither improves on a retry, which is what keeps both away from
    `TimelinePersistenceUnavailable`.

    Anything else -- a NOT NULL violation is `23502`, a CHECK is `23514` --
    cannot come from this table as it stands, but "the database refused this
    row" is still what happened, and `TimelineDataRejected` is the answer that
    says so without inviting a retry. `error.orig` can be `None`, so the lookup
    goes through `getattr` rather than reaching for the attribute: an
    `AttributeError` raised from in here would be neither the domain's error nor
    one of this module's.
    """
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == _UNIQUE_VIOLATION:
        raise TimelineRevisionAlreadyStored from None
    if sqlstate == _FOREIGN_KEY_VIOLATION:
        raise TimelineProjectMissing from None
    raise TimelineDataRejected from None


def _enumeration_member[MemberT: StrEnum](members: type[MemberT], stored: object) -> MemberT:
    """Parse a stored string back into a member, or refuse the row.

    Leaving the raw text on the object is the failure LE-04 recorded on
    `EditingJobStatus`: a bare string silently loses every `is` comparison
    against a member, and it loses them in the direction that reads as "carry
    on". `kind is TimelineTrackKind.VISUAL` would be `False` for the string
    `"visual"`, and the picture lane would quietly stop being the picture lane.

    Compares against the members rather than calling `members(stored)`, which is
    the obvious spelling and does not type-check here: through `type[MemberT]`
    the call resolves to `StrEnum.__new__`, which is annotated as taking `str`,
    while what arrives from a JSON document is `object`. Casting it to `str` to
    get past that would be a claim about the one value most likely to be
    something else -- `None`, a number, a nested object. Equality is honest
    about accepting anything, and it is the same lookup by value that calling
    the enumeration would have performed.
    """
    for member in members:
        if member.value == stored:
            return member
    raise InvalidTimelineModel


def _material_id(stored: object) -> MaterialId | None:
    """`None` is an ordinary value here: a caption clip carries text instead."""
    if stored is None:
        return None
    try:
        return MaterialId.parse(stored)
    except InvalidResourceId:
        raise InvalidTimelineModel from None


def _all_documents(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _tracks(stored: object) -> object:
    """Rebuild the tree, converting only what is already the right shape.

    Measured against PostgreSQL 18.4 with asyncpg 0.31.0: SQLAlchemy's asyncpg
    dialect registers a JSON codec, so a JSONB column arrives already parsed all
    the way down -- a JSON array as `list`, an object as `dict`, never as text.
    The domain declares tuples of dataclasses, so something has to convert.

    Anything that is not already a list of objects is handed on untouched, for
    the same reason the timestamp guard runs before it normalises. Converting
    first would raise a bare `TypeError` from inside this module on a number,
    and iterating a `dict` would quietly hand back a tuple of its keys -- the
    first is neither the domain's error nor one of this module's, and the second
    is a plausible-looking value built out of nonsense. Handed on, the value
    reaches `Timeline.__post_init__`, which refuses it as what it is: a row that
    is not a timeline.
    """
    if not _all_documents(stored):
        return stored
    return tuple(_track(track) for track in cast("list[dict[str, object]]", stored))


def _track(document: dict[str, object]) -> object:
    """One lane, or the raw document when it is not one.

    The key set has to match the domain's fields exactly. Missing keys would
    otherwise be read as `None` and extra keys ignored, and both are silent: a
    document spelling `clips` differently would hydrate into a lane holding
    nothing, and one carrying a field this code has never heard of comes from a
    shape it cannot claim to understand. Refusing is the only answer that does
    not invent what was meant.
    """
    if document.keys() != _TRACK_KEYS or not _all_documents(document["clips"]):
        return document
    return TimelineTrack(
        track_id=cast(str, document["track_id"]),
        kind=_enumeration_member(TimelineTrackKind, document["kind"]),
        clips=cast(
            "tuple[TimelineClip, ...]",
            tuple(_clip(clip) for clip in cast("list[dict[str, object]]", document["clips"])),
        ),
    )


def _clip(document: dict[str, object]) -> object:
    """One stretch of one lane, or the raw document when it is not one.

    This is where a dropped key does the most damage: `None` is a legal value
    for four of these nine fields, so a misspelt `gain_db` or `transition_in`
    would hydrate into an object the domain happily accepts and that quietly
    means something other than what was stored. Hence the exact key set, and
    hence subscripting rather than `.get` below -- once the keys match, a
    missing one is impossible rather than merely unlikely.
    """
    if document.keys() != _CLIP_KEYS:
        return document
    return TimelineClip(
        clip_id=cast(str, document["clip_id"]),
        start_ms=cast(int, document["start_ms"]),
        duration_ms=cast(int, document["duration_ms"]),
        source_material_id=_material_id(document["source_material_id"]),
        source_in_ms=cast("int | None", document["source_in_ms"]),
        source_out_ms=cast("int | None", document["source_out_ms"]),
        text=cast("str | None", document["text"]),
        gain_db=cast("float | None", document["gain_db"]),
        transition_in=cast("TimelineTransition | None", _transition(document["transition_in"])),
    )


def _transition(stored: object) -> object:
    """A hard cut is `None` -- the absence of a transition, not a kind of one.

    The domain deliberately has no `CUT` member, so `None` here is the ordinary
    case rather than a missing value, and it must survive untouched.
    """
    if stored is None:
        return None
    if not isinstance(stored, dict) or stored.keys() != _TRANSITION_KEYS:
        return stored
    return TimelineTransition(
        kind=_enumeration_member(TransitionKind, stored["kind"]),
        duration_ms=cast(int, stored["duration_ms"]),
    )


def _hydrate(row: RowMapping) -> Timeline:
    """Rebuild a timeline by constructing it, so a stored row is re-validated.

    Nothing in the table stops a row the domain would refuse -- least of all in
    `tracks`, which PostgreSQL does not inspect at any depth -- and rows arrive
    from migrations, fixtures and hand-run statements as well as from `save`.
    Going through the constructors makes every one of them meet the rules a
    caller meets, including the ones no single clip and no single track can
    check alone: that a picture lane has no gap in it, that a transition does
    not overlap more of the outgoing clip than an earlier transition has left of
    it, and that the declared length is where the picture lane really ends.
    `InvalidTimelineModel` then propagates rather than being translated: a row
    the domain rejects is bad data, not a repository failure, and the caller has
    to be able to tell those apart.

    `InvalidResourceId` folds into that same error rather than surfacing on its
    own -- the `uuid` columns accept every version, so a non-v4 identifier is one
    more way for a stored row to be unusable, and no caller should have to catch
    two exceptions to mean "this row is not a timeline".
    """
    try:
        timeline_id = TimelineId.parse(row["timeline_id"])
        project_id = EditingProjectId.parse(row["project_id"])
    except InvalidResourceId:
        raise InvalidTimelineModel from None
    return Timeline(
        timeline_id=timeline_id,
        project_id=project_id,
        revision=cast(int, row["revision"]),
        duration_ms=cast(int, row["duration_ms"]),
        tracks=cast("tuple[TimelineTrack, ...]", _tracks(row["tracks"])),
        created_at=cast(datetime, normalise_timestamp(row["created_at"])),
    )


def _transition_document(transition: TimelineTransition) -> dict[str, object]:
    return {"kind": transition.kind.value, "duration_ms": transition.duration_ms}


def _clip_document(clip: TimelineClip) -> dict[str, object]:
    """One clip as JSON, spelled with the field names the reader requires.

    `gain_db` goes in as the `float` the domain declares and must come back as
    one: JSON keeps `-6.0` and `-6` apart, PostgreSQL hands the second back as
    an `int`, and `TimelineClip` refuses an `int` level outright. So a level
    that happens to be a whole number must not be normalised on the way in --
    that would store a row nothing could ever load again.
    """
    return {
        "clip_id": clip.clip_id,
        "start_ms": clip.start_ms,
        "duration_ms": clip.duration_ms,
        "source_material_id": (
            None if clip.source_material_id is None else str(clip.source_material_id)
        ),
        "source_in_ms": clip.source_in_ms,
        "source_out_ms": clip.source_out_ms,
        "text": clip.text,
        "gain_db": clip.gain_db,
        "transition_in": (
            None if clip.transition_in is None else _transition_document(clip.transition_in)
        ),
    }


def _track_document(track: TimelineTrack) -> dict[str, object]:
    return {
        "track_id": track.track_id,
        "kind": track.kind.value,
        "clips": [_clip_document(clip) for clip in track.clips],
    }


def _column_values(timeline: Timeline) -> dict[str, object]:
    """The full row, with the domain's tree spelled as the JSON it is stored as."""
    return {
        "timeline_id": timeline.timeline_id.uuid,
        "revision": timeline.revision,
        "project_id": timeline.project_id.uuid,
        "duration_ms": timeline.duration_ms,
        "tracks": [_track_document(track) for track in timeline.tracks],
        "created_at": timeline.created_at,
    }


class SqlAlchemyTimelineRepository:
    """Write-once revision rows: a stored revision is never rewritten."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise TimelinePersistenceUnavailable
        self._database = database

    async def save(self, timeline: Timeline) -> None:
        """Insert one revision, leaving any existing row untouched.

        There is no lookup before the insert -- not for the revision and not for
        the project. That would let two callers both find nothing and both
        proceed, which is the same defect one level down. The primary key and
        the foreign key are what refuse the second one, and they refuse it
        whoever is racing.

        The row is built *before* the `try`, for the same reason `_row` hydrates
        after its own: building it is not database work, and a catch-all that
        covered it would report a broken serialiser as an unavailable database
        -- telling the caller to retry something no retry can fix. Nothing can
        raise there today, since every value it produces is a JSON native taken
        from an already-validated timeline; keeping the statement outside costs
        one line and stops a field added later from quietly landing inside.
        """
        if not isinstance(timeline, Timeline):
            raise TimelineDataRejected
        values = _column_values(timeline)
        try:
            async with self._database.session() as session:
                await session.execute(insert(timelines).values(**values))
        except IntegrityError as error:
            _refuse_integrity_violation(error)
        except _CONNECTION_FAILURES:
            raise TimelinePersistenceUnavailable from None
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
            # reach the caller verbatim. The same tail guards `_row`.
            raise TimelinePersistenceUnavailable from None

    async def get(self, timeline_id: TimelineId, revision: int) -> Timeline:
        """One exact revision, or `TimelineNotFound`.

        `revision` is checked with `type(...) is not int` rather than
        `isinstance`, because `True` is an `int` in Python and would otherwise
        be looked up as revision 1. The domain spells its own integer checks the
        same way. Whether the number is *in range* is not checked here: the
        domain owns that, and a revision below 1 correctly finds nothing --
        exactly as a well-formed revision nobody stored does.
        """
        if not isinstance(timeline_id, TimelineId) or type(revision) is not int:
            raise TimelineDataRejected
        row = await self._row(
            select(timelines).where(
                timelines.c.timeline_id == timeline_id.uuid,
                timelines.c.revision == revision,
            )
        )
        if row is None:
            raise TimelineNotFound
        return _hydrate(row)

    async def latest_revision(self, timeline_id: TimelineId) -> Timeline | None:
        """The highest revision of one timeline, or `None` if it has none yet.

        `None` is an answer rather than a failure -- "this timeline has no
        revisions" is a thing a caller needs to be able to act on, and raising
        would make it indistinguishable from a database that is down.

        The ordering is done by PostgreSQL and only one row comes back. Reading
        every revision and picking the largest in Python would work today and
        cost more with every revision stored, and it would be the same shape of
        mistake as looking a row up before inserting it.
        """
        if not isinstance(timeline_id, TimelineId):
            raise TimelineDataRejected
        row = await self._row(
            select(timelines)
            .where(timelines.c.timeline_id == timeline_id.uuid)
            .order_by(timelines.c.revision.desc())
            .limit(1)
        )
        return None if row is None else _hydrate(row)

    async def _row(self, statement: Select[Any]) -> RowMapping | None:
        """Read at most one row, with hydration deliberately left to the caller.

        Hydrating in here would put `Timeline.__post_init__` inside the `try`,
        where the catch-all tail would swallow a domain rejection and report it
        as an unavailable database -- turning "this stored row is broken" into
        "try again later", which is both wrong and unfixable by retrying.
        """
        try:
            async with self._database.session() as session:
                return (await session.execute(statement)).mappings().one_or_none()
        except _CONNECTION_FAILURES:
            raise TimelinePersistenceUnavailable from None
        except Exception:
            raise TimelinePersistenceUnavailable from None


__all__ = ["SqlAlchemyTimelineRepository"]
