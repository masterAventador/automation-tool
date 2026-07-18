"""Exchange long-lived device credentials for short, single-capability sessions."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialRejected,
    InvalidDeviceCredential,
)
from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
    DeviceSessionService,
    InvalidDeviceSessionCapability,
)

router = APIRouter(prefix="/api/v1/device-sessions", tags=["device-sessions"])
_bearer = HTTPBearer(auto_error=False, scheme_name="DeviceCredential")


class DeviceSessionExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: DeviceSessionCapability


class DeviceSessionExchangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    session_token: str = Field(alias="sessionToken")
    capability: DeviceSessionCapability
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")


def _credential(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_credential_error()
    return credentials.credentials


def _service(request: Request) -> DeviceSessionService:
    service = request.app.state.device_session_service
    if not isinstance(service, DeviceSessionService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="device_sessions_unavailable",
            message="Device session exchange is unavailable",
            retryable=True,
        )
    return service


def _invalid_credential_error() -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="device_credential_invalid",
        message="Device credential is invalid",
    )


def _translate_exchange_error(error: Exception) -> AppError:
    if isinstance(error, (InvalidDeviceCredential, DeviceCredentialRejected)):
        return _invalid_credential_error()
    if isinstance(error, InvalidDeviceSessionCapability):
        return AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        )
    raise error


@router.post(
    "",
    response_model=DeviceSessionExchangeResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="exchangeDeviceSession",
)
async def exchange_device_session(
    payload: DeviceSessionExchangeRequest,
    response: Response,
    credential: Annotated[str, Depends(_credential)],
    service: Annotated[DeviceSessionService, Depends(_service)],
) -> DeviceSessionExchangeResponse:
    response.headers["cache-control"] = "no-store"
    try:
        issued = await service.exchange(
            device_credential=credential,
            capability=payload.capability,
        )
    except Exception as error:
        raise _translate_exchange_error(error) from None
    return DeviceSessionExchangeResponse(
        sessionToken=issued.session_token,
        capability=issued.capability,
        issuedAt=issued.issued_at,
        expiresAt=issued.expires_at,
    )


__all__ = ["router"]
