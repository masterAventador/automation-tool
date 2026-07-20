from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator

from automation_tool.executor import package_manifest as manifest_module
from automation_tool.executor.package_manifest import (
    EXECUTOR_MANIFEST_FILE_NAME,
    EXECUTOR_SIGNATURE_FILE_NAME,
    ExecutorManifestRejected,
    write_signed_executor_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/fixtures/executor-package-v1/valid"
TEST_SIGNING_KEY = bytes(range(32))
MANIFEST_FIELDS = {
    "manifest_version",
    "executor_version",
    "build_id",
    "platform",
    "architecture",
    "entrypoint",
    "package_size",
    "package_sha256",
    "files",
}
MANIFEST_SCHEMA = json.loads(
    (REPOSITORY_ROOT / "contracts/protocol/executor-package-manifest-v1.schema.json").read_text(
        encoding="utf-8"
    )
)


def inventory_digest(files: list[dict[str, object]]) -> str:
    digest = hashlib.sha256(b"automation-tool.executor-package.v1\0")
    for item in files:
        path = str(item["path"]).encode("ascii")
        size = item["size"]
        assert isinstance(size, int)
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(str(item["sha256"])))
    return digest.hexdigest()


def copy_fixture_package(tmp_path: Path) -> Path:
    bundle = tmp_path / "automation-tool-executor"
    shutil.copytree(FIXTURE_ROOT / "package", bundle)
    return bundle


def test_manifest_is_deterministic_complete_and_signed_over_exact_bytes(tmp_path: Path) -> None:
    bundle = copy_fixture_package(tmp_path)

    generated = write_signed_executor_manifest(
        bundle_directory=bundle,
        executor_version="0.1.0",
        build_id="fixture-build-1",
        target_platform="macos",
        target_architecture="aarch64",
        signing_private_key=TEST_SIGNING_KEY,
    )
    repeated = write_signed_executor_manifest(
        bundle_directory=bundle,
        executor_version="0.1.0",
        build_id="fixture-build-1",
        target_platform="macos",
        target_architecture="aarch64",
        signing_private_key=TEST_SIGNING_KEY,
    )

    document = json.loads(generated.manifest_bytes)
    Draft202012Validator.check_schema(MANIFEST_SCHEMA)
    Draft202012Validator(MANIFEST_SCHEMA).validate(document)
    assert set(document) == MANIFEST_FIELDS
    assert document["manifest_version"] == "1"
    assert document["executor_version"] == "0.1.0"
    assert document["build_id"] == "fixture-build-1"
    assert document["platform"] == "macos"
    assert document["architecture"] == "aarch64"
    assert document["entrypoint"] == "automation-tool-executor"
    assert [item["path"] for item in document["files"]] == [
        "_internal/runtime.dat",
        "automation-tool-executor",
    ]
    assert document["package_size"] == sum(item["size"] for item in document["files"])
    assert document["package_sha256"] == inventory_digest(document["files"])
    assert generated.manifest_bytes.endswith(b"\n")
    assert generated.manifest_bytes == repeated.manifest_bytes
    assert generated.signature_bytes == repeated.signature_bytes
    assert (bundle / EXECUTOR_MANIFEST_FILE_NAME).read_bytes() == generated.manifest_bytes
    assert (bundle / EXECUTOR_SIGNATURE_FILE_NAME).read_bytes() == generated.signature_envelope
    assert generated.manifest_bytes == (FIXTURE_ROOT / EXECUTOR_MANIFEST_FILE_NAME).read_bytes()
    assert (
        generated.signature_envelope == (FIXTURE_ROOT / EXECUTOR_SIGNATURE_FILE_NAME).read_bytes()
    )

    prefix, encoded = generated.signature_envelope.rstrip(b"\n").split(b".", maxsplit=1)
    assert prefix == b"atems1"
    assert b"=" not in encoded
    assert base64.urlsafe_b64decode(encoded + b"==") == generated.signature_bytes
    Ed25519PrivateKey.from_private_bytes(TEST_SIGNING_KEY).public_key().verify(
        generated.signature_bytes,
        generated.manifest_bytes,
    )


@pytest.mark.parametrize(
    ("overrides", "entrypoint"),
    (
        ({"executor_version": "1"}, "automation-tool-executor"),
        ({"executor_version": "01.0.0"}, "automation-tool-executor"),
        ({"build_id": "../escape"}, "automation-tool-executor"),
        ({"target_platform": "linux"}, "automation-tool-executor"),
        ({"target_architecture": "arm64"}, "automation-tool-executor"),
        ({"target_platform": "windows"}, "automation-tool-executor"),
        ({"target_platform": "macos"}, "automation-tool-executor.exe"),
    ),
)
def test_manifest_rejects_ambiguous_identity_or_entrypoint(
    tmp_path: Path,
    overrides: dict[str, str],
    entrypoint: str,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / entrypoint).write_bytes(b"entrypoint")
    with pytest.raises(ExecutorManifestRejected, match=r"^Executor manifest is rejected$"):
        write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version=overrides.get("executor_version", "1.0.0"),
            build_id=overrides.get("build_id", "build-1"),
            target_platform=overrides.get("target_platform", "macos"),
            target_architecture=overrides.get("target_architecture", "aarch64"),
            signing_private_key=TEST_SIGNING_KEY,
        )


@pytest.mark.parametrize("key", (b"", b"x" * 31, b"x" * 33))
def test_manifest_rejects_invalid_private_key_without_reflecting_it(
    tmp_path: Path,
    key: bytes,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "automation-tool-executor").write_bytes(b"entrypoint")

    with pytest.raises(
        ExecutorManifestRejected,
        match=r"^Executor manifest is rejected$",
    ) as captured:
        write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="1.0.0",
            build_id="build-1",
            target_platform="macos",
            target_architecture="aarch64",
            signing_private_key=key,
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not key or key.hex() not in str(captured.value)


@pytest.mark.skipif(os.name == "nt", reason="creating a symlink requires optional Windows rights")
def test_manifest_rejects_every_symlink_in_the_package(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "automation-tool-executor").write_bytes(b"entrypoint")
    (bundle / "outside").symlink_to(tmp_path / "not-part-of-package")

    with pytest.raises(ExecutorManifestRejected):
        write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="1.0.0",
            build_id="build-1",
            target_platform="macos",
            target_architecture="aarch64",
            signing_private_key=TEST_SIGNING_KEY,
        )


def test_manifest_rejects_empty_or_non_directory_packages(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-directory"
    file_path.write_bytes(b"package")
    empty = tmp_path / "empty"
    empty.mkdir()

    for bundle in (tmp_path / "missing", file_path, empty, tmp_path):
        with pytest.raises(ExecutorManifestRejected):
            write_signed_executor_manifest(
                bundle_directory=bundle,
                executor_version="1.0.0",
                build_id="build-1",
                target_platform="macos",
                target_architecture="aarch64",
                signing_private_key=TEST_SIGNING_KEY,
            )


@pytest.mark.parametrize(
    "relative",
    (
        Path("名字"),
        Path("forbidden:name"),
        Path("a" * 256),
        Path(*(["a" * 200] * 21)),
    ),
)
def test_manifest_rejects_non_portable_paths(tmp_path: Path, relative: Path) -> None:
    with pytest.raises(ExecutorManifestRejected):
        manifest_module._portable_relative_path(tmp_path, tmp_path / relative)

    with pytest.raises(ExecutorManifestRejected):
        manifest_module._portable_relative_path(tmp_path, tmp_path)


def stat_result(*, mode: int, device: int = 1, inode: int = 2, size: int = 3) -> os.stat_result:
    return os.stat_result((mode, inode, device, 1, 0, 0, size, 0, 0, 0))


def test_file_identity_requires_two_identical_regular_files() -> None:
    regular = stat_result(mode=stat.S_IFREG | 0o600)

    assert manifest_module._same_file_identity(regular, regular)
    assert not manifest_module._same_file_identity(stat_result(mode=stat.S_IFDIR | 0o700), regular)
    assert not manifest_module._same_file_identity(regular, stat_result(mode=stat.S_IFDIR | 0o700))
    assert not manifest_module._same_file_identity(
        regular, stat_result(mode=regular.st_mode, device=9)
    )
    assert not manifest_module._same_file_identity(
        regular, stat_result(mode=regular.st_mode, inode=9)
    )
    assert not manifest_module._same_file_identity(
        regular, stat_result(mode=regular.st_mode, size=9)
    )


def test_manifest_rejects_a_file_replaced_while_it_is_hashed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "payload"
    path.write_bytes(b"payload")
    expected = path.lstat()
    monkeypatch.setattr(manifest_module, "_same_file_identity", lambda _left, _right: False)

    with pytest.raises(ExecutorManifestRejected):
        manifest_module._hash_stable_regular_file(path, expected)


def test_manifest_rejects_a_file_changed_after_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "payload"
    path.write_bytes(b"payload")
    expected = path.lstat()
    identities = iter((True, False))
    monkeypatch.setattr(
        manifest_module,
        "_same_file_identity",
        lambda _left, _right: next(identities),
    )

    with pytest.raises(ExecutorManifestRejected):
        manifest_module._hash_stable_regular_file(path, expected)


@pytest.mark.skipif(os.name == "nt", reason="mkfifo is unavailable on Windows")
def test_manifest_rejects_non_regular_directory_members(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "automation-tool-executor").write_bytes(b"entrypoint")
    cast(Callable[[Path], None], vars(os)["mkfifo"])(bundle / "named-pipe")

    with pytest.raises(ExecutorManifestRejected):
        write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="1.0.0",
            build_id="build-1",
            target_platform="macos",
            target_architecture="aarch64",
            signing_private_key=TEST_SIGNING_KEY,
        )


def test_manifest_enforces_file_count_package_size_and_nonempty_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    entrypoint = bundle / "automation-tool-executor"
    entrypoint.write_bytes(b"entrypoint")
    monkeypatch.setattr(manifest_module, "MAX_PACKAGE_FILES", 0)
    with pytest.raises(ExecutorManifestRejected):
        write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="1.0.0",
            build_id="build-1",
            target_platform="macos",
            target_architecture="aarch64",
            signing_private_key=TEST_SIGNING_KEY,
        )

    monkeypatch.setattr(manifest_module, "MAX_PACKAGE_FILES", 10)
    monkeypatch.setattr(manifest_module, "MAX_PACKAGE_BYTES", 0)
    with pytest.raises(ExecutorManifestRejected):
        write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="1.0.0",
            build_id="build-1",
            target_platform="macos",
            target_architecture="aarch64",
            signing_private_key=TEST_SIGNING_KEY,
        )

    monkeypatch.setattr(manifest_module, "MAX_PACKAGE_BYTES", 100)
    entrypoint.write_bytes(b"")
    with pytest.raises(ExecutorManifestRejected):
        write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="1.0.0",
            build_id="build-1",
            target_platform="macos",
            target_architecture="aarch64",
            signing_private_key=TEST_SIGNING_KEY,
        )


def test_manifest_maps_metadata_write_failures_to_the_fixed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "automation-tool-executor").write_bytes(b"entrypoint")
    monkeypatch.setattr(Path, "write_bytes", lambda _self, _data: (_ for _ in ()).throw(OSError()))

    with pytest.raises(ExecutorManifestRejected):
        write_signed_executor_manifest(
            bundle_directory=bundle,
            executor_version="1.0.0",
            build_id="build-1",
            target_platform="macos",
            target_architecture="aarch64",
            signing_private_key=TEST_SIGNING_KEY,
        )
