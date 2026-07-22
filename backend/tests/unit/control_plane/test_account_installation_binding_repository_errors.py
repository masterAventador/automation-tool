from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.account_installation_bindings import (
    BindingChallengeUsed,
)
from automation_tool.control_plane.application.device_credentials import DeviceCredentialFactory
from automation_tool.control_plane.domain import UserId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyAccountInstallationBindingRepository,
)


@pytest.mark.asyncio
async def test_binding_completion_normalizes_integrity_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )

    @asynccontextmanager
    async def failing_session() -> AsyncIterator[AsyncSession]:
        raise IntegrityError("private statement", {}, RuntimeError("private database detail"))
        yield cast(AsyncSession, object())

    monkeypatch.setattr(database, "session", failing_session)
    repository = SqlAlchemyAccountInstallationBindingRepository(database)
    pending = DeviceCredentialFactory(
        secret_source=lambda length: b"s" * length,
        id_source=lambda: UUID("323e4567-e89b-42d3-a456-426614174000"),
    ).create()

    with pytest.raises(BindingChallengeUsed):
        await repository.complete_challenge(
            challenge_id=UUID("123e4567-e89b-42d3-a456-426614174000"),
            user_id=UserId.parse("223e4567-e89b-42d3-a456-426614174000"),
            signing_payload=b"payload",
            signature=b"s" * 64,
            completed_at=datetime(2026, 7, 23, 2, 15, tzinfo=UTC),
            pending_credential=pending,
            request_id="bind-device",
        )
    await database.close()
