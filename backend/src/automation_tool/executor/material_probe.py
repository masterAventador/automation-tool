"""Local probing of one source file: what it is, whether it has usable audio, what it hashes to.

The facts produced here fill a `control_plane.domain.material.Material`, but this
module deliberately does not import it: the executor never depends on the
product layer (`CLAUDE.md` §4.3), and `Material` carries no path, which is what
keeps the operator's private paths off the Control Plane. The path-to-id mapping
stays on this side of the boundary.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Final, Never

from automation_tool.protocol.safe_text import contains_control_or_bidi

MAX_PATH_CHARACTERS: Final = 4096
MAX_CODEC_NAME_CHARACTERS: Final = 64
MAX_PROBE_OUTPUT_BYTES: Final = 1024 * 1024
PROBE_TIMEOUT_SECONDS: float = 30.0
# Measuring decodes the whole sound track, so it is allowed far longer than
# reading the header.
MEASURE_TIMEOUT_SECONDS: float = 15 * 60.0
MAX_MEASURE_OUTPUT_BYTES: Final = 1024 * 1024
# How often the report is measured while ffmpeg is still writing it. Reading
# the size after the process exits says what it wrote; it does not bound what it
# writes, and the timeout above is the only other thing that would have stopped
# it — a quarter of an hour. Measured on a 120-second 720p file with a scrambled
# payload: 2.12 MB of decoder diagnostics in 0.55 s, still climbing.
#
# What this buys is a bound of one interval's writing rather than the timeout's.
# It is deliberately not an exact bound: the measured burst rate is around
# 12 MB/s, so an interval's overshoot can reach roughly a megabyte. The exact
# limit still governs what is *read* — an oversized report is rejected unread,
# which is the part that protects memory. This protects the disk.
MEASURE_POLL_SECONDS: Final = 0.1

# Anything quieter than this for at least this long counts as silence. Measured
# against the packaged build: a tone attenuated by 80 dB is reported silent
# throughout, which is the intent — inaudible material must not keep an
# `ambient` track alive downstream.
SILENCE_NOISE_FLOOR_DB: Final = -50
SILENCE_MINIMUM_SECONDS: Final = 0.3
# `Material` stores loudness only inside this range and only as a float.
LOUDNESS_FLOOR_LUFS: Final = -70.0
LOUDNESS_CEILING_LUFS: Final = 0.0

# ffmpeg's report is not only the filter's output. The `Input #0, ... from
# '<path>':` header and the whole metadata dump go into the same stream, and
# both are chosen by whoever produced the file. Unanchored patterns charged a
# `silence_duration:` written into a file name or a comment tag as silence the
# filter never reported, which is enough to reject an audible file. Every
# pattern below is therefore tied to the line prefix only the filter itself can
# emit: measured, a tag value holding a newline is continued as
# `<spaces>: [Parsed_silencedetect_0 @ 0x1] ...`, and that leading `: ` is what
# `^\[Parsed_` refuses. A path holding a newline never gets this far —
# `contains_control_or_bidi` rejects it first.
_SILENCE_LINE_PREFIX = r"^\[Parsed_silencedetect_\d+ @ [^\]]*\] "
_SILENCE_DURATION_PATTERN = re.compile(
    _SILENCE_LINE_PREFIX + r".*silence_duration: ([0-9]+(?:\.[0-9]+)?)", re.MULTILINE
)
# Already anchored. A tag named `I` cannot reach this shape: the metadata dump
# pads the key out to a fixed width, so the colon never lands against the `I`.
_INTEGRATED_LOUDNESS_PATTERN = re.compile(
    r"^\s*I:\s*(-?(?:[0-9]+(?:\.[0-9]+)?|inf|nan))\s+LUFS", re.MULTILINE
)

# Mirrored from `control_plane.domain.material` rather than imported: the
# executor does not depend on the product layer (`CLAUDE.md` §4.3). Probing that
# accepted a wider range than `Material` would hand the caller facts that cannot
# be stored, so a cross-layer test pins these to the domain's own limits.
MAX_MATERIAL_DURATION_MS: Final = 4 * 60 * 60 * 1000
MAX_MATERIAL_DIMENSION: Final = 8192

_PROBE_ENTRIES: Final = (
    "format=duration,format_name:stream=codec_type,codec_name,width,height,duration"
)

# Container names ffprobe reports for a single still picture, measured with the
# packaged build: PNG and BMP demux as `*_pipe`, a JPEG as `image2`.
_PICTURE_CONTAINER_SUFFIX: Final = "_pipe"
_PICTURE_CONTAINER_NAMES: Final = frozenset({"image2"})

_MEASURE_FILTERS: Final = (
    f"silencedetect=noise={SILENCE_NOISE_FLOOR_DB}dB:d={SILENCE_MINIMUM_SECONDS},"
    "ebur128=peak=none:framelog=quiet"
)


class MaterialProbeRejection(StrEnum):
    """Why one file cannot become a material.

    A single opaque failure would leave the material library able to say only
    "probe failed", so the one action left to the user is to retry every file in
    turn. Each member names a different next step.
    """

    UNREADABLE = "unreadable"
    UNSAFE_PATH = "unsafe_path"
    UNDECODABLE = "undecodable"
    NO_USABLE_STREAM = "no_usable_stream"
    UNUSABLE_DURATION = "unusable_duration"
    TOO_LONG = "too_long"
    UNUSABLE_FRAME_SIZE = "unusable_frame_size"
    FRAME_TOO_LARGE = "frame_too_large"
    SILENT_AUDIO = "silent_audio"
    PROBE_CRASHED = "probe_crashed"
    PROBE_FAILED = "probe_failed"


class ProbedMaterialKind(StrEnum):
    """Mirrors `control_plane.domain.material.MaterialKind` by value.

    Declared here rather than imported for the same boundary reason as the
    limits above; a cross-layer test asserts the two stay identical.
    """

    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class MaterialProbeRejected(RuntimeError):
    """Carries a closed reason code; the message stays fixed and path-free.

    `ffprobe` and `ffmpeg` both name the offending file in their diagnostics, so
    neither their output nor the path may reach this message (`CLAUDE.md` §7).
    The reason is an enum rather than free text precisely so it can be surfaced
    without becoming a leak channel.
    """

    def __init__(self, rejection: MaterialProbeRejection) -> None:
        super().__init__("material probe rejected")
        self.rejection = rejection


def _reject(rejection: MaterialProbeRejection) -> Never:
    """Raise the rejection with nothing chained behind it.

    Several call sites sit inside `except (OSError, subprocess.SubprocessError)`,
    and the exception being handled there names the file: `TimeoutExpired.cmd`
    is the whole argv, `OSError.filename` is the path itself. Python links that
    exception onto `__context__` automatically, and every default renderer —
    `logging.exception`, `traceback.print_exc`, an uncaught exception — prints
    it. Keeping the report text out of every frame's locals therefore closes
    only half the door; `from None` closes the other half.
    """
    raise MaterialProbeRejected(rejection) from None


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _require_path_shape(path: object) -> Path:
    """Reject a path on its text alone, before anything touches the filesystem."""
    if not isinstance(path, Path):
        _reject(MaterialProbeRejection.UNSAFE_PATH)
    encoded = os.fspath(path)
    if (
        not path.is_absolute()
        or len(encoded) > MAX_PATH_CHARACTERS
        or contains_control_or_bidi(encoded)
    ):
        _reject(MaterialProbeRejection.UNSAFE_PATH)
    return path


def _require_tool(path: object) -> None:
    """Re-check a path the Rust caller already authorized.

    This mirrors `browser_runtime._require_path`, which guards the packaged
    Chromium the same way. It is a re-check, not a search: nothing here looks
    the tool up, so there is no `PATH` lookup to fall back to and no environment
    variable that could redirect a test build somewhere a shipped build would
    never look.
    """
    tool = _require_path_shape(path)
    # Both calls below touch the filesystem, so both can raise. `is_symlink()`
    # swallows only ENOENT/ENOTDIR/EBADF/ELOOP (`pathlib._IGNORED_ERRNOS`) —
    # ENAMETOOLONG propagates, and a component over NAME_MAX is reachable well
    # inside the length limit above. Escaping here would defeat the whole
    # design twice over: callers catching `MaterialProbeRejected` would miss a
    # bare `OSError`, and its message carries the full private path.
    # `_reject` raises a `RuntimeError` subclass, so the symlink rejection
    # passes through this `except` untouched.
    try:
        if _has_symlink_component(tool):
            _reject(MaterialProbeRejection.UNSAFE_PATH)
        metadata = tool.stat(follow_symlinks=False)
    except OSError:
        _reject(MaterialProbeRejection.UNREADABLE)
    if not stat.S_ISREG(metadata.st_mode) or not os.access(tool, os.X_OK):
        _reject(MaterialProbeRejection.UNREADABLE)


def _require_source_file(path: object) -> Path:
    """Check a file the user chose to import.

    Unlike the tools, a symlink is fine here: user media legitimately lives
    behind one, and there is no integrity claim to protect — the supply-chain
    reason for rejecting links applies to the packaged binaries, not to the
    operator's own library. The stat therefore follows links, and requiring a
    regular file at the end of the chain is what keeps a FIFO or character
    device out, either of which would leave ffprobe blocked instead of
    returning a fact.
    """
    source = _require_path_shape(path)
    try:
        metadata = source.stat()
    except OSError:
        _reject(MaterialProbeRejection.UNREADABLE)
    if not stat.S_ISREG(metadata.st_mode) or not os.access(source, os.R_OK):
        _reject(MaterialProbeRejection.UNREADABLE)
    return source


@dataclass(frozen=True, slots=True, repr=False)
class PackagedMediaTools:
    """Paths to the packaged FFmpeg pair, already verified by the Rust caller.

    Rust resolves these from the App's resource directory and checks the
    toolchain manifest, sizes and SHA-256 digests before handing them over, the
    same way it hands over the packaged Chromium. Production and tests run this
    one constructor; only the value differs.
    """

    ffprobe_path: Path
    ffmpeg_path: Path

    def __post_init__(self) -> None:
        self.revalidate()

    def revalidate(self) -> None:
        """Re-check both tools immediately before use.

        Validity at construction says nothing about validity now — the packaged
        files can be removed or replaced between the two moments.
        """
        _require_tool(self.ffprobe_path)
        _require_tool(self.ffmpeg_path)

    def __repr__(self) -> str:
        return "PackagedMediaTools(<redacted>)"


@dataclass(frozen=True, slots=True)
class MediaStreamFacts:
    """What ffprobe knows about one file. Deliberately carries no path.

    `duration_ms` is the container's and `audio_duration_ms` the sound track's;
    they are different facts and a clip whose sound stops before its picture
    does has both. `audio_duration_ms` is optional because Matroska and FLV
    state no per-stream duration at all — measured, not assumed.
    """

    kind: ProbedMaterialKind
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    audio_duration_ms: int | None


def _run_probe(ffprobe: Path, source: Path) -> dict[str, object]:
    """Ask ffprobe for exactly the entries used, and trust nothing it says.

    Only `-show_entries` is passed: adding `-show_format`/`-show_streams` makes
    the selection additive and drags in `tags`, which is attacker-controlled
    metadata carried inside the file.
    """
    # Rejected after the handler has been left rather than inside it. `_reject`
    # already suppresses the chain, but suppression only stops the renderers:
    # the handled exception stays reachable through `__context__`, and
    # `TimeoutExpired.cmd` is the argv with the source path in it. Leaving the
    # handler first drops the reference outright.
    completed: subprocess.CompletedProcess[bytes] | None
    try:
        completed = subprocess.run(
            [
                os.fspath(ffprobe),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_entries",
                _PROBE_ENTRIES,
                # Stops a name beginning with "-" from being read as an option.
                "--",
                os.fspath(source),
            ],
            # The executor's own stdin carries Tauri's bootstrap handshake and
            # command stream; a child inheriting it could consume bytes out of
            # that protocol channel.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            # Discarded at the pipe rather than captured and ignored. ffprobe
            # names the offending file in every diagnostic, and a captured
            # stream stays reachable through `CompletedProcess` in the frame
            # that raised — where a crash reporter walking `f_locals` would
            # carry the operator's private path off the machine. Discarding it
            # also removes the one stream with no size limit.
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is None:
        _reject(MaterialProbeRejection.PROBE_FAILED)
    if completed.returncode < 0:
        # POSIX reports a signalled child as a negative code. Telling the user
        # their file is unreadable would send them to replace a file that is
        # fine; the packaged tool is what died. Free to distinguish — no
        # diagnostic text is involved, so nothing here drifts with locale.
        _reject(MaterialProbeRejection.PROBE_CRASHED)
    if completed.returncode != 0:
        # Measured: unsupported data, a truncated container and an empty file
        # all yield exactly `exit=1` with `stdout={}`. Reading stdout without
        # checking this first would parse that `{}` into an empty mapping and
        # invent defaults from it. The three cannot be told apart without
        # matching ffprobe's English diagnostics, and that text names the file,
        # so it is never read back.
        _reject(MaterialProbeRejection.UNDECODABLE)
    if len(completed.stdout) > MAX_PROBE_OUTPUT_BYTES:
        _reject(MaterialProbeRejection.PROBE_FAILED)
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        _reject(MaterialProbeRejection.PROBE_FAILED)
    if not isinstance(payload, dict):
        _reject(MaterialProbeRejection.PROBE_FAILED)
    return payload


def _is_picture_container(format_name: object) -> bool:
    """Whether ffprobe named a container that holds one still picture.

    Measured with the packaged ffprobe: PNG and BMP demux as `png_pipe` and
    `bmp_pipe`, while a JPEG comes back as `image2`. Every timed container seen
    so far (`mov,mp4,...`, `matroska,webm`) is named without that suffix.
    """
    return isinstance(format_name, str) and (
        format_name.endswith(_PICTURE_CONTAINER_SUFFIX) or format_name in _PICTURE_CONTAINER_NAMES
    )


def _first_stream(streams: list[object], codec_type: str) -> dict[str, object] | None:
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == codec_type:
            return stream
    return None


def _duration_ms(text: object) -> int:
    if not isinstance(text, str):
        _reject(MaterialProbeRejection.UNUSABLE_DURATION)
    try:
        seconds = float(text)
    except ValueError:
        _reject(MaterialProbeRejection.UNUSABLE_DURATION)
    # Guards `round()` as much as the value: it raises on both infinity and NaN.
    if not math.isfinite(seconds):
        _reject(MaterialProbeRejection.UNUSABLE_DURATION)
    milliseconds = round(seconds * 1000)
    # `Material` needs at least 1 ms, which also covers every negative value.
    if milliseconds < 1:
        _reject(MaterialProbeRejection.UNUSABLE_DURATION)
    if milliseconds > MAX_MATERIAL_DURATION_MS:
        _reject(MaterialProbeRejection.TOO_LONG)
    return milliseconds


def _optional_duration_ms(text: object) -> int | None:
    """One stream's own duration, when ffprobe states a usable one.

    Absence is ordinary rather than malformed: measured against the packaged
    build, Matroska and FLV state a container duration and no stream duration at
    all, while MP4, MOV, WAV, FLAC and MPEG-TS state both. Anything unusable is
    treated as absence for the same reason — the container's duration stays as
    the bound, which is where this started, rather than rejecting a file over a
    field that is allowed to be missing.

    The `isfinite` guard is load-bearing here, unlike the one in
    `_storable_loudness`: `round()` raises on both infinity and NaN, and there
    is no later range test to reach.
    """
    if not isinstance(text, str):
        return None
    try:
        seconds = float(text)
    except ValueError:
        return None
    if not math.isfinite(seconds):
        return None
    milliseconds = round(seconds * 1000)
    return milliseconds if milliseconds >= 1 else None


def _frame_edge(stream: dict[str, object], key: str) -> int:
    value = stream.get(key)
    # `type(...) is not int` rather than `isinstance`: `bool` is an `int`
    # subclass, and `True` must not pass for a pixel count.
    if type(value) is not int or value < 1:
        _reject(MaterialProbeRejection.UNUSABLE_FRAME_SIZE)
    if value > MAX_MATERIAL_DIMENSION:
        _reject(MaterialProbeRejection.FRAME_TOO_LARGE)
    return value


def _codec_name(stream: dict[str, object]) -> str:
    value = stream.get("codec_name")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CODEC_NAME_CHARACTERS
        or contains_control_or_bidi(value)
    ):
        _reject(MaterialProbeRejection.PROBE_FAILED)
    return value


@dataclass(frozen=True, slots=True)
class _Measurement:
    """The only three numbers taken out of ffmpeg's report.

    Parsing down to this before returning is what keeps the report text off
    every raising path: the frames that hold it have all returned by the time
    any rejection is raised, so no traceback can carry the file name ffmpeg
    printed alongside its findings.
    """

    stated_loudness: bool
    loudness_lufs: float | None
    silent_seconds: float


@dataclass(frozen=True, slots=True)
class AudioFacts:
    """Whether a file actually sounds like anything, and how loud."""

    has_audio: bool
    loudness_lufs: float | None


def _run_measure(argv: list[str], sink: IO[bytes], report: Path) -> int:
    """Run one measuring pass, stopping it if its report outgrows the limit.

    Reading the size once the process has exited says what it wrote; it does
    not bound what it writes. Between the two moments sits
    `MEASURE_TIMEOUT_SECONDS`, and a file whose payload decodes badly fills that
    time with diagnostics. So the report is measured while it grows and the
    child is killed the moment it is too big — killed rather than left, because
    leaving it would mean waiting out the very process being abandoned.

    Nothing captured, so neither a `CompletedProcess` nor a `TimeoutExpired`
    ever holds ffmpeg's text; the argv one would carry is dropped by leaving the
    handler before rejecting, as in `_run_probe`.
    """
    with subprocess.Popen(
        argv,
        # The executor's own stdin carries Tauri's bootstrap handshake and
        # command stream; a child inheriting it could consume bytes out of that
        # protocol channel.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=sink,
    ) as process:
        deadline = time.monotonic() + MEASURE_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    return process.wait(timeout=min(MEASURE_POLL_SECONDS, remaining))
                except subprocess.TimeoutExpired:
                    outgrown = report.stat().st_size > MAX_MEASURE_OUTPUT_BYTES
                if not outgrown:
                    continue
            process.kill()
            _reject(MaterialProbeRejection.PROBE_FAILED)


def _measure(ffmpeg: Path, source: Path) -> _Measurement:
    """Run the silence and loudness pass, reduced to the numbers taken from it.

    The report arrives on stderr mixed in with diagnostics that name the file,
    so it is written to a file rather than captured: nothing that could carry
    the path ends up on `CompletedProcess` or on a `TimeoutExpired`, and the
    size can be checked before any of it is read.

    Every rejection here happens before a single byte is read, and the text is
    handed straight to the parser without being bound to a local, so nothing on
    a raising path can hold it. Every call that touches the filesystem is inside
    the `try`, the workspace's own cleanup included: an `OSError` escaping as
    itself would both miss every caller catching `MaterialProbeRejected` and
    carry the path in its message.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="automation-tool-measure-") as workspace:
            report = Path(workspace) / "report.log"
            with report.open("wb") as sink:
                returncode = _run_measure(
                    [
                        os.fspath(ffmpeg),
                        "-nostdin",
                        "-v",
                        "info",
                        # ffmpeg reads its input before its output options, and
                        # it has no `--` separator — it opens a literal "--" as
                        # a file. What keeps a name starting with "-" from being
                        # read as an option is `_require_source_file`, which
                        # only ever yields an absolute path.
                        "-i",
                        os.fspath(source),
                        # Nothing here looks at the picture, yet `-f null`
                        # selects the video stream too and decodes every frame
                        # into the sink. Measured on a 120-second 1280x720
                        # clip: 4.10 s of CPU without this against 0.17 s with
                        # it. An output option, so it goes after the input.
                        "-vn",
                        # `framelog=quiet` keeps ebur128 from printing a line
                        # every 100 ms: measured 65 stderr lines against 45 on a
                        # 2-second file, a gap that grows with duration until a
                        # 4-hour import emits roughly 144,000 lines.
                        "-af",
                        _MEASURE_FILTERS,
                        "-f",
                        "null",
                        "-",
                    ],
                    sink,
                    report,
                )
            if returncode < 0:
                _reject(MaterialProbeRejection.PROBE_CRASHED)
            if returncode != 0:
                _reject(MaterialProbeRejection.PROBE_FAILED)
            if report.stat().st_size > MAX_MEASURE_OUTPUT_BYTES:
                _reject(MaterialProbeRejection.PROBE_FAILED)
            return _parse_measurement(report.read_text(encoding="utf-8", errors="replace"))
    except (OSError, subprocess.SubprocessError):
        # `_reject` raises a `RuntimeError` subclass, so every rejection above
        # passes through here untouched and keeps its own reason.
        _reject(MaterialProbeRejection.PROBE_FAILED)


def _parse_measurement(report: str) -> _Measurement:
    """Reduce the report to numbers. Never raises, so it is never on a raising path."""
    match = _INTEGRATED_LOUDNESS_PATTERN.search(report)
    return _Measurement(
        stated_loudness=match is not None,
        loudness_lufs=None if match is None else _storable_loudness(match.group(1)),
        silent_seconds=_silent_seconds(report),
    )


def _silent_seconds(report: str) -> float:
    """Total silence the report accounts for.

    Every span silencedetect reports is closed, so summing the reported
    durations is the whole answer. An earlier version also charged an
    unterminated `silence_start` to the rest of the file — measured against the
    packaged build, that state does not occur: a wholly silent file, a file that
    fades to silence, one truncated mid-silence and a sound track ending before
    its picture all flush a closed `silence_end` at EOF. The assumption is
    pinned by a test that measures a real silent file rather than left as a
    branch nothing can enter.
    """
    return sum(float(value) for value in _SILENCE_DURATION_PATTERN.findall(report))


def _storable_loudness(text: str) -> float | None:
    """The reading, or `None` when it is not one `Material` can hold."""
    value = float(text)
    # The floor is what ebur128 prints when it has nothing to measure — a clip
    # shorter than its integration window reports it while being plainly
    # audible, so it states absence, not quietness. Above full scale cannot be
    # stored either. Reporting nothing beats inventing a reading.
    #
    # This range test also covers `inf`, `-inf` and `nan`, every one of which
    # compares false against both bounds. An `isfinite` guard in front of it
    # would be a term that can never decide anything — and `or` sub-conditions
    # are not measured individually, so it would have ridden along at full
    # coverage looking like a check.
    if not LOUDNESS_FLOOR_LUFS < value <= LOUDNESS_CEILING_LUFS:
        return None
    return value


def _sound_track_seconds(streams: MediaStreamFacts) -> float:
    """How much sound the reported silence has to cover for there to be none.

    `silencedetect` measures on the sound track's own timeline, so this is the
    sound track's extent — not the container's, which is usually the picture's.
    Comparing against the container reported a wholly silent clip as having
    sound whenever its sound stopped early, which is an ordinary shape rather
    than a corner case.

    Where ffprobe states both, the shorter is the sound track's: measured,
    MPEG-TS states 1.904 s of sound inside a 2.023 s container. Where it states
    only the container's — Matroska and FLV, measured — that is the only bound
    available and it is used as before.

    `read_stream_facts` cannot produce facts this rejects, but it is not the
    only way in: `read_audio_facts` is exported, so a caller can arrive with a
    `MediaStreamFacts` it built itself. Without this, an absent duration became
    a window of zero, every file was reported as having no audible sound, and a
    pure-audio file was rejected outright — after paying for a full decode.
    """
    stated = [
        value for value in (streams.duration_ms, streams.audio_duration_ms) if value is not None
    ]
    if not stated:
        _reject(MaterialProbeRejection.UNUSABLE_DURATION)
    shortest = min(stated)
    if shortest < 1:
        _reject(MaterialProbeRejection.UNUSABLE_DURATION)
    return shortest / 1000


def read_audio_facts(
    tools: PackagedMediaTools, source: Path, streams: MediaStreamFacts
) -> AudioFacts:
    """Decide whether a file carries audible sound, and how loud it is.

    This is the first stage of the three-stage speech funnel: it separates
    "there is sound" from "there is a track". Telling speech from ambience, and
    transcribing it, belong to later work.
    """
    if streams.audio_codec is None:
        # The stream list already answered this; decoding the file to confirm
        # it would cost a full pass for nothing.
        return AudioFacts(has_audio=False, loudness_lufs=None)
    tools.revalidate()
    path = _require_source_file(source)
    # Before the decode, not after it: facts that cannot answer the question
    # cannot be made to answer it by measuring harder.
    sound_track_seconds = _sound_track_seconds(streams)
    measurement = _measure(tools.ffmpeg_path, path)
    if not measurement.stated_loudness:
        _reject(MaterialProbeRejection.PROBE_FAILED)
    # Measured: a digitally silent 2-second AAC file reports 2.020136 seconds of
    # silence — longer than the stream, because of encoder padding. Comparing
    # with `>=` is what tolerates that.
    if measurement.silent_seconds >= sound_track_seconds:
        if streams.kind is ProbedMaterialKind.AUDIO:
            # `Material` forbids `kind=AUDIO` with `has_audio=False`, so this
            # can never be stored. Rejecting here keeps the failure inside the
            # closed set of reasons instead of surfacing as
            # `InvalidMaterialModel` from a caller that cannot catch it.
            _reject(MaterialProbeRejection.SILENT_AUDIO)
        return AudioFacts(has_audio=False, loudness_lufs=None)
    return AudioFacts(has_audio=True, loudness_lufs=measurement.loudness_lufs)


def read_stream_facts(tools: PackagedMediaTools, source: Path) -> MediaStreamFacts:
    """Read duration, frame size and codecs for one file with the packaged ffprobe."""
    tools.revalidate()
    path = _require_source_file(source)
    payload = _run_probe(tools.ffprobe_path, path)
    streams = payload.get("streams")
    container = payload.get("format")
    if not isinstance(streams, list) or not isinstance(container, dict):
        _reject(MaterialProbeRejection.PROBE_FAILED)

    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    duration_text = container.get("duration")
    if video is not None:
        # The container says whether this is a picture; the duration does not.
        # Both directions were measured wrong before this read `format_name`:
        # ffmpeg writing Matroska to a pipe cannot seek back to fill the
        # duration in, so a real 2-second H.264 clip arrives with no duration
        # and was filed as a still, while a JPEG arrives as `image2` *carrying*
        # a 0.040000 duration and was filed as a 40 ms video. Requiring no audio
        # as well keeps a picture container with a sound track — which
        # `Material` forbids outright — from being filed as one.
        kind = (
            ProbedMaterialKind.IMAGE
            if audio is None and _is_picture_container(container.get("format_name"))
            else ProbedMaterialKind.VIDEO
        )
    elif audio is not None:
        kind = ProbedMaterialKind.AUDIO
    else:
        _reject(MaterialProbeRejection.NO_USABLE_STREAM)

    return MediaStreamFacts(
        kind=kind,
        duration_ms=None if kind is ProbedMaterialKind.IMAGE else _duration_ms(duration_text),
        # `Material` forbids a frame size on audio, so none is invented here.
        width=None if video is None else _frame_edge(video, "width"),
        height=None if video is None else _frame_edge(video, "height"),
        video_codec=None if video is None else _codec_name(video),
        audio_codec=None if audio is None else _codec_name(audio),
        audio_duration_ms=None if audio is None else _optional_duration_ms(audio.get("duration")),
    )


__all__ = [
    "MAX_CODEC_NAME_CHARACTERS",
    "MAX_MATERIAL_DIMENSION",
    "MAX_MATERIAL_DURATION_MS",
    "MAX_MEASURE_OUTPUT_BYTES",
    "MAX_PATH_CHARACTERS",
    "MAX_PROBE_OUTPUT_BYTES",
    "AudioFacts",
    "MaterialProbeRejected",
    "MaterialProbeRejection",
    "MediaStreamFacts",
    "PackagedMediaTools",
    "ProbedMaterialKind",
    "read_audio_facts",
    "read_stream_facts",
]
