"""Fixed production runtime-secret delivery with no path or value reflection."""

from __future__ import annotations

import os
import stat
from enum import StrEnum
from typing import Final

_SECRET_DIRECTORY: Final = "/run/secrets"
_MAXIMUM_SECRET_BYTES: Final = 8192
_MODE_ENVIRONMENT: Final = "AUTOMATION_TOOL_RUNTIME_SECRET_MODE"


class RuntimeSecretName(StrEnum):
    DATABASE_URL = "database-url"
    ACCOUNT_PASSWORD_PEPPER = "account-password-pepper"
    ACCOUNT_FINGERPRINT_KEY = "account-fingerprint-key"
    ACCOUNT_OPERATIONS_CAPABILITY_DIGEST = "account-operations-capability-digest"
    ACTION_AUTHORIZATION_PRIVATE_KEY = "action-authorization-private-key"


_ENVIRONMENT_NAMES: Final = {
    RuntimeSecretName.DATABASE_URL: "AUTOMATION_TOOL_DATABASE_URL",
    RuntimeSecretName.ACCOUNT_PASSWORD_PEPPER: "AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER",
    RuntimeSecretName.ACCOUNT_FINGERPRINT_KEY: "AUTOMATION_TOOL_ACCOUNT_FINGERPRINT_KEY",
    RuntimeSecretName.ACCOUNT_OPERATIONS_CAPABILITY_DIGEST: (
        "AUTOMATION_TOOL_ACCOUNT_OPERATIONS_CAPABILITY_DIGEST"
    ),
    RuntimeSecretName.ACTION_AUTHORIZATION_PRIVATE_KEY: (
        "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PRIVATE_KEY"
    ),
}


class RuntimeSecretError(RuntimeError):
    """Secret delivery is absent or unsafe without reflecting a path or value."""

    def __init__(self) -> None:
        super().__init__("Runtime secret delivery is invalid")


def _validate_metadata(metadata: os.stat_result) -> None:
    effective_user = os.geteuid()
    effective_group = os.getegid()
    permissions = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeSecretError
    if metadata.st_uid not in {0, effective_user}:
        raise RuntimeSecretError
    if permissions & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IROTH | 0o111):
        raise RuntimeSecretError
    if metadata.st_uid == effective_user:
        if permissions != 0o400:
            raise RuntimeSecretError
        return
    if metadata.st_gid != effective_group or permissions != 0o440:
        raise RuntimeSecretError


def _read_secret_file(directory_descriptor: int, name: RuntimeSecretName) -> str | None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name.value, flags | no_follow, dir_fd=directory_descriptor)
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeSecretError from None
    try:
        _validate_metadata(os.fstat(descriptor))
        encoded = os.read(descriptor, _MAXIMUM_SECRET_BYTES + 1)
        if len(encoded) > _MAXIMUM_SECRET_BYTES:
            raise RuntimeSecretError
        if os.read(descriptor, 1):
            raise RuntimeSecretError
    except OSError:
        raise RuntimeSecretError from None
    finally:
        os.close(descriptor)
    try:
        value = encoded.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeSecretError from None
    if not value or value != value.strip() or "\x00" in value or "\n" in value or "\r" in value:
        raise RuntimeSecretError
    return value


def _file_secret(name: RuntimeSecretName) -> str | None:
    if os.open not in os.supports_dir_fd or not hasattr(os, "geteuid"):
        # The guarantee this mode sells is POSIX ownership plus a
        # directory-relative open that cannot be re-pointed between check and
        # use. A platform offering neither cannot make it, so the mode is
        # refused outright; approximating it would report every secret as
        # merely absent and let a deployment start wide open.
        raise RuntimeSecretError
    flags = (
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        directory_descriptor = os.open(_SECRET_DIRECTORY, flags)
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeSecretError from None
    try:
        return _read_secret_file(directory_descriptor, name)
    finally:
        os.close(directory_descriptor)


def runtime_secret(name: RuntimeSecretName, *, required: bool = False) -> str | None:
    """Read one named secret from the selected closed delivery mode."""

    mode = os.environ.get(_MODE_ENVIRONMENT, "environment")
    environment_name = _ENVIRONMENT_NAMES[name]
    if mode == "environment":
        value = os.environ.get(environment_name)
    elif mode == "files":
        if any(os.environ.get(candidate) is not None for candidate in _ENVIRONMENT_NAMES.values()):
            raise RuntimeSecretError
        value = _file_secret(name)
    else:
        raise RuntimeSecretError
    if required and value is None:
        raise RuntimeSecretError
    return value


__all__ = ["RuntimeSecretError", "RuntimeSecretName", "runtime_secret"]
