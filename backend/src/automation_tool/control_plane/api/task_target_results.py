"""Installation-scoped target result projection API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Never

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_target_results import (
    InvalidTaskTargetResult,
    TaskTargetResultNotFound,
    TaskTargetResultService,
    TaskTargetResultSnapshot,
    TaskTargetResultStatus,
    TaskTargetResultUnavailable,
)
from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    InstallationId,
    InvalidResourceId,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import MAX_TASK_TARGET_LIMIT, ActionResultEvidence

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskTargetResultItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target_id: str = Field(alias="targetId")
    ordinal: int = Field(ge=1, le=MAX_TASK_TARGET_LIMIT)
    display_name: str = Field(alias="displayName")
    public_handle: str | None = Field(alias="publicHandle")
    result_status: TaskTargetResultStatus = Field(alias="resultStatus")
    evidence: ActionResultEvidence
    action_id: str | None = Field(alias="actionId")
    updated_at: datetime = Field(alias="updatedAt")


class TaskTargetResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str = Field(alias="taskId")
    task_status: TaskStatus = Field(alias="taskStatus")
    task_revision: int = Field(alias="taskRevision", ge=1, le=MAX_TASK_EVENT_SEQUENCE)
    last_event_sequence: int = Field(alias="lastEventSequence", ge=0, le=MAX_TASK_EVENT_SEQUENCE)
    items: list[TaskTargetResultItemResponse] = Field(max_length=MAX_TASK_TARGET_LIMIT)


def _service(request: Request) -> TaskTargetResultService:
    service = request.app.state.task_target_result_service
    if not isinstance(service, TaskTargetResultService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_target_results_unavailable",
            message="Task target results are unavailable",
            retryable=True,
        )
    return service


def _failure(error: Exception) -> Never:
    if isinstance(error, (TaskTargetResultNotFound, InvalidResourceId)):
        raise AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="task_target_results_not_found",
            message="Task target results are unavailable",
        ) from None
    if isinstance(error, InvalidTaskTargetResult):
        raise AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        ) from None
    if isinstance(error, TaskTargetResultUnavailable):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_target_results_unavailable",
            message="Task target results are unavailable",
            retryable=True,
        ) from None
    raise error


def _response(snapshot: TaskTargetResultSnapshot) -> TaskTargetResultResponse:
    return TaskTargetResultResponse(
        taskId=str(snapshot.task.task_id),
        taskStatus=snapshot.task.status,
        taskRevision=snapshot.task.revision,
        lastEventSequence=snapshot.task.last_event_sequence,
        items=[
            TaskTargetResultItemResponse(
                targetId=str(item.target_id),
                ordinal=item.ordinal,
                displayName=item.display_name,
                publicHandle=item.public_handle,
                resultStatus=item.status,
                evidence=item.evidence,
                actionId=None if item.action_id is None else str(item.action_id),
                updatedAt=item.updated_at,
            )
            for item in snapshot.items
        ],
    )


@router.get(
    "/{task_id}/target-results",
    response_model=TaskTargetResultResponse,
    operation_id="getTaskTargetResults",
)
async def get_task_target_results(
    task_id: str,
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskTargetResultService, Depends(_service)],
) -> TaskTargetResultResponse:
    response.headers["cache-control"] = "no-store"
    try:
        parsed_task_id = TaskId.parse(task_id)
        snapshot = await service.get(
            installation_id=installation_id,
            task_id=parsed_task_id,
        )
    except Exception as error:
        _failure(error)
    return _response(snapshot)


__all__ = ["router"]
