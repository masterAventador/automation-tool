import base64
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.registration import (
    BootstrapTokenVerifier,
    InstallationRegistrationRepository,
    InstallationRegistrationService,
)
from automation_tool.control_plane.domain import DemoEnvironmentId


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 18, tzinfo=UTC)


def inert_registration_service() -> InstallationRegistrationService:
    return InstallationRegistrationService(
        repository=cast(InstallationRegistrationRepository, object()),
        bootstrap_verifier=cast(BootstrapTokenVerifier, object()),
        expected_environment_id=DemoEnvironmentId.parse("demo-cn-1"),
        clock=FixedClock(),
        nonce_source=lambda length: b"x" * length,
    )


def test_openapi_exposes_only_the_two_bearer_protected_registration_operations() -> None:
    schema = create_app(database=None).openapi()
    challenge = schema["paths"]["/api/v1/installations/registration-challenges"]
    completion = schema["paths"]["/api/v1/installations"]

    assert set(challenge) == {"post"}
    assert set(completion) == {"post"}
    assert challenge["post"]["operationId"] == "issueInstallationRegistrationChallenge"
    assert completion["post"]["operationId"] == "completeInstallationRegistration"
    assert challenge["post"]["security"] == [{"DemoBootstrap": []}]
    assert completion["post"]["security"] == [{"DemoBootstrap": []}]
    assert schema["components"]["securitySchemes"]["DemoBootstrap"] == {
        "scheme": "bearer",
        "type": "http",
    }
    assert "/api/v1/login" not in schema["paths"]
    assert "/api/v1/users" not in schema["paths"]


def test_registration_is_retryably_unavailable_when_deployment_has_no_verifier() -> None:
    response = TestClient(create_app(database=None)).post(
        "/api/v1/installations/registration-challenges",
        headers={"authorization": "Bearer syntactically-present"},
        json={
            "environmentId": "demo-cn-1",
            "devicePublicKey": base64url(b"x" * 32),
        },
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "registration_unavailable",
        "message": "Installation registration is unavailable",
        "requestId": response.headers["x-request-id"],
        "retryable": True,
    }
    assert UUID(response.headers["x-request-id"])


def test_missing_bearer_and_noncanonical_key_use_fixed_non_reflecting_errors() -> None:
    client = TestClient(
        create_app(database=None, registration_service=inert_registration_service())
    )
    missing = client.post(
        "/api/v1/installations/registration-challenges",
        json={
            "environmentId": "demo-cn-1",
            "devicePublicKey": base64url(b"x" * 32),
        },
    )
    invalid_value = "private+not-base64url___________________"
    invalid = client.post(
        "/api/v1/installations/registration-challenges",
        headers={"authorization": "Bearer syntactically-present"},
        json={
            "environmentId": "demo-cn-1",
            "devicePublicKey": invalid_value,
        },
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "bootstrap_invalid"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation"
    assert invalid_value not in invalid.text
