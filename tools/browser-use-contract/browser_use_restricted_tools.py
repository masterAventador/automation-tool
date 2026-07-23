#!/usr/bin/env python3
"""BU-03: closed Browser Use tool surface and bounded agent policy.

The publish flow only ever needs observation, same-domain navigation, click,
text input, dropdown selection, scrolling and controlled upload. This module
builds the upstream `Tools` registry with everything else excluded and then
verifies the result against a closed allowlist — an upstream default that
appears or disappears makes the verification fail closed instead of silently
widening or narrowing the surface.

Excluded outright: arbitrary JavaScript (`evaluate`), file access
(`read_file`/`write_file`/`replace_file`), downloads (`save_as_pdf`),
cross-domain search (`search`) and tab management (`close`/`switch`). Shell
access has no upstream action and must never gain one — the partition check
catches any new default.

`RestrictedAgentPolicy` bounds the agent run: a non-empty https-only domain
allowlist (fed to the BrowserSession), hard caps on steps, actions per step
and per-step duration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - typing only
    from browser_use import Tools

ALLOWED_ACTIONS: Final = (
    "click",
    "done",
    "dropdown_options",
    "extract",
    "find_elements",
    "find_text",
    "go_back",
    "input",
    "navigate",
    "screenshot",
    "scroll",
    "search_page",
    "select_dropdown",
    "send_keys",
    "upload_file",
    "wait",
)

FORBIDDEN_ACTIONS: Final = (
    "close",
    "evaluate",
    "read_file",
    "replace_file",
    "save_as_pdf",
    "search",
    "switch",
    "write_file",
)

MAX_AGENT_STEPS: Final = 50
MAX_ACTIONS_PER_STEP: Final = 5
MAX_STEP_TIMEOUT_SECONDS: Final = 300

_DOMAIN_PATTERN: Final = re.compile(
    r"^https://(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$"
)
_ROUTE_PREFIX_PATTERN: Final = re.compile(r"^/[A-Za-z0-9/_-]{0,199}$")
_HOST_PATTERN: Final = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


class RestrictedToolsRejected(RuntimeError):
    """The tool surface or agent policy violates the closed BU-03 contract."""


def _reject(message: str) -> None:
    raise RestrictedToolsRejected(f"restricted browser use tools rejected: {message}")


def create_restricted_tools() -> Tools:
    """Build the upstream Tools with the closed exclusion list, then verify."""
    from browser_use import Tools

    tools = Tools(exclude_actions=list(FORBIDDEN_ACTIONS))
    verify_restricted_registry(tools)
    return tools


def verify_restricted_registry(tools: Tools) -> None:
    """Fail closed unless the registry exposes exactly the allowlist."""
    names = set(tools.registry.registry.actions.keys())
    expected = set(ALLOWED_ACTIONS)
    if names != expected:
        _reject("registry does not match the closed allowlist")


@dataclass(frozen=True)
class RestrictedAgentPolicy:
    """Bounded execution policy for one restricted publish-flow agent."""

    allowed_domains: tuple[str, ...] = field(repr=False)
    max_steps: int
    max_actions_per_step: int
    step_timeout_seconds: int
    allowed_route_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.allowed_domains, tuple)
            or not self.allowed_domains
            or any(
                type(domain) is not str or _DOMAIN_PATTERN.fullmatch(domain) is None
                for domain in self.allowed_domains
            )
        ):
            _reject("allowed domains must be non-empty https origins")
        if not isinstance(self.allowed_route_prefixes, tuple) or any(
            type(prefix) is not str or _ROUTE_PREFIX_PATTERN.fullmatch(prefix) is None
            for prefix in self.allowed_route_prefixes
        ):
            _reject("route prefixes must be absolute, clean path prefixes")
        if (
            type(self.max_steps) is not int
            or not 1 <= self.max_steps <= MAX_AGENT_STEPS
            or type(self.max_actions_per_step) is not int
            or not 1 <= self.max_actions_per_step <= MAX_ACTIONS_PER_STEP
            or type(self.step_timeout_seconds) is not int
            or not 1 <= self.step_timeout_seconds <= MAX_STEP_TIMEOUT_SECONDS
        ):
            _reject("agent limits are out of the hard bounds")

    def session_allowed_domains(self) -> list[str]:
        """Return the domain allowlist in BrowserSession parameter shape."""
        return list(self.allowed_domains)

    def _host_allowed(self, host: str) -> bool:
        if _HOST_PATTERN.fullmatch(host) is None:
            return False
        for domain in self.allowed_domains:
            origin = domain.removeprefix("https://")
            if origin.startswith("*."):
                suffix = origin[2:]
                if host == suffix or host.endswith(f".{suffix}"):
                    return True
            elif host == origin:
                return True
        return False

    def is_url_allowed(self, url: str) -> bool:
        """Report whether one URL passes the domain + route allowlist."""
        if type(url) is not str:
            return False
        from urllib.parse import urlsplit

        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if parts.scheme != "https" or parts.username or parts.password or parts.port:
            return False
        host = (parts.hostname or "").lower()
        if not self._host_allowed(host):
            return False
        if not self.allowed_route_prefixes:
            return True
        path = parts.path or "/"
        return any(
            path == prefix or path.startswith(f"{prefix}/") or path.startswith(prefix)
            for prefix in self.allowed_route_prefixes
        )


def restricted_agent_kwargs(policy: RestrictedAgentPolicy) -> dict[str, object]:
    """Return the bounded Agent constructor keywords for one policy."""
    if not isinstance(policy, RestrictedAgentPolicy):
        _reject("policy must be a RestrictedAgentPolicy")
    return {
        "max_actions_per_step": policy.max_actions_per_step,
        "step_timeout": policy.step_timeout_seconds,
        "use_vision": True,
    }


def restricted_run_kwargs(policy: RestrictedAgentPolicy) -> dict[str, object]:
    """Return the bounded Agent.run keywords for one policy."""
    if not isinstance(policy, RestrictedAgentPolicy):
        _reject("policy must be a RestrictedAgentPolicy")
    return {"max_steps": policy.max_steps}


__all__ = [
    "ALLOWED_ACTIONS",
    "FORBIDDEN_ACTIONS",
    "MAX_ACTIONS_PER_STEP",
    "MAX_AGENT_STEPS",
    "MAX_STEP_TIMEOUT_SECONDS",
    "RestrictedAgentPolicy",
    "RestrictedToolsRejected",
    "create_restricted_tools",
    "restricted_agent_kwargs",
    "restricted_run_kwargs",
    "verify_restricted_registry",
]
