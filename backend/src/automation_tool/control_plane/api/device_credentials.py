"""Self-service rotation and revocation for long-lived device credentials."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialRejected,
    DeviceCredentialService,
    InvalidDeviceCredential,
)

router = APIRouter(prefix="/api/v1/device-credentials", tags=["device-credentials"])
_bearer = HTTPBearer(auto_error=False, scheme_name="DeviceCredential")


class RotatedDeviceCredentialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: str
    version: int
    scope: str


class RevokedDeviceCredentialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    status: str


def _credential(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _invalid_credential_error()
    return credentials.credentials


def _service(request: Request) -> DeviceCredentialService:
    service = request.app.state.device_credential_service
    if not isinstance(service, DeviceCredentialService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="device_credentials_unavailable",
            message="Device credential lifecycle is unavailable",
            retryable=True,
        )
    return service


def _invalid_credential_error() -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="device_credential_invalid",
        message="Device credential is invalid",
    )


def _translate_credential_error(error: Exception) -> AppError:
    if isinstance(error, (InvalidDeviceCredential, DeviceCredentialRejected)):
        return _invalid_credential_error()
    raise error


@router.post(
    "/rotations",
    response_model=RotatedDeviceCredentialResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="rotateDeviceCredential",
)
async def rotate_device_credential(
    response: Response,
    credential: Annotated[str, Depends(_credential)],
    service: Annotated[DeviceCredentialService, Depends(_service)],
) -> RotatedDeviceCredentialResponse:
    response.headers["cache-control"] = "no-store"
    try:
        rotated = await service.rotate(credential)
    except Exception as error:
        raise _translate_credential_error(error) from None
    return RotatedDeviceCredentialResponse(
        credential=rotated.credential,
        version=rotated.version,
        scope=rotated.scope,
    )


@router.post(
    "/revocations",
    response_model=RevokedDeviceCredentialResponse,
    status_code=status.HTTP_200_OK,
    operation_id="revokeDeviceCredential",
)
async def revoke_device_credential(
    response: Response,
    credential: Annotated[str, Depends(_credential)],
    service: Annotated[DeviceCredentialService, Depends(_service)],
) -> RevokedDeviceCredentialResponse:
    response.headers["cache-control"] = "no-store"
    try:
        revoked = await service.revoke(credential)
    except Exception as error:
        raise _translate_credential_error(error) from None
    return RevokedDeviceCredentialResponse(
        version=revoked.version,
        status=revoked.status,
    )


__all__ = ["router"]
