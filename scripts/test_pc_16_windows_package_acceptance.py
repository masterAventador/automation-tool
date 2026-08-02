#!/usr/bin/env python3
"""Static and portable contract tests for the PC-16 Windows package runner."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_pc_16_windows_package_acceptance as acceptance
import run_eb_16_windows_acceptance as production_windows
from build_motion_catalog_release import aggregate_digest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_pc_16_windows_package_acceptance.py"
PACKAGE = ROOT / "frontend/package.json"
TAURI_CONFIG = (
    ROOT
    / "frontend/src-tauri/tauri.pc16-windows-package-e2e.conf.json"
)
WDIO = ROOT / "frontend/wdio.pc16-windows-package.conf.ts"
PRODUCTION_WINDOWS = ROOT / "scripts/run_eb_16_windows_acceptance.py"


def record(relative: str, data: bytes) -> dict[str, object]:
    return {
        "path": relative,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class WindowsRunnerWiringTests(unittest.TestCase):
    def test_production_browser_probe_routes_chromium_logs_to_its_profile(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-eb16-browser-profile-"
        ) as raw:
            profile = Path(raw) / "profile"
            environment = production_windows.browser_probe_environment(
                profile,
                base_environment={"KEEP": "yes", "CHROME_LOG_FILE": "outside"},
            )

            self.assertEqual(environment["KEEP"], "yes")
            self.assertEqual(
                environment["CHROME_LOG_FILE"],
                os.fspath(profile / "chrome-debug.log"),
            )

    def test_windows_owned_tree_cleanup_uses_the_verbatim_root(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-pc16-cleanup-parent-"
        ) as raw:
            owned = Path(raw) / "owned"
            nested = owned / "deep" / "leaf"
            nested.mkdir(parents=True)
            (nested / "artifact.txt").write_text("owned", encoding="utf-8")
            removed_targets: list[str] = []

            def remove(target: str | os.PathLike[str]) -> None:
                removed_targets.append(os.fspath(target))
                shutil.rmtree(owned)

            acceptance.remove_owned_tree(owned, platform="nt", remove=remove)

            self.assertEqual(len(removed_targets), 1)
            self.assertTrue(removed_targets[0].startswith("\\\\?\\"))
            self.assertFalse(owned.exists())

    def test_windows_owned_tree_cleanup_refuses_a_link_before_removal(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-pc16-cleanup-link-"
        ) as raw:
            parent = Path(raw)
            target = parent / "not-owned"
            target.mkdir()
            artifact = target / "keep.txt"
            artifact.write_text("keep", encoding="utf-8")
            owned = parent / "owned"
            owned.symlink_to(target, target_is_directory=True)
            removed_targets: list[str] = []

            with self.assertRaisesRegex(acceptance.AcceptanceFailed, "reparse"):
                acceptance.remove_owned_tree(
                    owned,
                    platform="nt",
                    remove=lambda value: removed_targets.append(os.fspath(value)),
                )

            self.assertEqual(removed_targets, [])
            self.assertTrue(owned.is_symlink())
            self.assertEqual(artifact.read_text(encoding="utf-8"), "keep")

    def test_windows_verbatim_path_keeps_the_lexical_owned_root(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-pc16-verbatim-link-"
        ) as raw:
            parent = Path(raw)
            target = parent / "target"
            target.mkdir()
            owned = parent / "owned"
            owned.symlink_to(target, target_is_directory=True)

            rendered = acceptance.windows_verbatim_path(owned)

            self.assertIn("owned", rendered)
            self.assertNotIn("target", rendered)

    def test_non_windows_preflight_uses_pc16_vocabulary_without_a_traceback(self) -> None:
        with patch.object(
            acceptance,
            "require_windows",
            side_effect=RuntimeError("P9-04 acceptance requires Windows"),
        ):
            with self.assertRaisesRegex(
                acceptance.AcceptanceFailed,
                "PC-16 Windows package acceptance requires native Windows x86_64",
            ):
                acceptance.main()

    def test_one_command_reaches_the_native_windows_runner(self) -> None:
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        self.assertEqual(
            package["scripts"]["test:pc16-windows-package"],
            (
                "uv run --project ../backend --locked python "
                "../scripts/run_pc_16_windows_package_acceptance.py"
            ),
        )

    def test_test_shell_is_isolated_and_the_wdio_binary_is_runner_owned(self) -> None:
        configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
        production = json.loads(
            (ROOT / "frontend/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(configuration["identifier"], production["identifier"])
        self.assertEqual(
            configuration["identifier"],
            "com.aventador.automationtool.pc16windowspackage",
        )
        self.assertFalse(configuration["app"]["windows"][0]["visible"])
        self.assertEqual(configuration["bundle"]["targets"], ["nsis"])
        self.assertEqual(
            configuration["bundle"]["windows"]["nsis"]["installMode"],
            "currentUser",
        )
        wdio = WDIO.read_text(encoding="utf-8")
        self.assertIn("PC16_WINDOWS_APP_BINARY", wdio)
        self.assertIn("isAbsolute", wdio)
        self.assertNotIn('resolve("src-tauri/target', wdio)

    def test_runner_reuses_release_assembly_and_drives_the_installed_binary(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        for call in (
            "stage_browser_distribution(",
            "build_executor_candidate(",
            "prepare_video_runtime(",
            "stage_motion_catalog(",
            "install_video_runtime(",
            "install_motion_catalog(",
            "install_and_seal(",
            "require_packaged_browser(",
            "require_packaged_video_runtime(",
            "require_packaged_motion_catalog(",
            "write_windows_release_configuration(",
            "install_package(",
            "run_desktop_acceptance(",
            "inspect_film(",
            "uninstall_and_check(",
        ):
            self.assertIn(call, source)
        self.assertIn('"--bundles",\n            "nsis"', source)
        self.assertIn('"--features",\n            "control-plane-e2e"', source)
        self.assertNotIn(
            '"--debug"',
            source,
            "the installed user journey must exercise a release-mode App whose "
            "Executor verifier accepts the release signing key",
        )
        self.assertIn('cargo_target / "release"', source)
        self.assertIn("_answers_the_authoring_protocol", source)
        self.assertIn("134", source)
        self.assertIn("aggregateSha256", source)
        self.assertIn("cjkFontSha256", source)
        self.assertIn("missing-tree", source)
        self.assertIn("missing-manifest", source)
        self.assertIn("missing-sentinel", source)

    def test_installed_app_is_probed_with_the_windows_resource_path_shape(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("verify_installed_startup_gate_inputs(", source)
        self.assertIn('"--test",\n                "installed_release_startup"', source)
        self.assertIn(
            '"AUTOMATION_TOOL_WINDOWS_PACKAGE_PAYLOAD": os.fspath(root)',
            source,
        )
        self.assertIn(
            '"--test",\n                "motion_authoring_runtime"',
            source,
        )
        self.assertIn(
            '"the_windows_packaged_motion_worker_starts_from_the_verbatim_resource_path"',
            source,
        )
        self.assertIn(
            '"--test",\n                "video_media_toolchain"',
            source,
        )
        self.assertIn(
            '"the_windows_packaged_ffmpeg_runs_from_the_verbatim_resource_path"',
            source,
        )
        self.assertIn("os.path.abspath(os.fspath(path))", source)

    def test_production_windows_release_installs_and_gates_the_catalog(self) -> None:
        source = PRODUCTION_WINDOWS.read_text(encoding="utf-8")
        self.assertIn("require_non_elevated_process()", source)
        for call in (
            "stage_motion_catalog(",
            "install_motion_catalog(",
            "require_packaged_motion_catalog(",
        ):
            self.assertIn(call, source)


class InstalledCatalogAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="automation-tool-pc16-windows-contract-"
        )
        self.root = Path(self.temporary.name)
        self.catalog = self.root / "motion-catalog"
        records: list[dict[str, object]] = []
        for index in range(134):
            name = "lt-bold-block" if index == 0 else f"fixture-{index:03d}"
            relative = f"items/{name}/{name}.html"
            data = f"<p>{name}</p>".encode()
            path = self.catalog.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            records.append(record(relative, data))

        self.font_relative = (
            "offline-deps/fonts/woff2/noto-sans-sc/"
            "noto-sans-sc-variable-full.woff2"
        )
        font = b"locked-cjk-font"
        font_path = self.catalog.joinpath(*self.font_relative.split("/"))
        font_path.parent.mkdir(parents=True)
        font_path.write_bytes(font)
        records.append(record(self.font_relative, font))
        self.records = records
        (self.catalog / "manifest.json").write_text(
            json.dumps({"files": records}), encoding="utf-8"
        )
        self.release_lock = self.root / "release-lock.json"
        self.release_lock.write_text(
            json.dumps(
                {
                    "generated": {
                        "fileCount": len(records),
                        "aggregateSha256": aggregate_digest(records),
                    }
                }
            ),
            encoding="utf-8",
        )
        self.typography = self.root / "typography.json"
        self.typography.write_text(
            json.dumps({"chineseFace": {"artifactPath": self.font_relative}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def audit(self) -> dict[str, object]:
        with (
            patch.object(acceptance, "RELEASE_LOCK", self.release_lock),
            patch.object(acceptance, "TYPOGRAPHY_CONTRACT", self.typography),
        ):
            return acceptance.audit_installed_motion_catalog(self.root)

    def test_installed_copy_is_checked_file_by_file_and_counts_134_parts(self) -> None:
        facts = self.audit()
        self.assertEqual(facts["partDocuments"], 134)
        self.assertEqual(facts["files"], 135)
        self.assertEqual(facts["cjkFontBytes"], len(b"locked-cjk-font"))

    def test_a_digest_change_is_rejected_even_when_the_sentinel_still_exists(self) -> None:
        target = self.catalog / "items/fixture-133/fixture-133.html"
        target.write_bytes(b"tampered")
        with self.assertRaises(acceptance.AcceptanceFailed):
            self.audit()

    def test_required_tree_failure_matrix_has_three_rejections_and_one_control(self) -> None:
        self.assertEqual(
            acceptance.verify_catalog_failure_matrix(self.catalog),
            {
                "missing-tree": "rejected",
                "missing-manifest": "rejected",
                "missing-sentinel": "rejected",
                "intact": "accepted",
            },
        )


if __name__ == "__main__":
    unittest.main()
