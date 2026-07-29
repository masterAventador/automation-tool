from __future__ import annotations

import hashlib
import json
import os
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
if [ -f "$d/.probe-linger" ]; then
  sleep "$(cat "$d/.probe-linger")"
  printf 1 > "$d/.probe-finished"
fi
if [ -f "$d/.probe-wipe" ]; then rm -rf "$d"/automation-tool-measure-*; fi
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


def _measure_log(
    *,
    silences: list[tuple[float, float]] | None = None,
    integrated: str | None = "-21.8",
    trailing_silence_start: float | None = None,
) -> str:
    """Reproduce the stderr shape ffmpeg emits for silencedetect + ebur128."""
    lines: list[str] = []
    for start, end in silences or []:
        lines.append(f"[Parsed_silencedetect_0 @ 0x1] silence_start: {start}")
        lines.append(
            f"[Parsed_silencedetect_0 @ 0x1] silence_end: {end} | silence_duration: {end - start}"
        )
    if trailing_silence_start is not None:
        lines.append(f"[Parsed_silencedetect_0 @ 0x1] silence_start: {trailing_silence_start}")
    if integrated is not None:
        lines.append("[Parsed_ebur128_1 @ 0x2] Summary:")
        lines.append("")
        lines.append("  Integrated loudness:")
        lines.append(f"    I:         {integrated} LUFS")
        lines.append("    Threshold: -31.8 LUFS")
    return "\n".join(lines) + "\n"


def _metadata_dump(*statements: str) -> str:
    """The shape ffmpeg prints for the tags it read out of a file.

    Reproduced from a measured report: the key is padded out to a fixed width,
    so the colon never lands against a short key, and every value the file
    carries is printed verbatim.
    """
    lines = ["  Metadata:"]
    lines.extend(f"    comment         : {statement}" for statement in statements)
    return "\n".join(lines) + "\n"


def _continued_metadata(statement: str) -> str:
    """How ffmpeg continues a tag value that carries a newline.

    Copied from a measured report, byte for byte: the continuation is indented
    and prefixed with `: `, which is the only thing standing between an
    attacker-chosen line and the filter's own line format.
    """
    return f"  Metadata:\n    comment         : x\n                    : {statement}\n"


def _stream_facts(
    kind: ProbedMaterialKind = ProbedMaterialKind.VIDEO,
    *,
    duration_ms: int | None = 3000,
    audio_codec: str | None = "aac",
    audio_duration_ms: int | None = None,
) -> MediaStreamFacts:
    return MediaStreamFacts(
        kind=kind,
        duration_ms=duration_ms,
        width=None if kind is ProbedMaterialKind.AUDIO else 640,
        height=None if kind is ProbedMaterialKind.AUDIO else 360,
        video_codec=None if kind is ProbedMaterialKind.AUDIO else "h264",
        audio_codec=audio_codec,
        audio_duration_ms=audio_duration_ms,
    )


def _audio_tools(directory: Path, log: str, **stub: Any) -> PackagedMediaTools:
    """Only the ffmpeg stub carries behaviour: the measuring step never runs ffprobe."""
    return PackagedMediaTools(
        ffprobe_path=_ffprobe_stub(directory),
        ffmpeg_path=_ffmpeg_stub(directory, stderr=log, **stub),
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


class TestReadStreamFactsSoundTrackDuration:
    """The sound track's own duration, which is a different fact from the container's.

    One case per way ffprobe can decline to state one: absence is ordinary here
    — measured, Matroska and FLV state a container duration and no stream
    duration at all — so none of these may reject the file. A merged case would
    let one of these paths never run while the whole thing still read as covered.
    """

    def test_reads_the_sound_track_duration_when_it_is_stated(self, tmp_path: Path) -> None:
        facts = _facts_from(
            tmp_path,
            _probe_json([_video_stream(), _audio_stream(duration="2.000000")], duration="10.0"),
        )
        assert facts.duration_ms == 10000
        assert facts.audio_duration_ms == 2000

    def test_states_no_sound_track_duration_when_the_stream_omits_it(self, tmp_path: Path) -> None:
        """The Matroska and FLV shape: a container duration and nothing per stream."""
        facts = _facts_from(tmp_path, _probe_json([_video_stream(), _audio_stream()]))
        assert facts.duration_ms == 3000
        assert facts.audio_duration_ms is None

    def test_states_no_sound_track_duration_for_a_non_text_value(self, tmp_path: Path) -> None:
        facts = _facts_from(tmp_path, _probe_json([_audio_stream(duration=2.0)]))
        assert facts.audio_duration_ms is None

    def test_states_no_sound_track_duration_for_an_unparseable_value(self, tmp_path: Path) -> None:
        facts = _facts_from(tmp_path, _probe_json([_audio_stream(duration="N/A")]))
        assert facts.audio_duration_ms is None

    def test_states_no_sound_track_duration_for_a_non_finite_value(self, tmp_path: Path) -> None:
        """`round()` raises on infinity and NaN alike, so this guard really is load-bearing.

        Without it the escape is an `OverflowError`, which no caller of this
        module is catching.
        """
        facts = _facts_from(tmp_path, _probe_json([_audio_stream(duration="inf")]))
        assert facts.audio_duration_ms is None

    def test_states_no_sound_track_duration_for_a_span_below_a_millisecond(
        self, tmp_path: Path
    ) -> None:
        """Zero would be a window every file covers, which is the shape of the bug."""
        facts = _facts_from(tmp_path, _probe_json([_audio_stream(duration="0.0004")]))
        assert facts.audio_duration_ms is None

    def test_states_no_sound_track_duration_without_a_sound_track(self, tmp_path: Path) -> None:
        facts = _facts_from(tmp_path, _probe_json([_video_stream()]))
        assert facts.audio_duration_ms is None


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

    def test_reports_no_effective_sound_when_silence_covers_the_file(self, tmp_path: Path) -> None:
        """`has_audio` means audible sound, not the presence of a track.

        Measured: a digitally silent 2-second AAC file reports
        `silence_duration: 2.020136` — slightly longer than the stream itself,
        because of encoder padding. The comparison therefore has to tolerate
        silence running past the nominal duration.
        """
        facts = _audio_from(
            tmp_path,
            _measure_log(silences=[(0.0, 2.020136)], integrated="-70.0"),
            facts=_stream_facts(duration_ms=2000),
        )
        assert facts.has_audio is False
        assert facts.loudness_lufs is None


class TestReadAudioFactsIgnoresTextTheFilterDidNotWrite:
    """Only the filter's own lines count. Everything else in the report is the file talking."""

    def test_a_metadata_tag_stating_a_silence_span_counts_for_nothing(self, tmp_path: Path) -> None:
        facts = _audio_from(
            tmp_path,
            _metadata_dump("silence_duration: 9999") + _measure_log(),
        )
        assert facts.has_audio is True
        assert facts.loudness_lufs == pytest.approx(-21.8)

    def test_a_tag_quoting_the_filters_own_line_format_counts_for_nothing(
        self, tmp_path: Path
    ) -> None:
        """A newline inside a tag lets the file write a whole line of its own.

        The line it writes can copy the filter's `[Parsed_silencedetect_0 @ ...]`
        prefix exactly — measured, that is what ffmpeg prints. What it cannot
        copy is the *start* of the line, because a continued value is indented
        and prefixed with `: `. Anchoring at the prefix without anchoring at the
        line start therefore stops nothing.
        """
        facts = _audio_from(
            tmp_path,
            _continued_metadata("[Parsed_silencedetect_0 @ 0x1] silence_duration: 9999")
            + _measure_log(),
        )
        assert facts.has_audio is True

    def test_a_metadata_tag_stating_a_loudness_counts_for_nothing(self, tmp_path: Path) -> None:
        """The reading has to come from ebur128's summary, not from a tag quoting one."""
        facts = _audio_from(
            tmp_path,
            _metadata_dump("I:         -70.0 LUFS") + _measure_log(integrated="-3.2"),
        )
        assert facts.loudness_lufs == pytest.approx(-3.2)


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

    def test_states_no_loudness_when_the_measure_is_above_full_scale(self, tmp_path: Path) -> None:
        """Heavily limited material can integrate above 0 LUFS, which cannot be stored.

        Recording nothing is honest; clamping to 0.0 would invent a reading.
        """
        facts = _audio_from(tmp_path, _measure_log(integrated="1.5"))
        assert facts.has_audio is True
        assert facts.loudness_lufs is None

    def test_accepts_the_loudest_storable_measure(self, tmp_path: Path) -> None:
        facts = _audio_from(tmp_path, _measure_log(integrated="0.0"))
        assert facts.loudness_lufs == pytest.approx(0.0)

    def test_accepts_a_measure_just_above_the_floor(self, tmp_path: Path) -> None:
        facts = _audio_from(tmp_path, _measure_log(integrated="-69.9"))
        assert facts.loudness_lufs == pytest.approx(-69.9)


class TestReadAudioFactsSoundTrackWindow:
    """Silence is measured on the sound track, so it is compared against the sound track."""

    def test_silence_covering_a_short_sound_track_leaves_no_audible_sound(
        self, tmp_path: Path
    ) -> None:
        """Ten seconds of picture, two seconds of silent sound: no sound at all.

        Charging that silence against the container's ten seconds reported a
        file with nothing audible in it as having sound.
        """
        facts = _audio_from(
            tmp_path,
            _measure_log(silences=[(0.0, 2.020136)], integrated="-70.0"),
            facts=_stream_facts(duration_ms=10000, audio_duration_ms=2000),
        )
        assert facts.has_audio is False
        assert facts.loudness_lufs is None

    def test_a_sound_track_stating_more_than_the_container_does_not_widen_the_window(
        self, tmp_path: Path
    ) -> None:
        """Stream durations come out of the file, so the wider of the two is not trusted.

        The shorter bound is the honest one, and taking it means this change can
        only ever move a verdict toward "no audible sound" — never the way the
        bug went.
        """
        facts = _audio_from(
            tmp_path,
            _measure_log(silences=[(0.0, 3.018594)], integrated="-70.0"),
            facts=_stream_facts(duration_ms=3000, audio_duration_ms=9_000_000),
        )
        assert facts.has_audio is False

    def test_silence_exactly_as_long_as_the_sound_track_leaves_no_audible_sound(
        self, tmp_path: Path
    ) -> None:
        """The endpoint the `>=` exists for. `>` passes every other case unchanged.

        Measured silence usually overshoots the stated duration because of
        encoder padding, so the two are rarely equal and no other case pins
        which comparison is in use.
        """
        facts = _audio_from(
            tmp_path,
            _measure_log(silences=[(0.0, 2.0)], integrated="-70.0"),
            facts=_stream_facts(duration_ms=2000),
        )
        assert facts.has_audio is False

    def test_partial_silence_within_the_sound_track_still_counts_as_sound(
        self, tmp_path: Path
    ) -> None:
        """The narrower window must not turn every gap into a silent file."""
        facts = _audio_from(
            tmp_path,
            _measure_log(silences=[(0.0, 1.0)], integrated="-22.2"),
            facts=_stream_facts(duration_ms=10000, audio_duration_ms=2000),
        )
        assert facts.has_audio is True


class TestReadAudioFactsGuardsTheFactsItIsGiven:
    """`read_audio_facts` is exported, so the facts can come from somewhere else.

    Its sibling checks the tools and the source it is handed and then trusted
    `streams` completely — and the value it took from there decides the whole
    verdict.
    """

    def test_rejects_facts_stating_no_duration_at_all(self, tmp_path: Path) -> None:
        """An absent duration became a window of zero, which every file covers."""
        excinfo = _audio_reject(
            tmp_path,
            _measure_log(),
            facts=_stream_facts(duration_ms=None),
        )
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_rejects_facts_whose_only_stated_duration_rounds_to_nothing(
        self, tmp_path: Path
    ) -> None:
        excinfo = _audio_reject(
            tmp_path,
            _measure_log(),
            facts=_stream_facts(duration_ms=None, audio_duration_ms=0),
        )
        assert _rejection(excinfo) is MaterialProbeRejection.UNUSABLE_DURATION

    def test_refuses_before_paying_for_a_decode(self, tmp_path: Path) -> None:
        """The waste is the decode, so the facts are checked before it, not after."""
        with pytest.raises(MaterialProbeRejected):
            read_audio_facts(
                _audio_tools(tmp_path, _measure_log(), argv_log=True),
                _source(tmp_path),
                _stream_facts(duration_ms=None),
            )
        assert (tmp_path / ".probe-argv").read_text(encoding="utf-8") == ""


class TestReadAudioFactsSilentAudioMaterial:
    def test_rejects_an_audio_file_with_no_audible_sound(self, tmp_path: Path) -> None:
        """`Material` forbids `kind=AUDIO` with `has_audio=False`, so it cannot be built.

        Handing back a fact that cannot be stored would only move the failure to
        a place that raises `InvalidMaterialModel` instead of a probe rejection.
        """
        excinfo = _audio_reject(
            tmp_path,
            _measure_log(silences=[(0.0, 2.02)], integrated="-70.0"),
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
    def test_rejects_a_measure_that_reports_no_loudness_line(self, tmp_path: Path) -> None:
        excinfo = _audio_reject(tmp_path, _measure_log(integrated=None))
        assert _rejection(excinfo) is MaterialProbeRejection.PROBE_FAILED

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
    def test_silences_the_per_frame_loudness_log(self, tmp_path: Path) -> None:
        """Without `framelog=quiet` ebur128 prints a line every 100 ms.

        Measured on a 2-second file: 65 stderr lines against 45 with it. The gap
        grows with duration, so a 4-hour import would emit roughly 144,000
        lines — the "oversized file" row of the failure matrix.
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
        """The measuring step must read ffmpeg's stderr, which names the file.

        It is written to a file and parsed into numbers, so no frame on the
        raising path holds the text.
        """
        secret = "operator-private-wedding-2021"
        log = f"/private/var/folders/ab/{secret}.mp4: something went wrong\n"
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

    def test_accepts_a_report_exactly_at_the_size_limit(self, tmp_path: Path) -> None:
        body = _measure_log()
        padding = MAX_MEASURE_OUTPUT_BYTES - len(body.encode("utf-8"))
        assert padding >= 0
        facts = _audio_from(tmp_path, ("#" * padding) + body)
        assert facts.has_audio is True

    def test_charges_nothing_for_a_span_the_filter_left_open(self, tmp_path: Path) -> None:
        """An unterminated `silence_start` states no duration, so it counts for nothing.

        Measured against the packaged build, silencedetect never leaves one
        open: a wholly silent file, a file fading to silence, one truncated
        mid-silence and a sound track ending before its picture all flush a
        closed `silence_end` at EOF. The earlier code charged such a span to the
        rest of the file, which no report could reach — and once the patterns
        are anchored to the filter's own line, nothing can plant one either.
        """
        facts = _audio_from(
            tmp_path,
            _measure_log(integrated="-70.0", trailing_silence_start=0.0),
            facts=_stream_facts(duration_ms=2000),
        )
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
        assert streams.audio_duration_ms == 2000
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
