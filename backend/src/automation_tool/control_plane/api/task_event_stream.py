"""Authenticated Server-Sent Events over committed Task event facts."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.task_event_stream import (
    InvalidTaskEventStream,
    TaskEventRecord,
    TaskEventStreamBatch,
    TaskEventStreamNotFound,
    TaskEventStreamService,
    TaskEventStreamUnavailable,
)
from automation_tool.control_plane.domain import InstallationId, TaskEventType, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str = Field(alias="taskId")
    sequence: int
    event_version: str = Field(alias="eventVersion")
    event_type: TaskEventType = Field(alias="eventType")
    task_revision: int = Field(alias="taskRevision")
    task_status: TaskStatus = Field(alias="taskStatus")
    execution_attempt_id: str | None = Field(alias="executionAttemptId")
    action_id: str | None = Field(alias="actionId")
    progress_percent: int | None = Field(alias="progressPercent")
    occurred_at: str = Field(alias="occurredAt")
    recorded_at: str = Field(alias="recordedAt")
    message: str | None


def _service(request: Request) -> TaskEventStreamService:
    service = request.app.state.task_event_stream_service
    if not isinstance(service, TaskEventStreamService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="task_events_unavailable",
            message="Task events are unavailable",
            retryable=True,
        )
    return service


def _utc_text(event_time: datetime) -> str:
    return event_time.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event_frame(event: TaskEventRecord) -> str:
    payload = TaskEventResponse(
        taskId=str(event.task_id),
        sequence=event.sequence,
        eventVersion=event.event_version.value,
        eventType=event.event_type,
        taskRevision=event.task_revision,
        taskStatus=event.task_status,
        executionAttemptId=(
            None if event.execution_attempt_id is None else str(event.execution_attempt_id)
        ),
        actionId=None if event.action_id is None else str(event.action_id),
        progressPercent=event.progress_percent,
        occurredAt=_utc_text(event.occurred_at),
        recordedAt=_utc_text(event.recorded_at),
        message=None if event.safe_message is None else str(event.safe_message),
    )
    data = json.dumps(
        payload.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {event.sequence}\nevent: {event.event_type.value}\ndata: {data}\n\n"


async def _event_body(
    *,
    request: Request,
    service: TaskEventStreamService,
    installation_id: InstallationId,
    task_id: str,
    initial: TaskEventStreamBatch,
) -> AsyncIterator[str]:
    started_at = monotonic()
    last_keepalive_at = started_at
    batch = initial
    while True:
        for event in batch.events:
            yield _event_frame(event)
        if batch.close_after_batch:
            return
        if await request.is_disconnected():
            return
        if batch.caught_up:
            elapsed = monotonic() - started_at
            maximum = request.app.state.task_event_stream_max_connection_seconds
            if elapsed >= maximum:
                return
            await asyncio.sleep(
                min(request.app.state.task_event_stream_poll_interval_seconds, maximum - elapsed)
            )
            if await request.is_disconnected():
                return
            now = monotonic()
            if now - started_at >= maximum:
                return
            if (
                now - last_keepalive_at
                >= request.app.state.task_event_stream_keepalive_interval_seconds
            ):
                yield ": keep-alive\n\n"
                last_keepalive_at = now
        try:
            batch = await service.read(
                installation_id=installation_id,
                task_id=task_id,
                last_event_id=str(batch.next_sequence),
            )
        except asyncio.CancelledError:
            raise
        except (InvalidTaskEventStream, TaskEventStreamNotFound, TaskEventStreamUnavailable):
            logger.warning(
                "Task event stream stopped after response start",
                extra={"request_id": getattr(request.state, "request_id", None)},
            )
            return


def _map_preflight_error(error: Exception) -> AppError:
    if isinstance(error, TaskEventStreamNotFound):
        return AppError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="task_not_found",
            message="Task is unavailable",
        )
    if isinstance(error, InvalidTaskEventStream):
        return AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        )
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="task_events_unavailable",
        message="Task events are unavailable",
        retryable=True,
    )


@router.get(
    "/{task_id}/events",
    operation_id="streamTaskEvents",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Committed Task events in ascending sequence order",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_task_events(
    task_id: str,
    request: Request,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    service: Annotated[TaskEventStreamService, Depends(_service)],
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", max_length=16),
    ] = None,
) -> StreamingResponse:
    try:
        initial = await service.read(
            installation_id=installation_id,
            task_id=task_id,
            last_event_id=last_event_id,
        )
    except (InvalidTaskEventStream, TaskEventStreamNotFound, TaskEventStreamUnavailable) as error:
        raise _map_preflight_error(error) from None
    return StreamingResponse(
        _event_body(
            request=request,
            service=service,
            installation_id=installation_id,
            task_id=task_id,
            initial=initial,
        ),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-store, no-transform",
            "connection": "keep-alive",
            "x-accel-buffering": "no",
        },
    )


__all__ = ["router"]
