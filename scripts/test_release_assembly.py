#!/usr/bin/env python3
"""Deterministic tests for the production release assembler.

Synthetic bundles only (no network, no real browser, no Tauri build).

The gap these cover: until now the only non-test caller of
`install_distribution` was `scripts/run_eb_16_acceptance.py`, and
`tauri.conf.json` deliberately does not declare the browser under
`bundle.resources` — EB-16 measured that the bundler follows symlinks and
destroys the Chrome for Testing framework. The consequence was that a package
built by the ordinary candidate path (P9-03/P9-04) carried no browser at all,
and nothing refused to ship it; the failure only appeared on the user's
machine as a startup gate. So the assembly step has to live on a reusable
path, and reaching a distributable artifact has to require a verified browser.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_browser_distribution import (  # noqa: E402
    build_distribution_manifest,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)
from check_embedded_browser_package import browser_resource_root  # noqa: E402
from release_assembly import (  # noqa: E402
    RELEASE_RESOURCE_CONTRACT,
    VIDEO_RUNTIME_RESOURCES,
    ReleaseAssemblyRejected,
    install_and_seal,
    install_video_runtime,
    require_packaged_browser,
    require_packaged_video_runtime,
)
import run_eb_16_acceptance as eb16  # noqa: E402

STAGING_CONTRACT_PATH = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
TARGET_ID = "macos-arm64"
PLATFORM = "macos"
EXECUTABLE = (
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/"
    "Google Chrome for Testing"
)
INFO_PLIST = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist"
WIDEVINE_PREFIX = (
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Frameworks/"
    "Google Chrome for Testing Framework.framework/Versions/149.0.7827.55/"
    "Libraries/WidevineCdm/"
)


def _write_zip(path: Path, entries: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o755 if name == EXECUTABLE else 0o644) << 16
            archive.writestr(info, payload)
    return sha256_file(path)


def _synthetic_browser_entries() -> dict[str, bytes]:
    return {
        EXECUTABLE: b"synthetic browser binary",
        INFO_PLIST: b"<plist/>",
        f"{WIDEVINE_PREFIX}LICENSE": b"synthetic proprietary license",
        f"{WIDEVINE_PREFIX}manifest.json": b'{"name":"WidevineCdm"}',
        f"{WIDEVINE_PREFIX}_platform_specific/mac_arm64/libwidevinecdm.dylib": (
            b"synthetic proprietary binary"
        ),
    }


class ReleaseAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="release-assembly-test-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)

        self.application = self.base / "自动化运营工具.app"
        binary = self.application / "Contents/MacOS/自动化运营工具"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"synthetic release binary")
        binary.chmod(0o755)
        (self.application / "Contents/Info.plist").write_bytes(b"<plist/>")

        self.staging = self.base / "staging"
        archive = self.base / "archive.zip"
        digest = _write_zip(
            archive,
            _synthetic_browser_entries(),
        )
        build_staging(
            contract=load_staging_contract(STAGING_CONTRACT_PATH),
            target_id=TARGET_ID,
            archive_path=archive,
            archive_sha256=digest,
            output=self.staging,
        )
        build_distribution_manifest(
            staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
        )
        self.sealed: list[Path] = []

    def seal(self, application: Path) -> None:
        self.sealed.append(application)

    def assemble(self) -> None:
        install_and_seal(
            application=self.application,
            staging=self.staging,
            target_id=TARGET_ID,
            platform=PLATFORM,
            enforce_archive_lock=False,
            seal=self.seal,
        )

    def test_a_bundle_without_a_browser_is_not_distributable(self) -> None:
        # This is the shipped defect: the ordinary candidate build produces
        # exactly this bundle, and nothing used to refuse it.
        with self.assertRaises(ReleaseAssemblyRejected):
            require_packaged_browser(
                application=self.application, target_id=TARGET_ID, platform=PLATFORM
            )

    def test_assembly_installs_the_browser_and_then_seals_the_bundle(self) -> None:
        self.assemble()
        installed = browser_resource_root(self.application, PLATFORM)
        self.assertTrue((installed / EXECUTABLE).is_file())
        # Sealing must happen after the install, never before: a signature
        # taken before the browser lands does not cover it.
        self.assertEqual(self.sealed, [self.application])
        require_packaged_browser(
            application=self.application,
            target_id=TARGET_ID,
            platform=PLATFORM,
            enforce_archive_lock=False,
        )

    def test_a_tampered_browser_stops_assembly_before_sealing(self) -> None:
        (self.staging / INFO_PLIST).write_bytes(b"<plist>tampered</plist>")
        with self.assertRaises(ReleaseAssemblyRejected):
            self.assemble()
        # Nothing was signed, and no half-installed tree is left to be picked
        # up by a retry or, worse, by the packaging step.
        self.assertEqual(self.sealed, [])
        self.assertFalse(browser_resource_root(self.application, PLATFORM).exists())

    def test_assembly_refuses_to_install_over_an_existing_tree(self) -> None:
        self.assemble()
        second = self.base / "second-staging"
        shutil.copytree(self.staging, second, symlinks=True)
        with self.assertRaises(ReleaseAssemblyRejected):
            install_and_seal(
                application=self.application,
                staging=second,
                target_id=TARGET_ID,
                platform=PLATFORM,
                enforce_archive_lock=False,
                seal=self.seal,
            )


# The two paths that can produce a distributable artifact. The macOS one is a
# command in its own right; the Windows one still lives inside its acceptance
# script, which is recorded here rather than pretended away.
RELEASE_PATHS = ("build_release_package.py", "run_eb_16_windows_acceptance.py")


class AssemblerIsTheOnlyPathTests(unittest.TestCase):
    """The assembly and its gates must sit on the path that ships, not beside it."""

    def test_the_acceptance_script_delegates_to_the_release_command(self) -> None:
        source = (ROOT / "scripts/run_eb_16_acceptance.py").read_text(encoding="utf-8")
        # The acceptance script used to own the only copy of these steps, which
        # meant producing a shippable artifact required running an acceptance
        # suite and no workflow ran one. It now wraps the release command.
        self.assertIn("from build_release_package import", source)
        # If the acceptance script still called `install_distribution` itself,
        # the verified path and the shipped path could drift apart again.
        self.assertNotIn("install_distribution(", source)

    def test_every_release_path_installs_and_gates_all_declared_resources(self) -> None:
        # Writing the assembler without wiring it into the paths that produce a
        # shipped package is exactly how the browser gap survived its first fix,
        # and how `TauriPublishWorkspaceGateway` shipped unreachable. The gate
        # has to be on the path, not merely available.
        for script in RELEASE_PATHS:
            with self.subTest(script=script):
                source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
                for call in (
                    "install_video_runtime(",
                    "require_packaged_video_runtime(",
                    "install_motion_catalog(",
                    "require_packaged_motion_catalog(",
                    "install_and_seal(",
                    "require_packaged_browser(",
                ):
                    self.assertIn(call, source)

    def test_no_release_path_writes_bundle_resources_by_hand(self) -> None:
        # A hand-written `bundle.resources` is how a resource silently stops
        # being shipped: nothing relates what the configuration declares to what
        # the product resolves at runtime. Both paths go through the writer that
        # derives that list from the contract and refuses an incomplete payload.
        for script in RELEASE_PATHS:
            with self.subTest(script=script):
                source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
                self.assertNotIn('configuration["bundle"]["resources"]', source)

    def test_every_release_path_shows_the_audit_the_package(self) -> None:
        # `audit-production-package.mjs` can only assert what a package carries
        # when it is given the package. Handed a bare binary it audits the
        # binary alone, which is how an empty `bundle.resources` passed.
        for script in (*RELEASE_PATHS, "run_eb_16_acceptance.py"):
            with self.subTest(script=script):
                source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
                self.assertIn('"--package-root"', source)


class MacReleaseAcceptanceRunnerTests(unittest.TestCase):
    """The installer runner must accept the same formal package that ships."""

    def test_formal_developer_id_bundle_is_the_required_signature_boundary(
        self,
    ) -> None:
        application = Path("/private/tmp/Automation Tool.app")
        executable = (
            application
            / "Contents/Resources/embedded-browser/chrome-mac-arm64"
            / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        )
        identity = SimpleNamespace(
            certificate="Developer ID Application: Example (TEAMID1234)",
            team_id="TEAMID1234",
        )
        details = "\n".join(
            (
                "Identifier=com.aventador.automationtool",
                "Authority=Developer ID Application: Example (TEAMID1234)",
                "TeamIdentifier=TEAMID1234",
                "flags=0x10000(runtime) hashes=13+7 location=embedded",
            )
        )
        with (
            mock.patch.object(eb16, "run_checked") as run_checked,
            mock.patch.object(eb16, "signature_details", return_value=details),
            mock.patch.object(
                eb16, "packaged_browser_executable", return_value=executable
            ),
            mock.patch.object(eb16, "load_signing_identity", return_value=identity),
            mock.patch.object(
                eb16, "verify_embedded_mach_o_signature"
            ) as verify_embedded,
        ):
            eb16.verify_code_signatures(application, TARGET_ID)

        self.assertIn("--deep", run_checked.call_args.args[0])
        verify_embedded.assert_has_calls(
            (
                mock.call(executable, "com.google.chrome.for.testing"),
                mock.call(
                    executable.parent.parent
                    / "Frameworks/Google Chrome for Testing Framework.framework"
                    / "Versions/Current/Google Chrome for Testing Framework",
                    "com.google.chrome.for.testing.framework",
                ),
            )
        )

    def test_ad_hoc_outer_bundle_is_rejected_by_the_formal_runner(self) -> None:
        application = Path("/private/tmp/Automation Tool.app")
        details = "\n".join(
            (
                "Identifier=com.aventador.automationtool",
                "Signature=adhoc",
                "TeamIdentifier=not set",
                "flags=0x2(adhoc)",
            )
        )
        with (
            mock.patch.object(eb16, "run_checked"),
            mock.patch.object(eb16, "signature_details", return_value=details),
            mock.patch.object(
                eb16,
                "packaged_browser_executable",
                return_value=application / "Contents/MacOS/browser",
            ),
            mock.patch.object(eb16, "verify_embedded_mach_o_signature"),
            mock.patch.object(
                eb16,
                "load_signing_identity",
                return_value=SimpleNamespace(
                    certificate="Developer ID Application: Example (TEAMID1234)",
                    team_id="TEAMID1234",
                ),
            ),
            self.assertRaises(eb16.AcceptanceFailed),
        ):
            eb16.verify_code_signatures(application, TARGET_ID)

    def test_developer_id_browser_code_is_verified_in_its_bundle(self) -> None:
        executable = Path("/private/tmp/Automation Tool.app/Contents/MacOS/browser")
        details = "\n".join(
            (
                "Identifier=com.google.chrome.for.testing",
                "Authority=Developer ID Application: Example (TEAMID1234)",
                "TeamIdentifier=TEAMID1234",
                "flags=0x10000(runtime)",
            )
        )
        with (
            mock.patch.object(eb16, "signature_details", return_value=details),
            mock.patch.object(
                eb16,
                "load_signing_identity",
                return_value=SimpleNamespace(
                    certificate="Developer ID Application: Example (TEAMID1234)",
                    team_id="TEAMID1234",
                ),
            ),
            mock.patch.object(eb16, "run_checked") as run_checked,
        ):
            eb16.verify_embedded_mach_o_signature(
                executable, "com.google.chrome.for.testing"
            )

        run_checked.assert_called_once_with(
            ["codesign", "--verify", "--strict", str(executable)]
        )

    def test_skip_build_reuses_the_ordinary_production_release_configuration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="eb16-release-config-") as temporary:
            build = Path(temporary)
            generated = build / "tauri.release.generated.json"
            effective = build / "tauri.release.effective.json"
            generated.write_text("{}\n", encoding="utf-8")
            effective.write_text('{"bundle":{"active":true}}\n', encoding="utf-8")

            resolved = eb16.resolve_skip_build_configuration(build)

        self.assertEqual(resolved, effective)

    def test_relative_work_directory_is_anchored_to_the_repository(self) -> None:
        self.assertEqual(
            eb16.resolve_work_directory(Path(".local/release")),
            ROOT / ".local/release",
        )


class SingleResourceDeclarationTests(unittest.TestCase):
    """The six release resources are declared once, not once per gate.

    Three video runtime resources shipped absent on 2026-07-26 while every gate
    stayed green. Each gate that could have caught it knew a different, hand
    copied subset of "what a package must carry", so adding a resource in one
    place left the others silently satisfied. The inventory therefore lives in
    one contract and every gate derives from it.
    """

    def setUp(self) -> None:
        self.contract = json.loads(
            RELEASE_RESOURCE_CONTRACT.read_text(encoding="utf-8")
        )

    def test_the_video_runtime_resources_are_derived_from_the_contract(self) -> None:
        declared = [
            resource
            for resource in self.contract["resources"]
            if resource["category"] == "video"
        ]
        self.assertEqual(
            [
                (
                    resource.staging_name,
                    resource.installed_parts,
                    resource.required_files,
                    resource.windows_executables,
                )
                for resource in VIDEO_RUNTIME_RESOURCES
            ],
            [
                (
                    resource["name"],
                    tuple(resource["installedParts"]),
                    tuple(resource["requiredFiles"]),
                    tuple(resource["windowsExecutables"]),
                )
                for resource in declared
            ],
        )

    def test_the_assembler_does_not_restate_the_inventory(self) -> None:
        source = (ROOT / "scripts/release_assembly.py").read_text(encoding="utf-8")
        for literal in (
            "media-toolchain",
            "motion-video-worker",
            "material-video-worker",
            "motion-catalog",
            "bin/ffmpeg",
            "runtime/node",
            "automation-tool-material-video-worker",
        ):
            with self.subTest(literal=literal):
                self.assertNotIn(f'"{literal}"', source)

    def test_every_declared_resource_names_where_the_resolver_reads_it(self) -> None:
        names = [resource["name"] for resource in self.contract["resources"]]
        self.assertEqual(
            names,
            [
                "embedded-browser",
                "local-executor",
                "media-toolchain",
                "motion-video-worker",
                "material-video-worker",
                "motion-catalog",
            ],
        )
        for resource in self.contract["resources"]:
            with self.subTest(resource=resource["name"]):
                self.assertTrue(resource["installedParts"])
                self.assertIn("macos", resource["bundlerDeclared"])
                self.assertIn("windows", resource["bundlerDeclared"])


class ReleaseConfigurationTests(unittest.TestCase):
    """The Tauri configuration a release builds with must name every resource
    the bundler is responsible for, and only those.

    Written by hand, this list drifted: the macOS release configuration named
    the Executor and nothing else, which is correct, while nothing anywhere
    asserted that the four resources it leaves out are installed by the
    assembler instead. The writer now derives both halves from the contract.
    """

    def setUp(self) -> None:
        (ROOT / ".local").mkdir(exist_ok=True)
        self.base = Path(
            tempfile.mkdtemp(prefix="release-configuration-", dir=ROOT / ".local")
        )
        self.addCleanup(shutil.rmtree, self.base, True)
        self.executor = self.base / "build/executor/automation-tool-executor"
        self.executor.mkdir(parents=True)
        self.payload = self.base / "build/payload"
        self.payload.mkdir(parents=True)

    def test_the_macos_configuration_declares_only_the_executor(self) -> None:
        from release_configuration import write_macos_release_configuration

        written = write_macos_release_configuration(
            directory=self.base, executor=self.executor, name="tauri.test.json"
        )
        resources = json.loads(written.read_text(encoding="utf-8"))["bundle"][
            "resources"
        ]
        self.assertEqual(sorted(resources.values()), ["local-executor/package/"])
        self.assertEqual(
            json.loads(written.read_text(encoding="utf-8"))["bundle"]["macOS"],
            {"signingIdentity": "-"},
        )

    def test_the_windows_configuration_declares_every_bundled_resource(self) -> None:
        from release_configuration import write_windows_release_configuration

        written = write_windows_release_configuration(
            directory=self.base,
            executor=self.executor,
            payload=self.payload,
            name="tauri.test-windows.json",
        )
        resources = json.loads(written.read_text(encoding="utf-8"))["bundle"][
            "resources"
        ]
        self.assertEqual(
            sorted(resources.values()),
            [
                "embedded-browser/",
                "local-executor/package/",
                "material-video-worker/package/",
                "media-toolchain/",
                "motion-catalog/",
                "motion-video-worker/package/",
            ],
        )
        configuration = json.loads(
            (ROOT / "frontend/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            configuration["bundle"]["windows"]["nsis"]["installerHooks"],
            "windows-installer-hooks.nsh",
            "the production uninstaller must remove packaged browser directories "
            "that NSIS leaves empty after deleting their files",
        )
        hook = ROOT / "frontend/src-tauri/windows-installer-hooks.nsh"
        source = hook.read_text(encoding="utf-8")
        self.assertIn("!macro NSIS_HOOK_POSTUNINSTALL", source)
        self.assertNotIn(
            "RMDir /r",
            source,
            "the uninstaller must not follow a replaced browser directory recursively",
        )
        first_delete = source.index(
            'RMDir "$INSTDIR\\embedded-browser\\chrome-win64\\Dictionaries"'
        )
        self.assertIn("FILE_ATTRIBUTE_REPARSE_POINT", source)
        self.assertIn("GetFileAttributes", source)
        self.assertIn(
            "System::Call",
            source,
            "the hook must call the Windows attribute API through valid NSIS syntax",
        )
        for guarded in (
            "$INSTDIR",
            "$INSTDIR\\embedded-browser",
            "$INSTDIR\\embedded-browser\\chrome-win64",
            "$INSTDIR\\embedded-browser\\chrome-win64\\Dictionaries",
        ):
            guard = f'!insertmacro EBVS_ABORT_IF_REPARSE "{guarded}"'
            self.assertIn(guard, source)
            self.assertLess(
                source.index(guard),
                first_delete,
                "every ancestor must be checked before the first nested removal",
            )
        self.assertIn(
            'RMDir "$INSTDIR\\embedded-browser\\chrome-win64\\Dictionaries"',
            source,
        )
        self.assertIn('RMDir "$INSTDIR\\embedded-browser\\chrome-win64"', source)
        self.assertIn('RMDir "$INSTDIR\\embedded-browser"', source)
        self.assertIn('RMDir "$INSTDIR"', source)

    def test_the_windows_configuration_ships_the_release_identity(self) -> None:
        """A written identity that the bundler never carries proves nothing.

        `build_release_package.py --platform windows` writes
        `release-identity.v1.json` into the payload, and the EB-11 runner reads
        it back out of the *installed* package to prove which source tree the
        App came from. Between those two, nothing declared it: `bundle.resources`
        is derived from the resource contract, the identity is not a resource,
        so a real release produced the file, announced it, and shipped a package
        without it. Measured on the installed package built 2026-08-05 — the
        install root held six resource directories and no identity.
        """
        from release_configuration import write_windows_release_configuration
        from release_identity import PACKAGED_IDENTITY_NAME

        identity = self.payload / PACKAGED_IDENTITY_NAME
        identity.write_text("{}", encoding="utf-8")

        written = write_windows_release_configuration(
            directory=self.base,
            executor=self.executor,
            payload=self.payload,
            name="tauri.test-windows-identity.json",
            release_identity=identity,
        )

        resources = json.loads(written.read_text(encoding="utf-8"))["bundle"]["resources"]
        # No trailing slash: tauri-utils treats `"file": "name/"` as "write the
        # file *as* `name`" (`resources.rs` says so in its own TODO), while a
        # bare name lands it at the resource root, which on Windows is the
        # install root the runner reads.
        declared = [
            source
            for source, destination in resources.items()
            if destination == PACKAGED_IDENTITY_NAME
        ]
        self.assertEqual(len(declared), 1, resources)
        # Tauri resolves resource sources against its own root, so the recorded
        # relative path has to arrive back at the file that was written.
        self.assertEqual(
            Path(
                os.path.normpath(ROOT / "frontend/src-tauri" / declared[0])
            ),
            identity,
        )

    def test_a_release_identity_under_another_name_is_refused(self) -> None:
        from release_configuration import (
            ReleaseConfigurationRejected,
            write_windows_release_configuration,
        )

        stray = self.payload / "identity.json"
        stray.write_text("{}", encoding="utf-8")

        with self.assertRaises(ReleaseConfigurationRejected):
            write_windows_release_configuration(
                directory=self.base,
                executor=self.executor,
                payload=self.payload,
                name="tauri.test-windows-stray.json",
                release_identity=stray,
            )

    def test_a_missing_bundler_source_is_refused_rather_than_dropped(self) -> None:
        from release_configuration import (
            ReleaseConfigurationRejected,
            write_release_configuration,
        )

        with self.assertRaises(ReleaseConfigurationRejected):
            write_release_configuration(
                directory=self.base,
                platform="windows",
                sources={"local-executor": self.executor},
                name="tauri.incomplete.json",
            )


def _write_video_runtime(root: Path) -> None:
    """Lay down a minimally shaped, non-empty video runtime staging tree."""
    toolchain = root / "media-toolchain"
    (toolchain / "bin").mkdir(parents=True)
    (toolchain / "bin/ffmpeg").write_bytes(b"ffmpeg")
    (toolchain / "bin/ffprobe").write_bytes(b"ffprobe")
    (toolchain / "manifest.json").write_text("{}", encoding="utf-8")
    motion = root / "motion-video-worker"
    (motion / "runtime").mkdir(parents=True)
    (motion / "app").mkdir()
    (motion / "runtime/node").write_bytes(b"node")
    (motion / "app/worker.mjs").write_text("export {};", encoding="utf-8")
    material = root / "material-video-worker"
    material.mkdir()
    (material / "automation-tool-material-video-worker").write_bytes(b"worker")


class VideoRuntimeReleaseGateTests(unittest.TestCase):
    """The three video runtime resources need the same release gate as the browser.

    Every BM/IM acceptance script builds ffmpeg and the two Workers itself and
    hands their paths to a `video-studio-e2e` test build through environment
    variables. The production build reads `Contents/Resources/` instead, and
    nothing ever installed them there — so the shipped package showed
    "本机视频制作服务暂时无法启动" and "本机渲染组件暂时不可用" on a user's
    machine while every acceptance run stayed green.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="release-video-runtime-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.application = self.base / "Example.app"
        (self.application / "Contents/Resources").mkdir(parents=True)
        self.staging = self.base / "video-runtime"
        _write_video_runtime(self.staging)

    def test_a_bundle_without_the_video_runtime_is_rejected(self) -> None:
        with self.assertRaises(ReleaseAssemblyRejected):
            require_packaged_video_runtime(
                application=self.application, platform=PLATFORM
            )

    def test_installing_the_video_runtime_satisfies_the_gate(self) -> None:
        install_video_runtime(
            application=self.application, staging=self.staging, platform=PLATFORM
        )
        installed = require_packaged_video_runtime(
            application=self.application, platform=PLATFORM
        )
        self.assertEqual(
            sorted(installed),
            ["material-video-worker", "media-toolchain", "motion-video-worker"],
        )

    def test_each_missing_resource_is_named(self) -> None:
        for resource in (
            "media-toolchain",
            "motion-video-worker",
            "material-video-worker",
        ):
            with self.subTest(resource=resource):
                application = self.base / f"Missing-{resource}.app"
                (application / "Contents/Resources").mkdir(parents=True)
                partial = self.base / f"staging-{resource}"
                _write_video_runtime(partial)
                shutil.rmtree(partial / resource)
                with self.assertRaises(ReleaseAssemblyRejected) as caught:
                    install_video_runtime(
                        application=application, staging=partial, platform=PLATFORM
                    )
                self.assertIn(resource, str(caught.exception))

    def test_an_empty_worker_payload_is_rejected(self) -> None:
        # A directory that merely exists is exactly what the production
        # resolver trips over: it finds the path and then fails to launch.
        (self.staging / "motion-video-worker/runtime/node").unlink()
        with self.assertRaises(ReleaseAssemblyRejected):
            install_video_runtime(
                application=self.application, staging=self.staging, platform=PLATFORM
            )

    def test_a_rejected_install_leaves_nothing_behind(self) -> None:
        (
            self.staging / "material-video-worker/automation-tool-material-video-worker"
        ).unlink()
        with self.assertRaises(ReleaseAssemblyRejected):
            install_video_runtime(
                application=self.application, staging=self.staging, platform=PLATFORM
            )
        resources = self.application / "Contents/Resources"
        self.assertEqual(sorted(path.name for path in resources.iterdir()), [])


# A 64-bit little-endian Mach-O header is all `signable_nodes` needs to
# recognise code; the synthetic trees below carry nothing else.
MACH_O_HEADER = b"\xcf\xfa\xed\xfe" + b"\x0c\x00\x00\x01" + b"\x00" * 24


def _write_mach_o(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MACH_O_HEADER)
    path.chmod(0o755)
    return path


class RecordingRunner:
    """Stand in for the real `codesign`/`xattr`/`spctl`/`notarytool` calls."""

    def __init__(self, replies: dict[str, str] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.replies = replies or {}

    def __call__(self, command: list[str]) -> str:
        self.commands.append(list(command))
        for marker, reply in self.replies.items():
            if marker in command[0]:
                return reply
        return ""

    def tools(self) -> list[str]:
        return [Path(command[0]).name for command in self.commands]


class MacOSSigningContractTests(unittest.TestCase):
    """The signing identity and every entitlement are declared once, with reasons.

    An entitlement handed out to make a notarisation stop failing, with no
    record of which component needed it or why, is indistinguishable later from
    one nobody ever needed. The contract therefore carries the justification
    next to the grant, and this test refuses a grant without one.
    """

    def setUp(self) -> None:
        from release_assembly import MACOS_SIGNING_CONTRACT

        self.path = MACOS_SIGNING_CONTRACT
        self.contract = json.loads(self.path.read_text(encoding="utf-8"))

    def test_the_contract_declares_a_developer_id_and_a_notary_profile(self) -> None:
        from release_assembly import load_signing_identity

        identity = load_signing_identity()
        self.assertTrue(identity.certificate.startswith("Developer ID Application:"))
        self.assertTrue(identity.team_id)
        self.assertTrue(identity.notary_profile)
        # The keychain profile is a local alias for credentials held outside
        # this repository. The credentials themselves must never appear here.
        document = self.path.read_text(encoding="utf-8")
        for secret in ("password", "-----BEGIN", "app-specific", "AuthKey"):
            self.assertNotIn(secret, document)

    def test_notarization_retries_one_timed_out_upload_without_s3_acceleration(
        self,
    ) -> None:
        from release_assembly import SigningIdentity, notarize_and_staple

        commands: list[list[str]] = []

        def run(command: list[str]) -> str:
            commands.append(list(command))
            if "notarytool" not in command:
                return ""
            if "--no-s3-acceleration" not in command:
                raise ReleaseAssemblyRejected(
                    "release assembly rejected: xcrun failed: "
                    "Error: abortedUpload(error: HTTPClientError.deadlineExceeded)"
                )
            return '{"id":"retry-id","status":"Accepted"}'

        with tempfile.TemporaryDirectory(prefix="notary-retry-test-") as directory:
            artifact = Path(directory) / "自动化运营工具.app"
            artifact.mkdir()
            identifier = notarize_and_staple(
                artifact=artifact,
                identity=SigningIdentity(
                    certificate="Developer ID Application: Test (TEAMID)",
                    team_id="TEAMID",
                    notary_profile="test-profile",
                ),
                run=run,
            )

        submissions = [command for command in commands if "notarytool" in command]
        self.assertEqual(identifier, "retry-id")
        self.assertEqual(len(submissions), 2)
        self.assertNotIn("--no-s3-acceleration", submissions[0])
        self.assertIn("--no-s3-acceleration", submissions[1])
        self.assertEqual(
            sum("stapler" in command for command in commands),
            1,
        )

    def test_every_signed_component_is_a_declared_release_resource(self) -> None:
        from release_assembly import RELEASE_PACKAGE_RESOURCES

        known = {str(resource["name"]) for resource in RELEASE_PACKAGE_RESOURCES}
        known.add("application")
        self.assertEqual(set(self.contract["components"]) - known, set())

    def test_every_entitlement_names_its_component_and_its_reason(self) -> None:
        import plistlib

        from release_assembly import REPOSITORY_ROOT, entitlements_for

        granted = 0
        for name, component in self.contract["components"].items():
            with self.subTest(component=name):
                entitlements = component.get("entitlements")
                if entitlements is None:
                    self.assertIsNone(entitlements_for(name))
                    continue
                path = entitlements_for(name)
                self.assertIsNotNone(path)
                self.assertTrue(path.is_file(), f"{path} is declared but absent")
                keys = set(plistlib.loads(path.read_bytes()))
                reasons = entitlements["reasons"]
                # Every key the plist actually grants must carry a written
                # reason, and no reason may be recorded for a key that is not
                # granted — otherwise the justification drifts from the grant.
                self.assertEqual(keys, set(reasons))
                for key, reason in reasons.items():
                    self.assertGreater(
                        len(reason), 30, f"{name}/{key} has no real justification"
                    )
                granted += len(keys)
        # No assertion that `granted` is non-zero: how many entitlements this
        # package needs is a fact about Apple's runtime, measured by signing and
        # notarising it, not something a unit test may presume.
        # The plists live where a reviewer of the Tauri bundle would look.
        for name in self.contract["components"]:
            path = entitlements_for(name)
            if path is not None:
                self.assertTrue(
                    path.is_relative_to(REPOSITORY_ROOT / "frontend/src-tauri")
                )


class SigningOrderTests(unittest.TestCase):
    """Nested code is signed before the bundle that contains it, always.

    macOS seals a bundle over the bytes of everything inside it, so a signature
    taken before a nested helper is signed does not survive the helper being
    signed afterwards, and notarisation rejects the result. The order is a
    property of the assembler, not of the operator remembering it.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="release-signing-order-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.application = self.base / "Example.app"
        _write_mach_o(self.application / "Contents/MacOS/Example")
        browser = self.application / "Contents/Resources/embedded-browser/Chrome.app"
        _write_mach_o(browser / "Contents/MacOS/Chrome")
        framework = browser / "Contents/Frameworks/Chrome Framework.framework"
        version = framework / "Versions/149.0"
        _write_mach_o(version / "Chrome Framework")
        _write_mach_o(version / "Libraries/libEGL.dylib")
        _write_mach_o(
            version
            / "Helpers/Chrome Helper (GPU).app/Contents/MacOS/Chrome Helper (GPU)"
        )
        (framework / "Versions/Current").symlink_to("149.0")
        (framework / "Chrome Framework").symlink_to("Versions/Current/Chrome Framework")
        self.framework = framework
        self.browser = browser

    def test_every_nested_node_is_signed_before_the_node_that_contains_it(self) -> None:
        from release_assembly import signable_nodes

        nodes = signable_nodes(self.application)
        self.assertIn(self.application, nodes)
        position = {node: index for index, node in enumerate(nodes)}
        for node in nodes:
            for other in nodes:
                if node != other and node.is_relative_to(other):
                    self.assertLess(
                        position[node],
                        position[other],
                        f"{node} must be signed before {other}",
                    )
        # The bundle the user launches is sealed last, over everything else.
        self.assertEqual(nodes[-1], self.application)

    def test_the_framework_symlinks_are_never_signed_in_place(self) -> None:
        from release_assembly import signable_nodes

        # EB-16 measured what happens when the Chrome for Testing framework's
        # relative links are treated as ordinary files: the framework is
        # destroyed. Handing one to `codesign` replaces the link with a file.
        for node in signable_nodes(self.application):
            self.assertFalse(node.is_symlink(), f"{node} is a symlink")

    def test_an_already_inventoried_payload_is_never_signed_again(self) -> None:
        from release_assembly import signable_nodes

        # Three payloads in this package carry their own digest manifest, taken
        # over the signed bytes and re-verified on the customer's machine. The
        # outermost seal must not touch them: re-signing rewrites every Mach-O
        # it visits, which would leave each manifest describing a tree that no
        # longer exists and the product refusing its own resources at startup.
        nodes = signable_nodes(
            self.application,
            exclude=(self.application / "Contents/Resources/embedded-browser",),
        )
        self.assertIn(self.application, nodes)
        for node in nodes:
            self.assertFalse(
                node.is_relative_to(
                    self.application / "Contents/Resources/embedded-browser"
                ),
                f"{node} belongs to a payload that was already signed and inventoried",
            )
        # The application's own code is still signed.
        self.assertIn(self.application / "Contents/MacOS/Example", nodes)

    def test_the_outer_seal_excludes_every_declared_release_resource(self) -> None:
        from release_assembly import RELEASE_PACKAGE_RESOURCES, inventoried_payloads

        # Derived from the one resource declaration, not hand-listed here: a
        # resource added to the contract without being excluded would be
        # silently re-signed and would break its manifest.
        excluded = inventoried_payloads(self.application, platform="macos")
        self.assertEqual(len(excluded), len(RELEASE_PACKAGE_RESOURCES))
        for resource in RELEASE_PACKAGE_RESOURCES:
            expected = self.application.joinpath(
                "Contents/Resources", *resource["installedParts"]
            )
            self.assertIn(expected, excluded)

    def test_signing_asks_for_a_hardened_runtime_and_a_secure_timestamp(self) -> None:
        from release_assembly import SigningIdentity, sign_tree

        identity = SigningIdentity(
            certificate="Developer ID Application: Example (TEAMID1234)",
            team_id="TEAMID1234",
            notary_profile="example-notary",
        )
        runner = RecordingRunner()
        signed = sign_tree(
            root=self.application,
            component="application",
            identity=identity,
            run=runner,
        )
        self.assertEqual(len(signed), len(runner.commands))
        self.assertTrue(runner.commands)
        for command in runner.commands:
            self.assertEqual(Path(command[0]).name, "codesign")
            # Notarisation rejects a signature without a hardened runtime or
            # without a secure timestamp, whatever else is right about it.
            self.assertIn("--options", command)
            self.assertIn("runtime", command)
            self.assertIn("--timestamp", command)
            self.assertIn(identity.certificate, command)
            # An ad-hoc signature cannot be notarised. It must not survive
            # anywhere on the path that produces a distributable package.
            self.assertNotIn("-", command[command.index("--sign") + 1 :][:1])


class DistributionGateTests(unittest.TestCase):
    """A package is distributable only if a quarantined copy still opens.

    "The notary service accepted the submission" and "the customer can open the
    download" are different claims, and this project has already shipped once on
    the strength of the wrong one. The gate therefore reproduces what the
    customer's machine does: it marks the artifact as downloaded and asks
    Gatekeeper.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="release-gate-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.disk_image = self.base / "Example_0.1.0.dmg"
        self.disk_image.write_bytes(b"synthetic disk image")

    def gate(self, verdict: str) -> RecordingRunner:
        from release_assembly import require_distributable_artifact

        runner = RecordingRunner(replies={"spctl": verdict})
        require_distributable_artifact(artifact=self.disk_image, run=runner)
        return runner

    def test_a_quarantined_notarised_disk_image_is_distributable(self) -> None:
        runner = self.gate(
            "Example_0.1.0.dmg: accepted\n"
            "source=Notarized Developer ID\n"
            "origin=Developer ID Application: Example (TEAMID1234)\n"
        )
        self.assertEqual(runner.tools(), ["xattr", "spctl"])
        # The quarantine flag has to be applied before the assessment, or the
        # assessment is not the one the customer's machine performs.
        self.assertIn("com.apple.quarantine", runner.commands[0])
        self.assertIn(str(self.disk_image), runner.commands[0])
        self.assertIn("--context", runner.commands[1])
        self.assertIn("context:primary-signature", runner.commands[1])

    def test_an_unnotarised_disk_image_is_refused(self) -> None:
        with self.assertRaises(ReleaseAssemblyRejected):
            self.gate("Example_0.1.0.dmg: rejected\nsource=no usable signature\n")

    def test_a_signed_but_unnotarised_disk_image_is_refused(self) -> None:
        # This is the shape the mistake takes: a real Developer ID signature,
        # a submission that was accepted, and a ticket that never got stapled.
        with self.assertRaises(ReleaseAssemblyRejected):
            self.gate("Example_0.1.0.dmg: accepted\nsource=Unnotarized Developer ID\n")

    def test_an_ad_hoc_disk_image_is_refused(self) -> None:
        with self.assertRaises(ReleaseAssemblyRejected):
            self.gate("Example_0.1.0.dmg: accepted\nsource=Insufficient Context\n")


class NoSilentAdHocSealTests(unittest.TestCase):
    """Sealing a bundle ad-hoc must be something a caller asks for, not a default.

    `install_and_seal` used to default to `seal_with_adhoc_signature`, and by the
    time every release path passed its own Developer ID seal that default had no
    callers left — only the ability to hand a future one a bundle Gatekeeper
    offers the customer "Move to Trash" for, without anybody typing anything to
    that effect.
    """

    def test_install_and_seal_has_no_default_seal(self) -> None:
        import inspect

        import release_assembly

        parameter = inspect.signature(release_assembly.install_and_seal).parameters[
            "seal"
        ]
        self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_the_ad_hoc_sealer_is_gone_rather_than_merely_unused(self) -> None:
        import release_assembly

        self.assertFalse(hasattr(release_assembly, "seal_with_adhoc_signature"))
        source = (ROOT / "scripts/release_assembly.py").read_text(encoding="utf-8")
        self.assertNotIn('"--sign", "-"', source)


class StagingInventoryRefreshTests(unittest.TestCase):
    """The browser manifest has to describe the signed tree, not the staged one.

    `build_staging` inventories the tree as it comes out of the digest-locked
    archive and `build_distribution_manifest` carries that inventory forward.
    Signing rewrites every Mach-O it touches, so without re-taking the
    inventory the shipped package disagrees with its own manifest — and the
    disagreement surfaces on the customer's machine, where the Rust resolver
    refuses the browser it was given.
    """

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="staging-refresh-test-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.staging = self.base / "staging"
        archive = self.base / "archive.zip"
        digest = _write_zip(
            archive,
            _synthetic_browser_entries(),
        )
        build_staging(
            contract=load_staging_contract(STAGING_CONTRACT_PATH),
            target_id=TARGET_ID,
            archive_path=archive,
            archive_sha256=digest,
            output=self.staging,
        )

    def test_a_signed_tree_fails_verification_until_the_inventory_is_retaken(
        self,
    ) -> None:
        from build_embedded_browser_distribution import (
            DistributionRejected,
            build_distribution_manifest,
            verify_distribution,
        )
        from build_release_package import refresh_staging_inventory

        # Stand in for what `codesign` does: rewrite the executable's bytes.
        (self.staging / EXECUTABLE).write_bytes(b"synthetic browser binary SIGNED")
        build_distribution_manifest(
            staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
        )
        # Built from the stale staging inventory, the manifest describes bytes
        # that are no longer there.
        with self.assertRaises(DistributionRejected):
            verify_distribution(
                staging=self.staging,
                target_id=TARGET_ID,
                enforce_archive_lock=False,
            )
        refresh_staging_inventory(self.staging, TARGET_ID)
        build_distribution_manifest(
            staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
        )
        report = verify_distribution(
            staging=self.staging, target_id=TARGET_ID, enforce_archive_lock=False
        )
        self.assertGreater(report.verified_files, 0)

    def test_the_refresh_keeps_the_tree_tied_to_the_locked_archive(self) -> None:
        from build_release_package import refresh_staging_inventory

        before = json.loads(
            (self.staging / "staging-manifest.json").read_text(encoding="utf-8")
        )
        refresh_staging_inventory(self.staging, TARGET_ID)
        after = json.loads(
            (self.staging / "staging-manifest.json").read_text(encoding="utf-8")
        )
        # Re-taking the inventory must not quietly relax the one fact that ties
        # this tree to the upstream archive the contract pins.
        self.assertEqual(after["source"], before["source"])
        self.assertEqual(after["target"], before["target"])
        self.assertEqual(after["chromium"], before["chromium"])


class ReleasePathSignsAndNotarisesTests(unittest.TestCase):
    """The gate has to be on the path that ships, not merely available."""

    def setUp(self) -> None:
        self.source = (ROOT / "scripts/build_release_package.py").read_text(
            encoding="utf-8"
        )

    def test_the_release_path_signs_notarises_staples_and_gates(self) -> None:
        for call in (
            "sign_tree(",
            "notarize_and_staple(",
            "require_distributable_artifact(",
        ):
            with self.subTest(call=call):
                self.assertIn(call, self.source)

    def test_the_release_path_no_longer_ships_an_ad_hoc_signature(self) -> None:
        # `codesign --sign -` produced every package built so far. Gatekeeper
        # offers the customer "Move to Trash" for those, so no path that
        # produces a distributable artifact may still reach for it.
        self.assertNotIn('"--sign", "-"', self.source)
        self.assertNotIn("'--sign', '-'", self.source)

    def test_each_digest_manifest_is_written_after_its_payload_is_signed(self) -> None:
        # Signing rewrites the bytes of every Mach-O it touches. Three manifests
        # in this package record those bytes and are re-verified on the
        # customer's machine, so each has to be produced from the signed tree.
        for earlier, later in (
            ("sign_tree(", "build_distribution_manifest("),
            ("sign_tree(", "write_signed_executor_manifest("),
        ):
            with self.subTest(pair=(earlier, later)):
                self.assertLess(
                    self.source.index(earlier),
                    self.source.index(later),
                    f"{earlier} must run before {later}",
                )


if __name__ == "__main__":
    unittest.main()
