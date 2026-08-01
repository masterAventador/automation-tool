"""Cross-platform real-process fixture for LE-12 lifecycle acceptance."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import socket
import sys
import threading

WORKER_VERSION = "1.2.3"


def _proof(token: bytes, event: str, kind: str, protocol: str, detail: str) -> str:
    message = b"automation-tool.video-worker-event.v1\0" + b"\0".join(
        value.encode() for value in (event, kind, protocol, WORKER_VERSION, detail)
    )
    digest = hmac.digest(token, message, hashlib.sha256)
    return "atvwp1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _command_proof(
    token: bytes,
    command: str,
    kind: str,
    protocol: str,
    job_id: str,
    detail: str | None = None,
) -> str:
    parts = [command, kind, protocol, job_id]
    if detail is not None:
        parts.append(detail)
    message = b"automation-tool.video-worker-command.v1\0" + b"\0".join(
        value.encode() for value in parts
    )
    digest = hmac.digest(token, message, hashlib.sha256)
    return "atvwc1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


bootstrap = json.loads(sys.stdin.buffer.readline(16 * 1024))
if os.environ.get("LE12_PARENT_SECRET") is not None:
    raise SystemExit(19)
if set(bootstrap) != {
    "assetRoot",
    "bootstrapVersion",
    "enableWebUi",
    "localSessionToken",
    "mediaTools",
    "protocolVersion",
    "renderBrowser",
    "scriptModel",
    "workerKind",
} or set(bootstrap.get("mediaTools", {})) != {"ffmpegPath", "ffprobePath"}:
    raise SystemExit(19)
token = bytes.fromhex(bootstrap["localSessionToken"])
kind = bootstrap["workerKind"]
protocol = bootstrap["protocolVersion"]
asset_root = pathlib.Path(bootstrap["assetRoot"])

server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen()
port = server.getsockname()[1]
preview_path = "material-preview-v1-" + "A" * 43
stopping = False


def _serve() -> None:
    while not stopping:
        try:
            connection, _ = server.accept()
        except OSError:
            return
        request = connection.recv(8192).decode(errors="replace")
        authorized = (
            f"Authorization: Bearer {bootstrap['localSessionToken']}" in request
        )
        if authorized and request.startswith("GET /health HTTP/1.1"):
            body = json.dumps(
                {
                    "authenticationProof": _proof(
                        token, "worker.health", kind, protocol, str(port)
                    ),
                    "event": "worker.health",
                    "protocolVersion": protocol,
                    "workerKind": kind,
                    "workerVersion": WORKER_VERSION,
                    "port": port,
                },
                separators=(",", ":"),
            )
            response = (
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body.encode()))
                + "\r\nConnection: close\r\n\r\n"
                + body
            )
        else:
            response = (
                "HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n"
                "Connection: close\r\n\r\n"
            )
        connection.sendall(response.encode())
        connection.close()


threading.Thread(target=_serve, daemon=True).start()
print(
    json.dumps(
        {
            "authenticationProof": _proof(
                token, "worker.ready", kind, protocol, str(port)
            ),
            "event": "worker.ready",
            "materialPreviewAuthenticationProof": _proof(
                token,
                "worker.material_preview_ready",
                kind,
                protocol,
                f"{port}:{preview_path}",
            ),
            "materialPreviewPath": preview_path,
            "protocolVersion": protocol,
            "workerKind": kind,
            "workerVersion": WORKER_VERSION,
            "port": port,
        },
        separators=(",", ":"),
    ),
    flush=True,
)


def _event(event: str, job_id: str, detail: str, **facts: object) -> None:
    document: dict[str, object] = {
        "authenticationProof": _proof(token, event, kind, protocol, detail),
        "event": event,
        "jobId": job_id,
        "protocolVersion": protocol,
        "workerKind": kind,
        "workerVersion": WORKER_VERSION,
    }
    document.update(facts)
    print(json.dumps(document, separators=(",", ":")), flush=True)


def _progress(job_id: str, phase: str, progress: int) -> None:
    _event(
        "worker.editing.progress",
        job_id,
        f"{job_id}\0{phase}\0{progress}",
        phase=phase,
        progressPermille=progress,
    )


def _succeed(job_id: str) -> None:
    _progress(job_id, "rendering", 600)
    _progress(job_id, "publishing", 1000)
    artifact = "423e4567-e89b-42d3-a456-426614174103"
    _event(
        "worker.editing.succeeded",
        job_id,
        f"{job_id}\0{artifact}",
        outputArtifactId=artifact,
    )


active_job: str | None = None
for line in sys.stdin:
    command = json.loads(line)
    job_id = command.get("jobId", "")
    if command.get("command") == "worker.editing.start":
        editing = command.get("editing")
        canonical = json.dumps(editing, separators=(",", ":"), sort_keys=True)
        expected = _command_proof(
            token, "worker.editing.start", kind, protocol, job_id, canonical
        )
        if not hmac.compare_digest(command.get("authenticationProof", ""), expected):
            continue
        active_job = job_id
        _progress(job_id, "preparing", 0)
        revision = editing.get("timelineRevision")
        if revision == 9:
            marker = asset_root / "crashed-once"
            if not marker.exists():
                marker.write_text("crashed", encoding="utf-8")
                os._exit(17)
        if revision == 10:
            marker = asset_root / "app-restarted-once"
            if not marker.exists():
                marker.write_text("waiting", encoding="utf-8")
                continue
        if revision in (8, 11):
            continue
        _succeed(job_id)
        continue
    if command.get("command") == "worker.cancel" and active_job == job_id:
        expected = _command_proof(token, "worker.cancel", kind, protocol, job_id)
        if not hmac.compare_digest(command.get("authenticationProof", ""), expected):
            continue
        _event("worker.editing.cancelled", job_id, job_id)

stopping = True
server.close()
