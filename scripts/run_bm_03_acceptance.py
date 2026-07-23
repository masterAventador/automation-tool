#!/usr/bin/env python3
"""BM-03 shared-Chromium render adaptation acceptance.

Stages the real, digest-locked Chrome for Testing build with the EB-03
builder, then drives the production chain (Rust orchestrator -> Node worker
-> headless embedded Chromium) and the worker-level failure matrix. Only the
staged binary is ever launched; discovery and cache fallbacks stay poisoned.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from build_embedded_chromium_staging import build_staging, load_staging_contract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
DEFAULT_ARCHIVE = ROOT / ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
TARGET_ID = "macos-arm64"
RENDER_JOB_PREFIX = "automation-tool-renderjob-"


def require_platform() -> None:
    if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
        raise AssertionError("BM-03 acceptance currently runs on macOS arm64 only")


def node_executable() -> str:
    value = shutil.which("node")
    if value is None:
        raise AssertionError("development Node is unavailable")
    return value


def run_worker_gates() -> None:
    for script in (
        "scripts/test_motion_video_render_adapter.py",
        "scripts/test_motion_video_worker.py",
    ):
        subprocess.run([sys.executable, script], cwd=ROOT, check=True, timeout=300)


def stage_real_chromium(archive: Path, run_root: Path) -> tuple[Path, int]:
    contract = load_staging_contract(CONTRACT)
    target = contract.targets[TARGET_ID]
    result = build_staging(
        contract=contract,
        target_id=TARGET_ID,
        archive_path=archive,
        archive_sha256=target.archive_sha256,
        output=run_root / "staging",
    )
    executable = result.output / Path(*target.executable.split("/"))
    executable = executable.resolve(strict=True)
    major = int(contract.browser_version.split(".")[0])
    return executable, major


def run_production_chain(executable: Path, major: int) -> None:
    environment = os.environ.copy()
    environment["BM03_RENDER_BROWSER"] = str(executable)
    environment["BM03_CHROMIUM_MAJOR"] = str(major)
    environment["BM03_NODE"] = node_executable()
    subprocess.run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "local_video_orchestrator",
            "real_worker_render_verify_launches_the_locked_chromium",
            "--",
            "--exact",
            "--nocapture",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        timeout=600,
    )


def require_real_major_rejection(executable: Path, major: int) -> None:
    """The real binary must be refused when Rust declares a different major."""
    import base64
    import hashlib
    import hmac

    token = "c" * 64
    job_id = "b3b52c7c-0417-4f2f-8f5e-6f8e13d0a4b2"
    wrong_major = major + 1

    def command_proof(command: str) -> str:
        message = b"automation-tool.video-worker-command.v1\0" + b"\0".join(
            value.encode() for value in [command, "node", "1.0", job_id]
        )
        digest = hmac.digest(bytes.fromhex(token), message, hashlib.sha256)
        return "atvwc1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    with tempfile.TemporaryDirectory(prefix="automation-tool-bm03-assets-") as assets:
        bootstrap = {
            "assetRoot": str(Path(assets).resolve(strict=True)),
            "bootstrapVersion": "1",
            "enableWebUi": False,
            "localSessionToken": token,
            "protocolVersion": "1.0",
            "renderBrowser": {
                "chromiumMajor": wrong_major,
                "executablePath": str(executable),
                "launchTimeoutSeconds": 30,
            },
            "scriptModel": None,
            "workerKind": "node",
        }
        command = {
            "authenticationProof": command_proof("worker.render.verify"),
            "command": "worker.render.verify",
            "jobId": job_id,
            "protocolVersion": "1.0",
            "workerKind": "node",
        }
        process = subprocess.run(
            [node_executable(), str(ROOT / "workers/motion_composition/worker.mjs")],
            input=json.dumps(bootstrap) + "\n" + json.dumps(command) + "\n",
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={"PATH": ""},
        )
    if process.returncode != 0:
        raise AssertionError("BM-03 major-mismatch worker run must exit cleanly")
    events = [json.loads(line) for line in process.stdout.splitlines()]
    if len(events) != 2 or events[1].get("event") != "worker.render.failed" or (
        events[1].get("reasonCode") != "chromium_major_mismatch"
    ):
        raise AssertionError("BM-03 real binary must be rejected on a declared major mismatch")


def require_no_residue(run_root: Path) -> None:
    leftovers = [
        path
        for path in Path(tempfile.gettempdir()).iterdir()
        if path.name.startswith(RENDER_JOB_PREFIX)
    ]
    if leftovers:
        raise AssertionError(f"render job directories leaked: {leftovers}")
    survivors = subprocess.run(
        ["pgrep", "-f", str(run_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if survivors.stdout.strip():
        raise AssertionError("staged Chromium processes survived the acceptance")


def require_evidence() -> None:
    text = (ROOT / "docs/development/BM-03.md").read_text(encoding="utf-8")
    for heading in (
        "# BM-03 完成证据", "状态：🔍 待验收", "## RED", "## GREEN", "## 失败矩阵",
        "## 正常用户路径验收", "## 真实边界", "## 清理", "## 文档变化", "## 遗留项",
    ):
        if heading not in text:
            raise AssertionError(f"BM-03 evidence is missing {heading}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| BM-03 |")]
    if len(rows) != 1 or not rows[0].endswith("| 🔍 待验收 |"):
        raise AssertionError("BM-03 roadmap status is not pending native validation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--skip-evidence", action="store_true")
    arguments = parser.parse_args()
    require_platform()
    archive = arguments.archive.resolve(strict=True)
    run_root = (
        ROOT / ".local/embedded-browser-video-studio" / f"ebvs-bm03-{os.getpid()}"
    )
    run_root.mkdir(parents=True, exist_ok=False)
    try:
        run_worker_gates()
        executable, major = stage_real_chromium(archive, run_root)
        run_production_chain(executable, major)
        require_real_major_rejection(executable, major)
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
    require_no_residue(run_root)
    if not arguments.skip_evidence:
        require_evidence()
    print(f"BM-03 {platform.system()} shared-Chromium render adaptation acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
