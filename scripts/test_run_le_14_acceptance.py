from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

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
                patch.object(subprocess, "run", return_value=completed) as process,
                self.assertRaisesRegex(
                    run_le_14_acceptance.Le14AcceptanceFailure,
                    r"^LE-14 real acceptance failed$",
                ),
            ):
                run_le_14_acceptance.run_acceptance(secret)

        child = process.call_args
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


if __name__ == "__main__":
    unittest.main()
