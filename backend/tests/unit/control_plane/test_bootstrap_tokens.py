import base64
import json
from datetime import UTC, datetime
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.control_plane.domain import BootstrapPurpose, DemoEnvironmentId
from automation_tool.control_plane.infrastructure.security.bootstrap_tokens import (
    BootstrapCredentialRejected,
    Ed25519BootstrapTokenVerifier,
)


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def signed_token(
    private_key: Ed25519PrivateKey,
    *,
    environment_id: str = "demo-cn-1",
    not_before: int = 1_785_523_200,
    expires_at: int = 1_785_526_800,
    overrides: dict[str, object] | None = None,
    raw_payload: bytes | None = None,
) -> str:
    claims: dict[str, object] = {
        "environmentId": environment_id,
        "expiresAt": expires_at,
        "notBefore": not_before,
        "purpose": "installation.register",
        "version": 1,
    }
    claims.update(overrides or {})
    payload = raw_payload or json.dumps(
        claims,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    payload_segment = base64url(payload)
    signing_input = f"atb1.{payload_segment}".encode("ascii")
    return f"atb1.{payload_segment}.{base64url(private_key.sign(signing_input))}"


def token_with_short_signature(private_key: Ed25519PrivateKey) -> str:
    prefix, payload, _signature = signed_token(private_key).split(".")
    return f"{prefix}.{payload}.{base64url(b'x')}"


def test_valid_bootstrap_token_yields_exact_typed_grant_and_stable_fingerprint() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = signed_token(private_key)
    verifier = Ed25519BootstrapTokenVerifier(private_key.public_key().public_bytes_raw())

    verified = verifier.verify(token)

    assert verified.grant.environment_id == DemoEnvironmentId.parse("demo-cn-1")
    assert verified.grant.purpose is BootstrapPurpose.REGISTER_INSTALLATION
    assert verified.grant.not_before == datetime.fromtimestamp(1_785_523_200, UTC)
    assert verified.grant.expires_at == datetime.fromtimestamp(1_785_526_800, UTC)
    assert len(verified.fingerprint) == 32
    assert verifier.verify(token).fingerprint == verified.fingerprint


@pytest.mark.parametrize(
    "token_factory",
    (
        lambda key: "",
        lambda key: "atb1.only-two-parts",
        lambda key: "atb2.e30.invalid",
        lambda key: "atb1.***.***",
        lambda key: "atb1.A.A",
        lambda key: "atb1.AB.AA",
        token_with_short_signature,
        lambda key: signed_token(key, overrides={"version": 2}),
        lambda key: signed_token(key, overrides={"purpose": "task.create"}),
        lambda key: signed_token(key, overrides={"unknown": "field"}),
        lambda key: signed_token(key, overrides={"notBefore": True}),
        lambda key: signed_token(key, overrides={"expiresAt": "1785526800"}),
        lambda key: signed_token(key, raw_payload=b'{"version":1,"version":1}'),
        lambda key: signed_token(key, raw_payload=b"\xff"),
        lambda key: signed_token(key, raw_payload=b"[]"),
        lambda key: signed_token(key, environment_id="DEMO-CN-1"),
        lambda key: signed_token(key, expires_at=1_786_128_001),
        lambda key: signed_token(key, overrides={"expiresAt": 10**100}),
    ),
    ids=(
        "empty",
        "wrong-part-count",
        "wrong-version-prefix",
        "invalid-base64url",
        "invalid-base64-length",
        "noncanonical-base64url",
        "short-signature",
        "wrong-claims-version",
        "business-purpose",
        "unknown-claim",
        "boolean-time",
        "string-time",
        "duplicate-json-field",
        "non-utf8-json",
        "non-object-json",
        "invalid-environment",
        "lifetime-over-seven-days",
        "timestamp-overflow",
    ),
)
def test_malformed_or_over_scoped_bootstrap_tokens_fail_closed(
    token_factory: object,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = Ed25519BootstrapTokenVerifier(private_key.public_key().public_bytes_raw())
    factory = token_factory
    assert callable(factory)

    with pytest.raises(BootstrapCredentialRejected, match="Bootstrap credential rejected"):
        verifier.verify(factory(private_key))


def test_tampering_wrong_signer_and_oversized_credentials_are_indistinguishable() -> None:
    trusted_key = Ed25519PrivateKey.generate()
    wrong_key = Ed25519PrivateKey.generate()
    verifier = Ed25519BootstrapTokenVerifier(trusted_key.public_key().public_bytes_raw())
    valid = signed_token(trusted_key)
    prefix, payload, signature = valid.split(".")
    tampered = f"{prefix}.{payload[:-1]}A.{signature}"
    wrong_signer = signed_token(wrong_key)

    for rejected in (tampered, wrong_signer, valid + ("x" * 4096)):
        with pytest.raises(BootstrapCredentialRejected) as captured:
            verifier.verify(rejected)
        assert str(captured.value) == "Bootstrap credential rejected"
        assert rejected not in repr(captured.value)


def test_public_key_must_be_exactly_one_raw_ed25519_key() -> None:
    for invalid in (b"", b"x" * 31, b"x" * 33, cast(bytes, "x" * 32)):
        with pytest.raises(BootstrapCredentialRejected):
            Ed25519BootstrapTokenVerifier(invalid)


def test_non_string_and_non_ascii_tokens_are_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = Ed25519BootstrapTokenVerifier(private_key.public_key().public_bytes_raw())

    for invalid in (cast(str, b"token"), "atb1.载荷.签名"):
        with pytest.raises(BootstrapCredentialRejected):
            verifier.verify(invalid)
