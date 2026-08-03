"""COV-03: where the authoring chain looks for its shipped contracts.

The two answers differ by deployment, not by configuration: a frozen executable
unpacks its resources into a temporary directory PyInstaller names in
`sys._MEIPASS`, and a source checkout has them five levels above this module.
Only the second one is reachable while running the test suite, so the first is
driven by supplying the attribute the frozen build would have set -- the same
platform-value injection the Windows path helpers use, and for the same reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from automation_tool.executor.motion_authoring import resources


def test_a_source_checkout_finds_the_contracts_beside_the_repository() -> None:
    root = resources._resource_root()

    assert root.is_absolute()
    assert (root / "contracts").is_dir()
    assert resources.CONTRACTS_ROOT == resources.RESOURCE_ROOT / "contracts"


def test_a_frozen_build_reads_the_directory_pyinstaller_unpacked_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resources._resource_root() == tmp_path


def test_a_meipass_that_is_not_a_path_falls_back_to_the_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing else states what that attribute is, so its type is checked here."""
    monkeypatch.setattr(sys, "_MEIPASS", 42, raising=False)

    assert (resources._resource_root() / "contracts").is_dir()
