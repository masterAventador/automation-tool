"""PostgreSQL storage for editing projects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import and_, desc, insert, or_, select
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
    InstallationId,
    InvalidEditingProjectModel,
    InvalidResourceId,
    OutputSpec,
)

from .hydration import normalise_timestamp
from .schema import editing_project_installations, editing_projects
from .session import Database

# A refused or timed-out connection surfaces as an `OSError`, not a
# `SQLAlchemyError`: it comes out of asyncio's connect call, and the asyncpg
# dialect only wraps asyncpg's own exceptions. `session.py` and four other
# repositories catch the same pair for the same reason.
_CONNECTION_FAILURES = (OSError, SQLAlchemyError)


def _column_values(project: EditingProject) -> dict[str, object]:
    return {
        "project_id": project.project_id.uuid,
        "title": project.title,
        "output_width": project.output.width,
        "output_height": project.output.height,
        "output_fps": project.output.fps,
        "caption_font_key": project.caption_style.font_key,
        "caption_font_px": project.caption_style.font_px,
        "caption_stroke_px": project.caption_style.stroke_px,
        "caption_line_spacing": project.caption_style.line_spacing,
        "created_at": project.created_at,
    }


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
        created_at=cast(datetime, normalise_timestamp(row["created_at"])),
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
                await session.execute(insert(editing_projects).values(**_column_values(project)))
        except IntegrityError:
            raise EditingProjectAlreadyRegistered from None
        except _CONNECTION_FAILURES:
            raise EditingProjectPersistenceUnavailable from None
        except Exception:
            raise EditingProjectPersistenceUnavailable from None

    async def save_for_installation(
        self,
        project: EditingProject,
        installation_id: InstallationId,
    ) -> None:
        """Atomically create a project and bind its only owning Installation."""
        if not isinstance(project, EditingProject) or not isinstance(
            installation_id, InstallationId
        ):
            raise EditingProjectDataRejected
        try:
            async with self._database.session() as session:
                await session.execute(insert(editing_projects).values(**_column_values(project)))
                await session.execute(
                    insert(editing_project_installations).values(
                        project_id=project.project_id.uuid,
                        installation_id=installation_id.uuid,
                    )
                )
        except IntegrityError as error:
            constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
            if constraint in {
                "pk_editing_projects",
                "pk_editing_project_installations",
            }:
                raise EditingProjectAlreadyRegistered from None
            raise EditingProjectDataRejected from None
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
            # reach the caller verbatim. The same tail guards `save`.
            raise EditingProjectPersistenceUnavailable from None
        if row is None:
            raise EditingProjectNotFound
        return _hydrate(row)

    async def get_for_installation(
        self,
        project_id: EditingProjectId,
        installation_id: InstallationId,
    ) -> EditingProject:
        if not isinstance(project_id, EditingProjectId) or not isinstance(
            installation_id, InstallationId
        ):
            raise EditingProjectDataRejected
        statement = (
            select(editing_projects)
            .join(
                editing_project_installations,
                editing_project_installations.c.project_id == editing_projects.c.project_id,
            )
            .where(
                editing_projects.c.project_id == project_id.uuid,
                editing_project_installations.c.installation_id == installation_id.uuid,
            )
        )
        try:
            async with self._database.session() as session:
                row = (await session.execute(statement)).mappings().one_or_none()
        except _CONNECTION_FAILURES:
            raise EditingProjectPersistenceUnavailable from None
        except Exception:
            raise EditingProjectPersistenceUnavailable from None
        if row is None:
            raise EditingProjectNotFound
        return _hydrate(row)

    async def list_page(
        self,
        *,
        before_created_at: datetime | None,
        before_project_id: EditingProjectId | None,
        limit: int,
    ) -> tuple[EditingProject, ...]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 101
            or (before_created_at is None) != (before_project_id is None)
        ):
            raise EditingProjectDataRejected
        statement = select(editing_projects)
        if before_created_at is not None and before_project_id is not None:
            if (
                not isinstance(before_created_at, datetime)
                or before_created_at.tzinfo is None
                or before_created_at.utcoffset() != UTC.utcoffset(before_created_at)
                or not isinstance(before_project_id, EditingProjectId)
            ):
                raise EditingProjectDataRejected
            statement = statement.where(
                or_(
                    editing_projects.c.created_at < before_created_at,
                    and_(
                        editing_projects.c.created_at == before_created_at,
                        editing_projects.c.project_id < before_project_id.uuid,
                    ),
                )
            )
        statement = statement.order_by(
            desc(editing_projects.c.created_at),
            desc(editing_projects.c.project_id),
        ).limit(limit)
        try:
            async with self._database.session() as session:
                rows = (await session.execute(statement)).mappings().all()
        except _CONNECTION_FAILURES:
            raise EditingProjectPersistenceUnavailable from None
        except Exception:
            raise EditingProjectPersistenceUnavailable from None
        return tuple(_hydrate(row) for row in rows)

    async def list_page_for_installation(
        self,
        *,
        installation_id: InstallationId,
        before_created_at: datetime | None,
        before_project_id: EditingProjectId | None,
        limit: int,
    ) -> tuple[EditingProject, ...]:
        if (
            not isinstance(installation_id, InstallationId)
            or type(limit) is not int
            or not 1 <= limit <= 101
            or (before_created_at is None) != (before_project_id is None)
        ):
            raise EditingProjectDataRejected
        statement = (
            select(editing_projects)
            .join(
                editing_project_installations,
                editing_project_installations.c.project_id == editing_projects.c.project_id,
            )
            .where(editing_project_installations.c.installation_id == installation_id.uuid)
        )
        if before_created_at is not None and before_project_id is not None:
            if (
                not isinstance(before_created_at, datetime)
                or before_created_at.tzinfo is None
                or before_created_at.utcoffset() != UTC.utcoffset(before_created_at)
                or not isinstance(before_project_id, EditingProjectId)
            ):
                raise EditingProjectDataRejected
            statement = statement.where(
                or_(
                    editing_projects.c.created_at < before_created_at,
                    and_(
                        editing_projects.c.created_at == before_created_at,
                        editing_projects.c.project_id < before_project_id.uuid,
                    ),
                )
            )
        statement = statement.order_by(
            desc(editing_projects.c.created_at),
            desc(editing_projects.c.project_id),
        ).limit(limit)
        try:
            async with self._database.session() as session:
                rows = (await session.execute(statement)).mappings().all()
        except _CONNECTION_FAILURES:
            raise EditingProjectPersistenceUnavailable from None
        except Exception:
            raise EditingProjectPersistenceUnavailable from None
        return tuple(_hydrate(row) for row in rows)


__all__ = ["SqlAlchemyEditingProjectRepository"]
