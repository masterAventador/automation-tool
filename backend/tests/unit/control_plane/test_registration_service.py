import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.control_plane.application.registration import (
    CHALLENGE_LIFETIME,
    BootstrapCredentialRejected,
    BootstrapRegistrationDenied,
    BootstrapTokenVerifier,
    InstallationRegistrationService,
    InvalidRegistrationRequest,
    RegisteredInstallation,
    RegistrationChallengeRecord,
    VerifiedBootstrapCredential,
)
from automation_tool.control_plane.domain import DemoBootstrapGrant, DemoEnvironmentId

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


@dataclass
class FakeRepository:
    saved: list[RegistrationChallengeRecord] = field(default_factory=list)
    completions: list[dict[str, object]] = field(default_factory=list)

    async def save_challenge(self, challenge: RegistrationChallengeRecord) -> None:
        self.saved.append(challenge)

    async def complete_challenge(self, **values: object) -> RegisteredInstallation:
        self.completions.append(values)
        return RegisteredInstallation(
            installation_id=uuid4(),
            status="active",
            revision=1,
        )


@dataclass
class FixedVerifier:
    credential: VerifiedBootstrapCredential | None = None
    error: ValueError | None = None

    def verify(self, _token: str) -> VerifiedBootstrapCredential:
        if self.error is not None:
            raise self.error
        assert self.credential is not None
        return self.credential


@dataclass
class MutableClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


def credential(
    *,
    environment_id: str = "demo-cn-1",
    not_before: datetime = NOW - timedelta(minutes=1),
    expires_at: datetime = NOW + timedelta(hours=1),
) -> VerifiedBootstrapCredential:
    return VerifiedBootstrapCredential(
        grant=DemoBootstrapGrant(
            environment_id=DemoEnvironmentId.parse(environment_id),
            not_before=not_before,
            expires_at=expires_at,
        ),
        fingerprint=b"f" * 32,
    )


def service(
    *,
    repository: FakeRepository | None = None,
    verifier: BootstrapTokenVerifier | None = None,
    clock: MutableClock | None = None,
    nonce_source: Callable[[int], object] | None = None,
) -> tuple[InstallationRegistrationService, FakeRepository]:
    resolved_repository = repository or FakeRepository()
    resolved_verifier = verifier or FixedVerifier(credential())
    resolved_nonce_source = nonce_source or (lambda length: b"n" * length)
    return (
        InstallationRegistrationService(
            repository=resolved_repository,
            bootstrap_verifier=resolved_verifier,
            expected_environment_id=DemoEnvironmentId.parse("demo-cn-1"),
            clock=clock or MutableClock(NOW),
            nonce_source=cast(Callable[[int], bytes], resolved_nonce_source),
        ),
        resolved_repository,
    )


@pytest.mark.asyncio
async def test_issue_challenge_persists_only_hash_and_returns_canonical_payload() -> None:
    registration, repository = service()

    issued = await registration.issue_challenge(
        bootstrap_token="opaque",
        environment_id="demo-cn-1",
        device_public_key=b"k" * 32,
    )

    payload = json.loads(issued.signing_payload)
    assert payload == {
        "challenge": "bm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm4",
        "challengeId": str(issued.challenge_id),
        "devicePublicKey": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s",
        "environmentId": "demo-cn-1",
        "expiresAt": int((NOW + CHALLENGE_LIFETIME).timestamp()),
        "purpose": "installation.register",
        "version": 1,
    }
    assert issued.expires_at == NOW + CHALLENGE_LIFETIME
    assert len(repository.saved) == 1
    assert repository.saved[0].proof_hash not in issued.signing_payload
    assert len(repository.saved[0].proof_hash) == 32


@pytest.mark.asyncio
async def test_challenge_never_outlives_its_bootstrap_grant() -> None:
    registration, _ = service(
        verifier=FixedVerifier(credential(expires_at=NOW + timedelta(minutes=1)))
    )

    issued = await registration.issue_challenge(
        bootstrap_token="opaque",
        environment_id="demo-cn-1",
        device_public_key=b"k" * 32,
    )

    assert issued.expires_at == NOW + timedelta(minutes=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment_id", "public_key", "clock", "error_type"),
    (
        ("demo-cn-1", b"", NOW, InvalidRegistrationRequest),
        ("demo-cn-1", b"x" * 31, NOW, InvalidRegistrationRequest),
        ("demo-cn-1", cast(bytes, bytearray(32)), NOW, InvalidRegistrationRequest),
        ("INVALID", b"x" * 32, NOW, InvalidRegistrationRequest),
        ("demo-cn-2", b"x" * 32, NOW, BootstrapRegistrationDenied),
        ("demo-cn-1", b"x" * 32, NOW.replace(tzinfo=None), InvalidRegistrationRequest),
    ),
)
async def test_issue_rejects_invalid_key_environment_and_clock(
    environment_id: object,
    public_key: bytes,
    clock: datetime,
    error_type: type[Exception],
) -> None:
    registration, _ = service(clock=MutableClock(clock))

    with pytest.raises(error_type):
        await registration.issue_challenge(
            bootstrap_token="opaque",
            environment_id=environment_id,
            device_public_key=public_key,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_nonce", (b"", b"x" * 31, b"x" * 33, "x" * 32))
async def test_issue_rejects_a_broken_secure_nonce_source(bad_nonce: object) -> None:
    registration, _ = service(nonce_source=lambda _length: bad_nonce)

    with pytest.raises(RuntimeError, match="Secure challenge generation failed"):
        await registration.issue_challenge(
            bootstrap_token="opaque",
            environment_id="demo-cn-1",
            device_public_key=b"x" * 32,
        )


@pytest.mark.asyncio
async def test_bootstrap_verifier_failures_and_denied_grants_are_normalized() -> None:
    for verifier in (
        FixedVerifier(error=BootstrapCredentialRejected()),
        FixedVerifier(error=ValueError("private verifier detail")),
    ):
        registration, _ = service(verifier=verifier)
        with pytest.raises(BootstrapCredentialRejected) as captured:
            await registration.issue_challenge(
                bootstrap_token="private token",
                environment_id="demo-cn-1",
                device_public_key=b"x" * 32,
            )
        assert str(captured.value) == "Bootstrap credential rejected"

    denied, _ = service(verifier=FixedVerifier(credential(environment_id="demo-cn-2")))
    with pytest.raises(BootstrapRegistrationDenied):
        await denied.issue_challenge(
            bootstrap_token="opaque",
            environment_id="demo-cn-1",
            device_public_key=b"x" * 32,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("challenge_id", "environment_id", "payload", "signature", "clock", "error_type"),
    (
        (cast(UUID, "not-a-uuid"), "demo-cn-1", b"p", b"s" * 64, NOW, InvalidRegistrationRequest),
        (uuid4(), "demo-cn-1", b"", b"s" * 64, NOW, InvalidRegistrationRequest),
        (
            uuid4(),
            "demo-cn-1",
            cast(bytes, bytearray(b"p")),
            b"s" * 64,
            NOW,
            InvalidRegistrationRequest,
        ),
        (uuid4(), "demo-cn-1", b"p" * 2049, b"s" * 64, NOW, InvalidRegistrationRequest),
        (uuid4(), "demo-cn-1", b"p", cast(bytes, bytearray(64)), NOW, InvalidRegistrationRequest),
        (uuid4(), "demo-cn-1", b"p", b"s" * 63, NOW, InvalidRegistrationRequest),
        (uuid4(), "INVALID", b"p", b"s" * 64, NOW, InvalidRegistrationRequest),
        (uuid4(), "demo-cn-2", b"p", b"s" * 64, NOW, BootstrapRegistrationDenied),
        (
            uuid4(),
            "demo-cn-1",
            b"p",
            b"s" * 64,
            NOW.replace(tzinfo=None),
            InvalidRegistrationRequest,
        ),
    ),
)
async def test_completion_rejects_noncanonical_inputs_before_repository(
    challenge_id: UUID,
    environment_id: object,
    payload: bytes,
    signature: bytes,
    clock: datetime,
    error_type: type[Exception],
) -> None:
    registration, repository = service(clock=MutableClock(clock))

    with pytest.raises(error_type):
        await registration.complete_registration(
            bootstrap_token="opaque",
            environment_id=environment_id,
            challenge_id=challenge_id,
            signing_payload=payload,
            signature=signature,
        )
    assert repository.completions == []


@pytest.mark.asyncio
async def test_valid_completion_delegates_all_verified_bindings() -> None:
    registration, repository = service()
    challenge_id = uuid4()

    completed = await registration.complete_registration(
        bootstrap_token="opaque",
        environment_id="demo-cn-1",
        challenge_id=challenge_id,
        signing_payload=b"payload",
        signature=b"s" * 64,
    )

    assert completed.status == "active"
    assert repository.completions == [
        {
            "bootstrap_fingerprint": b"f" * 32,
            "challenge_id": challenge_id,
            "completed_at": NOW,
            "environment_id": DemoEnvironmentId.parse("demo-cn-1"),
            "signature": b"s" * 64,
            "signing_payload": b"payload",
        }
    ]
