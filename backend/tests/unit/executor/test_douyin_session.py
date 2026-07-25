from __future__ import annotations

from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
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
    """Models real match/visibility semantics: several matches, some hidden."""

    def __init__(self, matches: list[bool], *, fail: bool = False) -> None:
        self._matches = matches
        self._fail = fail

    @property
    def first(self) -> FakeLocator:
        return self

    def locator(self, selector: str) -> FakeLocator:
        assert selector == VISIBLE_MATCH_ENGINE
        return FakeLocator([match for match in self._matches if match], fail=self._fail)

    def count(self) -> int:
        if self._fail:
            raise RuntimeError("private page failure")
        return len(self._matches)

    def is_visible(self) -> bool:
        if self._fail:
            raise RuntimeError("private page failure")
        return any(self._matches)


class FakePage:
    def __init__(
        self,
        *,
        url: str = "https://www.douyin.com/",
        visible_selectors: set[str] | None = None,
        hidden_selectors: set[str] | None = None,
        fail: bool = False,
    ) -> None:
        self.url = url
        self.visible_selectors = set() if visible_selectors is None else visible_selectors
        self.hidden_selectors = set() if hidden_selectors is None else hidden_selectors
        self.fail = fail
        self.requested_selectors: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        self.requested_selectors.append(selector)
        matches: list[bool] = []
        for candidate in selector.split(", "):
            # Hidden placeholders are rendered before the visible node.
            matches.extend(False for _ in range(candidate in self.hidden_selectors))
            matches.extend(True for _ in range(candidate in self.visible_selectors))
        return FakeLocator(matches, fail=self.fail)


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


def test_a_hidden_placeholder_never_hides_a_visible_risk_challenge() -> None:
    """Regression: a pre-rendered hidden captcha node must not fail open."""
    page = FakePage(
        hidden_selectors={'[data-e2e="captcha-container"]'},
        visible_selectors={'[data-e2e="captcha-container"]'},
    )

    observation = DouyinSessionDetector().check(window(page))

    assert observation.state is DouyinSessionState.RISK
    assert observation.evidence is DouyinSessionEvidence.RISK_CHALLENGE
    assert observation.circuit_open


def test_a_hidden_login_entry_alone_is_not_treated_as_visible_evidence() -> None:
    page = FakePage(
        hidden_selectors={'[data-e2e="login-button"]'},
        visible_selectors={'[data-e2e="user-avatar"]'},
    )

    observation = DouyinSessionDetector().check(window(page))

    assert observation.state is DouyinSessionState.HEALTHY
