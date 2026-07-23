#!/usr/bin/env python3
"""BU-03 deterministic tests for the restricted Browser Use tool surface.

Runs inside the locked browser-use-contract environment (real library, no
browser, no network): the restricted registry must expose exactly the closed
allowlist, upstream drift must fail closed, and the agent policy must bound
domains, steps, per-step actions and step duration.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/browser-use-contract"))

from browser_use_restricted_tools import (  # noqa: E402
    ALLOWED_ACTIONS,
    FORBIDDEN_ACTIONS,
    RestrictedAgentPolicy,
    RestrictedToolsRejected,
    create_restricted_tools,
    restricted_agent_kwargs,
    restricted_run_kwargs,
    verify_restricted_registry,
)

DOUYIN_DOMAINS = ("https://creator.douyin.com", "https://*.douyin.com")


class RestrictedToolsTests(unittest.TestCase):
    def test_allow_and_forbid_lists_partition_the_upstream_defaults(self) -> None:
        from browser_use import Tools

        defaults = set(Tools().registry.registry.actions.keys())
        self.assertEqual(defaults, set(ALLOWED_ACTIONS) | set(FORBIDDEN_ACTIONS))
        self.assertFalse(set(ALLOWED_ACTIONS) & set(FORBIDDEN_ACTIONS))

    def test_restricted_tools_expose_exactly_the_allowlist(self) -> None:
        tools = create_restricted_tools()
        names = set(tools.registry.registry.actions.keys())
        self.assertEqual(names, set(ALLOWED_ACTIONS))
        for forbidden in FORBIDDEN_ACTIONS:
            self.assertNotIn(forbidden, names)

    def test_arbitrary_js_files_download_cross_domain_and_tabs_are_gone(self) -> None:
        names = set(create_restricted_tools().registry.registry.actions.keys())
        for forbidden in (
            "evaluate",
            "read_file",
            "write_file",
            "replace_file",
            "save_as_pdf",
            "search",
            "close",
            "switch",
        ):
            self.assertNotIn(forbidden, names)

    def test_registry_drift_fails_closed(self) -> None:
        tools = create_restricted_tools()
        actions = tools.registry.registry.actions
        actions["totally_new_upstream_action"] = next(iter(actions.values()))
        with self.assertRaises(RestrictedToolsRejected):
            verify_restricted_registry(tools)

    def test_missing_allowed_action_fails_closed(self) -> None:
        tools = create_restricted_tools()
        tools.registry.registry.actions.pop("click")
        with self.assertRaises(RestrictedToolsRejected):
            verify_restricted_registry(tools)


class RestrictedAgentPolicyTests(unittest.TestCase):
    def test_valid_policy_produces_bounded_agent_and_run_kwargs(self) -> None:
        policy = RestrictedAgentPolicy(
            allowed_domains=DOUYIN_DOMAINS,
            max_steps=20,
            max_actions_per_step=3,
            step_timeout_seconds=60,
        )
        agent_kwargs = restricted_agent_kwargs(policy)
        self.assertEqual(agent_kwargs["max_actions_per_step"], 3)
        self.assertEqual(agent_kwargs["step_timeout"], 60)
        run_kwargs = restricted_run_kwargs(policy)
        self.assertEqual(run_kwargs, {"max_steps": 20})

    def test_domains_must_be_non_empty_https_patterns(self) -> None:
        for invalid in (
            (),
            ("",),
            ("http://creator.douyin.com",),
            ("creator.douyin.com",),
            ("https://*",),
            ("https://creator.douyin.com/path",),
            ("file:///tmp",),
        ):
            with self.assertRaises(RestrictedToolsRejected):
                RestrictedAgentPolicy(
                    allowed_domains=invalid,
                    max_steps=10,
                    max_actions_per_step=2,
                    step_timeout_seconds=60,
                )

    def test_limits_are_hard_bounded(self) -> None:
        cases = (
            {"max_steps": 0},
            {"max_steps": 51},
            {"max_actions_per_step": 0},
            {"max_actions_per_step": 6},
            {"step_timeout_seconds": 0},
            {"step_timeout_seconds": 301},
        )
        for overrides in cases:
            values = {
                "allowed_domains": DOUYIN_DOMAINS,
                "max_steps": 10,
                "max_actions_per_step": 2,
                "step_timeout_seconds": 60,
                **overrides,
            }
            with self.assertRaises(RestrictedToolsRejected):
                RestrictedAgentPolicy(**values)

    def test_session_domain_allowlist_flows_from_policy(self) -> None:
        policy = RestrictedAgentPolicy(
            allowed_domains=DOUYIN_DOMAINS,
            max_steps=10,
            max_actions_per_step=2,
            step_timeout_seconds=60,
        )
        self.assertEqual(policy.session_allowed_domains(), list(DOUYIN_DOMAINS))

    def test_route_prefixes_are_validated(self) -> None:
        for invalid in (("creator",), ("/a/../b",), ("",), ("/{}",)):
            with self.assertRaises(RestrictedToolsRejected):
                RestrictedAgentPolicy(
                    allowed_domains=DOUYIN_DOMAINS,
                    allowed_route_prefixes=invalid,
                    max_steps=10,
                    max_actions_per_step=2,
                    step_timeout_seconds=60,
                )

    def test_url_allowlist_combines_domain_and_route(self) -> None:
        policy = RestrictedAgentPolicy(
            allowed_domains=DOUYIN_DOMAINS,
            allowed_route_prefixes=("/creator-micro", "/content/upload"),
            max_steps=10,
            max_actions_per_step=2,
            step_timeout_seconds=60,
        )
        self.assertTrue(policy.is_url_allowed("https://creator.douyin.com/creator-micro/home"))
        self.assertTrue(policy.is_url_allowed("https://sso.douyin.com/content/upload"))
        for denied in (
            "https://creator.douyin.com/other",
            "https://evil.example.com/creator-micro",
            "http://creator.douyin.com/creator-micro",
            "https://douyin.com.evil.com/creator-micro",
            "https://creator.douyin.com../creator-micro",
            "not-a-url",
        ):
            self.assertFalse(policy.is_url_allowed(denied), denied)

    def test_empty_route_prefixes_allow_any_path_on_allowed_domains_only(self) -> None:
        policy = RestrictedAgentPolicy(
            allowed_domains=("https://creator.douyin.com",),
            max_steps=10,
            max_actions_per_step=2,
            step_timeout_seconds=60,
        )
        self.assertTrue(policy.is_url_allowed("https://creator.douyin.com/anything"))
        self.assertFalse(policy.is_url_allowed("https://www.douyin.com/anything"))


if __name__ == "__main__":
    unittest.main()
