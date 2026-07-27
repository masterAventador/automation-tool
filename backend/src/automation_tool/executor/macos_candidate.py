"""Build and audit a signing-ready macOS Local Executor candidate."""

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
from pathlib import Path
from typing import Literal

BUNDLE_NAME = "automation-tool-executor"
ENTRYPOINT_NAME = BUNDLE_NAME
MAX_CANDIDATE_FILES = 10_000
MAX_CANDIDATE_BYTES = 8 * 1024 * 1024 * 1024
_SCAN_CHUNK_SIZE = 1024 * 1024
_THIN_MACH_O_ENDIAN: dict[bytes, Literal["little", "big"]] = {
    b"\xce\xfa\xed\xfe": "little",
    b"\xcf\xfa\xed\xfe": "little",
    b"\xfe\xed\xfa\xce": "big",
    b"\xfe\xed\xfa\xcf": "big",
}
_FAT_MACH_O: dict[bytes, tuple[Literal["little", "big"], int]] = {
    b"\xca\xfe\xba\xbe": ("big", 20),
    b"\xbe\xba\xfe\xca": ("little", 20),
    b"\xca\xfe\xba\xbf": ("big", 32),
    b"\xbf\xba\xfe\xca": ("little", 32),
}
_CPU_ARCHITECTURES = {
    0x01000007: "x86_64",
    0x0100000C: "aarch64",
}
_BROWSER_DIRECTORY_PREFIXES = ("chromium-", "firefox-", "webkit-", "ffmpeg-")


class MacOSExecutorCandidateRejected(ValueError):
    """Fixed failure boundary for an unsafe or incomplete release candidate.

    The audit rejections stay deliberately uniform: which of the fifteen checks
    tripped is not something a caller should branch on. A failed *build* is a
    different thing — nobody is being denied information, the operator simply
    needs to know what the builder said. This module is reached only from
    `scripts/build_release_package.py` and the acceptance drivers; it is never
    on a product runtime path, so a reason here cannot reach a user.
    """

    def __init__(self, reason: str = "") -> None:
        message = "macOS Executor candidate is rejected"
        super().__init__(f"{message}: {reason}" if reason else message)


@dataclass(frozen=True, slots=True)
class MacOSExecutorCandidateAudit:
    architecture: str
    file_count: int
    mach_o_file_count: int
    package_size: int


def _reject(reason: str = "") -> MacOSExecutorCandidateRejected:
    return MacOSExecutorCandidateRejected(reason)


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
    if normalized in {"arm64", "aarch64"}:
        return "aarch64"
    if normalized in {"x86_64", "amd64"}:
        return "x86_64"
    raise _reject()


def _mach_o_architectures(path: Path) -> frozenset[str] | None:
    with path.open("rb") as source:
        header = source.read(4096)
    if len(header) < 8:
        return None
    magic = header[:4]
    if magic in _THIN_MACH_O_ENDIAN:
        cpu_type = int.from_bytes(header[4:8], _THIN_MACH_O_ENDIAN[magic], signed=False)
        architecture = _CPU_ARCHITECTURES.get(cpu_type)
        if architecture is None:
            raise _reject()
        return frozenset((architecture,))
    fat = _FAT_MACH_O.get(magic)
    if fat is None:
        return None
    byte_order, record_size = fat
    architecture_count = int.from_bytes(header[4:8], byte_order, signed=False)
    if not 1 <= architecture_count <= 32 or 8 + architecture_count * record_size > len(header):
        raise _reject()
    architectures: set[str] = set()
    for index in range(architecture_count):
        offset = 8 + index * record_size
        cpu_type = int.from_bytes(header[offset : offset + 4], byte_order, signed=False)
        architecture = _CPU_ARCHITECTURES.get(cpu_type)
        if architecture is None:
            raise _reject()
        architectures.add(architecture)
    return frozenset(architectures)


def _contains_marker(path: Path, markers: tuple[bytes, ...]) -> bool:
    if not markers:
        return False
    overlap = max(len(marker) for marker in markers) - 1
    tail = b""
    with path.open("rb") as source:
        while chunk := source.read(_SCAN_CHUNK_SIZE):
            combined = tail + chunk
            if any(marker in combined for marker in markers):
                return True
            tail = combined[-overlap:] if overlap > 0 else b""
    return False


def _development_markers(roots: tuple[Path, ...]) -> tuple[bytes, ...]:
    markers: set[bytes] = set()
    for root in roots:
        resolved = root.resolve(strict=False)
        if resolved == Path(resolved.anchor):
            raise _reject()
        rendered = os.fspath(resolved)
        markers.add(rendered.encode())
        markers.add(rendered.replace(os.sep, "/").encode())
    return tuple(sorted(markers))


def _verify_code_signatures(  # pragma: no cover - exercised by the real macOS package runner
    paths: tuple[Path, ...],
) -> None:
    codesign = shutil.which("codesign")
    if codesign is None or not paths:
        raise _reject()
    for path in paths:
        completed = subprocess.run(
            [codesign, "--verify", "--strict", "--verbose=0", os.fspath(path)],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise _reject()


def _apply_adhoc_code_signatures(  # pragma: no cover - exercised by the real macOS package runner
    bundle_directory: Path,
) -> None:
    codesign = shutil.which("codesign")
    if codesign is None:
        raise _reject()
    mach_o_files: list[Path] = []
    for candidate in bundle_directory.rglob("*"):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise _reject()
        if stat.S_ISREG(metadata.st_mode) and _mach_o_architectures(candidate) is not None:
            mach_o_files.append(candidate)
    if not mach_o_files:
        raise _reject()
    for path in sorted(mach_o_files, key=lambda candidate: len(candidate.parts), reverse=True):
        completed = subprocess.run(
            [
                codesign,
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
                os.fspath(path),
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise _reject()


def audit_macos_executor_candidate(
    *,
    bundle_directory: Path,
    expected_architecture: str,
    forbidden_development_roots: tuple[Path, ...],
) -> MacOSExecutorCandidateAudit:
    """Verify one raw onedir before its offline Manifest and Developer ID signing steps."""

    try:
        expected = _normalize_architecture(expected_architecture)
        root_metadata = bundle_directory.lstat()
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            raise _reject()
        markers = _development_markers(forbidden_development_roots)
        files: list[Path] = []
        package_size = 0
        relative_paths: list[Path] = []
        mach_o_files: list[Path] = []
        for candidate in sorted(
            bundle_directory.rglob("*"),
            key=lambda path: path.relative_to(bundle_directory).as_posix(),
        ):
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise _reject()
            if stat.S_ISDIR(metadata.st_mode):
                relative = candidate.relative_to(bundle_directory)
                lowered = relative.name.lower()
                if lowered in {".local-browsers", "ms-playwright"} or lowered.startswith(
                    _BROWSER_DIRECTORY_PREFIXES
                ):
                    raise _reject()
                if lowered.endswith(".framework"):
                    # A framework only signs when `Versions/Current` is a symlink,
                    # and this payload forbids symlinks a few lines above. The two
                    # requirements cannot both hold, so the interpreter that emitted
                    # this — not the framework — is what has to change. Saying so
                    # here replaces a codesign message twenty minutes downstream
                    # that names neither the interpreter nor the fix.
                    raise _reject(
                        f"{relative.as_posix()} is a framework, which this payload "
                        "cannot carry: signing one requires Versions/Current to be a "
                        "symlink, and symlinks are refused here. Rebuild backend/.venv "
                        "on a standalone CPython (uv-managed, ships libpython*.dylib) "
                        "rather than a framework-layout interpreter such as Homebrew "
                        "python@3.12 — see docs/macos-release-machine-setup.md"
                    )
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise _reject()
            files.append(candidate)
            relative_paths.append(candidate.relative_to(bundle_directory))
            package_size += metadata.st_size
            if len(files) > MAX_CANDIDATE_FILES or package_size > MAX_CANDIDATE_BYTES:
                raise _reject()
            if _contains_marker(candidate, markers):
                raise _reject()
            architectures = _mach_o_architectures(candidate)
            if architectures is not None:
                if expected not in architectures:
                    raise _reject()
                mach_o_files.append(candidate)

        entrypoint = bundle_directory / ENTRYPOINT_NAME
        if (
            not entrypoint.is_file()
            or not os.access(entrypoint, os.X_OK)
            or entrypoint not in mach_o_files
        ):
            raise _reject()
        rendered_paths = tuple(path.as_posix() for path in relative_paths)
        if (
            not any(path.endswith("/base_library.zip") for path in rendered_paths)
            or not any("/playwright/" in f"/{path}/" for path in rendered_paths)
            or not any(path.endswith("/playwright/driver/node") for path in rendered_paths)
        ):
            raise _reject()
        _verify_code_signatures(tuple(mach_o_files))
        return MacOSExecutorCandidateAudit(
            architecture=expected,
            file_count=len(files),
            mach_o_file_count=len(mach_o_files),
            package_size=package_size,
        )
    except MacOSExecutorCandidateRejected:
        raise
    except (  # pragma: no cover - defensive OS race normalization
        OSError,
        OverflowError,
        subprocess.SubprocessError,
        ValueError,
    ):
        raise _reject() from None


def _run_pyinstaller(  # pragma: no cover - exercised by the real macOS package runner
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


def build_macos_executor_candidate(
    *,
    backend_root: Path,
    output_directory: Path,
    python_executable: Path = Path(sys.executable),
) -> MacOSExecutorCandidateAudit:
    """Build into isolated scratch space, audit twice, and preserve only the raw candidate."""

    try:
        if platform.system() != "Darwin" or output_directory.exists():
            raise _reject()
        architecture = _normalize_architecture(platform.machine())
        resolved_backend = backend_root.resolve(strict=True)
        if not (resolved_backend / "automation-tool-executor.spec").is_file():
            raise _reject()
        output_directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-p901-build-", dir=output_directory.parent
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
            _apply_adhoc_code_signatures(staged_bundle)
            audit_macos_executor_candidate(
                bundle_directory=staged_bundle,
                expected_architecture=architecture,
                forbidden_development_roots=(resolved_backend.parent, temporary_root),
            )
            shutil.copytree(staged_bundle, output_directory, symlinks=False)
            try:
                return audit_macos_executor_candidate(
                    bundle_directory=output_directory,
                    expected_architecture=architecture,
                    forbidden_development_roots=(resolved_backend.parent, temporary_root),
                )
            except BaseException:
                shutil.rmtree(output_directory)
                raise
    except MacOSExecutorCandidateRejected:
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
    parser = argparse.ArgumentParser(description="Build a signing-ready macOS Executor onedir")
    parser.add_argument("--backend-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", required=True, type=Path, dest="output_directory")
    return parser


def main() -> int:  # pragma: no cover - real CLI acceptance
    arguments = _parser().parse_args()
    try:
        result = build_macos_executor_candidate(
            backend_root=arguments.backend_root,
            output_directory=arguments.output_directory,
        )
    except MacOSExecutorCandidateRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        "macOS Executor candidate created: "
        f"{result.file_count} files, {result.mach_o_file_count} Mach-O files"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module CLI smoke uses a subprocess
    raise SystemExit(main())


__all__ = [
    "MacOSExecutorCandidateAudit",
    "MacOSExecutorCandidateRejected",
    "audit_macos_executor_candidate",
    "build_macos_executor_candidate",
    "main",
]
