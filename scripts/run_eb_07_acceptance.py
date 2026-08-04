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
from embedded_browser_archives import default_archives  # noqa: E402

STAGING_CONTRACT = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
DEFAULT_ARCHIVES = default_archives(ROOT)
MANIFEST_ARGS = ["--manifest-path", "frontend/src-tauri/Cargo.toml"]


def fail(message: str) -> None:
    print(f"EB-07 acceptance failed: {message}")
    raise SystemExit(1)


def require_evidence() -> None:
    text = (ROOT / "docs/development/EB-07.md").read_text(encoding="utf-8")
    for marker in ("# EB-07 完成证据", "## RED", "## GREEN", "## 失败矩阵", "## 清理"):
        if marker not in text:
            fail(f"EB-07 evidence is missing {marker}")


def current_target_id() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "Windows" and machine in {"x86_64", "amd64"}:
        return "windows-x86_64"
    fail(f"unsupported EB-07 host: {system}/{machine}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    return parser.parse_args()


def main() -> int:
    target_id = current_target_id()
    archive = parse_args().archive or DEFAULT_ARCHIVES[target_id]
    test_target = (
        "embedded_browser_authority_windows"
        if target_id == "windows-x86_64"
        else "embedded_browser_authority"
    )
    if not archive.is_file():
        fail(f"locked archive not downloaded yet: {archive}")

    deterministic = subprocess.run(
        ["cargo", "test", *MANIFEST_ARGS, "--test", test_target, "--locked"],
        cwd=ROOT,
        check=False,
    )
    if deterministic.returncode != 0:
        fail("deterministic authority tests failed")

    contract = load_staging_contract(STAGING_CONTRACT)
    digest = sha256_file(archive)
    with tempfile.TemporaryDirectory(prefix="eb07-resources-") as directory:
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
        environment["EB07_REAL_RESOURCE_DIR"] = str(resources)
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
                "real_distribution_resolves_through_the_authority",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        if real.returncode != 0:
            fail("real distribution failed to resolve through the authority")

        executable = staging / Path(*contract.targets[target_id].executable.split("/"))
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
    print(
        f"EB-07 acceptance passed: {target_id} authority resolve + executor launch request accepted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
