from __future__ import annotations

import hashlib
import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "scripts"))

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
        cleanup.assert_called_once_with(child_process.pid)

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
        cleanup.assert_called_once_with(child_process.pid)

    def test_postgres_cleanup_targets_only_the_child_pytest_project(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(run_le_14_acceptance.os, "name", "posix"),
            patch.object(subprocess, "run", return_value=completed) as process,
        ):
            run_le_14_acceptance._cleanup_postgres_resources(4545)

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
            patch.object(subprocess, "run", return_value=completed) as process,
        ):
            run_le_14_acceptance._terminate_process_tree(child_process)

        self.assertEqual(
            process.call_args.args[0],
            ["taskkill", "/PID", str(child_process.pid), "/T", "/F"],
        )
        self.assertEqual(
            process.call_args.kwargs["timeout"],
            run_le_14_acceptance.CLEANUP_TIMEOUT_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
