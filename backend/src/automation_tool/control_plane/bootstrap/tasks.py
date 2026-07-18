"""Runtime wiring for Installation-scoped Task creation."""

from datetime import UTC, datetime

from automation_tool.control_plane.application.tasks import TaskCreationService
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.task_repository import (
    SqlAlchemyTaskRepository,
)


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def task_creation_service(database: Database) -> TaskCreationService:
    return TaskCreationService(
        repository=SqlAlchemyTaskRepository(database),
        clock=_SystemClock(),
    )


__all__ = ["task_creation_service"]
