#!/usr/bin/env python3
"""Build, install and drive the LE-22 macOS original-speech package journey."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from acceptance_postgres import managed_test_postgres
from build_material_video_worker_candidate import (
    audit_candidate as audit_material_video_worker_candidate,
)
from build_motion_catalog_release import stage_for_release as stage_motion_catalog
from build_release_package import (
    DEFAULT_ARCHIVES,
    fill_disk_image,
    install_runtime_resources_and_sign,
    require_macos_target,
    stage_browser_distribution,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop_e2e_prerequisites import (
    install_signed_executor_package,
    startup_gate_environment,
    terminate_app_process_tree,
)
from le22_package_evidence import (
    Le22DatabaseSummary,
    compare_pcm_envelopes,
    validate_le22_database_evidence,
    validate_le22_ffprobe,
)
from prepare_video_runtime import prepare as prepare_video_runtime
from process_inspection import process_ids_matching, terminate_matching_processes
from release_assembly import (
    load_signing_identity,
    require_packaged_browser,
    require_packaged_motion_catalog,
    require_packaged_video_runtime,
)
from run_e4_14_acceptance import start_control_plane
from run_i2_13_acceptance import BACKEND_ROOT, compose_command, require_port_closed
from run_i2_13_acceptance import REPOSITORY_ROOT as ROOT
from run_le_14_acceptance import prepare_voice_fixture
from run_le_19_acceptance import (
    acceptance_environment,
    assert_no_private_evidence,
    read_model_key,
)
from run_t3_06_acceptance import base64url
from run_vf_06_acceptance import FRONTEND, pnpm_executable, unused_loopback_port
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

APP_NAME = "Automation Tool LE22 Mac Package Acceptance.app"
APP_IDENTIFIER = "com.aventador.automationtool.le22macpackage"
ENVIRONMENT_ID = "le22-macos-package-acceptance"
TAURI_ROOT = FRONTEND / "src-tauri"
TAURI_CONFIG = TAURI_ROOT / "tauri.le22-macos-package-e2e.conf.json"
WDIO_CONFIG = "wdio.le22-macos-package.conf.ts"
EVIDENCE_ROOT = ROOT / ".local/local-video-editing/le22-macos-evidence"
BUILD_STAGING = ROOT / ".local/le22-macos-package-staging"
PRIVATE_OUTPUT = re.compile(
    r"(?i)(?:authorization:\s*bearer|bearer\s+[a-z0-9._~+/-]{8,}|[?&]cap=[^\s&]+)"
)


def require_macos() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("LE-22 package acceptance requires macOS")


def app_data_directory() -> Path:
    destination = Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if destination.name != APP_IDENTIFIER:
        raise RuntimeError("LE-22 App data boundary is invalid")
    return destination


def signed_bootstrap() -> tuple[str, str]:
    signer = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    claims = {
        "environmentId": ENVIRONMENT_ID,
        "expiresAt": int((now + timedelta(hours=2)).timestamp()),
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


def isolated_environment(
    *,
    control_plane_port: int,
    database_port: int,
    webdriver_port: int,
    token: str,
    bootstrap_public_key: str,
    source: Path,
    api_key: str,
) -> dict[str, str]:
    environment = acceptance_environment()
    database_name = "automation_tool_le22_macos"
    database_password = secrets.token_hex(24)
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_le22_macos_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_le22_macos_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": database_name,
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": database_name,
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": (
                f"postgresql+asyncpg://{database_name}:{database_password}"
                f"@127.0.0.1:{database_port}/{database_name}"
            ),
            "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": bootstrap_public_key,
            "AUTOMATION_TOOL_LE18_BOOTSTRAP_TOKEN": token,
            "AUTOMATION_TOOL_LE18_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER": "1",
            "AUTOMATION_TOOL_LE18_PICK_1": os.fspath(source),
            "AUTOMATION_TOOL_LE22_MODEL_KEY": api_key,
            "TAURI_WEBDRIVER_PORT": str(webdriver_port),
        }
    )
    return startup_gate_environment(environment, control_plane_port=control_plane_port)


def bundle_binary(application: Path) -> Path:
    binaries = sorted((application / "Contents/MacOS").iterdir())
    if len(binaries) != 1 or not binaries[0].is_file():
        raise RuntimeError("LE-22 package does not have one App binary")
    return binaries[0]


def build_application(environment: dict[str, str]) -> Path:
    for stale in (
        TAURI_ROOT / "target/debug/bundle/macos",
        FRONTEND / "dist-le22-mac",
    ):
        shutil.rmtree(stale, ignore_errors=True)
    subprocess.run(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "build",
            "--debug",
            "--bundles",
            "app",
            "--features",
            "control-plane-e2e",
            "--config",
            os.fspath(TAURI_CONFIG),
            "--ci",
        ],
        cwd=FRONTEND,
        env=environment,
        check=True,
        timeout=1_800,
    )
    application = TAURI_ROOT / "target/debug/bundle/macos" / APP_NAME
    if not application.is_dir():
        raise RuntimeError("LE-22 macOS App bundle was not generated")
    return application


def install_production_resources(application: Path) -> str:
    identity = load_signing_identity()
    resources = application / "Contents/Resources"
    install_signed_executor_package(resource_root=resources)
    target_id, _architecture = require_macos_target()
    browser_staging = BUILD_STAGING / "browser"
    shutil.rmtree(browser_staging, ignore_errors=True)
    stage_browser_distribution(
        target_id,
        DEFAULT_ARCHIVES[target_id].resolve(strict=True),
        browser_staging,
        identity,
    )
    video_runtime = prepare_video_runtime(platform="macos")
    motion_catalog = stage_motion_catalog(staging=BUILD_STAGING / "catalog").parent
    install_runtime_resources_and_sign(
        application,
        staging=browser_staging,
        target_id=target_id,
        video_runtime=video_runtime,
        motion_catalog=motion_catalog,
        identity=identity,
    )
    require_packaged_browser(
        application=application, target_id=target_id, platform="macos"
    )
    require_packaged_motion_catalog(application=application, platform="macos")
    require_packaged_video_runtime(application=application, platform="macos")
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", os.fspath(application)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return target_id


def create_disk_image(application: Path, destination: Path) -> Path:
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-le22-image-", dir=destination.parent
    ) as staging_root:
        staging = Path(staging_root) / "image"
        staging.mkdir()
        subprocess.run(
            ["ditto", os.fspath(application), os.fspath(staging / APP_NAME)],
            check=True,
            timeout=600,
        )
        (staging / "Applications").symlink_to("/Applications")
        fill_disk_image(
            source=staging,
            volume_name="Automation Tool LE22 Acceptance",
            output=destination,
        )
    subprocess.run(
        ["hdiutil", "verify", os.fspath(destination)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    return destination


def install_from_disk_image(
    image: Path, *, mountpoint: Path, destination: Path
) -> Path:
    if destination.exists():
        raise RuntimeError("LE-22 isolated installation already exists")
    mountpoint.mkdir()
    subprocess.run(
        [
            "hdiutil",
            "attach",
            os.fspath(image),
            "-mountpoint",
            os.fspath(mountpoint),
            "-nobrowse",
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    try:
        packaged = mountpoint / APP_NAME
        if not packaged.is_dir():
            raise RuntimeError("LE-22 disk image carries no App bundle")
        destination.parent.mkdir(parents=True)
        subprocess.run(
            ["ditto", os.fspath(packaged), os.fspath(destination)],
            check=True,
            timeout=600,
        )
    finally:
        subprocess.run(
            ["hdiutil", "detach", os.fspath(mountpoint)],
            check=True,
            capture_output=True,
            timeout=300,
        )
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", os.fspath(destination)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return destination


def create_speech_video(ffmpeg: Path, voice: Path, destination: Path) -> str:
    subprocess.run(
        [
            os.fspath(ffmpeg),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=1280x720:r=17:d=7.173",
            "-i",
            os.fspath(voice),
            "-filter_complex",
            "[1:a:0]apad=pad_dur=1[audio]",
            "-map",
            "0:v:0",
            "-map",
            "[audio]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "flac",
            "-t",
            "6.173",
            os.fspath(destination),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    if not destination.is_file() or destination.stat().st_size < 10_000:
        raise RuntimeError("LE-22 controlled speech video was not created")
    return hashlib.sha256(destination.read_bytes()).hexdigest()


async def collect_database_evidence(database_url: str) -> Le22DatabaseSummary:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            materials = (
                await connection.execute(
                    text(
                        "select material_id, kind, duration_ms, has_speech, "
                        "speech_segments_ms, speech_transcript from materials "
                        "order by material_id"
                    )
                )
            ).all()
            timelines = (
                await connection.execute(
                    text(
                        "select revision, duration_ms, tracks from timelines "
                        "order by revision"
                    )
                )
            ).all()
            jobs = (
                await connection.execute(
                    text(
                        "select status, timeline_revision, failure_code, "
                        "output_artifact_id from editing_jobs order by created_at"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    if len(materials) != 1 or len(timelines) != 1 or len(jobs) != 1:
        raise RuntimeError("LE-22 database row counts are invalid")
    material = materials[0]
    timeline = timelines[0]
    job = jobs[0]
    document = {
        "material": {
            "materialId": str(material.material_id),
            "kind": material.kind,
            "durationMs": material.duration_ms,
            "hasSpeech": material.has_speech,
            "speechSegmentsMs": material.speech_segments_ms,
            "speechTranscript": material.speech_transcript,
        },
        "timeline": {
            "revision": timeline.revision,
            "durationMs": timeline.duration_ms,
            "tracks": timeline.tracks,
        },
        "job": {
            "status": job.status,
            "timelineRevision": job.timeline_revision,
            "failureCode": job.failure_code,
            "outputArtifactId": str(job.output_artifact_id),
        },
    }
    return validate_le22_database_evidence(
        document,
        required_transcript_words=frozenset({"QUILTER", "APOSTLE"}),
    )


async def collect_editing_job_failure(database_url: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "select status, failure_code from editing_jobs "
                        "order by created_at desc limit 1"
                    )
                )
            ).one_or_none()
    finally:
        await engine.dispose()
    if row is None:
        return "missing_job"
    return f"{row.status}:{row.failure_code or 'none'}"


def extract_pcm(
    ffmpeg: Path,
    source: Path,
    *,
    start_ms: int | None = None,
    duration_ms: int | None = None,
) -> bytes:
    timing: list[str] = []
    if start_ms is not None and duration_ms is not None:
        timing = ["-ss", f"{start_ms / 1_000:.3f}", "-t", f"{duration_ms / 1_000:.3f}"]
    completed = subprocess.run(
        [
            os.fspath(ffmpeg),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            *timing,
            "-i",
            os.fspath(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    return completed.stdout


def inspect_artifact(
    *,
    app_data: Path,
    source: Path,
    summary: Le22DatabaseSummary,
    ffmpeg: Path,
    ffprobe: Path,
    package_bytes: int,
    image_bytes: int,
) -> None:
    artifact = (
        app_data / "video-workspaces-v1" / "artifacts" / summary.artifact_id / "payload"
    )
    if not artifact.is_file():
        raise RuntimeError("LE-22 succeeded job has no local Artifact")
    completed = subprocess.run(
        [
            os.fspath(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames:format=duration,size",
            "-of",
            "json",
            os.fspath(artifact),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    probe = json.loads(completed.stdout)
    media = validate_le22_ffprobe(
        probe,
        artifact_bytes=artifact.stat().st_size,
        timeline_duration_ms=summary.timeline_duration_ms,
    )
    source_pcm = extract_pcm(
        ffmpeg,
        source,
        start_ms=summary.source_window_ms[0],
        duration_ms=summary.timeline_duration_ms,
    )
    artifact_pcm = extract_pcm(ffmpeg, artifact)
    correlation = compare_pcm_envelopes(source_pcm, artifact_pcm)
    if correlation < 0.90:
        raise RuntimeError("LE-22 rendered audio is not the imported original speech")

    EVIDENCE_ROOT.mkdir(parents=True)
    shutil.copy2(artifact, EVIDENCE_ROOT / "le22-original-speech.mp4")
    (EVIDENCE_ROOT / "ffprobe.json").write_text(
        json.dumps(probe, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    report = {
        "schemaVersion": 1,
        "platform": "macos",
        "revision": revision,
        "packageBytes": package_bytes,
        "diskImageBytes": image_bytes,
        "artifactBytes": media.artifact_bytes,
        "artifactSha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "videoFrames": media.video_frames,
        "durationMs": media.duration_ms,
        "sourceWindowMs": list(summary.source_window_ms),
        "speechSegmentCount": summary.speech_segment_count,
        "originalAudioEnvelopeCorrelation": round(correlation, 6),
    }
    (EVIDENCE_ROOT / "report.json").write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def package_size(application: Path) -> int:
    return sum(
        path.stat().st_size
        for path in application.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def run_wdio(
    *, application: Path, environment: dict[str, str], api_key: str, source: Path
) -> None:
    prepared = dict(environment)
    prepared["LE22_MAC_APP_BINARY"] = os.fspath(bundle_binary(application))
    process = subprocess.Popen(
        [pnpm_executable(), "exec", "wdio", "run", WDIO_CONFIG],
        cwd=FRONTEND,
        env=prepared,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output_bytes, _ = process.communicate(timeout=1_800)
    except subprocess.TimeoutExpired as error:
        terminate_app_process_tree(process)
        raise RuntimeError("LE-22 installed App journey did not finish") from error
    output = output_bytes.decode("utf-8", errors="replace")
    assert_no_private_evidence(output, api_key, source)
    print(output, end="")
    if PRIVATE_OUTPUT.search(output) or process.returncode != 0:
        raise RuntimeError("LE-22 installed App journey failed")


def stop_control_plane(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    require_macos()
    api_key = read_model_key()
    app_data = app_data_directory()
    if app_data.exists():
        raise RuntimeError("Refusing to reuse an existing LE-22 App data directory")
    if EVIDENCE_ROOT.exists():
        shutil.rmtree(EVIDENCE_ROOT)

    control_plane_port = unused_loopback_port()
    database_port = unused_loopback_port()
    webdriver_port = unused_loopback_port()
    while len({control_plane_port, database_port, webdriver_port}) != 3:
        database_port = unused_loopback_port()
        webdriver_port = unused_loopback_port()
    token, bootstrap_public_key = signed_bootstrap()

    with tempfile.TemporaryDirectory(
        prefix="automation-tool-le22-macos-", dir="/private/tmp"
    ) as temporary_root:
        temporary = Path(temporary_root)
        voice = prepare_voice_fixture(temporary / "controlled-human-speech.flac")
        placeholder_source = temporary / "controlled-human-speech.mkv"
        environment = isolated_environment(
            control_plane_port=control_plane_port,
            database_port=database_port,
            webdriver_port=webdriver_port,
            token=token,
            bootstrap_public_key=bootstrap_public_key,
            source=placeholder_source,
            api_key=api_key,
        )
        application = build_application(environment)
        target_id = install_production_resources(application)
        image = create_disk_image(application, temporary / "le22-macos.dmg")
        installed_application = install_from_disk_image(
            image,
            mountpoint=temporary / "mounted",
            destination=temporary / "installed" / APP_NAME,
        )
        installed_resources = require_packaged_video_runtime(
            application=installed_application, platform="macos"
        )
        require_packaged_browser(
            application=installed_application,
            target_id=target_id,
            platform="macos",
        )
        require_packaged_motion_catalog(
            application=installed_application, platform="macos"
        )
        material_worker = installed_resources["material-video-worker"]
        audit_material_video_worker_candidate(material_worker)
        media_toolchain = installed_resources["media-toolchain"] / "bin"
        ffmpeg = (media_toolchain / "ffmpeg").resolve(strict=True)
        ffprobe = (media_toolchain / "ffprobe").resolve(strict=True)
        source_digest = create_speech_video(ffmpeg, voice, placeholder_source)
        process_markers = (
            os.fspath(bundle_binary(installed_application)),
            os.fspath(
                installed_application
                / "Contents/Resources/local-executor/package/automation-tool-executor"
            ),
            os.fspath(material_worker / "automation-tool-material-video-worker"),
        )
        process_baselines = {
            marker: process_ids_matching(marker) for marker in process_markers
        }

        project_name = f"automation-tool-le22-macos-{secrets.token_hex(8)}"
        compose = compose_command(project_name)
        server: subprocess.Popen[bytes] | None = None
        try:
            with managed_test_postgres(
                compose=compose,
                database_port=database_port,
                environment=environment,
                repository_root=ROOT,
            ):
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"],
                    check=True,
                    cwd=BACKEND_ROOT,
                    env=environment,
                    timeout=300,
                )
                server = start_control_plane(
                    port=control_plane_port, environment=environment
                )
                try:
                    try:
                        run_wdio(
                            application=installed_application,
                            environment=environment,
                            api_key=api_key,
                            source=placeholder_source,
                        )
                    except RuntimeError as error:
                        failure_code = asyncio.run(
                            collect_editing_job_failure(
                                environment["AUTOMATION_TOOL_DATABASE_URL"]
                            )
                        )
                        raise RuntimeError(
                            f"LE-22 installed App journey failureCode={failure_code}"
                        ) from error
                    summary = asyncio.run(
                        collect_database_evidence(
                            environment["AUTOMATION_TOOL_DATABASE_URL"]
                        )
                    )
                    inspect_artifact(
                        app_data=app_data,
                        source=placeholder_source,
                        summary=summary,
                        ffmpeg=ffmpeg,
                        ffprobe=ffprobe,
                        package_bytes=package_size(installed_application),
                        image_bytes=image.stat().st_size,
                    )
                    if (
                        hashlib.sha256(placeholder_source.read_bytes()).hexdigest()
                        != source_digest
                    ):
                        raise RuntimeError("LE-22 changed the imported source")
                finally:
                    stop_control_plane(server)
                    server = None
        finally:
            if server is not None:
                stop_control_plane(server)
            leaked_processes = {
                marker: remaining
                for marker in process_markers
                if (
                    remaining := terminate_matching_processes(
                        marker, baseline=process_baselines[marker]
                    )
                )
            }
            if leaked_processes:
                raise RuntimeError("LE-22 cleanup left installed package processes")
            if app_data.exists():
                shutil.rmtree(app_data)
            require_port_closed(control_plane_port)
            require_port_closed(database_port)
            require_port_closed(webdriver_port)

    print(f"LE-22 macOS installed package acceptance passed; evidence: {EVIDENCE_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
