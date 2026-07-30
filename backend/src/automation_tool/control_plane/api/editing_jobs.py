"""App-session protected local-editing job submission and queries."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.editing_errors import translate_editing_error
from automation_tool.control_plane.api.errors import AppError, ErrorEnvelope
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.editing_jobs import (
    EditingJobListPage,
    EditingJobService,
    InvalidEditingJobQuery,
)
from automation_tool.control_plane.domain import (
    EditingJob,
    EditingJobFailureCode,
    EditingJobStatus,
    InstallationId,
)

project_router = APIRouter(
    prefix="/api/v1/editing-projects/{project_id}/jobs",
    tags=["editing-jobs"],
)
detail_router = APIRouter(prefix="/api/v1/editing-jobs", tags=["editing-jobs"])


class EditingJobSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditingJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    job_id: str = Field(alias="jobId")
    project_id: str = Field(alias="projectId")
    timeline_id: str = Field(alias="timelineId")
    timeline_revision: int = Field(alias="timelineRevision", ge=1)
    status: EditingJobStatus
    failure_code: EditingJobFailureCode | None = Field(alias="failureCode")
    output_artifact_id: str | None = Field(alias="outputArtifactId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class EditingJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    items: list[EditingJobResponse]
    next_cursor: str | None = Field(alias="nextCursor")


def _service(request: Request) -> EditingJobService:
    service = request.app.state.editing_job_service
    if not isinstance(service, EditingJobService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="editing_jobs_unavailable",
            message="Editing job service is unavailable",
            retryable=True,
        )
    return service


def _validation_error() -> AppError:
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation",
        message="Request validation failed",
    )


def _job_response(job: EditingJob) -> EditingJobResponse:
    return EditingJobResponse(
        jobId=str(job.job_id),
        projectId=str(job.project_id),
        timelineId=str(job.timeline_id),
        timelineRevision=job.timeline_revision,
        status=job.status,
        failureCode=job.failure_code,
        outputArtifactId=(None if job.output_artifact_id is None else str(job.output_artifact_id)),
        createdAt=job.created_at,
        updatedAt=job.updated_at,
    )


def _list_response(page: EditingJobListPage) -> EditingJobListResponse:
    return EditingJobListResponse(
        items=[_job_response(job) for job in page.items],
        nextCursor=page.next_cursor,
    )


@project_router.post(
    "",
    response_model=EditingJobResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="submitEditingJob",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Editing job conflicts with stored work",
            "model": ErrorEnvelope,
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Request validation failed",
            "model": ErrorEnvelope,
        },
    },
)
async def submit_editing_job(
    project_id: str,
    _payload: EditingJobSubmitRequest,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[EditingJobService, Depends(_service)],
) -> EditingJobResponse:
    response.headers["cache-control"] = "no-store"
    try:
        job = await service.submit(
            installation_id=installation_id,
            project_id=project_id,
        )
    except InvalidEditingJobQuery:
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _job_response(job)


@project_router.get(
    "",
    response_model=EditingJobListResponse,
    operation_id="listEditingJobs",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Request validation failed",
            "model": ErrorEnvelope,
        }
    },
)
async def list_editing_jobs(
    project_id: str,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[EditingJobService, Depends(_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EditingJobListResponse:
    response.headers["cache-control"] = "no-store"
    try:
        page = await service.list(
            installation_id=installation_id,
            project_id=project_id,
            cursor=cursor,
            limit=limit,
        )
    except InvalidEditingJobQuery:
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _list_response(page)


@detail_router.get(
    "/{job_id}",
    response_model=EditingJobResponse,
    operation_id="getEditingJob",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Request validation failed",
            "model": ErrorEnvelope,
        }
    },
)
async def get_editing_job(
    job_id: str,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[EditingJobService, Depends(_service)],
) -> EditingJobResponse:
    response.headers["cache-control"] = "no-store"
    try:
        job = await service.get(
            installation_id=installation_id,
            job_id=job_id,
        )
    except InvalidEditingJobQuery:
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _job_response(job)


__all__ = ["detail_router", "project_router"]
