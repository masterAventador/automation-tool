"""抖音搜索执行：goto 首页（代码）+ 搜索技能回放（自愈式自动化）。

写死选择器的搜索页对象已删除。登录/风控的前置把关在 discovery 的平台
会话健康检查里；技能不可路由或回放失败时如实报 PAGE_VERSION_UNKNOWN
（待技能录制/修复），不猜测页面上发生了什么。搜索没有外部副作用（只是
导航），不经过动作闸门与副作用台账。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import DOUYIN_HOME_URL
from automation_tool.executor.rpa.douyin.skills import (
    DOUYIN_SEARCH_KEYWORD_PARAMETER,
    DOUYIN_SEARCH_SKILL_ID,
    default_orchestrator,
)
from automation_tool.executor.skill_orchestrator import (
    SkillExecutionKind,
    SkillOrchestrator,
)
from automation_tool.executor.skill_replay_page import PlaywrightReplayPage
from automation_tool.executor.skill_replayer import ReplayPage
from automation_tool.protocol import DouyinSearchInput

DOUYIN_SEARCH_EXECUTION_VERSION = "douyin.search-execution.v2"
_NAVIGATION_TIMEOUT_MILLISECONDS = 30_000
_REPLAY_PAGE_TIMEOUT_SECONDS = 15


class DouyinSearchExecutionRejected(RuntimeError):
    """The search execution cannot run inside its fixed safety boundary."""

    def __init__(self) -> None:
        super().__init__("douyin search execution is unavailable")


class DouyinSearchExecutionState(StrEnum):
    SUCCEEDED = "succeeded"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class DouyinSearchExecutionEvidence(StrEnum):
    RESULTS_READY = "results_ready"
    NAVIGATION_TIMED_OUT = "navigation_timed_out"
    # 待技能录制/修复：自动化尚不能安全驱动这个页面。
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    PAGE_UNAVAILABLE = "page_unavailable"


_ALLOWED_OBSERVATIONS = frozenset(
    {
        (DouyinSearchExecutionState.SUCCEEDED, DouyinSearchExecutionEvidence.RESULTS_READY),
        (
            DouyinSearchExecutionState.TIMED_OUT,
            DouyinSearchExecutionEvidence.NAVIGATION_TIMED_OUT,
        ),
        (
            DouyinSearchExecutionState.UNKNOWN,
            DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN,
        ),
        (
            DouyinSearchExecutionState.UNKNOWN,
            DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinSearchExecutionObservation:
    state: DouyinSearchExecutionState
    evidence: DouyinSearchExecutionEvidence
    execution_version: str = DOUYIN_SEARCH_EXECUTION_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinSearchExecutionState)
            or not isinstance(self.evidence, DouyinSearchExecutionEvidence)
            or self.execution_version != DOUYIN_SEARCH_EXECUTION_VERSION
            or (self.state, self.evidence) not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinSearchExecutionRejected

    @property
    def succeeded(self) -> bool:
        return self.state is DouyinSearchExecutionState.SUCCEEDED

    @property
    def circuit_open(self) -> bool:
        return not self.succeeded

    def __repr__(self) -> str:
        return (
            "DouyinSearchExecutionObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"execution_version={self.execution_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class _SearchPage(Protocol):
    def goto(self, url: str, *, wait_until: str, timeout: float) -> object: ...


def _default_replay_page(window: BrowserWindow) -> ReplayPage:
    return PlaywrightReplayPage(
        window.playwright_page, action_timeout_seconds=_REPLAY_PAGE_TIMEOUT_SECONDS
    )


class DouyinSearchExecution:
    """Execute exactly one search without retries or unrelated page actions."""

    def __init__(
        self,
        window: BrowserWindow,
        search: DouyinSearchInput,
        *,
        orchestrator: SkillOrchestrator | None = None,
        replay_page_factory: Callable[[BrowserWindow], ReplayPage] | None = None,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(search, DouyinSearchInput)
            or not (orchestrator is None or isinstance(orchestrator, SkillOrchestrator))
            or not (replay_page_factory is None or callable(replay_page_factory))
        ):
            raise DouyinSearchExecutionRejected
        self._window = window
        self._page = cast(_SearchPage, window.playwright_page)
        self._search = search
        self._orchestrator = orchestrator
        self._replay_page_factory = replay_page_factory or _default_replay_page
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinSearchExecution(<redacted>)"

    def run(self) -> DouyinSearchExecutionObservation:
        if self._executed:
            raise DouyinSearchExecutionRejected
        self._executed = True
        try:
            self._page.goto(
                DOUYIN_HOME_URL,
                wait_until="domcontentloaded",
                timeout=_NAVIGATION_TIMEOUT_MILLISECONDS,
            )
        except PlaywrightTimeoutError:
            return _result(
                DouyinSearchExecutionState.TIMED_OUT,
                DouyinSearchExecutionEvidence.NAVIGATION_TIMED_OUT,
            )
        except Exception:
            return _unavailable()

        try:
            orchestrator = (
                self._orchestrator if self._orchestrator is not None else default_orchestrator()
            )
            report = orchestrator.execute(
                DOUYIN_SEARCH_SKILL_ID,
                self._replay_page_factory(self._window),
                parameters={DOUYIN_SEARCH_KEYWORD_PARAMETER: self._search.keyword},
            )
        except Exception:
            return _unavailable()

        if report.kind is SkillExecutionKind.REPLAYED:
            return _result(
                DouyinSearchExecutionState.SUCCEEDED,
                DouyinSearchExecutionEvidence.RESULTS_READY,
            )
        if report.kind in {
            SkillExecutionKind.NO_ROUTE,
            SkillExecutionKind.RECOVERY_PENDING,
        }:
            return _result(
                DouyinSearchExecutionState.UNKNOWN,
                DouyinSearchExecutionEvidence.PAGE_VERSION_UNKNOWN,
            )
        # 搜索技能没有外部步，reconcile 分支理论上到不了；防御性归为不可用。
        return _unavailable()


def _unavailable() -> DouyinSearchExecutionObservation:
    return _result(
        DouyinSearchExecutionState.UNKNOWN,
        DouyinSearchExecutionEvidence.PAGE_UNAVAILABLE,
    )


def _result(
    state: DouyinSearchExecutionState,
    evidence: DouyinSearchExecutionEvidence,
) -> DouyinSearchExecutionObservation:
    return DouyinSearchExecutionObservation(state=state, evidence=evidence)


__all__ = [
    "DOUYIN_SEARCH_EXECUTION_VERSION",
    "DouyinSearchExecution",
    "DouyinSearchExecutionEvidence",
    "DouyinSearchExecutionObservation",
    "DouyinSearchExecutionRejected",
    "DouyinSearchExecutionState",
]
