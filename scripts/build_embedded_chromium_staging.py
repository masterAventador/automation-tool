#!/usr/bin/env python3
"""EB-03: reproducible macOS staging of the locked embedded Chromium.

The builder consumes one already-downloaded, digest-locked archive of the
Playwright-locked Chrome for Testing build and produces a verifiable staging
directory: safe extraction (no traversal, no absolute or escaping symlinks,
no duplicate entries, exactly one expected root, no second browser), the
locked executable present, and a deterministic per-file manifest
(`staging-manifest.json`) with SHA-256, size, mode and symlink targets.
Everything is fail closed; nothing is downloaded at runtime.
"""

from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

MANIFEST_NAME: Final = "staging-manifest.json"
_MAX_ARCHIVE_ENTRIES: Final = 20_000
_SHA256_HEX_LENGTH: Final = 64


class StagingRejected(RuntimeError):
    """The staging input, archive content or output location is invalid."""


def _reject(message: str) -> None:
    raise StagingRejected(f"embedded chromium staging rejected: {message}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class StagingTarget:
    target_id: str
    buildable: bool
    download_url: str
    redirect_host_allowlist: tuple[str, ...]
    redirect_path_prefix: str
    archive_sha256: str
    root_entry: str
    executable: str
    forbidden_entry_substrings: tuple[str, ...]


@dataclass(frozen=True)
class StagingContract:
    verified_at: str
    browser_version: str
    revision: str
    targets: dict[str, StagingTarget]


@dataclass(frozen=True)
class StagingResult:
    output: Path
    manifest_path: Path
    file_count: int
    total_bytes: int


def load_staging_contract(path: Path) -> StagingContract:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _reject(f"contract unreadable: {type(error).__name__}")
    if (
        document.get("schema_version") != 1
        or document.get("policy") != "fail_closed"
        or not isinstance(document.get("verified_at"), str)
        or not isinstance(document.get("chromium"), dict)
        or not isinstance(document.get("targets"), dict)
    ):
        _reject("contract shape invalid")
    chromium = document["chromium"]
    browser_version = chromium.get("browser_version")
    revision = chromium.get("revision")
    if not isinstance(browser_version, str) or not isinstance(revision, str):
        _reject("contract chromium lock invalid")
    targets: dict[str, StagingTarget] = {}
    for target_id, value in document["targets"].items():
        if not isinstance(value, dict):
            _reject("contract target invalid")
        allowlist = value.get("redirect_host_allowlist")
        forbidden = value.get("forbidden_entry_substrings")
        if (
            not isinstance(value.get("buildable"), bool)
            or not isinstance(value.get("download_url"), str)
            or not isinstance(allowlist, list)
            or not isinstance(value.get("redirect_path_prefix"), str)
            or not isinstance(value.get("archive_sha256"), str)
            or not isinstance(value.get("root_entry"), str)
            or not isinstance(value.get("executable"), str)
            or not isinstance(forbidden, list)
        ):
            _reject("contract target field invalid")
        targets[target_id] = StagingTarget(
            target_id=target_id,
            buildable=value["buildable"],
            download_url=value["download_url"],
            redirect_host_allowlist=tuple(allowlist),
            redirect_path_prefix=value["redirect_path_prefix"],
            archive_sha256=value["archive_sha256"],
            root_entry=value["root_entry"],
            executable=value["executable"],
            forbidden_entry_substrings=tuple(forbidden),
        )
    return StagingContract(
        verified_at=document["verified_at"],
        browser_version=browser_version,
        revision=revision,
        targets=targets,
    )


def _entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _entry_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0o7777


def _validated_relative(name: str, root_entry: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        _reject("archive entry escapes the staging root")
    if path.parts[0] != root_entry:
        _reject("archive entry outside the expected root")
    return path


def safe_extract(archive_path: Path, destination: Path, *, root_entry: str) -> None:
    """Extract with traversal, symlink, duplicate and root confinement checks."""
    destination.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > _MAX_ARCHIVE_ENTRIES:
            _reject("archive entry count out of bounds")
        for info in entries:
            name = info.filename
            if name in seen:
                _reject("duplicate archive entry")
            seen.add(name)
            relative = _validated_relative(name.rstrip("/"), root_entry)
            target = destination / Path(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if _entry_is_symlink(info):
                link_target = archive.read(info).decode("utf-8", errors="strict")
                link_path = PurePosixPath(link_target)
                if link_path.is_absolute():
                    _reject("absolute symlink in archive")
                resolved = PurePosixPath(relative.parent)
                for part in link_path.parts:
                    if part == "..":
                        if not resolved.parts:
                            _reject("symlink escapes the staging root")
                        resolved = PurePosixPath(*resolved.parts[:-1])
                    elif part != ".":
                        resolved = resolved / part
                if not resolved.parts or resolved.parts[0] != root_entry:
                    _reject("symlink escapes the staging root")
                target.symlink_to(link_target)
                continue
            with archive.open(info) as source, target.open("wb") as sink:
                while chunk := source.read(1024 * 1024):
                    sink.write(chunk)
            target.chmod(0o755 if _entry_mode(info) & 0o111 else 0o644)


def generate_manifest(base: Path, *, root_entry: str) -> list[dict[str, object]]:
    """Deterministic, sorted per-entry manifest of the staged tree."""
    root = base / root_entry
    if not root.is_dir():
        _reject("staged root missing")
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = str(PurePosixPath(path.relative_to(base)))
        if path.is_symlink():
            entries.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "targetPath": str(path.readlink()),
                }
            )
        elif path.is_file():
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "executable": bool(path.stat().st_mode & 0o111),
                }
            )
    return entries


def build_staging(
    *,
    contract: StagingContract,
    target_id: str,
    archive_path: Path,
    archive_sha256: str,
    output: Path,
) -> StagingResult:
    """Verify, extract and manifest one locked archive into a fresh output."""
    target = contract.targets.get(target_id)
    if target is None:
        _reject("unknown staging target")
    if not target.buildable:
        _reject("target is not buildable on this task")
    if (
        len(archive_sha256) != _SHA256_HEX_LENGTH
        or sha256_file(archive_path) != archive_sha256
    ):
        _reject("archive digest mismatch")
    if output.exists():
        _reject("output directory already exists")

    safe_extract(archive_path, output, root_entry=target.root_entry)

    roots = sorted(entry.name for entry in output.iterdir())
    if roots != [target.root_entry]:
        _reject("staging must contain exactly the expected root")
    lowered_names = [
        str(PurePosixPath(path.relative_to(output))).lower()
        for path in output.rglob("*")
    ]
    for forbidden in target.forbidden_entry_substrings:
        if any(forbidden in name for name in lowered_names):
            _reject("forbidden browser entry present")
    executable = output / Path(*PurePosixPath(target.executable).parts)
    if not executable.is_file() or not executable.stat().st_mode & 0o111:
        _reject("locked executable missing from staging")

    entries = generate_manifest(output, root_entry=target.root_entry)
    total_bytes = sum(int(entry.get("size", 0)) for entry in entries)
    manifest = {
        "schemaVersion": 1,
        "target": target_id,
        "chromium": {
            "browser_version": contract.browser_version,
            "revision": contract.revision,
        },
        "verified_at": contract.verified_at,
        "source": {
            "download_url": target.download_url,
            "archive_sha256": archive_sha256,
        },
        "executable": target.executable,
        "fileCount": sum(1 for entry in entries if entry["type"] == "file"),
        "totalBytes": total_bytes,
        "entries": entries,
    }
    manifest_path = output / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return StagingResult(
        output=output,
        manifest_path=manifest_path,
        file_count=int(manifest["fileCount"]),
        total_bytes=total_bytes,
    )
