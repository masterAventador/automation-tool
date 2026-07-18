"""Public challenge/response API for installation registration."""

import base64
import binascii
import re
from datetime import datetime
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.application.registration import (
    BootstrapCredentialRejected,
    BootstrapRegistrationDenied,
    InstallationAlreadyRegistered,
    InstallationRegistrationService,
    InvalidRegistrationRequest,
    RegistrationChallengeExpired,
    RegistrationChallengeUsed,
    RegistrationProofRejected,
)

router = APIRouter(prefix="/api/v1/installations", tags=["installations"])
_bearer = HTTPBearer(auto_error=False, scheme_name="DemoBootstrap")
_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")


class RegistrationChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    environment_id: str = Field(alias="environmentId", min_length=1, max_length=64)
    device_public_key: str = Field(alias="devicePublicKey", min_length=43, max_length=43)


class RegistrationChallengeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    challenge_id: UUID = Field(alias="challengeId")
    signing_payload: str = Field(alias="signingPayload")
    expires_at: datetime = Field(alias="expiresAt")


class InstallationRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    challenge_id: UUID = Field(alias="challengeId")
    environment_id: str = Field(alias="environmentId", min_length=1, max_length=64)
    signing_payload: str = Field(alias="signingPayload", min_length=1, max_length=2731)
    signature: str = Field(min_length=86, max_length=86)

    @field_validator("challenge_id")
    @classmethod
    def require_uuid_v4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("UUIDv4 required")
        return value


class InstallationRegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    installation_id: UUID = Field(alias="installationId")
    status: str
    revision: int
    device_credential: "IssuedDeviceCredentialResponse" = Field(alias="deviceCredential")


class IssuedDeviceCredentialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    credential: str
    version: int
    scope: str


def _decode_base64url(value: str, *, exact_length: int | None = None) -> bytes:
    if _BASE64URL_PATTERN.fullmatch(value) is None:
        raise InvalidRegistrationRequest
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
    except (ValueError, binascii.Error):
        raise InvalidRegistrationRequest from None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value or (exact_length is not None and len(decoded) != exact_length):
        raise InvalidRegistrationRequest
    return decoded


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _bootstrap_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="bootstrap_invalid",
            message="Bootstrap credential is invalid",
        )
    return credentials.credentials


def _service(request: Request) -> InstallationRegistrationService:
    service = request.app.state.registration_service
    if not isinstance(service, InstallationRegistrationService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="registration_unavailable",
            message="Installation registration is unavailable",
            retryable=True,
        )
    return service


def _translate_registration_error(error: Exception) -> AppError:
    if isinstance(error, BootstrapCredentialRejected):
        return AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="bootstrap_invalid",
            message="Bootstrap credential is invalid",
        )
    if isinstance(error, BootstrapRegistrationDenied):
        return AppError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="bootstrap_denied",
            message="Bootstrap registration is denied",
        )
    if isinstance(error, InvalidRegistrationRequest):
        return AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        )
    if isinstance(error, RegistrationProofRejected):
        return AppError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="registration_proof_invalid",
            message="Installation registration proof is invalid",
        )
    if isinstance(error, RegistrationChallengeExpired):
        return AppError(
            status_code=status.HTTP_410_GONE,
            code="registration_challenge_expired",
            message="Installation registration challenge expired",
        )
    if isinstance(error, RegistrationChallengeUsed):
        return AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="registration_challenge_used",
            message="Installation registration challenge was already used",
        )
    if isinstance(error, InstallationAlreadyRegistered):
        return AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="installation_exists",
            message="Installation already exists",
        )
    raise error


@router.post(
    "/registration-challenges",
    response_model=RegistrationChallengeResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
    operation_id="issueInstallationRegistrationChallenge",
)
async def issue_registration_challenge(
    body: RegistrationChallengeRequest,
    response: Response,
    bootstrap_token: Annotated[str, Depends(_bootstrap_token)],
    service: Annotated[InstallationRegistrationService, Depends(_service)],
) -> RegistrationChallengeResponse:
    response.headers["cache-control"] = "no-store"
    try:
        challenge = await service.issue_challenge(
            bootstrap_token=bootstrap_token,
            environment_id=body.environment_id,
            device_public_key=_decode_base64url(body.device_public_key, exact_length=32),
        )
    except Exception as error:
        raise _translate_registration_error(error) from None
    return RegistrationChallengeResponse(
        challengeId=challenge.challenge_id,
        signingPayload=_encode_base64url(challenge.signing_payload),
        expiresAt=challenge.expires_at,
    )


@router.post(
    "",
    response_model=InstallationRegistrationResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
    operation_id="completeInstallationRegistration",
)
async def complete_installation_registration(
    body: InstallationRegistrationRequest,
    response: Response,
    bootstrap_token: Annotated[str, Depends(_bootstrap_token)],
    service: Annotated[InstallationRegistrationService, Depends(_service)],
) -> InstallationRegistrationResponse:
    response.headers["cache-control"] = "no-store"
    try:
        registered = await service.complete_registration(
            bootstrap_token=bootstrap_token,
            environment_id=body.environment_id,
            challenge_id=body.challenge_id,
            signing_payload=_decode_base64url(body.signing_payload),
            signature=_decode_base64url(body.signature, exact_length=64),
        )
    except Exception as error:
        raise _translate_registration_error(error) from None
    return InstallationRegistrationResponse(
        installationId=registered.installation_id,
        status=registered.status,
        revision=registered.revision,
        deviceCredential=IssuedDeviceCredentialResponse(
            credential=registered.device_credential.credential,
            version=registered.device_credential.version,
            scope=registered.device_credential.scope,
        ),
    )


__all__ = ["router"]
