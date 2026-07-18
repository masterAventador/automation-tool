"""Runtime wiring for persistent Executor command delivery."""

from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandDeliveryService,
)
from automation_tool.control_plane.application.task_controls import TaskControlService
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
)


def task_command_delivery_service(
    database: Database,
    registry: ExecutorConnectionRegistry,
) -> TaskCommandDeliveryService:
    return TaskCommandDeliveryService(
        repository=SqlAlchemyTaskCommandRepository(database),
        registry=registry,
    )


def task_control_service(database: Database) -> TaskControlService:
    return TaskControlService(repository=SqlAlchemyTaskCommandRepository(database))


__all__ = ["task_command_delivery_service", "task_control_service"]
