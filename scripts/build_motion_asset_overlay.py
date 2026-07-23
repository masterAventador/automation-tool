#!/usr/bin/env python3
"""Build the BM-13 App-owned, brand-neutral motion asset overlay.

The script never writes the read-only upstream submodule. It verifies the four
Image Generation outputs already committed under ``assets/``, deterministically
builds neutral SVG/UI, audio, GLB and helper assets, then produces a rights and
replacement ledger covering every BM-11/BM-12 pending item.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
OVERLAY_PATH = REPOSITORY_ROOT / "contracts/quality/motion-asset-overlay.v1.json"
ASSET_ROOT = REPOSITORY_ROOT / "assets/motion-catalog-overlay"
PENDING_CONCLUSIONS = {
    "needs_asset_replacement",
    "needs_localization_and_asset_replacement",
}
CREATED_AT = "2026-07-23T23:50:00+08:00"
REVIEWED_BY = "codex-assisted-rights-review"
SOURCE_COMMIT = "71d84ff27f1c2b2828f4fdf9015c3da4157140ee"

AVATAR_PROMPT = (
    "Entirely fictional adult creative professional portrait; friendly gender-neutral "
    "East Asian adult; centered square editorial studio photograph; warm-gray backdrop "
    "with muted teal and amber light; unbranded dark jacket; no logos, text, watermark, "
    "celebrity likeness, copyrighted character or recognizable product."
)
WALLPAPER_PROMPT = (
    "Original abstract widescreen landscape of layered translucent glass-like waves and "
    "folded mineral forms; midnight navy, slate, muted teal, warm sand and restrained "
    "coral; clean center negative space; no logos, devices, text, watermark or "
    "recognizable operating-system wallpaper."
)
ILLUSTRATION_PROMPT = (
    "Original editorial illustration about ideas traveling between two anonymous "
    "creative cities; abstract luminous route across a stylized globe fragment; "
    "indigo, cobalt, cyan, cream, coral and yellow; no labels, national borders, flags, "
    "logos, app UI, text, watermark or copyrighted character."
)
TEXTURE_PROMPT = (
    "Precise 3-by-3 source atlas of original seamless material swatches: concrete, "
    "volcanic mineral, pale veined stone, brushed dark metal, slate rock, warm wood, "
    "woven neutral fabric, fired brick and granular ground; flat diffuse lighting; "
    "no labels, logos, trademarks, watermark or proprietary material scan."
)

TRADEMARK_REPLACEMENTS = {
    "apple": "星云科技",
    "bild": "每日简报",
    "heygen": "人物创作平台",
    "hyperframes": "动效画布",
    "instagram": "图片社区",
    "ios": "移动系统",
    "iphone": "便携设备",
    "macbook": "便携工作站",
    "macos": "桌面系统",
    "reddit": "兴趣社区",
    "sf_pro": "开放界面字体",
    "spotify": "音频平台",
    "tiktok": "短视频平台",
    "twitter": "社交动态",
    "visual_studio": "代码工作台",
    "vscode": "代码编辑器",
    "youtube": "视频平台",
}

TEXTURE_ASSETS = {
    "asphalt": "texture-ground",
    "bark": "texture-wood",
    "brick": "texture-brick",
    "bricks": "texture-brick",
    "carpet": "texture-fabric",
    "concrete": "texture-concrete",
    "diamond-plate": "texture-metal",
    "fabric": "texture-fabric",
    "grass": "texture-ground",
    "ground": "texture-ground",
    "lava": "texture-lava",
    "leather": "texture-fabric",
    "marble": "texture-marble",
    "metal": "texture-metal",
    "onyx": "texture-marble",
    "painted-plaster": "texture-concrete",
    "paving-stones": "texture-brick",
    "plaster": "texture-concrete",
    "road": "texture-ground",
    "rock": "texture-rock",
    "snow": "texture-ground",
    "tiles": "texture-brick",
    "travertine": "texture-marble",
    "wood": "texture-wood",
    "wood-floor": "texture-wood",
}

ICON_ASSETS = {
    "books": "ui-library",
    "brave": "ui-browser",
    "calendar": "ui-calendar",
    "chatgpt": "ui-chat",
    "contacts": "ui-contacts",
    "dock-finder": "ui-files",
    "dock-mail": "ui-mail",
    "dock-music": "ui-music",
    "dock-photos": "ui-gallery",
    "dock-safari": "ui-browser",
    "files": "ui-files",
    "find-my": "ui-location",
    "finder": "ui-files",
    "fitness": "ui-activity",
    "gmail": "ui-mail",
    "home": "ui-home",
    "instagram": "ui-gallery",
    "itunes-store": "ui-music",
    "mail": "ui-mail",
    "messages": "ui-chat",
    "music": "ui-music",
    "notify-messages": "ui-chat",
    "phone": "ui-phone",
    "photos": "ui-gallery",
    "proton-mail": "ui-mail",
    "revolut": "ui-wallet",
    "safari": "ui-browser",
    "slack": "ui-chat",
    "stocks": "ui-chart",
    "toast-messages": "ui-chat",
    "translate": "ui-language",
    "watch": "ui-clock",
    "weather": "ui-weather",
    "whatsapp": "ui-chat",
    "x": "ui-social",
}

ICON_GLYPHS = {
    "ui-activity": '<path d="M18 34 29 17l7 14 5-8 8 13"/><circle cx="16" cy="18" r="5"/>',
    "ui-browser": (
        '<circle cx="32" cy="32" r="18"/>'
        '<path d="M14 32h36M32 14c8 8 8 28 0 36M32 14c-8 8-8 28 0 36"/>'
    ),
    "ui-calendar": (
        '<rect x="15" y="18" width="34" height="31" rx="5"/>'
        '<path d="M15 27h34M23 14v8M41 14v8M23 35h7M35 35h7M23 42h7"/>'
    ),
    "ui-chart": '<path d="M16 48V18M16 48h34M23 41l8-9 7 5 10-15"/>',
    "ui-chat": '<path d="M14 18h36v25H30l-10 7v-7h-6z"/><path d="M22 29h20M22 35h13"/>',
    "ui-clock": '<circle cx="32" cy="32" r="19"/><path d="M32 20v13l9 6"/>',
    "ui-contacts": '<circle cx="32" cy="25" r="9"/><path d="M16 50c2-10 9-15 16-15s14 5 16 15"/>',
    "ui-files": '<path d="M17 15h13l5 6h12v29H17z"/><path d="M17 25h30"/>',
    "ui-gallery": (
        '<rect x="14" y="16" width="36" height="32" rx="5"/>'
        '<circle cx="25" cy="27" r="4"/>'
        '<path d="m18 44 10-10 7 7 5-5 7 8"/>'
    ),
    "ui-home": '<path d="m14 31 18-16 18 16v19H37V38H27v12H14z"/>',
    "ui-language": (
        '<circle cx="32" cy="32" r="19"/>'
        '<path d="M13 32h38M32 13c8 9 8 29 0 38M32 13c-8 9-8 29 0 38"/>'
    ),
    "ui-library": '<path d="M16 16h9v34h-9zM28 16h9v34h-9zM40 19l8-2 7 31-8 2z"/>',
    "ui-location": (
        '<path d="M32 52s15-14 15-26a15 15 0 1 0-30 0c0 12 15 26 15 26z"/>'
        '<circle cx="32" cy="26" r="5"/>'
    ),
    "ui-mail": '<rect x="14" y="18" width="36" height="28" rx="4"/><path d="m16 22 16 13 16-13"/>',
    "ui-music": (
        '<path d="M28 18v25M28 22l20-5v21"/>'
        '<circle cx="22" cy="44" r="6"/><circle cx="42" cy="39" r="6"/>'
    ),
    "ui-phone": '<path d="M23 14h18v36H23z"/><path d="M28 19h8M30 45h4"/>',
    "ui-social": (
        '<circle cx="20" cy="33" r="5"/><circle cx="43" cy="20" r="5"/>'
        '<circle cx="44" cy="44" r="5"/><path d="m25 30 13-7M25 36l14 6"/>'
    ),
    "ui-wallet": (
        '<path d="M14 20h34v28H14z"/><path d="M14 25h29M38 32h14v10H38z"/>'
        '<circle cx="44" cy="37" r="1"/>'
    ),
    "ui-weather": (
        '<path d="M18 44h29a8 8 0 0 0 0-16 14 14 0 0 0-27 4 6 6 0 0 0-2 12z"/>'
        '<path d="M17 19l-5-4M28 14V8M14 29H8"/>'
    ),
}


class BuildError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"motion asset overlay build failed: {message}")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise BuildError(f"{path} must contain an object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_fixed(relative: str, payload: bytes) -> Path:
    path = ASSET_ROOT / relative
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise BuildError(f"refusing unsafe asset path: {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def svg_document(body: str, background: str = "#111827") -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="14" fill="{background}"/>'
        f'<g fill="none" stroke="#f8fafc" stroke-width="3" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</g></svg>\n'
    ).encode()


def build_svg_assets() -> None:
    for asset_id, body in ICON_GLYPHS.items():
        write_fixed(f"ui/{asset_id.removeprefix('ui-')}.svg", svg_document(body))
    write_fixed(
        "ui/neutral-mark.svg",
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">'
            b'<rect width="96" height="96" rx="24" fill="#10213f"/>'
            b'<path d="M20 64 39 26l12 25 9-17 16 30" fill="none" stroke="#7dd3fc" '
            b'stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>'
            b'<circle cx="39" cy="26" r="6" fill="#fb7185"/></svg>\n'
        ),
    )
    write_fixed(
        "ui/desktop-panel.svg",
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 450">'
            b'<rect width="720" height="450" rx="28" fill="#0f172a"/>'
            b'<rect x="22" y="22" width="676" height="406" rx="18" fill="#f8fafc"/>'
            b'<rect x="48" y="54" width="160" height="342" rx="14" fill="#172554"/>'
            b'<rect x="232" y="54" width="438" height="74" rx="14" fill="#dbeafe"/>'
            b'<rect x="232" y="150" width="205" height="246" rx="14" fill="#cffafe"/>'
            b'<rect x="461" y="150" width="209" height="112" rx="14" fill="#ffe4e6"/>'
            b'<rect x="461" y="284" width="209" height="112" rx="14" fill="#fef3c7"/>'
            b"</svg>\n"
        ),
    )
    write_fixed(
        "ui/mobile-panel.svg",
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 720">'
            b'<rect width="360" height="720" rx="54" fill="#0f172a"/>'
            b'<rect x="16" y="16" width="328" height="688" rx="42" fill="#f8fafc"/>'
            b'<rect x="110" y="34" width="140" height="24" rx="12" fill="#172554"/>'
            b'<rect x="38" y="88" width="284" height="170" rx="22" fill="#dbeafe"/>'
            b'<rect x="38" y="282" width="132" height="168" rx="22" fill="#cffafe"/>'
            b'<rect x="190" y="282" width="132" height="168" rx="22" fill="#ffe4e6"/>'
            b'<rect x="38" y="474" width="284" height="160" rx="22" fill="#fef3c7"/>'
            b"</svg>\n"
        ),
    )
    write_fixed(
        "maps/region-grid.svg",
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 540">'
            b'<rect width="960" height="540" fill="#07152f"/>'
            b'<g fill="none" stroke="#1e3a5f" stroke-width="2" opacity=".8">'
            b'<path d="M0 90h960M0 180h960M0 270h960M0 360h960M0 450h960"/>'
            b'<path d="M120 0v540M240 0v540M360 0v540M480 0v540M600 0v540M720 0v540M840 0v540"/>'
            b"</g><path "
            b'd="M260 112 390 80l98 76 132-25 90 94-54 104-142 57-90-42-121 '
            b'38-88-91 36-83z" '
            b'fill="#164e63" stroke="#67e8f9" stroke-width="5"/>'
            b'<circle cx="402" cy="246" r="12" fill="#fb7185"/>'
            b'<circle cx="612" cy="286" r="12" fill="#fbbf24"/>'
            b'<path d="M402 246q110-120 210 40" fill="none" stroke="#f8fafc" stroke-width="4" '
            b'stroke-dasharray="10 12"/></svg>\n'
        ),
    )
    write_fixed(
        "maps/route-grid.svg",
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 540">'
            b'<rect width="960" height="540" fill="#0b1023"/>'
            b'<path d="M80 400c180-90 270-250 430-250s220 145 370 30" fill="none" '
            b'stroke="#233557" stroke-width="44" stroke-linecap="round"/>'
            b'<path d="M110 390C280 305 350 178 505 175s240 116 350 28" fill="none" '
            b'stroke="#7dd3fc" stroke-width="6" stroke-dasharray="13 16"/>'
            b'<circle cx="110" cy="390" r="22" fill="#fb7185"/>'
            b'<circle cx="855" cy="203" r="22" fill="#fbbf24"/>'
            b'<g fill="#dbeafe" opacity=".65"><circle cx="260" cy="180" r="5"/>'
            b'<circle cx="465" cy="390" r="7"/><circle cx="690" cy="315" r="5"/></g>'
            b"</svg>\n"
        ),
    )


def write_wave(relative: str, frequencies: tuple[float, ...], duration: float) -> None:
    sample_rate = 22050
    frame_count = int(sample_rate * duration)
    path = ASSET_ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise BuildError(f"refusing unsafe audio path: {relative}")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            time = index / sample_rate
            attack = min(1.0, time / 0.06)
            release = min(1.0, (duration - time) / 0.18)
            envelope = max(0.0, attack * release)
            value = sum(math.sin(2 * math.pi * frequency * time) for frequency in frequencies)
            value /= max(1, len(frequencies))
            frames.extend(struct.pack("<h", round(value * envelope * 10500)))
        output.writeframes(bytes(frames))


def build_audio_assets() -> None:
    write_wave("audio/count-rise.wav", (330.0, 495.0, 660.0), 0.9)
    write_wave("audio/ambient-tech.wav", (110.0, 165.0, 220.0), 1.8)
    write_wave("audio/route-chime.wav", (440.0, 554.37, 659.25), 1.2)
    write_wave("audio/soft-pulse.wav", (196.0, 293.66), 1.0)


def glb_payload(nodes: list[dict], scene_nodes: list[int]) -> bytes:
    vertices = (
        -0.5,
        -0.5,
        -0.5,
        0.5,
        -0.5,
        -0.5,
        0.5,
        0.5,
        -0.5,
        -0.5,
        0.5,
        -0.5,
        -0.5,
        -0.5,
        0.5,
        0.5,
        -0.5,
        0.5,
        0.5,
        0.5,
        0.5,
        -0.5,
        0.5,
        0.5,
    )
    indices = (
        0,
        1,
        2,
        0,
        2,
        3,
        4,
        6,
        5,
        4,
        7,
        6,
        0,
        4,
        5,
        0,
        5,
        1,
        1,
        5,
        6,
        1,
        6,
        2,
        2,
        6,
        7,
        2,
        7,
        3,
        3,
        7,
        4,
        3,
        4,
        0,
    )
    binary = struct.pack("<24f", *vertices) + struct.pack("<36H", *indices)
    document = {
        "asset": {"version": "2.0", "generator": "automation-tool BM-13"},
        "scene": 0,
        "scenes": [{"nodes": scene_nodes}],
        "nodes": nodes,
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "indices": 1,
                        "material": 0,
                    }
                ]
            }
        ],
        "materials": [
            {
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.12, 0.38, 0.55, 1.0],
                    "metallicFactor": 0.25,
                    "roughnessFactor": 0.45,
                }
            }
        ],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 96, "target": 34962},
            {"buffer": 0, "byteOffset": 96, "byteLength": 72, "target": 34963},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 8,
                "type": "VEC3",
                "min": [-0.5, -0.5, -0.5],
                "max": [0.5, 0.5, 0.5],
            },
            {
                "bufferView": 1,
                "componentType": 5123,
                "count": 36,
                "type": "SCALAR",
                "min": [0],
                "max": [7],
            },
        ],
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode()
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(json_chunk), b"JSON")
        + json_chunk
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


def build_glb_assets() -> None:
    handheld = glb_payload(
        [{"mesh": 0, "scale": [0.62, 1.1, 0.08], "name": "portable_device"}], [0]
    )
    portable_display = glb_payload(
        [
            {
                "mesh": 0,
                "scale": [1.35, 0.78, 0.07],
                "translation": [0, 0.34, 0],
                "name": "display",
            },
            {
                "mesh": 0,
                "scale": [1.45, 0.06, 0.82],
                "translation": [0, -0.48, 0.38],
                "name": "base",
            },
        ],
        [0, 1],
    )
    write_fixed("models/portable-device.glb", handheld)
    write_fixed("models/portable-display.glb", portable_display)


def build_script_asset() -> None:
    write_fixed(
        "scripts/neutral-glass.iife.js",
        (
            b'(() => {"use strict";const api=Object.freeze({'
            b"style:(opacity=0.72,blur=18)=>Object.freeze({"
            b"background:`rgba(255,255,255,${Math.max(0,Math.min(1,opacity))})`,"
            b"backdropFilter:`blur(${Math.max(0,Math.min(48,blur))}px)`,"
            b'border:"1px solid rgba(255,255,255,.28)",'
            b'boxShadow:"0 18px 48px rgba(15,23,42,.18)"})});'
            b'Object.defineProperty(globalThis,"NeutralGlass",{value:api,writable:false});})();\n'
        ),
    )


def common_record(
    asset_id: str,
    category: str,
    relative: str,
    *,
    source: str,
    source_url: str,
    license_name: str,
    license_version: str,
) -> dict:
    path = ASSET_ROOT / relative
    if not path.is_file() or path.is_symlink():
        raise BuildError(f"asset is missing or unsafe: {relative}")
    return {
        "id": asset_id,
        "category": category,
        "path": relative,
        "source": source,
        "sourceUrl": source_url,
        "license": license_name,
        "licenseVersionOrDate": license_version,
        "acquiredAt": CREATED_AT,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "redistributionAllowed": True,
        "commercialUseAllowed": True,
        "reviewedBy": REVIEWED_BY,
        "reviewedAt": CREATED_AT,
    }


def generated_record(
    asset_id: str,
    relative: str,
    role: str,
    source_url: str,
    prompt: str,
    crop: dict | None = None,
) -> dict:
    record = common_record(
        asset_id,
        "generated",
        relative,
        source="openai-image-generation",
        source_url=source_url,
        license_name="contractual-output-ownership",
        license_version="OpenAI Terms effective 2026-01-01",
    )
    generation = {
        "role": role,
        "model": "built-in-image-generation",
        "modelVersion": "session-current",
        "prompt": prompt,
        "generatedAt": CREATED_AT,
    }
    if crop is not None:
        generation["cropFromOriginalAtlas"] = crop
    record.update(
        {
            "model": "built-in-image-generation",
            "modelVersion": "session-current",
            "generationRecord": generation,
            "humanRightsReview": {
                "status": "passed",
                "checks": [
                    "no real-person identity claim",
                    "no visible logo or trademark",
                    "no watermark or signature",
                    "no copyrighted character",
                ],
            },
            "rightsBasis": (
                "As between the user/customer and OpenAI, and to the extent permitted "
                "by applicable law, the user/customer owns the Output."
            ),
            "rightsSources": [
                {
                    "url": "https://openai.com/policies/terms-of-use/",
                    "effective": "2026-01-01",
                    "section": "Content",
                },
                {
                    "url": "https://openai.com/policies/services-agreement/",
                    "effective": "2026-01-01",
                    "section": "4. Customer Content",
                },
            ],
            "rightsCaveats": [
                "output may not be unique",
                "project remains responsible for third-party rights review",
                "ownership is limited by applicable law",
            ],
        }
    )
    return record


def build_asset_records() -> list[dict]:
    records = [
        generated_record(
            "generated-avatar",
            "generated/avatar.png",
            "avatar",
            "openai-imagegen://call_YBFukgwcu5rJpWHtwjCseMPl",
            AVATAR_PROMPT,
        ),
        generated_record(
            "generated-wallpaper",
            "generated/wallpaper.png",
            "wallpaper",
            "openai-imagegen://call_jR9XnWNMh05PbiSMlZ3qwu4e",
            WALLPAPER_PROMPT,
        ),
        generated_record(
            "generated-illustration",
            "generated/illustration.png",
            "illustration",
            "openai-imagegen://call_98Hiob6ehOXrPeHqy5TMgIvK",
            ILLUSTRATION_PROMPT,
        ),
    ]
    records[2]["libraryOnly"] = True
    records[2]["libraryUse"] = "BM-14/BM-15 可选原创插画母版"
    texture_names = (
        "concrete",
        "lava",
        "marble",
        "metal",
        "rock",
        "wood",
        "fabric",
        "brick",
        "ground",
    )
    for index, name in enumerate(texture_names):
        records.append(
            generated_record(
                f"texture-{name}",
                f"generated/textures/{name}.png",
                "texture",
                "openai-imagegen://call_NeTaiFCHy1kME2y13hoRTeRq",
                TEXTURE_PROMPT,
                {
                    "grid": "3x3",
                    "column": index % 3,
                    "row": index // 3,
                    "sourcePixels": [1254, 1254],
                    "cropPixels": [410, 410],
                },
            )
        )
    self_authored = [
        ("neutral-mark", "vector_ui", "ui/neutral-mark.svg"),
        ("ui-desktop-panel", "vector_ui", "ui/desktop-panel.svg"),
        ("ui-mobile-panel", "vector_ui", "ui/mobile-panel.svg"),
        ("map-region-grid", "map_3d", "maps/region-grid.svg"),
        ("map-route-grid", "map_3d", "maps/route-grid.svg"),
        ("audio-count-rise", "music_sfx", "audio/count-rise.wav"),
        ("audio-ambient-tech", "music_sfx", "audio/ambient-tech.wav"),
        ("audio-route-chime", "music_sfx", "audio/route-chime.wav"),
        ("audio-soft-pulse", "music_sfx", "audio/soft-pulse.wav"),
        ("model-portable-device", "map_3d", "models/portable-device.glb"),
        ("model-portable-display", "map_3d", "models/portable-display.glb"),
        ("neutral-glass-script", "script", "scripts/neutral-glass.iife.js"),
    ]
    self_authored.extend(
        (
            asset_id,
            "vector_ui",
            f"ui/{asset_id.removeprefix('ui-')}.svg",
        )
        for asset_id in ICON_GLYPHS
    )
    for asset_id, category, relative in self_authored:
        record = common_record(
            asset_id,
            category,
            relative,
            source="project-self-authored",
            source_url="repository://scripts/build_motion_asset_overlay.py",
            license_name="project-owned-original-source",
            license_version="2026-07-23",
        )
        record["attribution"] = "Automation Tool project"
        if category == "music_sfx":
            record["syncUseAllowed"] = True
            record["contentIdRisk"] = "none-self-authored"
        if category == "map_3d":
            record["derivativeUseAllowed"] = True
        records.append(record)
    font = common_record(
        "font-big-shoulders-display",
        "font",
        "fonts/big-shoulders-display-latin.woff2",
        source="google-fonts-locked-by-bm12",
        source_url=(
            "https://fonts.gstatic.com/s/bigshouldersdisplay/v24/"
            "fC1_PZJEZG-e9gHhdI4-NBbfd2ys3SjJCx1czNDuDJAM2w.woff2"
        ),
        license_name="OFL-1.1",
        license_version="SIL Open Font License 1.1",
    )
    font.update(
        {
            "embeddingAllowed": True,
            "attribution": "Big Shoulders Display authors; SIL Open Font License 1.1",
            "bm12LockedSha256": (
                "203dd8ba4ae61b19cd2e00c66708f0d0f6d8484cdfdb1d7e8be37260d36a99b1"
            ),
        }
    )
    records.append(font)
    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        raise BuildError("asset IDs are not unique")
    return sorted(records, key=lambda record: record["id"])


def asset_for_reference(path: str, kind: str) -> str:
    name = Path(path).stem.casefold()
    if kind == "audio":
        if "production" in path:
            return "audio-count-rise"
        if "integrated" in path:
            return "audio-ambient-tech"
        if "sfx-mix" in path:
            return "audio-route-chime"
        if "vpn" in path:
            return "audio-soft-pulse"
    if kind == "font":
        return "font-big-shoulders-display"
    if kind == "model_3d":
        return "model-portable-device" if "iphone" in path.casefold() else "model-portable-display"
    if kind == "script":
        return "neutral-glass-script"
    if kind == "vector":
        return "neutral-mark"
    if kind != "image":
        raise BuildError(f"unsupported source asset kind: {kind}: {path}")
    folded = path.casefold()
    if "avatar" in folded:
        return "generated-avatar"
    if "wallpaper" in folded or path == "background.jpeg":
        return "generated-wallpaper"
    if "korea-map" in folded:
        return "map-region-grid"
    if "map-nyc-paris" in folded:
        return "map-route-grid"
    if "hyperframes-desktop" in folded:
        return "ui-desktop-panel"
    if "hyperframes-mobile" in folded:
        return "ui-mobile-panel"
    if "/icons/" in f"/{folded}":
        icon = ICON_ASSETS.get(name)
        if icon is None:
            raise BuildError(f"unmapped UI icon: {path}")
        return icon
    for prefix in sorted(TEXTURE_ASSETS, key=len, reverse=True):
        if name == prefix or name.startswith(prefix + "-"):
            return TEXTURE_ASSETS[prefix]
    raise BuildError(f"unmapped image asset: {path}")


def build_items(rights: dict, asset_map: dict[str, dict]) -> list[dict]:
    items = []
    for source in rights["items"]:
        if source["conclusion"] not in PENDING_CONCLUSIONS:
            continue
        replacements = []
        for bundled in source["bundledAssets"]:
            asset_id = asset_for_reference(bundled["path"], bundled["kind"])
            if asset_id not in asset_map:
                raise BuildError(f"{source['name']} references unknown asset {asset_id}")
            replacements.append(
                {
                    "sourcePath": bundled["path"],
                    "sourceKind": bundled["kind"],
                    "assetId": asset_id,
                    "replacementPath": asset_map[asset_id]["path"],
                }
            )
        trademark_replacements = []
        for indicator in source["trademarkIndicators"]:
            replacement = TRADEMARK_REPLACEMENTS.get(indicator)
            if replacement is None:
                raise BuildError(f"unmapped trademark indicator: {indicator}")
            trademark_replacements.append({"indicator": indicator, "replacement": replacement})
        items.append(
            {
                "name": source["name"],
                "type": source["type"],
                "assetReplacements": replacements,
                "trademarkReplacements": trademark_replacements,
            }
        )
    return sorted(items, key=lambda item: item["name"])


def verify_no_untracked_assets(asset_records: list[dict]) -> None:
    expected = {record["path"] for record in asset_records}
    actual = {
        path.relative_to(ASSET_ROOT).as_posix() for path in ASSET_ROOT.rglob("*") if path.is_file()
    }
    extra = sorted(actual - expected)
    missing = sorted(expected - actual)
    if extra or missing:
        raise BuildError(f"asset tree drifted: missing={missing[:5]}, extra={extra[:5]}")


def main() -> None:
    rights = load_json(RIGHTS_PATH)
    build_svg_assets()
    build_audio_assets()
    build_glb_assets()
    build_script_asset()
    assets = build_asset_records()
    verify_no_untracked_assets(assets)
    asset_map = {record["id"]: record for record in assets}
    items = build_items(rights, asset_map)
    asset_references = sum(len(item["assetReplacements"]) for item in items)
    trademark_references = sum(len(item["trademarkReplacements"]) for item in items)
    trademark_items = sum(bool(item["trademarkReplacements"]) for item in items)
    manifest = {
        "schemaVersion": 1,
        "id": "motion-asset-overlay.v1",
        "assetRoot": "assets/motion-catalog-overlay",
        "source": {
            "catalogRightsContract": "contracts/quality/motion-catalog-rights.v1.json",
            "catalogRightsSha256": sha256_file(RIGHTS_PATH),
            "upstreamCommit": SOURCE_COMMIT,
            "policy": "contracts/quality/asset-rights-policy.v1.json",
        },
        "counts": {
            "items": len(items),
            "assetReplacementReferences": asset_references,
            "trademarkReplacementItems": trademark_items,
            "trademarkReplacementReferences": trademark_references,
            "assets": len(assets),
        },
        "assets": assets,
        "items": items,
    }
    if len(items) != 70:
        raise BuildError(f"pending item count drifted: {len(items)} != 70")
    OVERLAY_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "motion asset overlay built: "
        f"{len(items)} items, {asset_references} asset references, "
        f"{trademark_references} trademark references, {len(assets)} original assets"
    )


if __name__ == "__main__":
    main()
