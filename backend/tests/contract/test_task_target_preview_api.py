from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.api.task_target_previews import _map_failure
from automation_tool.control_plane.application.task_target_previews import (
    PendingTaskTargetConfirmation,
    PendingTaskTargetExclusions,
    TaskTargetPreviewConflict,
    TaskTargetPreviewItem,
    TaskTargetPreviewMutationResult,
    TaskTargetPreviewNotFound,
    TaskTargetPreviewService,
    TaskTargetPreviewSnapshot,
)
from automation_tool.control_plane.application.task_targets import TaskTargetRecord
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    InstallationId,
    TargetId,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

NOW = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = TaskId.parse("123e4567-e89b-42d3-a456-426614174005")
TARGET_ONE = TargetId.parse("123e4567-e89b-42d3-a456-426614174006")
TARGET_TWO = TargetId.parse("123e4567-e89b-42d3-a456-426614174007")


def _target(target_id: TargetId, ordinal: int) -> TaskTargetRecord:
    return TaskTargetRecord(
        target_id=target_id,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        ordinal=ordinal,
        candidate=DouyinCandidate(
            platform_target_id=f"private-platform-{ordinal}",
            summary=DouyinCandidateSummary(
                display_name=f"目标 {ordinal}",
                public_handle=f"public_{ordinal}",
            ),
            source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
            page_revision=7,
        ),
        disposition=DouyinCandidateDisposition.ELIGIBLE,
        policy_version=DOUYIN_CANDIDATE_POLICY_VERSION,
        evaluated_at=NOW,
        created_at=NOW,
    )


class MemoryPreviewRepository:
    def __init__(self) -> None:
        self.task_revision = 4
        self.last_event_sequence = 3
        self.excluded: tuple[TargetId, ...] = ()
        self.confirmed = False
        self.confirm_calls = 0
        self.failure: Exception | None = None

    def snapshot(self) -> TaskTargetPreviewSnapshot:
        status = TaskStatus.QUEUED if self.confirmed else TaskStatus.AWAITING_CONFIRMATION
        items = tuple(
            TaskTargetPreviewItem(
                target=_target(target_id, ordinal),
                user_excluded=target_id in self.excluded,
            )
            for ordinal, target_id in enumerate((TARGET_ONE, TARGET_TWO), start=1)
        )
        return TaskTargetPreviewSnapshot(
            task=TaskRecord(
                task_id=TASK_ID,
                installation_id=INSTALLATION_ID,
                status=status,
                revision=self.task_revision,
                last_event_sequence=self.last_event_sequence,
                created_at=NOW,
                updated_at=NOW,
            ),
            page_revision=7,
            confirmation_revision=(
                self.task_revision if not self.confirmed else self.task_revision - 1
            ),
            action=DouyinSearchExposureAction.COMMENT,
            message_template="您好 {{target_display_name}} 期待您的分享",
            items=items,
            selected_target_count=sum(item.selected for item in items),
            user_excluded_target_count=len(self.excluded),
            confirmed_at=NOW if self.confirmed else None,
        )

    async def read_page(self, **_: object) -> TaskTargetPreviewSnapshot:
        if self.failure is not None:
            raise self.failure
        return self.snapshot()

    async def replace_exclusions(
        self, pending: PendingTaskTargetExclusions
    ) -> TaskTargetPreviewMutationResult:
        if self.failure is not None:
            raise self.failure
        self.excluded = pending.excluded_target_ids
        self.task_revision += 1
        self.last_event_sequence += 1
        return TaskTargetPreviewMutationResult(snapshot=self.snapshot(), replayed=False)

    async def confirm(
        self, pending: PendingTaskTargetConfirmation
    ) -> TaskTargetPreviewMutationResult:
        if self.failure is not None:
            raise self.failure
        self.confirm_calls += 1
        replayed = self.confirmed
        if not replayed:
            self.confirmed = True
            self.task_revision += 1
            self.last_event_sequence += 1
        return TaskTargetPreviewMutationResult(snapshot=self.snapshot(), replayed=replayed)


def preview_app(
    repository: MemoryPreviewRepository | None = None,
) -> tuple[TestClient, MemoryPreviewRepository]:
    resolved = repository or MemoryPreviewRepository()
    app = create_app(
        database=None,
        task_target_preview_service=TaskTargetPreviewService(repository=resolved),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    return TestClient(app), resolved


def test_openapi_freezes_target_preview_operations() -> None:
    schema = create_app(database=None).openapi()

    preview = schema["paths"]["/api/v1/tasks/{task_id}/target-preview"]
    assert preview["get"]["operationId"] == "getTaskTargetPreview"
    exclusions = schema["paths"]["/api/v1/tasks/{task_id}/target-preview/exclusions"]["put"]
    confirmation = schema["paths"]["/api/v1/tasks/{task_id}/target-preview/confirmations"]["post"]
    assert exclusions["operationId"] == "replaceTaskTargetExclusions"
    assert confirmation["operationId"] == "confirmTaskTargetPreview"
    assert preview["get"]["security"] == [{"AppSession": []}]
    assert exclusions["security"] == [{"AppSession": []}]
    assert confirmation["security"] == [{"AppSession": []}]
    item_schema = schema["components"]["schemas"]["TaskTargetPreviewItemResponse"]
    assert item_schema["properties"]["source"] == {
        "$ref": "#/components/schemas/DouyinCandidateSource"
    }


def test_app_reads_excludes_and_confirms_only_public_preview_fields() -> None:
    client, _ = preview_app()

    preview = client.get(f"/api/v1/tasks/{TASK_ID}/target-preview")
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    assert preview.json() == {
        "taskId": str(TASK_ID),
        "taskStatus": "awaiting_confirmation",
        "taskRevision": 4,
        "confirmationRevision": 4,
        "lastEventSequence": 3,
        "pageRevision": 7,
        "action": "comment",
        "messageTemplate": "您好 {{target_display_name}} 期待您的分享",
        "selectedTargetCount": 2,
        "userExcludedTargetCount": 0,
        "confirmed": False,
        "confirmedAt": None,
        "items": [
            {
                "targetId": str(TARGET_ONE),
                "ordinal": 1,
                "displayName": "目标 1",
                "publicHandle": "public_1",
                "source": "general_search_author",
                "disposition": "eligible",
                "userExcluded": False,
                "selected": True,
            },
            {
                "targetId": str(TARGET_TWO),
                "ordinal": 2,
                "displayName": "目标 2",
                "publicHandle": "public_2",
                "source": "general_search_author",
                "disposition": "eligible",
                "userExcluded": False,
                "selected": True,
            },
        ],
        "nextCursor": None,
    }
    assert "private-platform" not in preview.text
    assert "dedupe" not in preview.text.lower()

    excluded = client.put(
        f"/api/v1/tasks/{TASK_ID}/target-preview/exclusions",
        headers={"Idempotency-Key": "task:preview:api:exclude"},
        json={
            "pageRevision": 7,
            "expectedTaskRevision": 4,
            "excludedTargetIds": [str(TARGET_TWO)],
        },
    )
    assert excluded.status_code == 200
    assert excluded.json()["taskRevision"] == 5
    assert excluded.json()["selectedTargetCount"] == 1
    assert excluded.json()["items"][1]["userExcluded"] is True

    confirmed = client.post(
        f"/api/v1/tasks/{TASK_ID}/target-preview/confirmations",
        headers={"Idempotency-Key": "task:preview:api:confirm"},
        json={"pageRevision": 7, "confirmationRevision": 5},
    )
    replayed = client.post(
        f"/api/v1/tasks/{TASK_ID}/target-preview/confirmations",
        headers={"Idempotency-Key": "task:preview:api:confirm"},
        json={"pageRevision": 7, "confirmationRevision": 5},
    )
    assert confirmed.status_code == 202
    assert replayed.status_code == 200
    assert confirmed.json() == replayed.json()
    assert confirmed.json()["taskStatus"] == "queued"
    assert confirmed.json()["confirmed"] is True

    old_confirmation_field = client.post(
        f"/api/v1/tasks/{TASK_ID}/target-preview/confirmations",
        headers={"Idempotency-Key": "task:preview:api:old-field"},
        json={"pageRevision": 7, "expectedTaskRevision": 5},
    )
    assert old_confirmation_field.status_code == 422


def test_preview_auth_validation_stale_not_found_and_unavailable_fail_closed() -> None:
    client, repository = preview_app()
    invalid_requests = (
        client.get(f"/api/v1/tasks/{TASK_ID}/target-preview", params={"limit": 0}),
        client.put(
            f"/api/v1/tasks/{TASK_ID}/target-preview/exclusions",
            json={
                "pageRevision": 7,
                "expectedTaskRevision": 4,
                "excludedTargetIds": [],
            },
        ),
        client.put(
            f"/api/v1/tasks/{TASK_ID}/target-preview/exclusions",
            headers={"Idempotency-Key": "task:preview:invalid-target"},
            json={
                "pageRevision": 7,
                "expectedTaskRevision": 4,
                "excludedTargetIds": ["private-invalid"],
            },
        ),
    )
    for response in invalid_requests:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation"

    invalid_task = client.get("/api/v1/tasks/private-invalid/target-preview")
    assert invalid_task.status_code == 404
    assert invalid_task.json()["error"]["code"] == "task_target_preview_not_found"

    repository.failure = TaskTargetPreviewConflict()
    stale = client.get(f"/api/v1/tasks/{TASK_ID}/target-preview")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "task_target_preview_stale"
    stale_exclusion = client.put(
        f"/api/v1/tasks/{TASK_ID}/target-preview/exclusions",
        headers={"Idempotency-Key": "task:preview:stale-exclusion"},
        json={
            "pageRevision": 7,
            "expectedTaskRevision": 4,
            "excludedTargetIds": [],
        },
    )
    stale_confirmation = client.post(
        f"/api/v1/tasks/{TASK_ID}/target-preview/confirmations",
        headers={"Idempotency-Key": "task:preview:stale-confirmation"},
        json={"pageRevision": 7, "confirmationRevision": 4},
    )
    assert stale_exclusion.status_code == 409
    assert stale_confirmation.status_code == 409

    repository.failure = TaskTargetPreviewNotFound()
    missing = client.get(f"/api/v1/tasks/{TASK_ID}/target-preview")
    assert missing.status_code == 404

    repository.failure = RuntimeError("private database location")
    unavailable = client.get(f"/api/v1/tasks/{TASK_ID}/target-preview")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "task_target_preview_unavailable"
    assert "private" not in unavailable.text

    no_auth = TestClient(create_app(database=None)).get(f"/api/v1/tasks/{TASK_ID}/target-preview")
    assert no_auth.status_code == 401

    missing_service_app = create_app(database=None)
    missing_service_app.dependency_overrides[require_current_installation_access] = lambda: (
        INSTALLATION_ID
    )
    missing_service = TestClient(missing_service_app).get(f"/api/v1/tasks/{TASK_ID}/target-preview")
    assert missing_service.status_code == 503

    with pytest.raises(RuntimeError, match="private-unknown"):
        _map_failure(RuntimeError("private-unknown"))
