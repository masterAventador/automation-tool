#!/usr/bin/env python3
"""BM-16 cross-platform determinism and release acceptance.

Runs the aggregated deterministic gates, composes the locked 134-item release
directory, then drives the real embedded Chromium through the production
worker for: a per-item render sweep over all 134 catalog parts, a 12-style
manual-template render sweep, and a same-input double-run frame-digest
comparison. Also verifies the first release has no URL-entry or scraping
entry point and leaves no staged Chromium or worker process behind. Formal
package acceptance and sleep/resume coverage are recorded in the task ledger.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import uuid
import zlib
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_embedded_chromium_staging import (
    CHROMIUM_CONTRACT,
    DEFAULT_ARCHIVES,
    build_staging,
    load_staging_contract,
)
from process_inspection import process_ids_matching, terminate_matching_processes
from run_bm_04_acceptance import current_target_id
from test_motion_video_render_adapter import (
    WorkerSession,
    bootstrap_document,
    expect_ready,
    render_browser_document,
)
from test_motion_video_render_sandbox import (
    SANDBOX_CPU_PARALLELISM_MAXIMUM,
    sandbox_command_line,
    sandbox_spec,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".local/embedded-browser-video-studio/bm-16-evidence"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# 2 帧（0 与 D/2）会被周期性动画混叠：transitions-radial 在 t=0 与 t=10 逐字节
# 同帧（实拍对照：0/5/10/15 四点三个不同摘要），两点采样正好踩在同相位上被静帧
# 门禁误拒。4 帧落在 0、D/4、D/2、3D/4，同一零件三个不同摘要能过；采样永远证明
# 不了「所有帧都动」，只把碰撞概率压到目录现有零件实测不再触发。
SWEEP_FRAMES = 4
STYLE_FRAMES = 3
DETERMINISM_FRAMES = 30
# Every render now pays a one-off warm-up (seek to zero, bounded image decode,
# two animation frames, one discarded capture) so the first kept frame is as
# settled as the rest. Both budgets are stall guards, not performance targets:
# the slowest sweep item plus that warm-up already exceeded 55s on this Mac,
# and a software-rasterising host (every Windows run here) is slower again.
SHORT_RENDER_BUDGET_SECONDS = 120
DETERMINISM_RENDER_BUDGET_SECONDS = 180
VISUAL_SAMPLE_PIXELS_MAXIMUM = 57_600
VISUAL_STANDARD_DEVIATION_MINIMUM = 0.5
VISUAL_NON_DOMINANT_FRACTION_MINIMUM = 0.001


class FrameVisualRejected(RuntimeError):
    """A captured PNG cannot serve as content-level visual evidence."""


COMPONENT_FRAGMENT_PREVIEW_CONTRACTS: dict[str, tuple[str, str, str]] = {
    "caption-blend-difference": (
        """
<div class="bm16-panel bm16-split">
  <div class="blend-difference bm16-hero">明暗之间</div>
</div>
""",
        "",
        (
            "getComputedStyle(document.querySelector('.blend-difference'))"
            ".mixBlendMode === 'difference'"
        ),
    ),
    "grain-overlay": (
        """
<div class="bm16-panel bm16-warm">
  <div class="bm16-kicker">ANALOG TEXTURE</div>
  <div class="bm16-hero">颗粒叠加</div>
</div>
""",
        "",
        (
            "document.querySelector('.grain-texture') !== null && "
            "getComputedStyle(document.querySelector('.grain-texture'))"
            ".backgroundImage !== 'none'"
        ),
    ),
    "grid-pixelate-wipe": (
        """
<div class="bm16-panel bm16-cool">
  <div class="bm16-kicker">GRID TRANSITION</div>
  <div class="bm16-hero">像素擦除</div>
</div>
""",
        (
            "document.querySelectorAll('#grid-pixelate-overlay .grid-cell')"
            ".forEach((cell, index) => {"
            "cell.style.transform = index % 3 === 0 ? 'scale(1)' : 'scale(.35)';"
            "});"
        ),
        (
            "document.querySelectorAll('#grid-pixelate-overlay .grid-cell')"
            ".length === 144 && "
            "getComputedStyle(document.querySelector("
            "'#grid-pixelate-overlay .grid-cell')).transform !== 'none'"
        ),
    ),
    "motion-blur": (
        """
<div class="bm16-panel bm16-cool">
  <div class="bm16-motion-track">
    <div class="bm16-motion-target">MOTION</div>
  </div>
</div>
""",
        "",
        "typeof window.attachMotionBlur === 'function'",
    ),
    "parallax-unzoom": (
        """
<div class="bm16-panel bm16-cards parallax-unzoom-grid" style="--pu-progress:.62">
  <div class="parallax-unzoom-card" data-pu-row="0" data-pu-col="0">A</div>
  <div class="parallax-unzoom-card" data-pu-row="0" data-pu-col="1">B</div>
  <div class="parallax-unzoom-card" data-pu-row="1" data-pu-col="0">C</div>
  <div class="parallax-unzoom-card" data-pu-row="1" data-pu-col="1"
       data-pu-focus="true">焦点</div>
</div>
""",
        "",
        (
            "document.querySelector('.parallax-unzoom-card:not([data-pu-focus])')"
            ".style.getPropertyValue('--pu-dx') !== ''"
        ),
    ),
    "parallax-zoom": (
        """
<div class="bm16-panel bm16-cards parallax-zoom-grid" style="--pz-progress:.62">
  <div class="parallax-zoom-card" data-pz-row="0" data-pz-col="0">A</div>
  <div class="parallax-zoom-card" data-pz-row="0" data-pz-col="1">B</div>
  <div class="parallax-zoom-card" data-pz-row="1" data-pz-col="0">C</div>
  <div class="parallax-zoom-card" data-pz-row="1" data-pz-col="1"
       data-pz-focus="true">焦点</div>
</div>
""",
        "",
        (
            "document.querySelector('.parallax-zoom-card:not([data-pz-focus])')"
            ".style.getPropertyValue('--pz-dx') !== ''"
        ),
    ),
    "shimmer-sweep": (
        """
<div class="bm16-panel bm16-dark">
  <div class="shimmer-sweep-target bm16-hero" style="--shimmer-pos:52%">
    光泽掠过
  </div>
</div>
""",
        "",
        (
            "document.querySelector('.shimmer-mask') !== null && "
            "getComputedStyle(document.querySelector('.shimmer-mask'))"
            ".backgroundImage !== 'none'"
        ),
    ),
    "texture-mask-text": (
        """
<div class="bm16-panel bm16-dark">
  <div class="hf-texture-text hf-texture-lava bm16-hero">纹理文字</div>
</div>
""",
        "",
        (
            "getComputedStyle(document.querySelector('.hf-texture-text'))"
            ".webkitMaskImage !== 'none'"
        ),
    ),
    "vignette": (
        """
<div class="bm16-panel bm16-cinema">
  <div class="bm16-kicker">CINEMATIC FRAME</div>
  <div class="bm16-hero">暗角</div>
</div>
""",
        "",
        (
            "document.querySelector('#hf-vignette') !== null && "
            "getComputedStyle(document.querySelector('#hf-vignette'))"
            ".backgroundImage !== 'none'"
        ),
    ),
}


def build_component_preview_html(*, name: str, source: str) -> str:
    """Put a packaged component in the smallest content-bearing honest host."""
    if re.search(r"<!doctype\s+html", source, flags=re.IGNORECASE):
        backdrop = (
            "<style data-bm16-component-backdrop>"
            "html,body{background:#10151d!important}"
            "</style>"
        )
        if not re.search(r"</head\s*>", source, flags=re.IGNORECASE):
            raise FrameVisualRejected(
                f"{name}: complete component document has no closing head"
            )
        return re.sub(
            r"</head\s*>",
            backdrop + "</head>",
            source,
            count=1,
            flags=re.IGNORECASE,
        )

    contract = COMPONENT_FRAGMENT_PREVIEW_CONTRACTS.get(name)
    if contract is None:
        raise FrameVisualRejected(
            f"{name}: component fragment has no fail-closed preview contract"
        )
    markup, setup, probe = contract
    escaped_name = html.escape(name)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=1920, height=1080">
  <title>BM-16 component preview · {escaped_name}</title>
  <style>
    *{{box-sizing:border-box}}
    html,body{{margin:0;width:1920px;height:1080px;overflow:hidden;background:#10151d}}
    .bm16-panel{{position:absolute;inset:0;display:flex;flex-direction:column;
      align-items:center;justify-content:center;isolation:isolate;
      font-family:Arial,"PingFang SC",sans-serif;color:#fff}}
    .bm16-split{{background:linear-gradient(90deg,#111827 0 50%,#f4d35e 50%)}}
    .bm16-warm{{background:linear-gradient(135deg,#f6e7cb,#d98e5f 52%,#512b32)}}
    .bm16-cool{{background:linear-gradient(145deg,#071a2b,#16697a 52%,#82c0cc)}}
    .bm16-dark{{background:linear-gradient(135deg,#050816,#283149 55%,#8b5cf6)}}
    .bm16-cinema{{background:radial-gradient(circle at 50% 44%,#f4a261,#264653 62%,#07111a)}}
    .bm16-kicker{{font-size:28px;letter-spacing:.32em;font-weight:700;margin-bottom:34px}}
    .bm16-hero{{font-size:150px;line-height:1;font-weight:900;letter-spacing:.04em}}
    .bm16-motion-track{{width:1500px;height:260px;border:6px solid #e9c46a;
      display:flex;align-items:center;padding:40px}}
    .bm16-motion-target{{font-size:132px;font-weight:900;letter-spacing:.08em;
      color:#f4a261;text-shadow:-18px 0 rgba(244,162,97,.18),-36px 0 rgba(244,162,97,.1)}}
    .bm16-cards{{display:grid;grid-template-columns:repeat(2,360px);
      grid-template-rows:repeat(2,260px);gap:38px;padding:220px 580px}}
    .bm16-cards>div{{display:flex;align-items:center;justify-content:center;
      min-width:360px;min-height:260px;border-radius:28px;
      background:linear-gradient(135deg,#e76f51,#f4a261);font-size:72px;font-weight:900;
      box-shadow:0 28px 70px rgba(0,0,0,.35)}}
  </style>
</head>
<body>
  <div data-composition-id="bm16-component-preview" data-start="0"
       data-duration="3" data-width="1920" data-height="1080">
{markup.strip()}
{source}
  </div>
  <script>
    (() => {{
      let ok = false;
      try {{
        {setup}
        ok = Boolean({probe});
      }} catch (_error) {{
        ok = false;
      }}
      if (!ok) {{
        document.body.replaceChildren();
        document.body.style.cssText =
          "margin:0;width:1920px;height:1080px;background:#10151d!important";
      }}
    }})();
  </script>
</body>
</html>
"""


def component_preview_capture_plan(source: str) -> dict[str, object]:
    """Sample real component timelines; do not invent motion for passive snippets."""
    if re.search(r"<!doctype\s+html", source, flags=re.IGNORECASE):
        duration = re.search(
            r"data-duration=[\"']([0-9]+(?:\.[0-9]+)?)",
            source,
        )
        if duration is None or float(duration.group(1)) <= 0:
            raise FrameVisualRejected(
                "complete component document has no positive capture duration"
            )
        width = re.search(r"data-width=[\"']([0-9]+)", source)
        height = re.search(r"data-height=[\"']([0-9]+)", source)
        if width is None or height is None:
            raise FrameVisualRejected(
                "complete component document has no declared capture canvas"
            )
        duration_millis = int(float(duration.group(1)) * 1_000)
        return {
            "canvas": {
                "deviceScaleFactor": 1,
                "height": int(height.group(1)),
                "width": int(width.group(1)),
            },
            "frameCountPerSample": 1,
            "sampleMillis": [
                duration_millis * numerator // 5 for numerator in range(1, 5)
            ],
        }
    return {
        "canvas": {
            "deviceScaleFactor": 1,
            "height": 1080,
            "width": 1920,
        },
        "frameCountPerSample": 1,
        "sampleMillis": [0],
    }


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _decode_png(path: Path) -> tuple[int, int, int, bytes]:
    """Decode the bounded 8-bit RGB/RGBA shape Chromium screenshots use."""
    payload = path.read_bytes()
    if not payload.startswith(PNG_MAGIC):
        raise FrameVisualRejected("visual frame is not a PNG")
    offset = len(PNG_MAGIC)
    header: tuple[int, int, int] | None = None
    compressed = bytearray()
    ended = False
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise FrameVisualRejected("visual PNG has a truncated chunk")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(payload):
            raise FrameVisualRejected("visual PNG chunk escapes the file")
        chunk = payload[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length : end])[0]
        if zlib.crc32(kind + chunk) & 0xFFFFFFFF != expected_crc:
            raise FrameVisualRejected("visual PNG chunk checksum is invalid")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise FrameVisualRejected("visual PNG has an invalid header")
            width, height, depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", chunk)
            )
            if (
                width <= 0
                or height <= 0
                or width > 8_192
                or height > 8_192
                or depth != 8
                or color_type not in (2, 6)
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise FrameVisualRejected("visual PNG uses an unsupported pixel shape")
            header = (width, height, 3 if color_type == 2 else 4)
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            ended = True
            offset = end
            break
        offset = end
    if header is None or not compressed or not ended or offset != len(payload):
        raise FrameVisualRejected("visual PNG is incomplete or carries trailing bytes")
    width, height, bytes_per_pixel = header
    stride = width * bytes_per_pixel
    expected_size = (stride + 1) * height
    if expected_size > 256 * 1024 * 1024:
        raise FrameVisualRejected("visual PNG expands beyond the evidence budget")
    decompressor = zlib.decompressobj()
    decoded = decompressor.decompress(bytes(compressed), expected_size + 1)
    decoded += decompressor.flush()
    if (
        len(decoded) != expected_size
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        raise FrameVisualRejected("visual PNG has an invalid decompressed size")

    pixels = bytearray(height * stride)
    previous = bytearray(stride)
    source_offset = 0
    for row_index in range(height):
        filter_type = decoded[source_offset]
        source_offset += 1
        filtered = decoded[source_offset : source_offset + stride]
        source_offset += stride
        row = bytearray(stride)
        for byte_index, value in enumerate(filtered):
            left = (
                row[byte_index - bytes_per_pixel]
                if byte_index >= bytes_per_pixel
                else 0
            )
            above = previous[byte_index]
            upper_left = (
                previous[byte_index - bytes_per_pixel]
                if byte_index >= bytes_per_pixel
                else 0
            )
            if filter_type == 0:
                restored = value
            elif filter_type == 1:
                restored = value + left
            elif filter_type == 2:
                restored = value + above
            elif filter_type == 3:
                restored = value + ((left + above) // 2)
            elif filter_type == 4:
                restored = value + _paeth_predictor(left, above, upper_left)
            else:
                raise FrameVisualRejected("visual PNG uses an unknown scanline filter")
            row[byte_index] = restored & 0xFF
        start = row_index * stride
        pixels[start : start + stride] = row
        previous = row
    return width, height, bytes_per_pixel, bytes(pixels)


def measure_frame_visuals(path: Path) -> dict[str, object]:
    """Return path-free pixel facts and reject a uniform/blank capture."""
    width, height, bytes_per_pixel, pixels = _decode_png(path)
    pixel_count = width * height
    sample_step = max(
        1,
        math.ceil(math.sqrt(pixel_count / VISUAL_SAMPLE_PIXELS_MAXIMUM)),
    )
    colors: Counter[tuple[int, int, int]] = Counter()
    luma_sum = 0.0
    luma_squared_sum = 0.0
    sampled = 0
    for y in range(0, height, sample_step):
        for x in range(0, width, sample_step):
            offset = (y * width + x) * bytes_per_pixel
            red, green, blue = pixels[offset : offset + 3]
            if bytes_per_pixel == 4:
                alpha = pixels[offset + 3]
                red = red * alpha // 255
                green = green * alpha // 255
                blue = blue * alpha // 255
            color = (red, green, blue)
            colors[color] += 1
            luma = (299 * red + 587 * green + 114 * blue) / 1_000
            luma_sum += luma
            luma_squared_sum += luma * luma
            sampled += 1
    mean = luma_sum / sampled
    variance = max(0.0, luma_squared_sum / sampled - mean * mean)
    standard_deviation = math.sqrt(variance)
    dominant = max(colors.values())
    non_dominant_fraction = 1.0 - dominant / sampled
    if len(colors) == 1 or (
        standard_deviation < VISUAL_STANDARD_DEVIATION_MINIMUM
        and non_dominant_fraction < VISUAL_NON_DOMINANT_FRACTION_MINIMUM
    ):
        raise FrameVisualRejected(
            "visual frame is uniform and carries no inspectable content"
        )
    return {
        "width": width,
        "height": height,
        "sampledPixels": sampled,
        "uniqueSampledColors": len(colors),
        "lumaStandardDeviation": round(standard_deviation, 4),
        "nonDominantFraction": round(non_dominant_fraction, 6),
    }


def build_category_contact_sheets(
    *,
    frames: Path,
    catalog_categories: dict[str, str],
    output: Path,
) -> dict[str, Path]:
    """Build dependency-free SVG sheets embedding one real PNG per item."""
    frame_paths = {path.stem: path for path in frames.glob("*.png")}
    if set(frame_paths) != set(catalog_categories):
        missing = sorted(set(catalog_categories) - set(frame_paths))
        extra = sorted(set(frame_paths) - set(catalog_categories))
        raise FrameVisualRejected(
            f"contact-sheet coverage drifted: missing={missing}, extra={extra}"
        )
    grouped: dict[str, list[str]] = {}
    for name, category in catalog_categories.items():
        grouped.setdefault(category, []).append(name)
    output.mkdir(parents=True, exist_ok=False)
    sheets: dict[str, Path] = {}
    columns = 4
    tile_width = 320
    image_height = 180
    label_height = 28
    gap = 16
    margin = 24
    for sheet_index, (category, names) in enumerate(sorted(grouped.items()), start=1):
        ordered = sorted(names)
        rows = math.ceil(len(ordered) / columns)
        width = margin * 2 + columns * tile_width + (columns - 1) * gap
        height = margin * 2 + 36 + rows * (image_height + label_height + gap) - gap
        elements = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                f'height="{height}" viewBox="0 0 {width} {height}">'
            ),
            f"<title>BM-16 {html.escape(category)} catalog contact sheet</title>",
            f'<rect width="{width}" height="{height}" fill="#10151d"/>',
            (
                f'<text x="{margin}" y="{margin + 24}" fill="#ffffff" '
                f'font-family="sans-serif" font-size="24">{html.escape(category)}'
                f" · {len(ordered)} 项</text>"
            ),
        ]
        top = margin + 36
        for offset, name in enumerate(ordered):
            column = offset % columns
            row = offset // columns
            x = margin + column * (tile_width + gap)
            y = top + row * (image_height + label_height + gap)
            encoded = base64.b64encode(frame_paths[name].read_bytes()).decode("ascii")
            elements.extend(
                [
                    (
                        f'<rect x="{x}" y="{y}" width="{tile_width}" '
                        f'height="{image_height}" rx="6" fill="#000000"/>'
                    ),
                    (
                        f'<image x="{x}" y="{y}" width="{tile_width}" '
                        f'height="{image_height}" preserveAspectRatio="xMidYMid meet" '
                        f'href="data:image/png;base64,{encoded}"/>'
                    ),
                    (
                        f'<text x="{x}" y="{y + image_height + 20}" fill="#dce5ef" '
                        f'font-family="monospace" font-size="14">{html.escape(name)}</text>'
                    ),
                ]
            )
        elements.append("</svg>")
        path = output / f"category-{sheet_index:02d}.svg"
        path.write_text("\n".join(elements) + "\n", encoding="utf-8", newline="\n")
        sheets[category] = path
    return sheets


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 1_800,
) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=timeout)


def run_deterministic_gates() -> None:
    _run([sys.executable, "scripts/test_bm_16_acceptance_contract.py"])
    _run([sys.executable, "scripts/test_bm_16_visual_evidence.py"])
    _run([sys.executable, "scripts/test_motion_catalog_render_exclusions.py"])
    _run([sys.executable, "scripts/test_motion_catalog_release.py"])
    _run([sys.executable, "scripts/check_third_party_sources.py"])
    _run([sys.executable, "scripts/check_motion_catalog.py"])
    _run([sys.executable, "scripts/check_motion_catalog_ui_projection.py"])
    _run([sys.executable, "scripts/test_motion_catalog_ui_projection.py"])
    _run([sys.executable, "scripts/test_motion_video_render_sandbox.py"])
    _run([sys.executable, "scripts/test_motion_video_worker.py"])
    _run([sys.executable, "scripts/test_motion_authoring_agent.py"])
    _run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "motion_video_studio",
            "--locked",
        ]
    )
    _run([sys.executable, "scripts/check_user_facing_branding.py"])
    _run([sys.executable, "scripts/check_embedded_browser_video_roadmap.py"])


def stage_release_directory(run_root: Path) -> Path:
    release = run_root / "release"
    _run([sys.executable, "scripts/build_offline_motion_catalog.py"])
    _run(
        [
            sys.executable,
            "scripts/build_motion_catalog_release.py",
            "--release-root",
            str(release),
        ]
    )
    _run(
        [
            sys.executable,
            "scripts/check_motion_catalog_release.py",
            "--release-root",
            str(release),
        ]
    )
    return release


def _stage_chromium(run_root: Path) -> tuple[Path, int]:
    target_id = current_target_id()
    contract = load_staging_contract(CHROMIUM_CONTRACT)
    target = contract.targets[target_id]
    if not target.buildable:
        raise RuntimeError(f"BM-16 Chromium target is not buildable: {target_id}")
    result = build_staging(
        contract=contract,
        target_id=target_id,
        archive_path=DEFAULT_ARCHIVES[target_id].resolve(strict=True),
        archive_sha256=target.archive_sha256,
        output=run_root / "chromium",
    )
    executable = (result.output / Path(*target.executable.split("/"))).resolve(
        strict=True
    )
    return executable, int(contract.browser_version.split(".")[0])


def _render_once(
    browser: Path,
    chromium_major: int,
    workspace: Path,
    entry: str,
    allowed_assets: list[str],
    frame_count: int,
    budget_seconds: int = SHORT_RENDER_BUDGET_SECONDS,
    spec_overrides: dict[str, object] | None = None,
    retained_frame: Path | None = None,
    require_visual: bool = True,
) -> dict[str, object]:
    """One real sandboxed render; returns protocol, digest and pixel evidence."""
    frames = workspace / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    job_id = str(uuid.uuid4())
    session = WorkerSession(
        bootstrap_document(
            str(workspace),
            render_browser_document(browser, major=chromium_major, timeout_seconds=45),
        ),
        os.environ.copy(),
    )
    try:
        expect_ready(session)
        specification = sandbox_spec(
            workspace,
            allowedAssets=allowed_assets,
            entryHtml=entry,
            frameCount=frame_count,
            # Wall clock is the stall guard. CPU seconds are summed across the
            # whole browser process tree, so a render that legitimately uses
            # several cores accrues them far faster; the sandbox contract states
            # that ceiling as the wall budget times the maximum declarable
            # average core occupancy, which is what a render sweep may use.
            maxCpuSeconds=budget_seconds * SANDBOX_CPU_PARALLELISM_MAXIMUM,
            maxDurationSeconds=budget_seconds,
            maxMemoryMegabytes=2048,
            maxOutputBytes=256 * 1024 * 1024,
            **(spec_overrides or {}),
        )
        session.send_line(sandbox_command_line(job_id, specification))
        event = session.read_event()
        if event.get("event") != "worker.render.sandboxed":
            raise RuntimeError(f"render failed for {entry}: {event}")
        if event.get("framesCaptured") != frame_count:
            raise RuntimeError(f"{entry}: frame count drifted: {event}")
    finally:
        code, stderr = session.finish()
        if code != 0:
            raise RuntimeError(f"worker exited abnormally for {entry}: {stderr}")

    digests: list[str] = []
    visible_frames: list[tuple[float, int, Path, dict[str, object]]] = []
    for index in range(1, frame_count + 1):
        frame = frames / f"frame-{index:05d}.png"
        raw = frame.read_bytes()
        if not raw.startswith(PNG_MAGIC) or len(raw) < 1_000:
            raise RuntimeError(f"{entry}: frame {index} is not a plausible PNG")
        digests.append(hashlib.sha256(raw).hexdigest())
        try:
            visual = measure_frame_visuals(frame)
        except FrameVisualRejected:
            continue
        score = float(visual["lumaStandardDeviation"]) * (
            0.25 + float(visual["nonDominantFraction"])
        )
        visible_frames.append((score, index, frame, visual))
    if not visible_frames:
        if require_visual:
            raise FrameVisualRejected(
                f"{entry}: every captured frame is uniform; "
                "no content-level evidence exists"
            )
        visual_result = None
    else:
        _score, visual_index, visual_path, visual = max(visible_frames)
        if retained_frame is not None:
            retained_frame.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(visual_path, retained_frame)
        visual_result = {"frameIndex": visual_index, **visual}
    shutil.rmtree(frames)
    return {
        "event": event,
        "frames": digests,
        "visual": visual_result,
    }


def _render_component_document_samples(
    *,
    browser: Path,
    chromium_major: int,
    workspace: Path,
    entry: str,
    allowed_assets: list[str],
    sample_millis: list[int],
    spec_overrides: dict[str, object],
    retained_frame: Path,
) -> dict[str, object]:
    """Seek full component documents at explicit points and aggregate the evidence."""
    samples: list[dict[str, object]] = []
    candidates: list[Path] = []
    try:
        for sample_index, sample_millis_value in enumerate(sample_millis, start=1):
            candidate = retained_frame.with_name(
                f".{retained_frame.stem}-sample-{sample_index}.png"
            )
            candidates.append(candidate)
            rendered = _render_once(
                browser,
                chromium_major,
                workspace,
                entry,
                allowed_assets,
                1,
                spec_overrides={
                    **spec_overrides,
                    "sourceStartMillis": sample_millis_value,
                    "sourceEndMillis": sample_millis_value + 1,
                },
                retained_frame=candidate,
                require_visual=False,
            )
            samples.append(
                {
                    **rendered,
                    "candidate": candidate,
                    "sampleIndex": sample_index,
                    "sampleMillis": sample_millis_value,
                }
            )
        digests = [str(sample["frames"][0]) for sample in samples]
        if len(set(digests)) < 2:
            raise FrameVisualRejected(
                f"{entry}: explicit component timeline samples never changed"
            )
        visible = [sample for sample in samples if sample["visual"] is not None]
        if not visible:
            raise FrameVisualRejected(
                f"{entry}: explicit component timeline samples are all uniform"
            )
        selected = max(
            visible,
            key=lambda sample: (
                float(sample["visual"]["lumaStandardDeviation"])
                * (0.25 + float(sample["visual"]["nonDominantFraction"]))
            ),
        )
        retained_frame.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected["candidate"], retained_frame)
        event = dict(samples[0]["event"])
        event["blockedRequests"] = sum(
            int(sample["event"]["blockedRequests"]) for sample in samples
        )
        visual = dict(selected["visual"])
        visual["frameIndex"] = selected["sampleIndex"]
        visual["sampleMillis"] = selected["sampleMillis"]
        return {
            "event": event,
            "frames": digests,
            "visual": visual,
        }
    finally:
        for candidate in candidates:
            candidate.unlink(missing_ok=True)


def _writable_release_copy(release: Path, destination: Path) -> Path:
    shutil.copytree(release, destination)
    for path in destination.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    return destination


def run_item_render_sweep(
    browser: Path, chromium_major: int, release: Path, run_root: Path
) -> tuple[dict[str, object], Path]:
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    workspace = _writable_release_copy(release, run_root / "item-sweep")
    # 白名单按引用收集，与产品的工作区写入器同一份遍历（PC-05 的决定）。
    # PC-13 把共享依赖加了字体之后，「零件文件 + 全部共享依赖」的老算法在
    # caption-texture 上超过了沙箱的 128 上限——产品早就不整树拷贝了，sweep 跟上。
    from automation_tool.executor.motion_authoring.part_workspace import (
        referenced_assets,
    )

    results: dict[str, object] = {}
    # PC-19 之后段必须自带时间窗，而本 sweep 一直用夹具默认的 [0, 3000ms] 渲每一个
    # 零件。零件的动作不必发生在前三秒——apple-money-count 声明 5 秒，数钱、闪绿、
    # 撒钱币都在后半段，于是三秒窗里它是静止的，被静帧门禁正确地拒了（2026-07-29
    # 实测，PC-19 之前这条 sweep 是 134/134 全过的）。block 窗口按目录声明的时长
    # 开；component 的目录行不重复时长，16 个完整文档从自身 data-duration 取四个
    # seek 点，9 个被动片段在三秒受控宿主里只取一帧。
    catalog_items = json.loads(
        (ROOT / "contracts/quality/motion-catalog.v1.json").read_text(encoding="utf-8")
    )["items"]
    catalog_durations = {
        entry["name"]: entry.get("duration") for entry in catalog_items
    }
    catalog_categories = {entry["name"]: entry["category"] for entry in catalog_items}
    # 画布同理（PC-05：渲染画布随请求走，零件在自己声明的舞台上渲）。控制实验
    # 2026-07-29：apple-money-count 在 1920×1080 下五个 seek 点五个画面，在模板的
    # 640×360 下前三秒是同一张——模板画布看到的是它舞台的静止角落，静帧门禁拒得
    # 对。component 的目录行同样不重复尺寸；完整文档从自身 data-width/data-height
    # 取，被动片段的受控宿主明确固定 1920×1080，不能回退模板画布。
    catalog_dimensions = {
        entry["name"]: entry.get("dimensions") for entry in catalog_items
    }
    # 1 个按设计静止的叠加项保留带原因豁免（PC-21 §18.6 逐类定性），按
    # 带原因的豁免清单跳过——清单卫生由 test_motion_catalog_render_exclusions
    # 守着（真实存在、类别封闭、理由非空），不是无声截断：跳过逐项打印，
    # 收尾核对「渲染 + 豁免 = 134」。内容修好一项就从清单划掉一项。
    exclusions: dict[str, dict[str, str]] = json.loads(
        (
            ROOT
            / "contracts/quality/motion-catalog-standalone-render-exclusions.v1.json"
        ).read_text(encoding="utf-8")
    )["items"]
    excluded_count = 0
    rendered_count = 0
    visual_frames = run_root / "item-visual-frames"
    visual_frames.mkdir()
    for index, item in enumerate(manifest["items"], start=1):
        name = item["name"]
        motion_excluded = name in exclusions
        if motion_excluded:
            excluded_count += 1
        else:
            rendered_count += 1
        files = list(item["files"])
        entries = [candidate for candidate in files if candidate.endswith(".html")]
        if not entries:
            raise RuntimeError(f"{name}: release item has no HTML entry")
        entry_path = workspace / entries[0]
        render_entry = entries[0]
        entry_source = entry_path.read_text(encoding="utf-8")
        component_preview_host = item["type"] == "component"
        if component_preview_host:
            preview_path = entry_path.with_name("bm16-component-preview.html")
            preview_path.write_text(
                build_component_preview_html(name=name, source=entry_source),
                encoding="utf-8",
                newline="\n",
            )
            render_entry = preview_path.relative_to(workspace).as_posix()
            entry_path = preview_path
            component_capture = component_preview_capture_plan(entry_source)
        else:
            component_capture = None
        referenced = referenced_assets(
            entry_path.read_text(encoding="utf-8"),
            catalog_root=workspace,
            origin=entry_path.parent,
            # 发布树里存在死引用（实测 video.mp4），渲染时沙箱拦截并计数——
            # sweep 沿用这个语义；工作区写入器保持拒绝（封闭树是产品的保证）。
            on_missing="skip",
        )
        allowed = sorted((set(files) | set(referenced)) - {entries[0]})
        if len(allowed) > 128:
            raise RuntimeError(f"{name}: allowlist exceeds the sandbox maximum")
        declared_seconds = catalog_durations.get(name)
        declared_dimensions = catalog_dimensions.get(name)
        component_samples = (
            list(component_capture["sampleMillis"]) if component_capture else []
        )
        sampled_component_document = len(component_samples) > 1
        overrides: dict[str, object] = {
            "sourceStartMillis": component_samples[0] if component_samples else 0,
            "sourceEndMillis": component_samples[0] + 1
            if component_samples
            else int(declared_seconds * 1000)
            if declared_seconds
            else 3000,
        }
        # 25 个 component 里只有 9 个是真正的被动片段；另外 16 个是带自己时间轴的
        # 完整 HTML。被动片段在受控宿主里渲一帧并核对 DOM/计算样式，完整文档则与
        # 109 个 block 一样扫四个明确 seek 点，不能再用开头连续四帧或单一盲点把
        # 时间轴中的空隙误当成代表帧。
        frame_count = (
            1
            if motion_excluded
            else component_capture["frameCountPerSample"]
            if component_capture
            else SWEEP_FRAMES
        )
        if component_capture:
            overrides["canvas"] = component_capture["canvas"]
        elif declared_dimensions:
            overrides["canvas"] = {
                # Factor 1：零件的舞台就是输出分辨率（PC-05 的装配同一条规则）。
                "deviceScaleFactor": 1,
                "height": declared_dimensions["height"],
                "width": declared_dimensions["width"],
            }
        retained_frame = visual_frames / f"{name}.png"
        if sampled_component_document:
            base_overrides = dict(overrides)
            base_overrides.pop("sourceStartMillis")
            base_overrides.pop("sourceEndMillis")
            rendered = _render_component_document_samples(
                browser=browser,
                chromium_major=chromium_major,
                workspace=workspace,
                entry=render_entry,
                allowed_assets=allowed,
                sample_millis=component_samples,
                spec_overrides=base_overrides,
                retained_frame=retained_frame,
            )
        else:
            rendered = _render_once(
                browser,
                chromium_major,
                workspace,
                render_entry,
                allowed,
                frame_count,
                spec_overrides=overrides,
                retained_frame=retained_frame,
            )
        results[name] = {
            "frames": rendered["frames"],
            "blockedRequests": rendered["event"]["blockedRequests"],
            "componentPreviewHost": component_preview_host,
            "componentSampleMillis": component_samples,
            "motionSweepExcluded": motion_excluded,
            "visual": rendered["visual"],
        }
        if motion_excluded:
            print(
                f"[bm-16] item {index}/134 visualized but motion-excluded"
                f" ({exclusions[name]['class']}): {name}",
                flush=True,
            )
        else:
            print(f"[bm-16] item {index}/134 rendered: {name}", flush=True)
    if len(results) != 134 or rendered_count + excluded_count != 134:
        raise RuntimeError("BM-16 sweep must cover exactly 134 items")
    if excluded_count != len(exclusions):
        raise RuntimeError("BM-16 exclusion list names items the manifest lacks")
    print(
        f"[bm-16] item sweep: {rendered_count} motion-rendered, "
        f"{excluded_count} motion-excluded, 134 visually evidenced",
        flush=True,
    )
    distinct = {digest for entry in results.values() for digest in entry["frames"]}
    if len(distinct) < 40:
        raise RuntimeError(
            "BM-16 sweep produced implausibly uniform frames; renders look blank"
        )
    sheets = build_category_contact_sheets(
        frames=visual_frames,
        catalog_categories=catalog_categories,
        output=run_root / "item-contact-sheets",
    )
    if len(sheets) != 11:
        raise RuntimeError(f"BM-16 expected 11 category sheets, found {len(sheets)}")
    return results, next(iter(sheets.values())).parent


def run_style_render_sweep(
    browser: Path, chromium_major: int, run_root: Path
) -> dict[str, object]:
    styles_directory = run_root / "styles"
    environment = os.environ.copy()
    environment["AUTOMATION_TOOL_BM16_STYLE_SWEEP_DIR"] = str(styles_directory)
    _run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "motion_video_studio",
            "bm16_all_twelve",
            "--locked",
        ],
        env=environment,
    )
    compositions = sorted(styles_directory.glob("*.html"))
    if len(compositions) != 12:
        raise RuntimeError(f"expected 12 frozen styles, found {len(compositions)}")
    results: dict[str, object] = {}
    for composition in compositions:
        workspace = run_root / f"style-{composition.stem}"
        workspace.mkdir()
        shutil.copyfile(composition, workspace / "composition.html")
        rendered = _render_once(
            browser,
            chromium_major,
            workspace,
            "composition.html",
            [],
            STYLE_FRAMES,
        )
        digests = rendered["frames"]
        if len(set(digests)) != STYLE_FRAMES:
            raise RuntimeError(
                f"style {composition.stem}: timeline seek produced duplicate frames"
            )
        results[composition.stem] = digests
        print(f"[bm-16] style rendered: {composition.stem}", flush=True)
    return results


def run_double_run_determinism(
    browser: Path, chromium_major: int, run_root: Path
) -> dict[str, object]:
    composition = run_root / "styles/blue-professional.html"
    runs: list[list[str]] = []
    for attempt in (1, 2):
        workspace = run_root / f"determinism-{attempt}"
        workspace.mkdir()
        shutil.copyfile(composition, workspace / "composition.html")
        rendered = _render_once(
            browser,
            chromium_major,
            workspace,
            "composition.html",
            [],
            DETERMINISM_FRAMES,
            DETERMINISM_RENDER_BUDGET_SECONDS,
        )
        runs.append(list(rendered["frames"]))
    if runs[0] != runs[1]:
        drifted = [
            index
            for index, (first, second) in enumerate(zip(runs[0], runs[1]), start=1)
            if first != second
        ]
        raise RuntimeError(f"BM-16 double-run determinism failed on frames {drifted}")
    return {"frameCount": DETERMINISM_FRAMES, "frames": runs[0]}


def verify_no_url_entry() -> None:
    spec = (ROOT / "frontend/e2e-tauri/motion-video-native.spec.ts").read_text(
        encoding="utf-8"
    )
    if "网址|URL|抓取" not in spec:
        raise RuntimeError("BM-08 desktop spec lost its no-URL-entry page assertion")
    offenders: list[str] = []
    for path in (ROOT / "frontend/src/features/video-studio").rglob("*.ts*"):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        if 'type="url"' in text or "http://" in text or "https://" in text:
            offenders.append(path.name)
        if "抓取" in text or "网址" in text:
            offenders.append(path.name)
    if offenders:
        raise RuntimeError(
            f"BM-16 found URL/scraping entry traces in the studio UI: {offenders}"
        )


def require_no_run_processes(
    run_root: Path, *, baseline: set[int] | frozenset[int] = frozenset()
) -> None:
    """Reject a completed run that left an owned worker or Chromium alive."""
    survivors = process_ids_matching(str(run_root)) - set(baseline)
    if survivors:
        raise RuntimeError(
            "BM-16 left a staged Chromium or worker process running: "
            + ", ".join(str(process_id) for process_id in sorted(survivors))
        )


def _retry_read_only_remove(
    remove: Callable[[str], object],
    path: str,
    error: tuple[type[BaseException], BaseException, TracebackType | None],
) -> None:
    """Make a generated read-only release path writable, then delete it."""
    if not isinstance(error[1], PermissionError):
        raise error[1]
    os.chmod(path, stat.S_IRWXU)
    remove(path)


def remove_run_root(run_root: Path) -> None:
    """Delete the complete run tree and fail closed if anything remains."""
    if not run_root.exists():
        return
    shutil.rmtree(run_root, onerror=_retry_read_only_remove)
    if run_root.exists():
        raise RuntimeError(f"BM-16 left its run directory behind: {run_root}")


def cleanup_run_root(
    run_root: Path,
    process_baseline: set[int] | frozenset[int],
    *,
    matching: Callable[[str], set[int]] = process_ids_matching,
    terminate_owned: Callable[..., set[int]] = terminate_matching_processes,
    require_none: Callable[..., None] = require_no_run_processes,
    remove: Callable[[Path], None] = remove_run_root,
) -> None:
    """Clean run-owned resources, but never turn a lifecycle leak into success."""
    leaked: set[int] = set()
    survivors: set[int] = set()
    process_cleanup_errors: list[tuple[str, Exception]] = []
    try:
        leaked.update(matching(str(run_root)) - set(process_baseline))
    except Exception as error:
        process_cleanup_errors.append(("initial process scan", error))
    try:
        survivors.update(
            terminate_owned(
                str(run_root), baseline=process_baseline, observed=leaked
            )
        )
    except Exception as error:
        process_cleanup_errors.append(("initial process termination", error))
    first_inspection_error: Exception | None = None
    try:
        require_none(run_root, baseline=process_baseline)
    except Exception as error:  # keep cleaning before surfacing the lifecycle failure
        first_inspection_error = error

    removal_error: Exception | None = None
    try:
        remove(run_root)
    except Exception as error:  # the final process pass must still run
        removal_error = error
    try:
        leaked.update(matching(str(run_root)) - set(process_baseline))
    except Exception as error:
        process_cleanup_errors.append(("post-removal process scan", error))
    # Always rescan inside the terminator as well: a process can appear after
    # the explicit post-removal snapshot and before cleanup returns.
    try:
        survivors.update(
            terminate_owned(
                str(run_root), baseline=process_baseline, observed=leaked
            )
        )
    except Exception as error:
        process_cleanup_errors.append(("final process termination", error))
    if removal_error is not None:
        try:
            remove(run_root)
        except Exception as error:
            removal_error = error
        else:
            removal_error = None
    # The directory is already gone, so a process discovered here cannot be a
    # legitimate child still starting from the staged runtime. Check again
    # after terminating it so cleanup never leaves the leak it reports.
    final_inspection_error: Exception | None = None
    try:
        require_none(run_root, baseline=process_baseline)
    except Exception as error:
        final_inspection_error = error
    if final_inspection_error is not None:
        # A child can become visible after the final terminator's last empty
        # snapshot. Give that PID one owned termination pass and prove it is
        # gone before reporting the lifecycle failure.
        try:
            survivors.update(
                terminate_owned(
                    str(run_root), baseline=process_baseline, observed=leaked
                )
            )
        except Exception as error:
            process_cleanup_errors.append(("late process termination", error))
        if removal_error is not None:
            try:
                remove(run_root)
            except Exception as error:
                removal_error = error
            else:
                removal_error = None
        try:
            require_none(run_root, baseline=process_baseline)
        except Exception as error:
            final_inspection_error = error
    if survivors:
        raise RuntimeError(
            "BM-16 could not terminate its staged Chromium or worker processes: "
            + ", ".join(str(process_id) for process_id in sorted(survivors))
        )
    if leaked:
        raise RuntimeError(
            "BM-16 left staged Chromium or worker processes after the run; "
            "cleanup terminated PID(s): "
            + ", ".join(str(process_id) for process_id in sorted(leaked))
        )
    if removal_error is not None:
        raise RuntimeError("BM-16 run directory cleanup failed") from removal_error
    if process_cleanup_errors:
        stages = ", ".join(stage for stage, _ in process_cleanup_errors)
        raise RuntimeError(
            f"BM-16 process cleanup tool failed during: {stages}"
        ) from process_cleanup_errors[0][1]
    if final_inspection_error is not None:
        raise RuntimeError("BM-16 final cleanup inspection failed") from final_inspection_error
    if first_inspection_error is not None:
        raise RuntimeError(
            "BM-16 first cleanup inspection failed before the final cleanup pass"
        ) from first_inspection_error


def main() -> int:
    if sys.version_info < (3, 10):
        raise RuntimeError("BM-16 acceptance requires python3.10+ (use python3.12)")
    run_deterministic_gates()
    run_root = (
        ROOT / ".local/embedded-browser-video-studio" / f"ebvs-bm16-{os.getpid()}"
    )
    process_baseline = process_ids_matching(str(run_root))
    run_root.mkdir(parents=True, exist_ok=False)
    try:
        release = stage_release_directory(run_root)
        browser, chromium_major = _stage_chromium(run_root)
        verify_no_url_entry()
        style_results = run_style_render_sweep(browser, chromium_major, run_root)
        determinism = run_double_run_determinism(browser, chromium_major, run_root)
        item_results, contact_sheets = run_item_render_sweep(
            browser, chromium_major, release, run_root
        )
        if EVIDENCE.exists():
            shutil.rmtree(EVIDENCE)
        EVIDENCE.mkdir(parents=True)
        copied_sheets = EVIDENCE / "catalog-contact-sheets"
        shutil.copytree(contact_sheets, copied_sheets)
        (EVIDENCE / "bm-16-acceptance.json").write_text(
            json.dumps(
                {
                    "chromiumMajor": chromium_major,
                    "contactSheets": sorted(
                        str(path.relative_to(EVIDENCE))
                        for path in copied_sheets.glob("*.svg")
                    ),
                    "determinism": determinism,
                    "items": item_results,
                    "styles": style_results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        cleanup_run_root(run_root, process_baseline)
    print(
        f"BM-16 {current_target_id()} acceptance passed:",
        "134-item visual sweep, 11 category sheets, 12-style sweep, "
        "double-run determinism, no URL entry;",
        "evidence:",
        EVIDENCE / "bm-16-acceptance.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
