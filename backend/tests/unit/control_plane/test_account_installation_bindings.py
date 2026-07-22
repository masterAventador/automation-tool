import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from automation_tool.control_plane.application.account_installation_bindings import (
    AccountBindingChallengeRecord,
    AccountInstallationBindingService,
    AccountInstallationBindingUnavailable,
    BindingChallengeExpired,
    BindingChallengeUsed,
    BindingProofRejected,
    CrossAccountBindingRejected,
    InvalidBindingRequest,
)
from automation_tool.control_plane.application.account_sessions import (
    AuthenticatedAccountSession,
)
from automation_tool.control_plane.application.device_credentials import (
    DeviceCredentialFactory,
    IssuedDeviceCredential,
    PendingDeviceCredential,
)
from automation_tool.control_plane.application.registration import RegisteredInstallation
from automation_tool.control_plane.domain import InstallationId, UserId

NOW = datetime(2026, 7, 23, 1, 0, tzinfo=UTC)
USER_ID = UserId.parse("123e4567-e89b-42d3-a456-426614174000")
OTHER_USER_ID = UserId.parse("223e4567-e89b-42d3-a456-426614174000")


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeAccountSessions:
    def __init__(self) -> None:
        self.user_id = USER_ID
        self.tokens: list[object] = []

    async def authenticate(self, *, access_token: object) -> AuthenticatedAccountSession:
        self.tokens.append(access_token)
        return AuthenticatedAccountSession(
            token_id=UUID("123e4567-e89b-42d3-a456-426614174001"),
            family_id=UUID("123e4567-e89b-42d3-a456-426614174002"),
            user_id=self.user_id,
            credential_version=1,
            expires_at=NOW + timedelta(minutes=10),
        )


class FakeRepository:
    def __init__(self) -> None:
        self.saved: AccountBindingChallengeRecord | None = None
        self.completed: dict[str, object] | None = None
        self.error: Exception | None = None
        installation_id = InstallationId.parse("523e4567-e89b-42d3-a456-426614174000")
        self.result = RegisteredInstallation(
            installation_id=installation_id.uuid,
            status="active",
            revision=1,
            device_credential=IssuedDeviceCredential(
                credential_id=UUID("623e4567-e89b-42d3-a456-426614174000"),
                installation_id=installation_id.uuid,
                credential="atdc1.private",
                version=1,
                scope="device.session.exchange",
            ),
        )

    async def save_challenge(self, challenge: AccountBindingChallengeRecord) -> None:
        self.saved = challenge

    async def complete_challenge(
        self,
        *,
        challenge_id: UUID,
        user_id: UserId,
        signing_payload: bytes,
        signature: bytes,
        completed_at: datetime,
        pending_credential: PendingDeviceCredential,
        request_id: str,
    ) -> RegisteredInstallation:
        self.completed = {
            "challenge_id": challenge_id,
            "user_id": user_id,
            "signing_payload": signing_payload,
            "signature": signature,
            "completed_at": completed_at,
            "pending_credential": pending_credential,
            "request_id": request_id,
        }
        if self.error is not None:
            raise self.error
        return self.result


def service(
    repository: FakeRepository, accounts: FakeAccountSessions
) -> AccountInstallationBindingService:
    ids = iter(
        (
            UUID("323e4567-e89b-42d3-a456-426614174000"),
            UUID("423e4567-e89b-42d3-a456-426614174000"),
        )
    )
    return AccountInstallationBindingService(
        repository=repository,
        account_sessions=accounts,
        clock=FixedClock(),
        nonce_source=lambda length: bytes(range(length)),
        id_source=lambda: next(ids),
        credential_factory=DeviceCredentialFactory(
            secret_source=lambda length: b"c" * length,
            id_source=lambda: next(ids),
        ),
    )


@pytest.mark.asyncio
async def test_challenge_is_bound_to_authenticated_account_and_device_key() -> None:
    repository = FakeRepository()
    accounts = FakeAccountSessions()
    binding = service(repository, accounts)

    issued = await binding.issue_challenge(
        access_token="atas1.private",
        device_public_key=b"d" * 32,
    )

    assert accounts.tokens == ["atas1.private"]
    assert repository.saved is not None
    assert repository.saved.user_id == USER_ID
    assert repository.saved.device_public_key == b"d" * 32
    assert repository.saved.proof_hash == hashlib.sha256(issued.signing_payload).digest()
    assert issued.expires_at == NOW + timedelta(minutes=5)
    assert b'"purpose":"account.installation.bind"' in issued.signing_payload
    assert b'"userId"' not in issued.signing_payload


@pytest.mark.asyncio
async def test_completion_reauthenticates_account_and_creates_fresh_credential() -> None:
    repository = FakeRepository()
    accounts = FakeAccountSessions()
    binding = service(repository, accounts)
    issued = await binding.issue_challenge(
        access_token="atas1.private",
        device_public_key=b"d" * 32,
    )

    result = await binding.complete_binding(
        access_token="atas1.private",
        challenge_id=issued.challenge_id,
        signing_payload=issued.signing_payload,
        signature=b"s" * 64,
        request_id="bind-request",
    )

    assert result == repository.result
    assert accounts.tokens == ["atas1.private", "atas1.private"]
    assert repository.completed is not None
    assert repository.completed["user_id"] == USER_ID
    assert repository.completed["request_id"] == "bind-request"
    pending_credential = repository.completed["pending_credential"]
    assert isinstance(pending_credential, PendingDeviceCredential)
    assert pending_credential.credential.startswith("atdc1.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("device_public_key", b"short"),
        ("challenge_id", UUID("123e4567-e89b-12d3-a456-426614174000")),
        ("signing_payload", b""),
        ("signature", b"short"),
        ("request_id", "invalid request id"),
    ),
)
async def test_noncanonical_inputs_fail_before_repository_completion(
    field: str, value: object
) -> None:
    repository = FakeRepository()
    accounts = FakeAccountSessions()
    binding = service(repository, accounts)
    values: dict[str, object] = {
        "access_token": "atas1.private",
        "challenge_id": UUID("323e4567-e89b-42d3-a456-426614174000"),
        "signing_payload": b"payload",
        "signature": b"s" * 64,
        "request_id": "bind-request",
    }
    if field == "device_public_key":
        with pytest.raises(InvalidBindingRequest):
            await binding.issue_challenge(access_token="atas1.private", device_public_key=value)
    else:
        values[field] = value
        with pytest.raises(InvalidBindingRequest):
            await binding.complete_binding(**values)
    assert repository.completed is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    (
        BindingProofRejected(),
        BindingChallengeExpired(),
        BindingChallengeUsed(),
        CrossAccountBindingRejected(),
    ),
)
async def test_security_failures_are_preserved_without_retry_fallback(error: Exception) -> None:
    repository = FakeRepository()
    repository.error = error
    binding = service(repository, FakeAccountSessions())

    with pytest.raises(type(error)):
        await binding.complete_binding(
            access_token="atas1.private",
            challenge_id=UUID("323e4567-e89b-42d3-a456-426614174000"),
            signing_payload=b"payload",
            signature=b"s" * 64,
            request_id="bind-request",
        )


@pytest.mark.asyncio
async def test_challenge_rejects_invalid_clock_and_random_sources() -> None:
    class InvalidClock:
        def now(self) -> datetime:
            return datetime(2026, 7, 23, 1, 0)

    repository = FakeRepository()
    accounts = FakeAccountSessions()
    credential_factory = DeviceCredentialFactory(
        secret_source=lambda length: b"c" * length,
        id_source=lambda: UUID("423e4567-e89b-42d3-a456-426614174000"),
    )
    invalid_clock = AccountInstallationBindingService(
        repository=repository,
        account_sessions=accounts,
        clock=InvalidClock(),
        credential_factory=credential_factory,
    )
    with pytest.raises(AccountInstallationBindingUnavailable):
        await invalid_clock.issue_challenge(
            access_token="atas1.private", device_public_key=b"d" * 32
        )

    invalid_sources = (
        (lambda _length: b"short", lambda: UUID("323e4567-e89b-42d3-a456-426614174000")),
        (
            lambda length: b"n" * length,
            lambda: UUID("123e4567-e89b-12d3-a456-426614174000"),
        ),
    )
    for nonce_source, id_source in invalid_sources:
        binding = AccountInstallationBindingService(
            repository=repository,
            account_sessions=accounts,
            clock=FixedClock(),
            nonce_source=nonce_source,
            id_source=id_source,
            credential_factory=credential_factory,
        )
        with pytest.raises(AccountInstallationBindingUnavailable):
            await binding.issue_challenge(access_token="atas1.private", device_public_key=b"d" * 32)
