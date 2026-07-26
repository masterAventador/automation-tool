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

# Specs the VF-06 full run must *not* execute, each with the entrypoint that
# owns it. Membership here is not a way to opt out of being run: a spec is only
# admissible if VF-06 cannot possibly pass it, and the owning entrypoint is
# checked below to actually reference it. Otherwise excluding a spec would turn
# it into a file nothing executes — a shape this repository has produced
# repeatedly and never noticed, because a spec nobody runs and a spec that
# passes look exactly alike from the outside.
DELEGATED_SPECS: dict[str, str] = {
    # Needs a real frozen Python Worker injected through
    # AUTOMATION_TOOL_IM05_WORKER, which the VF-06 entrypoint never builds.
    WEBUI_SPEC: "run_im_05_acceptance.py",
}

# Specs that left this wdio configuration for one of their own, with the runner
# configuration they moved to and the entrypoint that drives it.
#
# Moving a spec out of a shared configuration is the easiest way to lose it:
# nothing in the old configuration misses it, and the new one is only ever read
# by the entrypoint that names it. So each move is recorded here and both ends
# are checked — the configuration must list the spec, and the entrypoint must
# name the configuration.
RELOCATED_SPECS: dict[str, tuple[str, str]] = {
    # Waits on a real model round trip and a 360 frame render, which do not fit
    # the three minute Mocha budget this configuration gives its other specs.
    "./e2e-tauri/motion-one-sentence.spec.ts": (
        "wdio.t36.conf.ts",
        "run_t36_acceptance.py",
    ),
}


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
    for spec, owner in DELEGATED_SPECS.items():
        if spec not in configured:
            raise AssertionError(
                f"{spec} left the wdio config inventory; {owner} still expects it there"
            )
        # The exclusion is only legitimate while something else runs the spec.
        # Without this check a spec can be excluded from the full run and then
        # quietly dropped by its owner, and nothing anywhere goes red.
        entrypoint = ROOT / "scripts" / owner
        if not entrypoint.is_file():
            raise AssertionError(
                f"{spec} is delegated to {owner}, which does not exist"
            )
        if spec not in entrypoint.read_text(encoding="utf-8"):
            raise AssertionError(
                f"{spec} is excluded from the VF-06 full run and {owner} no longer "
                "references it, so nothing executes it at all"
            )

    for spec, (wdio_configuration, owner) in RELOCATED_SPECS.items():
        if spec in configured:
            raise AssertionError(
                f"{spec} is recorded as relocated but is still listed in "
                "wdio.video-studio.conf.ts; it would run twice, under two budgets"
            )
        relocated = ROOT / "frontend" / wdio_configuration
        if not relocated.is_file() or spec not in relocated.read_text(encoding="utf-8"):
            raise AssertionError(
                f"{spec} moved to {wdio_configuration}, which does not list it; "
                "the spec now belongs to no runner configuration at all"
            )
        entrypoint = ROOT / "scripts" / owner
        if not entrypoint.is_file():
            raise AssertionError(f"{spec} is driven by {owner}, which does not exist")
        source = entrypoint.read_text(encoding="utf-8")
        if wdio_configuration not in source or spec not in source:
            raise AssertionError(
                f"{owner} no longer names both {wdio_configuration} and {spec}, "
                "so nothing executes that spec"
            )

    arguments = getattr(vf06, "desktop_wdio_arguments", list)()
    effective = spec_arguments(list(arguments)) or configured
    for spec, owner in DELEGATED_SPECS.items():
        if spec in effective:
            raise AssertionError(
                f"VF-06 full acceptance would execute {spec} without the setup it "
                f"requires; that spec is covered by {owner}"
            )
    expected = [spec for spec in configured if spec not in DELEGATED_SPECS]
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
