#!/usr/bin/env python3
"""EB-06 acceptance: Rust resolver loads the real staged distribution.

Deterministic cargo tests first (synthetic trees, all rejection paths), then
the real chain: the digest-locked archive is staged and promoted by the
production Python builders into `<resources>/embedded-browser`, and the
production Rust `EmbeddedBrowserDistribution::load_for_target` must verify
all 331 files (~359MB digests) and resolve the executable. Ledger and
evidence checks close the run.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_embedded_browser_distribution import (  # noqa: E402
    build_distribution_manifest,
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
MANIFEST_ARGS = ["--manifest-path", "frontend/src-tauri/Cargo.toml"]


def fail(message: str) -> None:
    print(f"EB-06 acceptance failed: {message}")
    raise SystemExit(1)


def require_evidence() -> None:
    text = (ROOT / "docs/development/EB-06.md").read_text(encoding="utf-8")
    for marker in ("# EB-06 完成证据", "## RED", "## GREEN", "## 失败矩阵", "## 清理"):
        if marker not in text:
            fail(f"EB-06 evidence is missing {marker}")


def main() -> int:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        fail("EB-06 acceptance must run on macOS arm64")
    if not DEFAULT_ARCHIVE.is_file():
        fail(f"locked archive not downloaded yet: {DEFAULT_ARCHIVE}")

    deterministic = subprocess.run(
        ["cargo", "test", *MANIFEST_ARGS, "--test", "embedded_browser_distribution", "--locked"],
        cwd=ROOT,
        check=False,
    )
    if deterministic.returncode != 0:
        fail("deterministic Rust distribution tests failed")

    contract = load_staging_contract(STAGING_CONTRACT)
    digest = sha256_file(DEFAULT_ARCHIVE)
    with tempfile.TemporaryDirectory(prefix="eb06-resources-") as directory:
        resources = Path(directory) / "resources"
        resources.mkdir()
        staging = resources / "embedded-browser"
        build_staging(
            contract=contract,
            target_id="macos-arm64",
            archive_path=DEFAULT_ARCHIVE,
            archive_sha256=digest,
            output=staging,
        )
        build_distribution_manifest(staging=staging, target_id="macos-arm64")
        environment = dict(os.environ)
        environment["EB06_REAL_RESOURCE_DIR"] = str(resources)
        real = subprocess.run(
            [
                "cargo",
                "test",
                *MANIFEST_ARGS,
                "--test",
                "embedded_browser_distribution",
                "--locked",
                "--",
                "--ignored",
                "real_staged_distribution_loads_end_to_end",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        if real.returncode != 0:
            fail("real staged distribution failed to load through the Rust resolver")

    require_evidence()
    print("EB-06 acceptance passed: Rust resolver verified the real staged distribution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
