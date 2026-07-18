import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.device_credentials import _translate_credential_error
from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialFactory,
    DeviceCredentialService,
    IssuedDeviceCredential,
    ParsedDeviceCredential,
    PendingDeviceCredential,
    RevokedDeviceCredential,
)

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


@dataclass
class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeRepository:
    async def rotate(
        self,
        *,
        presented: ParsedDeviceCredential,
        replacement: PendingDeviceCredential,
        rotated_at: datetime,
    ) -> IssuedDeviceCredential:
        assert rotated_at == NOW
        return IssuedDeviceCredential(
            credential_id=replacement.credential_id,
            installation_id=uuid4(),
            credential=replacement.credential,
            version=2,
            scope=DEVICE_CREDENTIAL_SCOPE,
        )

    async def revoke(
        self,
        *,
        presented: ParsedDeviceCredential,
        revoked_at: datetime,
    ) -> RevokedDeviceCredential:
        assert revoked_at == NOW
        return RevokedDeviceCredential(
            credential_id=presented.credential_id,
            installation_id=uuid4(),
            version=2,
            status="revoked",
        )


def credential_factory() -> DeviceCredentialFactory:
    return DeviceCredentialFactory(secret_source=secrets.token_bytes, id_source=uuid4)


def service() -> DeviceCredentialService:
    return DeviceCredentialService(
        repository=FakeRepository(),
        clock=FixedClock(),
        credential_factory=credential_factory(),
    )


def test_openapi_exposes_only_bearer_protected_rotation_and_revocation() -> None:
    schema = create_app(database=None).openapi()
    rotation = schema["paths"]["/api/v1/device-credentials/rotations"]
    revocation = schema["paths"]["/api/v1/device-credentials/revocations"]

    assert set(rotation) == {"post"}
    assert set(revocation) == {"post"}
    assert rotation["post"]["operationId"] == "rotateDeviceCredential"
    assert revocation["post"]["operationId"] == "revokeDeviceCredential"
    assert rotation["post"]["security"] == [{"DeviceCredential": []}]
    assert revocation["post"]["security"] == [{"DeviceCredential": []}]
    assert schema["components"]["securitySchemes"]["DeviceCredential"] == {
        "scheme": "bearer",
        "type": "http",
    }


def test_lifecycle_is_retryably_unavailable_without_database_service() -> None:
    credential = credential_factory().create().credential
    response = TestClient(create_app(database=None)).post(
        "/api/v1/device-credentials/rotations",
        headers={"authorization": f"Bearer {credential}"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "device_credentials_unavailable",
        "message": "Device credential lifecycle is unavailable",
        "requestId": response.headers["x-request-id"],
        "retryable": True,
    }
    assert UUID(response.headers["x-request-id"])


def test_missing_and_malformed_bearers_share_a_fixed_non_reflecting_error() -> None:
    client = TestClient(create_app(database=None, device_credential_service=service()))
    missing = client.post("/api/v1/device-credentials/rotations")
    private_invalid = "private-invalid-device-credential"
    malformed = client.post(
        "/api/v1/device-credentials/revocations",
        headers={"authorization": f"Bearer {private_invalid}"},
    )

    for response in (missing, malformed):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "device_credential_invalid"
        assert response.json()["error"]["message"] == "Device credential is invalid"
        assert private_invalid not in response.text


def test_rotation_returns_new_secret_once_and_revocation_returns_only_state() -> None:
    current = credential_factory().create().credential
    client = TestClient(create_app(database=None, device_credential_service=service()))

    rotated = client.post(
        "/api/v1/device-credentials/rotations",
        headers={"authorization": f"Bearer {current}"},
    )
    revoked = client.post(
        "/api/v1/device-credentials/revocations",
        headers={"authorization": f"Bearer {current}"},
    )

    assert rotated.status_code == 201
    assert rotated.headers["cache-control"] == "no-store"
    assert set(rotated.json()) == {"credential", "scope", "version"}
    assert rotated.json()["credential"].startswith("atdc1.")
    assert rotated.json()["credential"] != current
    assert rotated.json()["scope"] == DEVICE_CREDENTIAL_SCOPE
    assert rotated.json()["version"] == 2
    assert revoked.status_code == 200
    assert revoked.headers["cache-control"] == "no-store"
    assert revoked.json() == {"status": "revoked", "version": 2}
    assert current not in rotated.text
    assert current not in revoked.text


def test_unexpected_lifecycle_errors_are_not_misreported_as_authentication_failures() -> None:
    unexpected = RuntimeError("private persistence detail")

    try:
        _translate_credential_error(unexpected)
    except RuntimeError as captured:
        assert captured is unexpected
    else:
        raise AssertionError("unexpected errors must remain unexpected")
