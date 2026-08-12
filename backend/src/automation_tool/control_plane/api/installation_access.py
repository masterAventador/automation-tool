"""Current Installation access for the single-machine demo deployment.

The device-identity mechanism was removed: business requests are not
authenticated per device any more. Every request resolves to the one fixed
local installation ensured at startup.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.domain import InstallationId

router = APIRouter(prefix="/api/v1/installations", tags=["installations"])


class InstallationAccessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    installation_id: str = Field(alias="installationId")
    status: Literal["active"]


async def require_current_installation_access(request: Request) -> InstallationId:
    """Return the fixed local Installation scope for this deployment."""
    installation_id = getattr(request.app.state, "local_installation_id", None)
    if type(installation_id) is not InstallationId:
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="installation_access_unavailable",
            message="Installation access check is unavailable",
            retryable=True,
        )
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
