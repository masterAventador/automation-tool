"""Installation-scoped Task creation and snapshot query API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_queries import (
    InvalidTaskQuery,
    TaskNotFound,
    TaskQueryService,
)
from automation_tool.control_plane.application.tasks import (
    InvalidTaskCreation,
    TaskCreationService,
    TaskPersistenceRejected,
    TaskRecord,
)
from automation_tool.control_plane.domain import (
    DOUYIN_SEARCH_EXPOSURE_TEMPLATE,
    MAX_MESSAGE_TEMPLATE_CHARACTERS,
    MAX_SEARCH_KEYWORD_CHARACTERS,
    MAX_TASK_EVENT_SEQUENCE,
    MAX_TASK_INTERVAL_SECONDS,
    MAX_TASK_TARGET_LIMIT,
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InstallationId,
    InvalidTaskDefinition,
    TaskStatus,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    template: Literal["douyin.search_exposure.v1"]
    search_keyword: str = Field(
        alias="searchKeyword",
        min_length=1,
        max_length=MAX_SEARCH_KEYWORD_CHARACTERS,
        strict=True,
    )
    action: DouyinSearchExposureAction
    message_template: str | None = Field(
        alias="messageTemplate",
        max_length=MAX_MESSAGE_TEMPLATE_CHARACTERS,
    )
    target_limit: int = Field(
        alias="targetLimit",
        ge=1,
        le=MAX_TASK_TARGET_LIMIT,
        strict=True,
    )
    minimum_interval_seconds: int = Field(
        alias="minimumIntervalSeconds",
        ge=1,
        le=MAX_TASK_INTERVAL_SECONDS,
        strict=True,
    )
    maximum_interval_seconds: int = Field(
        alias="maximumIntervalSeconds",
        ge=1,
        le=MAX_TASK_INTERVAL_SECONDS,
        strict=True,
    )
    preview_required: Literal[True] = Field(alias="previewRequired")
    final_confirmation_required: Literal[True] = Field(alias="finalConfirmationRequired")

    @model_validator(mode="after")
    def validate_definition(self) -> TaskCreateRequest:
        try:
            self.to_definition()
        except InvalidTaskDefinition:
            raise ValueError("invalid Task definition") from None
        return self

    def to_definition(self) -> DouyinSearchExposureDefinition:
        if self.template != DOUYIN_SEARCH_EXPOSURE_TEMPLATE:
            raise InvalidTaskDefinition
        return DouyinSearchExposureDefinition(
            search_keyword=self.search_keyword,
            action=self.action,
            message_template=self.message_template,
            target_limit=self.target_limit,
            minimum_interval_seconds=self.minimum_interval_seconds,
            maximum_interval_seconds=self.maximum_interval_seconds,
            preview_required=self.preview_required,
            final_confirmation_required=self.final_confirmation_required,
        )


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str = Field(alias="taskId")
    status: TaskStatus
    revision: int = Field(ge=1, le=MAX_TASK_EVENT_SEQUENCE)
    last_event_sequence: int = Field(
        alias="lastEventSequence",
        ge=0,
        le=MAX_TASK_EVENT_SEQUENCE,
    )
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class TaskListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    items: list[TaskResponse]
    next_cursor: str | None = Field(alias="nextCursor")


def _creation_service(request: Request) -> TaskCreationService:
    service = request.app.state.task_creation_service
    if not isinstance(service, TaskCreationService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="tasks_unavailable",
            message="Task service is unavailable",
            retryable=True,
        )
    return service


def _query_service(request: Request) -> TaskQueryService:
    service = request.app.state.task_query_service
    if not isinstance(service, TaskQueryService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="tasks_unavailable",
            message="Task service is unavailable",
            retryable=True,
        )
    return service


def _task_response(task: TaskRecord) -> TaskResponse:
    return TaskResponse(
        taskId=str(task.task_id),
        status=task.status,
        revision=task.revision,
        lastEventSequence=task.last_event_sequence,
        createdAt=task.created_at,
        updatedAt=task.updated_at,
    )


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createTask",
)
async def create_task(
    payload: TaskCreateRequest,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskCreationService, Depends(_creation_service)],
) -> TaskResponse:
    response.headers["cache-control"] = "no-store"
    try:
        result = await service.create(
            installation_id=installation_id,
            idempotency_key=idempotency_key,
            definition=payload.to_definition(),
        )
    except InvalidTaskCreation:
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    except TaskPersistenceRejected:
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="task_create_rejected",
            message="Task creation was rejected",
        ) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
    task = result.task
    return _task_response(task)


@router.get(
    "",
    response_model=TaskListResponse,
    operation_id="listTasks",
)
async def list_tasks(
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskQueryService, Depends(_query_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TaskListResponse:
    response.headers["cache-control"] = "no-store"
    try:
        page = await service.list(
            installation_id=installation_id,
            cursor=cursor,
            limit=limit,
        )
    except InvalidTaskQuery:
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    return TaskListResponse(
        items=[_task_response(task) for task in page.items],
        nextCursor=page.next_cursor,
    )


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    operation_id="getTask",
)
async def get_task(
    task_id: str,
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskQueryService, Depends(_query_service)],
) -> TaskResponse:
    response.headers["cache-control"] = "no-store"
    try:
        task = await service.get(installation_id=installation_id, task_id=task_id)
    except TaskNotFound:
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="task_not_found",
            message="Task is unavailable",
        ) from None
    except InvalidTaskQuery:
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    return _task_response(task)


__all__ = ["router"]
