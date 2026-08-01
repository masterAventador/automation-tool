from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from automation_tool.executor.local_material_preview import (
    LocalMaterialPreviewFailureCode,
    LocalMaterialPreviewRejected,
    LocalMaterialPreviewSource,
    safe_preview_content_type,
)
from automation_tool.executor.material_probe import (
    MaterialPathRegistry,
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
)

MATERIAL_ID = UUID("123e4567-e89b-42d3-a456-426614174321")


def _state_directory(root: Path) -> Path:
    state = root / "state"
    state.mkdir(mode=0o700)
    if os.name != "nt":
        state.chmod(0o700)
    return state


def _video_facts(_tools: PackagedMediaTools, _source: Path) -> MediaStreamFacts:
    return MediaStreamFacts(
        kind=ProbedMaterialKind.VIDEO,
        duration_ms=1000,
        width=320,
        height=240,
        video_codec="h264",
        audio_codec="aac",
    )


def _mp4_bytes(payload: bytes = b"preview-payload") -> bytes:
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + payload


def test_registered_material_opens_as_an_opaque_identity_checked_lease(
    tmp_path: Path,
) -> None:
    state = _state_directory(tmp_path)
    private_source = (tmp_path / "operator-private-name.mov").resolve()
    body = _mp4_bytes()
    private_source.write_bytes(body)
    MaterialPathRegistry(state_directory=state).register(MATERIAL_ID, private_source)
    previews = LocalMaterialPreviewSource(
        state_directory=state,
        media_tools=cast(PackagedMediaTools, object()),
        stream_facts_reader=_video_facts,
    )

    lease = previews.open(MATERIAL_ID)

    assert repr(lease) == "LocalMaterialPreviewLease(<redacted>)"
    assert lease.content_type == "video/mp4"
    assert lease.size_bytes == len(body)
    assert lease.read(4, 8) == body[4:12]
    assert private_source.name not in repr(lease)
    lease.close()
    lease.close()
    with pytest.raises(LocalMaterialPreviewRejected) as closed:
        lease.read(0, 1)
    assert closed.value.code is LocalMaterialPreviewFailureCode.FILE_CHANGED


def test_open_lease_aborts_if_the_open_file_changes_in_place(tmp_path: Path) -> None:
    state = _state_directory(tmp_path)
    private_source = (tmp_path / "private.mp4").resolve()
    body = _mp4_bytes(b"first")
    private_source.write_bytes(body)
    MaterialPathRegistry(state_directory=state).register(MATERIAL_ID, private_source)
    previews = LocalMaterialPreviewSource(
        state_directory=state,
        media_tools=cast(PackagedMediaTools, object()),
        stream_facts_reader=_video_facts,
    )
    lease = previews.open(MATERIAL_ID)
    private_source.write_bytes(_mp4_bytes(b"other"))
    os.utime(private_source, ns=(private_source.stat().st_atime_ns, 1))

    with pytest.raises(LocalMaterialPreviewRejected) as changed:
        lease.read(0, len(body))

    assert changed.value.code is LocalMaterialPreviewFailureCode.FILE_CHANGED
    assert str(private_source) not in str(changed.value)
    assert str(private_source) not in repr(changed.value)
    lease.close()


def test_missing_mapping_and_replaced_file_return_only_closed_codes(
    tmp_path: Path,
) -> None:
    state = _state_directory(tmp_path)
    private_source = (tmp_path / "private.mp4").resolve()
    private_source.write_bytes(_mp4_bytes())
    registry = MaterialPathRegistry(state_directory=state)
    registry.register(MATERIAL_ID, private_source)
    previews = LocalMaterialPreviewSource(
        state_directory=state,
        media_tools=cast(PackagedMediaTools, object()),
        stream_facts_reader=_video_facts,
    )
    private_source.unlink()

    with pytest.raises(LocalMaterialPreviewRejected) as missing:
        previews.open(MATERIAL_ID)
    with pytest.raises(LocalMaterialPreviewRejected) as unknown:
        previews.open(UUID("8e48954d-2df1-4168-8f33-b62c5772845c"))

    assert missing.value.code is LocalMaterialPreviewFailureCode.FILE_MISSING
    assert unknown.value.code is LocalMaterialPreviewFailureCode.NOT_REGISTERED
    for error in (missing.value, unknown.value):
        assert str(error) == "local material preview rejected"
        assert str(private_source) not in repr(error)


def test_content_type_probe_is_cached_per_registered_identity(tmp_path: Path) -> None:
    state = _state_directory(tmp_path)
    private_source = (tmp_path / "private.mp4").resolve()
    private_source.write_bytes(_mp4_bytes())
    registry = MaterialPathRegistry(state_directory=state)
    registry.register(MATERIAL_ID, private_source)
    calls: list[Path] = []

    def facts(tools: PackagedMediaTools, source: Path) -> MediaStreamFacts:
        calls.append(source)
        return _video_facts(tools, source)

    previews = LocalMaterialPreviewSource(
        state_directory=state,
        media_tools=cast(PackagedMediaTools, object()),
        stream_facts_reader=facts,
    )
    previews.open(MATERIAL_ID).close()
    previews.open(MATERIAL_ID).close()
    assert calls == [private_source]

    private_source.write_bytes(_mp4_bytes(b"new identity"))
    registry.register(MATERIAL_ID, private_source)
    previews.open(MATERIAL_ID).close()
    assert calls == [private_source, private_source]


def test_mapping_remap_during_open_never_returns_the_previous_file(tmp_path: Path) -> None:
    state = _state_directory(tmp_path)
    first = (tmp_path / "first.mp4").resolve()
    second = (tmp_path / "second.mp4").resolve()
    first.write_bytes(_mp4_bytes(b"first"))
    second.write_bytes(_mp4_bytes(b"second mapping"))
    registry = MaterialPathRegistry(state_directory=state)
    registry.register(MATERIAL_ID, first)

    def remap_then_return_facts(tools: PackagedMediaTools, source: Path) -> MediaStreamFacts:
        assert source == first
        registry.register(MATERIAL_ID, second)
        return _video_facts(tools, source)

    previews = LocalMaterialPreviewSource(
        state_directory=state,
        media_tools=cast(PackagedMediaTools, object()),
        stream_facts_reader=remap_then_return_facts,
    )

    with pytest.raises(LocalMaterialPreviewRejected) as changed:
        previews.open(MATERIAL_ID)

    assert changed.value.code is LocalMaterialPreviewFailureCode.FILE_CHANGED


@pytest.mark.parametrize(
    ("kind", "header", "expected"),
    [
        (ProbedMaterialKind.VIDEO, _mp4_bytes(), "video/mp4"),
        (ProbedMaterialKind.AUDIO, b"ID3\x04\x00\x00\x00\x00\x00\x00", "audio/mpeg"),
        (ProbedMaterialKind.AUDIO, b"fLaC\x00\x00\x00\x22", "audio/flac"),
        (ProbedMaterialKind.AUDIO, b"RIFF\x10\x00\x00\x00WAVEfmt ", "audio/wav"),
        (ProbedMaterialKind.IMAGE, b"\x89PNG\r\n\x1a\n", "image/png"),
        (ProbedMaterialKind.IMAGE, b"\xff\xd8\xff\xe0", "image/jpeg"),
        (ProbedMaterialKind.IMAGE, b"GIF89a", "image/gif"),
        (ProbedMaterialKind.IMAGE, b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image/webp"),
    ],
)
def test_mime_is_a_closed_intersection_of_probe_kind_and_magic(
    kind: ProbedMaterialKind,
    header: bytes,
    expected: str,
) -> None:
    assert safe_preview_content_type(kind, header) == expected


def test_mime_rejects_extension_only_unknown_and_kind_mismatch() -> None:
    for kind, header in (
        (ProbedMaterialKind.VIDEO, b"not really an mp4"),
        (ProbedMaterialKind.IMAGE, _mp4_bytes()),
        (ProbedMaterialKind.AUDIO, b"\x89PNG\r\n\x1a\n"),
    ):
        with pytest.raises(LocalMaterialPreviewRejected) as rejected:
            safe_preview_content_type(kind, header)
        assert rejected.value.code is LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA
