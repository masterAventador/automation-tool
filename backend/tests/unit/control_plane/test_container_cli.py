from importlib.metadata import entry_points
from typing import Any

import uvicorn

from automation_tool.control_plane.bootstrap.container_cli import main
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def test_container_console_script_targets_the_fixed_production_entrypoint() -> None:
    matching = [
        entry_point
        for entry_point in entry_points(group="console_scripts")
        if entry_point.name == "automation-tool-control-plane-container"
    ]

    assert len(matching) == 1
    assert matching[0].value == "automation_tool.control_plane.bootstrap.container_cli:main"


def test_container_cli_has_one_worker_fixed_port_and_graceful_shutdown(
    monkeypatch: Any,
) -> None:
    invocation: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        invocation["app"] = app
        invocation.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    main()

    assert invocation == {
        "app": "automation_tool.control_plane:create_app",
        "factory": True,
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 1,
        "access_log": False,
        "server_header": False,
        "timeout_graceful_shutdown": 30,
        "ws": "websockets-sansio",
        "ws_max_size": MAX_EXECUTOR_MESSAGE_BYTES,
    }
