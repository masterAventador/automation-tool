#!/usr/bin/env python3
"""PC-13 tests: the CJK face records a part document needs, and where they go.

The question this probe answers is whether a part can keep its own Latin
typeface and still show Chinese, without editing one byte of the read-only
`vendor/hyperframes` submodule. The mechanism under test is the one the
offline catalog already uses for Latin: several `@font-face` rules sharing one
family name, separated by `unicode-range`, so the browser picks a face per
code point.

These tests cover the two pure pieces of that: reading which family names a
part actually asks for, and turning those names into face records the existing
`build_offline_motion_catalog.stylesheet_css` can emit unchanged. The browser
half is not assertable here -- it is measured against the real packaged
Chromium by `motion_cjk_font_probe.py --render`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts/motion_cjk_font_probe.py"
BUILDER = ROOT / "scripts/build_offline_motion_catalog.py"


def load_module(path: Path):
    assert path.is_file(), f"{path.relative_to(ROOT)} is missing"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_named_font_families_keeps_only_real_families() -> None:
    probe = load_module(PROBE)
    text = """
      #a { font-family: "Archivo Black", sans-serif; }
      #b { font-family: 'Space Mono', monospace; }
      #c { font-family: system-ui, -apple-system, "Helvetica Neue", Arial; }
      #d { font-family: var(--brand-face), serif; }
      #e { font-family: Inter, ui-sans-serif; }
    """
    families = probe.named_font_families(text)
    assert families == frozenset(
        {"Archivo Black", "Space Mono", "Helvetica Neue", "Arial", "Inter"}
    ), families


def test_named_font_families_reads_the_real_part() -> None:
    """`lt-bold-block` is the part the PC-13 acceptance renders."""
    probe = load_module(PROBE)
    source = ROOT / "vendor/hyperframes/registry/blocks/lt-bold-block/lt-bold-block.html"
    families = probe.named_font_families(source.read_text(encoding="utf-8"))
    assert families == frozenset({"Archivo Black", "Space Mono"}), families


def test_cjk_face_records_are_emitted_by_the_existing_generator() -> None:
    """The builder that writes the Latin `@font-face` blocks needs no change.

    If this holds, adding Chinese is a change to locked data and not to code:
    the same `stylesheet_css` that emits the Latin subsets emits the CJK face
    from a record of the same shape.
    """
    probe = load_module(PROBE)
    builder = load_module(BUILDER)
    records = probe.cjk_face_records(
        families=["Archivo Black", "Space Mono"],
        weights=[400, 700],
        artifact_path="offline-deps/fonts/woff2/noto-sans-sc/full-400.woff2",
    )
    assert len(records) == 4, records
    css = builder.stylesheet_css(
        {"localPath": "offline-deps/fonts/css/probe.css", "faces": records}
    )
    assert css.count("@font-face") == 4, css
    assert "font-family: 'Archivo Black';" in css
    assert "font-family: 'Space Mono';" in css
    assert "src: url(../woff2/noto-sans-sc/full-400.woff2) format('woff2');" in css
    assert "U+4E00-9FFF" in css
    # The Latin ranges must stay out of the CJK face, otherwise the part loses
    # the typeface it was designed with.
    assert "U+0000-00FF" not in css


def test_cjk_range_excludes_latin_and_covers_the_common_han_block() -> None:
    probe = load_module(PROBE)
    covered = probe.cjk_codepoints()
    for character in "字体中文测试，。！":
        assert ord(character) in covered, character
    for character in "AZaz09 .,-'·":
        assert ord(character) not in covered, character


def test_frame_seek_times_cover_the_whole_part() -> None:
    """A film of the part, not one still, is what proves the text survives motion."""
    probe = load_module(PROBE)
    times = probe.frame_seek_times(duration_seconds=4.8, fps=30)
    assert len(times) == 144, len(times)
    assert times[0] == 0.0
    assert abs(times[1] - 1 / 30) < 1e-9, times[1]
    # The last capture stays inside the part's own duration: seeking past the
    # end of a GSAP timeline replays its final state and reads as a frozen tail.
    assert times[-1] < 4.8, times[-1]
    assert probe.frame_seek_times(duration_seconds=1.0, fps=2) == [0.0, 0.5]


def test_windows_render_has_a_native_cdp_transport() -> None:
    """PC-13 must run on Windows, where POSIX fd 3/4 wiring does not exist."""
    probe = load_module(PROBE)
    assert hasattr(probe, "measure_document_windows")


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print("motion cjk font probe tests passed")
    print(f"executed checks: {len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
