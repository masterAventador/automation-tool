"""LE-19 T1: prove a registered video's picture is decodable, not just declared."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from automation_tool.executor import smart_edit_media
from automation_tool.executor.material_probe import PackagedMediaTools, approve_source
from automation_tool.executor.smart_edit_media import (
    SmartEditMediaFailureCode,
    SmartEditMediaRejected,
    _decode_with_progress,
    verify_decodable_video,
)


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def _tools(tmp_path: Path, *, ffmpeg_body: str) -> PackagedMediaTools:
    return PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe", "exit 0"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg", ffmpeg_body),
    )


def _source(tmp_path: Path) -> tuple[Path, os.stat_result]:
    source = tmp_path / "私密素材.mp4"
    source.write_bytes(b"registered-video")
    return approve_source(source)


def test_successful_full_decode_returns_one_digest_bound_interval(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    log = tmp_path / "argv"
    tools = _tools(
        tmp_path,
        ffmpeg_body=(
            f"printf '%s\\n' \"$@\" > '{log}'\n"
            "printf 'frame=25\\nout_time_us=4321000\\nprogress=end\\n'\nexit 0"
        ),
    )
    material_id = uuid4()
    digest = "a" * 64

    result = verify_decodable_video(
        tools,
        source,
        approved,
        material_id=material_id,
        content_digest=digest,
        duration_ms=4_321,
        cancellation_requested=lambda: False,
    )

    assert result.material_id == material_id
    assert result.content_digest == digest
    assert tuple((interval.start_ms, interval.end_ms) for interval in result.intervals) == (
        (0, 4_321),
    )
    arguments = log.read_text(encoding="utf-8").splitlines()
    assert "-xerror" in arguments
    assert "0:v:0" in arguments
    assert arguments[arguments.index("-i") + 1] == os.fspath(source)
    assert os.fspath(source) not in repr(result)


def test_zero_exit_without_any_decoded_picture_is_rejected(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exit 0")

    with pytest.raises(SmartEditMediaRejected) as captured:
        verify_decodable_video(
            tools,
            source,
            approved,
            material_id=uuid4(),
            content_digest="e" * 64,
            duration_ms=1_000,
            cancellation_requested=lambda: False,
        )

    assert captured.value.code is SmartEditMediaFailureCode.UNDECODABLE


def test_declared_duration_is_clamped_to_the_real_decoded_picture_end(
    tmp_path: Path,
) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(
        tmp_path,
        ffmpeg_body="printf 'frame=10\\nout_time_us=400000\\nprogress=end\\n'",
    )

    result = verify_decodable_video(
        tools,
        source,
        approved,
        material_id=uuid4(),
        content_digest="9" * 64,
        duration_ms=4_000,
        cancellation_requested=lambda: False,
    )

    assert tuple((interval.start_ms, interval.end_ms) for interval in result.intervals) == (
        (0, 400),
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("exit 7", SmartEditMediaFailureCode.UNDECODABLE),
        ("kill -9 $$", SmartEditMediaFailureCode.UNDECODABLE),
    ],
)
def test_failed_or_crashed_decoder_returns_one_closed_reason_without_path(
    tmp_path: Path,
    body: str,
    expected: SmartEditMediaFailureCode,
) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body=body)

    with pytest.raises(SmartEditMediaRejected) as captured:
        verify_decodable_video(
            tools,
            source,
            approved,
            material_id=uuid4(),
            content_digest="b" * 64,
            duration_ms=1_000,
            cancellation_requested=lambda: False,
        )

    assert captured.value.code is expected
    assert str(captured.value) == "smart edit media rejected"
    assert captured.value.__cause__ is None
    assert os.fspath(source) not in repr(captured.value)


def test_cooperative_cancel_kills_decoder_and_returns_cancelled(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exec sleep 10")
    polls = 0

    def cancelled() -> bool:
        nonlocal polls
        polls += 1
        return polls >= 2

    with pytest.raises(SmartEditMediaRejected) as captured:
        verify_decodable_video(
            tools,
            source,
            approved,
            material_id=uuid4(),
            content_digest="c" * 64,
            duration_ms=1_000,
            cancellation_requested=cancelled,
        )

    assert captured.value.code is SmartEditMediaFailureCode.CANCELLED
    assert polls == 2


def test_source_identity_change_during_decode_is_not_reported_as_success(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(
        tmp_path,
        ffmpeg_body=(
            'previous=""\nfor value in "$@"; do\n'
            '  if [ "$previous" = "-i" ]; then target="$value"; fi\n'
            '  previous="$value"\ndone\nprintf x >> "$target"\n'
            "printf 'frame=1\\nout_time_us=1000000\\nprogress=end\\n'"
        ),
    )

    with pytest.raises(SmartEditMediaRejected) as captured:
        verify_decodable_video(
            tools,
            source,
            approved,
            material_id=uuid4(),
            content_digest="d" * 64,
            duration_ms=1_000,
            cancellation_requested=lambda: False,
        )

    assert captured.value.code is SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE
    assert os.fspath(source) not in repr(captured.value)


def _verify(
    tools: PackagedMediaTools,
    source: Path,
    approved: os.stat_result,
    **overrides: Any,
) -> Any:
    arguments: dict[str, Any] = {
        "material_id": uuid4(),
        "content_digest": "f" * 64,
        "duration_ms": 1_000,
        "cancellation_requested": lambda: False,
    }
    arguments.update(overrides)
    return verify_decodable_video(tools, source, approved, **arguments)


def test_a_rejection_must_name_a_reason_from_the_closed_vocabulary() -> None:
    """The code is what callers branch on, so a look-alike may not become one."""
    for label, value in [
        ("the bare string behind the member", "undecodable"),
        ("a reason from another module", "material_unavailable"),
        ("nothing at all", None),
    ]:
        with pytest.raises(TypeError) as caught:
            SmartEditMediaRejected(cast(Any, value))
        assert str(caught.value) == "smart edit media rejected", label


def test_a_cancellation_probe_that_cannot_be_trusted_stops_before_decoding(
    tmp_path: Path,
) -> None:
    """An unusable answer is not read as "carry on" -- the decoder never starts."""
    source, approved = _source(tmp_path)
    log = tmp_path / "argv"
    tools = _tools(tmp_path, ffmpeg_body=f"printf started > '{log}'\nexit 0")

    def raising() -> bool:
        raise RuntimeError("probe defect")

    for label, probe in [
        ("the probe raises", raising),
        ("the probe answers with something that is not a bool", lambda: cast(bool, 1)),
        ("the probe answers with nothing", lambda: cast(bool, None)),
    ]:
        with pytest.raises(SmartEditMediaRejected) as caught:
            _verify(tools, source, approved, cancellation_requested=probe)
        assert caught.value.code is SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE, label

    assert not log.exists(), "the decoder must not have been launched"


def test_a_job_already_cancelled_never_launches_the_decoder(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    log = tmp_path / "argv"
    tools = _tools(tmp_path, ffmpeg_body=f"printf started > '{log}'\nexit 0")

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved, cancellation_requested=lambda: True)

    assert caught.value.code is SmartEditMediaFailureCode.CANCELLED
    assert not log.exists()


def test_a_scratch_directory_that_cannot_be_created_is_a_workspace_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exit 0")

    def refuse(*_args: object, **_options: object) -> str:
        raise OSError("no space left on device")

    monkeypatch.setattr(tempfile, "mkdtemp", refuse)

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved)

    assert caught.value.code is SmartEditMediaFailureCode.WORKSPACE_UNUSABLE


def test_a_progress_file_that_cannot_be_created_is_a_workspace_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scratch directory exists but is not writable, so nothing can be recorded."""
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exit 0")
    workspace = tmp_path / "read-only-scratch"
    workspace.mkdir(mode=0o500)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *_a, **_o: os.fspath(workspace))

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved)

    assert caught.value.code is SmartEditMediaFailureCode.WORKSPACE_UNUSABLE
    assert not workspace.exists(), "the scratch directory is removed even when unusable"


def test_a_decoder_that_cannot_be_executed_is_a_tool_problem(tmp_path: Path) -> None:
    """Present, private and executable, yet the kernel still refuses to run it.

    A shebang naming an interpreter that is not there is the real shape of this:
    the file passes every check `revalidate` makes and `execve` fails anyway.
    """
    source, approved = _source(tmp_path)
    broken = tmp_path / "ffmpeg"
    broken.write_text("#!/nonexistent/interpreter\nexit 0\n", encoding="utf-8")
    broken.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    tools = PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe", "exit 0"),
        ffmpeg_path=broken,
    )

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved)

    assert caught.value.code is SmartEditMediaFailureCode.TOOL_UNAVAILABLE


def test_a_probe_that_starts_misbehaving_mid_decode_stops_the_decoder(
    tmp_path: Path,
) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exec sleep 10")
    polls = 0

    def probe() -> bool:
        nonlocal polls
        polls += 1
        if polls >= 2:
            raise RuntimeError("probe defect")
        return False

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved, cancellation_requested=probe)

    assert caught.value.code is SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE
    assert polls == 2


def test_progress_output_beyond_its_budget_stops_the_decoder(tmp_path: Path) -> None:
    """A decoder that will not stop talking is stopped rather than followed."""
    source, approved = _source(tmp_path)
    tools = _tools(
        tmp_path,
        ffmpeg_body="head -c 1200000 /dev/zero | tr '\\0' 'x'\nexec sleep 10",
    )

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved)

    assert caught.value.code is SmartEditMediaFailureCode.UNDECODABLE


def test_a_scratch_directory_that_disappears_mid_decode_is_a_workspace_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removed between two polls: the running decoder is stopped, not waited on."""
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exec sleep 10")
    workspace = tmp_path / "scratch"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *_a, **_o: os.fspath(workspace))
    polls = 0

    def probe() -> bool:
        nonlocal polls
        polls += 1
        if polls >= 2:
            shutil.rmtree(workspace)
        return False

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved, cancellation_requested=probe)

    assert caught.value.code is SmartEditMediaFailureCode.WORKSPACE_UNUSABLE


def test_a_decode_that_outlives_its_budget_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget itself is replaced rather than waited out: the floor is 30s."""
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exec sleep 30")
    monkeypatch.setattr(smart_edit_media, "_timeout", lambda _duration_ms: 0.0)

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved)

    assert caught.value.code is SmartEditMediaFailureCode.TIMED_OUT


def test_the_decode_budget_grows_with_the_material_but_stays_bounded() -> None:
    assert smart_edit_media._timeout(1) == 30.0
    assert smart_edit_media._timeout(60_000) == 120.0
    assert smart_edit_media._timeout(10_000_000) == 900.0


class _ProbeProgressPath(Path):
    """A progress file whose size and contents can disagree, on purpose.

    Between the loop's `stat` and the final `read_bytes` the decoder is still
    holding the descriptor, so a file that grows in that window is a real race
    the size check cannot see -- and the only way to land in it deterministically
    is to make the two answers differ here.
    """

    _reported_size: int | None = None
    _read_error: bool = False

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        real = super().stat(follow_symlinks=follow_symlinks)
        if self._reported_size is None:
            return real
        fields = list(real)
        fields[stat.ST_SIZE] = self._reported_size
        return os.stat_result(fields)

    def read_bytes(self) -> bytes:
        if self._read_error:
            raise OSError("input/output error")
        return super().read_bytes()


def test_progress_that_cannot_be_read_back_is_a_workspace_problem(tmp_path: Path) -> None:
    progress = _ProbeProgressPath(tmp_path / "progress")
    progress._read_error = True
    tools = _tools(tmp_path, ffmpeg_body="printf 'frame=1\\nout_time_us=1000\\nprogress=end\\n'")

    outcome = _decode_with_progress(
        tools.ffmpeg_path,
        _source(tmp_path)[0],
        progress_path=progress,
        duration_ms=1_000,
        cancellation_requested=lambda: False,
    )

    assert outcome is SmartEditMediaFailureCode.WORKSPACE_UNUSABLE


def test_progress_that_grew_past_its_budget_after_the_last_poll_is_undecodable(
    tmp_path: Path,
) -> None:
    progress = _ProbeProgressPath(tmp_path / "progress")
    progress._reported_size = 1
    tools = _tools(tmp_path, ffmpeg_body="head -c 1200000 /dev/zero | tr '\\0' 'x'")

    outcome = _decode_with_progress(
        tools.ffmpeg_path,
        _source(tmp_path)[0],
        progress_path=progress,
        duration_ms=1_000,
        cancellation_requested=lambda: False,
    )

    assert outcome is SmartEditMediaFailureCode.UNDECODABLE


def test_verification_refuses_arguments_it_cannot_trust(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exit 0")
    log = tmp_path / "argv"

    cases: list[tuple[str, tuple[Any, Any, Any], dict[str, Any]]] = [
        ("tools of the wrong type", (object(), source, approved), {}),
        ("a source that is not a path", (tools, os.fspath(source), approved), {}),
        ("an approval that is not a stat", (tools, source, (0, 0)), {}),
        (
            "a material id that is not canonical",
            (tools, source, approved),
            {"material_id": UUID(int=0)},
        ),
        ("a digest that is not text", (tools, source, approved), {"content_digest": b"a" * 64}),
        ("a digest of the wrong shape", (tools, source, approved), {"content_digest": "A" * 64}),
        ("a duration that is not an int", (tools, source, approved), {"duration_ms": 1.0}),
        ("a duration of zero", (tools, source, approved), {"duration_ms": 0}),
        ("a duration beyond the ceiling", (tools, source, approved), {"duration_ms": 10**12}),
        ("a probe that cannot be called", (tools, source, approved), {"cancellation_requested": 1}),
    ]
    for label, positional, overrides in cases:
        arguments: dict[str, Any] = {
            "material_id": uuid4(),
            "content_digest": "f" * 64,
            "duration_ms": 1_000,
            "cancellation_requested": lambda: False,
        }
        arguments.update(overrides)
        with pytest.raises(SmartEditMediaRejected) as caught:
            verify_decodable_video(*positional, **arguments)
        assert caught.value.code is SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE, label

    assert not log.exists(), "nothing may be decoded before the arguments are trusted"


def test_a_toolchain_that_no_longer_validates_is_a_tool_problem(tmp_path: Path) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exit 0")
    tools.ffmpeg_path.unlink()

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved)

    assert caught.value.code is SmartEditMediaFailureCode.TOOL_UNAVAILABLE


def test_a_source_replaced_before_decoding_starts_is_material_unavailable(
    tmp_path: Path,
) -> None:
    source, approved = _source(tmp_path)
    tools = _tools(tmp_path, ffmpeg_body="exit 0")
    source.unlink()
    source.write_bytes(b"a different video entirely")

    with pytest.raises(SmartEditMediaRejected) as caught:
        _verify(tools, source, approved)

    assert caught.value.code is SmartEditMediaFailureCode.MATERIAL_UNAVAILABLE
    assert os.fspath(source) not in repr(caught.value)
