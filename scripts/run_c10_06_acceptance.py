#!/usr/bin/env python3
"""Verify C10-06 Demo account operations through an isolated PostgreSQL stack."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import time
import tomllib
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
ROLE_SQL = REPOSITORY_ROOT / "deploy" / "operations" / "role.sql"
POSTGRES_IMAGE = "postgres:18.4-bookworm"
RUNTIME_UID = 65532
FILE_ONLY_MODE = "AUTOMATION_TOOL_RUNTIME_SECRET_MODE=files"

VOLUME_WRITER = r"""import json
import os
import sys

allowed = {
    "database-url",
    "account-password-pepper",
    "account-fingerprint-key",
    "account-operations-capability-digest",
    "action-authorization-private-key",
    "postgres-password",
}
payload = json.load(sys.stdin)
if not isinstance(payload, dict) or not payload or not set(payload) <= allowed:
    raise SystemExit(2)
for name, value in payload.items():
    if not isinstance(value, str) or not value:
        raise SystemExit(2)
    descriptor = os.open(f"/target/{name}", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fchown(descriptor, 65532, 65532)
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)
"""

HTTP_PROBE = r"""import json
import sys
import urllib.error
import urllib.request

payload = json.load(sys.stdin)
operation = payload.pop("operation")
if operation == "login":
    path = "/api/v1/account-sessions"
    body = {"loginName": payload["loginName"], "password": payload["password"]}
    headers = {"content-type": "application/json", "x-request-id": payload["requestId"]}
else:
    path = "/api/v1/account-password/recovery"
    body = {"newPassword": payload["newPassword"]}
    headers = {
        "authorization": "Bearer " + payload["recoveryToken"],
        "content-type": "application/json",
        "x-request-id": payload["requestId"],
    }
request = urllib.request.Request(
    "http://127.0.0.1:8000" + path,
    data=json.dumps(body, separators=(",", ":")).encode(),
    headers=headers,
    method="POST",
)
try:
    response = urllib.request.urlopen(request, timeout=10)
except urllib.error.HTTPError as error:
    encoded = error.read()
    try:
        code = json.loads(encoded).get("error", {}).get("code")
    except (json.JSONDecodeError, AttributeError):
        code = None
    print(json.dumps({"status": error.code, "errorCode": code}, sort_keys=True))
else:
    encoded = response.read()
    if operation == "login":
        account = json.loads(encoded)["account"]
        projection = {"status": response.status, "userId": account["userId"]}
    else:
        projection = {"status": response.status}
    print(json.dumps(projection, sort_keys=True))
"""

SEED_DEVICES = r"""import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import insert

from automation_tool.control_plane.bootstrap.database import database_from_environment
from automation_tool.control_plane.infrastructure.database import (
    device_credentials,
    device_sessions,
    installations,
)

payload = json.load(sys.stdin)


async def main():
    database = database_from_environment()
    result = {"target": [], "foreign": []}
    try:
        async with database.session() as session:
            ordinal = 0
            for owner, count in (("target", 2), ("foreign", 1)):
                for _index in range(count):
                    ordinal += 1
                    now = datetime.now(UTC)
                    installation_id = uuid4()
                    credential_id = uuid4()
                    await session.execute(insert(installations).values(
                        id=installation_id,
                        device_public_key=hashlib.sha256(f"device-{ordinal}".encode()).digest(),
                        owner_user_id=UUID(payload[owner]),
                        created_at=now,
                        updated_at=now,
                    ))
                    await session.execute(insert(device_credentials).values(
                        id=credential_id,
                        installation_id=installation_id,
                        version=1,
                        scope="device.session.exchange",
                        secret_digest=hashlib.sha256(f"credential-{ordinal}".encode()).digest(),
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ))
                    await session.execute(insert(device_sessions).values(
                        id=uuid4(),
                        installation_id=installation_id,
                        device_credential_id=credential_id,
                        credential_version=1,
                        capability="app.control-plane",
                        secret_digest=hashlib.sha256(f"session-{ordinal}".encode()).digest(),
                        created_at=now,
                        not_before=now,
                        expires_at=now + timedelta(minutes=5),
                    ))
                    result[owner].append(str(installation_id))
    finally:
        await database.close()
    print(json.dumps(result, sort_keys=True))


asyncio.run(main())
"""

VERIFY_PROJECTION = r"""import asyncio
import json
import sys
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.bootstrap.database import database_from_environment
from automation_tool.control_plane.infrastructure.database import (
    account_audit_events,
    account_session_families,
    account_session_tokens,
    device_credentials,
    device_sessions,
    installations,
    users,
)

payload = json.load(sys.stdin)
target_id = UUID(payload["target"])
foreign_device = UUID(payload["foreignDevice"])


async def denied(database, statement):
    try:
        async with database.session() as session:
            await session.execute(text(statement))
    except SQLAlchemyError:
        return True
    return False


async def main():
    database = database_from_environment()
    try:
        async with database.session() as session:
            user = (await session.execute(
                select(users).where(users.c.id == target_id)
            )).mappings().one()
            target_installations = (await session.execute(
                select(installations).where(installations.c.owner_user_id == target_id)
            )).mappings().all()
            target_ids = [row["id"] for row in target_installations]
            active_credentials = await session.scalar(select(func.count()).select_from(
                device_credentials
            ).where(
                device_credentials.c.installation_id.in_(target_ids),
                device_credentials.c.status == "active",
            ))
            live_device_sessions = await session.scalar(select(func.count()).select_from(
                device_sessions
            ).where(
                device_sessions.c.installation_id.in_(target_ids),
                device_sessions.c.revoked_at.is_(None),
            ))
            live_account_families = await session.scalar(select(func.count()).select_from(
                account_session_families
            ).where(
                account_session_families.c.user_id == target_id,
                account_session_families.c.revoked_at.is_(None),
            ))
            live_account_tokens = await session.scalar(select(func.count()).select_from(
                account_session_tokens
            ).where(
                account_session_tokens.c.user_id == target_id,
                account_session_tokens.c.revoked_at.is_(None),
            ))
            foreign = (await session.execute(select(installations).where(
                installations.c.id == foreign_device
            ))).mappings().one()
            events = (await session.execute(select(
                account_audit_events.c.event_type,
                account_audit_events.c.actor_kind,
                account_audit_events.c.reason_code,
            ).where(
                account_audit_events.c.request_id == "c10-06-emergency"
            ))).all()
            readable_users = await session.scalar(select(func.count()).select_from(users))
        event_types = [row.event_type for row in events]
        projection = {
            "account.disabled": user["status"] == "disabled",
            "credentialVersionAdvanced": user["credential_version"] == 2,
            "revisionAdvanced": user["revision"] == 2,
            "revokedDeviceCount": sum(
                row["status"] == "revoked" for row in target_installations
            ),
            "deviceCredentialsRevoked": active_credentials == 0,
            "deviceSessionsRevoked": live_device_sessions == 0,
            "session.all_revoked": live_account_families == 0 and live_account_tokens == 0,
            "device.revoked": event_types.count("device.revoked"),
            "auditTypes": sorted(set(event_types)),
            "auditOperationsOnly": all(
                row.actor_kind == "operations"
                and row.reason_code == "operations_emergency_revoked"
                for row in events
            ),
            "foreignDeviceActive": foreign["status"] == "active",
            "usersReadable": isinstance(readable_users, int) and readable_users >= 2,
            "ddlDenied": await denied(database, "CREATE TABLE c10_06_forbidden(id integer)"),
            "taskDataDenied": await denied(database, "SELECT id FROM tasks LIMIT 1"),
        }
    finally:
        await database.close()
    print(json.dumps(projection, sort_keys=True))


asyncio.run(main())
"""


class AcceptanceFailure(RuntimeError):
    """Fixed failure that cannot reflect a generated secret."""


def run(
    arguments: Sequence[str],
    *,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=REPOSITORY_ROOT,
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise AcceptanceFailure("C10-06 container command failed")
    return result


def inspect(kind: str, identity: str) -> dict[str, Any]:
    payload = json.loads(run(["docker", kind, "inspect", identity]).stdout)
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise AcceptanceFailure("C10-06 Docker inspection is invalid")
    return cast(dict[str, Any], payload[0])


def wait_for_healthy(container: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = inspect("container", container).get("State")
        if not isinstance(state, dict):
            raise AcceptanceFailure("C10-06 container state is invalid")
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status") == "healthy":
            return
        if state.get("Running") is False:
            raise AcceptanceFailure("C10-06 container exited before health")
        time.sleep(0.5)
    raise AcceptanceFailure("C10-06 container health timed out")


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def write_volume(*, image: str, volume: str, values: Mapping[str, str]) -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--user",
            "0:0",
            "--mount",
            f"type=volume,source={volume},target=/target",
            "--entrypoint",
            "python",
            image,
            "-c",
            VOLUME_WRITER,
        ],
        input_text=json.dumps(dict(values), separators=(",", ":")),
    )


def common_job_arguments(*, network: str, volume: str, actor_id: UUID) -> list[str]:
    return [
        "--network",
        network,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--security-opt",
        "no-new-privileges=true",
        "--cap-drop",
        "ALL",
        "--user",
        f"{RUNTIME_UID}:{RUNTIME_UID}",
        "--mount",
        f"type=volume,source={volume},target=/run/secrets,readonly",
        "--env",
        "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION=1",
        "--env",
        f"AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID={actor_id}",
    ]


def operation(
    *,
    image: str,
    network: str,
    volume: str,
    actor_id: UUID,
    command: Sequence[str],
    payload: Mapping[str, str],
    name: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    arguments = ["docker", "run"]
    if name is None:
        arguments.append("--rm")
    else:
        arguments.extend(("--name", name))
    arguments.append("--interactive")
    arguments.extend(
        common_job_arguments(network=network, volume=volume, actor_id=actor_id)
    )
    arguments.extend(("--entrypoint", "automation-tool-account-operations", image))
    arguments.extend(command)
    return run(
        arguments,
        input_text=json.dumps(dict(payload), separators=(",", ":")),
        check=check,
    )


def python_job(
    *,
    image: str,
    network: str,
    volume: str,
    actor_id: UUID,
    source: str,
    payload: Mapping[str, str],
) -> dict[str, Any]:
    arguments = ["docker", "run", "--rm", "--interactive"]
    arguments.extend(
        common_job_arguments(network=network, volume=volume, actor_id=actor_id)
    )
    arguments.extend(("--entrypoint", "python", image, "-c", source))
    result = run(arguments, input_text=json.dumps(dict(payload), separators=(",", ":")))
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise AcceptanceFailure("C10-06 job projection is invalid")
    return cast(dict[str, Any], parsed)


def http_probe(container: str, payload: Mapping[str, str]) -> dict[str, Any]:
    result = run(
        ["docker", "exec", "--interactive", container, "python", "-c", HTTP_PROBE],
        input_text=json.dumps(dict(payload), separators=(",", ":")),
    )
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise AcceptanceFailure("C10-06 HTTP projection is invalid")
    return cast(dict[str, Any], parsed)


def account_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise AcceptanceFailure("C10-06 account projection is invalid")
    return cast(dict[str, Any], parsed)


def remove_container(name: str) -> None:
    run(["docker", "container", "rm", "--force", name], check=False)


def main() -> None:
    suffix = uuid4().hex[:12]
    network = f"automation-tool-c10-06-{suffix}"
    postgres = f"automation-tool-c10-06-postgres-{suffix}"
    control_plane = f"automation-tool-c10-06-control-plane-{suffix}"
    emergency_job = f"automation-tool-c10-06-emergency-{suffix}"
    app_volume = f"automation-tool-c10-06-app-{suffix}"
    operations_volume = f"automation-tool-c10-06-operations-{suffix}"
    postgres_volume = f"automation-tool-c10-06-postgres-{suffix}"
    image = f"automation-tool-control-plane:c10-06-{suffix}"
    database_password = secrets.token_urlsafe(32)
    operations_password = secrets.token_urlsafe(32)
    operator_capability = f"atoc1.{base64url(secrets.token_bytes(32))}"
    original_password = "C10-06 original demo password"
    recovered_password = "C10-06 recovered demo password"
    actor_id = uuid4()
    target_login = f"target-{suffix}"
    foreign_login = f"foreign-{suffix}"
    app_database_url = (
        f"postgresql+asyncpg://c10_app:{database_password}@postgres:5432/c10_demo"
    )
    operations_database_url = (
        "postgresql+asyncpg://automation_tool_operations:"
        f"{operations_password}@postgres:5432/c10_demo"
    )
    shared_secrets = {
        "account-password-pepper": base64url(secrets.token_bytes(32)),
        "account-fingerprint-key": base64url(secrets.token_bytes(32)),
        "account-operations-capability-digest": base64url(
            hashlib.sha256(operator_capability.encode("ascii")).digest()
        ),
        "action-authorization-private-key": base64url(secrets.token_bytes(32)),
    }
    project = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    app_version = cast(str, project["project"]["version"])
    revision = run(["git", "rev-parse", "HEAD"]).stdout.strip()

    try:
        run(
            [
                "docker",
                "build",
                "--file",
                str(BACKEND_ROOT / "Dockerfile"),
                "--tag",
                image,
                "--build-arg",
                f"APP_VERSION={app_version}",
                "--build-arg",
                f"VCS_REF={revision}",
                str(BACKEND_ROOT),
            ]
        )
        image_config = inspect("image", image).get("Config")
        if not isinstance(image_config, dict) or FILE_ONLY_MODE not in image_config.get(
            "Env", []
        ):
            raise AcceptanceFailure("C10-06 image is not file-only")

        run(["docker", "network", "create", network])
        for volume in (app_volume, operations_volume, postgres_volume):
            run(["docker", "volume", "create", volume])
        write_volume(
            image=image,
            volume=app_volume,
            values={"database-url": app_database_url, **shared_secrets},
        )
        write_volume(
            image=image,
            volume=operations_volume,
            values={"database-url": operations_database_url, **shared_secrets},
        )
        write_volume(
            image=image,
            volume=postgres_volume,
            values={"postgres-password": database_password},
        )
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                postgres,
                "--network",
                network,
                "--network-alias",
                "postgres",
                "--mount",
                f"type=volume,source={postgres_volume},target=/run/secrets,readonly",
                "--env",
                "POSTGRES_USER=c10_app",
                "--env",
                "POSTGRES_DB=c10_demo",
                "--env",
                "POSTGRES_PASSWORD_FILE=/run/secrets/postgres-password",
                "--tmpfs",
                "/var/lib/postgresql:rw,noexec,nosuid,size=512m",
                "--health-cmd",
                "pg_isready --username c10_app --dbname c10_demo",
                "--health-interval",
                "1s",
                "--health-timeout",
                "3s",
                "--health-retries",
                "30",
                POSTGRES_IMAGE,
            ]
        )
        wait_for_healthy(postgres, timeout_seconds=45)
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--mount",
                f"type=volume,source={app_volume},target=/run/secrets,readonly",
                "--entrypoint",
                "alembic",
                image,
                "-c",
                "/app/alembic.ini",
                "upgrade",
                "head",
            ]
        )
        escaped_password = operations_password.replace("'", "''")
        role_sql = ROLE_SQL.read_text(encoding="utf-8")
        role_sql += (
            f"\nALTER ROLE automation_tool_operations PASSWORD '{escaped_password}';\n"
        )
        run(
            [
                "docker",
                "exec",
                "--interactive",
                postgres,
                "psql",
                "--username",
                "c10_app",
                "--dbname",
                "c10_demo",
                "--no-psqlrc",
                "--quiet",
            ],
            input_text=role_sql,
        )

        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                control_plane,
                "--network",
                network,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--security-opt",
                "no-new-privileges=true",
                "--cap-drop",
                "ALL",
                "--mount",
                f"type=volume,source={app_volume},target=/run/secrets,readonly",
                "--env",
                "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER_VERSION=1",
                "--env",
                f"AUTOMATION_TOOL_ACCOUNT_OPERATIONS_ACTOR_ID={actor_id}",
                "--env",
                "AUTOMATION_TOOL_ACTION_MINIMUM_INTERVAL_SECONDS=5",
                "--env",
                "AUTOMATION_TOOL_ACTION_TASK_LIMIT=20",
                "--env",
                "AUTOMATION_TOOL_ACTION_DAILY_LIMIT=100",
                "--env",
                "AUTOMATION_TOOL_ACTION_CONSECUTIVE_FAILURE_THRESHOLD=3",
                image,
            ]
        )
        wait_for_healthy(control_plane, timeout_seconds=60)

        base_payload = {
            "capability": operator_capability,
            "password": original_password,
        }
        target = account_result(
            operation(
                image=image,
                network=network,
                volume=operations_volume,
                actor_id=actor_id,
                command=(
                    "create",
                    "--login-name",
                    target_login,
                    "--request-id",
                    "c10-06-target",
                ),
                payload=base_payload,
            )
        )
        foreign = account_result(
            operation(
                image=image,
                network=network,
                volume=operations_volume,
                actor_id=actor_id,
                command=(
                    "create",
                    "--login-name",
                    foreign_login,
                    "--request-id",
                    "c10-06-foreign",
                ),
                payload=base_payload,
            )
        )
        duplicate = operation(
            image=image,
            network=network,
            volume=operations_volume,
            actor_id=actor_id,
            command=(
                "create",
                "--login-name",
                target_login,
                "--request-id",
                "c10-06-duplicate",
            ),
            payload=base_payload,
            check=False,
        )
        if duplicate.returncode == 0:
            raise AcceptanceFailure("C10-06 duplicate account creation succeeded")

        target_login_result = http_probe(
            control_plane,
            {
                "operation": "login",
                "loginName": target_login,
                "password": original_password,
                "requestId": "c10-06-target-login",
            },
        )
        if target_login_result.get("status") != HTTPStatus.CREATED:
            raise AcceptanceFailure("C10-06 target login failed")
        devices = python_job(
            image=image,
            network=network,
            volume=operations_volume,
            actor_id=actor_id,
            source=SEED_DEVICES,
            payload={
                "target": str(target["userId"]),
                "foreign": str(foreign["userId"]),
            },
        )

        disabled = account_result(
            operation(
                image=image,
                network=network,
                volume=operations_volume,
                actor_id=actor_id,
                command=(
                    "disable",
                    "--user-id",
                    str(foreign["userId"]),
                    "--expected-revision",
                    "1",
                    "--request-id",
                    "c10-06-disable",
                ),
                payload={"capability": operator_capability},
            )
        )
        if disabled.get("status") != "disabled" or disabled.get("revision") != 2:
            raise AcceptanceFailure("C10-06 disable projection is invalid")
        denied_login = http_probe(
            control_plane,
            {
                "operation": "login",
                "loginName": foreign_login,
                "password": original_password,
                "requestId": "c10-06-disabled-login",
            },
        )
        if denied_login != {
            "status": HTTPStatus.UNAUTHORIZED,
            "errorCode": "account_authentication_invalid",
        }:
            raise AcceptanceFailure("C10-06 disabled login was not rejected")
        restored = account_result(
            operation(
                image=image,
                network=network,
                volume=operations_volume,
                actor_id=actor_id,
                command=(
                    "restore",
                    "--user-id",
                    str(foreign["userId"]),
                    "--expected-revision",
                    "2",
                    "--request-id",
                    "c10-06-restore",
                ),
                payload={"capability": operator_capability},
            )
        )
        if restored.get("status") != "active" or restored.get("revision") != 3:
            raise AcceptanceFailure("C10-06 restore projection is invalid")
        reset = account_result(
            operation(
                image=image,
                network=network,
                volume=operations_volume,
                actor_id=actor_id,
                command=(
                    "reset",
                    "--login-name",
                    foreign_login,
                    "--request-id",
                    "c10-06-reset",
                ),
                payload={"capability": operator_capability},
            )
        )
        recovery_token = reset.get("recoveryToken")
        if not isinstance(recovery_token, str):
            raise AcceptanceFailure("C10-06 reset did not issue a recovery token")
        recovered = http_probe(
            control_plane,
            {
                "operation": "recover",
                "recoveryToken": recovery_token,
                "newPassword": recovered_password,
                "requestId": "c10-06-recover",
            },
        )
        if recovered.get("status") != HTTPStatus.NO_CONTENT:
            raise AcceptanceFailure("C10-06 password recovery failed")
        new_login = http_probe(
            control_plane,
            {
                "operation": "login",
                "loginName": foreign_login,
                "password": recovered_password,
                "requestId": "c10-06-recovered-login",
            },
        )
        if new_login.get("status") != HTTPStatus.CREATED:
            raise AcceptanceFailure("C10-06 recovered login failed")

        emergency = account_result(
            operation(
                image=image,
                network=network,
                volume=operations_volume,
                actor_id=actor_id,
                name=emergency_job,
                command=(
                    "emergency-revoke",
                    "--user-id",
                    str(target["userId"]),
                    "--expected-revision",
                    "1",
                    "--request-id",
                    "c10-06-emergency",
                ),
                payload={"capability": operator_capability},
            )
        )
        if emergency.get("revokedDeviceCount") != 2:
            raise AcceptanceFailure("C10-06 emergency device count is invalid")
        job = inspect("container", emergency_job)
        host_config = job.get("HostConfig")
        mounts = job.get("Mounts")
        config = job.get("Config")
        if (
            not isinstance(host_config, dict)
            or host_config.get("ReadonlyRootfs") is not True
        ):
            raise AcceptanceFailure("C10-06 operations root filesystem is writable")
        if host_config.get("PortBindings") not in ({}, None):
            raise AcceptanceFailure("C10-06 operations job published a host port")
        if host_config.get("CapDrop") != ["ALL"]:
            raise AcceptanceFailure("C10-06 operations capabilities are invalid")
        if not isinstance(mounts, list) or not any(
            isinstance(mount, dict)
            and mount.get("Destination") == "/run/secrets"
            and mount.get("RW") is False
            for mount in mounts
        ):
            raise AcceptanceFailure("C10-06 operations secret mount is invalid")
        if not isinstance(config, dict) or any(
            secret in json.dumps(config)
            for secret in (
                operator_capability,
                original_password,
                recovered_password,
                recovery_token,
            )
        ):
            raise AcceptanceFailure("C10-06 operations configuration contains a secret")

        foreign_devices = devices.get("foreign")
        if not isinstance(foreign_devices, list) or len(foreign_devices) != 1:
            raise AcceptanceFailure("C10-06 foreign device projection is invalid")
        projection = python_job(
            image=image,
            network=network,
            volume=operations_volume,
            actor_id=actor_id,
            source=VERIFY_PROJECTION,
            payload={
                "target": str(target["userId"]),
                "foreignDevice": str(foreign_devices[0]),
            },
        )
        expected = {
            "account.disabled": True,
            "credentialVersionAdvanced": True,
            "revisionAdvanced": True,
            "revokedDeviceCount": 2,
            "deviceCredentialsRevoked": True,
            "deviceSessionsRevoked": True,
            "session.all_revoked": True,
            "device.revoked": 2,
            "auditTypes": ["account.disabled", "device.revoked", "session.all_revoked"],
            "auditOperationsOnly": True,
            "foreignDeviceActive": True,
            "usersReadable": True,
            "ddlDenied": True,
            "taskDataDenied": True,
        }
        if projection != expected:
            raise AcceptanceFailure("C10-06 database projection is invalid")
        logs = run(["docker", "logs", control_plane]).stdout
        logs += run(["docker", "logs", emergency_job]).stdout
        if any(
            secret in logs
            for secret in (
                database_password,
                operations_password,
                operator_capability,
                original_password,
                recovered_password,
                recovery_token,
            )
        ):
            raise AcceptanceFailure("C10-06 container logs contain a secret")
        print(
            json.dumps(
                {
                    "accountOperations": [
                        "create",
                        "disable",
                        "restore",
                        "reset",
                        "emergency-revoke",
                    ],
                    "auditTypes": projection["auditTypes"],
                    "databaseRole": "automation_tool_operations",
                    "ddlDenied": projection["ddlDenied"],
                    "hostPorts": 0,
                    "revokedDeviceCount": projection["revokedDeviceCount"],
                    "taskDataDenied": projection["taskDataDenied"],
                },
                sort_keys=True,
            )
        )
    finally:
        for container in (emergency_job, control_plane, postgres):
            remove_container(container)
        run(["docker", "network", "rm", network], check=False)
        for volume in (app_volume, operations_volume, postgres_volume):
            run(["docker", "volume", "rm", "--force", volume], check=False)
        run(["docker", "image", "rm", "--force", image], check=False)


if __name__ == "__main__":
    main()
