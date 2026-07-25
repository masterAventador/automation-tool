"""The loopback grant must register through the one real registration API."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import AlembicRunner
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete, func, select

from automation_tool.control_plane import create_app
from automation_tool.control_plane.bootstrap.local_provisioning import (
    HANDOFF_FILE_NAME,
    LOCAL_BOOTSTRAP_LIFETIME,
    LOCAL_ENVIRONMENT_ID,
    provision_local_registration_bootstrap,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    installation_registration_challenges,
    installations,
)


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def error_code(response: Response) -> object:
    return cast(dict[str, Any], response.json())["error"]["code"]


def handed_off_token(directory: Path) -> str:
    document = json.loads((directory / HANDOFF_FILE_NAME).read_bytes().decode("ascii"))
    return cast(str, document["token"])


async def reset_registration_data(database_url: str) -> None:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            await session.execute(delete(installation_registration_challenges))
            await session.execute(delete(device_sessions))
            await session.execute(delete(device_credentials))
            await session.execute(delete(installations))
    finally:
        await database.close()


async def installation_count(database_url: str) -> int:
    database = Database.from_url(database_url)
    try:
        async with database.session() as session:
            return int(await session.scalar(select(func.count()).select_from(installations)) or 0)
    finally:
        await database.close()


def issue_challenge(client: TestClient, *, token: str, device_public_key: bytes) -> Response:
    return client.post(
        "/api/v1/installations/registration-challenges",
        headers={"authorization": f"Bearer {token}"},
        json={
            "environmentId": LOCAL_ENVIRONMENT_ID,
            "devicePublicKey": base64url(device_public_key),
        },
    )


def complete_registration(
    client: TestClient,
    *,
    token: str,
    challenge: dict[str, object],
    device_signing_key: Ed25519PrivateKey,
) -> Response:
    signing_payload = decode_base64url(str(challenge["signingPayload"]))
    return client.post(
        "/api/v1/installations",
        headers={"authorization": f"Bearer {token}"},
        json={
            "challengeId": challenge["challengeId"],
            "environmentId": LOCAL_ENVIRONMENT_ID,
            "signingPayload": challenge["signingPayload"],
            "signature": base64url(device_signing_key.sign(signing_payload)),
        },
    )


@pytest.fixture
def loopback_control_plane(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    asyncio.run(reset_registration_data(postgresql_url))
    monkeypatch.setenv("AUTOMATION_TOOL_DATABASE_URL", postgresql_url)
    monkeypatch.delenv("AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID", raising=False)
    monkeypatch.delenv("AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY", raising=False)


def test_local_grant_registers_one_installation_through_the_real_api(
    loopback_control_plane: None,
    postgresql_url: str,
    tmp_path: Path,
) -> None:
    provisioned = provision_local_registration_bootstrap(tmp_path)
    app = create_app(local_registration_bootstrap=provisioned)
    device_key = Ed25519PrivateKey.generate()

    with TestClient(app) as client:
        challenge = issue_challenge(
            client,
            token=handed_off_token(tmp_path),
            device_public_key=device_key.public_key().public_bytes_raw(),
        )
        assert challenge.status_code == 201, challenge.text
        registered = complete_registration(
            client,
            token=handed_off_token(tmp_path),
            challenge=cast(dict[str, object], challenge.json()),
            device_signing_key=device_key,
        )

    assert registered.status_code == 201, registered.text
    body = cast(dict[str, object], registered.json())
    credential = cast(dict[str, object], body["deviceCredential"])
    assert str(credential["credential"]).startswith("atdc1.")
    assert asyncio.run(installation_count(postgresql_url)) == 1
    asyncio.run(app.state.database.close())


def test_reregistering_the_same_device_key_is_refused_as_a_conflict(
    loopback_control_plane: None,
    postgresql_url: str,
    tmp_path: Path,
) -> None:
    """The brick state: a stored-credential failure leaves only this answer."""
    provisioned = provision_local_registration_bootstrap(tmp_path)
    app = create_app(local_registration_bootstrap=provisioned)
    device_key = Ed25519PrivateKey.generate()
    token = handed_off_token(tmp_path)

    with TestClient(app) as client:
        first = complete_registration(
            client,
            token=token,
            challenge=cast(
                dict[str, object],
                issue_challenge(
                    client,
                    token=token,
                    device_public_key=device_key.public_key().public_bytes_raw(),
                ).json(),
            ),
            device_signing_key=device_key,
        )
        assert first.status_code == 201, first.text
        retry = complete_registration(
            client,
            token=token,
            challenge=cast(
                dict[str, object],
                issue_challenge(
                    client,
                    token=token,
                    device_public_key=device_key.public_key().public_bytes_raw(),
                ).json(),
            ),
            device_signing_key=device_key,
        )

    assert retry.status_code == 409
    assert error_code(retry) == "installation_exists"
    assert asyncio.run(installation_count(postgresql_url)) == 1
    asyncio.run(app.state.database.close())


def test_a_fresh_device_key_recovers_after_the_conflict(
    loopback_control_plane: None,
    postgresql_url: str,
    tmp_path: Path,
) -> None:
    """Recovery from the conflict is a new device identity, not a new grant."""
    provisioned = provision_local_registration_bootstrap(tmp_path)
    app = create_app(local_registration_bootstrap=provisioned)
    token = handed_off_token(tmp_path)

    with TestClient(app) as client:
        for device_key in (Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()):
            completed = complete_registration(
                client,
                token=token,
                challenge=cast(
                    dict[str, object],
                    issue_challenge(
                        client,
                        token=token,
                        device_public_key=device_key.public_key().public_bytes_raw(),
                    ).json(),
                ),
                device_signing_key=device_key,
            )
            assert completed.status_code == 201, completed.text

    assert asyncio.run(installation_count(postgresql_url)) == 2
    asyncio.run(app.state.database.close())


def test_an_expired_local_grant_registers_nothing(
    loopback_control_plane: None,
    postgresql_url: str,
    tmp_path: Path,
) -> None:
    stale = datetime.now(UTC) - LOCAL_BOOTSTRAP_LIFETIME - timedelta(minutes=1)
    provisioned = provision_local_registration_bootstrap(tmp_path, now=stale)
    app = create_app(local_registration_bootstrap=provisioned)
    device_key = Ed25519PrivateKey.generate()

    with TestClient(app) as client:
        denied = issue_challenge(
            client,
            token=handed_off_token(tmp_path),
            device_public_key=device_key.public_key().public_bytes_raw(),
        )

    assert denied.status_code == 403
    assert error_code(denied) == "bootstrap_denied"
    assert asyncio.run(installation_count(postgresql_url)) == 0
    asyncio.run(app.state.database.close())


def test_a_grant_from_a_previous_start_no_longer_registers(
    loopback_control_plane: None,
    postgresql_url: str,
    tmp_path: Path,
) -> None:
    provision_local_registration_bootstrap(tmp_path)
    stale_token = handed_off_token(tmp_path)
    app = create_app(local_registration_bootstrap=provision_local_registration_bootstrap(tmp_path))
    device_key = Ed25519PrivateKey.generate()

    with TestClient(app) as client:
        rejected = issue_challenge(
            client,
            token=stale_token,
            device_public_key=device_key.public_key().public_bytes_raw(),
        )

    assert rejected.status_code == 401
    assert error_code(rejected) == "bootstrap_invalid"
    assert asyncio.run(installation_count(postgresql_url)) == 0
    asyncio.run(app.state.database.close())
