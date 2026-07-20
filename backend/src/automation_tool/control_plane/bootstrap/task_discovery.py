"""Runtime wiring for bounded target discovery."""

from automation_tool.control_plane.application.task_discovery import (
    TaskDiscoveryConvergenceService,
    TaskDiscoveryStartService,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.task_discovery_repository import (
    SqlAlchemyTaskDiscoveryRepository,
)


def task_discovery_services(
    database: Database,
) -> tuple[TaskDiscoveryStartService, TaskDiscoveryConvergenceService]:
    repository = SqlAlchemyTaskDiscoveryRepository(database)
    return (
        TaskDiscoveryStartService(repository=repository),
        TaskDiscoveryConvergenceService(repository=repository),
    )


__all__ = ["task_discovery_services"]
