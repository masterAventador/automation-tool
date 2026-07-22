"""Closed login, account Session and password lifecycle HTTP API."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.application.account_sessions import (
    AccountAuthenticationRejected,
    AccountRecoveryRejected,
    AccountSessionRejected,
    AccountSessionService,
    AccountSessionUnavailable,
    IssuedAccountSession,
)
from automation_tool.control_plane.domain import AccountStatus, InvalidAccountModel

router = APIRouter(prefix="/api/v1", tags=["account-sessions"])
_access_bearer = HTTPBearer(auto_error=False, scheme_name="AccountAccessToken")
_refresh_bearer = HTTPBearer(auto_error=False, scheme_name="AccountRefreshToken")
_recovery_bearer = HTTPBearer(auto_error=False, scheme_name="AccountRecoveryToken")


class AccountLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    login_name: str = Field(alias="loginName", min_length=3, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=128)


class AccountProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    user_id: str = Field(alias="userId")
    login_name: str = Field(alias="loginName")
    status: AccountStatus


class AccountSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    access_expires_at: datetime = Field(alias="accessExpiresAt")
    refresh_expires_at: datetime = Field(alias="refreshExpiresAt")
    account: AccountProjectionResponse


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    current_password: SecretStr = Field(alias="currentPassword", min_length=12, max_length=128)
    new_password: SecretStr = Field(alias="newPassword", min_length=12, max_length=128)


class PasswordRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    new_password: SecretStr = Field(alias="newPassword", min_length=12, max_length=128)


def _service(request: Request) -> AccountSessionService:
    service = request.app.state.account_session_service
    if not isinstance(service, AccountSessionService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="account_sessions_unavailable",
            message="Account sessions are unavailable",
            retryable=True,
        )
    return service


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if not isinstance(value, str):
        raise AccountSessionUnavailable
    return value


def _source_address(request: Request) -> str:
    if request.client is None:
        raise AccountAuthenticationRejected
    return request.client.host


def _bearer(
    credentials: HTTPAuthorizationCredentials | None,
    *,
    error_code: str,
    error_message: str,
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=error_code,
            message=error_message,
        )
    return credentials.credentials


def _access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_access_bearer)],
) -> str:
    return _bearer(
        credentials,
        error_code="account_session_invalid",
        error_message="Account session is invalid",
    )


def _refresh_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_refresh_bearer)],
) -> str:
    return _bearer(
        credentials,
        error_code="account_session_invalid",
        error_message="Account session is invalid",
    )


def _recovery_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_recovery_bearer)],
) -> str:
    return _bearer(
        credentials,
        error_code="account_recovery_invalid",
        error_message="Account recovery is invalid",
    )


def _session_response(issued: IssuedAccountSession) -> AccountSessionResponse:
    return AccountSessionResponse(
        accessToken=issued.access_token,
        refreshToken=issued.refresh_token,
        accessExpiresAt=issued.access_expires_at,
        refreshExpiresAt=issued.refresh_expires_at,
        account=AccountProjectionResponse(
            userId=str(issued.account.user_id),
            loginName=issued.account.login_name.value,
            status=issued.account.status,
        ),
    )


def _translate(
    error: Exception,
    *,
    recovery: bool = False,
    session: bool = False,
) -> AppError:
    if isinstance(error, AccountSessionRejected):
        return AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="account_session_invalid",
            message="Account session is invalid",
        )
    if isinstance(error, AccountAuthenticationRejected):
        return AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=(
                "account_recovery_invalid"
                if recovery
                else "account_session_invalid"
                if session
                else "account_authentication_invalid"
            ),
            message=(
                "Account recovery is invalid"
                if recovery
                else "Account session is invalid"
                if session
                else "Account authentication is invalid"
            ),
        )
    if isinstance(error, AccountRecoveryRejected):
        return AppError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="account_recovery_invalid",
            message="Account recovery is invalid",
        )
    if isinstance(error, AccountSessionUnavailable):
        return AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="account_sessions_unavailable",
            message="Account sessions are unavailable",
            retryable=True,
        )
    if isinstance(error, InvalidAccountModel):
        return AppError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation",
            message="Request validation failed",
        )
    raise error


@router.post(
    "/account-sessions",
    response_model=AccountSessionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="loginAccountSession",
)
async def login_account_session(
    payload: AccountLoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AccountSessionService, Depends(_service)],
) -> AccountSessionResponse:
    response.headers["cache-control"] = "no-store"
    try:
        issued = await service.login(
            login_name=payload.login_name,
            password=payload.password.get_secret_value(),
            source_address=_source_address(request),
            request_id=_request_id(request),
        )
    except Exception as error:
        raise _translate(error) from None
    return _session_response(issued)


@router.post(
    "/account-sessions/refresh",
    response_model=AccountSessionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="refreshAccountSession",
)
async def refresh_account_session(
    request: Request,
    response: Response,
    refresh_token: Annotated[str, Depends(_refresh_token)],
    service: Annotated[AccountSessionService, Depends(_service)],
) -> AccountSessionResponse:
    response.headers["cache-control"] = "no-store"
    try:
        issued = await service.refresh(
            refresh_token=refresh_token,
            source_address=_source_address(request),
            request_id=_request_id(request),
        )
    except Exception as error:
        raise _translate(error, session=True) from None
    return _session_response(issued)


@router.delete(
    "/account-sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="logoutAccountSession",
)
async def logout_account_session(
    request: Request,
    refresh_token: Annotated[str, Depends(_refresh_token)],
    service: Annotated[AccountSessionService, Depends(_service)],
) -> Response:
    try:
        await service.logout(
            refresh_token=refresh_token,
            request_id=_request_id(request),
        )
    except Exception as error:
        raise _translate(error, session=True) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"cache-control": "no-store"})


@router.post(
    "/account-password/changes",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="changeAccountPassword",
)
async def change_account_password(
    payload: PasswordChangeRequest,
    request: Request,
    access_token: Annotated[str, Depends(_access_token)],
    service: Annotated[AccountSessionService, Depends(_service)],
) -> Response:
    try:
        await service.change_password(
            access_token=access_token,
            current_password=payload.current_password.get_secret_value(),
            new_password=payload.new_password.get_secret_value(),
            request_id=_request_id(request),
        )
    except Exception as error:
        raise _translate(error) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"cache-control": "no-store"})


@router.post(
    "/account-password/recovery",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="recoverAccountPassword",
)
async def recover_account_password(
    payload: PasswordRecoveryRequest,
    request: Request,
    recovery_token: Annotated[str, Depends(_recovery_token)],
    service: Annotated[AccountSessionService, Depends(_service)],
) -> Response:
    try:
        await service.recover_password(
            recovery_token=recovery_token,
            new_password=payload.new_password.get_secret_value(),
            request_id=_request_id(request),
        )
    except Exception as error:
        raise _translate(error, recovery=True) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"cache-control": "no-store"})


__all__ = ["router"]
