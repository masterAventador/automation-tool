#!/usr/bin/env python3
"""BM-05 real-model acceptance for the restricted MotionAuthoringAgent.

Drives the *real* video-creation model (Bailian qwen3.7-max via the OpenAI
compatible endpoint) through the production authoring path: one sentence brief
→ DESIGN / SCRIPT / STORYBOARD → the locally rendered composition →
lint / check / snapshot → submit RenderJob into the RenderJob private
directory. It asserts:

- the model produced the three closed artifacts and the locally rendered
  composition passes the static gates, with no remote reference anywhere;
- everything landed inside the RenderJob workspace and nowhere else;
- the RenderJob submission lines up with the BM-04 sandbox spec (the frame
  render itself is out of scope — BM-08/BM-16 own the real user path);
- the one-sentence path is *unavailable* when no model is configured.

Since T92 the composition document is rendered locally from the model's beats
rather than written by the model, so this acceptance is what proves the *real*
model still answers the narrowed contract — the deterministic tests can only
prove the parser.

The api key is read at runtime from the git-ignored ``.local/secrets`` file and
never printed, logged, asserted on, or written into any artifact. No browser is
launched. The isolated workspace is removed on every exit path.

Usage:
    python3 scripts/run_bm_05_acceptance.py
    python3 scripts/run_bm_05_acceptance.py --secret <path> --keep
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from automation_tool.executor.motion_authoring.agent import (  # noqa: E402
    AuthoringWorkspace,
    MotionAuthoringAgent,
    MotionAuthoringTools,
    MotionAuthoringUnavailable,
    MotionBrief,
    call_video_creation_model,
    lint_composition,
    load_locked_authoring_workflow,
    load_video_creation_model_config,
)

CATALOG = ROOT / "contracts/video/bailian-model-catalog.v1.json"
WORKFLOW_CONTRACT = ROOT / "contracts/video/motion-authoring-workflow.v1.json"
VENDOR_ROOT = ROOT / "vendor/hyperframes"
DEFAULT_SECRET = ROOT / ".local/secrets/bailian-model.json"
RUNTIME_ASSET = "runtime/gsap.min.js"
BRIEF_TEXT = "用蓝色商务风做一段本周销售增长说明"


def _fail(message: str) -> None:
    raise SystemExit(f"BM-05 acceptance failed: {message}")


def _seed_workspace(root: Path) -> AuthoringWorkspace:
    asset = root / RUNTIME_ASSET
    asset.parent.mkdir(parents=True, exist_ok=True)
    # A local offline runtime stub stands in for the BM-12 OfflineMotionCatalog
    # GSAP bundle; BM-05 verifies structure statically, not real playback.
    asset.write_text("/* offline gsap runtime (stub for BM-05 static gate) */\n", encoding="utf-8")
    return AuthoringWorkspace(root)


def _assert_unavailable_without_model(root: Path) -> None:
    workspace = _seed_workspace(root)
    agent = MotionAuthoringAgent(
        workspace=workspace,
        tools=MotionAuthoringTools(workspace),
        workflow=load_locked_authoring_workflow(
            vendor_root=VENDOR_ROOT, contract_path=WORKFLOW_CONTRACT
        ),
        model_config=None,
        model_call=call_video_creation_model,
    )
    try:
        agent.author(
            MotionBrief(text=BRIEF_TEXT, aspect_ratio="16:9", duration_seconds=6, language="zh")
        )
    except MotionAuthoringUnavailable:
        print("[ok] one-sentence authoring is unavailable without a configured model")
        return
    _fail("agent authored a video without a configured model")


def _run_real_authoring(root: Path) -> None:
    config = load_video_creation_model_config(catalog_path=CATALOG, secret_path=Path(SECRET_PATH))
    if config is None:
        _fail("no video-creation model secret configured; cannot run real acceptance")
    print(f"[info] real video-creation model: {config.model_id}")

    workspace = _seed_workspace(root)
    agent = MotionAuthoringAgent(
        workspace=workspace,
        tools=MotionAuthoringTools(workspace),
        workflow=load_locked_authoring_workflow(
            vendor_root=VENDOR_ROOT, contract_path=WORKFLOW_CONTRACT
        ),
        model_config=config,
        model_call=call_video_creation_model,
    )
    brief = MotionBrief(text=BRIEF_TEXT, aspect_ratio="16:9", duration_seconds=6, language="zh")
    result = agent.author(brief)

    if not result.lint.ok:
        _fail(f"composition failed lint: {sorted(result.lint.codes())}")
    if not result.check.ok:
        _fail(f"composition failed check: {sorted(result.check.codes())}")

    # Every produced file must live inside the RenderJob workspace.
    for relative in ("DESIGN.json", "SCRIPT.json", "STORYBOARD.json",
                     result.composition_path, "renderjob.json"):
        target = (root / relative).resolve()
        if not target.is_file():
            _fail(f"expected artifact missing: {relative}")
        try:
            target.relative_to(root.resolve())
        except ValueError:
            _fail(f"artifact escaped the workspace: {relative}")

    # Re-lint the persisted composition independently: zero remote references.
    html = (root / result.composition_path).read_text(encoding="utf-8")
    independent = lint_composition(
        html,
        allowed_assets=workspace.seeded_assets(),
        max_bytes=512_000,
        entry_path=result.composition_path,
    )
    if not independent.ok:
        _fail(f"persisted composition is not offline-clean: {sorted(independent.codes())}")
    for scheme in ("http://", "https://", "ws://", "wss://"):
        if scheme in html.lower():
            _fail(f"composition contains a remote reference: {scheme}")

    spec = result.submission.to_sandbox_spec(str(root))
    expected_keys = {
        "workspace", "entryHtml", "allowedAssets", "frameCount",
        "maxDurationSeconds", "maxCpuSeconds", "maxMemoryMegabytes", "maxOutputBytes",
    }
    if set(spec) != expected_keys:
        _fail(f"submission spec keys drifted: {sorted(spec)}")
    # The render request the App finally sends adds `cancelMarker`; the
    # authoring submission must not, because that field decides whether the
    # user's cancel button can reach the render.
    if "cancelMarker" in spec:
        _fail("the authoring submission must not name the cancellation marker")
    if spec["frameCount"] != brief.duration_seconds * 30:
        _fail(f"unexpected frame count: {spec['frameCount']}")
    if RUNTIME_ASSET not in spec["allowedAssets"]:
        _fail("runtime asset missing from submission allowlist")

    print("[ok] DESIGN style preset:", result.design.style_preset_id)
    print("[ok] SCRIPT one message:", result.script.one_message)
    print("[ok] STORYBOARD beats:", len(result.storyboard.beats))
    print("[ok] composition:", result.composition_path,
          f"({len(html)} chars, offline-clean, seekable)")
    print("[ok] snapshot plan:", result.snapshot.frame_count, "frames @",
          result.snapshot.fps, "fps")
    print("[ok] submitted RenderJob:", result.submission.job_id)
    print("[ok] sandbox spec:", {k: spec[k] for k in ("frameCount", "maxDurationSeconds")})


SECRET_PATH = str(DEFAULT_SECRET)


def main() -> int:
    global SECRET_PATH
    parser = argparse.ArgumentParser(description="BM-05 real-model acceptance")
    parser.add_argument("--secret", default=str(DEFAULT_SECRET))
    parser.add_argument("--keep", action="store_true", help="keep the isolated workspace")
    args = parser.parse_args()
    SECRET_PATH = args.secret

    run_root = ROOT / ".local/embedded-browser-video-studio" / f"ebvs-bm05-{os.getpid()}"
    render_root = run_root / "renderjob"
    unavailable_root = run_root / "unavailable"
    render_root.mkdir(parents=True, exist_ok=True)
    unavailable_root.mkdir(parents=True, exist_ok=True)
    try:
        _assert_unavailable_without_model(unavailable_root)
        _run_real_authoring(render_root)
    finally:
        if not args.keep:
            shutil.rmtree(run_root, ignore_errors=True)
            print("[cleanup] removed isolated workspace")
    print("BM-05 acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
