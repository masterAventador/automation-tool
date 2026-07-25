#!/usr/bin/env python3
"""BM-08 native App editing, real render and Artifact lifecycle acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from build_embedded_chromium_staging import build_staging, load_staging_contract
from run_bm_04_acceptance import current_target_id
from run_vf_06_acceptance import (
    APP_IDENTIFIER,
    FRONTEND,
    TAURI_CONFIG,
    app_data_directory,
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
)

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM_CONTRACT = ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
_EB_03_CACHE = ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"


def _first_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


DEFAULT_ARCHIVES = {
    # The EB-03 archive cache lives in the primary checkout's .local; resolve
    # it both from the primary checkout itself and from a wt/<task> worktree.
    "macos-arm64": _first_existing(
        ROOT / _EB_03_CACHE,
        ROOT.parent.parent / _EB_03_CACHE,
    ),
    "macos-x86_64": ROOT / ".local/eb-mac-x64/chrome-mac-x64.zip",
    "windows-x86_64": ROOT / ".local/eb-04-windows/chrome-win64.zip",
}
DEFAULT_EVIDENCE = (
    ROOT / ".local/embedded-browser-video-studio/bm-08-evidence"
)


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 1_200,
) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=timeout)


def _required_executable(name: str) -> Path:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"BM-08 acceptance requires {name}")
    return Path(executable).resolve(strict=True)


def _stage_chromium(archive: Path, run_root: Path) -> tuple[Path, int]:
    target_id = current_target_id()
    contract = load_staging_contract(CHROMIUM_CONTRACT)
    target = contract.targets[target_id]
    if not target.buildable:
        raise RuntimeError(f"BM-08 Chromium target is not buildable: {target_id}")
    result = build_staging(
        contract=contract,
        target_id=target_id,
        archive_path=archive,
        archive_sha256=target.archive_sha256,
        output=run_root / "staging",
    )
    executable = (result.output / Path(*target.executable.split("/"))).resolve(
        strict=True
    )
    return executable, int(contract.browser_version.split(".")[0])


def _write_worker_wrapper(run_root: Path) -> Path:
    if os.name == "nt":
        raise RuntimeError(
            "BM-08 local acceptance wrapper currently runs on macOS; "
            "Windows package acceptance remains in BM-16"
        )
    node = _required_executable("node")
    worker = (ROOT / "workers/motion_composition/worker.mjs").resolve(strict=True)
    wrapper = run_root / "motion-video-worker"
    wrapper.write_text(
        f"#!/bin/sh\nexec {shlex.quote(str(node))} {shlex.quote(str(worker))}\n",
        encoding="utf-8",
    )
    wrapper.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return wrapper.resolve(strict=True)


def _write_browser_wrapper(run_root: Path, browser: Path) -> Path:
    """Delay only browser startup inside the render request.

    The real 90-frame render finishes in about two seconds on Apple
    silicon, which loses the race against the App-side cancellation
    choreography that the first acceptance submission must exercise. The
    delay keeps the worker, the execution protocol, the staged Chromium
    and FFmpeg fully real while making the cancellation window
    deterministic on any machine speed.
    """
    wrapper = run_root / "motion-video-browser"
    wrapper.write_text(
        f'#!/bin/sh\nsleep 3\nexec {shlex.quote(str(browser))} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return wrapper.resolve(strict=True)


def _validate_ffmpeg(ffmpeg: Path) -> Path:
    completed = subprocess.run(
        [str(ffmpeg), "-version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    contract = json.loads(
        (ROOT / "contracts/video/ffmpeg-toolchain.v1.json").read_text(
            encoding="utf-8"
        )
    )
    version = contract["ffmpeg"]["version"]
    if not completed.stdout.startswith(f"ffmpeg version {version} "):
        raise RuntimeError(f"BM-08 requires locked FFmpeg {version}")
    ffprobe = ffmpeg.with_name("ffprobe")
    return ffprobe.resolve(strict=True)


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
    checkpoints = sorted(
        private_app_data.rglob("motion-render-job.checkpoint")
    )
    if len(checkpoints) != 2:
        raise RuntimeError(
            f"BM-08 expected two real RenderJob checkpoints, found {len(checkpoints)}"
        )
    snapshots = [
        json.loads(checkpoint.read_text(encoding="utf-8"))
        for checkpoint in checkpoints
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
        raise RuntimeError("BM-08 App deletion left a RenderJob MP4 working copy behind")
    if list(private_app_data.rglob("frame-*.png")):
        raise RuntimeError("BM-08 renderer left frame scratch files behind")


def _run_desktop_acceptance(
    browser: Path,
    chromium_major: int,
    worker: Path,
    ffmpeg: Path,
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
        key: value
        for key, value in os.environ.items()
        if key != "TAURI_WEBDRIVER_PORT"
    }
    environment.update(
        {
            "TAURI_WEBDRIVER_PORT": str(port),
            "AUTOMATION_TOOL_BM08_BROWSER": str(browser),
            "AUTOMATION_TOOL_BM08_CHROMIUM_MAJOR": str(chromium_major),
            "AUTOMATION_TOOL_BM08_WORKER": str(worker),
            "AUTOMATION_TOOL_BM08_FFMPEG": str(ffmpeg),
            "AUTOMATION_TOOL_BM08_EVIDENCE_VIDEO": str(evidence_video),
        }
    )
    try:
        _run(
            [pnpm_executable(), "build:tauri:video-studio-test"],
            cwd=FRONTEND,
            env=environment,
        )
        require_port_closed(port)
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
        require_port_closed(port)
        _validate_private_app_state(private_app_data)
    finally:
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
        or stream["width"] != 640
        or stream["height"] != 360
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
            {
                scene.name: digest
                for scene, digest in zip(scenes, digests)
            },
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
        path.relative_to(ROOT).as_posix()
        for path in required
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError(f"BM-08 missing deliverables: {', '.join(missing)}")
    roadmap = (
        ROOT / "docs/embedded-browser-video-studio-roadmap.md"
    ).read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| BM-08 |")]
    if len(rows) != 1 or not any(
        rows[0].endswith(f"| {status} |")
        for status in ("🚧 实现中", "🔍 待验收", "✅ 已完成")
    ):
        raise RuntimeError("BM-08 roadmap row is missing, duplicated or inactive")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--evidence-directory", type=Path)
    arguments = parser.parse_args()
    require_deliverables()
    target_id = current_target_id()
    archive = (
        arguments.archive or DEFAULT_ARCHIVES[target_id]
    ).resolve(strict=True)
    evidence = (
        arguments.evidence_directory or DEFAULT_EVIDENCE
    ).resolve()
    evidence_root = (
        ROOT / ".local/embedded-browser-video-studio"
    ).resolve()
    if not evidence.is_relative_to(evidence_root) or evidence == evidence_root:
        raise RuntimeError(
            "BM-08 evidence directory must be a child of the project .local evidence root"
        )
    if evidence.exists():
        shutil.rmtree(evidence)
    evidence.mkdir(parents=True)
    evidence_video = evidence / "bm-08-real-app.mp4"
    run_root = (
        ROOT
        / ".local/embedded-browser-video-studio"
        / f"ebvs-bm08-{os.getpid()}"
    )
    run_root.mkdir(parents=True, exist_ok=False)
    try:
        run_deterministic_gates()
        staged_browser, chromium_major = _stage_chromium(archive, run_root)
        browser = _write_browser_wrapper(run_root, staged_browser)
        worker = _write_worker_wrapper(run_root)
        ffmpeg = _required_executable("ffmpeg")
        ffprobe = _validate_ffmpeg(ffmpeg)
        _run_desktop_acceptance(
            browser,
            chromium_major,
            worker,
            ffmpeg,
            evidence_video,
        )
        _inspect_video(evidence_video, evidence, ffmpeg, ffprobe)
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
    if os.name != "nt":
        survivors = subprocess.run(
            ["pgrep", "-f", str(run_root)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if survivors.stdout.strip():
            raise RuntimeError(
                "BM-08 left a staged Chromium or Worker process running: "
                + survivors.stdout.strip()
            )
    print(
        "BM-08 native App acceptance passed; visual evidence:",
        evidence / "contact-sheet.png",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
