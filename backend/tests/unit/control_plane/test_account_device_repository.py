from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.account_devices import AccountDevicesUnavailable
from automation_tool.control_plane.domain import InstallationId, UserId
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.account_device_repository import (
    SqlAlchemyAccountDeviceRepository,
    _record,
)

NOW = datetime(2026, 7, 23, 2, 15, tzinfo=UTC)
USER_ID = UserId.parse("123e4567-e89b-42d3-a456-426614174000")
INSTALLATION_ID = InstallationId.parse("223e4567-e89b-42d3-a456-426614174000")


def test_account_device_row_validation_fails_closed() -> None:
    with pytest.raises(AccountDevicesUnavailable):
        _record(cast(RowMapping, {}))


@pytest.mark.asyncio
async def test_account_device_repository_normalizes_database_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )

    @asynccontextmanager
    async def failing_session() -> AsyncIterator[AsyncSession]:
        raise SQLAlchemyError("private database detail")
        yield cast(AsyncSession, object())

    monkeypatch.setattr(database, "session", failing_session)
    repository = SqlAlchemyAccountDeviceRepository(database)

    with pytest.raises(AccountDevicesUnavailable):
        await repository.list_owned(user_id=USER_ID)
    with pytest.raises(AccountDevicesUnavailable):
        await repository.revoke_owned(
            user_id=USER_ID,
            installation_id=INSTALLATION_ID,
            expected_revision=1,
            revoked_at=NOW,
            request_id="revoke-device",
        )
    await database.close()
