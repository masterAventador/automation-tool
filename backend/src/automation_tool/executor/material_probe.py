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
# The report is `ametadata`'s output, whose size follows the sound track's
# duration rather than its content: ebur128 states one running loudness per
# 100 ms window. Measured on a real pass at the duration limit — four hours of
# sound — that is 9,085,156 bytes, or 8.66 MiB. Extrapolating from a minute
# would have said 7.91 MiB: the block a window costs grows as the frame counter
# and the timestamps take more digits, worth 9.5% over that span. The limit
# below leaves room for the measured figure, so a file at the duration limit
# passes and anything writing faster than the channel's fixed cadence is
# stopped.
MAX_MEASURE_OUTPUT_BYTES: Final = 16 * 1024 * 1024
# How often the report is measured while ffmpeg is still writing it. Reading
# the size after the process exits says what it wrote; it does not bound what it
# writes, and the timeout above is the only other thing that would have stopped
# it — a quarter of an hour.
#
# What this buys is a bound of one interval's writing rather than the timeout's.
# It is deliberately not an exact bound: the overshoot is one interval times
# whatever the writer's rate happens to be, and that rate is not something this
# code controls. The exact limit still governs what is *read* — an oversized
# report is rejected unread, which is the part that protects memory. This
# protects the disk.
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

# Nothing below reads ffmpeg's diagnostics. They carry the filters' findings but
# they also carry the `Input #0, ... from '<path>':` header and the whole
# metadata dump, and both are written by whoever produced the file. No pattern
# over that stream can separate the two: measured, a tag *value* holding a
# newline is continued as `<spaces>: <text>`, but a tag *name* is printed
# verbatim, so whatever follows a newline in a key starts in column 0 and can
# reproduce a filter's line byte for byte — prefix, address and all. Anchoring
# harder only moved the forgery.
#
# So the filters' findings are taken off a stream the file cannot write into.
# `ametadata` prints frame metadata, and frame metadata is produced by the
# filters themselves; the first `mode=delete` drops anything the demuxer or
# decoder attached before they add theirs. Measured against the packaged build,
# every line of it matches one of the two shapes below and a forged tag reaches
# neither.
_FRAME_PATTERN = re.compile(r"^frame:", re.MULTILINE)
_SILENCE_START_PATTERN = re.compile(r"^lavfi\.silence_start=(-?[0-9]+(?:\.[0-9]+)?)$", re.MULTILINE)
_SILENCE_END_PATTERN = re.compile(r"^lavfi\.silence_end=-?[0-9]+(?:\.[0-9]+)?$", re.MULTILINE)
_INTEGRATED_LOUDNESS_PATTERN = re.compile(
    r"^lavfi\.r128\.I=(-?(?:[0-9]+(?:\.[0-9]+)?|inf|nan))$", re.MULTILINE
)

# Mirrored from `control_plane.domain.material` rather than imported: the
# executor does not depend on the product layer (`CLAUDE.md` §4.3). Probing that
# accepted a wider range than `Material` would hand the caller facts that cannot
# be stored, so a cross-layer test pins these to the domain's own limits.
MAX_MATERIAL_DURATION_MS: Final = 4 * 60 * 60 * 1000
MAX_MATERIAL_DIMENSION: Final = 8192

_PROBE_ENTRIES: Final = "format=duration,format_name:stream=codec_type,codec_name,width,height"

# Container names ffprobe reports for a single still picture, measured with the
# packaged build: PNG and BMP demux as `*_pipe`, a JPEG as `image2`.
_PICTURE_CONTAINER_SUFFIX: Final = "_pipe"
_PICTURE_CONTAINER_NAMES: Final = frozenset({"image2"})

_MEASURE_FILTERS: Final = ",".join(
    (
        # Whatever the demuxer or the decoder attached to a frame goes first, so
        # the only keys the sink can see are the ones the two filters below add.
        # Nothing measured has ever put a file's own text on a frame, so this is
        # depth rather than a demonstrated necessity — it makes "the file cannot
        # write here" structural instead of resting on that search having been
        # exhaustive.
        "ametadata=mode=delete",
        # `metadata=1` is what puts the running loudness on the frames; the
        # summary it prints at the end goes to the diagnostics, which are
        # discarded. `framelog=quiet` is belt-and-braces now rather than the
        # saving it used to be: it stops a line every 100 ms, but those print at
        # `info` and this pass asks for `error`, so measured against the packaged
        # build the diagnostics of a 60-second file are 0 bytes either way. It
        # earns its place again the moment anyone raises the level to look.
        "ebur128=peak=none:framelog=quiet:metadata=1",
        # Downstream of ebur128, and that order is load-bearing. ebur128 asks
        # libavfilter for frames of exactly one window — 4410 samples at
        # 44.1 kHz — so the decoder's frames are merged to that length on the way
        # in, and merging keeps only the first frame's metadata
        # (`av_frame_copy_props(buf, frame0)`). A whole frame is therefore
        # swallowed, metadata and all, whenever one group takes two of them: that
        # needs a decoded frame no longer than half a window, and nothing else.
        #
        # It is the block length that decides this, not the codec. Measured at
        # 44.1 kHz: AAC decodes to 1024 samples, PCM s16le to 4096, FLAC to 4608
        # by default — but a FLAC written with a 512, 1024 or 2048-sample block
        # decodes to that, and upstream of ebur128 those fail exactly like AAC
        # (8/8, 8/8 and 7/8 of eight silence positions rejected outright as
        # silent, against 0/8 at 4096). Nothing here produces the operator's
        # files, so "our encoder happens to use long blocks" protects nobody.
        #
        # Measured with silencedetect in front, over 24 ordinary files carrying
        # four seconds of audible tone: 7 were rejected as silent, the closest
        # pair being a 0.30 s lead that passed against a 0.31 s lead that did
        # not. Behind ebur128 the frames are already the fixed length and every
        # one of those files comes back right.
        f"silencedetect=noise={SILENCE_NOISE_FLOOR_DB}dB:d={SILENCE_MINIMUM_SECONDS}",
        # ebur128 states six numbers per window and one of them is used.
        # Dropping the other five is what keeps the channel's size proportional
        # to duration: measured 1763 bytes per second of sound against 576.
        "ametadata=mode=delete:key=lavfi.r128.M",
        "ametadata=mode=delete:key=lavfi.r128.S",
        "ametadata=mode=delete:key=lavfi.r128.LRA",
        "ametadata=mode=delete:key=lavfi.r128.LRA.low",
        "ametadata=mode=delete:key=lavfi.r128.LRA.high",
        # Written to stdout, which nothing else touches: `-f null` is declared
        # `AVFMT_NOFILE`, so ffmpeg never opens the output at all. Measured with
        # an unwritable output path — it still exits 0 — and with the filter
        # removed, where stdout comes back empty.
        "ametadata=mode=print:file=-",
    )
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
    only half the door; this closes what the renderers show.

    Only that much, though: `from None` sets `__suppress_context__`, so the
    handled exception is still hanging off `__context__` with the path in its
    `filename`. Anything walking the chain itself rather than rendering it still
    reaches it. The two subprocess calls drop the reference outright by leaving
    the handler before rejecting; the four rejections raised from inside one —
    both path guards, the duration parser and the JSON parser — have this and
    nothing more.
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

    `duration_ms` is the container's. The sound track's own duration used to be
    read alongside it, to bound the silence a file had to contain before it
    counted as having none; that bound is gone — `read_audio_facts` now decides
    from what the filters measured rather than from what the header states — and
    with it the only reader of the field.
    """

    kind: ProbedMaterialKind
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None


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
    """The only facts taken out of ffmpeg's report.

    Parsing down to this before returning is what keeps the report text off
    every raising path: the frames that hold it have all returned by the time
    any rejection is raised, so no traceback can carry anything the report held.

    `stated_anything` separates "the measuring pass produced nothing" from "it
    produced findings but no loudness". The first is ordinary — a sound track
    shorter than one of ebur128's 100 ms windows states no loudness at all,
    measured — while the second means the pass did not do what it was asked.
    """

    stated_anything: bool
    stated_loudness: bool
    loudness_lufs: float | None
    silence_covers_the_track: bool


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
        stdout=sink,
        # ffmpeg names the offending file in every diagnostic, and nothing here
        # reads them.
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
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
                _reject(MaterialProbeRejection.PROBE_FAILED)
        except BaseException:
            # Every way out of that loop except the `return` leaves the child
            # running, and `Popen.__exit__` waits for it *without* a timeout —
            # so an exception on the way out trades the timeout for the child's
            # natural life. `report.stat()` is the reachable one: a tmp sweeper,
            # an unmounted volume or an EIO puts an `OSError` there while ffmpeg
            # is still writing. Measured before this existed, with a one-second
            # timeout and a thirty-second child: 30.59 s.
            process.kill()
            raise


def _measure(ffmpeg: Path, source: Path) -> _Measurement:
    """Run the silence and loudness pass, reduced to the facts taken from it.

    The findings arrive on the filter graph's own metadata channel, written to a
    file rather than captured: nothing that could carry the path ends up on a
    `CompletedProcess` or a `TimeoutExpired`, and the size can be checked before
    any of it is read. The diagnostics — which name the file and carry whatever
    its tags say — go to `DEVNULL` and are never a file at all.

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
                        # Nothing reads the diagnostics, and this is the level
                        # the input's metadata dump prints at.
                        "-v",
                        "error",
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
    """Reduce the report to facts. Never raises, so it is never on a raising path."""
    readings = _INTEGRATED_LOUDNESS_PATTERN.findall(report)
    return _Measurement(
        stated_anything=_FRAME_PATTERN.search(report) is not None,
        stated_loudness=bool(readings),
        # ebur128 states the loudness integrated *so far* once per window, so
        # the last one is the whole file's. Measured against its own summary on
        # a 440 Hz tone: -21.776 against the -21.8 it prints at the end.
        loudness_lufs=_storable_loudness(readings[-1]) if readings else None,
        silence_covers_the_track=_silence_covers_the_track(report),
    )


def _silence_covers_the_track(report: str) -> bool:
    """Whether the reported silence leaves no audible sound anywhere.

    This is read off the shape of what silencedetect reported rather than
    counted against a duration. Every span it opens closes as soon as sound
    resumes, so a closed span is itself proof of sound; the only span that can
    stay open is the last, and it stays open to the end of the track. Silence
    therefore covers everything exactly when one span opened at the very start
    and nothing ever closed it.

    Counting instead needed the sound track's length, and the file states that.
    Measured, three bytes of a rewritten `mdhd` shrank it to a millisecond and a
    second of leading silence then covered nine seconds of audible tone.

    The end of a track is not a frame, so the `silence_end` silencedetect
    flushes at EOF has nowhere to be attached and never reaches this channel —
    measured, a wholly silent file states `silence_start` and nothing else.
    Reading that absence as "still silent at EOF" is the whole point.

    Measured too: ffmpeg starts the filter timeline at zero whatever the
    container's own start time is, for MP4, MOV, Matroska, FLAC, Ogg and
    MPEG-TS alike, so "the very start" is `<= 0`.

    Counting the spans would say the same thing and say it less directly: a
    second span can only open once the first has closed, so "nothing closed"
    already means there is exactly one. Requiring one as well left a term no
    reachable report could decide — measured, `!= 1` loosened to `< 1` survived
    every test in this file.
    """
    starts = _SILENCE_START_PATTERN.findall(report)
    if not starts or _SILENCE_END_PATTERN.search(report) is not None:
        return False
    return float(starts[0]) <= 0.0


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
    measurement = _measure(tools.ffmpeg_path, path)
    # A report that states findings but no loudness means the pass did not run
    # the way it was asked to — ebur128 states one per 100 ms window, so any
    # frame at all in the report implies it had already stated several. A report
    # that states nothing is the ordinary shape of a sound track shorter than
    # one window, measured, and rejecting it would turn a 50 ms clip into a
    # failure.
    if measurement.stated_anything and not measurement.stated_loudness:
        _reject(MaterialProbeRejection.PROBE_FAILED)
    if measurement.silence_covers_the_track:
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
