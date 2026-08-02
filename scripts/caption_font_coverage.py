#!/usr/bin/env python3
"""Measure registered caption faces across the Unicode 17 Han blocks.

The report contains counts and code-point labels only. It never emits the
caption text or a filesystem path, so it is safe to keep as build evidence.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from fontTools.ttLib import TTFont


class UnicodeBlock(NamedTuple):
    key: str
    start: int
    end: int

    @property
    def slots(self) -> int:
        return self.end - self.start + 1


# Unicode 17.0, chapter 18.1.  The order follows code-point order, including
# Extension I between F and G.  Slots deliberately include currently
# unassigned values: the purpose of this first audit is to quantify exactly
# what the package can draw, without inheriting the host Python's Unicode
# database version or silently changing when that runtime upgrades.
CJK_BLOCKS = (
    UnicodeBlock("extension-a", 0x3400, 0x4DBF),
    UnicodeBlock("unified", 0x4E00, 0x9FFF),
    UnicodeBlock("extension-b", 0x20000, 0x2A6DF),
    UnicodeBlock("extension-c", 0x2A700, 0x2B73F),
    UnicodeBlock("extension-d", 0x2B740, 0x2B81F),
    UnicodeBlock("extension-e", 0x2B820, 0x2CEAF),
    UnicodeBlock("extension-f", 0x2CEB0, 0x2EBEF),
    UnicodeBlock("extension-i", 0x2EBF0, 0x2EE5F),
    UnicodeBlock("extension-g", 0x30000, 0x3134F),
    UnicodeBlock("extension-h", 0x31350, 0x323AF),
    UnicodeBlock("extension-j", 0x323B0, 0x3347F),
)

_FONT_KEY = re.compile(r"^[a-z][a-z0-9-]{0,63}\Z")
_CODEPOINT = re.compile(r"^U\+([0-9A-F]{4,6})\Z")


def _block_measurement(codepoints: frozenset[int], block: UnicodeBlock) -> dict[str, int]:
    covered = sum(1 for value in codepoints if block.start <= value <= block.end)
    return {"covered": covered, "missing": block.slots - covered, "slots": block.slots}


def measure_codepoints(
    faces: Mapping[str, frozenset[int]], *, sample_codepoints: Sequence[int] = ()
) -> dict[str, object]:
    """Return deterministic per-face and union coverage without exposing text."""
    ordered_faces = dict(sorted(faces.items()))
    per_face = {
        key: {
            block.key: _block_measurement(codepoints, block) for block in CJK_BLOCKS
        }
        for key, codepoints in ordered_faces.items()
    }
    union = frozenset().union(*ordered_faces.values()) if ordered_faces else frozenset()
    samples = {
        f"U+{value:04X}": [
            key for key, codepoints in ordered_faces.items() if value in codepoints
        ]
        for value in sample_codepoints
    }
    return {
        "faces": per_face,
        "samples": samples,
        "union": {block.key: _block_measurement(union, block) for block in CJK_BLOCKS},
    }


def read_cmap(path: Path) -> frozenset[int]:
    """Read the best Unicode cmap and reject a missing or non-font input."""
    if not path.is_file():
        raise ValueError("caption font coverage input is not a file")
    try:
        with TTFont(path, lazy=True) as font:
            cmap = font.getBestCmap()
    except Exception as error:  # fontTools has several parser-specific errors.
        raise ValueError("caption font coverage input is not a readable font") from error
    if not cmap:
        raise ValueError("caption font coverage input has no Unicode cmap")
    return frozenset(cmap)


def _font_argument(value: str) -> tuple[str, Path]:
    key, separator, raw_path = value.partition("=")
    if not separator or _FONT_KEY.fullmatch(key) is None or not raw_path:
        raise argparse.ArgumentTypeError("font must be KEY=PATH with a registered-style key")
    return key, Path(raw_path)


def _sample_argument(value: str) -> int:
    match = _CODEPOINT.fullmatch(value)
    if match is None:
        raise argparse.ArgumentTypeError("sample must be an uppercase U+XXXX code point")
    codepoint = int(match.group(1), 16)
    if codepoint > 0x10FFFF:
        raise argparse.ArgumentTypeError("sample is outside Unicode")
    return codepoint


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", action="append", required=True, type=_font_argument)
    parser.add_argument("--sample", action="append", default=[], type=_sample_argument)
    arguments = parser.parse_args(argv)
    supplied = dict(arguments.font)
    if len(supplied) != len(arguments.font):
        parser.error("font keys must be unique")
    report = measure_codepoints(
        {key: read_cmap(path) for key, path in supplied.items()},
        sample_codepoints=arguments.sample,
    )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
