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
# 2 帧（0 与 D/2）会被周期性动画混叠：transitions-radial 在 t=0 与 t=10 逐字节
# 同帧（实拍对照：0/5/10/15 四点三个不同摘要），两点采样正好踩在同相位上被静帧
# 门禁误拒。4 帧落在 0、D/4、D/2、3D/4，同一零件三个不同摘要能过；采样永远证明
# 不了「所有帧都动」，只把碰撞概率压到目录现有零件实测不再触发。
SWEEP_FRAMES = 4
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
    _run([sys.executable, "scripts/test_motion_catalog_render_exclusions.py"])
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
    spec_overrides: dict[str, object] | None = None,
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
            **(spec_overrides or {}),
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
    # 白名单按引用收集，与产品的工作区写入器同一份遍历（PC-05 的决定）。
    # PC-13 把共享依赖加了字体之后，「零件文件 + 全部共享依赖」的老算法在
    # caption-texture 上超过了沙箱的 128 上限——产品早就不整树拷贝了，sweep 跟上。
    from automation_tool.executor.motion_authoring.part_workspace import (
        referenced_assets,
    )

    results: dict[str, object] = {}
    # PC-19 之后段必须自带时间窗，而本 sweep 一直用夹具默认的 [0, 3000ms] 渲每一个
    # 零件。零件的动作不必发生在前三秒——apple-money-count 声明 5 秒，数钱、闪绿、
    # 撒钱币都在后半段，于是三秒窗里它是静止的，被静帧门禁正确地拒了（2026-07-29
    # 实测，PC-19 之前这条 sweep 是 134/134 全过的）。窗口按目录声明的时长开；
    # 25 个 component 不声明时长（PC-01：duration 只在 109 个 block 上），回退 3 秒。
    catalog_items = json.loads(
        (ROOT / "contracts/quality/motion-catalog.v1.json").read_text(encoding="utf-8")
    )["items"]
    catalog_durations = {entry["name"]: entry.get("duration") for entry in catalog_items}
    # 画布同理（PC-05：渲染画布随请求走，零件在自己声明的舞台上渲）。控制实验
    # 2026-07-29：apple-money-count 在 1920×1080 下五个 seek 点五个画面，在模板的
    # 640×360 下前三秒是同一张——模板画布看到的是它舞台的静止角落，静帧门禁拒得对。
    # 25 个 component 不声明尺寸，保持模板画布。
    catalog_dimensions = {
        entry["name"]: entry.get("dimensions") for entry in catalog_items
    }
    # 4 个尚不能独立渲染的项根因全部在内容/上游侧（PC-21 §18.6 逐类定性），按
    # 带原因的豁免清单跳过——清单卫生由 test_motion_catalog_render_exclusions
    # 守着（真实存在、类别封闭、理由非空），不是无声截断：跳过逐项打印，
    # 收尾核对「渲染 + 豁免 = 134」。内容修好一项就从清单划掉一项。
    exclusions: dict[str, dict[str, str]] = json.loads(
        (
            ROOT / "contracts/quality/motion-catalog-standalone-render-exclusions.v1.json"
        ).read_text(encoding="utf-8")
    )["items"]
    excluded_count = 0
    for index, item in enumerate(manifest["items"], start=1):
        name = item["name"]
        if name in exclusions:
            excluded_count += 1
            print(
                f"[bm-16] item {index}/134 excluded"
                f" ({exclusions[name]['class']}): {name}",
                flush=True,
            )
            continue
        files = list(item["files"])
        entries = [candidate for candidate in files if candidate.endswith(".html")]
        if not entries:
            raise RuntimeError(f"{name}: release item has no HTML entry")
        entry_path = workspace / entries[0]
        referenced = referenced_assets(
            entry_path.read_text(encoding="utf-8"),
            catalog_root=workspace,
            origin=entry_path.parent,
            # 发布树里存在死引用（实测 video.mp4），渲染时沙箱拦截并计数——
            # sweep 沿用这个语义；工作区写入器保持拒绝（封闭树是产品的保证）。
            on_missing="skip",
        )
        allowed = sorted((set(files) | set(referenced)) - {entries[0]})
        if len(allowed) > 128:
            raise RuntimeError(f"{name}: allowlist exceeds the sandbox maximum")
        declared_seconds = catalog_durations.get(name)
        declared_dimensions = catalog_dimensions.get(name)
        overrides: dict[str, object] = {
            "sourceStartMillis": 0,
            "sourceEndMillis": int(declared_seconds * 1000) if declared_seconds else 3000,
        }
        # component 是贴进宿主的片段，自己没有时间轴（PC-01：duration/dimensions 只在
        # 109 个 block 上）。单独渲它，静止就是真实状态——「必须动」对它构造性地
        # 不成立，所以渲一帧：一帧就是它的全部真相，worker 的静帧判定也只在
        # 帧数达到比较下限时才生效。block 仍渲 SWEEP_FRAMES 帧并要求动起来。
        frame_count = SWEEP_FRAMES if declared_seconds else 1
        if declared_dimensions:
            overrides["canvas"] = {
                # Factor 1：零件的舞台就是输出分辨率（PC-05 的装配同一条规则）。
                "deviceScaleFactor": 1,
                "height": declared_dimensions["height"],
                "width": declared_dimensions["width"],
            }
        rendered = _render_once(
            browser,
            chromium_major,
            workspace,
            entries[0],
            allowed,
            frame_count,
            spec_overrides=overrides,
        )
        results[name] = {
            "frames": rendered["frames"],
            "blockedRequests": rendered["event"]["blockedRequests"],
        }
        print(f"[bm-16] item {index}/134 rendered: {name}", flush=True)
    if len(results) + excluded_count != 134:
        raise RuntimeError("BM-16 sweep must cover exactly 134 items")
    if excluded_count != len(exclusions):
        raise RuntimeError("BM-16 exclusion list names items the manifest lacks")
    print(
        f"[bm-16] item sweep: {len(results)} rendered, {excluded_count} excluded",
        flush=True,
    )
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
