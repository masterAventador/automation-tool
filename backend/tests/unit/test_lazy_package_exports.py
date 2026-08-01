from __future__ import annotations

import subprocess
import sys


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
