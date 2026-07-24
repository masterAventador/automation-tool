#!/usr/bin/env python3
"""BM-07 locked motion-style validation and per-RenderJob freezing.

The model never reaches this module. The host gives it one already-selected
public style, the final ``frame.md`` text produced by the locked upstream
builder (or an advanced user import), bounded brand tokens and actual preview
copy. This module verifies the pinned source digest, validates the untrusted
frame specification and local assets, then atomically freezes an identical
copy into each private RenderJob.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final


class MotionStyleFreezeRejected(RuntimeError):
    """A locked source, frame schema, asset or containment boundary failed."""


def _reject(message: str) -> None:
    raise MotionStyleFreezeRejected(f"motion style freeze rejected: {message}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        _reject(message)


PUBLIC_PRESET_IDS: Final[frozenset[str]] = frozenset(
    {
        "biennale-yellow",
        "blockframe",
        "blue-professional",
        "bold-poster",
        "broadside",
        "capsule",
        "cartesian",
        "cobalt-grid",
        "coral",
        "creative-mode",
        "daisy-days",
        "editorial-forest",
    }
)
MAX_FRAME_BYTES: Final = 512_000
MAX_ASSET_BYTES: Final = 32 * 1024 * 1024
_HEX_COLOR: Final = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONT_FAMILY: Final = re.compile(r"^[\w .()'\-]{1,80}$", re.UNICODE)
_DIGEST: Final = re.compile(r"^[0-9a-f]{64}$")
_RELATIVE_SEGMENT: Final = re.compile(r"^[^/\\\x00]+$")
_URL_REFERENCE: Final = re.compile(r"url\(\s*(['\"]?)([^'\"\)]+)\1\s*\)", re.IGNORECASE)
_REMOTE_OR_ACTIVE: Final = re.compile(
    r"(?i)(?:https?|wss?|ftp|file|javascript|data):|(?<!:)//|"
    r"<\s*(?:script|iframe|object|embed|link|base)\b|"
    r"\bon[a-z]+\s*=|@import\b|fetch\s*\(|xmlhttprequest|websocket|"
    r"child_process|subprocess|os\.system|powershell|cmd\.exe"
)
_COLOR_VALUE: Final = re.compile(
    r"^(?:#[0-9a-fA-F]{6}|rgba?\(\s*\d+(?:\.\d+)?(?:\s*[,/]\s*|\s+)"
    r"\d+(?:\.\d+)?(?:\s*[,/]\s*|\s+)\d+(?:\.\d+)?"
    r"(?:\s*[,/]\s*[\d.]+%?)?\s*\))$",
    re.IGNORECASE,
)


def _exact_record(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _reject(f"{label} has an unexpected schema")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _validate_relative(relative: object) -> str:
    if type(relative) is not str or not relative or relative.startswith(("/", "\\")):
        _reject("asset path must be a non-empty relative path")
    parts = relative.split("/")
    if any(
        part in ("", ".", "..") or _RELATIVE_SEGMENT.fullmatch(part) is None
        for part in parts
    ):
        _reject("asset path is not a clean relative POSIX path")
    return relative


def _real_directory(root: Path, label: str) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        _reject(f"{label} must be an absolute path")
    if root.is_symlink() or not root.is_dir():
        _reject(f"{label} must be a real non-symlink directory")
    return root.resolve(strict=True)


def _resolve_local_asset(workspace: Path, relative: str) -> Path:
    clean = _validate_relative(relative)
    lexical = workspace
    for segment in clean.split("/"):
        lexical /= segment
        if lexical.is_symlink():
            _reject("asset path must not traverse a symlink")
    target = lexical.resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        _reject("asset path escapes the workspace")
    if target.is_symlink() or not target.is_file():
        _reject("asset must be a regular non-symlink workspace file")
    if target.stat().st_size <= 0 or target.stat().st_size > MAX_ASSET_BYTES:
        _reject("asset size is out of range")
    return target


def _validate_font_asset(path: Path) -> None:
    suffix = path.suffix.lower()
    _require(suffix in {".woff2", ".woff", ".ttf", ".otf"}, "unsupported font format")
    prefix = path.read_bytes()[:4]
    valid = {
        ".woff2": b"wOF2",
        ".woff": b"wOFF",
        ".ttf": b"\x00\x01\x00\x00",
        ".otf": b"OTTO",
    }
    _require(prefix == valid[suffix], "font file signature is invalid")


def _validate_logo_asset(path: Path) -> None:
    suffix = path.suffix.lower()
    _require(
        suffix in {".png", ".jpg", ".jpeg", ".webp"},
        "logo must be a bounded bitmap asset",
    )
    prefix = path.read_bytes()[:12]
    valid = (
        (suffix == ".png" and prefix.startswith(b"\x89PNG\r\n\x1a\n"))
        or (
            suffix in {".jpg", ".jpeg"}
            and prefix.startswith(b"\xff\xd8\xff")
        )
        or (
            suffix == ".webp"
            and prefix.startswith(b"RIFF")
            and prefix[8:12] == b"WEBP"
        )
    )
    _require(valid, "logo file signature is invalid")


@dataclass(frozen=True)
class BrandTokens:
    primary_color: str | None = None
    secondary_color: str | None = None
    font_family: str | None = None
    font_asset: str | None = None
    logo_asset: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("primary color", self.primary_color),
            ("secondary color", self.secondary_color),
        ):
            _require(
                value is None
                or (
                    type(value) is str
                    and _HEX_COLOR.fullmatch(value) is not None
                ),
                f"{label} must be #rrggbb",
            )
        _require(
            (self.font_family is None) == (self.font_asset is None),
            "font family and local font asset must be provided together",
        )
        if self.font_family is not None:
            _require(
                type(self.font_family) is str
                and _FONT_FAMILY.fullmatch(self.font_family) is not None,
                "font family is malformed",
            )
            _validate_relative(self.font_asset)
        if self.logo_asset is not None:
            _validate_relative(self.logo_asset)

    def canonical_value(self) -> dict[str, str | None]:
        return {
            "fontAsset": self.font_asset,
            "fontFamily": self.font_family,
            "logoAsset": self.logo_asset,
            "primaryColor": self.primary_color,
            "secondaryColor": self.secondary_color,
        }


@dataclass(frozen=True)
class PreviewContent:
    headline: str
    body: str

    def __post_init__(self) -> None:
        _require(
            type(self.headline) is str and 1 <= len(self.headline.strip()) <= 80,
            "preview headline is out of range",
        )
        _require(
            type(self.body) is str and 1 <= len(self.body.strip()) <= 240,
            "preview body is out of range",
        )

    def canonical_value(self) -> dict[str, str]:
        return {"body": self.body, "headline": self.headline}


@dataclass(frozen=True)
class LockedStyleSource:
    style_preset_id: str
    source_path: str
    sha256: str
    upstream_version: str
    upstream_commit: str


@dataclass(frozen=True)
class FrozenAsset:
    path: str
    sha256: str
    size_bytes: int

    def canonical_value(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
        }


@dataclass(frozen=True)
class FrozenMotionStyle:
    style_preset_id: str
    upstream_version: str
    upstream_commit: str
    source_frame_sha256: str
    brand_tokens_sha256: str
    frozen_frame_sha256: str
    frame_artifact_path: str
    assets: tuple[FrozenAsset, ...]
    preview_content: PreviewContent

    def canonical_value(self) -> dict[str, object]:
        return {
            "assets": [asset.canonical_value() for asset in self.assets],
            "brandTokensSha256": self.brand_tokens_sha256,
            "frameArtifactPath": self.frame_artifact_path,
            "frozenFrameSha256": self.frozen_frame_sha256,
            "previewContent": self.preview_content.canonical_value(),
            "schemaVersion": "motion-style-freeze.v1",
            "sourceFrameSha256": self.source_frame_sha256,
            "stylePresetId": self.style_preset_id,
            "upstreamCommit": self.upstream_commit,
            "upstreamVersion": self.upstream_version,
        }


def load_locked_style_sources(
    *, contract_path: Path, vendor_root: Path
) -> dict[str, LockedStyleSource]:
    try:
        document = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _reject("style freeze contract is unreadable")
        raise AssertionError from None
    data = _exact_record(
        document,
        {
            "schema_version",
            "policy",
            "upstream_version",
            "upstream_commit",
            "source_root",
            "presets",
        },
        "style freeze contract",
    )
    _require(
        data["schema_version"] == 1 and data["policy"] == "fail_closed",
        "style freeze contract policy drifted",
    )
    _require(data["upstream_version"] == "v0.7.68", "upstream version drifted")
    _require(
        data["upstream_commit"] == "71d84ff27f1c2b2828f4fdf9015c3da4157140ee",
        "upstream commit drifted",
    )
    source_root = _validate_relative(data["source_root"])
    presets = data["presets"]
    _require(isinstance(presets, list) and len(presets) == 12, "preset count drifted")
    vendor = _real_directory(vendor_root, "vendor root")
    found: dict[str, LockedStyleSource] = {}
    for item in presets:
        entry = _exact_record(item, {"id", "path", "sha256"}, "style source")
        style_id = entry["id"]
        _require(style_id in PUBLIC_PRESET_IDS and style_id not in found, "style id drifted")
        relative = _validate_relative(entry["path"])
        digest = entry["sha256"]
        _require(
            type(digest) is str and _DIGEST.fullmatch(digest) is not None,
            "source digest is malformed",
        )
        source_relative = f"{source_root}/{relative}"
        source_lexical = vendor
        for segment in source_relative.split("/"):
            source_lexical /= segment
            if source_lexical.is_symlink():
                _reject("style source path must not traverse a symlink")
        source = source_lexical.resolve()
        try:
            source.relative_to(vendor / source_root)
        except ValueError:
            _reject("style source escapes the locked source root")
        if source.is_symlink() or not source.is_file():
            _reject("style source is missing or a symlink")
        _require(_sha256(source.read_bytes()) == digest, "style source digest drifted")
        found[style_id] = LockedStyleSource(
            style_preset_id=style_id,
            source_path=f"{source_root}/{relative}",
            sha256=digest,
            upstream_version=data["upstream_version"],
            upstream_commit=data["upstream_commit"],
        )
    _require(set(found) == PUBLIC_PRESET_IDS, "public style inventory drifted")
    return found


def _frontmatter(frame_markdown: str) -> str:
    lines = frame_markdown.splitlines()
    _require(len(lines) >= 3 and lines[0] == "---", "frame.md frontmatter is missing")
    try:
        closing = lines.index("---", 1)
    except ValueError:
        _reject("frame.md frontmatter is not closed")
        raise AssertionError from None
    _require(closing >= 3, "frame.md frontmatter is empty")
    return "\n".join(lines[1:closing])


def _validate_color_block(frontmatter: str) -> None:
    lines = frontmatter.splitlines()
    try:
        start = lines.index("colors:")
    except ValueError:
        _reject("frame.md colors schema is missing")
        raise AssertionError from None
    values: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line[0].isspace():
            break
        match = re.match(r"^\s+[\w-]+:\s*(.+?)(?:\s+#.*)?$", line)
        if match is None:
            continue
        raw = match.group(1).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        _require(_COLOR_VALUE.fullmatch(raw) is not None, "frame.md color value is invalid")
        values.append(raw)
    _require(len(values) >= 3, "frame.md must define at least three colors")


def validate_frame_markdown(
    frame_markdown: str,
    *,
    workspace_root: Path,
    brand_tokens: BrandTokens,
) -> None:
    _require(type(frame_markdown) is str, "frame.md must be text")
    raw = frame_markdown.encode("utf-8")
    _require(1 <= len(raw) <= MAX_FRAME_BYTES, "frame.md size is out of range")
    _require("\x00" not in frame_markdown, "frame.md contains NUL")
    workspace = _real_directory(workspace_root, "workspace root")
    _require(isinstance(brand_tokens, BrandTokens), "brand tokens are required")
    frontmatter = _frontmatter(frame_markdown)
    for key in ("version", "name"):
        _require(
            re.search(rf"(?m)^{key}:\s*\S", frontmatter) is not None,
            f"frame.md {key} is missing",
        )
    _require(
        re.search(r"(?m)^typography:\s*$", frontmatter) is not None,
        "frame.md typography schema is missing",
    )
    _require(
        re.search(r'fontFamily:\s*["\'][^"\']+["\']', frontmatter) is not None,
        "frame.md typography has no font family",
    )
    _validate_color_block(frontmatter)
    _require(
        _REMOTE_OR_ACTIVE.search(frame_markdown) is None,
        "frame.md contains a remote or active-content reference",
    )

    referenced_assets: set[str] = set()
    for match in _URL_REFERENCE.finditer(frame_markdown):
        relative = _validate_relative(match.group(2).strip())
        _resolve_local_asset(workspace, relative)
        referenced_assets.add(relative)

    lowered = frame_markdown.lower()
    if brand_tokens.primary_color is not None:
        _require(
            brand_tokens.primary_color.lower() in lowered,
            "primary brand color was not applied to frame.md",
        )
    if brand_tokens.secondary_color is not None:
        _require(
            brand_tokens.secondary_color.lower() in lowered,
            "secondary brand color was not applied to frame.md",
        )
    if brand_tokens.font_asset is not None:
        font = _resolve_local_asset(workspace, brand_tokens.font_asset)
        _validate_font_asset(font)
        _require(
            brand_tokens.font_family in frame_markdown,
            "brand font family was not applied to frame.md",
        )
        _require(
            brand_tokens.font_asset in referenced_assets,
            "brand font asset is not declared by frame.md",
        )
    if brand_tokens.logo_asset is not None:
        _validate_logo_asset(_resolve_local_asset(workspace, brand_tokens.logo_asset))


def freeze_motion_style(
    *,
    contract_path: Path,
    vendor_root: Path,
    workspace_root: Path,
    render_job_root: Path,
    style_preset_id: str,
    frame_markdown: str,
    brand_tokens: BrandTokens,
    preview_content: PreviewContent,
) -> FrozenMotionStyle:
    sources = load_locked_style_sources(
        contract_path=contract_path, vendor_root=vendor_root
    )
    _require(style_preset_id in sources, "style preset is not a locked public style")
    workspace = _real_directory(workspace_root, "workspace root")
    render_job = _real_directory(render_job_root, "RenderJob root")
    _require(not any(render_job.iterdir()), "RenderJob freeze target must be empty")
    _require(isinstance(preview_content, PreviewContent), "preview content is required")
    validate_frame_markdown(
        frame_markdown,
        workspace_root=workspace,
        brand_tokens=brand_tokens,
    )

    asset_paths = tuple(
        sorted(
            value
            for value in (brand_tokens.font_asset, brand_tokens.logo_asset)
            if value is not None
        )
    )
    source_assets = [
        (relative, _resolve_local_asset(workspace, relative)) for relative in asset_paths
    ]
    frozen_assets = tuple(
        FrozenAsset(
            path=relative,
            sha256=_sha256(source.read_bytes()),
            size_bytes=source.stat().st_size,
        )
        for relative, source in source_assets
    )
    source = sources[style_preset_id]
    frame_raw = frame_markdown.encode("utf-8")
    frozen = FrozenMotionStyle(
        style_preset_id=style_preset_id,
        upstream_version=source.upstream_version,
        upstream_commit=source.upstream_commit,
        source_frame_sha256=source.sha256,
        brand_tokens_sha256=_sha256(
            _canonical_json(brand_tokens.canonical_value())
        ),
        frozen_frame_sha256=_sha256(frame_raw),
        frame_artifact_path="frame.md",
        assets=frozen_assets,
        preview_content=preview_content,
    )

    staging_raw = tempfile.mkdtemp(
        prefix=f".{render_job.name}-style-freeze-", dir=render_job.parent
    )
    staging = Path(staging_raw)
    try:
        (staging / "frame.md").write_bytes(frame_raw)
        for relative, source_path in source_assets:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
        (staging / "style-freeze.json").write_bytes(
            json.dumps(
                frozen.canonical_value(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8")
            + b"\n"
        )
        render_job.rmdir()
        os.replace(staging, render_job)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return frozen


__all__ = [
    "BrandTokens",
    "FrozenAsset",
    "FrozenMotionStyle",
    "LockedStyleSource",
    "MotionStyleFreezeRejected",
    "PreviewContent",
    "freeze_motion_style",
    "load_locked_style_sources",
    "validate_frame_markdown",
]
