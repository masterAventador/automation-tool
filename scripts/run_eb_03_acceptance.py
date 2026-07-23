#!/usr/bin/env python3
"""EB-03 acceptance: reproducible macOS embedded-Chromium staging, real launch.

Steps: deterministic builder tests, digest-locked archive verification against
the staging contract, two independent stagings whose manifests must be
byte-identical, a real offline launch of the staged binary through the locked
Playwright (executable_path, headless, fresh temp profile, offline context)
asserting the exact locked browser version, full process/profile cleanup, and
the ledger/evidence checks. No network is used at staging or launch time; the
archive download happens once beforehand into the local cache.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_chromium_staging import (  # noqa: E402
    MANIFEST_NAME,
    build_staging,
    load_staging_contract,
    sha256_file,
)

CONTRACT_PATH = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
DEFAULT_ARCHIVE = (
    ROOT.parent.parent
    / ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
)


def fail(message: str) -> None:
    print(f"EB-03 acceptance failed: {message}")
    raise SystemExit(1)


def run_deterministic_tests() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/test_embedded_chromium_staging.py"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        fail("deterministic staging tests failed")


def launch_probe(staged_root: Path, executable: str, expected_version: str) -> None:
    """Launch the staged browser offline via the locked Playwright runtime."""
    probe = f"""
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

executable = Path({json.dumps(str(staged_root))}) / {json.dumps(executable)}
with sync_playwright() as playwright:
    with __import__("tempfile").TemporaryDirectory(prefix="eb03-profile-") as profile:
        context = playwright.chromium.launch_persistent_context(
            profile,
            executable_path=str(executable),
            headless=True,
            offline=True,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        try:
            version = context.browser.version
            page = context.new_page()
            page.goto("about:blank")
            title = page.evaluate("() => navigator.userAgent")
        finally:
            context.close()
print(json.dumps({{"version": version, "userAgent": title}}))
"""
    result = subprocess.run(
        ["uv", "run", "--project", "backend", "--locked", "python", "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if result.returncode != 0:
        fail(f"staged browser launch failed: {result.stderr.strip()[-400:]}")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if payload["version"] != expected_version:
        fail(f"staged browser version {payload['version']} != {expected_version}")
    if "HeadlessChrome" not in payload["userAgent"]:
        fail("probe did not run the expected headless browser")
    print(f"EB-03 staged browser launched offline: {payload['version']}")


def require_evidence() -> None:
    text = (ROOT / "docs/development/EB-03.md").read_text(encoding="utf-8")
    for marker in (
        "# EB-03 完成证据",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 清理",
    ):
        if marker not in text:
            fail(f"EB-03 evidence is missing {marker}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| EB-03 |")]
    if len(rows) != 1 or not rows[0].rstrip().endswith(
        ("🧪 RED |", "🚧 实现中 |", "🔍 待验收 |", "✅ 已完成 |")
    ):
        fail("EB-03 roadmap row is missing or in an unexpected state")


def main() -> int:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        fail("EB-03 acceptance must run on macOS arm64")
    archive = (
        Path(sys.argv[sys.argv.index("--archive") + 1])
        if "--archive" in sys.argv
        else DEFAULT_ARCHIVE
    )
    if not archive.is_file():
        fail(f"locked archive not downloaded yet: {archive}")

    run_deterministic_tests()

    contract = load_staging_contract(CONTRACT_PATH)
    target = contract.targets["macos-arm64"]
    digest = sha256_file(archive)
    if target.archive_sha256 != digest:
        fail("archive digest does not match the contract lock")

    with tempfile.TemporaryDirectory(prefix="eb03-staging-") as directory:
        base = Path(directory)
        first = build_staging(
            contract=contract,
            target_id="macos-arm64",
            archive_path=archive,
            archive_sha256=digest,
            output=base / "staging-a",
        )
        build_staging(
            contract=contract,
            target_id="macos-arm64",
            archive_path=archive,
            archive_sha256=digest,
            output=base / "staging-b",
        )
        manifest_a = (base / "staging-a" / MANIFEST_NAME).read_bytes()
        manifest_b = (base / "staging-b" / MANIFEST_NAME).read_bytes()
        if manifest_a != manifest_b:
            fail("two stagings produced different manifests")
        if first.file_count < 100 or first.total_bytes < 100 * 1024 * 1024:
            fail("staged tree is implausibly small for a full Chromium")
        shutil.rmtree(base / "staging-b")
        launch_probe(base / "staging-a", target.executable, contract.browser_version)

    require_evidence()
    print(
        f"EB-03 macOS staging acceptance passed: {first.file_count} files, "
        f"{first.total_bytes} bytes, manifest reproducible"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
