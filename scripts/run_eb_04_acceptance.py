#!/usr/bin/env python3
"""EB-04 acceptance: reproducible Windows embedded-Chromium staging and launch."""

from __future__ import annotations

import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_chromium_staging import (  # noqa: E402
    MANIFEST_NAME,
    build_staging,
    load_staging_contract,
    sha256_file,
)

CONTRACT_PATH = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
DEFAULT_ARCHIVE = ROOT / ".local/eb-04-windows/chrome-win64.zip"
TARGET_ID = "windows-x86_64"
_PE_X86_64_MACHINE = 0x8664


def fail(message: str) -> None:
    raise RuntimeError(f"EB-04 acceptance failed: {message}")


def require_windows_x86_64() -> None:
    if sys.platform != "win32" or platform.machine().casefold() not in {
        "amd64",
        "x86_64",
    }:
        fail("acceptance requires Windows x86_64")


def run_deterministic_tests() -> None:
    subprocess.run(
        [sys.executable, "scripts/test_embedded_chromium_staging.py"],
        cwd=ROOT,
        check=True,
        timeout=300,
    )


def require_x86_64_pe(executable: Path) -> None:
    with executable.open("rb") as source:
        if source.read(2) != b"MZ":
            fail("staged executable is not PE")
        source.seek(0x3C)
        offset_bytes = source.read(4)
        if len(offset_bytes) != 4:
            fail("staged PE header is truncated")
        pe_offset = struct.unpack("<I", offset_bytes)[0]
        source.seek(pe_offset)
        if source.read(4) != b"PE\0\0":
            fail("staged executable has an invalid PE signature")
        machine_bytes = source.read(2)
        if len(machine_bytes) != 2:
            fail("staged PE machine field is truncated")
        machine = struct.unpack("<H", machine_bytes)[0]
    if machine != _PE_X86_64_MACHINE:
        fail(f"staged PE architecture is not x86_64: 0x{machine:04x}")


def normalized_windows_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def processes_for_executable(executable: Path) -> list[psutil.Process]:
    expected = normalized_windows_path(executable)
    matches: list[psutil.Process] = []
    for process in psutil.process_iter(["exe"]):
        try:
            actual = process.info["exe"]
            if actual is not None and normalized_windows_path(Path(actual)) == expected:
                matches.append(process)
        except (OSError, psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return matches


def terminate_owned_processes(executable: Path) -> None:
    matches = processes_for_executable(executable)
    for process in matches:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(matches, timeout=5)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
    _, still_alive = psutil.wait_procs(alive, timeout=5)
    if still_alive:
        fail("owned staged Chromium process tree did not exit")


def require_no_owned_processes(executable: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not processes_for_executable(executable):
            return
        time.sleep(0.1)
    fail("staged Chromium left a process running")


def launch_probe(staged_root: Path, executable: str, expected_version: str) -> None:
    browser_path = staged_root / Path(executable)
    if processes_for_executable(browser_path):
        fail("fresh staging executable is already running")
    probe = f"""
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path={json.dumps(str(browser_path))},
        headless=True,
        args=[
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
        ],
    )
    try:
        context = browser.new_context(offline=True)
        page = context.new_page()
        page.goto("about:blank")
        payload = {{
            "version": browser.version,
            "userAgent": page.evaluate("() => navigator.userAgent"),
        }}
        context.close()
    finally:
        browser.close()
print(json.dumps(payload))
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if completed.returncode != 0:
            fail(f"offline staged launch failed: {completed.stderr.strip()[-400:]}")
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        if payload.get("version") != expected_version:
            fail("offline staged browser version is unexpected")
        if "HeadlessChrome" not in str(payload.get("userAgent")):
            fail("offline probe did not use the staged headless browser")
        require_no_owned_processes(browser_path)
    finally:
        terminate_owned_processes(browser_path)


def require_evidence() -> None:
    evidence = (ROOT / "docs/development/EB-04.md").read_text(encoding="utf-8")
    for marker in (
        "# EB-04 完成证据",
        "> 状态：✅ 已完成",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 清理",
        "## 遗留项",
    ):
        if marker not in evidence:
            fail(f"evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| EB-04 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        fail("specialized roadmap does not mark EB-04 completed")


def archive_argument() -> Path:
    if "--archive" not in sys.argv:
        return DEFAULT_ARCHIVE
    index = sys.argv.index("--archive")
    if index + 1 >= len(sys.argv):
        fail("--archive requires a path")
    return Path(sys.argv[index + 1])


def main() -> int:
    require_windows_x86_64()
    archive = archive_argument().resolve()
    if not archive.is_file():
        fail("locked Windows archive is missing")
    run_deterministic_tests()

    contract = load_staging_contract(CONTRACT_PATH)
    target = contract.targets[TARGET_ID]
    digest = sha256_file(archive)
    if not target.buildable or target.archive_sha256 != digest:
        fail("Windows archive does not match the buildable contract lock")

    with tempfile.TemporaryDirectory(prefix="eb04-staging-") as temporary:
        base = Path(temporary)
        first = build_staging(
            contract=contract,
            target_id=TARGET_ID,
            archive_path=archive,
            archive_sha256=digest,
            output=base / "staging-a",
        )
        build_staging(
            contract=contract,
            target_id=TARGET_ID,
            archive_path=archive,
            archive_sha256=digest,
            output=base / "staging-b",
        )
        manifest_a = (base / "staging-a" / MANIFEST_NAME).read_bytes()
        manifest_b = (base / "staging-b" / MANIFEST_NAME).read_bytes()
        if manifest_a != manifest_b:
            fail("two Windows stagings produced different manifests")
        if first.file_count < 100 or first.total_bytes < 100 * 1024 * 1024:
            fail("staged Windows tree is implausibly small")
        manifest = json.loads(manifest_a)
        if (
            manifest.get("target") != TARGET_ID
            or manifest.get("executable") != target.executable
            or manifest.get("source", {}).get("archive_sha256") != digest
        ):
            fail("Windows staging manifest identity is inconsistent")
        executable = base / "staging-a" / Path(target.executable)
        require_x86_64_pe(executable)
        shutil.rmtree(base / "staging-b")
        launch_probe(base / "staging-a", target.executable, contract.browser_version)

    require_evidence()
    print(
        f"EB-04 Windows staging acceptance passed: {first.file_count} files, "
        f"{first.total_bytes} bytes, manifest reproducible"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
