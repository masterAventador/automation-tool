from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from automation_tool.executor import material_probe
from automation_tool.executor.material_probe import (
    MAX_CODEC_NAME_CHARACTERS,
    MAX_MATERIAL_DIMENSION,
    MAX_MATERIAL_DURATION_MS,
    MAX_PATH_CHARACTERS,
    MAX_PROBE_OUTPUT_BYTES,
    MaterialProbeRejected,
    MaterialProbeRejection,
    PackagedMediaTools,
    ProbedMaterialKind,
    read_stream_facts,
)


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    path.chmod(0o755)
    return path


# One fixed script, reused by every test through a hard link, because macOS
# validates a newly created executable the first time it runs: measured 258 ms
# for a fresh copy against 5 ms for a hard link to an already-run inode. Baking
# each test's behaviour into its own script cost this file 12 s. Validation is
# per-inode, so the script instead stays constant and reads what to do from
# control files sitting beside the source file it is asked about.
_STUB_SOURCE = """#!/bin/sh
last=""
for a in "$@"; do last="$a"; done
[ -n "$last" ] || exit 0
d=$(dirname "$last")
if [ -f "$d/.probe-argv" ]; then
  for a in "$@"; do printf '%s\\n' "$a" >> "$d/.probe-argv"; done
fi
if [ -f "$d/.probe-sleep" ]; then sleep "$(cat "$d/.probe-sleep")"; fi
if [ -f "$d/.probe-signal" ]; then kill -9 $$; fi
if [ -f "$d/.probe-drain" ]; then cat > "$d/.probe-stdin"; fi
if [ -f "$d/.probe-stderr" ]; then cat "$d/.probe-stderr" >&2; fi
if [ -f "$d/.probe-stdout" ]; then cat "$d/.probe-stdout"; fi
if [ -f "$d/.probe-exit" ]; then exit "$(cat "$d/.probe-exit")"; fi
exit 0
"""


def _stub_master(directory: Path) -> Path:
    """Create and warm the shared stub once per pytest run."""
    digest = hashlib.sha256(_STUB_SOURCE.encode("utf-8")).hexdigest()[:16]
    master = directory.parent / f"ffprobe-master-{digest}"
    if not master.exists():
        staged = directory.parent / f"{master.name}.{os.getpid()}"
        staged.write_text(_STUB_SOURCE, encoding="utf-8")
        staged.chmod(0o755)
        os.replace(staged, master)
        subprocess.run([os.fspath(master)], capture_output=True, check=False)
    return master


def _ffprobe_stub(
    directory: Path,
    *,
    stdout: str = "",
    exit_code: int = 0,
    stderr: str = "",
    sleep: str = "",
    huge: bool = False,
    signal: bool = False,
    drain_stdin: bool = False,
    argv_log: bool = False,
) -> Path:
    """A real executable standing in for ffprobe.

    Using a script rather than patching the subprocess call keeps the production
    path under test: the arguments really are assembled, really are handed to
    `execve`, and the real exit code and streams come back.
    """
    if argv_log:
        (directory / ".probe-argv").write_text("", encoding="utf-8")
    if sleep:
        (directory / ".probe-sleep").write_text(sleep, encoding="ascii")
    if stderr:
        (directory / ".probe-stderr").write_text(stderr, encoding="utf-8")
    if signal:
        (directory / ".probe-signal").write_text("1", encoding="ascii")
    if drain_stdin:
        (directory / ".probe-drain").write_text("1", encoding="ascii")
    if huge:
        (directory / ".probe-stdout").write_bytes(b"x" * (MAX_PROBE_OUTPUT_BYTES + 1024))
    elif stdout:
        (directory / ".probe-stdout").write_text(stdout, encoding="utf-8")
    if exit_code:
        (directory / ".probe-exit").write_text(str(exit_code), encoding="ascii")
    path = directory / "ffprobe"
    os.link(_stub_master(directory), path)
    return path


def _video_stream(**overrides: Any) -> dict[str, Any]:
    stream = {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360}
    stream.update(overrides)
    return stream


def _audio_stream(**overrides: Any) -> dict[str, Any]:
    stream: dict[str, Any] = {"codec_type": "audio", "codec_name": "aac"}
    stream.update(overrides)
    return stream


def _probe_json(
    streams: list[dict[str, Any]],
    *,
    duration: Any = "3.000000",
    format_name: Any = "mov,mp4,m4a,3gp,3g2,mj2",
    omit_format: bool = False,
    omit_streams: bool = False,
) -> str:
    payload: dict[str, Any] = {}
    if not omit_streams:
        payload["streams"] = streams
    if not omit_format:
        fmt: dict[str, Any] = {"format_name": format_name}
        if duration is not None:
            fmt["duration"] = duration
        payload["format"] = fmt
    return json.dumps(payload)


def _padded_json(streams: list[dict[str, Any]], *, total_bytes: int) -> str:
    """Valid JSON of an exact byte length, padded with the whitespace JSON allows."""
    payload = _probe_json(streams)
    padding = total_bytes - len(payload.encode("utf-8"))
    assert padding >= 0
    return payload + " " * padding


def _source(directory: Path, name: str = "clip.mp4") -> Path:
    path = directory / name
    path.write_bytes(b"\x00" * 32)
    return path


def _tools(directory: Path, **stub: Any) -> PackagedMediaTools:
    return PackagedMediaTools(
        ffprobe_path=_ffprobe_stub(directory, **stub),
        ffmpeg_path=_executable(directory, "ffmpeg"),
    )


def _facts_from(directory: Path, payload: str, **stub: Any):  # type: ignore[no-untyped-def]
    return read_stream_facts(_tools(directory, stdout=payload, **stub), _source(directory))


def _reject_from(directory: Path, **stub: Any) -> pytest.ExceptionInfo[MaterialProbeRejected]:
    with pytest.raises(MaterialProbeRejected) as excinfo:
        read_stream_facts(_tools(directory, **stub), _source(directory))
    return excinfo


@pytest.fixture
def tools(tmp_path: Path) -> PackagedMediaTools:
    return PackagedMediaTools(
        ffprobe_path=_executable(tmp_path, "ffprobe"),
        ffmpeg_path=_executable(tmp_path, "ffmpeg"),
    )


def _rejection(excinfo: pytest.ExceptionInfo[MaterialProbeRejected]) -> MaterialProbeRejection:
    return excinfo.value.rejection


class TestPackagedMediaToolsAcceptance:
    def test_accepts_a_pair_of_regular_executables(self, tools: PackagedMediaTools) -> None:
        assert tools.ffprobe_path.name == "ffprobe"
        assert tools.ffmpeg_path.name == "ffmpeg"


class TestPackagedMediaToolsRejectsUnsafePaths:
    """One case per disjunct: a merged case lets a never-true term keep full coverage."""

    def test_rejects_a_non_path_value(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=str(_executable(tmp_path, "ffprobe")),  # type: ignore[arg-type]
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_relative_path(self, tmp_path: Path) -> None:
        _executable(tmp_path, "ffprobe")
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=Path("ffprobe"),
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_path_longer_than_the_limit(self, tmp_path: Path) -> None:
        overlong = tmp_path / ("a" * (MAX_PATH_CHARACTERS + 1))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=overlong,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_a_path_at_the_length_limit_passes_the_length_guard(self, tmp_path: Path) -> None:
        """The endpoint itself is allowed: the limit rejects longer, not equal.

        No file can exist at this length, so the proof is *which* reason comes
        back — reaching the filesystem check means the length guard let it by.
        Without this, moving `>` to `>=` changes no test.
        """
        at_limit = Path("/" + "a" * (MAX_PATH_CHARACTERS - 1))
        assert len(os.fspath(at_limit)) == MAX_PATH_CHARACTERS
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=at_limit,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_path_holding_a_control_character(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=tmp_path / "ff\x01probe",
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_path_holding_a_bidi_override(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=tmp_path / "ff‮probe",
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_path_whose_parent_is_a_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        _executable(real, "ffprobe")
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=link / "ffprobe",
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_symlinked_tool_itself(self, tmp_path: Path) -> None:
        link = tmp_path / "ffprobe-link"
        link.symlink_to(_executable(tmp_path, "ffprobe"))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=link,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH


class TestPackagedMediaToolsRejectsUnusableTools:
    def test_rejects_a_path_component_the_filesystem_cannot_name(self, tmp_path: Path) -> None:
        """A component over `NAME_MAX` must still come back as a rejection.

        `Path.is_symlink()` only swallows `ENOENT`/`ENOTDIR`/`EBADF`/`ELOOP`
        (`pathlib._IGNORED_ERRNOS`), so `ENAMETOOLONG` propagates. Left
        uncaught it escapes as a bare `OSError` — callers catching
        `MaterialProbeRejected` miss it, and its message carries the full
        private path.

        The total length here stays well inside the limit, so the earlier
        length guard cannot shadow this the way it does for a 4097-character
        single component.
        """
        victim = tmp_path / ("a" * 300)
        assert len(os.fspath(victim)) < MAX_PATH_CHARACTERS
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=victim,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE
        assert str(tmp_path) not in str(excinfo.value)

    def test_rejects_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=tmp_path / "absent",
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_directory(self, tmp_path: Path) -> None:
        directory = tmp_path / "ffprobe"
        directory.mkdir()
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=directory,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_file_without_the_execute_bit(self, tmp_path: Path) -> None:
        plain = tmp_path / "ffprobe"
        plain.write_text("#!/bin/sh\n", encoding="ascii")
        plain.chmod(0o644)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=plain,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE


class TestPackagedMediaToolsGuardsBothTools:
    """The second tool has to be guarded too: a guard with no rejecting case is not a guard."""

    def test_rejects_an_unsafe_ffmpeg_path(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=_executable(tmp_path, "ffprobe"),
                ffmpeg_path=Path("ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_an_unusable_ffmpeg(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=_executable(tmp_path, "ffprobe"),
                ffmpeg_path=tmp_path / "absent",
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE


class TestPackagedMediaToolsRevalidation:
    def test_revalidate_accepts_while_the_tools_remain(self, tools: PackagedMediaTools) -> None:
        tools.revalidate()

    def test_revalidate_rejects_after_the_tool_is_removed(self, tools: PackagedMediaTools) -> None:
        os.unlink(tools.ffprobe_path)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            tools.revalidate()
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE


class TestPackagedMediaToolsLeaksNoPath:
    def test_repr_reveals_no_path(self, tools: PackagedMediaTools) -> None:
        rendered = repr(tools)
        assert rendered == "PackagedMediaTools(<redacted>)"
        assert str(tools.ffprobe_path) not in rendered

    def test_rejection_message_reveals_no_path(self, tmp_path: Path) -> None:
        secret = tmp_path / "operator-private-name"
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=secret,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert "operator-private-name" not in str(excinfo.value)
        assert str(tmp_path) not in str(excinfo.value)


class TestReadStreamFactsKind:
    """`format.duration` presence is the image discriminator — not `duration == 0`.

    Measured against the packaged ffprobe: a PNG reports `format_name: png_pipe`
    with no `duration` key at all, while every timed container carries one.
    """

    def test_reads_a_video_with_sound(self, tmp_path: Path) -> None:
        facts = _facts_from(tmp_path, _probe_json([_video_stream(), _audio_stream()]))
        assert facts.kind is ProbedMaterialKind.VIDEO
        assert facts.duration_ms == 3000
        assert facts.width == 640
        assert facts.height == 360
        assert facts.video_codec == "h264"
        assert facts.audio_codec == "aac"

    def test_reads_a_video_without_an_audio_track(self, tmp_path: Path) -> None:
        facts = _facts_from(tmp_path, _probe_json([_video_stream()]))
        assert facts.kind is ProbedMaterialKind.VIDEO
        assert facts.audio_codec is None

    def test_reads_a_still_image_from_a_picture_container(self, tmp_path: Path) -> None:
        facts = _facts_from(
            tmp_path,
            _probe_json([_video_stream(codec_name="png")], duration=None, format_name="png_pipe"),
        )
        assert facts.kind is ProbedMaterialKind.IMAGE
        assert facts.duration_ms is None
        assert facts.width == 640
        assert facts.height == 360

    def test_reads_a_still_image_that_reports_a_frame_duration(self, tmp_path: Path) -> None:
        """A JPEG comes back as `image2` carrying one frame's worth of duration.

        Measured with the packaged ffprobe: `shot.jpg` reports
        `format_name: image2` and `duration: 0.040000`. Treating a stated
        duration as proof of motion turned that still into a 40 ms video.
        """
        facts = _facts_from(
            tmp_path,
            _probe_json(
                [_video_stream(codec_name="mjpeg")],
                duration="0.040000",
                format_name="image2",
            ),
        )
        assert facts.kind is ProbedMaterialKind.IMAGE
        assert facts.duration_ms is None

    def test_treats_a_non_text_container_name_as_motion(self, tmp_path: Path) -> None:
        """ffprobe output is untrusted, so the container name may not be text."""
        facts = _facts_from(tmp_path, _probe_json([_video_stream()], format_name=7))
        assert facts.kind is ProbedMaterialKind.VIDEO
        assert facts.duration_ms == 3000

    def test_rejects_a_video_whose_container_states_no_duration(self, tmp_path: Path) -> None:
        """A real recording, not a still. It must not be quietly reshaped into one.

        Measured: ffmpeg writing Matroska to a pipe cannot seek back to fill the
        duration in, so `MediaRecorder` WebM and piped MKV arrive exactly like
        this. Judging by the missing duration filed a 2-second H.264 clip as a
        picture while `video_codec` said `h264` right beside it.
        """
        excinfo = _reject_from(
            tmp_path,
            stdout=_probe_json(
                [_video_stream(), _audio_stream()], duration=None, format_name="matroska,webm"
            ),
        )
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_rejects_a_silent_video_whose_container_states_no_duration(
        self, tmp_path: Path
    ) -> None:
        """The branch that used to be accepted outright, with no error anywhere.

        With no audio stream `Material` had nothing left to object to, so the
        clip became a permanent still in the library.
        """
        excinfo = _reject_from(
            tmp_path,
            stdout=_probe_json([_video_stream()], duration=None, format_name="matroska,webm"),
        )
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_treats_a_picture_container_carrying_audio_as_motion(self, tmp_path: Path) -> None:
        """`Material` forbids a picture with sound, so this may not be filed as one."""
        excinfo = _reject_from(
            tmp_path,
            stdout=_probe_json(
                [_video_stream(), _audio_stream()], duration=None, format_name="png_pipe"
            ),
        )
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_reads_audio_with_no_frame_size(self, tmp_path: Path) -> None:
        """`Material` forbids a frame size on audio, so probing must not invent one."""
        facts = _facts_from(tmp_path, _probe_json([_audio_stream()]))
        assert facts.kind is ProbedMaterialKind.AUDIO
        assert facts.duration_ms == 3000
        assert facts.width is None
        assert facts.height is None
        assert facts.video_codec is None
        assert facts.audio_codec == "aac"

    def test_skips_stream_entries_that_are_not_objects(self, tmp_path: Path) -> None:
        """ffprobe output is untrusted; a scalar in `streams` must not crash the scan."""
        payload = json.dumps(
            {
                "streams": ["junk", 7, None, _video_stream()],
                "format": {"format_name": "mov", "duration": "3.000000"},
            }
        )
        assert _facts_from(tmp_path, payload).kind is ProbedMaterialKind.VIDEO

    def test_rejects_a_file_with_neither_picture_nor_sound(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([]))
        assert _rejection(excinfo) is MaterialProbeRejection.NO_USABLE_STREAM


class TestReadStreamFactsProbeFailure:
    def test_rejects_a_non_zero_exit_even_though_stdout_is_valid_json(self, tmp_path: Path) -> None:
        """ffprobe prints `{}` and exits 1. Parsing stdout alone invents defaults.

        Measured: unsupported data, a truncated container and an empty file all
        produce exactly `exit=1` with `stdout={}` — indistinguishable, which is
        why they share one reason.
        """
        excinfo = _reject_from(tmp_path, stdout="{}", exit_code=1)
        assert _rejection(excinfo) is MaterialProbeRejection.UNDECODABLE

    def test_rejects_a_non_zero_exit_even_when_stdout_is_a_complete_answer(
        self, tmp_path: Path
    ) -> None:
        """The exit code decides, not the shape of stdout.

        With the return code unchecked this payload parses cleanly and the probe
        would report a perfectly ordinary 3-second clip for a file ffprobe just
        refused to read.
        """
        excinfo = _reject_from(
            tmp_path,
            stdout=_probe_json([_video_stream(), _audio_stream()]),
            exit_code=1,
        )
        assert _rejection(excinfo) is MaterialProbeRejection.UNDECODABLE

    def test_rejects_a_probe_killed_by_a_signal(self, tmp_path: Path) -> None:
        """A killed child reports a negative return code, so `!= 0` is the test.

        `> 0` would read SIGKILL as success and then parse whatever partial
        output the dying process had already written.
        """
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream()]), signal=True)
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_CRASHED

    def test_accepts_output_exactly_at_the_size_limit(self, tmp_path: Path) -> None:
        facts = _facts_from(
            tmp_path, _padded_json([_video_stream()], total_bytes=MAX_PROBE_OUTPUT_BYTES)
        )
        assert facts.kind is ProbedMaterialKind.VIDEO

    def test_rejects_oversized_output_that_would_otherwise_parse(self, tmp_path: Path) -> None:
        """The size guard needs a case only it can reject.

        The all-`x` payload is caught by JSON parsing anyway, so deleting this
        guard changed no result. This payload is valid JSON one byte over the
        limit: without the guard it parses and answers normally.
        """
        excinfo = _reject_from(
            tmp_path,
            stdout=_padded_json([_video_stream()], total_bytes=MAX_PROBE_OUTPUT_BYTES + 1),
        )
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_output_that_is_not_json(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout="not json at all")
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_an_empty_object_reported_as_success(self, tmp_path: Path) -> None:
        """The exact payload ffprobe prints when it fails, but with a success code.

        Today that pairing cannot happen — a failing ffprobe always exits
        non-zero. This pins the behaviour anyway, because the empty object is
        what a future ffprobe would most plausibly start returning for a file it
        cannot describe, and the answer must stay a rejection rather than an
        all-`None` fact.
        """
        excinfo = _reject_from(tmp_path, stdout="{}", exit_code=0)
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_output_missing_the_streams_key(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([], omit_streams=True))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_output_missing_the_format_key(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream()], omit_format=True))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_output_that_is_a_json_array(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout="[]")
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_oversized_output(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, huge=True)
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_a_probe_that_outruns_its_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(material_probe, "PROBE_TIMEOUT_SECONDS", 0.2)
        # A valid answer after the sleep: without the timeout this call simply
        # succeeds, so the assertion cannot pass for the wrong reason.
        excinfo = _reject_from(tmp_path, sleep="5", stdout=_probe_json([_video_stream()]))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED


class TestReadStreamFactsDuration:
    def test_rejects_a_non_numeric_duration(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream()], duration="N/A"))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_rejects_audio_whose_container_states_no_duration(self, tmp_path: Path) -> None:
        """Only a picture may lack a duration. Audio without one cannot be a `Material`."""
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_audio_stream()], duration=None))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_rejects_a_duration_that_is_not_text(self, tmp_path: Path) -> None:
        """ffprobe emits duration as a JSON string; a bare number is malformed output."""
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream()], duration=3.0))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_rejects_a_non_finite_duration(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream()], duration="inf"))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_rejects_a_negative_duration(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream()], duration="-1.0"))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_rejects_a_duration_that_rounds_to_no_milliseconds(self, tmp_path: Path) -> None:
        """`Material` needs at least 1 ms, so a sub-millisecond file cannot become one."""
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream()], duration="0.0004"))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_accepts_the_shortest_representable_duration(self, tmp_path: Path) -> None:
        facts = _facts_from(tmp_path, _probe_json([_video_stream()], duration="0.001"))
        assert facts.duration_ms == 1

    def test_accepts_the_longest_allowed_duration(self, tmp_path: Path) -> None:
        seconds = MAX_MATERIAL_DURATION_MS / 1000
        facts = _facts_from(tmp_path, _probe_json([_video_stream()], duration=f"{seconds:.6f}"))
        assert facts.duration_ms == MAX_MATERIAL_DURATION_MS

    def test_rejects_a_duration_beyond_the_limit(self, tmp_path: Path) -> None:
        seconds = (MAX_MATERIAL_DURATION_MS + 1) / 1000
        excinfo = _reject_from(
            tmp_path, stdout=_probe_json([_video_stream()], duration=f"{seconds:.6f}")
        )
        assert _rejection(excinfo) is MaterialProbeRejection.TOO_LONG


class TestReadStreamFactsFrameSize:
    def test_rejects_a_missing_width(self, tmp_path: Path) -> None:
        stream = _video_stream()
        del stream["width"]
        excinfo = _reject_from(tmp_path, stdout=_probe_json([stream]))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_FRAME_SIZE

    def test_rejects_a_missing_height(self, tmp_path: Path) -> None:
        stream = _video_stream()
        del stream["height"]
        excinfo = _reject_from(tmp_path, stdout=_probe_json([stream]))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_FRAME_SIZE

    def test_rejects_a_non_integer_width(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream(width="640")]))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_FRAME_SIZE

    def test_rejects_a_boolean_width(self, tmp_path: Path) -> None:
        """`bool` is an `int` subclass; a plain isinstance check would let it by."""
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream(width=True)]))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_FRAME_SIZE

    def test_rejects_a_zero_width(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream(width=0)]))
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_FRAME_SIZE

    def test_accepts_the_smallest_frame(self, tmp_path: Path) -> None:
        facts = _facts_from(tmp_path, _probe_json([_video_stream(width=1, height=1)]))
        assert (facts.width, facts.height) == (1, 1)

    def test_accepts_the_largest_allowed_frame(self, tmp_path: Path) -> None:
        largest = MAX_MATERIAL_DIMENSION
        facts = _facts_from(tmp_path, _probe_json([_video_stream(width=largest, height=largest)]))
        assert (facts.width, facts.height) == (largest, largest)

    def test_rejects_a_frame_wider_than_the_limit(self, tmp_path: Path) -> None:
        excinfo = _reject_from(
            tmp_path, stdout=_probe_json([_video_stream(width=MAX_MATERIAL_DIMENSION + 1)])
        )
        assert _rejection(excinfo) is MaterialProbeRejection.FRAME_TOO_LARGE

    def test_rejects_a_frame_taller_than_the_limit(self, tmp_path: Path) -> None:
        excinfo = _reject_from(
            tmp_path, stdout=_probe_json([_video_stream(height=MAX_MATERIAL_DIMENSION + 1)])
        )
        assert _rejection(excinfo) is MaterialProbeRejection.FRAME_TOO_LARGE


class TestReadStreamFactsCodecName:
    """Codec names come from the file, so they are untrusted text."""

    def test_rejects_a_missing_codec_name(self, tmp_path: Path) -> None:
        stream = _video_stream()
        del stream["codec_name"]
        excinfo = _reject_from(tmp_path, stdout=_probe_json([stream]))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_a_non_string_codec_name(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream(codec_name=7)]))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_an_empty_codec_name(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream(codec_name="")]))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_a_codec_name_holding_control_characters(self, tmp_path: Path) -> None:
        excinfo = _reject_from(tmp_path, stdout=_probe_json([_video_stream(codec_name="h26‮4")]))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_accepts_a_codec_name_at_the_length_limit(self, tmp_path: Path) -> None:
        name = "h" * MAX_CODEC_NAME_CHARACTERS
        facts = _facts_from(tmp_path, _probe_json([_video_stream(codec_name=name)]))
        assert facts.video_codec == name

    def test_rejects_an_overlong_codec_name(self, tmp_path: Path) -> None:
        excinfo = _reject_from(
            tmp_path,
            stdout=_probe_json([_video_stream(codec_name="h" * (MAX_CODEC_NAME_CHARACTERS + 1))]),
        )
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_a_bad_audio_codec_name_too(self, tmp_path: Path) -> None:
        excinfo = _reject_from(
            tmp_path, stdout=_probe_json([_video_stream(), _audio_stream(codec_name="")])
        )
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED


class TestReadStreamFactsSourceFile:
    def test_rejects_a_missing_source(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, tmp_path / "absent.mp4")
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_directory_as_source(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        directory = tmp_path / "album"
        directory.mkdir()
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, directory)
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_fifo_as_source(self, tmp_path: Path) -> None:
        """A pipe would make ffprobe block forever rather than return a fact."""
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        fifo = tmp_path / "pipe.mp4"
        os.mkfifo(fifo)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, fifo)
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_source_without_read_permission(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        source = _source(tmp_path)
        source.chmod(0o000)
        try:
            with pytest.raises(MaterialProbeRejected) as excinfo:
                read_stream_facts(tools, source)
            assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE
        finally:
            source.chmod(0o644)

    def test_rejects_a_relative_source_path(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, Path("clip.mp4"))
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_source_path_holding_a_control_character(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, tmp_path / "cl\x01ip.mp4")
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_accepts_a_source_reached_through_a_symlink(self, tmp_path: Path) -> None:
        """User media legitimately lives behind symlinks; only the tools must be real files."""
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        link = tmp_path / "linked.mp4"
        link.symlink_to(_source(tmp_path))
        assert read_stream_facts(tools, link).kind is ProbedMaterialKind.VIDEO

    def test_revalidates_the_tools_before_probing(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        source = _source(tmp_path)
        os.unlink(tools.ffprobe_path)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, source)
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE


class TestReadStreamFactsInvocation:
    def test_separates_options_from_the_source_path(self, tmp_path: Path) -> None:
        """`--` keeps a leading-dash filename from being read as an option."""
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]), argv_log=True)
        read_stream_facts(tools, _source(tmp_path, "-rf.mp4"))
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        assert argv[-2] == "--"
        assert argv[-1].endswith("-rf.mp4")

    def test_asks_only_for_the_entries_it_uses(self, tmp_path: Path) -> None:
        """Requesting `tags` would pull attacker-controlled metadata into the process."""
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]), argv_log=True)
        read_stream_facts(tools, _source(tmp_path))
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        assert "-show_format" not in argv
        assert "-show_streams" not in argv
        entries = argv[argv.index("-show_entries") + 1]
        assert "tags" not in entries


class TestReadStreamFactsProcessBoundary:
    def test_leaves_the_parent_stdin_untouched(self, tmp_path: Path) -> None:
        """The executor's own stdin carries Tauri's bootstrap and command stream.

        A child inheriting it could eat bytes out of that protocol channel, so
        the probe is handed `DEVNULL`. The stub actively drains whatever it is
        given; the sentinel therefore survives only if the child never saw it.
        """
        sentinel = b"BOOTSTRAP-SENTINEL\n"
        seeded = tmp_path / "stdin.bin"
        seeded.write_bytes(sentinel)
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]), drain_stdin=True)
        source = _source(tmp_path)
        saved = os.dup(0)
        try:
            with seeded.open("rb") as handle:
                os.dup2(handle.fileno(), 0)
                read_stream_facts(tools, source)
                assert os.read(0, len(sentinel)) == sentinel
        finally:
            os.dup2(saved, 0)
            os.close(saved)
        assert (tmp_path / ".probe-stdin").read_bytes() == b""


class TestReadStreamFactsLeaksNothing:
    def test_no_traceback_frame_retains_the_probe_diagnostic(self, tmp_path: Path) -> None:
        """Capturing stderr keeps the path alive in a frame long after the raise.

        `CompletedProcess` renders its captured streams, so a crash reporter
        walking `f_locals` would carry the operator's private path off the
        machine even though nothing ever reads that field.
        """
        secret = "operator-private-holiday-2019"
        diagnostic = f"/private/var/folders/ab/{secret}.mp4: Invalid data found"
        tools = _tools(tmp_path, stdout="{}", exit_code=1, stderr=diagnostic)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, _source(tmp_path))
        module_file = material_probe.__file__
        traceback = excinfo.value.__traceback__
        inspected = 0
        while traceback is not None:
            frame = traceback.tb_frame
            # Only the module's own frames: this test's frame naturally holds
            # the diagnostic it just wrote, which says nothing about the module.
            if frame.f_code.co_filename == module_file:
                inspected += 1
                for value in frame.f_locals.values():
                    assert secret not in repr(value)
            traceback = traceback.tb_next
        assert inspected, "no material_probe frame was inspected"

    def test_rejection_carries_neither_path_nor_probe_diagnostics(self, tmp_path: Path) -> None:
        """ffprobe names the file in its diagnostics; none of it may reach the caller."""
        secret = "operator-private-name"
        diagnostic = f"{secret}: Invalid data found when processing input"
        tools = _tools(tmp_path, stdout="{}", exit_code=1, stderr=diagnostic)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, _source(tmp_path, f"{secret}.mp4"))
        rendered = str(excinfo.value)
        assert rendered == "material probe rejected"
        assert secret not in rendered
        assert "Invalid data found" not in rendered
        assert str(tmp_path) not in rendered
