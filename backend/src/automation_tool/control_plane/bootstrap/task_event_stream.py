"""Runtime wiring for durable Task event streams."""

from automation_tool.control_plane.application.task_event_stream import TaskEventStreamService
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.task_event_stream_repository import (
    SqlAlchemyTaskEventStreamRepository,
)


def task_event_stream_service(database: Database) -> TaskEventStreamService:
    return TaskEventStreamService(repository=SqlAlchemyTaskEventStreamRepository(database))


__all__ = ["task_event_stream_service"]
