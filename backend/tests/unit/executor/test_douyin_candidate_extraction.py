"""候选提取：语义枚举（role=article 的卡片、role=link 的作者链接），
不再依赖任何 data-e2e/CSS 锚点。

隐私边界保留：只读作者链接的 href 与可见文本；href 必须是站内 /user/<id>
形态；含控制/双向字符或畸形 /user/ 路径按隐私拒绝整次提取。身份歧义
（一张卡片两个不同作者）跳过该卡片而不是猜。"""

from __future__ import annotations

from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.candidate_extraction import (
    DouyinCandidateExtraction,
    DouyinCandidateExtractionEvidence,
    DouyinCandidateExtractionRejected,
    DouyinCandidateExtractionState,
)

PAGE_REVISION = 7


class Link:
    def __init__(self, href: str | None, text: str, *, visible: bool = True) -> None:
        self.href = href
        self.text = text
        self.visible = visible

    def is_visible(self) -> bool:
        return self.visible

    def get_attribute(self, name: str) -> str | None:
        assert name == "href"
        return self.href

    def inner_text(self) -> str:
        return self.text


class Locator:
    def __init__(self, items: list[object], failure: Exception | None = None) -> None:
        self.items = items
        self.failure = failure

    def count(self) -> int:
        if self.failure is not None:
            raise self.failure
        return len(self.items)

    def nth(self, index: int) -> object:
        return self.items[index]


class Row:
    def __init__(self, links: list[Link]) -> None:
        self.links = links

    def get_by_role(self, role: str) -> Locator:
        assert role == "link"
        return Locator(cast(list[object], self.links))


class SemanticPage:
    def __init__(self, rows: list[Row], *, failure: Exception | None = None) -> None:
        self.rows = rows
        self.failure = failure

    def get_by_role(self, role: str) -> Locator:
        assert role == "article"
        return Locator(cast(list[object], self.rows), self.failure)


def author(target: str, name: str) -> Link:
    return Link(f"https://www.douyin.com/user/{target}", name)


def video_link() -> Link:
    return Link("https://www.douyin.com/video/7351234567890123456", "视频标题")


def extraction(
    page: SemanticPage, *, maximum: int = 10, page_revision: int = PAGE_REVISION
) -> DouyinCandidateExtraction:
    return DouyinCandidateExtraction(
        BrowserWindow._for_runtime(object(), cast(Any, page)),
        maximum=maximum,
        page_revision=page_revision,
    )


class TestSemanticExtraction:
    def test_author_links_become_bounded_candidates(self) -> None:
        page = SemanticPage(
            [
                Row([video_link(), author("creator-001", "护肤达人")]),
                Row([author("creator-002", "美妆博主"), video_link()]),
                Row([author("creator-003", "第三位")]),
            ]
        )

        observation = extraction(page, maximum=2).run()

        assert observation.state is DouyinCandidateExtractionState.COMPLETED
        assert observation.evidence is (
            DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED
        )
        assert [candidate.platform_target_id for candidate in observation.candidates] == [
            "creator-001",
            "creator-002",
        ]
        assert [
            candidate.summary.display_name for candidate in observation.candidates
        ] == ["护肤达人", "美妆博主"]
        assert all(
            candidate.page_revision == PAGE_REVISION
            for candidate in observation.candidates
        )

    def test_rows_without_an_author_link_are_skipped(self) -> None:
        page = SemanticPage(
            [
                Row([video_link()]),
                Row([author("creator-002", "美妆博主")]),
            ]
        )

        observation = extraction(page).run()

        assert [candidate.platform_target_id for candidate in observation.candidates] == [
            "creator-002"
        ]

    def test_an_ambiguous_row_with_two_authors_is_skipped_not_guessed(self) -> None:
        page = SemanticPage(
            [
                Row([author("creator-001", "甲"), author("creator-002", "乙")]),
                Row([author("creator-003", "丙")]),
            ]
        )

        observation = extraction(page).run()

        # 一张卡片两个人——把动作对准谁都可能错，跳过整张卡片。
        assert [candidate.platform_target_id for candidate in observation.candidates] == [
            "creator-003"
        ]

    def test_duplicate_links_to_the_same_author_yield_one_candidate(self) -> None:
        # 头像链接常无文字，名字链接有文字——同一目标取非空名。
        avatar = Link("https://www.douyin.com/user/creator-001", "")
        page = SemanticPage([Row([avatar, author("creator-001", "护肤达人")])])

        observation = extraction(page).run()

        assert len(observation.candidates) == 1
        assert observation.candidates[0].summary.display_name == "护肤达人"

    def test_invisible_links_do_not_count(self) -> None:
        hidden = Link("https://www.douyin.com/user/creator-009", "隐藏作者", visible=False)
        page = SemanticPage([Row([hidden, video_link()])])

        observation = extraction(page).run()

        assert observation.state is DouyinCandidateExtractionState.COMPLETED
        assert observation.candidates == ()

    def test_zero_result_rows_are_results_unavailable(self) -> None:
        observation = extraction(SemanticPage([])).run()

        assert observation.state is DouyinCandidateExtractionState.UNKNOWN
        assert observation.evidence is (
            DouyinCandidateExtractionEvidence.RESULTS_UNAVAILABLE
        )

    def test_a_hostile_href_rejects_the_whole_extraction(self) -> None:
        hostile = Link("https://www.douyin.com/user/creator‮001", "作者")
        page = SemanticPage([Row([hostile])])

        observation = extraction(page).run()

        assert observation.evidence is DouyinCandidateExtractionEvidence.PRIVACY_REJECTED
        assert observation.candidates == ()

    def test_a_malformed_user_path_rejects_the_whole_extraction(self) -> None:
        nested = Link("https://www.douyin.com/user/creator-001/extra", "作者")
        page = SemanticPage([Row([nested])])

        observation = extraction(page).run()

        assert observation.evidence is DouyinCandidateExtractionEvidence.PRIVACY_REJECTED

    def test_a_foreign_host_link_is_not_an_author(self) -> None:
        foreign = Link("https://evil.example/user/creator-001", "作者")
        page = SemanticPage([Row([foreign, author("creator-002", "真作者")])])

        observation = extraction(page).run()

        assert [candidate.platform_target_id for candidate in observation.candidates] == [
            "creator-002"
        ]

    def test_a_page_failure_is_page_unavailable(self) -> None:
        page = SemanticPage([], failure=RuntimeError("private page failure"))

        observation = extraction(page).run()

        assert observation.evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE

    def test_the_extraction_runs_exactly_once_and_validates_bounds(self) -> None:
        runner = extraction(SemanticPage([Row([author("creator-001", "作者")])]))
        assert runner.run().completed is True
        with pytest.raises(DouyinCandidateExtractionRejected):
            runner.run()

        with pytest.raises(DouyinCandidateExtractionRejected):
            extraction(SemanticPage([]), maximum=0)
