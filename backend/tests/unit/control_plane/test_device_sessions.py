import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialFactory,
    ParsedDeviceCredential,
)
from automation_tool.control_plane.application.device_sessions import (
    DEVICE_SESSION_CLOCK_SKEW,
    DEVICE_SESSION_LIFETIME,
    AuthenticatedDeviceSession,
    DeviceSessionCapability,
    DeviceSessionFactory,
    DeviceSessionRejected,
    DeviceSessionService,
    InvalidDeviceSession,
    InvalidDeviceSessionCapability,
    IssuedDeviceSession,
    ParsedDeviceSession,
    PendingDeviceSession,
    parse_device_session,
)
from automation_tool.control_plane.application.opaque_bearers import OpaqueBearerCodec
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.device_session_repository import (
    SqlAlchemyDeviceSessionRepository,
)

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
SESSION_ID = UUID("9ef928d0-92af-45e5-96ac-cc97829b5812")
INSTALLATION_ID = UUID("2a10fd92-c36d-4905-9e08-919ad0296bce")
CREDENTIAL_ID = UUID("ed224c6f-21f5-4587-82fe-a5351e1182e6")


@dataclass
class FixedClock:
    current: datetime = NOW

    def now(self) -> datetime:
        return self.current


@dataclass
class FakeRepository:
    issues: list[dict[str, object]] = field(default_factory=list)
    authentications: list[dict[str, object]] = field(default_factory=list)

    async def issue(self, **values: object) -> IssuedDeviceSession:
        self.issues.append(values)
        pending = cast(PendingDeviceSession, values["pending_session"])
        return IssuedDeviceSession(
            session_id=pending.session_id,
            installation_id=INSTALLATION_ID,
            credential_id=CREDENTIAL_ID,
            credential_version=3,
            session_token=pending.session_token,
            capability=cast(DeviceSessionCapability, values["capability"]),
            issued_at=cast(datetime, values["issued_at"]),
            not_before=cast(datetime, values["not_before"]),
            expires_at=cast(datetime, values["expires_at"]),
        )

    async def authenticate(self, **values: object) -> AuthenticatedDeviceSession:
        self.authentications.append(values)
        presented = cast(ParsedDeviceSession, values["presented_session"])
        return AuthenticatedDeviceSession(
            session_id=presented.session_id,
            installation_id=INSTALLATION_ID,
            credential_id=CREDENTIAL_ID,
            credential_version=3,
            capability=cast(DeviceSessionCapability, values["required_capability"]),
            expires_at=NOW + DEVICE_SESSION_LIFETIME,
        )


def session_factory(
    *,
    secret_source: Callable[[int], object] | None = None,
    id_source: Callable[[], object] | None = None,
) -> DeviceSessionFactory:
    return DeviceSessionFactory(
        secret_source=cast(
            Callable[[int], bytes],
            secret_source or (lambda length: bytes(range(length))),
        ),
        id_source=cast(Callable[[], UUID], id_source or (lambda: SESSION_ID)),
    )


def long_lived_credential() -> str:
    return (
        DeviceCredentialFactory(
            secret_source=lambda length: b"c" * length,
            id_source=lambda: CREDENTIAL_ID,
        )
        .create()
        .credential
    )


def test_factory_creates_canonical_256_bit_session_and_digest_only_storage_material() -> None:
    created = session_factory().create()
    encoded_secret = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")

    assert created == PendingDeviceSession(
        session_id=SESSION_ID,
        session_token=f"atds1.{SESSION_ID}.{encoded_secret}",
        secret_digest=hashlib.sha256(bytes(range(32))).digest(),
    )
    assert created.session_token.encode() not in created.secret_digest
    assert parse_device_session(created.session_token) == ParsedDeviceSession(
        session_id=SESSION_ID,
        secret_digest=created.secret_digest,
    )


def test_opaque_bearer_codec_rejects_an_unsafe_prefix() -> None:
    with pytest.raises(ValueError, match="Opaque bearer prefix is invalid"):
        OpaqueBearerCodec("unsafe.prefix")


@pytest.mark.parametrize(
    "value",
    (
        "",
        "private-session",
        "atds2.9ef928d0-92af-45e5-96ac-cc97829b5812.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "atds1.not-a-uuid.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "atds1.123e4567-e89b-12d3-a456-426614174000.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "atds1.9ef928d0-92af-45e5-96ac-cc97829b5812.AA",
        "atds1.9ef928d0-92af-45e5-96ac-cc97829b5812.私密值",
        "x" * 257,
        cast(str, b"not-text"),
    ),
)
def test_parser_rejects_noncanonical_sessions_without_reflection(value: str) -> None:
    with pytest.raises(InvalidDeviceSession) as captured:
        parse_device_session(value)
    assert str(captured.value) == "Device session is invalid"
    if isinstance(value, str) and value:
        assert value not in str(captured.value)


@pytest.mark.parametrize(
    ("secret", "session_id"),
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
    session_id: object,
) -> None:
    with pytest.raises(RuntimeError, match="Device session generation failed"):
        session_factory(
            secret_source=lambda _length: secret,
            id_source=lambda: session_id,
        ).create()


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("app.control-plane", DeviceSessionCapability.APP_CONTROL_PLANE),
        ("executor.connect", DeviceSessionCapability.EXECUTOR_CONNECT),
        (DeviceSessionCapability.APP_CONTROL_PLANE, DeviceSessionCapability.APP_CONTROL_PLANE),
    ),
)
def test_capability_parser_accepts_only_the_two_exact_minimal_capabilities(
    value: object,
    expected: DeviceSessionCapability,
) -> None:
    assert DeviceSessionCapability.parse(value) is expected


@pytest.mark.parametrize(
    "value",
    ("", "*", "task.write", "app.control-plane executor.connect", 1, None),
)
def test_capability_parser_rejects_unknown_or_combined_capabilities(value: object) -> None:
    with pytest.raises(InvalidDeviceSessionCapability) as captured:
        DeviceSessionCapability.parse(value)
    assert str(captured.value) == "Device session capability is invalid"


@pytest.mark.asyncio
async def test_exchange_binds_one_capability_and_a_bounded_skew_aware_window() -> None:
    repository = FakeRepository()
    service = DeviceSessionService(
        repository=repository,
        clock=FixedClock(),
        session_factory=session_factory(),
    )

    issued = await service.exchange(
        device_credential=long_lived_credential(),
        capability="app.control-plane",
    )

    assert issued.capability is DeviceSessionCapability.APP_CONTROL_PLANE
    assert issued.issued_at == NOW
    assert issued.not_before == NOW - DEVICE_SESSION_CLOCK_SKEW
    assert issued.expires_at == NOW + DEVICE_SESSION_LIFETIME
    assert repository.issues == [
        {
            "presented_credential": cast(
                ParsedDeviceCredential,
                repository.issues[0]["presented_credential"],
            ),
            "pending_session": session_factory().create(),
            "capability": DeviceSessionCapability.APP_CONTROL_PLANE,
            "issued_at": NOW,
            "not_before": NOW - timedelta(seconds=30),
            "expires_at": NOW + timedelta(minutes=5),
        }
    ]


@pytest.mark.asyncio
async def test_authentication_requires_a_strong_exact_capability_and_current_time() -> None:
    repository = FakeRepository()
    service = DeviceSessionService(
        repository=repository,
        clock=FixedClock(NOW + timedelta(minutes=1)),
        session_factory=session_factory(),
    )
    token = session_factory().create().session_token

    authenticated = await service.authenticate(
        session_token=token,
        required_capability=DeviceSessionCapability.EXECUTOR_CONNECT,
    )

    assert authenticated.capability is DeviceSessionCapability.EXECUTOR_CONNECT
    assert repository.authentications == [
        {
            "presented_session": parse_device_session(token),
            "required_capability": DeviceSessionCapability.EXECUTOR_CONNECT,
            "authenticated_at": NOW + timedelta(minutes=1),
        }
    ]
    with pytest.raises(InvalidDeviceSessionCapability):
        await service.authenticate(
            session_token=token,
            required_capability=cast(DeviceSessionCapability, "executor.connect"),
        )


@pytest.mark.asyncio
async def test_service_rejects_invalid_inputs_and_naive_clock_before_repository() -> None:
    repository = FakeRepository()
    service = DeviceSessionService(
        repository=repository,
        clock=FixedClock(NOW.replace(tzinfo=None)),
        session_factory=session_factory(),
    )

    with pytest.raises(InvalidDeviceSessionCapability):
        await service.exchange(
            device_credential=long_lived_credential(),
            capability="*",
        )
    with pytest.raises(RuntimeError, match="Device session clock is invalid"):
        await service.exchange(
            device_credential=long_lived_credential(),
            capability="executor.connect",
        )
    with pytest.raises(InvalidDeviceSession):
        await service.authenticate(
            session_token="private-invalid-session",
            required_capability=DeviceSessionCapability.APP_CONTROL_PLANE,
        )
    assert repository.issues == []
    assert repository.authentications == []


@pytest.mark.asyncio
async def test_repository_rejects_a_session_removed_during_authentication() -> None:
    session = MagicMock(spec=AsyncSession)

    def result(value: object) -> MagicMock:
        query_result = MagicMock()
        query_result.mappings.return_value.one_or_none.return_value = value
        query_result.mappings.return_value.one.return_value = value
        return query_result

    session.execute = AsyncMock(
        side_effect=[
            result(
                {
                    "installation_id": INSTALLATION_ID,
                    "device_credential_id": CREDENTIAL_ID,
                }
            ),
            result(
                {
                    "id": CREDENTIAL_ID,
                    "installation_id": INSTALLATION_ID,
                    "version": 3,
                    "status": "active",
                    "scope": "device.session.exchange",
                }
            ),
            result(None),
        ]
    )
    session.scalar = AsyncMock(return_value="active")
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=session)
    transaction.__aexit__ = AsyncMock(return_value=None)
    database = MagicMock(spec=Database)
    database.session.return_value = transaction
    repository = SqlAlchemyDeviceSessionRepository(database)
    pending = session_factory().create()

    with pytest.raises(DeviceSessionRejected):
        await repository.authenticate(
            presented_session=parse_device_session(pending.session_token),
            required_capability=DeviceSessionCapability.APP_CONTROL_PLANE,
            authenticated_at=NOW,
        )
