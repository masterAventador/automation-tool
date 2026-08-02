"""Runtime wiring for local-editing job submission and queries."""

from datetime import UTC, datetime

from automation_tool.control_plane.application.editing_jobs import EditingJobService
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyEditingJobRepository,
    SqlAlchemyTimelineRepository,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def editing_job_service(database: Database) -> EditingJobService:
    return EditingJobService(
        repository=SqlAlchemyEditingJobRepository(database),
        timelines=SqlAlchemyTimelineRepository(database),
        clock=_utc_now,
    )


__all__ = ["editing_job_service"]
