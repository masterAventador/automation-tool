#!/usr/bin/env python3
"""BU-02 acceptance: real dual-mode Browser Use control of the one Chromium.

Runs on macOS arm64. The EB-03 digest-locked archive is staged with the
production staging builder, then the production harness drives the staged
binary in both closed modes against a local fixture page:

1. isolated validation — `executable_path` + fresh temp profile;
2. operations takeover — the same binary started externally with a random
   loopback CDP port, taken over via `cdp_url`.

Both probes run inside the locked browser-use-contract environment with the
harness environment (cloud sync and telemetry disabled). Success requires the
exact locked browser version over CDP, the fixture title in both modes,
distinct profiles, and full process cleanup. Deterministic harness tests and
ledger checks run first; no system browser is ever discovered.
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

PROBE = r'''
import asyncio
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, os.environ["BU02_HARNESS_DIR"])
from browser_use_harness import (
    IsolatedLaunchPlan,
    TakeoverLaunchPlan,
    create_session,
)

EXECUTABLE = Path(os.environ["BU02_EXECUTABLE"])
EXPECTED_VERSION = os.environ["BU02_EXPECTED_VERSION"]

PAGE = b"<!doctype html><title>bu02-fixture</title><h1>BU-02</h1>"


async def settled_title(session, expected: str) -> str:
    for _ in range(100):
        title = await session.get_current_page_title()
        if title == expected:
            return title
        await asyncio.sleep(0.1)
    return await session.get_current_page_title()


HITS = {"count": 0}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        HITS["count"] += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, _format, *args):
        pass


async def isolated_probe(url: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="bu02-isolated-profile-") as profile:
        session = create_session(
            IsolatedLaunchPlan(
                executable_path=EXECUTABLE,
                user_data_dir=Path(profile) / "fresh",
            )
        )
        await session.start()
        try:
            await session.navigate_to(url)
            title = await settled_title(session, "bu02-fixture")
            current = await session.get_current_page_url()
        finally:
            await session.stop()
        return {"title": title, "url": current, "profile_mode": "isolated"}


async def takeover_probe(url: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="bu02-takeover-profile-") as profile:
        process = subprocess.Popen(
            [
                str(EXECUTABLE),
                "--headless=new",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--use-mock-keychain",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            active = Path(profile) / "DevToolsActivePort"
            for _ in range(300):
                if process.poll() is not None:
                    raise RuntimeError(f"cdp chromium exited: {process.returncode}")
                if active.is_file():
                    port = int(active.read_text().splitlines()[0])
                    break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("timed out waiting for random CDP port")
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=10
            ) as response:
                cdp_version = json.load(response)["Browser"]
            session = create_session(
                TakeoverLaunchPlan(cdp_url=f"http://127.0.0.1:{port}")
            )
            await session.start()
            try:
                await session.navigate_to(url)
                title = await settled_title(session, "bu02-fixture")
            finally:
                await session.stop()
            return {"title": title, "cdp_version": cdp_version, "random_port": port > 0}
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


async def main() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        isolated = await isolated_probe(url)
        takeover = await takeover_probe(url)
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert isolated["url"] == url, isolated
    assert HITS["count"] >= 2, HITS
    assert takeover["cdp_version"].endswith(f"/{EXPECTED_VERSION}"), takeover
    assert takeover["random_port"] is True
    print(json.dumps({"isolated": isolated, "takeover": takeover}))


asyncio.run(main())
'''


def fail(message: str) -> None:
    print(f"BU-02 acceptance failed: {message}")
    raise SystemExit(1)


def require_evidence() -> None:
    text = (ROOT / "docs/development/BU-02.md").read_text(encoding="utf-8")
    for marker in ("# BU-02 完成证据", "## RED", "## GREEN", "## 失败矩阵", "## 清理"):
        if marker not in text:
            fail(f"BU-02 evidence is missing {marker}")


def main() -> int:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        fail("BU-02 acceptance must run on macOS arm64")
    if not DEFAULT_ARCHIVE.is_file():
        fail(f"locked archive not downloaded yet: {DEFAULT_ARCHIVE}")

    deterministic = subprocess.run(
        [sys.executable, "scripts/test_browser_use_harness.py"], cwd=ROOT, check=False
    )
    if deterministic.returncode != 0:
        fail("deterministic harness tests failed")

    contract = load_staging_contract(STAGING_CONTRACT)
    target = contract.targets["macos-arm64"]
    digest = sha256_file(DEFAULT_ARCHIVE)
    if digest != target.archive_sha256:
        fail("archive digest does not match the staging contract lock")

    with tempfile.TemporaryDirectory(prefix="bu02-staging-") as directory:
        staging = Path(directory) / "staging"
        build_staging(
            contract=contract,
            target_id="macos-arm64",
            archive_path=DEFAULT_ARCHIVE,
            archive_sha256=digest,
            output=staging,
        )
        executable = staging / Path(*target.executable.split("/"))
        harness_dir = ROOT / "tools/browser-use-contract"
        sys.path.insert(0, str(harness_dir))
        from browser_use_harness import harness_environment

        import os

        probe_environment = harness_environment(dict(os.environ))
        probe_environment.update(
            {
                "BU02_HARNESS_DIR": str(harness_dir),
                "BU02_EXECUTABLE": str(executable),
                "BU02_EXPECTED_VERSION": contract.browser_version,
            }
        )
        result = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(harness_dir),
                "--locked",
                "python",
                "-c",
                PROBE,
            ],
            cwd=ROOT,
            env=probe_environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if result.returncode != 0:
            fail(f"dual-mode probe failed: {result.stderr.strip()[-500:]}")
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        if not payload["takeover"]["cdp_version"].endswith(f"/{contract.browser_version}"):
            fail("takeover mode did not control the locked browser")

    require_evidence()
    print(
        "BU-02 dual-mode acceptance passed: isolated executable_path + "
        f"random loopback CDP takeover on {contract.browser_version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
