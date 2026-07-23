#!/usr/bin/env python3
"""BU-06 acceptance: locked Bailian model built into a real Browser Use Agent.

Deterministic catalog/capability tests first. Then, gated by
`AUTOMATION_TOOL_REAL_CLOUD=1` and a local Bailian credential, the production
gateway builds a real ChatOpenAI against the locked base URL for the locked
vision model, performs one real Bailian completion through it, and assembles
it into a real restricted `Agent` (BU-03 tools) — proving the locked model
plugs into Browser Use with no upstream change and no general menu. The api
key is never printed.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_PATH = Path(
    os.environ.get(
        "AUTOMATION_TOOL_BAILIAN_CREDENTIALS",
        os.fspath(ROOT / ".local/secrets/bailian-model.json"),
    )
)

PROBE = r"""
import os, sys
sys.path.insert(0, "tools/browser-use-contract")
from browser_use_bailian import (
    build_bailian_chat_model,
    load_bailian_model_catalog,
    redacted_model_descriptor,
    select_bailian_model,
)
from browser_use_restricted_tools import RestrictedAgentPolicy, create_restricted_tools
from browser_use import Agent, BrowserSession
from pathlib import Path

api_key = os.environ["BU06_API_KEY"]
catalog = load_bailian_model_catalog(
    Path("contracts/browser-use/bailian-model-catalog.v1.json")
)
snapshot = select_bailian_model(
    catalog, model_id=catalog.vision_default_model_id, requires_vision=True
)
llm = build_bailian_chat_model(catalog=catalog, snapshot=snapshot, api_key=api_key)

import anyio
from browser_use.llm.messages import UserMessage

async def _one_call():
    return await llm.ainvoke([UserMessage(content="回复两个字：可用")])

result = anyio.run(_one_call)
assert result.completion, "empty completion"
assert api_key not in result.completion

policy = RestrictedAgentPolicy(
    allowed_domains=("https://creator.douyin.com",),
    max_steps=10,
    max_actions_per_step=3,
    step_timeout_seconds=60,
)
session = BrowserSession(
    is_local=True, cdp_url="http://127.0.0.1:59999", headless=True,
    keep_alive=False, enable_default_extensions=False, captcha_solver=False,
    allowed_domains=policy.session_allowed_domains(),
)
agent = Agent(task="占位：验证百炼模型装配进受限 Agent", llm=llm,
              browser_session=session, tools=create_restricted_tools(),
              max_actions_per_step=policy.max_actions_per_step,
              step_timeout=policy.step_timeout_seconds)
assert agent.llm.model == "qwen3.7-max-2026-06-08"
import json as _json
descriptor = redacted_model_descriptor(catalog, snapshot)
assert api_key not in _json.dumps(descriptor)
print("BU06_REAL_OK", snapshot.model_id, len(result.completion))
"""


def fail(message: str) -> None:
    print(f"BU-06 acceptance failed: {message}")
    raise SystemExit(1)


def require_evidence() -> None:
    text = (ROOT / "docs/development/BU-06.md").read_text(encoding="utf-8")
    for marker in ("# BU-06 完成证据", "## RED", "## GREEN", "## 失败矩阵", "## 清理"):
        if marker not in text:
            fail(f"BU-06 evidence is missing {marker}")


def main() -> int:
    deterministic = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "tools/browser-use-contract",
            "--locked",
            "python",
            "scripts/test_browser_use_bailian.py",
        ],
        cwd=ROOT,
        check=False,
    )
    if deterministic.returncode != 0:
        fail("deterministic catalog tests failed")

    if os.environ.get("AUTOMATION_TOOL_REAL_CLOUD") != "1" or not CREDENTIAL_PATH.exists():
        print("BU-06 real Bailian probe skipped (no AUTOMATION_TOOL_REAL_CLOUD=1 / credential)")
        require_evidence()
        return 0

    api_key = json.loads(CREDENTIAL_PATH.read_text(encoding="utf-8"))["apiKey"]
    environment = dict(os.environ)
    environment["BU06_API_KEY"] = api_key
    probe = subprocess.run(
        ["uv", "run", "--project", "tools/browser-use-contract", "--locked", "python", "-c", PROBE],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if probe.returncode != 0 or "BU06_REAL_OK" not in probe.stdout:
        redacted = probe.stderr.replace(api_key, "<redacted>")[-400:]
        fail(f"real Bailian agent assembly failed: {redacted}")

    require_evidence()
    print("BU-06 acceptance passed:", probe.stdout.strip().splitlines()[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
