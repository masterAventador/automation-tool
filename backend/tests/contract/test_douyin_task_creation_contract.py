from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.api.tasks import TaskCreateRequest
from automation_tool.control_plane.application.tasks import (
    TaskCreationResult,
    TaskCreationService,
    TaskRecord,
)
from automation_tool.control_plane.domain import (
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InstallationId,
    InvalidTaskDefinition,
    TaskId,
    TaskStatus,
)

NOW = datetime(2026, 7, 18, 23, 30, tzinfo=UTC)
INSTALLATION_ID = InstallationId.new()

VALID_DEFINITION = {
    "template": "douyin.search_exposure.v1",
    "searchKeyword": "新能源汽车",
    "action": "comment",
    "messageTemplate": "内容很有启发,期待更多分享",
    "targetLimit": 12,
    "minimumIntervalSeconds": 30,
    "maximumIntervalSeconds": 90,
    "previewRequired": True,
    "finalConfirmationRequired": True,
}


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingRepository:
    def __init__(self) -> None:
        self.definition: DouyinSearchExposureDefinition | None = None

    async def create(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        idempotency_key: str,
        definition: DouyinSearchExposureDefinition,
        created_at: datetime,
    ) -> TaskCreationResult:
        self.definition = definition
        return TaskCreationResult(
            task=TaskRecord(
                task_id=task_id,
                installation_id=installation_id,
                status=TaskStatus.DRAFT,
                revision=1,
                last_event_sequence=0,
                created_at=created_at,
                updated_at=created_at,
            ),
            created=True,
        )

    async def get(self, **_kwargs: object) -> TaskRecord | None:
        raise AssertionError("not used")

    async def transition(self, **_kwargs: object) -> TaskRecord:
        raise AssertionError("not used")


def client() -> tuple[TestClient, RecordingRepository]:
    repository = RecordingRepository()
    service = TaskCreationService(repository=repository, clock=FixedClock())
    app = create_app(database=None, task_creation_service=service)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app), repository


def test_openapi_requires_one_exact_douyin_search_exposure_definition() -> None:
    schema = create_app(database=None).openapi()
    request_schema = schema["components"]["schemas"]["TaskCreateRequest"]

    assert request_schema["additionalProperties"] is False
    assert set(request_schema["required"]) == {
        "template",
        "searchKeyword",
        "action",
        "messageTemplate",
        "targetLimit",
        "minimumIntervalSeconds",
        "maximumIntervalSeconds",
        "previewRequired",
        "finalConfirmationRequired",
    }


def test_valid_definition_reaches_the_service_as_canonical_typed_fields() -> None:
    app_client, repository = client()

    response = app_client.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "task:create:douyin-search-1"},
        json=VALID_DEFINITION,
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert repository.definition is not None
    assert {
        "template": repository.definition.template,
        "searchKeyword": repository.definition.search_keyword,
        "action": repository.definition.action,
        "messageTemplate": repository.definition.message_template,
        "targetLimit": repository.definition.target_limit,
        "minimumIntervalSeconds": repository.definition.minimum_interval_seconds,
        "maximumIntervalSeconds": repository.definition.maximum_interval_seconds,
        "previewRequired": repository.definition.preview_required,
        "finalConfirmationRequired": repository.definition.final_confirmation_required,
    } == VALID_DEFINITION
    assert "新能源汽车" not in response.text
    assert "内容很有启发" not in response.text


def test_http_caller_uses_unicode_code_points_and_the_exact_target_cap() -> None:
    app_client, repository = client()
    keyword = "😀" * 80

    response = app_client.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "task:create:unicode-boundary"},
        json={
            **VALID_DEFINITION,
            "searchKeyword": keyword,
            "targetLimit": 100,
        },
    )

    assert response.status_code == 201
    assert repository.definition is not None
    assert repository.definition.search_keyword == keyword
    assert repository.definition.target_limit == 100
    assert keyword not in response.text


def test_definition_validation_is_closed_consistent_and_secret_safe() -> None:
    app_client, _ = client()
    invalid: tuple[dict[str, Any], ...] = (
        {**VALID_DEFINITION, "template": "private.template"},
        {**VALID_DEFINITION, "searchKeyword": ""},
        {**VALID_DEFINITION, "searchKeyword": " leading"},
        {**VALID_DEFINITION, "searchKeyword": "line\nbreak"},
        {**VALID_DEFINITION, "searchKeyword": "control\u0085character"},
        {**VALID_DEFINITION, "searchKeyword": "词" * 81},
        {**VALID_DEFINITION, "action": "like"},
        {**VALID_DEFINITION, "action": "browse", "messageTemplate": "must be absent"},
        {**VALID_DEFINITION, "action": "comment", "messageTemplate": None},
        {**VALID_DEFINITION, "messageTemplate": "password=private-value"},
        {**VALID_DEFINITION, "targetLimit": 0},
        {**VALID_DEFINITION, "targetLimit": 101},
        {**VALID_DEFINITION, "targetLimit": True},
        {**VALID_DEFINITION, "minimumIntervalSeconds": 0},
        {**VALID_DEFINITION, "maximumIntervalSeconds": 3601},
        {
            **VALID_DEFINITION,
            "minimumIntervalSeconds": 91,
            "maximumIntervalSeconds": 90,
        },
        {**VALID_DEFINITION, "previewRequired": False},
        {**VALID_DEFINITION, "finalConfirmationRequired": False},
        {**VALID_DEFINITION, "unknown": "private-value"},
    )

    for index, payload in enumerate(invalid):
        response = app_client.post(
            "/api/v1/tasks",
            headers={"Idempotency-Key": f"task:create:invalid:{index}"},
            json=payload,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation"
        assert "private-value" not in response.text


def test_internal_definition_guards_reject_untyped_values_without_disclosure() -> None:
    request = TaskCreateRequest.model_construct(
        template="private.template",
        search_keyword="新能源汽车",
        action=DouyinSearchExposureAction.BROWSE,
        message_template=None,
        target_limit=12,
        minimum_interval_seconds=30,
        maximum_interval_seconds=90,
        preview_required=True,
        final_confirmation_required=True,
    )

    with pytest.raises(InvalidTaskDefinition) as request_error:
        request.to_definition()
    assert "private.template" not in repr(request_error.value)

    with pytest.raises(InvalidTaskDefinition) as domain_error:
        DouyinSearchExposureDefinition(
            search_keyword="新能源汽车",
            action=cast(DouyinSearchExposureAction, "browse"),
            message_template=None,
            target_limit=12,
            minimum_interval_seconds=30,
            maximum_interval_seconds=90,
            preview_required=True,
            final_confirmation_required=True,
        )
    assert "browse" not in repr(domain_error.value)
