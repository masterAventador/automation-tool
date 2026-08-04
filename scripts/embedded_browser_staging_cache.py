#!/usr/bin/env python3
"""Unpack the digest-locked Chromium once per machine, for every consumer.

Two callers needed a staged, manifest-verified browser tree: the desktop-E2E
prerequisites (into `.local/desktop-e2e/`) and the release packager (into a
fresh directory on every run). Both used the same locked archive, the same
`build_staging()` and the same contract, so the two trees were byte-identical
by construction — and kept apart only by that construction. Unpacking a 171 MB
archive into 328 digested files is also not free, and the release paid it on
every build.

One cache, keyed by the staging contract. That contract pins `archive_sha256`
per target, so replacing the archive changes the key; a cache keyed on anything
weaker would keep serving the previous browser.

What this deliberately does *not* hold is a signed tree. The release re-signs
every Mach-O under the Developer ID and re-takes the digest inventory
afterwards, because the manifest has to describe the shipped bytes. That work
happens on the caller's own copy: nothing signs the cache in place, so a
release run cannot leave a signed tree behind for a dev build to pick up.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from build_embedded_browser_distribution import (  # noqa: E402
    build_distribution_manifest,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)
from embedded_browser_archives import (  # noqa: E402
    MACOS_ARM64_ARCHIVE,
    WINDOWS_X86_64_ARCHIVE,
    archive_path,
)
from video_runtime_cache import ensure_cached  # noqa: E402

STAGING_CONTRACT_PATH: Final = (
    REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
)
LOCKED_ARCHIVES: Final = {
    "macos-arm64": MACOS_ARM64_ARCHIVE,
    "windows-x86_64": WINDOWS_X86_64_ARCHIVE,
}


class EmbeddedBrowserStagingUnavailable(RuntimeError):
    """The locked archive is absent, so no tree can be produced from it."""


def cache_name(target_id: str) -> str:
    """One entry per target: two targets in one entry would overwrite."""
    return f"embedded-browser-{target_id}"


def locked_archive(target_id: str) -> Path:
    try:
        name = LOCKED_ARCHIVES[target_id]
    except KeyError as error:
        raise EmbeddedBrowserStagingUnavailable(
            f"no locked Chromium archive is declared for {target_id}"
        ) from error
    return archive_path(name)


def ensure_staged_browser(*, target_id: str, root: Path | None = None) -> Path:
    """Return the verified, unsigned distribution tree for one target.

    Rebuilds only when the staging contract changed — which includes the
    archive digest it pins.
    """
    archive = locked_archive(target_id)

    def build(destination: Path) -> None:
        if not archive.is_file():
            raise EmbeddedBrowserStagingUnavailable(
                f"the locked Chromium archive is not downloaded yet: {archive}"
            )
        contract = load_staging_contract(STAGING_CONTRACT_PATH)
        build_staging(
            contract=contract,
            target_id=target_id,
            archive_path=archive,
            archive_sha256=sha256_file(archive),
            output=destination,
        )
        build_distribution_manifest(staging=destination, target_id=target_id)

    return ensure_cached(
        name=cache_name(target_id),
        contracts=[STAGING_CONTRACT_PATH],
        build=build,
        root=root,
    )


def copy_staged_browser(*, target_id: str, output: Path) -> Path:
    """Place a private, writable copy of the cached tree at `output`.

    Callers that sign or otherwise rewrite the tree must own their bytes: the
    cache is shared, and a signature applied in place would hand the next
    consumer a tree whose manifest no longer describes it.
    """
    staged = ensure_staged_browser(target_id=target_id)
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # `symlinks=True` is not a preference. Chrome for Testing's framework *is*
    # a symlink tree, the distribution manifest declares every link, and
    # copying with the default (follow) turns 330 files into 848 real ones —
    # `verify_distribution` then reports "symlink entry drifted". A release
    # signs and ships whatever it copied, so this has to be the same shape.
    shutil.copytree(staged, output, symlinks=True)
    return output


__all__ = [
    "LOCKED_ARCHIVES",
    "STAGING_CONTRACT_PATH",
    "EmbeddedBrowserStagingUnavailable",
    "cache_name",
    "copy_staged_browser",
    "ensure_staged_browser",
    "locked_archive",
]
