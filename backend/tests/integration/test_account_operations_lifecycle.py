import asyncio
import base64
import hashlib
import io
import json
from uuid import UUID, uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import select

from automation_tool.control_plane.application.account_sessions import (
    AccountAuthenticationRejected,
    AccountSessionRejected,
)
from automation_tool.control_plane.bootstrap.account_operations_cli import main
from automation_tool.control_plane.bootstrap.account_sessions import (
    account_session_service_from_environment,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    account_audit_events,
)

CAPABILITY = "atoc1." + base64.urlsafe_b64encode(b"o" * 32).rstrip(b"=").decode("ascii")
ACTOR_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def invoke_sync(arguments: list[str], payload: dict[str, object]) -> dict[str, object]:
    output = io.StringIO()
    main(
        arguments,
        input_stream=io.StringIO(json.dumps(payload)),
        output_stream=output,
    )
    result = json.loads(output.getvalue())
    assert isinstance(result, dict)
    return result


async def invoke(arguments: list[str], payload: dict[str, object]) -> dict[str, object]:
    return await asyncio.to_thread(invoke_sync, arguments, payload)


@pytest.mark.asyncio
async def test_authenticated_operations_create_disable_restore_and_issue_reset(
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
    login_name = f"demo-{uuid4().hex}"
    original_password = "original correct horse battery"
    replacement_password = "replacement correct horse battery"

    created = await invoke(
        ["create", "--login-name", login_name, "--request-id", "ops-create"],
        {"capability": CAPABILITY, "password": original_password},
    )
    user_id = created["userId"]
    assert created["status"] == "active"
    assert created["revision"] == 1

    database = Database.from_url(postgresql_url)
    sessions = account_session_service_from_environment(database)
    assert sessions is not None
    try:
        signed_in = await sessions.login(
            login_name=login_name,
            password=original_password,
            source_address="127.0.0.1",
            request_id="before-disable-login",
        )
        disabled = await invoke(
            [
                "disable",
                "--user-id",
                str(user_id),
                "--expected-revision",
                "1",
                "--request-id",
                "ops-disable",
            ],
            {"capability": CAPABILITY},
        )
        assert disabled["status"] == "disabled"
        assert disabled["revision"] == 2
        with pytest.raises(AccountSessionRejected):
            await sessions.authenticate(access_token=signed_in.access_token)

        restored = await invoke(
            [
                "restore",
                "--user-id",
                str(user_id),
                "--expected-revision",
                "2",
                "--request-id",
                "ops-restore",
            ],
            {"capability": CAPABILITY},
        )
        assert restored["status"] == "active"
        reset = await invoke(
            ["reset", "--login-name", login_name, "--request-id", "ops-reset"],
            {"capability": CAPABILITY},
        )
        assert str(reset["recoveryToken"]).startswith("atrp1.")
        await sessions.recover_password(
            recovery_token=reset["recoveryToken"],
            new_password=replacement_password,
            request_id="consume-reset",
        )
        with pytest.raises(AccountAuthenticationRejected):
            await sessions.login(
                login_name=login_name,
                password=original_password,
                source_address="127.0.0.2",
                request_id="old-password-login",
            )
        renewed = await sessions.login(
            login_name=login_name,
            password=replacement_password,
            source_address="127.0.0.3",
            request_id="new-password-login",
        )
        assert str(renewed.account.user_id) == user_id

        async with database.session() as session:
            operations_audits = (
                await session.execute(
                    select(
                        account_audit_events.c.event_type,
                        account_audit_events.c.actor_kind,
                        account_audit_events.c.actor_id,
                    ).where(
                        account_audit_events.c.request_id.in_(
                            ("ops-create", "ops-disable", "ops-restore", "ops-reset")
                        )
                    )
                )
            ).all()
        assert {row.event_type for row in operations_audits} >= {
            "account.created",
            "account.disabled",
            "account.enabled",
            "recovery.issued",
        }
        assert all(row.actor_kind == "operations" for row in operations_audits)
        assert all(row.actor_id == ACTOR_ID for row in operations_audits)
    finally:
        await database.close()
