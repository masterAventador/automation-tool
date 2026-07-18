import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialFactory,
    DeviceCredentialService,
    InvalidDeviceCredential,
    IssuedDeviceCredential,
    ParsedDeviceCredential,
    PendingDeviceCredential,
    RevokedDeviceCredential,
    parse_device_credential,
)

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


@dataclass
class FixedClock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


@dataclass
class FakeRepository:
    rotations: list[dict[str, object]] = field(default_factory=list)
    revocations: list[dict[str, object]] = field(default_factory=list)

    async def rotate(self, **values: object) -> IssuedDeviceCredential:
        self.rotations.append(values)
        pending = cast(PendingDeviceCredential, values["replacement"])
        return IssuedDeviceCredential(
            credential_id=pending.credential_id,
            installation_id=uuid4(),
            credential=pending.credential,
            version=2,
            scope=DEVICE_CREDENTIAL_SCOPE,
        )

    async def revoke(self, **values: object) -> RevokedDeviceCredential:
        self.revocations.append(values)
        presented = cast(ParsedDeviceCredential, values["presented"])
        return RevokedDeviceCredential(
            credential_id=presented.credential_id,
            installation_id=uuid4(),
            version=1,
            status="revoked",
        )


def factory(
    *,
    secret_source: Callable[[int], object] | None = None,
    id_source: Callable[[], object] | None = None,
) -> DeviceCredentialFactory:
    return DeviceCredentialFactory(
        secret_source=cast(
            Callable[[int], bytes],
            secret_source or (lambda length: bytes(range(length))),
        ),
        id_source=cast(Callable[[], UUID], id_source or uuid4),
    )


def test_factory_creates_canonical_256_bit_credential_and_only_a_digest_for_storage() -> None:
    credential_id = UUID("9ef928d0-92af-45e5-96ac-cc97829b5812")
    created = factory(id_source=lambda: credential_id).create()

    encoded_secret = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
    assert created == PendingDeviceCredential(
        credential_id=credential_id,
        credential=f"atdc1.{credential_id}.{encoded_secret}",
        secret_digest=hashlib.sha256(bytes(range(32))).digest(),
    )
    assert created.credential.encode() not in created.secret_digest
    assert parse_device_credential(created.credential).credential_id == credential_id
    assert parse_device_credential(created.credential).secret_digest == created.secret_digest


@pytest.mark.parametrize(
    "value",
    (
        "",
        "private-token",
        "atdc2.9ef928d0-92af-45e5-96ac-cc97829b5812.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "atdc1.123e4567-e89b-12d3-a456-426614174000.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "atdc1.not-a-uuid.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "atdc1.9ef928d0-92af-45e5-96ac-cc97829b5812.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        "atdc1.9ef928d0-92af-45e5-96ac-cc97829b5812.AA",
        "atdc1.9ef928d0-92af-45e5-96ac-cc97829b5812.私密值",
        "x" * 257,
        cast(str, b"not-text"),
    ),
)
def test_parser_rejects_noncanonical_credentials_without_reflecting_them(value: str) -> None:
    with pytest.raises(InvalidDeviceCredential) as captured:
        parse_device_credential(value)
    assert str(captured.value) == "Device credential is invalid"
    if isinstance(value, str) and value:
        assert value not in str(captured.value)


@pytest.mark.parametrize(
    ("secret", "credential_id"),
    (
        (b"", uuid4()),
        (b"x" * 31, uuid4()),
        (b"x" * 33, uuid4()),
        ("x" * 32, uuid4()),
        (b"x" * 32, UUID("123e4567-e89b-12d3-a456-426614174000")),
        (b"x" * 32, "not-a-uuid"),
    ),
)
def test_factory_fails_closed_for_broken_randomness_or_identifier_sources(
    secret: object,
    credential_id: object,
) -> None:
    with pytest.raises(RuntimeError, match="Device credential generation failed"):
        factory(
            secret_source=lambda _length: secret,
            id_source=lambda: credential_id,
        ).create()


@pytest.mark.asyncio
async def test_service_rotates_with_a_fresh_pending_secret_and_fixed_time() -> None:
    repository = FakeRepository()
    credential_factory = factory()
    current = credential_factory.create().credential
    manager = DeviceCredentialService(
        repository=repository,
        clock=FixedClock(),
        credential_factory=factory(secret_source=lambda length: b"z" * length),
    )

    issued = await manager.rotate(current)

    assert issued.version == 2
    assert issued.scope == "device.session.exchange"
    assert len(repository.rotations) == 1
    assert repository.rotations[0]["rotated_at"] == NOW
    assert repository.rotations[0]["presented"] == parse_device_credential(current)
    replacement = cast(PendingDeviceCredential, repository.rotations[0]["replacement"])
    assert replacement.credential != current


@pytest.mark.asyncio
async def test_service_revokes_a_parsed_credential_at_an_aware_time() -> None:
    repository = FakeRepository()
    current = factory().create().credential
    manager = DeviceCredentialService(
        repository=repository,
        clock=FixedClock(),
        credential_factory=factory(),
    )

    revoked = await manager.revoke(current)

    assert revoked.status == "revoked"
    assert repository.revocations == [
        {"presented": parse_device_credential(current), "revoked_at": NOW}
    ]


@pytest.mark.asyncio
async def test_service_rejects_invalid_credentials_and_naive_clock_before_repository() -> None:
    repository = FakeRepository()
    manager = DeviceCredentialService(
        repository=repository,
        clock=FixedClock(NOW.replace(tzinfo=None)),
        credential_factory=factory(),
    )

    with pytest.raises(InvalidDeviceCredential):
        await manager.rotate("private-invalid")
    with pytest.raises(RuntimeError, match="Device credential clock is invalid"):
        await manager.revoke(factory().create().credential)
    assert repository.rotations == []
    assert repository.revocations == []
