"""Private Streamlit process lifecycle for the material-video WebUI."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import types
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from job_observation_bridge import install_job_observation_bridge
from model_service_adapter import (
    ScriptModelConfiguration,
    generate_script,
    install_script_model,
    parse_script_model,
)

HOST: Final = "127.0.0.1"
START_TIMEOUT_SECONDS: Final = 25
STOP_TIMEOUT_SECONDS: Final = 5

# Upstream hard-codes a proprietary Windows face as its default subtitle font
# (`webui/Main.py` -> DEFAULT_SUBTITLE_SETTINGS) and, when that file is absent,
# falls back to the alphabetically first face in the directory — which is one of
# the Latin-only faces and would draw every Chinese subtitle as empty boxes.
# `webui/Main.py` is a read-only submodule, so the default is pinned the way
# upstream itself supports: through `[ui] font_name` in the private runtime
# configuration, whose value comes from the release contract rather than from a
# second copy of the font name.
UI_SECTION_HEADER: Final = "[ui]"
CONFIG_FILE_NAME: Final = "config.toml"
EXAMPLE_CONFIG_FILE_NAME: Final = "config.example.toml"


class WebUiRejected(RuntimeError):
    """Fixed boundary for WebUI startup and configuration failures."""


@dataclass(frozen=True)
class WebUiEndpoint:
    port: int
    path: str


@dataclass
class WebUiRuntime:
    endpoint: WebUiEndpoint
    process: subprocess.Popen[bytes]
    runtime_root: Path

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=STOP_TIMEOUT_SECONDS)


def _upstream_paths() -> tuple[Path, Path, Path, Path]:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str):
        root = Path(frozen_root)
        return (
            root / "upstream/app",
            root / "upstream/webui/Main.py",
            root / "upstream/config.example.toml",
            root / "upstream/resource",
        )
    repository = Path(__file__).resolve().parents[2]
    upstream = repository / "vendor/moneyprinterturbo"
    return (
        upstream / "app",
        upstream / "webui/Main.py",
        upstream / "config.example.toml",
        upstream / "resource",
    )


def _worker_contract_path() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str):
        return Path(frozen_root) / "contracts/material-video-worker-package.v1.json"
    repository = Path(__file__).resolve().parents[2]
    return repository / "contracts/quality/material-video-worker-package.v1.json"


def default_subtitle_font_name() -> str:
    """Read the cleared subtitle face the WebUI must preselect."""
    try:
        contract = json.loads(_worker_contract_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WebUiRejected(f"package contract unavailable: {error}") from None
    build = contract.get("build") if isinstance(contract, dict) else None
    name = build.get("defaultSubtitleFontName") if isinstance(build, dict) else None
    if not isinstance(name, str) or not name:
        raise WebUiRejected("package contract declares no default subtitle font")
    return name


def _private_config_document(example: str, font_name: str) -> str:
    """Return the upstream example configuration with the subtitle font pinned.

    The value is inserted immediately after the `[ui]` table header rather than
    by rewriting the document, so every other upstream default, comment and
    ordering survives verbatim and a future upstream option cannot be dropped by
    a round trip through a TOML serializer.
    """
    if (
        not font_name
        or font_name != Path(font_name).name
        or any(character in font_name for character in '"\\\n\r\t')
    ):
        raise WebUiRejected("invalid subtitle font name")
    lines = example.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() != UI_SECTION_HEADER:
            continue
        pinned = f'font_name = "{font_name}"\n'
        return "".join(lines[: index + 1]) + pinned + "".join(lines[index + 1 :])
    raise WebUiRejected("upstream configuration has no WebUI section")


def _reserve_port() -> int:
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        return int(listener.getsockname()[1])


def _child_command(
    port: int, path: str, runtime_root: Path, output_root: Path
) -> list[str]:
    values = ["--serve-webui", str(port), path, str(runtime_root), str(output_root)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *values]
    return [sys.executable, str(Path(__file__).with_name("worker_main.py")), *values]


def _script_model_document(configuration: ScriptModelConfiguration | None) -> object:
    if configuration is None:
        return None
    return {
        "apiKey": configuration.api_key,
        "baseUrl": configuration.base_url,
        "modelId": configuration.model_id,
        "sourceProvider": configuration.source_provider,
        "upstreamProvider": configuration.upstream_provider,
    }


def _native_path_for_upstream(path: Path) -> Path:
    if os.name != "nt":
        return path
    value = str(path)
    if value.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + value[8:])
    if value.startswith("\\\\?\\"):
        return Path(value[4:])
    return path


def start_webui(
    asset_root: Path, configuration: ScriptModelConfiguration | None
) -> WebUiRuntime:
    runtime_parent = asset_root / ".automation-tool-webui"
    runtime_parent.mkdir(mode=0o700, exist_ok=True)
    if runtime_parent.is_symlink() or not runtime_parent.is_dir():
        raise WebUiRejected("invalid runtime root")
    capability = secrets.token_urlsafe(32)
    path = f"studio-{capability}"
    runtime_root = runtime_parent / capability
    runtime_root.mkdir(mode=0o700)
    output_root = asset_root.parent / "outputs"
    if output_root.is_symlink() or not output_root.is_dir():
        raise WebUiRejected("invalid output root")
    port = _reserve_port()
    process = subprocess.Popen(
        _child_command(port, path, runtime_root, output_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=runtime_root,
        env={
            **os.environ,
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
            "STREAMLIT_SERVER_HEADLESS": "true",
        },
    )
    try:
        assert process.stdin is not None
        payload = json.dumps(
            _script_model_document(configuration), separators=(",", ":")
        ).encode()
        process.stdin.write(payload + b"\n")
        process.stdin.close()
        health_url = f"http://{HOST}:{port}/{path}/_stcore/health"
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise WebUiRejected("WebUI process exited")
            try:
                with urllib.request.urlopen(health_url, timeout=0.5) as response:
                    if response.status == 200:
                        return WebUiRuntime(
                            WebUiEndpoint(port, path), process, runtime_root
                        )
            except OSError:
                time.sleep(0.1)
        raise WebUiRejected("WebUI startup timed out")
    except Exception:
        runtime = WebUiRuntime(WebUiEndpoint(port, path), process, runtime_root)
        runtime.stop()
        raise


def _preload_private_config(runtime_root: Path) -> None:
    app_path, _, example, _ = _upstream_paths()
    if not app_path.is_dir() or not example.is_file():
        raise WebUiRejected("upstream package unavailable")
    runtime_root.mkdir(mode=0o700, exist_ok=True)
    example_document = example.read_text(encoding="utf-8")
    (runtime_root / EXAMPLE_CONFIG_FILE_NAME).write_text(
        example_document, encoding="utf-8"
    )
    # Upstream copies the example over only when config.toml is missing, so
    # writing it here is what makes the pinned subtitle font the effective
    # default instead of the proprietary face this release no longer ships.
    (runtime_root / CONFIG_FILE_NAME).write_text(
        _private_config_document(example_document, default_subtitle_font_name()),
        encoding="utf-8",
    )
    app_package = types.ModuleType("app")
    app_package.__path__ = [str(app_path)]
    app_package.__package__ = "app"
    config_package = types.ModuleType("app.config")
    config_package.__path__ = [str(app_path / "config")]
    config_package.__package__ = "app.config"
    config_module = types.ModuleType("app.config.config")
    config_module.__file__ = str(runtime_root / "app/config/config.py")
    config_module.__package__ = "app.config"
    sys.modules["app"] = app_package
    sys.modules["app.config"] = config_package
    sys.modules["app.config.config"] = config_module
    source = (app_path / "config/config.py").read_text(encoding="utf-8")
    exec(compile(source, config_module.__file__, "exec"), config_module.__dict__)
    config_package.config = config_module


def _prepare_private_project(runtime_root: Path) -> Path:
    _, upstream_main, _, upstream_resource = _upstream_paths()
    upstream_webui = upstream_main.parent
    if not upstream_webui.is_dir() or not upstream_resource.is_dir():
        raise WebUiRejected("upstream WebUI assets unavailable")
    private_webui = runtime_root / "webui"
    private_resource = runtime_root / "resource"
    shutil.copytree(upstream_webui, private_webui)
    shutil.copytree(upstream_resource, private_resource)
    (runtime_root / "storage/tasks").mkdir(parents=True)
    return private_webui / "Main.py"


def serve_webui(
    port: int, path: str, runtime_root: Path, output_root: Path, stream: TextIO
) -> int:
    if not 1 <= port <= 65535 or not path.startswith("studio-") or len(path) != 50:
        raise WebUiRejected("invalid WebUI endpoint")
    runtime_root = _native_path_for_upstream(runtime_root.resolve(strict=True))
    output_root = _native_path_for_upstream(output_root.resolve(strict=True))
    if output_root != runtime_root.parents[2] / "outputs":
        raise WebUiRejected("invalid output root")
    line = stream.readline(16 * 1024 + 1)
    if not line or len(line.encode()) > 16 * 1024:
        raise WebUiRejected("invalid model bootstrap")
    try:
        configuration = parse_script_model(json.loads(line))
    except (json.JSONDecodeError, UnicodeError, ValueError, TypeError):
        raise WebUiRejected("invalid model bootstrap") from None
    _preload_private_config(runtime_root)
    if configuration is not None:
        install_script_model(configuration)
    from app.services import llm
    from app.services import state as state_module
    from app.utils import utils

    llm._generate_response = generate_script
    utils.root_dir = lambda: str(runtime_root)
    main_path = _prepare_private_project(runtime_root)
    install_job_observation_bridge(state_module, runtime_root, output_root)
    if not main_path.is_file():
        raise WebUiRejected("upstream WebUI unavailable")
    sys.argv = [
        "streamlit",
        "run",
        str(main_path),
        "--global.developmentMode=false",
        f"--server.address={HOST}",
        f"--server.port={port}",
        f"--browser.serverAddress={HOST}",
        f"--server.baseUrlPath={path}",
        "--browser.gatherUsageStats=false",
        "--server.headless=true",
        "--server.enableCORS=true",
        "--server.enableXsrfProtection=true",
        "--client.toolbarMode=minimal",
        "--logger.hideWelcomeMessage=true",
        "--server.showEmailPrompt=false",
    ]
    from streamlit.web import cli

    return int(cli.main() or 0)
