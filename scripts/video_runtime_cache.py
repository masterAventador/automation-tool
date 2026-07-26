#!/usr/bin/env python3
"""A reusable, per-machine build cache for the pinned video runtime artifacts.

ffmpeg, the motion Worker and the material Worker are all built from pinned
versions with pinned source digests. Their output is deterministic, yet the
acceptance scripts rebuilt each of them into a temporary directory on every
run and deleted them afterwards — ffmpeg alone costs minutes of compilation to
reproduce a byte-identical result.

Disposable-per-run is the right policy for a database container or a browser
profile, and the wrong policy for a version-pinned compiled artifact. This
module keeps those artifacts on a stable per-machine path, keyed by the digest
of everything they are built from -- the contracts that pin their versions and
the source trees they are compiled from -- so a rebuild happens exactly when
those inputs change and never merely because a new run started.

Both halves of that key are load-bearing. Keying only on the contracts is how a
committed fix to the material Worker's web UI reached no user: the contracts
were unchanged, so the cache reported the artifact current and the release
shipped the binary frozen before the fix.

The cache deliberately lives outside the repository: the Worker build scripts
refuse to write inside the checkout, and repository-scoped cleanup would sweep
it away.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

CACHE_DIRECTORY_NAME = "automation-tool-build"
STAMP_SUFFIX = ".stamp.json"
STAMP_VERSION = 1

# Byte-code caches are a side effect of importing a source package, not part of
# what a build consumes. Digesting them would invalidate an artifact because
# somebody ran a test.
IGNORED_SOURCE_PARTS = frozenset({"__pycache__"})
IGNORED_SOURCE_SUFFIXES = frozenset({".pyc", ".pyo"})


class VideoRuntimeCacheRejected(RuntimeError):
    """The cache cannot answer for an artifact and refuses to guess."""


def cache_root() -> Path:
    """Return this machine's project-scoped artifact cache directory."""
    override = os.environ.get("AUTOMATION_TOOL_BUILD_CACHE")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches" / CACHE_DIRECTORY_NAME
    if os.name == "nt":
        # No extra `cache` leaf: the root's own name has to carry the project
        # scope on every platform, so a stray directory is attributable to
        # `automation-tool` from the directory name alone.
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / CACHE_DIRECTORY_NAME
        return Path.home() / "AppData/Local" / CACHE_DIRECTORY_NAME
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / CACHE_DIRECTORY_NAME
    return Path.home() / ".cache" / CACHE_DIRECTORY_NAME


def _source_package_entries(directory: Path) -> list[tuple[str, str]]:
    """Digest every file in a source package the build compiles or freezes.

    A pinning file names a version; a source package *is* the version, so the
    whole tree has to be read. Names are relative to the package, never
    absolute, because a per-machine cache is shared by every worktree and
    absolute names would give each checkout its own key.
    """
    entries: list[tuple[str, str]] = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if IGNORED_SOURCE_PARTS.intersection(relative.parts):
            continue
        if path.is_symlink():
            # Its target lives outside the digest, so its bytes could change
            # without changing the key.
            raise VideoRuntimeCacheRejected(
                f"the pinned source package {directory} contains a symbolic link "
                f"({relative}), whose target the cache cannot account for"
            )
        if not path.is_file() or path.suffix in IGNORED_SOURCE_SUFFIXES:
            continue
        entries.append(
            (
                f"{directory.name}/{relative.as_posix()}",
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    if not entries:
        raise VideoRuntimeCacheRejected(
            f"the pinned source package {directory} holds no files, so the cache "
            "cannot tell whether its artifact is still current"
        )
    return entries


def contract_fingerprint(contracts: Iterable[Path]) -> str:
    """Digest the pinned inputs so a changed input invalidates the cache.

    An entry is either a file that pins a version or a directory holding source
    the build consumes. Naming only the pinning files was how a fix to the
    material Worker's web UI failed to reach a release: the contract files were
    unchanged, so the cache handed back the binary frozen before the fix.
    """
    digest = hashlib.sha256()
    entries: list[tuple[str, str]] = []
    for contract in contracts:
        path = Path(contract)
        if path.is_dir() and not path.is_symlink():
            entries.extend(_source_package_entries(path))
            continue
        if not path.is_file():
            raise VideoRuntimeCacheRejected(
                f"the pinned input {path} does not exist, so the cache cannot "
                "tell whether its artifact is still current"
            )
        entries.append((path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
    if not entries:
        raise VideoRuntimeCacheRejected(
            "an artifact with no pinned input cannot be cached safely"
        )
    names = [name for name, _ in entries]
    collisions = sorted({name for name in names if names.count(name) > 1})
    if collisions:
        raise VideoRuntimeCacheRejected(
            "two pinned inputs share the cache key name "
            f"{', '.join(collisions)}, so the key cannot say which one changed"
        )
    # Sorted by name so the caller's argument order never changes the key. The
    # name is length-framed because it now carries a path: without a boundary,
    # two different input sets can concatenate to the same byte stream.
    for name, payload in sorted(entries):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
        digest.update(payload.encode("ascii"))
    return digest.hexdigest()


def _stamp_path(root: Path, name: str) -> Path:
    return root / f"{name}{STAMP_SUFFIX}"


def _stamped_fingerprint(stamp: Path) -> str | None:
    if not stamp.is_file():
        return None
    try:
        document = json.loads(stamp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A stamp we cannot read tells us nothing; rebuilding is always safe,
        # while trusting it would hand a stale artifact to the release step.
        return None
    if not isinstance(document, dict) or document.get("version") != STAMP_VERSION:
        return None
    fingerprint = document.get("fingerprint")
    return fingerprint if isinstance(fingerprint, str) else None


def ensure_cached(
    *,
    name: str,
    contracts: Iterable[Path],
    build: Callable[[Path], None],
    root: Path | None = None,
) -> Path:
    """Return a cached artifact, building it only when the pinned inputs changed.

    `build` receives a destination that does not yet exist and must populate
    it. If it raises, both the partial tree and the stamp are removed, so the
    next call rebuilds rather than shipping half an artifact.
    """
    destination_root = cache_root() if root is None else Path(root)
    destination = destination_root / name
    stamp = _stamp_path(destination_root, name)
    fingerprint = contract_fingerprint(contracts)

    if destination.is_dir() and _stamped_fingerprint(stamp) == fingerprint:
        return destination

    shutil.rmtree(destination, ignore_errors=True)
    stamp.unlink(missing_ok=True)
    destination_root.mkdir(parents=True, exist_ok=True)
    try:
        build(destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if not destination.is_dir():
        raise VideoRuntimeCacheRejected(
            f"the build for {name} reported success without producing {destination}"
        )
    stamp.write_text(
        json.dumps(
            {"version": STAMP_VERSION, "name": name, "fingerprint": fingerprint},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


__all__ = [
    "CACHE_DIRECTORY_NAME",
    "VideoRuntimeCacheRejected",
    "cache_root",
    "contract_fingerprint",
    "ensure_cached",
]
