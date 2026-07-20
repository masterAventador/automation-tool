"""Test-only packaged process that creates one long-running Windows descendant."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import sys
import time
from pathlib import Path

bootstrap = json.loads(sys.stdin.buffer.readline())
state_directory = Path(bootstrap["state_directory"])
mode = (state_directory / "e4-09-mode").read_text(encoding="ascii").strip()
marker = state_directory / "descendant-pids"
marker_source = str(marker).replace("'", "''")
subprocess.Popen(
    [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "$PID.ToString() | Add-Content "
            f"-LiteralPath '{marker_source}' -Encoding ascii; "
            "Start-Sleep -Seconds 120"
        ),
    ],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
if mode == "silent":
    time.sleep(120)
if mode != "healthy":
    raise SystemExit(2)
key = bytes.fromhex(bootstrap["local_session_token"])
event = "executor.healthy"
message = b"automation-tool.local-executor-event.v1\0" + event.encode("ascii") + b"\0" + b"1.0"
proof = base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=")
source = json.dumps(
    {
        "authenticationProof": "atlep1." + proof.decode("ascii"),
        "event": event,
        "protocolVersion": "1.0",
    },
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
sys.stdout.buffer.write(source + b"\n")
sys.stdout.buffer.flush()
time.sleep(120)
