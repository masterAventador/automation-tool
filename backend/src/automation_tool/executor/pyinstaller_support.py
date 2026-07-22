"""Build-time normalization for signed PyInstaller Executor packages."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path


class PyInstallerPackageMaterializationRejected(ValueError):
    """Fixed failure boundary for unsafe PyInstaller package links."""

    def __init__(self) -> None:
        super().__init__("PyInstaller package materialization is rejected")


@dataclass(frozen=True, slots=True)
class _PackageSymlink:
    path: Path
    target: Path
    raw_target: str
    target_is_directory: bool


def _reject() -> PyInstallerPackageMaterializationRejected:
    return PyInstallerPackageMaterializationRejected()


def materialize_internal_package_symlinks(package_root: Path) -> None:
    """Replace safe package-internal links with independent regular entries."""

    try:
        root_metadata = package_root.lstat()
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            raise _reject()
        resolved_root = package_root.resolve(strict=True)
        links: list[_PackageSymlink] = []
        for candidate in package_root.rglob("*"):
            metadata = candidate.lstat()
            if not stat.S_ISLNK(metadata.st_mode):
                continue
            target = candidate.resolve(strict=True)
            if not target.is_relative_to(resolved_root):
                raise _reject()
            target_metadata = target.lstat()
            target_is_directory = stat.S_ISDIR(target_metadata.st_mode)
            if not target_is_directory and not stat.S_ISREG(target_metadata.st_mode):
                raise _reject()
            resolved_parent = candidate.parent.resolve(strict=True)
            if target_is_directory and (
                resolved_parent == target or resolved_parent.is_relative_to(target)
            ):
                raise _reject()
            links.append(
                _PackageSymlink(
                    path=candidate,
                    target=target,
                    raw_target=os.readlink(candidate),
                    target_is_directory=target_is_directory,
                )
            )

        for link in sorted(
            links,
            key=lambda item: len(item.path.relative_to(package_root).parts),
            reverse=True,
        ):
            if not link.path.is_symlink() or os.readlink(link.path) != link.raw_target:
                raise _reject()
            link.path.unlink()
            if link.target_is_directory:
                shutil.copytree(link.target, link.path, symlinks=False)
            else:
                shutil.copy2(link.target, link.path, follow_symlinks=True)

        if any(candidate.is_symlink() for candidate in package_root.rglob("*")):
            raise _reject()
    except PyInstallerPackageMaterializationRejected:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _reject() from None


__all__ = [
    "PyInstallerPackageMaterializationRejected",
    "materialize_internal_package_symlinks",
]
