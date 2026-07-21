"""Runtime wiring for read-only workbench metrics."""

from automation_tool.control_plane.application.workbench_metrics import WorkbenchMetricsService
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.workbench_metrics_repository import (
    SqlAlchemyWorkbenchMetricsRepository,
)


def workbench_metrics_service(database: Database) -> WorkbenchMetricsService:
    return WorkbenchMetricsService(repository=SqlAlchemyWorkbenchMetricsRepository(database))


__all__ = ["workbench_metrics_service"]
