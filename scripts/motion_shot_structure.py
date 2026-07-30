#!/usr/bin/env python3
"""What shots a delivered film is made of, and whether any two are the same.

Route A renders one segment per shot and joins them, so a film's structure is
knowable exactly: the segment files the render left behind say how long each
shot is. Two things are done with that here.

**Print it.** The acceptance run states the shot table it actually produced, so
an evidence file can quote a machine's output instead of a person's memory. On
2026-07-29 the PC-10 evidence described eight 2.5-second shots while the kept
artifact has seven of 3/3/3/3/3/15/2 — the table had been transcribed from a
different run of the same brief, and nothing could see the difference because
nothing was producing a table to compare against.

**Refuse two shots that show the same picture.** This is PC-19's defect stated
directly: every template shot loaded the same composition and rendered the whole
of it, so all of them were the same footage. That film passed codec, canvas,
frame-count, duration and still-image checks — all of which ask about the file
rather than about whether the picture moves on.

Why midpoints rather than boundaries
------------------------------------
Sampling the first frame of each shot would call a healthy film a duplicate:
measured on the real artifact, all six template shots begin on the identical
background-only frame (luminance 47.26 to two decimals at 3.0, 6.0, 9.0 and
12.0 seconds) because each shot opens with its entry animation. The midpoint is
where a shot is showing what it is for.

Why not detect the cuts from the picture
----------------------------------------
Both obvious ways were measured on the real film and neither separates the
cases:

* scene detection (`select='gt(scene,0.3)'`) finds 2 of the 6 boundaries — the
  template shots share one blue background and differ only in the centred text,
  which does not move the score. Lowering the threshold overfits to this
  template;
* luminance jumps do not separate either: a real cut steps 2.61 while the entry
  animation inside a shot steps 2.37, so any threshold misjudges one side.

The segment durations are ground truth and need no threshold, which is why they
are what this module reads.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

# A shot shorter than this is not a shot; it is a rounding error or an empty
# segment that encoded anyway.
MINIMUM_SHOT_SECONDS: Final = 0.05

_SEGMENT_NAME: Final = re.compile(r"^segment-(\d+)\.mp4$")


class ShotStructureRejected(RuntimeError):
    """The film is not made of the shots it is supposed to be made of."""


@dataclass(frozen=True, slots=True)
class Shot:
    """One shot's place in the finished film."""

    index: int  # 1-based, as the printed table and any refusal name it
    start_seconds: float
    seconds: float

    @property
    def midpoint_seconds(self) -> float:
        return self.start_seconds + self.seconds / 2


def plan_shots(segment_seconds: Sequence[float]) -> list[Shot]:
    """Lay the segments end to end, which is what `-c copy` concatenation does."""
    if not segment_seconds:
        raise ShotStructureRejected(
            "the film reports no shots at all; a delivered film is joined from "
            "at least one segment"
        )
    shots: list[Shot] = []
    start = 0.0
    for index, seconds in enumerate(segment_seconds, start=1):
        if seconds < MINIMUM_SHOT_SECONDS:
            raise ShotStructureRejected(
                f"shot {index} is {seconds:.3f}s, shorter than the "
                f"{MINIMUM_SHOT_SECONDS}s below which a segment is not a shot"
            )
        shots.append(Shot(index=index, start_seconds=start, seconds=seconds))
        start += seconds
    return shots


def require_declared_shot_boundaries(
    *,
    declared_frames: Sequence[int],
    rendered_frames: Sequence[int],
    frames_per_second: int,
    tolerance_frames: int = 1,
) -> list[Shot]:
    """Compare the authored shot table with decoded segment boundaries.

    Checking each length alone is insufficient: two consecutive shots that are
    each one frame long put the third boundary two frames away from the answer.
    Starts and ends are therefore compared cumulatively. The returned table is
    built from the decoded counts, which is what the delivered film contains.
    """
    if (
        not declared_frames
        or len(declared_frames) != len(rendered_frames)
        or type(frames_per_second) is not int
        or frames_per_second <= 0
        or type(tolerance_frames) is not int
        or tolerance_frames < 0
        or any(type(value) is not int or value <= 0 for value in declared_frames)
        or any(type(value) is not int or value <= 0 for value in rendered_frames)
    ):
        raise ShotStructureRejected(
            "declared and rendered shot tables must have the same non-zero "
            "positive-integer shape"
        )
    declared_start = 0
    rendered_start = 0
    for index, (declared, rendered) in enumerate(
        zip(declared_frames, rendered_frames), start=1
    ):
        start_drift = abs(declared_start - rendered_start)
        if start_drift > tolerance_frames:
            raise ShotStructureRejected(
                f"shot {index} starts {start_drift} frames away from its "
                f"declared boundary; tolerance is {tolerance_frames}"
            )
        declared_start += declared
        rendered_start += rendered
        end_drift = abs(declared_start - rendered_start)
        if end_drift > tolerance_frames:
            raise ShotStructureRejected(
                f"shot {index} ends {end_drift} frames away from its "
                f"declared boundary; tolerance is {tolerance_frames}"
            )
    return plan_shots(
        [frames / frames_per_second for frames in rendered_frames]
    )


def refuse_duplicate_shots(shots: Sequence[Shot], digests: Sequence[str]) -> None:
    """No two shots may be showing the same picture at their midpoints.

    A known-legitimate false positive exists: the same part used twice with the
    same copy really would render two identical shots. It has not occurred, and
    a film that repeats a shot verbatim is worth stopping on anyway — so this
    refuses rather than warns, and names both shots so the reader can judge.
    """
    if len(digests) != len(shots):
        raise ShotStructureRejected(
            f"{len(digests)} frames were sampled for {len(shots)} shots; every "
            "shot has to be looked at or the ones skipped are unexamined"
        )
    seen: dict[str, Shot] = {}
    for shot, digest in zip(shots, digests):
        earlier = seen.get(digest)
        if earlier is not None:
            raise ShotStructureRejected(
                f"shot {earlier.index} (at {earlier.midpoint_seconds:.3f}s) and "
                f"shot {shot.index} (at {shot.midpoint_seconds:.3f}s) show the "
                "same picture. A film whose shots each re-rendered the whole "
                "composition looks exactly like this and passes every check "
                "that reads the container (PC-19)."
            )
        seen[digest] = shot


def describe_shots(shots: Sequence[Shot]) -> str:
    """The shot table, for the acceptance log and for evidence to quote."""
    lines = [
        f"  shot {shot.index}  start {shot.start_seconds:7.3f}s  "
        f"length {shot.seconds:7.3f}s"
        for shot in shots
    ]
    total = sum(shot.seconds for shot in shots)
    lines.append(f"  {len(shots)} shots, {total:.3f}s total")
    return "\n".join(lines)


def read_segment_seconds(segment_directory: Path, ffprobe: Path) -> list[float]:
    """How long each rendered segment is, in the order they were joined.

    Ordered by the index in the filename rather than by directory listing:
    `segment-010.mp4` sorts before `segment-9.mp4` as text, and a shot table in
    the wrong order would be worse than none.
    """
    numbered: list[tuple[int, Path]] = []
    for path in segment_directory.iterdir():
        matched = _SEGMENT_NAME.match(path.name)
        if matched is not None:
            numbered.append((int(matched.group(1)), path))
    if not numbered:
        raise ShotStructureRejected(
            f"no rendered segments under {segment_directory}; the render either "
            "did not run or cleaned up before its structure could be read"
        )
    return [_probe_seconds(path, ffprobe) for _, path in sorted(numbered)]


def _probe_seconds(video: Path, ffprobe: Path) -> float:
    probe = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "format=duration", "-of", "json", str(video),
        ],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return float(json.loads(probe.stdout)["format"]["duration"])


def require_distinct_shots(
    video: Path, ffmpeg: Path, shots: Sequence[Shot]
) -> list[str]:
    """Sample each shot where it is showing what it is for, and compare."""
    digests: list[str] = []
    with tempfile.TemporaryDirectory(prefix="automation-tool-shot-structure-") as raw:
        frames = Path(raw)
        for shot in shots:
            still = frames / f"shot-{shot.index:03}.png"
            subprocess.run(
                [str(ffmpeg), "-v", "error", "-ss", f"{shot.midpoint_seconds}",
                 "-i", str(video), "-frames:v", "1", "-y", str(still)],
                check=True, timeout=120,
            )
            digests.append(hashlib.sha256(still.read_bytes()).hexdigest())
    refuse_duplicate_shots(shots, digests)
    return digests


__all__ = [
    "MINIMUM_SHOT_SECONDS",
    "Shot",
    "ShotStructureRejected",
    "describe_shots",
    "plan_shots",
    "read_segment_seconds",
    "require_declared_shot_boundaries",
    "refuse_duplicate_shots",
    "require_distinct_shots",
]
