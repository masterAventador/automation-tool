#!/usr/bin/env python3
"""Minimal process boundary for the isolated material-video runtime."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import re
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Final, TextIO

from gateway import (
    MAX_BOOTSTRAP_BYTES,
    PROTOCOL_VERSION,
    WORKER_VERSION,
    GatewayRejected,
    create_gateway,
    event_proof,
    parse_bootstrap,
    parse_cancel_command,
)
from model_service_adapter import install_script_model
from webui_runtime import WebUiRejected, serve_webui, start_webui

if TYPE_CHECKING:
    from automation_tool.executor.local_editing_worker import (
        LocalEditingStartCommand,
        LocalMaterialForgetCommand,
        LocalMaterialImportCommand,
    )

RUNTIME_PROBE_PROTOCOL_VERSION: Final = 1
SAFE_MODULE_NAME: Final = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
RUNTIME_MODULES: Final[dict[str, str | None]] = {
    "moviepy": "moviepy",
    "streamlit": "streamlit",
    "streamlit-tour": None,
    "edge-tts": "edge_tts",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "openai": None,
    "faster-whisper": None,
    "dashscope": None,
    "azure-cognitiveservices-speech": None,
    "python-multipart": None,
    "pydub": "pydub",
    "litellm": None,
    "google-genai": None,
    "brotli": "brotli",
    "fonttools": "fontTools",
}


def runtime_probe() -> dict[str, object]:
    # Probe the production gateway's cold import before the upstream App warms
    # the frozen import graph. The ordinary stdin path imports this runtime
    # first, so reversing the order here can let a broken package pass its own
    # build audit and then reject every local-editing bootstrap.
    dependency_probe("local-editing-runtime")
    importlib.import_module("app")
    versions: dict[str, str] = {}
    for distribution, module in RUNTIME_MODULES.items():
        if module is not None:
            importlib.import_module(module)
        versions[distribution] = importlib.metadata.version(distribution)
    return {
        "protocolVersion": RUNTIME_PROBE_PROTOCOL_VERSION,
        "status": "ready",
        "python": (
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        ),
        "dependencies": versions,
        "capabilities": [
            "video_composition",
            "speech_synthesis",
            "subtitle_transcription",
            "web_ui",
        ],
    }


def dependency_probe(name: str) -> dict[str, object]:
    if name == "upstream-app":
        importlib.import_module("app")
        return {"dependency": name, "status": "ready"}
    if name == "local-editing-runtime":
        importlib.import_module("automation_tool.executor.local_editing_worker")
        importlib.import_module("automation_tool.executor.local_editing_worker_process")
        return {"dependency": name, "status": "ready"}
    module = RUNTIME_MODULES.get(name)
    if module is None:
        raise ValueError("dependency is not part of the startup set")
    importlib.import_module(module)
    return {"dependency": name, "status": "ready"}


def _report_local_editing_rejection(
    error: object, output: TextIO | None = None
) -> None:
    """Emit one closed operational reason without paths, exceptions or user data."""
    from automation_tool.executor.local_editing_worker_process import (
        LocalEditingRenderRejected,
    )

    if not isinstance(error, LocalEditingRenderRejected):
        return
    sink = sys.stderr if output is None else output
    print(
        f"Material video worker local editing rejected: {error.diagnostic.value}",
        file=sink,
        flush=True,
    )


def _report_local_material_rejection(
    error: object, output: TextIO | None = None
) -> None:
    """Emit one closed material reason without the selected source path."""
    from automation_tool.executor.local_editing_worker_process import (
        LocalMaterialOperationRejected,
    )

    if not isinstance(error, LocalMaterialOperationRejected):
        return
    sink = sys.stderr if output is None else output
    print(
        f"Material video worker material operation rejected: {error.code.value}",
        file=sink,
        flush=True,
    )


def _gateway_process(stream: TextIO, output: TextIO | None = None) -> int:
    sink = sys.stdout if output is None else output
    line = stream.readline(MAX_BOOTSTRAP_BYTES + 1)
    if not line:
        print("Material video worker command is required", file=sys.stderr)
        return 64
    webui = None
    try:
        from automation_tool.executor.local_editing_worker import (
            LocalEditingCancelCommand,
            LocalEditingStartCommand,
            LocalEditingWorkerFailureCode,
            LocalEditingWorkerPhase,
            LocalEditingWorkerProtocol,
            LocalMaterialForgetCommand,
            LocalMaterialImportCommand,
            LocalMaterialWorkerFailureCode,
            parse_local_editing_worker_bootstrap,
        )
        from automation_tool.executor.local_editing_worker_process import (
            LocalEditingRenderCancelled,
            LocalEditingRenderRejected,
            LocalMaterialOperationRejected,
            execute_local_editing_job,
            execute_local_material_forget,
            execute_local_material_import,
        )

        bootstrap_line = line.encode()
        bootstrap = parse_bootstrap(bootstrap_line)
        editing_bootstrap = (
            parse_local_editing_worker_bootstrap(bootstrap_line)
            if bootstrap.local_editing
            else None
        )
        editing_protocol = (
            LocalEditingWorkerProtocol(editing_bootstrap, WORKER_VERSION)
            if editing_bootstrap is not None
            else None
        )
        editing_protocol_lock = threading.Lock()
        script_model_id = (
            install_script_model(bootstrap.script_model)
            if bootstrap.script_model is not None
            else None
        )
        webui = (
            start_webui(bootstrap.asset_root, bootstrap.script_model)
            if bootstrap.web_ui
            else None
        )
        server = create_gateway(bootstrap)
    except Exception:
        if webui is not None:
            webui.stop()
        print("Material video worker bootstrap is rejected", file=sys.stderr)
        return 65
    port = int(server.server_address[1])
    thread = threading.Thread(
        target=server.serve_forever, name="material-video-gateway"
    )
    thread.start()
    ready = {
        "authenticationProof": event_proof(bootstrap, "worker.ready", str(port)),
        "event": "worker.ready",
        "port": port,
        "protocolVersion": PROTOCOL_VERSION,
        "scriptModelId": script_model_id,
        "webUiAuthenticationProof": (
            event_proof(
                bootstrap,
                "worker.web_ui_ready",
                f"{webui.endpoint.port}:{webui.endpoint.path}",
            )
            if webui is not None
            else None
        ),
        "webUiPath": webui.endpoint.path if webui is not None else None,
        "webUiPort": webui.endpoint.port if webui is not None else None,
        "workerKind": "python",
        "workerVersion": WORKER_VERSION,
    }
    output_lock = threading.Lock()

    def emit(payload: bytes | dict[str, object]) -> None:
        line = (
            payload.decode("utf-8").rstrip("\n")
            if isinstance(payload, bytes)
            else json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
        with output_lock:
            print(line, file=sink, flush=True)

    emit(ready)
    render_thread: threading.Thread | None = None
    material_thread: threading.Thread | None = None
    cancel_requested = threading.Event()

    def render(command: LocalEditingStartCommand) -> None:
        if editing_bootstrap is None or editing_protocol is None:
            return
        try:
            with editing_protocol_lock:
                preparing = editing_protocol.progress(
                    command.job_id, LocalEditingWorkerPhase.PREPARING, 0
                )
            emit(preparing)
            with editing_protocol_lock:
                rendering = editing_protocol.progress(
                    command.job_id, LocalEditingWorkerPhase.RENDERING, 800
                )
            emit(rendering)
            artifact_id = execute_local_editing_job(
                editing_bootstrap,
                command,
                cancel_requested=cancel_requested.is_set,
            )
            with editing_protocol_lock:
                publishing = editing_protocol.progress(
                    command.job_id, LocalEditingWorkerPhase.PUBLISHING, 1000
                )
                succeeded = editing_protocol.succeed(command.job_id, artifact_id)
            emit(publishing)
            emit(succeeded)
        except LocalEditingRenderCancelled:
            with suppress(Exception):
                with editing_protocol_lock:
                    cancelled = editing_protocol.cancelled(command.job_id)
                emit(cancelled)
        except LocalEditingRenderRejected as error:
            _report_local_editing_rejection(error)
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.fail(command.job_id, error.code)
                emit(failed)
        except Exception:
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.fail(
                        command.job_id, LocalEditingWorkerFailureCode.RENDER_FAILED
                    )
                emit(failed)

    def import_material(command: LocalMaterialImportCommand) -> None:
        if editing_bootstrap is None or editing_protocol is None:
            return
        try:
            facts = execute_local_material_import(editing_bootstrap, command)
            with editing_protocol_lock:
                imported = editing_protocol.material_imported(
                    command.material_id, facts
                )
            emit(imported)
        except LocalMaterialOperationRejected as error:
            _report_local_material_rejection(error)
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.material_import_failed(
                        command.material_id, error.code
                    )
                emit(failed)
        except Exception:
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.material_import_failed(
                        command.material_id,
                        LocalMaterialWorkerFailureCode.PROBE_FAILED,
                    )
                emit(failed)

    def forget_material(command: LocalMaterialForgetCommand) -> None:
        if editing_bootstrap is None or editing_protocol is None:
            return
        try:
            execute_local_material_forget(editing_bootstrap, command)
            with editing_protocol_lock:
                forgotten = editing_protocol.material_forgotten(command.material_id)
            emit(forgotten)
        except LocalMaterialOperationRejected as error:
            _report_local_material_rejection(error)
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.material_forget_failed(
                        command.material_id, error.code
                    )
                emit(failed)
        except Exception:
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.material_forget_failed(
                        command.material_id,
                        LocalMaterialWorkerFailureCode.REGISTRY_UNWRITABLE,
                    )
                emit(failed)

    try:
        for command_line in stream:
            if editing_protocol is None:
                try:
                    job_id = parse_cancel_command(bootstrap, command_line.encode())
                except (GatewayRejected, UnicodeEncodeError):
                    continue
                emit(
                    {
                        "authenticationProof": event_proof(
                            bootstrap, "worker.cancelled", job_id
                        ),
                        "event": "worker.cancelled",
                        "jobId": job_id,
                        "protocolVersion": PROTOCOL_VERSION,
                        "workerKind": "python",
                        "workerVersion": WORKER_VERSION,
                    }
                )
                continue
            try:
                with editing_protocol_lock:
                    command = editing_protocol.accept_command(command_line.encode())
            except Exception:
                # Preserve the material-studio cancellation vocabulary for a
                # WebUI-only session that has no active editing job.
                try:
                    job_id = parse_cancel_command(bootstrap, command_line.encode())
                except (GatewayRejected, UnicodeEncodeError):
                    continue
                cancelled: dict[str, object] = {
                    "authenticationProof": event_proof(
                        bootstrap, "worker.cancelled", job_id
                    ),
                    "event": "worker.cancelled",
                    "jobId": job_id,
                    "protocolVersion": PROTOCOL_VERSION,
                    "workerKind": "python",
                    "workerVersion": WORKER_VERSION,
                }
                emit(cancelled)
                continue
            if isinstance(command, LocalEditingStartCommand):
                cancel_requested.clear()
                render_thread = threading.Thread(
                    target=render,
                    args=(command,),
                    name="local-editing-render",
                )
                render_thread.start()
            elif isinstance(command, LocalEditingCancelCommand):
                cancel_requested.set()
            elif isinstance(command, LocalMaterialImportCommand):
                material_thread = threading.Thread(
                    target=import_material,
                    args=(command,),
                    name="local-material-import",
                )
                material_thread.start()
            elif isinstance(command, LocalMaterialForgetCommand):
                material_thread = threading.Thread(
                    target=forget_material,
                    args=(command,),
                    name="local-material-forget",
                )
                material_thread.start()
    finally:
        cancel_requested.set()
        if render_thread is not None:
            render_thread.join(timeout=REQUEST_SHUTDOWN_TIMEOUT_SECONDS)
        if material_thread is not None:
            material_thread.join(timeout=REQUEST_SHUTDOWN_TIMEOUT_SECONDS)
        server.shutdown()
        server.server_close()
        thread.join(timeout=REQUEST_SHUTDOWN_TIMEOUT_SECONDS)
        if webui is not None:
            webui.stop()
    return 0


REQUEST_SHUTDOWN_TIMEOUT_SECONDS: Final = 5


def main(
    arguments: list[str] | None = None, bootstrap_stream: TextIO | None = None
) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if len(values) == 5 and values[0] == "--serve-webui":
        try:
            return serve_webui(
                int(values[1]),
                values[2],
                Path(values[3]),
                Path(values[4]),
                sys.stdin if bootstrap_stream is None else bootstrap_stream,
            )
        except (ValueError, OSError, WebUiRejected):
            print("Material video WebUI is unavailable", file=sys.stderr)
            return 70
    if len(values) == 2 and values[0] == "--probe-dependency":
        try:
            payload = dependency_probe(values[1])
        except ModuleNotFoundError as error:
            missing = error.name or "unknown"
            if SAFE_MODULE_NAME.fullmatch(missing) is None:
                missing = "unknown"
            print(
                json.dumps(
                    {
                        "dependency": values[1],
                        "missingModule": missing,
                        "status": "unavailable",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 70
        except Exception as error:
            failure_type = type(error).__name__
            if SAFE_MODULE_NAME.fullmatch(failure_type) is None:
                failure_type = "unknown"
            print(
                json.dumps(
                    {
                        "dependency": values[1],
                        "failureType": failure_type,
                        "status": "unavailable",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 70
        print(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        return 0
    if values != ["--probe"]:
        if not values:
            return _gateway_process(
                sys.stdin if bootstrap_stream is None else bootstrap_stream
            )
        print("Material video worker command is required", file=sys.stderr)
        return 64
    try:
        payload = runtime_probe()
    except Exception:
        print("Material video worker startup is unavailable", file=sys.stderr)
        return 70
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
