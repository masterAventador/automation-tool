"""抖音浏览（打开目标主页）：goto 在代码里，主页可见性由浏览技能回答。

技能不可路由/回放失败 → PAGE_VERSION_UNKNOWN（待技能录制/修复）；取消
检查语义与原实现一致：goto 前后各查一次，取消即停。"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.browse import (
    DouyinBrowseExecution,
    DouyinBrowseExecutionEvidence,
    DouyinBrowseExecutionRejected,
    DouyinBrowseExecutionState,
)
from automation_tool.executor.rpa.douyin.page_version import douyin_user_profile_url
from automation_tool.executor.rpa.douyin.skills import DOUYIN_BROWSE_PROFILE_SKILL_ID
from automation_tool.executor.skill_orchestrator import (
    SkillOrchestrator,
    load_seed_registry,
)
from automation_tool.executor.skill_registry import SkillRegistry
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

FOLLOW_BUTTON = "关注"


class GotoPage:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.visited: list[str] = []

    def goto(self, url: str, *, wait_until: str, timeout: float) -> object:
        if self.failure is not None:
            raise self.failure
        self.visited.append(url)
        return object()


class FakeReplayPage:
    def __init__(self, *, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
        self.side_effects: list[tuple[str, str, str | None]] = []

    def find(
        self,
        role: str,
        name: str,
        *,
        near_text: str | None = None,
        relative_position: str | None = None,
    ) -> object | None:
        return None if name in self.missing else (role, name)

    def holds(self, kind: str, *, role=None, name=None, pattern=None) -> bool:
        return name not in self.missing

    def act(self, kind: str, handle: object, value: str | None) -> None:
        _role, name = cast(tuple[str, str], handle)
        self.side_effects.append((kind, name, value))

    def current_path(self) -> str:
        return "/user/target-0001"


def candidate() -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id="target-0001",
        summary=DouyinCandidateSummary(display_name="目标账号", public_handle=None),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=1,
    )


def execution(
    goto_page: GotoPage,
    replay_page: FakeReplayPage,
    *,
    orchestrator: SkillOrchestrator | None = None,
) -> DouyinBrowseExecution:
    return DouyinBrowseExecution(
        BrowserWindow._for_runtime(object(), cast(Any, goto_page)),
        candidate(),
        orchestrator=(
            orchestrator
            if orchestrator is not None
            else SkillOrchestrator(load_seed_registry())
        ),
        replay_page_factory=lambda _window: replay_page,
    )


def never_cancelled() -> bool:
    return False


class TestSkillDrivenBrowse:
    def test_a_visible_profile_completes(self) -> None:
        goto_page = GotoPage()

        observation = execution(goto_page, FakeReplayPage()).run(
            cancellation_requested=never_cancelled
        )

        assert observation.state is DouyinBrowseExecutionState.COMPLETED
        assert observation.evidence is DouyinBrowseExecutionEvidence.PROFILE_VISIBLE
        assert goto_page.visited == [douyin_user_profile_url("target-0001")]

    def test_a_missing_profile_anchor_lands_awaiting_repair(self) -> None:
        observation = execution(
            GotoPage(), FakeReplayPage(missing={FOLLOW_BUTTON})
        ).run(cancellation_requested=never_cancelled)

        assert observation.state is DouyinBrowseExecutionState.UNKNOWN
        assert observation.evidence is DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN

    def test_no_published_skill_lands_awaiting_recording(self) -> None:
        observation = execution(
            GotoPage(),
            FakeReplayPage(),
            orchestrator=SkillOrchestrator(SkillRegistry(trusted_public_key=b"\x01" * 32)),
        ).run(cancellation_requested=never_cancelled)

        assert observation.evidence is DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN

    def test_navigation_timeout_is_reported_as_timeout(self) -> None:
        goto_page = GotoPage(failure=PlaywrightTimeoutError("navigation timed out"))

        observation = execution(goto_page, FakeReplayPage()).run(
            cancellation_requested=never_cancelled
        )

        assert observation.state is DouyinBrowseExecutionState.TIMED_OUT
        assert observation.evidence is DouyinBrowseExecutionEvidence.NAVIGATION_TIMED_OUT

    def test_cancellation_before_navigation_stops_without_page_contact(self) -> None:
        goto_page = GotoPage()

        observation = execution(goto_page, FakeReplayPage()).run(
            cancellation_requested=lambda: True
        )

        assert observation.state is DouyinBrowseExecutionState.CANCELLED
        assert (
            observation.evidence is DouyinBrowseExecutionEvidence.CANCELLATION_REQUESTED
        )
        assert goto_page.visited == []

    def test_cancellation_after_navigation_stops_before_the_skill(self) -> None:
        goto_page = GotoPage()
        replay_page = FakeReplayPage()
        answers = iter([False, True])

        observation = execution(goto_page, replay_page).run(
            cancellation_requested=lambda: next(answers)
        )

        assert observation.state is DouyinBrowseExecutionState.CANCELLED
        assert goto_page.visited == [douyin_user_profile_url("target-0001")]
        assert replay_page.side_effects == []

    def test_a_broken_cancellation_check_is_unavailable(self) -> None:
        def broken() -> bool:
            raise RuntimeError("private cancellation failure")

        observation = execution(GotoPage(), FakeReplayPage()).run(
            cancellation_requested=broken
        )

        assert observation.state is DouyinBrowseExecutionState.UNKNOWN
        assert (
            observation.evidence
            is DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE
        )

    def test_the_execution_runs_exactly_once(self) -> None:
        runner = execution(GotoPage(), FakeReplayPage())
        assert runner.run(cancellation_requested=never_cancelled).completed is True
        with pytest.raises(DouyinBrowseExecutionRejected):
            runner.run(cancellation_requested=never_cancelled)

    def test_the_wired_skill_is_the_committed_browse_seed(self) -> None:
        registry = load_seed_registry()
        assert (
            registry.at(DOUYIN_BROWSE_PROFILE_SKILL_ID, 1).skill.risk_level == "low"
        )
