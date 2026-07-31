#!/usr/bin/env python3
"""BM-16 per-item visual evidence tests.

Digest diversity proves that bytes changed, not that a person can inspect the
catalog. These tests pin the missing content-level boundary before the heavy
134-item Chromium sweep is rerun: reject a blank frame, retain measurable pixel
facts, and assemble every item into its Chinese-category contact sheet.
"""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from run_bm_16_acceptance import (
    FrameVisualRejected,
    build_category_contact_sheets,
    build_component_preview_html,
    component_preview_capture_plan,
    measure_frame_visuals,
)


def _write_png(
    path: Path, *, width: int, height: int, pixels: list[tuple[int, int, int]]
) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", checksum)
        )

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for red, green, blue in pixels[y * width : (y + 1) * width]:
            rows.extend((red, green, blue))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows)))
        + chunk(b"IEND", b"")
    )


class FrameVisualEvidenceTests(unittest.TestCase):
    def test_component_fragment_is_exercised_inside_a_fail_closed_host(self) -> None:
        fragment = """
<style>
  .blend-difference { mix-blend-mode: difference; color: white; }
</style>
""".strip()

        preview = build_component_preview_html(
            name="caption-blend-difference",
            source=fragment,
        )

        self.assertIn(fragment, preview)
        self.assertIn('data-composition-id="bm16-component-preview"', preview)
        self.assertIn('class="blend-difference bm16-hero"', preview)
        self.assertIn("mixBlendMode === 'difference'", preview)
        self.assertIn("document.body.replaceChildren()", preview)
        self.assertNotIn("http://", preview)
        self.assertNotIn("https://", preview)

    def test_unknown_component_fragment_is_rejected_instead_of_shown_on_a_generic_card(
        self,
    ) -> None:
        with self.assertRaisesRegex(FrameVisualRejected, "preview contract"):
            build_component_preview_html(
                name="new-passive-effect",
                source="<style>.new-passive-effect { opacity: .5 }</style>",
            )

    def test_complete_component_document_only_receives_a_uniform_backdrop(self) -> None:
        document = """<!doctype html>
<html><head></head><body>
<div data-composition-id="caption" data-duration="3">VISIBLE</div>
<script>window.__timelines = {caption: "sentinel"};</script>
</body></html>"""

        preview = build_component_preview_html(
            name="caption-clip-wipe",
            source=document,
        )

        self.assertIn("VISIBLE", preview)
        self.assertIn('window.__timelines = {caption: "sentinel"};', preview)
        self.assertIn("background:#10151d!important", preview)
        self.assertNotIn("bm16-component-preview", preview)

    def test_complete_component_samples_its_timeline_instead_of_one_blind_point(
        self,
    ) -> None:
        source = """<!doctype html><html><body>
<div data-composition-id="caption" data-duration="8"
     data-width="1920" data-height="1080"></div>
</body></html>"""

        plan = component_preview_capture_plan(source)

        self.assertEqual(plan["frameCountPerSample"], 1)
        self.assertEqual(plan["sampleMillis"], [1_600, 3_200, 4_800, 6_400])
        self.assertEqual(
            plan["canvas"],
            {
                "deviceScaleFactor": 1,
                "height": 1080,
                "width": 1920,
            },
        )

    def test_passive_fragment_needs_one_frame_not_fake_motion(self) -> None:
        plan = component_preview_capture_plan("<style>.vignette{opacity:.7}</style>")

        self.assertEqual(plan["frameCountPerSample"], 1)
        self.assertEqual(plan["sampleMillis"], [0])
        self.assertEqual(plan["canvas"]["width"], 1920)
        self.assertEqual(plan["canvas"]["height"], 1080)

    def test_all_twenty_five_locked_components_have_a_preview_contract(self) -> None:
        components = (
            Path(__file__).resolve().parents[1]
            / "vendor"
            / "hyperframes"
            / "registry"
            / "components"
        )
        rendered: list[str] = []
        for directory in sorted(path for path in components.iterdir() if path.is_dir()):
            manifest = json.loads(
                (directory / "registry-item.json").read_text(encoding="utf-8")
            )
            source_path = directory / manifest["files"][0]["path"]

            preview = build_component_preview_html(
                name=manifest["name"],
                source=source_path.read_text(encoding="utf-8"),
            )

            self.assertIn("<!doctype html>", preview.lower())
            rendered.append(manifest["name"])
        self.assertEqual(len(rendered), 25)

    def test_a_uniform_frame_is_not_content_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bm16-visual-red-") as raw:
            frame = Path(raw) / "blank.png"
            _write_png(
                frame,
                width=320,
                height=180,
                pixels=[(16, 24, 32)] * (320 * 180),
            )

            with self.assertRaisesRegex(FrameVisualRejected, "uniform"):
                measure_frame_visuals(frame)

    def test_visible_content_keeps_bounded_pixel_facts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bm16-visual-green-") as raw:
            frame = Path(raw) / "panel.png"
            pixels = [
                (42, 157, 143) if 40 <= x < 280 and 30 <= y < 150 else (16, 24, 32)
                for y in range(180)
                for x in range(320)
            ]
            _write_png(frame, width=320, height=180, pixels=pixels)

            measured = measure_frame_visuals(frame)

        self.assertEqual(measured["width"], 320)
        self.assertEqual(measured["height"], 180)
        self.assertGreater(measured["lumaStandardDeviation"], 1.0)
        self.assertGreater(measured["nonDominantFraction"], 0.1)
        self.assertNotIn(str(frame), repr(measured))

    def test_contact_sheets_cover_every_named_item_once(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bm16-sheets-") as raw:
            root = Path(raw)
            frames = root / "frames"
            frames.mkdir()
            items = [
                ("alpha", "字幕", "#1d3557"),
                ("beta", "字幕", "#e63946"),
                ("gamma", "数据与地图", "#2a9d8f"),
            ]
            for name, _category, color in items:
                red, green, blue = bytes.fromhex(color.removeprefix("#"))
                _write_png(
                    frames / f"{name}.png",
                    width=320,
                    height=180,
                    pixels=[(red, green, blue)] * (320 * 180),
                )

            sheets = build_category_contact_sheets(
                frames=frames,
                catalog_categories={name: category for name, category, _ in items},
                output=root / "sheets",
            )

            self.assertEqual(set(sheets), {"字幕", "数据与地图"})
            self.assertTrue(all(path.is_file() for path in sheets.values()))
            self.assertEqual(
                sorted(path.name for path in frames.glob("*.png")),
                ["alpha.png", "beta.png", "gamma.png"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
