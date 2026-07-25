#!/usr/bin/env python3
"""BM-16 acceptance harness contract tests.

Proves the determinism-and-release acceptance harness exists with every
mandated macOS phase before the heavyweight run is attempted: deterministic
gates, the 134-item per-item render sweep, the 12-style render sweep, the
same-input double-run comparison, and the no-URL-entry verification.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_bm_16_acceptance.py"

REQUIRED_PHASES = (
    "run_deterministic_gates",
    "stage_release_directory",
    "run_item_render_sweep",
    "run_style_render_sweep",
    "run_double_run_determinism",
    "verify_no_url_entry",
)


def main() -> int:
    assert sys.version_info >= (3, 10), (
        "BM-16 acceptance requires python3.10+ (use python3.12)"
    )
    assert RUNNER.is_file(), "scripts/run_bm_16_acceptance.py is missing"

    specification = importlib.util.spec_from_file_location(
        "run_bm_16_acceptance", RUNNER
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["run_bm_16_acceptance"] = module
    specification.loader.exec_module(module)

    for phase in REQUIRED_PHASES:
        assert hasattr(module, phase), f"acceptance runner is missing phase: {phase}"

    assert (ROOT / "docs/development/BM-16.md").is_file(), (
        "docs/development/BM-16.md is missing"
    )
    roadmap = (
        ROOT / "docs/embedded-browser-video-studio-roadmap.md"
    ).read_text(encoding="utf-8")
    rows = [line for line in roadmap.splitlines() if line.startswith("| BM-16 |")]
    assert len(rows) == 1 and any(
        rows[0].endswith(f"| {status} |")
        for status in ("🧪 RED", "🚧 实现中", "🔍 待验收", "✅ 已完成")
    ), "BM-16 roadmap row is missing, duplicated or inactive"

    print("BM-16 acceptance harness contract tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
