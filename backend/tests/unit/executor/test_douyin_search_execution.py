"""抖音搜索执行：导航（goto 首页）在代码里，页面交互全部由搜索技能回放。

登录/风控的前置把关在 discovery 的平台会话健康检查里；这里技能不可路由
或回放失败时，如实报 PAGE_VERSION_UNKNOWN（待技能录制/修复），不猜测原因。"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import DOUYIN_HOME_URL
from automation_tool.executor.rpa.douyin.search import (
    DouyinSearchExecution,
    DouyinSearchExecutionEvidence,
    DouyinSearchExecutionRejected,
    DouyinSearchExecutionState,
)
from automation_tool.executor.rpa.douyin.skills import DOUYIN_SEARCH_SKILL_ID
from automation_tool.executor.skill_orchestrator import (
    SkillOrchestrator,
    load_seed_registry,
)
from automation_tool.executor.skill_registry import SkillRegistry
from automation_tool.protocol import DouyinSearchInput


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
    """脚本化语义页面：路径可设，锚点按名字可寻。"""

    def __init__(
        self, *, missing: set[str] | None = None, path: str = "/search/护肤"
    ) -> None:
        self.missing = missing or set()
        self.path = path
        self.side_effects: list[tuple[str, str, str | None]] = []
        self.filled: list[str] = []

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
        if kind == "url_prefix_matches":
            return pattern is not None and self.path.startswith(pattern)
        return True

    def act(self, kind: str, handle: object, value: str | None) -> None:
        _role, name = cast(tuple[str, str], handle)
        self.side_effects.append((kind, name, value))
        if kind == "fill" and value is not None:
            self.filled.append(value)

    def current_path(self) -> str:
        return self.path


def execution(
    goto_page: GotoPage,
    replay_page: FakeReplayPage,
    *,
    orchestrator: SkillOrchestrator | None = None,
) -> DouyinSearchExecution:
    return DouyinSearchExecution(
        BrowserWindow._for_runtime(object(), cast(Any, goto_page)),
        DouyinSearchInput(keyword="护肤", target_limit=10),
        orchestrator=(
            orchestrator
            if orchestrator is not None
            else SkillOrchestrator(load_seed_registry())
        ),
        replay_page_factory=lambda _window: replay_page,
    )


class TestSkillDrivenSearch:
    def test_a_replayed_skill_reports_results_ready(self) -> None:
        goto_page = GotoPage()
        replay_page = FakeReplayPage()

        observation = execution(goto_page, replay_page).run()

        assert observation.state is DouyinSearchExecutionState.SUCCEEDED
        assert observation.evidence is DouyinSearchExecutionEvidence.RESULTS_READY
        assert goto_page.visited == [DOUYIN_HOME_URL]
        # 关键词以运行时参数进入回放；搜索按钮点了一次；随后两次下滚加载结果。
        assert replay_page.filled == ["护肤"]
        assert (
            sum(1 for kind, _, _ in replay_page.side_effects if kind == "scroll") == 2
        )

    def test_no_published_skill_lands_awaiting_recording(self) -> None:
        observation = execution(
            GotoPage(),
            FakeReplayPage(),
            orchestrator=SkillOrchestrator(SkillRegistry(trusted_public_key=b"\x01" * 32)),
        ).run()

        assert observation.state is DouyinSearchExecutionState.UNKNOWN
        assert observation.evidence is DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN

    def test_a_failed_replay_lands_awaiting_repair(self) -> None:
        replay_page = FakeReplayPage(missing={"搜索"})

        observation = execution(GotoPage(), replay_page).run()

        assert observation.state is DouyinSearchExecutionState.UNKNOWN
        assert observation.evidence is DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN
        assert replay_page.side_effects == []

    def test_navigation_timeout_is_reported_as_timeout(self) -> None:
        goto_page = GotoPage(failure=PlaywrightTimeoutError("navigation timed out"))

        observation = execution(goto_page, FakeReplayPage()).run()

        assert observation.state is DouyinSearchExecutionState.TIMED_OUT
        assert observation.evidence is DouyinSearchExecutionEvidence.NAVIGATION_TIMED_OUT

    def test_navigation_failure_is_page_unavailable(self) -> None:
        goto_page = GotoPage(failure=RuntimeError("private navigation failure"))

        observation = execution(goto_page, FakeReplayPage()).run()

        assert observation.state is DouyinSearchExecutionState.UNKNOWN
        assert observation.evidence is DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE

    def test_the_execution_runs_exactly_once(self) -> None:
        runner = execution(GotoPage(), FakeReplayPage())
        assert runner.run().succeeded is True
        with pytest.raises(DouyinSearchExecutionRejected):
            runner.run()

    def test_the_wired_skill_is_the_committed_search_seed(self) -> None:
        registry = load_seed_registry()
        assert registry.at(DOUYIN_SEARCH_SKILL_ID, 1).skill.platform == "douyin"
