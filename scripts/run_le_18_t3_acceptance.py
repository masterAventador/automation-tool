#!/usr/bin/env python3
"""LE-18 T3 acceptance: real frozen Worker import and compensation."""

from __future__ import annotations

import argparse
import atexit
import base64
import hashlib
import hmac
import http.client
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TextIO
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

from automation_tool.executor.local_editing_worker import (  # noqa: E402
    LocalMaterialWorkerFailureCode,
    LocalMaterialWorkerStatus,
)
from automation_tool.executor.material_probe import (  # noqa: E402
    MaterialPathRegistry,
    MaterialPathRegistryRejected,
    MaterialPathRegistryRejection,
    MaterialProbeRejected,
    MaterialProbeRejection,
    approve_source,
    require_source_unchanged,
)
from prepare_video_runtime import prepare  # noqa: E402

COMMAND_DOMAIN = b"automation-tool.video-worker-command.v1\0"
EVENT_DOMAIN = b"automation-tool.video-worker-event.v1\0"
TIMEOUT_SECONDS = 90
SOURCE_SETTLEMENT_ATTEMPTS = 10
SOURCE_SETTLEMENT_INTERVAL_SECONDS = 1.0
_FAILURE_EVENT_FOR = {
    "worker.material.imported": "worker.material.import_failed",
    "worker.material.forgotten": "worker.material.forget_failed",
}
_PREVIEW_PATH = re.compile(r"^material-preview-v1-[A-Za-z0-9_-]{43}$")


def _proof(token: bytes, domain: bytes, prefix: str, parts: tuple[str, ...]) -> str:
    message = domain + b"\0".join(part.encode() for part in parts)
    digest = hmac.digest(token, message, hashlib.sha256)
    return prefix + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _command(
    token: bytes,
    name: str,
    material_id: UUID,
    source: Path | None = None,
) -> dict[str, object]:
    parts = (name, "python", "1.0", str(material_id))
    document: dict[str, object] = {
        "authenticationProof": _proof(
            token,
            COMMAND_DOMAIN,
            "atvwc1.",
            parts if source is None else (*parts, str(source)),
        ),
        "command": name,
        "materialId": str(material_id),
        "protocolVersion": "1.0",
        "workerKind": "python",
    }
    if source is not None:
        document["sourcePath"] = str(source)
    return document


def _pump(stream: TextIO, sink: queue.Queue[str]) -> None:
    for line in stream:
        sink.put(line)


def _next_event(
    lines: queue.Queue[str],
    errors: queue.Queue[str],
    process: subprocess.Popen[str],
    expected: str,
    transcript: list[str],
    source: Path,
) -> dict[str, object]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {expected}")
        try:
            line = lines.get(timeout=min(0.1, remaining))
        except queue.Empty:
            if process.poll() is None:
                continue
            diagnostics: list[str] = []
            while not errors.empty():
                diagnostics.append(errors.get_nowait())
            closed = "".join(diagnostics).strip()
            if str(source) in closed:
                raise AssertionError(
                    "frozen Worker stderr leaked the selected source path"
                )
            raise AssertionError(
                f"frozen Worker exited before {expected}: {closed or '(no diagnostic)'}"
            )
        transcript.append(line)
        if str(source) in line:
            raise AssertionError("frozen Worker event leaked the selected source path")
        document = json.loads(line)
        if not isinstance(document, dict):
            raise AssertionError("Worker emitted a non-object event")
        event = document.get("event")
        if event == expected:
            return document
        if event == _FAILURE_EVENT_FOR.get(expected):
            failure = document.get("failureCode")
            if not isinstance(failure, str):
                raise AssertionError(
                    f"frozen Worker reported an invalid {event}"
                ) from None
            try:
                closed_failure = LocalMaterialWorkerFailureCode(failure)
            except ValueError:
                raise AssertionError(
                    f"frozen Worker reported an invalid {event}"
                ) from None
            raise AssertionError(
                f"frozen Worker reported {event}: {closed_failure.value}"
            )


def _verify_event(event: dict[str, object], token: bytes, detail: str) -> None:
    name = event.get("event")
    worker_version = event.get("workerVersion")
    if not isinstance(name, str) or not isinstance(worker_version, str):
        raise AssertionError("Worker event identity is incomplete")
    expected = _proof(
        token,
        EVENT_DOMAIN,
        "atvwp1.",
        (name, "python", "1.0", worker_version, detail),
    )
    if event.get("authenticationProof") != expected:
        raise AssertionError(f"{name} authentication proof is invalid")


def _verify_status_event(
    event: dict[str, object],
    token: bytes,
    material_id: UUID,
    expected_status: str,
) -> None:
    """Verify a path-free, authenticated status without echoing rejected values."""

    status = event.get("status")
    if (
        event.get("event") != "worker.material.status"
        or event.get("workerKind") != "python"
        or event.get("protocolVersion") != "1.0"
        or event.get("materialId") != str(material_id)
        or not isinstance(status, str)
    ):
        raise AssertionError("invalid material status")
    try:
        closed_status = LocalMaterialWorkerStatus(status)
    except ValueError:
        raise AssertionError("invalid material status") from None
    if closed_status.value != expected_status:
        raise AssertionError("unexpected material status")
    _verify_event(event, token, f"{material_id}\0{closed_status.value}")


def _verify_preview_ready(event: dict[str, object], token: bytes) -> tuple[int, str]:
    port = event.get("port")
    path = event.get("materialPreviewPath")
    proof = event.get("materialPreviewAuthenticationProof")
    worker_version = event.get("workerVersion")
    if (
        type(port) is not int
        or not 1 <= port <= 65535
        or not isinstance(path, str)
        or _PREVIEW_PATH.fullmatch(path) is None
        or not isinstance(proof, str)
        or not isinstance(worker_version, str)
    ):
        raise AssertionError("invalid material preview endpoint")
    _verify_event(event, token, str(port))
    expected = _proof(
        token,
        EVENT_DOMAIN,
        "atvwp1.",
        (
            "worker.material_preview_ready",
            "python",
            "1.0",
            worker_version,
            f"{port}:{path}",
        ),
    )
    if not hmac.compare_digest(proof, expected):
        raise AssertionError("invalid material preview endpoint")
    return port, path


def _preview_request(
    port: int,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    result = (
        response.status,
        {name.lower(): value for name, value in response.getheaders()},
        body,
    )
    connection.close()
    return result


def _generate_source(ffmpeg: Path, source: Path) -> None:
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=20:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
    )


def _wait_for_generated_source(source: Path) -> None:
    """Wait until Windows has committed the fixture's final-write metadata."""

    for _ in range(SOURCE_SETTLEMENT_ATTEMPTS):
        try:
            path, approved = approve_source(source)
            time.sleep(SOURCE_SETTLEMENT_INTERVAL_SECONDS)
            require_source_unchanged(path, approved)
        except MaterialProbeRejected as error:
            if error.rejection is MaterialProbeRejection.SOURCE_NOT_AT_REST:
                continue
            raise AssertionError("generated acceptance source is unusable") from None
        return
    raise AssertionError("generated acceptance source did not settle")


def _stop_process(process: subprocess.Popen[str]) -> None:
    """Bound cleanup even when an acceptance assertion interrupts the dialogue."""

    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--ffprobe", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    supplied = (arguments.candidate, arguments.ffmpeg, arguments.ffprobe)
    if any(value is not None for value in supplied) and not all(
        value is not None for value in supplied
    ):
        raise AssertionError("candidate, ffmpeg and ffprobe must be supplied together")
    if not all(value is not None for value in supplied):
        staging = prepare(only=("media-toolchain", "material-video-worker"))
        candidate = staging / "material-video-worker"
        media = staging / "media-toolchain" / "bin"
        ffmpeg = media / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        ffprobe = media / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    else:
        assert arguments.candidate is not None
        assert arguments.ffmpeg is not None
        assert arguments.ffprobe is not None
        candidate = arguments.candidate.resolve(strict=True)
        ffmpeg = arguments.ffmpeg.resolve(strict=True)
        ffprobe = arguments.ffprobe.resolve(strict=True)
    executable = candidate / (
        "automation-tool-material-video-worker.exe"
        if os.name == "nt"
        else "automation-tool-material-video-worker"
    )
    if not all(path.is_file() for path in (executable, ffmpeg, ffprobe)):
        raise AssertionError("LE-18 T3 frozen runtime is incomplete")

    with tempfile.TemporaryDirectory(prefix="le18-t3-") as directory:
        root = Path(directory)
        app_data = root / "app-data"
        app_data.mkdir(mode=0o700)
        if os.name != "nt":
            app_data.chmod(0o700)
        source = (root / "operator-private-source.mp4").resolve()
        _generate_source(ffmpeg, source)
        _wait_for_generated_source(source)
        material_id = uuid4()
        token = os.urandom(32)
        bootstrap = {
            "assetRoot": str(app_data.resolve()),
            "bootstrapVersion": "1",
            "enableWebUi": False,
            "localSessionToken": token.hex(),
            "mediaTools": {
                "ffmpegPath": str(ffmpeg.resolve()),
                "ffprobePath": str(ffprobe.resolve()),
            },
            # 共享协议自 2026-08-05 起该键始终在场（无密钥构建为 null）；
            # 材料网关做精确形状校验，缺键的引导会被直接拒绝。
            "pexelsApiKey": None,
            "protocolVersion": "1.0",
            "renderBrowser": None,
            "scriptModel": None,
            "workerKind": "python",
        }
        process = subprocess.Popen(
            [str(executable)],
            cwd=candidate,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        atexit.register(_stop_process, process)
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_lines: queue.Queue[str] = queue.Queue()
        stderr_lines: queue.Queue[str] = queue.Queue()
        stdout_thread = threading.Thread(
            target=_pump, args=(process.stdout, stdout_lines), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_pump, args=(process.stderr, stderr_lines), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        transcript: list[str] = []

        process.stdin.write(json.dumps(bootstrap, separators=(",", ":")) + "\n")
        process.stdin.flush()
        ready = _next_event(
            stdout_lines,
            stderr_lines,
            process,
            "worker.ready",
            transcript,
            source,
        )
        preview_port, preview_capability = _verify_preview_ready(ready, token)

        process.stdin.write(
            json.dumps(
                _command(token, "worker.material.import", material_id, source),
                separators=(",", ":"),
            )
            + "\n"
        )
        process.stdin.flush()
        imported = _next_event(
            stdout_lines,
            stderr_lines,
            process,
            "worker.material.imported",
            transcript,
            source,
        )
        facts = imported.get("facts")
        if not isinstance(facts, dict):
            raise AssertionError("imported event has no material facts")
        canonical = json.dumps(
            facts, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        _verify_event(imported, token, f"{material_id}\0{canonical}")
        if set(facts) != {
            "audioLoudnessLufs",
            "contentDigest",
            "durationMs",
            "hasAudio",
            "height",
            "kind",
            "width",
        }:
            raise AssertionError(f"unexpected real probe fact keys: {facts!r}")
        expected = {
            "contentDigest": hashlib.sha256(source.read_bytes()).hexdigest(),
            "durationMs": 1000,
            "hasAudio": True,
            "height": 240,
            "kind": "video",
            "width": 320,
        }
        if {key: facts[key] for key in expected} != expected:
            raise AssertionError(f"unexpected real probe facts: {facts!r}")
        loudness = facts["audioLoudnessLufs"]
        if not isinstance(loudness, float) or not -70.0 <= loudness <= 0.0:
            raise AssertionError("real audio loudness is invalid")
        state = app_data / "local-executor" / "state"
        resolved, _ = MaterialPathRegistry(state_directory=state).resolve(material_id)
        if resolved != source:
            raise AssertionError("frozen Worker did not persist the selected mapping")

        process.stdin.write(
            json.dumps(
                _command(token, "worker.material.status", material_id),
                separators=(",", ":"),
            )
            + "\n"
        )
        process.stdin.flush()
        available = _next_event(
            stdout_lines,
            stderr_lines,
            process,
            "worker.material.status",
            transcript,
            source,
        )
        _verify_status_event(available, token, material_id, "available")

        preview_path = f"/api/v1/material-previews/{preview_capability}/{material_id}"
        status, headers, body = _preview_request(
            preview_port,
            preview_path,
            headers={"Origin": "tauri://localhost"},
        )
        if status != 200 or body != source.read_bytes():
            raise AssertionError("frozen Worker full preview is invalid")
        if (
            headers.get("content-type") != "video/mp4"
            or headers.get("accept-ranges") != "bytes"
            or headers.get("cache-control") != "no-store"
        ):
            raise AssertionError("frozen Worker preview headers are invalid")
        status, headers, body = _preview_request(
            preview_port,
            preview_path,
            headers={"Origin": "tauri://localhost", "Range": "bytes=4-31"},
        )
        if (
            status != 206
            or body != source.read_bytes()[4:32]
            or headers.get("content-range") != f"bytes 4-31/{source.stat().st_size}"
        ):
            raise AssertionError("frozen Worker byte range is invalid")
        status, _, body = _preview_request(
            preview_port,
            preview_path,
            headers={"Origin": "https://evil.example"},
        )
        if status != 403 or body:
            raise AssertionError("frozen Worker preview accepted an external origin")

        process.stdin.write(
            json.dumps(
                _command(token, "worker.material.forget", material_id),
                separators=(",", ":"),
            )
            + "\n"
        )
        process.stdin.flush()
        forgotten = _next_event(
            stdout_lines,
            stderr_lines,
            process,
            "worker.material.forgotten",
            transcript,
            source,
        )
        _verify_event(forgotten, token, str(material_id))
        try:
            MaterialPathRegistry(state_directory=state).resolve(material_id)
        except MaterialPathRegistryRejected as error:
            if error.rejection is not MaterialPathRegistryRejection.NOT_REGISTERED:
                raise
        else:
            raise AssertionError("explicit compensation left the mapping registered")

        process.stdin.write(
            json.dumps(
                _command(token, "worker.material.status", material_id),
                separators=(",", ":"),
            )
            + "\n"
        )
        process.stdin.flush()
        not_registered = _next_event(
            stdout_lines,
            stderr_lines,
            process,
            "worker.material.status",
            transcript,
            source,
        )
        _verify_status_event(not_registered, token, material_id, "not_registered")
        status, _, body = _preview_request(preview_port, preview_path)
        if status != 404 or body:
            raise AssertionError("forgotten material remained previewable")

        process.stdin.close()
        return_code = process.wait(timeout=TIMEOUT_SECONDS)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        while not stdout_lines.empty():
            transcript.append(stdout_lines.get_nowait())
        while not stderr_lines.empty():
            transcript.append(stderr_lines.get_nowait())
        if return_code != 0:
            raise AssertionError(f"frozen Worker exited {return_code}")
        if str(source) in "".join(transcript):
            raise AssertionError("frozen Worker output leaked the selected source path")
        atexit.unregister(_stop_process)

    print("LE-18 T3 frozen Worker import/compensation acceptance passed")
    print("LE-18 T4 frozen Worker material-status acceptance passed")
    print("LE-18 T5 frozen Worker material Range preview acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
