import inspect
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from automation_tool.control_plane.bootstrap import cli, container_cli, local_provisioning
from automation_tool.control_plane.bootstrap.cli import local_app, main
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
        "app": "automation_tool.control_plane.bootstrap.cli:local_app",
        "factory": True,
        "host": "127.0.0.1",
        "port": 8765,
        "access_log": False,
        "ws": "websockets-sansio",
        "ws_max_size": MAX_EXECUTOR_MESSAGE_BYTES,
    }


def test_local_factory_hands_one_fresh_bootstrap_to_the_app_private_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issued: list[dict[str, Any]] = []

    def fake_create_app(**kwargs: Any) -> object:
        issued.append(kwargs)
        return object()

    monkeypatch.setattr(cli, "local_app_data_directory", lambda: tmp_path)
    monkeypatch.setattr(cli, "create_app", fake_create_app)

    local_app()

    assert (tmp_path / local_provisioning.HANDOFF_FILE_NAME).is_file()
    assert len(issued) == 1
    provisioned = issued[0]["local_registration_bootstrap"]
    assert provisioned.environment_id == local_provisioning.LOCAL_ENVIRONMENT_ID


def test_container_entry_point_never_issues_a_local_bootstrap() -> None:
    source = inspect.getsource(container_cli)

    assert "local_provisioning" not in source
    assert "provision_local_registration_bootstrap" not in source
    assert "0.0.0.0" in source
