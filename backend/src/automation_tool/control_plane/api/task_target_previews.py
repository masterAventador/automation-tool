"""Installation-scoped target preview, exclusion, and confirmation API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Never

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_target_previews import (
    InvalidTaskTargetPreview,
    TaskTargetPreviewConflict,
    TaskTargetPreviewMutationResult,
    TaskTargetPreviewNotFound,
    TaskTargetPreviewPage,
    TaskTargetPreviewService,
    TaskTargetPreviewSnapshot,
    TaskTargetPreviewUnavailable,
)
from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    InstallationId,
    InvalidResourceId,
    TargetId,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import (
    MAX_EXECUTOR_SEQUENCE,
    MAX_TASK_TARGET_LIMIT,
    DouyinCandidateSource,
    IdempotencyKey,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskTargetPreviewItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target_id: str = Field(alias="targetId")
    ordinal: int = Field(ge=1, le=MAX_TASK_TARGET_LIMIT)
    display_name: str = Field(alias="displayName")
    public_handle: str | None = Field(alias="publicHandle")
    source: DouyinCandidateSource
    disposition: DouyinCandidateDisposition
    user_excluded: bool = Field(alias="userExcluded")
    selected: bool


class TaskTargetPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str = Field(alias="taskId")
    task_status: TaskStatus = Field(alias="taskStatus")
    task_revision: int = Field(alias="taskRevision", ge=1, le=MAX_TASK_EVENT_SEQUENCE)
    confirmation_revision: int = Field(
        alias="confirmationRevision", ge=1, le=MAX_TASK_EVENT_SEQUENCE
    )
    last_event_sequence: int = Field(alias="lastEventSequence", ge=0, le=MAX_TASK_EVENT_SEQUENCE)
    page_revision: int = Field(alias="pageRevision", ge=1, le=MAX_EXECUTOR_SEQUENCE)
    action: DouyinSearchExposureAction
    message_template: str | None = Field(alias="messageTemplate")
    selected_target_count: int = Field(alias="selectedTargetCount", ge=0, le=MAX_TASK_TARGET_LIMIT)
    user_excluded_target_count: int = Field(
        alias="userExcludedTargetCount", ge=0, le=MAX_TASK_TARGET_LIMIT
    )
    confirmed: bool
    confirmed_at: datetime | None = Field(alias="confirmedAt")
    items: list[TaskTargetPreviewItemResponse]
    next_cursor: str | None = Field(alias="nextCursor")


class TaskTargetExclusionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    page_revision: int = Field(alias="pageRevision", ge=1, le=MAX_EXECUTOR_SEQUENCE, strict=True)
    expected_task_revision: int = Field(
        alias="expectedTaskRevision", ge=1, le=MAX_TASK_EVENT_SEQUENCE, strict=True
    )
    excluded_target_ids: list[str] = Field(
        alias="excludedTargetIds", max_length=MAX_TASK_TARGET_LIMIT
    )


class TaskTargetConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    page_revision: int = Field(alias="pageRevision", ge=1, le=MAX_EXECUTOR_SEQUENCE, strict=True)
    confirmation_revision: int = Field(
        alias="confirmationRevision", ge=1, le=MAX_TASK_EVENT_SEQUENCE, strict=True
    )


def _service(request: Request) -> TaskTargetPreviewService:
    service = request.app.state.task_target_preview_service
    if not isinstance(service, TaskTargetPreviewService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_target_preview_unavailable",
            message="Task target preview is unavailable",
            retryable=True,
        )
    return service


def _snapshot_response(
    snapshot: TaskTargetPreviewSnapshot,
    *,
    next_cursor: str | None,
) -> TaskTargetPreviewResponse:
    return TaskTargetPreviewResponse(
        taskId=str(snapshot.task.task_id),
        taskStatus=snapshot.task.status,
        taskRevision=snapshot.task.revision,
        confirmationRevision=snapshot.confirmation_revision,
        lastEventSequence=snapshot.task.last_event_sequence,
        pageRevision=snapshot.page_revision,
        action=snapshot.action,
        messageTemplate=snapshot.message_template,
        selectedTargetCount=snapshot.selected_target_count,
        userExcludedTargetCount=snapshot.user_excluded_target_count,
        confirmed=snapshot.confirmed_at is not None,
        confirmedAt=snapshot.confirmed_at,
        items=[
            TaskTargetPreviewItemResponse(
                targetId=str(item.target.target_id),
                ordinal=item.target.ordinal,
                displayName=item.target.candidate.summary.display_name,
                publicHandle=item.target.candidate.summary.public_handle,
                source=item.target.candidate.source,
                disposition=item.target.disposition,
                userExcluded=item.user_excluded,
                selected=item.selected,
            )
            for item in snapshot.items
        ],
        nextCursor=next_cursor,
    )


def _page_response(page: TaskTargetPreviewPage) -> TaskTargetPreviewResponse:
    return _snapshot_response(page.snapshot, next_cursor=page.next_cursor)


def _mutation_response(result: TaskTargetPreviewMutationResult) -> TaskTargetPreviewResponse:
    return _snapshot_response(result.snapshot, next_cursor=None)


def _parse_task_id(value: str) -> TaskId:
    try:
        return TaskId.parse(value)
    except InvalidResourceId:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="task_target_preview_not_found",
            message="Task target preview is unavailable",
        ) from None


def _map_failure(error: Exception) -> Never:
    if isinstance(error, TaskTargetPreviewNotFound):
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="task_target_preview_not_found",
            message="Task target preview is unavailable",
        ) from None
    if isinstance(error, TaskTargetPreviewConflict):
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="task_target_preview_stale",
            message="Task target preview is stale",
        ) from None
    if isinstance(error, (InvalidTaskTargetPreview, InvalidResourceId, TypeError, ValueError)):
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    if isinstance(error, TaskTargetPreviewUnavailable):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_target_preview_unavailable",
            message="Task target preview is unavailable",
            retryable=True,
        ) from None
    raise error


@router.get(
    "/{task_id}/target-preview",
    response_model=TaskTargetPreviewResponse,
    operation_id="getTaskTargetPreview",
)
async def get_task_target_preview(
    task_id: str,
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskTargetPreviewService, Depends(_service)],
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TASK_TARGET_LIMIT)] = 20,
) -> TaskTargetPreviewResponse:
    response.headers["cache-control"] = "no-store"
    parsed_task_id = _parse_task_id(task_id)
    try:
        page = await service.get(
            installation_id=installation_id,
            task_id=parsed_task_id,
            cursor=cursor,
            limit=limit,
        )
    except Exception as error:
        _map_failure(error)
    return _page_response(page)


@router.put(
    "/{task_id}/target-preview/exclusions",
    response_model=TaskTargetPreviewResponse,
    operation_id="replaceTaskTargetExclusions",
)
async def replace_task_target_exclusions(
    task_id: str,
    payload: TaskTargetExclusionsRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskTargetPreviewService, Depends(_service)],
) -> TaskTargetPreviewResponse:
    response.headers["cache-control"] = "no-store"
    parsed_task_id = _parse_task_id(task_id)
    try:
        normalized_key = str(IdempotencyKey(idempotency_key))
        excluded = tuple(TargetId.parse(value) for value in payload.excluded_target_ids)
        result = await service.replace_exclusions(
            installation_id=installation_id,
            task_id=parsed_task_id,
            page_revision=payload.page_revision,
            expected_task_revision=payload.expected_task_revision,
            excluded_target_ids=excluded,
            idempotency_key=normalized_key,
        )
    except Exception as error:
        _map_failure(error)
    return _mutation_response(result)


@router.post(
    "/{task_id}/target-preview/confirmations",
    response_model=TaskTargetPreviewResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="confirmTaskTargetPreview",
)
async def confirm_task_target_preview(
    task_id: str,
    payload: TaskTargetConfirmationRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskTargetPreviewService, Depends(_service)],
) -> TaskTargetPreviewResponse:
    response.headers["cache-control"] = "no-store"
    parsed_task_id = _parse_task_id(task_id)
    try:
        normalized_key = str(IdempotencyKey(idempotency_key))
        result = await service.confirm(
            installation_id=installation_id,
            task_id=parsed_task_id,
            page_revision=payload.page_revision,
            expected_task_revision=payload.confirmation_revision,
            idempotency_key=normalized_key,
        )
    except Exception as error:
        _map_failure(error)
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return _mutation_response(result)


__all__ = ["router"]
