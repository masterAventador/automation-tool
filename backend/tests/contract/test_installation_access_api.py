from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.device_credentials import ParsedDeviceCredential
from automation_tool.control_plane.application.device_sessions import (
    AuthenticatedDeviceSession,
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionRejected,
    DeviceSessionService,
    IssuedDeviceSession,
    ParsedDeviceSession,
    PendingDeviceSession,
)

NOW = datetime(2026, 7, 18, 13, 0, tzinfo=UTC)
INSTALLATION_ID = UUID("123e4567-e89b-42d3-a456-426614174003")


@dataclass
class FixedClock:
    def now(self) -> datetime:
        return NOW


class AccessRepository:
    def __init__(self, expected: ParsedDeviceSession) -> None:
        self.expected = expected
        self.active = True

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
        raise AssertionError("not used")

    async def authenticate(
        self,
        *,
        presented_session: ParsedDeviceSession,
        required_capability: DeviceSessionCapability,
        authenticated_at: datetime,
    ) -> AuthenticatedDeviceSession:
        if (
            not self.active
            or presented_session != self.expected
            or required_capability is not DeviceSessionCapability.APP_CONTROL_PLANE
            or authenticated_at != NOW
        ):
            raise DeviceSessionRejected
        return AuthenticatedDeviceSession(
            session_id=presented_session.session_id,
            installation_id=INSTALLATION_ID,
            credential_id=uuid4(),
            credential_version=1,
            capability=required_capability,
            expires_at=NOW + timedelta(minutes=5),
        )


def access_app() -> tuple[TestClient, AccessRepository, str]:
    material = DeviceSessionFactory(
        secret_source=secrets.token_bytes,
        id_source=uuid4,
    ).create()
    repository = AccessRepository(
        ParsedDeviceSession(
            session_id=material.session_id,
            secret_digest=material.secret_digest,
        )
    )
    service = DeviceSessionService(
        repository=repository,
        clock=FixedClock(),
        session_factory=DeviceSessionFactory(
            secret_source=secrets.token_bytes,
            id_source=uuid4,
        ),
    )
    return (
        TestClient(create_app(database=None, device_session_service=service)),
        repository,
        material.session_token,
    )


def test_openapi_exposes_one_app_session_protected_access_probe() -> None:
    schema = create_app(database=None).openapi()
    operation = schema["paths"]["/api/v1/installations/current"]["get"]

    assert operation["operationId"] == "getCurrentInstallationAccess"
    assert operation["security"] == [{"AppSession": []}]
    assert schema["components"]["securitySchemes"]["AppSession"] == {
        "scheme": "bearer",
        "type": "http",
    }


def test_active_app_session_returns_only_public_installation_state() -> None:
    client, _, session_token = access_app()

    response = client.get(
        "/api/v1/installations/current",
        headers={"authorization": f"Bearer {session_token}"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "installationId": str(INSTALLATION_ID),
        "status": "active",
    }
    assert session_token not in response.text


def test_missing_malformed_and_revoked_access_share_fixed_non_cacheable_error() -> None:
    client, repository, session_token = access_app()
    repository.active = False
    responses = (
        client.get("/api/v1/installations/current"),
        client.get(
            "/api/v1/installations/current",
            headers={"authorization": "Bearer private-invalid-session"},
        ),
        client.get(
            "/api/v1/installations/current",
            headers={"authorization": f"Bearer {session_token}"},
        ),
    )

    for response in responses:
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "installation_access_denied"
        assert response.json()["error"]["message"] == "Installation access is unavailable"
        assert "private" not in response.text


def test_access_probe_is_retryably_unavailable_without_session_service() -> None:
    response = TestClient(create_app(database=None)).get(
        "/api/v1/installations/current",
        headers={"authorization": "Bearer private-session"},
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "installation_access_unavailable"
    assert response.json()["error"]["retryable"] is True
