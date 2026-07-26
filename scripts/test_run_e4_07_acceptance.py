from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_e4_07_acceptance  # noqa: E402
import run_e4_09_acceptance  # noqa: E402
import run_e4_10_acceptance  # noqa: E402


class BuildSignedExecutorTests(unittest.TestCase):
    def test_pyinstaller_failure_preserves_builder_output(self) -> None:
        failed_build = subprocess.CompletedProcess(
            args=["python", "-m", "PyInstaller"],
            returncode=1,
            stdout="PyInstaller stdout: missing hidden import\n",
            stderr="PyInstaller stderr: build traceback\n",
        )

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            with patch.object(
                run_e4_07_acceptance.subprocess,
                "run",
                return_value=failed_build,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    run_e4_07_acceptance.build_signed_executor(
                        workspace,
                        build_id="diagnostic-test",
                    )

        message = str(raised.exception)
        self.assertIn("PyInstaller stdout: missing hidden import", message)
        self.assertIn("PyInstaller stderr: build traceback", message)

    def test_pyinstaller_failure_keeps_only_twenty_tail_lines_per_stream(self) -> None:
        failed_build = subprocess.CompletedProcess(
            args=["python", "-m", "PyInstaller"],
            returncode=1,
            stdout="\n".join(f"stdout-line-{index:02d}" for index in range(25)),
            stderr="\n".join(f"stderr-line-{index:02d}" for index in range(25)),
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                run_e4_07_acceptance.subprocess,
                "run",
                return_value=failed_build,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    run_e4_07_acceptance.build_signed_executor(
                        Path(directory) / "workspace",
                        build_id="bounded-diagnostic-test",
                    )

        message = str(raised.exception)
        for stream in ("stdout", "stderr"):
            self.assertNotIn(f"{stream}-line-00", message)
            self.assertNotIn(f"{stream}-line-04", message)
            self.assertIn(f"{stream}-line-05", message)
            self.assertIn(f"{stream}-line-24", message)

    def _assert_probe_builder_preserves_output(self, module: object) -> None:
        failed_build = subprocess.CompletedProcess(
            args=["python", "-m", "PyInstaller"],
            returncode=1,
            stdout="probe builder stdout\n",
            stderr="probe builder stderr\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module.subprocess, "run", return_value=failed_build):  # type: ignore[attr-defined]
                with self.assertRaises(RuntimeError) as raised:
                    module.build_signed_probe(Path(directory))  # type: ignore[attr-defined]
        self.assertIn("probe builder stdout", str(raised.exception))
        self.assertIn("probe builder stderr", str(raised.exception))

    def test_process_tree_probe_pyinstaller_failure_preserves_output(self) -> None:
        self._assert_probe_builder_preserves_output(run_e4_09_acceptance)

    def test_diagnostic_probe_pyinstaller_failure_preserves_output(self) -> None:
        self._assert_probe_builder_preserves_output(run_e4_10_acceptance)


if __name__ == "__main__":
    unittest.main()
