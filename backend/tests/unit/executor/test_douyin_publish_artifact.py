from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.rpa.douyin.publish_artifact import (
    DOUYIN_PUBLISH_ARTIFACT_MEDIA_TYPES,
    MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES,
    DouyinPublishArtifact,
    DouyinPublishArtifactRejected,
    open_publish_artifact,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/publishing/douyin-browser-use-preflight.v1.json"
PAYLOAD = b"\x00\x00\x00\x18ftypmp42automation-tool-pb-05-fixture"


def write_artifact(directory: Path, name: str = "clip.mp4", payload: bytes = PAYLOAD) -> Path:
    path = directory / name
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def test_contract_pins_the_artifact_boundary() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["artifact"]
    assert contract["maximumBytes"] == MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES
    assert dict(DOUYIN_PUBLISH_ARTIFACT_MEDIA_TYPES) == contract["mediaTypes"]


def test_known_video_extensions_resolve_with_a_streamed_digest(tmp_path: Path) -> None:
    for name, media_type in (("clip.mp4", "video/mp4"), ("clip.mov", "video/quicktime")):
        artifact = open_publish_artifact(write_artifact(tmp_path, name))
        assert artifact.media_type == media_type
        assert artifact.size_bytes == len(PAYLOAD)
        assert artifact.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        assert artifact.path.name == name


def test_artifact_repr_never_exposes_the_local_path(tmp_path: Path) -> None:
    artifact = open_publish_artifact(write_artifact(tmp_path))
    text = repr(artifact)
    assert str(tmp_path) not in text
    assert "clip.mp4" not in text
    assert artifact.sha256[:12] in text


def test_relative_and_untyped_paths_are_rejected(tmp_path: Path) -> None:
    write_artifact(tmp_path)
    for candidate in (
        "clip.mp4",
        Path("clip.mp4"),
        cast(Any, None),
        cast(Any, 7),
        cast(Any, b"/tmp/clip.mp4"),
        tmp_path / ".." / tmp_path.name / "clip.mp4",
    ):
        with pytest.raises(DouyinPublishArtifactRejected):
            open_publish_artifact(candidate)


def test_missing_directory_and_special_files_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "folder.mp4").mkdir()
    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(tmp_path / "folder.mp4")
    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(tmp_path / "absent.mp4")


def require_symlink_support(tmp_path: Path) -> None:
    """Windows only allows symlinks with developer mode or elevation."""
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(tmp_path)
    except (OSError, NotImplementedError):
        pytest.skip("this platform does not allow creating symlinks")
    probe.unlink()


def test_symlinked_artifacts_are_rejected(tmp_path: Path) -> None:
    require_symlink_support(tmp_path)
    target = write_artifact(tmp_path)
    link = tmp_path / "link.mp4"
    link.symlink_to(target)
    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(link)


def test_symlinked_ancestors_are_rejected(tmp_path: Path) -> None:
    require_symlink_support(tmp_path)
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    write_artifact(real_directory)
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(linked_directory / "clip.mp4")


def test_hard_linked_artifacts_are_rejected(tmp_path: Path) -> None:
    target = write_artifact(tmp_path)
    os.link(target, tmp_path / "second.mp4")
    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(target)


def test_unknown_media_extensions_are_rejected(tmp_path: Path) -> None:
    for name in ("clip.txt", "clip.exe", "clip.MP4", "clip.mp4.txt", "clip", "clip."):
        path = tmp_path / name
        path.write_bytes(PAYLOAD)
        with pytest.raises(DouyinPublishArtifactRejected):
            open_publish_artifact(path)


def test_empty_and_oversized_artifacts_are_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(empty)
    oversized = write_artifact(tmp_path, "big.mp4")
    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(oversized, maximum_bytes=len(PAYLOAD) - 1)


def test_maximum_bytes_bounds_are_enforced(tmp_path: Path) -> None:
    path = write_artifact(tmp_path)
    for invalid in (0, -1, MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES + 1, 1.5, "1024"):
        with pytest.raises(DouyinPublishArtifactRejected):
            open_publish_artifact(path, maximum_bytes=cast(Any, invalid))


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "chmod(0) only sets the read-only attribute on Windows; the file stays "
        "readable, so this is a POSIX-only way to build an unreadable file. "
        "This path is not covered on Windows yet - see "
        "docs/development/windows-evidence-checklist.md section 18."
    ),
)
def test_unreadable_artifacts_are_rejected(tmp_path: Path) -> None:
    path = write_artifact(tmp_path)
    path.chmod(0o000)
    try:
        with pytest.raises(DouyinPublishArtifactRejected):
            open_publish_artifact(path)
    finally:
        path.chmod(0o600)


def test_path_substitution_after_resolution_is_rejected_on_revalidation(tmp_path: Path) -> None:
    path = write_artifact(tmp_path)
    artifact = open_publish_artifact(path)
    artifact.revalidate()
    path.unlink()
    write_artifact(tmp_path, "clip.mp4", PAYLOAD + b"tampered")
    with pytest.raises(DouyinPublishArtifactRejected):
        artifact.revalidate()


def test_artifact_construction_requires_consistent_facts(tmp_path: Path) -> None:
    path = write_artifact(tmp_path)
    with pytest.raises(DouyinPublishArtifactRejected):
        DouyinPublishArtifact(
            path=path,
            media_type="application/octet-stream",
            size_bytes=len(PAYLOAD),
            sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        )
    with pytest.raises(DouyinPublishArtifactRejected):
        DouyinPublishArtifact(
            path=path,
            media_type="video/mp4",
            size_bytes=len(PAYLOAD),
            sha256="not-a-digest",
        )


def test_revalidation_refuses_a_file_that_changed_since_it_was_opened(tmp_path: Path) -> None:
    """Proven once at preflight and again right before the upload: same file or none."""
    path = write_artifact(tmp_path)
    artifact = open_publish_artifact(path)

    path.write_bytes(PAYLOAD[:-1] + b"X")
    path.chmod(0o600)

    with pytest.raises(DouyinPublishArtifactRejected):
        artifact.revalidate()


def test_revalidation_accepts_the_same_file_it_first_proved(tmp_path: Path) -> None:
    artifact = open_publish_artifact(write_artifact(tmp_path))

    artifact.revalidate()


def test_a_reading_budget_outside_the_platform_ceiling_is_refused(tmp_path: Path) -> None:
    from automation_tool.executor.rpa.douyin.publish_artifact import (
        MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES,
    )

    path = write_artifact(tmp_path)
    for label, maximum in [
        ("a budget that is not an int", cast(int, 1.0)),
        ("a budget of zero", 0),
        ("a budget past the platform ceiling", MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES + 1),
    ]:
        with pytest.raises(DouyinPublishArtifactRejected):
            open_publish_artifact(path, maximum_bytes=maximum)
        assert label


def test_a_file_that_changes_while_it_is_digested_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opened, sized and hashed are three moments; the descriptor has to agree at the end."""
    path = write_artifact(tmp_path)
    real_read = os.read

    def truncating(descriptor: int, size: int) -> bytes:
        return b""

    def growing(descriptor: int, size: int) -> bytes:
        chunk = real_read(descriptor, size)
        return chunk if chunk else b"extra"

    for label, hook in [
        ("the file ran out early", truncating),
        ("the file grew past its declared size", growing),
    ]:
        monkeypatch.setattr(os, "read", hook)
        with pytest.raises(DouyinPublishArtifactRejected):
            open_publish_artifact(path)
        monkeypatch.undo()
        assert label


def test_a_file_replaced_between_the_descriptor_and_the_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_artifact(tmp_path)
    real_fstat = os.fstat
    calls = 0

    def drifting(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        metadata = real_fstat(descriptor)
        if calls == 1:
            return metadata
        fields = list(metadata)
        fields[os.stat_result.n_fields - 1 if False else 1] = metadata.st_ino + 1
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", drifting)

    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(path)


def test_an_artifact_with_more_than_one_name_is_refused(tmp_path: Path) -> None:
    """A hard link means something else can change the bytes behind this name."""
    path = write_artifact(tmp_path)
    (tmp_path / "second-name.mp4").hardlink_to(path)

    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(path)


def test_a_file_swapped_after_the_descriptor_closed_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The digest belongs to a descriptor; the caller later opens the path again."""
    path = write_artifact(tmp_path)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(PAYLOAD)
    replacement.chmod(0o600)
    real_close = os.close
    swapped = False

    def swapping(descriptor: int) -> None:
        nonlocal swapped
        real_close(descriptor)
        if not swapped:
            swapped = True
            os.replace(replacement, path)

    monkeypatch.setattr(os, "close", swapping)

    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(path)


def test_an_artifact_owned_by_another_account_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Something another account can rewrite is not ours to prove."""
    path = write_artifact(tmp_path)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(path).st_uid + 1)

    with pytest.raises(DouyinPublishArtifactRejected):
        open_publish_artifact(path)
