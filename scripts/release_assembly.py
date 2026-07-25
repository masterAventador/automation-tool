#!/usr/bin/env python3
"""The production release assembly step: put the browser in, prove it, seal it.

`tauri.conf.json` deliberately does not declare the embedded browser under
`bundle.resources`. EB-16 measured what happens when it does: the bundler
follows symlinks while copying, which drops the Chrome for Testing framework's
`Resources`, `Libraries` and `Helpers` links, duplicates its 230 MB binary and
invalidates the upstream signature — the resulting package is judged
"browser component damaged" by the production resolver on the user's machine.

So the browser has to be installed after the bundle is built. That step used
to live inside `scripts/run_eb_16_acceptance.py` and nowhere else, which meant
a package built by the ordinary candidate path (P9-03/P9-04) shipped with no
browser at all and nothing refused to ship it. The startup gate caught it, but
that is a runtime backstop on the user's machine, not a release gate.

This module is that step, on a reusable path, with the verification made
mandatory: a bundle only becomes distributable after its installed browser has
been re-verified file-by-file against the EB-05 manifest. Sealing happens
strictly after the install, because a signature taken before the browser lands
does not cover it.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from build_embedded_browser_distribution import (
    DistributionRejected,
    verify_distribution,
)
from check_embedded_browser_package import browser_resource_root


class ReleaseAssemblyRejected(RuntimeError):
    """The bundle cannot be assembled into a distributable release."""


def _reject(message: str) -> None:
    raise ReleaseAssemblyRejected(f"release assembly rejected: {message}")


def seal_with_adhoc_signature(application: Path) -> None:
    """Re-seal a macOS bundle so the signature covers the installed browser."""
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(application)],
        check=True,
    )


def require_packaged_browser(
    *,
    application: Path,
    target_id: str,
    platform: str,
    enforce_archive_lock: bool = True,
) -> Path:
    """Fail closed unless the bundle carries a complete, verified browser.

    This is the release gate. An ordinary candidate build produces a bundle
    that fails here, which is the point: such a bundle must never reach a disk
    image, an installer or a user.
    """
    installed = browser_resource_root(application, platform)
    if not installed.is_dir():
        _reject(
            f"the bundle carries no embedded browser at {installed} — it was built "
            "without the release assembly step"
        )
    try:
        verify_distribution(
            staging=installed,
            target_id=target_id,
            enforce_archive_lock=enforce_archive_lock,
        )
    except DistributionRejected as error:
        _reject(f"the packaged browser does not match its manifest: {error}")
    return installed


def install_and_seal(
    *,
    application: Path,
    staging: Path,
    target_id: str,
    platform: str,
    enforce_archive_lock: bool = True,
    seal: Callable[[Path], None] = seal_with_adhoc_signature,
) -> Path:
    """Install the staged browser into a built bundle, verify it, then seal.

    On any rejection the partially installed tree is removed and nothing is
    sealed, so a failed assembly cannot leave behind a bundle that later steps
    would mistake for a finished one.
    """
    from build_embedded_browser_distribution import install_distribution

    installed = browser_resource_root(application, platform)
    if installed.is_symlink() or installed.exists():
        _reject(f"the bundle already carries an embedded browser at {installed}")
    try:
        install_distribution(staging=staging, destination=installed)
        require_packaged_browser(
            application=application,
            target_id=target_id,
            platform=platform,
            enforce_archive_lock=enforce_archive_lock,
        )
    except BaseException:
        shutil.rmtree(installed, ignore_errors=True)
        raise
    seal(application)
    return installed


__all__ = [
    "ReleaseAssemblyRejected",
    "install_and_seal",
    "require_packaged_browser",
    "seal_with_adhoc_signature",
]
