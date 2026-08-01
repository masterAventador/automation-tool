#!/usr/bin/env python3
"""The subtitle fonts the release is allowed to redistribute, fetched at build time.

`contracts/quality/asset-rights-policy.v1.json` denies every asset by default,
so the register is the only place that can say a font may travel inside the
installer. Packaging, the candidate audit, the third-party notice projection and
the rights gate all read this one module rather than keeping their own list: a
font that is not cleared here cannot be frozen into the Worker, and a font that
is frozen in cannot avoid appearing on the disclosure page.

The font binaries are **not** checked into the repository. Thirty-odd megabytes
of binary in Git history cannot be removed again without rewriting history, and
every other large pinned artifact this product ships already avoids that: the
embedded Chromium is downloaded against a pinned digest and ffmpeg is compiled
from a pinned source tarball. The fonts follow the same rule — a locked upstream
URL, a locked SHA-256, and `video_runtime_cache` holding the result outside the
checkout so a rebuild happens exactly when the pin changes.

Three things are verified against the bytes rather than trusted from the
register, all of them before the bytes are used for anything:

* the digest and the length, so a substituted, truncated or tampered face never
  reaches the packager;
* the copyright notice inside the font's own ``name`` table, which the SIL Open
  Font License requires every copy to carry and which the disclosure page
  reproduces — so the notice on the page cannot drift from the notice inside the
  file a user receives;
* the same two checks again on the frozen candidate, so the audit covers what
  actually shipped and not merely what was downloaded.

There is deliberately no fallback. A font that cannot be fetched or does not
match its lock fails the build; it never degrades to "use a system font" or
"skip subtitles", because both of those produce a video that looks finished and
has empty boxes where the Chinese should be.
"""

from __future__ import annotations

import hashlib
import json
import struct
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ASSET_RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/asset-rights-policy.v1.json"
WORKER_CONTRACT_PATH = (
    REPOSITORY_ROOT / "contracts/quality/material-video-worker-package.v1.json"
)

# The one runtime that renders subtitles, and the directory the upstream WebUI
# lists fonts from. `webui/Main.py` only offers a face whose file name ends in
# `.ttf` or `.ttc`, so a font that ships under any other extension is invisible
# to the user and unusable as a default.
BUNDLE_TARGET = "material-video-worker"
PACKAGED_FONT_DIRECTORY = "upstream/resource/fonts"
LISTABLE_FONT_SUFFIXES = (".ttf", ".ttc")

# Name of the cached artifact under `video_runtime_cache.cache_root()`, beside
# `media-toolchain`, `motion-video-worker` and `material-video-worker`.
CACHE_NAME = "subtitle-fonts"

# Downloads are pinned to one upstream repository at one release tag. Keeping
# the prefix in code rather than only in the register means a rewritten
# `sourceUrl` cannot quietly point the build somewhere else.
FONT_SOURCE_URL_PREFIX = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/"
)
FETCH_TIMEOUT_SECONDS = 120
FETCH_ATTEMPTS = 3
MAXIMUM_FETCH_BYTES = 64 * 1024 * 1024

# The SIL OFL is a template: unlike MIT or Apache-2.0 its text names no
# copyright holder, so the holder's notice has to be published next to it.
OPEN_FONT_LICENSE = "OFL-1.1"
OPEN_FONT_LICENSE_MARKER = "SIL OPEN FONT LICENSE Version 1.1"

_NAME_ID_COPYRIGHT = 0
# Windows/Unicode BMP/en-US first, then the Macintosh/Roman/English fallback.
_NAME_RECORD_PREFERENCE = ((3, 1, 0x409), (1, 0, 0))


class SubtitleFontRightsError(RuntimeError):
    """A font is not cleared for redistribution inside the installer."""


class SubtitleFontUnavailable(SubtitleFontRightsError):
    """The cleared fonts could not be fetched, or did not match their lock."""


@dataclass(frozen=True)
class BundledSubtitleFont:
    """One cleared face: where it comes from, and what it must be."""

    id: str
    packaged_name: str
    source_url: str
    upstream_file_name: str
    sha256: str
    bytes: int
    license: str
    attribution: str


@dataclass(frozen=True)
class PackagedLicenseNotice:
    """The licence text that travels with the fonts inside the package."""

    packaged_name: str
    source_url: str
    sha256: str
    bytes: int


def _reject(message: str) -> None:
    raise SubtitleFontRightsError(message)


def load_asset_rights(path: Path = ASSET_RIGHTS_PATH) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _reject(f"cannot read the asset rights register: {error}")
    if not isinstance(value, dict):
        _reject("the asset rights register must contain an object")
    return value


def load_worker_contract(path: Path = WORKER_CONTRACT_PATH) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _reject(f"cannot read the material Worker package contract: {error}")
    if not isinstance(value, dict):
        _reject("the material Worker package contract must contain an object")
    return value


def _locked_url(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(FONT_SOURCE_URL_PREFIX):
        _reject(f"{field} must point at the locked upstream release")
    assert isinstance(value, str)
    return value


def _digest(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _reject(f"{field} must be a lowercase SHA-256 digest")
    assert isinstance(value, str)
    return value


def _positive_length(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        _reject(f"{field} must be a positive byte count")
    assert isinstance(value, int)
    return value


def _packaged_file_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        _reject(f"{field} must be a non-empty file name")
    assert isinstance(value, str)
    if value != PurePosixPath(value).name or value in {".", ".."} or "\\" in value:
        _reject(f"{field} must be a bare file name inside the font directory")
    return value


def font_copyright_notice(payload: bytes) -> str:
    """Read the copyright the font binary itself declares.

    OFL section 1 requires every copy of the Font Software to carry the
    copyright notice. Fonts store it in ``name`` ID 0, which is exactly the
    "appropriate machine-readable metadata field" the licence points at, so this
    is the notice a recipient actually receives with the file.
    """
    offset = 0
    if payload[:4] == b"ttcf":
        if len(payload) < 16:
            _reject("font collection header is truncated")
        offset = struct.unpack_from(">I", payload, 12)[0]
    found: dict[tuple[int, int, int], str] = {}
    try:
        table_count = struct.unpack_from(">H", payload, offset + 4)[0]
        directory = {}
        for index in range(table_count):
            record = offset + 12 + 16 * index
            tag = payload[record : record + 4]
            table_offset, table_length = struct.unpack_from(">II", payload, record + 8)
            directory[tag] = (table_offset, table_length)
        name_offset, _ = directory[b"name"]
        _, record_count, strings = struct.unpack_from(">HHH", payload, name_offset)
        for index in range(record_count):
            record = name_offset + 6 + 12 * index
            platform, encoding, language, name_id, length, string_offset = (
                struct.unpack_from(">HHHHHH", payload, record)
            )
            if name_id != _NAME_ID_COPYRIGHT:
                continue
            start = name_offset + strings + string_offset
            raw = payload[start : start + length]
            codec = "utf-16-be" if platform in (0, 3) else "latin-1"
            found[(platform, encoding, language)] = raw.decode(codec).strip()
    except (KeyError, struct.error, UnicodeDecodeError, IndexError) as error:
        _reject(f"cannot read the font copyright notice: {error}")
    for key in _NAME_RECORD_PREFERENCE:
        notice = found.get(key)
        if notice:
            return notice
    _reject("the font declares no copyright notice")
    raise AssertionError("unreachable")


def verify_font_payload(font: BundledSubtitleFont, payload: bytes) -> None:
    """Refuse bytes that are not the reviewed font, before anything uses them."""
    if len(payload) != font.bytes:
        raise SubtitleFontUnavailable(
            f"{font.packaged_name}: the font is {len(payload)} bytes but the register "
            f"locks {font.bytes}, so its digest cannot be trusted"
        )
    if hashlib.sha256(payload).hexdigest() != font.sha256:
        raise SubtitleFontUnavailable(
            f"{font.packaged_name}: the font does not match its locked digest"
        )
    try:
        notice = font_copyright_notice(payload)
    except SubtitleFontRightsError as error:
        raise SubtitleFontUnavailable(f"{font.packaged_name}: {error}") from error
    if notice != font.attribution:
        raise SubtitleFontUnavailable(
            f"{font.packaged_name}: the font carries a copyright notice the register "
            "does not publish"
        )


def verify_license_payload(notice: PackagedLicenseNotice, payload: bytes) -> None:
    """Refuse a licence text that is not the licence the fonts were cleared under."""
    if len(payload) != notice.bytes:
        raise SubtitleFontUnavailable(
            f"{notice.packaged_name}: the licence text is {len(payload)} bytes but the "
            f"register locks {notice.bytes}"
        )
    if hashlib.sha256(payload).hexdigest() != notice.sha256:
        raise SubtitleFontUnavailable(
            f"{notice.packaged_name}: the licence text does not match its locked digest"
        )
    if OPEN_FONT_LICENSE_MARKER not in payload.decode("utf-8", errors="replace"):
        raise SubtitleFontUnavailable(
            f"{notice.packaged_name}: the text is not the SIL Open Font License"
        )


def _required_fields(rights: dict) -> tuple[str, ...]:
    shared = rights.get("distributionRequiredFields")
    categories = rights.get("requiredCategories")
    if not isinstance(shared, list) or not shared:
        _reject("the asset rights register requires no distribution information")
    if not isinstance(categories, dict):
        _reject("the asset rights register covers no category")
    assert isinstance(categories, dict)
    font_fields = categories.get("font")
    if not isinstance(font_fields, list) or not font_fields:
        _reject("the asset rights register requires no font information")
    assert isinstance(shared, list) and isinstance(font_fields, list)
    return tuple(dict.fromkeys([*shared, *font_fields]))


def _font_entries(rights: dict) -> list[dict]:
    entries = rights.get("entries")
    if not isinstance(entries, list):
        _reject("the asset rights register has no register")
    assert isinstance(entries, list)
    selected = []
    for entry in entries:
        if not isinstance(entry, dict):
            _reject("an asset rights entry is not an object")
        assert isinstance(entry, dict)
        if entry.get("category") == "font" and entry.get("bundledIn") == BUNDLE_TARGET:
            selected.append(entry)
    return selected


def bundled_subtitle_fonts(
    rights: dict | None = None,
) -> tuple[BundledSubtitleFont, ...]:
    """Return every font cleared to ship inside the material-video Worker.

    Declaration only: this reads no font bytes and reaches no network, so the
    rights gate and the disclosure projection stay offline and deterministic.
    The bytes are checked where they are fetched and again where the frozen
    candidate is audited.
    """
    rights = load_asset_rights() if rights is None else rights
    if rights.get("defaultDecision") != "deny":
        _reject("the asset rights register no longer denies unregistered assets")
    required = _required_fields(rights)

    fonts: list[BundledSubtitleFont] = []
    for entry in _font_entries(rights):
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            _reject("a bundled font entry has no id")
        assert isinstance(identifier, str)
        for field in required:
            if field not in entry or entry[field] in (None, "", []):
                _reject(f"{identifier}: rights entry is missing {field}")
        for permission in (
            "redistributionAllowed",
            "commercialUseAllowed",
            "embeddingAllowed",
        ):
            if entry.get(permission) is not True:
                _reject(f"{identifier}: rights entry does not clear {permission}")
        if entry.get("license") != OPEN_FONT_LICENSE:
            _reject(
                f"{identifier}: only {OPEN_FONT_LICENSE} fonts may be redistributed"
            )
        packaged_name = _packaged_file_name(
            entry.get("packagedName"), f"{identifier}: packagedName"
        )
        if not packaged_name.endswith(LISTABLE_FONT_SUFFIXES):
            _reject(
                f"{identifier}: packagedName must end in "
                f"{' or '.join(LISTABLE_FONT_SUFFIXES)} or the WebUI cannot list it"
            )
        attribution = entry.get("attribution")
        assert isinstance(attribution, str)
        fonts.append(
            BundledSubtitleFont(
                id=identifier,
                packaged_name=packaged_name,
                source_url=_locked_url(
                    entry.get("sourceUrl"), f"{identifier}: sourceUrl"
                ),
                upstream_file_name=_packaged_file_name(
                    entry.get("upstreamFileName"), f"{identifier}: upstreamFileName"
                ),
                sha256=_digest(entry.get("sha256"), f"{identifier}: sha256"),
                bytes=_positive_length(entry.get("bytes"), f"{identifier}: bytes"),
                license=OPEN_FONT_LICENSE,
                attribution=attribution,
            )
        )

    if not fonts:
        _reject("no subtitle font is cleared for the material video Worker")
    names = [font.packaged_name for font in fonts]
    if len(set(names)) != len(names):
        _reject("two cleared fonts would land on the same packaged file name")
    return tuple(sorted(fonts, key=lambda font: font.packaged_name))


def packaged_license_notice(rights: dict | None = None) -> PackagedLicenseNotice:
    """Return the single licence text that ships beside the cleared fonts."""
    rights = load_asset_rights() if rights is None else rights
    declared = {
        (
            str(entry.get("packagedLicenseName")),
            str(entry.get("licenseTextUrl")),
            str(entry.get("licenseTextSha256")),
            entry.get("licenseTextBytes"),
        )
        for entry in _font_entries(rights)
    }
    if len(declared) != 1:
        _reject("the cleared fonts do not share exactly one licence text")
    packaged, url, digest, length = next(iter(declared))
    return PackagedLicenseNotice(
        packaged_name=_packaged_file_name(packaged, "packagedLicenseName"),
        source_url=_locked_url(url, "licenseTextUrl"),
        sha256=_digest(digest, "licenseTextSha256"),
        bytes=_positive_length(length, "licenseTextBytes"),
    )


def default_subtitle_font_name(contract: dict | None = None) -> str:
    """Return the face the WebUI must preselect once the upstream default is gone."""
    contract = load_worker_contract() if contract is None else contract
    build = contract.get("build")
    if not isinstance(build, dict):
        _reject("the material Worker package contract declares no build section")
    assert isinstance(build, dict)
    name = build.get("defaultSubtitleFontName")
    if not isinstance(name, str) or not name:
        _reject(
            "the material Worker package contract declares no default subtitle font"
        )
    assert isinstance(name, str)
    registered = {font.packaged_name for font in bundled_subtitle_fonts()}
    if name not in registered:
        _reject(f"the default subtitle font {name} is not a cleared font")
    return name


def _fetch_locked_url(url: str) -> bytes:
    """Fetch one locked artifact, refusing anything unexpected."""
    if not url.startswith(FONT_SOURCE_URL_PREFIX):
        raise SubtitleFontUnavailable(f"{url} is not the locked upstream release")
    last_error: urllib.error.URLError | OSError | ValueError | None = None
    for _attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
                payload = response.read(MAXIMUM_FETCH_BYTES + 1)
            break
        except (urllib.error.URLError, OSError, ValueError) as error:
            last_error = error
    else:
        assert last_error is not None
        raise SubtitleFontUnavailable(
            f"cannot fetch {url}: {last_error}"
        ) from last_error
    if len(payload) > MAXIMUM_FETCH_BYTES:
        raise SubtitleFontUnavailable(f"{url} is larger than the fetch limit")
    return payload


def ensure_subtitle_fonts(
    *,
    root: Path | None = None,
    rights: dict | None = None,
    fonts: tuple[BundledSubtitleFont, ...] | None = None,
    notice: PackagedLicenseNotice | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> Path:
    """Return the cached directory holding every cleared font and its licence.

    Fetches only when the pinning contract changed, verifies every byte before
    it is written, and fails closed on anything else. `video_runtime_cache`
    deletes a partially built directory when this raises, so a rejected download
    can never leave a half-populated font directory for the packager to use.
    """
    from video_runtime_cache import ensure_cached

    resolved_fonts = bundled_subtitle_fonts(rights) if fonts is None else fonts
    resolved_notice = packaged_license_notice(rights) if notice is None else notice
    download = _fetch_locked_url if fetch is None else fetch

    def obtain(url: str) -> bytes:
        try:
            return download(url)
        except SubtitleFontUnavailable:
            raise
        except Exception as error:  # noqa: BLE001
            raise SubtitleFontUnavailable(f"cannot fetch {url}: {error}") from error

    def build(destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        for font in resolved_fonts:
            payload = obtain(font.source_url)
            verify_font_payload(font, payload)
            (destination / font.packaged_name).write_bytes(payload)
        payload = obtain(resolved_notice.source_url)
        verify_license_payload(resolved_notice, payload)
        (destination / resolved_notice.packaged_name).write_bytes(payload)

    return ensure_cached(
        name=CACHE_NAME, contracts=[ASSET_RIGHTS_PATH], build=build, root=root
    )


__all__ = [
    "ASSET_RIGHTS_PATH",
    "BUNDLE_TARGET",
    "CACHE_NAME",
    "FONT_SOURCE_URL_PREFIX",
    "BundledSubtitleFont",
    "OPEN_FONT_LICENSE",
    "PACKAGED_FONT_DIRECTORY",
    "PackagedLicenseNotice",
    "SubtitleFontRightsError",
    "SubtitleFontUnavailable",
    "bundled_subtitle_fonts",
    "default_subtitle_font_name",
    "ensure_subtitle_fonts",
    "font_copyright_notice",
    "load_asset_rights",
    "load_worker_contract",
    "packaged_license_notice",
    "verify_font_payload",
    "verify_license_payload",
]
