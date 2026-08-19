"""抖音浏览（打开目标主页）：goto（代码）+ 浏览技能回放（自愈式自动化）。

写死选择器的主页就绪检查已删除；「主页真的加载出来了吗」由已签名的浏览
技能回答（等关注按钮出现）。技能不可路由/回放失败 → PAGE_VERSION_UNKNOWN
（待技能录制/修复）。浏览没有外部副作用，不经过闸门与台账。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_version import douyin_user_profile_url
from automation_tool.executor.rpa.douyin.skills import (
    DOUYIN_BROWSE_PROFILE_SKILL_ID,
    default_orchestrator,
)
from automation_tool.executor.skill_orchestrator import (
    SkillExecutionKind,
    SkillOrchestrator,
)
from automation_tool.executor.skill_replay_page import PlaywrightReplayPage
from automation_tool.executor.skill_replayer import ReplayPage
from automation_tool.protocol import DouyinCandidate

DOUYIN_BROWSE_EXECUTION_VERSION = "douyin.browse-execution.v2"
_NAVIGATION_TIMEOUT_MILLISECONDS = 30_000
_REPLAY_PAGE_TIMEOUT_SECONDS = 10


class DouyinBrowseExecutionRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("douyin browse execution is unavailable")


class DouyinBrowseExecutionState(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class DouyinBrowseExecutionEvidence(StrEnum):
    PROFILE_VISIBLE = "profile_visible"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLATION_UNAVAILABLE = "cancellation_unavailable"
    NAVIGATION_TIMED_OUT = "navigation_timed_out"
    # 待技能录制/修复：自动化尚不能安全驱动这个页面。
    PAGE_VERSION_UNKNOWN = "page_version_unknown"
    PAGE_UNAVAILABLE = "page_unavailable"


_ALLOWED_OBSERVATIONS = frozenset(
    {
        (
            DouyinBrowseExecutionState.COMPLETED,
            DouyinBrowseExecutionEvidence.PROFILE_VISIBLE,
        ),
        (
            DouyinBrowseExecutionState.CANCELLED,
            DouyinBrowseExecutionEvidence.CANCELLATION_REQUESTED,
        ),
        (
            DouyinBrowseExecutionState.TIMED_OUT,
            DouyinBrowseExecutionEvidence.NAVIGATION_TIMED_OUT,
        ),
        *(
            (DouyinBrowseExecutionState.UNKNOWN, evidence)
            for evidence in (
                DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE,
                DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN,
                DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE,
            )
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinBrowseExecutionObservation:
    state: DouyinBrowseExecutionState
    evidence: DouyinBrowseExecutionEvidence
    execution_version: str = DOUYIN_BROWSE_EXECUTION_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinBrowseExecutionState)
            or not isinstance(self.evidence, DouyinBrowseExecutionEvidence)
            or self.execution_version != DOUYIN_BROWSE_EXECUTION_VERSION
            or (self.state, self.evidence) not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinBrowseExecutionRejected

    @property
    def completed(self) -> bool:
        return self.state is DouyinBrowseExecutionState.COMPLETED

    @property
    def circuit_open(self) -> bool:
        return not self.completed

    def __repr__(self) -> str:
        return (
            "DouyinBrowseExecutionObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"execution_version={self.execution_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class _BrowsePage(Protocol):
    def goto(self, url: str, *, wait_until: str, timeout: float) -> object: ...


def _default_replay_page(window: BrowserWindow) -> ReplayPage:
    return PlaywrightReplayPage(
        window.playwright_page, action_timeout_seconds=_REPLAY_PAGE_TIMEOUT_SECONDS
    )


class DouyinBrowseExecution:
    """Navigate to one discovered target without triggering any page action."""

    def __init__(
        self,
        window: BrowserWindow,
        candidate: DouyinCandidate,
        *,
        orchestrator: SkillOrchestrator | None = None,
        replay_page_factory: Callable[[BrowserWindow], ReplayPage] | None = None,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or not isinstance(candidate, DouyinCandidate)
            or not (orchestrator is None or isinstance(orchestrator, SkillOrchestrator))
            or not (replay_page_factory is None or callable(replay_page_factory))
        ):
            raise DouyinBrowseExecutionRejected
        self._window = window
        self._page = cast(_BrowsePage, window.playwright_page)
        self._candidate = candidate
        self._orchestrator = orchestrator
        self._replay_page_factory = replay_page_factory or _default_replay_page
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinBrowseExecution(<redacted>)"

    def run(
        self,
        *,
        cancellation_requested: Callable[[], bool],
    ) -> DouyinBrowseExecutionObservation:
        if self._executed or not callable(cancellation_requested):
            raise DouyinBrowseExecutionRejected
        self._executed = True
        cancelled = _cancellation_requested(cancellation_requested)
        if cancelled is None:
            return _unavailable_cancellation()
        if cancelled:
            return _cancelled()

        target_url = douyin_user_profile_url(self._candidate.platform_target_id)
        try:
            self._page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=_NAVIGATION_TIMEOUT_MILLISECONDS,
            )
        except PlaywrightTimeoutError:
            return _result(
                DouyinBrowseExecutionState.TIMED_OUT,
                DouyinBrowseExecutionEvidence.NAVIGATION_TIMED_OUT,
            )
        except Exception:
            return _unavailable()

        cancelled = _cancellation_requested(cancellation_requested)
        if cancelled is None:
            return _unavailable_cancellation()
        if cancelled:
            return _cancelled()

        try:
            orchestrator = (
                self._orchestrator if self._orchestrator is not None else default_orchestrator()
            )
            report = orchestrator.execute(
                DOUYIN_BROWSE_PROFILE_SKILL_ID,
                self._replay_page_factory(self._window),
                parameters={},
            )
        except Exception:
            return _unavailable()

        if report.kind is SkillExecutionKind.REPLAYED:
            return _result(
                DouyinBrowseExecutionState.COMPLETED,
                DouyinBrowseExecutionEvidence.PROFILE_VISIBLE,
            )
        if report.kind in {
            SkillExecutionKind.NO_ROUTE,
            SkillExecutionKind.RECOVERY_PENDING,
        }:
            return _result(
                DouyinBrowseExecutionState.UNKNOWN,
                DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN,
            )
        # 浏览技能没有外部步，reconcile 分支理论上到不了；防御性归为不可用。
        return _unavailable()


def _cancellation_requested(check: Callable[[], bool]) -> bool | None:
    try:
        value = check()
    except Exception:
        return None
    return value if type(value) is bool else None


def _cancelled() -> DouyinBrowseExecutionObservation:
    return _result(
        DouyinBrowseExecutionState.CANCELLED,
        DouyinBrowseExecutionEvidence.CANCELLATION_REQUESTED,
    )


def _unavailable_cancellation() -> DouyinBrowseExecutionObservation:
    return _result(
        DouyinBrowseExecutionState.UNKNOWN,
        DouyinBrowseExecutionEvidence.CANCELLATION_UNAVAILABLE,
    )


def _unavailable() -> DouyinBrowseExecutionObservation:
    return _result(
        DouyinBrowseExecutionState.UNKNOWN,
        DouyinBrowseExecutionEvidence.PAGE_UNAVAILABLE,
    )


def _result(
    state: DouyinBrowseExecutionState,
    evidence: DouyinBrowseExecutionEvidence,
) -> DouyinBrowseExecutionObservation:
    return DouyinBrowseExecutionObservation(state=state, evidence=evidence)


__all__ = [
    "DOUYIN_BROWSE_EXECUTION_VERSION",
    "DouyinBrowseExecution",
    "DouyinBrowseExecutionEvidence",
    "DouyinBrowseExecutionObservation",
    "DouyinBrowseExecutionRejected",
    "DouyinBrowseExecutionState",
]
