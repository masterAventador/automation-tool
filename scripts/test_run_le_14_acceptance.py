from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "scripts"))

import acceptance_postgres
import run_le_14_acceptance


class Le14AcceptanceFixtureTests(unittest.TestCase):
    @staticmethod
    def _contract(root: Path, payload: bytes) -> Path:
        path = root / "fixture-contract.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "id": run_le_14_acceptance.CONTRACT_ID,
                    "policy": {
                        "acceptanceOnly": True,
                        "shipped": False,
                        "applicationRuntimeDownloadAllowed": False,
                    },
                    "upstream": {
                        "datasetHomepage": run_le_14_acceptance.DATASET_HOMEPAGE,
                        "dataset": run_le_14_acceptance.DATASET_NAME,
                        "split": run_le_14_acceptance.DATASET_SPLIT,
                        "utteranceId": run_le_14_acceptance.FIXTURE_UTTERANCE_ID,
                    },
                    "fixture": {
                        "sourcePath": run_le_14_acceptance.FIXTURE_SOURCE_PATH,
                        "sourceUrl": run_le_14_acceptance.FIXTURE_SOURCE_URL,
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "format": {
                            "container": "flac",
                            "codec": "flac",
                            "channels": 1,
                            "sampleRateHz": 16000,
                            "durationMs": 5855,
                        },
                    },
                    "license": {
                        "spdx": "CC-BY-4.0",
                        "sourceUrl": run_le_14_acceptance.LICENSE_SOURCE_URL,
                        "attribution": (
                            "LibriSpeech: an ASR corpus based on public domain audio books; "
                            "Vassil Panayotov, Guoguo Chen, Daniel Povey and Sanjeev Khudanpur"
                        ),
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_fixture_is_fetched_once_and_digest_verified(self) -> None:
        payload = b"fLaC" + b"\0" * 128
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = self._contract(root, payload)
            target = root / "voice.flac"
            calls: list[str] = []

            actual = run_le_14_acceptance.prepare_voice_fixture(
                target,
                contract_path=contract,
                fetch=lambda url: calls.append(url) or payload,
            )

            self.assertEqual(actual, target)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(calls, [run_le_14_acceptance.FIXTURE_SOURCE_URL])
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_fixture_write_uses_binary_mode(self) -> None:
        payload = b"fLaC" + b"\0" * 128
        synthetic_binary_flag = 1 << 29
        observed_flags: list[int] = []
        real_open = os.open

        def open_without_synthetic_flag(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
        ) -> int:
            observed_flags.append(flags)
            return real_open(path, flags & ~synthetic_binary_flag, mode)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = self._contract(root, payload)
            target = root / "voice.flac"
            with (
                patch.object(
                    run_le_14_acceptance.os,
                    "O_BINARY",
                    synthetic_binary_flag,
                    create=True,
                ),
                patch.object(
                    run_le_14_acceptance.os,
                    "open",
                    side_effect=open_without_synthetic_flag,
                ),
            ):
                run_le_14_acceptance.prepare_voice_fixture(
                    target,
                    contract_path=contract,
                    fetch=lambda _url: payload,
                )

        self.assertTrue(observed_flags)
        self.assertTrue(observed_flags[-1] & synthetic_binary_flag)

    def test_fixture_rejects_wrong_digest_without_leaving_output(self) -> None:
        expected = b"fLaC" + b"a" * 128
        replacement = b"fLaC" + b"b" * 128
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = self._contract(root, expected)
            target = root / "voice.flac"

            with self.assertRaisesRegex(
                run_le_14_acceptance.Le14AcceptanceFailure,
                r"^LE-14 speech fixture is unavailable$",
            ):
                run_le_14_acceptance.prepare_voice_fixture(
                    target,
                    contract_path=contract,
                    fetch=lambda _url: replacement,
                )

            self.assertFalse(target.exists())

    def test_fixture_rejects_an_existing_or_linked_destination(self) -> None:
        payload = b"fLaC" + b"\0" * 128
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = self._contract(root, payload)
            private = root / "private.flac"
            private.write_bytes(b"private")
            target = root / "voice.flac"
            try:
                target.symlink_to(private)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")

            with self.assertRaisesRegex(
                run_le_14_acceptance.Le14AcceptanceFailure,
                r"^LE-14 speech fixture is unavailable$",
            ):
                run_le_14_acceptance.prepare_voice_fixture(
                    target,
                    contract_path=contract,
                    fetch=lambda _url: payload,
                )

            self.assertEqual(private.read_bytes(), b"private")

    def test_interrupted_chunked_download_is_a_fixed_failure(self) -> None:
        response = MagicMock()
        response.status = 200
        response.geturl.return_value = run_le_14_acceptance.FIXTURE_SOURCE_URL
        response.read.side_effect = http.client.IncompleteRead(b"partial", 10)
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response

        with (
            patch.object(
                run_le_14_acceptance.urllib.request,
                "build_opener",
                return_value=opener,
            ),
            self.assertRaisesRegex(
                run_le_14_acceptance.Le14AcceptanceFailure,
                r"^LE-14 speech fixture is unavailable$",
            ),
        ):
            run_le_14_acceptance._fetch_fixture(run_le_14_acceptance.FIXTURE_SOURCE_URL)


class Le14AcceptanceRunnerTests(unittest.TestCase):
    def test_runner_uses_paths_not_key_and_rejects_collect_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "credential.json"
            api_key = f"sk-{'a' * 20}"
            secret.write_text(
                json.dumps({"provider": "bailian", "apiKey": api_key}),
                encoding="utf-8",
            )
            secret.chmod(0o600)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="1 test collected in 0.01s\n",
                stderr="",
            )
            child_process = MagicMock()
            child_process.pid = 4242
            child_process.returncode = completed.returncode
            child_process.communicate.return_value = (
                completed.stdout,
                completed.stderr,
            )
            with (
                patch.dict(
                    os.environ, {"PYTEST_ADDOPTS": "--collect-only"}, clear=False
                ),
                patch.object(
                    run_le_14_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(run_le_14_acceptance, "ensure_silero_vad_assets"),
                patch.object(
                    run_le_14_acceptance,
                    "prepare_voice_fixture",
                    return_value=root / "voice.flac",
                ),
                patch.object(subprocess, "run", return_value=completed),
                patch.object(
                    subprocess,
                    "Popen",
                    return_value=child_process,
                ) as popen,
                self.assertRaisesRegex(
                    run_le_14_acceptance.Le14AcceptanceFailure,
                    r"^LE-14 real acceptance failed$",
                ),
            ):
                run_le_14_acceptance.run_acceptance(secret)

        child = popen.call_args
        self.assertIsNotNone(child)
        argv = child.args[0]
        environment = child.kwargs["env"]
        self.assertNotIn(api_key, argv)
        self.assertNotIn(api_key, environment.values())
        self.assertNotIn("PYTEST_ADDOPTS", environment)
        self.assertEqual(
            environment[run_le_14_acceptance.SECRET_PATH_ENVIRONMENT],
            os.fspath(secret),
        )
        self.assertEqual(
            environment[run_le_14_acceptance.VOICE_PATH_ENVIRONMENT],
            os.fspath((root / "voice.flac").resolve()),
        )
        windows_postgres_root = Path(
            environment[acceptance_postgres.WINDOWS_POSTGRES_ROOT_ENVIRONMENT]
        )
        self.assertEqual(windows_postgres_root.name, "windows-postgres")
        self.assertEqual(
            child.kwargs["start_new_session"],
            os.name != "nt",
        )
        self.assertEqual(
            child.kwargs["creationflags"],
            (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
        child_process.communicate.assert_called_once_with(
            timeout=run_le_14_acceptance.ACCEPTANCE_TIMEOUT_SECONDS
        )

    def test_runner_timeout_cleans_process_tree_and_postgres_without_path_leak(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "private-credential.json"
            api_key = f"sk-{'b' * 20}"
            secret.write_text(
                json.dumps({"provider": "bailian", "apiKey": api_key}),
                encoding="utf-8",
            )
            secret.chmod(0o600)
            private_command = [os.fspath(root / "private-python"), "--private"]
            timeout = subprocess.TimeoutExpired(
                private_command,
                1,
                output=os.fspath(secret),
                stderr=api_key,
            )
            child_process = MagicMock()
            child_process.pid = 4343
            child_process.communicate.side_effect = timeout
            with (
                patch.object(
                    run_le_14_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(run_le_14_acceptance, "ensure_silero_vad_assets"),
                patch.object(
                    run_le_14_acceptance,
                    "prepare_voice_fixture",
                    return_value=root / "voice.flac",
                ),
                patch.object(subprocess, "run", side_effect=timeout),
                patch.object(subprocess, "Popen", return_value=child_process),
                patch.object(
                    run_le_14_acceptance,
                    "_terminate_process_tree",
                    create=True,
                ) as terminate,
                patch.object(
                    run_le_14_acceptance,
                    "_cleanup_postgres_resources",
                    create=True,
                ) as cleanup,
                self.assertRaises(run_le_14_acceptance.Le14AcceptanceFailure) as raised,
            ):
                run_le_14_acceptance.run_acceptance(secret)

        self.assertEqual(str(raised.exception), "LE-14 real acceptance failed")
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn(api_key, str(raised.exception))
        self.assertNotIn(os.fspath(secret), str(raised.exception))
        terminate.assert_called_once_with(child_process)
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0], child_process.pid)
        self.assertEqual(cleanup.call_args.args[1].name, "windows-postgres")

    def test_runner_timeout_keeps_sigterm_handler_until_cleanup_finishes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "credential.json"
            secret.write_text(
                json.dumps({"provider": "bailian", "apiKey": f"sk-{'b' * 20}"}),
                encoding="utf-8",
            )
            secret.chmod(0o600)
            child_process = MagicMock()
            child_process.pid = 4393
            child_process.communicate.side_effect = subprocess.TimeoutExpired([], 1)
            original_handler = MagicMock(
                side_effect=AssertionError("old SIGTERM handler interrupted cleanup")
            )
            active_handler = original_handler

            def install_handler(
                _signal_number: int,
                handler: object,
            ) -> object:
                nonlocal active_handler
                previous = active_handler
                active_handler = handler
                return previous

            def terminate_during_second_sigterm(process: object) -> None:
                self.assertIs(process, child_process)
                active_handler(signal.SIGTERM, None)

            with (
                patch.object(
                    run_le_14_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(run_le_14_acceptance, "ensure_silero_vad_assets"),
                patch.object(
                    run_le_14_acceptance,
                    "prepare_voice_fixture",
                    return_value=root / "voice.flac",
                ),
                patch.object(subprocess, "Popen", return_value=child_process),
                patch.object(
                    run_le_14_acceptance.signal,
                    "signal",
                    side_effect=install_handler,
                ),
                patch.object(
                    run_le_14_acceptance,
                    "_terminate_process_tree",
                    side_effect=terminate_during_second_sigterm,
                ) as terminate,
                patch.object(
                    run_le_14_acceptance,
                    "_cleanup_postgres_resources",
                ) as cleanup,
                self.assertRaisesRegex(
                    run_le_14_acceptance.Le14AcceptanceFailure,
                    r"^LE-14 real acceptance failed$",
                ),
            ):
                run_le_14_acceptance.run_acceptance(secret)

        self.assertIs(active_handler, original_handler)
        terminate.assert_called_once_with(child_process)
        cleanup.assert_called_once()

    def test_runner_cancellation_cleans_process_tree_and_postgres(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "credential.json"
            secret.write_text(
                json.dumps({"provider": "bailian", "apiKey": f"sk-{'c' * 20}"}),
                encoding="utf-8",
            )
            secret.chmod(0o600)
            child_process = MagicMock()
            child_process.pid = 4444
            child_process.communicate.side_effect = KeyboardInterrupt
            with (
                patch.object(
                    run_le_14_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(run_le_14_acceptance, "ensure_silero_vad_assets"),
                patch.object(
                    run_le_14_acceptance,
                    "prepare_voice_fixture",
                    return_value=root / "voice.flac",
                ),
                patch.object(subprocess, "run", side_effect=KeyboardInterrupt),
                patch.object(subprocess, "Popen", return_value=child_process),
                patch.object(
                    run_le_14_acceptance,
                    "_terminate_process_tree",
                    create=True,
                ) as terminate,
                patch.object(
                    run_le_14_acceptance,
                    "_cleanup_postgres_resources",
                    create=True,
                ) as cleanup,
                self.assertRaises(KeyboardInterrupt),
            ):
                run_le_14_acceptance.run_acceptance(secret)

        terminate.assert_called_once_with(child_process)
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0], child_process.pid)
        self.assertEqual(cleanup.call_args.args[1].name, "windows-postgres")

    def test_runner_sigterm_cleans_owned_resources_and_restores_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "credential.json"
            secret.write_text(
                json.dumps({"provider": "bailian", "apiKey": f"sk-{'d' * 20}"}),
                encoding="utf-8",
            )
            secret.chmod(0o600)
            child_process = MagicMock()
            child_process.pid = 4494
            original_handler = MagicMock(
                side_effect=AssertionError("SIGTERM handler was not installed")
            )
            active_handler = original_handler

            def install_handler(
                _signal_number: int,
                handler: object,
            ) -> object:
                nonlocal active_handler
                previous = active_handler
                active_handler = handler
                return previous

            def communicate(*, timeout: int) -> tuple[str, str]:
                del timeout
                return active_handler(signal.SIGTERM, None)

            def terminate_during_second_sigterm(process: object) -> None:
                self.assertIs(process, child_process)
                active_handler(signal.SIGTERM, None)

            child_process.communicate.side_effect = communicate
            with (
                patch.object(
                    run_le_14_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(run_le_14_acceptance, "ensure_silero_vad_assets"),
                patch.object(
                    run_le_14_acceptance,
                    "prepare_voice_fixture",
                    return_value=root / "voice.flac",
                ),
                patch.object(subprocess, "Popen", return_value=child_process),
                patch.object(
                    run_le_14_acceptance.signal,
                    "signal",
                    side_effect=install_handler,
                ),
                patch.object(
                    run_le_14_acceptance,
                    "_terminate_process_tree",
                    create=True,
                    side_effect=terminate_during_second_sigterm,
                ) as terminate,
                patch.object(
                    run_le_14_acceptance,
                    "_cleanup_postgres_resources",
                    create=True,
                ) as cleanup,
                self.assertRaisesRegex(
                    run_le_14_acceptance.Le14AcceptanceFailure,
                    r"^LE-14 real acceptance failed$",
                ),
            ):
                run_le_14_acceptance.run_acceptance(secret)

        self.assertIs(active_handler, original_handler)
        terminate.assert_called_once_with(child_process)
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0], child_process.pid)
        self.assertEqual(cleanup.call_args.args[1].name, "windows-postgres")

    @unittest.skipIf(os.name == "nt", "POSIX SIGTERM/session regression")
    def test_real_sigterm_reaps_the_isolated_pytest_session(self) -> None:
        harness_source = """
import os
import sys
from pathlib import Path

sys.path.insert(0, os.fspath(Path(sys.argv[1]) / "scripts"))
import run_le_14_acceptance as acceptance

root = Path(sys.argv[2])
acceptance.read_bailian_api_key = lambda _path: "sk-" + "e" * 20
acceptance.ensure_silero_vad_assets = lambda: root
acceptance.prepare_verified_media_toolchain = lambda _path: root / "toolchain"
acceptance.prepare_voice_fixture = lambda _path: root / "voice.flac"
acceptance.ACCEPTANCE_TEST = os.fspath(root / "sleeping_acceptance_test.py")
try:
    acceptance.run_acceptance(root / "credential.json")
except acceptance.Le14AcceptanceFailure as error:
    raise SystemExit(str(error)) from None
"""
        sleeping_test_source = """
import os
import time
from pathlib import Path

def test_sleep_until_cancelled() -> None:
    Path(os.environ["AUTOMATION_TOOL_SIGTERM_CHILD_PID"]).write_text(
        str(os.getpid()),
        encoding="ascii",
    )
    time.sleep(60)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sleeping_test = root / "sleeping_acceptance_test.py"
            sleeping_test.write_text(sleeping_test_source, encoding="utf-8")
            child_pid_path = root / "child.pid"
            environment = os.environ.copy()
            environment["AUTOMATION_TOOL_SIGTERM_CHILD_PID"] = os.fspath(child_pid_path)
            harness = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    harness_source,
                    os.fspath(ROOT),
                    os.fspath(root),
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid: int | None = None
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if child_pid_path.is_file():
                        child_pid = int(child_pid_path.read_text(encoding="ascii"))
                        break
                    if harness.poll() is not None:
                        break
                    time.sleep(0.02)
                if child_pid is None:
                    self.fail(
                        f"sleeping pytest did not start: {harness.communicate(timeout=5)}"
                    )
                os.kill(harness.pid, signal.SIGTERM)
                stdout, stderr = harness.communicate(timeout=15)
                self.assertNotEqual(harness.returncode, 0)
                self.assertIn("LE-14 real acceptance failed", stderr)
                self.assertNotIn(os.fspath(root), stdout + stderr)
                for _ in range(100):
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)
                else:
                    self.fail(f"isolated pytest process survived SIGTERM: {child_pid}")
            finally:
                if harness.poll() is None:
                    harness.kill()
                    harness.wait(timeout=5)
                if child_pid is not None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(child_pid, signal.SIGKILL)

    def test_postgres_cleanup_targets_only_the_child_pytest_project(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(run_le_14_acceptance.os, "name", "posix"),
            patch.object(subprocess, "run", return_value=completed) as process,
        ):
            run_le_14_acceptance._cleanup_postgres_resources(
                4545,
                Path("unused-on-posix"),
            )

        command = process.call_args.args[0]
        self.assertEqual(
            command,
            [
                "docker",
                "compose",
                "--project-name",
                "automation-tool-pytest-4545",
                "--env-file",
                os.devnull,
                "--file",
                os.fspath(run_le_14_acceptance.REPOSITORY_ROOT / "compose.yaml"),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
        )
        self.assertEqual(
            process.call_args.kwargs["timeout"],
            run_le_14_acceptance.CLEANUP_TIMEOUT_SECONDS,
        )

    def test_windows_cleanup_stops_parent_owned_postgres(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "windows-postgres"
            data = root / "data"
            pg_ctl = os.fspath(Path(directory) / "pg_ctl.exe")
            data.mkdir(parents=True)
            with (
                patch.object(run_le_14_acceptance.os, "name", "nt"),
                patch.object(
                    run_le_14_acceptance.shutil,
                    "which",
                    return_value=pg_ctl,
                ),
                patch.object(subprocess, "run", return_value=completed) as process,
            ):
                run_le_14_acceptance._cleanup_postgres_resources(4595, root)

        self.assertEqual(
            process.call_args.args[0],
            [
                pg_ctl,
                "--pgdata",
                os.fspath(data),
                "--mode",
                "fast",
                "--wait",
                "stop",
            ],
        )
        self.assertEqual(
            process.call_args.kwargs["timeout"],
            run_le_14_acceptance.CLEANUP_TIMEOUT_SECONDS,
        )

    def test_windows_cleanup_escalates_a_failed_fast_postgres_stop(self) -> None:
        failed = subprocess.CompletedProcess([], 1, "", "")
        running = subprocess.CompletedProcess([], 0, "", "")
        succeeded = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "windows-postgres"
            data = root / "data"
            pg_ctl = os.fspath(Path(directory) / "pg_ctl.exe")
            data.mkdir(parents=True)
            with (
                patch.object(run_le_14_acceptance.os, "name", "nt"),
                patch.object(
                    run_le_14_acceptance.shutil,
                    "which",
                    return_value=pg_ctl,
                ),
                patch.object(
                    subprocess,
                    "run",
                    side_effect=[failed, running, succeeded],
                ) as process,
            ):
                run_le_14_acceptance._cleanup_postgres_resources(4596, root)

        self.assertEqual(len(process.call_args_list), 3)
        self.assertEqual(
            process.call_args_list[0].args[0][
                process.call_args_list[0].args[0].index("--mode") + 1
            ],
            "fast",
        )
        self.assertEqual(process.call_args_list[1].args[0][-1], "status")
        self.assertEqual(
            process.call_args_list[2].args[0][
                process.call_args_list[2].args[0].index("--mode") + 1
            ],
            "immediate",
        )

    def test_windows_cleanup_rejects_a_failed_immediate_postgres_stop(self) -> None:
        failed = subprocess.CompletedProcess([], 1, "", "")
        running = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "windows-postgres"
            data = root / "data"
            pg_ctl = os.fspath(Path(directory) / "pg_ctl.exe")
            data.mkdir(parents=True)
            with (
                patch.object(run_le_14_acceptance.os, "name", "nt"),
                patch.object(
                    run_le_14_acceptance.shutil,
                    "which",
                    return_value=pg_ctl,
                ),
                patch.object(
                    subprocess,
                    "run",
                    side_effect=[failed, running, failed, running],
                ),
                self.assertRaisesRegex(
                    run_le_14_acceptance.Le14AcceptanceFailure,
                    r"^LE-14 real acceptance failed$",
                ),
            ):
                run_le_14_acceptance._cleanup_postgres_resources(4597, root)

    def test_windows_cleanup_accepts_postgres_that_is_already_stopped(self) -> None:
        failed = subprocess.CompletedProcess([], 1, "", "")
        stopped = subprocess.CompletedProcess([], 3, "", "")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "windows-postgres"
            data = root / "data"
            pg_ctl = os.fspath(Path(directory) / "pg_ctl.exe")
            data.mkdir(parents=True)
            with (
                patch.object(run_le_14_acceptance.os, "name", "nt"),
                patch.object(
                    run_le_14_acceptance.shutil,
                    "which",
                    return_value=pg_ctl,
                ),
                patch.object(
                    subprocess,
                    "run",
                    side_effect=[failed, stopped],
                ) as process,
            ):
                run_le_14_acceptance._cleanup_postgres_resources(4598, root)

        self.assertEqual(len(process.call_args_list), 2)
        self.assertEqual(process.call_args_list[1].args[0][-1], "status")

    @unittest.skipIf(
        os.name == "nt" or not hasattr(signal, "SIGKILL"),
        "POSIX process-group signals are unavailable",
    )
    def test_posix_process_tree_cleanup_escalates_its_owned_session(self) -> None:
        child_process = MagicMock()
        child_process.pid = 4646
        child_process.poll.return_value = None
        child_process.wait.side_effect = [
            subprocess.TimeoutExpired([], 1),
            0,
        ]
        with (
            patch.object(run_le_14_acceptance.os, "name", "posix"),
            patch.object(
                run_le_14_acceptance.os,
                "getpgid",
                return_value=child_process.pid,
                create=True,
            ),
            patch.object(
                run_le_14_acceptance.os,
                "killpg",
                create=True,
            ) as kill_group,
        ):
            run_le_14_acceptance._terminate_process_tree(child_process)

        self.assertEqual(
            kill_group.call_args_list,
            [
                call(child_process.pid, signal.SIGTERM),
                call(child_process.pid, signal.SIGKILL),
            ],
        )

    @unittest.skipIf(
        os.name == "nt" or not hasattr(signal, "SIGKILL"),
        "POSIX process-group signals are unavailable",
    )
    def test_posix_cleanup_kills_group_after_leader_exits(self) -> None:
        child_process = MagicMock()
        child_process.pid = 4696
        child_process.wait.return_value = 0
        with (
            patch.object(run_le_14_acceptance.os, "name", "posix"),
            patch.object(
                run_le_14_acceptance.os,
                "killpg",
                create=True,
            ) as kill_group,
        ):
            run_le_14_acceptance._terminate_process_tree(child_process)

        self.assertEqual(
            kill_group.call_args_list,
            [
                call(child_process.pid, signal.SIGTERM),
                call(child_process.pid, signal.SIGKILL),
            ],
        )

    def test_windows_process_tree_cleanup_uses_taskkill_tree(self) -> None:
        child_process = MagicMock()
        child_process.pid = 4747
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(run_le_14_acceptance.os, "name", "nt"),
            patch.object(
                run_le_14_acceptance,
                "_windows_descendant_process_ids",
                return_value=(),
                create=True,
            ),
            patch.object(subprocess, "run", return_value=completed) as process,
        ):
            run_le_14_acceptance._terminate_process_tree(child_process)

        self.assertEqual(
            process.call_args.args[0],
            ["taskkill", "/PID", str(child_process.pid), "/T", "/F"],
        )
        self.assertGreater(
            process.call_args.kwargs["timeout"],
            0,
        )
        self.assertLessEqual(
            process.call_args.kwargs["timeout"],
            run_le_14_acceptance.CLEANUP_TIMEOUT_SECONDS,
        )

    def test_windows_taskkill_failure_recaptures_late_descendants(self) -> None:
        child_process = MagicMock()
        child_process.pid = 4848
        failed = subprocess.CompletedProcess([], 1, "", "")
        succeeded = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(run_le_14_acceptance.os, "name", "nt"),
            patch.object(
                run_le_14_acceptance,
                "_windows_descendant_process_ids",
                side_effect=[(4850,), (4851, 4850)],
            ) as descendants,
            patch.object(run_le_14_acceptance.time, "monotonic", return_value=0.0),
            patch.object(
                subprocess,
                "run",
                side_effect=[failed, succeeded, succeeded],
            ) as process,
        ):
            run_le_14_acceptance._terminate_process_tree(child_process)

        self.assertEqual(descendants.call_count, 2)
        self.assertEqual(
            [entry.args[0] for entry in process.call_args_list],
            [
                ["taskkill", "/PID", "4848", "/T", "/F"],
                ["taskkill", "/PID", "4851", "/T", "/F"],
                ["taskkill", "/PID", "4850", "/T", "/F"],
            ],
        )
        child_process.kill.assert_called_once_with()
        child_process.wait.assert_called_with(
            timeout=run_le_14_acceptance.PROCESS_KILL_TIMEOUT_SECONDS
        )

    def test_windows_taskkill_fallback_rejects_a_surviving_descendant(self) -> None:
        child_process = MagicMock()
        child_process.pid = 4878
        failed = subprocess.CompletedProcess([], 1, "", "")
        succeeded = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(run_le_14_acceptance.os, "name", "nt"),
            patch.object(
                run_le_14_acceptance,
                "_windows_descendant_process_ids",
                side_effect=[(4880,), (4881, 4880)],
            ),
            patch.object(
                run_le_14_acceptance,
                "_windows_existing_process_ids",
                return_value=(4881,),
            ),
            patch.object(run_le_14_acceptance.time, "monotonic", return_value=0.0),
            patch.object(
                subprocess,
                "run",
                side_effect=[failed, failed, succeeded],
            ),
            self.assertRaisesRegex(
                run_le_14_acceptance.Le14AcceptanceFailure,
                r"^LE-14 real acceptance failed$",
            ),
        ):
            run_le_14_acceptance._terminate_process_tree(child_process)

    def test_windows_taskkill_nonzero_accepts_an_exited_descendant(self) -> None:
        child_process = MagicMock()
        child_process.pid = 4890
        failed = subprocess.CompletedProcess([], 1, "", "")
        succeeded = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(run_le_14_acceptance.os, "name", "nt"),
            patch.object(
                run_le_14_acceptance,
                "_windows_descendant_process_ids",
                side_effect=[(4892,), (4893, 4892)],
            ),
            patch.object(
                run_le_14_acceptance,
                "_windows_existing_process_ids",
                return_value=(),
                create=True,
            ) as existing_process_ids,
            patch.object(run_le_14_acceptance.time, "monotonic", return_value=0.0),
            patch.object(
                subprocess,
                "run",
                side_effect=[failed, failed, succeeded],
            ),
        ):
            run_le_14_acceptance._terminate_process_tree(child_process)

        existing_process_ids.assert_called_once()

    def test_windows_taskkill_fallback_shares_one_cleanup_deadline(self) -> None:
        child_process = MagicMock()
        child_process.pid = 4898
        failed = subprocess.CompletedProcess([], 1, "", "")
        with (
            patch.object(run_le_14_acceptance.os, "name", "nt"),
            patch.object(
                run_le_14_acceptance,
                "_windows_descendant_process_ids",
                side_effect=[(4900, 4899), (4901, 4900, 4899)],
            ) as descendants,
            patch.object(
                run_le_14_acceptance.time,
                "monotonic",
                side_effect=[0.0, 0.0, 61.0, 61.0, 61.0, 61.0],
            ),
            patch.object(subprocess, "run", return_value=failed) as process,
            self.assertRaisesRegex(
                run_le_14_acceptance.Le14AcceptanceFailure,
                r"^LE-14 real acceptance failed$",
            ),
        ):
            run_le_14_acceptance._terminate_process_tree(child_process)

        self.assertEqual(descendants.call_count, 2)
        taskkill_commands = [
            entry.args[0]
            for entry in process.call_args_list
            if entry.args[0][0] == "taskkill"
        ]
        self.assertEqual(
            taskkill_commands,
            [["taskkill", "/PID", "4898", "/T", "/F"]],
        )

    def test_windows_descendant_snapshot_is_bounded_and_path_free(self) -> None:
        completed = subprocess.CompletedProcess(
            [],
            0,
            "4850\nnot-a-pid\n4849\n4848\n4850\n",
            "",
        )
        with patch.object(subprocess, "run", return_value=completed) as process:
            descendants = run_le_14_acceptance._windows_descendant_process_ids(4848)

        self.assertEqual(descendants, (4850, 4849))
        self.assertEqual(
            process.call_args.kwargs["env"][
                run_le_14_acceptance.WINDOWS_CLEANUP_ROOT_PID_ENVIRONMENT
            ],
            "4848",
        )
        self.assertNotIn("4848", process.call_args.args[0])
        self.assertEqual(
            process.call_args.kwargs["timeout"],
            run_le_14_acceptance.PROCESS_STOP_TIMEOUT_SECONDS,
        )

    def test_windows_existing_process_ids_filters_the_system_snapshot(self) -> None:
        completed = subprocess.CompletedProcess(
            [],
            0,
            "4850\nnot-a-pid\n9999\n4850\n",
            "",
        )
        with (
            patch.object(subprocess, "run", return_value=completed) as process,
            patch.object(
                run_le_14_acceptance.time,
                "monotonic",
                return_value=0.0,
            ),
        ):
            existing = run_le_14_acceptance._windows_existing_process_ids(
                (4850, 4849),
                deadline=run_le_14_acceptance.CLEANUP_TIMEOUT_SECONDS,
            )

        self.assertEqual(existing, (4850,))
        self.assertNotIn("env", process.call_args.kwargs)
        self.assertNotIn("4850", process.call_args.args[0])
        self.assertGreater(process.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(
            process.call_args.kwargs["timeout"],
            run_le_14_acceptance.PROCESS_STOP_TIMEOUT_SECONDS,
        )


class WindowsPostgresHandoffTests(unittest.TestCase):
    def test_native_postgres_uses_the_parent_owned_root(self) -> None:
        calls: list[list[str]] = []
        windows_tool_root = PureWindowsPath("C:/PostgreSQL/bin")
        tools = {
            name: os.fspath(windows_tool_root / f"{name}.exe")
            for name in ("initdb", "pg_ctl", "createdb")
        }

        def fake_run(
            command: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[0] == tools["initdb"]:
                data = Path(command[command.index("--pgdata") + 1])
                data.mkdir(parents=True)
                (data / "postgresql.conf").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            parent_owned_root = Path(directory) / "windows-postgres"
            environment = {
                "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_test",
                "AUTOMATION_TOOL_TEST_DB_PASSWORD": "private-test-password",
                "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_test",
                acceptance_postgres.WINDOWS_POSTGRES_ROOT_ENVIRONMENT: os.fspath(
                    parent_owned_root
                ),
            }
            with (
                patch.object(
                    acceptance_postgres,
                    "_required_postgres_tool",
                    side_effect=tools.__getitem__,
                ),
                patch.object(
                    acceptance_postgres.subprocess,
                    "run",
                    side_effect=fake_run,
                ),
                acceptance_postgres._native_windows_postgres(
                    database_port=55432,
                    environment=environment,
                ),
            ):
                self.assertTrue((parent_owned_root / "data").is_dir())

            self.assertTrue(parent_owned_root.is_dir())

        stop = calls[-1]
        self.assertEqual(stop[0], tools["pg_ctl"])
        self.assertEqual(
            stop[stop.index("--pgdata") + 1],
            os.fspath(parent_owned_root / "data"),
        )
        self.assertEqual(stop[stop.index("--mode") + 1], "fast")


if __name__ == "__main__":
    unittest.main()
