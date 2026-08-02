"""Runtime wiring for latest-timeline reads and immutable revision saves."""

from datetime import UTC, datetime

from automation_tool.control_plane.application.timelines import TimelineService
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTimelineRepository,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def timeline_service(database: Database) -> TimelineService:
    return TimelineService(
        repository=SqlAlchemyTimelineRepository(database),
        clock=_utc_now,
    )


__all__ = ["timeline_service"]
