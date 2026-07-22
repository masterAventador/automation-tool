from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from automation_tool.control_plane.application.account_devices import (
    AccountDeviceRecord,
    AccountDeviceRevocationRejected,
    AccountDeviceService,
    AccountDevicesUnavailable,
)
from automation_tool.control_plane.application.account_sessions import AuthenticatedAccountSession
from automation_tool.control_plane.domain import InstallationId, InstallationStatus, UserId

NOW = datetime(2026, 7, 23, 2, 15, tzinfo=UTC)
USER_ID = UserId.parse("123e4567-e89b-42d3-a456-426614174000")
INSTALLATION_ID = InstallationId.parse("223e4567-e89b-42d3-a456-426614174000")


class Clock:
    def now(self) -> datetime:
        return NOW


class Sessions:
    async def authenticate(self, *, access_token: object) -> AuthenticatedAccountSession:
        assert access_token == "atas1.private"
        return AuthenticatedAccountSession(
            token_id=UUID("323e4567-e89b-42d3-a456-426614174000"),
            family_id=UUID("423e4567-e89b-42d3-a456-426614174000"),
            user_id=USER_ID,
            credential_version=1,
            expires_at=NOW,
        )


class Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def list_owned(self, *, user_id: UserId) -> tuple[AccountDeviceRecord, ...]:
        self.calls.append(("list", user_id))
        return (
            AccountDeviceRecord(
                installation_id=INSTALLATION_ID,
                status=InstallationStatus.ACTIVE,
                revision=1,
                created_at=NOW,
                updated_at=NOW,
            ),
        )

    async def revoke_owned(
        self,
        *,
        user_id: UserId,
        installation_id: InstallationId,
        expected_revision: int,
        revoked_at: datetime,
        request_id: str,
    ) -> AccountDeviceRecord:
        self.calls.append(
            (
                "revoke",
                user_id,
                installation_id,
                expected_revision,
                revoked_at,
                request_id,
            )
        )
        return AccountDeviceRecord(
            installation_id=installation_id,
            status=InstallationStatus.REVOKED,
            revision=2,
            created_at=NOW,
            updated_at=NOW,
        )


@pytest.mark.asyncio
async def test_account_identity_comes_only_from_access_session_for_list_and_revoke() -> None:
    repository = Repository()
    service = AccountDeviceService(
        repository=repository, account_sessions=Sessions(), clock=Clock()
    )

    devices = await service.list_devices(access_token="atas1.private")
    revoked = await service.revoke_device(
        access_token="atas1.private",
        installation_id=str(INSTALLATION_ID),
        expected_revision=1,
        request_id="revoke-device",
    )

    assert devices[0].installation_id == INSTALLATION_ID
    assert revoked.status is InstallationStatus.REVOKED
    assert repository.calls == [
        ("list", USER_ID),
        ("revoke", USER_ID, INSTALLATION_ID, 1, NOW, "revoke-device"),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("installation_id", cast(InstallationId, object())),
        ("status", cast(InstallationStatus, object())),
        ("revision", 0),
        ("revision", True),
        ("created_at", datetime(2026, 7, 23, 2, 15)),
        ("created_at", cast(datetime, object())),
        ("updated_at", datetime(2026, 7, 23, 2, 15)),
        ("updated_at", cast(datetime, object())),
        ("updated_at", NOW - timedelta(seconds=1)),
    ),
)
def test_account_device_record_rejects_invalid_projections(field: str, value: object) -> None:
    values: dict[str, object] = {
        "installation_id": INSTALLATION_ID,
        "status": InstallationStatus.ACTIVE,
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values[field] = value

    with pytest.raises(AccountDevicesUnavailable):
        AccountDeviceRecord(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("installation_id", "not-an-installation"),
        ("expected_revision", 0),
        ("expected_revision", True),
        ("request_id", "contains space"),
        ("request_id", None),
    ),
)
async def test_revoke_rejects_noncanonical_inputs(field: str, value: object) -> None:
    service = AccountDeviceService(
        repository=Repository(), account_sessions=Sessions(), clock=Clock()
    )
    arguments: dict[str, object] = {
        "access_token": "atas1.private",
        "installation_id": str(INSTALLATION_ID),
        "expected_revision": 1,
        "request_id": "revoke-device",
    }
    arguments[field] = value

    with pytest.raises(AccountDeviceRevocationRejected):
        await service.revoke_device(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("current", (None, datetime(2026, 7, 23, 2, 15)))
async def test_revoke_requires_timezone_aware_clock(current: object) -> None:
    class InvalidClock:
        def now(self) -> datetime:
            return cast(datetime, current)

    service = AccountDeviceService(
        repository=Repository(), account_sessions=Sessions(), clock=InvalidClock()
    )

    with pytest.raises(AccountDevicesUnavailable):
        await service.revoke_device(
            access_token="atas1.private",
            installation_id=str(INSTALLATION_ID),
            expected_revision=1,
            request_id="revoke-device",
        )
