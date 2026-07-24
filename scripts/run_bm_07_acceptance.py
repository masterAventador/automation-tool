#!/usr/bin/env python3
"""BM-07 locked builder/freezer and production-App acceptance entrypoint."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from run_bm_06_acceptance import run_desktop_acceptance
from run_vf_06_acceptance import pnpm_executable

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
VENDOR_ROOT = ROOT / "vendor/hyperframes"
CONTRACT = ROOT / "contracts/video/motion-style-freeze.v1.json"
BUILDER = (
    VENDOR_ROOT
    / "skills/faceless-explainer/scripts/build-frame.mjs"
)
sys.path.insert(0, str(ROOT / "tools/motion-authoring"))

from motion_style_freezer import (  # noqa: E402
    BrandTokens,
    PreviewContent,
    freeze_motion_style,
)


def _node_executable() -> str:
    executable = shutil.which("node")
    if executable is None:
        raise RuntimeError("BM-07 acceptance requires the project Node runtime")
    return executable


def _write_brand_workspace(project: Path) -> tuple[str, str]:
    tokens = project / "capture/extracted/tokens.json"
    captured_font = project / "capture/assets/fonts/AcmeSans-Regular.woff2"
    logo = project / "assets/brand/acme-logo.png"
    tokens.parent.mkdir(parents=True)
    captured_font.parent.mkdir(parents=True)
    logo.parent.mkdir(parents=True)
    tokens.write_text(
        json.dumps(
            {
                "colors": ["#f2eadb", "#1234ab", "#111111"],
                "fonts": [{"family": "Acme Sans", "weights": [400]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured_font.write_bytes(b"wOF2" + b"\0" * 64)
    logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    return "assets/fonts/AcmeSans-Regular.woff2", "assets/brand/acme-logo.png"


def run_locked_builder_and_freezer() -> None:
    with TemporaryDirectory(prefix="ebvs-bm07-") as raw:
        root = Path(raw)
        project = root / "project"
        first_job = root / "renderjob-a"
        second_job = root / "renderjob-b"
        project.mkdir()
        first_job.mkdir()
        second_job.mkdir()
        font_asset, logo_asset = _write_brand_workspace(project)
        subprocess.run(
            [
                _node_executable(),
                str(BUILDER),
                "--preset",
                "blue-professional",
                "--hyperframes",
                str(project),
            ],
            cwd=ROOT,
            check=True,
        )
        frame_path = project / "frame.md"
        if not frame_path.is_file():
            raise RuntimeError("locked builder did not produce frame.md")
        frame_markdown = frame_path.read_text(encoding="utf-8")
        lowered_frame = frame_markdown.lower()
        for expected in ("#1234ab", "#f2eadb", "acme sans", font_asset.lower()):
            if expected not in lowered_frame:
                raise RuntimeError(f"brand override missing from frame.md: {expected}")
        brand_tokens = BrandTokens(
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
            workspace_root=project,
            render_job_root=first_job,
            style_preset_id="blue-professional",
            frame_markdown=frame_markdown,
            brand_tokens=brand_tokens,
            preview_content=preview,
        )
        second = freeze_motion_style(
            contract_path=CONTRACT,
            vendor_root=VENDOR_ROOT,
            workspace_root=project,
            render_job_root=second_job,
            style_preset_id="blue-professional",
            frame_markdown=frame_markdown,
            brand_tokens=brand_tokens,
            preview_content=preview,
        )
        if first != second:
            raise RuntimeError("same style input did not reproduce the same frozen artifact")
        for relative in (
            "frame.md",
            "style-freeze.json",
            font_asset,
            logo_asset,
        ):
            if (first_job / relative).read_bytes() != (second_job / relative).read_bytes():
                raise RuntimeError(f"RenderJob frozen copy drifted: {relative}")
        print(
            "[ok] locked builder + brand remix + two RenderJob freezes:",
            first.frozen_frame_sha256,
        )


def run_deterministic_gates() -> None:
    subprocess.run(
        ["python3", "scripts/test_motion_style_freezer.py"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            pnpm_executable(),
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/features/video-studio/motion-style-authoring.test.ts",
            "src/features/video-studio/motion-style-catalog.test.ts",
            "src/features/video-studio/VideoStudio.test.tsx",
        ],
        cwd=ROOT,
        check=True,
    )


def require_deliverables() -> None:
    required = (
        CONTRACT,
        ROOT / "tools/motion-authoring/motion_style_freezer.py",
        ROOT / "scripts/test_motion_style_freezer.py",
        ROOT / "frontend/src/features/video-studio/motion-style-authoring.ts",
        ROOT / "frontend/src/features/video-studio/motion-style-authoring.test.ts",
        ROOT / "frontend/src/features/video-studio/MotionStyleCatalog.tsx",
        ROOT / "frontend/e2e-tauri/motion-style-catalog.spec.ts",
        ROOT / "docs/development/BM-07.md",
    )
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"BM-07 missing deliverables: {', '.join(missing)}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| BM-07 |")]
    if len(rows) != 1 or not any(
        rows[0].endswith(f"| {status} |")
        for status in ("🚧 实现中", "🔍 待验收", "✅ 已完成")
    ):
        raise SystemExit("BM-07 roadmap row is missing, duplicated or not active")


def main() -> int:
    require_deliverables()
    run_deterministic_gates()
    run_locked_builder_and_freezer()
    run_desktop_acceptance()
    print("BM-07 motion style authoring and freeze acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
