"""Local probing of one source file: what it is, whether it has usable audio, what it hashes to.

The facts produced here fill a `control_plane.domain.material.Material`, but this
module deliberately does not import it: the executor never depends on the
product layer (`CLAUDE.md` §4.3), and `Material` carries no path, which is what
keeps the operator's private paths off the Control Plane. The path-to-id mapping
stays on this side of the boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Final, Never, cast
from uuid import RFC_4122, UUID

from automation_tool.protocol.safe_text import contains_control_or_bidi

MAX_PATH_CHARACTERS: Final = 4096
MAX_CODEC_NAME_CHARACTERS: Final = 64
MAX_PROBE_OUTPUT_BYTES: Final = 1024 * 1024
# The largest file the digest will read. Unlike the duration and frame-size
# limits further down this mirrors nothing — `Material` has no size field — so
# it is not a shape constraint but a bound on work, and it is chosen against the
# two things that do constrain it.
#
# Against the duration limit: four hours is the longest material that can exist,
# and four hours at roughly 9.5 Mbps is this figure, which covers ordinary
# consumer 1080p at that length. Above it are professional intermediates —
# ProRes 1080p would be some 265 GB over the same four hours — and nothing here
# is meant to edit those.
#
# Against time, from two separate measurements rather than one extrapolated:
# hashing a real 512 MiB file ran at 2.08 GiB/s, which puts a file at this limit
# at 7.7 s; streaming and hashing a 16 GiB file end to end actually took 8.7 s,
# or 1.83 GiB/s, the difference being that the larger read no longer fits the
# cache. Either way the measuring pass above is already allowed fifteen minutes
# for one file, so this is a small part of what probing one material may cost.
MAX_SOURCE_FILE_BYTES: Final = 16 * 1024 * 1024 * 1024
# Matching `package_manifest._BUFFER_SIZE` and `macos_candidate._SCAN_CHUNK_SIZE`,
# the two other places in the executor that stream a file past a hash or a scan.
_DIGEST_CHUNK_BYTES: Final = 1024 * 1024
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

# The path registry's document, below the private Executor state directory the
# bootstrap already owns — the same place `ledger` puts its database.
MATERIAL_PATH_REGISTRY_FILE_NAME: Final = "material-paths.json"
# Stamped into the document so a later format can be recognised rather than
# guessed at. A document stating anything else is refused, not reinterpreted.
MATERIAL_PATH_REGISTRY_VERSION: Final = "executor.material-path-registry.v1"
# The whole document is read into memory to be parsed, so it needs a bound for
# the same reason the probe's output does. Sharing `MAX_MEASURE_OUTPUT_BYTES`'s
# value rather than inventing a figure: it is already this module's answer to
# "the largest local scratch file one material may cost us to read".
#
# There is deliberately no second limit on the number of entries. One would have
# to be kept consistent with this one, and it is this one that bounds the work;
# the count follows from it. An ordinary entry — an absolute path of a hundred
# characters and the four numbers beside it — is under 200 bytes, so the limit
# is worth some eighty thousand materials, and even a library of paths at the
# length limit gets several hundred.
MAX_MATERIAL_PATH_REGISTRY_BYTES: Final = MAX_MEASURE_OUTPUT_BYTES
_REGISTRY_ENTRY_KEYS: Final = frozenset({"path", "device", "inode", "modified_ns", "size_bytes"})

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

    Whether importing the same file again, untouched, could succeed is what
    separates the two members below, and that separation is the whole of how
    retryability is stated here. A second, parallel encoding of it — a flag on
    every member, with no caller yet to read one — would only be somewhere for
    the two to disagree.
    """

    UNREADABLE = "unreadable"
    # Split out of `UNREADABLE`, which had become the confluence of eight
    # separate findings. Six of them say the same thing — something else is
    # writing this file right now — and are gathered here: the file was already
    # a different one when it was opened, it grew, it was truncated, it was
    # rewritten in place, the path came to name another file, or the probe's
    # own end-to-end check saw it move between its first step and its last. The
    # ordinary way to reach any of them is importing a download or a recording
    # before it finished, and "this file cannot be read, go and find it again"
    # is the wrong instruction for that: the right one is to wait.
    #
    # The two that stay behind under `UNREADABLE` will not settle on their own:
    # anything the filesystem refused outright (no permission, nothing at that
    # path, a real IO error), and a path that names something other than a
    # regular file. A FIFO is the sharpest case of the difference — waiting for
    # one to come to rest waits forever.
    #
    # **This covers only a part-written file whose header already parses, and
    # the commonest layout is not that.** Measured against the packaged tools
    # with a 60-second clip: an MP4 written the default way puts `moov` at 90%
    # of the file, so while it is being written ffprobe cannot read it at all
    # and the probe ends at `UNDECODABLE`, the header never having been
    # reached. Only `+faststart`, which moves `moov` to offset 36, gets far
    # enough for the file to be seen moving: 20 trials out of 20 came back
    # `SOURCE_NOT_AT_REST`. Browsers and yt-dlp write the default layout, so
    # the ordinary half-finished download lands on `UNDECODABLE` — the same
    # wrong instruction this member exists to remove, one layer further down.
    #
    # Two qualifications on that, both measured. The 20 trials are a rate and
    # not a guarantee: what is detected is the file *moving across the probe's
    # span*, so a writer that finishes before the opening stat leaves a
    # complete file, which is probed correctly, and one that finishes wholly
    # inside the residual window below leaves nothing to notice. And
    # `+faststart` describes the finished file, not the one being written —
    # ffmpeg relocates `moov` in a finalizing pass, so an encoder writing that
    # flag in real time still has the default layout while it runs: five
    # trials out of five gave `UNDECODABLE`. The flag helps a file being
    # copied or downloaded, never one being recorded.
    #
    # It is not fixable here. Telling a truncated container from a corrupt one
    # needs ffprobe's English diagnostics, and those name the file (§7); the
    # exit status is identical. So **anything consuming these must treat
    # `UNDECODABLE` as possibly-not-finished-yet as well**, rather than as
    # proof the file is permanently broken.
    #
    # A third shape escapes both, and it is the worst of the three: once the
    # writer has stopped, a truncated `+faststart` file is not moving and its
    # header parses, so it is probed successfully — and what comes back is
    # **the whole material's facts, not the surviving prefix's**. The duration
    # is read from a header written before the content it describes. Measured
    # at three truncation points of a 60-second clip: 75%, 50% and 25% each
    # state 60000 ms, 320x240 and the same codecs as the complete file — every
    # field but the digest identical to it — while at 25% only 268 of the 1500
    # frames actually decode.
    #
    # So it is indistinguishable from the *complete* material, not from a short
    # one, and neither consequence surfaces as an error. Anything choosing a
    # range inside that duration can choose one with no frames behind it. And
    # the digest, which is the dedup key, is the one field that does differ —
    # so a half-finished download is filed as a second material whose duration,
    # frame size and codecs match the complete one exactly. A consumer must not
    # read "different digest" as "different material".
    SOURCE_NOT_AT_REST = "source_not_at_rest"
    UNSAFE_PATH = "unsafe_path"
    UNDECODABLE = "undecodable"
    NO_USABLE_STREAM = "no_usable_stream"
    UNUSABLE_DURATION = "unusable_duration"
    TOO_LONG = "too_long"
    UNUSABLE_FRAME_SIZE = "unusable_frame_size"
    FRAME_TOO_LARGE = "frame_too_large"
    FILE_TOO_LARGE = "file_too_large"
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


def _require_source_file(path: object) -> tuple[Path, os.stat_result]:
    """Check a file the user chose to import, and hand back what was checked.

    Unlike the tools, a symlink is fine here: user media legitimately lives
    behind one, and there is no integrity claim to protect — the supply-chain
    reason for rejecting links applies to the packaged binaries, not to the
    operator's own library. The stat therefore follows links, and requiring a
    regular file at the end of the chain is what keeps a FIFO or character
    device out, either of which would leave ffprobe blocked instead of
    returning a fact.

    The stat is returned rather than dropped because nothing here can hold the
    path still afterwards: what this approved and what a caller later opens are
    two different moments, and only a caller holding both can tell that they
    were the same file. The two ffprobe callers have no use for it — they hand
    the path to a subprocess, which is a third moment again — but the digest
    does.
    """
    source = _require_path_shape(path)
    try:
        metadata = source.stat()
    except OSError:
        _reject(MaterialProbeRejection.UNREADABLE)
    if not stat.S_ISREG(metadata.st_mode) or not os.access(source, os.R_OK):
        _reject(MaterialProbeRejection.UNREADABLE)
    return source, metadata


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
    path, _ = _require_source_file(source)
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
    path, _ = _require_source_file(source)
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


def _names_the_same_file(left: os.stat_result, right: os.stat_result) -> bool:
    """Whether two stats are of one file. Size and time are checked separately.

    `package_manifest._same_file_identity` states the same rule for the build's
    own inventory and folds `st_size` in; here the byte count the loop actually
    hashed is a sharper test of the length than a second stat is, and the
    modification time — which that one does not look at — is what catches a
    writer who changes neither the inode nor the length.
    """
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _digest_stable_file(path: Path, expected: os.stat_result) -> str | MaterialProbeRejection:
    """Hash a file, or report why it would not hold still long enough to be hashed.

    A reason rather than a raised rejection: the caller turns it into one after
    leaving its `except`, and returning rather than raising here is what keeps
    that possible. Almost every finding below is the file still being written,
    which is `SOURCE_NOT_AT_REST`; the one that is not — a path that has stopped
    naming a regular file — will not settle on its own and stays `UNREADABLE`.

    `expected` is the stat `_require_source_file` took. Between that check and
    this open the path is not held still by anything, so the two are different
    moments and only comparing them says they were one file.

    What this refuses to hash, each measured rather than assumed:

    - **Unlinked while being read.** POSIX keeps the inode alive behind the
      descriptor, so the read neither fails nor comes up short — measured, every
      remaining byte still arrives and the digest is correct. Nothing about the
      read looks wrong; only asking the *path* again notices, and it raises
      `FileNotFoundError` when it does.
    - **Grown while being read.** `read()` does not stop at the size the
      descriptor stated: measured, a file stating 1 MiB handed back 3 MiB after
      2 MiB were appended mid-read. Stopping at the stated size bounds the work
      to something already checked against the limit rather than to whatever the
      writer decides to produce.
    - **Rewritten in place while being read.** The one that neither the inode nor
      the byte count can see: same `st_dev`, same `st_ino`, same `st_size`, and
      measured over five trials the digest came back describing content that was
      no longer on disk, five times out of five. A pre-allocating writer does
      exactly this for its whole run — aria2's `prealloc`, BitTorrent clients and
      recorders size the file up front and fill it in — so importing a download
      too early is the ordinary way to reach it. Only `st_mtime_ns` moves, so
      that is what is compared. This narrows the window rather than closing it:
      the resolution is the filesystem's, nanoseconds on APFS but whole seconds
      on some network filesystems, and a rewrite finishing inside one tick is
      invisible.
    - **Replaced by another file.** The descriptor keeps the old inode, so the
      read succeeds and yields the old content's digest for the new file's path
      — measured, `st_ino` on the descriptor is unchanged while the path's is
      not. Storing that is exactly the mis-dedup this value exists to prevent.

    Truncation is the fifth, and it needs no check of its own: the loop ends
    early and the byte count comes up short of what was stated.
    """
    # `O_NONBLOCK` because the mode cannot be checked until the open returns, and
    # for a FIFO with no writer it never does: measured, a plain read-only open
    # was still waiting after a full second, while this one came back in 0.165 ms
    # and reported the FIFO. `_require_source_file` rejects a FIFO, but it cannot
    # keep the path from becoming one afterwards, and this is the only one of the
    # three entry points with no subprocess timeout behind it to end the wait.
    # On a regular file the flag changes nothing — measured, the same bytes in
    # the same chunks.
    #
    # Through `getattr` because Windows has no `O_NONBLOCK`, and Windows is a
    # shipping target: `os.O_NONBLOCK` would raise a bare `AttributeError` here,
    # which is not one of the reasons this module promises to fail with.
    descriptor = os.open(os.fspath(path), os.O_RDONLY | cast(int, getattr(os, "O_NONBLOCK", 0)))
    try:
        # Taken from the descriptor rather than from the path, so everything
        # below is about the file actually being read. A path stat leaves room
        # for a different file to be there by the time it is opened.
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            return MaterialProbeRejection.UNREADABLE
        if not _names_the_same_file(expected, opened):
            return MaterialProbeRejection.SOURCE_NOT_AT_REST
        if opened.st_size > MAX_SOURCE_FILE_BYTES:
            _reject(MaterialProbeRejection.FILE_TOO_LARGE)
        digest = hashlib.sha256()
        read_bytes = 0
        while chunk := os.read(descriptor, _DIGEST_CHUNK_BYTES):
            read_bytes += len(chunk)
            if read_bytes > opened.st_size:
                return MaterialProbeRejection.SOURCE_NOT_AT_REST
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if read_bytes < opened.st_size:
        return MaterialProbeRejection.SOURCE_NOT_AT_REST
    if after.st_mtime_ns != opened.st_mtime_ns:
        return MaterialProbeRejection.SOURCE_NOT_AT_REST
    if not _names_the_same_file(opened, path.stat()):
        return MaterialProbeRejection.SOURCE_NOT_AT_REST
    return digest.hexdigest()


def read_content_digest(source: Path) -> str:
    """The SHA-256 of one file's bytes, so two imports of one material can be told apart.

    The only fact about a material that the packaged tools are not needed for,
    so they are not taken: nothing here starts a subprocess.
    """
    path, expected = _require_source_file(source)
    # Rejected after the handler has been left, as in `_run_probe`. `_reject`
    # suppresses the chain, but suppression only stops the renderers: the
    # handled exception stays reachable through `__context__`, and
    # `OSError.filename` is the operator's path. Leaving the handler first drops
    # the reference outright.
    outcome: str | MaterialProbeRejection
    try:
        outcome = _digest_stable_file(path, expected)
    except OSError:
        # Whatever the filesystem refused — no permission, a vanished path, a
        # real IO error — will not come right by importing the same file again.
        outcome = MaterialProbeRejection.UNREADABLE
    if isinstance(outcome, MaterialProbeRejection):
        _reject(outcome)
    return outcome


@dataclass(frozen=True, slots=True)
class MaterialFacts:
    """Everything one probe learned about one file. Structurally unable to hold a path.

    `Material` has no path field, so nothing the Control Plane stores can carry
    one; that says nothing about what the executor hands its own callers, and
    this is where the same guarantee is made on this side of the boundary. It is
    made by there being nowhere to put a path rather than by a rule about what
    to leave out, which is also what makes `repr` safe without overriding it.

    Every field is required and the whole thing is frozen, so a half-filled set
    of facts cannot be built at all — not before the last step has returned, and
    not by filling one in afterwards. The orchestration therefore has no state
    to unwind when a step rejects: there is nothing part-built to discard.
    """

    kind: ProbedMaterialKind
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    has_audio: bool
    audio_loudness_lufs: float | None
    content_digest: str


def _held_still(before: os.stat_result, after: os.stat_result) -> bool:
    """Whether one path named the same unchanged file at two moments.

    `_names_the_same_file` answers who the file is; the other two answer whether
    it changed while staying itself. The timestamp is what a rewrite in place
    moves, and the byte count is what still shows a change on a filesystem whose
    timestamps are too coarse to show one — whole seconds, on some network
    filesystems, against nanoseconds on APFS.

    Nothing else from the stat is compared. Reading a file is not a change, and
    `st_atime` moves when it is read, so comparing the two results outright
    would reject every material the probe was handed.
    """
    return (
        _names_the_same_file(before, after)
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_size == after.st_size
    )


def probe_material(tools: PackagedMediaTools, source: Path) -> MaterialFacts:
    """Read one file's facts with the packaged tools, or reject it.

    The three steps run in this order for reasons that outlast the code: the
    stream list is what says whether a measuring pass is needed at all, and the
    digest goes last because it is the only step that reads the file end to end
    — up to some nine seconds at the size limit — so an earlier rejection should
    not have paid for it.

    Nothing here decides which materials get measured. `read_audio_facts`
    returns without starting ffmpeg when the stream list carries no sound track,
    and it is also what refuses a sound-only file with nothing audible in it,
    which `Material` cannot represent. Repeating either rule here would give the
    two copies somewhere to drift apart.

    Nor is anything caught: a step's rejection travels up as the object it was
    raised as, carrying the frame it came from. Rebuilding it around the same
    reason would lose that and add nothing.

    **The file is not held still across the three steps, and cannot be.** Each
    step hands the path to something that opens it itself, two of them to a
    subprocess that is given a name rather than a descriptor, so each describes
    whatever was there when it looked — three moments, and the measuring pass
    alone may take fifteen minutes of the span between the first and the last.
    The digest refuses a file that moved while *it* was hashing, but until now
    nothing watched the window in front of it, where a swap would leave facts
    describing one file stored beside another file's digest. That is a mis-dedup
    that surfaces as no error at all.

    So the stat that approved the import is compared against a fresh one at the
    end. That notices rather than prevents, which is all a name-taking
    subprocess leaves available, and rejecting on notice beats reporting facts
    that were never all true at once. It narrows the window rather than closing
    it: a change that begins and ends between the two stats, leaving the inode,
    the length and the timestamp where they were, is invisible — the same
    residual the digest's own checks have, for the same reason.

    How much residual is left depends on the filesystem, and on the main
    platform it is small: measured over 3000 create/delete cycles, APFS handed
    out 3000 distinct inode numbers and reused none, so a file replaced by
    another cannot come back wearing the same identity. What is left is one
    shape — the same inode rewritten in place and its length and timestamp put
    back. `st_ctime_ns` would close most of that, since it moves on a write
    even when `st_mtime_ns` is restored, but it also moves for things that are
    not changes to the content at all: an xattr written by a Spotlight importer
    or a virus scanner touching a file mid-probe would refuse a material that
    nobody edited. Refusing what is fine is worse here than missing the rare
    forgery, so it is left out deliberately rather than overlooked.

    Nothing here takes a lock, so a probe spending its whole budget — the
    reading pass, then up to fifteen minutes measuring, then the digest — blocks
    no other work.
    """
    path, before = _require_source_file(source)
    # The size is already in hand, and no tool could say anything that changes
    # what it earns. Left to the digest alone this was the cheapest rejection in
    # the module reached last, behind a reading pass and a measuring pass that
    # may run for fifteen minutes — the opposite of the ordering argued for
    # above. The digest's own check stays and stays authoritative: this one
    # reads a path, and a path can name a different file by the time it is
    # opened. Both compare against `MAX_SOURCE_FILE_BYTES`, so the early exit
    # cannot drift into a second, different limit.
    if before.st_size > MAX_SOURCE_FILE_BYTES:
        _reject(MaterialProbeRejection.FILE_TOO_LARGE)
    streams = read_stream_facts(tools, path)
    audio = read_audio_facts(tools, path, streams)
    content_digest = read_content_digest(path)
    # Through `_require_source_file` rather than a bare `stat` so that a file
    # that vanished, or stopped being readable, in the meantime comes back as
    # the reason it already has instead of an `OSError` carrying the path.
    _, after = _require_source_file(path)
    if not _held_still(before, after):
        _reject(MaterialProbeRejection.SOURCE_NOT_AT_REST)
    return MaterialFacts(
        kind=streams.kind,
        duration_ms=streams.duration_ms,
        width=streams.width,
        height=streams.height,
        video_codec=streams.video_codec,
        audio_codec=streams.audio_codec,
        has_audio=audio.has_audio,
        audio_loudness_lufs=audio.loudness_lufs,
        content_digest=content_digest,
    )


class MaterialPathRegistryRejection(StrEnum):
    """Why one material's file cannot be recorded or handed back.

    Separate from `MaterialProbeRejection`, which answers "why this file cannot
    become a material". These answer questions probing has no word for, and each
    names a different next step for the material library: re-import it, point it
    at the file again, or stop and show that something is wrong with the
    library itself.
    """

    UNUSABLE_IDENTIFIER = "unusable_identifier"
    NOT_REGISTERED = "not_registered"
    # Split from each other because the user's next step differs. The file is
    # gone from the path it was imported from — moved, deleted, or on a volume
    # that is not mounted — against: something is at that path, but it is not
    # the file that was probed. The first is answered by finding it again, the
    # second by importing it again, and telling the user the wrong one sends
    # them looking for a file that is sitting right there.
    FILE_MISSING = "file_missing"
    # And split again for the same reason one level down: a file whose
    # permissions changed, or that stopped being a regular file, is still
    # exactly where the user left it. Reporting that as missing starts a search
    # that cannot succeed — the same wrong instruction `SOURCE_NOT_AT_REST` was
    # split out of `UNREADABLE` to stop giving.
    FILE_UNREADABLE = "file_unreadable"
    FILE_CHANGED = "file_changed"
    REGISTRY_UNREADABLE = "registry_unreadable"
    REGISTRY_UNWRITABLE = "registry_unwritable"
    REGISTRY_FULL = "registry_full"


class MaterialPathRegistryRejected(RuntimeError):
    """Carries a closed reason code; the message stays fixed and path-free.

    Every path this class can fail about is one of the operator's own, so the
    message may not name it (`CLAUDE.md` §7), exactly as with
    `MaterialProbeRejected`.
    """

    def __init__(self, rejection: MaterialPathRegistryRejection) -> None:
        super().__init__("material path registry rejected")
        self.rejection = rejection


def _reject_registry(rejection: MaterialPathRegistryRejection) -> Never:
    """Raise with nothing chained behind it, for `_reject`'s reasons.

    `OSError.filename` is the path itself and it survives `from None` on
    `__context__`. Every call site below therefore reduces its failure to one of
    these codes *inside* the handler and raises after leaving it, so there is no
    handled exception left to reach.
    """
    raise MaterialPathRegistryRejected(rejection) from None


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    """Which file, and whether it is still the way it was. Four numbers, no path.

    The same four things `_held_still` compares, in a form that survives a round
    trip through JSON — which an `os.stat_result` does not. That duplication is
    deliberate and is held in place by a test asserting the two agree field for
    field; see `TestRegisteredIdentityAgreesWithTheProbesOwnRule`.
    """

    device: int
    inode: int
    modified_ns: int
    size_bytes: int


def _identity_of(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        modified_ns=metadata.st_mtime_ns,
        size_bytes=metadata.st_size,
    )


@dataclass(frozen=True, slots=True, repr=False)
class _RegisteredFile:
    """One material's file, as it was when the mapping was recorded.

    The redacted `repr` is not decoration: this object is reachable from the
    registry's own frame locals, and a crash reporter walking them is precisely
    the leak `PackagedMediaTools` closes the same way.
    """

    path: Path
    identity: _FileIdentity

    def __repr__(self) -> str:
        return "_RegisteredFile(<redacted>)"


def _usable_identifier(value: object) -> UUID | None:
    """The material's identifier, or `None` when it is not one that could exist.

    The Control Plane issues these as `control_plane.domain.material.MaterialId`,
    which the executor may not import (`CLAUDE.md` §4.3), so what crosses the
    boundary is the `uuid.UUID` inside it. The rule is
    `local_artifact._valid_artifact_id`'s, stated again here for the same reason:
    reaching into another module's private name would be worse than saying it
    twice. A test pins this against the domain's own identifier so an
    identifier the Control Plane can issue and this cannot store — a row nothing
    could ever look up again — fails loudly rather than silently.

    The round trip through `str` is what a `UUID` subclass with its own
    `__str__` would fail: the text is the document's key, so a value that does
    not print canonically would be written under a key nothing looks up.
    """
    if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
        return None
    return value if str(value) == str(UUID(str(value))) else None


def _shaped_path(value: object) -> Path | None:
    """A path out of the document, checked the way an imported path is checked.

    Reads back through `_require_path_shape` rather than restating its rules, so
    a document can never smuggle in something the import path would have
    refused. It returns rather than raises because the caller is parsing a
    damaged file and owes the user a registry reason, not a probe reason.
    """
    if not isinstance(value, str):
        return None
    try:
        return _require_path_shape(Path(value))
    except MaterialProbeRejected:
        return None


def _whole_number(value: object) -> bool:
    """`type(...) is not int` rather than `isinstance`: `True` is not a device number."""
    return type(value) is int and value >= 0


def _parsed_entry(value: object) -> _RegisteredFile | None:
    if not isinstance(value, dict) or set(value) != _REGISTRY_ENTRY_KEYS:
        return None
    path = _shaped_path(value["path"])
    numbers = (value["device"], value["inode"], value["modified_ns"], value["size_bytes"])
    if path is None or not all(_whole_number(number) for number in numbers):
        return None
    device, inode, modified_ns, size_bytes = (cast(int, number) for number in numbers)
    return _RegisteredFile(
        path=path,
        identity=_FileIdentity(
            device=device,
            inode=inode,
            modified_ns=modified_ns,
            size_bytes=size_bytes,
        ),
    )


def _parsed_document(payload: bytes) -> dict[UUID, _RegisteredFile] | None:
    """Every mapping the document states, or `None` when it does not state mappings.

    `json.loads` raising is only half of what can be wrong: measured, it returns
    a list for `[]` and a string for `"a string"` without raising at all, so the
    shape is checked rather than caught. Both non-UTF-8 bytes and malformed text
    arrive as `ValueError` — `UnicodeDecodeError` and `JSONDecodeError` are both
    subclasses, measured — so one handler covers them.
    """
    try:
        document = json.loads(payload)
    except ValueError:
        return None
    if (
        not isinstance(document, dict)
        or document.get("version") != MATERIAL_PATH_REGISTRY_VERSION
        or not isinstance(document.get("entries"), dict)
    ):
        return None
    entries: dict[UUID, _RegisteredFile] = {}
    for key, value in document["entries"].items():
        identifier = _usable_identifier(_parsed_identifier(key))
        entry = _parsed_entry(value)
        if identifier is None or entry is None:
            return None
        entries[identifier] = entry
    return entries


def _parsed_identifier(key: str) -> UUID | None:
    """The identifier a document's key names, or `None` when it names none.

    No check that the key is text: JSON has no other kind of key, so every
    caller reaching here has one from `json.loads`. A guard for it would be a
    term nothing reachable could decide, which is the shape this module has
    twice found riding along at full coverage while checking nothing.
    """
    try:
        parsed = UUID(key)
    except ValueError:
        return None
    return parsed if str(parsed) == key else None


class MaterialPathRegistry:
    """Where each material's file is, kept on this computer and nowhere else.

    `Material` has no path field and `MaterialFacts` has nowhere to put one, so
    the operator's private paths never reach the Control Plane (`CLAUDE.md`
    §4.2, §7). Something still has to remember which file a material was made
    from — to play it back, to re-cut it, to tell the user it has gone missing —
    and this is that something: a JSON document below the Executor's private
    state directory, which never leaves the machine.

    **A mapping is a fact about a moment.** T5 established that nothing holds a
    file still even across one probe; between a probe and the next use of what
    it learned there is no bound at all. So each entry records the file's
    identity as well as its path, and `resolve` re-checks it: what comes back is
    either the file that was registered or a reason it is not. Storing the path
    alone would reopen exactly the window T5 closed, one layer up — facts about
    one file used against another, with nothing surfacing as an error.

    **The registry is not an identity.** A digest tells two materials apart only
    in one direction: identical digests do mean identical bytes, but different
    digests do not mean different materials — a half-finished download of a
    `+faststart` file states the complete material's duration, frame size and
    codecs while hashing differently (see `SOURCE_NOT_AT_REST`). Nothing here is
    keyed or deduplicated by content for that reason; the key is the identifier
    the Control Plane issued, and deciding what is a duplicate belongs where the
    materials themselves are stored.

    **One writer.** The document is held in memory and rewritten whole on each
    registration, which is correct for the single local Executor the product
    runs (`CLAUDE.md` §4.3) and would lose writes if there were two. Nothing
    here takes a lock, so this is an assumption the deployment satisfies rather
    than one the code enforces.
    """

    __slots__ = ("_document", "_entries", "_state_directory")

    def __init__(self, *, state_directory: Path) -> None:
        """Load what is already recorded, or start empty on a computer that has none.

        The directory is the bootstrap's, taken rather than searched for or
        created: this is a tenant of the Executor's private state root, and a
        registry that created its own directory would answer a mistyped path by
        silently starting a second, empty library.
        """
        self._state_directory = _require_state_directory(state_directory)
        self._document = self._state_directory / MATERIAL_PATH_REGISTRY_FILE_NAME
        self._entries = self._load()

    def register(self, material_id: UUID, source: Path) -> None:
        """Record where one material's file is, replacing whatever was recorded before.

        Re-registering an identifier at a *different* path is how a material the
        user moved is found again; refusing it would leave the material
        unreachable for good, and the library's "this file is missing" would
        have no cure. The previous path is forgotten rather than kept, because
        nothing reads a history and keeping one would be a second place a path
        lives.

        Re-registering the *same* pair rewrites the same bytes, so it is
        idempotent. The identity is re-read each time rather than preserved: a
        caller reaching this line has just probed the file, so what it observes
        now is the newer truth.

        Two materials naming one file is allowed. Whether that should have been
        one material is a question about content, and this is not where content
        is compared.

        The file itself is checked exactly as an imported file is checked, and
        those failures are raised as `MaterialProbeRejected` — the caller is
        already handling them from the probe it just ran, and restating
        `UNSAFE_PATH` against `UNREADABLE` in a second vocabulary would lose the
        distinction to no purpose.
        """
        identifier = _usable_identifier(material_id)
        if identifier is None:
            _reject_registry(MaterialPathRegistryRejection.UNUSABLE_IDENTIFIER)
        path, metadata = _require_source_file(source)
        entries = dict(self._entries)
        entries[identifier] = _RegisteredFile(path=path, identity=_identity_of(metadata))
        self._write(_serialized(entries))
        # Only now: a registration that could not be persisted must not be
        # visible to this process either, or a restart would appear to lose it.
        self._entries = entries

    def resolve(self, material_id: UUID) -> tuple[Path, os.stat_result]:
        """The file this material was made from, if it is still that file.

        The check is not optional and not a separate call, because a caller that
        could take the path without it would be back to using facts about one
        file against another.

        The stat is handed back for `_require_source_file`'s reason, which
        applies here with a longer window in front of it: what this approved and
        what the caller later opens are two different moments, and only a caller
        holding both can tell they were the same file. Returning the path alone
        made the check something that had already expired when it was read — a
        consumer could pass it and open a file swapped immediately afterwards,
        with nothing surfacing as an error. With the stat in hand the consumer
        carries the window forward itself, through `_held_still`.

        **The path that comes back is still the user's, and must be treated as
        one.** Nothing about having been registered makes it safer than the
        path that was imported: whoever consumes it opens it through
        `_require_source_file` like any other, which the probing chain already
        does. The registry's own trust boundary is the private state directory,
        not the contents of what it stores.
        """
        identifier = _usable_identifier(material_id)
        if identifier is None:
            _reject_registry(MaterialPathRegistryRejection.UNUSABLE_IDENTIFIER)
        entry = self._entries.get(identifier)
        if entry is None:
            _reject_registry(MaterialPathRegistryRejection.NOT_REGISTERED)
        # `_require_source_file` refuses a vanished path, an unreadable one and
        # anything that is no longer a regular file, and gives all three the
        # same reason — so the difference the library needs is worked out
        # separately below. Reduced to a code inside the handler so the
        # `OSError` underneath, which carries the path, is not left on the
        # chain.
        metadata: os.stat_result | None
        try:
            _, metadata = _require_source_file(entry.path)
        except MaterialProbeRejected:
            metadata = None
        if metadata is None:
            _reject_registry(_why_the_file_cannot_be_used(entry.path))
        if _identity_of(metadata) != entry.identity:
            _reject_registry(MaterialPathRegistryRejection.FILE_CHANGED)
        return entry.path, metadata

    def __repr__(self) -> str:
        return "MaterialPathRegistry(<redacted>)"

    def _load(self) -> dict[UUID, _RegisteredFile]:
        """What the document states, refusing rather than rebuilding when it is damaged.

        **A damaged document is not rebuilt empty.** Writes go through
        `os.replace`, so a half-written document is not something a killed
        process leaves behind; one that will not parse means something else
        happened to it. Starting over would then destroy every mapping the user
        has and present the result as an ordinary empty library — the user could
        not tell that from having imported nothing, and there would be nothing
        left to recover from. Refusing keeps the file on disk and says so.

        The absent document is the other case entirely, and the two must not be
        confused: no file is a first run, while a file that cannot be read is a
        failure. `FileNotFoundError` is what separates them, so a permission
        error can never be mistaken for a fresh start.
        """
        self._sweep_scratch()
        payload = _read_document(self._document)
        if payload is None:
            return {}
        if isinstance(payload, MaterialPathRegistryRejection):
            _reject_registry(payload)
        entries = _parsed_document(payload)
        if entries is None:
            _reject_registry(MaterialPathRegistryRejection.REGISTRY_UNREADABLE)
        return entries

    def _sweep_scratch(self) -> None:
        """Remove the scratch files of writes that were killed before they finished.

        `_write` clears up after itself on every path it can still run on, but a
        `SIGKILL` between `mkstemp` and `os.replace` leaves one behind and
        nothing afterwards had any reason to look — measured, three kills left
        three files and an ordinary start removed none of them. Unbounded growth
        in the directory this class promises holds one document.

        Only names this class writes: the pattern is the `mkstemp` prefix and
        suffix, so a neighbour's file in the shared state directory is not this
        module's to delete.

        Tidying, never a precondition. Every failure is swallowed, because a
        leftover nobody can remove must not be the reason the library will not
        open.
        """
        pattern = f".{MATERIAL_PATH_REGISTRY_FILE_NAME}.*.tmp"
        with suppress(OSError):
            for leftover in self._state_directory.glob(pattern):
                with suppress(OSError):
                    leftover.unlink()

    def _write(self, payload: bytes) -> None:
        """Publish a whole document or none of it.

        The bytes go to a scratch file in the same directory — the same
        filesystem, so the rename cannot fail for crossing one — and only
        `os.replace` makes them the document. A process killed at any point
        before that leaves the previous document exactly as it was, and the
        scratch file behind; killed after it, the new one. There is no moment at
        which the document is half of anything. This states
        `control_plane.bootstrap.local_provisioning._write_private_document`'s
        shape, which is the same trade for the same reason.

        `fsync` on the scratch file makes its contents durable before the
        rename. The rename itself is not followed by a directory `fsync`, so a
        power cut in that window can still lose the registration — the guarantee
        here is against a killed process, not against a power cut, and the
        sibling writer above draws the line in the same place.

        `mkstemp` opens with `O_EXCL` at mode 0600 and `os.replace` carries that
        mode across, both measured, so the document is never briefly readable by
        anyone else.
        """
        if len(payload) > MAX_MATERIAL_PATH_REGISTRY_BYTES:
            _reject_registry(MaterialPathRegistryRejection.REGISTRY_FULL)
        outcome: MaterialPathRegistryRejection | None = None
        scratch: str | None = None
        try:
            descriptor, scratch = tempfile.mkstemp(
                dir=self._state_directory,
                prefix=f".{MATERIAL_PATH_REGISTRY_FILE_NAME}.",
                suffix=".tmp",
            )
            with os.fdopen(descriptor, "wb") as sink:
                sink.write(payload)
                sink.flush()
                os.fsync(sink.fileno())
            os.replace(scratch, self._document)
            scratch = None
        except OSError:
            outcome = MaterialPathRegistryRejection.REGISTRY_UNWRITABLE
        finally:
            # Whatever went wrong, the scratch file is this method's to remove:
            # left behind it would sit in the state directory forever, and
            # `register` promises the directory holds one document and nothing
            # else.
            if scratch is not None:
                with suppress(OSError):
                    os.unlink(scratch)
        if outcome is not None:
            _reject_registry(outcome)


def _why_the_file_cannot_be_used(path: Path) -> MaterialPathRegistryRejection:
    """Whether the file is gone, or is sitting there and cannot be used.

    A reason rather than a raised rejection, so the caller raises after leaving
    its handler; `OSError.filename` is the operator's path.

    `FileNotFoundError` is what says nothing is there, and `NotADirectoryError`
    says the same thing about a component of the path — both send the user to
    find the file. Everything else, permissions above all, means it has not
    moved: measured, a file at mode 000 still stats while `os.access` refuses
    it, which is exactly the case `_require_source_file` folds in with the
    vanished one.
    """
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return MaterialPathRegistryRejection.FILE_MISSING
    except OSError:
        pass
    return MaterialPathRegistryRejection.FILE_UNREADABLE


def _require_state_directory(state_directory: object) -> Path:
    """The bootstrap's private state directory, which must already be one.

    Deliberately lighter than `local_artifact._require_private_directory`, which
    also refuses linked ancestors and checks the mode and owner. That is not
    reuse of a shared helper — it is another module's private one — and the
    stronger check belongs with whoever wires this into the Executor's startup,
    where the same guarantee is already being made about the state root for the
    ledger. Registered as an open question rather than quietly matched.
    """
    if not isinstance(state_directory, Path) or not state_directory.is_dir():
        _reject_registry(MaterialPathRegistryRejection.REGISTRY_UNREADABLE)
    return state_directory


def _read_document(document: Path) -> bytes | MaterialPathRegistryRejection | None:
    """The document's bytes, `None` when there is none, or why it could not be read.

    A reason rather than a raised rejection, for `_digest_stable_file`'s reason:
    returning from inside the handler lets the caller raise after the handled
    `OSError` — which carries the path in `filename` — has been let go of.

    The size is taken from the descriptor the read will use and checked before
    a byte is read, so an oversized document costs nothing to refuse. That is
    the shape `_run_probe` was found not to have: it reads ffprobe's output in
    full and compares afterwards.
    """
    # `O_NONBLOCK` for `_digest_stable_file`'s reason, which applies here with
    # nothing in front of it to help: the mode cannot be checked until the open
    # returns, and for a FIFO with no writer it never does — measured, a plain
    # read-only open was still waiting after a full second while this one came
    # back in a fraction of a millisecond and reported the FIFO. There the path
    # had already been stat'd; here it has not, so this flag is the only thing
    # between a pipe left in the state directory and an Executor that never
    # finishes starting. On a regular file it changes nothing, measured.
    #
    # `O_NOFOLLOW` is cheap depth and nothing more. It is tempting to justify it
    # as defence against someone redirecting what we read, but that argument
    # does not survive being followed: anyone who can put a link here can write
    # the document itself, and a document naming `/etc/passwd` is accepted
    # without complaint — measured. There is no path restriction that would
    # help either, because the operator's media legitimately lives anywhere.
    #
    # **The load-bearing boundary is the directory: 0600 inside the App's
    # private state root.** Whoever can write there already has the App. This
    # flag only refuses the final component; every ancestor may still be a link,
    # so it is not even a complete version of the thing it is not relied upon
    # for.
    #
    # Both flags go through `getattr`: neither exists on Windows, which is a
    # shipping target, and an `AttributeError` out of here is not a rejection
    # code — nothing catching `MaterialPathRegistryRejected` would see it.
    flags = (
        os.O_RDONLY
        | cast(int, getattr(os, "O_NONBLOCK", 0))
        | cast(int, getattr(os, "O_NOFOLLOW", 0))
    )
    try:
        descriptor = os.open(document, flags)
    except FileNotFoundError:
        return None
    except OSError:
        return MaterialPathRegistryRejection.REGISTRY_UNREADABLE
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_MATERIAL_PATH_REGISTRY_BYTES
        ):
            return MaterialPathRegistryRejection.REGISTRY_UNREADABLE
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, _DIGEST_CHUNK_BYTES))
            if not chunk:
                return MaterialPathRegistryRejection.REGISTRY_UNREADABLE
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    except OSError:
        return MaterialPathRegistryRejection.REGISTRY_UNREADABLE
    finally:
        os.close(descriptor)


def _serialized(entries: dict[UUID, _RegisteredFile]) -> bytes:
    """The document, written the one way, so the same mapping is the same bytes.

    Sorted and separator-fixed because `register` is idempotent by producing
    identical bytes rather than by comparing first, and `ensure_ascii` because a
    document of pure ASCII cannot depend on anything reading it guessing an
    encoding. Both follow `page_drift_artifact`.
    """
    document = {
        "entries": {
            str(identifier): {
                "path": os.fspath(entry.path),
                "device": entry.identity.device,
                "inode": entry.identity.inode,
                "modified_ns": entry.identity.modified_ns,
                "size_bytes": entry.identity.size_bytes,
            }
            for identifier, entry in entries.items()
        },
        "version": MATERIAL_PATH_REGISTRY_VERSION,
    }
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = [
    "MATERIAL_PATH_REGISTRY_FILE_NAME",
    "MATERIAL_PATH_REGISTRY_VERSION",
    "MAX_CODEC_NAME_CHARACTERS",
    "MAX_MATERIAL_DIMENSION",
    "MAX_MATERIAL_DURATION_MS",
    "MAX_MATERIAL_PATH_REGISTRY_BYTES",
    "MAX_MEASURE_OUTPUT_BYTES",
    "MAX_PATH_CHARACTERS",
    "MAX_PROBE_OUTPUT_BYTES",
    "MAX_SOURCE_FILE_BYTES",
    "AudioFacts",
    "MaterialFacts",
    "MaterialPathRegistry",
    "MaterialPathRegistryRejected",
    "MaterialPathRegistryRejection",
    "MaterialProbeRejected",
    "MaterialProbeRejection",
    "MediaStreamFacts",
    "PackagedMediaTools",
    "ProbedMaterialKind",
    "probe_material",
    "read_audio_facts",
    "read_content_digest",
    "read_stream_facts",
]
