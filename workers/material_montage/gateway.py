"""Authenticated, loopback-only HTTP gateway for the material-video Worker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import stat
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import urlsplit

from model_service_adapter import ScriptModelConfiguration, parse_script_model

HOST: Final = "127.0.0.1"
WORKER_VERSION: Final = "1.3.2"
PROTOCOL_VERSION: Final = "1.0"
BOOTSTRAP_VERSION: Final = "1"
MAX_BOOTSTRAP_BYTES: Final = 16 * 1024
MAX_BODY_BYTES: Final = 64 * 1024
MAX_ASSET_BYTES: Final = 2 * 1024 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS: Final = 10
TOKEN_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ALLOWED_ORIGINS: Final = frozenset(
    {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"}
)
ALLOWED_ROUTES: Final = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/api/v1/capabilities"),
        ("POST", "/api/v1/assets/inspect"),
    }
)
EVENT_DOMAIN: Final = b"automation-tool.video-worker-event.v1\0"
COMMAND_DOMAIN: Final = b"automation-tool.video-worker-command.v1\0"
WINDOWS_REPARSE_POINT: Final = 0x400


class GatewayRejected(ValueError):
    """Fixed boundary for invalid bootstrap, request, or private paths."""


@dataclass(frozen=True)
class GatewayBootstrap:
    token_text: str
    token_bytes: bytes
    asset_root: Path
    script_model: ScriptModelConfiguration | None = None
    web_ui: bool = False
    local_editing: bool = False


def _fixed_json(code: str) -> bytes:
    return json.dumps({"code": code}, separators=(",", ":")).encode()


def _load_object(payload: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise GatewayRejected("duplicate JSON field")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GatewayRejected("invalid JSON") from None
    if not isinstance(value, dict):
        raise GatewayRejected("JSON object required")
    return value


def _unsafe_metadata(path: Path) -> bool:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & WINDOWS_REPARSE_POINT)


def validate_asset_root(value: object) -> Path:
    if not isinstance(value, str) or not value or len(value.encode()) > 4096:
        raise GatewayRejected("invalid asset root")
    path = Path(value)
    if not path.is_absolute():
        raise GatewayRejected("invalid asset root")
    for ancestor in path.parents:
        if ancestor.exists() and _unsafe_metadata(ancestor):
            raise GatewayRejected("invalid asset root")
    if not path.is_dir() or _unsafe_metadata(path):
        raise GatewayRejected("invalid asset root")
    return path.resolve(strict=True)


def parse_bootstrap(line: bytes) -> GatewayBootstrap:
    if not line.endswith(b"\n") or not 1 < len(line) <= MAX_BOOTSTRAP_BYTES:
        raise GatewayRejected("invalid bootstrap")
    value = _load_object(line[:-1])
    base_keys = {
        "assetRoot",
        "bootstrapVersion",
        "enableWebUi",
        "localSessionToken",
        "protocolVersion",
        "renderBrowser",
        "scriptModel",
        "workerKind",
    }
    keys = set(value)
    local_editing = keys == base_keys | {"mediaTools"}
    if keys != base_keys and not local_editing:
        raise GatewayRejected("invalid bootstrap")
    if local_editing:
        media_tools = value.get("mediaTools")
        if not isinstance(media_tools, dict) or set(media_tools) != {
            "ffmpegPath",
            "ffprobePath",
        }:
            raise GatewayRejected("invalid bootstrap")
        ffmpeg = media_tools.get("ffmpegPath")
        ffprobe = media_tools.get("ffprobePath")
        if (
            not isinstance(ffmpeg, str)
            or not isinstance(ffprobe, str)
            or not Path(ffmpeg).is_absolute()
            or not Path(ffprobe).is_absolute()
            or ffmpeg == ffprobe
        ):
            raise GatewayRejected("invalid bootstrap")
    token = value.get("localSessionToken")
    if (
        value.get("bootstrapVersion") != BOOTSTRAP_VERSION
        or value.get("protocolVersion") != PROTOCOL_VERSION
        or value.get("workerKind") != "python"
        or value.get("renderBrowser") is not None
        or not isinstance(value.get("enableWebUi"), bool)
        or not isinstance(token, str)
        or TOKEN_PATTERN.fullmatch(token) is None
    ):
        raise GatewayRejected("invalid bootstrap")
    return GatewayBootstrap(
        token_text=token,
        token_bytes=bytes.fromhex(token),
        asset_root=validate_asset_root(value.get("assetRoot")),
        script_model=parse_script_model(value.get("scriptModel")),
        web_ui=value["enableWebUi"],
        local_editing=local_editing,
    )


def event_proof(bootstrap: GatewayBootstrap, event: str, detail: str) -> str:
    message = EVENT_DOMAIN + b"\0".join(
        value.encode()
        for value in [event, "python", PROTOCOL_VERSION, WORKER_VERSION, detail]
    )
    digest = hmac.digest(bootstrap.token_bytes, message, hashlib.sha256)
    return "atvwp1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def verify_cancel_proof(
    bootstrap: GatewayBootstrap, job_id: str, proof: object
) -> bool:
    message = COMMAND_DOMAIN + b"\0".join(
        value.encode()
        for value in ["worker.cancel", "python", PROTOCOL_VERSION, job_id]
    )
    digest = hmac.digest(bootstrap.token_bytes, message, hashlib.sha256)
    expected = "atvwc1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return isinstance(proof, str) and hmac.compare_digest(proof, expected)


def parse_cancel_command(bootstrap: GatewayBootstrap, payload: bytes) -> str:
    if not payload or len(payload) > MAX_BOOTSTRAP_BYTES:
        raise GatewayRejected("invalid command")
    value = _load_object(payload)
    if set(value) != {
        "authenticationProof",
        "command",
        "jobId",
        "protocolVersion",
        "workerKind",
    }:
        raise GatewayRejected("invalid command")
    job_id = value.get("jobId")
    try:
        parsed_job_id = uuid.UUID(job_id) if isinstance(job_id, str) else None
    except ValueError:
        parsed_job_id = None
    if (
        value.get("command") != "worker.cancel"
        or value.get("protocolVersion") != PROTOCOL_VERSION
        or value.get("workerKind") != "python"
        or parsed_job_id is None
        or parsed_job_id.version != 4
        or str(parsed_job_id) != job_id
        or not verify_cancel_proof(bootstrap, job_id, value.get("authenticationProof"))
    ):
        raise GatewayRejected("invalid command")
    return job_id


def inspect_asset(root: Path, value: object) -> int:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode()) > 512
        or "\\" in value
    ):
        raise GatewayRejected("invalid asset path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or str(relative) != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise GatewayRejected("invalid asset path")
    current = root
    for part in relative.parts:
        current = current / part
        if not current.exists() or _unsafe_metadata(current):
            raise GatewayRejected("asset unavailable")
    resolved = current.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise GatewayRejected("asset unavailable")
    size = resolved.stat().st_size
    if not 0 < size <= MAX_ASSET_BYTES:
        raise GatewayRejected("asset unavailable")
    return size


def gateway_handler(
    bootstrap: GatewayBootstrap, port: int
) -> type[BaseHTTPRequestHandler]:
    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "AutomationTool"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

        def log_message(self, _format: str, *args: object) -> None:
            del args

        def send_error(
            self,
            code: int,
            message: str | None = None,
            explain: str | None = None,
        ) -> None:
            del message, explain
            self._respond(code, _fixed_json("request_rejected"))

        def _origin(self) -> str | None:
            values = self.headers.get_all("Origin", [])
            if not values:
                return None
            if len(values) != 1 or values[0] not in ALLOWED_ORIGINS:
                raise GatewayRejected("origin rejected")
            return values[0]

        def _authorized(self) -> bool:
            values = self.headers.get_all("Authorization", [])
            expected = f"Bearer {bootstrap.token_text}"
            return len(values) == 1 and hmac.compare_digest(values[0], expected)

        def _path(self) -> str:
            parsed = urlsplit(self.path)
            if parsed.query or parsed.fragment or not parsed.path.startswith("/"):
                raise GatewayRejected("path rejected")
            return parsed.path

        def _respond(
            self, status_code: int, body: bytes, origin: str | None = None
        ) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            if origin is not None:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _read_json(self) -> dict[str, object]:
            if self.headers.get("Transfer-Encoding") is not None:
                raise GatewayRejected("streaming body rejected")
            content_lengths = self.headers.get_all("Content-Length", [])
            content_types = self.headers.get_all("Content-Type", [])
            if (
                len(content_lengths) != 1
                or not content_lengths[0].isdigit()
                or not 0 < int(content_lengths[0]) <= MAX_BODY_BYTES
                or len(content_types) != 1
                or content_types[0].split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise GatewayRejected("body rejected")
            return _load_object(self.rfile.read(int(content_lengths[0])))

        def do_OPTIONS(self) -> None:
            try:
                origin = self._origin()
                path = self._path()
                requested_method = self.headers.get("Access-Control-Request-Method")
                requested_headers = {
                    value.strip().lower()
                    for value in self.headers.get(
                        "Access-Control-Request-Headers", ""
                    ).split(",")
                    if value.strip()
                }
                allowed_headers = {"authorization", "content-type", "x-request-id"}
                if (
                    origin is None
                    or (requested_method, path) not in ALLOWED_ROUTES
                    or not requested_headers.issubset(allowed_headers)
                ):
                    raise GatewayRejected("preflight rejected")
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", requested_method)
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Authorization, Content-Type, X-Request-ID",
                )
                self.send_header("Access-Control-Max-Age", "300")
                self.send_header("Vary", "Origin")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
            except GatewayRejected:
                self._respond(403, _fixed_json("request_rejected"))

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            try:
                origin = self._origin()
                path = self._path()
                if not self._authorized():
                    self._respond(401, _fixed_json("authentication_required"), origin)
                    return
                if (method, path) not in ALLOWED_ROUTES:
                    self._respond(404, _fixed_json("route_unavailable"), origin)
                    return
                request_id = self.headers.get("X-Request-ID")
                if (
                    request_id is not None
                    and REQUEST_ID_PATTERN.fullmatch(request_id) is None
                ):
                    raise GatewayRejected("request id rejected")
                if method == "GET" and path == "/health":
                    body = json.dumps(
                        {
                            "authenticationProof": event_proof(
                                bootstrap, "worker.health", str(port)
                            ),
                            "event": "worker.health",
                            "port": port,
                            "protocolVersion": PROTOCOL_VERSION,
                            "workerKind": "python",
                            "workerVersion": WORKER_VERSION,
                        },
                        separators=(",", ":"),
                    ).encode()
                elif method == "GET":
                    body = json.dumps(
                        {
                            "capabilities": [
                                "video_composition",
                                "speech_synthesis",
                                "subtitle_transcription",
                                "web_ui",
                            ],
                            "status": "ready",
                        },
                        separators=(",", ":"),
                    ).encode()
                else:
                    value = self._read_json()
                    if set(value) != {"relativePath"}:
                        raise GatewayRejected("asset request rejected")
                    size = inspect_asset(
                        bootstrap.asset_root, value.get("relativePath")
                    )
                    body = json.dumps(
                        {"sizeBytes": size, "status": "available"},
                        separators=(",", ":"),
                    ).encode()
                self._respond(200, body, origin)
            except (TimeoutError, GatewayRejected, OSError):
                self._respond(400, _fixed_json("request_rejected"))

    return GatewayHandler


class LoopbackGateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def create_gateway(bootstrap: GatewayBootstrap) -> LoopbackGateway:
    server = LoopbackGateway(
        (HOST, 0), gateway_handler(bootstrap, 0), bind_and_activate=False
    )
    server.server_bind()
    port = int(server.server_address[1])
    server.RequestHandlerClass = gateway_handler(bootstrap, port)
    server.server_activate()
    return server
