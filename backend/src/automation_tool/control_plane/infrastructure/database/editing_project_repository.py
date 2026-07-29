"""PostgreSQL storage for editing projects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.editing_projects import (
    EditingProjectRepositoryRejected,
)
from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    OutputSpec,
)

from .schema import editing_projects
from .session import Database

# A refused or timed-out connection surfaces as an `OSError`, not a
# `SQLAlchemyError`: it comes out of asyncio's connect call, and the asyncpg
# dialect only wraps asyncpg's own exceptions. `session.py` catches the same
# pair for the same reason. Catching one without the other lets a raw socket
# error, carrying host and port, reach the caller.
_DATABASE_FAILURES = (OSError, SQLAlchemyError)


def _hydrate(row: RowMapping) -> EditingProject:
    """Rebuild a project by constructing it, so a stored row is re-validated.

    Nothing in the table stops a row that the domain would refuse, and rows can
    arrive from a migration, a fixture or a hand-run statement. Going through
    the constructor means every one of those has to satisfy the same rules a
    caller does; `InvalidEditingProjectModel` propagates rather than being
    translated, because a row the domain rejects is not a repository failure.
    """
    return EditingProject(
        project_id=EditingProjectId.parse(row["project_id"]),
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
        # The domain accepts any zero-offset timezone, so it is not what makes
        # the loaded timestamp UTC. Normalising here is.
        created_at=cast(datetime, row["created_at"]).astimezone(UTC),
    )


class SqlAlchemyEditingProjectRepository:
    """Write-once project rows: a repeated identifier is refused, never merged."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise EditingProjectRepositoryRejected
        self._database = database

    async def save(self, project: EditingProject) -> None:
        """Insert one project, leaving any existing row untouched.

        There is no lookup before the insert: that would let two callers both
        find nothing and both proceed. The primary key is what refuses the
        second one, and it refuses it whoever is racing.
        """
        if not isinstance(project, EditingProject):
            raise EditingProjectRepositoryRejected
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
        except _DATABASE_FAILURES:
            raise EditingProjectRepositoryRejected from None

    async def get(self, project_id: EditingProjectId) -> EditingProject:
        if not isinstance(project_id, EditingProjectId):
            raise EditingProjectRepositoryRejected
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
        except _DATABASE_FAILURES:
            raise EditingProjectRepositoryRejected from None
        if row is None:
            raise EditingProjectRepositoryRejected
        return _hydrate(row)


__all__ = ["SqlAlchemyEditingProjectRepository"]
