from __future__ import annotations

import os
from pathlib import Path

import pytest

from automation_tool.executor.pyinstaller_support import (
    PyInstallerPackageMaterializationRejected,
    materialize_internal_package_symlinks,
)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require developer mode")
def test_internal_pyinstaller_symlinks_are_materialized_as_regular_entries(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "automation-tool-executor"
    framework = package_root / "_internal" / "Python.framework" / "Versions" / "3.12"
    framework.mkdir(parents=True)
    python_binary = framework / "Python"
    python_binary.write_bytes(b"python-runtime")
    resources = framework / "Resources"
    resources.mkdir()
    (resources / "Info.plist").write_bytes(b"plist")

    versions = framework.parent
    (versions / "Current").symlink_to("3.12", target_is_directory=True)
    framework_root = versions.parent
    (framework_root / "Python").symlink_to("Versions/Current/Python")
    (framework_root / "Resources").symlink_to(
        "Versions/Current/Resources",
        target_is_directory=True,
    )

    materialize_internal_package_symlinks(package_root)

    assert not any(path.is_symlink() for path in package_root.rglob("*"))
    assert (framework_root / "Python").is_file()
    assert (framework_root / "Python").read_bytes() == b"python-runtime"
    assert (framework_root / "Resources" / "Info.plist").read_bytes() == b"plist"
    assert (versions / "Current" / "Python").read_bytes() == b"python-runtime"
    assert (framework_root / "Python").stat().st_ino != python_binary.stat().st_ino


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require developer mode")
def test_materialization_rejects_a_symlink_that_escapes_the_package(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "automation-tool-executor"
    package_root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    escaping_link = package_root / "escape"
    escaping_link.symlink_to(outside)

    with pytest.raises(PyInstallerPackageMaterializationRejected):
        materialize_internal_package_symlinks(package_root)

    assert escaping_link.is_symlink()
    assert outside.read_bytes() == b"private"


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require developer mode")
@pytest.mark.parametrize("root_kind", ("file", "symlink"))
def test_materialization_rejects_a_non_directory_or_linked_package_root(
    tmp_path: Path,
    root_kind: str,
) -> None:
    package_root = tmp_path / "automation-tool-executor"
    if root_kind == "file":
        package_root.write_bytes(b"not-a-package")
    else:
        real_package = tmp_path / "real-package"
        real_package.mkdir()
        package_root.symlink_to(real_package, target_is_directory=True)

    with pytest.raises(PyInstallerPackageMaterializationRejected):
        materialize_internal_package_symlinks(package_root)


def test_materialization_maps_missing_package_errors_to_the_fixed_rejection(
    tmp_path: Path,
) -> None:
    with pytest.raises(PyInstallerPackageMaterializationRejected) as captured:
        materialize_internal_package_symlinks(tmp_path / "missing-package")

    assert str(captured.value) == "PyInstaller package materialization is rejected"


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "mkfifo"),
    reason="special-file link targets require POSIX",
)
def test_materialization_rejects_a_special_file_target(tmp_path: Path) -> None:
    package_root = tmp_path / "automation-tool-executor"
    package_root.mkdir()
    fifo = package_root / "runtime-pipe"
    os.mkfifo(fifo)
    (package_root / "linked-pipe").symlink_to(fifo.name)

    with pytest.raises(PyInstallerPackageMaterializationRejected):
        materialize_internal_package_symlinks(package_root)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require developer mode")
def test_materialization_rejects_a_directory_link_cycle(tmp_path: Path) -> None:
    package_root = tmp_path / "automation-tool-executor"
    runtime_directory = package_root / "runtime"
    runtime_directory.mkdir(parents=True)
    (runtime_directory / "loop").symlink_to(".", target_is_directory=True)

    with pytest.raises(PyInstallerPackageMaterializationRejected):
        materialize_internal_package_symlinks(package_root)
