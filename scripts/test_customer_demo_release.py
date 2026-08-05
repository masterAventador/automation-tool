#!/usr/bin/env python3
"""Deterministic tests for the customer Demo release material.

No build, no network, no key generation on the side. These cover the two
things that decide whether a customer Demo package is usable at all:

* the signed deployment profile reaches the compiler, and is refused here —
  before a twenty-minute build — if the Rust verifier would reject it;
* the action authorization keypair is supplied from outside and *kept*, so the
  public half compiled into the App matches the private half the Control Plane
  holds as its `action-authorization-private-key` Secret. It used to be
  generated per build and dropped on the floor, which made "execute an action"
  permanently dead in any package.

The private halves are read from files and never enter argv, the environment,
the returned material or a message.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_release_package import (  # noqa: E402
    ReleaseFailed,
    parse_arguments,
    resolve_update_configuration,
)
from customer_demo_release import (  # noqa: E402
    CustomerDemoMaterialRejected,
    customer_demo_material,
    load_signing_seed,
    require_compiled_deployment,
)
from run_p9_03_acceptance import release_environment  # noqa: E402

DEMO_SEED = bytes(range(32))
ACTION_SEED = bytes(range(32, 64))
MANIFEST = {
    "profileId": "demo-xuanbai",
    "baseUrl": "https://at.xuanbai.tech",
    "allowedHosts": ["at.xuanbai.tech"],
}


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def write_key(directory: Path, name: str, seed: bytes, *, mode: int = 0o600) -> Path:
    path = directory / name
    path.write_text(f"{base64url(seed)}\n", encoding="utf-8")
    path.chmod(mode)
    if os.name == "nt" and mode == 0o600:
        # `chmod` only toggles the read-only attribute here. What the loader
        # actually requires on this host is a DACL granting this user and
        # nobody else, so the fixture has to produce one — otherwise every
        # test would exercise the rejection path.
        make_windows_private(path)
    return path


def make_windows_private(path: Path) -> None:
    user = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
    for arguments in (["/inheritance:r"], ["/grant", f"{user}:(F)"], ["/setowner", user]):
        subprocess.run(
            ["icacls", str(path), *arguments],
            check=True,
            capture_output=True,
        )


def write_manifest(directory: Path, manifest: object) -> Path:
    path = directory / "deployment.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class CustomerDemoMaterialTests(unittest.TestCase):
    def material(self, directory: Path, manifest: object = None):
        return customer_demo_material(
            deployment_path=write_manifest(directory, MANIFEST if manifest is None else manifest),
            profile_signing_key_path=write_key(directory, "profile-key", DEMO_SEED),
            action_authorization_key_path=write_key(directory, "action-key", ACTION_SEED),
        )

    def test_signs_the_canonical_payload_the_rust_verifier_reconstructs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            material = self.material(Path(temporary))

        payload = decode(material.profile_payload)
        # Byte-for-byte: the Rust side re-serialises the parsed manifest and
        # refuses the build unless it reproduces these exact bytes, so field
        # order is part of the contract, not a formatting preference.
        self.assertEqual(
            payload.decode("utf-8"),
            '{"version":"customer-demo-profile.v1","profile":"demo",'
            '"profileId":"demo-xuanbai","baseUrl":"https://at.xuanbai.tech",'
            '"allowedHosts":["at.xuanbai.tech"]}',
        )
        verifying_key = Ed25519PublicKey.from_public_bytes(decode(material.profile_verifying_key))
        verifying_key.verify(decode(material.profile_signature), payload)
        self.assertEqual(material.base_url, "https://at.xuanbai.tech")
        self.assertEqual(material.profile_id, "demo-xuanbai")

    def test_a_tampered_payload_no_longer_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            material = self.material(Path(temporary))

        verifying_key = Ed25519PublicKey.from_public_bytes(decode(material.profile_verifying_key))
        tampered = decode(material.profile_payload).replace(b"at.xuanbai.tech", b"at.attacker.test")
        with self.assertRaises(InvalidSignature):
            verifying_key.verify(decode(material.profile_signature), tampered)

    def test_publishes_only_public_halves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            material = self.material(Path(temporary))

        rendered = repr(material) + json.dumps(material.environment())
        for secret in (base64url(DEMO_SEED), base64url(ACTION_SEED)):
            self.assertNotIn(secret, rendered)
        # The action authorization public key must be the one derived from the
        # operator's key file, never a per-build throwaway.
        self.assertEqual(
            material.action_authorization_public_key,
            base64url(
                Ed25519PrivateKey.from_private_bytes(ACTION_SEED).public_key().public_bytes_raw()
            ),
        )

    def test_refuses_a_deployment_the_rust_verifier_would_reject(self) -> None:
        rejected = [
            {**MANIFEST, "baseUrl": "http://at.xuanbai.tech"},
            {**MANIFEST, "baseUrl": "https://at.xuanbai.tech:8443"},
            {**MANIFEST, "baseUrl": "https://at.xuanbai.tech/api"},
            {**MANIFEST, "baseUrl": "https://at.xuanbai.tech/"},
            {**MANIFEST, "baseUrl": "https://49.233.213.109"},
            # The App accepts no bare address literal, however consistently it
            # is declared: the last label has to be lowercase letters.
            {
                "profileId": "demo-xuanbai",
                "baseUrl": "https://49.233.213.109",
                "allowedHosts": ["49.233.213.109"],
            },
            {
                "profileId": "demo-xuanbai",
                "baseUrl": "https://localhost",
                "allowedHosts": ["localhost"],
            },
            {**MANIFEST, "allowedHosts": ["other.xuanbai.tech"]},
            {**MANIFEST, "allowedHosts": []},
            {**MANIFEST, "allowedHosts": ["b.xuanbai.tech", "at.xuanbai.tech"]},
            {**MANIFEST, "profileId": "xuanbai"},
            {**MANIFEST, "profileId": "demo-Xuanbai"},
            {**MANIFEST, "version": "customer-demo-profile.v1"},
            {"baseUrl": "https://at.xuanbai.tech", "allowedHosts": ["at.xuanbai.tech"]},
        ]
        for manifest in rejected:
            with (
                tempfile.TemporaryDirectory() as temporary,
                self.assertRaises(CustomerDemoMaterialRejected, msg=repr(manifest)),
            ):
                self.material(Path(temporary), manifest)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_refuses_a_key_file_other_local_users_can_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            readable = write_key(directory, "loose-key", DEMO_SEED, mode=0o644)
            with self.assertRaises(CustomerDemoMaterialRejected):
                load_signing_seed(readable)

    @unittest.skipUnless(os.name == "nt", "Windows access control")
    def test_refuses_a_key_file_windows_lets_another_principal_read(self) -> None:
        """`0o600` cannot be expressed here, and cannot be checked either.

        CPython reports `S_IMODE` `0o666` for every writable file on this host,
        so the mode comparison is not merely wrong — it can never pass, and a
        Windows release could not be built at all. The property it protects is
        real: an Ed25519 seed that signs deployment profiles must be readable by
        nobody else. Windows states that as a DACL, which is exactly what the
        Profile directories already use.
        """
        base = Path(tempfile.mkdtemp(prefix="demo-key-"))
        self.addCleanup(shutil.rmtree, base, True)
        user = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"

        private = base / "private-key"
        private.write_text(base64url(DEMO_SEED), encoding="utf-8")
        for arguments in (["/inheritance:r"], ["/grant", f"{user}:(F)"]):
            subprocess.run(["icacls", str(private), *arguments], check=True, capture_output=True)

        self.assertEqual(load_signing_seed(private), DEMO_SEED)

        # The same file with its inherited entries left in place: on this
        # machine `%TEMP%` grants an extra group read access, which is exactly
        # the leak this refuses.
        shared = base / "shared-key"
        shared.write_text(base64url(DEMO_SEED), encoding="utf-8")
        with self.assertRaises(CustomerDemoMaterialRejected):
            load_signing_seed(shared)

    def test_refuses_a_key_file_that_is_not_a_canonical_ed25519_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, content in (
                ("short", base64url(bytes(31))),
                ("padded", base64.urlsafe_b64encode(DEMO_SEED).decode()),
                ("standard", base64.b64encode(bytes(range(32))).decode()),
                ("empty", ""),
                ("text", "not-a-key"),
            ):
                path = directory / name
                path.write_text(content, encoding="utf-8")
                path.chmod(0o600)
                with self.assertRaises(CustomerDemoMaterialRejected, msg=name):
                    load_signing_seed(path)


class CompiledDeploymentTests(unittest.TestCase):
    """A release must prove the binary carries the deployment it was built for.

    The three profile variables are consumed by `build.rs`, which is free to be
    skipped entirely by a stale `cargo` cache. Checking the finished binary is
    the only statement that cannot be satisfied by a build that did not happen.
    """

    def material(self, directory: Path):
        return customer_demo_material(
            deployment_path=write_manifest(directory, MANIFEST),
            profile_signing_key_path=write_key(directory, "profile-key", DEMO_SEED),
            action_authorization_key_path=write_key(directory, "action-key", ACTION_SEED),
        )

    def test_accepts_a_binary_carrying_the_profile_and_the_action_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            material = self.material(directory)
            binary = directory / "desktop"
            # Exactly what `tauri build` produces, and no more. The Control
            # Plane address is *inside* the base64url payload — the Rust side
            # decodes and signature-checks it at startup — so it never appears
            # in the binary as a readable literal. A gate that looked for one
            # rejected a perfectly good package on its first real run.
            binary.write_bytes(
                bytes(4096)
                + material.profile_payload.encode()
                + material.profile_signature.encode()
                + material.profile_verifying_key.encode()
                + material.action_authorization_public_key.encode()
            )
            self.assertNotIn(material.base_url.encode(), binary.read_bytes())

            require_compiled_deployment(binary, material)

            # The address is still proven, by the payload that is present.
            self.assertIn(
                material.base_url,
                decode(material.profile_payload).decode("utf-8"),
            )

    def test_refuses_a_binary_built_without_the_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            material = self.material(directory)
            for missing, payload in (
                ("profile payload", material.profile_signature.encode()),
                (
                    "action authorization key",
                    material.profile_payload.encode()
                    + material.profile_signature.encode()
                    + material.profile_verifying_key.encode()
                    + material.base_url.encode(),
                ),
                ("everything", b"an ordinary local-profile release"),
            ):
                binary = directory / "desktop"
                binary.write_bytes(bytes(4096) + payload)
                with self.assertRaises(CustomerDemoMaterialRejected, msg=missing):
                    require_compiled_deployment(binary, material)


class ReleaseArgumentTests(unittest.TestCase):
    def test_every_path_argument_is_resolved_against_the_invocation_directory(
        self,
    ) -> None:
        """A relative path must not be re-interpreted by a subprocess.

        `tauri build` runs with `cwd=frontend/`, so a relative `--work-dir`
        produced a generated configuration the bundler then failed to find —
        reported as "No such file or directory" against a path that did exist,
        which is a long way from the actual mistake.
        """
        arguments = parse_arguments(
            [
                "--work-dir",
                ".local/customer-demo-release",
                "--archive",
                "cache/chrome.zip",
                "--deployment-profile",
                "deployment.json",
                "--profile-signing-key",
                "keys/profile",
                "--action-authorization-key",
                "keys/action",
            ]
        )

        for name in (
            "work_dir",
            "archive",
            "deployment_profile",
            "profile_signing_key",
            "action_authorization_key",
        ):
            path = getattr(arguments, name)
            self.assertTrue(path.is_absolute(), name)
            self.assertEqual(path, Path.cwd() / path.relative_to(Path.cwd()), name)

    def test_absent_optional_paths_stay_absent(self) -> None:
        arguments = parse_arguments([])

        self.assertIsNone(arguments.archive)
        self.assertIsNone(arguments.deployment_profile)
        self.assertTrue(arguments.work_dir.is_absolute())

    def test_update_endpoint_and_public_key_file_are_one_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            public_key = Path(temporary) / "update-key"
            public_key.write_text("encoded-minisign-public-key\n", encoding="utf-8")
            endpoint = (
                "https://updates.xuanbai.tech/desktop-updates/v1/stable/"
                "{{target}}/{{arch}}/{{current_version}}"
            )
            arguments = parse_arguments(
                [
                    "--update-endpoint",
                    endpoint,
                    "--update-public-key-file",
                    os.fspath(public_key),
                ]
            )

            self.assertEqual(
                resolve_update_configuration(arguments),
                (endpoint, "encoded-minisign-public-key"),
            )

        with self.assertRaises(ReleaseFailed):
            resolve_update_configuration(
                parse_arguments(["--update-endpoint", endpoint])
            )


class ReleaseEnvironmentTests(unittest.TestCase):
    def test_a_release_without_a_deployment_stays_exactly_as_it_was(self) -> None:
        environment = release_environment(Path("/tmp/target"), "executor-key")

        self.assertNotIn("AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD", environment)
        self.assertEqual(environment["AUTOMATION_TOOL_UPDATE_DISABLED"], "1")
        self.assertNotIn("AUTOMATION_TOOL_UPDATE_ENDPOINT", environment)
        self.assertNotIn("AUTOMATION_TOOL_UPDATE_PUBLIC_KEY", environment)
        self.assertEqual(
            environment["AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY"],
            "executor-key",
        )

    def test_a_real_update_configuration_is_compiled_as_one_pair(self) -> None:
        endpoint = (
            "https://updates.xuanbai.tech/desktop-updates/v1/stable/"
            "{{target}}/{{arch}}/{{current_version}}"
        )
        environment = release_environment(
            Path("/tmp/target"),
            "executor-key",
            update_endpoint=endpoint,
            update_public_key="encoded-minisign-public-key",
        )

        self.assertNotIn("AUTOMATION_TOOL_UPDATE_DISABLED", environment)
        self.assertEqual(environment["AUTOMATION_TOOL_UPDATE_ENDPOINT"], endpoint)
        self.assertEqual(
            environment["AUTOMATION_TOOL_UPDATE_PUBLIC_KEY"],
            "encoded-minisign-public-key",
        )

    def test_a_partial_update_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            release_environment(
                Path("/tmp/target"),
                "executor-key",
                update_endpoint=(
                    "https://updates.xuanbai.tech/desktop-updates/v1/stable/"
                    "{{target}}/{{arch}}/{{current_version}}"
                ),
            )

    def test_a_customer_demo_release_carries_the_profile_and_the_action_key(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            material = customer_demo_material(
                deployment_path=write_manifest(directory, MANIFEST),
                profile_signing_key_path=write_key(directory, "profile", DEMO_SEED),
                action_authorization_key_path=write_key(directory, "action", ACTION_SEED),
            )

        previous = os.environ.get("AUTOMATION_TOOL_LEAKED_FROM_THE_SHELL")
        os.environ["AUTOMATION_TOOL_LEAKED_FROM_THE_SHELL"] = "1"
        try:
            environment = release_environment(
                Path("/tmp/target"),
                "executor-key",
                deployment_profile=material.environment(),
                action_authorization_public_key=material.action_authorization_public_key,
            )
        finally:
            if previous is None:
                del os.environ["AUTOMATION_TOOL_LEAKED_FROM_THE_SHELL"]
            else:
                os.environ["AUTOMATION_TOOL_LEAKED_FROM_THE_SHELL"] = previous

        self.assertEqual(
            environment["AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD"],
            material.profile_payload,
        )
        self.assertEqual(
            environment["AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE"],
            material.profile_signature,
        )
        self.assertEqual(
            environment["AUTOMATION_TOOL_DEPLOYMENT_PROFILE_VERIFYING_KEY"],
            material.profile_verifying_key,
        )
        # Two different keys with two different owners: the Executor manifest
        # key is generated per build, the action authorization key belongs to
        # the deployment. Passing one where the other belongs is what made the
        # Control Plane unable to sign anything the App would accept.
        self.assertEqual(
            environment["AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY"],
            material.action_authorization_public_key,
        )
        self.assertNotEqual(
            environment["AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY"],
            environment["AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY"],
        )
        # Everything else the ambient shell offers is still stripped.
        self.assertNotIn("AUTOMATION_TOOL_LEAKED_FROM_THE_SHELL", environment)


if __name__ == "__main__":
    unittest.main()
