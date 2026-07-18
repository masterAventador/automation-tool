"""App-session protected current Installation access probe."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.application.device_sessions import (
    DeviceSessionCapability,
    DeviceSessionRejected,
    DeviceSessionService,
    InvalidDeviceSession,
)
from automation_tool.control_plane.domain import InstallationId, InvalidResourceId

router = APIRouter(prefix="/api/v1/installations", tags=["installations"])
_bearer = HTTPBearer(auto_error=False, scheme_name="AppSession")


class InstallationAccessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    installation_id: str = Field(alias="installationId")
    status: Literal["active"]


def _session_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _access_denied()
    return credentials.credentials


def _service(request: Request) -> DeviceSessionService:
    service = request.app.state.device_session_service
    if not isinstance(service, DeviceSessionService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="installation_access_unavailable",
            message="Installation access check is unavailable",
            retryable=True,
        )
    return service


def _access_denied() -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="installation_access_denied",
        message="Installation access is unavailable",
    )


async def require_current_installation_access(
    session_token: Annotated[str, Depends(_session_token)],
    service: Annotated[DeviceSessionService, Depends(_service)],
) -> InstallationId:
    """Authenticate one App business request and return its Installation scope."""
    try:
        authenticated = await service.authenticate(
            session_token=session_token,
            required_capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
        installation_id = InstallationId.parse(authenticated.installation_id)
    except (DeviceSessionRejected, InvalidDeviceSession, InvalidResourceId):
        raise _access_denied() from None
    return installation_id


@router.get(
    "/current",
    response_model=InstallationAccessResponse,
    operation_id="getCurrentInstallationAccess",
)
async def get_current_installation_access(
    response: Response,
    installation_id: Annotated[InstallationId, Depends(require_current_installation_access)],
) -> InstallationAccessResponse:
    response.headers["cache-control"] = "no-store"
    return InstallationAccessResponse(
        installationId=str(installation_id),
        status="active",
    )


__all__ = ["require_current_installation_access", "router"]
