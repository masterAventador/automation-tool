from __future__ import annotations

from typing import Any, cast

import pytest

from automation_tool.protocol import (
    DOUYIN_CANDIDATE_VERSION,
    MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS,
    MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS,
    MAX_DOUYIN_TARGET_ID_CHARACTERS,
    DouyinCandidate,
    DouyinCandidateKey,
    DouyinCandidateRejected,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)


def candidate(
    *,
    target_id: Any = "MS4wLjABAAAA_stable-target-01",
    display_name: Any = "新能源观察员",
    public_handle: Any = "douyin_123456",
    source: Any = DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
    page_revision: Any = 1,
) -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id=target_id,
        summary=DouyinCandidateSummary(
            display_name=display_name,
            public_handle=public_handle,
        ),
        source=source,
        page_revision=page_revision,
    )


def test_candidate_contains_only_stable_identity_minimum_summary_and_page_fact() -> None:
    value = candidate()

    assert value.platform_target_id == "MS4wLjABAAAA_stable-target-01"
    assert value.summary.display_name == "新能源观察员"
    assert value.summary.public_handle == "douyin_123456"
    assert value.source is DouyinCandidateSource.GENERAL_SEARCH_AUTHOR
    assert value.page_revision == 1
    assert value.version == DOUYIN_CANDIDATE_VERSION
    assert str(value.dedupe_key) == "atdck1_l-ib0HUDDah7zsDX6o1Stfgv8ieJnwEcBYSSk3h4ZHU"


def test_dedupe_key_is_stable_across_summary_and_page_revision_changes() -> None:
    first = candidate()
    renamed = candidate(
        display_name="名称已变化",
        public_handle=None,
        page_revision=2**53 - 1,
    )
    other = candidate(target_id="MS4wLjABAAAA_stable-target-02")

    assert first.dedupe_key == renamed.dedupe_key
    assert hash(first.dedupe_key) == hash(renamed.dedupe_key)
    assert first.dedupe_key != other.dedupe_key
    assert DouyinCandidateKey.parse(str(first.dedupe_key)) is not first.dedupe_key
    assert DouyinCandidateKey.parse(first.dedupe_key) is first.dedupe_key


@pytest.mark.parametrize(
    "target_id",
    (
        "",
        " leading",
        "trailing ",
        "https://www.douyin.com/user/private",
        "target/path",
        "target?query",
        "目标",
        "a" * 129,
        None,
        1,
    ),
)
def test_platform_target_id_is_canonical_bounded_and_url_free(target_id: Any) -> None:
    with pytest.raises(DouyinCandidateRejected, match="candidate is invalid") as captured:
        candidate(target_id=target_id)

    assert "private" not in str(captured.value)


@pytest.mark.parametrize(
    "display_name",
    (
        "",
        " ",
        " leading",
        "trailing\u00a0",
        "line\nbreak",
        "control\u0085value",
        "bidi\u202evalue",
        "password=private-value",
        "file:///private-value",
        "data:text/plain,private-value",
        "名" * 81,
        None,
        1,
    ),
)
def test_display_name_uses_shared_safe_text_policy(display_name: Any) -> None:
    with pytest.raises(DouyinCandidateRejected, match="candidate is invalid") as captured:
        candidate(display_name=display_name)

    assert "private-value" not in str(captured.value)


@pytest.mark.parametrize(
    "public_handle",
    (
        "",
        " leading",
        "trailing ",
        "@douyin_user",
        "handle/path",
        "抖音号",
        "a" * 65,
        1,
    ),
)
def test_optional_public_handle_is_canonical_ascii_when_present(public_handle: Any) -> None:
    with pytest.raises(DouyinCandidateRejected, match="candidate is invalid"):
        candidate(public_handle=public_handle)

    assert candidate(public_handle=None).summary.public_handle is None


@pytest.mark.parametrize("page_revision", (0, -1, 2**53, True, 1.0, "1"))
def test_page_revision_uses_cross_runtime_safe_integer(page_revision: Any) -> None:
    with pytest.raises(DouyinCandidateRejected, match="candidate is invalid"):
        candidate(page_revision=page_revision)


def test_candidate_rejects_untyped_source_summary_or_forged_key() -> None:
    with pytest.raises(DouyinCandidateRejected):
        candidate(source="general_search_author")
    with pytest.raises(DouyinCandidateRejected):
        DouyinCandidate(
            platform_target_id="target",
            summary=cast(DouyinCandidateSummary, object()),
            source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
            page_revision=1,
        )

    for key in (
        "",
        "atdck1_short",
        "ATDCK1_Yg37USlVQioHc-ZtllZ1uKhv50ObKu6-Ll78ajO5O4k",
        "atdck1_Yg37USlVQioHc+ZtllZ1uKhv50ObKu6-Ll78ajO5O4k",
        1,
    ):
        with pytest.raises(DouyinCandidateRejected):
            DouyinCandidateKey.parse(key)


def test_candidate_values_are_immutable_and_redacted() -> None:
    value = candidate()

    assert repr(value) == (
        "DouyinCandidate(source='general_search_author', page_revision=1, <redacted>)"
    )
    assert repr(value.summary) == "DouyinCandidateSummary(<redacted>)"
    assert repr(value.dedupe_key) == "DouyinCandidateKey(<stable>)"
    for owner, attribute, replacement in (
        (value, "platform_target_id", "replacement"),
        (value.summary, "display_name", "replacement"),
        (value.dedupe_key, "value", "atdck1_replacement"),
    ):
        with pytest.raises((AttributeError, TypeError)):
            setattr(owner, attribute, replacement)


def test_candidate_bounds_are_the_explicit_mvp_contract() -> None:
    assert DOUYIN_CANDIDATE_VERSION == "douyin.candidate.v1"
    assert MAX_DOUYIN_TARGET_ID_CHARACTERS == 128
    assert MAX_CANDIDATE_DISPLAY_NAME_CHARACTERS == 80
    assert MAX_CANDIDATE_PUBLIC_HANDLE_CHARACTERS == 64
