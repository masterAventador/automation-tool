#!/usr/bin/env python3
"""BM-16 determinism and release acceptance (macOS scope).

Runs the aggregated deterministic gates, composes the locked 134-item release
directory, then drives the real embedded Chromium through the production
worker for: a per-item render sweep over all 134 catalog parts, a 12-style
manual-template render sweep, and a same-input double-run frame-digest
comparison. Also verifies the first release has no URL-entry or scraping
entry point. Windows package acceptance and sleep/resume coverage are
recorded as pending evidence in the task ledger, never silently skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_embedded_chromium_staging import (
    CHROMIUM_CONTRACT,
    DEFAULT_ARCHIVES,
    build_staging,
    load_staging_contract,
)
from run_bm_04_acceptance import current_target_id
from test_motion_video_render_adapter import (
    WorkerSession,
    bootstrap_document,
    expect_ready,
    render_browser_document,
)
from test_motion_video_render_sandbox import (
    SANDBOX_CPU_PARALLELISM_MAXIMUM,
    sandbox_command_line,
    sandbox_spec,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".local/embedded-browser-video-studio/bm-16-evidence"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SWEEP_FRAMES = 2
STYLE_FRAMES = 3
DETERMINISM_FRAMES = 30
# Every render now pays a one-off warm-up (seek to zero, bounded image decode,
# two animation frames, one discarded capture) so the first kept frame is as
# settled as the rest. Both budgets are stall guards, not performance targets:
# the slowest sweep item plus that warm-up already exceeded 55s on this Mac,
# and a software-rasterising host (every Windows run here) is slower again.
SHORT_RENDER_BUDGET_SECONDS = 120
DETERMINISM_RENDER_BUDGET_SECONDS = 180


def _run(
    arguments: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 1_800,
) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=timeout)


def run_deterministic_gates() -> None:
    _run([sys.executable, "scripts/test_bm_16_acceptance_contract.py"])
    _run([sys.executable, "scripts/check_third_party_sources.py"])
    _run([sys.executable, "scripts/check_motion_catalog.py"])
    _run([sys.executable, "scripts/check_motion_catalog_ui_projection.py"])
    _run([sys.executable, "scripts/test_motion_catalog_ui_projection.py"])
    _run([sys.executable, "scripts/test_motion_video_render_sandbox.py"])
    _run([sys.executable, "scripts/test_motion_authoring_agent.py"])
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
    _run([sys.executable, "scripts/check_user_facing_branding.py"])
    _run([sys.executable, "scripts/check_embedded_browser_video_roadmap.py"])


def stage_release_directory(run_root: Path) -> Path:
    release = run_root / "release"
    _run([sys.executable, "scripts/build_offline_motion_catalog.py"])
    _run(
        [
            sys.executable,
            "scripts/build_motion_catalog_release.py",
            "--release-root",
            str(release),
        ]
    )
    _run(
        [
            sys.executable,
            "scripts/check_motion_catalog_release.py",
            "--release-root",
            str(release),
        ]
    )
    return release


def _stage_chromium(run_root: Path) -> tuple[Path, int]:
    target_id = current_target_id()
    contract = load_staging_contract(CHROMIUM_CONTRACT)
    target = contract.targets[target_id]
    if not target.buildable:
        raise RuntimeError(f"BM-16 Chromium target is not buildable: {target_id}")
    result = build_staging(
        contract=contract,
        target_id=target_id,
        archive_path=DEFAULT_ARCHIVES[target_id].resolve(strict=True),
        archive_sha256=target.archive_sha256,
        output=run_root / "chromium",
    )
    executable = (result.output / Path(*target.executable.split("/"))).resolve(
        strict=True
    )
    return executable, int(contract.browser_version.split(".")[0])


def _render_once(
    browser: Path,
    chromium_major: int,
    workspace: Path,
    entry: str,
    allowed_assets: list[str],
    frame_count: int,
    budget_seconds: int = SHORT_RENDER_BUDGET_SECONDS,
    window_end_millis: int = 3000,
) -> dict[str, object]:
    """One real sandboxed render; returns the success event and frame digests."""
    frames = workspace / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    job_id = str(uuid.uuid4())
    session = WorkerSession(
        bootstrap_document(
            str(workspace),
            render_browser_document(browser, major=chromium_major, timeout_seconds=45),
        ),
        os.environ.copy(),
    )
    try:
        expect_ready(session)
        specification = sandbox_spec(
            workspace,
            allowedAssets=allowed_assets,
            entryHtml=entry,
            frameCount=frame_count,
            # Wall clock is the stall guard. CPU seconds are summed across the
            # whole browser process tree, so a render that legitimately uses
            # several cores accrues them far faster; the sandbox contract states
            # that ceiling as the wall budget times the maximum declarable
            # average core occupancy, which is what a render sweep may use.
            maxCpuSeconds=budget_seconds * SANDBOX_CPU_PARALLELISM_MAXIMUM,
            maxDurationSeconds=budget_seconds,
            maxMemoryMegabytes=2048,
            maxOutputBytes=256 * 1024 * 1024,
            sourceStartMillis=0,
            sourceEndMillis=window_end_millis,
        )
        session.send_line(sandbox_command_line(job_id, specification))
        event = session.read_event()
        if event.get("event") != "worker.render.sandboxed":
            raise RuntimeError(f"render failed for {entry}: {event}")
        if event.get("framesCaptured") != frame_count:
            raise RuntimeError(f"{entry}: frame count drifted: {event}")
    finally:
        code, stderr = session.finish()
        if code != 0:
            raise RuntimeError(f"worker exited abnormally for {entry}: {stderr}")

    digests: list[str] = []
    for index in range(1, frame_count + 1):
        frame = frames / f"frame-{index:05d}.png"
        raw = frame.read_bytes()
        if not raw.startswith(PNG_MAGIC) or len(raw) < 1_000:
            raise RuntimeError(f"{entry}: frame {index} is not a plausible PNG")
        digests.append(hashlib.sha256(raw).hexdigest())
    shutil.rmtree(frames)
    return {"event": event, "frames": digests}


def _writable_release_copy(release: Path, destination: Path) -> Path:
    shutil.copytree(release, destination)
    for path in destination.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    return destination


def run_item_render_sweep(
    browser: Path, chromium_major: int, release: Path, run_root: Path
) -> dict[str, object]:
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    workspace = _writable_release_copy(release, run_root / "item-sweep")
    shared = sorted(
        # The sandbox contract is POSIX-relative and rejects backslashes, so a
        # native `str()` here makes every Windows sweep fail closed with
        # `render_sandbox_invalid` before a single item renders.
        path.relative_to(workspace).as_posix()
        for path in (workspace / "offline-deps").rglob("*")
        if path.is_file()
    )
    results: dict[str, object] = {}
    # PC-19 之后段必须自带时间窗，而本 sweep 一直用夹具默认的 [0, 3000ms] 渲每一个
    # 零件。零件的动作不必发生在前三秒——apple-money-count 声明 5 秒，数钱、闪绿、
    # 撒钱币都在后半段，于是三秒窗里它是静止的，被静帧门禁正确地拒了（2026-07-29
    # 实测，PC-19 之前这条 sweep 是 134/134 全过的）。窗口按目录声明的时长开；
    # 25 个 component 不声明时长（PC-01：duration 只在 109 个 block 上），回退 3 秒。
    catalog_durations = {
        entry["name"]: entry.get("duration")
        for entry in json.loads(
            (ROOT / "contracts/quality/motion-catalog.v1.json").read_text(
                encoding="utf-8"
            )
        )["items"]
    }
    for index, item in enumerate(manifest["items"], start=1):
        name = item["name"]
        files = list(item["files"])
        entries = [candidate for candidate in files if candidate.endswith(".html")]
        if not entries:
            raise RuntimeError(f"{name}: release item has no HTML entry")
        allowed = sorted(set(files + shared) - {entries[0]})
        if len(allowed) > 128:
            raise RuntimeError(f"{name}: allowlist exceeds the sandbox maximum")
        declared_seconds = catalog_durations.get(name)
        rendered = _render_once(
            browser,
            chromium_major,
            workspace,
            entries[0],
            allowed,
            SWEEP_FRAMES,
            window_end_millis=(
                int(declared_seconds * 1000) if declared_seconds else 3000
            ),
        )
        results[name] = {
            "frames": rendered["frames"],
            "blockedRequests": rendered["event"]["blockedRequests"],
        }
        print(f"[bm-16] item {index}/134 rendered: {name}", flush=True)
    if len(results) != 134:
        raise RuntimeError("BM-16 sweep must cover exactly 134 items")
    distinct = {digest for entry in results.values() for digest in entry["frames"]}
    if len(distinct) < 40:
        raise RuntimeError(
            "BM-16 sweep produced implausibly uniform frames; renders look blank"
        )
    return results


def run_style_render_sweep(
    browser: Path, chromium_major: int, run_root: Path
) -> dict[str, object]:
    styles_directory = run_root / "styles"
    environment = os.environ.copy()
    environment["AUTOMATION_TOOL_BM16_STYLE_SWEEP_DIR"] = str(styles_directory)
    _run(
        [
            "cargo",
            "test",
            "--manifest-path",
            "frontend/src-tauri/Cargo.toml",
            "--test",
            "motion_video_studio",
            "bm16_all_twelve",
            "--locked",
        ],
        env=environment,
    )
    compositions = sorted(styles_directory.glob("*.html"))
    if len(compositions) != 12:
        raise RuntimeError(f"expected 12 frozen styles, found {len(compositions)}")
    results: dict[str, object] = {}
    for composition in compositions:
        workspace = run_root / f"style-{composition.stem}"
        workspace.mkdir()
        shutil.copyfile(composition, workspace / "composition.html")
        rendered = _render_once(
            browser,
            chromium_major,
            workspace,
            "composition.html",
            [],
            STYLE_FRAMES,
        )
        digests = rendered["frames"]
        if len(set(digests)) != STYLE_FRAMES:
            raise RuntimeError(
                f"style {composition.stem}: timeline seek produced duplicate frames"
            )
        results[composition.stem] = digests
        print(f"[bm-16] style rendered: {composition.stem}", flush=True)
    return results


def run_double_run_determinism(
    browser: Path, chromium_major: int, run_root: Path
) -> dict[str, object]:
    composition = run_root / "styles/blue-professional.html"
    runs: list[list[str]] = []
    for attempt in (1, 2):
        workspace = run_root / f"determinism-{attempt}"
        workspace.mkdir()
        shutil.copyfile(composition, workspace / "composition.html")
        rendered = _render_once(
            browser,
            chromium_major,
            workspace,
            "composition.html",
            [],
            DETERMINISM_FRAMES,
            DETERMINISM_RENDER_BUDGET_SECONDS,
        )
        runs.append(list(rendered["frames"]))
    if runs[0] != runs[1]:
        drifted = [
            index
            for index, (first, second) in enumerate(zip(runs[0], runs[1]), start=1)
            if first != second
        ]
        raise RuntimeError(
            f"BM-16 double-run determinism failed on frames {drifted}"
        )
    return {"frameCount": DETERMINISM_FRAMES, "frames": runs[0]}


def verify_no_url_entry() -> None:
    spec = (ROOT / "frontend/e2e-tauri/motion-video-native.spec.ts").read_text(
        encoding="utf-8"
    )
    if "网址|URL|抓取" not in spec:
        raise RuntimeError(
            "BM-08 desktop spec lost its no-URL-entry page assertion"
        )
    offenders: list[str] = []
    for path in (ROOT / "frontend/src/features/video-studio").rglob("*.ts*"):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        if 'type="url"' in text or "http://" in text or "https://" in text:
            offenders.append(path.name)
        if "抓取" in text or "网址" in text:
            offenders.append(path.name)
    if offenders:
        raise RuntimeError(
            f"BM-16 found URL/scraping entry traces in the studio UI: {offenders}"
        )


def main() -> int:
    if sys.version_info < (3, 10):
        raise RuntimeError("BM-16 acceptance requires python3.10+ (use python3.12)")
    run_deterministic_gates()
    run_root = (
        ROOT / ".local/embedded-browser-video-studio" / f"ebvs-bm16-{os.getpid()}"
    )
    run_root.mkdir(parents=True, exist_ok=False)
    try:
        release = stage_release_directory(run_root)
        browser, chromium_major = _stage_chromium(run_root)
        verify_no_url_entry()
        style_results = run_style_render_sweep(browser, chromium_major, run_root)
        determinism = run_double_run_determinism(browser, chromium_major, run_root)
        item_results = run_item_render_sweep(
            browser, chromium_major, release, run_root
        )
        if EVIDENCE.exists():
            shutil.rmtree(EVIDENCE)
        EVIDENCE.mkdir(parents=True)
        (EVIDENCE / "bm-16-acceptance.json").write_text(
            json.dumps(
                {
                    "chromiumMajor": chromium_major,
                    "determinism": determinism,
                    "items": item_results,
                    "styles": style_results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
    survivors = subprocess.run(
        ["pgrep", "-f", str(run_root)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if survivors.stdout.strip():
        raise RuntimeError(
            "BM-16 left a staged Chromium or worker process running: "
            + survivors.stdout.strip()
        )
    print(
        "BM-16 macOS acceptance passed:",
        "134-item sweep, 12-style sweep, double-run determinism, no URL entry;",
        "evidence:",
        EVIDENCE / "bm-16-acceptance.json",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
