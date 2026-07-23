#!/usr/bin/env python3
"""EB-05: single distribution manifest and fail-closed verifier.

One staged embedded-Chromium tree (EB-03) is promoted into the single
distribution unit by `distribution-manifest.v1.json`: every locked runtime
version the first release ships as one compatibility unit (Playwright Python,
the Chromium build and revision, the Browser Use harness, the render engine),
the per-file digest inventory, the executable, the license notices and a
CycloneDX-style SBOM component list.

`verify_distribution` re-verifies the tree against the manifest and every
locked contract: a tampered, missing or extra file, an extra browser binary,
a platform mismatch or any version drift is rejected with a fixed message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from build_embedded_chromium_staging import (
    MANIFEST_NAME as STAGING_MANIFEST_NAME,
)
from build_embedded_chromium_staging import (
    load_staging_contract,
    sha256_file,
)

DISTRIBUTION_MANIFEST_NAME: Final = "distribution-manifest.v1.json"

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
_STAGING_CONTRACT: Final = (
    _REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
)
_COMPATIBILITY_CONTRACT: Final = (
    _REPOSITORY_ROOT / "contracts/browser/embedded-chromium-compatibility.v1.json"
)
_BROWSER_USE_CONTRACT: Final = (
    _REPOSITORY_ROOT / "contracts/browser-use/api-contract.v1.json"
)
_VALIDATION_CONTRACT: Final = (
    _REPOSITORY_ROOT / "contracts/browser/shared-chromium-validation.v1.json"
)

_FORBIDDEN_NAME_SUBSTRINGS: Final = (
    "chrome-headless-shell",
    "headless_shell",
    "firefox",
    "webkit",
)


class DistributionRejected(RuntimeError):
    """The staged tree, manifest or verification input is invalid."""


def _reject(message: str) -> None:
    raise DistributionRejected(f"embedded browser distribution rejected: {message}")


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _reject(f"unreadable document: {type(error).__name__}")
    if not isinstance(value, dict):
        _reject("document must be an object")
    return value


@dataclass(frozen=True)
class VerificationReport:
    """Successful verification facts for one staged distribution."""

    target_id: str
    verified_files: int
    total_bytes: int


def _require_single_target_tree(staging: Path, root_entry: str) -> None:
    allowed_top_level = {
        root_entry,
        STAGING_MANIFEST_NAME,
        DISTRIBUTION_MANIFEST_NAME,
    }
    if {entry.name for entry in staging.iterdir()} - allowed_top_level:
        _reject("unexpected top-level distribution entry")


def _locked_runtime() -> dict[str, object]:
    compatibility = _load_json(_COMPATIBILITY_CONTRACT)
    production = compatibility["production_runtime"]
    if not isinstance(production, dict):
        _reject("compatibility contract runtime invalid")
    chromium = production["chromium"]
    if not isinstance(chromium, dict):
        _reject("compatibility contract chromium invalid")
    browser_use = _load_json(_BROWSER_USE_CONTRACT)
    package = browser_use.get("package")
    if not isinstance(package, dict) or not isinstance(package.get("version"), str):
        _reject("browser use contract package invalid")
    validation = _load_json(_VALIDATION_CONTRACT)
    render_engine = validation.get("render_engine_version")
    if not isinstance(render_engine, str):
        _reject("shared validation contract render engine invalid")
    if validation.get("browser_use_version") != package["version"]:
        _reject("browser use version differs between contracts")
    if validation.get("chromium", {}).get("browser_version") != chromium.get(
        "browser_version"
    ):
        _reject("chromium version differs between contracts")
    return {
        "playwright_python": production["playwright_python"],
        "chromium": {
            "title": chromium["title"],
            "browser_version": chromium["browser_version"],
            "revision": chromium["revision"],
        },
        "browser_use": package["version"],
        "render_engine": render_engine,
    }


def build_distribution_manifest(
    *, staging: Path, target_id: str, enforce_archive_lock: bool = True
) -> Path:
    """Promote one staged tree into the single distribution manifest.

    `enforce_archive_lock=False` is for synthetic-tree unit tests only; the
    real acceptance always keeps the contract archive digest enforced.
    """
    contract = load_staging_contract(_STAGING_CONTRACT)
    target = contract.targets.get(target_id)
    if target is None or not target.buildable:
        _reject("unknown or non-buildable distribution target")
    staging_manifest_path = staging / STAGING_MANIFEST_NAME
    staging_manifest = _load_json(staging_manifest_path)
    if (
        staging_manifest.get("target") != target_id
        or staging_manifest.get("chromium", {}).get("browser_version")
        != contract.browser_version
    ):
        _reject("staging manifest does not match the locked contracts")
    if (
        enforce_archive_lock
        and staging_manifest.get("source", {}).get("archive_sha256")
        != target.archive_sha256
    ):
        _reject("staging archive digest does not match the contract lock")
    entries = staging_manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        _reject("staging manifest entries missing")
    _require_single_target_tree(staging, target.root_entry)

    runtime = _locked_runtime()
    chromium_version = str(contract.browser_version)
    document = {
        "schemaVersion": 1,
        "policy": "fail_closed",
        "verified_at": contract.verified_at,
        "target": target_id,
        "runtime": runtime,
        "executable": target.executable,
        "source": staging_manifest["source"],
        "fileCount": staging_manifest["fileCount"],
        "totalBytes": staging_manifest["totalBytes"],
        "entries": entries,
        "licenses": {
            "chromium_build": {
                "component": "chrome-for-testing",
                "notice": (
                    "Chrome for Testing binary; Chromium sources are BSD-3-Clause, "
                    "the assembled Chrome build ships Google-licensed components. "
                    "Redistribution review is tracked before the first release "
                    "package (EB-16)."
                ),
                "redistribution_review": "pending",
            }
        },
        "sbom": [
            {
                "type": "application",
                "name": "chrome-for-testing",
                "version": chromium_version,
                "purl": f"pkg:generic/chrome-for-testing@{chromium_version}",
                "source_url": target.download_url,
                "hashes": [{"alg": "SHA-256", "content": target.archive_sha256}],
            }
        ],
    }
    manifest_path = staging / DISTRIBUTION_MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def verify_distribution(
    *, staging: Path, target_id: str, enforce_archive_lock: bool = True
) -> VerificationReport:
    """Re-verify one staged distribution against manifest and contracts."""
    manifest_path = staging / DISTRIBUTION_MANIFEST_NAME
    if not manifest_path.is_file():
        _reject("distribution manifest missing")
    document = _load_json(manifest_path)
    if document.get("schemaVersion") != 1 or document.get("policy") != "fail_closed":
        _reject("distribution manifest shape invalid")
    if document.get("target") != target_id:
        _reject("distribution target mismatch")
    runtime = document.get("runtime")
    if runtime != _locked_runtime():
        _reject("distribution runtime versions drifted from the locked contracts")

    contract = load_staging_contract(_STAGING_CONTRACT)
    target = contract.targets.get(target_id)
    if target is None:
        _reject("unknown distribution target")
    if document.get("executable") != target.executable:
        _reject("distribution executable differs from the target contract")
    if (
        enforce_archive_lock
        and document.get("source", {}).get("archive_sha256") != target.archive_sha256
    ):
        _reject("distribution archive digest does not match the contract lock")

    entries = document.get("entries")
    if not isinstance(entries, list) or not entries:
        _reject("distribution entries missing")
    expected_paths: set[str] = set()
    verified_files = 0
    total_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get("path")) is not str:
            _reject("distribution entry invalid")
        relative = entry["path"]
        expected_paths.add(relative)
        actual = staging / Path(*relative.split("/"))
        if entry.get("type") == "symlink":
            if not actual.is_symlink() or str(actual.readlink()) != entry.get(
                "targetPath"
            ):
                _reject("symlink entry drifted")
            continue
        if not actual.is_file() or actual.is_symlink():
            _reject("manifest file missing from staging")
        if sha256_file(actual) != entry.get("sha256"):
            _reject("file digest mismatch")
        if actual.stat().st_size != entry.get("size"):
            _reject("file size mismatch")
        verified_files += 1
        total_bytes += int(entry["size"])

    root = staging / target.root_entry
    if not root.is_dir():
        _reject("staged root missing")
    _require_single_target_tree(staging, target.root_entry)
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = "/".join(path.relative_to(staging).parts)
        lowered = relative.lower()
        if any(name in lowered for name in _FORBIDDEN_NAME_SUBSTRINGS):
            _reject("forbidden browser entry present in staging")
        if relative not in expected_paths:
            _reject("unexpected extra file in staging")

    executable = staging / Path(*str(document.get("executable")).split("/"))
    if not executable.is_file() or not executable.stat().st_mode & 0o111:
        _reject("distribution executable missing")
    return VerificationReport(
        target_id=target_id, verified_files=verified_files, total_bytes=total_bytes
    )


__all__ = [
    "DISTRIBUTION_MANIFEST_NAME",
    "DistributionRejected",
    "VerificationReport",
    "build_distribution_manifest",
    "verify_distribution",
]
