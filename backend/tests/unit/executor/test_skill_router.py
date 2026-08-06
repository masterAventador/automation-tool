"""SA-06: pick a published version for a page — never just the newest.

Published versions are immutable and never deleted (SA-03). The router chooses
among the versions that *match the page in front of it* — same fingerprint,
language and viewport — and among those, prefers the one with the better
historical success rate, breaking ties by most recent hit. A/B variants may
match the same page and coexist; a rolled-back version is excluded but not
deleted, so rollback is reversible.
"""

from __future__ import annotations

import pytest
from automation_tool.executor.skill_router import (
    NoRouteAvailable,
    PageContext,
    VersionStats,
    route_skill,
)


def _version(version: int, *, fingerprint: str, language: str = "zh-CN", vw: int = 1280):
    # A minimal routing candidate: the router only reads these routing facts,
    # not the whole skill document.
    return {
        "version": version,
        "fingerprint": fingerprint,
        "language": language,
        "viewportWidth": vw,
        "disabled": False,
    }


def _context(fingerprint: str = "fp-a", language: str = "zh-CN", vw: int = 1280) -> PageContext:
    return PageContext(fingerprint=fingerprint, language=language, viewport_width=vw)


class TestRouting:
    def test_it_does_not_simply_pick_the_newest_version(self) -> None:
        candidates = [
            _version(1, fingerprint="fp-a"),
            _version(2, fingerprint="fp-a"),
        ]
        stats = {
            1: VersionStats(successes=90, failures=10, last_hit=5),
            2: VersionStats(successes=1, failures=9, last_hit=9),
        }
        # v2 is newer and most recently hit, but v1 succeeds far more often.
        chosen = route_skill(candidates, _context(), stats)
        assert chosen == 1

    def test_recency_breaks_a_success_rate_tie(self) -> None:
        candidates = [_version(1, fingerprint="fp-a"), _version(2, fingerprint="fp-a")]
        stats = {
            1: VersionStats(successes=8, failures=2, last_hit=3),
            2: VersionStats(successes=8, failures=2, last_hit=7),
        }
        assert route_skill(candidates, _context(), stats) == 2

    def test_only_versions_that_match_the_page_are_eligible(self) -> None:
        candidates = [
            _version(1, fingerprint="fp-a"),
            _version(2, fingerprint="fp-b"),  # different page variant
        ]
        stats = {
            1: VersionStats(successes=1, failures=0, last_hit=1),
            2: VersionStats(successes=100, failures=0, last_hit=9),
        }
        # v2 has a perfect record but does not match this page's fingerprint.
        assert route_skill(candidates, _context(fingerprint="fp-a"), stats) == 1

    def test_ab_variants_on_the_same_page_both_stay_eligible(self) -> None:
        candidates = [_version(1, fingerprint="fp-a"), _version(2, fingerprint="fp-a")]
        stats = {
            1: VersionStats(successes=5, failures=5, last_hit=1),
            2: VersionStats(successes=6, failures=4, last_hit=1),
        }
        assert route_skill(candidates, _context(), stats) == 2

    def test_language_and_viewport_must_match(self) -> None:
        candidates = [_version(1, fingerprint="fp-a", language="en-US", vw=1920)]
        stats = {1: VersionStats(successes=10, failures=0, last_hit=1)}
        with pytest.raises(NoRouteAvailable):
            route_skill(candidates, _context(language="zh-CN", vw=1280), stats)


class TestRollback:
    def test_a_rolled_back_version_is_excluded_but_not_deleted(self) -> None:
        v2 = _version(2, fingerprint="fp-a")
        v2["disabled"] = True
        candidates = [_version(1, fingerprint="fp-a"), v2]
        stats = {
            1: VersionStats(successes=5, failures=5, last_hit=1),
            2: VersionStats(successes=100, failures=0, last_hit=9),
        }
        # Rolled back v2 is skipped; routing falls back to v1, which still exists.
        assert route_skill(candidates, _context(), stats) == 1

    def test_all_versions_rolled_back_leaves_no_route(self) -> None:
        v1 = _version(1, fingerprint="fp-a")
        v1["disabled"] = True
        with pytest.raises(NoRouteAvailable):
            route_skill([v1], _context(), {1: VersionStats(1, 0, 1)})


class TestUnseenVersions:
    def test_a_version_with_no_history_is_eligible_but_ranked_low(self) -> None:
        # A freshly published v2 (no stats yet) should not beat a proven v1, but
        # it must still be reachable so it can accumulate a record.
        candidates = [_version(1, fingerprint="fp-a"), _version(2, fingerprint="fp-a")]
        stats = {1: VersionStats(successes=8, failures=2, last_hit=5)}
        assert route_skill(candidates, _context(), stats) == 1

        # With v1 rolled back, the unproven v2 is the only route and is returned.
        candidates[0]["disabled"] = True
        assert route_skill(candidates, _context(), stats) == 2

    def test_a_never_successful_version_yields_to_an_unproven_candidate(self) -> None:
        """REVIEW-2026-08-06 SA#3：「有记录」曾无条件压过「无记录」。

        页面改版后 v1 一路失败，SA-05/06 编译出 v2 来治它——旧排序下
        0 成功/500 失败的 v1 却永远赢，v2 永远拿不到第一次机会去积累
        记录，自愈链在路由这里死掉，除非人工停用 v1。
        """
        candidates = [_version(1, fingerprint="fp-a"), _version(2, fingerprint="fp-a")]
        stats = {1: VersionStats(successes=0, failures=500, last_hit=9)}
        assert route_skill(candidates, _context(), stats) == 2

    def test_the_yield_threshold_needs_three_pure_failures(self) -> None:
        # 端点两侧都要有用例（这条线反复栽在「新增区间没有端点用例」）：
        # 两次纯失败还不构成「版本已死」的证据，不把流量切给未知；
        # 第三次越过阈值，让位。
        candidates = [_version(1, fingerprint="fp-a"), _version(2, fingerprint="fp-a")]
        assert (
            route_skill(
                candidates, _context(), {1: VersionStats(successes=0, failures=2, last_hit=9)}
            )
            == 1
        )
        assert (
            route_skill(
                candidates, _context(), {1: VersionStats(successes=0, failures=3, last_hit=9)}
            )
            == 2
        )

    def test_a_low_but_nonzero_success_rate_still_beats_unproven(self) -> None:
        # 让位只针对「从未成功过」的版本：成功率低但确实能跑通的 v1，
        # 仍然优先于一无所知的 v2。
        candidates = [_version(1, fingerprint="fp-a"), _version(2, fingerprint="fp-a")]
        stats = {1: VersionStats(successes=1, failures=9, last_hit=9)}
        assert route_skill(candidates, _context(), stats) == 1
