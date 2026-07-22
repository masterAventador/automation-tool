"""Current-account Installation inventory and revocation HTTP API."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.application.account_devices import (
    AccountDeviceRecord,
    AccountDeviceRevocationRejected,
    AccountDeviceService,
    AccountDevicesUnavailable,
)
from automation_tool.control_plane.application.account_sessions import AccountSessionRejected

router = APIRouter(prefix="/api/v1", tags=["account-devices"])
_access_bearer = HTTPBearer(auto_error=False, scheme_name="AccountAccessToken")


class AccountDeviceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    installation_id: str = Field(alias="installationId")
    status: str
    revision: int
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AccountDeviceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: list[AccountDeviceResponse]


def _service(request: Request) -> AccountDeviceService:
    service = request.app.state.account_device_service
    if not isinstance(service, AccountDeviceService):
        raise AppError(
            status_code=503,
            code="account_devices_unavailable",
            message="Account devices are unavailable",
            retryable=True,
        )
    return service


def _access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_access_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            status_code=401,
            code="account_session_invalid",
            message="Account session is invalid",
        )
    return credentials.credentials


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if not isinstance(value, str):
        raise AccountDevicesUnavailable
    return value


def _response(record: AccountDeviceRecord) -> AccountDeviceResponse:
    return AccountDeviceResponse(
        installationId=str(record.installation_id),
        status=record.status.value,
        revision=record.revision,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _translate(error: Exception) -> AppError:
    if isinstance(error, AccountSessionRejected):
        return AppError(
            status_code=401,
            code="account_session_invalid",
            message="Account session is invalid",
        )
    if isinstance(error, AccountDeviceRevocationRejected):
        return AppError(
            status_code=409,
            code="account_device_revocation_rejected",
            message="Device revocation is rejected",
        )
    if isinstance(error, AccountDevicesUnavailable):
        return AppError(
            status_code=503,
            code="account_devices_unavailable",
            message="Account devices are unavailable",
            retryable=True,
        )
    raise error


@router.get(
    "/account-installations",
    response_model=AccountDeviceListResponse,
    operation_id="listAccountInstallations",
)
async def list_account_installations(
    response: Response,
    access_token: Annotated[str, Depends(_access_token)],
    service: Annotated[AccountDeviceService, Depends(_service)],
) -> AccountDeviceListResponse:
    response.headers["cache-control"] = "no-store"
    try:
        records = await service.list_devices(access_token=access_token)
    except Exception as error:
        raise _translate(error) from None
    return AccountDeviceListResponse(devices=[_response(record) for record in records])


@router.delete(
    "/account-installations/{installation_id}",
    response_model=AccountDeviceResponse,
    operation_id="revokeAccountInstallation",
)
async def revoke_account_installation(
    installation_id: str,
    request: Request,
    response: Response,
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
    access_token: Annotated[str, Depends(_access_token)],
    service: Annotated[AccountDeviceService, Depends(_service)],
) -> AccountDeviceResponse:
    response.headers["cache-control"] = "no-store"
    try:
        record = await service.revoke_device(
            access_token=access_token,
            installation_id=installation_id,
            expected_revision=expected_revision,
            request_id=_request_id(request),
        )
    except Exception as error:
        raise _translate(error) from None
    return _response(record)


__all__ = ["router"]
