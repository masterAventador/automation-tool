#!/usr/bin/env python3
"""Write the Tauri configuration a release build uses, from one declaration.

`bundle.resources` is the only half of the release payload the Tauri bundler is
responsible for. Which half that is differs by target, and the split is not
arbitrary:

* macOS — the bundler follows symlinks while copying, which destroys the Chrome
  for Testing framework and invalidates its upstream signature. Only the Local
  Executor may be declared; the browser and the three video runtime resources
  are installed into the finished `.app` by `scripts/release_assembly.py`.
* Windows — there are no symlinks in the target and an NSIS installer cannot be
  reopened once built, so every resource is declared here and copied by the
  bundler from a payload the assembler has already verified.

Both halves used to be written out by hand inside the two EB-16 acceptance
scripts. That is how three video runtime resources came to be absent from a
shipped macOS package while every gate stayed green: no code anywhere related
"what the configuration declares" to "what the product resolves at runtime".
This module derives both from `contracts/quality/release-package-resources.v1.json`,
and refuses to write a configuration that leaves a bundler-owned resource out.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from release_assembly import RELEASE_PACKAGE_RESOURCES

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TAURI_ROOT = REPOSITORY_ROOT / "frontend/src-tauri"
CANDIDATE_CONFIGURATIONS = {
    "macos": TAURI_ROOT / "tauri.macos-candidate.conf.json",
    "windows": TAURI_ROOT / "tauri.windows-candidate.conf.json",
}


class ReleaseConfigurationRejected(RuntimeError):
    """The release configuration cannot be written as declared."""


def _reject(message: str) -> None:
    raise ReleaseConfigurationRejected(f"release configuration rejected: {message}")


def bundler_declared_resources(platform: str) -> tuple[str, ...]:
    """Names of the resources the bundler must ship on this target."""
    if platform not in CANDIDATE_CONFIGURATIONS:
        _reject(f"unsupported release platform: {platform}")
    return tuple(
        str(resource["name"])
        for resource in RELEASE_PACKAGE_RESOURCES
        if resource["bundlerDeclared"][platform] is True
    )


def installed_destination(name: str) -> str:
    for resource in RELEASE_PACKAGE_RESOURCES:
        if resource["name"] == name:
            return "/".join(resource["installedParts"])
    _reject(f"{name} is not a declared release resource")
    raise AssertionError("unreachable")


def assembler_installed_resources(platform: str) -> tuple[str, ...]:
    """Names of the resources the release assembler must install itself."""
    declared = set(bundler_declared_resources(platform))
    return tuple(
        str(resource["name"])
        for resource in RELEASE_PACKAGE_RESOURCES
        if str(resource["name"]) not in declared
    )


def relative_to_tauri_root(path: Path) -> str:
    """Tauri resolves resource sources against its own root, not the drive."""
    try:
        relative = os.path.relpath(path, TAURI_ROOT).replace(os.sep, "/")
    except ValueError:
        # Windows raises rather than returning something the checks below can
        # catch when the two paths sit on different drives -- which is the very
        # case this rejects, so it is spelled as the rejection, not a crash.
        _reject("resource source must be relative to the Tauri root")
        raise AssertionError("unreachable") from None
    if os.path.isabs(relative) or ":" in relative:
        _reject("resource source must be relative to the Tauri root")
    return relative


def write_release_configuration(
    *,
    directory: Path,
    platform: str,
    sources: dict[str, Path],
    name: str,
    bundle_overrides: dict[str, object] | None = None,
    relative_sources: bool = False,
) -> Path:
    """Write one release configuration declaring every bundler-owned resource.

    `sources` must name exactly the resources the bundler owns on this target.
    A missing entry is refused rather than silently written out as a smaller
    package — that omission is the defect this module exists to prevent, and a
    configuration is the last place it can still be caught cheaply.
    """
    required = set(bundler_declared_resources(platform))
    provided = set(sources)
    if provided != required:
        _reject(
            "the release payload does not match the declared resources: missing "
            f"{sorted(required - provided)}, unexpected {sorted(provided - required)}"
        )
    configuration = json.loads(
        CANDIDATE_CONFIGURATIONS[platform].read_text(encoding="utf-8")
    )
    resources: dict[str, str] = {}
    for resource_name, source in sources.items():
        rendered = (
            f"{relative_to_tauri_root(source)}/"
            if relative_sources
            else f"{os.fspath(source)}{os.sep}"
        )
        resources[rendered] = f"{installed_destination(resource_name)}/"
    configuration["bundle"]["resources"] = resources
    for key, value in (bundle_overrides or {}).items():
        configuration["bundle"][key] = value
    destination = directory / name
    destination.write_text(
        json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def write_macos_release_configuration(
    *, directory: Path, executor: Path, name: str
) -> Path:
    """macOS: the Executor is the only tree the bundler may be trusted with."""
    return write_release_configuration(
        directory=directory,
        platform="macos",
        sources={"local-executor": executor},
        name=name,
        bundle_overrides={"macOS": {"signingIdentity": "-"}},
    )


def write_windows_release_configuration(
    *, directory: Path, executor: Path, payload: Path, name: str
) -> Path:
    """Windows: every resource ships through the bundler, from a verified payload."""
    sources: dict[str, Path] = {}
    for resource_name in bundler_declared_resources("windows"):
        if resource_name == "local-executor":
            sources[resource_name] = executor
            continue
        sources[resource_name] = payload.joinpath(
            *installed_destination(resource_name).split("/")
        )
    return write_release_configuration(
        directory=directory,
        platform="windows",
        sources=sources,
        name=name,
        relative_sources=True,
    )


def merge_configuration(base: object, overlay: object) -> object:
    """Merge a Tauri config overlay the same way `tauri build --config` does."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = merge_configuration(merged.get(key), value)
        return merged
    return overlay


def effective_configuration(*, overlay: Path, directory: Path, name: str) -> Path:
    """Write the configuration the build actually used, for auditing."""
    merged = merge_configuration(
        json.loads((TAURI_ROOT / "tauri.conf.json").read_text(encoding="utf-8")),
        json.loads(overlay.read_text(encoding="utf-8")),
    )
    destination = directory / name
    destination.write_text(
        json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


__all__ = [
    "ReleaseConfigurationRejected",
    "assembler_installed_resources",
    "bundler_declared_resources",
    "effective_configuration",
    "installed_destination",
    "merge_configuration",
    "relative_to_tauri_root",
    "write_macos_release_configuration",
    "write_release_configuration",
    "write_windows_release_configuration",
]
