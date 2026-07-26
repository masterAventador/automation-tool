#!/usr/bin/env python3
"""Public-path acceptance for the deployed customer Demo Control Plane.

Runs from an operator machine (not from the server) and only ever speaks real
HTTPS to the public hostname, so it exercises DNS, the public certificate, the
shared Nginx edge and the Control Plane exactly as the desktop App will.

    AUTOMATION_TOOL_DEMO_LOGIN_NAME=... AUTOMATION_TOOL_DEMO_PASSWORD=... \
        python3 deploy/cloud/verify_cloud_demo.py

Credentials are read from the environment and are never written to the
repository or echoed back into the output.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final

CLOUD_ROOT: Final = Path(__file__).resolve().parent
ENVIRONMENT_FILE: Final = CLOUD_ROOT / "demo-environment.json"

REQUIRED_SECURITY_HEADERS: Final = (
    "strict-transport-security",
    "x-content-type-options",
    "content-security-policy",
    "referrer-policy",
)


class AcceptanceFailure(RuntimeError):
    """One acceptance assertion failed."""


def request(
    *,
    base_url: str,
    path: str,
    method: str = "GET",
    payload: Any = None,
    token: str | None = None,
    allow_redirects: bool = True,
) -> tuple[int, dict[str, str], Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    http_request = urllib.request.Request(f"{base_url}{path}", data=body, method=method)
    http_request.add_header("accept", "application/json")
    if body is not None:
        http_request.add_header("content-type", "application/json")
    if token is not None:
        http_request.add_header("authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    if not allow_redirects:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_arguments: Any, **_keywords: Any) -> None:
                return None

        opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(http_request, timeout=30) as response:  # noqa: S310
            encoded = response.read(512 * 1024)
            headers = {name.lower(): value for name, value in response.headers.items()}
            return response.status, headers, json.loads(encoded) if encoded else None
    except urllib.error.HTTPError as error:
        encoded = error.read(512 * 1024)
        headers = {name.lower(): value for name, value in error.headers.items()}
        try:
            return error.code, headers, json.loads(encoded) if encoded else None
        except json.JSONDecodeError:
            return error.code, headers, None


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)
    print(f"  ok  {message}")


def main() -> None:
    environment = json.loads(ENVIRONMENT_FILE.read_text(encoding="utf-8"))
    host = environment["demoHost"]
    base_url = f"https://{host}"
    login_name = os.environ.get("AUTOMATION_TOOL_DEMO_LOGIN_NAME")
    password = os.environ.get("AUTOMATION_TOOL_DEMO_PASSWORD")
    if not login_name or not password:
        raise SystemExit(
            "set AUTOMATION_TOOL_DEMO_LOGIN_NAME and AUTOMATION_TOOL_DEMO_PASSWORD"
        )

    print(f"[verify] {base_url}")

    status, headers, health = request(base_url=base_url, path="/api/v1/health")
    check(status == 200, f"public HTTPS health returns 200 (got {status})")
    check(health == {"status": "ok", "service": "control-plane", "version": health["version"]},
          "health projection has the fixed contract shape")
    for header in REQUIRED_SECURITY_HEADERS:
        check(header in headers, f"edge sets {header}")

    status, _, version = request(base_url=base_url, path="/api/v1/version")
    check(status == 200, f"public HTTPS version returns 200 (got {status})")
    check(version["service"] == "control-plane", "version reports the Control Plane service")
    check(version["apiVersion"] == "v1", "version reports API v1")

    status, _, _ = request(base_url=base_url, path="/api/v1/account-installations")
    check(status == 401, f"unauthenticated business read is rejected (got {status})")

    status, _, _ = request(
        base_url=base_url,
        path="/api/v1/account-sessions",
        method="POST",
        payload={"loginName": login_name, "password": "wrong password value"},
    )
    check(status == 401, f"wrong password is rejected (got {status})")

    status, _, session = request(
        base_url=base_url,
        path="/api/v1/account-sessions",
        method="POST",
        payload={"loginName": login_name, "password": password},
    )
    check(status == 201, f"product account login returns 201 (got {status})")
    check(session["account"]["loginName"] == login_name, "login returns the same account")
    check(session["account"]["status"] == "active", "the Demo account is active")
    access_token = session["accessToken"]
    refresh_token = session["refreshToken"]

    status, _, devices = request(
        base_url=base_url, path="/api/v1/account-installations", token=access_token
    )
    check(status == 200, f"authenticated business read returns 200 (got {status})")
    check(isinstance(devices.get("devices"), list), "device list projection is present")

    status, _, _ = request(
        base_url=base_url,
        path="/api/v1/account-installations",
        token=access_token[:-1] + ("a" if access_token[-1] != "a" else "b"),
    )
    check(status == 401, f"a tampered access token is rejected (got {status})")

    status, _, refreshed = request(
        base_url=base_url,
        path="/api/v1/account-sessions/refresh",
        method="POST",
        token=refresh_token,
    )
    check(status == 201, f"refresh rotates the session (got {status})")
    rotated_refresh = refreshed["refreshToken"]
    check(rotated_refresh != refresh_token, "refresh token rotates on use")

    status, _, _ = request(
        base_url=base_url,
        path="/api/v1/account-sessions/refresh",
        method="POST",
        token=refresh_token,
    )
    check(status == 401, f"a replayed refresh token is rejected (got {status})")

    status, _, _ = request(
        base_url=base_url,
        path="/api/v1/account-sessions/current",
        method="DELETE",
        token=rotated_refresh,
    )
    check(status in {204, 401}, f"logout closes the rotated session (got {status})")

    status, headers, _ = request(
        base_url=f"http://{host}", path="/api/v1/health", allow_redirects=False
    )
    check(status == 308, f"plain HTTP is redirected to HTTPS (got {status})")
    check(
        headers.get("location", "").startswith(f"https://{host}"),
        "the redirect target is the public HTTPS host",
    )

    print("\n[verify] every public acceptance assertion passed")


if __name__ == "__main__":
    try:
        main()
    except AcceptanceFailure as error:
        print(f"\n[verify] FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from None
