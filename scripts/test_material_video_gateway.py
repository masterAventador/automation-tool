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
    material_preview_capability_path,
    parse_bootstrap,
    parse_cancel_command,
)

TOKEN = "11" * 32
ORIGIN = "tauri://localhost"
MATERIAL_ID = "123e4567-e89b-42d3-a456-426614174321"


class _PreviewLease:
    content_type = "video/mp4"

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.size_bytes = len(body)
        self.closed = False

    def read(self, start: int, length: int) -> bytes:
        if self.closed:
            raise AssertionError("closed preview lease was read")
        return self._body[start : start + length]

    def close(self) -> None:
        self.closed = True


class _PreviewSource:
    def __init__(self, body: bytes = b"0123456789") -> None:
        self.body = body
        self.opened: list[str] = []
        self.leases: list[_PreviewLease] = []

    def open(self, material_id: object) -> _PreviewLease:
        self.opened.append(str(material_id))
        lease = _PreviewLease(self.body)
        self.leases.append(lease)
        return lease


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
def running_gateway(
    asset_root: Path,
    *,
    preview_source: _PreviewSource | None = None,
) -> Iterator[tuple[int, GatewayBootstrap]]:
    bootstrap = GatewayBootstrap(
        TOKEN,
        bytes.fromhex(TOKEN),
        asset_root.resolve(),
        local_editing=preview_source is not None,
    )
    server = create_gateway(bootstrap, material_preview=preview_source)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield int(server.server_address[1]), bootstrap
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


def request_with_repeated_headers(
    port: int,
    path: str,
    headers: list[tuple[str, str]],
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.putrequest("GET", path, skip_host=True)
    for name, value in headers:
        connection.putheader(name, value)
    connection.endheaders()
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
            self.assertEqual(repr(parsed), "GatewayBootstrap(<redacted>)")
            self.assertNotIn(TOKEN, repr(parsed))
            self.assertNotIn(str(root), repr(parsed))
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
            running_gateway(Path(directory)) as running,
        ):
            port, _ = running
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
                with running_gateway(root) as running:
                    port, _ = running
                    status, headers, body = request(
                        port,
                        "OPTIONS",
                        "/api/v1/assets/inspect",
                        headers={
                            "Origin": ORIGIN,
                            "Access-Control-Request-Method": "POST",
                            "Access-Control-Request-Headers": (
                                "authorization, content-type"
                            ),
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
                with running_gateway(root) as running:
                    port, _ = running
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

    def test_material_preview_supports_exact_full_head_and_single_ranges(self) -> None:
        with tempfile.TemporaryDirectory(prefix="le18-preview-http-") as directory:
            source = _PreviewSource()
            with running_gateway(Path(directory), preview_source=source) as running:
                port, bootstrap = running
                capability = material_preview_capability_path(bootstrap)
                other_bootstrap = GatewayBootstrap(
                    "22" * 32,
                    bytes.fromhex("22" * 32),
                    Path(directory).resolve(),
                    local_editing=True,
                )
                self.assertNotEqual(
                    capability,
                    material_preview_capability_path(other_bootstrap),
                )
                self.assertNotIn(TOKEN, capability)
                path = f"/api/v1/material-previews/{capability}/{MATERIAL_ID}"

                status, headers, body = request(
                    port, "GET", path, headers={"Origin": ORIGIN}
                )
                self.assertEqual((status, body), (200, source.body))
                self.assertEqual(headers["content-type"], "video/mp4")
                self.assertEqual(headers["content-length"], "10")
                self.assertEqual(headers["accept-ranges"], "bytes")
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertEqual(headers["referrer-policy"], "no-referrer")

                status, headers, body = request(
                    port,
                    "HEAD",
                    path,
                    headers={"Origin": ORIGIN},
                )
                self.assertEqual((status, body), (200, b""))
                self.assertEqual(headers["content-length"], "10")

                for range_value, expected_range, expected_body in (
                    ("bytes=2-5", "bytes 2-5/10", b"2345"),
                    ("bytes=7-", "bytes 7-9/10", b"789"),
                    ("bytes=-3", "bytes 7-9/10", b"789"),
                ):
                    status, headers, body = request(
                        port,
                        "GET",
                        path,
                        headers={"Origin": ORIGIN, "Range": range_value},
                    )
                    self.assertEqual((status, body), (206, expected_body))
                    self.assertEqual(headers["content-range"], expected_range)
                    self.assertEqual(headers["content-length"], str(len(expected_body)))

                self.assertEqual(source.opened, [MATERIAL_ID] * 5)
                self.assertTrue(all(lease.closed for lease in source.leases))

    def test_material_preview_rejects_invalid_ranges_origins_and_capabilities(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="le18-preview-reject-") as directory:
            private = str(Path(directory).resolve())
            source = _PreviewSource()
            with running_gateway(Path(directory), preview_source=source) as running:
                port, bootstrap = running
                capability = material_preview_capability_path(bootstrap)
                path = f"/api/v1/material-previews/{capability}/{MATERIAL_ID}"
                for range_value in (
                    "items=0-1",
                    "bytes=",
                    "bytes=1-2,4-5",
                    "bytes=10-",
                    "bytes=5-4",
                    "bytes=-0",
                    "bytes=+1-2",
                    "bytes=" + "9" * 129 + "-",
                ):
                    status, headers, body = request(
                        port, "GET", path, headers={"Range": range_value}
                    )
                    self.assertEqual((status, body), (416, b""))
                    self.assertEqual(headers["content-range"], "bytes */10")

                status, headers, body = request_with_repeated_headers(
                    port,
                    path,
                    [
                        ("Host", f"127.0.0.1:{port}"),
                        ("Range", "bytes=0-1"),
                        ("Range", "bytes=2-3"),
                    ],
                )
                self.assertEqual((status, body), (416, b""))
                self.assertEqual(headers["content-range"], "bytes */10")

                status, _, body = request_with_repeated_headers(
                    port,
                    path,
                    [("Host", "evil.example")],
                )
                self.assertEqual((status, body), (403, b""))

                status, _, body = request(
                    port,
                    "GET",
                    path,
                    headers={"Origin": "https://evil.example"},
                )
                self.assertEqual((status, body), (403, b""))

                for rejected_path in (
                    f"/api/v1/material-previews/wrong/{MATERIAL_ID}",
                    f"/api/v1/material-previews/{capability}/not-a-uuid",
                    f"/api/v1/material-previews/{capability}/{MATERIAL_ID}?path={private}",
                ):
                    status, _, body = request(port, "GET", rejected_path)
                    self.assertEqual((status, body), (404, b""))
                    self.assertNotIn(private.encode(), body)
                    self.assertNotIn(TOKEN.encode(), body)


if __name__ == "__main__":
    unittest.main()
