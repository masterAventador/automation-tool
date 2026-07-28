"""Guard: the Aliyun cloud-editing route is gone and must not come back.

LE-01 removed it. The evidence that made us remove it: VE-01..VE-08 were all
marked done while `control_plane/api/` had no editing route at all, so nothing
users could reach ever called this code. Re-adding a vendor module here means
someone is rebuilding that same unreachable layer.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_REMOVED_DOMAIN_MODULES = (
    "aliyun_ims_editing_provider",
    "aliyun_ims_editing_staging",
    "aliyun_ims_editing_output",
    "aliyun_ims_editing_reconciliation",
    "aliyun_ims_editing_callback",
    "video_editing_provider",
    "video_editing_provider_conformance",
    "fake_second_editing_provider",
)

_REMOVED_DATABASE_MODULES = (
    "aliyun_editing_intent_repository",
    "editing_output_ledger_repository",
)

_DOMAIN_PACKAGE = "automation_tool.control_plane.domain"
_DATABASE_PACKAGE = "automation_tool.control_plane.infrastructure.database"


def test_parent_packages_still_import() -> None:
    """The module guards below assert ModuleNotFoundError. A broken parent
    package raises exactly that too, so without this check those guards could
    go green because the package itself stopped importing — the opposite of
    what they are meant to prove.
    """
    importlib.import_module(_DOMAIN_PACKAGE)
    importlib.import_module(_DATABASE_PACKAGE)


@pytest.mark.parametrize("module_name", _REMOVED_DOMAIN_MODULES)
def test_cloud_editing_domain_module_is_gone(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"{_DOMAIN_PACKAGE}.{module_name}")


@pytest.mark.parametrize("module_name", _REMOVED_DATABASE_MODULES)
def test_cloud_editing_database_module_is_gone(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"{_DATABASE_PACKAGE}.{module_name}")


@pytest.mark.xfail(reason="Task 3 清理 schema.py 之前，两张云剪辑表仍在声明中", strict=True)
def test_schema_declares_no_cloud_editing_tables() -> None:
    from automation_tool.control_plane.infrastructure.database import schema

    table_names = set(schema.metadata.tables)
    assert "aliyun_editing_intents" not in table_names
    assert "editing_output_lineages" not in table_names


@pytest.mark.xfail(reason="Task 6 删除契约文件之前，该文件仍存在", strict=True)
def test_aliyun_editing_contract_file_is_gone() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    contract = repository_root / "contracts/video/aliyun-ims-editing-staging.v1.json"
    assert not contract.exists()
