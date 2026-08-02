from collections.abc import Callable
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import ValidationError

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.errors import (
    AppError,
    TimelineRevisionConflictDetails,
    register_error_handlers,
)
from automation_tool.control_plane.application.executor_connection_registry import (
    ExecutorConnectionRegistry,
)


def assert_error_response(
    response: Response,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
) -> None:
    assert response.status_code == status_code
    assert set(response.json()) == {"error"}
    error = response.json()["error"]
    assert set(error) == {"code", "message", "retryable", "requestId"}
    assert error["code"] == code
    assert error["message"] == message
    assert error["retryable"] is retryable
    assert UUID(error["requestId"])
    assert response.headers["x-request-id"] == error["requestId"]


def app_with_failing_route(error_factory: Callable[[], Exception]) -> FastAPI:
    app = create_app(database=None)

    async def fail() -> None:
        raise error_factory()

    app.add_api_route("/failure", fail, methods=["GET"])
    return app


def test_factory_returns_isolated_apps_with_explicit_lifespan() -> None:
    first = create_app(database=None)
    second = create_app(database=None)

    assert first is not second
    assert isinstance(first.state.executor_connection_registry, ExecutorConnectionRegistry)
    assert isinstance(second.state.executor_connection_registry, ExecutorConnectionRegistry)
    assert first.state.executor_connection_registry is not second.state.executor_connection_registry
    assert first.state.task_event_convergence_service is None
    assert second.state.task_event_convergence_service is None
    assert first.state.task_event_stream_service is None
    assert second.state.task_event_stream_service is None
    assert first.state.lifecycle_state == "created"
    assert second.state.lifecycle_state == "created"

    with TestClient(first):
        assert first.state.lifecycle_state == "running"
        assert second.state.lifecycle_state == "created"

    assert first.state.lifecycle_state == "stopped"


def test_lifespan_tolerates_an_unavailable_registry_during_shutdown() -> None:
    app = create_app(database=None)
    app.state.executor_connection_registry = object()

    with TestClient(app):
        assert app.state.lifecycle_state == "running"

    assert app.state.lifecycle_state == "stopped"


def test_application_error_uses_the_public_structured_envelope() -> None:
    app = app_with_failing_route(
        lambda: AppError(
            status_code=409,
            code="conflict",
            message="Task conflicts",
            retryable=True,
        )
    )

    response = TestClient(app).get("/failure")

    assert_error_response(
        response,
        status_code=409,
        code="conflict",
        message="Task conflicts",
        retryable=True,
    )


def test_timeline_revision_conflict_has_the_only_public_details_shape() -> None:
    details = TimelineRevisionConflictDetails(
        kind="timeline_revision_conflict.v1",
        currentRevision=3,
    )
    app = app_with_failing_route(
        lambda: AppError(
            status_code=409,
            code="timeline_revision_conflict",
            message="Timeline revision conflicts",
            details=details,
        )
    )

    response = TestClient(app).get("/failure")

    assert response.status_code == 409
    assert response.json()["error"]["details"] == {
        "kind": "timeline_revision_conflict.v1",
        "currentRevision": 3,
    }


@pytest.mark.parametrize(
    "details",
    [
        {"kind": "other", "currentRevision": 3},
        {"kind": "timeline_revision_conflict.v1", "currentRevision": 0},
        {"kind": "timeline_revision_conflict.v1", "currentRevision": True},
        {
            "kind": "timeline_revision_conflict.v1",
            "currentRevision": 3,
            "private": "must-not-cross-boundary",
        },
    ],
)
def test_timeline_revision_conflict_details_are_strict(
    details: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TimelineRevisionConflictDetails.model_validate(details)


def test_framework_errors_are_normalized_without_reflecting_invalid_input() -> None:
    app = create_app(database=None)

    async def accepts_count(count: int) -> dict[str, int]:
        return {"count": count}

    async def forbidden() -> None:
        raise HTTPException(status_code=403, detail="private policy detail")

    app.add_api_route("/validated", accepts_count, methods=["GET"])
    app.add_api_route("/forbidden", forbidden, methods=["GET"])

    not_found = TestClient(app).get("/missing")
    invalid = TestClient(app).get("/validated", params={"count": "private-invalid-value"})
    forbidden_response = TestClient(app).get("/forbidden")

    assert_error_response(
        not_found,
        status_code=404,
        code="not_found",
        message="Resource not found",
    )
    assert_error_response(
        invalid,
        status_code=422,
        code="validation",
        message="Request validation failed",
    )
    assert "private-invalid-value" not in invalid.text
    assert_error_response(
        forbidden_response,
        status_code=403,
        code="request_rejected",
        message="Request failed",
    )
    assert "private policy detail" not in forbidden_response.text


def test_unhandled_errors_fail_closed_without_leaking_exception_text() -> None:
    app = app_with_failing_route(lambda: RuntimeError("password=private"))

    response = TestClient(app, raise_server_exceptions=False).get("/failure")

    assert_error_response(
        response,
        status_code=500,
        code="internal",
        message="Internal server error",
    )
    assert "private" not in response.text


def test_valid_request_id_is_propagated_and_invalid_value_is_replaced() -> None:
    app = create_app(database=None)
    client = TestClient(app)
    oversized_request_id = "x" * 129

    accepted = client.get("/missing", headers={"x-request-id": "demo-request-42"})
    replaced = client.get("/missing", headers={"x-request-id": oversized_request_id})

    assert accepted.headers["x-request-id"] == "demo-request-42"
    assert accepted.json()["error"]["requestId"] == "demo-request-42"
    assert replaced.headers["x-request-id"] != oversized_request_id
    assert UUID(replaced.headers["x-request-id"])


def test_error_handler_generates_a_request_id_when_context_middleware_is_absent() -> None:
    app = FastAPI()
    register_error_handlers(app)

    async def fail() -> None:
        raise AppError(status_code=409, code="conflict", message="Conflict")

    app.add_api_route("/failure", fail, methods=["GET"])

    response = TestClient(app).get("/failure")

    assert_error_response(
        response,
        status_code=409,
        code="conflict",
        message="Conflict",
    )
