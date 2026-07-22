import asyncio
import base64
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import insert, select

from automation_tool.control_plane.application.account_sessions import AccountSessionRejected
from automation_tool.control_plane.bootstrap.account_operations_cli import main
from automation_tool.control_plane.bootstrap.account_sessions import (
    account_session_service_from_environment,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    account_audit_events,
    account_session_families,
    account_session_tokens,
    device_credentials,
    device_sessions,
    installations,
    users,
)

CAPABILITY = "atoc1." + base64.urlsafe_b64encode(b"e" * 32).rstrip(b"=").decode("ascii")
ACTOR_ID = UUID("223e4567-e89b-42d3-a456-426614174000")


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def invoke_sync(arguments: list[str], payload: dict[str, object]) -> dict[str, object]:
    output = io.StringIO()
    main(arguments, input_stream=io.StringIO(json.dumps(payload)), output_stream=output)
    result = json.loads(output.getvalue())
    assert isinstance(result, dict)
    return result


async def invoke(arguments: list[str], payload: dict[str, object]) -> dict[str, object]:
    return await asyncio.to_thread(invoke_sync, arguments, payload)


async def seed_device(database: Database, user_id: UUID, *, ordinal: int) -> UUID:
    now = datetime.now(UTC)
    installation_id = uuid4()
    credential_id = uuid4()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id,
                device_public_key=bytes([ordinal]) * 32,
                owner_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(
            insert(device_credentials).values(
                id=credential_id,
                installation_id=installation_id,
                version=1,
                scope="device.session.exchange",
                secret_digest=hashlib.sha256(f"credential-{ordinal}".encode()).digest(),
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(
            insert(device_sessions).values(
                id=uuid4(),
                installation_id=installation_id,
                device_credential_id=credential_id,
                credential_version=1,
                capability="app.control-plane",
                secret_digest=hashlib.sha256(f"session-{ordinal}".encode()).digest(),
                created_at=now,
                not_before=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
    return installation_id


@pytest.mark.asyncio
async def test_emergency_revoke_is_atomic_scoped_audited_and_single_winner(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    monkeypatch.setenv("AUTOMATION_TOOL_DATABASE_URL", postgresql_url)
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER", base64url(b"p" * 32))
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION", "1")
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY", base64url(b"f" * 32))
    monkeypatch.setenv(
        "AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST",
        base64url(hashlib.sha256(CAPABILITY.encode("ascii")).digest()),
    )
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID", str(ACTOR_ID))
    target_login = f"emergency-{uuid4().hex}"
    foreign_login = f"foreign-{uuid4().hex}"
    password = "emergency correct horse battery"
    target = await invoke(
        ["create", "--login-name", target_login, "--request-id", "emergency-create"],
        {"capability": CAPABILITY, "password": password},
    )
    foreign = await invoke(
        ["create", "--login-name", foreign_login, "--request-id", "foreign-create"],
        {"capability": CAPABILITY, "password": password},
    )
    target_id = UUID(str(target["userId"]))
    foreign_id = UUID(str(foreign["userId"]))
    database = Database.from_url(postgresql_url)
    sessions = account_session_service_from_environment(database)
    assert sessions is not None
    try:
        target_session = await sessions.login(
            login_name=target_login,
            password=password,
            source_address="127.0.0.1",
            request_id="emergency-login",
        )
        foreign_session = await sessions.login(
            login_name=foreign_login,
            password=password,
            source_address="127.0.0.2",
            request_id="foreign-login",
        )
        target_devices = {
            await seed_device(database, target_id, ordinal=1),
            await seed_device(database, target_id, ordinal=2),
        }
        foreign_device = await seed_device(database, foreign_id, ordinal=3)

        revoked = await invoke(
            [
                "emergency-revoke",
                "--user-id",
                str(target_id),
                "--expected-revision",
                "1",
                "--request-id",
                "emergency-revoke",
            ],
            {"capability": CAPABILITY},
        )
        assert revoked == {
            "userId": str(target_id),
            "status": "disabled",
            "revision": 2,
            "revokedDeviceCount": 2,
        }
        with pytest.raises(SystemExit, match="Account operations command failed"):
            await invoke(
                [
                    "emergency-revoke",
                    "--user-id",
                    str(target_id),
                    "--expected-revision",
                    "1",
                    "--request-id",
                    "emergency-replay",
                ],
                {"capability": CAPABILITY},
            )
        with pytest.raises(SystemExit, match="Account operations command failed"):
            await invoke(
                [
                    "emergency-revoke",
                    "--user-id",
                    str(target_id),
                    "--expected-revision",
                    "2",
                    "--request-id",
                    "emergency-disabled-replay",
                ],
                {"capability": CAPABILITY},
            )
        with pytest.raises(SystemExit, match="Account operations command failed"):
            await invoke(
                [
                    "emergency-revoke",
                    "--user-id",
                    str(uuid4()),
                    "--expected-revision",
                    "1",
                    "--request-id",
                    "emergency-unknown",
                ],
                {"capability": CAPABILITY},
            )

        with pytest.raises(AccountSessionRejected):
            await sessions.authenticate(access_token=target_session.access_token)
        authenticated_foreign = await sessions.authenticate(
            access_token=foreign_session.access_token
        )
        assert authenticated_foreign.user_id.uuid == foreign_id

        async with database.session() as session:
            target_user = (
                (await session.execute(select(users).where(users.c.id == target_id)))
                .mappings()
                .one()
            )
            target_installations = (
                (
                    await session.execute(
                        select(installations).where(installations.c.id.in_(target_devices))
                    )
                )
                .mappings()
                .all()
            )
            foreign_installation = (
                (
                    await session.execute(
                        select(installations).where(installations.c.id == foreign_device)
                    )
                )
                .mappings()
                .one()
            )
            target_credentials = (
                (
                    await session.execute(
                        select(device_credentials).where(
                            device_credentials.c.installation_id.in_(target_devices)
                        )
                    )
                )
                .mappings()
                .all()
            )
            target_device_sessions = (
                (
                    await session.execute(
                        select(device_sessions).where(
                            device_sessions.c.installation_id.in_(target_devices)
                        )
                    )
                )
                .mappings()
                .all()
            )
            account_families = (
                (
                    await session.execute(
                        select(account_session_families).where(
                            account_session_families.c.user_id == target_id
                        )
                    )
                )
                .mappings()
                .all()
            )
            account_tokens = (
                (
                    await session.execute(
                        select(account_session_tokens).where(
                            account_session_tokens.c.user_id == target_id
                        )
                    )
                )
                .mappings()
                .all()
            )
            audits = (
                (
                    await session.execute(
                        select(account_audit_events).where(
                            account_audit_events.c.request_id == "emergency-revoke"
                        )
                    )
                )
                .mappings()
                .all()
            )

        assert target_user["status"] == "disabled"
        assert target_user["credential_version"] == 2
        assert target_user["revision"] == 2
        assert all(
            row["status"] == "revoked" and row["revision"] == 2 for row in target_installations
        )
        assert foreign_installation["status"] == "active"
        assert foreign_installation["revision"] == 1
        assert all(row["status"] == "revoked" and row["revoked_at"] for row in target_credentials)
        assert all(row["revoked_at"] for row in target_device_sessions)
        assert all(row["revoked_at"] for row in account_families)
        assert all(row["revoked_at"] for row in account_tokens)
        assert [row["event_type"] for row in audits].count("device.revoked") == 2
        assert {row["event_type"] for row in audits} >= {
            "account.disabled",
            "session.all_revoked",
            "device.revoked",
        }
        assert all(row["actor_kind"] == "operations" for row in audits)
        assert all(row["actor_id"] == ACTOR_ID for row in audits)
        assert all(row["reason_code"] == "operations_emergency_revoked" for row in audits)

        empty = await invoke(
            ["create", "--login-name", f"empty-{uuid4().hex}", "--request-id", "empty-create"],
            {"capability": CAPABILITY, "password": password},
        )
        empty_revoked = await invoke(
            [
                "emergency-revoke",
                "--user-id",
                str(empty["userId"]),
                "--expected-revision",
                "1",
                "--request-id",
                "empty-emergency",
            ],
            {"capability": CAPABILITY},
        )
        assert empty_revoked["revokedDeviceCount"] == 0
    finally:
        await database.close()
