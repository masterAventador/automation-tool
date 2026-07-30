"""LE-07 T7: real materials, the packaged tools, and the domain model they fill.

Everything in `test_material_probe.py` that stubs a tool proves what the module
does with an answer. Nothing there can prove that the answers are real ones: a
stub replies to any command line, legal or not, and it prints whatever shape the
test author imagined. Both have already cost this line a Critical each — a
command line ffmpeg rejects outright while 110 stub tests stayed green, and three
silence patterns verified only against a hand-written log.

So every judgement below is produced by the packaged ffprobe and ffmpeg reading a
file the packaged ffmpeg built moments earlier, and every reading is stated as a
number rather than as a range. Three further things are asserted only here:

- the values are cross-checked against a **second, differently shaped** ffprobe
  query, so a fact is never compared only with itself;
- every set of facts is used to **construct a real `Material`**, which is the
  only proof that what the executor learns is what the Control Plane can store —
  matching the two layers' limits cannot show it, since identical limits still
  admit a video filed as a still;
- every member of `MaterialProbeRejection` is **provoked**, and the count is
  stated the way it was counted: **11 have a real file behind them, 8 of those
  are read by the packaged tools, and 12 need no stub at all** (`unreadable` and
  `unsafe_path` have no file to have; `source_not_at_rest` and `file_too_large`
  have one no tool ever reads). The thirteenth, `probe_crashed`, runs the real
  ffprobe behind a wrapper — see `_crashing_probe`.

The materials are built under a directory whose name carries a space, `&`, `$`,
an apostrophe and Chinese, so the whole table is read through an awkward path
rather than one test being about that. Durations are deliberately off the second
and off ebur128's 100 ms grid: the sibling file records a round of green tests
that all used whole-second material, and the grid is where two of this module's
real defects hid.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path

import pytest
from test_material_probe import (
    # The planted statement is the sibling's: one value for one attack, since
    # both files plant the same line and a second copy could drift from the
    # patterns it is aimed at.
    PLANTED_STATEMENT,
    SPECIAL_DIRECTORY_NAME,
    _encode,
    _packaged_tools,
)

from automation_tool.control_plane.domain import material as domain_material
from automation_tool.control_plane.domain.material import (
    DescriptionSource,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor import material_probe
from automation_tool.executor.material_probe import (
    MAX_MATERIAL_DIMENSION,
    MAX_MATERIAL_DURATION_MS,
    MAX_SOURCE_FILE_BYTES,
    MaterialFacts,
    MaterialProbeRejected,
    MaterialProbeRejection,
    PackagedMediaTools,
    ProbedMaterialKind,
    probe_material,
    read_stream_facts,
    require_source_unchanged,
)

# Every duration below is off the second and off the 100 ms grid ebur128 states
# its readings on. The sibling file's T3 notes record why: a round of material
# cut to whole seconds passed everything while the silence handling was wrong,
# and a boundary that lands exactly on the grid is the one case a rounding error
# cannot be seen in.
SOUND_SECONDS = "3.037"
SILENT_SECONDS = "2.017"
MUTE_SECONDS = "1.083"
AUDIO_SECONDS = "2.041"
PIPED_SECONDS = "2.037"

# What the packaged ffprobe reports for the files built from those, measured.
# Stated as exact figures rather than tolerances: a toolchain upgrade that starts
# rounding a container's duration differently should be a red test and not a
# silent change in what every material's length becomes.
SOUND_MS = 3037
SILENT_MS = 2017
MUTE_MS = 1083
AUDIO_MS = 2041

# What the JPEG states for itself, measured. Nothing uses it as a duration — a
# picture container has none — and this is here so that the ignoring is asserted
# rather than described.
PHOTO_STATED_SECONDS = 0.04

# How far the module's reading may sit from ebur128's own summary. The module
# takes the last per-window value and the summary prints one decimal, so they
# differ in the third: measured 21.774 against 21.8, 21.833 against 21.8, and
# 22.315 against 22.3. Wide enough for that, narrow enough to catch a systematic
# shift — a +0.4 LUFS one went unnoticed before this reading existed.
SUMMARY_LUFS_TOLERANCE = 0.1

# A tone at this level reads here, measured against ebur128's own summary: -21.8
# LUFS, which it states as -21.776 per window. The tolerance covers the encoder
# rather than the measurement.
TONE_LUFS = -21.8
TONE_LUFS_TOLERANCE = 0.5

# A second of digital silence and a second and a half of tone, both off the grid:
# the span silencedetect opens at zero has to close when the tone starts, and the
# closing line is the field nothing else here produces. Measured on the pair:
# 2540 ms, audible, -22.3 LUFS.
LEAD_SILENCE_SECONDS = "1.03"
LEAD_TONE_SECONDS = "1.51"
LEAD_SILENCE_MS = 2540
LEAD_SILENCE_LUFS = -22.3

# Over four hours by one second, which no real encode would produce cheaply —
# see `_state_a_longer_duration`.
TOO_LONG_SECONDS = 14401

# `MAX_MATERIAL_DIMENSION` is 8192; this is comfortably past it and a PNG of it
# costs three kilobytes.
OVERSIZED_FRAME_WIDTH = 9000
OVERSIZED_FRAME_HEIGHT = 100

# Where the truncated copy is cut. The point is not the fraction but that the
# header — moved to the front by `+faststart` — still parses afterwards.
TRUNCATION_FRACTION = 0.5

# What ffprobe's plain output format prints for an entry a file does not state,
# measured on the PNG: the JSON form the module reads omits the key entirely
# while this one writes these two letters.
ABSENT_IN_PLAIN_OUTPUT = "N/A"


def _sparse_file(directory: Path, name: str, size: int) -> Path:
    """A file of a stated length that occupies no disk.

    The byte limit is 16 GiB and the check reads `st_size`, so the case can be
    made honestly without writing 16 GiB: measured, this returns instantly and
    the containing volume loses nothing.
    """
    path = directory / name
    with path.open("wb") as sink:
        sink.truncate(size)
    assert path.stat().st_size == size
    return path


def _truncate_to(source: Path, destination: Path, fraction: float) -> None:
    payload = source.read_bytes()
    destination.write_bytes(payload[: int(len(payload) * fraction)])


def _state_a_longer_duration(source: Path, destination: Path, seconds: int) -> None:
    """Rewrite one Matroska's stated duration, changing nothing else.

    Four hours of real sound is the cheapest material that exceeds the duration
    limit and it still costs an encode of four hours; every header field of an
    MP4 was tried first and none of them decides the answer — measured, ffprobe
    builds an MP4's duration from the sample table, so `mvhd`, `tkhd` and `mdhd`
    can all state four hours while it keeps reporting two seconds. Matroska is
    the container that states its duration and is believed: a float in
    `SegmentInfo`, in the file's timecode scale, which is milliseconds by
    default.

    The assertion is what keeps this honest. `0x4489` occurs in the file for
    other reasons — measured, four times in a two-second clip — so the element is
    the one whose length is a double and whose value is the duration the file
    really has. If that ever matches none or several, this fails rather than
    quietly producing an unmodified copy.
    """
    data = bytearray(source.read_bytes())
    known_ms = _independent_duration_seconds(source) * 1000
    found: list[int] = []
    index = data.find(b"\x44\x89")
    while index != -1:
        length = data[index + 2] & 0x7F
        if length == 8:
            stated = struct.unpack(">d", bytes(data[index + 3 : index + 11]))[0]
            if abs(stated - known_ms) <= 2.0:
                found.append(index)
        index = data.find(b"\x44\x89", index + 1)
    assert len(found) == 1, f"expected one duration element, found {found}"
    data[found[0] + 3 : found[0] + 11] = struct.pack(">d", float(seconds * 1000))
    assert len(data) == source.stat().st_size
    destination.write_bytes(bytes(data))


def _state_no_frame_size(source: Path, destination: Path) -> None:
    """Rewrite one BMP's stated width to zero, keeping the file readable.

    A picture container is what makes this reachable: for anything timed the
    duration is read first, so a raw stream reporting `width: 0` — measured, an
    empty `.h264` does exactly that — is refused for its duration before the
    frame size is looked at. A BMP demuxes as `bmp_pipe`, where the duration is
    not asked for at all.

    Built by hand rather than by ffmpeg, because ffmpeg will not encode a picture
    of no width; the header is 54 bytes of fixed layout and the width sits at
    offset 18.
    """
    data = bytearray(source.read_bytes())
    assert struct.unpack_from("<i", data, 18)[0] == 1
    struct.pack_into("<i", data, 18, 0)
    destination.write_bytes(bytes(data))


def _state_no_codec_name(source: Path, destination: Path) -> None:
    """Replace one MP4's video sample entry type, so ffprobe cannot name the codec.

    Measured: with `avc1` renamed in the sample table, ffprobe exits 0 and
    reports the stream as `codec_type: video` with a width, a height and **no
    `codec_name` at all**, which is the shape `_codec_name` refuses. Doing the
    same to an audio entry does not work — ffprobe recovers `aac` from the
    bitstream — so the video entry is the one that produces this.

    The first `avc1` in the file is the `ftyp` brand list, not the sample entry;
    the one patched is the last, and the assertion pins which by reading the
    frame size that only the sample entry carries.
    """
    data = bytearray(source.read_bytes())
    entries = []
    index = data.find(b"avc1")
    while index != -1:
        entries.append(index)
        index = data.find(b"avc1", index + 1)
    sample_entry = entries[-1]
    assert struct.unpack_from(">H", data, sample_entry + 28)[0] == 640
    data[sample_entry : sample_entry + 4] = b"zzzz"
    destination.write_bytes(bytes(data))


def _independent_duration_seconds(source: Path) -> float:
    """The container's duration in seconds, read through a different query than the module's.

    The module asks for a JSON document listing several entries at once; this
    asks for one value in ffprobe's plain output format. A fact compared only
    against the same query answering the same way is compared with itself.
    """
    return float(
        _ffprobe(
            source,
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
        )
    )


def _independent_loudness_lufs(source: Path) -> float:
    """ebur128's own summary, from an invocation the module does not make.

    The one number in the facts that had nothing behind it. Every other value is
    cross-read from a second ffprobe query, but the loudness came from the module
    alone and was only ever asserted inside a tolerance — measured, shifting
    `_storable_loudness` by +0.4 LUFS left all 49 tests here green.

    This asks a different question of a different tool: no `metadata=1`, no
    `ametadata` sink, no filter graph of the module's own — just ebur128 printing
    its own end-of-file summary to the diagnostics, which the module discards and
    never parses. The two agree to a tenth of a LUFS, which is all the summary
    prints.
    """
    completed = subprocess.run(
        [
            os.fspath(_packaged_tools().ffmpeg_path),
            "-nostdin",
            "-v",
            "info",
            "-i",
            os.fspath(source),
            "-vn",
            "-af",
            "ebur128=peak=none",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    # The summary's line, not the running ones: those are prefixed with the
    # filter's name and address, this one is indented under `Summary:`.
    stated = re.findall(r"^\s+I:\s+(-?[0-9]+(?:\.[0-9]+)?) LUFS$", completed.stderr, re.MULTILINE)
    assert stated, completed.stderr[-400:]
    return float(stated[-1])


def _ffprobe(source: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [
            os.fspath(_packaged_tools().ffprobe_path),
            "-v",
            "error",
            *arguments,
            "--",
            os.fspath(source),
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def _independent_reading(source: Path) -> tuple[float | None, str, str]:
    """Duration, picture and sound, each read by a query shaped unlike the module's.

    `-select_streams` picks the stream rather than the parser doing it, and the
    csv output has no keys at all, so nothing about the module's own parsing is
    reused.

    The absences are spelled differently by the two output formats, measured: the
    JSON document the module reads simply has no `duration` key for a PNG, while
    the plain format prints `N/A` — and the keyless csv prints an empty line for a
    stream that does not exist. Which is the point of reading it twice: an
    absence that arrives as text in one form and as a missing key in the other is
    exactly where a parser can be wrong in one place and look right.
    """
    stated = _ffprobe(
        source, "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1"
    )
    if stated == ABSENT_IN_PLAIN_OUTPUT:
        stated = ""
    picture = _ffprobe(
        source,
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height",
        "-of",
        "csv=p=0",
    )
    sound = _ffprobe(
        source, "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0"
    )
    return (float(stated) if stated else None), picture, sound


@pytest.fixture(scope="session")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The materials of the acceptance table, built by the packaged ffmpeg.

    Built rather than committed: a media file in the repository would be a
    binary nobody can review, and one built here is known to be exactly what the
    shipped encoder produces. Measured, the whole set takes about a second.

    They live under a directory whose name carries every character the plan calls
    out, so every judgement in this file is reached through a path a shell would
    mangle — and nothing here goes through a shell.
    """
    tools = _packaged_tools()
    ffmpeg = tools.ffmpeg_path
    directory = tmp_path_factory.mktemp("t7-media") / SPECIAL_DIRECTORY_NAME
    directory.mkdir()
    files = {
        "sound": directory / "sound.mp4",
        "silent": directory / "silent.mp4",
        "mute": directory / "mute.mp4",
        "audio": directory / "audio.m4a",
        "picture": directory / "image.png",
        "corrupt": directory / "corrupt.mp4",
        "piped": directory / "pipe_av.mkv",
        "photo": directory / "shot.jpg",
        "silent_audio": directory / "silent.m4a",
        "subtitles": directory / "only-text.srt",
        "oversized_frame": directory / "huge.png",
        "one_pixel": directory / "one-pixel.bmp",
        "unusable_frame": directory / "zero-width.bmp",
        "nameless_codec": directory / "unknown-codec.mp4",
        "seekable_matroska": directory / "seekable.mka",
        "too_long": directory / "too-long.mka",
        "faststart": directory / "faststart.mp4",
        "truncated": directory / "faststart-half.mp4",
        "planted": directory / "planted.mp4",
        "lead_silence": directory / "lead-silence.m4a",
    }
    # 1. A video with sound: the ordinary import, and the only one that reaches
    #    every parsed field in one pass.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s=640x360:r=25:d={SOUND_SECONDS}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={SOUND_SECONDS}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        os.fspath(files["sound"]),
    )
    # 2. A video whose sound track is digital silence: having a track and having
    #    sound are different facts, and only the measuring pass can tell them
    #    apart.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=1280x720:r=30:d={SILENT_SECONDS}",
        "-f",
        "lavfi",
        "-t",
        SILENT_SECONDS,
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        os.fspath(files["silent"]),
    )
    # 3. A video with no sound track at all: the shape that must not pay for a
    #    measuring pass.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        f"color=c=red:s=480x854:r=24:d={MUTE_SECONDS}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        os.fspath(files["mute"]),
    )
    # 4. Sound only, audible: the kind with no frame size to state.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=330:duration={AUDIO_SECONDS}",
        "-c:a",
        "aac",
        os.fspath(files["audio"]),
    )
    # 5. A still picture, in the container family that demuxes as `*_pipe`.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "color=c=green:s=800x600",
        "-frames:v",
        "1",
        os.fspath(files["picture"]),
    )
    # 6. The first bytes of a real video: the header never arrives, so ffprobe
    #    cannot describe it at all.
    _truncate_to(files["sound"], files["corrupt"], 5000 / files["sound"].stat().st_size)
    # 7. Matroska written to a pipe. ffmpeg cannot seek back to fill the
    #    duration in, so a real video arrives stating none — the shape that was
    #    filed as a still picture before the container decided the kind.
    with files["piped"].open("wb") as sink:
        subprocess.run(
            [
                os.fspath(ffmpeg),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=white:s=320x240:r=25:d={PIPED_SECONDS}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=550:duration={PIPED_SECONDS}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                "-f",
                "matroska",
                "pipe:1",
            ],
            stdout=sink,
            stderr=subprocess.PIPE,
            check=True,
        )
    # 8. A JPEG: container `image2`, and it states a duration of 0.04 s. The
    #    other direction of the same defect — filed as a 40 ms video while the
    #    duration decided the kind.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "color=c=orange:s=800x600",
        "-frames:v",
        "1",
        os.fspath(files["photo"]),
    )
    # Sound only, and silent throughout: a state `Material` forbids outright.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-t",
        SILENT_SECONDS,
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-c:a",
        "aac",
        os.fspath(files["silent_audio"]),
    )
    # A file ffprobe reads happily and that carries neither picture nor sound.
    files["subtitles"].write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n", encoding="utf-8")
    # A picture past the frame limit, which costs three kilobytes to make.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={OVERSIZED_FRAME_WIDTH}x{OVERSIZED_FRAME_HEIGHT}",
        "-frames:v",
        "1",
        os.fspath(files["oversized_frame"]),
    )
    # A one-pixel BMP, and the same file with its stated width set to zero.
    header = b"BM" + struct.pack("<I", 58) + b"\x00" * 4 + struct.pack("<I", 54)
    information = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 96, 96, 0, 0)
    files["one_pixel"].write_bytes(header + information + b"\x00\x00\xff\x00")
    _state_no_frame_size(files["one_pixel"], files["unusable_frame"])
    # A video stream ffprobe cannot name.
    _state_no_codec_name(files["sound"], files["nameless_codec"])
    # Matroska written to a file, which does state its duration, and a copy
    # whose stated duration is past the limit.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=330:duration={AUDIO_SECONDS}",
        "-c:a",
        "aac",
        os.fspath(files["seekable_matroska"]),
    )
    _state_a_longer_duration(files["seekable_matroska"], files["too_long"], TOO_LONG_SECONDS)
    # The same video with its header at the front, and half of that: the shape
    # whose facts describe a whole material that is not all there.
    _encode(
        ffmpeg,
        "-i",
        os.fspath(files["sound"]),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        os.fspath(files["faststart"]),
    )
    _truncate_to(files["faststart"], files["truncated"], TRUNCATION_FRACTION)
    # Silence first, then a tone: the only shape that makes silencedetect *close*
    # a span. Without it no material in this file produces `lavfi.silence_end` at
    # all — measured, the pattern for it could be replaced with one that never
    # matches and all 49 tests here still passed, the sibling file's fixtures
    # being what actually covered it.
    _encode(
        ffmpeg,
        "-f",
        "lavfi",
        "-t",
        LEAD_SILENCE_SECONDS,
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={LEAD_TONE_SECONDS}",
        "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1[out]",
        "-map",
        "[out]",
        "-c:a",
        "aac",
        os.fspath(files["lead_silence"]),
    )
    # And the same video carrying a tag that states silence.
    _encode(
        ffmpeg,
        "-i",
        os.fspath(files["sound"]),
        "-c",
        "copy",
        "-metadata",
        f"comment={PLANTED_STATEMENT}",
        os.fspath(files["planted"]),
    )
    return files


@pytest.fixture
def tools() -> PackagedMediaTools:
    return _packaged_tools()


# One row per material of the acceptance table: the kind, the duration in
# milliseconds, the frame size, the two codec names, whether anything is audible,
# and whether a loudness is stated. Spelled out here rather than derived, so a
# change in any single cell is a change to this table.
JUDGEMENTS: list[
    tuple[
        str,
        ProbedMaterialKind,
        int | None,
        tuple[int | None, int | None],
        str | None,
        str | None,
        bool,
        float | None,
    ]
] = [
    ("sound", ProbedMaterialKind.VIDEO, SOUND_MS, (640, 360), "h264", "aac", True, TONE_LUFS),
    ("silent", ProbedMaterialKind.VIDEO, SILENT_MS, (1280, 720), "h264", "aac", False, None),
    ("mute", ProbedMaterialKind.VIDEO, MUTE_MS, (480, 854), "h264", None, False, None),
    ("audio", ProbedMaterialKind.AUDIO, AUDIO_MS, (None, None), None, "aac", True, TONE_LUFS),
    (
        "lead_silence",
        ProbedMaterialKind.AUDIO,
        LEAD_SILENCE_MS,
        (None, None),
        None,
        "aac",
        True,
        LEAD_SILENCE_LUFS,
    ),
    ("picture", ProbedMaterialKind.IMAGE, None, (800, 600), "png", None, False, None),
    ("photo", ProbedMaterialKind.IMAGE, None, (800, 600), "mjpeg", None, False, None),
]

# The two of the eight that are refused, and what by. A rejection is the whole
# judgement for these, so they cannot sit in the table above.
REFUSALS: list[tuple[str, MaterialProbeRejection]] = [
    ("corrupt", MaterialProbeRejection.UNDECODABLE),
    ("piped", MaterialProbeRejection.UNUSABLE_DURATION),
]


class TestTheJudgementTable:
    """Eight real materials, each judged to the cell.

    The six that produce facts are asserted field by field against exact
    figures; the two that are refused are asserted against their reason. Neither
    kind of row is derived from the module's own output — the numbers are the
    ones a second ffprobe query states, and they are written down here as well so
    that a change in either has to be a change to this file.
    """

    @pytest.mark.parametrize(
        ("name", "kind", "duration_ms", "frame", "video_codec", "audio_codec", "audible", "lufs"),
        JUDGEMENTS,
        ids=[row[0] for row in JUDGEMENTS],
    )
    def test_one_material_is_judged_to_the_cell(
        self,
        media: dict[str, Path],
        tools: PackagedMediaTools,
        name: str,
        kind: ProbedMaterialKind,
        duration_ms: int | None,
        frame: tuple[int | None, int | None],
        video_codec: str | None,
        audio_codec: str | None,
        audible: bool,
        lufs: float | None,
    ) -> None:
        facts = probe_material(tools, media[name])
        assert facts.kind is kind
        assert facts.duration_ms == duration_ms
        assert (facts.width, facts.height) == frame
        assert (facts.video_codec, facts.audio_codec) == (video_codec, audio_codec)
        assert facts.has_audio is audible
        if lufs is None:
            assert facts.audio_loudness_lufs is None
        else:
            assert facts.audio_loudness_lufs == pytest.approx(lufs, abs=TONE_LUFS_TOLERANCE)

    @pytest.mark.parametrize("name", [row[0] for row in JUDGEMENTS])
    def test_a_second_query_states_the_same_values(
        self, media: dict[str, Path], tools: PackagedMediaTools, name: str
    ) -> None:
        """The facts, compared against readings the module's own parser never touched.

        Every assertion here has `facts` on one side and the second query on the
        other — deliberately not the table above, which would make this a check
        of one written-down row against ffprobe and say nothing about the parsing
        in between. The first version of this test did exactly that.

        What it adds over the table is that nothing here was written down by
        hand, so it holds for a material added later without anyone having to
        look a figure up. Measured with the production module's frame edges
        swapped: five of the six rows above fail and all five of these do — the
        table catches that one too, because no material in it is square, which is
        a property of the fixtures rather than of the assertion.

        `-select_streams` moves the stream selection out of the parser and the
        keyless csv output removes the field names, so nothing about how the
        module reads its answer is reused here.
        """
        facts = probe_material(tools, media[name])
        stated, picture, sound = _independent_reading(media[name])
        assert sound == (facts.audio_codec or "")
        if facts.kind is ProbedMaterialKind.AUDIO:
            assert picture == ""
            assert (facts.width, facts.height) == (None, None)
        else:
            assert picture == f"{facts.video_codec},{facts.width},{facts.height}"
        if facts.kind is ProbedMaterialKind.IMAGE:
            # A JPEG states 0.04 s and a PNG states nothing at all — and both are
            # asserted, because "the duration is ignored for a picture" is only
            # shown by a picture that states one. The comment used to say this
            # while the test read `stated` and threw it away.
            assert facts.duration_ms is None
            assert stated == (PHOTO_STATED_SECONDS if name == "photo" else None)
        else:
            assert stated is not None
            assert round(stated * 1000) == facts.duration_ms
        if facts.audio_loudness_lufs is not None:
            assert facts.audio_loudness_lufs == pytest.approx(
                _independent_loudness_lufs(media[name]), abs=SUMMARY_LUFS_TOLERANCE
            )

    @pytest.mark.parametrize(("name", "rejection"), REFUSALS, ids=[row[0] for row in REFUSALS])
    def test_a_refused_material_is_refused_for_the_stated_reason(
        self,
        media: dict[str, Path],
        tools: PackagedMediaTools,
        name: str,
        rejection: MaterialProbeRejection,
    ) -> None:
        """Both refusals come from a real tool rather than from a shape we wrote.

        The Matroska row is the sharper of the two: it is a real two-second video
        with a picture and a sound track, and the only thing wrong with it is
        that its container states no duration. A build that let the duration
        decide the kind would file it as a still picture and **succeed** — so the
        refusal, and not a kind assertion, is what says that defect is gone.
        """
        with pytest.raises(MaterialProbeRejected) as excinfo:
            probe_material(tools, media[name])
        assert excinfo.value.rejection is rejection

    def test_the_digest_is_the_files_own_bytes(
        self, media: dict[str, Path], tools: PackagedMediaTools
    ) -> None:
        """Hashed a second time by the standard library, over every material.

        One material would leave the chunk loop tested at one length. These span
        two and a half kilobytes to thirty-two, all under the 1 MiB chunk, which
        the sibling file covers at three chunks and at the boundaries.
        """
        for name, *_ in JUDGEMENTS:
            source = media[name]
            facts = probe_material(tools, source)
            assert facts.content_digest == hashlib.sha256(source.read_bytes()).hexdigest()

    def test_no_two_materials_share_a_digest(
        self, media: dict[str, Path], tools: PackagedMediaTools
    ) -> None:
        """What the value is for: telling one import from another."""
        digests = {
            name: probe_material(tools, media[name]).content_digest for name, *_ in JUDGEMENTS
        }
        assert len(set(digests.values())) == len(digests)

    def test_a_copy_under_another_name_hashes_the_same(
        self, media: dict[str, Path], tools: PackagedMediaTools, tmp_path: Path
    ) -> None:
        """The other half of dedup: the same bytes filed twice must collide."""
        copy = tmp_path / "renamed-holiday.mp4"
        copy.write_bytes(media["sound"].read_bytes())
        assert (
            probe_material(tools, copy).content_digest
            == probe_material(tools, media["sound"]).content_digest
        )


def _material_from(facts: MaterialFacts) -> Material:
    """Build the domain's own model out of one probe's product.

    Every field `Material` needs and LE-07 does not fill is stated here as the
    absence it is: speech is LE-14's, shot boundaries are LE-08's, and the
    description is LE-13's. Nothing is invented — `has_speech=False` with no
    segments and no transcript is what the model requires of a material nobody
    has listened to yet.

    `MaterialKind(facts.kind.value)` rather than a mapping table: the two enums
    are pinned to each other value for value by
    `TestTheKindsMatchTheDomain`, so a table would be a second thing to keep
    right.
    """
    return Material(
        material_id=MaterialId.new(),
        kind=MaterialKind(facts.kind.value),
        duration_ms=facts.duration_ms,
        width=facts.width,
        height=facts.height,
        content_digest=facts.content_digest,
        has_audio=facts.has_audio,
        audio_loudness_lufs=facts.audio_loudness_lufs,
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
        shot_boundaries_ms=(),
        ai_description=None,
        ai_tags=(),
        description_source=DescriptionSource.AI,
        described_at=None,
    )


# The two facts the domain model has nowhere to put. `Material` stores no codec
# names, so these stay on this side of the boundary — which is not a gap: they
# are what a later pass needs to decide whether a material has to be re-encoded
# before it can be cut, and that decision is made on the machine holding the
# file. Named here so that `TestEveryFactHasAHome` can be exact rather than
# approximate.
FACTS_WITH_NO_FIELD_IN_THE_DOMAIN = frozenset({"video_codec", "audio_codec"})


def _facts_read_by(function: str) -> frozenset[str]:
    """Which `MaterialFacts` fields a function in this file actually reads.

    Read out of this module's own syntax tree rather than by calling it, because
    what is being asserted is that the code mentions every field — a field left
    out would otherwise show up as a `Material` that still constructs, since
    every remaining field has a value.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return frozenset(
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "facts"
            )
    raise AssertionError(f"{function} not found in {Path(__file__).name}")


class TestFactsFillAMaterial:
    """The proof that what the executor learns is what the Control Plane stores.

    Matching the two layers' limits cannot show this. Two identical limits still
    admit a video filed as a still, an audio file carrying a frame size, or a
    silent sound-only file — every one of which `Material` refuses outright, and
    the first of which was a real defect here. Constructing the model is the
    assertion: it validates the combination, not the ranges.
    """

    @pytest.mark.parametrize("name", [row[0] for row in JUDGEMENTS])
    def test_one_materials_facts_construct_the_domain_model(
        self, media: dict[str, Path], tools: PackagedMediaTools, name: str
    ) -> None:
        facts = probe_material(tools, media[name])
        material = _material_from(facts)
        assert material.kind.value == facts.kind.value
        assert material.duration_ms == facts.duration_ms
        assert (material.width, material.height) == (facts.width, facts.height)
        assert material.content_digest == facts.content_digest
        assert material.has_audio is facts.has_audio
        assert material.audio_loudness_lufs == facts.audio_loudness_lufs

    def test_the_silent_sound_only_file_is_refused_before_it_reaches_the_model(
        self, media: dict[str, Path], tools: PackagedMediaTools
    ) -> None:
        """`Material` cannot hold `kind=AUDIO` with nothing audible, so nor does this.

        The alternative would be handing a caller facts that raise
        `InvalidMaterialModel` from inside a constructor it has no way to catch
        as a probe failure. Asserted here as well as in the sibling file because
        this is the only place both sides of that sentence are present.
        """
        with pytest.raises(MaterialProbeRejected) as excinfo:
            probe_material(tools, media["silent_audio"])
        assert excinfo.value.rejection is MaterialProbeRejection.SILENT_AUDIO

    def test_a_video_with_a_silent_track_still_becomes_a_material(
        self, media: dict[str, Path], tools: PackagedMediaTools
    ) -> None:
        """The same absence is legal for a video, and the model has to take it.

        `has_audio=False` with no loudness and no speech: the combination
        `Material` forbids for `AUDIO` and requires here.
        """
        material = _material_from(probe_material(tools, media["silent"]))
        assert material.kind is MaterialKind.VIDEO
        assert material.has_audio is False
        assert material.audio_loudness_lufs is None


class TestEveryFactHasAHome:
    """Nothing the probe learns may quietly go nowhere.

    A field added to `MaterialFacts` later has to be either stored or explicitly
    kept on this side; the failure mode without this is a fact produced at cost
    and dropped on the floor, which nothing else would notice.
    """

    def test_every_field_is_either_stored_or_listed_as_staying_here(self) -> None:
        produced = {field.name for field in dataclasses.fields(MaterialFacts)}
        assert _facts_read_by("_material_from") | FACTS_WITH_NO_FIELD_IN_THE_DOMAIN == produced

    def test_the_fields_that_stay_here_really_have_no_field_in_the_domain(self) -> None:
        """The exemption list is checked against the model rather than trusted.

        Left unchecked it would be a place to put anything inconvenient — and if
        `Material` ever gains a codec field, the list has to stop naming it.
        """
        stored = {field.name for field in dataclasses.fields(Material)}
        assert FACTS_WITH_NO_FIELD_IN_THE_DOMAIN.isdisjoint(stored)


class TestTheKindsMatchTheDomain:
    """The executor may not import the domain, so the two enums are pinned here.

    `ProbedMaterialKind` is declared beside the probe precisely so that
    `executor/` never depends on `control_plane/`; the cost of that is two
    declarations of one set of values, and this is what keeps them one set.
    """

    def test_the_members_and_the_values_are_identical(self) -> None:
        assert [member.name for member in ProbedMaterialKind] == [
            member.name for member in MaterialKind
        ]
        assert [member.value for member in ProbedMaterialKind] == [
            member.value for member in MaterialKind
        ]

    def test_every_probed_kind_names_a_domain_kind(self) -> None:
        """Which is what `_material_from` relies on when it converts by value."""
        for member in ProbedMaterialKind:
            assert MaterialKind(member.value).name == member.name


class TestTheLimitsMatchTheDomain:
    """Probing that accepted more than `Material` can hold would produce facts nobody can store."""

    def test_the_duration_limit_is_the_domains(self) -> None:
        assert MAX_MATERIAL_DURATION_MS == domain_material.MAX_MATERIAL_DURATION_MS

    def test_the_frame_limit_is_the_domains(self) -> None:
        assert MAX_MATERIAL_DIMENSION == domain_material.MAX_MATERIAL_DIMENSION


def _crashing_probe(directory: Path, ffprobe: Path) -> Path:
    """The real ffprobe, wrapped in a script that dies from a signal afterwards.

    A signalled child is the one shape the packaged tool cannot be asked for on
    demand — what can be done is a race, killing the real ffprobe from a watchdog
    thread the moment it appears, which is how this was first shown reachable at
    all (6 trials out of 6, with a 1000-track file to widen the window). A race
    is not a test, so the wrapper makes it deterministic instead.

    Running the real tool first is the point, not decoration. The answer on
    stdout is a complete, legal reading of a real file — so what this pins is
    that the return code is checked **before** the answer is trusted. A script
    that only killed itself could not: with no output at all, refusing it says
    nothing about which of the two the code looked at.
    """
    path = directory / "ffprobe"
    path.write_text(
        f'#!/bin/sh\n"{os.fspath(ffprobe)}" "$@"\nkill -9 $$\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


class TestEveryRejectionIsReachable:
    """Each member of the closed reason set, provoked once.

    A member nothing can produce is a promise to the material library that
    cannot be kept — and the library's whole job with these is to tell the user
    what to do next, one instruction per member. This line has already found
    members that no test reached and a `-k` filter that hid the test that did.

    The counts, counted rather than rounded: **11 members have a real file
    behind them, 8 of those are read by the packaged tools, and 12 need no stub**.
    `unreadable` and `unsafe_path` have no file to have — a path that is not
    there and a path that is not absolute; `source_not_at_rest` and
    `file_too_large` have a real file that no tool ever opens, the first being
    the public window check and the second refused on its `st_size` before either
    tool runs. `workspace_unusable` is about this module's own scratch space and
    has no file either. The thirteenth, `probe_crashed`, wraps the real ffprobe;
    see `_crashing_probe`.
    """

    def _provoke(
        self,
        rejection: MaterialProbeRejection,
        media: dict[str, Path],
        tools: PackagedMediaTools,
        tmp_path: Path,
    ) -> None:
        if rejection is MaterialProbeRejection.UNREADABLE:
            probe_material(tools, tmp_path / "not-imported-yet.mp4")
        elif rejection is MaterialProbeRejection.UNSAFE_PATH:
            probe_material(tools, Path("holiday.mp4"))
        elif rejection is MaterialProbeRejection.SOURCE_NOT_AT_REST:
            source = tmp_path / "still-downloading.mp4"
            source.write_bytes(media["sound"].read_bytes())
            approved = source.stat()
            descriptor = os.open(source, os.O_WRONLY)
            try:
                os.pwrite(descriptor, b"\x00" * 512, 0)
            finally:
                os.close(descriptor)
            require_source_unchanged(source, approved)
        elif rejection is MaterialProbeRejection.UNDECODABLE:
            probe_material(tools, media["corrupt"])
        elif rejection is MaterialProbeRejection.NO_USABLE_STREAM:
            probe_material(tools, media["subtitles"])
        elif rejection is MaterialProbeRejection.UNUSABLE_DURATION:
            probe_material(tools, media["piped"])
        elif rejection is MaterialProbeRejection.TOO_LONG:
            probe_material(tools, media["too_long"])
        elif rejection is MaterialProbeRejection.UNUSABLE_FRAME_SIZE:
            probe_material(tools, media["unusable_frame"])
        elif rejection is MaterialProbeRejection.FRAME_TOO_LARGE:
            probe_material(tools, media["oversized_frame"])
        elif rejection is MaterialProbeRejection.FILE_TOO_LARGE:
            probe_material(tools, _sparse_file(tmp_path, "raw.mov", MAX_SOURCE_FILE_BYTES + 1))
        elif rejection is MaterialProbeRejection.SILENT_AUDIO:
            probe_material(tools, media["silent_audio"])
        elif rejection is MaterialProbeRejection.PROBE_FAILED:
            probe_material(tools, media["nameless_codec"])
        elif rejection is MaterialProbeRejection.WORKSPACE_UNUSABLE:
            # Nothing to do with the material: the pass needs a few hundred bytes
            # of scratch space and this is a scratch root it cannot write in. A
            # full volume is the real shape and reaches the same line, measured
            # on a 4 MB ram disk as `TMPDIR`.
            locked = tmp_path / "locked-scratch"
            locked.mkdir(mode=0o500)
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(tempfile, "tempdir", os.fspath(locked))
                probe_material(tools, media["sound"])
        else:
            crashing = PackagedMediaTools(
                ffprobe_path=_crashing_probe(tmp_path, tools.ffprobe_path),
                ffmpeg_path=tools.ffmpeg_path,
            )
            read_stream_facts(crashing, media["sound"])

    @pytest.mark.parametrize(
        "rejection", list(MaterialProbeRejection), ids=[m.value for m in MaterialProbeRejection]
    )
    def test_a_member_is_produced_by_something(
        self,
        media: dict[str, Path],
        tools: PackagedMediaTools,
        tmp_path: Path,
        rejection: MaterialProbeRejection,
    ) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            self._provoke(rejection, media, tools, tmp_path)
        assert excinfo.value.rejection is rejection

    def test_the_reason_is_all_the_caller_is_told(
        self, media: dict[str, Path], tools: PackagedMediaTools
    ) -> None:
        """One fixed sentence, whatever the reason, and no path in it.

        The file being refused here has a name that would be quoted in
        ffprobe's own diagnostics, and the directory it sits in carries every
        awkward character; neither may appear.
        """
        with pytest.raises(MaterialProbeRejected) as excinfo:
            probe_material(tools, media["corrupt"])
        rendered = str(excinfo.value)
        assert rendered == "material probe rejected"
        assert SPECIAL_DIRECTORY_NAME not in rendered
        assert media["corrupt"].name not in rendered


class TestWhatARealFileCanStillHide:
    """Two things a successful probe does not promise, both measured here.

    Neither is a defect this module can fix and both are already handed on — to
    LE-16 and to LE-18 — so what these do is keep the evidence for them alive
    against a change that quietly alters either.
    """

    def test_a_truncated_faststart_file_reports_the_whole_materials_facts(
        self, media: dict[str, Path], tools: PackagedMediaTools
    ) -> None:
        """Half a download, and every field but the digest matches the complete file.

        `+faststart` puts the header at the front, so the header parses while the
        content it describes is missing. Nothing is wrong from the module's point
        of view: the duration, frame size and codecs are the complete
        material's, and only the digest differs — which is why a consumer must
        not read "different digest" as "different material", and why choosing a
        cut inside that duration can choose one with no frames behind it.
        """
        whole = probe_material(tools, media["faststart"])
        half = probe_material(tools, media["truncated"])
        assert media["truncated"].stat().st_size < media["faststart"].stat().st_size
        assert (half.kind, half.duration_ms) == (whole.kind, whole.duration_ms)
        assert (half.width, half.height) == (whole.width, whole.height)
        assert (half.video_codec, half.audio_codec) == (whole.video_codec, whole.audio_codec)
        assert half.content_digest != whole.content_digest

    def test_the_lead_silence_material_is_what_covers_the_closing_pattern(
        self, media: dict[str, Path], tools: PackagedMediaTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A silence span that closes, which nothing else in this file produces.

        `_silence_covers_the_track` reads two things off the report: that a span
        opened at zero, and that nothing closed it. Every other material here
        either states no silence at all or states silence to the end, so the
        closing line was never in a single report — measured, replacing its
        pattern with one that never matches left all 49 tests in this file green.

        Breaking the pattern here is what shows the material earns its place: the
        file becomes a sound-only material with nothing audible in it, which is a
        state `Material` forbids, so the verdict flips from "audible" to a
        refusal. Both halves are asserted, since the first alone would pass for a
        material that states no silence whatsoever.
        """
        facts = probe_material(tools, media["lead_silence"])
        assert facts.kind is ProbedMaterialKind.AUDIO
        assert facts.has_audio is True
        assert facts.audio_loudness_lufs == pytest.approx(
            LEAD_SILENCE_LUFS, abs=TONE_LUFS_TOLERANCE
        )
        monkeypatch.setattr(
            material_probe, "_SILENCE_END_PATTERN", re.compile(r"^never-matches$", re.MULTILINE)
        )
        with pytest.raises(MaterialProbeRejected) as excinfo:
            probe_material(tools, media["lead_silence"])
        assert excinfo.value.rejection is MaterialProbeRejection.SILENT_AUDIO

    def test_a_tag_stating_silence_does_not_silence_a_real_video(
        self, media: dict[str, Path], tools: PackagedMediaTools
    ) -> None:
        """The findings are taken off a channel the file cannot write into.

        The sibling file proves this for sound-only files and for a tag *name*
        forging a whole line. This is the ordinary case on the ordinary
        container: anything downloaded can carry this comment, and ffmpeg prints
        every tag it reads.
        """
        facts = probe_material(tools, media["planted"])
        assert facts.has_audio is True
        assert facts.audio_loudness_lufs == pytest.approx(TONE_LUFS, abs=TONE_LUFS_TOLERANCE)


class TestTheAcceptanceCannotQuietlyStopRunning:
    """A skipped acceptance test looks green, so a missing toolchain is a failure.

    Every test in this file depends on the packaged pair being where the build
    cache puts it. If that stops being true the reason has to arrive as a
    failure naming the script that fixes it.
    """

    def test_missing_tooling_fails_rather_than_skipping(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("AUTOMATION_TOOL_BUILD_CACHE", os.fspath(tmp_path))
        with pytest.raises(AssertionError, match="prepare_video_runtime"):
            _packaged_tools()

    def test_the_materials_really_came_from_the_packaged_encoder(
        self, media: dict[str, Path]
    ) -> None:
        """The fixture is only worth anything if the files are real media.

        A silently failed encode would leave an empty file, which every
        assertion above would report as some other kind of problem.
        """
        for name, source in media.items():
            assert source.stat().st_size > 0, name
