"""Build and audit a Windows Local Executor candidate."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from automation_tool.executor.silero_vad import (
    SileroVadUnavailable,
    audit_packaged_silero_vad_runtime,
)

BUNDLE_NAME = "automation-tool-executor"
ENTRYPOINT_NAME = f"{BUNDLE_NAME}.exe"
MAX_CANDIDATE_FILES = 10_000
MAX_CANDIDATE_BYTES = 8 * 1024 * 1024 * 1024
_SCAN_CHUNK_SIZE = 1024 * 1024
_PE_MACHINE_ARCHITECTURES = {
    0x8664: "x86_64",
    0xAA64: "aarch64",
}
_PE_SUFFIXES = {".dll", ".exe", ".pyd"}
_BROWSER_DIRECTORY_PREFIXES = ("chromium-", "firefox-", "webkit-", "ffmpeg-")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class WindowsExecutorCandidateRejected(ValueError):
    """Fixed failure boundary for an unsafe or incomplete release candidate."""

    def __init__(self, reason: str = "") -> None:
        message = "Windows Executor candidate is rejected"
        super().__init__(f"{message}: {reason}" if reason else message)


@dataclass(frozen=True, slots=True)
class WindowsExecutorCandidateAudit:
    architecture: str
    file_count: int
    pe_file_count: int
    package_size: int


def _reject(reason: str = "") -> WindowsExecutorCandidateRejected:
    return WindowsExecutorCandidateRejected(reason)


_BUILDER_OUTPUT_LINES = 20


def _builder_output(completed: subprocess.CompletedProcess[bytes]) -> str:
    """The tail of what PyInstaller said, so a failed build explains itself."""
    parts: list[str] = []
    for name, raw in (("stderr", completed.stderr), ("stdout", completed.stdout)):
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")
        lines = [line for line in text.splitlines() if line.strip()]
        if lines:
            parts.append(f"{name}:\n" + "\n".join(lines[-_BUILDER_OUTPUT_LINES:]))
    return "\n".join(parts) if parts else "the builder produced no output"


def _normalize_architecture(machine: str) -> str:
    normalized = machine.strip().lower()
    if normalized in {"amd64", "x86_64"}:
        return "x86_64"
    if normalized in {"aarch64", "arm64"}:
        return "aarch64"
    raise _reject()


def _pe_architecture(path: Path) -> str:
    with path.open("rb") as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise _reject()
        pe_offset = int.from_bytes(header[0x3C:0x40], "little", signed=False)
        if not 64 <= pe_offset <= 16 * 1024 * 1024:
            raise _reject()
        source.seek(pe_offset)
        pe_header = source.read(6)
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        raise _reject()
    architecture = _PE_MACHINE_ARCHITECTURES.get(
        int.from_bytes(pe_header[4:6], "little", signed=False)
    )
    if architecture is None:
        raise _reject()
    return architecture


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _contains_marker(path: Path, markers: tuple[bytes, ...]) -> bool:
    if not markers:
        return False
    overlap = max(len(marker) for marker in markers) - 1
    tail = b""
    with path.open("rb") as source:
        while chunk := source.read(_SCAN_CHUNK_SIZE):
            combined = (tail + chunk).lower()
            if any(marker in combined for marker in markers):
                return True
            tail = combined[-overlap:] if overlap > 0 else b""
    return False


def _development_markers(roots: tuple[Path, ...]) -> tuple[bytes, ...]:
    markers: set[bytes] = set()
    for root in roots:
        rendered = os.fspath(root)
        windows = PureWindowsPath(rendered)
        if rendered in {"/", "\\"} or (
            windows.anchor and windows == PureWindowsPath(windows.anchor)
        ):
            raise _reject()
        variants = {
            rendered,
            rendered.replace("\\", "/"),
            rendered.replace("/", "\\"),
        }
        if root.is_absolute():
            resolved = os.fspath(root.resolve(strict=False))
            variants.update(
                {
                    resolved,
                    resolved.replace("\\", "/"),
                    resolved.replace("/", "\\"),
                }
            )
        markers.update(variant.lower().encode() for variant in variants if variant)
    return tuple(sorted(markers))


def audit_windows_executor_candidate(
    *,
    bundle_directory: Path,
    expected_architecture: str,
    forbidden_development_roots: tuple[Path, ...],
) -> WindowsExecutorCandidateAudit:
    """Verify one raw Windows onedir before its offline Manifest and Authenticode steps."""

    try:
        expected = _normalize_architecture(expected_architecture)
        root_metadata = bundle_directory.lstat()
        if _is_link_or_reparse(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
            raise _reject()
        markers = _development_markers(forbidden_development_roots)
        files: list[Path] = []
        pe_files: list[Path] = []
        package_size = 0
        relative_paths: list[str] = []
        for candidate in sorted(
            bundle_directory.rglob("*"),
            key=lambda path: path.relative_to(bundle_directory).as_posix().casefold(),
        ):
            metadata = candidate.lstat()
            if _is_link_or_reparse(metadata):
                raise _reject()
            if stat.S_ISDIR(metadata.st_mode):
                lowered = candidate.name.casefold()
                if lowered in {".local-browsers", "ms-playwright"} or lowered.startswith(
                    _BROWSER_DIRECTORY_PREFIXES
                ):
                    raise _reject()
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise _reject()
            files.append(candidate)
            relative = candidate.relative_to(bundle_directory).as_posix()
            relative_paths.append(relative)
            package_size += metadata.st_size
            if len(files) > MAX_CANDIDATE_FILES or package_size > MAX_CANDIDATE_BYTES:
                raise _reject()
            if _contains_marker(candidate, markers):
                raise _reject()
            if candidate.suffix.casefold() in _PE_SUFFIXES:
                if _pe_architecture(candidate) != expected:
                    raise _reject()
                pe_files.append(candidate)

        entrypoint = bundle_directory / ENTRYPOINT_NAME
        rendered_paths = tuple(path.casefold() for path in relative_paths)
        if (
            not entrypoint.is_file()
            or entrypoint not in pe_files
            or not any(path.endswith("/base_library.zip") for path in rendered_paths)
            or not any("/playwright/" in f"/{path}/" for path in rendered_paths)
            or not any(path.endswith("/playwright/driver/node.exe") for path in rendered_paths)
        ):
            raise _reject()
        try:
            audit_packaged_silero_vad_runtime(bundle_directory)
        except SileroVadUnavailable:
            raise _reject() from None
        return WindowsExecutorCandidateAudit(
            architecture=expected,
            file_count=len(files),
            pe_file_count=len(pe_files),
            package_size=package_size,
        )
    except WindowsExecutorCandidateRejected:
        raise
    except (  # pragma: no cover - defensive OS race normalization
        OSError,
        OverflowError,
        subprocess.SubprocessError,
        ValueError,
    ):
        raise _reject() from None


def _run_pyinstaller(  # pragma: no cover - exercised by the real Windows package runner
    *,
    backend_root: Path,
    config_directory: Path,
    distribution_root: Path,
    python_executable: Path,
    work_root: Path,
) -> None:
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = os.fspath(config_directory)
    completed = subprocess.run(
        [
            os.fspath(python_executable),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            os.fspath(distribution_root),
            "--workpath",
            os.fspath(work_root),
            os.fspath(backend_root / "automation-tool-executor.spec"),
        ],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0:
        raise _reject(_builder_output(completed))


def build_windows_executor_candidate(
    *,
    backend_root: Path,
    output_directory: Path,
    python_executable: Path = Path(sys.executable),
) -> WindowsExecutorCandidateAudit:
    """Build in isolated scratch space, audit twice, and preserve only the candidate."""

    try:
        if platform.system() != "Windows" or output_directory.exists():
            raise _reject()
        architecture = _normalize_architecture(platform.machine())
        resolved_backend = backend_root.resolve(strict=True)
        if not (resolved_backend / "automation-tool-executor.spec").is_file():
            raise _reject()
        output_directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-p902-build-", dir=output_directory.parent
        ) as temporary:
            temporary_root = Path(temporary)
            distribution_root = temporary_root / "dist"
            _run_pyinstaller(
                backend_root=resolved_backend,
                config_directory=temporary_root / "cache",
                distribution_root=distribution_root,
                python_executable=python_executable,
                work_root=temporary_root / "build",
            )
            staged_bundle = distribution_root / BUNDLE_NAME
            audit_windows_executor_candidate(
                bundle_directory=staged_bundle,
                expected_architecture=architecture,
                forbidden_development_roots=(resolved_backend.parent, temporary_root),
            )
            shutil.copytree(staged_bundle, output_directory, symlinks=False)
            try:
                return audit_windows_executor_candidate(
                    bundle_directory=output_directory,
                    expected_architecture=architecture,
                    forbidden_development_roots=(resolved_backend.parent, temporary_root),
                )
            except BaseException:
                shutil.rmtree(output_directory)
                raise
    except WindowsExecutorCandidateRejected:
        raise
    except (  # pragma: no cover - defensive OS race normalization
        OSError,
        RuntimeError,
        shutil.Error,
        subprocess.SubprocessError,
        ValueError,
    ):
        raise _reject() from None


def _parser() -> argparse.ArgumentParser:  # pragma: no cover - real CLI acceptance
    parser = argparse.ArgumentParser(description="Build a Windows Executor onedir candidate")
    parser.add_argument("--backend-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", required=True, type=Path, dest="output_directory")
    return parser


def main() -> int:  # pragma: no cover - real CLI acceptance
    arguments = _parser().parse_args()
    try:
        result = build_windows_executor_candidate(
            backend_root=arguments.backend_root,
            output_directory=arguments.output_directory,
        )
    except WindowsExecutorCandidateRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        "Windows Executor candidate created: "
        f"{result.file_count} files, {result.pe_file_count} PE files"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module CLI smoke uses a subprocess
    raise SystemExit(main())


__all__ = [
    "WindowsExecutorCandidateAudit",
    "WindowsExecutorCandidateRejected",
    "audit_windows_executor_candidate",
    "build_windows_executor_candidate",
    "main",
]
