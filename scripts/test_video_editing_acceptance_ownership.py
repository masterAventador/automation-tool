#!/usr/bin/env python3
"""Failure-injection tests for the LE-17 spec ownership gate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from check_video_editing_acceptance_ownership import audit_repository


class VideoEditingAcceptanceOwnershipTests(unittest.TestCase):
    def make_repository(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory(prefix="le17-spec-owner-")
        root = Path(temporary.name)
        frontend = root / "frontend"
        (frontend / "e2e-tauri").mkdir(parents=True)
        (frontend / "src-tauri").mkdir()
        (root / "scripts").mkdir()
        (frontend / "e2e-tauri/video-editing.spec.ts").write_text(
            "describe('real editing', () => {});\n", encoding="utf-8"
        )
        (frontend / "wdio.video-editing.conf.ts").write_text(
            'export const config = { specs: ["./e2e-tauri/video-editing.spec.ts"] };\n',
            encoding="utf-8",
        )
        (frontend / "src-tauri/tauri.video-editing-e2e.conf.json").write_text(
            json.dumps({"identifier": "com.example.le17"}), encoding="utf-8"
        )
        (root / "scripts/run_le_17_acceptance.py").write_text(
            'WDIO = "wdio.video-editing.conf.ts"\n', encoding="utf-8"
        )
        (frontend / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build:tauri:video-editing-test": (
                            "tauri build --debug --no-bundle "
                            "--features control-plane-e2e "
                            "--config src-tauri/tauri.video-editing-e2e.conf.json"
                        ),
                        "test:le17-video-editing-app": (
                            "../backend/.venv/bin/python "
                            "../scripts/run_le_17_acceptance.py"
                        ),
                    }
                }
            ),
            encoding="utf-8",
        )
        return temporary, root

    def test_exact_owner_and_control_plane_feature_pass(self) -> None:
        temporary, root = self.make_repository()
        self.addCleanup(temporary.cleanup)

        self.assertEqual(audit_repository(root), [])

    def test_unowned_or_duplicate_spec_fails(self) -> None:
        temporary, root = self.make_repository()
        self.addCleanup(temporary.cleanup)
        owner = root / "frontend/wdio.video-editing.conf.ts"
        owner.write_text("export const config = { specs: [] };\n", encoding="utf-8")

        self.assertIn("video-editing.spec.ts has no WDIO owner", audit_repository(root))

        owner.write_text(
            'export const config = { specs: ["./e2e-tauri/video-editing.spec.ts"] };\n',
            encoding="utf-8",
        )
        (root / "frontend/wdio.duplicate.conf.ts").write_text(
            'export const config = { specs: ["./e2e-tauri/video-editing.spec.ts"] };\n',
            encoding="utf-8",
        )

        self.assertTrue(
            any(
                "video-editing.spec.ts has duplicate WDIO owners" in error
                for error in audit_repository(root)
            )
        )

    def test_missing_tauri_configuration_fails(self) -> None:
        temporary, root = self.make_repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src-tauri/tauri.video-editing-e2e.conf.json").unlink()

        self.assertIn(
            "video-editing Tauri configuration is missing",
            audit_repository(root),
        )

    def test_wrong_build_feature_fails(self) -> None:
        temporary, root = self.make_repository()
        self.addCleanup(temporary.cleanup)
        package_path = root / "frontend/package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package["scripts"]["build:tauri:video-editing-test"] = (
            "tauri build --debug --no-bundle --features desktop-e2e "
            "--config src-tauri/tauri.video-editing-e2e.conf.json"
        )
        package_path.write_text(json.dumps(package), encoding="utf-8")

        self.assertIn(
            "video-editing build must use only control-plane-e2e",
            audit_repository(root),
        )


if __name__ == "__main__":
    unittest.main()
