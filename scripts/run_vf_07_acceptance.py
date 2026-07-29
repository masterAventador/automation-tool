#!/usr/bin/env python3
"""VF-07 deterministic and production-App acceptance entrypoint."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    required = (
        ROOT / "frontend/src/features/video-studio/VideoStudio.tsx",
        ROOT / "frontend/src/features/video-studio/VideoStudio.test.tsx",
        ROOT / "frontend/e2e-tauri/video-creation-methods.spec.ts",
        ROOT / "contracts/quality/user-facing-terminology.v1.json",
        ROOT / "docs/development/VF-07.md",
    )
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"VF-07 missing deliverables: {', '.join(missing)}")

    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(
        encoding="utf-8"
    )
    rows = [line for line in roadmap.splitlines() if line.startswith("| VF-07 |")]
    if len(rows) != 1 or not rows[0].endswith("| ✅ 已完成 |"):
        raise SystemExit("VF-07 roadmap row is missing, duplicated or incomplete")

    # sys.executable 而不是裸 "python3"：被转调的 run_vf_06 要 import
    # automation_tool（经 desktop_e2e_prerequisites → run_e4_14 的链），那只在
    # backend/.venv 里有。裸字符串会拿到系统解释器，在第一条断言之前就
    # ModuleNotFoundError——2026-07-29 实测。别处调门禁脚本的裸 python3 不受
    # 影响（它们不 import 业务包），不顺手改。
    subprocess.run(
        [sys.executable, "scripts/run_vf_06_acceptance.py"],
        cwd=ROOT,
        check=True,
    )
    print("VF-07 creation method acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
