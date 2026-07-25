"""Single definition of visible-anchor probing for the Douyin page objects.

Security-relevant probes (captcha, slider, risk challenge, login panel,
blocking overlay) must answer "is any match visible", not "is the first match
visible". A single-page app routinely pre-renders hidden placeholders and
template nodes, so a first-match-only probe fails open: the hidden node
answers "not visible" and the real, visible challenge on screen is missed.

Every probe therefore filters through Playwright's visible engine before
counting, and a failing probe raises instead of reporting absence.

``any_visible`` probes each selector separately so a group may mix Playwright
engines (``text=``, ``:text-is`` and CSS), which cannot be comma-joined.
``unique_visible`` needs one deduplicated element count to detect ambiguous
anchors, so its groups must be CSS-only selector lists.

``unique_visible_in_snapshot`` is the same rule applied to a pinned element
rather than to a live query. A locator re-runs its selector on every read, so
reading several facts of one row through one is not guaranteed to describe one
row; a snapshot is the only way to keep them together.
"""

from __future__ import annotations

from typing import Final, Protocol

VISIBLE_MATCH_ENGINE: Final = "visible=true"


class AnchorConflict(RuntimeError):
    """Several visible elements matched one anchor group; the page is ambiguous."""


class AnchorLocator(Protocol):
    """The locator surface the shared visible probes rely on."""

    @property
    def first(self) -> AnchorLocator: ...

    def count(self) -> int: ...

    def locator(self, selector: str) -> AnchorLocator: ...


class AnchorSnapshot(Protocol):
    """One pinned element, in contrast to a locator's re-evaluated query."""

    def query_selector_all(self, selector: str) -> list[AnchorSnapshot]: ...

    def dispose(self) -> None: ...


class _Page(Protocol):
    def locator(self, selector: str) -> AnchorLocator: ...


def visible_matches(page: _Page, selector: str) -> AnchorLocator:
    """Return the visible-only locator for one selector."""
    return page.locator(selector).locator(VISIBLE_MATCH_ENGINE)


def any_visible(page: _Page, selectors: tuple[str, ...]) -> bool:
    """Report whether any element of the anchor group is visible on screen.

    Each selector is probed separately, so the group may mix engines.
    """
    return any(_visible_count(page, selector)[0] > 0 for selector in selectors)


def unique_visible(page: _Page, selectors: tuple[str, ...]) -> AnchorLocator | None:
    """Return the single visible match, or None; several visible matches conflict.

    The group is probed as one CSS selector list so an element matching two
    selectors of the same group counts once.
    """
    count, locator = _visible_count(page, ", ".join(selectors))
    if count > 1:
        raise AnchorConflict
    return locator.first if count == 1 else None


def unique_visible_in_snapshot(
    node: AnchorSnapshot,
    selectors: tuple[str, ...],
) -> AnchorSnapshot | None:
    """``unique_visible`` against a pinned element instead of a live query.

    An ``ElementHandle`` has no ``locator``, so the shared visible engine is
    chained onto the group with ``>>`` here instead of by a nested ``locator``
    call. The group is still one CSS selector list, so an element matching two
    selectors of the same group counts once, exactly as in ``unique_visible``.

    Ownership: the returned snapshot belongs to the caller; every other
    snapshot resolved here is released before returning or raising.
    """
    matches = node.query_selector_all(f"{', '.join(selectors)} >> {VISIBLE_MATCH_ENGINE}")
    if type(matches) is not list:
        raise ValueError
    if len(matches) == 1:
        return matches[0]
    for match in matches:
        match.dispose()
    if matches:
        raise AnchorConflict
    return None


def _visible_count(page: _Page, selector: str) -> tuple[int, AnchorLocator]:
    locator = visible_matches(page, selector)
    count = locator.count()
    if type(count) is not int or count < 0:
        raise ValueError
    return count, locator


__all__ = [
    "VISIBLE_MATCH_ENGINE",
    "AnchorConflict",
    "AnchorLocator",
    "AnchorSnapshot",
    "any_visible",
    "unique_visible",
    "unique_visible_in_snapshot",
    "visible_matches",
]
