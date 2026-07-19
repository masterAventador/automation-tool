"""Runtime wiring for platform Session health convergence."""

from automation_tool.control_plane.application.platform_session_health import (
    PlatformSessionHealthService,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyPlatformSessionHealthRepository,
)


def platform_session_health_service(database: Database) -> PlatformSessionHealthService:
    return PlatformSessionHealthService(
        repository=SqlAlchemyPlatformSessionHealthRepository(database),
    )


__all__ = ["platform_session_health_service"]
