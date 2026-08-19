"""The real ``ReplayPage``: SA-04's replay protocol bound to a Playwright page.

This is the adapter SA-04/SA-05 left as legacy — the deterministic replayer was
proven against ``FakePage`` only until it existed. It binds the semantic
protocol to the real DOM with the same fail-closed posture as the engine:

* **anchors resolve by role + accessible name, exactly** — ``find`` returns a
  handle only when exactly one *visible* element matches; zero or several is
  the drift signal and yields ``None`` so the replay fails instead of guessing;
* **near-text disambiguates, and is verified** — when the goal carries
  ``near_text``, a candidate only qualifies if the text is visible on the page
  and any declared relative position (above/below/left_of/right_of/inside)
  holds against one of its occurrences; among qualifiers the strictly closest
  wins and an exact tie refuses. The context text disappearing is drift, so a
  unique-but-out-of-context candidate refuses too. A ``relative_position``
  without ``near_text`` has nothing to anchor to and is unverifiable —
  fail-closed, it refuses rather than silently ignoring the constraint;
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

import math
import time
from typing import Final, Literal, cast
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import FloatRect, Locator, Page

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
    "status",
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
        # toast/live region：评论、私信这类不产生导航的动作用它证明结果。
        "status",
    }
)
_RELATIVE_POSITIONS: Final = frozenset({"above", "below", "left_of", "right_of", "inside"})
_SCROLL_PIXELS: Final = 600
_POLL_MILLISECONDS: Final = 100

_Box = FloatRect


def _aria_role(role: str) -> _AriaRole:
    if role not in _GOAL_ROLES:
        raise ValueError(f"role {role!r} is not in the closed goal vocabulary")
    return cast(_AriaRole, role)


def _center(box: _Box) -> tuple[float, float]:
    return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def _distance(candidate: _Box, context: _Box) -> float:
    (candidate_x, candidate_y), (context_x, context_y) = _center(candidate), _center(context)
    return math.hypot(candidate_x - context_x, candidate_y - context_y)


def _direction_holds(candidate: _Box, context: _Box, relative_position: str | None) -> bool:
    if relative_position is None:
        return True
    if relative_position == "inside":
        return (
            candidate["x"] >= context["x"]
            and candidate["y"] >= context["y"]
            and candidate["x"] + candidate["width"] <= context["x"] + context["width"]
            and candidate["y"] + candidate["height"] <= context["y"] + context["height"]
        )
    (candidate_x, candidate_y), (context_x, context_y) = _center(candidate), _center(context)
    if relative_position == "above":
        return candidate_y < context_y
    if relative_position == "below":
        return candidate_y > context_y
    if relative_position == "left_of":
        return candidate_x < context_x
    return candidate_x > context_x  # right_of — the vocabulary is closed upstream


class PlaywrightReplayPage:
    """Implements ``skill_replayer.ReplayPage`` over a live Playwright page."""

    def __init__(self, page: Page, *, action_timeout_seconds: int = 10) -> None:
        if not 1 <= action_timeout_seconds <= 120:
            raise ValueError("the action timeout must be within 1..120 seconds")
        self._page = page
        self._timeout_milliseconds = action_timeout_seconds * 1_000
        self._timeout_seconds = float(action_timeout_seconds)

    def find(
        self,
        role: str,
        name: str,
        *,
        near_text: str | None = None,
        relative_position: str | None = None,
    ) -> object | None:
        aria = _aria_role(role)
        if relative_position is not None and relative_position not in _RELATIVE_POSITIONS:
            raise ValueError(
                f"relative position {relative_position!r} is not in the closed vocabulary"
            )
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            resolved = self._resolve_once(aria, name, near_text, relative_position)
            if resolved is not None:
                return resolved
            if time.monotonic() >= deadline:
                return None
            self._page.wait_for_timeout(_POLL_MILLISECONDS)

    def _resolve_once(
        self,
        aria: _AriaRole,
        name: str,
        near_text: str | None,
        relative_position: str | None,
    ) -> Locator | None:
        matches = self._visible_matches(aria, name)
        if matches is None:
            return None
        if near_text is None:
            if relative_position is not None:
                # Nothing to anchor the direction to — unverifiable, so refuse
                # (fail-closed) instead of silently dropping the constraint.
                return None
            return matches[0] if len(matches) == 1 else None
        contexts = self._visible_text_boxes(near_text)
        if not contexts:
            # The context text going away is drift even when the candidate
            # itself is unique — the goal promised "near this text".
            return None
        qualifying: list[tuple[float, Locator]] = []
        for candidate in matches:
            box = self._box_of(candidate)
            if box is None:
                continue
            scores = [
                _distance(box, context)
                for context in contexts
                if _direction_holds(box, context, relative_position)
            ]
            if scores:
                qualifying.append((min(scores), candidate))
        if not qualifying:
            return None
        if len(qualifying) == 1:
            return qualifying[0][1]
        qualifying.sort(key=lambda pair: pair[0])
        if qualifying[0][0] < qualifying[1][0]:
            return qualifying[0][1]
        return None  # an exact tie is ambiguity, not a coin toss

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

    def _visible_text_boxes(self, text: str) -> list[_Box]:
        """Boxes of the visible occurrences of the context text; [] on churn
        too — the polling loop retries either way."""
        locator = self._page.get_by_text(text)
        try:
            return [
                box
                for index in range(locator.count())
                if (occurrence := locator.nth(index)).is_visible()
                and (box := occurrence.bounding_box()) is not None
            ]
        except PlaywrightError:
            return []

    def _box_of(self, candidate: Locator) -> _Box | None:
        try:
            return candidate.bounding_box()
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
