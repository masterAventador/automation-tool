#!/usr/bin/env python3
"""Real loopback and failure-matrix tests for the material-video gateway."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers/material_montage"))

from gateway import (  # noqa: E402
    GatewayBootstrap,
    GatewayRejected,
    create_gateway,
    parse_bootstrap,
    parse_cancel_command,
)

TOKEN = "11" * 32
ORIGIN = "tauri://localhost"


def bootstrap_line(asset_root: Path, **changes: object) -> bytes:
    value: dict[str, object] = {
        "assetRoot": str(asset_root),
        "bootstrapVersion": "1",
        "enableWebUi": False,
        "localSessionToken": TOKEN,
        "protocolVersion": "1.0",
        "renderBrowser": None,
        "scriptModel": None,
        "workerKind": "python",
    }
    value.update(changes)
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


@contextmanager
def running_gateway(asset_root: Path) -> Iterator[int]:
    bootstrap = GatewayBootstrap(TOKEN, bytes.fromhex(TOKEN), asset_root.resolve())
    server = create_gateway(bootstrap)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    result = (
        response.status,
        {key.lower(): value for key, value in response.getheaders()},
        payload,
    )
    connection.close()
    return result


def authorized_headers(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", **extra}


class MaterialVideoGatewayTest(unittest.TestCase):
    def test_bootstrap_requires_exact_secret_and_private_absolute_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im03-bootstrap-") as directory:
            root = Path(directory).resolve()
            parsed = parse_bootstrap(bootstrap_line(root))
            self.assertEqual(parsed.asset_root, root)
            for changes in (
                {"localSessionToken": "short"},
                {"workerKind": "node"},
                {"protocolVersion": "2.0"},
                {"enableWebUi": "true"},
                {"renderBrowser": {}},
                {"assetRoot": "relative"},
                {"extra": True},
            ):
                with self.assertRaises(GatewayRejected):
                    parse_bootstrap(bootstrap_line(root, **changes))
            duplicate = bootstrap_line(root).replace(
                b'"workerKind":"python"',
                b'"workerKind":"python","workerKind":"python"',
            )
            with self.assertRaises(GatewayRejected):
                parse_bootstrap(duplicate)

    def test_cancel_command_requires_exact_authenticated_document(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im03-command-") as directory:
            bootstrap = GatewayBootstrap(
                TOKEN, bytes.fromhex(TOKEN), Path(directory).resolve()
            )
            job_id = "123e4567-e89b-42d3-a456-426614174321"
            message = b"automation-tool.video-worker-command.v1\0" + b"\0".join(
                value.encode() for value in ["worker.cancel", "python", "1.0", job_id]
            )
            proof = (
                "atvwc1."
                + base64.urlsafe_b64encode(
                    hmac.digest(bytes.fromhex(TOKEN), message, hashlib.sha256)
                )
                .rstrip(b"=")
                .decode()
            )
            value = {
                "authenticationProof": proof,
                "command": "worker.cancel",
                "jobId": job_id,
                "protocolVersion": "1.0",
                "workerKind": "python",
            }
            payload = json.dumps(value, separators=(",", ":")).encode()
            self.assertEqual(parse_cancel_command(bootstrap, payload), job_id)
            duplicate = payload.replace(
                b'"command":"worker.cancel"',
                b'"command":"worker.cancel","command":"worker.cancel"',
            )
            with self.assertRaises(GatewayRejected):
                parse_cancel_command(bootstrap, duplicate)

    def test_health_authentication_route_and_cors_are_fail_closed(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="im03-http-") as directory,
            running_gateway(Path(directory)) as port,
        ):
            status, headers, body = request(port, "GET", "/health")
            self.assertEqual(status, 401)
            self.assertEqual(json.loads(body), {"code": "authentication_required"})
            self.assertNotIn("access-control-allow-origin", headers)

            status, headers, body = request(
                port,
                "GET",
                "/health",
                headers=authorized_headers(Origin=ORIGIN),
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers["access-control-allow-origin"], ORIGIN)
            health = json.loads(body)
            self.assertEqual(health["event"], "worker.health")
            self.assertEqual(health["port"], port)
            self.assertNotIn(TOKEN, body.decode())

            status, _, body = request(
                port,
                "GET",
                "/health?debug=1",
                headers=authorized_headers(),
            )
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body), {"code": "request_rejected"})

            status, _, body = request(
                port,
                "GET",
                "/api/v1/unknown",
                headers=authorized_headers(),
            )
            self.assertEqual(status, 404)
            self.assertEqual(json.loads(body), {"code": "route_unavailable"})

            status, _, body = request(
                port,
                "GET",
                "/api/v1/capabilities",
                headers=authorized_headers(Origin="https://evil.example"),
            )
            self.assertEqual(status, 400)
            self.assertNotIn(str(Path(directory)), body.decode())
            self.assertNotIn(TOKEN, body.decode())

    def test_preflight_and_asset_containment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im03-assets-") as directory:
            root = Path(directory).resolve()
            source = root / "inputs/clip.mp4"
            source.parent.mkdir()
            source.write_bytes(b"safe-video")
            outside = root.parent / f"{root.name}-outside.txt"
            outside.write_bytes(b"outside")
            try:
                with running_gateway(root) as port:
                    status, headers, body = request(
                        port,
                        "OPTIONS",
                        "/api/v1/assets/inspect",
                        headers={
                            "Origin": ORIGIN,
                            "Access-Control-Request-Method": "POST",
                            "Access-Control-Request-Headers": "authorization, content-type",
                        },
                    )
                    self.assertEqual((status, body), (204, b""))
                    self.assertEqual(headers["access-control-allow-origin"], ORIGIN)

                    payload = json.dumps({"relativePath": "inputs/clip.mp4"}).encode()
                    status, _, body = request(
                        port,
                        "POST",
                        "/api/v1/assets/inspect",
                        body=payload,
                        headers=authorized_headers(
                            **{
                                "Content-Type": "application/json",
                                "Content-Length": str(len(payload)),
                            }
                        ),
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        json.loads(body),
                        {"sizeBytes": len(b"safe-video"), "status": "available"},
                    )

                    for relative in (
                        "../outside.txt",
                        str(outside),
                        "inputs\\clip.mp4",
                        "missing.mp4",
                    ):
                        payload = json.dumps({"relativePath": relative}).encode()
                        status, _, body = request(
                            port,
                            "POST",
                            "/api/v1/assets/inspect",
                            body=payload,
                            headers=authorized_headers(
                                **{"Content-Type": "application/json"}
                            ),
                        )
                        self.assertEqual(status, 400)
                        self.assertEqual(json.loads(body), {"code": "request_rejected"})
                        self.assertNotIn(str(root), body.decode())

                    oversized = b"{}"
                    status, _, body = request(
                        port,
                        "POST",
                        "/api/v1/assets/inspect",
                        body=oversized,
                        headers=authorized_headers(
                            **{
                                "Content-Type": "application/json",
                                "Content-Length": "65537",
                            }
                        ),
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(json.loads(body), {"code": "request_rejected"})
            finally:
                outside.unlink(missing_ok=True)

    @unittest.skipIf(
        os.name == "nt", "Windows symlink creation requires optional privilege"
    )
    def test_asset_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im03-symlink-") as directory:
            root = Path(directory).resolve()
            outside = root.parent / f"{root.name}-outside.txt"
            outside.write_text("outside", encoding="utf-8")
            try:
                (root / "linked.txt").symlink_to(outside)
                with running_gateway(root) as port:
                    payload = json.dumps({"relativePath": "linked.txt"}).encode()
                    status, _, _ = request(
                        port,
                        "POST",
                        "/api/v1/assets/inspect",
                        body=payload,
                        headers=authorized_headers(
                            **{"Content-Type": "application/json"}
                        ),
                    )
                    self.assertEqual(status, 400)
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
