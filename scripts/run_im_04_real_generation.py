#!/usr/bin/env python3
"""IM-04 gated real-generation acceptance against the live Bailian gateway.

Runs only when the operator opts in with `AUTOMATION_TOOL_REAL_CLOUD=1` and a
local Bailian credential file exists (never committed). The production worker
adapter module — the exact code frozen into the material-video worker — parses
the real workspace key, installs the compatible client against the locked
production base URL and performs one tiny real generation per locked model.
Outputs never contain the key, the base URL or raw provider errors.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers/material_montage"))

from model_service_adapter import (  # noqa: E402
    ALLOWED_MODELS,
    PRODUCTION_BASE_URL,
    generate_script,
    install_script_model,
    parse_script_model,
)

CREDENTIAL_PATH = Path(
    os.environ.get(
        "AUTOMATION_TOOL_BAILIAN_CREDENTIALS",
        os.fspath(ROOT / ".local/secrets/bailian-model.json"),
    )
)


def main() -> int:
    if os.environ.get("AUTOMATION_TOOL_REAL_CLOUD") != "1":
        print("IM-04 real generation skipped: AUTOMATION_TOOL_REAL_CLOUD=1 not set")
        return 3
    if not CREDENTIAL_PATH.exists():
        print("IM-04 real generation skipped: local Bailian credential file missing")
        return 3
    api_key = json.loads(CREDENTIAL_PATH.read_text(encoding="utf-8"))["apiKey"]

    for model_id in sorted(ALLOWED_MODELS):
        configuration = parse_script_model(
            {
                "apiKey": api_key,
                "baseUrl": PRODUCTION_BASE_URL,
                "modelId": model_id,
                "sourceProvider": "bailian",
                "upstreamProvider": "openai",
            }
        )
        assert configuration is not None
        assert api_key not in repr(configuration), "adapter repr leaked the key"
        installed = install_script_model(configuration)
        assert installed == model_id
        text = generate_script("用一句中文介绍如何提高睡眠质量，不超过30个字。")
        if text.startswith("Error:"):
            print(f"IM-04 real generation FAILED for {model_id}: fixed error returned")
            return 1
        if api_key in text or PRODUCTION_BASE_URL in text:
            print(f"IM-04 real generation FAILED for {model_id}: output leaked secrets")
            return 1
        print(f"IM-04 real generation ok: {model_id} -> {len(text)} chars")
    print("IM-04 real Bailian generation passed for all locked models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
