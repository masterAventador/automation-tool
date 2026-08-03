from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from automation_tool.executor.pyinstaller_support import (
    PyInstallerPackageMaterializationRejected,
    materialize_internal_package_symlinks,
    remove_browser_installer_scripts,
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


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require developer mode")
def test_materialization_rejects_a_link_target_changed_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "automation-tool-executor"
    package_root.mkdir()
    target = package_root / "runtime"
    target.write_bytes(b"runtime")
    link = package_root / "runtime-link"
    link.symlink_to(target.name)
    original_readlink = os.readlink
    read_count = 0

    def drifting_readlink(path: os.PathLike[str] | str) -> str:
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            return "changed-after-preflight"
        return original_readlink(path)

    monkeypatch.setattr(os, "readlink", drifting_readlink)

    with pytest.raises(PyInstallerPackageMaterializationRejected):
        materialize_internal_package_symlinks(package_root)

    assert link.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require developer mode")
def test_materialization_rejects_a_link_created_after_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "automation-tool-executor"
    package_root.mkdir()
    target = package_root / "runtime"
    target.write_bytes(b"runtime")
    late_link = package_root / "late-link"
    original_rglob = Path.rglob
    scan_count = 0

    def racing_rglob(path: Path, pattern: str) -> Iterator[Path]:
        nonlocal scan_count
        if path == package_root:
            scan_count += 1
            if scan_count == 2:
                late_link.symlink_to(target.name)
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", racing_rglob)

    with pytest.raises(PyInstallerPackageMaterializationRejected):
        materialize_internal_package_symlinks(package_root)

    assert late_link.is_symlink()


DRIVER_BIN = "playwright/driver/package/bin"


def test_browser_installer_scripts_are_dropped_from_the_package() -> None:
    """打包时必须丢掉上游那批"下载并安装系统浏览器"的脚本。

    Playwright 的 driver 自带 17 个这样的脚本（Chrome/Edge 各渠道、WebKit WSL、
    媒体包），作用就是联网下载并安装系统浏览器。产品的硬约束是"不发现、选择、
    下载或回退到系统浏览器"，这些脚本一旦进正式包，用户机器上就多了一条能绕开
    该约束的现成路径——即使产品自己从不调用它们。
    """
    entries = [
        (f"{DRIVER_BIN}/reinstall_chrome_stable_mac.sh", "/src/a", "DATA"),
        (f"{DRIVER_BIN}/reinstall_msedge_beta_win.ps1", "/src/b", "DATA"),
        (f"{DRIVER_BIN}/install_media_pack.ps1", "/src/c", "DATA"),
        (f"{DRIVER_BIN}/install_webkit_wsl.ps1", "/src/d", "DATA"),
    ]
    assert remove_browser_installer_scripts(entries) == []


def test_the_playwright_driver_itself_is_kept() -> None:
    """只丢安装脚本，不动 driver 本体——执行器要靠它驱动包内 Chromium。"""
    entries = [
        ("playwright/driver/package/cli.js", "/src/cli.js", "DATA"),
        ("playwright/driver/node", "/src/node", "BINARY"),
        (f"{DRIVER_BIN}/README.md", "/src/readme", "DATA"),
    ]
    assert remove_browser_installer_scripts(entries) == entries


def test_a_script_outside_the_driver_bin_is_kept() -> None:
    """判据是"driver 的安装脚本"，不是"文件名里有 install"——后者会误伤产品自己的脚本。"""
    entries = [
        ("automation_tool/install_notes.sh", "/src/notes", "DATA"),
        ("automation_tool/py.typed", "/src/py.typed", "DATA"),
    ]
    assert remove_browser_installer_scripts(entries) == entries


def test_windows_style_separators_are_handled() -> None:
    """PyInstaller 在 Windows 上给出反斜杠路径，过滤不能只认斜杠。"""
    windows_path = DRIVER_BIN.replace("/", "\\") + "\\reinstall_chrome_stable_win.ps1"
    entries = [(windows_path, "/src/x", "DATA")]
    assert remove_browser_installer_scripts(entries) == []
