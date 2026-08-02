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
    material_preview_capability_path,
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
        LocalSmartEditStartCommand,
    )
    from automation_tool.executor.smart_edit_generation import SmartEditGenerationStage

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
    "numpy": "numpy",
    "onnxruntime": "onnxruntime",
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
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "dependencies": versions,
        "capabilities": [
            "video_composition",
            "speech_synthesis",
            "subtitle_transcription",
            "smart_edit_generation",
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
        importlib.import_module("automation_tool.executor.local_material_preview")
        importlib.import_module("automation_tool.executor.smart_edit_worker_process")
        return {"dependency": name, "status": "ready"}
    module = RUNTIME_MODULES.get(name)
    if module is None:
        raise ValueError("dependency is not part of the startup set")
    importlib.import_module(module)
    return {"dependency": name, "status": "ready"}


def smart_edit_runtime_probe() -> None:
    """Audit the frozen model, license and native CPU runtime in place."""

    from automation_tool.executor.silero_vad import (
        audit_packaged_silero_vad_runtime,
    )

    audit_packaged_silero_vad_runtime(Path(sys.executable).resolve().parent)


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
            LocalMaterialStatusCommand,
            LocalMaterialWorkerFailureCode,
            LocalMaterialWorkerStatus,
            LocalSmartEditAbortCommand,
            LocalSmartEditCommitCommand,
            LocalSmartEditFailureCode,
            LocalSmartEditStartCommand,
            parse_local_editing_worker_bootstrap,
        )
        from automation_tool.executor.local_editing_worker_process import (
            LocalEditingRenderCancelled,
            LocalEditingRenderRejected,
            LocalMaterialOperationRejected,
            execute_local_editing_job,
            execute_local_material_forget,
            execute_local_material_import,
            execute_local_material_status,
        )
        from automation_tool.executor.local_material_preview import (
            LocalMaterialPreviewSource,
        )
        from automation_tool.executor.smart_edit_worker_process import (
            LocalSmartEditStagedJob,
            LocalSmartEditWorkerCancelled,
            LocalSmartEditWorkerRejected,
            abort_smart_edit_job,
            commit_smart_edit_job,
            create_local_smart_edit_pipeline,
            prepare_smart_edit_job,
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
        material_preview = (
            LocalMaterialPreviewSource(
                state_directory=(
                    editing_bootstrap.asset_root / "local-executor" / "state"
                ),
                media_tools=editing_bootstrap.media_tools,
            )
            if editing_bootstrap is not None
            else None
        )
        server = create_gateway(bootstrap, material_preview=material_preview)
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
    material_preview_path = (
        material_preview_capability_path(bootstrap)
        if material_preview is not None
        else None
    )
    ready: dict[str, object] = {
        "authenticationProof": event_proof(bootstrap, "worker.ready", str(port)),
        "event": "worker.ready",
        "materialPreviewAuthenticationProof": (
            event_proof(
                bootstrap,
                "worker.material_preview_ready",
                f"{port}:{material_preview_path}",
            )
            if material_preview_path is not None
            else None
        ),
        "materialPreviewPath": material_preview_path,
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
    smart_state_lock = threading.Lock()
    smart_staged: LocalSmartEditStagedJob | None = None
    smart_commit_timer: threading.Timer | None = None
    smart_cleanup_pending: list[LocalSmartEditStagedJob] = []

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
                        command.job_id,
                        LocalEditingWorkerFailureCode.RENDER_FAILED,
                    )
                emit(failed)

    def smart_edit(command: LocalSmartEditStartCommand) -> None:
        nonlocal smart_commit_timer, smart_staged
        if editing_bootstrap is None or editing_protocol is None:
            return
        prepared_job: LocalSmartEditStagedJob | None = None

        def report_progress(stage: SmartEditGenerationStage, value: int) -> None:
            with editing_protocol_lock:
                payload = editing_protocol.smart_edit_progress(
                    command.job_id,
                    stage,
                    value,
                )
            emit(payload)

        try:
            staged = prepare_smart_edit_job(
                editing_bootstrap,
                command,
                pipeline_factory=lambda workspace: create_local_smart_edit_pipeline(
                    editing_bootstrap,
                    workspace,
                ),
                cancel_requested=cancel_requested.is_set,
                progress=report_progress,
            )
            prepared_job = staged
            if cancel_requested.is_set():
                abort_smart_edit_job(staged)
                with editing_protocol_lock:
                    cancelled = editing_protocol.smart_edit_cancelled(command.job_id)
                emit(cancelled)
                return
            with smart_state_lock:
                if smart_staged is not None:
                    raise RuntimeError("smart edit staging slot unavailable")
                smart_staged = staged
                with editing_protocol_lock:
                    prepared = editing_protocol.smart_edit_prepared(
                        command.job_id,
                        staged.result_digest,
                    )
                timer = threading.Timer(
                    SMART_EDIT_COMMIT_TIMEOUT_SECONDS,
                    expire_smart_edit,
                    args=(command.job_id,),
                )
                timer.daemon = True
                smart_commit_timer = timer
                timer.start()
            emit(prepared)
        except LocalSmartEditWorkerCancelled:
            with suppress(Exception):
                with editing_protocol_lock:
                    cancelled = editing_protocol.smart_edit_cancelled(command.job_id)
                emit(cancelled)
        except LocalSmartEditWorkerRejected as error:
            if prepared_job is not None and not prepared_job.finalized:
                try:
                    abort_smart_edit_job(prepared_job)
                except Exception:
                    smart_cleanup_pending.append(prepared_job)
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.smart_edit_failed(
                        command.job_id, error.code
                    )
                emit(failed)
        except Exception:
            with smart_state_lock:
                if smart_commit_timer is not None:
                    smart_commit_timer.cancel()
                    smart_commit_timer = None
                published = smart_staged
                if published is not None and published.job_id == command.job_id:
                    if not published.finalized:
                        try:
                            abort_smart_edit_job(published)
                        except Exception:
                            smart_cleanup_pending.append(published)
                    smart_staged = None
                elif prepared_job is not None and not prepared_job.finalized:
                    try:
                        abort_smart_edit_job(prepared_job)
                    except Exception:
                        smart_cleanup_pending.append(prepared_job)
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.smart_edit_failed(
                        command.job_id,
                        LocalSmartEditFailureCode.LOCAL_FAILED,
                    )
                emit(failed)

    def expire_smart_edit(job_id: object) -> None:
        nonlocal smart_commit_timer, smart_staged
        failed: bytes | None = None
        with smart_state_lock:
            staged = smart_staged
            if staged is None or staged.job_id != job_id:
                return
            smart_commit_timer = None
            try:
                abort_smart_edit_job(staged)
            except Exception:
                smart_cleanup_pending.append(staged)
            smart_staged = None
            with suppress(Exception):
                with editing_protocol_lock:
                    failed = editing_protocol.smart_edit_failed(
                        staged.job_id,
                        LocalSmartEditFailureCode.COMMIT_FAILED,
                    )
        if failed is not None:
            emit(failed)

    def finalize_smart_edit(
        command: LocalSmartEditCommitCommand | LocalSmartEditAbortCommand,
    ) -> None:
        nonlocal smart_commit_timer, smart_staged
        if editing_bootstrap is None or editing_protocol is None:
            return
        terminal: bytes | None = None
        with smart_state_lock:
            staged = smart_staged
            if staged is None or staged.job_id != command.job_id:
                return
            if smart_commit_timer is not None:
                smart_commit_timer.cancel()
                smart_commit_timer = None
            if isinstance(command, LocalSmartEditAbortCommand):
                try:
                    abort_smart_edit_job(staged)
                    with editing_protocol_lock:
                        terminal = editing_protocol.smart_edit_aborted(command.job_id)
                except Exception:
                    if not staged.finalized:
                        smart_cleanup_pending.append(staged)
                    with suppress(Exception):
                        with editing_protocol_lock:
                            terminal = editing_protocol.smart_edit_failed(
                                command.job_id,
                                LocalSmartEditFailureCode.LOCAL_FAILED,
                            )
                smart_staged = None
            else:
                try:
                    commit_smart_edit_job(editing_bootstrap, staged)
                    with editing_protocol_lock:
                        terminal = editing_protocol.smart_edit_succeeded(
                            command.job_id,
                            staged.result_digest,
                        )
                except Exception:
                    if not staged.finalized:
                        try:
                            abort_smart_edit_job(staged)
                        except Exception:
                            smart_cleanup_pending.append(staged)
                    with suppress(Exception):
                        with editing_protocol_lock:
                            terminal = editing_protocol.smart_edit_failed(
                                command.job_id,
                                LocalSmartEditFailureCode.COMMIT_FAILED,
                            )
                smart_staged = None
        if terminal is not None:
            emit(terminal)

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

    def material_status(command: LocalMaterialStatusCommand) -> None:
        if editing_bootstrap is None or editing_protocol is None:
            return
        try:
            status = execute_local_material_status(editing_bootstrap, command)
            with editing_protocol_lock:
                event = editing_protocol.material_status(command.material_id, status)
            emit(event)
        except Exception:
            with suppress(Exception):
                with editing_protocol_lock:
                    event = editing_protocol.material_status(
                        command.material_id,
                        LocalMaterialWorkerStatus.REGISTRY_UNREADABLE,
                    )
                emit(event)

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
            allow_legacy_cancel = False
            try:
                with editing_protocol_lock:
                    try:
                        command = editing_protocol.accept_command(command_line.encode())
                    except Exception:
                        allow_legacy_cancel = (
                            not editing_protocol.has_active_operation()
                        )
                        raise
            except Exception:
                # Preserve the material-studio cancellation vocabulary for a
                # WebUI-only session that has no active editing job.
                if not allow_legacy_cancel:
                    continue
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
            elif isinstance(command, LocalSmartEditStartCommand):
                cancel_requested.clear()
                render_thread = threading.Thread(
                    target=smart_edit,
                    args=(command,),
                    name="local-smart-edit-generation",
                )
                render_thread.start()
            elif isinstance(command, LocalEditingCancelCommand):
                cancel_requested.set()
            elif isinstance(
                command,
                (LocalSmartEditCommitCommand, LocalSmartEditAbortCommand),
            ):
                finalize_smart_edit(command)
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
            elif isinstance(command, LocalMaterialStatusCommand):
                material_thread = threading.Thread(
                    target=material_status,
                    args=(command,),
                    name="local-material-status",
                )
                material_thread.start()
    finally:
        cancel_requested.set()
        if render_thread is not None:
            render_thread.join(timeout=REQUEST_SHUTDOWN_TIMEOUT_SECONDS)
        if material_thread is not None:
            material_thread.join(timeout=REQUEST_SHUTDOWN_TIMEOUT_SECONDS)
        with smart_state_lock:
            if smart_commit_timer is not None:
                smart_commit_timer.cancel()
                smart_commit_timer = None
            if smart_staged is not None:
                smart_cleanup_pending.append(smart_staged)
                smart_staged = None
        for staged in smart_cleanup_pending:
            if not staged.finalized:
                with suppress(Exception):
                    abort_smart_edit_job(staged)
        server.shutdown()
        server.server_close()
        thread.join(timeout=REQUEST_SHUTDOWN_TIMEOUT_SECONDS)
        if webui is not None:
            webui.stop()
    return 0


REQUEST_SHUTDOWN_TIMEOUT_SECONDS: Final = 5
SMART_EDIT_COMMIT_TIMEOUT_SECONDS: Final = 120


def main(
    arguments: list[str] | None = None, bootstrap_stream: TextIO | None = None
) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if values == ["--probe-smart-edit-runtime"]:
        try:
            smart_edit_runtime_probe()
        except Exception:
            print(
                "Material video worker smart-edit runtime is unavailable",
                file=sys.stderr,
            )
            return 70
        print(
            json.dumps(
                {"runtime": "smart_edit", "status": "ready"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
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
