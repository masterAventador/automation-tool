"""Current-account Installation inventory and revocation boundary."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from automation_tool.control_plane.application.account_sessions import (
    AuthenticatedAccountSession,
)
from automation_tool.control_plane.domain import (
    InstallationId,
    InstallationStatus,
    InvalidResourceId,
    UserId,
)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)


class AccountDeviceRevocationRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__("Account device revocation is rejected")


class AccountDevicesUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Account devices are unavailable")


@dataclass(frozen=True, slots=True)
class AccountDeviceRecord:
    installation_id: InstallationId
    status: InstallationStatus
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.installation_id, InstallationId)
            or not isinstance(self.status, InstallationStatus)
            or type(self.revision) is not int
            or self.revision <= 0
            or type(self.created_at) is not datetime
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() != timedelta(0)
            or type(self.updated_at) is not datetime
            or self.updated_at.tzinfo is None
            or self.updated_at.utcoffset() != timedelta(0)
            or self.updated_at < self.created_at
        ):
            raise AccountDevicesUnavailable


class AccountDeviceRepository(Protocol):
    async def list_owned(self, *, user_id: UserId) -> tuple[AccountDeviceRecord, ...]: ...

    async def revoke_owned(
        self,
        *,
        user_id: UserId,
        installation_id: InstallationId,
        expected_revision: int,
        revoked_at: datetime,
        request_id: str,
    ) -> AccountDeviceRecord: ...


class AccountAuthenticator(Protocol):
    async def authenticate(self, *, access_token: object) -> AuthenticatedAccountSession: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class AccountDeviceService:
    """Resolve account identity from native-held access authority only."""

    def __init__(
        self,
        *,
        repository: AccountDeviceRepository,
        account_sessions: AccountAuthenticator,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._account_sessions = account_sessions
        self._clock = clock

    async def list_devices(self, *, access_token: object) -> tuple[AccountDeviceRecord, ...]:
        account = await self._account_sessions.authenticate(access_token=access_token)
        return await self._repository.list_owned(user_id=account.user_id)

    async def revoke_device(
        self,
        *,
        access_token: object,
        installation_id: object,
        expected_revision: object,
        request_id: object,
    ) -> AccountDeviceRecord:
        account = await self._account_sessions.authenticate(access_token=access_token)
        try:
            target = InstallationId.parse(installation_id)
        except InvalidResourceId:
            raise AccountDeviceRevocationRejected from None
        if type(expected_revision) is not int or expected_revision <= 0:
            raise AccountDeviceRevocationRejected
        if type(request_id) is not str or _REQUEST_ID.fullmatch(request_id) is None:
            raise AccountDeviceRevocationRejected
        now = self._clock.now()
        if type(now) is not datetime or now.tzinfo is None:
            raise AccountDevicesUnavailable
        return await self._repository.revoke_owned(
            user_id=account.user_id,
            installation_id=target,
            expected_revision=expected_revision,
            revoked_at=now.astimezone(UTC),
            request_id=request_id,
        )


__all__ = [
    "AccountDeviceRecord",
    "AccountDeviceRepository",
    "AccountDeviceRevocationRejected",
    "AccountDeviceService",
    "AccountDevicesUnavailable",
]
