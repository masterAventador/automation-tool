from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "scripts"))

import run_le_13_acceptance


class Le13AcceptanceCredentialTests(unittest.TestCase):
    def test_reader_does_not_consume_a_replacement_after_metadata_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = root / "credential.json"
            original_key = f"sk-{'a' * 20}"
            replacement = root / "replacement.json"
            replacement_key = f"sk-{'b' * 20}"
            credential.write_text(
                json.dumps({"provider": "bailian", "apiKey": original_key}),
                encoding="utf-8",
            )
            replacement.write_text(
                json.dumps({"provider": "bailian", "apiKey": replacement_key}),
                encoding="utf-8",
            )
            credential.chmod(0o600)
            replacement.chmod(0o600)
            original_lstat = Path.lstat
            original_fstat = os.fstat
            replaced = False

            def replace_credential() -> None:
                nonlocal replaced
                if not replaced:
                    credential.unlink()
                    credential.symlink_to(replacement)
                    replaced = True

            def replace_after_lstat(path: Path) -> os.stat_result:
                metadata = original_lstat(path)
                if path == credential:
                    replace_credential()
                return metadata

            def replace_after_fstat(descriptor: int) -> os.stat_result:
                metadata = original_fstat(descriptor)
                replace_credential()
                return metadata

            with (
                patch.object(
                    Path, "lstat", autospec=True, side_effect=replace_after_lstat
                ),
                patch(
                    "run_le_13_acceptance.os.fstat",
                    autospec=True,
                    side_effect=replace_after_fstat,
                ),
            ):
                actual_key = run_le_13_acceptance.read_bailian_api_key(credential)

        self.assertEqual(actual_key, original_key)

    def test_cli_does_not_resolve_away_a_symlinked_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = root / "credential.json"
            credential.write_text(
                json.dumps({"provider": "bailian", "apiKey": f"sk-{'a' * 20}"}),
                encoding="utf-8",
            )
            credential.chmod(0o600)
            linked = root / "linked.json"
            try:
                linked.symlink_to(credential)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")

            with (
                patch.object(
                    sys, "argv", ["run_le_13_acceptance.py", "--secret", str(linked)]
                ),
                patch("run_le_13_acceptance.subprocess.run") as process,
                self.assertRaisesRegex(
                    SystemExit,
                    r"^LE-13 model credential is unavailable$",
                ),
            ):
                run_le_13_acceptance.main()

        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
