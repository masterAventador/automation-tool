#!/usr/bin/env python3
"""Build, install and drive the LE-23 Windows original-speech package journey."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_ROOT = FRONTEND_ROOT / "src-tauri"

sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
sys.path.insert(0, os.fspath(BACKEND_ROOT / "src"))

from acceptance_postgres import managed_test_postgres
from build_material_video_worker_candidate import (
    audit_candidate as audit_material_video_worker_candidate,
)
from build_motion_catalog_release import (
    stage_for_release as stage_motion_catalog,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from desktop_e2e_prerequisites import startup_gate_environment
from embedded_browser_archives import (
    WINDOWS_X86_64_ARCHIVE,
    archive_path,
)
from le22_package_evidence import (
    Le22DatabaseSummary,
    compare_pcm_envelopes,
    validate_le22_ffprobe,
)
from prepare_video_runtime import prepare as prepare_video_runtime
from release_assembly import (
    install_and_seal,
    install_motion_catalog,
    install_video_runtime,
    require_packaged_browser,
    require_packaged_motion_catalog,
    require_packaged_video_runtime,
)
from release_configuration import (
    merge_configuration,
    write_windows_release_configuration,
)
from run_e4_14_acceptance import (
    require_port_available,
    start_control_plane,
)
from run_eb_16_windows_acceptance import (
    build_executor_candidate,
    pnpm_executable,
    require_no_process_matching,
    seal_windows_payload,
    stage_browser_distribution,
    terminate_processes_matching,
)
from run_i2_13_acceptance import compose_command
from run_le_14_acceptance import prepare_voice_fixture
from run_le_19_acceptance import assert_no_private_evidence
from run_le_22_macos_package_acceptance import (
    collect_database_evidence,
    create_speech_video,
    extract_pcm,
    package_size,
    stop_control_plane,
)
from run_p9_04_acceptance import (
    install_root,
    installer_environment,
    one_file,
    release_environment,
    require_non_elevated_process,
    require_windows,
    run_checked,
    windows_registry_installations,
)
from run_pc_16_windows_package_acceptance import (
    audit_installed_motion_catalog,
)
from run_t3_06_acceptance import base64url
from run_t36_acceptance import read_model_key
from run_vf_06_acceptance import (
    require_port_closed,
    unused_loopback_port,
)

APP_IDENTIFIER = "com.aventador.automationtool.le23windowspackage"
PRODUCT_NAME = "Automation Tool LE23 Windows Package Acceptance"
MAIN_BINARY_NAME = "automation-tool-le23-windows-package"
ENVIRONMENT_ID = "le23-windows-package-acceptance"
TARGET_ID = "windows-x86_64"
BASE_CONFIG = TAURI_ROOT / "tauri.conf.json"
ACCEPTANCE_CONFIG = TAURI_ROOT / "tauri.le23-windows-package-e2e.conf.json"
WDIO_CONFIG = "wdio.le23-windows-package.conf.ts"
DEFAULT_SECRET = REPOSITORY_ROOT / ".local/secrets/bailian-model.json"
DEFAULT_WORK_DIRECTORY = REPOSITORY_ROOT / ".local/le23-windows-package-acceptance"
EVIDENCE_ROOT = REPOSITORY_ROOT / ".local/local-video-editing/le23-windows-evidence"
PROJECT_STEM = "automation-tool-le23-windows"


class AcceptanceFailed(RuntimeError):
    """The LE-23 Windows installed-package journey failed."""


def announce(message: str) -> None:
    print(f"[LE-23-WIN] {message}", flush=True)


def private_app_data() -> Path:
    roaming = os.environ.get("APPDATA")
    if not roaming:
        raise AcceptanceFailed("Windows roaming AppData is unavailable")
    return Path(roaming) / APP_IDENTIFIER


def installed_root() -> Path:
    return install_root(product_name=PRODUCT_NAME)


def installed_binary(root: Path) -> Path:
    binary = root / f"{MAIN_BINARY_NAME}.exe"
    if not binary.is_file():
        raise AcceptanceFailed("LE-23 installed App binary is missing")
    return binary


def isolated_ports() -> tuple[int, int, int]:
    ports: list[int] = []
    while len(ports) < 3:
        candidate = unused_loopback_port()
        if candidate not in ports:
            require_port_available(candidate)
            ports.append(candidate)
    return ports[0], ports[1], ports[2]


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
    cargo_target: Path,
    executor_public_key: str,
    bootstrap_token: str,
    bootstrap_public_key: str,
    source: Path,
    api_key: str,
) -> dict[str, str]:
    environment = release_environment(cargo_target, executor_public_key)
    database_name = "automation_tool_le23_windows"
    database_password = secrets.token_hex(24)
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_le23_windows_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_le23_windows_dev",
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
            "AUTOMATION_TOOL_LE18_BOOTSTRAP_TOKEN": bootstrap_token,
            "AUTOMATION_TOOL_LE18_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER": "1",
            "AUTOMATION_TOOL_LE18_PICK_1": os.fspath(source),
            "AUTOMATION_TOOL_LE22_MODEL_KEY": api_key,
            "TAURI_WEBDRIVER_PORT": str(webdriver_port),
        }
    )
    return startup_gate_environment(environment, control_plane_port=control_plane_port)


def write_build_configuration(
    *, directory: Path, executor: Path, payload: Path
) -> Path:
    resource_configuration = write_windows_release_configuration(
        directory=directory,
        executor=executor,
        payload=payload,
        name="tauri.le23-windows-resources.json",
    )
    merged: object = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    for overlay in (ACCEPTANCE_CONFIG, resource_configuration):
        merged = merge_configuration(
            merged, json.loads(overlay.read_text(encoding="utf-8"))
        )
    if not isinstance(merged, dict):
        raise AcceptanceFailed("LE-23 Windows Tauri configuration is invalid")
    destination = directory / "tauri.le23-windows-effective.json"
    destination.write_text(
        json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def build_installer(
    *, configuration: Path, environment: dict[str, str], cargo_target: Path
) -> Path:
    bundle_root = cargo_target / "debug/bundle"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "build",
            "--debug",
            "--features",
            "control-plane-e2e",
            "--bundles",
            "nsis",
            "--config",
            os.fspath(configuration),
            "--ci",
        ],
        environment=environment,
    )
    if not (cargo_target / "debug" / f"{MAIN_BINARY_NAME}.exe").is_file():
        raise AcceptanceFailed("LE-23 built App binary is missing")
    return one_file(
        cargo_target / "debug/bundle/nsis",
        "*-setup.exe",
        "LE-23 Windows NSIS installer was not generated exactly once",
    )


def install_package(installer: Path, root: Path) -> None:
    if (
        root.exists()
        or windows_registry_installations(machine_wide=False, product_name=PRODUCT_NAME)
        or windows_registry_installations(machine_wide=True, product_name=PRODUCT_NAME)
    ):
        raise AcceptanceFailed("an earlier LE-23 Windows installation still exists")
    run_checked([os.fspath(installer), "/S"], environment=installer_environment())
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        current_user = windows_registry_installations(
            machine_wide=False, product_name=PRODUCT_NAME
        )
        machine_wide = windows_registry_installations(
            machine_wide=True, product_name=PRODUCT_NAME
        )
        if machine_wide:
            raise AcceptanceFailed("LE-23 package wrote a machine-wide installation")
        if (
            (root / f"{MAIN_BINARY_NAME}.exe").is_file()
            and (root / "uninstall.exe").is_file()
            and len(current_user) == 1
        ):
            return
        time.sleep(0.2)
    raise AcceptanceFailed("LE-23 Windows NSIS installation did not converge")


def uninstall_and_check(root: Path) -> None:
    terminate_processes_matching(os.fspath(root))
    uninstaller = root / "uninstall.exe"
    if uninstaller.is_file():
        run_checked([os.fspath(uninstaller), "/S"], environment=installer_environment())
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if (
            not root.exists()
            and not windows_registry_installations(
                machine_wide=False, product_name=PRODUCT_NAME
            )
            and not windows_registry_installations(
                machine_wide=True, product_name=PRODUCT_NAME
            )
        ):
            require_no_process_matching(os.fspath(root))
            return
        time.sleep(0.2)
    raise AcceptanceFailed("LE-23 Windows uninstaller left owned state")


def run_wdio(
    *, binary: Path, environment: dict[str, str], api_key: str, source: Path
) -> None:
    prepared = dict(environment)
    prepared["LE23_WINDOWS_APP_BINARY"] = os.fspath(binary)
    process = subprocess.Popen(
        [
            pnpm_executable(),
            "exec",
            "wdio",
            "run",
            WDIO_CONFIG,
        ],
        cwd=FRONTEND_ROOT,
        env=prepared,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output_bytes, _ = process.communicate(timeout=1_800)
    except subprocess.TimeoutExpired as error:
        terminate_processes_matching(os.fspath(binary.parent))
        raise AcceptanceFailed("LE-23 installed App journey did not finish") from error
    output = output_bytes.decode("utf-8", errors="replace")
    assert_no_private_evidence(output, api_key, source)
    if process.returncode != 0:
        raise AcceptanceFailed("LE-23 installed App journey failed")


def inspect_windows_artifact(
    *,
    app_data: Path,
    source: Path,
    summary: Le22DatabaseSummary,
    ffmpeg: Path,
    ffprobe: Path,
    root: Path,
    installer: Path,
    catalog_audit: dict[str, object],
    worker_files: int,
    worker_bytes: int,
) -> None:
    artifact = (
        app_data / "video-workspaces-v1" / "artifacts" / summary.artifact_id / "payload"
    )
    if not artifact.is_file():
        raise AcceptanceFailed("LE-23 succeeded job has no local Artifact")
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
    correlation = compare_pcm_envelopes(source_pcm, extract_pcm(ffmpeg, artifact))
    if correlation < 0.90:
        raise AcceptanceFailed(
            "LE-23 rendered audio is not the imported original speech"
        )

    EVIDENCE_ROOT.mkdir(parents=True)
    shutil.copy2(artifact, EVIDENCE_ROOT / "le23-windows-original-speech.mp4")
    (EVIDENCE_ROOT / "ffprobe.json").write_text(
        json.dumps(probe, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    report = {
        "schemaVersion": 1,
        "platform": "windows",
        "revision": revision,
        "installedPackageBytes": package_size(root),
        "installerBytes": installer.stat().st_size,
        "artifactBytes": media.artifact_bytes,
        "artifactSha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "videoFrames": media.video_frames,
        "durationMs": media.duration_ms,
        "sourceWindowMs": list(summary.source_window_ms),
        "speechSegmentCount": summary.speech_segment_count,
        "originalAudioEnvelopeCorrelation": round(correlation, 6),
        "materialWorkerFiles": worker_files,
        "materialWorkerBytes": worker_bytes,
        "cjkFont": catalog_audit["cjkFont"],
        "cjkFontBytes": catalog_audit["cjkFontBytes"],
        "cjkFontSha256": catalog_audit["cjkFontSha256"],
    }
    (EVIDENCE_ROOT / "report.json").write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret", type=Path, default=DEFAULT_SECRET)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIRECTORY)
    return parser.parse_args()


def main() -> int:
    architecture = require_windows()
    require_non_elevated_process()
    if architecture != "x86_64":
        raise AcceptanceFailed("LE-23 Windows package acceptance requires x86_64")
    arguments = parse_arguments()
    api_key = read_model_key(arguments.secret.resolve())
    archive = arguments.archive or archive_path(REPOSITORY_ROOT, WINDOWS_X86_64_ARCHIVE)
    work_directory: Path = arguments.work_dir
    build_directory = work_directory / "build"
    cargo_target = work_directory / "cargo-target"
    root = installed_root()
    app_data = private_app_data()
    for stale in (EVIDENCE_ROOT, build_directory, app_data):
        if stale.exists():
            shutil.rmtree(stale)
    build_directory.mkdir(parents=True)

    with tempfile.TemporaryDirectory(
        prefix="automation-tool-le23-windows-"
    ) as temporary:
        temporary_root = Path(temporary)
        placeholder_source = temporary_root / "controlled-human-speech.mkv"
        control_plane_port, database_port, webdriver_port = isolated_ports()
        bootstrap_token, bootstrap_public_key = signed_bootstrap()

        browser_staging = build_directory / "browser-staging"
        stage_browser_distribution(archive, browser_staging)
        executor = build_directory / "executor/automation-tool-executor"
        executor_public_key, _private_key = build_executor_candidate(
            executor, architecture
        )
        payload = build_directory / "payload"
        video_runtime = prepare_video_runtime(platform="windows")
        catalog_staging = stage_motion_catalog(
            staging=build_directory / "catalog-staging"
        ).parent
        install_video_runtime(
            application=payload, staging=video_runtime, platform="windows"
        )
        install_motion_catalog(
            application=payload, staging=catalog_staging, platform="windows"
        )
        install_and_seal(
            application=payload,
            staging=browser_staging,
            target_id=TARGET_ID,
            platform="windows",
            seal=seal_windows_payload,
        )
        require_packaged_browser(
            application=payload, target_id=TARGET_ID, platform="windows"
        )
        require_packaged_video_runtime(application=payload, platform="windows")
        require_packaged_motion_catalog(application=payload, platform="windows")

        configuration = write_build_configuration(
            directory=build_directory, executor=executor, payload=payload
        )
        environment = isolated_environment(
            control_plane_port=control_plane_port,
            database_port=database_port,
            webdriver_port=webdriver_port,
            cargo_target=cargo_target,
            executor_public_key=executor_public_key,
            bootstrap_token=bootstrap_token,
            bootstrap_public_key=bootstrap_public_key,
            source=placeholder_source,
            api_key=api_key,
        )
        installer = build_installer(
            configuration=configuration,
            environment=environment,
            cargo_target=cargo_target,
        )

        installed = False
        server: subprocess.Popen[bytes] | None = None
        project_name = f"{PROJECT_STEM}-{os.getpid()}"
        compose = compose_command(project_name)
        try:
            install_package(installer, root)
            installed = True
            binary = installed_binary(root)
            require_packaged_browser(
                application=root, target_id=TARGET_ID, platform="windows"
            )
            installed_resources = require_packaged_video_runtime(
                application=root, platform="windows"
            )
            require_packaged_motion_catalog(application=root, platform="windows")
            catalog_audit = audit_installed_motion_catalog(root)
            material_worker = installed_resources["material-video-worker"]
            worker_audit = audit_material_video_worker_candidate(material_worker)
            media_bin = installed_resources["media-toolchain"] / "bin"
            ffmpeg = (media_bin / "ffmpeg.exe").resolve(strict=True)
            ffprobe = (media_bin / "ffprobe.exe").resolve(strict=True)
            voice = prepare_voice_fixture(
                temporary_root / "controlled-human-speech.flac"
            )
            source_digest = create_speech_video(ffmpeg, voice, placeholder_source)

            with managed_test_postgres(
                compose=compose,
                database_port=database_port,
                environment=environment,
                repository_root=REPOSITORY_ROOT,
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
                    run_wdio(
                        binary=binary,
                        environment=environment,
                        api_key=api_key,
                        source=placeholder_source,
                    )
                    summary = asyncio.run(
                        collect_database_evidence(
                            environment["AUTOMATION_TOOL_DATABASE_URL"]
                        )
                    )
                    inspect_windows_artifact(
                        app_data=app_data,
                        source=placeholder_source,
                        summary=summary,
                        ffmpeg=ffmpeg,
                        ffprobe=ffprobe,
                        root=root,
                        installer=installer,
                        catalog_audit=catalog_audit,
                        worker_files=worker_audit.file_count,
                        worker_bytes=worker_audit.package_bytes,
                    )
                    if (
                        hashlib.sha256(placeholder_source.read_bytes()).hexdigest()
                        != source_digest
                    ):
                        raise AcceptanceFailed("LE-23 changed the imported source")
                finally:
                    stop_control_plane(server)
                    server = None
        finally:
            if server is not None:
                stop_control_plane(server)
            terminate_processes_matching(os.fspath(root))
            if (
                installed
                or root.exists()
                or windows_registry_installations(
                    machine_wide=False, product_name=PRODUCT_NAME
                )
            ):
                uninstall_and_check(root)
            if app_data.exists():
                shutil.rmtree(app_data)
            require_port_closed(control_plane_port)
            require_port_closed(database_port)
            require_port_closed(webdriver_port)

    announce(f"installed package acceptance passed; evidence: {EVIDENCE_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
