"""The timeline persistence boundary's failure vocabulary.

Five outcomes, because a caller has five different things to do about them. A
revision that is already stored means this cut exists and the caller has to pick
a new revision -- revisions are immutable snapshots, so there is nothing to
merge into. A project that is not stored means the *other* resource is missing,
which is a different thing to report and a different thing to fix. A missing
timeline is a question that got an answer. An unavailable database is worth
retrying. A rejected argument is a bug upstairs. Collapsing them makes every
caller guess, and leaves the REST layer above answering 409, 404 and 503 with
the same status.

Every message is a fixed string and no constructor takes an argument, so nothing
reaching a log through one of these can carry a connection string, an identifier
or a private path -- and `raise ... ("detail")` is a `TypeError` at the call
site rather than a leak. That matters more here than it looks: PostgreSQL's
DETAIL line on both of the integrity failures below quotes the offending key
values verbatim, so the tempting thing to attach is exactly the thing that must
not travel.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from automation_tool.control_plane.domain import (
    EditingProjectId,
    InstallationId,
    InvalidResourceId,
    InvalidTimelineModel,
    Timeline,
    TimelineId,
    TimelineTrack,
)


class _TimelinePersistenceFailure(RuntimeError):
    message = "Timeline persistence failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class TimelineRevisionAlreadyStored(_TimelinePersistenceFailure):
    """This `(timeline_id, revision)` is taken, and a revision is write-once.

    Deliberately not phrased as "already registered" like the two tables before
    it: what is taken is one revision, not the timeline. Storing revision 4 of a
    timeline whose revision 3 exists is the ordinary case, not a conflict.
    """

    message = "Timeline revision is already stored"


class TimelineProjectMissing(_TimelinePersistenceFailure):
    """The project this timeline belongs to is not in the database.

    A caller error rather than a corrupt row: the timeline itself is well
    formed, and `EditingProjectId` cannot tell whether a project was ever
    stored -- no domain object holds a reference to another aggregate, which is
    why this can only be caught here. Retrying with the same timeline never
    succeeds; storing the project first does.
    """

    message = "Timeline names a project that is not stored"


class TimelineNotFound(_TimelinePersistenceFailure):
    message = "Timeline revision was not found"


class TimelineDataRejected(_TimelinePersistenceFailure):
    """The argument, or the row it would have written, was refused.

    Covers both a caller handing over something that is not a `Timeline` and a
    constraint violation this module does not have a specific answer for. The
    two share what a caller must do: fix the input. Neither improves on a retry,
    which is what separates them from `TimelinePersistenceUnavailable`.
    """

    message = "Timeline data is rejected"


class TimelinePersistenceUnavailable(_TimelinePersistenceFailure):
    message = "Timeline persistence is unavailable"


class InvalidTimelineQuery(ValueError):
    def __init__(self) -> None:
        super().__init__("Timeline query is invalid")


class TimelineRevisionConflict(RuntimeError):
    """A safe service-level conflict carrying only the latest revision number."""

    def __init__(self, current_revision: int) -> None:
        if type(current_revision) is not int or current_revision < 1:
            raise ValueError("Timeline conflict revision is invalid")
        super().__init__("Timeline revision conflicts")
        self.current_revision = current_revision


class TimelineRepository(Protocol):
    async def save(
        self,
        timeline: Timeline,
        installation_id: InstallationId,
    ) -> None: ...

    async def get(
        self,
        timeline_id: TimelineId,
        revision: int,
        installation_id: InstallationId,
    ) -> Timeline: ...

    async def latest_revision(
        self,
        project_id: EditingProjectId,
        installation_id: InstallationId,
    ) -> Timeline | None: ...


class TimelineService:
    def __init__(
        self,
        *,
        repository: TimelineRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    @staticmethod
    def _project_id(value: str) -> EditingProjectId:
        try:
            return EditingProjectId.parse(value)
        except InvalidResourceId:
            raise InvalidTimelineQuery from None

    async def get(
        self,
        *,
        project_id: str,
        installation_id: InstallationId,
    ) -> Timeline:
        if not isinstance(installation_id, InstallationId):
            raise InvalidTimelineQuery
        latest = await self._repository.latest_revision(
            self._project_id(project_id),
            installation_id,
        )
        if latest is None:
            raise TimelineNotFound
        return latest

    async def save(
        self,
        *,
        project_id: str,
        installation_id: InstallationId,
        duration_ms: int,
        tracks: tuple[TimelineTrack, ...],
    ) -> Timeline:
        if not isinstance(installation_id, InstallationId):
            raise InvalidTimelineQuery
        parsed_project_id = self._project_id(project_id)
        latest = await self._repository.latest_revision(
            parsed_project_id,
            installation_id,
        )
        try:
            timeline = Timeline(
                timeline_id=TimelineId.new() if latest is None else latest.timeline_id,
                project_id=parsed_project_id,
                revision=1 if latest is None else latest.revision + 1,
                duration_ms=duration_ms,
                tracks=tracks,
                created_at=self._clock(),
            )
        except InvalidTimelineModel:
            raise InvalidTimelineQuery from None
        try:
            await self._repository.save(timeline, installation_id)
        except TimelineRevisionAlreadyStored:
            current = await self._repository.latest_revision(
                parsed_project_id,
                installation_id,
            )
            if current is None:
                raise TimelinePersistenceUnavailable from None
            raise TimelineRevisionConflict(current.revision) from None
        return timeline


__all__ = [
    "InvalidTimelineQuery",
    "TimelineDataRejected",
    "TimelineNotFound",
    "TimelinePersistenceUnavailable",
    "TimelineProjectMissing",
    "TimelineRepository",
    "TimelineRevisionAlreadyStored",
    "TimelineRevisionConflict",
    "TimelineService",
]
