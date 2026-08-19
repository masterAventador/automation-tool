"""候选提取：语义枚举一页搜索结果，隐私边界不变。

写死的 data-e2e/CSS 锚点已删除：卡片按 ``role=article`` 枚举，作者按卡片内
``role=link`` 且 href 为站内 ``/user/<id>`` 识别，显示名取该链接的可见文本。

隐私与身份规则：

* 只读作者链接的 href 与文本，其余 DOM 事实一概不读、不出模块；
* href 含控制/双向字符、或 ``/user/`` 路径畸形 → 整次提取按隐私拒绝——
  这是可疑页面的形态，不是"跳过一行"能对付的；
* 一张卡片出现两个不同作者 → 跳过该卡片，不猜动作该对准谁；
* 非作者卡片（纯视频/广告）→ 跳过。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from urllib.parse import urlsplit

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.protocol import (
    MAX_TASK_TARGET_LIMIT,
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)
from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE
from automation_tool.protocol.safe_text import contains_control_or_bidi

DOUYIN_CANDIDATE_EXTRACTION_VERSION = "douyin.candidate-extraction.v2"
_DOUYIN_ORIGIN_HOST = "www.douyin.com"
_DOUYIN_USER_PATH_PREFIX = "/user/"
_MAX_CANDIDATE_LINK_CHARACTERS = 2_048


class DouyinCandidateExtractionRejected(RuntimeError):
    """Candidate extraction cannot run through the bounded privacy boundary."""

    def __init__(self) -> None:
        super().__init__("douyin candidate extraction is unavailable")


class _PrivacyRejected(Exception):
    """The page carries author facts in a shape we refuse to interpret."""


class DouyinCandidateExtractionState(StrEnum):
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class DouyinCandidateExtractionEvidence(StrEnum):
    CANDIDATES_EXTRACTED = "candidates_extracted"
    RESULTS_UNAVAILABLE = "results_unavailable"
    PRIVACY_REJECTED = "privacy_rejected"
    PAGE_UNAVAILABLE = "page_unavailable"


_UNKNOWN_EVIDENCE = frozenset(
    {
        DouyinCandidateExtractionEvidence.RESULTS_UNAVAILABLE,
        DouyinCandidateExtractionEvidence.PRIVACY_REJECTED,
        DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCandidateExtractionObservation:
    state: DouyinCandidateExtractionState
    evidence: DouyinCandidateExtractionEvidence
    candidates: tuple[DouyinCandidate, ...]
    requested_limit: int
    page_revision: int
    extraction_version: str = DOUYIN_CANDIDATE_EXTRACTION_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, DouyinCandidateExtractionState)
            or not isinstance(self.evidence, DouyinCandidateExtractionEvidence)
            or type(self.candidates) is not tuple
            or type(self.requested_limit) is not int
            or not 1 <= self.requested_limit <= MAX_TASK_TARGET_LIMIT
            or type(self.page_revision) is not int
            or not 1 <= self.page_revision <= MAX_CROSS_RUNTIME_SEQUENCE
            or self.extraction_version != DOUYIN_CANDIDATE_EXTRACTION_VERSION
            or len(self.candidates) > self.requested_limit
            or any(
                not isinstance(candidate, DouyinCandidate)
                or candidate.page_revision != self.page_revision
                for candidate in self.candidates
            )
            or not self._state_matches_payload()
        ):
            raise DouyinCandidateExtractionRejected

    def _state_matches_payload(self) -> bool:
        if self.state is DouyinCandidateExtractionState.COMPLETED:
            return self.evidence is DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED
        return not self.candidates and self.evidence in _UNKNOWN_EVIDENCE

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def completed(self) -> bool:
        return self.state is DouyinCandidateExtractionState.COMPLETED

    @property
    def circuit_open(self) -> bool:
        return not self.completed

    def __repr__(self) -> str:
        return (
            "DouyinCandidateExtractionObservation("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"candidate_count={self.candidate_count!r}, "
            f"requested_limit={self.requested_limit!r}, "
            f"page_revision={self.page_revision!r}, "
            f"extraction_version={self.extraction_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class _Link(Protocol):
    def is_visible(self) -> bool: ...

    def get_attribute(self, name: str) -> str | None: ...

    def inner_text(self) -> str: ...


class _Locator(Protocol):
    def count(self) -> int: ...

    def nth(self, index: int) -> object: ...


class _Row(Protocol):
    def get_by_role(self, role: str) -> _Locator: ...


class _Page(Protocol):
    def get_by_role(self, role: str) -> _Locator: ...


class DouyinCandidateExtraction:
    """Return only validated Candidates; raw DOM facts never leave this module."""

    def __init__(
        self,
        window: BrowserWindow,
        *,
        maximum: int,
        page_revision: int,
    ) -> None:
        if (
            not isinstance(window, BrowserWindow)
            or type(maximum) is not int
            or not 1 <= maximum <= MAX_TASK_TARGET_LIMIT
            or type(page_revision) is not int
            or not 1 <= page_revision <= MAX_CROSS_RUNTIME_SEQUENCE
        ):
            raise DouyinCandidateExtractionRejected
        self._page = cast(_Page, window.playwright_page)
        self._maximum = maximum
        self._page_revision = page_revision
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinCandidateExtraction(<redacted>)"

    def run(self) -> DouyinCandidateExtractionObservation:
        if self._executed:
            raise DouyinCandidateExtractionRejected
        self._executed = True
        try:
            rows = self._page.get_by_role("article")
            count = rows.count()
        except Exception:
            return self._unknown(DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE)
        if type(count) is not int or count < 0:
            return self._unknown(DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE)
        if count == 0:
            return self._unknown(DouyinCandidateExtractionEvidence.RESULTS_UNAVAILABLE)

        candidates: list[DouyinCandidate] = []
        try:
            for index in range(count):
                if len(candidates) >= self._maximum:
                    break
                candidate = _candidate_from_row(
                    cast(_Row, rows.nth(index)), page_revision=self._page_revision
                )
                if candidate is not None:
                    candidates.append(candidate)
        except _PrivacyRejected:
            return self._unknown(DouyinCandidateExtractionEvidence.PRIVACY_REJECTED)
        except Exception:
            return self._unknown(DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE)
        return self._result(
            DouyinCandidateExtractionState.COMPLETED,
            DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED,
            tuple(candidates),
        )

    def _unknown(
        self,
        evidence: DouyinCandidateExtractionEvidence,
    ) -> DouyinCandidateExtractionObservation:
        return self._result(DouyinCandidateExtractionState.UNKNOWN, evidence)

    def _result(
        self,
        state: DouyinCandidateExtractionState,
        evidence: DouyinCandidateExtractionEvidence,
        candidates: tuple[DouyinCandidate, ...] = (),
    ) -> DouyinCandidateExtractionObservation:
        return DouyinCandidateExtractionObservation(
            state=state,
            evidence=evidence,
            candidates=candidates,
            requested_limit=self._maximum,
            page_revision=self._page_revision,
        )


def _candidate_from_row(row: _Row, *, page_revision: int) -> DouyinCandidate | None:
    links = row.get_by_role("link")
    link_count = links.count()
    if type(link_count) is not int or link_count < 0:
        raise _PrivacyRejected
    owners: dict[str, str] = {}
    for index in range(link_count):
        link = cast(_Link, links.nth(index))
        if not link.is_visible():
            continue
        href = link.get_attribute("href")
        if href is None or type(href) is not str:
            continue
        target_id = _target_id_from_user_href(href)
        if target_id is None:
            continue
        text = link.inner_text()
        cleaned = text.strip() if type(text) is str else ""
        existing = owners.get(target_id)
        if existing is None or (existing == "" and cleaned):
            owners[target_id] = cleaned
    if not owners or len(owners) > 1:
        # 没有作者，或两个不同作者（歧义）：跳过这张卡片，不猜。
        return None
    target_id, display_name = next(iter(owners.items()))
    if not display_name:
        return None
    try:
        return DouyinCandidate(
            platform_target_id=target_id,
            summary=DouyinCandidateSummary(
                display_name=display_name,
                public_handle=None,
            ),
            source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
            page_revision=page_revision,
        )
    except Exception:
        # 提取到的事实过不了候选自身的校验——按可疑页面拒绝，不静默丢弃。
        raise _PrivacyRejected from None


def _target_id_from_user_href(source: str) -> str | None:
    """站内 ``/user/<id>`` 链接给出目标 id；其他链接不是作者；可疑形态拒绝。"""
    if not source or len(source) > _MAX_CANDIDATE_LINK_CHARACTERS:
        return None
    if contains_control_or_bidi(source):
        # 控制/双向字符是身份混淆的经典载体——整次提取拒绝。
        raise _PrivacyRejected
    try:
        parsed = urlsplit(source)
    except (TypeError, ValueError):
        raise _PrivacyRejected from None
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme != "https"
            or parsed.hostname != _DOUYIN_ORIGIN_HOST
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
    elif not source.startswith("/") or source.startswith("//"):
        return None
    if not parsed.path.startswith(_DOUYIN_USER_PATH_PREFIX):
        return None
    target_id = parsed.path.removeprefix(_DOUYIN_USER_PATH_PREFIX)
    if not target_id or "/" in target_id or parsed.fragment:
        # /user/ 前缀命中却给不出干净 id——可疑，不跳过。
        raise _PrivacyRejected
    return target_id


__all__ = [
    "DOUYIN_CANDIDATE_EXTRACTION_VERSION",
    "DouyinCandidateExtraction",
    "DouyinCandidateExtractionEvidence",
    "DouyinCandidateExtractionObservation",
    "DouyinCandidateExtractionRejected",
    "DouyinCandidateExtractionState",
]
