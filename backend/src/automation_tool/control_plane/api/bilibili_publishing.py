"""Authenticated desktop bridge for official Bilibili archive publishing."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveFields,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
    BilibiliGatewayUnreachable,
    BilibiliPublishMaterial,
    BilibiliPublishPhase,
    BilibiliPublishStepFailed,
)
from automation_tool.control_plane.application.bilibili_publishing_runtime import (
    BilibiliCredentialRotation,
    BilibiliPublishingCredential,
    BilibiliPublishingRuntime,
    BilibiliPublishRuntimeResult,
)
from automation_tool.control_plane.domain.resource_ids import InstallationId
from automation_tool.control_plane.domain.video_publishing import PublishFailureCode, PublishJobId

router = APIRouter(prefix="/api/v1/publishing/bilibili", tags=["publishing"])

_STREAM_CHUNK_BYTES = 1024 * 1024


class BilibiliCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    client_id: str = Field(alias="clientId", min_length=1, max_length=256)
    app_secret: SecretStr = Field(alias="appSecret", min_length=1, max_length=4096)
    access_token: SecretStr = Field(alias="accessToken", min_length=1, max_length=4096)
    refresh_token: SecretStr = Field(alias="refreshToken", min_length=1, max_length=4096)
    expires_at_epoch_seconds: int = Field(alias="expiresAtEpochSeconds", gt=0)

    @field_validator("client_id")
    @classmethod
    def compact_client_id(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError
        return value


class BilibiliMaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    size_bytes: int = Field(alias="sizeBytes", gt=0)
    duration_seconds: int = Field(alias="durationSeconds", gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BilibiliArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    title: str = Field(min_length=1, max_length=80)
    tid: int = Field(gt=0)
    tag: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=250)
    no_reprint: Literal[0, 1] = Field(alias="noReprint")


class PrepareBilibiliPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: BilibiliCredentialRequest
    material: BilibiliMaterialRequest
    archive: BilibiliArchiveRequest


class BilibiliCredentialRotationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    expires_at_epoch_seconds: int = Field(alias="expiresAtEpochSeconds")


class BilibiliPublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    publish_job_id: str = Field(alias="publishJobId")
    phase: BilibiliPublishPhase
    request_digest: str = Field(alias="requestDigest")
    resource_id: str | None = Field(alias="resourceId")
    replayed: bool
    session_token: str | None = Field(alias="sessionToken")
    credential_rotation: BilibiliCredentialRotationResponse | None = Field(
        alias="credentialRotation"
    )


def _runtime(request: Request) -> BilibiliPublishingRuntime:
    runtime = request.app.state.bilibili_publishing_runtime
    if not isinstance(runtime, BilibiliPublishingRuntime):
        raise _unavailable()
    return runtime


def _unavailable() -> AppError:
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="bilibili_publishing_unavailable",
        message="Bilibili publishing is unavailable",
        retryable=True,
    )


def _invalid() -> AppError:
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="bilibili_publishing_invalid",
        message="Bilibili publishing request is invalid",
        retryable=False,
    )


def _platform_failure(error: BilibiliPublishStepFailed) -> AppError:
    retryable = error.rejection.failure_code is PublishFailureCode.DEPENDENCY_UNAVAILABLE
    return AppError(
        status_code=(
            status.HTTP_503_SERVICE_UNAVAILABLE if retryable else status.HTTP_409_CONFLICT
        ),
        code="bilibili_platform_rejected",
        message="Bilibili rejected the publishing step",
        retryable=retryable,
    )


def _job(value: str) -> PublishJobId:
    try:
        return PublishJobId.parse(value)
    except ValueError:
        raise _invalid() from None


def _rotation(
    value: BilibiliCredentialRotation | None,
) -> BilibiliCredentialRotationResponse | None:
    if value is None:
        return None
    return BilibiliCredentialRotationResponse(
        accessToken=value.access_token,
        refreshToken=value.refresh_token,
        expiresAtEpochSeconds=value.expires_at_epoch_seconds,
    )


def _response(
    publish_job_id: PublishJobId,
    result: BilibiliPublishRuntimeResult,
    *,
    session_token: str | None,
) -> BilibiliPublishResponse:
    return BilibiliPublishResponse(
        publishJobId=str(publish_job_id),
        phase=result.phase,
        requestDigest=result.request_digest,
        resourceId=result.resource_id,
        replayed=result.replayed,
        sessionToken=session_token,
        credentialRotation=_rotation(result.credential_rotation),
    )


@router.post(
    "/jobs/{publish_job_id}",
    response_model=BilibiliPublishResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="prepareBilibiliPublish",
)
async def prepare_bilibili_publish(
    publish_job_id: str,
    body: PrepareBilibiliPublishRequest,
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    runtime: Annotated[BilibiliPublishingRuntime, Depends(_runtime)],
) -> BilibiliPublishResponse:
    response.headers["cache-control"] = "no-store"
    job = _job(publish_job_id)
    try:
        token, result = await runtime.prepare(
            installation_id=installation_id,
            publish_job_id=job,
            credential=BilibiliPublishingCredential(
                client_id=body.credential.client_id,
                app_secret=body.credential.app_secret.get_secret_value(),
                access_token=body.credential.access_token.get_secret_value(),
                refresh_token=body.credential.refresh_token.get_secret_value(),
                expires_at_epoch_seconds=body.credential.expires_at_epoch_seconds,
            ),
            material=BilibiliPublishMaterial(
                file_name=f"{job}.mp4",
                size_bytes=body.material.size_bytes,
                duration_seconds=body.material.duration_seconds,
                sha256=body.material.sha256,
            ),
            fields=BilibiliArchiveFields(
                title=body.archive.title,
                tid=body.archive.tid,
                tag=body.archive.tag,
                copyright=1,
                description=body.archive.description,
                source=None,
                no_reprint=body.archive.no_reprint,
            ),
        )
    except BilibiliArchivePublishRejected:
        raise _invalid() from None
    except (BilibiliArchivePublishUnavailable, BilibiliGatewayUnreachable):
        raise _unavailable() from None
    return _response(job, result, session_token=token)


async def _stream_private_video(
    request: Request,
    *,
    destination: Path,
    maximum_bytes: int,
    declared_bytes: int,
) -> tuple[int, str]:
    if declared_bytes < 1 or declared_bytes > maximum_bytes:
        raise _invalid()
    written = 0
    digest = hashlib.sha256()
    try:
        with destination.open("xb") as handle:
            os.chmod(destination, 0o600)
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > declared_bytes or written > maximum_bytes:
                    raise _invalid()
                handle.write(chunk)
                digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except AppError:
        raise
    except (OSError, RuntimeError):
        raise _unavailable() from None
    if written != declared_bytes:
        raise _invalid()
    return written, digest.hexdigest()


@router.put(
    "/jobs/{publish_job_id}/video",
    response_model=BilibiliPublishResponse,
    operation_id="uploadBilibiliPublishVideo",
)
async def upload_bilibili_publish_video(
    publish_job_id: str,
    request: Request,
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    runtime: Annotated[BilibiliPublishingRuntime, Depends(_runtime)],
    publish_session: Annotated[
        str,
        Header(
            alias="x-bilibili-publish-session",
            min_length=32,
            max_length=128,
        ),
    ],
    content_length: Annotated[int, Header(alias="content-length", gt=0)],
) -> BilibiliPublishResponse:
    response.headers["cache-control"] = "no-store"
    job = _job(publish_job_id)
    try:
        with tempfile.TemporaryDirectory(prefix="automation-tool-bilibili-") as raw_root:
            root = Path(raw_root)
            os.chmod(root, 0o700)
            destination = root / f"{job}.mp4"
            await _stream_private_video(
                request,
                destination=destination,
                maximum_bytes=runtime.maximum_video_bytes,
                declared_bytes=content_length,
            )
            result = await runtime.upload_video(
                installation_id=installation_id,
                publish_job_id=job,
                session_token=publish_session,
                material_root=root,
            )
    except BilibiliArchivePublishRejected:
        raise _invalid() from None
    except BilibiliPublishStepFailed as error:
        raise _platform_failure(error) from None
    except (BilibiliArchivePublishUnavailable, BilibiliGatewayUnreachable):
        raise _unavailable() from None
    return _response(job, result, session_token=None)


@router.post(
    "/jobs/{publish_job_id}/submission",
    response_model=BilibiliPublishResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="submitBilibiliPublish",
)
async def submit_bilibili_publish(
    publish_job_id: str,
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    runtime: Annotated[BilibiliPublishingRuntime, Depends(_runtime)],
    publish_session: Annotated[
        str,
        Header(
            alias="x-bilibili-publish-session",
            min_length=32,
            max_length=128,
        ),
    ],
) -> BilibiliPublishResponse:
    response.headers["cache-control"] = "no-store"
    job = _job(publish_job_id)
    try:
        result = await runtime.submit(
            installation_id=installation_id,
            publish_job_id=job,
            session_token=publish_session,
        )
    except BilibiliArchivePublishRejected:
        raise _invalid() from None
    except (BilibiliArchivePublishUnavailable, BilibiliGatewayUnreachable):
        raise _unavailable() from None
    return _response(job, result, session_token=None)


@router.delete(
    "/jobs/{publish_job_id}/session",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="cancelBilibiliPublishSession",
)
async def cancel_bilibili_publish_session(
    publish_job_id: str,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
    runtime: Annotated[BilibiliPublishingRuntime, Depends(_runtime)],
    publish_session: Annotated[
        str,
        Header(
            alias="x-bilibili-publish-session",
            min_length=32,
            max_length=128,
        ),
    ],
) -> Response:
    job = _job(publish_job_id)
    try:
        await runtime.cancel(
            installation_id=installation_id,
            publish_job_id=job,
            session_token=publish_session,
        )
    except BilibiliArchivePublishRejected:
        raise _invalid() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"cache-control": "no-store"})


__all__ = ["router"]
