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
