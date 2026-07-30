#!/usr/bin/env python3
"""BM-02 fixed-boundary rejection tests for the Node Worker source."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers/motion_composition/worker.mjs"


def run_worker(payload: str) -> subprocess.CompletedProcess[str]:
    node = os.environ.get("BM02_NODE", shutil_which_node())
    environment = {"PATH": ""}
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR"):
            if key in os.environ:
                environment[key] = os.environ[key]
    return subprocess.run(
        [node, str(WORKER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )


def shutil_which_node() -> str:
    import shutil

    value = shutil.which("node")
    if value is None:
        raise AssertionError("development Node is unavailable")
    return value


def main() -> int:
    missing = run_worker("")
    assert missing.returncode == 64
    assert missing.stdout == ""
    assert missing.stderr == "Motion composition worker command is required\n"
    malformed = run_worker('{"localSessionToken":"private-value"}\n')
    assert malformed.returncode == 65
    assert malformed.stdout == ""
    assert malformed.stderr == "Motion composition worker bootstrap is rejected\n"
    with tempfile.TemporaryDirectory(prefix="bm02-assets-") as directory:
        bootstrap = {
            "assetRoot": directory,
            "bootstrapVersion": "1",
            "enableWebUi": False,
            "localSessionToken": "a" * 64,
            "protocolVersion": "1.0",
            "scriptModel": None,
            "workerKind": "node",
            "unexpectedSecret": "must-not-leak",
        }
        rejected = run_worker(json.dumps(bootstrap) + "\n")
        assert rejected.returncode == 65
        assert "must-not-leak" not in rejected.stderr
        assert directory not in rejected.stderr
    # A headless Chromium with the GPU disabled has no WebGL at all unless the
    # software rasterizer is explicitly allowed: Chromium 149 refuses the
    # SwiftShader fallback without `--enable-unsafe-swiftshader`, every shader
    # catalog item then takes its no-GL branch and renders one static page, and
    # the worker's own static-frame gate refuses the job (BM-16, first seen on
    # `chromatic-radial-split`). So the two flags may only travel together.
    source = WORKER.read_text(encoding="utf-8")
    disables = source.count('"--disable-gpu"')
    allows = source.count('"--enable-unsafe-swiftshader"')
    assert disables >= 1
    assert disables == allows, (
        "every --disable-gpu launch needs --enable-unsafe-swiftshader beside "
        f"it: {disables} vs {allows}"
    )
    print("BM-02 Node Worker rejection tests passed")
    print("executed checks: 4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
