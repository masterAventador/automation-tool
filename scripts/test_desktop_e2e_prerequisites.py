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
import types
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import desktop_e2e_prerequisites as prerequisites  # noqa: E402
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

# The two halves of the one Task-offer fixture in this layer. `run_t3_14_acceptance`
# owns both; `run_h8_01`-`run_h8_06` and `run_t3_18` already import them. Naming
# them here is what keeps the next driver from growing a private copy of the offer
# half and silently dropping the confirmation half.
SHARED_OFFER_SEEDER = "seed_attempt_and_offer"
SHARED_CONFIRMATION_SEEDER = "seed_task_confirmation"

# The one way this layer stops an acceptance App run, defined in the shared
# prerequisites module so the next migration reaches every driver at once.
SHARED_PROCESS_TREE_TERMINATOR = "terminate_app_process_tree"


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
    assert prepared["AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN"] == "http://127.0.0.1:19001", (
        "the compiled-in origin must name the port this run actually serves"
    )
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
        "one process runs one Control Plane; drivers that import each other must agree on its port"
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
        path.name for path in drivers if SHARED_MODULE not in path.read_text(encoding="utf-8")
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
    assert not offenders, f"these drivers still pin the Control Plane port: {', '.join(offenders)}"


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
    scripts = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))["scripts"]
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
    source = (ROOT / "frontend/src-tauri/src/browser_profiles.rs").read_text(encoding="utf-8")
    declared = re.search(r'const PROFILE_ROOT_DIRECTORY: &str = "([a-z-]+)";', source)
    assert declared is not None, "the Rust Profile root constant moved"
    assert declared.group(1) == OPERATIONS_PROFILE_ROOT, (
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


def check_every_app_created_task_offer_seeds_the_production_confirmation() -> None:
    """A seeded `task.offer` must satisfy the production unconfirmed-delivery guard.

    `SqlAlchemyTaskCommandRepository.enqueue` refuses a `task.offer` for a Task
    that carries a Douyin search-exposure definition unless a matching
    `task_target_confirmations` row exists — the guard that keeps a side effect
    from reaching a platform before the operator confirmed its targets. Every
    Task an acceptance App creates through the production `create_task` path
    carries that definition, so a driver that inserts an attempt and enqueues the
    offer itself has to seed the confirmation as well.

    Six drivers were migrated when the guard landed and six were not, and the six
    fail inside their own fixture with `TaskCommandDeliveryRejected` after the App
    phases already passed, which reads exactly like a product regression.
    """
    stale = sorted(
        path.name
        for path in control_plane_e2e_drivers()
        if (
            SHARED_OFFER_SEEDER in (source := path.read_text(encoding="utf-8"))
            or "command_type=TaskCommandType.TASK_OFFER" in source
        )
        and SHARED_CONFIRMATION_SEEDER not in source
    )
    assert not stale, (
        "these drivers enqueue a task.offer for an App-created Task without seeding "
        f"the confirmation the production guard requires: {', '.join(stale)}"
    )


def check_every_driver_stops_the_whole_app_process_tree() -> None:
    """An acceptance App run must be stopped as a tree, not as one process.

    `pnpm test:*-tauri` starts a chain — pnpm, WebdriverIO, `tauri-driver`, the
    App — and `Popen.terminate()` signals only the first link. The rest is
    reparented to init, the orphaned App rewrites the isolated App data directory
    its driver just removed, and the same driver's next run dies in under a
    second on "Refusing to reuse an existing … App data directory".
    """
    stale = sorted(
        path.name
        for path in control_plane_e2e_drivers()
        if "app_process = subprocess.Popen(" in (source := path.read_text(encoding="utf-8"))
        and (SHARED_PROCESS_TREE_TERMINATOR not in source or "start_new_session" not in source)
    )
    assert not stale, (
        "these drivers spawn an acceptance App without starting it in its own session "
        f"and stopping the whole tree through {SHARED_PROCESS_TREE_TERMINATOR}: "
        f"{', '.join(stale)}"
    )


def _write_executor_input(root: Path, relative: str, content: str) -> None:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def check_executor_cache_key_tracks_real_source_inputs() -> None:
    """Changing one Executor source line must make the shared package cache stale."""
    with tempfile.TemporaryDirectory() as directory:
        repository_root = Path(directory) / "repository"
        backend_root = repository_root / "backend"
        cache_root = repository_root / ".local/desktop-e2e/executor-package"
        source_path = backend_root / "src/automation_tool/executor/runtime.py"
        spec_path = backend_root / "automation-tool-executor.spec"
        contract_path = repository_root / "contracts/video/motion-render-canvas.v1.json"
        silero_assets_path = repository_root / "scripts/silero_vad_assets.py"

        _write_executor_input(
            repository_root,
            "backend/src/automation_tool/executor/runtime.py",
            "EXECUTOR_SENTINEL = 'before'\n",
        )
        _write_executor_input(
            repository_root,
            "backend/automation-tool-executor.spec",
            "motion_authoring_resources = [\n"
            '    "contracts/video/motion-render-canvas.v1.json",\n'
            '    "vendor/hyperframes/skills/hyperframes-core/references/'
            'minimal-composition.md",\n'
            "]\n",
        )
        _write_executor_input(
            repository_root,
            "backend/pyproject.toml",
            "[project]\nname = 'executor-cache-test'\n",
        )
        _write_executor_input(repository_root, "backend/uv.lock", "version = 1\n")
        _write_executor_input(
            repository_root,
            "scripts/silero_vad_assets.py",
            "SILERO_ASSET_SENTINEL = 'before'\n",
        )
        _write_executor_input(
            repository_root,
            "scripts/video_runtime_cache.py",
            "CACHE_SENTINEL = 'locked'\n",
        )
        for relative in (
            "contracts/protocol/executor-v1.schema.json",
            "contracts/quality/motion-catalog.v1.json",
            "contracts/video/motion-render-canvas.v1.json",
            "contracts/video/motion-one-sentence-brief.v1.json",
            "contracts/video/motion-authoring-model-call.v1.json",
            "contracts/video/motion-authoring-refusal.v1.json",
            "contracts/video/motion-storyboard-duration.v1.json",
            "contracts/video/motion-authoring-workflow.v1.json",
            "vendor/hyperframes/skills/hyperframes-core/references/minimal-composition.md",
            "vendor/hyperframes/skills/hyperframes-core/references/determinism-rules.md",
        ):
            _write_executor_input(repository_root, relative, f"{relative}\n")

        build_ids: list[str] = []
        fake_builder_module = types.ModuleType("run_e4_07_acceptance")

        def fake_build_signed_executor(workspace: Path, *, build_id: str) -> Path:
            build_ids.append(build_id)
            package = workspace / "dist/automation-tool-executor"
            package.mkdir(parents=True, exist_ok=True)
            (package / prerequisites.EXECUTOR_MANIFEST_NAME).write_text("{}", encoding="utf-8")
            (package / prerequisites.EXECUTOR_MANIFEST_SIGNATURE_NAME).write_text(
                "test", encoding="utf-8"
            )
            return package

        fake_builder_module.build_signed_executor = fake_build_signed_executor  # type: ignore[attr-defined]

        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(sys.modules, {"run_e4_07_acceptance": fake_builder_module})
            )
            stack.enter_context(patch.object(prerequisites, "REPOSITORY_ROOT", repository_root))
            stack.enter_context(
                patch.object(prerequisites, "BACKEND_ROOT", backend_root, create=True)
            )
            stack.enter_context(
                patch.object(
                    prerequisites,
                    "EXECUTOR_SOURCE_ROOT",
                    backend_root / "src",
                    create=True,
                )
            )
            stack.enter_context(
                patch.object(
                    prerequisites,
                    "EXECUTOR_SPEC_PATH",
                    backend_root / "automation-tool-executor.spec",
                    create=True,
                )
            )
            stack.enter_context(
                patch.object(prerequisites, "EXECUTOR_PACKAGE_CACHE_ROOT", cache_root)
            )

            first_package = prerequisites.ensure_signed_executor_package(
                build_id="source-sensitive"
            )
            unchanged_package = prerequisites.ensure_signed_executor_package(
                build_id="source-sensitive"
            )
            source_path.write_text("EXECUTOR_SENTINEL = 'after'\n", encoding="utf-8")
            second_package = prerequisites.ensure_signed_executor_package(
                build_id="source-sensitive"
            )
            spec_path.write_text(
                spec_path.read_text(encoding="utf-8") + "# changed spec\n",
                encoding="utf-8",
            )
            third_package = prerequisites.ensure_signed_executor_package(
                build_id="source-sensitive"
            )
            contract_path.write_text('{"changed": true}\n', encoding="utf-8")
            fourth_package = prerequisites.ensure_signed_executor_package(
                build_id="source-sensitive"
            )
            silero_assets_path.write_text(
                "SILERO_ASSET_SENTINEL = 'after'\n",
                encoding="utf-8",
            )
            fifth_package = prerequisites.ensure_signed_executor_package(
                build_id="source-sensitive"
            )

        assert (
            len(
                {
                    first_package,
                    second_package,
                    third_package,
                    fourth_package,
                    fifth_package,
                }
            )
            == 5
        ), "source, spec, contract and model asset builder must each select a new package"
        assert unchanged_package == first_package, (
            "unchanged Executor inputs must keep reusing the same cached package"
        )
        assert len(build_ids) == 5, (
            "source, spec, contract and model asset builder changes must each rebuild "
            "instead of reusing the stale signed package"
        )


def check_locked_browser_archives_use_shared_archive_resolver() -> None:
    module_source = Path(prerequisites.__file__).read_text(encoding="utf-8")
    assert "archive_path(" in module_source, (
        "locked browser archives must use the shared archive_path() worktree resolver"
    )


def check_a_stale_cache_names_the_step_that_rebuilds_it() -> None:
    """A cache that no longer matches the contract has to say what to run.

    Measured 2026-07-27: `d5e5111` changed the staging contract at 21:29 on
    07-26 to exclude Widevine, and the cached distribution on this machine had
    been built at 05:00 that morning with `exclusions: null`. Verification
    refused it correctly — and then said only that the records differ, leaving
    the reader with a rejected 340 MB tree and no next step. Thirty-six
    acceptance drivers reach that line; every one of them was stuck from 21:29
    onward and nothing reported it, because none of them runs in any gate.

    The missing-cache case has named its build step since it was written. This
    is the same courtesy for the case that actually happened.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cache = root / "cache"
        cache.mkdir()
        # Present, so the missing-cache branch does not claim it, and invalid,
        # so verification is what rejects it.
        (cache / "distribution-manifest.v1.json").write_text("{}", encoding="utf-8")

        try:
            stage_embedded_browser(cache=cache, resource_root=root / "debug")
        except DesktopPrerequisiteRejected as error:
            assert "build_embedded_browser_cache" in str(error), (
                f"the rejection does not name the rebuild step: {error}"
            )
        else:
            raise AssertionError("a stale browser cache was accepted")


def check_every_declared_check_is_registered() -> None:
    """A check that is defined but not listed runs zero times and says nothing.

    Found the hard way while writing the check above: appended after `main()`,
    it was never executed and the run still printed a tidy "16 checks passed".
    The count is derived from `CHECKS` and so cannot drift — but membership was
    hand-maintained, which is the shape this repository has been burned by
    before (T51, T62).
    """
    declared = {
        name for name, value in globals().items() if name.startswith("check_") and callable(value)
    }
    registered = {check.__name__ for check in CHECKS}
    missing = sorted(declared - registered)
    assert not missing, f"defined but never run: {missing}"


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
    check_every_app_created_task_offer_seeds_the_production_confirmation,
    check_every_driver_stops_the_whole_app_process_tree,
    check_executor_cache_key_tracks_real_source_inputs,
    check_locked_browser_archives_use_shared_archive_resolver,
    check_a_stale_cache_names_the_step_that_rebuilds_it,
    check_every_declared_check_is_registered,
)


def main() -> int:
    for check in CHECKS:
        check()
        print(f"ok  {check.__name__}")
    print(f"desktop e2e prerequisite checks passed ({len(CHECKS)} checks)")
    print(f"executed checks: {len(CHECKS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
