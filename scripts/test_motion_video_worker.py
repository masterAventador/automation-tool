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
    print("BM-02 Node Worker rejection tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
