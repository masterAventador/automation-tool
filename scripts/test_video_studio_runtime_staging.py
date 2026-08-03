#!/usr/bin/env python3
"""Checks for staging the video runtime into a video-studio acceptance App.

The `video-studio-e2e` build used to resolve ffmpeg, both video Workers and the
browser from environment variables the acceptance scripts set, while the release
read them from the packaged resource directory. Acceptance therefore never
asked the only question that mattered — whether the installed application
carries those resources — and a release shipped with none of them.

The build now resolves them exactly once, from the resource directory, in every
configuration. These checks cover the staging step that has to put real
resources there before an acceptance App can run.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_video_runtime import host_platform  # noqa: E402
from release_assembly import VIDEO_RUNTIME_RESOURCES  # noqa: E402
import run_bm_08_acceptance as bm08_acceptance  # noqa: E402
from run_vf_06_acceptance import (  # noqa: E402
    DEBUG_APP_RESOURCE_ROOT,
    EMBEDDED_BROWSER_MANIFEST,
    VideoRuntimeStagingRejected,
    require_staged_embedded_browser,
    stage_video_runtime,
)

WDIO_CONFIG = ROOT / "frontend/wdio.video-studio.conf.ts"
BM08_DRIVER = ROOT / "scripts/run_bm_08_acceptance.py"
IM05_DRIVER = ROOT / "scripts/run_im_05_acceptance.py"


def _write(path: Path, content: bytes = b"payload") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _complete_staging(root: Path) -> Path:
    """A staging tree carrying this platform's artifact names, and only those.

    The names are asked of the release resource contract for the platform in
    force rather than written out for one of them. `bin/ffmpeg` is `ffmpeg.exe`
    on Windows, and the checker under test reads the same contract, so a
    fixture that names the other platform's layout fails the way a real missing
    resource would -- which is what it did on the acceptance machine.
    """
    staging = root / "staging"
    for resource in VIDEO_RUNTIME_RESOURCES:
        for name in resource.required_for(host_platform()):
            _write(staging / resource.staging_name / name)
    return staging


def check_staging_installs_every_resource_where_the_release_reads_it() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staging = _complete_staging(root)
        resources = root / "debug"

        installed = stage_video_runtime(staging=staging, resource_root=resources)

        for resource in VIDEO_RUNTIME_RESOURCES:
            location = resources.joinpath(*resource.installed_parts)
            assert location.is_dir(), f"{resource.staging_name} was not installed"
            assert installed[resource.staging_name] == location
            for name in resource.required_for(host_platform()):
                assert (location / name).is_file(), f"{name} was not installed"


def check_incomplete_staging_is_rejected_and_leaves_nothing_behind() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staging = _complete_staging(root)
        resources = root / "debug"
        broken = VIDEO_RUNTIME_RESOURCES[-1]
        shutil.rmtree(staging / broken.staging_name)

        try:
            stage_video_runtime(staging=staging, resource_root=resources)
        except VideoRuntimeStagingRejected as error:
            assert broken.staging_name in str(error)
        else:
            raise AssertionError(
                "staging accepted a tree missing "
                f"{broken.staging_name}; the App would start and fail at render time"
            )

        for resource in VIDEO_RUNTIME_RESOURCES:
            top = resources / resource.installed_parts[0]
            assert not top.exists(), (
                f"a rejected staging left {resource.staging_name} behind, so a later "
                "run would mistake a partial tree for a finished one"
            )


def check_an_empty_required_file_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staging = _complete_staging(root)
        resources = root / "debug"
        resource = VIDEO_RUNTIME_RESOURCES[0]
        empty = resource.required_for(host_platform())[0]
        _write(staging / resource.staging_name / empty, b"")

        try:
            stage_video_runtime(staging=staging, resource_root=resources)
        except VideoRuntimeStagingRejected as error:
            assert empty in str(error)
        else:
            raise AssertionError(
                "staging accepted an empty required file; the resolver would find the "
                "directory and still fail to launch"
            )


def check_restaging_replaces_a_previous_run() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staging = _complete_staging(root)
        resources = root / "debug"
        stage_video_runtime(staging=staging, resource_root=resources)
        stale = (
            resources.joinpath(*VIDEO_RUNTIME_RESOURCES[0].installed_parts) / "stale"
        )
        _write(stale)

        stage_video_runtime(staging=staging, resource_root=resources)

        assert not stale.exists(), (
            "restaging kept a file from the previous run; a debug target directory is "
            "reused, so acceptance would inherit stale resources"
        )


def check_restaging_breaks_a_resource_link_without_touching_its_target() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staging = _complete_staging(root)
        resources = root / "debug"
        resources.mkdir()
        external = root / "main-checkout-resource"
        external.mkdir()
        sentinel = external / "belongs-to-main"
        _write(sentinel, b"unchanged")
        linked = resources / VIDEO_RUNTIME_RESOURCES[0].installed_parts[0]
        linked.symlink_to(external, target_is_directory=True)

        stage_video_runtime(staging=staging, resource_root=resources)

        assert not linked.is_symlink(), "the acceptance resource stayed linked to main"
        assert sentinel.read_bytes() == b"unchanged", (
            "restaging followed the worktree link and modified main's resource"
        )


def check_a_missing_embedded_browser_names_the_provisioning_step() -> None:
    with tempfile.TemporaryDirectory() as directory:
        resources = Path(directory)
        try:
            require_staged_embedded_browser(resource_root=resources)
        except VideoRuntimeStagingRejected as error:
            assert "embedded-browser" in str(error)
            assert "build_embedded_browser_distribution" in str(error)
        else:
            raise AssertionError(
                "a missing embedded browser was accepted; the startup gate would block "
                "the workbench and every spec would fail without saying why"
            )

        _write(resources / EMBEDDED_BROWSER_MANIFEST, b"{}")
        require_staged_embedded_browser(resource_root=resources)


def check_the_resource_root_is_where_the_acceptance_app_actually_runs() -> None:
    """Tauri treats the directory holding the executable as the resource root.

    The wdio config is the single place that decides which executable the
    acceptance App is, so staging has to follow it. If the acceptance ever moves
    to a bundled target, the resource root moves with it and this check fails
    instead of silently staging into a directory nothing reads.
    """
    source = WDIO_CONFIG.read_text(encoding="utf-8")
    expected = DEBUG_APP_RESOURCE_ROOT.relative_to(ROOT / "frontend").as_posix()
    assert f'resolve("{expected}"' in source, (
        f"the wdio config no longer resolves the acceptance App from {expected}, so "
        "DEBUG_APP_RESOURCE_ROOT points at a directory the App never reads"
    )


def check_native_video_drivers_stage_the_runtime_instead_of_injecting_dead_paths() -> (
    None
):
    """Real render/WebUI drivers must feed the production resource resolvers.

    The crate deliberately ignores the old BM08/IM05 path variables. Keeping
    those variables in a driver makes its setup look complete while the App
    still reports ``render_unavailable`` from an empty resource directory.
    """
    bm08 = BM08_DRIVER.read_text(encoding="utf-8")
    for variable in (
        "AUTOMATION_TOOL_BM08_BROWSER",
        "AUTOMATION_TOOL_BM08_CHROMIUM_MAJOR",
        "AUTOMATION_TOOL_BM08_WORKER",
        "AUTOMATION_TOOL_BM08_FFMPEG",
    ):
        assert variable not in bm08, (
            f"BM-08 still injects ignored runtime path {variable}"
        )
    assert "install_video_runtime(" in bm08, (
        "BM-08 does not install its prepared runtime into the debug App resource root"
    )
    assert 'runtime_names = ("media-toolchain", "motion-video-worker")' in bm08, (
        "BM-08 must install only the two resources its render path uses"
    )
    assert "process_ids_matching" in bm08 and "terminate_matching_processes" in bm08, (
        "BM-08 no longer rejects and cleans Worker/browser/FFmpeg process residue"
    )

    im05 = IM05_DRIVER.read_text(encoding="utf-8")
    normal_app_entry = im05.split("def require_normal_app_entry", 1)[1].split(
        "def require_evidence", 1
    )[0]
    assert "AUTOMATION_TOOL_IM05_WORKER" not in normal_app_entry, (
        "IM-05 normal App acceptance still injects a Worker path the product ignores"
    )
    assert "install_video_runtime(" in normal_app_entry, (
        "IM-05 does not install its frozen Worker into the debug App resource root"
    )


def check_bm08_builds_frontend_before_tauri_compile() -> None:
    """A fresh worktree has no dist directory for Tauri's generate_context macro."""

    commands: list[list[str]] = []
    with (
        mock.patch.object(bm08_acceptance, "pnpm_executable", return_value="pnpm"),
        mock.patch.object(
            bm08_acceptance,
            "_run",
            side_effect=lambda arguments, **_options: commands.append(arguments),
        ),
    ):
        bm08_acceptance.run_deterministic_gates()

    frontend_build = ["pnpm", "--dir", "frontend", "build"]
    cargo_test = next(
        command for command in commands if command[:2] == ["cargo", "test"]
    )
    assert frontend_build in commands, (
        "BM-08 starts Tauri compilation without first creating frontend/dist"
    )
    assert commands.index(frontend_build) < commands.index(cargo_test), (
        "BM-08 creates frontend/dist only after Tauri already needed it"
    )


def check_the_staging_fixture_matches_what_the_checker_requires_on_every_platform() -> (
    None
):
    """The fixture has to fabricate the artifact names of the platform it runs on.

    Measured on the Windows acceptance machine 2026-07-27 (T123): this file
    failed there, and nothing it covers had gone wrong. `_complete_staging`
    asked the release resource contract for `macos` outright, so it wrote
    `bin/ffmpeg` while `require_staged_video_runtime` -- reading the same
    contract for the platform it is actually on -- demanded `bin/ffmpeg.exe`.
    A test whose own scaffolding fails the way a defect would is worse than no
    test: it costs a diagnosis every time someone runs the suite on Windows.

    Both branches are driven from here by moving the one fact they both derive
    from, `sys.platform`. Nothing on this path is platform-native -- staging is
    a copy and the check is a name lookup -- so a macOS run can hold the
    Windows layout to the same standard. What it cannot reach is a real
    Windows filesystem; T124 records what was re-verified on one.
    """
    for platform_id, expected in (("darwin", "macos"), ("win32", "windows")):
        with mock.patch.object(sys, "platform", platform_id):
            assert host_platform() == expected, (
                "the fixture and the checker no longer read the same platform, so "
                "staging for one and verifying against the other would pass by luck"
            )
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                staging = _complete_staging(root)

                for resource in VIDEO_RUNTIME_RESOURCES:
                    tree = staging / resource.staging_name
                    staged = {
                        path.relative_to(tree).as_posix()
                        for path in tree.rglob("*")
                        if path.is_file()
                    }
                    assert staged == set(resource.required_for(expected)), (
                        f"on {expected} the fixture staged {sorted(staged)} for "
                        f"{resource.staging_name}, but the contract names "
                        f"{sorted(resource.required_for(expected))}; staging both "
                        "spellings would hide the disagreement rather than settle it"
                    )

                installed = stage_video_runtime(
                    staging=staging, resource_root=root / "debug"
                )
                for resource in VIDEO_RUNTIME_RESOURCES:
                    for name in resource.required_for(expected):
                        assert (installed[resource.staging_name] / name).is_file(), (
                            f"{name} was not installed on {expected}"
                        )


def check_every_declared_check_is_registered() -> None:
    """A check that is defined but not listed runs zero times and says nothing.

    The same guard `test_desktop_e2e_prerequisites.py` grew on 2026-07-27,
    after a check appended there ran zero times while the output still read as
    a tidy pass. The count printed below is derived from `CHECKS` and so cannot
    drift from what ran -- but membership is hand-maintained, and this file is
    the same shape.
    """
    declared = {
        name
        for name, value in globals().items()
        if name.startswith("check_") and callable(value)
    }
    registered = {check.__name__ for check in CHECKS}
    missing = sorted(declared - registered)
    assert not missing, f"defined but never run: {missing}"


CHECKS = (
    check_the_resource_root_is_where_the_acceptance_app_actually_runs,
    check_staging_installs_every_resource_where_the_release_reads_it,
    check_incomplete_staging_is_rejected_and_leaves_nothing_behind,
    check_an_empty_required_file_is_rejected,
    check_restaging_replaces_a_previous_run,
    check_restaging_breaks_a_resource_link_without_touching_its_target,
    check_a_missing_embedded_browser_names_the_provisioning_step,
    check_native_video_drivers_stage_the_runtime_instead_of_injecting_dead_paths,
    check_bm08_builds_frontend_before_tauri_compile,
    check_the_staging_fixture_matches_what_the_checker_requires_on_every_platform,
    check_every_declared_check_is_registered,
)


def main() -> int:
    for check in CHECKS:
        check()
        print(f"ok  {check.__name__}")
    print(f"video studio runtime staging checks passed ({len(CHECKS)} checks)")
    print(f"executed checks: {len(CHECKS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
