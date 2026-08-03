from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "scripts"))

import run_le_15_acceptance
from run_le_14_acceptance import Le14AcceptanceFailure


class Le15AcceptanceRunnerTests(unittest.TestCase):
    @staticmethod
    def _credential(root: Path) -> tuple[Path, str]:
        secret = root / "credential.json"
        api_key = f"sk-{'a' * 20}"
        secret.write_text(
            json.dumps({"provider": "bailian", "apiKey": api_key}),
            encoding="utf-8",
        )
        secret.chmod(0o600)
        return secret, api_key

    def test_runner_passes_only_private_paths_and_rejects_pytest_injection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret, api_key = self._credential(root)
            child = MagicMock()
            child.pid = 1515
            child.returncode = 0
            child.communicate.return_value = (
                ("LE-15 real script request id: request-1\n1 passed in 0.01s\n"),
                "",
            )
            with (
                patch.dict(
                    os.environ,
                    {
                        "PYTHONOPTIMIZE": "2",
                        "PYTEST_ADDOPTS": "--collect-only",
                        "PYTEST_CURRENT_TEST": "private",
                    },
                    clear=False,
                ),
                patch.object(
                    run_le_15_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(subprocess, "Popen", return_value=child) as popen,
            ):
                run_le_15_acceptance.run_acceptance(secret)

        invocation = popen.call_args
        self.assertIsNotNone(invocation)
        command = invocation.args[0]
        environment = invocation.kwargs["env"]
        self.assertNotIn(api_key, command)
        self.assertNotIn(api_key, environment.values())
        self.assertNotIn("PYTHONOPTIMIZE", environment)
        self.assertNotIn("PYTEST_ADDOPTS", environment)
        self.assertNotIn("PYTEST_CURRENT_TEST", environment)
        self.assertEqual(
            environment[run_le_15_acceptance.SECRET_PATH_ENVIRONMENT],
            os.fspath(secret),
        )
        self.assertEqual(
            environment[run_le_15_acceptance.TOOLCHAIN_ROOT_ENVIRONMENT],
            os.fspath((root / "media-toolchain").resolve()),
        )
        self.assertEqual(invocation.kwargs["start_new_session"], os.name != "nt")
        self.assertEqual(
            invocation.kwargs["creationflags"],
            (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
        child.communicate.assert_called_once_with(
            timeout=run_le_15_acceptance.ACCEPTANCE_TIMEOUT_SECONDS
        )

    def test_pytest_workspace_is_inside_the_driver_owned_temporary_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret, _ = self._credential(root)
            child = MagicMock()
            child.pid = 1520
            child.returncode = 0
            child.communicate.return_value = ("1 passed in 0.01s\n", "")
            with (
                patch.object(
                    run_le_15_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(subprocess, "Popen", return_value=child) as popen,
            ):
                run_le_15_acceptance.run_acceptance(secret)

        command = popen.call_args.args[0]
        basetemp_index = command.index("--basetemp")
        pytest_root = Path(command[basetemp_index + 1])
        self.assertEqual(pytest_root.name, "pytest")
        self.assertTrue(
            pytest_root.parent.name.startswith("automation-tool-le15-acceptance-")
        )
        self.assertFalse(pytest_root.exists())

    def test_collect_only_exit_zero_is_not_a_real_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret, _ = self._credential(root)
            child = MagicMock()
            child.pid = 1516
            child.returncode = 0
            child.communicate.return_value = ("1 test collected in 0.01s\n", "")
            with (
                patch.object(
                    run_le_15_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(subprocess, "Popen", return_value=child),
                patch.object(
                    run_le_15_acceptance,
                    "_terminate_process_tree",
                ) as terminate,
                self.assertRaisesRegex(
                    run_le_15_acceptance.Le15AcceptanceFailure,
                    r"^LE-15 real acceptance failed$",
                ),
            ):
                run_le_15_acceptance.run_acceptance(secret)

        terminate.assert_called_once_with(child)

    def test_output_cannot_publish_the_private_toolchain_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret, _ = self._credential(root)
            toolchain = root / "private-toolchain"
            child = MagicMock()
            child.pid = 1518
            child.returncode = 0
            child.communicate.return_value = (
                f"toolchain={toolchain}\n1 passed in 0.01s\n",
                "",
            )
            with (
                patch.object(
                    run_le_15_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=toolchain,
                ),
                patch.object(subprocess, "Popen", return_value=child),
                patch.object(
                    run_le_15_acceptance,
                    "_terminate_process_tree",
                ) as terminate,
                self.assertRaisesRegex(
                    run_le_15_acceptance.Le15AcceptanceFailure,
                    r"^LE-15 real acceptance failed$",
                ),
            ):
                run_le_15_acceptance.run_acceptance(secret)

        terminate.assert_called_once_with(child)

    def test_timeout_cleans_the_child_tree_and_returns_a_fixed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret, api_key = self._credential(root)
            child = MagicMock()
            child.pid = 1517
            child.communicate.side_effect = subprocess.TimeoutExpired(
                [os.fspath(root / "private-python")],
                1,
                output=os.fspath(secret),
                stderr=api_key,
            )
            with (
                patch.object(
                    run_le_15_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(subprocess, "Popen", return_value=child),
                patch.object(
                    run_le_15_acceptance,
                    "_terminate_process_tree",
                ) as terminate,
                self.assertRaises(run_le_15_acceptance.Le15AcceptanceFailure) as raised,
            ):
                run_le_15_acceptance.run_acceptance(secret)

        self.assertEqual(str(raised.exception), "LE-15 real acceptance failed")
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn(api_key, str(raised.exception))
        self.assertNotIn(os.fspath(secret), str(raised.exception))
        terminate.assert_called_once_with(child)

    def test_nonzero_exit_cleans_the_child_tree_before_fixed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret, _ = self._credential(root)
            child = MagicMock()
            child.pid = 1519
            child.returncode = 1
            child.communicate.return_value = ("", "private pytest failure")
            with (
                patch.object(
                    run_le_15_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(subprocess, "Popen", return_value=child),
                patch.object(
                    run_le_15_acceptance,
                    "_terminate_process_tree",
                ) as terminate,
                self.assertRaisesRegex(
                    run_le_15_acceptance.Le15AcceptanceFailure,
                    r"^LE-15 real acceptance failed$",
                ),
            ):
                run_le_15_acceptance.run_acceptance(secret)

        terminate.assert_called_once_with(child)

    def test_le14_cleanup_failure_is_normalized_without_a_traceback_chain(self) -> None:
        child = MagicMock()
        with (
            patch.object(
                run_le_15_acceptance,
                "_terminate_owned_process_tree",
                side_effect=Le14AcceptanceFailure("LE-14 private cleanup path"),
            ),
            self.assertRaisesRegex(
                run_le_15_acceptance.Le15AcceptanceFailure,
                r"^LE-15 real acceptance failed$",
            ) as raised,
        ):
            run_le_15_acceptance._terminate_process_tree(child)

        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn("LE-14", str(raised.exception))
        self.assertNotIn("private cleanup path", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
