from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import automation_tool.executor as executor_package
import automation_tool.protocol as protocol_package

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _loaded_modules_after(statement: str) -> set[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                f"{statement}; "
                "print(json.dumps(sorted(sys.modules)))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    import json

    return set(json.loads(result.stdout))


def test_local_editing_leaf_import_does_not_load_unrelated_executor_protocol() -> None:
    loaded = _loaded_modules_after(
        "import automation_tool.executor.local_editing_worker_process"
    )

    assert "automation_tool.protocol.executor_envelope" not in loaded


def test_editing_domain_leaf_import_does_not_load_unrelated_platform_domains() -> None:
    loaded = _loaded_modules_after(
        "import automation_tool.control_plane.domain.editing_project"
    )

    assert "automation_tool.control_plane.domain.bilibili_open_api" not in loaded


def test_executor_bundle_declares_every_lazy_runtime_module_as_a_hidden_import() -> None:
    specification = ast.parse(
        (_BACKEND_ROOT / "automation-tool-executor.spec").read_text(encoding="utf-8")
    )
    declared_strings = {
        node.value
        for node in ast.walk(specification)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    for package_name, modules in (
        (protocol_package.__name__, protocol_package._PUBLIC_MODULES),
        (executor_package.__name__, executor_package._PUBLIC_MODULES),
    ):
        for module_name in modules:
            assert f"{package_name}.{module_name}" in declared_strings
