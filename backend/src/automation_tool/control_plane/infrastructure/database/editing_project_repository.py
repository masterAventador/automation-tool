"""PostgreSQL storage for editing projects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.editing_projects import (
    EditingProjectAlreadyRegistered,
    EditingProjectDataRejected,
    EditingProjectNotFound,
    EditingProjectPersistenceUnavailable,
)
from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    InvalidEditingProjectModel,
    InvalidResourceId,
    OutputSpec,
)

from .schema import editing_projects
from .session import Database

# A refused or timed-out connection surfaces as an `OSError`, not a
# `SQLAlchemyError`: it comes out of asyncio's connect call, and the asyncpg
# dialect only wraps asyncpg's own exceptions. `session.py` and four other
# repositories catch the same pair for the same reason.
_CONNECTION_FAILURES = (OSError, SQLAlchemyError)


def _timestamp(value: object) -> object:
    """Normalise an already-valid timestamp; hand anything else on untouched.

    The order matters. `.astimezone(UTC)` on a *naive* datetime does not fail --
    it reinterprets it in the host's local timezone, moves the instant by that
    offset and hands back something aware, which then sails through the domain's
    check. Normalising before validating would launder exactly the value the
    domain exists to refuse. `None` and text are worse: they would raise a bare
    `AttributeError` from inside the repository, which is neither the domain's
    error nor one of this module's. So the guard runs first, and only a
    timestamp that is already aware gets converted.
    """
    if isinstance(value, datetime) and value.utcoffset() is not None:
        return value.astimezone(UTC)
    return value


def _hydrate(row: RowMapping) -> EditingProject:
    """Rebuild a project by constructing it, so a stored row is re-validated.

    Nothing in the table stops a row the domain would refuse, and rows arrive
    from migrations, fixtures and hand-run statements as well as from `save`.
    Going through the constructor makes every one of them meet the rules a
    caller meets. `InvalidEditingProjectModel` then propagates rather than being
    translated: a row the domain rejects is bad data, not a repository failure,
    and the caller has to be able to tell those apart.

    `InvalidResourceId` folds into that same error rather than surfacing on its
    own -- the `uuid` column accepts every version, so a non-v4 identifier is
    one more way for a stored row to be unusable, and no caller should have to
    catch two exceptions to mean "this row is not a project".
    """
    try:
        project_id = EditingProjectId.parse(row["project_id"])
    except InvalidResourceId:
        raise InvalidEditingProjectModel from None
    return EditingProject(
        project_id=project_id,
        title=cast(str, row["title"]),
        output=OutputSpec(
            width=cast(int, row["output_width"]),
            height=cast(int, row["output_height"]),
            fps=cast(int, row["output_fps"]),
        ),
        caption_style=CaptionStyle(
            font_key=cast(str, row["caption_font_key"]),
            font_px=cast(int, row["caption_font_px"]),
            stroke_px=cast(int, row["caption_stroke_px"]),
            line_spacing=cast(float, row["caption_line_spacing"]),
        ),
        created_at=cast(datetime, _timestamp(row["created_at"])),
    )


class SqlAlchemyEditingProjectRepository:
    """Write-once project rows: a repeated identifier is refused, never merged."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise EditingProjectPersistenceUnavailable
        self._database = database

    async def save(self, project: EditingProject) -> None:
        """Insert one project, leaving any existing row untouched.

        There is no lookup before the insert: that would let two callers both
        find nothing and both proceed. The primary key is what refuses the
        second one, and it refuses it whoever is racing.
        """
        if not isinstance(project, EditingProject):
            raise EditingProjectDataRejected
        try:
            async with self._database.session() as session:
                await session.execute(
                    insert(editing_projects).values(
                        project_id=project.project_id.uuid,
                        title=project.title,
                        output_width=project.output.width,
                        output_height=project.output.height,
                        output_fps=project.output.fps,
                        caption_font_key=project.caption_style.font_key,
                        caption_font_px=project.caption_style.font_px,
                        caption_stroke_px=project.caption_style.stroke_px,
                        caption_line_spacing=project.caption_style.line_spacing,
                        created_at=project.created_at,
                    )
                )
        except IntegrityError:
            raise EditingProjectAlreadyRegistered from None
        except _CONNECTION_FAILURES:
            raise EditingProjectPersistenceUnavailable from None
        except Exception:
            raise EditingProjectPersistenceUnavailable from None

    async def get(self, project_id: EditingProjectId) -> EditingProject:
        if not isinstance(project_id, EditingProjectId):
            raise EditingProjectDataRejected
        try:
            async with self._database.session() as session:
                row = (
                    (
                        await session.execute(
                            select(editing_projects).where(
                                editing_projects.c.project_id == project_id.uuid
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except _CONNECTION_FAILURES:
            raise EditingProjectPersistenceUnavailable from None
        except Exception:
            # Authentication and authorisation failures are neither of the
            # above: `InvalidPasswordError`, `InsufficientPrivilegeError`,
            # `InvalidCatalogNameError` and `TooManyConnectionsError` derive from
            # `PostgresError` and `Exception` only, and their messages name the
            # role and the database. Without this tail they reach the caller
            # verbatim. The same tail guards `save`.
            raise EditingProjectPersistenceUnavailable from None
        if row is None:
            raise EditingProjectNotFound
        return _hydrate(row)


__all__ = ["SqlAlchemyEditingProjectRepository"]
