from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

from automation_tool.executor import local_artifact as artifact_module
from automation_tool.executor.local_artifact import (
    MAX_LOCAL_ARTIFACT_BYTES,
    LocalArtifactPolicy,
    LocalArtifactRef,
    LocalArtifactRejected,
    LocalArtifactStore,
)

MEDIA_TYPE = "application/vnd.automation-tool.page-drift+json"
DIRECTORY = "artifacts/evidence/page-drift"


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"923e4567-e89b-42d3-a456-{self.value:012d}")


def policy(*, maximum_artifacts: int = 20) -> LocalArtifactPolicy:
    return LocalArtifactPolicy(
        relative_directory=DIRECTORY,
        file_extension="json",
        media_type=MEDIA_TYPE,
        maximum_bytes=2_048,
        maximum_artifacts=maximum_artifacts,
    )


def store(root: Path, *, maximum_artifacts: int = 20) -> LocalArtifactStore:
    root.mkdir(mode=0o700)
    return LocalArtifactStore(
        root_directory=root,
        policy=policy(maximum_artifacts=maximum_artifacts),
        id_source=Ids(),
    )


def test_capture_resolve_and_read_use_one_stable_private_reference(tmp_path: Path) -> None:
    active = store(tmp_path / "state")
    payload = b'{"artifact_version":"executor.page-drift-artifact.v1"}'

    reference = active.capture(payload)

    expected_path = f"{DIRECTORY}/{reference.artifact_id}.json"
    assert reference == LocalArtifactRef(
        artifact_id=reference.artifact_id,
        sha256=hashlib.sha256(payload).hexdigest(),
        media_type=MEDIA_TYPE,
        size_bytes=len(payload),
        relative_path=expected_path,
    )
    assert active.resolve(reference.artifact_id) == reference
    assert active.read(reference) == payload
    assert active.list_references() == (reference,)
    artifact_path = tmp_path / "state" / expected_path
    if os.name != "nt":
        for directory in (
            artifact_path.parents[0],
            artifact_path.parents[1],
            artifact_path.parents[2],
        ):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(artifact_path.stat().st_mode) == 0o600


def test_list_references_returns_stable_id_order(tmp_path: Path) -> None:
    active = store(tmp_path / "state")
    references = (
        active.capture(b"first"),
        active.capture(b"second"),
    )

    assert active.list_references() == tuple(
        sorted(references, key=lambda reference: str(reference.artifact_id))
    )


def test_reference_policy_and_payload_shape_fail_closed() -> None:
    artifact_id = UUID("923e4567-e89b-42d3-a456-426614174001")
    valid_reference: dict[str, object] = {
        "artifact_id": artifact_id,
        "sha256": "a" * 64,
        "media_type": MEDIA_TYPE,
        "size_bytes": 1,
        "relative_path": f"{DIRECTORY}/{artifact_id}.json",
    }
    for changes in (
        {"artifact_id": cast(Any, object())},
        {"artifact_id": UUID(int=4)},
        {"sha256": "A" * 64},
        {"media_type": "text/plain; charset=utf-8"},
        {"size_bytes": True},
        {"size_bytes": 0},
        {"size_bytes": MAX_LOCAL_ARTIFACT_BYTES + 1},
        {"relative_path": "/private/artifact.json"},
        {"relative_path": "artifacts/../private/artifact.json"},
        {"relative_path": f"{DIRECTORY}/different.json"},
    ):
        arguments = dict(valid_reference)
        arguments.update(changes)
        with pytest.raises(LocalArtifactRejected):
            LocalArtifactRef(**cast(Any, arguments))

    valid_policy: dict[str, object] = {
        "relative_directory": DIRECTORY,
        "file_extension": "json",
        "media_type": MEDIA_TYPE,
        "maximum_bytes": 2_048,
        "maximum_artifacts": 20,
    }
    for changes in (
        {"relative_directory": "/private"},
        {"relative_directory": "artifacts/../private"},
        {"file_extension": ".json"},
        {"file_extension": "private/path"},
        {"media_type": "application/json; private=1"},
        {"maximum_bytes": True},
        {"maximum_bytes": 0},
        {"maximum_artifacts": 0},
    ):
        arguments = dict(valid_policy)
        arguments.update(changes)
        with pytest.raises(LocalArtifactRejected):
            LocalArtifactPolicy(**cast(Any, arguments))


def test_tamper_root_replacement_and_untrusted_entries_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "state"
    active = store(root)
    reference = active.capture(b"fixed evidence")
    artifact = root / reference.relative_path

    artifact.write_bytes(b"changed evidence")
    with pytest.raises(LocalArtifactRejected):
        active.read(reference)

    moved = tmp_path / "moved-state"
    root.rename(moved)
    root.mkdir(mode=0o700)
    with pytest.raises(LocalArtifactRejected):
        active.resolve(reference.artifact_id)

    other = store(tmp_path / "other")
    artifact_directory = tmp_path / "other" / DIRECTORY
    (artifact_directory / "unexpected.txt").write_bytes(b"private")
    with pytest.raises(LocalArtifactRejected):
        other.capture(b"fixed evidence")
    with pytest.raises(LocalArtifactRejected):
        other.list_references()


def test_capacity_collision_and_write_failure_leave_no_partial_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "state"
    active = store(root, maximum_artifacts=1)
    active.capture(b"first")
    with pytest.raises(LocalArtifactRejected):
        active.capture(b"second")

    failing_root = tmp_path / "failing-state"
    failing = store(failing_root)
    monkeypatch.setattr(
        os,
        "fsync",
        lambda *_arguments: (_ for _ in ()).throw(OSError("private disk failure")),
    )
    with pytest.raises(
        LocalArtifactRejected,
        match=r"^Local Artifact is unavailable$",
    ) as captured:
        failing.capture(b"private payload")
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    artifact_directory = failing_root / DIRECTORY
    assert tuple(artifact_directory.iterdir()) == ()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs Windows developer mode")
def test_symlink_and_permission_expansion_fail_closed(tmp_path: Path) -> None:
    linked_root = tmp_path / "linked-state"
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    linked_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(LocalArtifactRejected):
        LocalArtifactStore(
            root_directory=linked_root,
            policy=policy(),
            id_source=Ids(),
        )

    nested_root = tmp_path / "nested-state"
    nested_root.mkdir(mode=0o700)
    nested_target = tmp_path / "nested-target"
    nested_target.mkdir(mode=0o755)
    (nested_root / "artifacts").symlink_to(nested_target, target_is_directory=True)
    with pytest.raises(LocalArtifactRejected):
        LocalArtifactStore(
            root_directory=nested_root,
            policy=policy(),
            id_source=Ids(),
        )
    assert stat.S_IMODE(nested_target.stat().st_mode) == 0o755

    root = tmp_path / "state"
    active = store(root)
    reference = active.capture(b"fixed evidence")
    artifact = root / reference.relative_path
    artifact.chmod(0o644)
    with pytest.raises(LocalArtifactRejected):
        active.read(reference)


def test_public_operations_reject_invalid_callers_payloads_and_policy_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    valid_policy = policy()
    for changes in (
        {"root_directory": cast(Path, object())},
        {"root_directory": tmp_path / "missing"},
        {"policy": cast(LocalArtifactPolicy, object())},
        {"id_source": cast(Any, object())},
    ):
        arguments: dict[str, object] = {
            "root_directory": root,
            "policy": valid_policy,
            "id_source": Ids(),
        }
        arguments.update(changes)
        with pytest.raises(LocalArtifactRejected):
            LocalArtifactStore(**cast(Any, arguments))

    active = LocalArtifactStore(root_directory=root, policy=valid_policy, id_source=Ids())
    with pytest.raises(LocalArtifactRejected):
        valid_policy.relative_path(cast(UUID, object()))
    with pytest.raises(LocalArtifactRejected):
        active.capture_generated(cast(Any, object()))
    with pytest.raises(LocalArtifactRejected):
        active.resolve(cast(UUID, object()))
    with pytest.raises(LocalArtifactRejected):
        active.read(cast(LocalArtifactRef, object()))
    for payload in (cast(bytes, object()), b"", b"x" * 2_049):
        with pytest.raises(LocalArtifactRejected):
            active.capture(payload)

    invalid_id_store = LocalArtifactStore(
        root_directory=root,
        policy=valid_policy,
        id_source=lambda: object(),
    )
    with pytest.raises(LocalArtifactRejected):
        invalid_id_store.capture(b"fixed")

    reference = active.capture(b"fixed")
    mismatches = (
        LocalArtifactRef(
            artifact_id=reference.artifact_id,
            sha256=reference.sha256,
            media_type="application/json",
            size_bytes=reference.size_bytes,
            relative_path=reference.relative_path,
        ),
        LocalArtifactRef(
            artifact_id=reference.artifact_id,
            sha256=reference.sha256,
            media_type=reference.media_type,
            size_bytes=2_049,
            relative_path=reference.relative_path,
        ),
    )
    for mismatch in mismatches:
        with pytest.raises(LocalArtifactRejected):
            active.read(mismatch)


def test_directory_and_reference_grammar_is_fully_closed() -> None:
    invalid_directories = (
        cast(str, object()),
        "",
        "a" * 385,
        "artifacts\\evidence",
        "artifacts//evidence",
        "a/b/c/d/e/f/g/h/i",
        "Artifacts/evidence",
        "artifacts/private_evidence",
        f"artifacts/{'a' * 65}",
    )
    for directory in invalid_directories:
        with pytest.raises(LocalArtifactRejected):
            LocalArtifactPolicy(
                relative_directory=directory,
                file_extension="json",
                media_type=MEDIA_TYPE,
                maximum_bytes=2_048,
                maximum_artifacts=20,
            )

    artifact_id = UUID("923e4567-e89b-42d3-a456-426614174001")
    invalid_paths = (
        cast(str, object()),
        "",
        "artifacts\\evidence\\private.json",
        "a" * 513,
        f"/{DIRECTORY}/{artifact_id}.json",
        f"artifacts//evidence/page-drift/{artifact_id}.json",
        f"{artifact_id}.json",
        f"Artifacts/evidence/page-drift/{artifact_id}.json",
        f"{DIRECTORY}/{artifact_id}.private-path",
    )
    for relative_path in invalid_paths:
        with pytest.raises(LocalArtifactRejected):
            LocalArtifactRef(
                artifact_id=artifact_id,
                sha256="a" * 64,
                media_type=MEDIA_TYPE,
                size_bytes=1,
                relative_path=relative_path,
            )


def test_leaf_replacement_malformed_uuid_and_preexisting_overflow_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    active = store(root, maximum_artifacts=1)
    artifact_directory = root / DIRECTORY
    moved = tmp_path / "moved-artifacts"
    artifact_directory.rename(moved)
    artifact_directory.mkdir(mode=0o700)
    with pytest.raises(LocalArtifactRejected):
        active.capture(b"fixed")

    malformed_root = tmp_path / "malformed"
    malformed = store(malformed_root)
    malformed_path = malformed_root / DIRECTORY / "not-a-uuid.json"
    malformed_path.write_bytes(b"x")
    malformed_path.chmod(0o600)
    with pytest.raises(LocalArtifactRejected):
        malformed.capture(b"fixed")

    overflow_root = tmp_path / "overflow"
    overflow = store(overflow_root, maximum_artifacts=1)
    overflow_directory = overflow_root / DIRECTORY
    for index in (1, 2):
        artifact_id = UUID(f"923e4567-e89b-42d3-a456-{index:012d}")
        path = overflow_directory / f"{artifact_id}.json"
        path.write_bytes(b"x")
        path.chmod(0o600)
    with pytest.raises(LocalArtifactRejected):
        overflow.capture(b"fixed")


def test_read_and_post_write_identity_races_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "state"
    active = store(root)
    reference = active.capture(b"fixed")
    original_read = os.read

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "read", lambda *_arguments: b"")
        with pytest.raises(LocalArtifactRejected):
            active.read(reference)

    with monkeypatch.context() as scoped:
        calls = 0

        def extra_byte(descriptor: int, maximum: int) -> bytes:
            nonlocal calls
            calls += 1
            return b"x" if calls == 2 else original_read(descriptor, maximum)

        scoped.setattr(os, "read", extra_byte)
        with pytest.raises(LocalArtifactRejected):
            active.read(reference)

    with monkeypatch.context() as scoped:
        identities = iter(((1, 1, 5, 1), (1, 1, 5, 2)))
        scoped.setattr(artifact_module, "_file_identity", lambda _metadata: next(identities))
        with pytest.raises(LocalArtifactRejected):
            active.read(reference)

    with monkeypatch.context() as scoped:
        identities = iter(((1, 1, 5, 1), (1, 1, 5, 1), (1, 1, 5, 1), (1, 2, 5, 1)))
        scoped.setattr(artifact_module, "_file_identity", lambda _metadata: next(identities))
        with pytest.raises(LocalArtifactRejected):
            active.read(reference)

    mismatch_root = tmp_path / "post-write"
    mismatch = store(mismatch_root)
    with monkeypatch.context() as scoped:
        original = mismatch._read_stable
        calls = 0

        def different_after_write(path: Path) -> bytes:
            nonlocal calls
            calls += 1
            return b"different" if calls == 1 else original(path)

        scoped.setattr(mismatch, "_read_stable", different_after_write)
        with pytest.raises(LocalArtifactRejected):
            mismatch.capture(b"fixed")
    assert tuple((mismatch_root / DIRECTORY).iterdir()) == ()


def test_zero_write_and_windows_acl_adapter_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "state"
    active = store(root)
    monkeypatch.setattr(os, "write", lambda *_arguments: 0)
    with pytest.raises(LocalArtifactRejected):
        active.capture(b"fixed")
    assert tuple((root / DIRECTORY).iterdir()) == ()

    calls: list[Path] = []
    adapter = ModuleType("automation_tool.executor.windows_acl")
    adapter.validate_private_acl = lambda path: calls.append(path)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "automation_tool.executor.windows_acl", adapter)
    monkeypatch.setattr(os, "name", "nt")
    artifact_module._validate_windows_private_acl(root)
    assert calls == [root]


def test_reparse_attribute_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = cast(
        os.stat_result,
        SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400),
    )
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)
    assert artifact_module._is_reparse_point(metadata) is True
