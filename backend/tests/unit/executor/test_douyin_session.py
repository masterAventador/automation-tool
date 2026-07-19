from __future__ import annotations

from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.session import (
    DOUYIN_SESSION_PROBE_URL,
    DOUYIN_SESSION_SELECTOR_VERSION,
    DouyinSessionDetectionRejected,
    DouyinSessionDetector,
    DouyinSessionEvidence,
    DouyinSessionObservation,
    DouyinSessionState,
    _state_for,
)


def test_session_probe_uses_the_official_protected_self_page() -> None:
    assert DOUYIN_SESSION_PROBE_URL == "https://www.douyin.com/user/self"


class FakeLocator:
    def __init__(self, visible: bool, *, fail: bool = False) -> None:
        self._visible = visible
        self._fail = fail

    @property
    def first(self) -> FakeLocator:
        return self

    def is_visible(self) -> bool:
        if self._fail:
            raise RuntimeError("private page failure")
        return self._visible


class FakePage:
    def __init__(
        self,
        *,
        url: str = "https://www.douyin.com/",
        visible_selectors: set[str] | None = None,
        fail: bool = False,
    ) -> None:
        self.url = url
        self.visible_selectors = set() if visible_selectors is None else visible_selectors
        self.fail = fail
        self.requested_selectors: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        return FakeLocator(selector in self.visible_selectors, fail=self.fail)


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


@pytest.mark.parametrize(
    ("selector", "state", "evidence", "circuit_open"),
    [
        (
            'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]',
            DouyinSessionState.RISK,
            DouyinSessionEvidence.RISK_CHALLENGE,
            True,
        ),
        (
            '[data-e2e="login-expired"]',
            DouyinSessionState.EXPIRED,
            DouyinSessionEvidence.LOGIN_EXPIRED,
            True,
        ),
        (
            '[data-e2e="user-avatar"]',
            DouyinSessionState.HEALTHY,
            DouyinSessionEvidence.AUTHENTICATED_SHELL,
            False,
        ),
        (
            '[data-e2e="login-button"]',
            DouyinSessionState.MISSING,
            DouyinSessionEvidence.LOGIN_ENTRY,
            True,
        ),
    ],
)
def test_detector_maps_one_visible_page_fact_to_one_closed_state(
    selector: str,
    state: DouyinSessionState,
    evidence: DouyinSessionEvidence,
    circuit_open: bool,
) -> None:
    page = FakePage(visible_selectors={selector})

    observation = DouyinSessionDetector().check(window(page))

    assert observation.state is state
    assert observation.evidence is evidence
    assert observation.circuit_open is circuit_open
    assert observation.selector_version == "douyin.session.v1"
    assert "douyin.com" not in repr(observation)


def test_conflicting_or_absent_evidence_fails_closed_to_unknown() -> None:
    conflicting = FakePage(
        visible_selectors={
            '[data-e2e="user-avatar"]',
            '[data-e2e="login-button"]',
        }
    )
    conflict = DouyinSessionDetector().check(window(conflicting))
    absent = DouyinSessionDetector().check(window(FakePage()))

    assert conflict.state is DouyinSessionState.UNKNOWN
    assert conflict.evidence is DouyinSessionEvidence.CONFLICTING
    assert conflict.circuit_open
    assert absent.state is DouyinSessionState.UNKNOWN
    assert absent.evidence is DouyinSessionEvidence.INSUFFICIENT
    assert absent.circuit_open


def test_wrong_origin_and_page_failure_are_unknown_without_reflection() -> None:
    wrong_origin = FakePage(
        url="https://example.com/private?token=secret",
        visible_selectors={'[data-e2e="user-avatar"]'},
    )
    invalid = DouyinSessionDetector().check(window(wrong_origin))
    unavailable = DouyinSessionDetector().check(window(FakePage(fail=True)))

    assert invalid.state is DouyinSessionState.UNKNOWN
    assert invalid.evidence is DouyinSessionEvidence.ORIGIN_INVALID
    assert unavailable.state is DouyinSessionState.UNKNOWN
    assert unavailable.evidence is DouyinSessionEvidence.PAGE_UNAVAILABLE
    assert "example.com" not in repr(invalid)
    assert "secret" not in str(invalid)


def test_detector_rejects_non_window_input_and_never_reads_cookie_apis() -> None:
    detector = DouyinSessionDetector()
    with pytest.raises(
        DouyinSessionDetectionRejected,
        match=r"^douyin session detection is unavailable$",
    ):
        detector.check(cast(Any, object()))


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://www.douyin.com/\u202eprivate",
        "https://www.douyin.com:not-a-port/",
    ],
)
def test_malformed_official_origin_candidates_fail_closed(url: str) -> None:
    observation = DouyinSessionDetector().check(window(FakePage(url=url)))

    assert observation.state is DouyinSessionState.UNKNOWN
    assert observation.evidence is DouyinSessionEvidence.ORIGIN_INVALID


@pytest.mark.parametrize(
    "changes",
    [
        {"state": cast(Any, "healthy")},
        {"evidence": cast(Any, "authenticated_shell")},
        {"selector_version": "douyin.session.v2"},
    ],
)
def test_observation_and_internal_state_mapping_reject_changed_contracts(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "state": DouyinSessionState.HEALTHY,
        "evidence": DouyinSessionEvidence.AUTHENTICATED_SHELL,
        "selector_version": DOUYIN_SESSION_SELECTOR_VERSION,
    }
    values.update(changes)
    with pytest.raises(DouyinSessionDetectionRejected):
        DouyinSessionObservation(**cast(Any, values))

    with pytest.raises(DouyinSessionDetectionRejected):
        _state_for(DouyinSessionEvidence.CONFLICTING)
