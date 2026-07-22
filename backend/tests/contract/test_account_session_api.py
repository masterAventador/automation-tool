from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.account_sessions import (
    _bearer,
    _request_id,
    _source_address,
    _translate,
)
from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.application.account_sessions import (
    AccountAuthenticationRejected,
    AccountProjection,
    AccountRecoveryRejected,
    AccountSessionRejected,
    AccountSessionService,
    AccountSessionUnavailable,
    IssuedAccountSession,
)
from automation_tool.control_plane.domain import (
    AccountStatus,
    InvalidAccountModel,
    LoginName,
    UserId,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
USER_ID = UserId.parse("123e4567-e89b-42d3-a456-426614174000")
ACCESS_TOKEN = (
    "atas1.123e4567-e89b-42d3-a456-426614174001.YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE"
)
REFRESH_TOKEN = (
    "atrs1.123e4567-e89b-42d3-a456-426614174002.cnJycnJycnJycnJycnJycnJycnJycnJycnJycnJycnI"
)


class FakeAccountSessionService(AccountSessionService):
    def __init__(self) -> None:
        self.login_error: Exception | None = None
        self.recovery_error: Exception | None = None
        self.last_call: tuple[str, tuple[object, ...]] | None = None

    def issued(self) -> IssuedAccountSession:
        return IssuedAccountSession(
            access_token=ACCESS_TOKEN,
            refresh_token=REFRESH_TOKEN,
            access_expires_at=NOW + timedelta(minutes=10),
            refresh_expires_at=NOW + timedelta(days=30),
            account=AccountProjection(
                user_id=USER_ID,
                login_name=LoginName.parse("alice.ops"),
                status=AccountStatus.ACTIVE,
            ),
        )

    async def login(
        self,
        *,
        login_name: object,
        password: object,
        source_address: object,
        request_id: object,
    ) -> IssuedAccountSession:
        self.last_call = ("login", (login_name, password, source_address, request_id))
        if self.login_error is not None:
            raise self.login_error
        return self.issued()

    async def refresh(
        self,
        *,
        refresh_token: object,
        source_address: object,
        request_id: object,
    ) -> IssuedAccountSession:
        self.last_call = ("refresh", (refresh_token, source_address, request_id))
        if self.login_error is not None:
            raise self.login_error
        return self.issued()

    async def logout(self, *, refresh_token: object, request_id: object) -> None:
        self.last_call = ("logout", (refresh_token, request_id))
        if self.login_error is not None:
            raise self.login_error

    async def change_password(
        self,
        *,
        access_token: object,
        current_password: object,
        new_password: object,
        request_id: object,
    ) -> None:
        self.last_call = (
            "change_password",
            (access_token, current_password, new_password, request_id),
        )
        if self.login_error is not None:
            raise self.login_error

    async def recover_password(
        self,
        *,
        recovery_token: object,
        new_password: object,
        request_id: object,
    ) -> None:
        self.last_call = ("recover_password", (recovery_token, new_password, request_id))
        if self.recovery_error is not None:
            raise self.recovery_error


def app_client(service: AccountSessionService | None) -> TestClient:
    return TestClient(create_app(database=None, account_session_service=service))


def test_openapi_exposes_only_closed_account_session_and_password_operations() -> None:
    schema = create_app(database=None).openapi()

    assert set(schema["paths"]["/api/v1/account-sessions"]) == {"post"}
    assert set(schema["paths"]["/api/v1/account-sessions/refresh"]) == {"post"}
    assert set(schema["paths"]["/api/v1/account-sessions/current"]) == {"delete"}
    assert set(schema["paths"]["/api/v1/account-password/changes"]) == {"post"}
    assert set(schema["paths"]["/api/v1/account-password/recovery"]) == {"post"}
    assert "/api/v1/account-password/recovery-requests" not in schema["paths"]
    assert schema["paths"]["/api/v1/account-sessions"]["post"].get("security") is None
    assert schema["paths"]["/api/v1/account-sessions/refresh"]["post"]["security"] == [
        {"AccountRefreshToken": []}
    ]
    assert schema["paths"]["/api/v1/account-sessions/current"]["delete"]["security"] == [
        {"AccountRefreshToken": []}
    ]
    assert schema["paths"]["/api/v1/account-password/changes"]["post"]["security"] == [
        {"AccountAccessToken": []}
    ]
    assert schema["paths"]["/api/v1/account-password/recovery"]["post"]["security"] == [
        {"AccountRecoveryToken": []}
    ]


def test_login_returns_two_opaque_tokens_and_safe_account_projection() -> None:
    service = FakeAccountSessionService()
    response = app_client(service).post(
        "/api/v1/account-sessions",
        headers={"x-request-id": "login-request"},
        json={"loginName": "Alice.OPS", "password": "correct horse battery staple"},
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "accessToken": ACCESS_TOKEN,
        "refreshToken": REFRESH_TOKEN,
        "accessExpiresAt": "2026-07-22T12:10:00Z",
        "refreshExpiresAt": "2026-08-21T12:00:00Z",
        "account": {
            "userId": str(USER_ID),
            "loginName": "alice.ops",
            "status": "active",
        },
    }
    assert service.last_call == (
        "login",
        ("Alice.OPS", "correct horse battery staple", "testclient", "login-request"),
    )


def test_unknown_wrong_locked_and_rate_limited_login_share_one_error() -> None:
    private_values = ("unknown-user", "wrong-private-password", "locked-private-user")
    for private in private_values:
        service = FakeAccountSessionService()
        service.login_error = AccountAuthenticationRejected()
        response = app_client(service).post(
            "/api/v1/account-sessions",
            json={"loginName": private, "password": private},
        )

        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "account_authentication_invalid"
        assert response.json()["error"]["message"] == "Account authentication is invalid"
        assert private not in response.text


def test_refresh_rotates_logout_revokes_and_missing_bearers_are_uniform() -> None:
    service = FakeAccountSessionService()
    client = app_client(service)
    refreshed = client.post(
        "/api/v1/account-sessions/refresh",
        headers={"authorization": f"Bearer {REFRESH_TOKEN}"},
    )
    assert refreshed.status_code == 201
    assert refreshed.headers["cache-control"] == "no-store"
    assert refreshed.json()["refreshToken"] == REFRESH_TOKEN
    assert service.last_call is not None and service.last_call[0] == "refresh"

    logged_out = client.delete(
        "/api/v1/account-sessions/current",
        headers={"authorization": f"Bearer {REFRESH_TOKEN}"},
    )
    assert logged_out.status_code == 204
    assert logged_out.headers["cache-control"] == "no-store"
    assert logged_out.content == b""

    for path, method in (
        ("/api/v1/account-sessions/refresh", client.post),
        ("/api/v1/account-sessions/current", client.delete),
    ):
        missing = method(path)
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "account_session_invalid"

    service.login_error = AccountSessionRejected()
    rejected = client.post(
        "/api/v1/account-sessions/refresh",
        headers={"authorization": f"Bearer {REFRESH_TOKEN}"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "account_session_invalid"


def test_password_change_and_recovery_take_secrets_only_on_fixed_boundaries() -> None:
    service = FakeAccountSessionService()
    client = app_client(service)
    changed = client.post(
        "/api/v1/account-password/changes",
        headers={"authorization": f"Bearer {ACCESS_TOKEN}"},
        json={
            "currentPassword": "correct horse battery staple",
            "newPassword": "replacement horse battery staple",
        },
    )
    assert changed.status_code == 204
    assert changed.headers["cache-control"] == "no-store"

    recovered = client.post(
        "/api/v1/account-password/recovery",
        headers={"authorization": "Bearer atrp1.private-recovery"},
        json={"newPassword": "recovered horse battery staple"},
    )
    assert recovered.status_code == 204
    assert recovered.headers["cache-control"] == "no-store"
    assert service.last_call is not None and service.last_call[0] == "recover_password"


def test_unavailable_validation_and_recovery_fail_without_secret_reflection() -> None:
    unavailable = app_client(None).post(
        "/api/v1/account-sessions",
        json={"loginName": "alice.ops", "password": "correct horse battery staple"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "account_sessions_unavailable"
    assert unavailable.json()["error"]["retryable"] is True

    private = "private-recovery-token"
    service = FakeAccountSessionService()
    service.recovery_error = AccountRecoveryRejected()
    rejected = app_client(service).post(
        "/api/v1/account-password/recovery",
        headers={"authorization": f"Bearer {private}"},
        json={"newPassword": "recovered horse battery staple"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "account_recovery_invalid"
    assert private not in rejected.text

    invalid = app_client(service).post(
        "/api/v1/account-sessions",
        json={"loginName": "alice.ops", "password": "short", "extra": private},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation"
    assert private not in invalid.text


def test_account_api_helpers_fail_closed_for_missing_context_and_known_errors() -> None:
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    with pytest.raises(AccountSessionUnavailable):
        _request_id(request)
    with pytest.raises(AccountAuthenticationRejected):
        _source_address(request)
    with pytest.raises(AppError) as bearer_error:
        _bearer(
            HTTPAuthorizationCredentials(scheme="Basic", credentials="private"),
            error_code="account_session_invalid",
            error_message="Account session is invalid",
        )
    assert bearer_error.value.code == "account_session_invalid"

    assert _translate(AccountSessionUnavailable()).status_code == 503
    assert _translate(InvalidAccountModel()).status_code == 422
    assert (
        _translate(AccountAuthenticationRejected(), session=True).code == "account_session_invalid"
    )
    assert (
        _translate(AccountAuthenticationRejected(), recovery=True).code
        == "account_recovery_invalid"
    )
    unexpected = RuntimeError("fixed")
    with pytest.raises(RuntimeError) as captured:
        _translate(unexpected)
    assert captured.value is unexpected


def test_logout_and_password_change_translate_service_rejections() -> None:
    service = FakeAccountSessionService()
    service.login_error = AccountSessionRejected()
    client = app_client(service)

    logged_out = client.delete(
        "/api/v1/account-sessions/current",
        headers={"authorization": f"Bearer {REFRESH_TOKEN}"},
    )
    changed = client.post(
        "/api/v1/account-password/changes",
        headers={"authorization": f"Bearer {ACCESS_TOKEN}"},
        json={
            "currentPassword": "correct horse battery staple",
            "newPassword": "replacement horse battery staple",
        },
    )

    assert logged_out.status_code == 401
    assert logged_out.json()["error"]["code"] == "account_session_invalid"
    assert changed.status_code == 401
    assert changed.json()["error"]["code"] == "account_session_invalid"
