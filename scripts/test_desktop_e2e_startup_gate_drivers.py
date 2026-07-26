#!/usr/bin/env python3
"""Keep every plain `desktop-e2e` acceptance App inside the startup harness.

`fb6d122` (2026-07-26 21:16) pointed `test-tauri-main.tsx` at the production
composition root, so these Apps stopped mounting the workbench behind a stub
that always answered "ready" and started running the real startup gate. None of
their drivers supplied what that gate asks for, so the whole family has been
failing on its first assertion ever since — while the task ledgers still carried
a green run from that morning.

The `control-plane-e2e` family already has `prepare_startup_gate`, and the
`video-studio-e2e` family already has a complete harness. This layer is the
third: builds that carry the plain `desktop-e2e` feature compile the *product
default* Control Plane origin, because `configured_local_control_plane_origin()`
is only an `option_env!` under `control-plane-e2e`. So a driver here cannot pick
a free port for the App to call — it has to own the product's own origin.

Nothing here is a hand-kept list. The driver set is derived from the build
commands, so a seventh acceptance App of this shape is covered the day it is
declared rather than the day someone remembers this file.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "scripts"
PACKAGE_JSON = ROOT / "frontend" / "package.json"
sys.path.insert(0, str(SCRIPTS_ROOT))

import desktop_e2e_prerequisites as prerequisites  # noqa: E402

SHARED_MODULE = "desktop_e2e_prerequisites"
SHARED_HARNESS = "desktop_e2e_startup_harness"

# Drivers that need the same four gate inputs but cannot take them from this
# harness yet, each with the reason and the work that removes the exemption. The
# list is here rather than absent so that the gate cannot go green while part of
# the family is still dying at the first assertion — the failure mode T125 was
# opened to end. Every entry must name a concrete blocker.
REVIEWED_UNWIRED_DRIVERS = {
    "run_h8_22_macos_package_acceptance.py": (
        "The packaged updater replaces the whole .app mid-run, so the embedded "
        "browser and the signed Executor have to travel inside every built "
        "bundle AND inside the updater tarball tauri produces during the same "
        "build. That is a bundle.resources change to "
        "tauri.update-macos-package-e2e.conf.json, not a driver change, and it "
        "multiplies three builds by ~340 MB of copy plus tar. Staging into the "
        "installed copy after the build would pass the first scenario and then "
        "fail 'verify-installed' on the updated bundle."
    ),
    "run_h8_22_windows_package_acceptance.py": (
        "Same bundle.resources blocker as its macOS twin, and no Windows host "
        "was available to verify a change to it; wiring it blind would put a "
        "green gate on a driver nobody could run."
    ),
}

# The six drivers this layer currently owns. Named so that a change to the set
# is a decision someone makes on purpose, while the *membership test* below
# stays derived from the build commands.
EXPECTED_DRIVERS = (
    "run_h8_13_acceptance.py",
    "run_h8_20_acceptance.py",
    "run_h8_21_acceptance.py",
    "run_h8_22_macos_package_acceptance.py",
    "run_h8_22_windows_package_acceptance.py",
    "run_vf_05_acceptance.py",
)

_PLAIN_DESKTOP_E2E_FEATURES = re.compile(r"--features\s+desktop-e2e(?![\w-])")


def plain_desktop_e2e_build_scripts() -> frozenset[str]:
    """npm build scripts that compile the plain `desktop-e2e` feature."""
    scripts = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["scripts"]
    return frozenset(
        name
        for name, command in scripts.items()
        if name.startswith("build:tauri:") and _PLAIN_DESKTOP_E2E_FEATURES.search(command)
    )


def _builds_the_feature_inline(tree: ast.AST) -> bool:
    """True when the driver spells out `tauri build --features desktop-e2e`.

    The two packaged-update drivers do not go through an npm script: they build
    the bundle themselves so they can rewrite the version between builds.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        constants = [item.value for item in node.elts if isinstance(item, ast.Constant)]
        if "tauri" in constants and "build" in constants and "desktop-e2e" in constants:
            return True
    return False


def desktop_e2e_drivers() -> tuple[Path, ...]:
    """Every `scripts/run_*.py` that launches a plain `desktop-e2e` App."""
    tokens = plain_desktop_e2e_build_scripts()
    drivers: list[Path] = []
    for path in sorted(SCRIPTS_ROOT.glob("run_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if any(token in source for token in tokens) or _builds_the_feature_inline(tree):
            drivers.append(path)
    return tuple(drivers)


def check_the_driver_set_is_derived_and_contains_the_six_known_drivers() -> None:
    derived = tuple(path.name for path in desktop_e2e_drivers())
    assert derived == EXPECTED_DRIVERS, (
        "the plain desktop-e2e App surface changed; derived "
        f"{list(derived)} but this layer is defined as {list(EXPECTED_DRIVERS)}"
    )


# --------------------------------------------------------------------------- #
# Structure: every App build and every WDIO run sits under the shared harness
# --------------------------------------------------------------------------- #


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _enclosing_nodes(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> Iterator[ast.AST]:
    while node in parents:
        node = parents[node]
        yield node


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
    imported = imports[0].asname or imports[0].name
    shadowed = any(
        (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == imported
        )
        or (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == imported)
        or (isinstance(node, ast.arg) and node.arg == imported)
        for node in ast.walk(tree)
    )
    if shadowed:
        raise AssertionError(f"{path.name} shadows the imported {SHARED_HARNESS} with a decoy")
    return imported


def _is_harness_context(node: ast.AST, *, imported_name: str) -> bool:
    """A `with harness(app_data, environment=...) as environment:` statement.

    The bound name matters: a driver that enters the harness and then builds the
    App with the environment it assembled *before* the harness gets none of the
    compile-time gate inputs, which is the exact failure this file exists for.
    """
    if not isinstance(node, (ast.With, ast.AsyncWith)) or len(node.items) != 1:
        return False
    item = node.items[0]
    call = item.context_expr
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == imported_name
        and len(call.args) == 1
        and any(keyword.arg == "environment" for keyword in call.keywords)
        and isinstance(item.optional_vars, ast.Name)
    )


def _app_launch_calls(tree: ast.AST, tokens: frozenset[str]) -> tuple[ast.Call, ...]:
    """Calls that build the acceptance App or run its WDIO session."""
    launches: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for argument in node.args:
            if not isinstance(argument, (ast.List, ast.Tuple)):
                continue
            constants = {
                item.value
                for item in argument.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
            if constants & tokens or "wdio" in constants or {"tauri", "build"} <= constants:
                launches.append(node)
                break
    return tuple(launches)


def _top_level_owner(
    node: ast.AST, tree: ast.Module, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    owner = None
    for enclosing in _enclosing_nodes(node, parents):
        if isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = enclosing
    return owner if owner in tree.body else None


def check_driver(path: Path, tokens: frozenset[str]) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_name = _imported_harness_name(tree, path)
    parents = _parents(tree)
    contexts = [
        node for node in ast.walk(tree) if _is_harness_context(node, imported_name=imported_name)
    ]
    if len(contexts) != 1:
        raise AssertionError(
            f"{path.name} must enter exactly one complete "
            f"{SHARED_HARNESS}(app_data, environment=...) context, found {len(contexts)}"
        )
    context = contexts[0]
    for enclosing in _enclosing_nodes(context, parents):
        if isinstance(enclosing, ast.If) and isinstance(enclosing.test, ast.Constant):
            if not bool(enclosing.test.value):
                raise AssertionError(f"{path.name} hides the startup harness behind a dead branch")

    launches = _app_launch_calls(tree, tokens)
    if not launches:
        raise AssertionError(f"{path.name} builds or runs no acceptance App at all")

    # A launch is covered when it is lexically inside the harness, or when the
    # only way to reach its function is from inside the harness. The packaged
    # drivers build three versions from a helper, so covering only the lexical
    # case would reject a correct driver.
    covered_owners = {
        owner.name
        for call in ast.walk(context)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        for owner in tree.body
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) and owner.name == call.func.id
    }
    uncovered: list[str] = []
    for launch in launches:
        if context in tuple(_enclosing_nodes(launch, parents)):
            continue
        owner = _top_level_owner(launch, tree, parents)
        if owner is not None and owner.name in covered_owners:
            continue
        uncovered.append(f"line {launch.lineno}")
    if uncovered:
        raise AssertionError(
            f"{path.name} builds or runs the App outside the startup harness at "
            f"{', '.join(uncovered)}"
        )


def check_every_desktop_e2e_driver_prepares_the_startup_gate() -> None:
    tokens = plain_desktop_e2e_build_scripts()
    failures: list[str] = []
    for path in desktop_e2e_drivers():
        if path.name in REVIEWED_UNWIRED_DRIVERS:
            continue
        try:
            check_driver(path, tokens)
        except AssertionError as error:
            failures.append(str(error))
    assert not failures, "\n".join(failures)


def check_each_exemption_is_a_current_driver_carrying_a_written_blocker() -> None:
    """An exemption outlives its reason silently unless something re-reads it."""
    derived = {path.name for path in desktop_e2e_drivers()}
    stale = sorted(set(REVIEWED_UNWIRED_DRIVERS) - derived)
    assert not stale, f"these exemptions name drivers that no longer exist: {stale}"
    thin = sorted(name for name, reason in REVIEWED_UNWIRED_DRIVERS.items() if len(reason) < 80)
    assert not thin, f"these exemptions carry no usable blocker: {thin}"
    tokens = plain_desktop_e2e_build_scripts()
    repaired = []
    for name in REVIEWED_UNWIRED_DRIVERS:
        try:
            check_driver(SCRIPTS_ROOT / name, tokens)
        except AssertionError:
            continue
        repaired.append(name)
    assert not repaired, (
        f"these drivers now satisfy the harness and must lose their exemption: {repaired}"
    )


@contextmanager
def _temporary_driver(source: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="desktop-startup-gate-test-") as directory:
        path = Path(directory) / "run_decoy_acceptance.py"
        path.write_text(source, encoding="utf-8")
        yield path


def check_incomplete_and_decoy_harnesses_are_rejected() -> None:
    tokens = frozenset({"build:tauri:update-download-test"})
    cases = (
        (
            """
def desktop_e2e_startup_harness(*args, **kwargs):
    raise AssertionError

def main():
    with desktop_e2e_startup_harness(private_app_data, environment=environment) as environment:
        subprocess.run([pnpm_executable(), "build:tauri:update-download-test"])
""",
            "must import",
        ),
        (
            """
from desktop_e2e_prerequisites import desktop_e2e_startup_harness

def main():
    with desktop_e2e_startup_harness(private_app_data):
        subprocess.run([pnpm_executable(), "build:tauri:update-download-test"])
""",
            "exactly one complete",
        ),
        (
            """
from desktop_e2e_prerequisites import desktop_e2e_startup_harness

def main():
    if False:
        with desktop_e2e_startup_harness(private_app_data, environment=environment) as environment:
            subprocess.run([pnpm_executable(), "build:tauri:update-download-test"])
""",
            "dead branch",
        ),
        (
            """
from desktop_e2e_prerequisites import desktop_e2e_startup_harness

def main():
    with desktop_e2e_startup_harness(private_app_data, environment=environment) as environment:
        subprocess.run([pnpm_executable(), "build:tauri:update-download-test"])
    subprocess.run([pnpm_executable(), "exec", "wdio", "run", "conf.ts"])
""",
            "outside the startup harness",
        ),
    )
    for source, expected in cases:
        with _temporary_driver(source) as path:
            try:
                check_driver(path, tokens)
            except AssertionError as error:
                assert expected in str(error), f"the decoy was rejected for the wrong reason: {error}"
                continue
            raise AssertionError(f"the structural gate accepted a decoy:\n{source}")


def check_a_helper_called_from_the_harness_is_accepted() -> None:
    source = """
from desktop_e2e_prerequisites import desktop_e2e_startup_harness

def build_version(environment):
    subprocess.run([pnpm_executable(), "exec", "tauri", "build", "--features", "desktop-e2e"])

def main():
    with desktop_e2e_startup_harness(private_app_data, environment=environment) as environment:
        build_version(environment)
        subprocess.run([pnpm_executable(), "exec", "wdio", "run", "conf.ts"])
"""
    with _temporary_driver(source) as path:
        check_driver(path, frozenset())


# --------------------------------------------------------------------------- #
# Behaviour: what the harness actually does around one acceptance run
# --------------------------------------------------------------------------- #


class _FakeHealthServer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.stopped = False

    def stop(self) -> None:
        self.events.append("health_stop")
        self.stopped = True


def _exercise_harness(*, fail_health_server: bool) -> tuple[list[str], dict[str, str], bool]:
    events: list[str] = []
    server = _FakeHealthServer(events)

    def prepare(private_app_data: Path, **_kwargs: object) -> None:
        assert private_app_data.name == "private-app-data"
        events.append("local_resources")

    def start(*, port: int) -> _FakeHealthServer:
        events.append(f"health_start:{port}")
        if fail_health_server:
            raise RuntimeError("synthetic health Control Plane startup failure")
        return server

    closed: list[int] = []
    captured: dict[str, str] = {}
    rejected = False
    with tempfile.TemporaryDirectory() as directory:
        private_app_data = Path(directory) / "private-app-data"
        original = {
            "PATH": os.environ.get("PATH", ""),
            "AUTOMATION_TOOL_UPDATE_ENDPOINT": "https://127.0.0.1:1/feed",
        }
        with (
            patch.object(prerequisites, "port_is_free", return_value=True),
            patch.object(prerequisites, "prepare_startup_gate", side_effect=prepare),
            patch.object(prerequisites, "_start_health_control_plane", side_effect=start),
            patch.object(
                prerequisites,
                "require_product_origin_released",
                side_effect=lambda port: closed.append(port),
            ),
        ):
            try:
                with prerequisites.desktop_e2e_startup_harness(
                    private_app_data,
                    environment=original,
                ) as environment:
                    events.append("driver_body")
                    captured = dict(environment)
            except RuntimeError as error:
                assert fail_health_server, error
                assert "synthetic health Control Plane startup failure" in str(error)
                rejected = True
        assert original == {
            "PATH": os.environ.get("PATH", ""),
            "AUTOMATION_TOOL_UPDATE_ENDPOINT": "https://127.0.0.1:1/feed",
        }, "the harness mutated the caller's environment in place"
    assert closed == [prerequisites.PRODUCT_CONTROL_PLANE_PORT] or fail_health_server
    return events, captured, rejected


def check_the_harness_supplies_the_gate_inputs_and_stops_what_it_started() -> None:
    events, environment, rejected = _exercise_harness(fail_health_server=False)
    assert not rejected
    assert events == [
        "local_resources",
        f"health_start:{prerequisites.PRODUCT_CONTROL_PLANE_PORT}",
        "driver_body",
        "health_stop",
    ], events
    assert environment["AUTOMATION_TOOL_UPDATE_ENDPOINT"] == "https://127.0.0.1:1/feed", (
        "the harness discarded a driver input it must preserve"
    )
    assert (
        environment["AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY"]
        == prerequisites.ACTION_AUTHORIZATION_PUBLIC_KEY
    )
    assert environment["AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT"] == (
        prerequisites.LOCAL_ACTION_TASK_LIMIT
    )
    assert environment["AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS"] == (
        prerequisites.LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS
    )


def check_a_failed_health_server_does_not_report_a_running_one() -> None:
    events, _, rejected = _exercise_harness(fail_health_server=True)
    assert rejected
    assert "driver_body" not in events
    assert "health_stop" not in events


def check_an_occupied_product_origin_is_rejected_without_touching_its_owner() -> None:
    with (
        patch.object(prerequisites, "port_is_free", return_value=False),
        patch.object(prerequisites, "prepare_startup_gate") as prepare,
        patch.object(prerequisites, "_start_health_control_plane") as start,
    ):
        try:
            with prerequisites.desktop_e2e_startup_harness(
                Path("/not-created"),
                environment={"PATH": os.environ.get("PATH", "")},
            ):
                raise AssertionError("an occupied product origin was reused")
        except prerequisites.DesktopPrerequisiteRejected as error:
            assert "occupied" in str(error)
        else:
            raise AssertionError("an occupied product origin was accepted")
    prepare.assert_not_called()
    start.assert_not_called()


def check_the_health_control_plane_answers_the_product_contract() -> None:
    """The App calls `/health` and `/version` and compares both to its own build.

    A stub that returns `200 {}` would satisfy a naive probe and still leave the
    gate closed, so this asserts the real production `create_app` shape.
    """
    # One literal, because `commit_gate.discover_sys_path_roots` re-derives
    # MYPYPATH from these lines and reads a split path as two separate roots.
    sys.path.insert(0, str(ROOT / "backend/src"))
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from automation_tool.control_plane.bootstrap.app import create_app  # noqa: PLC0415

    with TestClient(create_app(database=None)) as client:
        health = client.get("/api/v1/health")
        version = client.get("/api/v1/version")
    assert health.status_code == 200, health.text
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "control-plane"
    assert version.status_code == 200, version.text
    assert version.json()["version"] == health.json()["version"]
    assert version.json()["apiVersion"] == "v1"


CHECKS = (
    check_the_driver_set_is_derived_and_contains_the_six_known_drivers,
    check_every_desktop_e2e_driver_prepares_the_startup_gate,
    check_each_exemption_is_a_current_driver_carrying_a_written_blocker,
    check_incomplete_and_decoy_harnesses_are_rejected,
    check_a_helper_called_from_the_harness_is_accepted,
    check_the_harness_supplies_the_gate_inputs_and_stops_what_it_started,
    check_a_failed_health_server_does_not_report_a_running_one,
    check_an_occupied_product_origin_is_rejected_without_touching_its_owner,
    check_the_health_control_plane_answers_the_product_contract,
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
