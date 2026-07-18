"""Runtime wiring for durable Executor Task event convergence."""

from automation_tool.control_plane.application.task_event_convergence import (
    TaskEventConvergenceService,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskEventConvergenceRepository,
)


def task_event_convergence_service(database: Database) -> TaskEventConvergenceService:
    return TaskEventConvergenceService(
        repository=SqlAlchemyTaskEventConvergenceRepository(database),
    )


__all__ = ["task_event_convergence_service"]
