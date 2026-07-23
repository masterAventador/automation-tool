#!/usr/bin/env python3
"""Verify a locked macOS Chromium archive and its exact Mach-O architecture."""

from __future__ import annotations

import argparse
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_chromium_staging import (  # noqa: E402
    load_staging_contract,
    sha256_file,
)

CONTRACT_PATH = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
EXPECTED_CPU_TYPES = {
    "macos-arm64": 0x0100000C,
    "macos-x86_64": 0x01000007,
}
MACHO_64_LITTLE_ENDIAN = b"\xcf\xfa\xed\xfe"
MAX_ARCHIVE_ENTRIES = 20_000


class ArchiveVerificationError(ValueError):
    """The archive does not match its locked macOS target."""


def verify_macho_header(header: bytes, target_id: str) -> None:
    if len(header) < 12 or header[:4] != MACHO_64_LITTLE_ENDIAN:
        raise ArchiveVerificationError("browser executable is not a thin 64-bit Mach-O")
    cpu_type = struct.unpack_from("<I", header, 4)[0]
    if cpu_type != EXPECTED_CPU_TYPES[target_id]:
        raise ArchiveVerificationError(
            f"browser Mach-O architecture mismatch: {cpu_type:#x}"
        )


def verify_archive(archive_path: Path, target_id: str) -> tuple[int, int]:
    contract = load_staging_contract(CONTRACT_PATH)
    target = contract.targets.get(target_id)
    if target is None or target_id not in EXPECTED_CPU_TYPES or not target.buildable:
        raise ArchiveVerificationError("unsupported or non-buildable macOS target")
    if sha256_file(archive_path) != target.archive_sha256:
        raise ArchiveVerificationError("archive SHA-256 differs from the contract")

    seen: set[str] = set()
    total_size = 0
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ArchiveVerificationError("archive entry count out of bounds")
        executable = None
        for entry in entries:
            path = PurePosixPath(entry.filename.rstrip("/"))
            if (
                not path.parts
                or path.is_absolute()
                or ".." in path.parts
                or path.parts[0] != target.root_entry
                or entry.filename in seen
            ):
                raise ArchiveVerificationError("archive path or root is invalid")
            seen.add(entry.filename)
            lowered = entry.filename.lower()
            if any(name in lowered for name in target.forbidden_entry_substrings):
                raise ArchiveVerificationError("archive contains a second browser")
            total_size += entry.file_size
            if entry.filename == target.executable:
                executable = entry
        if executable is None or executable.is_dir():
            raise ArchiveVerificationError("locked executable is absent")
        with archive.open(executable) as handle:
            verify_macho_header(handle.read(32), target_id)
    return len(entries), total_size


def self_test() -> None:
    arm = MACHO_64_LITTLE_ENDIAN + struct.pack(
        "<II", EXPECTED_CPU_TYPES["macos-arm64"], 0
    )
    intel = MACHO_64_LITTLE_ENDIAN + struct.pack(
        "<II", EXPECTED_CPU_TYPES["macos-x86_64"], 0
    )
    verify_macho_header(arm, "macos-arm64")
    verify_macho_header(intel, "macos-x86_64")
    for header, target in (
        (arm, "macos-x86_64"),
        (intel, "macos-arm64"),
        (b"not-a-mach-o", "macos-x86_64"),
    ):
        try:
            verify_macho_header(header, target)
        except ArchiveVerificationError:
            continue
        raise ArchiveVerificationError("self-test accepted a wrong architecture")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--target", choices=tuple(EXPECTED_CPU_TYPES))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if not args.self_test and (args.archive is None or args.target is None):
        parser.error("--archive and --target are required unless --self-test is used")
    return args


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        print("macOS Chromium architecture verifier self-test passed")
        return
    entries, total_size = verify_archive(args.archive, args.target)
    print(
        f"macOS Chromium archive verified: {args.target}, "
        f"{entries} entries, {total_size} uncompressed bytes"
    )


if __name__ == "__main__":
    try:
        main()
    except (ArchiveVerificationError, OSError, zipfile.BadZipFile) as error:
        raise SystemExit(
            f"macOS Chromium archive verification failed: {error}"
        ) from error
