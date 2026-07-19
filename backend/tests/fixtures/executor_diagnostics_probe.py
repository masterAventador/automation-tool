"""Test-only packaged process that emits hostile stderr before becoming healthy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

bootstrap = json.loads(sys.stdin.buffer.readline())
state_directory = Path(bootstrap["state_directory"])
mode = (state_directory / "e4-10-mode").read_text(encoding="ascii").strip()
if mode == "shared-fixture":
    document = json.loads((state_directory / "diagnostic-inputs.json").read_text(encoding="utf-8"))
    for case in document["cases"]:
        sys.stderr.buffer.write(case["input"].encode("utf-8") + b"\n")
elif mode == "limits":
    counter_path = state_directory / "e4-10-run-count"
    run_count = int(counter_path.read_text(encoding="ascii")) + 1 if counter_path.exists() else 1
    counter_path.write_text(str(run_count), encoding="ascii")
    secret = b"atds1.private-diagnostic-session"
    for index in range(400):
        sys.stderr.buffer.write(
            b"line="
            + str(index).encode("ascii")
            + b" session="
            + secret
            + b" "
            + b"x" * 900
            + b"\r\n"
        )
    sys.stderr.buffer.write(b"y" * 5000 + b"\n")
    sys.stderr.buffer.write(b"\xff\xfe\n")
    sys.stderr.buffer.write(f"E4-10-finished-{run_count}\r\n".encode("ascii"))
else:
    raise SystemExit(2)
sys.stderr.buffer.flush()
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
