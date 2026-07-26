#!/usr/bin/env python3
"""The material that turns a release build into a customer Demo package.

Two things separate a customer Demo package from an ordinary release, and
until now neither could be supplied to a build at all:

* **Where the App talks to.** The signed deployment profile is compiled in;
  `frontend/src-tauri/build.rs` verifies the signature and freezes it into the
  binary, and `deployment_profile.rs` re-verifies it at startup. Nothing at
  runtime can change it, by design and by test, so the address has to arrive
  here or not at all.
* **Whose action authorizations it accepts.** The App compiles in a public key
  and refuses any platform action not signed by its private half. That private
  half is the Control Plane's `action-authorization-private-key` Secret. The
  release path used to generate this keypair per build and drop the private
  half on the floor, which left every package permanently unable to execute an
  action — the server could not produce a signature the App would accept.

So both arrive as *files*, and only paths appear in argv. The private halves
are read, used, and never placed in the environment, in the returned material,
in a log line or in an error message: `CustomerDemoMaterial` has `slots=True`
and carries public halves only, so it cannot structurally hold one.

Every rule `frontend/src-tauri/src/deployment_profile.rs` enforces is enforced
again here, before the build starts. That duplication is deliberate: the
alternative is discovering a rejected profile as a `panic!` twenty minutes into
a release, or worse, shipping a package that panics on the user's machine.
"""

from __future__ import annotations

import base64
import json
import stat
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PROFILE_VERSION = "customer-demo-profile.v1"
PROFILE_KIND = "demo"
# Mirrors deployment_profile.rs: the field order is part of the signed bytes.
DEPLOYMENT_FIELDS = ("profileId", "baseUrl", "allowedHosts")
MAX_ENCODED_PAYLOAD_LENGTH = 4096
MAX_ALLOWED_HOSTS = 8
REQUIRED_KEY_MODE = 0o600
ED25519_SEED_BYTES = 32
PAYLOAD_ENVIRONMENT = "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_PAYLOAD"
SIGNATURE_ENVIRONMENT = "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_SIGNATURE"
VERIFYING_KEY_ENVIRONMENT = "AUTOMATION_TOOL_DEPLOYMENT_PROFILE_VERIFYING_KEY"


class CustomerDemoMaterialRejected(RuntimeError):
    """The deployment or its keys cannot produce a customer Demo package."""


def _reject(message: str) -> None:
    # Never interpolates file content — only what the operator can already see.
    raise CustomerDemoMaterialRejected(f"customer Demo release rejected: {message}")


@dataclass(frozen=True, slots=True)
class CustomerDemoMaterial:
    """Public halves only. A private key does not fit in this shape."""

    profile_payload: str
    profile_signature: str
    profile_verifying_key: str
    action_authorization_public_key: str
    profile_id: str
    base_url: str
    allowed_hosts: tuple[str, ...]

    def environment(self) -> dict[str, str]:
        """The three compile-time variables `build.rs` expects together."""
        return {
            PAYLOAD_ENVIRONMENT: self.profile_payload,
            SIGNATURE_ENVIRONMENT: self.profile_signature,
            VERIFYING_KEY_ENVIRONMENT: self.profile_verifying_key,
        }


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_canonical(value: str) -> bytes:
    """Decode exactly the way `deployment_profile.rs::decode_canonical` does."""
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as error:
        raise CustomerDemoMaterialRejected(
            "customer Demo release rejected: key material is not canonical base64url"
        ) from error
    if base64url(decoded) != value:
        _reject("key material is not canonical base64url")
    return decoded


def load_signing_seed(path: Path) -> bytes:
    """Read one Ed25519 seed from a file only its owner can read.

    The seed never reaches argv or the environment; the caller gets bytes and
    is expected to derive from them and let them go.
    """
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _reject(f"key file must be a regular file: {path}")
    if stat.S_IMODE(metadata.st_mode) != REQUIRED_KEY_MODE:
        _reject(
            f"key file must be mode {REQUIRED_KEY_MODE:04o}, not "
            f"{stat.S_IMODE(metadata.st_mode):04o}: {path}"
        )
    if metadata.st_size > 128:
        _reject(f"key file is too large to be one Ed25519 seed: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        _reject(f"key file is not UTF-8: {path}")
    encoded = content[:-1] if content.endswith("\n") else content
    if encoded != encoded.strip():
        _reject(f"key file has leading or trailing whitespace: {path}")
    if not encoded:
        _reject(f"key file is empty: {path}")
    seed = _decode_canonical(encoded)
    if len(seed) != ED25519_SEED_BYTES:
        _reject(f"key file is not a {ED25519_SEED_BYTES}-byte Ed25519 seed: {path}")
    return seed


def _valid_hostname(value: str) -> bool:
    """`deployment_profile.rs::valid_hostname`, rule for rule."""
    if not value or len(value) > 253 or "." not in value:
        return False
    labels = value.split(".")
    for label in labels:
        if not label or len(label) > 63:
            return False
        if not (label[0].isascii() and label[0].isalnum()):
            return False
        if not (label[-1].isascii() and label[-1].isalnum()):
            return False
        if any(
            not (character.isascii() and (character.islower() or character.isdigit()))
            and character != "-"
            for character in label
        ):
            return False
    top_level = labels[-1]
    # An address literal dies here: the last label of `49.233.213.109` is not
    # made of lowercase letters, which is why the App accepts no bare IP.
    return len(top_level) >= 2 and all(
        character.isascii() and character.islower() for character in top_level
    )


def _valid_profile_id(value: str) -> bool:
    return (
        6 <= len(value) <= 48
        and value.startswith("demo-")
        and not value.endswith("-")
        and all(
            character.isascii() and (character.islower() or character.isdigit() or character == "-")
            for character in value
        )
    )


def canonical_manifest(deployment: Any) -> dict[str, Any]:
    """Validate an operator's deployment file and build the manifest to sign.

    The operator states only what varies. `version` and `profile` are constants
    of the format and are added here, so they cannot be mistyped into a package
    that fails to start.
    """
    if not isinstance(deployment, dict):
        _reject("the deployment file must contain one JSON object")
    unexpected = sorted(set(deployment) - set(DEPLOYMENT_FIELDS))
    missing = sorted(set(DEPLOYMENT_FIELDS) - set(deployment))
    if unexpected or missing:
        _reject(
            f"the deployment file must declare exactly {list(DEPLOYMENT_FIELDS)}: "
            f"missing {missing}, unexpected {unexpected}"
        )

    profile_id = deployment["profileId"]
    base_url = deployment["baseUrl"]
    allowed_hosts = deployment["allowedHosts"]
    if not isinstance(profile_id, str) or not _valid_profile_id(profile_id):
        _reject("profileId must be 6-48 lowercase characters beginning with 'demo-'")
    if not isinstance(base_url, str):
        _reject("baseUrl must be a string")
    if (
        not isinstance(allowed_hosts, list)
        or not allowed_hosts
        or len(allowed_hosts) > MAX_ALLOWED_HOSTS
        or not all(isinstance(host, str) for host in allowed_hosts)
    ):
        _reject(f"allowedHosts must be 1-{MAX_ALLOWED_HOSTS} host names")
    if any(first >= second for first, second in pairwise(allowed_hosts)):
        _reject("allowedHosts must be sorted ascending with no duplicates")
    for host in allowed_hosts:
        if not _valid_hostname(host):
            _reject(f"allowedHosts contains a host name the App will reject: {host}")

    parsed = urlsplit(base_url)
    host = parsed.hostname
    if host is None:
        _reject("baseUrl must contain a host name")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
        or base_url != f"https://{host}"
    ):
        # The App accepts `https://<host>` and nothing else: no port (so the
        # deployment must answer on 443), no path prefix, not even a trailing
        # slash, and no bare IP address.
        _reject(
            "baseUrl must be exactly https://<host> — no port, no path, no "
            f"trailing slash, no IP address: {base_url}"
        )
    if host not in allowed_hosts:
        _reject(f"allowedHosts must contain the baseUrl host: {host}")

    return {
        "version": PROFILE_VERSION,
        "profile": PROFILE_KIND,
        "profileId": profile_id,
        "baseUrl": base_url,
        "allowedHosts": list(allowed_hosts),
    }


def customer_demo_material(
    *,
    deployment_path: Path,
    profile_signing_key_path: Path,
    action_authorization_key_path: Path,
) -> CustomerDemoMaterial:
    """Sign one deployment profile and derive the action authorization key."""
    try:
        deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CustomerDemoMaterialRejected(
            f"customer Demo release rejected: unreadable deployment file: {deployment_path}"
        ) from error
    manifest = canonical_manifest(deployment)
    # Compact, key order as declared: `serde_json::to_vec` on the Rust struct
    # must reproduce these exact bytes or the App refuses the profile.
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded_payload = base64url(payload)
    if len(encoded_payload) > MAX_ENCODED_PAYLOAD_LENGTH:
        _reject("the signed deployment profile is too large for the App to accept")

    profile_signer = Ed25519PrivateKey.from_private_bytes(
        load_signing_seed(profile_signing_key_path)
    )
    signature = profile_signer.sign(payload)
    profile_verifying_key = profile_signer.public_key().public_bytes_raw()
    del profile_signer

    action_signer = Ed25519PrivateKey.from_private_bytes(
        load_signing_seed(action_authorization_key_path)
    )
    action_public_key = action_signer.public_key().public_bytes_raw()
    del action_signer

    return CustomerDemoMaterial(
        profile_payload=encoded_payload,
        profile_signature=base64url(signature),
        profile_verifying_key=base64url(profile_verifying_key),
        action_authorization_public_key=base64url(action_public_key),
        profile_id=manifest["profileId"],
        base_url=manifest["baseUrl"],
        allowed_hosts=tuple(manifest["allowedHosts"]),
    )


def require_compiled_deployment(binary: Path, material: CustomerDemoMaterial) -> None:
    """Refuse a package whose binary does not carry the deployment it was built for.

    Everything upstream of this is an instruction to the compiler, and a stale
    `cargo` cache is entitled to ignore an instruction. This is the one check
    that a build which did not happen cannot pass: the compiled profile and the
    action authorization key are read back out of the finished artifact.
    """
    content = binary.read_bytes()
    # The Control Plane address is deliberately absent from this list. It is
    # carried *inside* the base64url payload, which the App decodes and
    # signature-checks at startup, so a correct package contains no readable
    # copy of it. Requiring one rejected a good package on the first real run.
    # The payload's presence is the stronger claim anyway: it is the exact
    # bytes that were signed, and they decode to this deployment and no other.
    required = {
        "deployment profile payload": material.profile_payload,
        "deployment profile signature": material.profile_signature,
        "deployment profile verifying key": material.profile_verifying_key,
        "action authorization public key": material.action_authorization_public_key,
    }
    absent = sorted(
        name for name, value in required.items() if value.encode("utf-8") not in content
    )
    if absent:
        _reject(
            f"the built binary does not carry the deployment it was built for: {absent} "
            f"absent from {binary}"
        )


def describe_deployment(material: CustomerDemoMaterial) -> dict[str, object]:
    """What a release may print about a deployment: public facts only."""
    return {
        "profileId": material.profile_id,
        "baseUrl": material.base_url,
        "allowedHosts": list(material.allowed_hosts),
        "profileVerifyingKey": material.profile_verifying_key,
        "actionAuthorizationPublicKey": material.action_authorization_public_key,
    }


__all__ = [
    "CustomerDemoMaterial",
    "CustomerDemoMaterialRejected",
    "canonical_manifest",
    "customer_demo_material",
    "describe_deployment",
    "load_signing_seed",
    "require_compiled_deployment",
]
