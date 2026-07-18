from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from importlib.metadata import entry_points
from typing import Any

import pytest

from automation_tool.control_plane.application.installation_revocations import (
    InstallationRevocationRejected,
    RevokedInstallation,
)
from automation_tool.control_plane.bootstrap import revoke_installation_cli
from automation_tool.control_plane.domain import InstallationId

INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")


class FakeDatabase:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeService:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.calls: list[dict[str, object]] = []

    async def revoke(self, **values: object) -> RevokedInstallation:
        self.calls.append(values)
        if self.reject:
            raise InstallationRevocationRejected
        return RevokedInstallation(
            installation_id=INSTALLATION_ID,
            revision=2,
            revoked_at=datetime(2026, 7, 18, 13, 0, tzinfo=UTC),
        )


def test_console_script_targets_the_fail_closed_operator_cli() -> None:
    matching = [
        entry_point
        for entry_point in entry_points(group="console_scripts")
        if entry_point.name == "automation-tool-revoke-installation"
    ]

    assert len(matching) == 1
    assert (
        matching[0].value == "automation_tool.control_plane.bootstrap.revoke_installation_cli:main"
    )


def test_internal_runner_closes_database_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    service = FakeService()
    monkeypatch.setattr(revoke_installation_cli, "database_from_environment", lambda: database)
    monkeypatch.setattr(
        revoke_installation_cli,
        "installation_revocation_service",
        lambda value: service if value is database else None,
    )

    result = asyncio.run(revoke_installation_cli._revoke(str(INSTALLATION_ID), 1))

    assert result == {"revision": 2, "status": "revoked"}
    assert service.calls == [{"installation_id": str(INSTALLATION_ID), "expected_revision": 1}]
    assert database.closed is True


def test_internal_runner_closes_database_after_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    service = FakeService(reject=True)
    monkeypatch.setattr(revoke_installation_cli, "database_from_environment", lambda: database)
    monkeypatch.setattr(
        revoke_installation_cli,
        "installation_revocation_service",
        lambda _value: service,
    )

    with pytest.raises(InstallationRevocationRejected):
        asyncio.run(revoke_installation_cli._revoke(str(INSTALLATION_ID), 1))

    assert database.closed is True


def test_main_outputs_only_public_success(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    async def succeed(_installation_id: str, _expected_revision: int) -> dict[str, object]:
        return {"revision": 2, "status": "revoked"}

    monkeypatch.setattr(revoke_installation_cli, "_revoke", succeed)

    revoke_installation_cli.main(
        ["--installation-id", str(INSTALLATION_ID), "--expected-revision", "1"]
    )

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"revision": 2, "status": "revoked"}
    assert str(INSTALLATION_ID) not in captured.out
    assert captured.err == ""


def test_main_failure_is_fixed_and_does_not_reflect_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject(_installation_id: str, _expected_revision: int) -> dict[str, object]:
        raise RuntimeError("private database failure")

    monkeypatch.setattr(revoke_installation_cli, "_revoke", reject)

    with pytest.raises(SystemExit) as captured:
        revoke_installation_cli.main(
            ["--installation-id", str(INSTALLATION_ID), "--expected-revision", "1"]
        )

    assert str(captured.value) == "Installation revocation failed"
    assert str(INSTALLATION_ID) not in str(captured.value)
