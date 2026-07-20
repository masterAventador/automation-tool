from __future__ import annotations

from typing import cast

import pytest

from automation_tool.executor.rpa.douyin.page_version import (
    DOUYIN_HOME_URL,
    DOUYIN_PAGE_MODEL_VERSION,
    DOUYIN_SEARCH_ENTRY_URL,
    DOUYIN_SESSION_PROBE_URL,
    DOUYIN_VIDEO_ENTRY_URL,
    DouyinPageEntry,
    DouyinPageEvidence,
    DouyinPageObservation,
    DouyinPageVersion,
    DouyinPageVersionModel,
    DouyinPageVersionRejected,
    douyin_search_results_url,
)
from automation_tool.protocol import DouyinSearchInput


@pytest.mark.parametrize(
    ("source", "entry", "evidence"),
    (
        (DOUYIN_HOME_URL, DouyinPageEntry.HOME, DouyinPageEvidence.KNOWN_HOME_ENTRY),
        (
            "https://www.douyin.com:443/",
            DouyinPageEntry.HOME,
            DouyinPageEvidence.KNOWN_HOME_ENTRY,
        ),
        (
            DOUYIN_SESSION_PROBE_URL,
            DouyinPageEntry.SESSION_PROBE,
            DouyinPageEvidence.KNOWN_SESSION_ENTRY,
        ),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/%E6%96%B0%E8%83%BD%E6%BA%90%E6%B1%BD%E8%BD%A6?type=general",
            DouyinPageEntry.SEARCH_RESULTS,
            DouyinPageEvidence.KNOWN_SEARCH_ENTRY,
        ),
        (
            f"{DOUYIN_VIDEO_ENTRY_URL}/7351234567890123456",
            DouyinPageEntry.VIDEO_DETAIL,
            DouyinPageEvidence.KNOWN_VIDEO_ENTRY,
        ),
        (
            "https://www.douyin.com/user/creator-001",
            DouyinPageEntry.USER_PROFILE,
            DouyinPageEvidence.KNOWN_USER_PROFILE_ENTRY,
        ),
    ),
)
def test_known_official_entries_resolve_to_one_page_contract(
    source: str,
    entry: DouyinPageEntry,
    evidence: DouyinPageEvidence,
) -> None:
    observation = DouyinPageVersionModel().check(source)

    assert observation == DouyinPageObservation(
        version=DouyinPageVersion.WEB_V1,
        entry=entry,
        evidence=evidence,
    )
    assert observation.model_version == DOUYIN_PAGE_MODEL_VERSION
    assert observation.compatible is True
    assert observation.circuit_open is False
    assert source not in repr(observation)


@pytest.mark.parametrize(
    ("source", "evidence"),
    (
        (None, DouyinPageEvidence.ORIGIN_INVALID),
        ("", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://www.douyin.com/\n", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://www.douyin.com/\u202e", DouyinPageEvidence.ORIGIN_INVALID),
        ("http://www.douyin.com/", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://douyin.com/", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://www.douyin.com.example/", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://user@www.douyin.com/", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://user:password@www.douyin.com/", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://www.douyin.com:444/", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://www.douyin.com:invalid/", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://[www.douyin.com/", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://www.douyin.com/#search", DouyinPageEvidence.ORIGIN_INVALID),
        ("https://www.douyin.com/" + "a" * 2048, DouyinPageEvidence.ORIGIN_INVALID),
        ("https://www.douyin.com/live", DouyinPageEvidence.ENTRY_UNKNOWN),
        ("https://www.douyin.com/user/", DouyinPageEvidence.ENTRY_UNKNOWN),
        ("https://www.douyin.com/user/-other", DouyinPageEvidence.ENTRY_UNKNOWN),
        ("https://www.douyin.com/user/other/extra", DouyinPageEvidence.ENTRY_UNKNOWN),
        ("https://www.douyin.com/user/other?from=test", DouyinPageEvidence.ENTRY_UNKNOWN),
        ("https://www.douyin.com/?from=test", DouyinPageEvidence.ENTRY_UNKNOWN),
        ("https://www.douyin.com/user/self?from=test", DouyinPageEvidence.ENTRY_UNKNOWN),
        (f"{DOUYIN_VIDEO_ENTRY_URL}/", DouyinPageEvidence.ENTRY_UNKNOWN),
        (f"{DOUYIN_VIDEO_ENTRY_URL}/0", DouyinPageEvidence.ENTRY_UNKNOWN),
        (f"{DOUYIN_VIDEO_ENTRY_URL}/abc", DouyinPageEvidence.ENTRY_UNKNOWN),
        (f"{DOUYIN_VIDEO_ENTRY_URL}/123/extra", DouyinPageEvidence.ENTRY_UNKNOWN),
        (f"{DOUYIN_VIDEO_ENTRY_URL}/123?from=test", DouyinPageEvidence.ENTRY_UNKNOWN),
        (f"{DOUYIN_SEARCH_ENTRY_URL}", DouyinPageEvidence.SEARCH_ROUTE_INVALID),
        (f"{DOUYIN_SEARCH_ENTRY_URL}/", DouyinPageEvidence.SEARCH_ROUTE_INVALID),
        (f"{DOUYIN_SEARCH_ENTRY_URL}/%ZZ?type=general", DouyinPageEvidence.SEARCH_ROUTE_INVALID),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/%e6%96%b0?type=general",
            DouyinPageEvidence.SEARCH_ROUTE_INVALID,
        ),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/%FF?type=general",
            DouyinPageEvidence.SEARCH_ROUTE_INVALID,
        ),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/%E2%80%AE?type=general",
            DouyinPageEvidence.SEARCH_ROUTE_INVALID,
        ),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/keyword/extra?type=general",
            DouyinPageEvidence.SEARCH_ROUTE_INVALID,
        ),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/{'a' * 257}?type=general",
            DouyinPageEvidence.SEARCH_ROUTE_INVALID,
        ),
        (f"{DOUYIN_SEARCH_ENTRY_URL}/keyword", DouyinPageEvidence.SEARCH_ROUTE_INVALID),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/keyword?type=video",
            DouyinPageEvidence.SEARCH_ROUTE_INVALID,
        ),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/keyword?type=general&aid=test",
            DouyinPageEvidence.SEARCH_ROUTE_INVALID,
        ),
        (
            f"{DOUYIN_SEARCH_ENTRY_URL}/keyword?type=general&type=general",
            DouyinPageEvidence.SEARCH_ROUTE_INVALID,
        ),
    ),
)
def test_unknown_origin_entry_or_search_shape_fails_closed(
    source: object,
    evidence: DouyinPageEvidence,
) -> None:
    observation = DouyinPageVersionModel().check(source)

    assert observation.version is DouyinPageVersion.UNKNOWN
    assert observation.entry is DouyinPageEntry.UNKNOWN
    assert observation.evidence is evidence
    assert observation.compatible is False
    assert observation.circuit_open is True


def test_entry_requirement_rejects_unknown_or_mismatched_pages() -> None:
    model = DouyinPageVersionModel()

    home = model.require_entry(DOUYIN_HOME_URL, DouyinPageEntry.HOME)
    assert home.entry is DouyinPageEntry.HOME

    with pytest.raises(DouyinPageVersionRejected, match="page version is unavailable"):
        model.require_entry(DOUYIN_HOME_URL, DouyinPageEntry.SEARCH_RESULTS)
    with pytest.raises(DouyinPageVersionRejected, match="page version is unavailable"):
        model.require_entry("https://www.douyin.com/live", DouyinPageEntry.HOME)
    with pytest.raises(DouyinPageVersionRejected, match="page version is unavailable"):
        model.require_entry(DOUYIN_HOME_URL, cast(DouyinPageEntry, "home"))


@pytest.mark.parametrize(
    "values",
    (
        {
            "version": cast(DouyinPageVersion, "douyin.web.v1"),
            "entry": DouyinPageEntry.HOME,
            "evidence": DouyinPageEvidence.KNOWN_HOME_ENTRY,
        },
        {
            "version": DouyinPageVersion.WEB_V1,
            "entry": DouyinPageEntry.UNKNOWN,
            "evidence": DouyinPageEvidence.KNOWN_HOME_ENTRY,
        },
        {
            "version": DouyinPageVersion.WEB_V1,
            "entry": DouyinPageEntry.HOME,
            "evidence": DouyinPageEvidence.KNOWN_SEARCH_ENTRY,
        },
        {
            "version": DouyinPageVersion.UNKNOWN,
            "entry": DouyinPageEntry.HOME,
            "evidence": DouyinPageEvidence.ENTRY_UNKNOWN,
        },
        {
            "version": DouyinPageVersion.UNKNOWN,
            "entry": DouyinPageEntry.UNKNOWN,
            "evidence": DouyinPageEvidence.KNOWN_SESSION_ENTRY,
        },
    ),
)
def test_observation_rejects_forged_version_entry_or_evidence_combinations(
    values: dict[str, object],
) -> None:
    with pytest.raises(DouyinPageVersionRejected, match="page version is unavailable"):
        DouyinPageObservation(**values)  # type: ignore[arg-type]


def test_model_and_errors_do_not_reflect_page_input() -> None:
    source = "https://www.douyin.com/private-value"
    model = DouyinPageVersionModel()

    assert repr(model) == "DouyinPageVersionModel(version='douyin.web.v1')"
    with pytest.raises(DouyinPageVersionRejected) as captured:
        model.require_entry(source, DouyinPageEntry.HOME)
    assert source not in str(captured.value)


def test_canonical_search_url_preserves_shared_validated_keyword() -> None:
    search = DouyinSearchInput(keyword="新能源汽车 😀", target_limit=20)

    result = douyin_search_results_url(search.keyword)

    assert result == (
        f"{DOUYIN_SEARCH_ENTRY_URL}/"
        "%E6%96%B0%E8%83%BD%E6%BA%90%E6%B1%BD%E8%BD%A6%20%F0%9F%98%80?type=general"
    )
    assert (
        DouyinPageVersionModel()
        .require_entry(
            result,
            DouyinPageEntry.SEARCH_RESULTS,
        )
        .compatible
    )
    with pytest.raises(DouyinPageVersionRejected, match="page version is unavailable"):
        douyin_search_results_url(cast(str, object()))
