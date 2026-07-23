#!/usr/bin/env python3
"""BU-03 acceptance: restricted tool surface wired into the real Agent class.

Inside the locked browser-use-contract environment: deterministic registry
and policy tests first, then a real `Agent` instance is constructed from the
production restricted tools, a harness-validated isolated BrowserSession plan
and the bounded policy keywords — proving the closed surface plugs into the
real upstream classes without widening. No model call, no browser launch and
no network happen here; the real publish run belongs to PB-05 and later.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
import sys
sys.path.insert(0, "tools/browser-use-contract")
from browser_use_restricted_tools import (
    ALLOWED_ACTIONS,
    RestrictedAgentPolicy,
    create_restricted_tools,
    restricted_agent_kwargs,
    restricted_run_kwargs,
)

from browser_use import Agent, BrowserSession
from browser_use.llm import ChatOpenAI

policy = RestrictedAgentPolicy(
    allowed_domains=("https://creator.douyin.com", "https://*.douyin.com"),
    max_steps=20,
    max_actions_per_step=3,
    step_timeout_seconds=60,
)
tools = create_restricted_tools()
session = BrowserSession(
    is_local=True,
    cdp_url="http://127.0.0.1:59999",
    headless=True,
    keep_alive=False,
    enable_default_extensions=False,
    captcha_solver=False,
    highlight_elements=False,
    allowed_domains=policy.session_allowed_domains(),
)
llm = ChatOpenAI(
    model="placeholder-not-called",
    api_key="sk-placeholder-never-used",
    base_url="http://127.0.0.1:9/compatible-mode/v1",
)
agent = Agent(
    task="占位任务：仅验证受限面能装配进真实 Agent，不执行",
    llm=llm,
    browser_session=session,
    tools=tools,
    **restricted_agent_kwargs(policy),
)
from browser_use_restricted_tools import FORBIDDEN_ACTIONS

registered = sorted(agent.tools.registry.registry.actions.keys())
# use_vision=True 时上游 Agent 会自行收走 screenshot 动作；安全性质是
# 注册面必须是白名单子集、关键动作在场、禁用动作绝不出现。
assert set(registered) <= set(ALLOWED_ACTIONS), registered
assert {
    "click", "input", "navigate", "upload_file", "select_dropdown", "scroll", "done"
} <= set(registered), registered
assert not set(registered) & set(FORBIDDEN_ACTIONS), registered
assert agent.settings.max_actions_per_step == 3
run_kwargs = restricted_run_kwargs(policy)
assert run_kwargs == {"max_steps": 20}
print("BU03_PROBE_OK", len(registered))
"""


def fail(message: str) -> None:
    print(f"BU-03 acceptance failed: {message}")
    raise SystemExit(1)


def require_evidence() -> None:
    text = (ROOT / "docs/development/BU-03.md").read_text(encoding="utf-8")
    for marker in ("# BU-03 完成证据", "## RED", "## GREEN", "## 失败矩阵", "## 清理"):
        if marker not in text:
            fail(f"BU-03 evidence is missing {marker}")


def main() -> int:
    deterministic = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "tools/browser-use-contract",
            "--locked",
            "python",
            "scripts/test_browser_use_restricted_tools.py",
        ],
        cwd=ROOT,
        check=False,
    )
    if deterministic.returncode != 0:
        fail("deterministic restricted-tools tests failed")

    probe = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "tools/browser-use-contract",
            "--locked",
            "python",
            "-c",
            PROBE,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if probe.returncode != 0 or "BU03_PROBE_OK" not in probe.stdout:
        fail(f"real Agent assembly probe failed: {probe.stderr.strip()[-400:]}")

    require_evidence()
    print("BU-03 restricted tools acceptance passed:", probe.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
