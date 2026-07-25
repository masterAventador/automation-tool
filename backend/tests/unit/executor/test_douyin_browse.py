from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.browse import (
    DOUYIN_BROWSE_EXECUTION_VERSION,
    DouyinBrowseExecution,
    DouyinBrowseExecutionEvidence,
    DouyinBrowseExecutionObservation,
    DouyinBrowseExecutionRejected,
    DouyinBrowseExecutionState,
)
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
from automation_tool.executor.rpa.douyin.page_version import douyin_user_profile_url
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

PROFILE_ROOT = 'main[aria-label="用户主页"]'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'


class FakeLocator:
    def __init__(self, selector: str, page: FakePage) -> None:
        self.selector = selector
        self.page = page

    @property
    def first(self) -> FakeLocator:
        return self

    def locator(self, selector: str) -> FakeLocator:
        """Every element this page models is on screen, so the filter keeps them all."""
        assert selector == VISIBLE_MATCH_ENGINE
        return self

    def count(self) -> int:
        if self.page.probe_failure:
            raise RuntimeError("private probe failure")
        return sum(
            selector in self.page.visible_selectors for selector in self.selector.split(", ")
        )

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert 0 < timeout <= 10_000
        if self.page.wait_failure:
            raise RuntimeError("private wait failure")
        callback = self.page.wait_callbacks.get(self.selector)
        if callback is not None:
            callback()
        if self.count() == 0:
            raise PlaywrightTimeoutError("private wait timeout")


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.visible_selectors: set[str] = set()
        self.navigations: list[tuple[str, str, float]] = []
        self.requested_selectors: list[str] = []
        self.wait_callbacks: dict[str, Callable[[], None]] = {}
        self.goto_timeout = False
        self.goto_failure = False
        self.probe_failure = False
        self.wait_failure = False
        self.after_goto: Callable[[], None] | None = None

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        self.navigations.append((url, wait_until, timeout))
        if self.goto_timeout:
            raise PlaywrightTimeoutError("private navigation timeout")
        if self.goto_failure:
            raise RuntimeError("private navigation failure")
        self.url = url
        if self.after_goto is not None:
            self.after_goto()

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return FakeLocator(selector, self)


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


def candidate() -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id="creator-001",
        summary=DouyinCandidateSummary(display_name="目标账号", public_handle=None),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=1,
    )


def test_browse_navigates_once_to_canonical_target_and_never_sends() -> None:
    page = FakePage()
    page.after_goto = lambda: page.visible_selectors.add(PROFILE_ROOT)
    checks: list[str] = []

    def not_cancelled() -> bool:
        checks.append("checked")
        return False

    observation = DouyinBrowseExecution(window(page), candidate()).run(
        cancellation_requested=not_cancelled
    )

    assert page.navigations == [
        (douyin_user_profile_url("creator-001"), "domcontentloaded", 30_000)
    ]
    assert checks == ["checked", "checked", "checked"]
    assert observation == DouyinBrowseExecutionObservation(
        state=DouyinBrowseExecutionState.COMPLETED,
        evidence=DouyinBrowseExecutionEvidence.PROFILE_VISIBLE,
    )
    assert observation.execution_version == DOUYIN_BROWSE_EXECUTION_VERSION
    assert observation.completed is True
    assert observation.circuit_open is False
    assert "creator-001" not in repr(observation)


@pytest.mark.parametrize(
    ("checks", "expected_navigations", "probes_dom"),
    (([True], 0, False), ([False, True], 1, False), ([False, False, True], 1, True)),
)
def test_cancellation_at_each_checkpoint_stops_without_retry(
    checks: list[bool],
    expected_navigations: int,
    probes_dom: bool,
) -> None:
    page = FakePage()
    page.after_goto = lambda: page.visible_selectors.add(PROFILE_ROOT)
    values = iter(checks)
    observation = DouyinBrowseExecution(window(page), candidate()).run(
        cancellation_requested=lambda: next(values)
    )
    assert observation.state is DouyinBrowseExecutionState.CANCELLED
    assert observation.evidence is DouyinBrowseExecutionEvidence.CANCELLATION_REQUESTED
    assert len(page.navigations) == expected_navigations
    assert bool(page.requested_selectors) is probes_dom


@pytest.mark.parametrize("value", (None, 1, "false"))
def test_invalid_or_failed_cancellation_probe_fails_closed(value: object) -> None:
    page = FakePage()
    observation = DouyinBrowseExecution(window(page), candidate()).run(
        cancellation_requested=cast(Callable[[], bool], lambda: value)
    )
    assert observation.state is DouyinBrowseExecutionState.UNKNOWN
    assert observation.evidence is DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE
    assert page.navigations == []

    failed = DouyinBrowseExecution(window(FakePage()), candidate()).run(
        cancellation_requested=lambda: (_ for _ in ()).throw(RuntimeError("private"))
    )
    assert failed.evidence is DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE


@pytest.mark.parametrize(
    ("checks", "probes_dom"),
    (([False, None], False), ([False, False, None], True)),
)
def test_cancellation_probe_unavailable_after_navigation_or_page_ready_is_closed(
    checks: list[bool | None],
    probes_dom: bool,
) -> None:
    page = FakePage()
    page.after_goto = lambda: page.visible_selectors.add(PROFILE_ROOT)
    values = iter(checks)

    observation = DouyinBrowseExecution(window(page), candidate()).run(
        cancellation_requested=cast(Callable[[], bool], lambda: next(values))
    )

    assert observation.state is DouyinBrowseExecutionState.UNKNOWN
    assert observation.evidence is DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE
    assert len(page.navigations) == 1
    assert bool(page.requested_selectors) is probes_dom


@pytest.mark.parametrize(
    ("attribute", "state", "evidence"),
    (
        (
            "goto_timeout",
            DouyinBrowseExecutionState.TIMED_OUT,
            DouyinBrowseExecutionEvidence.NAVIGATION_TIMED_OUT,
        ),
        (
            "goto_failure",
            DouyinBrowseExecutionState.UNKNOWN,
            DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE,
        ),
    ),
)
def test_navigation_failures_are_bounded_redacted_and_not_retried(
    attribute: str,
    state: DouyinBrowseExecutionState,
    evidence: DouyinBrowseExecutionEvidence,
) -> None:
    page = FakePage()
    setattr(page, attribute, True)
    observation = DouyinBrowseExecution(window(page), candidate()).run(
        cancellation_requested=lambda: False
    )
    assert observation.state is state
    assert observation.evidence is evidence
    assert len(page.navigations) == 1
    assert "private" not in repr(observation)


@pytest.mark.parametrize(
    ("selectors", "url", "state", "evidence"),
    (
        (
            set(),
            douyin_user_profile_url("creator-001"),
            DouyinBrowseExecutionState.TIMED_OUT,
            DouyinBrowseExecutionEvidence.PROFILE_READY_TIMED_OUT,
        ),
        (
            {LOGIN_DIALOG, BLOCKING_DIALOG},
            douyin_user_profile_url("creator-001"),
            DouyinBrowseExecutionState.LOGIN_REQUIRED,
            DouyinBrowseExecutionEvidence.LOGIN_REQUIRED,
        ),
        (
            {BLOCKING_DIALOG},
            douyin_user_profile_url("creator-001"),
            DouyinBrowseExecutionState.DIALOG_BLOCKED,
            DouyinBrowseExecutionEvidence.BLOCKING_DIALOG,
        ),
        (
            {PROFILE_ROOT},
            "https://www.douyin.com/live",
            DouyinBrowseExecutionState.UNKNOWN,
            DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN,
        ),
    ),
)
def test_page_outcomes_map_to_closed_browse_results(
    selectors: set[str],
    url: str,
    state: DouyinBrowseExecutionState,
    evidence: DouyinBrowseExecutionEvidence,
) -> None:
    page = FakePage()

    def after_goto() -> None:
        page.visible_selectors = selectors
        page.url = url

    page.after_goto = after_goto
    observation = DouyinBrowseExecution(window(page), candidate()).run(
        cancellation_requested=lambda: False
    )
    assert observation.state is state
    assert observation.evidence is evidence


def test_driver_failure_and_duplicate_profile_anchor_fail_closed() -> None:
    failed = FakePage()
    failed.after_goto = lambda: setattr(failed, "probe_failure", True)
    unavailable = DouyinBrowseExecution(window(failed), candidate()).run(
        cancellation_requested=lambda: False
    )
    assert unavailable.evidence is DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE

    duplicate = FakePage()
    duplicate.after_goto = lambda: duplicate.visible_selectors.update(
        {PROFILE_ROOT, '[data-e2e="user-detail"]'}
    )
    conflict = DouyinBrowseExecution(window(duplicate), candidate()).run(
        cancellation_requested=lambda: False
    )
    assert conflict.state is DouyinBrowseExecutionState.UNKNOWN
    assert conflict.evidence is DouyinBrowseExecutionEvidence.CONFLICTING_ANCHORS


def test_profile_anchor_is_rechecked_immediately_before_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage()
    page.after_goto = lambda: page.visible_selectors.add(PROFILE_ROOT)

    def reject_profile_root(_page_object: object) -> None:
        raise RuntimeError("private disappearing profile")

    monkeypatch.setattr(
        "automation_tool.executor.rpa.douyin.profile_page.DouyinProfilePage.profile_root",
        reject_profile_root,
    )
    observation = DouyinBrowseExecution(window(page), candidate()).run(
        cancellation_requested=lambda: False
    )
    assert observation.state is DouyinBrowseExecutionState.UNKNOWN
    assert observation.evidence is DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE


def test_constructor_observation_and_second_run_are_closed() -> None:
    page = FakePage()
    page.after_goto = lambda: page.visible_selectors.add(PROFILE_ROOT)
    execution = DouyinBrowseExecution(window(page), candidate())
    assert repr(execution) == "DouyinBrowseExecution(<redacted>)"
    assert execution.run(cancellation_requested=lambda: False).completed
    with pytest.raises(DouyinBrowseExecutionRejected):
        execution.run(cancellation_requested=lambda: False)
    with pytest.raises(DouyinBrowseExecutionRejected):
        DouyinBrowseExecution(cast(BrowserWindow, object()), candidate())
    with pytest.raises(DouyinBrowseExecutionRejected):
        DouyinBrowseExecution(window(page), cast(DouyinCandidate, object()))
    with pytest.raises(DouyinBrowseExecutionRejected):
        DouyinBrowseExecution(window(page), candidate()).run(
            cancellation_requested=cast(Callable[[], bool], object())
        )

    valid = DouyinBrowseExecutionObservation(
        state=DouyinBrowseExecutionState.COMPLETED,
        evidence=DouyinBrowseExecutionEvidence.PROFILE_VISIBLE,
    )
    for changes in (
        {"state": cast(DouyinBrowseExecutionState, "completed")},
        {"execution_version": "private"},
        {"evidence": DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE},
    ):
        with pytest.raises(DouyinBrowseExecutionRejected):
            replace(valid, **changes)
