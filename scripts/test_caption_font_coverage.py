#!/usr/bin/env python3
"""LE-20 T1: deterministic evidence for the Chinese font expansion decision."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "scripts/caption_font_coverage.py"
CONTRACT = ROOT / "contracts/quality/caption-font-coverage.v1.json"


def _load_auditor():
    assert AUDITOR.is_file(), "scripts/caption_font_coverage.py is missing"
    spec = importlib.util.spec_from_file_location("caption_font_coverage", AUDITOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unicode_17_han_ranges_are_complete_ordered_and_disjoint() -> None:
    auditor = _load_auditor()
    assert [(block.key, block.start, block.end) for block in auditor.CJK_BLOCKS] == [
        ("extension-a", 0x3400, 0x4DBF),
        ("unified", 0x4E00, 0x9FFF),
        ("extension-b", 0x20000, 0x2A6DF),
        ("extension-c", 0x2A700, 0x2B73F),
        ("extension-d", 0x2B740, 0x2B81F),
        ("extension-e", 0x2B820, 0x2CEAF),
        ("extension-f", 0x2CEB0, 0x2EBEF),
        ("extension-i", 0x2EBF0, 0x2EE5F),
        ("extension-g", 0x30000, 0x3134F),
        ("extension-h", 0x31350, 0x323AF),
        ("extension-j", 0x323B0, 0x3347F),
    ]
    for previous, current in zip(auditor.CJK_BLOCKS, auditor.CJK_BLOCKS[1:]):
        assert previous.end < current.start


def test_measurement_reports_counts_gaps_and_sample_codepoints_without_text() -> None:
    auditor = _load_auditor()
    measured = auditor.measure_codepoints(
        {
            "base": frozenset({0x3400, 0x4E00, 0x20000}),
            "fallback": frozenset({0x20001, 0x2A700}),
        },
        sample_codepoints=(0x4E00, 0x20000, 0x20001, 0x2A700, 0x31350),
    )

    assert measured["faces"]["base"]["unified"]["covered"] == 1
    assert measured["faces"]["base"]["extension-b"]["missing"] == 42719
    assert measured["union"]["extension-b"]["covered"] == 2
    assert measured["union"]["extension-c"]["covered"] == 1
    assert measured["samples"] == {
        "U+4E00": ["base"],
        "U+20000": ["base"],
        "U+20001": ["fallback"],
        "U+2A700": ["fallback"],
        "U+31350": [],
    }
    assert "一" not in json.dumps(measured, ensure_ascii=False)


def test_locked_baseline_records_a_reproducible_supplement_required_decision() -> None:
    _load_auditor()
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert document["schemaVersion"] == 1
    assert document["unicodeVersion"] == "17.0.0"
    assert document["decision"] == "supplement-required"
    assert document["baseline"]["fontKey"] == "noto-sans-cjk-sc-regular"
    assert document["baseline"]["sha256"] == (
        "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
    )
    assert document["baseline"]["measurements"]["extension-b"] == {
        "covered": 2108,
        "slots": 42720,
    }
    assert document["baseline"]["measurements"]["extension-h"]["covered"] == 0
    assert document["baseline"]["measurements"]["extension-j"]["covered"] == 0
    assert document["acceptance"]["requiredFallbackBlocks"] == [
        "extension-b",
        "extension-c",
        "extension-d",
        "extension-e",
        "extension-f",
        "extension-i",
        "extension-g",
        "extension-h",
        "extension-j",
    ]


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print("caption font coverage tests passed")
    print(f"executed checks: {len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
