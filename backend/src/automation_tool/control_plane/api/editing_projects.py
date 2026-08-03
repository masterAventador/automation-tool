"""App-session protected local-editing project creation and queries."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.editing_errors import translate_editing_error
from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.editing_projects import (
    EditingProjectService,
    InvalidEditingProjectQuery,
)
from automation_tool.control_plane.domain import (
    MAX_CAPTION_FONT_PX,
    MAX_CAPTION_LINE_SPACING,
    MAX_CAPTION_STROKE_PX,
    MAX_OUTPUT_DIMENSION,
    MAX_OUTPUT_FPS,
    MAX_PROJECT_TITLE_CHARACTERS,
    MIN_CAPTION_FONT_PX,
    MIN_CAPTION_LINE_SPACING,
    MIN_OUTPUT_DIMENSION,
    MIN_OUTPUT_FPS,
    CaptionStyle,
    EditingProject,
    InstallationId,
    InvalidEditingProjectModel,
    OutputSpec,
)

router = APIRouter(prefix="/api/v1/editing-projects", tags=["editing-projects"])


class EditingOutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(ge=MIN_OUTPUT_DIMENSION, le=MAX_OUTPUT_DIMENSION, strict=True)
    height: int = Field(ge=MIN_OUTPUT_DIMENSION, le=MAX_OUTPUT_DIMENSION, strict=True)
    fps: int = Field(ge=MIN_OUTPUT_FPS, le=MAX_OUTPUT_FPS, strict=True)

    def to_domain(self) -> OutputSpec:
        return OutputSpec(width=self.width, height=self.height, fps=self.fps)


class EditingCaptionStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    font_key: str = Field(alias="fontKey", min_length=1, max_length=64, strict=True)
    font_px: int = Field(
        alias="fontPx",
        ge=MIN_CAPTION_FONT_PX,
        le=MAX_CAPTION_FONT_PX,
        strict=True,
    )
    stroke_px: int = Field(
        alias="strokePx",
        ge=0,
        le=MAX_CAPTION_STROKE_PX,
        strict=True,
    )
    line_spacing: float = Field(
        alias="lineSpacing",
        ge=MIN_CAPTION_LINE_SPACING,
        le=MAX_CAPTION_LINE_SPACING,
        strict=True,
    )

    def to_domain(self) -> CaptionStyle:
        return CaptionStyle(
            font_key=self.font_key,
            font_px=self.font_px,
            stroke_px=self.stroke_px,
            line_spacing=self.line_spacing,
        )


class EditingProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_PROJECT_TITLE_CHARACTERS, strict=True)
    output: EditingOutputSpec
    caption_style: EditingCaptionStyle = Field(alias="captionStyle")


class EditingProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_id: str = Field(alias="projectId")
    title: str
    output: EditingOutputSpec
    caption_style: EditingCaptionStyle = Field(alias="captionStyle")
    created_at: datetime = Field(alias="createdAt")


class EditingProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    items: list[EditingProjectResponse]
    next_cursor: str | None = Field(alias="nextCursor")


def _service(request: Request) -> EditingProjectService:
    service = request.app.state.editing_project_service
    if not isinstance(service, EditingProjectService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="editing_projects_unavailable",
            message="Editing project service is unavailable",
            retryable=True,
        )
    return service


def _validation_error() -> AppError:
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation",
        message="Request validation failed",
    )


def _project_response(project: EditingProject) -> EditingProjectResponse:
    return EditingProjectResponse(
        projectId=str(project.project_id),
        title=project.title,
        output=EditingOutputSpec(
            width=project.output.width,
            height=project.output.height,
            fps=project.output.fps,
        ),
        captionStyle=EditingCaptionStyle(
            fontKey=project.caption_style.font_key,
            fontPx=project.caption_style.font_px,
            strokePx=project.caption_style.stroke_px,
            lineSpacing=project.caption_style.line_spacing,
        ),
        createdAt=project.created_at,
    )


@router.post(
    "",
    response_model=EditingProjectResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createEditingProject",
)
async def create_editing_project(
    payload: EditingProjectCreateRequest,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[EditingProjectService, Depends(_service)],
) -> EditingProjectResponse:
    response.headers["cache-control"] = "no-store"
    try:
        project = await service.create(
            installation_id=installation_id,
            title=payload.title,
            output=payload.output.to_domain(),
            caption_style=payload.caption_style.to_domain(),
        )
    except (InvalidEditingProjectModel, InvalidEditingProjectQuery):
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _project_response(project)


@router.get(
    "",
    response_model=EditingProjectListResponse,
    operation_id="listEditingProjects",
)
async def list_editing_projects(
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[EditingProjectService, Depends(_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EditingProjectListResponse:
    response.headers["cache-control"] = "no-store"
    try:
        page = await service.list(
            installation_id=installation_id,
            cursor=cursor,
            limit=limit,
        )
    except InvalidEditingProjectQuery:
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return EditingProjectListResponse(
        items=[_project_response(project) for project in page.items],
        nextCursor=page.next_cursor,
    )


@router.get(
    "/{project_id}",
    response_model=EditingProjectResponse,
    operation_id="getEditingProject",
)
async def get_editing_project(
    project_id: str,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[EditingProjectService, Depends(_service)],
) -> EditingProjectResponse:
    response.headers["cache-control"] = "no-store"
    try:
        project = await service.get(
            installation_id=installation_id,
            project_id=project_id,
        )
    except InvalidEditingProjectQuery:
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _project_response(project)


__all__ = ["router"]
