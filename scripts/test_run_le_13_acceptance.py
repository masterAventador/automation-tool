from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "scripts"))

import run_le_13_acceptance
from prepare_video_runtime import MEDIA_TOOLCHAIN_TARGETS


class Le13AcceptanceCredentialTests(unittest.TestCase):
    @staticmethod
    def _write_credential(root: Path, key_character: str = "a") -> tuple[Path, str]:
        credential = root / "credential.json"
        api_key = f"sk-{key_character * 20}"
        credential.write_text(
            json.dumps({"provider": "bailian", "apiKey": api_key}),
            encoding="utf-8",
        )
        credential.chmod(0o600)
        return credential, api_key

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

    def test_reader_rejects_symlink_without_no_follow_support(self) -> None:
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
                patch.object(os, "O_NOFOLLOW", 0),
                self.assertRaisesRegex(
                    run_le_13_acceptance.Le13AcceptanceFailure,
                    r"^LE-13 model credential is unavailable$",
                ),
            ):
                run_le_13_acceptance.read_bailian_api_key(linked)

    def test_reader_validates_windows_private_acl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            credential, api_key = self._write_credential(Path(directory))
            windows_acl = types.ModuleType("automation_tool.executor.windows_acl")
            validate_private_acl = Mock()
            windows_acl.validate_private_acl = validate_private_acl  # type: ignore[attr-defined]

            with (
                patch.object(os, "name", "nt"),
                patch.dict(
                    sys.modules,
                    {"automation_tool.executor.windows_acl": windows_acl},
                ),
            ):
                actual_key = run_le_13_acceptance.read_bailian_api_key(credential)

        self.assertEqual(actual_key, api_key)
        validate_private_acl.assert_called_once_with(credential)


class Le13AcceptanceRunnerTests(unittest.TestCase):
    def test_runner_rejects_collect_only_and_strips_pytest_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential, _ = Le13AcceptanceCredentialTests._write_credential(root)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "tests/integration/"
                    "test_material_understanding_real_acceptance.py::"
                    "test_real_material_understanding_round_trip\n"
                    "1 test collected in 0.01s\n"
                ),
                stderr="",
            )
            with (
                patch.dict(
                    os.environ,
                    {"PYTEST_ADDOPTS": "--collect-only"},
                    clear=False,
                ),
                patch.object(
                    run_le_13_acceptance,
                    "prepare_verified_media_toolchain",
                    return_value=root / "media-toolchain",
                ),
                patch.object(
                    subprocess,
                    "run",
                    return_value=completed,
                ) as process,
                self.assertRaisesRegex(
                    run_le_13_acceptance.Le13AcceptanceFailure,
                    r"^LE-13 real acceptance failed$",
                ),
            ):
                run_le_13_acceptance.run_acceptance(credential)

        child_environment = process.call_args.kwargs["env"]
        self.assertNotIn("PYTEST_ADDOPTS", child_environment)
        self.assertEqual(
            child_environment[run_le_13_acceptance.TOOLCHAIN_ROOT_ENVIRONMENT],
            os.fspath((root / "media-toolchain").resolve()),
        )

    def test_media_toolchain_is_prepared_installed_and_manifest_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            resource_root = Path(directory)
            staging = resource_root / "staging"
            installed_toolchain = resource_root / "media-toolchain"
            installed = {"media-toolchain": installed_toolchain}

            with (
                patch.object(
                    run_le_13_acceptance,
                    "host_platform",
                    return_value="macos",
                ),
                patch.object(
                    run_le_13_acceptance,
                    "prepare_video_runtime",
                    return_value=staging,
                ) as prepare,
                patch.object(
                    run_le_13_acceptance,
                    "install_video_runtime",
                    return_value=installed,
                ) as install,
                patch.object(
                    run_le_13_acceptance,
                    "validate_toolchain_contract",
                ) as validate_contract,
                patch.object(
                    run_le_13_acceptance,
                    "validate_toolchain_candidate",
                ) as validate_candidate,
            ):
                actual = run_le_13_acceptance.prepare_verified_media_toolchain(
                    resource_root
                )

        self.assertEqual(actual, installed_toolchain)
        prepare.assert_called_once_with(
            platform="macos",
            only=("media-toolchain",),
        )
        install.assert_called_once_with(
            staging=staging,
            resource_root=resource_root,
            only=("media-toolchain",),
            platform="macos",
        )
        validate_contract.assert_called_once()
        contract = validate_contract.call_args.args[0]
        validate_candidate.assert_called_once_with(
            installed_toolchain,
            MEDIA_TOOLCHAIN_TARGETS["macos"],
            contract,
        )

    def test_media_toolchain_probe_timeout_is_a_fixed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            resource_root = Path(directory)
            staging = resource_root / "staging"
            installed_toolchain = resource_root / "media-toolchain"
            private_binary = installed_toolchain / "bin" / "ffmpeg"

            with (
                patch.object(
                    run_le_13_acceptance,
                    "host_platform",
                    return_value="macos",
                ),
                patch.object(
                    run_le_13_acceptance,
                    "prepare_video_runtime",
                    return_value=staging,
                ),
                patch.object(
                    run_le_13_acceptance,
                    "install_video_runtime",
                    return_value={"media-toolchain": installed_toolchain},
                ),
                patch.object(
                    run_le_13_acceptance,
                    "validate_toolchain_contract",
                ),
                patch.object(
                    run_le_13_acceptance,
                    "validate_toolchain_candidate",
                    side_effect=subprocess.TimeoutExpired([private_binary], 15),
                ),
                self.assertRaisesRegex(
                    run_le_13_acceptance.Le13AcceptanceFailure,
                    r"^LE-13 packaged media toolchain is unavailable$",
                ),
            ):
                run_le_13_acceptance.prepare_verified_media_toolchain(resource_root)


if __name__ == "__main__":
    unittest.main()
