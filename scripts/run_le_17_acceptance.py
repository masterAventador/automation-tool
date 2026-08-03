#!/usr/bin/env python3
"""LE-17 hidden App journey through real Control Plane, Worker and MP4 Artifact."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from acceptance_postgres import WINDOWS_POSTGRES_ROOT_ENVIRONMENT
from automation_tool.executor.material_probe import MaterialPathRegistry
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    DEBUG_APP_RESOURCE_ROOT,
    terminate_app_process_tree,
    video_studio_startup_harness,
)
from prepare_video_runtime import install as install_video_runtime
from prepare_video_runtime import prepare as prepare_video_runtime
from process_inspection import process_ids_matching, terminate_matching_processes
from run_e4_14_acceptance import pnpm_executable
from run_i2_13_acceptance import require_port_closed, unused_loopback_port
from run_t3_06_acceptance import base64url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
TAURI_CONFIG = FRONTEND / "src-tauri/tauri.video-editing-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.le17acceptance"
ENVIRONMENT_ID = "le17-acceptance"
MATERIAL_ID = UUID("718cdcf5-0ff4-4f14-8259-30431a2447ce")
DEFAULT_EVIDENCE = ROOT / ".local/local-video-editing/le17-evidence"
SAFE_STARTUP_DESKTOP_EVENTS = frozenset(
    {
        "app.setup.started",
        "app.setup.control_plane_client.initialized",
        "app.setup.profile_data_directory.ready",
        "app.setup.update_coordinator.initialized",
        "app.setup.local_services.initialized",
        "app.setup.workspace.initialized",
        "app.setup.executor_service.initialized",
        "app.setup.credentials.initialized",
        "app.setup.completed",
        "startup.local.started",
        "startup.local.app_data.completed",
        "startup.local.browser.completed",
        "startup.local.executor.started",
        "startup.local.executor.configuration.ready",
        "startup.local.executor.configuration.rejected",
        "startup.local.executor.manager_status.ready",
        "startup.local.executor.manager_status.rejected",
        "startup.local.executor.package.ready",
        "startup.local.executor.package.rejected",
        "startup.local.executor.package.configuration_rejected",
        "startup.local.executor.package.signature_rejected",
        "startup.local.executor.package.manifest_rejected",
        "startup.local.executor.package.platform_rejected",
        "startup.local.executor.package.version_rejected",
        "startup.local.executor.package.inventory_rejected",
        "startup.local.executor.package.io_rejected",
        "executor.package.root.ready",
        "executor.package.manifest.read",
        "executor.package.signature.read",
        "executor.package.signature.verified",
        "executor.package.identity.verified",
        "executor.package.inventory.started",
        "executor.package.inventory.paths_verified",
        "executor.package.inventory.hashes_verified",
        "executor.package.inventory.digest_verified",
        "executor.package.inventory.rewalk_verified",
        "startup.local.executor.completed",
        "startup.local.completed",
        "startup.local.rejected",
        "startup.control_plane.started",
        "startup.control_plane.service_health.completed",
        "startup.control_plane.registration.completed",
        "startup.control_plane.installation_access.completed",
        "startup.control_plane.completed",
        "startup.control_plane.rejected",
    }
)


def acceptance_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep ambient tools but only the acceptance-owned product override."""
    ambient = os.environ if source is None else source
    return {
        key: value
        for key, value in ambient.items()
        if not key.startswith("AUTOMATION_TOOL_")
        or key == WINDOWS_POSTGRES_ROOT_ENVIRONMENT
    }


def app_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if roaming is None:
            raise RuntimeError("Windows roaming AppData is unavailable")
        return Path(roaming) / APP_IDENTIFIER
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / APP_IDENTIFIER


def signed_bootstrap() -> tuple[str, str]:
    signer = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    claims = {
        "environmentId": ENVIRONMENT_ID,
        "expiresAt": int((now + timedelta(hours=1)).timestamp()),
        "notBefore": int((now - timedelta(seconds=30)).timestamp()),
        "purpose": "installation.register",
        "version": 1,
    }
    payload = json.dumps(
        claims,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    segment = base64url(payload)
    signing_input = f"atb1.{segment}".encode("ascii")
    token = f"atb1.{segment}.{base64url(signer.sign(signing_input))}"
    return token, base64url(signer.public_key().public_bytes_raw())


def require_hidden_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("LE-17 acceptance must use one isolated hidden App")


def create_controlled_material(ffmpeg: Path, destination: Path) -> str:
    subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x3157d5:s=1280x720:r=30:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(destination),
        ],
        check=True,
        cwd=ROOT,
        timeout=120,
    )
    payload = destination.read_bytes()
    if len(payload) < 1_000:
        raise RuntimeError("LE-17 controlled H.264 material was not created")
    return hashlib.sha256(payload).hexdigest()


async def terminal_job(database_url: str) -> tuple[str, str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "select status, failure_code, output_artifact_id "
                        "from editing_jobs order by created_at"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    if len(rows) != 1:
        raise RuntimeError(f"LE-17 expected one EditingJob, found {len(rows)}")
    status, failure_code, artifact_id = rows[0]
    if status != "succeeded" or failure_code is not None or artifact_id is None:
        raise RuntimeError(
            "LE-17 EditingJob did not converge to succeeded with an Artifact"
        )
    return status, str(artifact_id)


async def editing_job_diagnostics(database_url: str) -> str:
    """Describe only safe lifecycle facts when the hidden journey fails."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "select status, failure_code, output_artifact_id is not null "
                        "from editing_jobs order by created_at"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    facts = [
        f"{status}:{failure_code or 'none'}:artifact={has_artifact}"
        for status, failure_code, has_artifact in rows
    ]
    return f"count={len(rows)} facts={','.join(facts) or 'none'}"


def local_runtime_diagnostics(private_app_data: Path) -> str:
    """Report only counts and booleans from the isolated runtime workspace."""
    jobs = private_app_data / "video-workspaces-v1" / "jobs"
    workspaces = [candidate for candidate in jobs.iterdir()] if jobs.is_dir() else []
    checkpoint_count = sum(
        (
            candidate / "checkpoints" / "local-editing-render-request.checkpoint"
        ).is_file()
        for candidate in workspaces
    )
    output_count = sum(
        len(list((candidate / "outputs").iterdir()))
        for candidate in workspaces
        if (candidate / "outputs").is_dir()
    )
    return (
        f"workspaces={len(workspaces)} checkpoints={checkpoint_count} "
        f"outputs={output_count}"
    )


def desktop_event_diagnostics(private_app_data: Path) -> str:
    """Return only validated fixed event names from the isolated desktop log."""
    events: list[str] = []
    logs = private_app_data / "logs"
    if not logs.is_dir():
        return "none"
    for path in sorted(logs.glob("desktop-*.log*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or set(record) != {
                "timestampUnixMs",
                "event",
            }:
                continue
            event = record.get("event")
            timestamp = record.get("timestampUnixMs")
            if (
                isinstance(timestamp, int)
                and isinstance(event, str)
                and event in SAFE_STARTUP_DESKTOP_EVENTS
            ):
                events.append(event)
    return ",".join(events[-64:]) or "none"


def inspect_artifact(
    *,
    private_app_data: Path,
    artifact_id: str,
    ffprobe: Path,
    evidence: Path,
) -> None:
    UUID(artifact_id)
    artifact = (
        private_app_data / "video-workspaces-v1" / "artifacts" / artifact_id / "payload"
    )
    if not artifact.is_file() or artifact.stat().st_size < 1_000:
        raise RuntimeError("LE-17 succeeded job has no real local Artifact payload")
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,nb_read_frames:format=duration,size",
            "-of",
            "json",
            str(artifact),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    probe = json.loads(completed.stdout)
    streams = probe.get("streams", [])
    if len(streams) != 1:
        raise RuntimeError("LE-17 Artifact does not contain exactly one video stream")
    stream = streams[0]
    duration = float(probe.get("format", {}).get("duration", "nan"))
    if (
        stream.get("codec_name") != "h264"
        or stream.get("width") != 720
        or stream.get("height") != 1280
        or stream.get("avg_frame_rate") != "20/1"
        or int(stream.get("nb_read_frames", 0)) != 20
        or not 0.95 <= duration <= 1.05
    ):
        raise RuntimeError(
            "LE-17 Artifact video stream drifted from the saved output contract"
        )
    evidence.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, evidence / "le17-real-editing.mp4")
    (evidence / "ffprobe.json").write_text(
        json.dumps(probe, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def main() -> int:
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing LE-17 App data directory")
    evidence = DEFAULT_EVIDENCE.resolve()
    evidence_root = (ROOT / ".local/local-video-editing").resolve()
    if not evidence.is_relative_to(evidence_root) or evidence == evidence_root:
        raise RuntimeError("LE-17 evidence must stay below the project evidence root")
    if evidence.exists():
        shutil.rmtree(evidence)

    platform = "windows" if sys.platform == "win32" else "macos"
    runtime_names = ("media-toolchain", "material-video-worker")
    staging = prepare_video_runtime(platform=platform, only=runtime_names)
    installed = install_video_runtime(
        staging=staging,
        resource_root=DEBUG_APP_RESOURCE_ROOT,
        only=runtime_names,
        platform=platform,
    )
    suffix = ".exe" if os.name == "nt" else ""
    ffmpeg = (installed["media-toolchain"] / f"bin/ffmpeg{suffix}").resolve(strict=True)
    ffprobe = (installed["media-toolchain"] / f"bin/ffprobe{suffix}").resolve(
        strict=True
    )
    webdriver_port = unused_loopback_port()
    token, public_key = signed_bootstrap()
    environment = acceptance_environment()
    environment["TAURI_WEBDRIVER_PORT"] = str(webdriver_port)
    worker_marker = os.fspath(installed["material-video-worker"])
    worker_baseline = process_ids_matching(worker_marker)
    app_process: subprocess.Popen[bytes] | None = None
    restore_failed = False

    with tempfile.TemporaryDirectory(prefix="automation-tool-le17-") as temporary:
        source = Path(temporary) / "controlled-source.mp4"
        digest = create_controlled_material(ffmpeg, source)
        try:
            with video_studio_startup_harness(
                private_app_data,
                environment=environment,
                demo_environment_id=ENVIRONMENT_ID,
                demo_bootstrap_public_key=public_key,
            ) as prepared:
                prepared.update(
                    {
                        "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
                        "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": public_key,
                        "AUTOMATION_TOOL_LE17_BOOTSTRAP_TOKEN": token,
                        "AUTOMATION_TOOL_LE17_ENVIRONMENT_ID": ENVIRONMENT_ID,
                        "AUTOMATION_TOOL_LE17_MATERIAL_ID": str(MATERIAL_ID),
                        "AUTOMATION_TOOL_LE17_MATERIAL_DIGEST": digest,
                        "TAURI_WEBDRIVER_PORT": str(webdriver_port),
                    }
                )
                state = private_app_data / "local-executor" / "state"
                state.mkdir(mode=0o700, parents=True)
                if os.name != "nt":
                    state.chmod(0o700)
                MaterialPathRegistry(state_directory=state).register(
                    MATERIAL_ID, source
                )

                subprocess.run(
                    [pnpm_executable(), "build:tauri:video-editing-test"],
                    check=True,
                    cwd=FRONTEND,
                    env=prepared,
                    timeout=1_200,
                )
                require_port_closed(webdriver_port)
                app_process = subprocess.Popen(
                    [
                        pnpm_executable(),
                        "exec",
                        "wdio",
                        "run",
                        "wdio.video-editing.conf.ts",
                    ],
                    cwd=FRONTEND,
                    env=prepared,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=sys.platform != "win32",
                )
                try:
                    output_bytes, _ = app_process.communicate(timeout=420)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "LE-17 hidden App journey did not finish"
                    ) from error
                output = output_bytes.decode("utf-8", errors="replace")
                print(output, end="")
                if app_process.returncode != 0:
                    diagnostics = asyncio.run(
                        editing_job_diagnostics(
                            prepared["AUTOMATION_TOOL_DATABASE_URL"]
                        )
                    )
                    print(f"LE-17 editing-job diagnostics: {diagnostics}")
                    print(
                        "LE-17 local-runtime diagnostics: "
                        f"{local_runtime_diagnostics(private_app_data)}"
                    )
                    print(
                        "LE-17 desktop-event diagnostics: "
                        f"{desktop_event_diagnostics(private_app_data)}"
                    )
                    raise RuntimeError("LE-17 hidden App editing journey failed")
                app_process = None
                _, artifact_id = asyncio.run(
                    terminal_job(prepared["AUTOMATION_TOOL_DATABASE_URL"])
                )
                inspect_artifact(
                    private_app_data=private_app_data,
                    artifact_id=artifact_id,
                    ffprobe=ffprobe,
                    evidence=evidence,
                )
                require_port_closed(webdriver_port)
        finally:
            if app_process is not None:
                terminate_app_process_tree(app_process)
            remaining = terminate_matching_processes(
                worker_marker, baseline=worker_baseline
            )
            if remaining:
                raise RuntimeError(
                    f"LE-17 Worker cleanup left process IDs: {sorted(remaining)}"
                )
            restore = subprocess.run(
                [pnpm_executable(), "build"],
                cwd=FRONTEND,
                env=environment,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=600,
            )
            restore_failed = restore.returncode != 0
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            require_port_closed(webdriver_port)
    if restore_failed:
        raise RuntimeError("LE-17 failed to restore production Vite assets")
    print(f"LE-17 real App editing acceptance passed; evidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
