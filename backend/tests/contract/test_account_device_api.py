from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api import account_devices as account_device_api
from automation_tool.control_plane.application.account_devices import (
    AccountDeviceRecord,
    AccountDeviceRevocationRejected,
    AccountDeviceService,
    AccountDevicesUnavailable,
)
from automation_tool.control_plane.application.account_sessions import AccountSessionRejected
from automation_tool.control_plane.domain import InstallationId, InstallationStatus

NOW = datetime(2026, 7, 23, 2, 15, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("223e4567-e89b-42d3-a456-426614174000")
ACCESS_TOKEN = "atas1.123e4567-e89b-42d3-a456-426614174001.private"


class FakeAccountDeviceService(AccountDeviceService):
    def __init__(self) -> None:
        self.last_call: tuple[object, ...] | None = None
        self.error: Exception | None = None

    async def list_devices(self, *, access_token: object) -> tuple[AccountDeviceRecord, ...]:
        self.last_call = ("list", access_token)
        if self.error is not None:
            raise self.error
        return (self._record(InstallationStatus.ACTIVE, 1),)

    async def revoke_device(self, **kwargs: object) -> AccountDeviceRecord:
        self.last_call = ("revoke", *kwargs.values())
        if self.error is not None:
            raise self.error
        return self._record(InstallationStatus.REVOKED, 2)

    @staticmethod
    def _record(status: InstallationStatus, revision: int) -> AccountDeviceRecord:
        return AccountDeviceRecord(
            installation_id=INSTALLATION_ID,
            status=status,
            revision=revision,
            created_at=NOW,
            updated_at=NOW,
        )


def test_openapi_exposes_only_current_account_device_list_and_revoke() -> None:
    schema = create_app(database=None).openapi()
    collection = schema["paths"]["/api/v1/account-installations"]
    member = schema["paths"]["/api/v1/account-installations/{installation_id}"]

    assert set(collection) == {"get"}
    assert set(member) == {"delete"}
    assert collection["get"]["security"] == [{"AccountAccessToken": []}]
    assert member["delete"]["security"] == [{"AccountAccessToken": []}]


def test_list_and_revoke_return_secret_free_owned_device_projection() -> None:
    service = FakeAccountDeviceService()
    app = TestClient(create_app(database=None, account_device_service=service))
    headers = {"authorization": f"Bearer {ACCESS_TOKEN}", "x-request-id": "device-request"}

    listed = app.get("/api/v1/account-installations", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == {
        "devices": [
            {
                "installationId": str(INSTALLATION_ID),
                "status": "active",
                "revision": 1,
                "createdAt": "2026-07-23T02:15:00Z",
                "updatedAt": "2026-07-23T02:15:00Z",
            }
        ]
    }
    assert "credential" not in listed.text
    assert "devicePublicKey" not in listed.text

    revoked = app.delete(
        f"/api/v1/account-installations/{INSTALLATION_ID}?expectedRevision=1",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert service.last_call == (
        "revoke",
        ACCESS_TOKEN,
        str(INSTALLATION_ID),
        1,
        "device-request",
    )


def test_account_device_routes_fail_closed_without_access_session() -> None:
    app = TestClient(create_app(database=None, account_device_service=FakeAccountDeviceService()))
    response = app.get("/api/v1/account-installations")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_session_invalid"


def test_account_device_routes_fail_closed_for_wrong_auth_scheme_and_service() -> None:
    with pytest.raises(Exception) as wrong_scheme:
        account_device_api._access_token(
            HTTPAuthorizationCredentials(scheme="Basic", credentials="x")
        )
    assert getattr(wrong_scheme.value, "code", None) == "account_session_invalid"

    response = TestClient(create_app(database=None)).get(
        "/api/v1/account-installations",
        headers={"authorization": f"Bearer {ACCESS_TOKEN}"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "account_devices_unavailable"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    (
        (AccountSessionRejected(), 401, "account_session_invalid"),
        (AccountDeviceRevocationRejected(), 409, "account_device_revocation_rejected"),
        (AccountDevicesUnavailable(), 503, "account_devices_unavailable"),
    ),
)
def test_account_device_failures_have_fixed_secret_free_responses(
    error: Exception, status: int, code: str
) -> None:
    service = FakeAccountDeviceService()
    service.error = error
    app = TestClient(create_app(database=None, account_device_service=service))
    headers = {"authorization": f"Bearer {ACCESS_TOKEN}", "x-request-id": "device-request"}
    path = (
        f"/api/v1/account-installations/{INSTALLATION_ID}?expectedRevision=1"
        if isinstance(error, AccountDeviceRevocationRejected)
        else "/api/v1/account-installations"
    )
    response = (
        app.delete(path, headers=headers)
        if path != "/api/v1/account-installations"
        else app.get(path, headers=headers)
    )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert ACCESS_TOKEN not in response.text


def test_account_device_private_boundaries_preserve_unknown_failures() -> None:
    with pytest.raises(RuntimeError, match="unknown"):
        account_device_api._translate(RuntimeError("unknown"))
    with pytest.raises(AccountDevicesUnavailable):
        account_device_api._request_id(cast(Request, SimpleNamespace(state=SimpleNamespace())))
