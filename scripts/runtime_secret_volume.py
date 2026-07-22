"""Test-only Docker volume writer that keeps runtime secrets off argv and env."""

from __future__ import annotations

import json
from collections.abc import Mapping

_WRITER = r"""import json
import os
import sys

payload = json.load(sys.stdin)
if not isinstance(payload, dict) or not payload:
    raise SystemExit(2)
for name, value in payload.items():
    if name != "database-url" or not isinstance(value, str):
        raise SystemExit(2)
    descriptor = os.open(f"/target/{name}", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fchown(descriptor, 65532, 65532)
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)
"""


def writer_command(*, image: str, volume: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--user",
        "0:0",
        "--mount",
        f"type=volume,source={volume},target=/target",
        "--entrypoint",
        "python",
        image,
        "-c",
        _WRITER,
    ]


def writer_payload(values: Mapping[str, str]) -> str:
    if set(values) != {"database-url"} or not values["database-url"]:
        raise ValueError("Runtime secret payload is invalid")
    return json.dumps(dict(values), separators=(",", ":"))


__all__ = ["writer_command", "writer_payload"]
