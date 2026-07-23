#!/usr/bin/env python3
"""EB-07 acceptance: the authority feeds the executor protocol with real paths.

Deterministic cargo tests first, then the real chain: the digest-locked
archive is staged and promoted by the production builders, the production
Rust `EmbeddedBrowserAuthority` resolves the executable with full
verification, and the production Python `BrowserLaunchRequest` — the exact
model the Local Executor consumes — accepts that resolved path. No system
browser discovery is consulted anywhere on this path.
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
    print(f"EB-07 acceptance failed: {message}")
    raise SystemExit(1)


def require_evidence() -> None:
    text = (ROOT / "docs/development/EB-07.md").read_text(encoding="utf-8")
    for marker in ("# EB-07 完成证据", "## RED", "## GREEN", "## 失败矩阵", "## 清理"):
        if marker not in text:
            fail(f"EB-07 evidence is missing {marker}")


def main() -> int:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        fail("EB-07 acceptance must run on macOS arm64")
    if not DEFAULT_ARCHIVE.is_file():
        fail(f"locked archive not downloaded yet: {DEFAULT_ARCHIVE}")

    deterministic = subprocess.run(
        ["cargo", "test", *MANIFEST_ARGS, "--test", "embedded_browser_authority", "--locked"],
        cwd=ROOT,
        check=False,
    )
    if deterministic.returncode != 0:
        fail("deterministic authority tests failed")

    contract = load_staging_contract(STAGING_CONTRACT)
    digest = sha256_file(DEFAULT_ARCHIVE)
    with tempfile.TemporaryDirectory(prefix="eb07-resources-") as directory:
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
        environment["EB07_REAL_RESOURCE_DIR"] = str(resources)
        real = subprocess.run(
            [
                "cargo",
                "test",
                *MANIFEST_ARGS,
                "--test",
                "embedded_browser_authority",
                "--locked",
                "--",
                "--ignored",
                "real_distribution_resolves_through_the_authority",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        if real.returncode != 0:
            fail("real distribution failed to resolve through the authority")

        executable = staging / Path(*contract.targets["macos-arm64"].executable.split("/"))
        probe_source = "\n".join(
            [
                "import os, tempfile",
                "from pathlib import Path",
                "from automation_tool.executor.browser_runtime import BrowserLaunchRequest",
                "with tempfile.TemporaryDirectory(prefix='eb07-profile-') as profile:",
                "    # 生产链路由 Rust Authority 下发 canonical 路径；探针等价使用 resolve()",
                "    request = BrowserLaunchRequest(",
                "        executable_path=Path(os.environ['EB07_EXECUTABLE']),",
                "        profile_directory=Path(profile).resolve(),",
                "    )",
                "    request.revalidate()",
                "    assert 'redacted' in repr(request)",
                "    print('EB07_LAUNCH_REQUEST_OK')",
            ]
        )
        probe_environment = dict(os.environ)
        probe_environment["EB07_EXECUTABLE"] = str(executable.resolve())
        probe = subprocess.run(
            ["uv", "run", "--project", "backend", "--locked", "python", "-c", probe_source],
            cwd=ROOT,
            env=probe_environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if probe.returncode != 0 or "EB07_LAUNCH_REQUEST_OK" not in probe.stdout:
            fail(f"executor BrowserLaunchRequest rejected the resolved path: {probe.stderr[-300:]}")

    require_evidence()
    print("EB-07 acceptance passed: authority resolve + executor launch request accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
