from importlib.metadata import entry_points
from typing import Any

import uvicorn

from automation_tool.control_plane.bootstrap.cli import main
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def test_control_plane_console_script_targets_the_factory() -> None:
    matching = [
        entry_point
        for entry_point in entry_points(group="console_scripts")
        if entry_point.name == "automation-tool-control-plane"
    ]

    assert len(matching) == 1
    assert matching[0].value == "automation_tool.control_plane.bootstrap.cli:main"


def test_local_cli_binds_loopback_and_enables_factory_mode(monkeypatch: Any) -> None:
    invocation: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        invocation["app"] = app
        invocation.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    main()

    assert invocation == {
        "app": "automation_tool.control_plane:create_app",
        "factory": True,
        "host": "127.0.0.1",
        "port": 8765,
        "access_log": False,
        "ws": "websockets-sansio",
        "ws_max_size": MAX_EXECUTOR_MESSAGE_BYTES,
    }
