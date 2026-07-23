#!/usr/bin/env python3
"""EB-05 acceptance: real single-distribution manifest over the staged browser.

Deterministic tests first, then the digest-locked archive is staged with the
production EB-03 builder, promoted into the distribution manifest with the
archive lock enforced, and fully re-verified file by file. A deliberate
tamper afterwards must be rejected. Ledger and evidence checks close the run.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_browser_distribution import (  # noqa: E402
    DistributionRejected,
    build_distribution_manifest,
    verify_distribution,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)

STAGING_CONTRACT = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
DEFAULT_ARCHIVE = (
    ROOT.parent.parent
    / ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
)


def fail(message: str) -> None:
    print(f"EB-05 acceptance failed: {message}")
    raise SystemExit(1)


def require_evidence() -> None:
    text = (ROOT / "docs/development/EB-05.md").read_text(encoding="utf-8")
    for marker in ("# EB-05 完成证据", "## RED", "## GREEN", "## 失败矩阵", "## 清理"):
        if marker not in text:
            fail(f"EB-05 evidence is missing {marker}")


def main() -> int:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        fail("EB-05 acceptance must run on macOS arm64")
    if not DEFAULT_ARCHIVE.is_file():
        fail(f"locked archive not downloaded yet: {DEFAULT_ARCHIVE}")

    deterministic = subprocess.run(
        [sys.executable, "scripts/test_embedded_browser_distribution.py"],
        cwd=ROOT,
        check=False,
    )
    if deterministic.returncode != 0:
        fail("deterministic distribution tests failed")

    contract = load_staging_contract(STAGING_CONTRACT)
    digest = sha256_file(DEFAULT_ARCHIVE)
    with tempfile.TemporaryDirectory(prefix="eb05-staging-") as directory:
        staging = Path(directory) / "staging"
        build_staging(
            contract=contract,
            target_id="macos-arm64",
            archive_path=DEFAULT_ARCHIVE,
            archive_sha256=digest,
            output=staging,
        )
        manifest_path = build_distribution_manifest(
            staging=staging, target_id="macos-arm64"
        )
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        if document["runtime"]["browser_use"] != "0.13.6":
            fail("manifest runtime aggregation drifted")
        report = verify_distribution(staging=staging, target_id="macos-arm64")
        if report.verified_files < 100 or report.total_bytes < 100 * 1024 * 1024:
            fail("verified tree is implausibly small for a full Chromium")

        tampered = (
            staging
            / "chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist"
        )
        original = tampered.read_bytes()
        tampered.write_bytes(original + b"<!-- tampered -->")
        try:
            verify_distribution(staging=staging, target_id="macos-arm64")
        except DistributionRejected:
            pass
        else:
            fail("tampered staging was not rejected")
        tampered.write_bytes(original)

    require_evidence()
    print(
        f"EB-05 distribution acceptance passed: {report.verified_files} files, "
        f"{report.total_bytes} bytes verified, tamper rejected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
