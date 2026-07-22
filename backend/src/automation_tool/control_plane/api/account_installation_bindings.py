"""Account-authenticated device ownership binding HTTP API."""

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
from automation_tool.control_plane.application.account_installation_bindings import (
    AccountInstallationBindingService,
    AccountInstallationBindingUnavailable,
    BindingChallengeExpired,
    BindingChallengeUsed,
    BindingProofRejected,
    CrossAccountBindingRejected,
    InvalidBindingRequest,
    RevokedInstallationBindingRejected,
)
from automation_tool.control_plane.application.account_sessions import (
    AccountSessionRejected,
    AccountSessionUnavailable,
)

router = APIRouter(prefix="/api/v1/account-installations", tags=["account-installations"])
_bearer = HTTPBearer(auto_error=False, scheme_name="AccountAccessToken")
_BASE64URL: Final = re.compile(r"[A-Za-z0-9_-]+")


class BindingChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    device_public_key: str = Field(alias="devicePublicKey", min_length=43, max_length=43)


class BindingChallengeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    challenge_id: UUID = Field(alias="challengeId")
    signing_payload: str = Field(alias="signingPayload")
    expires_at: datetime = Field(alias="expiresAt")


class BindingCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    challenge_id: UUID = Field(alias="challengeId")
    signing_payload: str = Field(alias="signingPayload", min_length=1, max_length=2731)
    signature: str = Field(min_length=86, max_length=86)

    @field_validator("challenge_id")
    @classmethod
    def require_uuid_v4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("UUIDv4 required")
        return value


class IssuedCredentialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: str
    version: int
    scope: str


class BindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    installation_id: UUID = Field(alias="installationId")
    status: str
    revision: int
    device_credential: IssuedCredentialResponse = Field(alias="deviceCredential")


def _decode(value: str, *, exact_length: int | None = None) -> bytes:
    if _BASE64URL.fullmatch(value) is None:
        raise InvalidBindingRequest
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
    except (ValueError, binascii.Error):
        raise InvalidBindingRequest from None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value or (exact_length is not None and len(decoded) != exact_length):
        raise InvalidBindingRequest
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="account_session_invalid",
            message="Account session is invalid",
        )
    return credentials.credentials


def _service(request: Request) -> AccountInstallationBindingService:
    service = request.app.state.account_installation_binding_service
    if not isinstance(service, AccountInstallationBindingService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="account_installation_binding_unavailable",
            message="Account Installation binding is unavailable",
            retryable=True,
        )
    return service


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, str):
        raise AccountInstallationBindingUnavailable
    return request_id


def _translate(error: Exception) -> AppError:
    if isinstance(error, AccountSessionRejected):
        return AppError(
            status_code=401,
            code="account_session_invalid",
            message="Account session is invalid",
        )
    if isinstance(error, AccountSessionUnavailable):
        return AppError(
            status_code=503,
            code="account_installation_binding_unavailable",
            message="Account Installation binding is unavailable",
            retryable=True,
        )
    if isinstance(error, BindingProofRejected):
        return AppError(
            status_code=403,
            code="installation_binding_proof_invalid",
            message="Installation binding proof is invalid",
        )
    if isinstance(error, BindingChallengeExpired):
        return AppError(
            status_code=410,
            code="installation_binding_challenge_expired",
            message="Installation binding challenge expired",
        )
    if isinstance(error, BindingChallengeUsed):
        return AppError(
            status_code=409,
            code="installation_binding_challenge_used",
            message="Installation binding challenge was already used",
        )
    if isinstance(error, CrossAccountBindingRejected):
        return AppError(
            status_code=409,
            code="installation_owned_by_other_account",
            message="Installation belongs to another account",
        )
    if isinstance(error, RevokedInstallationBindingRejected):
        return AppError(
            status_code=409, code="installation_revoked", message="Installation is revoked"
        )
    if isinstance(error, InvalidBindingRequest):
        return AppError(status_code=422, code="validation", message="Request validation failed")
    if isinstance(error, AccountInstallationBindingUnavailable):
        return AppError(
            status_code=503,
            code="account_installation_binding_unavailable",
            message="Account Installation binding is unavailable",
            retryable=True,
        )
    raise error


@router.post(
    "/binding-challenges",
    response_model=BindingChallengeResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
    operation_id="issueAccountInstallationBindingChallenge",
)
async def issue_binding_challenge(
    body: BindingChallengeRequest,
    response: Response,
    access_token: Annotated[str, Depends(_access_token)],
    service: Annotated[AccountInstallationBindingService, Depends(_service)],
) -> BindingChallengeResponse:
    response.headers["cache-control"] = "no-store"
    try:
        issued = await service.issue_challenge(
            access_token=access_token,
            device_public_key=_decode(body.device_public_key, exact_length=32),
        )
    except Exception as error:
        raise _translate(error) from None
    return BindingChallengeResponse(
        challengeId=issued.challenge_id,
        signingPayload=_encode(issued.signing_payload),
        expiresAt=issued.expires_at,
    )


@router.post(
    "/bindings",
    response_model=BindingResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
    operation_id="completeAccountInstallationBinding",
)
async def complete_binding(
    body: BindingCompletionRequest,
    request: Request,
    response: Response,
    access_token: Annotated[str, Depends(_access_token)],
    service: Annotated[AccountInstallationBindingService, Depends(_service)],
) -> BindingResponse:
    response.headers["cache-control"] = "no-store"
    try:
        bound = await service.complete_binding(
            access_token=access_token,
            challenge_id=body.challenge_id,
            signing_payload=_decode(body.signing_payload),
            signature=_decode(body.signature, exact_length=64),
            request_id=_request_id(request),
        )
    except Exception as error:
        raise _translate(error) from None
    return BindingResponse(
        installationId=bound.installation_id,
        status=bound.status,
        revision=bound.revision,
        deviceCredential=IssuedCredentialResponse(
            credential=bound.device_credential.credential,
            version=bound.device_credential.version,
            scope=bound.device_credential.scope,
        ),
    )


__all__ = ["router"]
