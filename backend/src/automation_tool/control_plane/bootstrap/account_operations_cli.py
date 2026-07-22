"""Authenticated server-side CLI for customer Demo account operations."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final, TextIO
from uuid import UUID

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from automation_tool.control_plane.application.customer_accounts import (
    AccountAuditActor,
    CustomerAccountService,
)
from automation_tool.control_plane.bootstrap.account_sessions import (
    account_password_hasher_from_environment,
    account_session_service_from_environment,
)
from automation_tool.control_plane.bootstrap.database import database_from_environment
from automation_tool.control_plane.bootstrap.runtime_secrets import (
    RuntimeSecretError,
    RuntimeSecretName,
    runtime_secret,
)
from automation_tool.control_plane.domain import AccountAuditActorKind, UserId
from automation_tool.control_plane.infrastructure.database import (
    SqlAlchemyCustomerAccountRepository,
)

_CAPABILITY_PATTERN: Final = re.compile(r"atoc1\.[A-Za-z0-9_-]{43}", re.ASCII)
_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
_MAX_STDIN_CHARACTERS: Final = 4096
_FIXED_FAILURE: Final = "Account operations command failed"


class AccountOperationsAuthenticationRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__("Account operations authentication is rejected")


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATION_TOOL_", extra="ignore")

    account_operations_actor_id: str | None = None


class OperationsIdentity:
    def __init__(self, *, capability_digest: bytes, actor_id: UUID) -> None:
        self._capability_digest = capability_digest
        self._actor_id = actor_id

    def authenticate(self, capability: object) -> AccountAuditActor:
        if type(capability) is not str or _CAPABILITY_PATTERN.fullmatch(capability) is None:
            raise AccountOperationsAuthenticationRejected
        secret_segment = capability.removeprefix("atoc1.")
        try:
            secret = base64.urlsafe_b64decode(secret_segment + "=")
        except (ValueError, binascii.Error):
            raise AccountOperationsAuthenticationRejected from None
        if (
            len(secret) != 32
            or base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii") != secret_segment
            or not hmac.compare_digest(
                hashlib.sha256(capability.encode("ascii")).digest(),
                self._capability_digest,
            )
        ):
            raise AccountOperationsAuthenticationRejected
        return AccountAuditActor(kind=AccountAuditActorKind.OPERATIONS, actor_id=self._actor_id)


def _decode_digest(value: object) -> bytes:
    if type(value) is not str or _BASE64URL_PATTERN.fullmatch(value) is None:
        raise AccountOperationsAuthenticationRejected
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except (ValueError, binascii.Error):
        raise AccountOperationsAuthenticationRejected from None
    if (
        len(decoded) != 32
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value
    ):
        raise AccountOperationsAuthenticationRejected
    return decoded


def operations_identity_from_environment() -> OperationsIdentity:
    try:
        settings = _Settings()
        actor_id = UUID(settings.account_operations_actor_id or "")
        capability_digest = runtime_secret(
            RuntimeSecretName.ACCOUNT_OPERATIONS_CAPABILITY_DIGEST,
            required=True,
        )
    except (RuntimeSecretError, ValidationError, ValueError):
        raise AccountOperationsAuthenticationRejected from None
    if actor_id.version != 4 or str(actor_id) != settings.account_operations_actor_id:
        raise AccountOperationsAuthenticationRejected
    return OperationsIdentity(
        capability_digest=_decode_digest(capability_digest),
        actor_id=actor_id,
    )


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage customer Demo product accounts")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--login-name", required=True)
    create.add_argument("--request-id", required=True)
    for command in ("disable", "restore", "emergency-revoke"):
        transition = commands.add_parser(command)
        transition.add_argument("--user-id", required=True)
        transition.add_argument("--expected-revision", required=True, type=int)
        transition.add_argument("--request-id", required=True)
    reset = commands.add_parser("reset")
    reset.add_argument("--login-name", required=True)
    reset.add_argument("--request-id", required=True)
    return parser


def _payload(stream: TextIO, *, command: str) -> dict[str, object]:
    encoded = stream.read(_MAX_STDIN_CHARACTERS + 1)
    if len(encoded) > _MAX_STDIN_CHARACTERS:
        raise ValueError
    try:
        parsed = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError from None
    expected = {"capability", "password"} if command == "create" else {"capability"}
    if (
        not isinstance(parsed, dict)
        or set(parsed) != expected
        or any(type(parsed[key]) is not str for key in expected)
    ):
        raise ValueError
    return parsed


async def _execute(
    command: str,
    arguments: argparse.Namespace,
    payload: dict[str, object],
) -> dict[str, object]:
    actor = operations_identity_from_environment().authenticate(payload["capability"])
    database = database_from_environment()
    try:
        password_hasher = account_password_hasher_from_environment()
        accounts = CustomerAccountService(
            repository=SqlAlchemyCustomerAccountRepository(database),
            password_hasher=password_hasher,
            clock=_Clock(),
        )
        if command == "create":
            created = await accounts.create(
                login_name=arguments.login_name,
                password=payload["password"],
                actor=actor,
                request_id=arguments.request_id,
            )
            return {
                "userId": str(created.user_id),
                "status": created.status.value,
                "revision": created.revision,
            }
        if command in {"disable", "restore"}:
            transition = accounts.disable if command == "disable" else accounts.restore
            changed = await transition(
                user_id=UserId.parse(arguments.user_id),
                expected_revision=arguments.expected_revision,
                actor=actor,
                request_id=arguments.request_id,
            )
            return {
                "userId": str(changed.user_id),
                "status": changed.status.value,
                "revision": changed.revision,
            }
        if command == "emergency-revoke":
            revoked = await accounts.emergency_revoke(
                user_id=UserId.parse(arguments.user_id),
                expected_revision=arguments.expected_revision,
                actor=actor,
                request_id=arguments.request_id,
            )
            return {
                "userId": str(revoked.account.user_id),
                "status": revoked.account.status.value,
                "revision": revoked.account.revision,
                "revokedDeviceCount": revoked.revoked_device_count,
            }
        sessions = account_session_service_from_environment(database)
        if sessions is None:
            raise RuntimeError
        recovery = await sessions.issue_recovery(
            login_name=arguments.login_name,
            actor=actor,
            request_id=arguments.request_id,
        )
        return {
            "userId": str(recovery.account.user_id),
            "recoveryToken": recovery.recovery_token,
            "expiresAt": recovery.expires_at.isoformat().replace("+00:00", "Z"),
        }
    finally:
        await database.close()


def main(
    arguments: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    parsed = build_parser().parse_args(arguments)
    source = sys.stdin if input_stream is None else input_stream
    destination = sys.stdout if output_stream is None else output_stream
    try:
        payload = _payload(source, command=parsed.command)
        operations_identity_from_environment().authenticate(payload["capability"])
        result = asyncio.run(_execute(parsed.command, parsed, payload))
    except Exception:
        raise SystemExit(_FIXED_FAILURE) from None
    destination.write(json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n")


__all__ = [
    "AccountOperationsAuthenticationRejected",
    "OperationsIdentity",
    "build_parser",
    "main",
    "operations_identity_from_environment",
]
