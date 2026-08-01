#!/usr/bin/env python3
"""LE-19 hidden App journey through real import, model, draft and render paths."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from acceptance_postgres import WINDOWS_POSTGRES_ROOT_ENVIRONMENT
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
TAURI_CONFIG = FRONTEND / "src-tauri/tauri.smart-edit-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.le19acceptance"
ENVIRONMENT_ID = "le19-acceptance"
EVIDENCE_ROOT = ROOT / ".local/local-video-editing/le19-evidence"
PRIVATE_OUTPUT = re.compile(
    r"(?i)(?:authorization:\s*bearer|bearer\s+[a-z0-9._~+/-]{8,}|[?&]cap=[^\s&]+)"
)


def acceptance_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep ambient tools while dropping unrelated product overrides."""
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


def secret_locations() -> tuple[Path, ...]:
    locations = [ROOT / ".local/secrets/bailian-model.json"]
    if ROOT.parent.name == "wt":
        locations.append(ROOT.parent.parent / ".local/secrets/bailian-model.json")
    return tuple(locations)


def read_model_key() -> str:
    secret = next((path for path in secret_locations() if path.is_file()), None)
    if secret is None:
        raise RuntimeError(
            "LE-19 acceptance needs .local/secrets/bailian-model.json in this "
            "checkout or its owning worktree repository"
        )
    try:
        document = json.loads(secret.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("LE-19 model secret is unreadable") from error
    key = document.get("apiKey") if isinstance(document, dict) else None
    if (
        not isinstance(key, str)
        or re.fullmatch(r"sk-[A-Za-z0-9._-]{17,253}", key) is None
    ):
        raise RuntimeError("LE-19 model secret carries no usable apiKey")
    return key


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
        claims, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    segment = base64url(payload)
    token = (
        f"atb1.{segment}.{base64url(signer.sign(f'atb1.{segment}'.encode('ascii')))}"
    )
    return token, base64url(signer.public_key().public_bytes_raw())


def require_hidden_configuration() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("LE-19 acceptance must use one isolated hidden App")


def create_controlled_image(ffmpeg: Path, destination: Path) -> str:
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=1",
            "-frames:v",
            "1",
            str(destination),
        ],
        check=False,
        capture_output=True,
        cwd=ROOT,
        timeout=120,
    )
    if completed.returncode != 0 or not destination.is_file():
        raise RuntimeError("LE-19 controlled image was not created")
    payload = destination.read_bytes()
    if len(payload) < 1_000:
        raise RuntimeError("LE-19 controlled image is empty")
    return hashlib.sha256(payload).hexdigest()


async def assert_database_outcome(database_url: str) -> str:
    """Prove the failed attempt wrote nothing and both successful modes committed."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            project_count = int(
                (
                    await connection.execute(
                        text("select count(*) from editing_projects")
                    )
                ).scalar_one()
            )
            timelines = (
                await connection.execute(
                    text(
                        "select revision, duration_ms from timelines order by revision"
                    )
                )
            ).all()
            materials = (
                await connection.execute(
                    text(
                        "select kind, ai_description, description_source "
                        "from materials order by created_at"
                    )
                )
            ).all()
            jobs = (
                await connection.execute(
                    text(
                        "select timeline_revision, status, failure_code, output_artifact_id "
                        "from editing_jobs order by created_at"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    if project_count != 1:
        raise RuntimeError("LE-19 project count drifted")
    if [row.revision for row in timelines] != [1, 2] or any(
        row.duration_ms < 1 for row in timelines
    ):
        raise RuntimeError(
            "LE-19 expected exactly draft and render timeline revisions; "
            "the configuration failure must not write one"
        )
    image_rows = [row for row in materials if row.kind == "image"]
    audio_rows = [row for row in materials if row.kind == "audio"]
    if (
        len(image_rows) != 1
        or not image_rows[0].ai_description
        or image_rows[0].description_source != "ai"
        or not audio_rows
    ):
        raise RuntimeError(
            "LE-19 material understanding or narration writeback is missing"
        )
    if len(jobs) != 1:
        raise RuntimeError(
            "LE-19 one-click render did not create exactly one EditingJob"
        )
    job = jobs[0]
    if (
        job.timeline_revision != 2
        or job.status != "succeeded"
        or job.failure_code is not None
        or job.output_artifact_id is None
    ):
        raise RuntimeError(
            "LE-19 EditingJob did not converge to the revision-2 Artifact"
        )
    return str(job.output_artifact_id)


async def editing_job_diagnostics(database_url: str) -> str:
    """Return fixed lifecycle facts only; never identifiers or user content."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "select timeline_revision, status, failure_code, "
                        "output_artifact_id is not null as has_artifact "
                        "from editing_jobs order by created_at"
                    )
                )
            ).all()
            timeline_count = int(
                (
                    await connection.execute(text("select count(*) from timelines"))
                ).scalar_one()
            )
            material_count = int(
                (
                    await connection.execute(text("select count(*) from materials"))
                ).scalar_one()
            )
    finally:
        await engine.dispose()
    facts = ",".join(
        f"r{row.timeline_revision}:{row.status}:{row.failure_code or 'none'}:"
        f"artifact={row.has_artifact}"
        for row in rows
    )
    return (
        f"timelines={timeline_count} materials={material_count} "
        f"jobs={len(rows)} facts={facts or 'none'}"
    )


def inspect_artifact(
    *, private_app_data: Path, artifact_id: str, ffprobe: Path, evidence: Path
) -> None:
    UUID(artifact_id)
    artifact = (
        private_app_data / "video-workspaces-v1" / "artifacts" / artifact_id / "payload"
    )
    if not artifact.is_file() or artifact.stat().st_size < 1_000:
        raise RuntimeError("LE-19 succeeded job has no real local Artifact payload")
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,duration:format=duration,size",
            "-of",
            "json",
            str(artifact),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    probe = json.loads(completed.stdout)
    streams = probe.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    duration = float(probe.get("format", {}).get("duration", "nan"))
    if (
        len(videos) != 1
        or videos[0].get("codec_name") != "h264"
        or videos[0].get("width") != 720
        or videos[0].get("height") != 1280
        or videos[0].get("avg_frame_rate") != "20/1"
        or int(videos[0].get("nb_read_frames", 0)) < 1
        or len(audios) != 1
        or audios[0].get("codec_name") != "aac"
        or not 0.1 <= duration <= 600
    ):
        raise RuntimeError("LE-19 Artifact streams drifted from the editing contract")
    evidence.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, evidence / "le19-smart-edit.mp4")
    (evidence / "ffprobe.json").write_text(
        json.dumps(probe, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def assert_no_private_evidence(output: str, api_key: str, source: Path) -> None:
    if (
        api_key in output
        or str(source) in output
        or source.name in output
        or PRIVATE_OUTPUT.search(output)
    ):
        raise RuntimeError("LE-19 acceptance output exposed private local data")


def main() -> int:
    require_hidden_configuration()
    api_key = read_model_key()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing LE-19 App data directory")
    platform = "windows" if sys.platform == "win32" else "macos"
    evidence = EVIDENCE_ROOT / platform
    if evidence.exists():
        shutil.rmtree(evidence)

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

    with tempfile.TemporaryDirectory(prefix="automation-tool-le19-") as temporary:
        source = Path(temporary) / "controlled-test-pattern.png"
        source_digest = create_controlled_image(ffmpeg, source)
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
                        # Reuse the already isolated picker driver delivered by LE-18;
                        # the user action remains the production import command.
                        "AUTOMATION_TOOL_LE18_BOOTSTRAP_TOKEN": token,
                        "AUTOMATION_TOOL_LE18_ENVIRONMENT_ID": ENVIRONMENT_ID,
                        "AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER": "1",
                        "AUTOMATION_TOOL_LE18_PICK_1": str(source),
                        "AUTOMATION_TOOL_LE19_MODEL_KEY": api_key,
                        "TAURI_WEBDRIVER_PORT": str(webdriver_port),
                    }
                )
                state = private_app_data / "local-executor" / "state"
                state.mkdir(mode=0o700, parents=True)
                if os.name != "nt":
                    state.chmod(0o700)

                build = subprocess.run(
                    [pnpm_executable(), "build:tauri:smart-edit-test"],
                    check=False,
                    capture_output=True,
                    cwd=FRONTEND,
                    env=prepared,
                    timeout=1_200,
                )
                build_output = (build.stdout + build.stderr).decode(
                    "utf-8", errors="replace"
                )
                assert_no_private_evidence(build_output, api_key, source)
                if build.returncode != 0:
                    print(build_output, end="")
                    raise RuntimeError("LE-19 hidden App build failed")
                require_port_closed(webdriver_port)
                app_process = subprocess.Popen(
                    [
                        pnpm_executable(),
                        "exec",
                        "wdio",
                        "run",
                        "wdio.smart-edit.conf.ts",
                    ],
                    cwd=FRONTEND,
                    env=prepared,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=sys.platform != "win32",
                )
                try:
                    output_bytes, _ = app_process.communicate(timeout=1_500)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "LE-19 hidden App journey did not finish"
                    ) from error
                output = output_bytes.decode("utf-8", errors="replace")
                assert_no_private_evidence(output, api_key, source)
                print(output, end="")
                if app_process.returncode != 0:
                    print(
                        "LE-19 editing-job diagnostics: "
                        + asyncio.run(
                            editing_job_diagnostics(
                                prepared["AUTOMATION_TOOL_DATABASE_URL"]
                            )
                        )
                    )
                    raise RuntimeError("LE-19 hidden App smart-edit journey failed")
                app_process = None
                artifact_id = asyncio.run(
                    assert_database_outcome(prepared["AUTOMATION_TOOL_DATABASE_URL"])
                )
                inspect_artifact(
                    private_app_data=private_app_data,
                    artifact_id=artifact_id,
                    ffprobe=ffprobe,
                    evidence=evidence,
                )
                if (
                    not source.is_file()
                    or hashlib.sha256(source.read_bytes()).hexdigest() != source_digest
                ):
                    raise RuntimeError("LE-19 changed the user's imported source")
                require_port_closed(webdriver_port)
        finally:
            if app_process is not None:
                terminate_app_process_tree(app_process)
            remaining = terminate_matching_processes(
                worker_marker, baseline=worker_baseline
            )
            if remaining:
                raise RuntimeError("LE-19 Worker cleanup left acceptance processes")
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
        raise RuntimeError("LE-19 failed to restore production Vite assets")
    print(f"LE-19 real App smart-edit acceptance passed; evidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
