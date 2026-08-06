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
from typing import Final, Protocol
from urllib.parse import urlsplit

from model_service_adapter import ScriptModelConfiguration, parse_script_model

HOST: Final = "127.0.0.1"
WORKER_VERSION: Final = "1.3.2"
PROTOCOL_VERSION: Final = "1.0"
BOOTSTRAP_VERSION: Final = "1"
MAX_BOOTSTRAP_BYTES: Final = 16 * 1024
MAX_BODY_BYTES: Final = 64 * 1024
MAX_ASSET_BYTES: Final = 2 * 1024 * 1024 * 1024
PREVIEW_CHUNK_BYTES: Final = 64 * 1024
MAX_RANGE_HEADER_BYTES: Final = 128
REQUEST_TIMEOUT_SECONDS: Final = 10
TOKEN_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Pexels issues opaque alphanumeric keys around 56 characters; the bounds are
# generous so a rotated key still fits, while whitespace, quoting and CJK are
# rejected before the value can reach a TOML document.
PEXELS_API_KEY_PATTERN: Final = re.compile(r"^[A-Za-z0-9]{20,120}$")
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
PREVIEW_CAPABILITY_DOMAIN: Final = b"automation-tool.material-preview-capability.v1\0"
PREVIEW_CAPABILITY_PREFIX: Final = "material-preview-v1-"
PREVIEW_ROUTE_PREFIX: Final = "/api/v1/material-previews/"
PREVIEW_CONTENT_TYPES: Final = frozenset(
    {
        "audio/aac",
        "audio/flac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "video/mp4",
        "video/ogg",
        "video/quicktime",
        "video/webm",
        "video/x-matroska",
    }
)
_BYTE_RANGE = re.compile(r"^bytes=(0|[1-9][0-9]*)-(?:(0|[1-9][0-9]*))?$")
_SUFFIX_RANGE = re.compile(r"^bytes=-(0|[1-9][0-9]*)$")


class GatewayRejected(ValueError):
    """Fixed boundary for invalid bootstrap, request, or private paths."""


@dataclass(frozen=True, repr=False)
class GatewayBootstrap:
    token_text: str
    token_bytes: bytes
    asset_root: Path
    script_model: ScriptModelConfiguration | None = None
    web_ui: bool = False
    local_editing: bool = False
    pexels_api_key: str | None = None
    montage_request: object | None = None

    def __repr__(self) -> str:
        return "GatewayBootstrap(<redacted>)"


class MaterialPreviewLease(Protocol):
    content_type: str
    size_bytes: int

    def read(self, start: int, length: int) -> bytes: ...

    def close(self) -> None: ...


class MaterialPreviewSource(Protocol):
    def open(self, material_id: uuid.UUID) -> MaterialPreviewLease: ...


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
    # Always present, null when the build carries no key: an optional field
    # would make "packaged with a key" and "packaged without" two different
    # protocols, and the two workers reading this document would drift.
    base_keys.add("pexelsApiKey")
    keys = set(value) - {"montageRequest"}
    local_editing = keys == base_keys | {"mediaTools"}
    if keys != base_keys and not local_editing:
        raise GatewayRejected("invalid bootstrap")
    montage_request = None
    if "montageRequest" in value and value.get("montageRequest") is not None:
        # One process, one surface: the montage pipeline and the WebUI both
        # own the worker's private runtime, so a bootstrap asking for both is
        # a caller bug rather than a combination to support.
        if value.get("enableWebUi") is not False or local_editing:
            raise GatewayRejected("invalid bootstrap")
        try:
            from montage_runtime import parse_montage_request

            montage_request = parse_montage_request(value.get("montageRequest"))
        except ValueError as error:
            raise GatewayRejected("invalid bootstrap") from error
    pexels_api_key = value.get("pexelsApiKey")
    if pexels_api_key is not None and (
        not isinstance(pexels_api_key, str)
        or PEXELS_API_KEY_PATTERN.fullmatch(pexels_api_key) is None
    ):
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
        pexels_api_key=pexels_api_key,
        montage_request=montage_request,
    )


def event_proof(bootstrap: GatewayBootstrap, event: str, detail: str) -> str:
    message = EVENT_DOMAIN + b"\0".join(
        value.encode()
        for value in [event, "python", PROTOCOL_VERSION, WORKER_VERSION, detail]
    )
    digest = hmac.digest(bootstrap.token_bytes, message, hashlib.sha256)
    return "atvwp1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def material_preview_capability_path(bootstrap: GatewayBootstrap) -> str:
    """Derive a preview-only capability without exposing the session bearer."""

    if not isinstance(bootstrap, GatewayBootstrap) or not bootstrap.local_editing:
        raise GatewayRejected("material preview unavailable")
    digest = hmac.digest(
        bootstrap.token_bytes, PREVIEW_CAPABILITY_DOMAIN, hashlib.sha256
    )
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PREVIEW_CAPABILITY_PREFIX + encoded


def _preview_material_id(
    bootstrap: GatewayBootstrap,
    path: str,
) -> uuid.UUID | None:
    if not path.startswith(PREVIEW_ROUTE_PREFIX):
        return None
    remainder = path.removeprefix(PREVIEW_ROUTE_PREFIX)
    parts = remainder.split("/")
    if len(parts) != 2:
        return None
    supplied_capability, supplied_material_id = parts
    try:
        expected_capability = material_preview_capability_path(bootstrap)
        material_id = uuid.UUID(supplied_material_id)
    except (GatewayRejected, ValueError):
        return None
    if (
        not hmac.compare_digest(supplied_capability, expected_capability)
        or material_id.version != 4
        or str(material_id) != supplied_material_id
    ):
        return None
    return material_id


def _requested_byte_window(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return 0, size
    if not value.isascii() or len(value) > MAX_RANGE_HEADER_BYTES:
        return None
    matched = _BYTE_RANGE.fullmatch(value)
    if matched is not None:
        start = int(matched.group(1))
        end_text = matched.group(2)
        if start >= size:
            return None
        end = size - 1 if end_text is None else int(end_text)
        if end < start:
            return None
        end = min(end, size - 1)
        return start, end - start + 1
    matched = _SUFFIX_RANGE.fullmatch(value)
    if matched is None:
        return None
    suffix = int(matched.group(1))
    if suffix == 0:
        return None
    length = min(suffix, size)
    return size - length, length


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
    bootstrap: GatewayBootstrap,
    port: int,
    material_preview: MaterialPreviewSource | None = None,
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
            if (
                parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/")
            ):
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

        def do_HEAD(self) -> None:
            self._dispatch("HEAD")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            preview_shape = urlsplit(self.path).path.startswith(PREVIEW_ROUTE_PREFIX)
            try:
                origin = self._origin()
            except GatewayRejected:
                if preview_shape:
                    self._respond_preview_empty(403)
                else:
                    self._respond(400, _fixed_json("request_rejected"))
                return
            try:
                path = self._path()
            except GatewayRejected:
                if preview_shape:
                    self._respond_preview_empty(404, origin=origin)
                else:
                    self._respond(400, _fixed_json("request_rejected"), origin)
                return
            if preview_shape:
                self._dispatch_preview(method, path, origin)
                return
            try:
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

        def _dispatch_preview(
            self,
            method: str,
            path: str,
            origin: str | None,
        ) -> None:
            material_id = _preview_material_id(bootstrap, path)
            hosts = self.headers.get_all("Host", [])
            if (
                method not in {"GET", "HEAD"}
                or material_preview is None
                or material_id is None
                or self.headers.get("Transfer-Encoding") is not None
                or self.headers.get("Content-Length") is not None
            ):
                self._respond_preview_empty(404, origin=origin)
                return
            if len(hosts) != 1 or hosts[0] != f"{HOST}:{port}":
                self._respond_preview_empty(403, origin=origin)
                return
            ranges = self.headers.get_all("Range", [])
            lease: MaterialPreviewLease | None = None
            try:
                lease = material_preview.open(material_id)
                size = lease.size_bytes
                content_type = lease.content_type
                if (
                    type(size) is not int
                    or not 0 < size <= MAX_ASSET_BYTES
                    or content_type not in PREVIEW_CONTENT_TYPES
                ):
                    self._respond_preview_empty(404, origin=origin)
                    return
                if len(ranges) > 1:
                    self._respond_preview_empty(416, size=size, origin=origin)
                    return
                supplied_range = ranges[0] if ranges else None
                window = _requested_byte_window(supplied_range, size)
                if window is None:
                    self._respond_preview_empty(
                        416,
                        size=size,
                        origin=origin,
                    )
                    return
                start, length = window
                partial = supplied_range is not None
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if partial:
                    self.send_header(
                        "Content-Range",
                        f"bytes {start}-{start + length - 1}/{size}",
                    )
                self._preview_security_headers(origin)
                self.end_headers()
                if method == "GET":
                    offset = start
                    remaining = length
                    while remaining:
                        chunk_length = min(remaining, PREVIEW_CHUNK_BYTES)
                        chunk = lease.read(offset, chunk_length)
                        if not isinstance(chunk, bytes) or len(chunk) != chunk_length:
                            raise GatewayRejected("preview read rejected")
                        self.wfile.write(chunk)
                        offset += chunk_length
                        remaining -= chunk_length
                self.close_connection = True
            except (GatewayRejected, OSError, TimeoutError, ValueError):
                if not self.wfile.closed:
                    self.close_connection = True
            except Exception:
                # The preview provider's closed exception type lives in the
                # packaged backend.  It is deliberately not rendered here:
                # exception chains can contain the private source path.
                if lease is None:
                    self._respond_preview_empty(404, origin=origin)
                else:
                    self.close_connection = True
            finally:
                if lease is not None:
                    try:
                        lease.close()
                    except Exception:
                        # A provider must not make its private file exception
                        # escape into BaseHTTPRequestHandler's traceback.
                        pass

        def _preview_security_headers(self, origin: str | None) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.send_header("Connection", "close")
            if origin is not None:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _respond_preview_empty(
            self,
            status_code: int,
            *,
            size: int | None = None,
            origin: str | None = None,
        ) -> None:
            self.send_response(status_code)
            self.send_header("Content-Length", "0")
            self.send_header("Accept-Ranges", "bytes")
            if status_code == 416 and size is not None:
                self.send_header("Content-Range", f"bytes */{size}")
            self._preview_security_headers(origin)
            self.end_headers()
            self.close_connection = True

    return GatewayHandler


class LoopbackGateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def create_gateway(
    bootstrap: GatewayBootstrap,
    *,
    material_preview: MaterialPreviewSource | None = None,
) -> LoopbackGateway:
    if bootstrap.local_editing != (material_preview is not None):
        raise GatewayRejected("material preview configuration rejected")
    server = LoopbackGateway(
        (HOST, 0),
        gateway_handler(bootstrap, 0, material_preview),
        bind_and_activate=False,
    )
    server.server_bind()
    port = int(server.server_address[1])
    server.RequestHandlerClass = gateway_handler(bootstrap, port, material_preview)
    server.server_activate()
    return server
