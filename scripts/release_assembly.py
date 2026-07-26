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

import json
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


class _VideoResource:
    """One runtime resource the production video code resolves from Resources.

    `staging_name` is what the build scripts produce; `installed_parts` is where
    the production resolver actually looks, which is not always the same shape —
    both Workers are read from `<name>/package/...` while the media toolchain is
    read from `<name>/...` directly. Getting this mapping wrong produces a
    bundle that looks complete and still fails at runtime, so the mapping is
    declared once here rather than reconstructed at each call site.
    """

    def __init__(
        self,
        *,
        staging_name: str,
        installed_parts: tuple[str, ...],
        required_files: tuple[str, ...],
        windows_executables: tuple[str, ...] = (),
    ) -> None:
        self.staging_name = staging_name
        self.installed_parts = installed_parts
        self.required_files = required_files
        self.windows_executables = windows_executables

    def required_for(self, platform: str) -> tuple[str, ...]:
        if platform != "windows":
            return self.required_files
        return tuple(
            f"{name}.exe" if name in self.windows_executables else name
            for name in self.required_files
        )


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# One declaration of what a distributable package must carry, shared with
# `frontend/scripts/audit-production-package.mjs` and
# `scripts/check_release_package_wiring.py`. Each gate used to know its own hand
# copied subset of this list, which is how three video runtime resources reached
# a user while every gate stayed green.
RELEASE_RESOURCE_CONTRACT = (
    REPOSITORY_ROOT / "contracts/quality/release-package-resources.v1.json"
)


def load_release_resources() -> tuple[dict[str, object], ...]:
    """Return every resource a distributable package must carry, in order."""
    document = json.loads(RELEASE_RESOURCE_CONTRACT.read_text(encoding="utf-8"))
    resources = document.get("resources")
    if not isinstance(resources, list) or not resources:
        _reject("the release package resource contract declares no resources")
    return tuple(resources)


RELEASE_PACKAGE_RESOURCES: tuple[dict[str, object], ...] = load_release_resources()

VIDEO_RUNTIME_RESOURCES: tuple[_VideoResource, ...] = tuple(
    _VideoResource(
        staging_name=str(resource["name"]),
        installed_parts=tuple(resource["installedParts"]),
        required_files=tuple(resource["requiredFiles"]),
        windows_executables=tuple(resource["windowsExecutables"]),
    )
    for resource in RELEASE_PACKAGE_RESOURCES
    if resource["category"] == "video"
)


def resource_directory(application: Path, platform: str) -> Path:
    """Return the directory the production resolver treats as its resource root."""
    if platform == "macos":
        return application.joinpath("Contents", "Resources")
    if platform == "windows":
        return application
    _reject(f"unsupported package platform: {platform}")
    raise AssertionError("unreachable")


def require_packaged_video_runtime(
    *, application: Path, platform: str
) -> dict[str, Path]:
    """Fail closed unless the bundle carries all three video runtime resources.

    This is the second half of the same release gate as the browser. The video
    resources went missing for the same reason the browser did — every BM/IM
    acceptance script builds ffmpeg and both Workers itself and hands the paths
    to a `video-studio-e2e` build through environment variables, while the
    production build reads this directory and nothing ever wrote to it.
    """
    root = resource_directory(application, platform)
    installed: dict[str, Path] = {}
    for resource in VIDEO_RUNTIME_RESOURCES:
        location = root.joinpath(*resource.installed_parts)
        if not location.is_dir():
            _reject(
                f"the bundle carries no {resource.staging_name} at {location} — it "
                "was built without the video runtime assembly step"
            )
        for name in resource.required_for(platform):
            payload = location / name
            if not payload.is_file() or payload.stat().st_size == 0:
                _reject(
                    f"{resource.staging_name} is incomplete: {name} is missing or "
                    "empty, so the production resolver would find the directory "
                    "and still fail to launch"
                )
        installed[resource.staging_name] = location
    return installed


def install_video_runtime(
    *, application: Path, staging: Path, platform: str
) -> dict[str, Path]:
    """Install the three video runtime resources, then verify them as a set.

    On any rejection every tree installed by this call is removed, so a failed
    assembly cannot leave a partially populated bundle for a later step to
    mistake for a finished one.
    """
    root = resource_directory(application, platform)
    written: list[Path] = []
    try:
        for resource in VIDEO_RUNTIME_RESOURCES:
            source = staging / resource.staging_name
            if not source.is_dir():
                _reject(
                    f"the staging tree carries no {resource.staging_name} at {source}"
                )
            destination = root.joinpath(*resource.installed_parts)
            if destination.is_symlink() or destination.exists():
                _reject(
                    f"the bundle already carries {resource.staging_name} at "
                    f"{destination}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            # The video resources contain no symlinked frameworks, so a plain
            # copy is correct here; the browser needs its own installer because
            # its framework links must survive.
            shutil.copytree(source, destination, symlinks=True)
            written.append(root / resource.installed_parts[0])
        return require_packaged_video_runtime(
            application=application, platform=platform
        )
    except BaseException:
        for path in written:
            shutil.rmtree(path, ignore_errors=True)
        raise


__all__ = [
    "RELEASE_PACKAGE_RESOURCES",
    "RELEASE_RESOURCE_CONTRACT",
    "VIDEO_RUNTIME_RESOURCES",
    "ReleaseAssemblyRejected",
    "install_and_seal",
    "install_video_runtime",
    "load_release_resources",
    "require_packaged_browser",
    "require_packaged_video_runtime",
    "resource_directory",
    "seal_with_adhoc_signature",
]
