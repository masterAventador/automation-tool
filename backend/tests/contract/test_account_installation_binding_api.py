import base64
import binascii
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api import account_installation_bindings as binding_api
from automation_tool.control_plane.application.account_installation_bindings import (
    AccountInstallationBindingService,
    AccountInstallationBindingUnavailable,
    BindingChallengeExpired,
    BindingChallengeUsed,
    BindingProofRejected,
    CrossAccountBindingRejected,
    InvalidBindingRequest,
    IssuedAccountBindingChallenge,
    RevokedInstallationBindingRejected,
)
from automation_tool.control_plane.application.account_sessions import (
    AccountSessionRejected,
    AccountSessionUnavailable,
)
from automation_tool.control_plane.application.device_credentials import (
    IssuedDeviceCredential,
)
from automation_tool.control_plane.application.registration import RegisteredInstallation

NOW = datetime(2026, 7, 23, 1, 0, tzinfo=UTC)
ACCESS_TOKEN = (
    "atas1.123e4567-e89b-42d3-a456-426614174001.YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE"
)
CHALLENGE_ID = UUID("323e4567-e89b-42d3-a456-426614174000")
INSTALLATION_ID = UUID("423e4567-e89b-42d3-a456-426614174000")


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class FakeBindingService(AccountInstallationBindingService):
    def __init__(self) -> None:
        self.last_call: tuple[str, tuple[object, ...]] | None = None
        self.error: Exception | None = None

    async def issue_challenge(
        self, *, access_token: object, device_public_key: object
    ) -> IssuedAccountBindingChallenge:
        self.last_call = ("issue", (access_token, device_public_key))
        if self.error is not None:
            raise self.error
        return IssuedAccountBindingChallenge(
            challenge_id=CHALLENGE_ID,
            signing_payload=b"canonical-binding-payload",
            expires_at=NOW + timedelta(minutes=5),
        )

    async def complete_binding(self, **kwargs: object) -> RegisteredInstallation:
        self.last_call = ("complete", tuple(kwargs.values()))
        if self.error is not None:
            raise self.error
        return RegisteredInstallation(
            installation_id=INSTALLATION_ID,
            status="active",
            revision=2,
            device_credential=IssuedDeviceCredential(
                credential_id=UUID("523e4567-e89b-42d3-a456-426614174000"),
                installation_id=INSTALLATION_ID,
                credential="atdc1.private",
                version=3,
                scope="device.session.exchange",
            ),
        )


def client(service: AccountInstallationBindingService | None) -> TestClient:
    return TestClient(create_app(database=None, account_installation_binding_service=service))


def test_openapi_exposes_only_two_account_authenticated_binding_operations() -> None:
    schema = create_app(database=None).openapi()
    challenge = schema["paths"]["/api/v1/account-installations/binding-challenges"]
    completion = schema["paths"]["/api/v1/account-installations/bindings"]

    assert set(challenge) == {"post"}
    assert set(completion) == {"post"}
    assert challenge["post"]["operationId"] == "issueAccountInstallationBindingChallenge"
    assert completion["post"]["operationId"] == "completeAccountInstallationBinding"
    assert challenge["post"]["security"] == [{"AccountAccessToken": []}]
    assert completion["post"]["security"] == [{"AccountAccessToken": []}]
    assert "/api/v1/account-installations/pairing-codes" not in schema["paths"]
    assert "/api/v1/account-installations/approval-polls" not in schema["paths"]


def test_challenge_and_completion_have_fixed_secret_safe_shapes() -> None:
    service = FakeBindingService()
    app = client(service)
    challenge = app.post(
        "/api/v1/account-installations/binding-challenges",
        headers={"authorization": f"Bearer {ACCESS_TOKEN}"},
        json={"devicePublicKey": base64url(b"d" * 32)},
    )
    assert challenge.status_code == 201
    assert challenge.headers["cache-control"] == "no-store"
    assert challenge.json() == {
        "challengeId": str(CHALLENGE_ID),
        "signingPayload": base64url(b"canonical-binding-payload"),
        "expiresAt": "2026-07-23T01:05:00Z",
    }
    assert service.last_call == ("issue", (ACCESS_TOKEN, b"d" * 32))

    completed = app.post(
        "/api/v1/account-installations/bindings",
        headers={"authorization": f"Bearer {ACCESS_TOKEN}", "x-request-id": "bind-request"},
        json={
            "challengeId": str(CHALLENGE_ID),
            "signingPayload": base64url(b"canonical-binding-payload"),
            "signature": base64url(b"s" * 64),
        },
    )
    assert completed.status_code == 201
    assert completed.headers["cache-control"] == "no-store"
    assert completed.json() == {
        "installationId": str(INSTALLATION_ID),
        "status": "active",
        "revision": 2,
        "deviceCredential": {
            "credential": "atdc1.private",
            "version": 3,
            "scope": "device.session.exchange",
        },
    }
    request_text = str(completed.request.content)
    assert "userId" not in request_text
    assert "accountId" not in request_text
    assert "installationId" not in request_text


def test_missing_or_unavailable_account_binding_fails_closed() -> None:
    missing = client(FakeBindingService()).post(
        "/api/v1/account-installations/binding-challenges",
        json={"devicePublicKey": base64url(b"d" * 32)},
    )
    unavailable = client(None).post(
        "/api/v1/account-installations/binding-challenges",
        headers={"authorization": f"Bearer {ACCESS_TOKEN}"},
        json={"devicePublicKey": base64url(b"d" * 32)},
    )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "account_session_invalid"
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "account_installation_binding_unavailable"


def test_binding_security_failures_are_fixed_and_do_not_reflect_secrets() -> None:
    cases = (
        (BindingProofRejected(), 403, "installation_binding_proof_invalid"),
        (BindingChallengeExpired(), 410, "installation_binding_challenge_expired"),
        (BindingChallengeUsed(), 409, "installation_binding_challenge_used"),
        (CrossAccountBindingRejected(), 409, "installation_owned_by_other_account"),
        (RevokedInstallationBindingRejected(), 409, "installation_revoked"),
    )
    for error, expected_status, expected_code in cases:
        service = FakeBindingService()
        service.error = error
        response = client(service).post(
            "/api/v1/account-installations/binding-challenges",
            headers={"authorization": f"Bearer {ACCESS_TOKEN}"},
            json={"devicePublicKey": base64url(b"d" * 32)},
        )
        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == expected_code
        assert ACCESS_TOKEN not in response.text


def test_noncanonical_or_extra_identity_fields_are_rejected() -> None:
    app = client(FakeBindingService())
    for body in (
        {"devicePublicKey": "private+invalid"},
        {"devicePublicKey": base64url(b"d" * 32), "userId": "private-user"},
    ):
        response = app.post(
            "/api/v1/account-installations/binding-challenges",
            headers={"authorization": f"Bearer {ACCESS_TOKEN}"},
            json=body,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation"
        assert "private-user" not in response.text


def test_binding_api_private_validation_and_translation_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(InvalidBindingRequest):
        binding_api._decode("invalid+")
    with pytest.raises(InvalidBindingRequest):
        binding_api._decode(base64url(b"short"), exact_length=32)

    canonical_key = base64url(b"d" * 32)
    monkeypatch.setattr(
        base64,
        "urlsafe_b64decode",
        lambda _value: (_ for _ in ()).throw(binascii.Error()),
    )
    with pytest.raises(InvalidBindingRequest):
        binding_api._decode(canonical_key)

    with pytest.raises(AccountInstallationBindingUnavailable):
        binding_api._request_id(cast(Request, SimpleNamespace(state=SimpleNamespace())))

    cases = (
        (AccountSessionRejected(), 401, "account_session_invalid"),
        (AccountSessionUnavailable(), 503, "account_installation_binding_unavailable"),
        (InvalidBindingRequest(), 422, "validation"),
        (
            AccountInstallationBindingUnavailable(),
            503,
            "account_installation_binding_unavailable",
        ),
    )
    for error, expected_status, expected_code in cases:
        translated = binding_api._translate(error)
        assert translated.status_code == expected_status
        assert translated.code == expected_code
    with pytest.raises(RuntimeError, match="unknown"):
        binding_api._translate(RuntimeError("unknown"))


def test_binding_completion_translates_service_failures() -> None:
    service = FakeBindingService()
    service.error = AccountSessionRejected()
    response = client(service).post(
        "/api/v1/account-installations/bindings",
        headers={"authorization": f"Bearer {ACCESS_TOKEN}", "x-request-id": "bind-request"},
        json={
            "challengeId": str(CHALLENGE_ID),
            "signingPayload": base64url(b"canonical-binding-payload"),
            "signature": base64url(b"s" * 64),
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_session_invalid"


def test_binding_completion_requires_uuid_v4() -> None:
    response = client(FakeBindingService()).post(
        "/api/v1/account-installations/bindings",
        headers={"authorization": f"Bearer {ACCESS_TOKEN}", "x-request-id": "bind-request"},
        json={
            "challengeId": "123e4567-e89b-12d3-a456-426614174000",
            "signingPayload": base64url(b"canonical-binding-payload"),
            "signature": base64url(b"s" * 64),
        },
    )
    assert response.status_code == 422
