from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO, cast
from unittest import mock
from uuid import UUID

import pytest

from automation_tool.executor.local_material_preview import (
    _MAX_CONTENT_TYPE_CACHE_ENTRIES,
    LocalMaterialPreviewFailureCode,
    LocalMaterialPreviewLease,
    LocalMaterialPreviewRejected,
    LocalMaterialPreviewSource,
    safe_preview_content_type,
)
from automation_tool.executor.material_probe import (
    MaterialPathRegistry,
    MaterialProbeRejected,
    MaterialProbeRejection,
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


def _image_facts(_tools: PackagedMediaTools, _source: Path) -> MediaStreamFacts:
    return MediaStreamFacts(
        kind=ProbedMaterialKind.IMAGE,
        duration_ms=None,
        width=320,
        height=240,
        video_codec=None,
        audio_codec=None,
    )


def _previews(
    state: Path,
    reader: Callable[[PackagedMediaTools, Path], MediaStreamFacts] = _video_facts,
) -> LocalMaterialPreviewSource:
    return LocalMaterialPreviewSource(
        state_directory=state,
        media_tools=cast(PackagedMediaTools, object()),
        stream_facts_reader=reader,
    )


def _registered(tmp_path: Path, body: bytes = _mp4_bytes()) -> tuple[Path, Path]:
    state = _state_directory(tmp_path)
    private_source = (tmp_path / "private.mp4").resolve()
    private_source.write_bytes(body)
    MaterialPathRegistry(state_directory=state).register(MATERIAL_ID, private_source)
    return state, private_source


@contextmanager
def _after_opening(target: Path, callback: Callable[[], None]) -> Iterator[None]:
    """Run `callback` the moment the preview source opens `target`.

    Several of the refusals below guard a window that only exists between two
    of `open`'s own steps -- the file is opened, and by the time its header or
    the mapping is read again the world has moved. Nothing else in the call is
    reachable from a test, so the hook is placed on the one operation that
    starts that window.
    """
    original = Path.open

    def opening(self: Path, *args: Any, **options: Any) -> Any:
        stream = original(self, *args, **options)
        if self == target:
            callback()
        return stream

    with mock.patch.object(Path, "open", opening):
        yield


class _RefusingStream:
    """A real descriptor whose reads fail, which a closed file cannot express."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self.closed_once = False

    def fileno(self) -> int:
        return self._stream.fileno()

    def read(self, _length: int = -1) -> bytes:
        raise OSError("input/output error")

    def seek(self, _offset: int, _whence: int = 0) -> int:
        raise OSError("input/output error")

    def close(self) -> None:
        self.closed_once = True
        self._stream.close()


class _ShortStream(_RefusingStream):
    def read(self, length: int = -1) -> bytes:
        return self._stream.read(max(0, length - 1))

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._stream.seek(offset, whence)


def test_probe_reasons_map_onto_the_preview_vocabulary(tmp_path: Path) -> None:
    """Each probe reason names a different next step, so none may be flattened."""
    state, private_source = _registered(tmp_path)

    for label, rejection, expected in [
        (
            "the file cannot be read",
            MaterialProbeRejection.UNREADABLE,
            LocalMaterialPreviewFailureCode.FILE_UNREADABLE,
        ),
        (
            "the file is still being written",
            MaterialProbeRejection.SOURCE_NOT_AT_REST,
            LocalMaterialPreviewFailureCode.FILE_CHANGED,
        ),
        (
            "nothing here can be played",
            MaterialProbeRejection.UNDECODABLE,
            LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA,
        ),
    ]:

        def refuse(
            _tools: PackagedMediaTools,
            _source: Path,
            _rejection: MaterialProbeRejection = rejection,
        ) -> MediaStreamFacts:
            raise MaterialProbeRejected(_rejection)

        with pytest.raises(LocalMaterialPreviewRejected) as caught:
            _previews(state, refuse).open(MATERIAL_ID)

        assert caught.value.code is expected, label
        assert str(private_source) not in str(caught.value), label


def test_the_preview_source_never_prints_where_it_reads_from(tmp_path: Path) -> None:
    state, _private_source = _registered(tmp_path)

    assert repr(_previews(state)) == "LocalMaterialPreviewSource(<redacted>)"


def test_a_probe_kind_outside_the_closed_set_is_unsupported(tmp_path: Path) -> None:
    state, _private_source = _registered(tmp_path)

    def foreign_kind(_tools: PackagedMediaTools, _source: Path) -> MediaStreamFacts:
        facts = _video_facts(_tools, _source)
        return replace(facts, kind=cast(ProbedMaterialKind, "video"))

    with pytest.raises(LocalMaterialPreviewRejected) as caught:
        _previews(state, foreign_kind).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA


def test_the_content_type_cache_evicts_its_oldest_entry(tmp_path: Path) -> None:
    """Bounded so a long-running executor cannot grow one entry per material."""
    state, _private_source = _registered(tmp_path)
    previews = _previews(state)
    oldest = UUID("00000000-0000-4000-8000-000000000000")
    for index in range(_MAX_CONTENT_TYPE_CACHE_ENTRIES):
        previews._content_types[UUID(int=index) if index else oldest] = (
            (1, index, 0, 0),
            ProbedMaterialKind.VIDEO,
            "video/mp4",
        )

    previews.open(MATERIAL_ID).close()

    assert len(previews._content_types) == _MAX_CONTENT_TYPE_CACHE_ENTRIES
    assert oldest not in previews._content_types
    assert MATERIAL_ID in previews._content_types


@contextmanager
def _refusing_to_open(target: Path) -> Iterator[None]:
    """The approved file will not open, and only that file.

    Revoking the mode instead would be refused one step earlier, by the identity
    re-check, which also opens the file -- so both arms would collapse onto the
    probe reason and this branch would never run.
    """
    original = Path.open

    def opening(self: Path, *args: Any, **options: Any) -> Any:
        if self == target:
            raise PermissionError("operation not permitted")
        return original(self, *args, **options)

    with mock.patch.object(Path, "open", opening):
        yield


def test_a_file_that_cannot_be_opened_is_reported_without_naming_it(tmp_path: Path) -> None:
    """The mapping still resolves, so the failure is about the file, not the library."""
    state, private_source = _registered(tmp_path)

    with _refusing_to_open(private_source), pytest.raises(LocalMaterialPreviewRejected) as caught:
        _previews(state).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_UNREADABLE
    assert str(private_source) not in str(caught.value)


def test_a_library_that_became_unreadable_replaces_the_file_reason(tmp_path: Path) -> None:
    """Re-resolving is how the two are told apart, so its own failure wins.

    The library is relaxed only after the first resolve has already succeeded --
    relaxing it up front would be refused there instead, and this branch, which
    exists precisely for a library that changed mid-open, would never run.
    """
    state, private_source = _registered(tmp_path)

    def relax_the_library(_tools: PackagedMediaTools, source: Path) -> MediaStreamFacts:
        facts = _video_facts(_tools, source)
        state.chmod(0o755)
        return facts

    with _refusing_to_open(private_source), pytest.raises(LocalMaterialPreviewRejected) as caught:
        _previews(state, relax_the_library).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.REGISTRY_UNREADABLE
    state.chmod(0o700)


def test_a_descriptor_that_cannot_be_inspected_is_closed_before_refusing(
    tmp_path: Path,
) -> None:
    """Opened but unusable: the descriptor must not survive the refusal."""
    state, private_source = _registered(tmp_path)
    opened: list[Any] = []
    descriptors: set[int] = set()
    original = Path.open

    def recording_open(self: Path, *args: Any, **options: Any) -> Any:
        stream = original(self, *args, **options)
        if self == private_source:
            opened.append(stream)
            descriptors.add(stream.fileno())
        return stream

    real_fstat = os.fstat
    refused = False

    def refusing_fstat(descriptor: int) -> os.stat_result:
        # Only the preview's own descriptor, and only once: the library's
        # document is read through this call too, and the kernel hands its
        # descriptor the number the closed preview just gave back.
        nonlocal refused
        if not refused and descriptor in descriptors:
            refused = True
            raise OSError("bad file descriptor")
        return real_fstat(descriptor)

    with (
        mock.patch.object(Path, "open", recording_open),
        mock.patch.object(os, "fstat", refusing_fstat),
        pytest.raises(LocalMaterialPreviewRejected) as caught,
    ):
        _previews(state).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_UNREADABLE
    assert [stream.closed for stream in opened] == [True]


def test_an_opened_file_that_is_not_the_approved_one_is_refused(tmp_path: Path) -> None:
    """The approval and the descriptor must describe the same file, mode included.

    `fstat` is what answers that, and the window it guards -- between approving
    the path and holding a descriptor on it -- cannot be entered from outside.
    Its answer is therefore supplied directly, which is the only way to be in it.
    """
    state, private_source = _registered(tmp_path)
    real_fstat = os.fstat
    source_inode = private_source.stat().st_ino
    calls = 0

    def drifting(descriptor: int) -> os.stat_result:
        nonlocal calls
        metadata = real_fstat(descriptor)
        if metadata.st_ino != source_inode:
            return metadata
        calls += 1
        if calls > 1:
            return metadata
        fields = list(metadata)
        fields[stat.ST_MODE] = stat.S_IFREG | 0o644
        return os.stat_result(fields)

    with (
        mock.patch.object(os, "fstat", drifting),
        pytest.raises(LocalMaterialPreviewRejected) as caught,
    ):
        _previews(state).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_CHANGED


def test_a_header_that_cannot_be_read_is_a_file_problem(tmp_path: Path) -> None:
    state, private_source = _registered(tmp_path)
    original = Path.open
    wrapped: list[_RefusingStream] = []

    def refusing_open(self: Path, *args: Any, **options: Any) -> Any:
        stream = original(self, *args, **options)
        if self != private_source:
            return stream
        wrapper = _RefusingStream(stream)
        wrapped.append(wrapper)
        return wrapper

    with (
        mock.patch.object(Path, "open", refusing_open),
        pytest.raises(LocalMaterialPreviewRejected) as caught,
    ):
        _previews(state).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_UNREADABLE
    assert [wrapper.closed_once for wrapper in wrapped] == [True]


def test_a_file_that_changes_while_its_header_is_read_is_refused(tmp_path: Path) -> None:
    state, private_source = _registered(tmp_path)
    real_fstat = os.fstat
    source_inode = private_source.stat().st_ino
    calls = 0

    def drifting(descriptor: int) -> os.stat_result:
        nonlocal calls
        metadata = real_fstat(descriptor)
        if metadata.st_ino != source_inode:
            return metadata
        calls += 1
        if calls != 2:
            return metadata
        fields = list(metadata)
        fields[stat.ST_SIZE] = metadata.st_size + 1
        return os.stat_result(fields)

    with (
        mock.patch.object(os, "fstat", drifting),
        pytest.raises(LocalMaterialPreviewRejected) as caught,
    ):
        _previews(state).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_CHANGED
    assert str(private_source) not in str(caught.value)


def test_a_header_no_content_type_matches_closes_the_file(tmp_path: Path) -> None:
    state, _private_source = _registered(tmp_path)

    with pytest.raises(LocalMaterialPreviewRejected) as caught:
        _previews(state, _image_facts).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA


def test_a_cache_entry_evicted_mid_open_is_not_written_back(tmp_path: Path) -> None:
    """Another preview may have pushed this one out; the stale key is not revived."""
    state, private_source = _registered(tmp_path)
    previews = _previews(state)

    def evict() -> None:
        previews._content_types.clear()

    with _after_opening(private_source, evict):
        lease = previews.open(MATERIAL_ID)

    assert lease.content_type == "video/mp4"
    assert MATERIAL_ID not in previews._content_types
    lease.close()


def test_a_mapping_that_becomes_unresolvable_mid_open_closes_the_file(
    tmp_path: Path,
) -> None:
    state, private_source = _registered(tmp_path)

    def revoke_library() -> None:
        state.chmod(0o755)

    with (
        _after_opening(private_source, revoke_library),
        pytest.raises(LocalMaterialPreviewRejected) as caught,
    ):
        _previews(state).open(MATERIAL_ID)

    assert caught.value.code is LocalMaterialPreviewFailureCode.REGISTRY_UNREADABLE
    state.chmod(0o700)


def test_a_lease_refuses_a_range_outside_the_file(tmp_path: Path) -> None:
    state, _private_source = _registered(tmp_path)
    lease = _previews(state).open(MATERIAL_ID)
    size = lease.size_bytes

    for label, start, length in [
        ("a start that is not an int", cast(int, 1.0), 1),
        ("a length that is not an int", 0, cast(int, 1.0)),
        ("a negative start", -1, 1),
        ("a length of zero", 0, 0),
        ("a negative length", 0, -1),
        ("a start past the end", size + 1, 1),
        ("a range that runs past the end", size - 1, 2),
    ]:
        with pytest.raises(LocalMaterialPreviewRejected) as caught:
            lease.read(start, length)
        assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_CHANGED, label

    lease.close()


def test_a_lease_whose_reads_fail_reports_the_file_not_the_range(tmp_path: Path) -> None:
    _state, private_source = _registered(tmp_path)
    with private_source.open("rb", buffering=0) as raw:
        lease = LocalMaterialPreviewLease(
            cast(BinaryIO, _RefusingStream(raw)),
            os.fstat(raw.fileno()),
            "video/mp4",
        )

        with pytest.raises(LocalMaterialPreviewRejected) as caught:
            lease.read(0, 4)

    assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_UNREADABLE


def test_a_lease_that_reads_back_short_is_refused(tmp_path: Path) -> None:
    """Short is not "less than asked for" here -- the range was already checked."""
    _state, private_source = _registered(tmp_path)
    with private_source.open("rb", buffering=0) as raw:
        lease = LocalMaterialPreviewLease(
            cast(BinaryIO, _ShortStream(raw)),
            os.fstat(raw.fileno()),
            "video/mp4",
        )

        with pytest.raises(LocalMaterialPreviewRejected) as caught:
            lease.read(0, 4)

    assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_CHANGED


def test_a_lease_whose_descriptor_is_gone_reports_a_changed_file(tmp_path: Path) -> None:
    state, _private_source = _registered(tmp_path)
    lease = _previews(state).open(MATERIAL_ID)
    lease._stream.close()

    with pytest.raises(LocalMaterialPreviewRejected) as caught:
        lease.read(0, 4)

    assert caught.value.code is LocalMaterialPreviewFailureCode.FILE_CHANGED
    lease.close()


def test_the_content_type_allowlist_covers_every_shipped_container() -> None:
    matroska = b"\x1aE\xdf\xa3" + b"\x00" * 8 + b"matroska" + b"\x00" * 16
    webm = b"\x1aE\xdf\xa3" + b"\x00" * 8 + b"webm" + b"\x00" * 16
    quicktime = b"\x00\x00\x00\x18ftypqt  \x00\x00\x02\x00qt  "

    cases: list[tuple[str, ProbedMaterialKind, bytes, str]] = [
        ("audio in an mp4 container", ProbedMaterialKind.AUDIO, _mp4_bytes(), "audio/mp4"),
        ("quicktime video", ProbedMaterialKind.VIDEO, quicktime, "video/quicktime"),
        ("matroska video", ProbedMaterialKind.VIDEO, matroska, "video/x-matroska"),
        ("webm video", ProbedMaterialKind.VIDEO, webm, "video/webm"),
        ("webm audio", ProbedMaterialKind.AUDIO, webm, "audio/webm"),
        ("ogg video", ProbedMaterialKind.VIDEO, b"OggS" + b"\x00" * 24, "video/ogg"),
        ("ogg audio", ProbedMaterialKind.AUDIO, b"OggS" + b"\x00" * 24, "audio/ogg"),
        ("raw aac", ProbedMaterialKind.AUDIO, b"\xff\xf1\x50\x80", "audio/aac"),
    ]
    for label, kind, header, expected in cases:
        assert safe_preview_content_type(kind, header) == expected, label


def test_the_content_type_allowlist_refuses_what_it_cannot_name() -> None:
    matroska = b"\x1aE\xdf\xa3" + b"\x00" * 8 + b"matroska" + b"\x00" * 16

    cases: list[tuple[str, Any, Any]] = [
        ("a kind from outside the closed set", "video", _mp4_bytes()),
        ("a header that is not bytes", ProbedMaterialKind.VIDEO, "ftyp"),
        ("matroska claimed as audio", ProbedMaterialKind.AUDIO, matroska),
        (
            "an ebml header naming neither",
            ProbedMaterialKind.VIDEO,
            b"\x1aE\xdf\xa3" + b"\x00" * 20,
        ),
        ("an mp4 box too short to read", ProbedMaterialKind.VIDEO, b"\x00\x00\x00\x18ftyp"),
        ("an image kind with no magic", ProbedMaterialKind.IMAGE, b"not an image"),
    ]
    for label, kind, header in cases:
        with pytest.raises(LocalMaterialPreviewRejected) as caught:
            safe_preview_content_type(kind, header)
        assert caught.value.code is LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA, label
