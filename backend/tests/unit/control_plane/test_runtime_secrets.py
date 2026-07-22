from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from automation_tool.control_plane.bootstrap import runtime_secrets
from automation_tool.control_plane.bootstrap.runtime_secrets import (
    RuntimeSecretError,
    RuntimeSecretName,
    _read_secret_file,
    _validate_metadata,
    runtime_secret,
)


def metadata(*, mode: int, uid: int | None = None, gid: int | None = None) -> os.stat_result:
    return os.stat_result(
        (
            mode,
            1,
            1,
            1,
            os.geteuid() if uid is None else uid,
            os.getegid() if gid is None else gid,
            1,
            0,
            0,
            0,
        )
    )


def write_secret(root: Path, name: str, value: bytes, *, mode: int = 0o400) -> Path:
    path = root / name
    path.write_bytes(value)
    path.chmod(mode)
    return path


def clear_secret_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOMATION_TOOL_RUNTIME_SECRET_MODE", raising=False)
    for name in runtime_secrets._ENVIRONMENT_NAMES.values():
        monkeypatch.delenv(name, raising=False)


def test_environment_mode_preserves_local_development_without_reflection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_secret_environment(monkeypatch)
    monkeypatch.setenv("AUTOMATION_TOOL_DATABASE_URL", "private-local-value")

    assert runtime_secret(RuntimeSecretName.DATABASE_URL) == "private-local-value"
    assert runtime_secret(RuntimeSecretName.ACCOUNT_PASSWORD_PEPPER) is None
    with pytest.raises(RuntimeSecretError) as captured:
        runtime_secret(RuntimeSecretName.ACCOUNT_PASSWORD_PEPPER, required=True)
    assert "private-local-value" not in repr(captured.value)


def test_unknown_mode_and_secret_environment_in_file_mode_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_secret_environment(monkeypatch)
    monkeypatch.setenv("AUTOMATION_TOOL_RUNTIME_SECRET_MODE", "unknown")
    with pytest.raises(RuntimeSecretError):
        runtime_secret(RuntimeSecretName.DATABASE_URL)

    monkeypatch.setenv("AUTOMATION_TOOL_RUNTIME_SECRET_MODE", "files")
    monkeypatch.setenv("AUTOMATION_TOOL_ACCOUNT_PASSWORD_PEPPER", "must-not-be-read")
    with pytest.raises(RuntimeSecretError) as captured:
        runtime_secret(RuntimeSecretName.DATABASE_URL)
    assert "must-not-be-read" not in repr(captured.value)


def test_file_mode_reads_only_the_fixed_name_and_enforces_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_secret_environment(monkeypatch)
    monkeypatch.setenv("AUTOMATION_TOOL_RUNTIME_SECRET_MODE", "files")
    seen: list[RuntimeSecretName] = []

    def fake_file_secret(name: RuntimeSecretName) -> str | None:
        seen.append(name)
        return "fixed-value" if name is RuntimeSecretName.DATABASE_URL else None

    monkeypatch.setattr(runtime_secrets, "_file_secret", fake_file_secret)

    assert runtime_secret(RuntimeSecretName.DATABASE_URL, required=True) == "fixed-value"
    assert runtime_secret(RuntimeSecretName.ACCOUNT_FINGERPRINT_KEY) is None
    with pytest.raises(RuntimeSecretError):
        runtime_secret(RuntimeSecretName.ACCOUNT_FINGERPRINT_KEY, required=True)
    assert seen == [
        RuntimeSecretName.DATABASE_URL,
        RuntimeSecretName.ACCOUNT_FINGERPRINT_KEY,
        RuntimeSecretName.ACCOUNT_FINGERPRINT_KEY,
    ]


def test_fixed_directory_missing_is_an_absent_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runtime_secrets, "_SECRET_DIRECTORY", str(tmp_path / "missing"))

    assert runtime_secrets._file_secret(RuntimeSecretName.DATABASE_URL) is None


def test_fixed_directory_open_error_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(*_arguments: Any, **_keywords: Any) -> int:
        raise PermissionError

    monkeypatch.setattr(os, "open", denied)

    with pytest.raises(RuntimeSecretError):
        runtime_secrets._file_secret(RuntimeSecretName.DATABASE_URL)


def test_fixed_directory_symlink_is_not_followed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(runtime_secrets, "_SECRET_DIRECTORY", str(link))

    with pytest.raises(RuntimeSecretError):
        runtime_secrets._file_secret(RuntimeSecretName.DATABASE_URL)


def test_fixed_directory_delegates_to_bounded_reader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runtime_secrets, "_SECRET_DIRECTORY", str(tmp_path))
    write_secret(tmp_path, RuntimeSecretName.DATABASE_URL.value, b"fixed-value")

    assert runtime_secrets._file_secret(RuntimeSecretName.DATABASE_URL) == "fixed-value"


def test_bounded_reader_returns_none_for_a_missing_fixed_file(tmp_path: Path) -> None:
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        assert _read_secret_file(directory, RuntimeSecretName.DATABASE_URL) is None
    finally:
        os.close(directory)


def test_bounded_reader_rejects_symlink_without_following_it(tmp_path: Path) -> None:
    target = write_secret(tmp_path, "target", b"do-not-follow")
    (tmp_path / RuntimeSecretName.DATABASE_URL.value).symlink_to(target)
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(RuntimeSecretError):
            _read_secret_file(directory, RuntimeSecretName.DATABASE_URL)
    finally:
        os.close(directory)


@pytest.mark.parametrize(
    "value",
    (
        b"",
        b" leading",
        b"trailing ",
        b"line\n",
        b"line\r",
        b"nul\x00byte",
        b"\xff",
        b"x" * 8193,
    ),
)
def test_bounded_reader_rejects_unsafe_content(tmp_path: Path, value: bytes) -> None:
    write_secret(tmp_path, RuntimeSecretName.DATABASE_URL.value, value)
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(RuntimeSecretError):
            _read_secret_file(directory, RuntimeSecretName.DATABASE_URL)
    finally:
        os.close(directory)


def test_bounded_reader_normalizes_read_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    write_secret(tmp_path, RuntimeSecretName.DATABASE_URL.value, b"fixed-value")
    directory = os.open(tmp_path, os.O_RDONLY)

    def failed_read(_descriptor: int, _size: int) -> bytes:
        raise OSError

    monkeypatch.setattr(os, "read", failed_read)
    try:
        with pytest.raises(RuntimeSecretError):
            _read_secret_file(directory, RuntimeSecretName.DATABASE_URL)
    finally:
        os.close(directory)


def test_bounded_reader_rejects_a_file_that_grows_after_first_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    write_secret(tmp_path, RuntimeSecretName.DATABASE_URL.value, b"fixed-value")
    directory = os.open(tmp_path, os.O_RDONLY)
    reads = iter((b"fixed-value", b"grew"))
    monkeypatch.setattr(os, "read", lambda _descriptor, _size: next(reads))
    try:
        with pytest.raises(RuntimeSecretError):
            _read_secret_file(directory, RuntimeSecretName.DATABASE_URL)
    finally:
        os.close(directory)


def test_metadata_accepts_private_owner_and_root_runtime_group_files() -> None:
    _validate_metadata(metadata(mode=stat.S_IFREG | 0o400))
    root_group_file = metadata(mode=stat.S_IFREG | 0o440, uid=0)
    _validate_metadata(root_group_file)
    assert root_group_file.st_gid == os.getegid()


@pytest.mark.parametrize(
    "candidate",
    (
        metadata(mode=stat.S_IFDIR | 0o400),
        metadata(mode=stat.S_IFREG | 0o400, uid=123456),
        metadata(mode=stat.S_IFREG | 0o600),
        metadata(mode=stat.S_IFREG | 0o410),
        metadata(mode=stat.S_IFREG | 0o404),
        metadata(mode=stat.S_IFREG | 0o440),
        metadata(mode=stat.S_IFREG | 0o000),
        metadata(mode=stat.S_IFREG | 0o400, uid=0, gid=123456),
        metadata(mode=stat.S_IFREG | 0o400, uid=0),
    ),
)
def test_metadata_rejects_unsafe_type_owner_or_permissions(candidate: os.stat_result) -> None:
    with pytest.raises(RuntimeSecretError):
        _validate_metadata(candidate)
