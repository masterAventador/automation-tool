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

import argparse
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
DEFAULT_MACOS_ARM64_ARCHIVE = (
    ROOT.parent.parent / ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
)
DEFAULT_ARCHIVES = {
    "macos-arm64": DEFAULT_MACOS_ARM64_ARCHIVE,
    "macos-x86_64": ROOT / ".local/eb-mac-x64/chrome-mac-x64.zip",
    "windows-x86_64": ROOT / ".local/eb-04-windows/chrome-win64.zip",
}
MANIFEST_ARGS = ["--manifest-path", "frontend/src-tauri/Cargo.toml"]


def fail(message: str) -> None:
    print(f"EB-06 acceptance failed: {message}")
    raise SystemExit(1)


def require_evidence() -> None:
    text = (ROOT / "docs/development/EB-06.md").read_text(encoding="utf-8")
    for marker in ("# EB-06 完成证据", "## RED", "## GREEN", "## 失败矩阵", "## 清理"):
        if marker not in text:
            fail(f"EB-06 evidence is missing {marker}")


def current_target_id() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "Darwin" and machine in {"x86_64", "amd64"}:
        return "macos-x86_64"
    if system == "Windows" and machine in {"x86_64", "amd64"}:
        return "windows-x86_64"
    fail(f"unsupported EB-06 host: {system}/{machine}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    return parser.parse_args()


def main() -> int:
    target_id = current_target_id()
    archive = parse_args().archive or DEFAULT_ARCHIVES[target_id]
    test_target = (
        "embedded_browser_distribution_windows"
        if target_id == "windows-x86_64"
        else "embedded_browser_distribution"
    )
    if not archive.is_file():
        fail(f"locked archive not downloaded yet: {archive}")

    deterministic = subprocess.run(
        ["cargo", "test", *MANIFEST_ARGS, "--test", test_target, "--locked"],
        cwd=ROOT,
        check=False,
    )
    if deterministic.returncode != 0:
        fail("deterministic Rust distribution tests failed")

    contract = load_staging_contract(STAGING_CONTRACT)
    digest = sha256_file(archive)
    with tempfile.TemporaryDirectory(prefix="eb06-resources-") as directory:
        resources = Path(directory) / "resources"
        resources.mkdir()
        staging = resources / "embedded-browser"
        build_staging(
            contract=contract,
            target_id=target_id,
            archive_path=archive,
            archive_sha256=digest,
            output=staging,
        )
        build_distribution_manifest(staging=staging, target_id=target_id)
        environment = dict(os.environ)
        environment["EB06_REAL_RESOURCE_DIR"] = str(resources)
        real = subprocess.run(
            [
                "cargo",
                "test",
                *MANIFEST_ARGS,
                "--test",
                test_target,
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
    print(
        f"EB-06 acceptance passed: Rust resolver verified the real staged {target_id} distribution"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
