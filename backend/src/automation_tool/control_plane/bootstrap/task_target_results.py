"""Runtime wiring for target-level result projections."""

from automation_tool.control_plane.application.task_target_results import (
    TaskTargetResultService,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.task_target_result_repository import (
    SqlAlchemyTaskTargetResultRepository,
)


def task_target_result_service(database: Database) -> TaskTargetResultService:
    return TaskTargetResultService(repository=SqlAlchemyTaskTargetResultRepository(database))


__all__ = ["task_target_result_service"]
