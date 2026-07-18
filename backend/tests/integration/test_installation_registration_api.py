import asyncio
import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from conftest import AlembicRunner
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete, func, select

from automation_tool.control_plane import create_app
from automation_tool.control_plane.application.registration import (
    CHALLENGE_LIFETIME,
    InstallationRegistrationService,
    RegistrationChallengeExpired,
    RegistrationChallengeUsed,
    RegistrationProofRejected,
)
from automation_tool.control_plane.domain import DemoEnvironmentId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    installation_registration_challenges,
    installations,
)
from automation_tool.control_plane.infrastructure.database.registration import (
    SqlAlchemyInstallationRegistrationRepository,
)
from automation_tool.control_plane.infrastructure.security.bootstrap_tokens import (
    Ed25519BootstrapTokenVerifier,
)

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def signed_bootstrap_token(
    private_key: Ed25519PrivateKey,
    *,
    environment_id: str = "demo-cn-1",
    not_before: datetime = NOW - timedelta(minutes=1),
    expires_at: datetime = NOW + timedelta(hours=1),
) -> str:
    claims = {
        "environmentId": environment_id,
        "expiresAt": int(expires_at.timestamp()),
        "notBefore": int(not_before.timestamp()),
        "purpose": "installation.register",
        "version": 1,
    }
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("ascii")
    payload_segment = base64url(payload)
    signing_input = f"atb1.{payload_segment}".encode("ascii")
    return f"atb1.{payload_segment}.{base64url(private_key.sign(signing_input))}"


@dataclass
class MutableClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


def fixed_nonce_source(value: bytes) -> Callable[[int], bytes]:
    def generate(length: int) -> bytes:
        assert length == 32
        return value

    return generate


def registration_service(
    database: Database,
    bootstrap_signing_key: Ed25519PrivateKey,
    clock: MutableClock,
) -> InstallationRegistrationService:
    return InstallationRegistrationService(
        repository=SqlAlchemyInstallationRegistrationRepository(database),
        bootstrap_verifier=Ed25519BootstrapTokenVerifier(
            bootstrap_signing_key.public_key().public_bytes_raw()
        ),
        expected_environment_id=DemoEnvironmentId.parse("demo-cn-1"),
        clock=clock,
        nonce_source=fixed_nonce_source(bytes(range(32))),
    )


async def reset_registration_data(database_url: str) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            await session.execute(delete(installation_registration_challenges))
            await session.execute(delete(installations))
    finally:
        await database.close()


async def persisted_registration_counts(database_url: str) -> tuple[int, int, int]:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            installation_count = await session.scalar(
                select(func.count()).select_from(installations)
            )
            challenge_count = await session.scalar(
                select(func.count()).select_from(installation_registration_challenges)
            )
            consumed_count = await session.scalar(
                select(func.count())
                .select_from(installation_registration_challenges)
                .where(installation_registration_challenges.c.consumed_at.is_not(None))
            )
        return int(installation_count or 0), int(challenge_count or 0), int(consumed_count or 0)
    finally:
        await database.close()


def issue_challenge(
    client: TestClient,
    *,
    token: str,
    device_public_key: bytes,
    environment_id: str = "demo-cn-1",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/installations/registration-challenges",
        headers={"authorization": f"Bearer {token}"},
        json={
            "environmentId": environment_id,
            "devicePublicKey": base64url(device_public_key),
        },
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = cast(dict[str, object], response.json())
    assert set(body) == {"challengeId", "expiresAt", "signingPayload"}
    signing_payload = body["signingPayload"]
    assert isinstance(signing_payload, str)
    assert len(decode_base64url(signing_payload)) > 32
    return body


def complete_registration(
    client: TestClient,
    *,
    token: str,
    challenge: dict[str, object],
    device_signing_key: Ed25519PrivateKey,
    environment_id: str = "demo-cn-1",
) -> Response:
    signing_payload = decode_base64url(str(challenge["signingPayload"]))
    return client.post(
        "/api/v1/installations",
        headers={"authorization": f"Bearer {token}"},
        json={
            "challengeId": challenge["challengeId"],
            "environmentId": environment_id,
            "signingPayload": challenge["signingPayload"],
            "signature": base64url(device_signing_key.sign(signing_payload)),
        },
    )


def test_signed_registration_creates_one_installation_and_replay_is_rejected(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    asyncio.run(reset_registration_data(postgresql_url))
    database = Database.from_url(postgresql_url)
    bootstrap_key = Ed25519PrivateKey.generate()
    device_key = Ed25519PrivateKey.generate()
    token = signed_bootstrap_token(bootstrap_key)
    app = create_app(
        database=database,
        registration_service=registration_service(database, bootstrap_key, MutableClock(NOW)),
    )

    with TestClient(app) as client:
        challenge = issue_challenge(
            client,
            token=token,
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        registered = complete_registration(
            client,
            token=token,
            challenge=challenge,
            device_signing_key=device_key,
        )
        replay = complete_registration(
            client,
            token=token,
            challenge=challenge,
            device_signing_key=device_key,
        )

    assert registered.status_code == 201
    assert set(registered.json()) == {"installationId", "revision", "status"}
    assert registered.json()["status"] == "active"
    assert registered.json()["revision"] == 1
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "registration_challenge_used"
    assert asyncio.run(persisted_registration_counts(postgresql_url)) == (1, 1, 1)


def test_impersonation_and_cross_bootstrap_attempts_do_not_consume_the_challenge(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    asyncio.run(reset_registration_data(postgresql_url))
    database = Database.from_url(postgresql_url)
    bootstrap_key = Ed25519PrivateKey.generate()
    device_key = Ed25519PrivateKey.generate()
    attacker_device_key = Ed25519PrivateKey.generate()
    first_token = signed_bootstrap_token(bootstrap_key)
    other_valid_token = signed_bootstrap_token(
        bootstrap_key,
        not_before=NOW - timedelta(seconds=30),
    )
    app = create_app(
        database=database,
        registration_service=registration_service(database, bootstrap_key, MutableClock(NOW)),
    )

    with TestClient(app) as client:
        challenge = issue_challenge(
            client,
            token=first_token,
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        wrong_device = complete_registration(
            client,
            token=first_token,
            challenge=challenge,
            device_signing_key=attacker_device_key,
        )
        tampered_challenge = {
            **challenge,
            "signingPayload": base64url(b"tampered-signing-payload"),
        }
        wrong_payload = complete_registration(
            client,
            token=first_token,
            challenge=tampered_challenge,
            device_signing_key=device_key,
        )
        wrong_bootstrap = complete_registration(
            client,
            token=other_valid_token,
            challenge=challenge,
            device_signing_key=device_key,
        )
        legitimate = complete_registration(
            client,
            token=first_token,
            challenge=challenge,
            device_signing_key=device_key,
        )

    for rejected in (wrong_device, wrong_payload, wrong_bootstrap):
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "registration_proof_invalid"
        assert first_token not in rejected.text
        assert other_valid_token not in rejected.text
    assert legitimate.status_code == 201
    assert asyncio.run(persisted_registration_counts(postgresql_url)) == (1, 1, 1)


def test_missing_challenge_and_duplicate_device_key_are_stable_conflicts(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    asyncio.run(reset_registration_data(postgresql_url))
    database = Database.from_url(postgresql_url)
    bootstrap_key = Ed25519PrivateKey.generate()
    device_key = Ed25519PrivateKey.generate()
    token = signed_bootstrap_token(bootstrap_key)
    app = create_app(
        database=database,
        registration_service=registration_service(database, bootstrap_key, MutableClock(NOW)),
    )

    with TestClient(app) as client:
        missing = complete_registration(
            client,
            token=token,
            challenge={
                "challengeId": str(uuid4()),
                "expiresAt": (NOW + CHALLENGE_LIFETIME).isoformat(),
                "signingPayload": base64url(b"unknown-challenge"),
            },
            device_signing_key=device_key,
        )
        first_challenge = issue_challenge(
            client,
            token=token,
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        first = complete_registration(
            client,
            token=token,
            challenge=first_challenge,
            device_signing_key=device_key,
        )
        duplicate_challenge = issue_challenge(
            client,
            token=token,
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        duplicate = complete_registration(
            client,
            token=token,
            challenge=duplicate_challenge,
            device_signing_key=device_key,
        )

    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "registration_proof_invalid"
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "installation_exists"
    assert asyncio.run(persisted_registration_counts(postgresql_url)) == (1, 2, 1)


def test_expired_challenge_is_rejected_without_installation_or_consumption(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    asyncio.run(reset_registration_data(postgresql_url))
    database = Database.from_url(postgresql_url)
    bootstrap_key = Ed25519PrivateKey.generate()
    device_key = Ed25519PrivateKey.generate()
    clock = MutableClock(NOW)
    token = signed_bootstrap_token(bootstrap_key)
    app = create_app(
        database=database,
        registration_service=registration_service(database, bootstrap_key, clock),
    )

    with TestClient(app) as client:
        challenge = issue_challenge(
            client,
            token=token,
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        clock.current = NOW + CHALLENGE_LIFETIME
        expired = complete_registration(
            client,
            token=token,
            challenge=challenge,
            device_signing_key=device_key,
        )

    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "registration_challenge_expired"
    assert asyncio.run(persisted_registration_counts(postgresql_url)) == (0, 1, 0)


def test_missing_invalid_or_cross_environment_bootstrap_is_rejected_before_persistence(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    asyncio.run(reset_registration_data(postgresql_url))
    database = Database.from_url(postgresql_url)
    bootstrap_key = Ed25519PrivateKey.generate()
    device_key = Ed25519PrivateKey.generate()
    token = signed_bootstrap_token(bootstrap_key)
    app = create_app(
        database=database,
        registration_service=registration_service(database, bootstrap_key, MutableClock(NOW)),
    )
    body = {
        "environmentId": "demo-cn-1",
        "devicePublicKey": base64url(device_key.public_key().public_bytes_raw()),
    }

    with TestClient(app) as client:
        missing = client.post("/api/v1/installations/registration-challenges", json=body)
        malformed = client.post(
            "/api/v1/installations/registration-challenges",
            headers={"authorization": "Bearer private-invalid-token"},
            json=body,
        )
        cross_environment = client.post(
            "/api/v1/installations/registration-challenges",
            headers={"authorization": f"Bearer {token}"},
            json={**body, "environmentId": "demo-cn-2"},
        )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "bootstrap_invalid"
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "bootstrap_invalid"
    assert "private-invalid-token" not in malformed.text
    assert cross_environment.status_code == 403
    assert cross_environment.json()["error"]["code"] == "bootstrap_denied"
    assert asyncio.run(persisted_registration_counts(postgresql_url)) == (0, 0, 0)


@pytest.mark.asyncio
async def test_concurrent_completion_allows_exactly_one_transaction(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    await reset_registration_data(postgresql_url)
    database = Database.from_url(postgresql_url)
    bootstrap_key = Ed25519PrivateKey.generate()
    device_key = Ed25519PrivateKey.generate()
    token = signed_bootstrap_token(bootstrap_key)
    service = registration_service(database, bootstrap_key, MutableClock(NOW))
    try:
        challenge = await service.issue_challenge(
            bootstrap_token=token,
            environment_id="demo-cn-1",
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        signature = device_key.sign(challenge.signing_payload)

        results = await asyncio.gather(
            *(
                service.complete_registration(
                    bootstrap_token=token,
                    environment_id="demo-cn-1",
                    challenge_id=challenge.challenge_id,
                    signing_payload=challenge.signing_payload,
                    signature=signature,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
    finally:
        await database.close()

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, RegistrationChallengeUsed) for result in results) == 1
    assert await persisted_registration_counts(postgresql_url) == (1, 1, 1)


@pytest.mark.asyncio
async def test_repository_rejects_environment_mismatch_without_consuming_challenge(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    await reset_registration_data(postgresql_url)
    database = Database.from_url(postgresql_url)
    bootstrap_key = Ed25519PrivateKey.generate()
    device_key = Ed25519PrivateKey.generate()
    token = signed_bootstrap_token(bootstrap_key)
    verifier = Ed25519BootstrapTokenVerifier(bootstrap_key.public_key().public_bytes_raw())
    repository = SqlAlchemyInstallationRegistrationRepository(database)
    service = registration_service(database, bootstrap_key, MutableClock(NOW))
    try:
        challenge = await service.issue_challenge(
            bootstrap_token=token,
            environment_id="demo-cn-1",
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        with pytest.raises(RegistrationProofRejected):
            await repository.complete_challenge(
                challenge_id=challenge.challenge_id,
                environment_id=DemoEnvironmentId.parse("demo-cn-2"),
                bootstrap_fingerprint=verifier.verify(token).fingerprint,
                signing_payload=challenge.signing_payload,
                signature=device_key.sign(challenge.signing_payload),
                completed_at=NOW,
            )
        completed = await service.complete_registration(
            bootstrap_token=token,
            environment_id="demo-cn-1",
            challenge_id=challenge.challenge_id,
            signing_payload=challenge.signing_payload,
            signature=device_key.sign(challenge.signing_payload),
        )
    finally:
        await database.close()

    assert completed.status == "active"
    assert await persisted_registration_counts(postgresql_url) == (1, 1, 1)


@pytest.mark.asyncio
async def test_repository_explicitly_rejects_missing_expired_and_invalid_signature(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    await reset_registration_data(postgresql_url)
    database = Database.from_url(postgresql_url)
    bootstrap_key = Ed25519PrivateKey.generate()
    device_key = Ed25519PrivateKey.generate()
    token = signed_bootstrap_token(bootstrap_key)
    verifier = Ed25519BootstrapTokenVerifier(bootstrap_key.public_key().public_bytes_raw())
    fingerprint = verifier.verify(token).fingerprint
    environment_id = DemoEnvironmentId.parse("demo-cn-1")
    repository = SqlAlchemyInstallationRegistrationRepository(database)
    service = registration_service(database, bootstrap_key, MutableClock(NOW))
    try:
        with pytest.raises(RegistrationProofRejected):
            await repository.complete_challenge(
                challenge_id=uuid4(),
                environment_id=environment_id,
                bootstrap_fingerprint=fingerprint,
                signing_payload=b"missing",
                signature=b"s" * 64,
                completed_at=NOW,
            )

        expired = await service.issue_challenge(
            bootstrap_token=token,
            environment_id="demo-cn-1",
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        with pytest.raises(RegistrationChallengeExpired):
            await repository.complete_challenge(
                challenge_id=expired.challenge_id,
                environment_id=environment_id,
                bootstrap_fingerprint=fingerprint,
                signing_payload=expired.signing_payload,
                signature=device_key.sign(expired.signing_payload),
                completed_at=expired.expires_at,
            )

        invalid_signature = await service.issue_challenge(
            bootstrap_token=token,
            environment_id="demo-cn-1",
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        with pytest.raises(RegistrationProofRejected):
            await repository.complete_challenge(
                challenge_id=invalid_signature.challenge_id,
                environment_id=environment_id,
                bootstrap_fingerprint=fingerprint,
                signing_payload=invalid_signature.signing_payload,
                signature=b"s" * 64,
                completed_at=NOW,
            )
    finally:
        await database.close()

    assert await persisted_registration_counts(postgresql_url) == (0, 2, 0)
