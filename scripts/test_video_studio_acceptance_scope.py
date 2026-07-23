#!/usr/bin/env python3
"""Deterministic scope checks for the video-studio desktop acceptance entrypoints.

Reproduces the VF-06 / IM-05 contradiction: `run_vf_06_acceptance.py` used to run
`wdio.video-studio.conf.ts` without `--spec`, which pulls in
`material-video-webui.spec.ts`; that spec requires a real frozen Python Worker
injected through `AUTOMATION_TOOL_IM05_WORKER`, which the VF-06 entrypoint never
builds, so a full VF-06 re-run deterministically failed at that spec.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WDIO_CONFIG = ROOT / "frontend/wdio.video-studio.conf.ts"
WEBUI_SPEC = "./e2e-tauri/material-video-webui.spec.ts"


def configured_specs() -> list[str]:
    source = WDIO_CONFIG.read_text(encoding="utf-8")
    block = re.search(r"specs:\s*\[(.*?)\]", source, re.DOTALL)
    if block is None:
        raise AssertionError("wdio.video-studio.conf.ts has no specs list")
    return re.findall(r"\"(\./e2e-tauri/[^\"]+)\"", block.group(1))


def spec_arguments(arguments: list[str]) -> list[str]:
    return [
        arguments[index + 1]
        for index, value in enumerate(arguments)
        if value == "--spec"
    ]


def main() -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_bm_06_acceptance as bm06
    import run_vf_06_acceptance as vf06

    configured = configured_specs()
    if WEBUI_SPEC not in configured:
        raise AssertionError(
            "material-video-webui.spec.ts left the wdio config inventory; "
            "IM-05 acceptance still expects it there"
        )

    arguments = getattr(vf06, "desktop_wdio_arguments", list)()
    effective = spec_arguments(list(arguments)) or configured
    if WEBUI_SPEC in effective:
        raise AssertionError(
            "VF-06 full acceptance would execute material-video-webui.spec.ts "
            "without building or injecting AUTOMATION_TOOL_IM05_WORKER; that "
            "spec belongs to IM-05 and is covered by run_im_05_acceptance.py"
        )
    expected = [spec for spec in configured if spec != WEBUI_SPEC]
    if effective != expected:
        raise AssertionError(
            "VF-06 desktop acceptance must keep covering every non-IM-05 spec "
            f"from the wdio config; expected {expected}, got {effective}"
        )
    if getattr(bm06, "desktop_wdio_arguments", None) is not getattr(
        vf06, "desktop_wdio_arguments", object()
    ):
        raise AssertionError(
            "BM-06 must reuse the same VF-06 wdio scope instead of a second "
            "hand-maintained list"
        )
    print("video-studio acceptance scope checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
