"""The wiring that hands the publishing runtime its contract refuses bad input.

Every failure here is the same one to the caller: the deployment is misconfigured
and the runtime must not come up half-built.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from automation_tool.control_plane.bootstrap import bilibili_publishing as bootstrap
from automation_tool.control_plane.bootstrap.bilibili_publishing import (
    BilibiliPublishingConfigurationError,
    bilibili_publishing_runtime,
)
from automation_tool.control_plane.infrastructure.database import Database

_CONTRACT_ENVIRONMENT = "AUTOMATION_TOOL_BILIBILI_CONTRACT_FILE"


class _UnusedDatabase(Database):
    """Accepted by the type check and never asked to open a session."""

    def __init__(self) -> None:
        pass

    def session(self) -> Any:
        raise AssertionError("configuration failed before any session was needed")


def test_the_runtime_refuses_anything_that_is_not_a_database() -> None:
    for candidate in (None, "database", object()):
        with pytest.raises(BilibiliPublishingConfigurationError):
            bilibili_publishing_runtime(candidate)  # type: ignore[arg-type]


def test_a_contract_that_is_in_none_of_the_known_places_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_CONTRACT_ENVIRONMENT, raising=False)
    monkeypatch.setattr(
        bootstrap, "_CONTRACT_RELATIVE", Path("contracts/publishing/absent-on-purpose.json")
    )

    with pytest.raises(BilibiliPublishingConfigurationError):
        bilibili_publishing_runtime(_UnusedDatabase())


def test_a_contract_file_that_does_not_parse_is_a_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Present but unreadable is not better than absent, and must not start the runtime."""
    broken = tmp_path / "bilibili-open-api.v1.json"
    broken.write_text("{ this is not the contract", encoding="utf-8")
    monkeypatch.setenv(_CONTRACT_ENVIRONMENT, str(broken))

    with pytest.raises(BilibiliPublishingConfigurationError):
        bilibili_publishing_runtime(_UnusedDatabase())
