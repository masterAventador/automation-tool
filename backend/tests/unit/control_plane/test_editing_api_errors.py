"""The editing API has one exhaustive, fail-closed persistence-error vocabulary."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Final, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from automation_tool.control_plane.api.errors import AppError, register_error_handlers
from automation_tool.control_plane.application import (
    editing_jobs,
    editing_projects,
    materials,
    timelines,
)
from automation_tool.control_plane.application.editing_jobs import (
    EditingJobAlreadyRegistered,
    EditingJobDataRejected,
    EditingJobNotFound,
    EditingJobPersistenceUnavailable,
    EditingJobRevisionAlreadyQueued,
    EditingJobStale,
    EditingJobTimelineRevisionMissing,
    EditingJobTransitionConflict,
)
from automation_tool.control_plane.application.editing_projects import (
    EditingProjectAlreadyRegistered,
    EditingProjectDataRejected,
    EditingProjectNotFound,
    EditingProjectPersistenceUnavailable,
)
from automation_tool.control_plane.application.materials import (
    MaterialAlreadyRegistered,
    MaterialDataRejected,
    MaterialDescriptionProtected,
    MaterialInUse,
    MaterialNotFound,
    MaterialPersistenceUnavailable,
    MaterialSnapshotConflict,
)
from automation_tool.control_plane.application.timelines import (
    TimelineDataRejected,
    TimelineMaterialMissing,
    TimelineNotFound,
    TimelinePersistenceUnavailable,
    TimelineProjectMissing,
    TimelineRevisionAlreadyStored,
)
from automation_tool.control_plane.domain import (
    InvalidEditingJobModel,
    InvalidEditingProjectModel,
    InvalidMaterialModel,
    InvalidTimelineModel,
)


@dataclass(frozen=True)
class ExpectedPublicFailure:
    status_code: int
    code: str
    retryable: bool = False


PUBLIC_FAILURES: Final = {
    EditingProjectAlreadyRegistered: ExpectedPublicFailure(
        409, "editing_project_already_registered"
    ),
    EditingProjectNotFound: ExpectedPublicFailure(404, "editing_project_not_found"),
    EditingProjectPersistenceUnavailable: ExpectedPublicFailure(
        503, "editing_project_persistence_unavailable", True
    ),
    MaterialAlreadyRegistered: ExpectedPublicFailure(409, "material_already_registered"),
    MaterialNotFound: ExpectedPublicFailure(404, "material_not_found"),
    MaterialDescriptionProtected: ExpectedPublicFailure(409, "material_description_protected"),
    MaterialInUse: ExpectedPublicFailure(409, "material_in_use"),
    MaterialPersistenceUnavailable: ExpectedPublicFailure(
        503, "material_persistence_unavailable", True
    ),
    MaterialSnapshotConflict: ExpectedPublicFailure(409, "material_snapshot_conflict"),
    TimelineRevisionAlreadyStored: ExpectedPublicFailure(409, "timeline_revision_already_stored"),
    TimelineMaterialMissing: ExpectedPublicFailure(409, "timeline_material_missing"),
    TimelineProjectMissing: ExpectedPublicFailure(409, "timeline_project_missing"),
    TimelineNotFound: ExpectedPublicFailure(404, "timeline_not_found"),
    TimelinePersistenceUnavailable: ExpectedPublicFailure(
        503, "timeline_persistence_unavailable", True
    ),
    EditingJobAlreadyRegistered: ExpectedPublicFailure(409, "editing_job_already_registered"),
    EditingJobRevisionAlreadyQueued: ExpectedPublicFailure(
        409, "editing_job_revision_already_queued"
    ),
    EditingJobTimelineRevisionMissing: ExpectedPublicFailure(
        409, "editing_job_timeline_revision_missing"
    ),
    EditingJobNotFound: ExpectedPublicFailure(404, "editing_job_not_found"),
    EditingJobStale: ExpectedPublicFailure(409, "editing_job_stale"),
    EditingJobTransitionConflict: ExpectedPublicFailure(409, "editing_job_conflict"),
    EditingJobPersistenceUnavailable: ExpectedPublicFailure(
        503, "editing_job_persistence_unavailable", True
    ),
}

INTERNAL_PERSISTENCE_FAILURES: Final = {
    EditingProjectDataRejected,
    MaterialDataRejected,
    TimelineDataRejected,
    EditingJobDataRejected,
}

INVALID_STORED_MODELS: Final = (
    InvalidEditingProjectModel,
    InvalidMaterialModel,
    InvalidTimelineModel,
    InvalidEditingJobModel,
)

PERSISTENCE_MODULES: Final = (
    editing_projects,
    materials,
    timelines,
    editing_jobs,
)


def translate(error: Exception) -> AppError:
    module = importlib.import_module("automation_tool.control_plane.api.editing_errors")
    translator: Callable[[Exception], AppError] = module.translate_editing_error
    return translator(error)


def persistence_failures(module: ModuleType) -> set[type[Exception]]:
    base = cast(
        type[Exception],
        next(
            member
            for name, member in inspect.getmembers(module, inspect.isclass)
            if name.startswith("_")
            and name.endswith("PersistenceFailure")
            and member.__module__ == module.__name__
        ),
    )
    return {
        member
        for _, member in inspect.getmembers(module, inspect.isclass)
        if member is not base and member.__module__ == module.__name__ and issubclass(member, base)
    }


def test_every_persistence_failure_has_one_explicit_disposition() -> None:
    discovered = set().union(*(persistence_failures(module) for module in PERSISTENCE_MODULES))

    assert discovered == set(PUBLIC_FAILURES) | INTERNAL_PERSISTENCE_FAILURES

    for failure, expected in PUBLIC_FAILURES.items():
        source = failure()
        translated = translate(source)

        assert translated.status_code == expected.status_code
        assert translated.code == expected.code
        assert translated.message == str(source)
        assert translated.retryable is expected.retryable

    for failure in INTERNAL_PERSISTENCE_FAILURES:
        source = failure()
        with pytest.raises(failure) as captured:
            translate(source)
        assert captured.value is source


@pytest.mark.parametrize("failure", INVALID_STORED_MODELS)
def test_an_invalid_stored_model_uses_the_logged_internal_error_envelope(
    failure: type[Exception],
) -> None:
    app = FastAPI()
    register_error_handlers(app)

    async def fail() -> None:
        translate(failure())

    app.add_api_route("/failure", fail, methods=["GET"])

    response = TestClient(app, raise_server_exceptions=False).get("/failure")

    assert response.status_code == 500
    assert set(response.json()) == {"error"}
    assert response.json()["error"] == {
        "code": "internal",
        "message": "Internal server error",
        "retryable": False,
        "requestId": response.headers["x-request-id"],
    }
    assert UUID(response.headers["x-request-id"])
    assert str(failure()) not in response.text


@pytest.mark.parametrize("failure", INVALID_STORED_MODELS)
def test_an_invalid_stored_model_is_preserved_for_the_unexpected_handler(
    failure: type[Exception],
) -> None:
    source = failure()

    with pytest.raises(failure) as captured:
        translate(source)

    assert captured.value is source


def test_an_unknown_error_is_not_guessed_from_its_name() -> None:
    class EditingProjectSurprise(RuntimeError):
        pass

    source = EditingProjectSurprise("private")

    with pytest.raises(EditingProjectSurprise) as captured:
        translate(source)

    assert captured.value is source
