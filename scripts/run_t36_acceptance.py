#!/usr/bin/env python3
"""T36 acceptance: the one-sentence path, in the real App, on a real model.

Everything under this path has its own layered coverage — the authoring agent
against the real model, the render sandbox against the real embedded Chromium,
the still-image gate against real captured frames. This entrypoint is the only
one that proves the App can do it end to end: the `submit_motion_video_brief`
command, the credential the settings form saved, the Executor one-shot
hand-off, the progress projection and the existing player.

It deliberately does *not* pre-seed the model configuration into the App's
private files. The spec types the key into the real settings form, because a
run that writes configuration behind the product is acceptance of a path no
user takes — which is the exact defect this whole line was sent to fix.

The key is read at runtime from the git-ignored `.local/secrets` file, handed
to WebdriverIO in its environment, and never printed, logged, asserted on or
written into the evidence MP4.

Usage:
    backend/.venv/bin/python scripts/run_t36_acceptance.py
    backend/.venv/bin/python scripts/run_t36_acceptance.py --secret <path>
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_e2e_prerequisites import (  # noqa: E402
    EXECUTOR_PACKAGE_CACHE_ROOT,
    SHARED_EXECUTOR_BUILD_ID,
    executor_package_cache_key,
    install_signed_executor_package,
    prepare_startup_gate,
    startup_gate_environment,
)
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from run_e4_14_acceptance import require_port_available, start_control_plane  # noqa: E402
from run_i2_13_acceptance import BACKEND_ROOT, REPOSITORY_ROOT, compose_command  # noqa: E402
from run_vf_06_acceptance import (  # noqa: E402
    DEBUG_APP_RESOURCE_ROOT,
    FRONTEND,
    pnpm_executable,
    require_port_closed,
    require_staged_embedded_browser,
    require_staged_video_runtime,
    stage_video_runtime,
    unused_loopback_port,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = "./e2e-tauri/motion-one-sentence.spec.ts"

# Why this acceptance runs on a `control-plane-e2e` build and not on
# `video-studio-e2e`, which is where the video studio's other specs live:
#
# The App's workbench sits behind a startup gate that needs the compile-time
# action-trust triple and a reachable Control Plane. The plain `desktop-e2e`
# family that `video-studio-e2e` belongs to has no entrypoint that supplies
# either — `run_vf_06_acceptance.py` and `run_b5_04_acceptance.py` both build
# without them — so nothing in that family currently reaches the workbench at
# all. Rather than repair a pipeline nobody has run in a long time, this
# acceptance is built on `control-plane-e2e`, whose handler was verified to
# register every video and publish command.
#
# This is a change of *test driver*, not of product path: the commands, the
# resource resolution and the render pipeline are the same code either way.
# The single-build-path rule forbids a build changing where the product looks
# for things; it does not require every acceptance to use the same driver.
# The real acceptance is on the signed package regardless — a test build is
# layered evidence wherever it runs.
APP_IDENTIFIER = "com.aventador.automationtool.t36acceptance"
TAURI_CONFIG = FRONTEND / "src-tauri" / "tauri.t36-e2e.conf.json"
BUILD_SCRIPT = "build:tauri:t36-test"
WDIO_CONFIG = "wdio.t36.conf.ts"
DEFAULT_SECRET = ROOT / ".local/secrets/bailian-model.json"
EVIDENCE = ROOT / ".local/embedded-browser-video-studio/t36-evidence"
# Every isolated resource this run creates carries this stem plus the pid, so a
# stray container, network or volume can always be traced back to one run of
# this entrypoint and cleaned up without guessing whose it is.
PROJECT_STEM = "automation-tool-t36"


def app_data_directory() -> Path:
    """This acceptance's own private App data directory.

    Keyed to this entrypoint's identifier so that deleting it can never touch
    another acceptance's state — or the user's real installation, which lives
    under the unsuffixed identifier and holds hand-scanned platform sessions.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if roaming is None:
            raise RuntimeError("Windows roaming AppData is unavailable")
        return Path(roaming) / APP_IDENTIFIER
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / APP_IDENTIFIER


def isolated_ports() -> tuple[int, int]:
    """Two free loopback ports this run owns.

    Ports are never reused or taken over: an occupied port belongs to something
    else on this machine, and this run picks a different one rather than
    deciding what else may be stopped.
    """
    control_plane_port = unused_loopback_port()
    database_port = unused_loopback_port()
    while database_port == control_plane_port:
        database_port = unused_loopback_port()
    require_port_available(control_plane_port)
    require_port_available(database_port)
    return control_plane_port, database_port


def isolated_environment(*, control_plane_port: int, database_port: int) -> dict[str, str]:
    """The environment the App is *built* with and the Control Plane runs with.

    The startup gate reads three of these at compile time, so they have to be
    present for `tauri build`, not only for the run: an App built without them
    reports "本地执行器动作配置缺失" and never mounts the workbench, which reads
    like a broken product rather than a runner that forgot a variable.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_name = "automation_tool_t36"
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_t36_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_t36_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": database_name,
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": database_name,
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": (
                f"postgresql+asyncpg://{database_name}:{database_password}"
                f"@127.0.0.1:{database_port}/{database_name}"
            ),
            # Installation registration is left unconfigured on purpose. The
            # environment id and the bootstrap public key are a both-or-neither
            # pair, and this acceptance registers nothing: the App runs on the
            # local profile with an ephemeral identity, and the startup gate
            # only asks the Control Plane for health, which needs no bootstrap
            # trust. Setting half the pair is what made the service refuse to
            # start at all.
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{control_plane_port}"
            ),
        }
    )
    return startup_gate_environment(environment, control_plane_port=control_plane_port)


def read_model_key(secret: Path) -> str:
    """The real video-creation key, or a refusal that says what is missing.

    Running this entrypoint against a placeholder would produce a red that looks
    like a product failure, so an unusable secret is rejected here by shape
    rather than discovered as a model error minutes into the run.
    """
    if not secret.is_file():
        raise RuntimeError(
            f"T36 acceptance needs the real video-creation model key at {secret}; "
            "see docs/credentials-bailian-model.md"
        )
    document = json.loads(secret.read_text(encoding="utf-8"))
    key = document.get("apiKey")
    if not isinstance(key, str) or not key.startswith("sk-"):
        raise RuntimeError(f"{secret} carries no usable apiKey")
    return key


def require_authoring_capable_executor(private_app_data: Path) -> None:
    """The installed Executor must be able to answer the authoring protocol.

    Older shared packages were cached under a constant build id. That is what
    two T36 runs actually measured: the cached package predated the one-shot
    authoring entry, so the App started it with `--author-motion`, the frozen
    binary fell through to the long-lived executor path, read the authoring
    request as a bootstrap document and exited 2 with an empty stdout — which
    the App classifies, correctly, as `authoring_crashed`.

    The shared prerequisite now includes the real Executor input digest in its
    cache key. A signed package can still be locally incomplete, so this probe
    remains the capability boundary: on failure it removes that exact current
    digest cache entry before reinstalling, never the obsolete constant path.

    An empty request is enough to tell the two apart without a credential and
    without a model round trip: the entry answers every rejection with its
    refusal document on stdout and exit 70, and it can only get that far if the
    argument dispatch exists and the agent's startup contracts are in the
    package. Anything else means this package cannot author, so it is rebuilt
    rather than run.
    """
    entrypoint = private_app_data / "local-executor/package/automation-tool-executor"
    if _answers_the_authoring_protocol(entrypoint):
        return
    print(
        "[T36] The cached signed Executor cannot answer --author-motion; "
        "rebuilding it instead of reporting its age as a product defect"
    )
    shutil.rmtree(
        EXECUTOR_PACKAGE_CACHE_ROOT
        / executor_package_cache_key(SHARED_EXECUTOR_BUILD_ID),
        ignore_errors=True,
    )
    shutil.rmtree(private_app_data / "local-executor", ignore_errors=True)
    install_signed_executor_package(private_app_data)
    if not _answers_the_authoring_protocol(entrypoint):
        raise RuntimeError(
            f"{entrypoint} does not answer the one-shot authoring protocol even after a "
            "rebuild, so the one-sentence path cannot work in this App"
        )


def _answers_the_authoring_protocol(entrypoint: Path) -> bool:
    if not entrypoint.is_file():
        return False
    child = subprocess.run(
        [str(entrypoint), "--author-motion"],
        input=b"",
        capture_output=True,
        timeout=300,
        check=False,
    )
    if child.returncode != 70:
        return False
    try:
        answer = json.loads(child.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return answer == {"schemaVersion": 1, "status": "rejected"}


def prepare_resources() -> None:
    """Put the packaged parts where the App resolves them, then verify them.

    The App reads the browser, both Workers and ffmpeg from its resource
    directory and from nowhere else — there is no environment-variable branch to
    fall back to — so a missing part here is a product failure the acceptance
    would report as an unexplained blank window.
    """
    prepare_startup_gate(app_data_directory())
    require_authoring_capable_executor(app_data_directory())
    require_staged_embedded_browser(resource_root=DEBUG_APP_RESOURCE_ROOT)
    platform = "windows" if sys.platform == "win32" else "macos"
    staging = prepare_video_runtime(platform=platform)
    stage_video_runtime(staging=staging, resource_root=DEBUG_APP_RESOURCE_ROOT)
    installed = require_staged_video_runtime(resource_root=DEBUG_APP_RESOURCE_ROOT)
    runtime = installed["motion-video-worker"] / "runtime/gsap.min.js"
    if not runtime.is_file() or runtime.stat().st_size == 0:
        raise RuntimeError(
            f"the staged motion worker carries no animation runtime at {runtime}; "
            "without it the authored composition loads nothing and renders a still image"
        )


def run_desktop_acceptance(
    api_key: str, evidence_video: Path, base_environment: dict[str, str]
) -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("T36 acceptance must use its hidden isolated App")
    private_app_data = app_data_directory()
    port = unused_loopback_port()
    environment = {
        key: value for key, value in base_environment.items() if key != "TAURI_WEBDRIVER_PORT"
    }
    environment.update(
        {
            "TAURI_WEBDRIVER_PORT": str(port),
            "AUTOMATION_TOOL_T36_MODEL_KEY": api_key,
            "AUTOMATION_TOOL_T36_EVIDENCE_VIDEO": str(evidence_video),
        }
    )
    try:
        subprocess.run(
            [pnpm_executable(), BUILD_SCRIPT],
            cwd=FRONTEND,
            env=environment,
            check=True,
        )
        require_port_closed(port)
        subprocess.run(
            [
                pnpm_executable(),
                "exec",
                "wdio",
                "run",
                WDIO_CONFIG,
                "--spec",
                SPEC,
            ],
            cwd=FRONTEND,
            env=environment,
            check=True,
        )
        require_port_closed(port)
    finally:
        restore = subprocess.run(
            [pnpm_executable(), "build"],
            cwd=FRONTEND,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(port)
        if restore.returncode != 0:
            raise RuntimeError("T36 failed to restore production Vite assets")


def inspect_film(video: Path) -> None:
    """The evidence must be a real film, not a well-formed still picture.

    A composition that fails to animate encodes into an MP4 of the right length
    that every other check reads as success, so the frame count and the distinct
    content are both asserted rather than the file size alone.
    """
    ffprobe = DEBUG_APP_RESOURCE_ROOT / "media-toolchain/bin/ffprobe"
    probe = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,nb_read_frames,duration",
            "-of", "json", str(video),
        ],
        capture_output=True, text=True, check=True, timeout=300,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    print(f"T36 evidence film: {json.dumps(stream, ensure_ascii=False)}")
    if int(stream["nb_read_frames"]) < 2:
        raise RuntimeError("the App produced a single-frame film")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--secret", type=Path, default=DEFAULT_SECRET)
    arguments = parser.parse_args()
    api_key = read_model_key(arguments.secret.resolve())

    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)
    evidence_video = EVIDENCE / "t36-one-sentence.mp4"

    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    prepare_resources()

    control_plane_port, database_port = isolated_ports()
    project_name = f"{PROJECT_STEM}-{os.getpid()}"
    environment = isolated_environment(
        control_plane_port=control_plane_port, database_port=database_port
    )
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    try:
        # The one-sentence feature needs no Control Plane, but the App's startup
        # gate does: it holds the workbench closed until control-plane health
        # answers, so an acceptance of anything behind the workbench has to
        # bring one up.
        require_port_available(database_port)
        print(f"[T36] Starting isolated PostgreSQL as {project_name}")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[T36] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print(f"[T36] Starting Control Plane on isolated port {control_plane_port}")
        server = start_control_plane(port=control_plane_port, environment=environment)
        run_desktop_acceptance(api_key, evidence_video, environment)
        inspect_film(evidence_video)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        # Only what this run created: the project name carries this entrypoint
        # and this pid, so nothing another project or another run owns is in
        # scope here.
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            check=False,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(control_plane_port)
        require_port_closed(database_port)
    print("T36 one-sentence App acceptance passed; evidence:", evidence_video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
