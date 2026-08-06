"""The real ``ReplayPage``: SA-04's replay protocol bound to a Playwright page.

This is the adapter SA-04/SA-05 left as legacy — the deterministic replayer was
proven against ``FakePage`` only until it existed. It binds the semantic
protocol to the real DOM with the same fail-closed posture as the engine:

* **anchors resolve by role + accessible name, exactly** — ``find`` returns a
  handle only when exactly one *visible* element matches; zero or several is
  the drift signal and yields ``None`` so the replay fails instead of guessing;
* **real timers live here** — every lookup, condition and action is bounded by
  the adapter's timeout; conditions poll until they hold or the deadline
  passes, because a click's navigation settles asynchronously;
* **transient page churn is not an answer** — a lookup that lands mid-navigation
  (execution context destroyed) counts as "not yet", never as "absent", until
  the deadline makes it final;
* **unknown vocabulary fails loud** — a role or action kind outside the closed
  contract vocabulary is a programming error upstream, not a lookup miss.
"""

from __future__ import annotations

import time
from typing import Final, Literal, cast
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

_AriaRole = Literal[
    "button",
    "link",
    "textbox",
    "combobox",
    "checkbox",
    "radio",
    "tab",
    "menuitem",
    "listitem",
    "dialog",
    "img",
]
_GOAL_ROLES: Final = frozenset(
    {
        "button",
        "link",
        "textbox",
        "combobox",
        "checkbox",
        "radio",
        "tab",
        "menuitem",
        "listitem",
        "dialog",
        "img",
    }
)
_SCROLL_PIXELS: Final = 600
_POLL_MILLISECONDS: Final = 100


def _aria_role(role: str) -> _AriaRole:
    if role not in _GOAL_ROLES:
        raise ValueError(f"role {role!r} is not in the closed goal vocabulary")
    return cast(_AriaRole, role)


class PlaywrightReplayPage:
    """Implements ``skill_replayer.ReplayPage`` over a live Playwright page."""

    def __init__(self, page: Page, *, action_timeout_seconds: int = 10) -> None:
        if not 1 <= action_timeout_seconds <= 120:
            raise ValueError("the action timeout must be within 1..120 seconds")
        self._page = page
        self._timeout_milliseconds = action_timeout_seconds * 1_000
        self._timeout_seconds = float(action_timeout_seconds)

    def find(self, role: str, name: str) -> object | None:
        aria = _aria_role(role)
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            matches = self._visible_matches(aria, name)
            if matches is not None and len(matches) == 1:
                return matches[0]
            if time.monotonic() >= deadline:
                return None
            self._page.wait_for_timeout(_POLL_MILLISECONDS)

    def holds(
        self,
        kind: str,
        *,
        role: str | None = None,
        name: str | None = None,
        pattern: str | None = None,
    ) -> bool:
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            verdict = self._holds_once(kind, role=role, name=name, pattern=pattern)
            if verdict:
                return True
            if time.monotonic() >= deadline:
                return False
            self._page.wait_for_timeout(_POLL_MILLISECONDS)

    def act(self, kind: str, handle: object, value: str | None) -> None:
        locator = cast(Locator, handle)
        timeout = self._timeout_milliseconds
        if kind == "click":
            locator.click(timeout=timeout)
        elif kind == "fill":
            if value is None:
                raise ValueError("a fill action needs its resolved parameter value")
            locator.fill(value, timeout=timeout)
        elif kind == "press_key":
            if value is None:
                raise ValueError("a press_key action needs its key")
            locator.press(value, timeout=timeout)
        elif kind == "scroll":
            if value not in {"up", "down"}:
                raise ValueError("a scroll action needs a direction")
            delta = _SCROLL_PIXELS if value == "down" else -_SCROLL_PIXELS
            self._page.mouse.wheel(0, delta)
        elif kind == "wait":
            locator.wait_for(state="visible", timeout=timeout)
        else:
            raise ValueError(f"action kind {kind!r} is not in the closed vocabulary")

    def current_path(self) -> str:
        return urlsplit(self._page.url).path or "/"

    def _visible_matches(self, role: _AriaRole, name: str) -> list[Locator] | None:
        """Visible exact matches right now, or ``None`` while the page churns."""
        locator = self._page.get_by_role(role, name=name, exact=True)
        try:
            return [
                candidate
                for index in range(locator.count())
                if (candidate := locator.nth(index)).is_visible()
            ]
        except PlaywrightError:
            return None

    def _holds_once(
        self,
        kind: str,
        *,
        role: str | None,
        name: str | None,
        pattern: str | None,
    ) -> bool:
        if kind == "url_matches":
            if pattern is None:
                raise ValueError("a url_matches condition needs a pattern")
            return self.current_path() == pattern
        if kind in {"element_visible", "element_absent"}:
            if role is None or name is None:
                raise ValueError(f"a {kind} condition needs a role and a name")
            matches = self._visible_matches(_aria_role(role), name)
            if matches is None:
                # Mid-navigation churn: "absent" must never be concluded from a
                # page that is still being torn down or built up.
                return False
            if kind == "element_visible":
                return len(matches) >= 1
            return len(matches) == 0
        raise ValueError(f"condition kind {kind!r} is not in the closed vocabulary")


__all__ = ["PlaywrightReplayPage"]
