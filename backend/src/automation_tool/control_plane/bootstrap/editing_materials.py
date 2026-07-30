"""Runtime wiring for local-editing material registration and queries."""

from automation_tool.control_plane.application.materials import MaterialService
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyMaterialRepository,
)


def material_service(database: Database) -> MaterialService:
    return MaterialService(repository=SqlAlchemyMaterialRepository(database))


__all__ = ["material_service"]
