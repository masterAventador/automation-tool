#!/usr/bin/env python3
"""The video runtime staging tree has to be installable where readers look.

`frontend/src-tauri/tests/motion_authoring_runtime.rs` reads the motion Worker
package out of the resource directory a debug App resolves from, and when it is
absent it panics with "Build it with scripts/prepare_video_runtime.py before
running this suite."

Running that script changed nothing. It exits 0 and lays the artifacts out under
the per-machine build cache (`~/Library/Caches/automation-tool-build` on macOS);
nothing ever copied them into `target/debug`, so the remedy was a command that
succeeded without addressing the failure it was printed for. The test itself is
right to fail loudly -- its own comment records that it used to return early and
report a pass -- the pointer was what was wrong.

These tests cover the half that was missing: taking a prepared staging tree and
installing it at the layout the release resource contract declares. The staging
tree here is fabricated and stamped as current, so no Worker is built and no
byte is downloaded; what is under test is the mapping, which is what was absent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_video_runtime.py"
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_video_runtime  # noqa: E402
from release_assembly import MOTION_CATALOG_RESOURCES, VIDEO_RUNTIME_RESOURCES  # noqa: E402
from video_runtime_cache import STAMP_VERSION, contract_fingerprint  # noqa: E402

MOTION_WORKER_CONTRACT = ROOT / "contracts/quality/motion-video-worker-package.v1.json"
MOTION_WORKER_SOURCE = ROOT / "workers/motion_composition/worker.mjs"
# What `release-package-resources.v1.json` says the Worker is installed as.
INSTALLED = ("motion-video-worker", "package")

# The one declaration of what a staged motion Worker is called, per platform.
# `required_for` turns `runtime/node` into `runtime/node.exe` on Windows off the
# same contract entry the production installer reads, so a fixture that asks it
# cannot stage a tree the installer then refuses.
MOTION_WORKER = next(
    resource
    for resource in VIDEO_RUNTIME_RESOURCES
    if resource.staging_name == "motion-video-worker"
)
# The frozen catalog of animation parts. Built elsewhere, installed by the same
# code as everything else that has to land where the resolver reads it.
(MOTION_CATALOG,) = MOTION_CATALOG_RESOURCES
# The animation runtime the composition loads. It is not one of the release
# contract's required files -- that contract fixes what must not be missing,
# and this asset is proven by the Rust suite instead -- so its name comes from
# the Worker package contract, the same key `build_motion_video_worker_candidate`
# writes it from. It carries no extension that varies by platform.
AUTHORING_RUNTIME_ASSET: str = json.loads(
    MOTION_WORKER_CONTRACT.read_text(encoding="utf-8")
)["packageLayout"]["authoringRuntimeAsset"]
# Exactly the platforms `--platform` accepts, taken from the same mapping that
# populates its `choices`.
DECLARED_PLATFORMS = tuple(sorted(prepare_video_runtime.MEDIA_TOOLCHAIN_TARGETS))


def declared_inputs(resource: str) -> tuple[Path, ...]:
    """What `prepare()` actually hands the cache as that resource's key.

    Read off the real call rather than off a constant, because the defect this
    covers was a call site that named fewer inputs than the build consumes.
    Both the build and the font fetch are stubbed: this asks what the key is
    made of, and answering that must not cost a PyInstaller run.
    """
    recorded: dict[str, tuple[Path, ...]] = {}

    def record(*, name: str, contracts: Iterable[Path], build: object, root: Path) -> Path:
        recorded[name] = tuple(Path(entry) for entry in contracts)
        return Path(root) / name

    with (
        TemporaryDirectory(prefix="automation-tool-declared-inputs-") as directory,
        mock.patch.object(prepare_video_runtime, "ensure_cached", record),
        mock.patch.object(prepare_video_runtime, "ensure_subtitle_fonts", lambda **_: None),
    ):
        prepare_video_runtime.prepare(platform="macos", root=Path(directory), only=[resource])
    return recorded[resource]


# Every top-level directory a build input can live in. A literal naming one of
# these inside a build driver is a repository file whose bytes reach the
# artifact, so it has to be in that artifact's cache key.
BUILD_INPUT_PATTERN = re.compile(
    r"(?<![\w./-])((?:backend|contracts|frontend|scripts|vendor|workers)/[A-Za-z0-9_./-]+)"
)

# The scripts and spec files that turn pinned inputs into each artifact. Their
# path literals are what the gate below reads.
BUILD_DRIVERS: dict[str, tuple[Path, ...]] = {
    "media-toolchain": (
        ROOT / "scripts/build_video_media_toolchain.sh",
        ROOT / "scripts/write_video_media_toolchain_manifest.py",
    ),
    "motion-video-worker": (ROOT / "scripts/build_motion_video_worker_candidate.py",),
    "material-video-worker": (
        ROOT / "scripts/build_material_video_worker_candidate.py",
        ROOT / "workers/material_montage/material-video-worker.spec",
        ROOT / "scripts/subtitle_font_assets.py",
    ),
}

# Inputs deliberately kept out of a cache key, each with the reason it cannot
# silently change the artifact. An unlisted, undeclared input fails the gate.
EXEMPT_BUILD_INPUTS: dict[str, str] = {
    "vendor/moneyprinterturbo": (
        "a ~900 MB pinned submodule; digesting it on every cache lookup is not "
        "viable, and its exact commit is pinned by "
        "contracts/quality/third-party-sources.v1.json, which is declared in "
        "its place"
    ),
    "scripts/run_bm_02_acceptance.py": (
        "named only in the message that tells a developer which entrypoint to "
        "run; it calls the builder rather than feeding it, so its bytes cannot "
        "reach the artifact"
    ),
}


def covered_by(inputs: tuple[Path, ...], path: Path) -> bool:
    """Whether a declared input digests this path, directly or as its tree."""
    return any(entry == path or entry in path.parents for entry in inputs)


def host_payloads() -> tuple[str, ...]:
    """The Worker payload names `install()` requires on the platform in force.

    A call rather than a constant: the platform is read at use time, so a test
    can hold the fixture and its assertions to the other platform's layout.
    """
    return MOTION_WORKER.required_for(prepare_video_runtime.host_platform())


def stamped_motion_worker(staging: Path) -> Path:
    """A staging tree the cache accepts as current, so nothing gets rebuilt.

    The payload names are asked of the release resource contract for the
    platform this run installs for, rather than written out. They used to be
    written out as the macOS spelling, which is why three tests in this file
    failed on the Windows acceptance machine while nothing they cover was
    broken. Only presence and non-emptiness are read here -- `install()` checks
    both, and no test in this file executes a staged payload -- so the contents
    are placeholders and the names are the whole point.
    """
    package = staging / "motion-video-worker"
    for name in (*host_payloads(), AUTHORING_RUNTIME_ASSET):
        payload = package / name
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(b"staged by test_prepare_video_runtime\n")
    (staging / "motion-video-worker.stamp.json").write_text(
        json.dumps(
            {
                "version": STAMP_VERSION,
                "name": "motion-video-worker",
                "fingerprint": contract_fingerprint(declared_inputs("motion-video-worker")),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return package


def run_prepare(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class CacheKeysCoverEveryBuildInput(unittest.TestCase):
    """A staged artifact is only reusable if its key names everything it is made of.

    On 07-26 a user reported that all three background-music options in the
    material Worker produced silence. T32 fixed it and the commit shipped. The
    package the user built that evening still carried the control, and today's
    comparison of the two binaries found the reason: `material-video-worker`
    declared two contract files as its cache key and nothing else, so the
    Worker sources PyInstaller freezes could change without the cache noticing.
    The release step asked for the Worker, the cache said the pinned inputs
    were unchanged, and it handed back the binary from before the fix.

    The Worker source case is the reported one. The two tests that matter more
    are the ones for the other two artifacts and the gate at the end: the same
    omission is available to anyone who adds a build input later.
    """

    def test_every_material_worker_source_file_is_in_its_cache_key(self) -> None:
        inputs = declared_inputs("material-video-worker")
        sources = sorted(
            path
            for path in (ROOT / "workers/material_montage").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        self.assertTrue(sources, "the material Worker source package must exist")
        uncovered = [
            path.relative_to(ROOT).as_posix() for path in sources if not covered_by(inputs, path)
        ]
        self.assertEqual(
            [],
            uncovered,
            "editing these files changes the frozen Worker but not its cache key, "
            "so a release reuses the binary built before the change",
        )

    def test_the_frozen_worker_is_rebuilt_after_its_web_ui_source_changes(self) -> None:
        """The reported defect, asserted as behaviour rather than as a file list.

        The declared inputs are copied so the repository is never written to;
        the fingerprint is deliberately independent of where a checkout lives,
        which is what makes the copy a faithful stand-in.
        """
        webui = ROOT / "workers/material_montage/webui_runtime.py"
        self.assertTrue(webui.is_file(), "the file T32 edited must still exist")
        with TemporaryDirectory(prefix="automation-tool-worker-edit-") as directory:
            copied = []
            for index, entry in enumerate(declared_inputs("material-video-worker")):
                target = Path(directory) / str(index) / entry.name
                target.parent.mkdir(parents=True)
                if entry.is_dir():
                    shutil.copytree(entry, target)
                else:
                    shutil.copy2(entry, target)
                copied.append(target)
            edited = next(
                (path / webui.name for path in copied if (path / webui.name).is_file()),
                None,
            )
            self.assertIsNotNone(edited, f"{webui.name} must be inside a declared input")

            before = contract_fingerprint(copied)
            assert edited is not None
            edited.write_text(
                edited.read_text(encoding="utf-8") + "\n# the T32 fix\n", encoding="utf-8"
            )

            self.assertNotEqual(
                before,
                contract_fingerprint(copied),
                "the Worker web UI changed and the cache still calls the old build current",
            )

    def test_every_motion_worker_build_input_is_in_its_cache_key(self) -> None:
        inputs = declared_inputs("motion-video-worker")
        for relative in (
            "workers/motion_composition/worker.mjs",
            "contracts/quality/motion-video-worker-package.v1.json",
            # Pins the animation runtime digest that is written into the package.
            "contracts/video/offline-motion-dependencies.v1.json",
            "scripts/build_motion_video_worker_candidate.py",
        ):
            with self.subTest(relative):
                self.assertTrue(covered_by(inputs, ROOT / relative))

    def test_every_media_toolchain_build_input_is_in_its_cache_key(self) -> None:
        inputs = declared_inputs("media-toolchain")
        for relative in (
            "contracts/video/ffmpeg-toolchain.v1.json",
            "scripts/build_video_media_toolchain.sh",
            # Writes manifest.json into the built toolchain, so its bytes are
            # part of the artifact the release verifies.
            "scripts/write_video_media_toolchain_manifest.py",
        ):
            with self.subTest(relative):
                self.assertTrue(covered_by(inputs, ROOT / relative))

    def test_no_build_driver_reads_a_repository_path_outside_its_cache_key(self) -> None:
        """The gate: a new build input has to be declared or explained.

        Content digests catch an edit to a file already in the key. They cannot
        catch the other half of this defect's shape -- a driver that grows a new
        input nobody adds to the key -- because the new file was never digested
        to begin with. This reads the drivers themselves: every repository path
        they name has to be covered by that artifact's key, or listed with the
        reason it cannot change the artifact.
        """
        for resource, drivers in BUILD_DRIVERS.items():
            inputs = declared_inputs(resource)
            for driver in drivers:
                with self.subTest(resource=resource, driver=driver.name):
                    self.assertTrue(
                        covered_by(inputs, driver),
                        f"{driver.relative_to(ROOT)} decides what {resource} contains, "
                        "so editing it must invalidate the cached artifact",
                    )
                    named = {
                        match
                        for match in BUILD_INPUT_PATTERN.findall(
                            driver.read_text(encoding="utf-8")
                        )
                        if (ROOT / match).exists()
                    }
                    undeclared = sorted(
                        relative
                        for relative in named
                        if relative not in EXEMPT_BUILD_INPUTS
                        and not covered_by(inputs, ROOT / relative)
                    )
                    self.assertEqual(
                        [],
                        undeclared,
                        f"{driver.relative_to(ROOT)} reads these while building "
                        f"{resource}, but they are not in its cache key and carry no "
                        "recorded reason for being left out",
                    )


class MediaToolchainBuilderDiagnostics(unittest.TestCase):
    def test_builder_shell_can_be_selected_for_a_windows_msys2_host(self) -> None:
        shell = r"C:\msys64\usr\bin\bash.exe"
        completed = subprocess.CompletedProcess(
            args=[shell, "builder"], returncode=0, stdout=b"", stderr=b""
        )
        with (
            mock.patch.dict(os.environ, {"AUTOMATION_TOOL_BASH": shell}),
            mock.patch.object(
                prepare_video_runtime.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            prepare_video_runtime._build_media_toolchain(
                Path("unused"), platform="windows"
            )

        self.assertEqual(shell, run.call_args.args[0][0])

    def test_non_utf8_windows_builder_output_is_captured_as_bytes(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["bash", "builder"],
            returncode=1,
            stdout=b"",
            stderr=b"\xc0\xee\xea\xe0\xeb\xfc\xed\xfb\xe9 builder failure\n",
        )
        with mock.patch.object(
            prepare_video_runtime.subprocess,
            "run",
            return_value=completed,
        ) as run:
            with self.assertRaisesRegex(
                prepare_video_runtime.VideoRuntimeUnavailable,
                "stderr",
            ):
                prepare_video_runtime._build_media_toolchain(
                    Path("unused"), platform="windows"
                )

        self.assertFalse(
            run.call_args.kwargs.get("text", False),
            "Windows builder output must not be decoded by subprocess reader threads",
        )


class InstallIntoAResourceDirectory(unittest.TestCase):
    def test_the_staged_worker_installs_on_every_platform_it_is_built_for(self) -> None:
        """The fixture has to name the artifacts of the platform it stages for.

        Measured on the Windows acceptance machine 2026-07-27 (T123): three of
        this file's tests failed there, and none of them was testing anything
        that had gone wrong. The staging fixture wrote `runtime/node`, which is
        what the Worker is called on macOS; `install()` asks the release
        resource contract, which on Windows names `runtime/node.exe`. The
        fixture and the code under test disagreed about the artifact's name, so
        the suite reported a defect that only its own scaffolding had.

        Driving both branches from here is possible because neither the naming
        nor the install is platform-native work: `--platform` selects which
        contract entry `install()` enforces, so a macOS run can hold the
        Windows layout to the same standard. What this cannot prove is anything
        that needs a real Windows filesystem; see T124 for what was re-verified
        there.
        """
        for platform in DECLARED_PLATFORMS:
            with (
                self.subTest(platform=platform),
                TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory,
                mock.patch.object(
                    prepare_video_runtime, "host_platform", return_value=platform
                ),
            ):
                base = Path(directory)
                staging = base / "staging"
                staging.mkdir()
                package = stamped_motion_worker(staging)
                resources = base / "resources"

                staged = {
                    path.relative_to(package).as_posix()
                    for path in package.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(
                    {*MOTION_WORKER.required_for(platform), AUTHORING_RUNTIME_ASSET},
                    staged,
                    "the fixture must stage this platform's artifact names and no "
                    "others -- staging both spellings would hide the disagreement "
                    "rather than settle it",
                )

                completed = run_prepare(
                    "--platform",
                    platform,
                    "--root",
                    str(staging),
                    "--only",
                    "motion-video-worker",
                    "--install-into",
                    str(resources),
                )

                self.assertEqual(
                    0,
                    completed.returncode,
                    f"installing the staged Worker for {platform} must succeed:\n"
                    f"{completed.stdout}{completed.stderr}",
                )
                installed = resources.joinpath(*INSTALLED)
                for name in MOTION_WORKER.required_for(platform):
                    self.assertTrue(
                        (installed / name).is_file(),
                        f"{name} must be installed at {installed} on {platform}",
                    )

    def test_the_worker_lands_where_the_rust_test_reads_it(self) -> None:
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            base = Path(directory)
            staging = base / "staging"
            staging.mkdir()
            stamped_motion_worker(staging)
            resources = base / "resources"

            completed = run_prepare(
                "--root",
                str(staging),
                "--only",
                "motion-video-worker",
                "--install-into",
                str(resources),
            )

            self.assertEqual(
                0,
                completed.returncode,
                "preparing and installing one resource must succeed:\n"
                f"{completed.stdout}{completed.stderr}",
            )
            installed = resources.joinpath(*INSTALLED)
            for name in host_payloads():
                self.assertTrue(
                    (installed / name).is_file(),
                    f"the Worker's {name} must be installed at {installed}",
                )
            self.assertTrue(
                (installed / AUTHORING_RUNTIME_ASSET).is_file(),
                "the animation runtime the composition loads must come with it",
            )

    def test_installing_twice_replaces_rather_than_refusing(self) -> None:
        """A developer reruns cargo test; the second run must not need a rm -rf."""
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            base = Path(directory)
            staging = base / "staging"
            staging.mkdir()
            stamped_motion_worker(staging)
            resources = base / "resources"
            common = (
                "--root",
                str(staging),
                "--only",
                "motion-video-worker",
                "--install-into",
                str(resources),
            )

            self.assertEqual(0, run_prepare(*common).returncode)
            stale = resources.joinpath(*INSTALLED, "runtime", "stale-leftover")
            stale.write_text("from an older build", encoding="utf-8")

            second = run_prepare(*common)

            self.assertEqual(
                0,
                second.returncode,
                f"a repeat install must succeed:\n{second.stdout}{second.stderr}",
            )
            self.assertFalse(stale.exists(), "a repeat install must not merge into the old tree")
            for name in host_payloads():
                self.assertTrue(resources.joinpath(*INSTALLED, name).is_file())

    def test_install_replaces_a_linked_resource_root_without_touching_its_target(
        self,
    ) -> None:
        """A worktree must never follow an old resource link into main."""
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            base = Path(directory)
            staging = base / "staging"
            staging.mkdir()
            stamped_motion_worker(staging)
            resources = base / "resources"
            resources.mkdir()
            external = base / "main-checkout-motion-worker"
            external.mkdir()
            sentinel = external / "belongs-to-main"
            sentinel.write_text("leave me alone\n", encoding="utf-8")
            (resources / "motion-video-worker").symlink_to(
                external,
                target_is_directory=True,
            )

            completed = run_prepare(
                "--root",
                str(staging),
                "--only",
                "motion-video-worker",
                "--install-into",
                str(resources),
            )

            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            installed_root = resources / "motion-video-worker"
            self.assertFalse(
                installed_root.is_symlink(),
                "the worktree resource root must be independently materialized",
            )
            for name in host_payloads():
                self.assertTrue(installed_root.joinpath(INSTALLED[1], name).is_file())
            self.assertEqual(
                "leave me alone\n",
                sentinel.read_text(encoding="utf-8"),
                "installing in a worktree followed the link and modified main",
            )

    def test_the_frozen_catalog_installs_into_a_resource_root_like_any_other_tree(
        self,
    ) -> None:
        """A debug App has to resolve the parts where a packaged one does.

        PC-16 installs the 134 parts into the release bundle and PC-18 made the
        App send that path to the authoring child. Between the two, a debug
        build had no catalog at all: `--install-into target/debug` stages the
        browser, both Workers and ffmpeg and stops, because the catalog is
        deliberately not a video runtime — those three are built here and cached
        per machine, while the catalog comes out of
        `build_motion_catalog_release.py` with its own locked digest.

        The consequence was not a missing directory anyone would notice. The
        resolver worked, the path existed as a name, and every beat that chose a
        part failed at the moment its working copy was written — on the machine
        every acceptance runs on. Installing is the same operation for both
        kinds of tree, so it is the same code; only who builds the tree differs.
        """
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            base = Path(directory)
            staging = base / "staging" / "motion-catalog"
            for name in MOTION_CATALOG.required_for("macos"):
                payload = staging / name
                payload.parent.mkdir(parents=True, exist_ok=True)
                payload.write_text("staged\n", encoding="utf-8")
            resources = base / "resources"

            completed = run_prepare(
                "--root",
                str(base / "staging"),
                "--only",
                MOTION_CATALOG.staging_name,
                "--install-into",
                str(resources),
            )

            self.assertEqual(
                0,
                completed.returncode,
                f"installing the staged catalog must succeed:\n"
                f"{completed.stdout}{completed.stderr}",
            )
            installed = resources.joinpath(*MOTION_CATALOG.installed_parts)
            for name in MOTION_CATALOG.required_for("macos"):
                self.assertTrue(
                    (installed / name).is_file(),
                    f"{name} must be installed at {installed}",
                )

    def test_an_unknown_resource_name_is_refused(self) -> None:
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            completed = run_prepare("--only", "no-such-resource", "--install-into", directory)
            self.assertNotEqual(0, completed.returncode, "a typo must not silently install nothing")
            self.assertIn("no-such-resource", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
