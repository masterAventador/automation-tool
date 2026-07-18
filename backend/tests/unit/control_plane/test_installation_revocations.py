from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from automation_tool.control_plane.application.installation_revocations import (
    InstallationRevocationRejected,
    InstallationRevocationService,
    RevokedInstallation,
)
from automation_tool.control_plane.bootstrap import installation_revocations
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import Database

NOW = datetime(2026, 7, 18, 13, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")


@dataclass
class FixedClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class FakeRepository:
    def __init__(self) -> None:
        self.reject = False
        self.calls: list[dict[str, object]] = []

    async def revoke(
        self,
        *,
        installation_id: InstallationId,
        expected_revision: int,
        revoked_at: datetime,
    ) -> RevokedInstallation:
        self.calls.append(
            {
                "installation_id": installation_id,
                "expected_revision": expected_revision,
                "revoked_at": revoked_at,
            }
        )
        if self.reject:
            raise InstallationRevocationRejected
        return RevokedInstallation(
            installation_id=installation_id,
            revision=expected_revision + 1,
            revoked_at=revoked_at,
        )


@pytest.mark.asyncio
async def test_revocation_parses_identity_revision_and_uses_aware_utc() -> None:
    repository = FakeRepository()
    service = InstallationRevocationService(repository=repository, clock=FixedClock())

    revoked = await service.revoke(
        installation_id=str(INSTALLATION_ID),
        expected_revision=1,
    )

    assert revoked == RevokedInstallation(
        installation_id=INSTALLATION_ID,
        revision=2,
        revoked_at=NOW,
    )
    assert repository.calls == [
        {
            "installation_id": INSTALLATION_ID,
            "expected_revision": 1,
            "revoked_at": NOW,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installation_id", "expected_revision"),
    [
        (None, 1),
        ("private-invalid-installation", 1),
        (UUID("123e4567-e89b-42d3-a456-426614174003"), True),
        (INSTALLATION_ID, 0),
        (INSTALLATION_ID, -1),
        (INSTALLATION_ID, "1"),
    ],
)
async def test_invalid_targets_and_revisions_share_one_safe_rejection(
    installation_id: object,
    expected_revision: object,
) -> None:
    repository = FakeRepository()
    service = InstallationRevocationService(repository=repository, clock=FixedClock())

    with pytest.raises(InstallationRevocationRejected) as captured:
        await service.revoke(
            installation_id=installation_id,
            expected_revision=expected_revision,
        )

    assert str(captured.value) == "Installation revocation is rejected"
    assert "private-invalid-installation" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert repository.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("clock_value", [datetime(2026, 7, 18, 13, 0), None, "now"])
async def test_invalid_clock_fails_without_calling_repository(clock_value: object) -> None:
    repository = FakeRepository()
    service = InstallationRevocationService(
        repository=repository,
        clock=FixedClock(cast(datetime, clock_value)),
    )

    with pytest.raises(RuntimeError, match="Installation revocation clock is invalid"):
        await service.revoke(installation_id=INSTALLATION_ID, expected_revision=1)

    assert repository.calls == []


@pytest.mark.asyncio
async def test_repository_rejection_is_preserved_without_target_reflection() -> None:
    repository = FakeRepository()
    repository.reject = True
    service = InstallationRevocationService(repository=repository, clock=FixedClock())

    with pytest.raises(InstallationRevocationRejected) as captured:
        await service.revoke(installation_id=INSTALLATION_ID, expected_revision=1)

    assert str(INSTALLATION_ID) not in str(captured.value)


@pytest.mark.asyncio
async def test_runtime_builder_uses_the_database_repository_and_aware_system_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeRepository()
    database = cast(Database, object())
    monkeypatch.setattr(
        installation_revocations,
        "SqlAlchemyInstallationRevocationRepository",
        lambda value: repository if value is database else None,
    )

    service = installation_revocations.installation_revocation_service(database)
    before = datetime.now(UTC)
    revoked = await service.revoke(installation_id=INSTALLATION_ID, expected_revision=1)
    after = datetime.now(UTC)

    assert before <= revoked.revoked_at <= after
    assert revoked.revoked_at.utcoffset() == UTC.utcoffset(revoked.revoked_at)
    assert repository.calls[0]["installation_id"] == INSTALLATION_ID
