#!/usr/bin/env python3
"""PB-05 acceptance: the frozen publish policy on the real restricted tool surface.

Inside the locked browser-use-contract environment the PB-05 publish contract
is loaded and turned into a real `RestrictedAgentPolicy`, and the real
upstream `Tools` registry is built through the production BU-03 factory. The
result proves that the douyin publish preflight can only ever reach the frozen
creator routes with the closed action surface: arbitrary JavaScript, file
access, downloads, cross-domain search and tab management stay unreachable,
and www.douyin.com or any other origin is denied. No model call, no browser
launch and no network happen here; the real browser chain is covered by
`backend/tests/integration/test_douyin_publish_embedded_browser.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/publishing/douyin-browser-use-preflight.v1.json"

PROBE = r"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "tools/browser-use-contract")
from browser_use_harness import harness_environment

# BU-02 boundary: cloud sync, telemetry, credentials and proxies stay disabled.
os.environ.clear()
os.environ.update(harness_environment(dict(os.environ)))

from browser_use_restricted_tools import (
    ALLOWED_ACTIONS,
    FORBIDDEN_ACTIONS,
    RestrictedAgentPolicy,
    RestrictedToolsRejected,
    create_restricted_tools,
    restricted_agent_kwargs,
    restricted_run_kwargs,
)

contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
agent_contract = contract["agent"]
surface = contract["surface"]
assert contract["stopBeforeSubmit"] is True, "PB-05 must stop before submission"
# This script verifies the policy the Browser Use agent will run under (PB-06/PB-08).
# The preflight delivered by PB-05 itself is the deterministic Playwright driver.
assert contract["agentMechanism"] == "browser_use"
assert contract["preflightImplementation"] == "deterministic_playwright"

policy = RestrictedAgentPolicy(
    allowed_domains=tuple(agent_contract["allowedDomains"]),
    allowed_route_prefixes=tuple(agent_contract["allowedRoutePrefixes"]),
    max_steps=agent_contract["maxSteps"],
    max_actions_per_step=agent_contract["maxActionsPerStep"],
    step_timeout_seconds=agent_contract["stepTimeoutSeconds"],
)

entry = surface["entryUrl"]
form = f"https://{surface['host']}{surface['formRoute']}"
allowed = (entry, form)
denied = (
    "https://www.douyin.com/user/self",
    "https://creator.douyin.com/creator-micro/home",
    "https://creator.douyin.com.evil.test/creator-micro/content/upload",
    "http://creator.douyin.com/creator-micro/content/upload",
    "https://creator.douyin.com:8443/creator-micro/content/upload",
    "https://rmc.bytedance.com/verifycenter/captcha/x",
    "file:///etc/passwd",
)
for url in allowed:
    assert policy.is_url_allowed(url), f"publish route must be reachable: {url}"
for url in denied:
    assert not policy.is_url_allowed(url), f"origin must be denied: {url}"

tools = create_restricted_tools()
names = set(tools.registry.registry.actions.keys())
assert names == set(ALLOWED_ACTIONS), "the closed allowlist changed"
assert not (names & set(FORBIDDEN_ACTIONS)), "a forbidden action became reachable"
assert "upload_file" in names, "controlled upload is required by the publish flow"

kwargs = restricted_agent_kwargs(policy)
assert kwargs["use_vision"] is True
assert kwargs["max_actions_per_step"] == agent_contract["maxActionsPerStep"]
assert kwargs["step_timeout"] == agent_contract["stepTimeoutSeconds"]
assert restricted_run_kwargs(policy)["max_steps"] == agent_contract["maxSteps"]

try:
    RestrictedAgentPolicy(
        allowed_domains=("https://creator.douyin.com",),
        allowed_route_prefixes=("/creator-micro/content",),
        max_steps=agent_contract["maxSteps"],
        max_actions_per_step=agent_contract["maxActionsPerStep"],
        step_timeout_seconds=10_000,
    )
except RestrictedToolsRejected:
    pass
else:  # pragma: no cover - the bound must stay enforced
    raise AssertionError("agent limits must stay inside the hard bounds")

print("PB-05 restricted publish surface verified")
print(f"actions={len(names)} allowed_routes={agent_contract['allowedRoutePrefixes']}")
"""


def main() -> int:
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "tools/browser-use-contract",
            "--locked",
            "python",
            "-c",
            PROBE,
            str(CONTRACT),
        ],
        check=False,
        cwd=ROOT,
        text=True,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
