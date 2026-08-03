#!/usr/bin/env python3
"""Keep every real video-studio desktop build inside the complete startup harness."""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import tempfile
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import desktop_e2e_prerequisites as prerequisites  # noqa: E402

BUILD_SCRIPT = "build:tauri:video-studio-test"
SHARED_MODULE = "desktop_e2e_prerequisites"
SHARED_HARNESS = "video_studio_startup_harness"
VIDEO_CONTROL_PLANE_PORT = 8765


def _is_video_studio_build(call: ast.Call) -> bool:
    """Recognize the build token only when it is an actual command argument."""
    return any(
        isinstance(argument, (ast.List, ast.Tuple))
        and any(
            isinstance(item, ast.Constant) and item.value == BUILD_SCRIPT for item in argument.elts
        )
        for argument in call.args
    )


def _build_calls(tree: ast.AST) -> tuple[ast.Call, ...]:
    return tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_video_studio_build(node)
    )


def _is_video_studio_execution(call: ast.Call) -> bool:
    for argument in call.args:
        if not isinstance(argument, (ast.List, ast.Tuple)):
            continue
        for item in argument.elts:
            if isinstance(item, ast.Constant) and item.value == "wdio":
                return True
            if (
                isinstance(item, ast.Starred)
                and isinstance(item.value, ast.Call)
                and isinstance(item.value.func, ast.Name)
                and item.value.func.id == "desktop_wdio_arguments"
            ):
                return True
    return False


def video_studio_build_drivers() -> tuple[Path, ...]:
    """Derive the driver set from executable Python syntax, never a hand list."""
    drivers: list[Path] = []
    for path in SCRIPTS_ROOT.glob("run_*_acceptance.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _build_calls(tree):
            drivers.append(path)
    return tuple(sorted(drivers))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _imported_harness_name(tree: ast.AST, path: Path) -> str:
    imports = [
        alias
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == SHARED_MODULE
        for alias in node.names
        if alias.name == SHARED_HARNESS
    ]
    if len(imports) != 1:
        raise AssertionError(
            f"{path.name} must import {SHARED_HARNESS} exactly once from {SHARED_MODULE}"
        )
    imported_name = imports[0].asname or imports[0].name
    shadowed = any(
        (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == imported_name
        )
        or (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id == imported_name
        )
        or (isinstance(node, ast.arg) and node.arg == imported_name)
        for node in ast.walk(tree)
    )
    if shadowed:
        raise AssertionError(
            f"{path.name} shadows the imported {SHARED_HARNESS} with a same-name decoy"
        )
    return imported_name


def _enclosing_nodes(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> Iterator[ast.AST]:
    while node in parents:
        node = parents[node]
        yield node


def _complete_harness(
    node: ast.AST,
    *,
    imported_name: str,
) -> bool:
    if not isinstance(node, (ast.With, ast.AsyncWith)) or len(node.items) != 1:
        return False
    item = node.items[0]
    call = item.context_expr
    if (
        not isinstance(call, ast.Call)
        or not isinstance(call.func, ast.Name)
        or call.func.id != imported_name
        or len(call.args) != 1
        or not isinstance(call.args[0], ast.Name)
        or call.args[0].id != "private_app_data"
        or not isinstance(item.optional_vars, ast.Name)
        or item.optional_vars.id != "environment"
    ):
        return False
    environment_keywords = [
        keyword
        for keyword in call.keywords
        if keyword.arg == "environment"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "environment"
    ]
    return len(environment_keywords) == 1


def check_driver(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    builds = _build_calls(tree)
    if len(builds) != 1:
        raise AssertionError(
            f"{path.name} must contain exactly one video-studio App build, got {len(builds)}"
        )
    imported_name = _imported_harness_name(tree, path)
    parents = _parents(tree)
    enclosing = tuple(_enclosing_nodes(builds[0], parents))
    owner_index = next(
        (
            index
            for index, node in enumerate(enclosing)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
        None,
    )
    if owner_index is None:
        raise AssertionError(f"{path.name} builds the App outside an acceptance function")
    owner = enclosing[owner_index]
    if owner not in tree.body:
        raise AssertionError(f"{path.name} hides the App build in an uncalled nested function")
    main = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
        ),
        None,
    )
    calls_owner = (
        main is not None
        and isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == owner.name
            for node in ast.walk(main)
        )
    )
    if not calls_owner:
        raise AssertionError(f"{path.name} does not call its video-studio build owner from main")
    function_scope = enclosing[:owner_index]
    if any(
        isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef))
        for node in function_scope
    ):
        raise AssertionError(f"{path.name} hides the App build in a nested callable")
    harnesses = [
        node for node in function_scope if _complete_harness(node, imported_name=imported_name)
    ]
    if len(harnesses) != 1:
        raise AssertionError(
            f"{path.name} must wrap its real App build in exactly one complete "
            f"{SHARED_HARNESS}(private_app_data, environment=environment) context"
        )
    harness = harnesses[0]
    executions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_video_studio_execution(node)
    ]
    if len(executions) != 1:
        raise AssertionError(
            f"{path.name} must execute exactly one WDIO/App command after its build, "
            f"got {len(executions)}"
        )
    execution_enclosing = tuple(_enclosing_nodes(executions[0], parents))
    if harness not in execution_enclosing or executions[0].lineno <= builds[0].lineno:
        raise AssertionError(
            f"{path.name} must keep the subsequent WDIO/App execution after the "
            "build inside the same startup harness context"
        )
    for node in function_scope:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            if not bool(node.test.value):
                raise AssertionError(
                    f"{path.name} hides the App build behind an unreachable branch"
                )


def check_the_driver_set_is_derived_and_currently_contains_seven_builds() -> None:
    drivers = video_studio_build_drivers()
    assert len(drivers) == 7, (
        "the executable video-studio build surface changed; expected the current "
        f"seven independent drivers, derived {len(drivers)}: "
        f"{', '.join(path.name for path in drivers)}"
    )


def check_every_real_build_is_inside_the_complete_shared_harness() -> None:
    failures: list[str] = []
    for path in video_studio_build_drivers():
        try:
            check_driver(path)
        except AssertionError as error:
            failures.append(str(error))
    assert not failures, "\n".join(failures)


def _assigned_driver_environment_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "environment"
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                    and target.slice.value.startswith("AUTOMATION_TOOL_")
                ):
                    names.add(target.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "environment"
            and node.func.attr == "update"
        ):
            for argument in node.args:
                if isinstance(argument, ast.Dict):
                    names.update(
                        key.value
                        for key in argument.keys
                        if isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value.startswith("AUTOMATION_TOOL_")
                    )
    return names


def check_the_environment_allowlist_exactly_matches_dynamic_driver_inputs() -> None:
    assigned: set[str] = set()
    for path in video_studio_build_drivers():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assigned.update(_assigned_driver_environment_names(tree))
    expected = assigned | {prerequisites.WINDOWS_POSTGRES_ROOT_ENVIRONMENT}
    assert expected == prerequisites.VIDEO_STUDIO_DRIVER_ENVIRONMENT_NAMES, (
        "the video startup harness allowlist drifted from the dynamically "
        f"discovered driver and harness-owned inputs: expected={sorted(expected)}, "
        f"allowlist={sorted(prerequisites.VIDEO_STUDIO_DRIVER_ENVIRONMENT_NAMES)}"
    )


@contextmanager
def _temporary_driver(source: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="video-startup-gate-test-") as directory:
        path = Path(directory) / "run_decoy_acceptance.py"
        path.write_text(source, encoding="utf-8")
        yield path


def check_local_and_incomplete_harness_decoys_are_rejected() -> None:
    cases = (
        (
            """
def video_studio_startup_harness(*args, **kwargs):
    raise AssertionError

def run_desktop_acceptance():
    with video_studio_startup_harness(
        private_app_data, environment=environment
    ) as environment:
        subprocess.run([pnpm_executable(), "build:tauri:video-studio-test"])
        subprocess.run([pnpm_executable(), "exec", "wdio", "run", "config.ts"])

def main():
    run_desktop_acceptance()
""",
            "must import",
        ),
        (
            """
from desktop_e2e_prerequisites import video_studio_startup_harness

def run_desktop_acceptance():
    with video_studio_startup_harness(private_app_data):
        subprocess.run([pnpm_executable(), "build:tauri:video-studio-test"])
        subprocess.run([pnpm_executable(), "exec", "wdio", "run", "config.ts"])

def main():
    run_desktop_acceptance()
""",
            "complete video_studio_startup_harness",
        ),
        (
            """
from desktop_e2e_prerequisites import video_studio_startup_harness

def run_desktop_acceptance():
    if False:
        with video_studio_startup_harness(
            private_app_data, environment=environment
        ) as environment:
            subprocess.run([pnpm_executable(), "build:tauri:video-studio-test"])
            subprocess.run([pnpm_executable(), "exec", "wdio", "run", "config.ts"])

def main():
    run_desktop_acceptance()
""",
            "unreachable branch",
        ),
        (
            """
from desktop_e2e_prerequisites import video_studio_startup_harness

def video_studio_startup_harness(*args, **kwargs):
    raise AssertionError

def run_desktop_acceptance():
    with video_studio_startup_harness(
        private_app_data, environment=environment
    ) as environment:
        subprocess.run([pnpm_executable(), "build:tauri:video-studio-test"])
        subprocess.run([pnpm_executable(), "exec", "wdio", "run", "config.ts"])

def main():
    run_desktop_acceptance()
""",
            "same-name decoy",
        ),
        (
            """
from desktop_e2e_prerequisites import video_studio_startup_harness

def outer():
    def run_desktop_acceptance():
        with video_studio_startup_harness(
            private_app_data, environment=environment
        ) as environment:
            subprocess.run([pnpm_executable(), "build:tauri:video-studio-test"])
            subprocess.run([pnpm_executable(), "exec", "wdio", "run", "config.ts"])

def main():
    pass
""",
            "uncalled nested function",
        ),
    )
    for source, expected_failure in cases:
        with _temporary_driver(source) as path:
            try:
                check_driver(path)
            except AssertionError as error:
                assert expected_failure in str(error), (
                    f"the decoy was rejected for the wrong reason: {error}"
                )
                continue
            raise AssertionError(f"the structural gate accepted a decoy:\n{source}")


def check_a_build_only_context_does_not_leave_wdio_unprotected() -> None:
    source = """
from desktop_e2e_prerequisites import video_studio_startup_harness

def run_desktop_acceptance():
    with video_studio_startup_harness(
        private_app_data, environment=environment
    ) as environment:
        subprocess.run([pnpm_executable(), "build:tauri:video-studio-test"])
    subprocess.run([pnpm_executable(), "exec", "wdio", "run", "config.ts"])

def main():
    run_desktop_acceptance()
"""
    with _temporary_driver(source) as path:
        try:
            check_driver(path)
        except AssertionError as error:
            assert "same startup harness context" in str(error)
        else:
            raise AssertionError("the structural gate accepted WDIO outside the harness")


class _FakeServer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.events.append("server_terminate")
        self.returncode = 0

    def kill(self) -> None:
        self.events.append("server_kill")
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.events.append(f"server_wait:{timeout}")
        assert self.returncode is not None
        return self.returncode


def _fake_lifecycle_modules(
    events: list[str],
    *,
    fail_control_plane: bool,
    fail_control_plane_close_probe: bool = False,
) -> dict[str, types.ModuleType]:
    postgres = types.ModuleType("acceptance_postgres")

    @contextmanager
    def managed_test_postgres(**_kwargs: object) -> Iterator[None]:
        events.append("postgres_enter")
        try:
            yield
        finally:
            events.append("postgres_down_volumes")

    postgres.managed_test_postgres = managed_test_postgres  # type: ignore[attr-defined]

    e4 = types.ModuleType("run_e4_14_acceptance")

    def start_control_plane(*, port: int, environment: dict[str, str]) -> _FakeServer:
        events.append(f"control_plane_start:{port}")
        assert environment["AUTOMATION_TOOL_DATABASE_URL"].startswith("postgresql+asyncpg://")
        if fail_control_plane:
            raise RuntimeError("synthetic Control Plane startup failure")
        return _FakeServer(events)

    e4.start_control_plane = start_control_plane  # type: ignore[attr-defined]

    ports = iter((15433, 15434))
    i2 = types.ModuleType("run_i2_13_acceptance")
    i2.BACKEND_ROOT = ROOT / "backend"  # type: ignore[attr-defined]

    def compose_command(project_name: str) -> list[str]:
        prefix = "automation-tool-video-studio-"
        assert project_name.startswith(prefix)
        UUID(project_name.removeprefix(prefix))
        return ["docker", "compose", "--project-name", project_name]

    i2.compose_command = compose_command  # type: ignore[attr-defined]
    i2.unused_loopback_port = lambda: next(ports)  # type: ignore[attr-defined]

    def require_port_closed(port: int) -> None:
        events.append(f"port_closed:{port}")
        if fail_control_plane_close_probe and port == VIDEO_CONTROL_PLANE_PORT:
            raise RuntimeError("misclassified unknown Control Plane listener")

    i2.require_port_closed = require_port_closed  # type: ignore[attr-defined]
    return {
        "acceptance_postgres": postgres,
        "run_e4_14_acceptance": e4,
        "run_i2_13_acceptance": i2,
    }


def _exercise_lifecycle(
    *,
    fail_control_plane: bool,
    fail_control_plane_close_probe: bool = False,
) -> tuple[list[str], bool]:
    events: list[str] = []
    modules = _fake_lifecycle_modules(
        events,
        fail_control_plane=fail_control_plane,
        fail_control_plane_close_probe=fail_control_plane_close_probe,
    )

    def prepare(private_app_data: Path, **_kwargs: object) -> None:
        assert private_app_data.name == "private-app-data"
        events.append("local_resources")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert command[-4:] == ["-m", "alembic", "upgrade", "head"]
        assert kwargs["check"] is True
        events.append("alembic_upgrade")
        return subprocess.CompletedProcess(command, 0)

    rejected = False
    with tempfile.TemporaryDirectory() as directory:
        private_app_data = Path(directory) / "private-app-data"
        original = {
            "PATH": os.environ.get("PATH", ""),
            "TAURI_WEBDRIVER_PORT": "4444",
            "AUTOMATION_TOOL_BM08_BROWSER": "/isolated/browser",
            "AUTOMATION_TOOL_BM08_EVIDENCE_VIDEO": "/isolated/evidence.mp4",
            "AUTOMATION_TOOL_IM05_WORKER": "/isolated/worker",
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": "production",
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY": "production-secret",
            "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER": "production-secret",
        }
        with (
            patch.dict(sys.modules, modules),
            patch.object(prerequisites, "port_is_free", return_value=True),
            patch.object(prerequisites, "prepare_startup_gate", side_effect=prepare),
            patch.object(prerequisites.subprocess, "run", side_effect=run),
        ):
            try:
                with prerequisites.video_studio_startup_harness(
                    private_app_data,
                    environment=original,
                ) as environment:
                    events.append("driver_body")
                    assert environment["TAURI_WEBDRIVER_PORT"] == "4444"
                    assert environment["AUTOMATION_TOOL_IM05_WORKER"] == ("/isolated/worker")
                    assert environment["AUTOMATION_TOOL_BM08_EVIDENCE_VIDEO"] == (
                        "/isolated/evidence.mp4"
                    )
                    assert "AUTOMATION_TOOL_BM08_BROWSER" not in environment
                    assert "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID" not in environment
                    assert "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY" not in environment
                    assert "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER" not in environment
                    assert environment["AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN"] == (
                        "http://127.0.0.1:8765"
                    )
                    assert (
                        environment["AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY"]
                        == prerequisites.ACTION_AUTHORIZATION_PUBLIC_KEY
                    )
            except RuntimeError as error:
                assert fail_control_plane
                assert "synthetic Control Plane startup failure" in str(error)
                rejected = True
        assert original == {
            "PATH": os.environ.get("PATH", ""),
            "TAURI_WEBDRIVER_PORT": "4444",
            "AUTOMATION_TOOL_BM08_BROWSER": "/isolated/browser",
            "AUTOMATION_TOOL_BM08_EVIDENCE_VIDEO": "/isolated/evidence.mp4",
            "AUTOMATION_TOOL_IM05_WORKER": "/isolated/worker",
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": "production",
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY": "production-secret",
            "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER": "production-secret",
        }
    return events, rejected


def check_complete_harness_supplies_truthful_environment_and_cleans_up() -> None:
    events, rejected = _exercise_lifecycle(fail_control_plane=False)
    assert not rejected
    assert events == [
        "local_resources",
        "postgres_enter",
        "alembic_upgrade",
        "control_plane_start:8765",
        "driver_body",
        "server_terminate",
        "server_wait:10",
        "postgres_down_volumes",
        "port_closed:8765",
        "port_closed:15433",
    ]


def check_control_plane_startup_failure_still_cleans_database_and_ports() -> None:
    events, rejected = _exercise_lifecycle(fail_control_plane=True)
    assert rejected
    assert events == [
        "local_resources",
        "postgres_enter",
        "alembic_upgrade",
        "control_plane_start:8765",
        "postgres_down_volumes",
        "port_closed:15433",
    ]


def check_a_startup_port_race_preserves_the_original_rejection() -> None:
    events, rejected = _exercise_lifecycle(
        fail_control_plane=True,
        fail_control_plane_close_probe=True,
    )
    assert rejected, "the startup rejection was replaced by a close-probe diagnosis"
    assert "port_closed:8765" not in events
    assert not any(event.startswith("server_") for event in events)
    assert events[-1] == "port_closed:15433"


def check_an_occupied_fixed_port_is_rejected_without_touching_its_owner() -> None:
    with (
        patch.object(prerequisites, "port_is_free", return_value=False),
        patch.object(prerequisites, "prepare_startup_gate") as prepare,
        patch.object(prerequisites, "_terminate_owned_control_plane") as terminate,
        patch.object(prerequisites.subprocess, "run") as run,
    ):
        try:
            with prerequisites.video_studio_startup_harness(
                Path("/not-created"),
                environment={"PATH": os.environ.get("PATH", "")},
            ):
                raise AssertionError("an occupied production origin was reused")
        except prerequisites.DesktopPrerequisiteRejected as error:
            assert "occupied" in str(error)
        else:
            raise AssertionError("an occupied production origin was accepted")
    prepare.assert_not_called()
    terminate.assert_not_called()
    run.assert_not_called()


def check_control_plane_health_failure_stops_the_spawned_process() -> None:
    with patch.object(
        prerequisites,
        "reserve_control_plane_port",
        return_value=18765,
    ):
        module = importlib.import_module("run_e4_14_acceptance")
    events: list[str] = []
    server = _FakeServer(events)
    with (
        patch.object(module, "require_port_available"),
        patch.object(module.subprocess, "Popen", return_value=server),
        patch.object(
            module,
            "wait_for_control_plane",
            side_effect=RuntimeError("synthetic unhealthy Control Plane"),
        ),
    ):
        try:
            module.start_control_plane(
                port=VIDEO_CONTROL_PLANE_PORT,
                environment={"PATH": os.environ.get("PATH", "")},
            )
        except RuntimeError as error:
            assert "synthetic unhealthy Control Plane" in str(error)
        else:
            raise AssertionError("an unhealthy Control Plane was accepted")
    assert events == ["server_terminate", "server_wait:10"], (
        "start_control_plane leaked the process it spawned when health waiting failed"
    )


def check_partial_compose_startup_still_runs_destructive_cleanup() -> None:
    module = importlib.import_module("acceptance_postgres")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if "up" in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    compose = ["docker", "compose", "--project-name", "video-startup-test"]
    with (
        patch.object(module.platform, "system", return_value="Darwin"),
        patch.object(module.subprocess, "run", side_effect=run),
    ):
        try:
            with module.managed_test_postgres(
                compose=compose,
                database_port=15433,
                environment={"PATH": os.environ.get("PATH", "")},
                repository_root=ROOT,
            ):
                raise AssertionError("a failed compose up yielded its context")
        except subprocess.CalledProcessError:
            pass
    assert len(calls) == 2, "compose up failure skipped the cleanup command"
    assert calls[1] == [
        *compose,
        "down",
        "--volumes",
        "--remove-orphans",
    ]


CHECKS = (
    check_the_driver_set_is_derived_and_currently_contains_seven_builds,
    check_every_real_build_is_inside_the_complete_shared_harness,
    check_the_environment_allowlist_exactly_matches_dynamic_driver_inputs,
    check_local_and_incomplete_harness_decoys_are_rejected,
    check_a_build_only_context_does_not_leave_wdio_unprotected,
    check_complete_harness_supplies_truthful_environment_and_cleans_up,
    check_control_plane_startup_failure_still_cleans_database_and_ports,
    check_a_startup_port_race_preserves_the_original_rejection,
    check_an_occupied_fixed_port_is_rejected_without_touching_its_owner,
    check_control_plane_health_failure_stops_the_spawned_process,
    check_partial_compose_startup_still_runs_destructive_cleanup,
)


def main() -> int:
    failures: list[str] = []
    for check in CHECKS:
        try:
            check()
        except (
            AssertionError,
            AttributeError,
            OSError,
            RuntimeError,
            SyntaxError,
            ValueError,
        ) as error:
            failures.append(f"{check.__name__}: {error}")
            print(f"FAIL  {check.__name__}: {error}")
        else:
            print(f"ok  {check.__name__}")
    print(f"executed checks: {len(CHECKS)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
