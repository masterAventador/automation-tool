"""Runtime wiring for the target preview service."""

from automation_tool.control_plane.application.task_target_previews import (
    TaskTargetPreviewService,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.task_target_preview_repository import (
    SqlAlchemyTaskTargetPreviewRepository,
)


def task_target_preview_service(database: Database) -> TaskTargetPreviewService:
    return TaskTargetPreviewService(
        repository=SqlAlchemyTaskTargetPreviewRepository(database),
    )


__all__ = ["task_target_preview_service"]
