import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.device_sessions import _translate_exchange_error
from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialFactory,
    ParsedDeviceCredential,
)
from automation_tool.control_plane.application.device_sessions import (
    AuthenticatedDeviceSession,
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionService,
    InvalidDeviceSessionCapability,
    IssuedDeviceSession,
    ParsedDeviceSession,
    PendingDeviceSession,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


@dataclass
class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeRepository:
    async def issue(
        self,
        *,
        presented_credential: ParsedDeviceCredential,
        pending_session: PendingDeviceSession,
        capability: DeviceSessionCapability,
        issued_at: datetime,
        not_before: datetime,
        expires_at: datetime,
    ) -> IssuedDeviceSession:
        return IssuedDeviceSession(
            session_id=pending_session.session_id,
            installation_id=uuid4(),
            credential_id=presented_credential.credential_id,
            credential_version=3,
            session_token=pending_session.session_token,
            capability=capability,
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
        )

    async def authenticate(
        self,
        *,
        presented_session: ParsedDeviceSession,
        required_capability: DeviceSessionCapability,
        authenticated_at: datetime,
    ) -> AuthenticatedDeviceSession:
        raise AssertionError("not used by the exchange API")


def credential() -> str:
    return (
        DeviceCredentialFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        )
        .create()
        .credential
    )


def service() -> DeviceSessionService:
    return DeviceSessionService(
        repository=FakeRepository(),
        clock=FixedClock(),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )


def test_openapi_exposes_one_bearer_protected_session_exchange() -> None:
    schema = create_app(database=None).openapi()
    exchange = schema["paths"]["/api/v1/device-sessions"]

    assert set(exchange) == {"post"}
    assert exchange["post"]["operationId"] == "exchangeDeviceSession"
    assert exchange["post"]["security"] == [{"DeviceCredential": []}]
    assert schema["components"]["securitySchemes"]["DeviceCredential"] == {
        "scheme": "bearer",
        "type": "http",
    }


def test_exchange_is_retryably_unavailable_without_database_service() -> None:
    response = TestClient(create_app(database=None)).post(
        "/api/v1/device-sessions",
        headers={"authorization": f"Bearer {credential()}"},
        json={"capability": "app.control-plane"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "device_sessions_unavailable",
        "message": "Device session exchange is unavailable",
        "requestId": response.headers["x-request-id"],
        "retryable": True,
    }
    assert UUID(response.headers["x-request-id"])


def test_missing_and_malformed_credentials_share_fixed_error() -> None:
    client = TestClient(create_app(database=None, device_session_service=service()))
    missing = client.post(
        "/api/v1/device-sessions",
        json={"capability": "app.control-plane"},
    )
    private_invalid = "private-invalid-device-credential"
    malformed = client.post(
        "/api/v1/device-sessions",
        headers={"authorization": f"Bearer {private_invalid}"},
        json={"capability": "executor.connect"},
    )

    for response in (missing, malformed):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "device_credential_invalid"
        assert response.json()["error"]["message"] == "Device credential is invalid"
        assert private_invalid not in response.text


def test_exchange_returns_one_short_lived_secret_with_no_store() -> None:
    current = credential()
    response = TestClient(create_app(database=None, device_session_service=service())).post(
        "/api/v1/device-sessions",
        headers={"authorization": f"Bearer {current}"},
        json={"capability": "executor.connect"},
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {
        "sessionToken",
        "capability",
        "issuedAt",
        "expiresAt",
    }
    assert response.json()["sessionToken"].startswith("atds1.")
    assert response.json()["capability"] == "executor.connect"
    assert response.json()["issuedAt"] == "2026-07-18T12:00:00Z"
    assert response.json()["expiresAt"] == "2026-07-18T12:05:00Z"
    assert current not in response.text


def test_capability_is_exact_and_unknown_request_fields_are_forbidden() -> None:
    client = TestClient(create_app(database=None, device_session_service=service()))
    headers = {"authorization": f"Bearer {credential()}"}
    invalid_capability = client.post(
        "/api/v1/device-sessions",
        headers=headers,
        json={"capability": "*"},
    )
    extra_field = client.post(
        "/api/v1/device-sessions",
        headers=headers,
        json={"capability": "app.control-plane", "ttl": 3600},
    )

    for response in (invalid_capability, extra_field):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation"
        assert "*" not in response.text
        assert "3600" not in response.text


def test_exchange_error_translation_is_fixed_and_preserves_unexpected_errors() -> None:
    translated = _translate_exchange_error(InvalidDeviceSessionCapability())

    assert translated.status_code == 422
    assert translated.code == "validation"
    assert translated.message == "Request validation failed"
    unexpected = RuntimeError("private persistence detail")
    with pytest.raises(RuntimeError) as captured:
        _translate_exchange_error(unexpected)
    assert captured.value is unexpected
