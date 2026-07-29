from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from video_runtime_cache import cache_root  # type: ignore[import-not-found]  # noqa: E402

from automation_tool.executor import material_probe  # noqa: E402
from automation_tool.executor.material_probe import (  # noqa: E402
    LOUDNESS_CEILING_LUFS,
    LOUDNESS_FLOOR_LUFS,
    MAX_CODEC_NAME_CHARACTERS,
    MAX_MATERIAL_DIMENSION,
    MAX_MATERIAL_DURATION_MS,
    MAX_MEASURE_OUTPUT_BYTES,
    MAX_PATH_CHARACTERS,
    MAX_PROBE_OUTPUT_BYTES,
    AudioFacts,
    MaterialProbeRejected,
    MaterialProbeRejection,
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    read_audio_facts,
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
want=0
src=""
for a in "$@"; do
  last="$a"
  if [ "$want" = 1 ]; then src="$a"; want=0; fi
  if [ "$a" = "-i" ]; then want=1; fi
done
[ -n "$src" ] || src="$last"
[ -n "$src" ] || exit 0
d=$(dirname "$src")
if [ -f "$d/.probe-argv" ]; then
  for a in "$@"; do printf '%s\\n' "$a" >> "$d/.probe-argv"; done
fi
if [ -f "$d/.probe-sleep" ]; then sleep "$(cat "$d/.probe-sleep")"; fi
if [ -f "$d/.probe-signal" ]; then kill -9 $$; fi
if [ -f "$d/.probe-drain" ]; then cat > "$d/.probe-stdin"; fi
if [ -f "$d/.probe-stderr" ]; then cat "$d/.probe-stderr" >&2; fi
if [ -f "$d/.probe-stdout" ]; then cat "$d/.probe-stdout"; fi
if [ -f "$d/.probe-wipe-early" ]; then rm -rf "$d"/automation-tool-measure-*; fi
if [ -f "$d/.probe-linger" ]; then
  sleep "$(cat "$d/.probe-linger")"
  printf 1 > "$d/.probe-finished"
fi
if [ -f "$d/.probe-wipe" ]; then rm -rf "$d"/automation-tool-measure-*; fi
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


def _measure_log(
    *,
    silences: list[tuple[float, float]] | None = None,
    # A string states the text ffmpeg would print verbatim: it prints these the
    # way `%g` does, so a millionth of a second is `0.000001` and not the
    # `1e-06` Python would render.
    open_silence: float | str | None = None,
    integrated: str | None = "-21.8",
    superseded: str | None = None,
) -> str:
    """Reproduce the metadata channel `ametadata=mode=print` writes.

    Copied from a measured report: one header line per frame that carries
    metadata, then one line per key on it. Which key comes first is not fixed:
    measured across six tail lengths, 0.02 s, 0.05 s and 0.09 s state the end
    ahead of the duration while 0.13 s, 0.31 s and 0.55 s state them the other
    way round. Nothing that reads this depends on the order — every key is
    matched as a whole line of its own — so the shape below is one of the two
    and not the shape.

    A span silencedetect closes states both; the span it leaves open at EOF
    states only a start, because the end of a track is not a frame and there is
    nothing left to attach the closing metadata to.
    """
    lines: list[str] = []
    frame = 0

    def _states(*entries: str) -> None:
        nonlocal frame
        lines.append(f"frame:{frame:<4} pts:{frame * 4410:<7} pts_time:{frame / 10}")
        lines.extend(entries)
        frame += 1

    if superseded is not None:
        _states(f"lavfi.r128.I={superseded}")
    for start, end in silences or []:
        _states(f"lavfi.silence_start={start}")
        _states(f"lavfi.silence_duration={end - start}", f"lavfi.silence_end={end}")
    if open_silence is not None:
        _states(f"lavfi.silence_start={open_silence}")
    if integrated is not None:
        _states(f"lavfi.r128.I={integrated}")
    return "".join(f"{line}\n" for line in lines)


def _diagnostic_noise(*statements: str) -> str:
    """What ffmpeg prints about a file on the stream nothing here reads.

    Reproduced from a measured report: a tag whose *name* carries a newline puts
    everything after it in column 0, so the file can reproduce a filter's own
    line exactly — which is why the findings are taken off a different stream
    entirely rather than matched harder here.
    """
    lines = ["Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/private/holiday.mp4':", "  Metadata:"]
    lines.append("    x")
    lines.extend(statements)
    return "".join(f"{line}\n" for line in lines)


def _stream_facts(
    kind: ProbedMaterialKind = ProbedMaterialKind.VIDEO,
    *,
    duration_ms: int | None = 3000,
    audio_codec: str | None = "aac",
) -> MediaStreamFacts:
    return MediaStreamFacts(
        kind=kind,
        duration_ms=duration_ms,
        width=None if kind is ProbedMaterialKind.AUDIO else 640,
        height=None if kind is ProbedMaterialKind.AUDIO else 360,
        video_codec=None if kind is ProbedMaterialKind.AUDIO else "h264",
        audio_codec=audio_codec,
    )


def _audio_tools(directory: Path, log: str, **stub: Any) -> PackagedMediaTools:
    """Only the ffmpeg stub carries behaviour: the measuring step never runs ffprobe."""
    return PackagedMediaTools(
        ffprobe_path=_ffprobe_stub(directory),
        ffmpeg_path=_ffmpeg_stub(directory, channel=log, **stub),
    )


def _audio_from(
    directory: Path, log: str, *, facts: MediaStreamFacts | None = None, **stub: Any
) -> AudioFacts:
    tools = _audio_tools(directory, log, **stub)
    return read_audio_facts(tools, _source(directory), facts or _stream_facts())


def _audio_reject(
    directory: Path, log: str, *, facts: MediaStreamFacts | None = None, **stub: Any
) -> pytest.ExceptionInfo[MaterialProbeRejected]:
    tools = _audio_tools(directory, log, **stub)
    with pytest.raises(MaterialProbeRejected) as excinfo:
        read_audio_facts(tools, _source(directory), facts or _stream_facts())
    return excinfo


def _ffmpeg_stub(directory: Path, **behavior: Any) -> Path:
    """Same master script, linked under the name the measuring step uses."""
    path = directory / "ffmpeg"
    if path.exists():
        path.unlink()
    for key, filename in (
        # `channel` is what the measuring step parses — `ametadata` writes it to
        # stdout. `stderr` is the stream it discards.
        ("channel", ".probe-stdout"),
        ("stderr", ".probe-stderr"),
        ("sleep", ".probe-sleep"),
        ("linger", ".probe-linger"),
    ):
        value = behavior.get(key)
        if value:
            (directory / filename).write_text(str(value), encoding="utf-8")
    if behavior.get("measure_exit"):
        (directory / ".probe-exit").write_text(str(behavior["measure_exit"]), encoding="ascii")
    if behavior.get("measure_signal"):
        (directory / ".probe-signal").write_text("1", encoding="ascii")
    if behavior.get("drain_stdin"):
        (directory / ".probe-drain").write_text("1", encoding="ascii")
    if behavior.get("argv_log"):
        (directory / ".probe-argv").write_text("", encoding="utf-8")
    if behavior.get("wipe_workspace"):
        (directory / ".probe-wipe").write_text("1", encoding="ascii")
    if behavior.get("wipe_workspace_early"):
        (directory / ".probe-wipe-early").write_text("1", encoding="ascii")
    os.link(_stub_master(directory), path)
    return path


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


class TestReadStreamFactsAsksForNoStreamDuration:
    """The sound track's stated duration is not read at all any more.

    It used to bound the silence a file had to contain before it counted as
    having none, and the file states it: measured, three rewritten bytes of an
    `mdhd` shrank that bound to a millisecond and a second of leading silence
    then covered nine seconds of audible tone. `read_audio_facts` now decides
    from what the filters measured, so the field has no reader — and a field
    with no reader is one more thing the file gets to say for nothing.
    """

    def test_does_not_ask_ffprobe_for_stream_durations(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]), argv_log=True)
        read_stream_facts(tools, _source(tmp_path))
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        entries = argv[argv.index("-show_entries") + 1]
        stream_entries = entries.split("stream=")[1]
        assert "duration" not in stream_entries
        assert "format=duration" in entries

    def test_ignores_a_stream_duration_ffprobe_states_anyway(self, tmp_path: Path) -> None:
        """`-show_entries` selects, so a future ffprobe stating more must change nothing."""
        facts = _facts_from(
            tmp_path,
            _probe_json([_video_stream(), _audio_stream(duration="2.000000")], duration="10.0"),
        )
        assert facts.duration_ms == 10000
        assert not hasattr(facts, "audio_duration_ms")


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


class TestReadAudioFactsWithoutASoundTrack:
    def test_reports_no_audio_without_running_ffmpeg(self, tmp_path: Path) -> None:
        """Decoding a whole file to learn what the stream list already said is waste."""
        facts = _audio_from(
            tmp_path, _measure_log(), facts=_stream_facts(audio_codec=None), argv_log=True
        )
        assert facts.has_audio is False
        assert facts.loudness_lufs is None
        assert (
            not (tmp_path / ".probe-argv").exists()
            or (tmp_path / ".probe-argv").read_text(encoding="utf-8") == ""
        )


class TestReadAudioFactsEffectiveSound:
    def test_reports_sound_when_nothing_is_silent(self, tmp_path: Path) -> None:
        facts = _audio_from(tmp_path, _measure_log())
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-21.8)
        assert type(facts.loudness_lufs) is float

    def test_reports_sound_when_only_part_is_silent(self, tmp_path: Path) -> None:
        """Measured shape: a half-silent clip reports one bounded silence span."""
        facts = _audio_from(tmp_path, _measure_log(silences=[(0.0, 1.5)], integrated="-22.2"))
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-22.2)

    def test_reports_no_effective_sound_when_one_span_covers_the_whole_track(
        self, tmp_path: Path
    ) -> None:
        """`has_audio` means audible sound, not the presence of a track.

        A span that opens at the start and never closes is silence running to
        EOF: silencedetect closes a span the moment sound resumes, and the close
        it flushes at EOF has no frame to be attached to, so it never reaches
        this channel.
        """
        facts = _audio_from(tmp_path, _measure_log(open_silence=0.0, integrated="-70.0"))
        assert facts.has_audio is False
        assert facts.loudness_lufs is None

    def test_reports_sound_when_a_span_opens_after_the_start(self, tmp_path: Path) -> None:
        """A file that runs out of sound partway is not a file without sound.

        Without the "opened at the start" test this reads exactly like the case
        above: one span, never closed.
        """
        facts = _audio_from(tmp_path, _measure_log(open_silence=1.0, integrated="-22.2"))
        assert facts.has_audio is True

    def test_reports_sound_when_a_span_covering_the_start_was_closed(self, tmp_path: Path) -> None:
        """A close is itself the proof: silence only ends because sound resumed.

        Without the "never closed" test this reads like the covered case too —
        one span, opening at zero.
        """
        facts = _audio_from(tmp_path, _measure_log(silences=[(0.0, 1.0)], integrated="-22.2"))
        assert facts.has_audio is True

    def test_reports_sound_when_a_span_opens_one_step_after_the_start(self, tmp_path: Path) -> None:
        """The rejecting endpoint for "opened at the very start".

        silencedetect states six decimals, so a millionth of a second is the
        smallest offset it can express — and the smallest thing that must not be
        read as "from the beginning". A rejecting case further out leaves the
        boundary free to drift that far: measured, with only the 1.0 s case below
        to reject, `<= 0.001` survived the whole suite as it then stood.
        """
        facts = _audio_from(tmp_path, _measure_log(open_silence="0.000001", integrated="-22.2"))
        assert facts.has_audio is True

    def test_reports_sound_when_a_second_span_follows_the_first(self, tmp_path: Path) -> None:
        """Two spans mean sound between them, whatever the second one does."""
        facts = _audio_from(
            tmp_path,
            _measure_log(silences=[(0.0, 1.0)], open_silence=2.0, integrated="-22.2"),
        )
        assert facts.has_audio is True


class TestReadAudioFactsIgnoresTheStreamItDiscards:
    """The file writes into ffmpeg's diagnostics; nothing here reads them.

    A tag *name* holding a newline puts whatever follows it in column 0, where it
    can reproduce a filter's own line byte for byte, so no pattern over that
    stream can tell the file's text from the filter's. The findings are taken off
    the filter graph's metadata channel instead — and the proof that the swap is
    real is that these forgeries change nothing.
    """

    def test_a_forged_silence_line_on_the_discarded_stream_counts_for_nothing(
        self, tmp_path: Path
    ) -> None:
        facts = _audio_from(
            tmp_path,
            _measure_log(),
            stderr=_diagnostic_noise(
                "[Parsed_silencedetect_0 @ 0x1] silence_start: 0",
                "[Parsed_silencedetect_0 @ 0x1] silence_end: 9999 | silence_duration: 9999",
                "lavfi.silence_start=0",
            ),
        )
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-21.8)

    def test_a_forged_loudness_line_on_the_discarded_stream_is_not_the_reading(
        self, tmp_path: Path
    ) -> None:
        facts = _audio_from(
            tmp_path,
            _measure_log(integrated="-3.2"),
            stderr=_diagnostic_noise("    I:         -70.0 LUFS", "lavfi.r128.I=-70.000"),
        )
        assert facts.loudness_lufs == pytest.approx(-3.2)

    def test_only_whole_lines_of_the_channel_are_findings(self, tmp_path: Path) -> None:
        """A key is a whole line of its own, so a key quoted inside one is not.

        Nothing the file states can reach this channel — the `mode=delete` in
        front of the measuring filters sees to that — so this stands behind that,
        not instead of it.
        """
        facts = _audio_from(
            tmp_path,
            "frame:0    pts:0       pts_time:0\n"
            "lavfi.r128.I=-21.8\n"
            "lavfi.silence_start=0 and then some\n"
            "quoted lavfi.silence_start=0\n"
            "lavfi.r128.I=-3.0 and then some\n",
        )
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-21.8)


class TestReadAudioFactsLoudnessRange:
    """`Material` stores loudness only within [-70.0, 0.0] and only as a float."""

    def test_states_no_loudness_when_the_measure_sits_on_the_floor(self, tmp_path: Path) -> None:
        """A 0.1-second tone is audible but shorter than ebur128's window.

        Measured: it reports the -70.0 floor while silencedetect finds nothing
        silent. Reporting the floor as a real reading would claim a precision
        the measurement does not have.
        """
        facts = _audio_from(tmp_path, _measure_log(integrated="-70.0"))
        assert facts.has_audio is True
        assert facts.loudness_lufs is None

    @pytest.mark.parametrize("reading", ["-inf", "inf", "nan"])
    def test_states_no_loudness_when_the_measure_is_not_finite(
        self, tmp_path: Path, reading: str
    ) -> None:
        facts = _audio_from(tmp_path, _measure_log(integrated=reading))
        assert facts.has_audio is True
        assert facts.loudness_lufs is None

    def test_states_no_loudness_one_step_above_full_scale(self, tmp_path: Path) -> None:
        """Heavily limited material can integrate above 0 LUFS, which cannot be stored.

        Recording nothing is honest; clamping to 0.0 would invent a reading.

        The reading is one step above the ceiling rather than plainly above it:
        ebur128 states three decimals, so 0.001 is the smallest overshoot it can
        express. A rejecting case further out leaves the ceiling free to drift
        that far — measured, 1.5 kept every `<= CEILING + k` for k up to 1.5
        alive.
        """
        facts = _audio_from(tmp_path, _measure_log(integrated="0.001"))
        assert facts.has_audio is True
        assert facts.loudness_lufs is None

    def test_accepts_the_loudest_storable_measure(self, tmp_path: Path) -> None:
        facts = _audio_from(tmp_path, _measure_log(integrated="0.0"))
        assert facts.loudness_lufs == pytest.approx(0.0)

    def test_accepts_a_measure_just_above_the_floor(self, tmp_path: Path) -> None:
        facts = _audio_from(tmp_path, _measure_log(integrated="-69.999"))
        assert facts.loudness_lufs == pytest.approx(-69.999)

    def test_the_last_stated_loudness_is_the_reading(self, tmp_path: Path) -> None:
        """ebur128 states the loudness integrated so far once per window.

        Every window before the last states a partial answer, so taking the
        first match reports the loudness of the opening 100 ms. Measured on a
        440 Hz tone: the first window states -70.000 and the last -21.776, which
        is what its own end-of-run summary rounds to -21.8.
        """
        facts = _audio_from(tmp_path, _measure_log(superseded="-70.0", integrated="-21.8"))
        assert facts.loudness_lufs == pytest.approx(-21.8)


class TestReadAudioFactsTakesNoDurationFromTheFacts:
    """The verdict no longer consults a duration, so no duration can bend it.

    `read_audio_facts` is exported and the facts can be built by the caller, so
    while a duration decided the verdict the caller decided it too — and where
    those facts come from `read_stream_facts`, the file decided it. Measured:
    three rewritten bytes of an `mdhd` were enough.
    """

    @pytest.mark.parametrize("duration_ms", [None, 0, 1, 10_000_000])
    def test_the_verdict_is_the_same_whatever_the_facts_state(
        self, tmp_path: Path, duration_ms: int | None
    ) -> None:
        facts = _audio_from(
            tmp_path,
            _measure_log(silences=[(0.0, 1.0)], integrated="-22.2"),
            facts=_stream_facts(duration_ms=duration_ms),
        )
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-22.2)


class TestReadAudioFactsSilentAudioMaterial:
    def test_rejects_an_audio_file_with_no_audible_sound(self, tmp_path: Path) -> None:
        """`Material` forbids `kind=AUDIO` with `has_audio=False`, so it cannot be built.

        Handing back a fact that cannot be stored would only move the failure to
        a place that raises `InvalidMaterialModel` instead of a probe rejection.
        """
        excinfo = _audio_reject(
            tmp_path,
            _measure_log(open_silence=0.0, integrated="-70.0"),
            facts=_stream_facts(ProbedMaterialKind.AUDIO, duration_ms=2000),
        )
        assert _rejection(excinfo) is MaterialProbeRejection.SILENT_AUDIO

    def test_accepts_an_audio_file_that_has_sound(self, tmp_path: Path) -> None:
        facts = _audio_from(
            tmp_path,
            _measure_log(),
            facts=_stream_facts(ProbedMaterialKind.AUDIO, duration_ms=2000),
        )
        assert facts.has_audio is True


class TestReadAudioFactsMeasureFailure:
    def test_rejects_a_measure_that_states_findings_but_no_loudness(self, tmp_path: Path) -> None:
        """ebur128 states one reading per 100 ms window, so a report holding any
        frame at all should already hold several. None means the pass did not run
        the way it was asked to, whatever its exit code said.
        """
        excinfo = _audio_reject(tmp_path, _measure_log(silences=[(0.0, 1.0)], integrated=None))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_accepts_a_measure_that_states_nothing_at_all(self, tmp_path: Path) -> None:
        """A sound track shorter than one window states nothing, measured.

        Rejecting an empty report would turn a 50 ms clip into a failure the user
        can do nothing about, so the two absences are told apart rather than
        merged.
        """
        facts = _audio_from(tmp_path, _measure_log(integrated=None))
        assert facts.has_audio is True
        assert facts.loudness_lufs is None

    def test_rejects_a_measure_that_exits_non_zero(self, tmp_path: Path) -> None:
        excinfo = _audio_reject(tmp_path, _measure_log(), measure_exit=1)
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_a_measure_killed_by_a_signal(self, tmp_path: Path) -> None:
        excinfo = _audio_reject(tmp_path, _measure_log(), measure_signal=True)
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_CRASHED

    def test_rejects_a_measure_that_outruns_its_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(material_probe, "MEASURE_TIMEOUT_SECONDS", 0.2)
        excinfo = _audio_reject(tmp_path, _measure_log(), sleep="5")
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_rejects_an_oversized_measure_report(self, tmp_path: Path) -> None:
        """A report already complete when the size is read: caught after the exit."""
        excinfo = _audio_reject(
            tmp_path, "x" * (MAX_MEASURE_OUTPUT_BYTES + 1) + "\n" + _measure_log()
        )
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_stops_a_measure_still_writing_past_the_limit(self, tmp_path: Path) -> None:
        """Checking the size after the process exits does not bound what it writes.

        Measured: a 120-second 720p file with a scrambled payload wrote 2.33 MB
        of decoder diagnostics in under a second, and nothing stopped it
        carrying on for the whole 15-minute timeout — the failure matrix's "disk
        full" row, reached from inside a single import.

        The stub writes past the limit and then stays alive. Only a limit
        enforced while it writes can end this call, and the marker it would have
        left behind is what proves it was ended rather than waited out.
        """
        excinfo = _audio_reject(
            tmp_path,
            "x" * (MAX_MEASURE_OUTPUT_BYTES + 1) + "\n" + _measure_log(),
            linger="5",
        )
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED
        assert not (tmp_path / ".probe-finished").exists()

    def test_lets_a_slow_measure_under_the_limit_finish(self, tmp_path: Path) -> None:
        """The size is what ends it, not the waiting — otherwise every slow file dies."""
        facts = _audio_from(tmp_path, _measure_log(), linger="0.4")
        assert facts.has_audio is True
        assert (tmp_path / ".probe-finished").exists()

    def test_lets_a_measure_sitting_exactly_on_the_limit_finish(self, tmp_path: Path) -> None:
        """The endpoint for the check that runs while the report is still growing.

        The limit rejects longer, not equal, and the after-the-exit check has an
        endpoint case of its own — this one is the only thing pinning the
        comparison that runs mid-flight.
        """
        body = _measure_log()
        padding = MAX_MEASURE_OUTPUT_BYTES - len(body.encode("utf-8"))
        assert padding >= 0
        facts = _audio_from(tmp_path, ("#" * padding) + body, linger="0.4")
        assert facts.has_audio is True
        assert (tmp_path / ".probe-finished").exists()


class TestReadAudioFactsInvocation:
    def test_takes_the_findings_off_the_filter_graphs_own_channel(self, tmp_path: Path) -> None:
        """The whole shape of the fix, asserted on the command line that carries it.

        `ametadata` prints frame metadata, and frame metadata is written by the
        filters. The `mode=delete` in front of them drops anything the demuxer or
        decoder attached, so nothing the file states can be in there; the
        diagnostics, which is where the file does get to write, are asked for at
        `error` and read by nobody.
        """
        _audio_from(tmp_path, _measure_log(), argv_log=True)
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        filters = argv[argv.index("-af") + 1].split(",")
        assert filters[0] == "ametadata=mode=delete"
        assert filters[-1] == "ametadata=mode=print:file=-"
        assert filters.index("ametadata=mode=delete") < filters.index(
            f"silencedetect=noise={material_probe.SILENCE_NOISE_FLOOR_DB}dB"
            f":d={material_probe.SILENCE_MINIMUM_SECONDS}"
        )
        assert argv[argv.index("-v") + 1] == "error"

    def test_asks_ebur128_to_state_its_reading_on_that_channel(self, tmp_path: Path) -> None:
        """Without `metadata=1` the loudness exists only in the summary it logs.

        That summary lands on the stream the file can write into, and takes the
        first match: measured, a tag naming itself `x\\n    I: -3.0 LUFS` made a
        -21.8 LUFS tone report -3.0.
        """
        _audio_from(tmp_path, _measure_log(), argv_log=True)
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        filters = argv[argv.index("-af") + 1].split(",")
        assert "ebur128=peak=none:framelog=quiet:metadata=1" in filters
        # The five ebur128 states that are not read, dropped before the sink.
        assert sum(f.startswith("ametadata=mode=delete:key=lavfi.r128.") for f in filters) == 5

    def test_detects_silence_downstream_of_the_loudness_filter(self, tmp_path: Path) -> None:
        """Upstream of ebur128, three silence events in four are thrown away.

        ebur128 asks libavfilter for frames of exactly one window, so the
        decoder's frames are merged before it sees them and merging keeps only
        the first frame's metadata, so a whole frame goes whenever one group
        takes two — which needs a decoded frame no longer than half a window.
        Measured with silencedetect in front, over 24 ordinary files holding four
        seconds of audible tone: 7 came back rejected as silent. Downstream it
        sees frames that are already the fixed length and all 24 come back right.
        """
        _audio_from(tmp_path, _measure_log(), argv_log=True)
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        filters = argv[argv.index("-af") + 1].split(",")
        loudness = next(index for index, f in enumerate(filters) if f.startswith("ebur128="))
        silence = next(index for index, f in enumerate(filters) if f.startswith("silencedetect="))
        assert silence > loudness

    def test_silences_the_per_frame_loudness_log(self, tmp_path: Path) -> None:
        """Without `framelog=quiet` ebur128 prints a line every 100 ms.

        Measured on a 2-second file: 65 stderr lines against 45 with it, a gap
        that grows with duration until a 4-hour import emits roughly 144,000
        lines. Those print at `info` and this pass asks for `error`, so on the
        command line as it stands the diagnostics are empty either way —
        measured, 0 bytes for a 60-second file. This keeps it from coming back
        the moment anyone raises the level to look at something.
        """
        _audio_from(tmp_path, _measure_log(), argv_log=True)
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        filters = next(a for a in argv if "ebur128" in a)
        assert "framelog=quiet" in filters
        assert "silencedetect" in filters
        assert "-nostdin" in argv

    def test_asks_for_no_picture_at_all(self, tmp_path: Path) -> None:
        """Nothing here looks at the picture, and `-f null` still selects it without this.

        Measured on a 120-second 1280x720 clip: 4.10 s of CPU against 0.17 s
        with `-vn` — about 96% of the work spent decoding frames into a sink.
        It lands on the timeout, and a timeout is reported as `PROBE_FAILED`,
        whose whole point is "worth retrying"; a long enough film would fail
        that way every retry.

        `-vn` sits after the input, as an output option: ffmpeg reads its input
        first, and putting it earlier would make it mean something else.
        """
        _audio_from(tmp_path, _measure_log(), argv_log=True)
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        assert "-vn" in argv
        assert argv.index("-vn") > argv.index("-i") + 1

    def test_names_the_input_before_the_output_options(self, tmp_path: Path) -> None:
        """ffmpeg reads its input first and has no `--`; an absolute path is the guard."""
        _audio_from(tmp_path, _measure_log(), argv_log=True)
        argv = (tmp_path / ".probe-argv").read_text(encoding="utf-8").splitlines()
        source_index = argv.index("-i") + 1
        assert argv[source_index].startswith("/")
        assert argv.index("-af") > source_index
        assert "--" not in argv


class TestReadAudioFactsLeaksNothing:
    def test_no_traceback_frame_retains_the_measure_report(self, tmp_path: Path) -> None:
        """Whatever the report holds, no frame on the raising path holds it.

        The stream that names the file is discarded now, so the report is not
        meant to carry a path at all — this pins the structure that would keep
        one out anyway: the text is written to a file, handed straight to the
        parser, and reduced to facts before anything can reject.
        """
        secret = "operator-private-wedding-2021"
        log = (
            _measure_log(silences=[(0.0, 1.0)], integrated=None)
            + f"/private/var/folders/ab/{secret}.mp4: something went wrong\n"
        )
        excinfo = _audio_reject(tmp_path, log)
        module_file = material_probe.__file__
        traceback = excinfo.value.__traceback__
        inspected = 0
        while traceback is not None:
            frame = traceback.tb_frame
            if frame.f_code.co_filename == module_file:
                inspected += 1
                for value in frame.f_locals.values():
                    assert secret not in repr(value)
            traceback = traceback.tb_next
        assert inspected, "no material_probe frame was inspected"


def _rendered_traceback(error: BaseException) -> str:
    """What `logging.exception` and an uncaught exception both print."""
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


class TestNoRenderedTracebackCarriesThePath:
    """Walking `f_locals` misses the other half of a traceback: the chained exception.

    Both steps reject from inside `except (OSError, subprocess.SubprocessError)`,
    and `subprocess.TimeoutExpired.cmd` is the whole argv — the source path
    among it. Python links that exception onto `__context__`, and every default
    renderer prints it, so the path reaches any ordinary log line without a
    single frame holding it.
    """

    def test_the_reading_step_renders_no_path_when_it_times_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(material_probe, "PROBE_TIMEOUT_SECONDS", 0.2)
        secret = "operator-private-holiday-2019"
        tools = _tools(tmp_path, sleep="5", stdout=_probe_json([_video_stream()]))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, _source(tmp_path, f"{secret}.mp4"))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED
        assert secret not in _rendered_traceback(excinfo.value)
        assert excinfo.value.__context__ is None

    def test_the_reading_step_renders_no_path_for_a_missing_source(self, tmp_path: Path) -> None:
        """The commonest failure of all, and `OSError.filename` is the path itself.

        Four more rejections are raised from inside a handler — both path
        guards, the duration parser and the JSON parser — and this is the one a
        user reaches by moving a file. Dropping the reference the way the two
        subprocess calls do would mean restructuring each of them, so the
        suppression in `_reject` is what covers them.
        """
        secret = "operator-private-anniversary-2024"
        tools = _tools(tmp_path, stdout=_probe_json([_video_stream()]))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_stream_facts(tools, tmp_path / f"{secret}.mp4")
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE
        assert secret not in _rendered_traceback(excinfo.value)

    def test_the_measuring_step_renders_no_path_for_a_missing_source(self, tmp_path: Path) -> None:
        secret = "operator-private-reunion-2023"
        tools = _audio_tools(tmp_path, _measure_log())
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_audio_facts(tools, tmp_path / f"{secret}.mp4", _stream_facts())
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE
        assert secret not in _rendered_traceback(excinfo.value)

    def test_the_measuring_step_renders_no_path_when_it_times_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(material_probe, "MEASURE_TIMEOUT_SECONDS", 0.2)
        secret = "operator-private-wedding-2021"
        tools = _audio_tools(tmp_path, _measure_log(), sleep="5")
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_audio_facts(tools, _source(tmp_path, f"{secret}.mp4"), _stream_facts())
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED
        assert secret not in _rendered_traceback(excinfo.value)
        assert excinfo.value.__context__ is None


class TestReadAudioFactsGuardsItsOwnInputs:
    """The measuring step re-checks everything the reading step checks.

    It is a separate entry point, so a caller can reach it with a source the
    reading step never saw.
    """

    def test_rejects_a_missing_source(self, tmp_path: Path) -> None:
        tools = _audio_tools(tmp_path, _measure_log())
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_audio_facts(tools, tmp_path / "absent.mp4", _stream_facts())
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_relative_source_path(self, tmp_path: Path) -> None:
        tools = _audio_tools(tmp_path, _measure_log())
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_audio_facts(tools, Path("clip.mp4"), _stream_facts())
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_fifo_as_source(self, tmp_path: Path) -> None:
        tools = _audio_tools(tmp_path, _measure_log())
        fifo = tmp_path / "pipe.mp4"
        os.mkfifo(fifo)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_audio_facts(tools, fifo, _stream_facts())
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_revalidates_the_tools_before_measuring(self, tmp_path: Path) -> None:
        tools = _audio_tools(tmp_path, _measure_log())
        source = _source(tmp_path)
        os.unlink(tools.ffmpeg_path)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_audio_facts(tools, source, _stream_facts())
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_leaves_the_parent_stdin_untouched(self, tmp_path: Path) -> None:
        """Same protocol channel as the reading step, same guard needed."""
        sentinel = b"BOOTSTRAP-SENTINEL\n"
        seeded = tmp_path / "stdin.bin"
        seeded.write_bytes(sentinel)
        tools = _audio_tools(tmp_path, _measure_log(), drain_stdin=True)
        source = _source(tmp_path)
        saved = os.dup(0)
        try:
            with seeded.open("rb") as handle:
                os.dup2(handle.fileno(), 0)
                read_audio_facts(tools, source, _stream_facts())
                assert os.read(0, len(sentinel)) == sentinel
        finally:
            os.dup2(saved, 0)
            os.close(saved)
        assert (tmp_path / ".probe-stdin").read_bytes() == b""

    def test_rejects_a_report_that_vanishes_before_it_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reading the report can fail, and failing there must stay a rejection.

        `stat()`, `read_text()` and the workspace's own cleanup all sit after
        the process has exited and all raise `OSError`. Left outside the `try`
        they escape as themselves — callers catching `MaterialProbeRejected`
        miss them, and `OSError.filename` is the path in plain text. This is the
        same escape shape `c96cbae` closed for `_require_tool`.

        The stub deletes the workspace as its last act, which is the failure
        matrix's "file removed underneath us" rather than an injected error.
        """
        monkeypatch.setattr(tempfile, "tempdir", os.fspath(tmp_path))
        excinfo = _audio_reject(tmp_path, _measure_log(), wipe_workspace=True)
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

    def test_kills_a_measure_whose_report_vanishes_while_it_still_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same disappearance, but during the flight rather than after it.

        The size is read once per poll while the child is still running, so the
        workspace going away underneath it raises there — inside the `with`, not
        after it. Leaving a `Popen` block by exception waits without a timeout
        and never kills, so the call sits out the child's natural life: measured
        with a one-second timeout and a thirty-second child, 30.59 s. The timeout
        is fifteen minutes in production, which is the failure matrix's "hangs"
        row reached from an ordinary tmp sweeper.

        The marker the stub would leave behind is what separates "killed" from
        "waited out" — the same proof `test_stops_a_measure_still_writing_past_the_limit`
        relies on.
        """
        monkeypatch.setattr(tempfile, "tempdir", os.fspath(tmp_path))
        excinfo = _audio_reject(tmp_path, _measure_log(), wipe_workspace_early=True, linger="5")
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED
        assert not (tmp_path / ".probe-finished").exists()

    def test_kills_a_measure_when_the_import_itself_is_interrupted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl-C during an import must not leave ffmpeg decoding a four-hour film.

        `KeyboardInterrupt` and `SystemExit` are not `Exception`, so a handler
        narrower than `BaseException` would let them out of the `Popen` block the
        one way that waits for the child without a timeout and never kills it.
        Neither is caught anywhere below either, so the interrupt still arrives
        as itself rather than as a rejection.

        The marker the other kill cases rely on proves nothing here: CPython
        special-cases `KeyboardInterrupt` in `Popen.__exit__` and waits a quarter
        of a second rather than indefinitely, so an unkilled child simply
        outlives the assertion instead of delaying it — measured, the marker
        alone let `except Exception` survive. How the child ended is what
        separates them, and asking the child to record that races its own
        startup against the first poll 0.1 s later: a stub that had not reached
        the recording step yet failed this 2 runs in 20. The parent's own handle
        is subject to no such race.
        """
        spawned: list[subprocess.Popen[bytes]] = []
        launch = subprocess.Popen

        def record(*arguments: Any, **keywords: Any) -> subprocess.Popen[bytes]:
            process: subprocess.Popen[bytes] = launch(*arguments, **keywords)
            spawned.append(process)
            return process

        stat = Path.stat

        def interrupt(self: Path, *arguments: Any, **keywords: Any) -> os.stat_result:
            if self.name == "report.log":
                raise KeyboardInterrupt
            return stat(self, *arguments, **keywords)

        # Built before the recorder is installed: laying the stub down warms it
        # by running it once, and that spawn is not the one under test.
        tools = _audio_tools(tmp_path, _measure_log(), linger="2")
        source = _source(tmp_path)
        monkeypatch.setattr(subprocess, "Popen", record)
        monkeypatch.setattr(Path, "stat", interrupt)
        with pytest.raises(KeyboardInterrupt):
            read_audio_facts(tools, source, _stream_facts())
        assert len(spawned) == 1
        # Reaps whichever way it ended, so the verdict is how it ended and not
        # how quickly: killed gives `-SIGKILL`, left alone gives the stub's own
        # exit code once its sleep is over.
        spawned[0].wait(timeout=10)
        assert spawned[0].returncode == -signal.SIGKILL

    def test_accepts_a_report_exactly_at_the_size_limit(self, tmp_path: Path) -> None:
        body = _measure_log()
        padding = MAX_MEASURE_OUTPUT_BYTES - len(body.encode("utf-8"))
        assert padding >= 0
        facts = _audio_from(tmp_path, ("#" * padding) + body)
        assert facts.has_audio is True


PACKAGED_TOOL_SUBDIRECTORY = "media-toolchain/bin"


def _packaged_tool_root() -> Path:
    """Where the build cache lays the packaged pair out on this machine.

    `cache_root()` is the one function that knows: it honours
    `AUTOMATION_TOOL_BUILD_CACHE` and gives Windows and Linux roots of their
    own. Spelling `~/Library/Caches/...` out here instead would turn "the
    toolchain lives elsewhere on this machine" into "the toolchain is missing"
    on every machine that is not a default macOS one.
    """
    root: Path = cache_root()
    return root / PACKAGED_TOOL_SUBDIRECTORY


def _packaged_tools() -> PackagedMediaTools:
    """The packaged pair, resolved the way the build cache lays it out.

    A stub answers whatever it is asked, so it cannot tell a valid ffmpeg
    command line from one ffmpeg rejects outright — every stub test passed while
    the real binary failed on every file. Missing tooling fails loudly rather
    than skipping: a skipped acceptance test looks green.
    """
    root = _packaged_tool_root()
    ffprobe, ffmpeg = root / "ffprobe", root / "ffmpeg"
    if not (ffprobe.exists() and ffmpeg.exists()):
        raise AssertionError(
            "packaged media toolchain missing; run scripts/prepare_video_runtime.py"
        )
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


def _encode(ffmpeg: Path, *arguments: str) -> None:
    subprocess.run(
        [os.fspath(ffmpeg), "-y", "-loglevel", "error", *arguments],
        check=True,
        capture_output=True,
    )


# Names for the materials the packaged ffmpeg builds below. The two planted ones
# spell out an attack rather than a shape: `PLANTED_NAME` is what an attacker
# calls a file, `PLANTED_TAG` is what they write inside one.
PLANTED_STATEMENT = "silence_duration: 9999"
PLANTED_NAME = f"{PLANTED_STATEMENT} holiday.m4a"
# A tag *name* carrying a newline. ffmpeg continues a tag value across lines by
# indenting it and prefixing `: `, but it prints the key verbatim, so whatever
# follows the newline in a key lands in column 0 — where it can reproduce a
# filter's line byte for byte. These two spell out the two forgeries that buys.
FORGED_SILENCE_KEY = f"x\n[Parsed_silencedetect_0 @ 0x1] {PLANTED_STATEMENT}: v"
FORGED_LOUDNESS_LUFS = -3.0
FORGED_LOUDNESS_KEY = f"x\n    I:         {FORGED_LOUDNESS_LUFS} LUFS\n    y"
# The sound track's stated duration, rewritten to 44/44100 s — one millisecond,
# the shortest span `read_stream_facts` will hand on.
FORGED_SOUND_TRACK_TICKS = 44
SOUND_TRACK_SAMPLE_RATE = 44100
# ebur128 states its reading once per this many seconds, and asks libavfilter for
# frames of exactly that length to do it. Measured: 0.05 s and 0.09 s of sound
# produce no reading at all, 0.10 s produces one, and every frame the sink sees
# is stamped 0.1 s after the last.
GRID_SECONDS = 0.1
# How much sound the channel's byte rate is measured over, and how much room the
# limit has to leave on top of the rate that projects. The margin covers what
# the projection cannot see: a four-hour file's frame numbers and timestamps
# carry more digits than a one-minute file's, so its blocks are slightly longer.
RATE_SAMPLE_SECONDS = 60
REPORT_LIMIT_MARGIN = 1.5


def _shorten_the_stated_sound_track(source: Path, destination: Path) -> None:
    """Rewrite one MP4's sound track duration in place, changing nothing else.

    `mdhd` is a fixed-layout box, so the duration is at a known offset: type(4),
    version+flags(4), creation(4), modification(4), timescale(4), duration(4).
    Only the sound track's is touched — the picture's `mdhd` is what
    `format.duration` is built from, and collapsing that too would make the file
    fail on its container duration long before the sound track mattered.

    The sound track is the one whose media timescale is the sample rate. The
    assertion is what keeps this honest: if it ever matches none or both, the
    test fails rather than silently producing an unmodified file.
    """
    data = bytearray(source.read_bytes())
    patched = 0
    index = data.find(b"mdhd")
    while index != -1:
        timescale = int.from_bytes(data[index + 16 : index + 20], "big")
        if data[index + 4] == 0 and timescale == SOUND_TRACK_SAMPLE_RATE:
            data[index + 20 : index + 24] = FORGED_SOUND_TRACK_TICKS.to_bytes(4, "big")
            patched += 1
        index = data.find(b"mdhd", index + 1)
    assert patched == 1, f"expected exactly one sound track mdhd, patched {patched}"
    assert len(data) == source.stat().st_size
    destination.write_bytes(bytes(data))


@pytest.fixture(scope="session")
def real_media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Materials built by the packaged ffmpeg, covering every parsed field.

    A stub answers whatever it is asked, so it can neither reject an illegal
    command line nor produce the output shape the parser has to survive. Every
    field this module reads out of ffmpeg's report needs one material here that
    really made ffmpeg print it — a continuous tone prints no `silence_*` line
    at all, so a tone alone leaves three of the four patterns unproven.
    """
    tools = _packaged_tools()
    ffmpeg = tools.ffmpeg_path
    directory = tmp_path_factory.mktemp("real-media")
    media = {
        "tone": directory / "tone.m4a",
        "silent": directory / "silent.m4a",
        "tail_silent": directory / "tail-silent.m4a",
        "planted_tag": directory / "holiday.m4a",
        "planted_line": directory / "reunion.m4a",
        "planted_name": directory / PLANTED_NAME,
        "short_sound": directory / "short-sound.mp4",
        "plain_copy": directory / "plain-copy.mp4",
        "forged_silence_key": directory / "forged-silence-key.mp4",
        "forged_loudness_key": directory / "forged-loudness-key.mp4",
        "lead_silence": directory / "lead-silence.mp4",
        "forged_sound_track": directory / "forged-sound-track.mp4",
        "sub_bin": directory / "sub-bin.m4a",
        "on_grid_lead": directory / "on-grid-lead.m4a",
        "off_grid_lead": directory / "off-grid-lead.m4a",
        "mid_grid_lead": directory / "mid-grid-lead.m4a",
        "one_minute": directory / "one-minute.m4a",
    }
    # Audible throughout: silencedetect stays quiet, ebur128 states a reading.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-c:a",
        "aac",
        os.fspath(media["tone"]),
    )
    # Digital silence throughout: the only material that makes silencedetect
    # print `silence_start`, `silence_end` and `silence_duration` for real.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-t",
        "3",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-c:a",
        "aac",
        os.fspath(media["silent"]),
    )
    # A second of tone, then silence to the end of the file.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-f",
        "lavfi",
        "-t",
        "2",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1",
        "-c:a",
        "aac",
        os.fspath(media["tail_silent"]),
    )
    # The tone again, carrying a comment tag that states silence. ffmpeg prints
    # every tag it reads, twice, into the same report the parser reads.
    _encode(
        ffmpeg,
        "-i",
        os.fspath(media["tone"]),
        "-c",
        "copy",
        "-metadata",
        f"comment={PLANTED_STATEMENT}",
        os.fspath(media["planted_tag"]),
    )
    # A tag carrying a newline, so the file writes a whole line of the report
    # itself — and copies the filter's line format exactly while doing it.
    _encode(
        ffmpeg,
        "-i",
        os.fspath(media["tone"]),
        "-c",
        "copy",
        "-metadata",
        f"comment=x\n[Parsed_silencedetect_0 @ 0x1] {PLANTED_STATEMENT}",
        os.fspath(media["planted_line"]),
    )
    # Byte-identical to the tone; only the name states silence.
    media["planted_name"].write_bytes(media["tone"].read_bytes())
    # Ten seconds of picture over two seconds of digitally silent sound — the
    # ordinary shape of a clip whose sound track ends before its picture does.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:r=25:d=10",
        "-f",
        "lavfi",
        "-t",
        "2",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        os.fspath(media["short_sound"]),
    )
    # The same sound track in an ordinary MP4, and two copies of it whose only
    # difference is one tag *name*. `-c copy` keeps the AAC payload identical, so
    # any verdict that differs between these three came out of the tag.
    for name, arguments in (
        ("plain_copy", ()),
        ("forged_silence_key", ("-metadata", f"{FORGED_SILENCE_KEY}=1")),
        ("forged_loudness_key", ("-metadata", f"{FORGED_LOUDNESS_KEY}=1")),
    ):
        _encode(
            ffmpeg,
            "-i",
            os.fspath(media["tone"]),
            "-c",
            "copy",
            # MP4 carries only the tags it knows unless asked otherwise. A file
            # written by anything but ffmpeg has no such restraint, so this only
            # reproduces with the packaged tool what an authored file states
            # outright.
            "-movflags",
            "+use_metadata_tags",
            *arguments,
            os.fspath(media[name]),
        )
    # A second of silence and then nine seconds of tone, under ten seconds of
    # picture. Plainly audible, and the leading silence is long enough for
    # `silencedetect` to report it.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-t",
        "1",
        "-i",
        f"anullsrc=r={SOUND_TRACK_SAMPLE_RATE}:cl=mono",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=9",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:r=25:d=10",
        "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1[sound]",
        "-map",
        "2:v",
        "-map",
        "[sound]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        os.fspath(media["lead_silence"]),
    )
    _shorten_the_stated_sound_track(media["lead_silence"], media["forged_sound_track"])
    # Three files that differ only in where their leading silence ends. ebur128
    # states its reading once per 100 ms, and a silence boundary that does not
    # land on that grid is the shape every other material here happens to avoid:
    # each of them is a whole number of seconds long.
    for name, lead in (
        ("on_grid_lead", GRID_SECONDS * 3),
        ("off_grid_lead", GRID_SECONDS * 3 + 0.01),
        ("mid_grid_lead", GRID_SECONDS * 5.5),
    ):
        _encode(
            ffmpeg,
            "-f",
            "lavfi",
            "-t",
            f"{lead:.2f}",
            "-i",
            f"anullsrc=r={SOUND_TRACK_SAMPLE_RATE}:cl=mono",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1",
            "-c:a",
            "aac",
            os.fspath(media[name]),
        )
    # Long enough for the channel's own rate to be worth measuring: the block a
    # window costs grows a little as the frame counter and timestamps get more
    # digits, so a two-second file would understate it badly.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={RATE_SAMPLE_SECONDS}",
        "-c:a",
        "aac",
        os.fspath(media["one_minute"]),
    )
    # Shorter than one of ebur128's 100 ms windows: measured, it states no
    # loudness at all, which must not be read as a broken measuring pass.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=0.05",
        "-c:a",
        "aac",
        os.fspath(media["sub_bin"]),
    )
    return media


@pytest.fixture(scope="session")
def real_clip(real_media: dict[str, Path]) -> Path:
    """Two seconds of real tone, encoded by the packaged ffmpeg."""
    return real_media["tone"]


class TestPackagedToolLocation:
    """The plan forbids spelling the cache path out here, for a measurable reason."""

    def test_the_tool_root_follows_the_build_cache_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`cache_root()` honours `AUTOMATION_TOOL_BUILD_CACHE`; this must follow it.

        A hard-coded `~/Library/Caches/...` ignores the override and gives the
        wrong root on Windows and Linux outright, so a present toolchain is
        reported as a missing one — the acceptance tests fail loudly for a
        reason that has nothing to do with the product.
        """
        monkeypatch.setenv("AUTOMATION_TOOL_BUILD_CACHE", os.fspath(tmp_path))
        assert _packaged_tool_root() == tmp_path / PACKAGED_TOOL_SUBDIRECTORY


class TestUntrustedTextCannotStateSilence:
    """ffmpeg's report is not only the filter's output: the file speaks in it too.

    Every line of it goes past the same patterns — the `Input #0 ... from
    '<path>'` header and the whole metadata dump included. Both are chosen by
    whoever produced the file, and a match anywhere in the report is charged as
    silence the filter never reported.
    """

    def test_a_name_that_states_silence_does_not_silence_the_file(
        self, real_media: dict[str, Path]
    ) -> None:
        """Byte-identical to the tone; only the name differs.

        A file called `silence_duration: 9999 holiday.m4a` is not exotic — the
        rejection it earns says "this audio has no sound", carries no path by
        design, and so leaves the operator no way to connect it to the name.
        """
        tools = _packaged_tools()
        planted = real_media["planted_name"]
        assert planted.read_bytes() == real_media["tone"].read_bytes()
        streams = read_stream_facts(tools, planted)
        facts = read_audio_facts(tools, planted, streams)
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-21.8, abs=1.0)

    def test_a_tag_that_states_silence_does_not_silence_the_file(
        self, real_media: dict[str, Path]
    ) -> None:
        """The same claim written inside the file, where renaming cannot undo it.

        Anything downloaded or handed over can carry this comment, and ffmpeg
        prints every tag it reads — once for the input, once for the output.
        """
        tools = _packaged_tools()
        planted = real_media["planted_tag"]
        streams = read_stream_facts(tools, planted)
        facts = read_audio_facts(tools, planted, streams)
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-21.8, abs=1.0)

    def test_a_tag_forging_the_filters_own_line_does_not_silence_the_file(
        self, real_media: dict[str, Path]
    ) -> None:
        """A newline in a tag buys a whole line, and the file spends it on a forgery.

        This is the vector an anchor on the prefix alone would still let
        through: the forged line reproduces `[Parsed_silencedetect_0 @ 0x1]`
        exactly. Only requiring it at the start of a line stops it — measured,
        ffmpeg indents a continued value and prefixes it with `: `.
        """
        tools = _packaged_tools()
        planted = real_media["planted_line"]
        streams = read_stream_facts(tools, planted)
        facts = read_audio_facts(tools, planted, streams)
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-21.8, abs=1.0)


class TestATagNameCannotForgeAWholeLine:
    """A tag *value* holding a newline is continued indented; a tag *name* is not.

    ffmpeg prints a tag as `av_log(ctx, INFO, "%s  %-16s: ", indent, tag->key)`
    followed by the value, and only the value gets the continuation treatment.
    Whatever follows a newline inside the key therefore starts in column 0, where
    it can reproduce a filter's own line byte for byte — prefix, address and all.
    No pattern over that text can tell the two apart, so nothing here is a
    question of anchoring: the parsed text has to come from somewhere the file
    cannot write.
    """

    def test_a_tag_name_forging_a_filter_line_does_not_silence_the_file(
        self, real_media: dict[str, Path]
    ) -> None:
        """Nine seconds of 440 Hz tone, rejected outright as having no sound.

        The rejection carries no path by design, so the operator is told their
        audio file is silent and given nothing to connect it to.
        """
        tools = _packaged_tools()
        for name in ("plain_copy", "forged_silence_key"):
            source = real_media[name]
            streams = read_stream_facts(tools, source)
            facts = read_audio_facts(tools, source, streams)
            assert facts.has_audio is True, name
            assert facts.loudness_lufs == pytest.approx(-21.8, abs=1.0), name

    def test_a_tag_name_forging_the_loudness_summary_is_not_the_reading(
        self, real_media: dict[str, Path]
    ) -> None:
        """The same channel states a loudness, and the file's number wins.

        ebur128's summary is printed after the input is dumped, and the pattern
        takes the first match, so a tag quoting the summary's shape is read in
        preference to the measurement.
        """
        tools = _packaged_tools()
        source = real_media["forged_loudness_key"]
        streams = read_stream_facts(tools, source)
        facts = read_audio_facts(tools, source, streams)
        assert facts.loudness_lufs is not None
        assert facts.loudness_lufs != pytest.approx(FORGED_LOUDNESS_LUFS)
        assert facts.loudness_lufs == pytest.approx(-21.8, abs=1.0)


class TestTheReportLimitFollowsTheChannelsCadence:
    """The limit is a consequence of the channel, not a round number.

    Every other case for it states the fixture in terms of the constant itself,
    so moving the constant moves the fixture with it and nothing goes red. What
    the value has to satisfy is a relation to something outside this module:
    ebur128 states a reading per 100 ms whatever the material, so the report of
    a file at `MAX_MATERIAL_DURATION_MS` is that rate times that duration. Cut
    the limit to a quarter and four-hour imports start failing as
    `PROBE_FAILED`, with nothing red to say so.
    """

    def test_the_limit_leaves_room_for_a_material_at_the_duration_limit(
        self, tmp_path: Path, real_media: dict[str, Path]
    ) -> None:
        tools = _packaged_tools()
        channel = tmp_path / "channel.log"
        with channel.open("wb") as sink:
            subprocess.run(
                [
                    os.fspath(tools.ffmpeg_path),
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    os.fspath(real_media["one_minute"]),
                    "-vn",
                    "-af",
                    material_probe._MEASURE_FILTERS,
                    "-f",
                    "null",
                    "-",
                ],
                stdout=sink,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        bytes_per_second = channel.stat().st_size / RATE_SAMPLE_SECONDS
        longest = MAX_MATERIAL_DURATION_MS / 1000
        needed = bytes_per_second * longest * REPORT_LIMIT_MARGIN
        assert needed <= MAX_MEASURE_OUTPUT_BYTES


class TestSilenceBoundariesThatMissTheLoudnessGrid:
    """Four seconds of plainly audible tone, and where its silence ends decides.

    `ebur128` asks libavfilter for 100 ms frames so it can state a reading per
    window, so the decoder's own frames are merged to that length before it sees
    them — and merging keeps only the first frame's metadata
    (`av_frame_copy_props(buf, frame0)`). A whole frame is swallowed, metadata
    and all, exactly when one group takes two of them, which needs a decoded
    frame no longer than half a window.

    The block length decides that, not the codec. Measured at 44.1 kHz against a
    4410-sample window: AAC decodes to 1024, PCM s16le to 4096, FLAC to 4608 by
    default — but FLAC written with 512, 1024 or 2048-sample blocks decodes to
    that, and upstream of ebur128 those lose events exactly like AAC (8/8, 8/8
    and 7/8 of eight silence positions rejected as silent, against 0/8 at 4096).
    The operator's files are not written by anything here, so a long default
    block in our own encoder protects nobody.

    What disappears here is the `silence_end` that proves sound resumed, and
    without it a file reads exactly like one that is silent to EOF — which for
    `kind=AUDIO` is a hard rejection of an audible file.

    The three materials differ only in where the leading silence ends. Measured
    on the packaged build: 0.30 s came back with sound and 0.31 s was rejected,
    ten milliseconds apart. Every other real material in this file is a whole
    number of seconds long, so all of them sit on the grid and none of them can
    see this.
    """

    @pytest.mark.parametrize("name", ["on_grid_lead", "off_grid_lead", "mid_grid_lead"])
    def test_a_span_that_closes_off_the_grid_still_counts_as_sound(
        self, real_media: dict[str, Path], name: str
    ) -> None:
        tools = _packaged_tools()
        source = real_media[name]
        streams = read_stream_facts(tools, source)
        assert streams.kind is ProbedMaterialKind.AUDIO
        facts = read_audio_facts(tools, source, streams)
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-21.9, abs=1.5)


class TestAStatedSoundTrackDurationCannotSilenceTheFile:
    """Three bytes of a header decide whether nine seconds of tone are heard.

    `mdhd` states the sound track's duration and the file states `mdhd`. Where
    that number bounded the window silence had to cover, shrinking it to one
    millisecond made a second of leading silence cover the whole track.
    """

    def test_a_forged_sound_track_duration_does_not_silence_the_file(
        self, real_media: dict[str, Path]
    ) -> None:
        tools = _packaged_tools()
        honest = real_media["lead_silence"]
        forged = real_media["forged_sound_track"]
        assert len(forged.read_bytes()) == len(honest.read_bytes())
        for source in (honest, forged):
            streams = read_stream_facts(tools, source)
            assert streams.duration_ms == 10000
            facts = read_audio_facts(tools, source, streams)
            assert facts.has_audio is True, source.name
            assert facts.loudness_lufs == pytest.approx(-21.8, abs=1.0), source.name


class TestSoundTrackShorterThanThePicture:
    """The silence total is measured on the sound track; the container is not it.

    A clip whose sound stops before its picture does is an ordinary shape, and
    comparing the one against the other is what let a file with no sound in it
    at all be reported as having sound.
    """

    def test_a_silent_sound_track_shorter_than_the_picture_is_still_silent(
        self, real_media: dict[str, Path]
    ) -> None:
        tools = _packaged_tools()
        source = real_media["short_sound"]
        streams = read_stream_facts(tools, source)
        assert streams.kind is ProbedMaterialKind.VIDEO
        assert streams.duration_ms == 10000
        assert streams.audio_codec == "aac"
        facts = read_audio_facts(tools, source, streams)
        assert facts.has_audio is False
        assert facts.loudness_lufs is None


class TestAgainstThePackagedBinaries:
    def test_measures_a_real_clip(self, real_clip: Path) -> None:
        """A 440 Hz tone reads -21.8 LUFS, measured.

        Asserting only `FLOOR < x <= CEILING` restates `_storable_loudness`'s
        own postcondition, so it holds for any reading the parser is willing to
        return and cannot tell a real measurement from a misparsed one.
        """
        tools = _packaged_tools()
        streams = read_stream_facts(tools, real_clip)
        assert streams.kind is ProbedMaterialKind.AUDIO
        assert streams.audio_codec == "aac"
        assert streams.duration_ms == 2000
        facts = read_audio_facts(tools, real_clip, streams)
        assert facts.has_audio is True
        assert facts.loudness_lufs is not None
        assert facts.loudness_lufs == pytest.approx(-21.8, abs=1.0)
        assert LOUDNESS_FLOOR_LUFS < facts.loudness_lufs <= LOUDNESS_CEILING_LUFS

    def test_rejects_a_real_file_that_is_silent_all_through(
        self, real_media: dict[str, Path]
    ) -> None:
        """The only case that makes silencedetect print for real, and a pin on how.

        A continuous tone prints no `silence_*` line at all, so a tone on its
        own leaves the silence patterns tested against nothing but a hand-written
        log — which is how a pattern that matched a file name got this far.

        It also pins the flush: this verdict needs a `silence_duration` covering
        the file, and the span that produces it only ends because silencedetect
        closes it at EOF. Measured across four shapes — a wholly silent file, a
        file that fades to silence, one truncated mid-silence, and a sound track
        shorter than its picture — every span came back closed. If that ever
        stops being true this goes red rather than quietly reporting sound.
        """
        tools = _packaged_tools()
        source = real_media["silent"]
        streams = read_stream_facts(tools, source)
        assert streams.kind is ProbedMaterialKind.AUDIO
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_audio_facts(tools, source, streams)
        assert _rejection(excinfo) is MaterialProbeRejection.SILENT_AUDIO

    def test_accepts_a_real_clip_shorter_than_one_loudness_window(
        self, real_media: dict[str, Path]
    ) -> None:
        """Fifty milliseconds of tone: audible, and ebur128 states nothing at all.

        Measured against the packaged build — 0.05 s and 0.09 s produce no
        `lavfi.r128.I` at all, 0.10 s produces one — because the reading is
        stated once per completed 100 ms window and a shorter track completes
        none. An empty report therefore cannot mean "the pass went wrong".
        """
        tools = _packaged_tools()
        source = real_media["sub_bin"]
        streams = read_stream_facts(tools, source)
        assert streams.kind is ProbedMaterialKind.AUDIO
        assert streams.duration_ms == 50
        facts = read_audio_facts(tools, source, streams)
        assert facts.has_audio is True
        assert facts.loudness_lufs is None

    def test_reads_sound_from_a_real_file_that_ends_in_silence(
        self, real_media: dict[str, Path]
    ) -> None:
        """A second of tone then silence to the end: silence is reported, sound wins."""
        tools = _packaged_tools()
        source = real_media["tail_silent"]
        streams = read_stream_facts(tools, source)
        facts = read_audio_facts(tools, source, streams)
        assert facts.has_audio is True
        assert facts.loudness_lufs is not None

    def test_the_rejection_message_stays_fixed_on_the_measuring_step(
        self, real_media: dict[str, Path]
    ) -> None:
        """Same guarantee the reading step has: a closed reason, and no text from ffmpeg."""
        tools = _packaged_tools()
        source = real_media["silent"]
        streams = read_stream_facts(tools, source)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            read_audio_facts(tools, source, streams)
        rendered = str(excinfo.value)
        assert rendered == "material probe rejected"
        assert os.fspath(source.parent) not in rendered
        assert "silence_duration" not in rendered
