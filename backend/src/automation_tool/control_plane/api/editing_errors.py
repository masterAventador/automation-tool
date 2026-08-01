"""Exhaustive HTTP dispositions for the local-editing persistence vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.application.editing_jobs import (
    EditingJobAlreadyRegistered,
    EditingJobNotFound,
    EditingJobPersistenceUnavailable,
    EditingJobRevisionAlreadyQueued,
    EditingJobStale,
    EditingJobTimelineRevisionMissing,
    EditingJobTransitionConflict,
)
from automation_tool.control_plane.application.editing_projects import (
    EditingProjectAlreadyRegistered,
    EditingProjectNotFound,
    EditingProjectPersistenceUnavailable,
)
from automation_tool.control_plane.application.materials import (
    MaterialAlreadyRegistered,
    MaterialDescriptionProtected,
    MaterialInUse,
    MaterialNotFound,
    MaterialPersistenceUnavailable,
)
from automation_tool.control_plane.application.timelines import (
    TimelineMaterialMissing,
    TimelineNotFound,
    TimelinePersistenceUnavailable,
    TimelineProjectMissing,
    TimelineRevisionAlreadyStored,
)


@dataclass(frozen=True)
class _PublicFailure:
    status_code: int
    code: str
    retryable: bool = False


_PUBLIC_FAILURES: Final[dict[type[Exception], _PublicFailure]] = {
    EditingProjectAlreadyRegistered: _PublicFailure(409, "editing_project_already_registered"),
    EditingProjectNotFound: _PublicFailure(404, "editing_project_not_found"),
    EditingProjectPersistenceUnavailable: _PublicFailure(
        503, "editing_project_persistence_unavailable", True
    ),
    MaterialAlreadyRegistered: _PublicFailure(409, "material_already_registered"),
    MaterialNotFound: _PublicFailure(404, "material_not_found"),
    MaterialDescriptionProtected: _PublicFailure(409, "material_description_protected"),
    MaterialInUse: _PublicFailure(409, "material_in_use"),
    MaterialPersistenceUnavailable: _PublicFailure(503, "material_persistence_unavailable", True),
    TimelineRevisionAlreadyStored: _PublicFailure(409, "timeline_revision_already_stored"),
    TimelineMaterialMissing: _PublicFailure(409, "timeline_material_missing"),
    TimelineProjectMissing: _PublicFailure(409, "timeline_project_missing"),
    TimelineNotFound: _PublicFailure(404, "timeline_not_found"),
    TimelinePersistenceUnavailable: _PublicFailure(503, "timeline_persistence_unavailable", True),
    EditingJobAlreadyRegistered: _PublicFailure(409, "editing_job_already_registered"),
    EditingJobRevisionAlreadyQueued: _PublicFailure(409, "editing_job_revision_already_queued"),
    EditingJobTimelineRevisionMissing: _PublicFailure(409, "editing_job_timeline_revision_missing"),
    EditingJobNotFound: _PublicFailure(404, "editing_job_not_found"),
    EditingJobStale: _PublicFailure(409, "editing_job_stale"),
    EditingJobTransitionConflict: _PublicFailure(409, "editing_job_conflict"),
    EditingJobPersistenceUnavailable: _PublicFailure(
        503, "editing_job_persistence_unavailable", True
    ),
}


def translate_editing_error(error: Exception) -> AppError:
    """Return a safe public error, or preserve failures that belong to the 500 handler.

    Exact types are deliberate. A new subclass is a new vocabulary member and
    must fail the dynamic guard until its caller action is decided; inheriting
    the nearest status by accident would make that decision invisible.

    `DataRejected`, invalid stored models, and unknown errors are not wrapped
    in `AppError(500)`. Reraising lets the global unexpected-error handler log
    the incident before it emits its fixed, non-reflective internal envelope.
    """

    public = _PUBLIC_FAILURES.get(type(error))
    if public is None:
        raise error
    return AppError(
        status_code=public.status_code,
        code=public.code,
        message=str(error),
        retryable=public.retryable,
    )


__all__ = ["translate_editing_error"]
