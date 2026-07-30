#!/usr/bin/env python3
"""Third-party software notice UI projection builder.

Composes ``contracts/quality/third-party-notice-ui.v1.json`` from the locked
source, asset-rights and motion-rights contracts: the upstream projects the
legal disclosure page has to name, plus the font and material rights summary it
publishes.

The projection is the only one of these payloads the frontend may import. The
source contracts also carry the internal rights review — per-item trademark
indicators, bundled sample asset paths, CDN addresses, verification markers —
which is 87 KB of data no user reads, so none of it is projected. Chinese
wording stays in the page: this file carries facts, not copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import subtitle_font_assets

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = REPOSITORY_ROOT / "contracts/quality/third-party-sources.v1.json"
ASSET_RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/asset-rights-policy.v1.json"
MOTION_RIGHTS_PATH = REPOSITORY_ROOT / "contracts/quality/motion-catalog-rights.v1.json"
FFMPEG_TOOLCHAIN_PATH = REPOSITORY_ROOT / "contracts/video/ffmpeg-toolchain.v1.json"
CHROMIUM_STAGING_PATH = REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
MOTION_WORKER_PATH = REPOSITORY_ROOT / "contracts/quality/motion-video-worker-package.v1.json"
MATERIAL_WORKER_PATH = REPOSITORY_ROOT / "contracts/quality/material-video-worker-package.v1.json"
SILERO_VAD_RUNTIME_PATH = REPOSITORY_ROOT / "contracts/quality/silero-vad-runtime.v1.json"
PROJECTION_PATH = REPOSITORY_ROOT / "contracts/quality/third-party-notice-ui.v1.json"
LICENSE_TEXT_ROOT = (
    REPOSITORY_ROOT / "frontend/src/features/legal/third-party-software/license-texts"
)

SCHEMA_VERSION = "1.0"
PROJECTION_ID = "automation-tool.third-party-notice-ui.v1"

CLEARED_CONCLUSION = "cleared"
REPOSITORY_ADDRESS = re.compile(r"^https://[^/]+/([^/]+)/([^/]+?)(?:\.git)?$")
COPYRIGHT_LINE = re.compile(r"^\s*(Copyright\b.*?)\s*$", re.MULTILINE)

# The licence texts the App itself carries so a user never has to open the
# installed package to read one. `mit` and `apache-2.0` must be the locked
# submodule LICENSE blobs byte for byte; `gpl-3.0` is FFmpeg 8.1.2's own
# COPYING.GPLv3, the same file `scripts/build_video_media_toolchain.sh` copies
# into `media-toolchain/`.
LICENSE_TEXT_SPDX: dict[str, str] = {
    "mit": "MIT",
    "apache-2.0": "Apache-2.0",
    "gpl-3.0": "GPL-3.0-only",
    "ofl-1.1": "OFL-1.1",
}
GPL_3_0_SHA256 = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
LICENSE_TEXT_BY_SPDX: dict[str, str] = {"MIT": "mit", "Apache-2.0": "apache-2.0"}

# The subtitle fonts the installer redistributes in place of the four
# proprietary Windows/macOS system faces the upstream project bundled. They are
# one disclosure entry rather than one per face: a user reads about a licence and
# a copyright holder, and both are identical across the faces — the register and
# the candidate audit are where the per-file digests live.
SUBTITLE_FONT_COMPONENT_ID = "subtitle-fonts"
SUBTITLE_FONT_COMPONENT_NAME = "Noto Sans CJK SC"
SUBTITLE_FONT_LICENSE_TEXT_ID = "ofl-1.1"
SUBTITLE_FONT_VERSION = "Sans2.004"
SUBTITLE_FONT_SOURCE_URL = "https://github.com/notofonts/noto-cjk"
MATERIAL_WORKER_INTERNAL_PREFIX = "material-video-worker/package/_internal"
LOCAL_EXECUTOR_INTERNAL_PREFIX = "local-executor/package/_internal"

# Where a locked submodule's own LICENSE file lands inside the installed
# package. Only the material-video Worker carries one: its PyInstaller spec
# copies `vendor/moneyprinterturbo/LICENSE` into `upstream/`. Nothing from the
# motion project ships as a file — what the product distributes from it is the
# derived 134-part catalogue compiled into the App bundle — so it has no
# in-package path and relies on the licence text the App carries.
UPSTREAM_PACKAGED_NOTICE: dict[str, str | None] = {
    "moneyprinterturbo": "material-video-worker/package/_internal/upstream/LICENSE",
    "hyperframes": None,
}

# Paths that no contract declares, each with the build step whose source must
# still contain the file name. `_verify_packaged_paths` re-reads those scripts,
# so a rename that leaves this notice pointing at a file the installer no longer
# writes fails the gate instead of shipping a dead path to a user.
PACKAGED_PATH_PRODUCERS: dict[str, str] = {
    "motion-video-worker/package/NODE-LICENSE": ("scripts/build_motion_video_worker_candidate.py"),
    "material-video-worker/package/_internal/licenses/material-video-worker-dependencies.json": (
        "scripts/build_material_video_worker_candidate.py"
    ),
    "material-video-worker/package/_internal/upstream/LICENSE": (
        "workers/material_montage/material-video-worker.spec"
    ),
}

# The embedded browser is a Google build, not a Chromium source build: the
# Chromium parts stay BSD-3-Clause while the assembled binary carries Google's
# own terms. Its complete third-party notice is the credits page inside the
# very browser the product ships, which is why it publishes a channel rather
# than a file path.
BROWSER_LICENSE = "BSD-3-Clause AND LicenseRef-Google-Chrome-Terms-of-Service"
BROWSER_NOTICE_CHANNEL = "chromium_credits_page"


class ProjectionError(RuntimeError):
    """Raised when the projection cannot be composed safely."""


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProjectionError(f"{path.name} must contain an object")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(f"{field} must be a non-empty string")
    return value


def _attribute(identifier: str, url: str) -> tuple[str, str]:
    """Derive ``owner/repository`` and the repository name from the locked URL.

    Deriving beats restating: the page can never disagree with the address the
    source lock and the submodule gate already agree on.
    """
    match = REPOSITORY_ADDRESS.match(url)
    if match is None:
        raise ProjectionError(f"{identifier}: url is not an attributable repository")
    owner, name = match.group(1), match.group(2)
    return f"{owner}/{name}", name


def _normalized_bytes(path: Path) -> bytes:
    """Read a licence text with line endings normalised.

    A clean Windows checkout may materialize the same blob with CRLF, and a
    licence that appears to change with the checkout would make every digest in
    this projection platform-dependent.
    """
    try:
        return path.read_bytes().replace(b"\r\n", b"\n")
    except OSError as error:
        raise ProjectionError(f"cannot read licence text {path.name}: {error}") from error


def _copyright_line(identifier: str, license_path: Path) -> str:
    """Return the upstream copyright notice MIT and Apache-2.0 oblige us to keep.

    Deriving it from the locked LICENSE blob is the point: a retyped copyright
    line is exactly the kind of thing that silently goes stale, and the source
    gate already binds that blob to the locked commit.
    """
    text = _normalized_bytes(license_path).decode("utf-8")
    match = COPYRIGHT_LINE.search(text)
    if match is None:
        raise ProjectionError(f"{identifier}: LICENSE carries no copyright line")
    return match.group(1)


def _license_texts() -> list[dict]:
    """Bind every licence text the App ships to its bytes."""
    texts = []
    for identifier, spdx in sorted(LICENSE_TEXT_SPDX.items()):
        path = LICENSE_TEXT_ROOT / f"{identifier}.txt"
        payload = _normalized_bytes(path)
        if not payload:
            raise ProjectionError(f"{identifier}: shipped licence text is empty")
        texts.append(
            {
                "id": identifier,
                "spdx": spdx,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    return texts


def _require_shipped_license_texts(sources: dict, texts: list[dict]) -> None:
    """Refuse to publish a licence text that is not the licence.

    The two submodule texts are compared against the locked LICENSE blobs and
    the GPL text against a pinned digest, so this projection cannot advertise a
    paraphrase, a truncation or the wrong version of a licence.
    """
    by_id = {text["id"]: text for text in texts}
    for entry in sources.get("sources", []):
        licence = entry.get("license")
        if not isinstance(licence, dict):
            raise ProjectionError("source license record is missing")
        spdx = _text(licence.get("spdx"), "license.spdx")
        identifier = LICENSE_TEXT_BY_SPDX.get(spdx)
        if identifier is None:
            raise ProjectionError(f"{spdx}: the App ships no text for this licence")
        expected = _text(licence.get("sha256"), f"{spdx}: license.sha256")
        if by_id[identifier]["sha256"] != expected:
            raise ProjectionError(
                f"{identifier}: shipped licence text is not the locked LICENSE blob"
            )
    if by_id["gpl-3.0"]["sha256"] != GPL_3_0_SHA256:
        raise ProjectionError("gpl-3.0: shipped licence text is not FFmpeg's COPYING.GPLv3")
    # The App's copy of the OFL and the copy fetched into the package must be
    # the same bytes, or a user could read one licence on screen while a
    # different one travels with the fonts.
    try:
        packaged_licence = subtitle_font_assets.packaged_license_notice()
    except subtitle_font_assets.SubtitleFontRightsError as error:
        raise ProjectionError(f"the subtitle fonts are not cleared: {error}") from error
    if by_id[SUBTITLE_FONT_LICENSE_TEXT_ID]["sha256"] != packaged_licence.sha256:
        raise ProjectionError(
            f"{SUBTITLE_FONT_LICENSE_TEXT_ID}: the shipped licence text is not the one "
            "the package fetches beside the fonts"
        )


def _media_toolchain_path(layout: dict, key: str) -> str:
    root = _text(layout.get("root"), "package_layout.root")
    return f"{root}/{_text(layout.get(key), f'package_layout.{key}')}"


def subtitle_font_license_path() -> str:
    """Where the font licence text lands inside the installed package.

    Derived from the asset rights register and the packaging layout the Worker
    spec uses, so a renamed licence file cannot leave this page pointing at
    something the installer never writes.
    """
    try:
        notice = subtitle_font_assets.packaged_license_notice()
    except subtitle_font_assets.SubtitleFontRightsError as error:
        raise ProjectionError(f"the subtitle fonts are not cleared: {error}") from error
    return (
        f"{MATERIAL_WORKER_INTERNAL_PREFIX}/"
        f"{subtitle_font_assets.PACKAGED_FONT_DIRECTORY}/{notice.packaged_name}"
    )


def _subtitle_font_component() -> dict:
    """Disclose the open fonts that replaced the proprietary system faces.

    The SIL Open Font License is unlike the other licences on this page: its text
    is a template that names no copyright holder, so publishing the text alone
    would satisfy neither half of section 1. The holder is therefore read out of
    the ``name`` table of the very font files the installer ships, which is where
    the licence itself says that notice may live.
    """
    try:
        fonts = subtitle_font_assets.bundled_subtitle_fonts()
    except subtitle_font_assets.SubtitleFontRightsError as error:
        raise ProjectionError(f"the subtitle fonts are not cleared: {error}") from error
    notices = {font.attribution for font in fonts}
    if len(notices) != 1:
        raise ProjectionError("the shipped fonts disagree on their copyright notice")
    return {
        "id": SUBTITLE_FONT_COMPONENT_ID,
        "name": SUBTITLE_FONT_COMPONENT_NAME,
        "version": SUBTITLE_FONT_VERSION,
        "license": subtitle_font_assets.OPEN_FONT_LICENSE,
        "copyleft": False,
        "copyright": notices.pop(),
        "licenseTextId": SUBTITLE_FONT_LICENSE_TEXT_ID,
        "packagedNoticePath": subtitle_font_license_path(),
        "noticeChannelId": None,
        "packagedSourcePaths": [],
        "upstreamSourceUrl": SUBTITLE_FONT_SOURCE_URL,
    }


def _local_executor_internal_path(value: object, field: str) -> str:
    relative = _text(value, field)
    parts = relative.split("/")
    if relative.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ProjectionError(f"{field} is not a canonical package-relative path")
    return f"{LOCAL_EXECUTOR_INTERNAL_PREFIX}/{relative}"


def _silero_vad_components(contract: dict) -> list[dict]:
    upstream = contract.get("upstream")
    model_license = contract.get("license")
    runtime = contract.get("runtime")
    if not all(isinstance(value, dict) for value in (upstream, model_license, runtime)):
        raise ProjectionError("the Silero VAD runtime contract is incomplete")
    assert (
        isinstance(upstream, dict) and isinstance(model_license, dict) and isinstance(runtime, dict)
    )
    return [
        {
            "id": "onnxruntime",
            "name": "ONNX Runtime",
            "version": _text(runtime.get("version"), "silero.runtime.version"),
            "license": _text(runtime.get("licenseSpdx"), "silero.runtime.licenseSpdx"),
            "copyleft": False,
            "copyright": None,
            "licenseTextId": None,
            "packagedNoticePath": _local_executor_internal_path(
                runtime.get("packagedLicensePath"),
                "silero.runtime.packagedLicensePath",
            ),
            "noticeChannelId": None,
            "packagedSourcePaths": [],
            "upstreamSourceUrl": None,
        },
        {
            "id": "silero-vad-model",
            "name": "Silero VAD model",
            "version": _text(upstream.get("tag"), "silero.upstream.tag"),
            "license": _text(model_license.get("spdx"), "silero.license.spdx"),
            "copyleft": False,
            "copyright": None,
            "licenseTextId": None,
            "packagedNoticePath": _local_executor_internal_path(
                model_license.get("packagedPath"),
                "silero.license.packagedPath",
            ),
            "noticeChannelId": None,
            "packagedSourcePaths": [],
            "upstreamSourceUrl": None,
        },
    ]


def _distributed_components(
    ffmpeg_contract: dict,
    chromium: dict,
    motion_worker: dict,
    material_worker: dict,
    silero_vad_runtime: dict,
) -> list[dict]:
    """Every third-party runtime the installer puts on a user's disk.

    These are the components whose licences the product has to satisfy as a
    distributor, which is a different question from which upstream projects the
    two video features are built on. FFmpeg and x264 are the reason this block
    exists: they are GPL, they ship as executables, and a notice that never
    names them or says where their source is fails the licence outright.
    """
    ffmpeg = ffmpeg_contract.get("ffmpeg")
    x264 = ffmpeg_contract.get("x264")
    layout = ffmpeg_contract.get("package_layout")
    if not all(isinstance(value, dict) for value in (ffmpeg, x264, layout)):
        raise ProjectionError("the media toolchain contract is incomplete")
    assert isinstance(ffmpeg, dict) and isinstance(x264, dict) and isinstance(layout, dict)

    browser = chromium.get("chromium")
    if not isinstance(browser, dict):
        raise ProjectionError("the embedded browser contract discloses no build")
    runtime = motion_worker.get("runtime")
    if not isinstance(runtime, dict):
        raise ProjectionError("the motion Worker contract discloses no runtime")
    python = material_worker.get("python")
    if not isinstance(python, dict):
        raise ProjectionError("the material Worker contract discloses no runtime")

    licence_path = _media_toolchain_path(layout, "license")
    return [
        {
            "id": "embedded-browser",
            "name": _text(browser.get("title"), "chromium.title"),
            "version": _text(browser.get("browser_version"), "chromium.browser_version"),
            "license": BROWSER_LICENSE,
            "copyleft": False,
            "copyright": None,
            "licenseTextId": None,
            "packagedNoticePath": None,
            "noticeChannelId": BROWSER_NOTICE_CHANNEL,
            "packagedSourcePaths": [],
            "upstreamSourceUrl": None,
        },
        {
            "id": "ffmpeg",
            "name": "FFmpeg",
            "version": _text(ffmpeg.get("version"), "ffmpeg.version"),
            "license": _text(ffmpeg.get("license"), "ffmpeg.license"),
            "copyleft": True,
            "copyright": None,
            "licenseTextId": "gpl-3.0",
            "packagedNoticePath": licence_path,
            "noticeChannelId": None,
            "packagedSourcePaths": [_media_toolchain_path(layout, "source_archive")],
            "upstreamSourceUrl": _text(ffmpeg.get("source_url"), "ffmpeg.source_url"),
        },
        {
            "id": "x264",
            "name": "x264",
            "version": _text(x264.get("revision"), "x264.revision"),
            "license": _text(x264.get("license"), "x264.license"),
            "copyleft": True,
            "copyright": None,
            # x264 is GPL-2.0-or-later and is statically linked into the one
            # FFmpeg executable, so the conveyed binary is a single GPL-3.0
            # work and GPL-3.0 is the licence a recipient actually receives it
            # under. Its own COPYING sits inside the bundled source archive.
            "licenseTextId": "gpl-3.0",
            "packagedNoticePath": licence_path,
            "noticeChannelId": None,
            "packagedSourcePaths": [_media_toolchain_path(layout, "x264_source_archive")],
            "upstreamSourceUrl": _text(x264.get("source_url"), "x264.source_url"),
        },
        {
            "id": "nodejs",
            "name": "Node.js",
            "version": _text(runtime.get("version"), "runtime.version"),
            "license": "MIT",
            "copyleft": False,
            "copyright": None,
            "licenseTextId": None,
            "packagedNoticePath": "motion-video-worker/package/NODE-LICENSE",
            "noticeChannelId": None,
            "packagedSourcePaths": [],
            "upstreamSourceUrl": None,
        },
        {
            "id": "material-video-worker-python",
            "name": "CPython",
            "version": _text(python.get("version"), "python.version"),
            "license": "PSF-2.0",
            "copyleft": False,
            "copyright": None,
            "licenseTextId": None,
            # The same file lists every Python package frozen into that Worker
            # with the licence each one declares.
            "packagedNoticePath": (
                "material-video-worker/package/_internal/licenses/"
                "material-video-worker-dependencies.json"
            ),
            "noticeChannelId": None,
            "packagedSourcePaths": [],
            "upstreamSourceUrl": None,
        },
        *_silero_vad_components(silero_vad_runtime),
        _subtitle_font_component(),
    ]


def _upstream_projects(sources: dict) -> list[dict]:
    entries = sources.get("sources")
    if not isinstance(entries, list) or not entries:
        raise ProjectionError("the source lock discloses no upstream project")
    # The lock's order is meaningful and already deterministic, so it is kept
    # rather than re-sorted: the page reads in the order the product uses them.
    projects = []
    for entry in entries:
        identifier = _text(entry.get("id"), "source id")
        url = _text(entry.get("url"), f"{identifier}: url")
        commit = _text(entry.get("commit"), f"{identifier}: commit")
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise ProjectionError(f"{identifier}: commit must be a full lowercase SHA-1")
        licence = entry.get("license")
        if not isinstance(licence, dict):
            raise ProjectionError(f"{identifier}: license must be an object")
        repository, name = _attribute(identifier, url)
        spdx = _text(licence.get("spdx"), f"{identifier}: license.spdx")
        license_text_id = LICENSE_TEXT_BY_SPDX.get(spdx)
        if license_text_id is None:
            raise ProjectionError(f"{identifier}: the App ships no {spdx} licence text")
        source_root = REPOSITORY_ROOT / _text(entry.get("path"), f"{identifier}: path")
        license_file = source_root / _text(licence.get("path"), f"{identifier}: license.path")
        projects.append(
            {
                "id": identifier,
                "name": name,
                "repository": repository,
                "sourceUrl": url,
                "version": _text(entry.get("tag"), f"{identifier}: tag"),
                "commit": commit,
                "license": spdx,
                "copyright": _copyright_line(identifier, license_file),
                "licenseTextId": license_text_id,
                "packagedNoticePath": UPSTREAM_PACKAGED_NOTICE.get(identifier),
            }
        )
    return projects


def _asset_rights(policy: dict) -> dict:
    shared = policy.get("distributionRequiredFields")
    if not isinstance(shared, list) or not shared:
        raise ProjectionError("the rights policy requires no distribution information")
    categories = policy.get("requiredCategories")
    if not isinstance(categories, dict) or not categories:
        raise ProjectionError("the rights policy covers no category")
    entries = policy.get("entries")
    if not isinstance(entries, list):
        raise ProjectionError("the rights policy has no register")
    projected = []
    for identifier in sorted(categories):
        fields = categories[identifier]
        if not isinstance(fields, list) or not fields:
            raise ProjectionError(f"{identifier}: category requires no rights information")
        projected.append({"id": identifier, "requiredFieldCount": len(fields)})
    return {
        "deniedByDefault": policy.get("defaultDecision") == "deny",
        "sharedRequiredFieldCount": len(shared),
        "registeredEntryCount": len(entries),
        "categories": projected,
    }


def _motion_asset_rights(review: dict) -> dict:
    stats = review.get("stats")
    if not isinstance(stats, dict):
        raise ProjectionError("the motion rights review has no summary")
    counts = stats.get("conclusionCounts")
    if not isinstance(counts, dict) or not counts:
        raise ProjectionError("the motion rights review counted no conclusion")
    total = sum(counts.values())
    if total <= 0:
        raise ProjectionError("the motion rights review counted no part")
    cleared = counts.get(CLEARED_CONCLUSION, 0)
    packages = review.get("remoteDependencyPackages")
    if not isinstance(packages, list) or not packages:
        raise ProjectionError("the motion rights review lists no borrowed package")
    dependencies = []
    for package in packages:
        name = _text(package.get("package"), "borrowed package name")
        count = package.get("itemCount")
        if not isinstance(count, int) or count <= 0:
            raise ProjectionError(f"{name}: borrowed package is used by no part")
        dependencies.append(
            {
                "name": name,
                "license": _text(package.get("assumedLicense"), f"{name}: assumedLicense"),
                "partCount": count,
            }
        )
    licence = review.get("codeLicense")
    source = review.get("source")
    if not isinstance(licence, dict) or not isinstance(source, dict):
        raise ProjectionError("the motion rights review discloses no licence or version")
    return {
        "codeLicense": _text(licence.get("spdx"), "motion codeLicense.spdx"),
        "version": _text(source.get("tag"), "motion source.tag"),
        "totalPartCount": total,
        "clearedPartCount": cleared,
        "partsNeedingWorkCount": total - cleared,
        "webFontFamilyCount": stats["googleFontFamilyCount"],
        "bundledSampleAssetPartCount": stats["itemsWithBundledSampleAssets"],
        "networkDependentPartCount": stats["itemsWithRuntimeRemoteDependencies"],
        "dependencies": dependencies,
    }


def compose_projection() -> dict:
    sources = _load(SOURCES_PATH)
    texts = _license_texts()
    _require_shipped_license_texts(sources, texts)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": PROJECTION_ID,
        "upstreamProjects": _upstream_projects(sources),
        "distributedComponents": _distributed_components(
            _load(FFMPEG_TOOLCHAIN_PATH),
            _load(CHROMIUM_STAGING_PATH),
            _load(MOTION_WORKER_PATH),
            _load(MATERIAL_WORKER_PATH),
            _load(SILERO_VAD_RUNTIME_PATH),
        ),
        "licenseTexts": texts,
        "assetRights": _asset_rights(_load(ASSET_RIGHTS_PATH)),
        "motionAssetRights": _motion_asset_rights(_load(MOTION_RIGHTS_PATH)),
    }


def serialize_projection(projection: dict) -> str:
    return json.dumps(projection, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECTION_PATH)
    arguments = parser.parse_args()
    projection = compose_projection()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with open(arguments.output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(serialize_projection(projection))
    print(
        "third-party notice ui projection written:",
        f"{len(projection['upstreamProjects'])} upstream projects,",
        f"{len(projection['motionAssetRights']['dependencies'])} borrowed packages",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
