#!/usr/bin/env python3
"""LE-18 hidden App journey through the real Control Plane and frozen Worker."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
TAURI_CONFIG = FRONTEND / "src-tauri/tauri.material-library-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.le18acceptance"
ENVIRONMENT_ID = "le18-acceptance"
PRIVATE_OUTPUT = re.compile(
    r"(?i)(?:authorization:\s*bearer|bearer\s+[a-z0-9._~+/-]{8,}|[?&]cap=[^\s&]+)"
)


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def pnpm_executable() -> str:
    name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError("LE-18 pnpm executable is unavailable")
    return executable


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def require_port_closed(port: int) -> None:
    with socket.socket() as connection:
        connection.settimeout(0.25)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError("LE-18 WebDriver port is still open")


def acceptance_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep ambient tools but drop every unrelated product override."""
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
        claims, ensure_ascii=True, separators=(",", ":"), sort_keys=True
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
        raise RuntimeError("LE-18 acceptance must use one isolated hidden App")


def run_ffmpeg(ffmpeg: Path, arguments: list[str]) -> None:
    subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", *arguments],
        check=True,
        capture_output=True,
        cwd=ROOT,
        timeout=120,
    )


def create_sources(ffmpeg: Path, directory: Path) -> list[Path]:
    video = directory / "fixture-1.mp4"
    audio = directory / "fixture-2.wav"
    image = directory / "fixture-3.png"
    referenced = directory / "fixture-4.mp4"
    deletable = directory / "fixture-5.png"
    run_ffmpeg(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=20:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
    )
    run_ffmpeg(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=1",
            "-c:a",
            "pcm_s16le",
            str(audio),
        ],
    )
    run_ffmpeg(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=0x3157d5:s=320x240",
            "-frames:v",
            "1",
            str(image),
        ],
    )
    run_ffmpeg(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=0xd56a31:s=320x240:r=20:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(referenced),
        ],
    )
    run_ffmpeg(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=0x31a05a:s=320x240",
            "-frames:v",
            "1",
            str(deletable),
        ],
    )
    if not all(
        source.is_file() and source.stat().st_size > 100
        for source in (video, audio, image, referenced, deletable)
    ):
        raise RuntimeError("LE-18 controlled media set was not created")
    return [video, video, audio, image, referenced, deletable]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_unchanged_after_delete(path: Path, expected_digest: str) -> None:
    if not path.is_file() or digest(path) != expected_digest:
        raise RuntimeError("LE-18 material deletion changed the user source")


async def assert_database_outcome(
    database_url: str, initial_digests: list[str]
) -> None:
    video_digest, audio_digest, image_digest, referenced_digest, deleted_digest = (
        initial_digests
    )
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "select kind, content_digest, ai_description, description_source "
                        "from materials order by material_id"
                    )
                )
            ).all()
            reference_rows = (
                await connection.execute(
                    text("select material_id from timeline_material_references")
                )
            ).all()
    finally:
        await engine.dispose()
    by_digest = {str(row.content_digest).strip(): row for row in rows}
    if set(by_digest) != {video_digest, audio_digest, image_digest, referenced_digest}:
        raise RuntimeError("LE-18 Control Plane material inventory drifted")
    described = by_digest[video_digest]
    if (
        described.kind != "video"
        or described.ai_description != "人工确认：蓝色测试画面，可用于片头。"
        or described.description_source != "user"
    ):
        raise RuntimeError("LE-18 human material description did not persist")
    if (
        by_digest[audio_digest].kind != "audio"
        or by_digest[image_digest].kind != "image"
    ):
        raise RuntimeError("LE-18 material kinds drifted")
    if deleted_digest in by_digest or len(reference_rows) != 1:
        raise RuntimeError("LE-18 deletion or reference protection did not persist")


def safe_registry_count(private_app_data: Path) -> int | None:
    try:
        document = json.loads(
            (private_app_data / "local-executor/state/material-paths.json").read_text(
                encoding="utf-8"
            )
        )
        entries = document.get("entries")
        return len(entries) if isinstance(entries, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return None


async def safe_failure_diagnostics(
    database_url: str, deleted_digest: str, private_app_data: Path
) -> str:
    """Return counts and booleans only; never return identifiers or content."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            material_count = int(
                (
                    await connection.execute(text("select count(*) from materials"))
                ).scalar_one()
            )
            deleted_present = bool(
                (
                    await connection.execute(
                        text(
                            "select count(*) from materials where content_digest = :digest"
                        ),
                        {"digest": deleted_digest},
                    )
                ).scalar_one()
            )
            reference_count = int(
                (
                    await connection.execute(
                        text("select count(*) from timeline_material_references")
                    )
                ).scalar_one()
            )
    finally:
        await engine.dispose()
    return (
        f"materials={material_count} deleted_present={deleted_present} "
        f"references={reference_count} local_mappings={safe_registry_count(private_app_data)}"
    )


def assert_no_private_evidence(output: str, sources: list[Path]) -> None:
    forbidden = {value for source in sources for value in (str(source), source.name)}
    if PRIVATE_OUTPUT.search(output) or any(value in output for value in forbidden):
        raise RuntimeError(
            "LE-18 acceptance output exposed private local material data"
        )


def main() -> int:
    require_hidden_configuration()
    private_app_data = app_data_directory()
    if private_app_data.exists():
        raise RuntimeError("Refusing to reuse an existing LE-18 App data directory")

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
    webdriver_port = unused_loopback_port()
    token, public_key = signed_bootstrap()
    environment = acceptance_environment()
    environment["TAURI_WEBDRIVER_PORT"] = str(webdriver_port)
    worker_marker = os.fspath(installed["material-video-worker"])
    worker_baseline = process_ids_matching(worker_marker)
    app_process: subprocess.Popen[bytes] | None = None
    restore_failed = False

    with tempfile.TemporaryDirectory(prefix="automation-tool-le18-") as temporary:
        sources = create_sources(ffmpeg, Path(temporary))
        unique_sources = [sources[0], *sources[2:]]
        initial_digests = [digest(source) for source in unique_sources]
        deleted_digest = initial_digests[-1]
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
                        "AUTOMATION_TOOL_LE18_BOOTSTRAP_TOKEN": token,
                        "AUTOMATION_TOOL_LE18_ENVIRONMENT_ID": ENVIRONMENT_ID,
                        "AUTOMATION_TOOL_LE18_ACCEPTANCE_PICKER": "1",
                        "TAURI_WEBDRIVER_PORT": str(webdriver_port),
                    }
                )
                for index, source in enumerate(sources, start=1):
                    prepared[f"AUTOMATION_TOOL_LE18_PICK_{index}"] = str(source)
                state = private_app_data / "local-executor" / "state"
                state.mkdir(mode=0o700, parents=True)
                if os.name != "nt":
                    state.chmod(0o700)

                build = subprocess.run(
                    [pnpm_executable(), "build:tauri:material-library-test"],
                    check=False,
                    capture_output=True,
                    cwd=FRONTEND,
                    env=prepared,
                    timeout=1_200,
                )
                if build.returncode != 0:
                    raise RuntimeError("LE-18 hidden App build failed")
                require_port_closed(webdriver_port)
                app_process = subprocess.Popen(
                    [
                        pnpm_executable(),
                        "exec",
                        "wdio",
                        "run",
                        "wdio.material-library.conf.ts",
                    ],
                    cwd=FRONTEND,
                    env=prepared,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=sys.platform != "win32",
                )
                try:
                    output_bytes, _ = app_process.communicate(timeout=600)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "LE-18 hidden App journey did not finish"
                    ) from error
                output = output_bytes.decode("utf-8", errors="replace")
                assert_no_private_evidence(output, sources)
                if app_process.returncode != 0:
                    print(output, end="")
                    print(
                        "LE-18 database diagnostics: "
                        + asyncio.run(
                            safe_failure_diagnostics(
                                prepared["AUTOMATION_TOOL_DATABASE_URL"],
                                deleted_digest,
                                private_app_data,
                            )
                        )
                    )
                    raise RuntimeError(
                        "LE-18 hidden App material-library journey failed"
                    )
                app_process = None
                asyncio.run(
                    assert_database_outcome(
                        prepared["AUTOMATION_TOOL_DATABASE_URL"], initial_digests
                    )
                )
                source_unchanged_after_delete(unique_sources[-1], deleted_digest)
                require_port_closed(webdriver_port)
        finally:
            if app_process is not None:
                terminate_app_process_tree(app_process)
            remaining = terminate_matching_processes(
                worker_marker, baseline=worker_baseline
            )
            if remaining:
                raise RuntimeError("LE-18 Worker cleanup left acceptance processes")
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
        raise RuntimeError("LE-18 failed to restore production Vite assets")
    print("LE-18 real App material-library acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
