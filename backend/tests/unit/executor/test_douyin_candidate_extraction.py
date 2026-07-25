from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, fields
from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.candidate_extraction import (
    DOUYIN_CANDIDATE_EXTRACTION_VERSION,
    DouyinCandidateExtraction,
    DouyinCandidateExtractionEvidence,
    DouyinCandidateExtractionObservation,
    DouyinCandidateExtractionRejected,
    DouyinCandidateExtractionState,
)
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
from automation_tool.executor.rpa.douyin.page_version import douyin_search_results_url
from automation_tool.executor.rpa.douyin.search_page import DouyinSearchPage
from automation_tool.protocol import DouyinCandidate, DouyinCandidateSource

RESULT_LIST = '[role="feed"]'
RESULT_ITEM = '[role="feed"] > article'
RESULT_ITEM_FALLBACK = '[data-e2e="search-result-item"]'
AUTHOR = '[data-e2e="search-result-author"]'
AUTHOR_NAME = '[data-e2e="search-result-author-name"]'
LOGIN_DIALOG = '[role="dialog"]:has-text("扫码登录")'
BLOCKING_DIALOG = '[role="dialog"]'


class FakeNode:
    def __init__(
        self,
        *,
        visible: object = True,
        attributes: dict[str, object] | None = None,
        text: object = "",
        children: dict[str, list[FakeNode]] | None = None,
    ) -> None:
        self.visible = visible
        self.attributes = {} if attributes is None else attributes
        self.text = text
        self.children = {} if children is None else children


def visible(node: FakeNode) -> bool:
    value = node.visible
    if isinstance(value, Exception):
        raise value
    return cast(bool, value)


def descendants(nodes: list[FakeNode], selector: str) -> list[FakeNode]:
    children: list[FakeNode] = []
    for candidate in selector.split(", "):
        for node in nodes:
            children.extend(node.children.get(candidate, []))
    return children


class FakeLocator:
    """Models Playwright's lazy locator: every read re-evaluates the query.

    A locator holds a query, not a node. The list it points at can change
    between two reads, which is exactly the window a candidate row must not be
    read through.
    """

    def __init__(self, page: FakePage, resolve: Callable[[], list[FakeNode]]) -> None:
        self._page = page
        self._resolve = resolve

    @property
    def first(self) -> FakeLocator:
        return self.nth(0)

    def nth(self, index: int) -> FakeLocator:
        return type(self)(self._page, lambda: self._resolve()[index : index + 1])

    def locator(self, selector: str) -> FakeLocator:
        if selector == VISIBLE_MATCH_ENGINE:
            return type(self)(
                self._page,
                lambda: [node for node in self._resolve() if visible(node)],
            )
        if self._page.nested_failure and selector == AUTHOR:
            raise RuntimeError("private nested locator failure")
        return type(self)(self._page, lambda: descendants(self._resolve(), selector))

    def element_handle(self, *, timeout: float) -> FakeHandle:
        assert timeout > 0
        nodes = self._resolve()
        if not nodes:
            raise RuntimeError("private element handle timeout")
        return FakeHandle(self._page, nodes[0])

    def count(self) -> int:
        if self._page.count_value is not None:
            return cast(int, self._page.count_value)
        return len(self._resolve())


class FakeHandle:
    """Models an ElementHandle: one pinned node that is never re-resolved.

    Its read surface deliberately differs from ``FakeLocator``'s: a handle has
    no ``locator`` and its field reads take no timeout, because there is no
    selector left to wait on.
    """

    def __init__(self, page: FakePage, node: FakeNode) -> None:
        self._page = page
        self._node = node
        self.disposed = False
        page.handles.append(self)

    def query_selector_all(self, selector: str) -> list[FakeHandle]:
        query, separator, engine = selector.partition(" >> ")
        assert not separator or engine == VISIBLE_MATCH_ENGINE
        if self._page.nested_failure and query == AUTHOR:
            raise RuntimeError("private nested locator failure")
        matched = descendants([self._node], query)
        if separator:
            matched = [node for node in matched if visible(node)]
        return [type(self)(self._page, node) for node in matched]

    def dispose(self) -> None:
        self.disposed = True

    def is_visible(self) -> bool:
        answer = visible(self._node)
        self._page.drift("visibility")
        return answer

    def get_attribute(self, name: str) -> str | None:
        value = self._node.attributes.get(name)
        if isinstance(value, Exception):
            raise value
        return cast(str | None, value)

    def inner_text(self) -> str:
        if self._page.drift_on_text:
            self._page.url = "https://www.douyin.com/live"
        self._page.drift("text")
        value = self._node.text
        if isinstance(value, Exception):
            raise value
        return cast(str, value)


class FakePage:
    def __init__(
        self,
        *,
        items: list[FakeNode] | None = None,
        item_selector: str = RESULT_ITEM,
        visible_selectors: set[str] | None = None,
    ) -> None:
        self.url = douyin_search_results_url("新能源汽车")
        self.items = [] if items is None else items
        self.item_selector = item_selector
        self.visible_selectors = {RESULT_LIST} if visible_selectors is None else visible_selectors
        self.count_value: object | None = None
        self.nested_failure = False
        self.drift_on_text = False
        self.handles: list[FakeHandle] = []
        self.drift_row: FakeNode | None = None
        self.drift_trigger = ""
        self.drifted = False

    def drift(self, trigger: str) -> None:
        """Reveal one more row at the top of the feed, once, mid-read.

        A pre-rendered skeleton becoming visible shifts every later index of
        the visible-filtered feed by one.
        """
        if self.drifted or self.drift_row is None or trigger != self.drift_trigger:
            return
        self.drifted = True
        self.items.insert(0, self.drift_row)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, lambda: self._match(selector))

    def _match(self, selector: str) -> list[FakeNode]:
        matched: list[FakeNode] = []
        for candidate in selector.split(", "):
            if candidate == self.item_selector:
                matched.extend(self.items)
            elif candidate in self.visible_selectors:
                matched.append(FakeNode())
        return matched


def item(
    *,
    target_id: object = "creator-001",
    href: object = "/user/creator-001?from=general_search&token=page-secret",
    display_name: object = "创作者甲",
    public_handle: object = "creator.one",
    visible: bool = True,
) -> FakeNode:
    author = FakeNode(
        attributes={
            "data-user-id": target_id,
            "data-user-handle": public_handle,
            "href": href,
            "data-avatar": "https://private.invalid/avatar-secret.jpg",
            "data-contact": "private-phone-13800000000",
        },
    )
    name = FakeNode(text=display_name)
    return FakeNode(
        visible=visible,
        attributes={"data-page-copy": "private-page-body"},
        children={AUTHOR: [author], AUTHOR_NAME: [name]},
    )


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


def extract(
    page: FakePage,
    *,
    maximum: int = 20,
    page_revision: int = 7,
) -> DouyinCandidateExtractionObservation:
    return DouyinCandidateExtraction(
        window(page),
        maximum=maximum,
        page_revision=page_revision,
    ).run()


def test_success_returns_only_minimum_candidate_and_discards_private_page_data() -> None:
    observation = extract(FakePage(items=[item()]))

    assert observation.state is DouyinCandidateExtractionState.COMPLETED
    assert observation.evidence is DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED
    assert observation.completed is True
    assert observation.circuit_open is False
    assert observation.candidate_count == 1
    assert observation.extraction_version == DOUYIN_CANDIDATE_EXTRACTION_VERSION
    assert observation.requested_limit == 20
    assert observation.page_revision == 7
    candidate = observation.candidates[0]
    assert candidate.platform_target_id == "creator-001"
    assert candidate.summary.display_name == "创作者甲"
    assert candidate.summary.public_handle == "creator.one"
    assert candidate.source is DouyinCandidateSource.GENERAL_SEARCH_AUTHOR
    assert {field.name for field in fields(candidate)} == {
        "platform_target_id",
        "summary",
        "source",
        "page_revision",
        "dedupe_key",
    }
    serialized = repr(asdict(observation))
    for private in (
        "page-secret",
        "private-page-body",
        "avatar-secret",
        "private-phone",
        "https://www.douyin.com/user/",
    ):
        assert private not in serialized
        assert private not in repr(observation)


def test_relative_and_official_absolute_author_routes_are_reduced_to_target_ids() -> None:
    page = FakePage(
        items=[
            item(
                target_id=None,
                href="/user/relative-1?tracking=discard-me",
                display_name="  相对链接作者  ",
                public_handle="",
            ),
            item(
                target_id=None,
                href="https://www.douyin.com/user/absolute-2?token=discard-me",
                display_name="绝对链接作者",
                public_handle=None,
            ),
        ]
    )

    observation = extract(page, maximum=2, page_revision=9)

    assert [value.platform_target_id for value in observation.candidates] == [
        "relative-1",
        "absolute-2",
    ]
    assert [value.summary.display_name for value in observation.candidates] == [
        "相对链接作者",
        "绝对链接作者",
    ]
    assert all(value.summary.public_handle is None for value in observation.candidates)
    assert all(value.page_revision == 9 for value in observation.candidates)


def test_maximum_bounds_output_and_versioned_result_item_fallback_is_used() -> None:
    page = FakePage(
        items=[
            item(target_id="creator-1", href="/user/creator-1"),
            item(target_id="creator-2", href="/user/creator-2"),
            item(target_id="creator-3", href="/user/creator-3"),
        ],
        item_selector=RESULT_ITEM_FALLBACK,
    )

    observation = extract(page, maximum=2)

    assert [value.platform_target_id for value in observation.candidates] == [
        "creator-1",
        "creator-2",
    ]


@pytest.mark.parametrize(
    "href",
    (
        "",
        "user/creator-001",
        "https://evil.example/user/creator-001",
        "https://operator:credential@www.douyin.com/user/creator-001",
        "https://www.douyin.com:bad/user/creator-001",
        "//www.douyin.com/user/creator-001",
        "/user/creator-001#private-fragment",
        "/video/creator-001",
        "/user/creator-001/extra",
        "/user/",
        True,
    ),
)
def test_untrusted_or_credential_bearing_author_routes_fail_closed_without_echo(
    href: object,
) -> None:
    observation = extract(FakePage(items=[item(target_id=None, href=href)]))

    assert observation.state is DouyinCandidateExtractionState.UNKNOWN
    assert observation.evidence is DouyinCandidateExtractionEvidence.PRIVACY_REJECTED
    assert observation.candidates == ()
    assert "credential" not in repr(observation)
    assert "private-fragment" not in repr(observation)


def test_conflicting_target_id_and_author_route_fail_closed() -> None:
    observation = extract(
        FakePage(items=[item(target_id="creator-001", href="/user/different-002")])
    )

    assert observation.evidence is DouyinCandidateExtractionEvidence.PRIVACY_REJECTED
    assert observation.candidates == ()


@pytest.mark.parametrize(
    "candidate_item",
    (
        item(target_id=None, href=None),
        item(target_id="bad/id", href=None),
        item(display_name="\u202esecret"),
        item(display_name="   "),
        item(public_handle="bad/handle"),
        FakeNode(children={AUTHOR_NAME: [FakeNode(text="无作者链接")]}),
        FakeNode(children={AUTHOR: [FakeNode(attributes={"data-user-id": "creator-1"})]}),
    ),
)
def test_missing_or_noncanonical_controlled_fields_fail_closed(
    candidate_item: FakeNode,
) -> None:
    observation = extract(FakePage(items=[candidate_item]))

    assert observation.state is DouyinCandidateExtractionState.UNKNOWN
    assert observation.evidence is DouyinCandidateExtractionEvidence.PRIVACY_REJECTED
    assert observation.candidates == ()


def test_a_hidden_skeleton_row_is_skipped_instead_of_discarding_every_candidate() -> None:
    """A pre-rendered skeleton row must not throw away the real rows behind it.

    Counting unfiltered rows makes the first index the skeleton, and the
    visibility check then rejects the whole snapshot. A result feed that always
    renders a skeleton would therefore never yield a single candidate.
    """
    observation = extract(FakePage(items=[FakeNode(visible=False), item()]))

    assert observation.state is DouyinCandidateExtractionState.COMPLETED
    assert observation.evidence is DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED
    assert observation.candidate_count == 1
    assert observation.candidates[0].platform_target_id == "creator-001"


def test_a_hidden_author_placeholder_does_not_hide_the_real_author_node() -> None:
    """The card's own template nodes must not look like a missing author."""
    card = item()
    card.children[AUTHOR].insert(0, FakeNode(visible=False, attributes={"data-user-id": "ghost"}))
    card.children[AUTHOR_NAME].insert(0, FakeNode(visible=False, text="占位"))

    observation = extract(FakePage(items=[card]))

    assert observation.state is DouyinCandidateExtractionState.COMPLETED
    assert observation.candidate_count == 1
    assert observation.candidates[0].platform_target_id == "creator-001"
    assert observation.candidates[0].summary.display_name == "创作者甲"


def test_two_visible_author_nodes_in_one_card_fail_closed() -> None:
    """Reading identity facts off an arbitrary one of two authors targets a stranger."""
    card = item()
    card.children[AUTHOR].append(
        FakeNode(attributes={"data-user-id": "creator-002", "href": "/user/creator-002"})
    )

    observation = extract(FakePage(items=[card]))

    assert observation.state is DouyinCandidateExtractionState.UNKNOWN
    assert observation.evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE
    assert observation.candidates == ()


def drifting_row() -> FakeNode:
    """The row that appears at the top of the feed while a candidate is read."""
    return item(
        target_id="creator-drift",
        href="/user/creator-drift",
        display_name="骨架占位",
        public_handle="drift.one",
    )


def test_a_row_revealed_between_two_field_reads_cannot_mix_two_authors() -> None:
    """One candidate's identity and display name must come from one row.

    A lazy locator re-resolves ``nth(index)`` on every read, so a skeleton row
    becoming visible at the top of the feed between the name read and the
    identity read shifts the index onto a different card. The action would then
    be aimed at one creator while the operator, and the rendered message, name
    another.
    """
    page = FakePage(items=[item()])
    page.drift_row = drifting_row()
    page.drift_trigger = "text"

    observation = extract(page, maximum=1)

    assert page.drifted is True
    assert observation.state is DouyinCandidateExtractionState.COMPLETED
    candidate = observation.candidates[0]
    assert (candidate.platform_target_id, candidate.summary.display_name) == (
        "creator-001",
        "创作者甲",
    )
    assert candidate.summary.public_handle == "creator.one"


def test_the_row_whose_visibility_was_checked_is_the_row_that_is_read() -> None:
    """The visibility gate is worthless if the next read lands on another row."""
    page = FakePage(items=[item()])
    page.drift_row = drifting_row()
    page.drift_trigger = "visibility"

    observation = extract(page, maximum=1)

    assert page.drifted is True
    assert observation.state is DouyinCandidateExtractionState.COMPLETED
    candidate = observation.candidates[0]
    assert (candidate.platform_target_id, candidate.summary.display_name) == (
        "creator-001",
        "创作者甲",
    )


def test_every_row_snapshot_is_released_on_success_and_on_failure() -> None:
    """A pinned row holds a browser-side reference until it is disposed."""
    read = FakePage(items=[item(), item(target_id="creator-002", href="/user/creator-002")])
    assert extract(read, maximum=2).candidate_count == 2
    assert read.handles and all(handle.disposed for handle in read.handles)

    rejected = FakePage(items=[item(target_id=None, href=None)])
    assert extract(rejected).evidence is DouyinCandidateExtractionEvidence.PRIVACY_REJECTED
    assert rejected.handles and all(handle.disposed for handle in rejected.handles)

    failed = FakePage(items=[item(display_name=RuntimeError("private name failure"))])
    assert extract(failed).evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE
    assert failed.handles and all(handle.disposed for handle in failed.handles)


def test_only_hidden_rows_report_an_empty_snapshot_rather_than_a_privacy_rejection() -> None:
    """A feed that has rendered nothing visible is empty, not a page we must reject."""
    observation = extract(FakePage(items=[item(visible=False)]))

    assert observation.state is DouyinCandidateExtractionState.COMPLETED
    assert observation.evidence is DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED
    assert observation.candidates == ()
    assert observation.candidate_count == 0


@pytest.mark.parametrize(
    "candidate_item",
    (
        item(target_id=RuntimeError("private attribute failure")),
        item(display_name=RuntimeError("private name failure")),
    ),
)
def test_browser_field_read_failures_are_page_unavailable_and_redacted(
    candidate_item: FakeNode,
) -> None:
    observation = extract(FakePage(items=[candidate_item]))

    assert observation.state is DouyinCandidateExtractionState.UNKNOWN
    assert observation.evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE
    assert "private" not in repr(observation)


def test_browser_visibility_or_nested_locator_failure_is_page_unavailable() -> None:
    invisible_read = FakePage(items=[item()])
    invisible_read.items[0].visible = RuntimeError("private visibility failure")
    assert extract(invisible_read).evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE

    nested_read = FakePage(items=[item()])
    nested_read.nested_failure = True
    assert extract(nested_read).evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE


@pytest.mark.parametrize(
    ("selectors", "state", "evidence"),
    (
        (
            {LOGIN_DIALOG, BLOCKING_DIALOG},
            DouyinCandidateExtractionState.BLOCKED,
            DouyinCandidateExtractionEvidence.LOGIN_REQUIRED,
        ),
        (
            {BLOCKING_DIALOG},
            DouyinCandidateExtractionState.BLOCKED,
            DouyinCandidateExtractionEvidence.BLOCKING_DIALOG,
        ),
        (
            set(),
            DouyinCandidateExtractionState.UNKNOWN,
            DouyinCandidateExtractionEvidence.RESULTS_UNAVAILABLE,
        ),
    ),
)
def test_page_state_is_rechecked_before_any_candidate_is_returned(
    selectors: set[str],
    state: DouyinCandidateExtractionState,
    evidence: DouyinCandidateExtractionEvidence,
) -> None:
    observation = extract(FakePage(items=[item()], visible_selectors=selectors))

    assert observation.state is state
    assert observation.evidence is evidence
    assert observation.candidates == ()


def test_page_count_failure_or_noncanonical_text_type_is_rejected() -> None:
    invalid_count = FakePage(items=[item()])
    invalid_count.count_value = True
    assert extract(invalid_count).evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE

    class InvalidFallbackLocator(FakeLocator):
        def count(self) -> int:
            return cast(int, True)

    class InvalidFallbackPage(FakePage):
        def locator(self, selector: str) -> FakeLocator:
            if selector == RESULT_ITEM_FALLBACK:
                return InvalidFallbackLocator(self, list)
            return super().locator(selector)

    assert (
        extract(InvalidFallbackPage()).evidence
        is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE
    )

    drifting = FakePage(items=[item(display_name="作者")])
    name = drifting.items[0].children[AUTHOR_NAME][0]
    original_text = name.text

    class DriftText(str):
        pass

    name.text = DriftText(cast(str, original_text))
    observation = extract(drifting)
    assert observation.evidence is DouyinCandidateExtractionEvidence.PRIVACY_REJECTED


def test_page_drift_during_read_discards_the_entire_candidate_snapshot() -> None:
    drifting = FakePage(items=[item()])
    drifting.drift_on_text = True

    observation = extract(drifting)

    assert observation.evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE
    assert observation.candidates == ()


def test_page_object_rejects_direct_invalid_extraction_bounds() -> None:
    page = DouyinSearchPage(window(FakePage()))
    for maximum, page_revision in ((0, 1), (1, 0), (True, 1), (1, True)):
        with pytest.raises(RuntimeError, match="search page is unavailable"):
            page.candidate_items(maximum=maximum, page_revision=page_revision)


def test_empty_result_list_is_a_valid_minimum_snapshot() -> None:
    observation = extract(FakePage(items=[]), maximum=1)

    assert observation.state is DouyinCandidateExtractionState.COMPLETED
    assert observation.candidates == ()
    assert observation.candidate_count == 0


@pytest.mark.parametrize(
    ("maximum", "page_revision"),
    ((0, 1), (101, 1), (True, 1), (1, 0), (1, 2**53), (1, True)),
)
def test_constructor_rejects_invalid_bounds_or_revision(
    maximum: Any,
    page_revision: Any,
) -> None:
    with pytest.raises(DouyinCandidateExtractionRejected, match="extraction is unavailable"):
        DouyinCandidateExtraction(
            window(FakePage()),
            maximum=maximum,
            page_revision=page_revision,
        )

    with pytest.raises(DouyinCandidateExtractionRejected):
        DouyinCandidateExtraction(
            cast(BrowserWindow, object()),
            maximum=1,
            page_revision=1,
        )


def test_extractor_is_single_shot_and_repr_is_redacted() -> None:
    extractor = DouyinCandidateExtraction(window(FakePage()), maximum=1, page_revision=1)

    assert repr(extractor) == "DouyinCandidateExtraction(<redacted>)"
    assert extractor.run().completed is True
    with pytest.raises(DouyinCandidateExtractionRejected):
        extractor.run()


def test_page_observation_failure_is_unknown_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_observation(_self: DouyinSearchPage) -> None:
        raise RuntimeError("private observation failure")

    monkeypatch.setattr(DouyinSearchPage, "observe", fail_observation)

    observation = extract(FakePage())

    assert observation.evidence is DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE
    assert "private" not in repr(observation)


def test_observation_rejects_forged_state_evidence_candidates_or_metadata() -> None:
    valid_candidate = extract(FakePage(items=[item()]), maximum=1).candidates[0]
    valid = {
        "state": DouyinCandidateExtractionState.COMPLETED,
        "evidence": DouyinCandidateExtractionEvidence.CANDIDATES_EXTRACTED,
        "candidates": (valid_candidate,),
        "requested_limit": 1,
        "page_revision": 7,
    }
    mutations = (
        {"state": "completed"},
        {"evidence": DouyinCandidateExtractionEvidence.PAGE_UNAVAILABLE},
        {"candidates": [valid_candidate]},
        {"candidates": (cast(DouyinCandidate, object()),)},
        {"requested_limit": 0},
        {"page_revision": 0},
        {"candidates": (valid_candidate, valid_candidate)},
        {
            "state": DouyinCandidateExtractionState.UNKNOWN,
            "evidence": DouyinCandidateExtractionEvidence.PRIVACY_REJECTED,
        },
    )
    for mutation in mutations:
        with pytest.raises(DouyinCandidateExtractionRejected):
            DouyinCandidateExtractionObservation(**(valid | mutation))  # type: ignore[arg-type]

    failed = DouyinCandidateExtractionObservation(
        state=DouyinCandidateExtractionState.UNKNOWN,
        evidence=DouyinCandidateExtractionEvidence.PRIVACY_REJECTED,
        candidates=(),
        requested_limit=1,
        page_revision=7,
    )
    assert failed.circuit_open is True
