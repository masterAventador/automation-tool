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
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_e2e_prerequisites import prepare_startup_gate  # noqa: E402
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from run_vf_06_acceptance import (  # noqa: E402
    APP_IDENTIFIER,
    DEBUG_APP_RESOURCE_ROOT,
    FRONTEND,
    TAURI_CONFIG,
    app_data_directory,
    pnpm_executable,
    require_port_closed,
    require_staged_embedded_browser,
    require_staged_video_runtime,
    stage_video_runtime,
    unused_loopback_port,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = "./e2e-tauri/motion-one-sentence.spec.ts"
DEFAULT_SECRET = ROOT / ".local/secrets/bailian-model.json"
EVIDENCE = ROOT / ".local/embedded-browser-video-studio/t36-evidence"


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


def prepare_resources() -> None:
    """Put the packaged parts where the App resolves them, then verify them.

    The App reads the browser, both Workers and ffmpeg from its resource
    directory and from nowhere else — there is no environment-variable branch to
    fall back to — so a missing part here is a product failure the acceptance
    would report as an unexplained blank window.
    """
    prepare_startup_gate(app_data_directory())
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


def run_desktop_acceptance(api_key: str, evidence_video: Path) -> None:
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
        key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"
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
            [pnpm_executable(), "build:tauri:video-studio-test"],
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
                "wdio.video-studio.conf.ts",
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
    run_desktop_acceptance(api_key, evidence_video)
    inspect_film(evidence_video)
    print("T36 one-sentence App acceptance passed; evidence:", evidence_video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
