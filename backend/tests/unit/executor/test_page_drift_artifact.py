from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from automation_tool.executor.local_artifact import LocalArtifactRef, LocalArtifactStore
from automation_tool.executor.page_drift_artifact import (
    MAX_PAGE_DRIFT_ARTIFACTS,
    PAGE_DRIFT_ARTIFACT_DIRECTORY,
    PAGE_DRIFT_ARTIFACT_MEDIA_TYPE,
    PAGE_DRIFT_ARTIFACT_POLICY,
    PageDriftArtifactRef,
    PageDriftArtifactRejected,
    PageDriftArtifactStore,
    SystemPageDriftArtifactClock,
)

NOW = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)


class Clock:
    @staticmethod
    def now() -> datetime:
        return NOW


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"923e4567-e89b-42d3-a456-{self.value:012d}")


def store(state_directory: Path) -> PageDriftArtifactStore:
    state_directory.mkdir(mode=0o700)
    return PageDriftArtifactStore(
        state_directory=state_directory,
        clock=Clock(),
        id_source=Ids(),
    )


def test_capture_writes_one_bounded_private_fixed_schema_artifact(tmp_path: Path) -> None:
    active = store(tmp_path / "state")

    reference = active.capture(
        evidence="page_version_unknown",
        page_revision=7,
        stage="search",
    )

    assert isinstance(reference, LocalArtifactRef)
    expected_relative = f"{PAGE_DRIFT_ARTIFACT_DIRECTORY}/{reference.artifact_id}.json"
    assert reference.relative_path == expected_relative
    assert reference.media_type == PAGE_DRIFT_ARTIFACT_MEDIA_TYPE
    artifact_path = tmp_path / "state" / reference.relative_path
    payload = artifact_path.read_bytes()
    generic_store = LocalArtifactStore(
        root_directory=tmp_path / "state",
        policy=PAGE_DRIFT_ARTIFACT_POLICY,
    )
    assert generic_store.read(reference) == payload
    assert reference.size_bytes == len(payload)
    assert reference.sha256 == hashlib.sha256(payload).hexdigest()
    assert reference.size_bytes <= 2048
    document = json.loads(payload)
    assert document == {
        "artifact_id": str(reference.artifact_id),
        "artifact_version": "executor.page-drift-artifact.v1",
        "evidence": "page_version_unknown",
        "observed_at": "2026-07-20T05:00:00Z",
        "operation": "douyin_target_discovery",
        "page_revision": 7,
        "platform": "douyin",
        "stage": "search",
    }
    assert "keyword" not in document
    assert "url" not in document
    assert "html" not in document
    assert "cookie" not in document
    if os.name != "nt":
        assert stat.S_IMODE(artifact_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(artifact_path.stat().st_mode) == 0o600


def test_capture_is_exclusive_and_refuses_to_exceed_the_count_bound(tmp_path: Path) -> None:
    active = store(tmp_path / "state")

    for revision in range(1, MAX_PAGE_DRIFT_ARTIFACTS + 1):
        active.capture(
            evidence="conflicting_anchors",
            page_revision=revision,
            stage="search",
        )

    with pytest.raises(PageDriftArtifactRejected):
        active.capture(
            evidence="page_version_unknown",
            page_revision=MAX_PAGE_DRIFT_ARTIFACTS + 1,
            stage="search",
        )
    assert len(tuple((tmp_path / "state" / PAGE_DRIFT_ARTIFACT_DIRECTORY).iterdir())) == (
        MAX_PAGE_DRIFT_ARTIFACTS
    )


def test_invalid_inputs_and_private_path_replacement_fail_closed(tmp_path: Path) -> None:
    active = store(tmp_path / "state")
    invalid = (
        {"evidence": "private_dom"},
        {"page_revision": 0},
        {"page_revision": True},
        {"stage": "private-stage"},
    )
    for changes in invalid:
        arguments: dict[str, object] = {
            "evidence": "page_version_unknown",
            "page_revision": 7,
            "stage": "search",
        }
        arguments.update(changes)
        with pytest.raises(PageDriftArtifactRejected):
            active.capture(**cast(Any, arguments))

    artifact_directory = tmp_path / "state" / PAGE_DRIFT_ARTIFACT_DIRECTORY
    artifact_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    (artifact_directory / "unexpected.txt").write_text("private", encoding="utf-8")
    with pytest.raises(PageDriftArtifactRejected):
        active.capture(evidence="page_version_unknown", page_revision=7, stage="search")


def test_constructor_and_write_failures_are_fixed_and_leave_no_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    for arguments in (
        {"state_directory": cast(Path, object())},
        {"state_directory": tmp_path / "missing"},
        {"clock": object()},
        {"id_source": object()},
    ):
        values: dict[str, object] = {
            "state_directory": state_directory,
            "clock": Clock(),
            "id_source": Ids(),
        }
        values.update(arguments)
        with pytest.raises(PageDriftArtifactRejected):
            PageDriftArtifactStore(**cast(Any, values))

    active = PageDriftArtifactStore(
        state_directory=state_directory,
        clock=Clock(),
        id_source=Ids(),
    )
    monkeypatch.setattr(
        os,
        "open",
        lambda *_arguments, **_keywords: (_ for _ in ()).throw(OSError("disk full private")),
    )
    with pytest.raises(
        PageDriftArtifactRejected,
        match=r"^page drift artifact is unavailable$",
    ) as captured:
        active.capture(evidence="page_version_unknown", page_revision=7, stage="search")
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    artifact_directory = state_directory / PAGE_DRIFT_ARTIFACT_DIRECTORY
    assert not artifact_directory.exists() or tuple(artifact_directory.iterdir()) == ()


def test_reference_clock_identity_and_existing_artifact_validation_fail_closed(
    tmp_path: Path,
) -> None:
    assert SystemPageDriftArtifactClock().now().utcoffset() == UTC.utcoffset(NOW)
    artifact_id = UUID("923e4567-e89b-42d3-a456-426614174001")
    valid = {
        "artifact_id": artifact_id,
        "sha256": "a" * 64,
        "media_type": PAGE_DRIFT_ARTIFACT_MEDIA_TYPE,
        "size_bytes": 1,
        "relative_path": f"{PAGE_DRIFT_ARTIFACT_DIRECTORY}/{artifact_id}.json",
    }
    for changes in (
        {"artifact_id": cast(Any, object())},
        {"artifact_id": UUID(int=4)},
        {"sha256": "A" * 64},
        {"media_type": "application/json"},
        {"size_bytes": 0},
        {"relative_path": "private.json"},
    ):
        arguments = dict(valid)
        arguments.update(changes)
        with pytest.raises(PageDriftArtifactRejected):
            PageDriftArtifactRef(**cast(Any, arguments))

    state_directory = tmp_path / "identity-state"
    active = store(state_directory)
    moved = tmp_path / "moved-state"
    state_directory.rename(moved)
    state_directory.mkdir(mode=0o700)
    with pytest.raises(PageDriftArtifactRejected):
        active.capture(evidence="page_version_unknown", page_revision=7, stage="search")

    file_state = tmp_path / "file-state"
    file_state.write_text("private", encoding="utf-8")
    with pytest.raises(PageDriftArtifactRejected):
        PageDriftArtifactStore(state_directory=file_state)


def test_invalid_clock_id_and_preexisting_overflow_fail_closed(tmp_path: Path) -> None:
    class InvalidClock:
        @staticmethod
        def now() -> object:
            return object()

    invalid_clock_state = tmp_path / "invalid-clock"
    invalid_clock_state.mkdir(mode=0o700)
    invalid_clock = PageDriftArtifactStore(
        state_directory=invalid_clock_state,
        clock=cast(Any, InvalidClock()),
    )
    with pytest.raises(PageDriftArtifactRejected):
        invalid_clock.capture(evidence="page_version_unknown", page_revision=7, stage="search")

    invalid_id_state = tmp_path / "invalid-id"
    invalid_id_state.mkdir(mode=0o700)
    invalid_id = PageDriftArtifactStore(
        state_directory=invalid_id_state,
        clock=Clock(),
        id_source=lambda: object(),
    )
    with pytest.raises(PageDriftArtifactRejected):
        invalid_id.capture(evidence="page_version_unknown", page_revision=7, stage="search")

    overflow_state = tmp_path / "overflow"
    overflow_state.mkdir(mode=0o700)
    overflow_store = PageDriftArtifactStore(state_directory=overflow_state)
    artifact_directory = overflow_state / PAGE_DRIFT_ARTIFACT_DIRECTORY
    artifact_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for index in range(1, MAX_PAGE_DRIFT_ARTIFACTS + 2):
        artifact_id = UUID(f"923e4567-e89b-42d3-a456-{index:012d}")
        artifact_path = artifact_directory / f"{artifact_id}.json"
        artifact_path.write_bytes(b"x")
        if os.name != "nt":
            artifact_path.chmod(0o600)
    with pytest.raises(PageDriftArtifactRejected):
        overflow_store.capture(evidence="page_version_unknown", page_revision=7, stage="search")


@pytest.mark.parametrize("entry_kind", ("unknown", "empty", "oversized", "directory", "symlink"))
def test_every_untrusted_artifact_directory_entry_fails_closed(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    if entry_kind == "symlink" and os.name == "nt":
        pytest.skip("Windows symlink creation requires a privileged developer configuration")
    state_directory = tmp_path / entry_kind
    state_directory.mkdir(mode=0o700)
    active = PageDriftArtifactStore(state_directory=state_directory)
    artifact_directory = state_directory / PAGE_DRIFT_ARTIFACT_DIRECTORY
    artifact_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    artifact_id = UUID("923e4567-e89b-42d3-a456-426614174001")
    entry = artifact_directory / f"{artifact_id}.json"
    if entry_kind == "unknown":
        entry = artifact_directory / "unexpected.txt"
        entry.write_bytes(b"x")
    elif entry_kind == "empty":
        entry.write_bytes(b"")
    elif entry_kind == "oversized":
        entry.write_bytes(b"x" * 2049)
    elif entry_kind == "directory":
        entry.mkdir()
    else:
        target = tmp_path / "private-target"
        target.write_bytes(b"x")
        entry.symlink_to(target)

    with pytest.raises(PageDriftArtifactRejected):
        active.capture(evidence="page_version_unknown", page_revision=7, stage="search")


def test_partial_write_failures_remove_the_exclusive_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    active = PageDriftArtifactStore(
        state_directory=state_directory,
        clock=Clock(),
        id_source=Ids(),
    )
    original_write = os.write
    monkeypatch.setattr(
        os,
        "write",
        lambda *_arguments, **_keywords: (_ for _ in ()).throw(OSError("private write")),
    )
    with pytest.raises(PageDriftArtifactRejected):
        active.capture(evidence="page_version_unknown", page_revision=7, stage="search")
    artifact_directory = state_directory / PAGE_DRIFT_ARTIFACT_DIRECTORY
    assert tuple(artifact_directory.iterdir()) == ()

    monkeypatch.setattr(os, "write", original_write)
    monkeypatch.setattr(
        os,
        "fsync",
        lambda *_arguments: (_ for _ in ()).throw(OSError("private fsync")),
    )
    with pytest.raises(PageDriftArtifactRejected):
        active.capture(evidence="page_version_unknown", page_revision=7, stage="search")
    assert tuple(artifact_directory.iterdir()) == ()
