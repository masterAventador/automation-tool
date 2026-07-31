#!/usr/bin/env python3
"""BM-08 native App editing, real render and Artifact lifecycle acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from desktop_e2e_prerequisites import video_studio_startup_harness
from prepare_video_runtime import install as install_video_runtime
from prepare_video_runtime import prepare as prepare_video_runtime
from process_inspection import process_ids_matching, terminate_matching_processes
from run_vf_06_acceptance import (
    APP_IDENTIFIER,
    DEBUG_APP_RESOURCE_ROOT,
    FRONTEND,
    TAURI_CONFIG,
    app_data_directory,
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / ".local/embedded-browser-video-studio/bm-08-evidence"
# The frame size this acceptance expects is derived, never restated. Hard-coding
# it here is what made this run red after the canvas contract raised its device
# scale factor: the render was correct and the expectation was stale. The
# contract is the one place that says how large a captured frame is.
_RENDER_CANVAS = json.loads(
    (ROOT / "contracts/video/motion-render-canvas.v1.json").read_text(encoding="utf-8")
)
# What this run measures is a *delivered film*, not one captured frame, and
# PC-18 split those into two declarations: a shot is captured on whatever stage
# its part declares (640x360 at factor 2 for the built-in template, 1920x1080
# for most of the catalog), while the finished file is always the film canvas.
# Reading `outputWidth` here kept deriving — from the wrong section — and went
# red on 2026-07-29 against a film that was correct at 1920x1080.
_FILM_CANVAS = _RENDER_CANVAS["film"]["byAspectRatio"]["16:9"]
EXPECTED_FRAME_WIDTH = _FILM_CANVAS["width"]
EXPECTED_FRAME_HEIGHT = _FILM_CANVAS["height"]


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 1_200,
) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=timeout)


def run_deterministic_gates() -> None:
    _run(["node", "--check", "workers/motion_composition/worker.mjs"])
    _run([sys.executable, "scripts/test_motion_video_render_sandbox.py"])
    _run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "motion_video_studio",
            "--locked",
        ]
    )
    _run(
        [
            pnpm_executable(),
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/features/video-studio/VideoStudio.test.tsx",
            "src/platform/tauri/material-video-studio-gateway.test.ts",
        ]
    )
    _run([pnpm_executable(), "--dir", "frontend", "typecheck"])


def _validate_private_app_state(private_app_data: Path) -> None:
    checkpoints = sorted(private_app_data.rglob("motion-render-job.checkpoint"))
    if len(checkpoints) != 2:
        raise RuntimeError(
            f"BM-08 expected two real RenderJob checkpoints, found {len(checkpoints)}"
        )
    snapshots = [
        json.loads(checkpoint.read_text(encoding="utf-8")) for checkpoint in checkpoints
    ]
    statuses = sorted(snapshot["status"] for snapshot in snapshots)
    if statuses != ["cancelled", "succeeded"]:
        raise RuntimeError(f"BM-08 real lifecycle statuses drifted: {statuses}")
    succeeded = next(
        snapshot for snapshot in snapshots if snapshot["status"] == "succeeded"
    )
    if (
        succeeded["progressPercent"] != 100
        or succeeded["artifactId"] is not None
        or succeeded["artifactSizeBytes"] is not None
    ):
        raise RuntimeError(
            "BM-08 deleted Artifact was not removed from its succeeded checkpoint"
        )
    artifacts = private_app_data / "video-workspaces-v1/artifacts"
    if not artifacts.is_dir() or any(artifacts.iterdir()):
        raise RuntimeError("BM-08 App deletion left an Artifact payload behind")
    if list(private_app_data.rglob("brand-motion-result.mp4")):
        raise RuntimeError(
            "BM-08 App deletion left a RenderJob MP4 working copy behind"
        )
    if list(private_app_data.rglob("frame-*.png")):
        raise RuntimeError("BM-08 renderer left frame scratch files behind")


def _print_checkpoint_diagnostics(private_app_data: Path) -> None:
    """Emit bounded, non-sensitive lifecycle facts before failure cleanup."""
    summaries: list[dict[str, object]] = []
    for checkpoint in sorted(private_app_data.rglob("motion-render-job.checkpoint")):
        try:
            snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summaries.append({"checkpoint": "unreadable"})
            continue
        summaries.append(
            {
                "status": snapshot.get("status"),
                "progressPercent": snapshot.get("progressPercent"),
                "failureCode": snapshot.get("failureCode"),
            }
        )
    print(
        "BM-08 checkpoint diagnostics:",
        json.dumps(summaries, ensure_ascii=False),
        file=sys.stderr,
    )


def _run_desktop_acceptance(
    evidence_video: Path,
) -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("BM-08 acceptance must use its hidden isolated App")
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = {
        key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"
    }
    environment.update(
        {
            "TAURI_WEBDRIVER_PORT": str(port),
            "AUTOMATION_TOOL_BM08_EVIDENCE_VIDEO": str(evidence_video),
        }
    )
    process_markers = (
        str(private_app_data),
        str(DEBUG_APP_RESOURCE_ROOT / "motion-video-worker"),
        str(DEBUG_APP_RESOURCE_ROOT / "media-toolchain"),
    )
    process_baselines = {
        marker: process_ids_matching(marker) for marker in process_markers
    }
    process_residue: list[str] = []
    try:
        with video_studio_startup_harness(
            private_app_data,
            environment=environment,
        ) as environment:
            _run(
                [pnpm_executable(), "build:tauri:video-studio-test"],
                cwd=FRONTEND,
                env=environment,
            )
            require_port_closed(port)
            try:
                _run(
                    [
                        pnpm_executable(),
                        "exec",
                        "wdio",
                        "run",
                        "wdio.video-studio.conf.ts",
                        "--spec",
                        "./e2e-tauri/motion-video-native.spec.ts",
                    ],
                    cwd=FRONTEND,
                    env=environment,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                _print_checkpoint_diagnostics(private_app_data)
                raise
            require_port_closed(port)
            _validate_private_app_state(private_app_data)
    finally:
        for marker, baseline in process_baselines.items():
            unexpected = process_ids_matching(marker) - baseline
            if unexpected:
                remaining = terminate_matching_processes(marker, baseline=baseline)
                process_residue.append(
                    f"{marker}: started={sorted(unexpected)}, remaining={sorted(remaining)}"
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
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(port)
        if restore.returncode != 0:
            raise RuntimeError("BM-08 failed to restore production Vite assets")
        if process_residue:
            raise RuntimeError(
                "BM-08 App exit left owned process residue: "
                + "; ".join(process_residue)
            )


def _inspect_video(
    video: Path,
    evidence: Path,
    ffmpeg: Path,
    ffprobe: Path,
) -> None:
    if not video.is_file() or video.stat().st_size <= 10_000:
        raise RuntimeError("BM-08 real App did not export a usable evidence MP4")
    probe = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,avg_frame_rate,nb_read_frames:format=duration,size",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    metadata = json.loads(probe.stdout)
    stream = metadata["streams"][0]
    duration = float(metadata["format"]["duration"])
    if (
        stream["codec_name"] != "h264"
        or stream["width"] != EXPECTED_FRAME_WIDTH
        or stream["height"] != EXPECTED_FRAME_HEIGHT
        or stream["pix_fmt"] != "yuv420p"
        or stream["avg_frame_rate"] != "30/1"
        or int(stream["nb_read_frames"]) != 90
        or not 2.9 <= duration <= 3.1
    ):
        raise RuntimeError(f"BM-08 MP4 contract drifted: {metadata}")
    (evidence / "ffprobe.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    scenes: list[Path] = []
    for index, timestamp in enumerate(("0.20", "1.20", "2.20"), start=1):
        scene = evidence / f"scene-{index}.png"
        _run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                timestamp,
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-y",
                str(scene),
            ],
            timeout=60,
        )
        scenes.append(scene)
    digests = [hashlib.sha256(scene.read_bytes()).hexdigest() for scene in scenes]
    if len(set(digests)) != 3:
        raise RuntimeError("BM-08 timeline seek did not produce three distinct scenes")
    _run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(scenes[0]),
            "-i",
            str(scenes[1]),
            "-i",
            str(scenes[2]),
            "-filter_complex",
            "[0:v][1:v][2:v]hstack=inputs=3",
            "-frames:v",
            "1",
            "-y",
            str(evidence / "contact-sheet.png"),
        ],
        timeout=60,
    )
    (evidence / "frame-sha256.json").write_text(
        json.dumps(
            {scene.name: digest for scene, digest in zip(scenes, digests, strict=True)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def require_deliverables() -> None:
    required = (
        ROOT / "frontend/src-tauri/src/motion_video_studio.rs",
        ROOT / "frontend/src-tauri/tests/motion_video_studio.rs",
        ROOT / "frontend/e2e-tauri/motion-video-native.spec.ts",
        ROOT / "workers/motion_composition/worker.mjs",
        ROOT / "docs/development/BM-08.md",
    )
    missing = [
        path.relative_to(ROOT).as_posix() for path in required if not path.is_file()
    ]
    if missing:
        raise RuntimeError(f"BM-08 missing deliverables: {', '.join(missing)}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| BM-08 |")]
    if len(rows) != 1 or not any(
        rows[0].endswith(f"| {status} |")
        for status in ("🚧 实现中", "🔍 待验收", "✅ 已完成")
    ):
        raise RuntimeError("BM-08 roadmap row is missing, duplicated or inactive")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-directory", type=Path)
    arguments = parser.parse_args()
    require_deliverables()
    evidence = (arguments.evidence_directory or DEFAULT_EVIDENCE).resolve()
    evidence_root = (ROOT / ".local/embedded-browser-video-studio").resolve()
    if not evidence.is_relative_to(evidence_root) or evidence == evidence_root:
        raise RuntimeError(
            "BM-08 evidence directory must be a child of the project .local evidence root"
        )
    if evidence.exists():
        shutil.rmtree(evidence)
    evidence.mkdir(parents=True)
    evidence_video = evidence / "bm-08-real-app.mp4"
    run_deterministic_gates()
    platform = "windows" if sys.platform == "win32" else "macos"
    runtime_names = ("media-toolchain", "motion-video-worker")
    staging = prepare_video_runtime(
        platform=platform,
        only=runtime_names,
    )
    installed = install_video_runtime(
        staging=staging,
        resource_root=DEBUG_APP_RESOURCE_ROOT,
        only=runtime_names,
        platform=platform,
    )
    suffix = ".exe" if os.name == "nt" else ""
    toolchain = installed["media-toolchain"]
    ffmpeg = (toolchain / f"bin/ffmpeg{suffix}").resolve(strict=True)
    ffprobe = (toolchain / f"bin/ffprobe{suffix}").resolve(strict=True)
    _run_desktop_acceptance(evidence_video)
    _inspect_video(evidence_video, evidence, ffmpeg, ffprobe)
    print(
        "BM-08 native App acceptance passed; visual evidence:",
        evidence / "contact-sheet.png",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
