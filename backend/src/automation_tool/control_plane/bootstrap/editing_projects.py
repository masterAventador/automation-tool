"""Runtime wiring for local-editing project creation and queries."""

from datetime import UTC, datetime

from automation_tool.control_plane.application.editing_projects import EditingProjectService
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyEditingProjectRepository,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def editing_project_service(database: Database) -> EditingProjectService:
    return EditingProjectService(
        repository=SqlAlchemyEditingProjectRepository(database),
        clock=_utc_now,
    )


__all__ = ["editing_project_service"]
