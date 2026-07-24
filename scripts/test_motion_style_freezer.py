#!/usr/bin/env python3
"""BM-07 deterministic tests for frame.md validation and RenderJob freezing."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/motion-authoring"))

from motion_style_freezer import (  # noqa: E402
    BrandTokens,
    MotionStyleFreezeRejected,
    PreviewContent,
    freeze_motion_style,
    load_locked_style_sources,
    validate_frame_markdown,
)

CONTRACT = ROOT / "contracts/video/motion-style-freeze.v1.json"
VENDOR_ROOT = ROOT / "vendor/hyperframes"
PRESET_ID = "blue-professional"
PRESET_FRAME = (
    VENDOR_ROOT
    / "skills/hyperframes-creative/frame-presets"
    / PRESET_ID
    / "FRAME.md"
)


def _write_brand_assets(workspace: Path) -> tuple[str, str]:
    font = workspace / "assets/fonts/AcmeSans-Regular.woff2"
    logo = workspace / "assets/brand/acme-logo.png"
    font.parent.mkdir(parents=True)
    logo.parent.mkdir(parents=True)
    font.write_bytes(b"wOF2" + b"\0" * 32)
    logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    return (
        font.relative_to(workspace).as_posix(),
        logo.relative_to(workspace).as_posix(),
    )


def _brand_frame(font_asset: str) -> str:
    original = PRESET_FRAME.read_text(encoding="utf-8")
    remixed = (
        original.replace("Space Grotesk", "Acme Sans")
        .replace("Inter", "Acme Sans")
        .replace("#1e2bfa", "#1234ab")
        .replace("#fdfae7", "#f2eadb")
    )
    return (
        remixed
        + "\n\n## Font loading\n\n"
        + "```html\n<style>\n"
        + '@font-face{font-family:"Acme Sans";font-weight:400;'
        + f'src:url("{font_asset}") format("woff2");}}\n'
        + "</style>\n```\n"
    )


class LockedStyleSourceTests(unittest.TestCase):
    def test_loads_exactly_twelve_digest_verified_public_sources(self) -> None:
        sources = load_locked_style_sources(
            contract_path=CONTRACT, vendor_root=VENDOR_ROOT
        )
        self.assertEqual(len(sources), 12)
        source = sources[PRESET_ID]
        self.assertEqual(
            source.sha256,
            hashlib.sha256(PRESET_FRAME.read_bytes()).hexdigest(),
        )
        self.assertEqual(source.upstream_version, "v0.7.68")

    def test_source_digest_drift_fails_closed(self) -> None:
        with TemporaryDirectory() as raw:
            contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
            contract["presets"][0]["sha256"] = "0" * 64
            path = Path(raw) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaises(MotionStyleFreezeRejected):
                load_locked_style_sources(
                    contract_path=path, vendor_root=VENDOR_ROOT
                )


class FrameMarkdownValidationTests(unittest.TestCase):
    def test_accepts_locked_schema_and_local_brand_assets(self) -> None:
        with TemporaryDirectory() as raw:
            workspace = Path(raw)
            font_asset, logo_asset = _write_brand_assets(workspace)
            frame = _brand_frame(font_asset)
            validate_frame_markdown(
                frame,
                workspace_root=workspace,
                brand_tokens=BrandTokens(
                    primary_color="#1234ab",
                    secondary_color="#f2eadb",
                    font_family="Acme Sans",
                    font_asset=font_asset,
                    logo_asset=logo_asset,
                ),
            )

    def test_rejects_missing_schema_remote_content_and_active_markup(self) -> None:
        with TemporaryDirectory() as raw:
            workspace = Path(raw)
            tokens = BrandTokens()
            for malicious in (
                "# no frontmatter",
                PRESET_FRAME.read_text(encoding="utf-8").replace(
                    "#fdfae7", "https://evil.example/style.css", 1
                ),
                PRESET_FRAME.read_text(encoding="utf-8") + "\n<script>alert(1)</script>",
            ):
                with (
                    self.subTest(marker=malicious[:24]),
                    self.assertRaises(MotionStyleFreezeRejected),
                ):
                    validate_frame_markdown(
                        malicious,
                        workspace_root=workspace,
                        brand_tokens=tokens,
                    )

    def test_rejects_missing_traversing_or_symlinked_assets(self) -> None:
        with TemporaryDirectory() as raw:
            workspace = Path(raw)
            outside = workspace.parent / f"{workspace.name}-outside.woff2"
            outside.write_bytes(b"wOF2" + b"\0" * 8)
            try:
                link = workspace / "font.woff2"
                link.symlink_to(outside)
                real = workspace / "real.woff2"
                real.write_bytes(b"wOF2" + b"\0" * 8)
                internal_link = workspace / "internal.woff2"
                internal_link.symlink_to(real)
                for asset in (
                    "../outside.woff2",
                    "missing.woff2",
                    "font.woff2",
                    "internal.woff2",
                ):
                    with (
                        self.subTest(asset=asset),
                        self.assertRaises(MotionStyleFreezeRejected),
                    ):
                        validate_frame_markdown(
                            _brand_frame(asset),
                            workspace_root=workspace,
                            brand_tokens=BrandTokens(
                                font_family="Acme Sans", font_asset=asset
                            ),
                        )
            finally:
                outside.unlink(missing_ok=True)


class RenderJobFreezeTests(unittest.TestCase):
    def test_freezes_stable_digests_and_reproduces_two_render_jobs(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            first_job = root / "job-a"
            second_job = root / "job-b"
            workspace.mkdir()
            first_job.mkdir()
            second_job.mkdir()
            font_asset, logo_asset = _write_brand_assets(workspace)
            frame = _brand_frame(font_asset)
            tokens = BrandTokens(
                primary_color="#1234ab",
                secondary_color="#f2eadb",
                font_family="Acme Sans",
                font_asset=font_asset,
                logo_asset=logo_asset,
            )
            preview = PreviewContent(
                headline="本周销售增长 38%",
                body="华东区和续费业务共同推动增长。",
            )

            first = freeze_motion_style(
                contract_path=CONTRACT,
                vendor_root=VENDOR_ROOT,
                workspace_root=workspace,
                render_job_root=first_job,
                style_preset_id=PRESET_ID,
                frame_markdown=frame,
                brand_tokens=tokens,
                preview_content=preview,
            )
            second = freeze_motion_style(
                contract_path=CONTRACT,
                vendor_root=VENDOR_ROOT,
                workspace_root=workspace,
                render_job_root=second_job,
                style_preset_id=PRESET_ID,
                frame_markdown=frame,
                brand_tokens=tokens,
                preview_content=preview,
            )

            self.assertEqual(first, second)
            self.assertEqual(first.style_preset_id, PRESET_ID)
            self.assertEqual(first.frame_artifact_path, "frame.md")
            self.assertEqual(first.brand_tokens_sha256, second.brand_tokens_sha256)
            self.assertEqual(first.frozen_frame_sha256, second.frozen_frame_sha256)
            self.assertEqual(
                (first_job / "frame.md").read_bytes(),
                (second_job / "frame.md").read_bytes(),
            )
            self.assertEqual(
                json.loads((first_job / "style-freeze.json").read_text(encoding="utf-8")),
                json.loads((second_job / "style-freeze.json").read_text(encoding="utf-8")),
            )
            for relative in (font_asset, logo_asset):
                self.assertEqual(
                    (first_job / relative).read_bytes(),
                    (second_job / relative).read_bytes(),
                )

    def test_rejects_unknown_style_and_nonempty_render_job_target(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            render_job = root / "job"
            workspace.mkdir()
            render_job.mkdir()
            (render_job / "existing").write_text("x", encoding="utf-8")
            with self.assertRaises(MotionStyleFreezeRejected):
                freeze_motion_style(
                    contract_path=CONTRACT,
                    vendor_root=VENDOR_ROOT,
                    workspace_root=workspace,
                    render_job_root=render_job,
                    style_preset_id="code-editorial",
                    frame_markdown=PRESET_FRAME.read_text(encoding="utf-8"),
                    brand_tokens=BrandTokens(),
                    preview_content=PreviewContent(headline="标题", body="正文"),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
