#!/usr/bin/env python3
"""Checks for the shared startup-gate preparation of the control-plane E2E layer.

Every `control-plane-e2e` acceptance App runs the production startup gate, so it
only mounts the workbench when four things hold at once: the compile-time action
authorization triple is baked into the binary, a verified embedded-browser
distribution sits in the resource directory, a signed Executor package is
installed under the App data directory, and the Control Plane the binary was
compiled to call is actually listening.

Between 2026-07-22 and 2026-07-26 no driver in this layer supplied the first two,
so the whole layer was blocked before its first assertion while every task ledger
recorded a pass. These checks keep the preparation in one place and keep every
driver wired to it, so the same knowledge cannot drift back into 30 copies.
"""

from __future__ import annotations

import json
import re
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from desktop_e2e_prerequisites import (  # noqa: E402
    ACTION_AUTHORIZATION_PUBLIC_KEY,
    CONTROL_PLANE_PORT_RANGE,
    DEBUG_APP_RESOURCE_ROOT,
    OPERATIONS_PROFILE_ROOT,
    DesktopPrerequisiteRejected,
    control_plane_e2e_drivers,
    remove_staged_embedded_browser,
    reserve_control_plane_port,
    stage_embedded_browser,
    startup_gate_environment,
)

SHARED_MODULE = "desktop_e2e_prerequisites"


def check_the_startup_gate_environment_supplies_every_compile_time_input() -> None:
    """All four `option_env!` inputs the gate reads must be set together.

    `startup_environment_state()` returns `ConfigurationRequired` from the action
    authorization triple before it ever looks at the installed Executor package,
    and the Rust transport bakes the Control Plane origin in at compile time. A
    driver that sets some of them still gets a blocked App.
    """
    prepared = startup_gate_environment({"PATH": "/usr/bin"}, control_plane_port=19001)

    assert prepared["AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY"] == (
        ACTION_AUTHORIZATION_PUBLIC_KEY
    ), "the action authorization public key is missing"
    assert prepared["AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS"].isdigit()
    assert prepared["AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT"].isdigit()
    assert (
        prepared["AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN"]
        == "http://127.0.0.1:19001"
    ), "the compiled-in origin must name the port this run actually serves"
    assert prepared["PATH"] == "/usr/bin", "caller isolation values must survive"


def check_the_startup_gate_environment_does_not_mutate_the_caller() -> None:
    """Drivers pass the environment they already assembled; it must stay intact."""
    original = {"AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t306"}

    prepared = startup_gate_environment(original, control_plane_port=19002)

    assert original == {"AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_t306"}
    assert prepared["AUTOMATION_TOOL_TEST_DB_NAME"] == "automation_tool_t306"


def check_reserved_control_plane_ports_stay_inside_the_project_range() -> None:
    """The port must be project-owned, verified free and stable per process.

    23 drivers used to pin 8765. Running them back to back made 20 of them fail
    in under a second on `Errno 48` without ever building an App, because the
    previous run's listener had not left `TIME_WAIT`. Nothing may be terminated
    to free a port, so the reservation moves to the next project-owned one.
    """
    port = reserve_control_plane_port()

    assert port in CONTROL_PLANE_PORT_RANGE, (
        f"{port} is outside the automation-tool range {CONTROL_PLANE_PORT_RANGE}"
    )
    assert reserve_control_plane_port() == port, (
        "one process runs one Control Plane; drivers that import each other must "
        "agree on its port"
    )
    with socket.socket() as probe:
        probe.settimeout(0.2)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, (
            "the reserved port already has a listener"
        )


def check_every_control_plane_driver_goes_through_the_shared_preparation() -> None:
    """Each driver of a `control-plane-e2e` build must use the shared module.

    The driver set is derived from `frontend/package.json`, so a new acceptance
    App cannot be added without either wiring it here or failing this check.
    """
    drivers = control_plane_e2e_drivers()
    assert len(drivers) >= 30, f"only {len(drivers)} drivers were derived"

    unwired = sorted(
        path.name
        for path in drivers
        if SHARED_MODULE not in path.read_text(encoding="utf-8")
    )
    assert not unwired, (
        "these drivers build a control-plane-e2e App without the shared startup "
        f"gate preparation: {', '.join(unwired)}"
    )


def check_no_control_plane_driver_hardcodes_the_shared_port() -> None:
    """A literal 8765 in this layer is the port-collision defect coming back."""
    offenders = sorted(
        path.name
        for path in control_plane_e2e_drivers()
        if re.search(r"\b8765\b", path.read_text(encoding="utf-8"))
    )
    assert not offenders, (
        f"these drivers still pin the Control Plane port: {', '.join(offenders)}"
    )


def check_staging_rejects_a_browser_tree_that_fails_verification() -> None:
    """A tampered cache must not reach the resource root as a silent pass.

    The App verifies every file against the EB-05 manifest, so an unverified
    tree would only turn `browser_component_missing` into
    `browser_component_damaged` and cost a full build to discover.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cache = root / "cache"
        cache.mkdir()
        (cache / "distribution-manifest.v1.json").write_text("{}", encoding="utf-8")
        resource_root = root / "debug"

        try:
            stage_embedded_browser(cache=cache, resource_root=resource_root)
        except DesktopPrerequisiteRejected:
            pass
        else:
            raise AssertionError("an unverified browser tree was staged")

        assert not (resource_root / "embedded-browser").exists(), (
            "a rejected staging left a partial tree behind"
        )


def check_a_missing_cache_names_the_step_that_builds_it() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        try:
            stage_embedded_browser(cache=root / "absent", resource_root=root / "debug")
        except DesktopPrerequisiteRejected as error:
            assert "build_embedded_browser_distribution" in str(error), (
                f"the rejection does not name the build step: {error}"
            )
        else:
            raise AssertionError("a missing browser cache was accepted")


def check_removing_the_staged_browser_leaves_no_distribution() -> None:
    """H8-16E asserts the blocked page, so it needs the browser deliberately gone.

    The resource root is shared by every acceptance App and survives runs, so a
    driver that needs the component absent has to say so rather than rely on
    whatever the previous run left behind.
    """
    with tempfile.TemporaryDirectory() as directory:
        resource_root = Path(directory)
        staged = resource_root / "embedded-browser"
        staged.mkdir()
        (staged / "distribution-manifest.v1.json").write_text("{}", encoding="utf-8")

        remove_staged_embedded_browser(resource_root=resource_root)

        assert not staged.exists(), "the staged browser survived removal"
        remove_staged_embedded_browser(resource_root=resource_root)


def check_the_resource_root_is_where_the_acceptance_app_actually_runs() -> None:
    """Staging into a directory no App reads would pass every check and fix nothing."""
    configuration = ROOT / "frontend/wdio.task-creation.conf.ts"
    expected = DEBUG_APP_RESOURCE_ROOT.relative_to(ROOT / "frontend").as_posix()
    assert f'resolve("{expected}"' in configuration.read_text(encoding="utf-8"), (
        f"the wdio config no longer runs the App from {expected}"
    )


def check_the_derived_driver_set_matches_the_packaged_builds() -> None:
    """The driver set must come from package.json, not from a hand-kept list."""
    scripts = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))[
        "scripts"
    ]
    builds = {
        name
        for name, command in scripts.items()
        if name.startswith("build:tauri:") and "control-plane-e2e" in command
    }
    assert builds, "package.json declares no control-plane-e2e build"
    tokens = builds | {
        name
        for name, command in scripts.items()
        if any(re.search(rf"\b{re.escape(build)}\b", command) for build in builds)
    }
    expected = sorted(
        path.name
        for path in (ROOT / "scripts").glob("run_*.py")
        if any(token in path.read_text(encoding="utf-8") for token in tokens)
    )
    assert sorted(path.name for path in control_plane_e2e_drivers()) == expected


def check_every_driver_names_the_operations_profile_root_the_app_writes() -> None:
    """The Profile root has one definition, and it is the one Rust creates.

    EB-09 moved the store to `embedded-browser-profiles` and declared the old
    development root neither migrated nor read. A driver that still reads the old
    root passes its App phases and then fails on its own post-conditions, which
    reads exactly like a product regression.
    """
    source = (
        ROOT / "frontend/src-tauri/src/browser_profiles.rs"
    ).read_text(encoding="utf-8")
    declared = re.search(
        r'const PROFILE_ROOT_DIRECTORY: &str = "([a-z-]+)";', source
    )
    assert declared is not None, "the Rust Profile root constant moved"
    assert OPERATIONS_PROFILE_ROOT == declared.group(1), (
        f"OPERATIONS_PROFILE_ROOT is {OPERATIONS_PROFILE_ROOT!r} but the App writes "
        f"{declared.group(1)!r}"
    )
    stale = sorted(
        path.name
        for path in control_plane_e2e_drivers()
        if '"browser-profiles"' in path.read_text(encoding="utf-8")
    )
    assert not stale, (
        "these drivers still read the pre-EB-09 Profile root instead of "
        f"OPERATIONS_PROFILE_ROOT: {', '.join(stale)}"
    )


def check_no_driver_pins_its_own_copy_of_the_executor_ledger_schema_version() -> None:
    """The ledger schema version is asserted from the product constant, not a literal.

    Each of these drivers froze the version that was current when it was written,
    so every migration silently arms all of them at once: the App phases pass and
    the driver then fails on its own post-condition, one task at a time, months
    later.
    """
    stale = sorted(
        path.name
        for path in control_plane_e2e_drivers()
        if "PRAGMA user_version" in (source := path.read_text(encoding="utf-8"))
        and "EXECUTOR_LEDGER_SCHEMA_VERSION" not in source
    )
    assert not stale, (
        "these drivers compare the Executor ledger schema version against their own "
        f"frozen literal instead of EXECUTOR_LEDGER_SCHEMA_VERSION: {', '.join(stale)}"
    )


CHECKS = (
    check_the_startup_gate_environment_supplies_every_compile_time_input,
    check_the_startup_gate_environment_does_not_mutate_the_caller,
    check_reserved_control_plane_ports_stay_inside_the_project_range,
    check_every_control_plane_driver_goes_through_the_shared_preparation,
    check_no_control_plane_driver_hardcodes_the_shared_port,
    check_staging_rejects_a_browser_tree_that_fails_verification,
    check_a_missing_cache_names_the_step_that_builds_it,
    check_removing_the_staged_browser_leaves_no_distribution,
    check_the_resource_root_is_where_the_acceptance_app_actually_runs,
    check_the_derived_driver_set_matches_the_packaged_builds,
    check_every_driver_names_the_operations_profile_root_the_app_writes,
    check_no_driver_pins_its_own_copy_of_the_executor_ledger_schema_version,
)


def main() -> int:
    for check in CHECKS:
        check()
        print(f"ok  {check.__name__}")
    print(f"desktop e2e prerequisite checks passed ({len(CHECKS)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
