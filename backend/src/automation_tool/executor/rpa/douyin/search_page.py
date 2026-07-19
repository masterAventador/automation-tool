"""Read-only Douyin search page object with centralized versioned anchors."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import (
    DouyinPageEntry,
    DouyinPageObservation,
    DouyinPageVersion,
    DouyinPageVersionModel,
)
from automation_tool.protocol import MAX_TASK_TARGET_LIMIT

DOUYIN_SEARCH_PAGE_SELECTOR_VERSION = "douyin.search-page.v1"

_SEARCH_INPUT_SELECTORS = (
    'input[aria-label="搜索"]',
    'input[placeholder="搜索"]',
    '[data-e2e="searchbar-input"]',
)
_SEARCH_SUBMIT_SELECTORS = (
    'button[aria-label="搜索"]',
    '[role="button"][aria-label="搜索"]',
    '[data-e2e="searchbar-button"]',
)
_RESULT_LIST_SELECTORS = (
    '[role="feed"]',
    '[data-e2e="search-result-list"]',
    '[data-e2e="scroll-list"]',
)
_RESULT_ITEM_SELECTORS = (
    '[role="feed"] > article',
    '[data-e2e="search-result-item"]',
    '[data-e2e="feed-item"]',
)
_LOGIN_DIALOG_SELECTORS = (
    '[role="dialog"]:has-text("扫码登录")',
    '[data-e2e="login-modal"]',
    '[data-e2e="login-panel"]',
)
_BLOCKING_DIALOG_SELECTORS = (
    '[role="dialog"]',
    '[data-e2e="modal"]',
)
_MAX_WAIT_MILLISECONDS = 60_000


class DouyinSearchPageRejected(RuntimeError):
    """The current page cannot provide a safe versioned search anchor."""

    def __init__(self) -> None:
        super().__init__("douyin search page is unavailable")


class DouyinSearchPageState(StrEnum):
    HOME_READY = "home_ready"
    RESULTS_READY = "results_ready"
    LOGIN_REQUIRED = "login_required"
    DIALOG_BLOCKED = "dialog_blocked"
    UNKNOWN = "unknown"


class DouyinSearchPageEvidence(StrEnum):
    SEARCH_ENTRY_VISIBLE = "search_entry_visible"
    RESULT_LIST_VISIBLE = "result_list_visible"
    LOGIN_REDIRECT = "login_redirect"
    LOGIN_DIALOG = "login_dialog"
    BLOCKING_DIALOG = "blocking_dialog"
    REQUIRED_ANCHOR_MISSING = "required_anchor_missing"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    PAGE_UNAVAILABLE = "page_unavailable"


_ALLOWED_OBSERVATIONS = frozenset(
    {
        (
            DouyinPageVersion.WEB_V1,
            DouyinPageEntry.HOME,
            DouyinSearchPageState.HOME_READY,
            DouyinSearchPageEvidence.SEARCH_ENTRY_VISIBLE,
        ),
        (
            DouyinPageVersion.WEB_V1,
            DouyinPageEntry.SEARCH_RESULTS,
            DouyinSearchPageState.RESULTS_READY,
            DouyinSearchPageEvidence.RESULT_LIST_VISIBLE,
        ),
        (
            DouyinPageVersion.WEB_V1,
            DouyinPageEntry.SESSION_PROBE,
            DouyinSearchPageState.LOGIN_REQUIRED,
            DouyinSearchPageEvidence.LOGIN_REDIRECT,
        ),
        *(
            (
                DouyinPageVersion.WEB_V1,
                entry,
                DouyinSearchPageState.LOGIN_REQUIRED,
                DouyinSearchPageEvidence.LOGIN_DIALOG,
            )
            for entry in (DouyinPageEntry.HOME, DouyinPageEntry.SEARCH_RESULTS)
        ),
        *(
            (
                DouyinPageVersion.WEB_V1,
                entry,
                DouyinSearchPageState.DIALOG_BLOCKED,
                DouyinSearchPageEvidence.BLOCKING_DIALOG,
            )
            for entry in (DouyinPageEntry.HOME, DouyinPageEntry.SEARCH_RESULTS)
        ),
        *(
            (
                DouyinPageVersion.WEB_V1,
                entry,
                DouyinSearchPageState.UNKNOWN,
                evidence,
            )
            for entry in (DouyinPageEntry.HOME, DouyinPageEntry.SEARCH_RESULTS)
            for evidence in (
                DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING,
                DouyinSearchPageEvidence.CONFLICTING_ANCHORS,
                DouyinSearchPageEvidence.PAGE_UNAVAILABLE,
            )
        ),
        (
            DouyinPageVersion.UNKNOWN,
            DouyinPageEntry.UNKNOWN,
            DouyinSearchPageState.UNKNOWN,
            DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN,
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinSearchPageObservation:
    page_version: DouyinPageVersion
    entry: DouyinPageEntry
    state: DouyinSearchPageState
    evidence: DouyinSearchPageEvidence
    selector_version: str = DOUYIN_SEARCH_PAGE_SELECTOR_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.page_version, DouyinPageVersion)
            or not isinstance(self.entry, DouyinPageEntry)
            or not isinstance(self.state, DouyinSearchPageState)
            or not isinstance(self.evidence, DouyinSearchPageEvidence)
            or self.selector_version != DOUYIN_SEARCH_PAGE_SELECTOR_VERSION
            or (self.page_version, self.entry, self.state, self.evidence)
            not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinSearchPageRejected

    @property
    def ready(self) -> bool:
        return self.state in {
            DouyinSearchPageState.HOME_READY,
            DouyinSearchPageState.RESULTS_READY,
        }

    @property
    def circuit_open(self) -> bool:
        return not self.ready

    def __repr__(self) -> str:
        return (
            "DouyinSearchPageObservation("
            f"page_version={self.page_version.value!r}, entry={self.entry.value!r}, "
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"selector_version={self.selector_version!r}, circuit_open={self.circuit_open!r})"
        )


class _Locator(Protocol):
    @property
    def first(self) -> _Locator: ...

    def is_visible(self) -> bool: ...

    def count(self) -> int: ...

    def wait_for(self, *, state: str, timeout: float) -> None: ...


class _Page(Protocol):
    @property
    def url(self) -> str: ...

    def locator(self, selector: str) -> _Locator: ...


class DouyinSearchPage:
    """Expose no selector or action except fixed read-only search-page anchors."""

    def __init__(self, window: BrowserWindow) -> None:
        if not isinstance(window, BrowserWindow):
            raise DouyinSearchPageRejected
        self._page = cast(_Page, window.playwright_page)
        self._versions = DouyinPageVersionModel()

    def __repr__(self) -> str:
        return "DouyinSearchPage(<redacted>)"

    def observe(self) -> DouyinSearchPageObservation:
        version = self._versions.check(self._page.url)
        if not version.compatible:
            return _observation(
                version,
                DouyinSearchPageState.UNKNOWN,
                DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN,
            )
        if version.entry is DouyinPageEntry.SESSION_PROBE:
            return _observation(
                version,
                DouyinSearchPageState.LOGIN_REQUIRED,
                DouyinSearchPageEvidence.LOGIN_REDIRECT,
            )
        try:
            if _visible_locator(self._page, _LOGIN_DIALOG_SELECTORS) is not None:
                return _observation(
                    version,
                    DouyinSearchPageState.LOGIN_REQUIRED,
                    DouyinSearchPageEvidence.LOGIN_DIALOG,
                )
            if _visible_locator(self._page, _BLOCKING_DIALOG_SELECTORS) is not None:
                return _observation(
                    version,
                    DouyinSearchPageState.DIALOG_BLOCKED,
                    DouyinSearchPageEvidence.BLOCKING_DIALOG,
                )
            search_input = _visible_locator(self._page, _SEARCH_INPUT_SELECTORS)
            search_submit = _visible_locator(self._page, _SEARCH_SUBMIT_SELECTORS)
            result_list = _visible_locator(self._page, _RESULT_LIST_SELECTORS)
        except Exception:
            return _observation(
                version,
                DouyinSearchPageState.UNKNOWN,
                DouyinSearchPageEvidence.PAGE_UNAVAILABLE,
            )
        has_search_anchor = search_input is not None or search_submit is not None
        has_complete_search_entry = search_input is not None and search_submit is not None
        has_result_list = result_list is not None
        if version.entry is DouyinPageEntry.HOME:
            if has_result_list:
                return _observation(
                    version,
                    DouyinSearchPageState.UNKNOWN,
                    DouyinSearchPageEvidence.CONFLICTING_ANCHORS,
                )
            if has_complete_search_entry:
                return _observation(
                    version,
                    DouyinSearchPageState.HOME_READY,
                    DouyinSearchPageEvidence.SEARCH_ENTRY_VISIBLE,
                )
        else:
            # The version model admits only HOME or SEARCH_RESULTS here; the
            # SESSION_PROBE and UNKNOWN entries returned above before DOM access.
            if has_search_anchor:
                return _observation(
                    version,
                    DouyinSearchPageState.UNKNOWN,
                    DouyinSearchPageEvidence.CONFLICTING_ANCHORS,
                )
            if has_result_list:
                return _observation(
                    version,
                    DouyinSearchPageState.RESULTS_READY,
                    DouyinSearchPageEvidence.RESULT_LIST_VISIBLE,
                )
        return _observation(
            version,
            DouyinSearchPageState.UNKNOWN,
            DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING,
        )

    def search_input(self) -> _Locator:
        self._require_state(DouyinSearchPageState.HOME_READY)
        return self._require_locator(_SEARCH_INPUT_SELECTORS)

    def search_submit(self) -> _Locator:
        self._require_state(DouyinSearchPageState.HOME_READY)
        return self._require_locator(_SEARCH_SUBMIT_SELECTORS)

    def result_list(self) -> _Locator:
        self._require_state(DouyinSearchPageState.RESULTS_READY)
        return self._require_locator(_RESULT_LIST_SELECTORS)

    def result_item_count(self, *, maximum: int) -> int:
        """Return only a bounded count; candidate contents remain a later concern."""

        if type(maximum) is not int or not 1 <= maximum <= MAX_TASK_TARGET_LIMIT:
            raise DouyinSearchPageRejected
        self._require_state(DouyinSearchPageState.RESULTS_READY)
        try:
            for selector in _RESULT_ITEM_SELECTORS:
                count = self._page.locator(selector).count()
                if type(count) is not int or count < 0:
                    raise ValueError
                if count:
                    return min(count, maximum)
        except Exception:
            raise DouyinSearchPageRejected from None
        return 0

    def login_dialog(self) -> _Locator:
        observation = self.observe()
        if observation.evidence is not DouyinSearchPageEvidence.LOGIN_DIALOG:
            raise DouyinSearchPageRejected
        return self._require_locator(_LOGIN_DIALOG_SELECTORS)

    def blocking_dialog(self) -> _Locator:
        self._require_state(DouyinSearchPageState.DIALOG_BLOCKED)
        return self._require_locator(_BLOCKING_DIALOG_SELECTORS)

    def wait_for_home_ready(
        self,
        *,
        timeout_milliseconds: int,
    ) -> DouyinSearchPageObservation:
        """Wait only for the versioned home anchors, then re-observe all facts."""

        return self._wait_for_state(
            DouyinSearchPageState.HOME_READY,
            (_SEARCH_INPUT_SELECTORS, _SEARCH_SUBMIT_SELECTORS),
            timeout_milliseconds,
        )

    def wait_for_results_ready(
        self,
        *,
        timeout_milliseconds: int,
    ) -> DouyinSearchPageObservation:
        """Wait only for the versioned result anchor, then re-observe all facts."""

        return self._wait_for_state(
            DouyinSearchPageState.RESULTS_READY,
            (_RESULT_LIST_SELECTORS,),
            timeout_milliseconds,
        )

    def _require_state(self, state: DouyinSearchPageState) -> None:
        if self.observe().state is not state:
            raise DouyinSearchPageRejected

    def _require_locator(self, selectors: tuple[str, ...]) -> _Locator:
        try:
            locator = _visible_locator(self._page, selectors)
        except Exception:
            raise DouyinSearchPageRejected from None
        if locator is None:
            raise DouyinSearchPageRejected
        return locator

    def _wait_for_state(
        self,
        expected: DouyinSearchPageState,
        anchor_groups: tuple[tuple[str, ...], ...],
        timeout_milliseconds: int,
    ) -> DouyinSearchPageObservation:
        if (
            type(timeout_milliseconds) is not int
            or not 1 <= timeout_milliseconds <= _MAX_WAIT_MILLISECONDS
        ):
            raise DouyinSearchPageRejected
        observation = self.observe()
        if not _can_wait(observation, expected):
            return observation
        deadline = monotonic() + timeout_milliseconds / 1_000
        for selectors in anchor_groups:
            remaining = (deadline - monotonic()) * 1_000
            if remaining <= 0:
                return self.observe()
            try:
                self._page.locator(", ".join(selectors)).first.wait_for(
                    state="visible",
                    timeout=remaining,
                )
            except PlaywrightTimeoutError:
                return self.observe()
            except Exception:
                return self._page_unavailable()
            observation = self.observe()
            if not _can_wait(observation, expected):
                return observation
        return observation

    def _page_unavailable(self) -> DouyinSearchPageObservation:
        try:
            version = self._versions.check(self._page.url)
        except Exception:
            version = self._versions.check("")
        if not version.compatible:
            return _observation(
                version,
                DouyinSearchPageState.UNKNOWN,
                DouyinSearchPageEvidence.PAGE_VERSION_UNKNOWN,
            )
        return _observation(
            version,
            DouyinSearchPageState.UNKNOWN,
            DouyinSearchPageEvidence.PAGE_UNAVAILABLE,
        )


def _visible_locator(page: _Page, selectors: tuple[str, ...]) -> _Locator | None:
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.is_visible():
            return locator
    return None


def _can_wait(
    observation: DouyinSearchPageObservation,
    expected: DouyinSearchPageState,
) -> bool:
    return (
        observation.state is DouyinSearchPageState.UNKNOWN
        and observation.evidence is DouyinSearchPageEvidence.REQUIRED_ANCHOR_MISSING
        and (
            (
                expected is DouyinSearchPageState.HOME_READY
                and observation.entry is DouyinPageEntry.HOME
            )
            or (
                expected is DouyinSearchPageState.RESULTS_READY
                and observation.entry is DouyinPageEntry.SEARCH_RESULTS
            )
        )
    )


def _observation(
    page: DouyinPageObservation,
    state: DouyinSearchPageState,
    evidence: DouyinSearchPageEvidence,
) -> DouyinSearchPageObservation:
    return DouyinSearchPageObservation(
        page_version=page.version,
        entry=page.entry,
        state=state,
        evidence=evidence,
    )


__all__ = [
    "DOUYIN_SEARCH_PAGE_SELECTOR_VERSION",
    "DouyinSearchPage",
    "DouyinSearchPageEvidence",
    "DouyinSearchPageObservation",
    "DouyinSearchPageRejected",
    "DouyinSearchPageState",
]
