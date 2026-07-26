#!/usr/bin/env python3
"""The frontend distribution a package audit is allowed to look at.

`frontend/dist` is a single directory shared by every build in this checkout.
`pnpm build:tauri:*-test` rewrites it in `desktop-e2e` mode, and a release run
takes minutes between `tauri build` and the last audit, so auditing it in place
made the verdict a property of the checkout's current state rather than of the
artifact under audit. On 2026-07-26 that surfaced twice in one night: once as a
false rejection of a clean package, and once as a pass whose distribution was
overwritten 13 seconds later.

Freezing the distribution the moment the build returns takes the shared
directory out of the audit's inputs. It is not by itself a proof — the copy is
still taken from shared state — so `audit-production-package.mjs` additionally
requires the frozen copy to be the one the audited binary embedded. This module
narrows the window; that check is what makes a residual race fail closed
instead of producing a verdict about somebody else's build.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
PRODUCTION_ASSETS = FRONTEND_ROOT / "dist"
AUDITED_DISTRIBUTION_NAME = "audited-distribution"


class ProductionAssetsUnavailable(RuntimeError):
    """The distribution a build consumed cannot be frozen or found again."""


def snapshot_production_assets(
    destination: Path, *, source: Path = PRODUCTION_ASSETS
) -> Path:
    """Freeze the distribution the build just consumed, and return its path.

    Call this directly after the `tauri build` that produced the binary; every
    step between the build and the copy is time in which another build can
    replace the shared directory.
    """
    if not (source / "index.html").is_file():
        raise ProductionAssetsUnavailable(
            f"no frontend distribution to audit at {source}"
        )
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=False)
    return destination


def require_frozen_distribution(destination: Path) -> Path:
    """The frozen distribution belonging to an already built artifact.

    Re-auditing a previously built package (`--skip-build`) must reuse the copy
    taken when that package was built. Falling back to the shared directory
    would reintroduce exactly the defect this module exists to remove, so a
    missing snapshot is refused instead.
    """
    if not (destination / "index.html").is_file():
        raise ProductionAssetsUnavailable(
            f"no frozen distribution at {destination}; rebuild without --skip-build"
        )
    return destination
