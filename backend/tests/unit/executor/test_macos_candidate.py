from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import pytest

from automation_tool.executor import macos_candidate
from automation_tool.executor.macos_candidate import (
    MacOSExecutorCandidateRejected,
    audit_macos_executor_candidate,
    build_macos_executor_candidate,
)


# This module audits the *macOS* candidate, and several of its fixtures are
# POSIX artefacts rather than incidental spellings: a permission bit that
# Windows does not model, a FIFO it cannot create, a `file://` development root
# in POSIX shape. Those cases are reported as skips with a reason rather than
# quietly counted as passes -- on Windows they were "passing" only because
# nothing raised.
requires_posix_filesystem = pytest.mark.skipif(
    os.name == "nt",
    reason="the fixture needs POSIX permission bits, FIFOs or POSIX absolute paths",
)


def _write(path: Path, content: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _arm64_macho() -> bytes:
    return bytes.fromhex("cffaedfe0c000001") + bytes(64)


def _fat_macho(*cpu_types: int) -> bytes:
    records = b"".join(cpu.to_bytes(4, "big") + bytes(16) for cpu in cpu_types)
    return bytes.fromhex("cafebabe") + len(cpu_types).to_bytes(4, "big") + records


def _candidate(root: Path) -> Path:
    bundle = root / "automation-tool-executor"
    entrypoint = _write(bundle / "automation-tool-executor", _arm64_macho())
    entrypoint.chmod(0o755)
    _write(bundle / "_internal/base_library.zip")
    _write(bundle / "_internal/playwright/__init__.py")
    driver = _write(bundle / "_internal/playwright/driver/node")
    driver.chmod(0o755)
    return bundle


def test_audit_accepts_a_native_signing_ready_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _candidate(tmp_path)
    verified: list[Path] = []
    monkeypatch.setattr(
        macos_candidate,
        "_verify_code_signatures",
        lambda paths: verified.extend(paths),
    )

    result = audit_macos_executor_candidate(
        bundle_directory=bundle,
        expected_architecture="aarch64",
        forbidden_development_roots=(tmp_path / "source",),
    )

    assert result.architecture == "aarch64"
    assert result.file_count == 4
    assert result.mach_o_file_count == 1
    assert verified == [bundle / "automation-tool-executor"]


@pytest.mark.parametrize(
    "relative_path,content",
    (
        pytest.param(
            "_internal/automation_tool-0.1.0.dist-info/direct_url.json",
            b'{"url":"file:///private/source/backend"}',
            marks=requires_posix_filesystem,
        ),
        ("_internal/ms-playwright/chromium-123/chrome", b"browser-cache"),
    ),
)
def test_audit_rejects_development_paths_and_bundled_browsers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
    content: bytes,
) -> None:
    bundle = _candidate(tmp_path)
    _write(bundle / relative_path, content)
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)

    with pytest.raises(MacOSExecutorCandidateRejected) as captured:
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(Path("/private/source"),),
        )

    assert str(captured.value) == "macOS Executor candidate is rejected"


def test_audit_rejects_missing_runtime_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _candidate(tmp_path)
    (bundle / "_internal/base_library.zip").unlink()
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)

    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )


def test_audit_rejects_wrong_architecture_or_broken_code_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _candidate(tmp_path)
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="x86_64",
            forbidden_development_roots=(),
        )

    def reject_signature(paths: tuple[Path, ...]) -> None:
        raise MacOSExecutorCandidateRejected()

    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", reject_signature)
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )


def test_audit_rejects_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _candidate(tmp_path)
    target = bundle / "_internal/base_library.zip"
    link = bundle / "_internal/base_library-link.zip"
    try:
        link.symlink_to(os.path.relpath(target, link.parent))
    except OSError:
        pytest.skip("symlinks are unavailable")
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)

    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )


def test_mach_o_parser_accepts_native_thin_and_fat_headers_and_rejects_malformed_ones(
    tmp_path: Path,
) -> None:
    target = _write(tmp_path / "binary", b"short")
    assert macos_candidate._mach_o_architectures(target) is None
    target.write_bytes(b"not-mach-o")
    assert macos_candidate._mach_o_architectures(target) is None
    target.write_bytes(bytes.fromhex("feedfacf01000007") + bytes(64))
    assert macos_candidate._mach_o_architectures(target) == frozenset(("x86_64",))
    target.write_bytes(_fat_macho(0x01000007, 0x0100000C))
    assert macos_candidate._mach_o_architectures(target) == frozenset(("x86_64", "aarch64"))

    for malformed in (
        bytes.fromhex("cffaedfe00000000") + bytes(64),
        bytes.fromhex("cafebabe00000000"),
        bytes.fromhex("cafebabe00000002") + bytes(20),
        _fat_macho(0),
    ):
        target.write_bytes(malformed)
        with pytest.raises(MacOSExecutorCandidateRejected):
            macos_candidate._mach_o_architectures(target)


def test_marker_scanner_handles_chunk_boundaries_and_rejects_a_broad_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _write(tmp_path / "payload", b"xx/private/source/yy")
    monkeypatch.setattr(macos_candidate, "_SCAN_CHUNK_SIZE", 4)
    assert macos_candidate._contains_marker(target, (b"/private/source",))
    assert not macos_candidate._contains_marker(target, (b"/different/source",))
    assert not macos_candidate._contains_marker(target, (b"z",))
    assert not macos_candidate._contains_marker(target, ())
    with pytest.raises(MacOSExecutorCandidateRejected):
        macos_candidate._development_markers((Path("/"),))


def test_architecture_normalization_is_closed() -> None:
    assert macos_candidate._normalize_architecture(" AMD64 ") == "x86_64"
    assert macos_candidate._normalize_architecture("arm64") == "aarch64"
    with pytest.raises(MacOSExecutorCandidateRejected):
        macos_candidate._normalize_architecture("riscv64")


@pytest.mark.parametrize("directory_name", (".local-browsers", "firefox-123"))
def test_audit_rejects_each_browser_cache_directory_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_name: str,
) -> None:
    bundle = _candidate(tmp_path)
    _write(bundle / f"_internal/{directory_name}/payload")
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )


# Split out: `os.mkfifo` is absent on Windows, and one AttributeError used to
# take the root and resource-limit checks down with it.
@requires_posix_filesystem
def test_audit_rejects_special_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)
    bundle = _candidate(tmp_path / "special")
    fifo = bundle / "_internal/fifo"
    os.mkfifo(fifo)
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )


def test_audit_rejects_invalid_roots_and_resource_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)
    not_a_directory = _write(tmp_path / "not-a-directory")
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=not_a_directory,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )

    bundle = _candidate(tmp_path / "limit")
    monkeypatch.setattr(macos_candidate, "MAX_CANDIDATE_FILES", 3)
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )
    monkeypatch.setattr(macos_candidate, "MAX_CANDIDATE_FILES", 10_000)
    monkeypatch.setattr(macos_candidate, "MAX_CANDIDATE_BYTES", 1)
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )


@pytest.mark.parametrize(
    "failure",
    (
        "missing",
        pytest.param("not_executable", marks=requires_posix_filesystem),
        "not_mach_o",
    ),
)
def test_audit_rejects_an_invalid_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    bundle = _candidate(tmp_path)
    entrypoint = bundle / "automation-tool-executor"
    if failure == "missing":
        entrypoint.unlink()
    elif failure == "not_executable":
        entrypoint.chmod(0o644)
    else:
        entrypoint.write_bytes(b"not-mach-o")
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )


@pytest.mark.parametrize("missing", ("playwright", "driver"))
def test_audit_rejects_each_missing_runtime_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    bundle = _candidate(tmp_path)
    if missing == "playwright":
        (bundle / "_internal/playwright").rename(bundle / "_internal/python-driver")
    else:
        (bundle / "_internal/playwright/driver/node").unlink()
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)
    with pytest.raises(MacOSExecutorCandidateRejected):
        audit_macos_executor_candidate(
            bundle_directory=bundle,
            expected_architecture="aarch64",
            forbidden_development_roots=(),
        )


def test_builder_uses_an_isolated_staging_root_and_keeps_the_audited_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "source/backend"
    _write(backend_root / "automation-tool-executor.spec", b"fixture spec")
    output = tmp_path / "artifacts/automation-tool-executor"
    observed: dict[str, Path] = {}
    signed: list[Path] = []

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

    monkeypatch.setattr(macos_candidate, "_run_pyinstaller", fake_pyinstaller)
    monkeypatch.setattr(
        macos_candidate,
        "_apply_adhoc_code_signatures",
        lambda bundle: signed.append(bundle),
    )
    monkeypatch.setattr(macos_candidate, "_verify_code_signatures", lambda paths: None)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")

    result = build_macos_executor_candidate(
        backend_root=backend_root,
        output_directory=output,
        python_executable=Path("/isolated/python"),
    )

    assert result.architecture == "aarch64"
    assert output.is_dir()
    assert (output / "automation-tool-executor").is_file()
    assert observed["backend_root"] == backend_root
    assert observed["python_executable"] == Path("/isolated/python")
    assert len(signed) == 1
    assert signed[0].name == "automation-tool-executor"
    assert not observed["distribution_root"].exists()
    assert not observed["work_root"].exists()
    assert not observed["config_directory"].exists()


def test_builder_never_overwrites_an_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "source/backend"
    _write(backend_root / "automation-tool-executor.spec", b"fixture spec")
    output = tmp_path / "artifacts/automation-tool-executor"
    _write(output / "keep.txt", b"user-owned")
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")

    with pytest.raises(MacOSExecutorCandidateRejected):
        build_macos_executor_candidate(
            backend_root=backend_root,
            output_directory=output,
            python_executable=Path("/isolated/python"),
        )

    assert (output / "keep.txt").read_bytes() == b"user-owned"


def test_builder_rejects_other_platforms_and_a_missing_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "source/backend"
    backend_root.mkdir(parents=True)
    output = tmp_path / "artifacts/automation-tool-executor"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    with pytest.raises(MacOSExecutorCandidateRejected):
        build_macos_executor_candidate(
            backend_root=backend_root,
            output_directory=output,
            python_executable=Path("/isolated/python"),
        )

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    with pytest.raises(MacOSExecutorCandidateRejected):
        build_macos_executor_candidate(
            backend_root=backend_root,
            output_directory=output,
            python_executable=Path("/isolated/python"),
        )


def test_builder_removes_its_new_output_when_the_final_audit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "source/backend"
    _write(backend_root / "automation-tool-executor.spec")
    output = tmp_path / "artifacts/automation-tool-executor"

    def fake_pyinstaller(**arguments: Path) -> None:
        _candidate(arguments["distribution_root"])

    audits = 0

    def fail_final_audit(**arguments: object) -> macos_candidate.MacOSExecutorCandidateAudit:
        nonlocal audits
        audits += 1
        if audits == 2:
            raise MacOSExecutorCandidateRejected()
        return macos_candidate.MacOSExecutorCandidateAudit("aarch64", 4, 1, 100)

    monkeypatch.setattr(macos_candidate, "_run_pyinstaller", fake_pyinstaller)
    monkeypatch.setattr(macos_candidate, "_apply_adhoc_code_signatures", lambda bundle: None)
    monkeypatch.setattr(macos_candidate, "audit_macos_executor_candidate", fail_final_audit)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")

    with pytest.raises(MacOSExecutorCandidateRejected):
        build_macos_executor_candidate(
            backend_root=backend_root,
            output_directory=output,
            python_executable=Path("/isolated/python"),
        )

    assert audits == 2
    assert not output.exists()


def test_framework_root_binary_is_pruned_only_when_it_matches_the_versioned_binary(
    tmp_path: Path,
) -> None:
    bundle = _candidate(tmp_path)
    framework = bundle / "_internal/Python.framework"
    root_alias = _write(framework / "Python", b"same-python-binary")
    canonical = _write(framework / "Versions/3.12/Python", b"same-python-binary")
    (framework / "Versions/Current").mkdir()
    _write(framework / "Versions/README")
    (framework / "Versions/Alias").symlink_to("3.12")

    macos_candidate._prune_redundant_framework_binaries(bundle)

    assert not root_alias.exists()
    assert canonical.read_bytes() == b"same-python-binary"

    root_alias = _write(framework / "Python", b"different-python-binary")
    with pytest.raises(MacOSExecutorCandidateRejected):
        macos_candidate._prune_redundant_framework_binaries(bundle)
    assert root_alias.read_bytes() == b"different-python-binary"


def test_framework_pruning_rejects_ambiguous_shapes(tmp_path: Path) -> None:
    bundle = _candidate(tmp_path)
    internal = bundle / "_internal"
    _write(internal / "Broken.framework")
    with pytest.raises(MacOSExecutorCandidateRejected):
        macos_candidate._prune_redundant_framework_binaries(bundle)

    (internal / "Broken.framework").unlink()
    framework = internal / "Python.framework"
    (framework / "Versions/3.12").mkdir(parents=True)
    macos_candidate._prune_redundant_framework_binaries(bundle)

    (framework / "Python").mkdir()
    with pytest.raises(MacOSExecutorCandidateRejected):
        macos_candidate._prune_redundant_framework_binaries(bundle)
    (framework / "Python").rmdir()

    _write(framework / "Python", b"alias")
    (framework / "Versions/3.13").mkdir()
    with pytest.raises(MacOSExecutorCandidateRejected):
        macos_candidate._prune_redundant_framework_binaries(bundle)
    (framework / "Versions/3.13").rmdir()

    with pytest.raises(MacOSExecutorCandidateRejected):
        macos_candidate._prune_redundant_framework_binaries(bundle)
    canonical = _write(framework / "Versions/canonical", b"target")
    binary = framework / "Versions/3.12/Python"
    binary.symlink_to(os.path.relpath(canonical, binary.parent))
    with pytest.raises(MacOSExecutorCandidateRejected):
        macos_candidate._prune_redundant_framework_binaries(bundle)


def test_a_failed_pyinstaller_run_carries_its_own_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build that fails must say why.

    On 2026-07-26 a release build stopped at this call with the message
    "macOS Executor candidate is rejected" and nothing else. PyInstaller had
    printed the real reason — a missing vendored file the authoring agent reads
    at startup — and `capture_output=True` had swallowed it. Finding it took a
    hand-run of the same command. On a build machine there is nobody to do that.
    """

    def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=b"the Executor package cannot be built without vendor/x/y.md\n",
            stderr=b"",
        )

    monkeypatch.setattr(macos_candidate.subprocess, "run", failing_run)

    with pytest.raises(MacOSExecutorCandidateRejected) as captured:
        macos_candidate._run_pyinstaller(
            backend_root=tmp_path,
            config_directory=tmp_path / "cache",
            distribution_root=tmp_path / "dist",
            python_executable=Path("/usr/bin/false"),
            work_root=tmp_path / "work",
        )

    assert "vendor/x/y.md" in str(captured.value)


def test_a_pyinstaller_run_that_says_nothing_still_reports_that_it_said_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def silent_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(macos_candidate.subprocess, "run", silent_run)

    with pytest.raises(MacOSExecutorCandidateRejected) as captured:
        macos_candidate._run_pyinstaller(
            backend_root=tmp_path,
            config_directory=tmp_path / "cache",
            distribution_root=tmp_path / "dist",
            python_executable=Path("/usr/bin/false"),
            work_root=tmp_path / "work",
        )

    assert "no output" in str(captured.value)
