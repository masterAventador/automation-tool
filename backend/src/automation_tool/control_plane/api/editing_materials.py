"""App-session protected local-editing material registration and queries."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
)

from automation_tool.control_plane.api.editing_errors import translate_editing_error
from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.materials import (
    InvalidMaterialQuery,
    MaterialService,
)
from automation_tool.control_plane.domain import (
    MAX_DESCRIPTION_CHARACTERS,
    MAX_SHOT_BOUNDARIES,
    MAX_SPEECH_SEGMENTS,
    MAX_TAGS,
    MAX_TRANSCRIPT_CHARACTERS,
    DescriptionSource,
    InstallationId,
    InvalidMaterialModel,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId

router = APIRouter(prefix="/api/v1/editing-materials", tags=["editing-materials"])

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_RFC3339_DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


def _require_datetime_wire_string(value: object) -> object:
    """Keep the JSON boundary equal to OpenAPI's RFC 3339 date-time string."""
    if not isinstance(value, str) or _RFC3339_DATETIME_PATTERN.fullmatch(value) is None:
        raise ValueError("Date-time input must be an RFC 3339 string")
    return value


StrictAwareDatetime = Annotated[
    AwareDatetime,
    BeforeValidator(_require_datetime_wire_string),
]


class EditingMaterialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str = Field(alias="materialId", strict=True)
    kind: MaterialKind
    duration_ms: StrictInt | None = Field(alias="durationMs")
    width: StrictInt | None
    height: StrictInt | None
    content_digest: str = Field(
        alias="contentDigest",
        min_length=64,
        max_length=64,
        pattern=_SHA256_PATTERN,
        strict=True,
    )
    has_audio: StrictBool = Field(alias="hasAudio")
    audio_loudness_lufs: StrictFloat | None = Field(alias="audioLoudnessLufs")
    has_speech: StrictBool = Field(alias="hasSpeech")
    speech_segments_ms: list[tuple[StrictInt, StrictInt]] = Field(
        alias="speechSegmentsMs",
        max_length=MAX_SPEECH_SEGMENTS,
    )
    speech_transcript: str | None = Field(
        alias="speechTranscript",
        max_length=MAX_TRANSCRIPT_CHARACTERS,
        strict=True,
    )
    shot_boundaries_ms: list[StrictInt] = Field(
        alias="shotBoundariesMs",
        max_length=MAX_SHOT_BOUNDARIES,
    )
    ai_description: str | None = Field(
        alias="aiDescription",
        max_length=MAX_DESCRIPTION_CHARACTERS,
        strict=True,
    )
    ai_tags: list[str] = Field(alias="aiTags", max_length=MAX_TAGS)
    description_source: DescriptionSource = Field(alias="descriptionSource")
    described_at: StrictAwareDatetime | None = Field(alias="describedAt")

    def to_domain(self) -> Material:
        return Material.register(
            material_id=MaterialId.parse(self.material_id),
            kind=self.kind,
            duration_ms=self.duration_ms,
            width=self.width,
            height=self.height,
            content_digest=self.content_digest,
            has_audio=self.has_audio,
            audio_loudness_lufs=self.audio_loudness_lufs,
            has_speech=self.has_speech,
            speech_segments_ms=tuple(self.speech_segments_ms),
            speech_transcript=self.speech_transcript,
            shot_boundaries_ms=tuple(self.shot_boundaries_ms),
            ai_description=self.ai_description,
            ai_tags=tuple(self.ai_tags),
            description_source=self.description_source,
            described_at=self.described_at,
        )


class EditingMaterialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str = Field(alias="materialId")
    kind: MaterialKind
    duration_ms: int | None = Field(alias="durationMs")
    width: int | None
    height: int | None
    content_digest: str = Field(alias="contentDigest")
    has_audio: bool = Field(alias="hasAudio")
    audio_loudness_lufs: float | None = Field(alias="audioLoudnessLufs")
    has_speech: bool = Field(alias="hasSpeech")
    speech_segments_ms: list[tuple[int, int]] = Field(alias="speechSegmentsMs")
    speech_transcript: str | None = Field(alias="speechTranscript")
    shot_boundaries_ms: list[int] = Field(alias="shotBoundariesMs")
    ai_description: str | None = Field(alias="aiDescription")
    ai_tags: list[str] = Field(alias="aiTags")
    description_source: DescriptionSource = Field(alias="descriptionSource")
    described_at: datetime | None = Field(alias="describedAt")


class UserMaterialDescriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["user"]
    description: str = Field(
        min_length=1,
        max_length=MAX_DESCRIPTION_CHARACTERS,
        strict=True,
    )


class AiMaterialDescriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["ai"]
    description: str = Field(
        min_length=1,
        max_length=MAX_DESCRIPTION_CHARACTERS,
        strict=True,
    )
    tags: list[str] = Field(max_length=MAX_TAGS)
    shot_boundaries_ms: list[StrictInt] = Field(
        alias="shotBoundariesMs",
        min_length=1,
        max_length=MAX_SHOT_BOUNDARIES,
    )
    described_at: StrictAwareDatetime = Field(alias="describedAt")


MaterialDescriptionRequest = Annotated[
    UserMaterialDescriptionRequest | AiMaterialDescriptionRequest,
    Field(discriminator="source"),
]


def _service(request: Request) -> MaterialService:
    service = request.app.state.material_service
    if not isinstance(service, MaterialService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="editing_materials_unavailable",
            message="Editing material service is unavailable",
            retryable=True,
        )
    return service


def _validation_error() -> AppError:
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation",
        message="Request validation failed",
    )


def _material_response(material: Material) -> EditingMaterialResponse:
    return EditingMaterialResponse(
        materialId=str(material.material_id),
        kind=material.kind,
        durationMs=material.duration_ms,
        width=material.width,
        height=material.height,
        contentDigest=material.content_digest,
        hasAudio=material.has_audio,
        audioLoudnessLufs=material.audio_loudness_lufs,
        hasSpeech=material.has_speech,
        speechSegmentsMs=list(material.speech_segments_ms),
        speechTranscript=material.speech_transcript,
        shotBoundariesMs=list(material.shot_boundaries_ms),
        aiDescription=material.ai_description,
        aiTags=list(material.ai_tags),
        descriptionSource=material.description_source,
        describedAt=material.described_at,
    )


@router.post(
    "",
    response_model=EditingMaterialResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="registerEditingMaterial",
)
async def register_editing_material(
    payload: EditingMaterialCreateRequest,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[MaterialService, Depends(_service)],
) -> EditingMaterialResponse:
    response.headers["cache-control"] = "no-store"
    try:
        material = await service.register(
            installation_id=installation_id,
            material=payload.to_domain(),
        )
    except (InvalidMaterialModel, InvalidMaterialQuery, InvalidResourceId):
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _material_response(material)


@router.get(
    "",
    response_model=EditingMaterialResponse,
    operation_id="findEditingMaterialByDigest",
)
async def find_editing_material_by_digest(
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[MaterialService, Depends(_service)],
    content_digest: Annotated[
        str,
        Query(
            alias="contentDigest",
            min_length=64,
            max_length=64,
            pattern=_SHA256_PATTERN,
        ),
    ],
) -> EditingMaterialResponse:
    response.headers["cache-control"] = "no-store"
    try:
        material = await service.find_by_digest(
            installation_id=installation_id,
            content_digest=content_digest,
        )
    except InvalidMaterialQuery:
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _material_response(material)


@router.get(
    "/{material_id}",
    response_model=EditingMaterialResponse,
    operation_id="getEditingMaterial",
)
async def get_editing_material(
    material_id: str,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[MaterialService, Depends(_service)],
) -> EditingMaterialResponse:
    response.headers["cache-control"] = "no-store"
    try:
        material = await service.get(
            installation_id=installation_id,
            material_id=material_id,
        )
    except InvalidMaterialQuery:
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _material_response(material)


@router.put(
    "/{material_id}/description",
    response_model=EditingMaterialResponse,
    operation_id="updateEditingMaterialDescription",
)
async def update_editing_material_description(
    material_id: str,
    payload: MaterialDescriptionRequest,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[MaterialService, Depends(_service)],
) -> EditingMaterialResponse:
    response.headers["cache-control"] = "no-store"
    try:
        if isinstance(payload, UserMaterialDescriptionRequest):
            source = DescriptionSource.USER
            tags: tuple[str, ...] = ()
            shot_boundaries_ms = None
            described_at = None
        else:
            source = DescriptionSource.AI
            tags = tuple(payload.tags)
            shot_boundaries_ms = tuple(payload.shot_boundaries_ms)
            described_at = payload.described_at
        material = await service.update_understanding(
            installation_id=installation_id,
            material_id=material_id,
            source=source,
            description=payload.description,
            tags=tags,
            shot_boundaries_ms=shot_boundaries_ms,
            described_at=described_at,
        )
    except (InvalidMaterialModel, InvalidMaterialQuery):
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _material_response(material)


__all__ = ["router"]
