from __future__ import annotations

import os
import platform
import stat
import subprocess
from pathlib import Path
from typing import cast

import pytest

from automation_tool.executor import windows_candidate
from automation_tool.executor.windows_candidate import (
    WindowsExecutorCandidateRejected,
    audit_windows_executor_candidate,
    build_windows_executor_candidate,
)


def _write(path: Path, content: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _pe(machine: int) -> bytes:
    header = bytearray(512)
    header[:2] = b"MZ"
    header[0x3C:0x40] = (0x80).to_bytes(4, "little")
    header[0x80:0x84] = b"PE\0\0"
    header[0x84:0x86] = machine.to_bytes(2, "little")
    return bytes(header)


def _candidate(root: Path, machine: int = 0x8664) -> Path:
    bundle = root / "automation-tool-executor"
    _write(bundle / "automation-tool-executor.exe", _pe(machine))
    _write(bundle / "_internal/base_library.zip")
    _write(bundle / "_internal/playwright/__init__.py")
    _write(bundle / "_internal/playwright/driver/node.exe", _pe(machine))
    _write(bundle / "_internal/python312.dll", _pe(machine))
    return bundle


def test_audit_accepts_a_native_windows_candidate(tmp_path: Path) -> None:
    bundle = _candidate(tmp_path)

    result = audit_windows_executor_candidate(
        bundle_directory=bundle,
        expected_architecture="x86_64",
        forbidden_development_roots=(tmp_path / "source",),
    )

    assert result.architecture == "x86_64"
    assert result.file_count == 5
    assert result.pe_file_count == 3
    assert result.package_size > 0


def test_pe_parser_accepts_supported_targets_and_rejects_malformed_headers(
    tmp_path: Path,
) -> None:
    target = _write(tmp_path / "binary.exe", _pe(0x8664))
    assert windows_candidate._pe_architecture(target) == "x86_64"
    target.write_bytes(_pe(0xAA64))
    assert windows_candidate._pe_architecture(target) == "aarch64"

    for malformed in (
        b"short",
        b"not-pe" + bytes(512),
        _pe(0x014C),
        b"MZ" + bytes(510),
        b"MZ" + bytes(58) + (4096).to_bytes(4, "little") + bytes(64),
    ):
        target.write_bytes(malformed)
        with pytest.raises(WindowsExecutorCandidateRejected):
            windows_candidate._pe_architecture(target)


def test_architecture_marker_and_reparse_helpers_are_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert windows_candidate._normalize_architecture(" AMD64 ") == "x86_64"
    assert windows_candidate._normalize_architecture("ARM64") == "aarch64"
    with pytest.raises(WindowsExecutorCandidateRejected):
        windows_candidate._normalize_architecture("riscv64")
    with pytest.raises(WindowsExecutorCandidateRejected):
        windows_candidate._development_markers((Path("/"),))
    with pytest.raises(WindowsExecutorCandidateRejected):
        windows_candidate._development_markers((Path("C:/"),))

    target = _write(tmp_path / "marker", b"xxC:/SOURCE/automation-toolyy")
    monkeypatch.setattr(windows_candidate, "_SCAN_CHUNK_SIZE", 4)
    assert windows_candidate._contains_marker(target, (b"c:/source/automation-tool",))
    assert not windows_candidate._contains_marker(target, (b"different",))
    assert not windows_candidate._contains_marker(target, (b"z",))
    assert not windows_candidate._contains_marker(target, ())

    class ReparseMetadata:
        st_mode = stat.S_IFREG
        st_file_attributes = 0x400

    assert windows_candidate._is_link_or_reparse(cast(os.stat_result, ReparseMetadata()))


@pytest.mark.parametrize("missing", ("base_library", "playwright", "driver"))
def test_audit_rejects_each_missing_runtime_dependency(tmp_path: Path, missing: str) -> None:
    bundle = _candidate(tmp_path)
    if missing == "base_library":
        (bundle / "_internal/base_library.zip").unlink()
    elif missing == "playwright":
        (bundle / "_internal/playwright").rename(bundle / "_internal/python-driver")
    else:
        (bundle / "_internal/playwright/driver/node.exe").unlink()

    with pytest.raises(WindowsExecutorCandidateRejected):
        audit_windows_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="x86_64",
            forbidden_development_roots=(),
        )


@pytest.mark.parametrize(
    "relative_path,content",
    (
        (
            "_internal/automation_tool-0.1.0.dist-info/direct_url.json",
            b'{"url":"file:///C:/source/automation-tool/backend"}',
        ),
        ("_internal/ms-playwright/chromium-123/chrome.exe", b"browser-cache"),
    ),
)
def test_audit_rejects_development_paths_and_bundled_browsers(
    tmp_path: Path, relative_path: str, content: bytes
) -> None:
    bundle = _candidate(tmp_path)
    _write(bundle / relative_path, content)

    with pytest.raises(WindowsExecutorCandidateRejected):
        audit_windows_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="x86_64",
            forbidden_development_roots=(Path("C:/source/automation-tool"),),
        )


def test_audit_rejects_wrong_architecture_symlinks_and_resource_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _candidate(tmp_path)
    with pytest.raises(WindowsExecutorCandidateRejected):
        audit_windows_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )

    link = bundle / "_internal/base-library-link.zip"
    try:
        link.symlink_to(os.path.relpath(bundle / "_internal/base_library.zip", link.parent))
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(WindowsExecutorCandidateRejected):
        audit_windows_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="x86_64",
            forbidden_development_roots=(),
        )
    link.unlink()

    monkeypatch.setattr(windows_candidate, "MAX_CANDIDATE_FILES", 4)
    with pytest.raises(WindowsExecutorCandidateRejected):
        audit_windows_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="x86_64",
            forbidden_development_roots=(),
        )


def test_audit_rejects_an_invalid_root(tmp_path: Path) -> None:
    not_a_directory = _write(tmp_path / "not-a-directory")
    with pytest.raises(WindowsExecutorCandidateRejected):
        audit_windows_executor_candidate(
            bundle_directory=not_a_directory,
            expected_architecture="x86_64",
            forbidden_development_roots=(),
        )


# Split out rather than left joined to the case above: `os.mkfifo` does not
# exist on Windows, so one AttributeError used to take the root check down with
# it -- inside the file that audits Windows candidates.
@pytest.mark.skipif(
    not hasattr(os, "mkfifo"),
    reason="no way to place a non-regular file inside a directory tree on this platform",
)
def test_audit_rejects_special_files(tmp_path: Path) -> None:
    bundle = _candidate(tmp_path / "special")
    fifo = bundle / "_internal/fifo"
    os.mkfifo(fifo)
    with pytest.raises(WindowsExecutorCandidateRejected):
        audit_windows_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="x86_64",
            forbidden_development_roots=(),
        )


def test_builder_uses_isolated_staging_and_preserves_only_the_audited_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "source/backend"
    _write(backend_root / "automation-tool-executor.spec", b"fixture spec")
    output = tmp_path / "artifacts/automation-tool-executor"
    observed: dict[str, Path] = {}

    def fake_pyinstaller(
        *,
        backend_root: Path,
        config_directory: Path,
        distribution_root: Path,
        python_executable: Path,
        work_root: Path,
    ) -> None:
        observed.update(
            backend_root=backend_root,
            config_directory=config_directory,
            distribution_root=distribution_root,
            python_executable=python_executable,
            work_root=work_root,
        )
        _candidate(distribution_root)

    monkeypatch.setattr(windows_candidate, "_run_pyinstaller", fake_pyinstaller)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    result = build_windows_executor_candidate(
        backend_root=backend_root,
        output_directory=output,
        python_executable=Path("C:/isolated/python.exe"),
    )

    assert result.architecture == "x86_64"
    assert (output / "automation-tool-executor.exe").is_file()
    assert observed["backend_root"] == backend_root
    assert observed["python_executable"] == Path("C:/isolated/python.exe")
    assert not observed["distribution_root"].exists()
    assert not observed["work_root"].exists()
    assert not observed["config_directory"].exists()


def test_builder_never_overwrites_and_cleans_a_failed_new_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "source/backend"
    _write(backend_root / "automation-tool-executor.spec")
    output = tmp_path / "artifacts/automation-tool-executor"
    _write(output / "keep.txt", b"user-owned")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    with pytest.raises(WindowsExecutorCandidateRejected):
        build_windows_executor_candidate(
            backend_root=backend_root,
            output_directory=output,
        )
    assert (output / "keep.txt").read_bytes() == b"user-owned"

    output = tmp_path / "new-artifact/automation-tool-executor"

    def fake_pyinstaller(**arguments: Path) -> None:
        _candidate(arguments["distribution_root"])

    audits = 0

    def fail_final_audit(**arguments: object) -> windows_candidate.WindowsExecutorCandidateAudit:
        nonlocal audits
        audits += 1
        if audits == 2:
            raise WindowsExecutorCandidateRejected()
        return windows_candidate.WindowsExecutorCandidateAudit("x86_64", 5, 3, 100)

    monkeypatch.setattr(windows_candidate, "_run_pyinstaller", fake_pyinstaller)
    monkeypatch.setattr(windows_candidate, "audit_windows_executor_candidate", fail_final_audit)
    with pytest.raises(WindowsExecutorCandidateRejected):
        build_windows_executor_candidate(
            backend_root=backend_root,
            output_directory=output,
        )
    assert audits == 2
    assert not output.exists()


def test_builder_rejects_other_platforms_and_a_missing_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "source/backend"
    backend_root.mkdir(parents=True)
    output = tmp_path / "artifacts/automation-tool-executor"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    with pytest.raises(WindowsExecutorCandidateRejected):
        build_windows_executor_candidate(
            backend_root=backend_root,
            output_directory=output,
        )

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    with pytest.raises(WindowsExecutorCandidateRejected):
        build_windows_executor_candidate(
            backend_root=backend_root,
            output_directory=output,
        )


def test_a_failed_pyinstaller_run_carries_its_own_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=b"the Executor package cannot be built without vendor/x/y.md\n",
            stderr=b"PyInstaller traceback\n",
        )

    monkeypatch.setattr(windows_candidate.subprocess, "run", failing_run)

    with pytest.raises(WindowsExecutorCandidateRejected) as captured:
        windows_candidate._run_pyinstaller(
            backend_root=tmp_path,
            config_directory=tmp_path / "cache",
            distribution_root=tmp_path / "dist",
            python_executable=Path("C:/Python/python.exe"),
            work_root=tmp_path / "work",
        )

    assert "vendor/x/y.md" in str(captured.value)
    assert "PyInstaller traceback" in str(captured.value)
