"""Closed Douyin web route/version model shared by Executor-side page objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import SplitResult, quote, unquote, urlsplit

from automation_tool.protocol.safe_text import contains_control_or_bidi

DOUYIN_PAGE_MODEL_VERSION = "douyin.web.v1"
DOUYIN_HOME_URL = "https://www.douyin.com/"
DOUYIN_SESSION_PROBE_URL = "https://www.douyin.com/user/self"
DOUYIN_SEARCH_ENTRY_URL = "https://www.douyin.com/search"

_OFFICIAL_HOST = "www.douyin.com"
_SESSION_PATH = "/user/self"
_SEARCH_PATH_PREFIX = "/search/"
_SEARCH_QUERY = "type=general"
_MAX_PAGE_URL_CHARACTERS = 2048
_MAX_SEARCH_ROUTE_CHARACTERS = 256
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class DouyinPageVersionRejected(RuntimeError):
    """The page cannot be used through a known Douyin web contract."""

    def __init__(self) -> None:
        super().__init__("douyin page version is unavailable")


class DouyinPageVersion(StrEnum):
    WEB_V1 = DOUYIN_PAGE_MODEL_VERSION
    UNKNOWN = "unknown"


class DouyinPageEntry(StrEnum):
    HOME = "home"
    SESSION_PROBE = "session_probe"
    SEARCH_RESULTS = "search_results"
    UNKNOWN = "unknown"


class DouyinPageEvidence(StrEnum):
    KNOWN_HOME_ENTRY = "known_home_entry"
    KNOWN_SESSION_ENTRY = "known_session_entry"
    KNOWN_SEARCH_ENTRY = "known_search_entry"
    ORIGIN_INVALID = "origin_invalid"
    ENTRY_UNKNOWN = "entry_unknown"
    SEARCH_ROUTE_INVALID = "search_route_invalid"


_KNOWN_EVIDENCE = {
    DouyinPageEntry.HOME: DouyinPageEvidence.KNOWN_HOME_ENTRY,
    DouyinPageEntry.SESSION_PROBE: DouyinPageEvidence.KNOWN_SESSION_ENTRY,
    DouyinPageEntry.SEARCH_RESULTS: DouyinPageEvidence.KNOWN_SEARCH_ENTRY,
}
_FAILURE_EVIDENCE = frozenset(
    {
        DouyinPageEvidence.ORIGIN_INVALID,
        DouyinPageEvidence.ENTRY_UNKNOWN,
        DouyinPageEvidence.SEARCH_ROUTE_INVALID,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinPageObservation:
    version: DouyinPageVersion
    entry: DouyinPageEntry
    evidence: DouyinPageEvidence

    def __post_init__(self) -> None:
        if (
            not isinstance(self.version, DouyinPageVersion)
            or not isinstance(self.entry, DouyinPageEntry)
            or not isinstance(self.evidence, DouyinPageEvidence)
        ):
            raise DouyinPageVersionRejected
        if self.version is DouyinPageVersion.WEB_V1:
            if _KNOWN_EVIDENCE.get(self.entry) is not self.evidence:
                raise DouyinPageVersionRejected
        elif (
            self.version is not DouyinPageVersion.UNKNOWN
            or self.entry is not DouyinPageEntry.UNKNOWN
            or self.evidence not in _FAILURE_EVIDENCE
        ):
            raise DouyinPageVersionRejected

    @property
    def model_version(self) -> str:
        return self.version.value

    @property
    def compatible(self) -> bool:
        return self.version is DouyinPageVersion.WEB_V1

    @property
    def circuit_open(self) -> bool:
        return not self.compatible

    def __repr__(self) -> str:
        return (
            "DouyinPageObservation("
            f"version={self.version.value!r}, entry={self.entry.value!r}, "
            f"evidence={self.evidence.value!r}, compatible={self.compatible!r})"
        )


class DouyinPageVersionModel:
    """Recognize only the official routes owned by the installed v1 adapter."""

    def check(self, source: object) -> DouyinPageObservation:
        parsed = _official_url(source)
        if parsed is None:
            return _unknown(DouyinPageEvidence.ORIGIN_INVALID)
        if parsed.path == "/" and not parsed.query:
            return _known(DouyinPageEntry.HOME)
        if parsed.path == _SESSION_PATH and not parsed.query:
            return _known(DouyinPageEntry.SESSION_PROBE)
        if parsed.path == DOUYIN_SEARCH_ENTRY_URL.removeprefix(
            "https://www.douyin.com"
        ) or parsed.path.startswith(_SEARCH_PATH_PREFIX):
            if _valid_search_route(parsed):
                return _known(DouyinPageEntry.SEARCH_RESULTS)
            return _unknown(DouyinPageEvidence.SEARCH_ROUTE_INVALID)
        return _unknown(DouyinPageEvidence.ENTRY_UNKNOWN)

    def require_entry(
        self,
        source: object,
        expected: DouyinPageEntry,
    ) -> DouyinPageObservation:
        if not isinstance(expected, DouyinPageEntry) or expected is DouyinPageEntry.UNKNOWN:
            raise DouyinPageVersionRejected
        observation = self.check(source)
        if not observation.compatible or observation.entry is not expected:
            raise DouyinPageVersionRejected
        return observation

    def __repr__(self) -> str:
        return f"DouyinPageVersionModel(version={DOUYIN_PAGE_MODEL_VERSION!r})"


def _official_url(source: object) -> SplitResult | None:
    if (
        type(source) is not str
        or not source
        or len(source) > _MAX_PAGE_URL_CHARACTERS
        or contains_control_or_bidi(source)
    ):
        return None
    try:
        parsed = urlsplit(source)
        if (
            parsed.scheme != "https"
            or parsed.hostname != _OFFICIAL_HOST
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            return None
    except ValueError:
        return None
    return parsed


def _valid_search_route(parsed: SplitResult) -> bool:
    if not parsed.path.startswith(_SEARCH_PATH_PREFIX) or parsed.query != _SEARCH_QUERY:
        return False
    encoded_search = parsed.path.removeprefix(_SEARCH_PATH_PREFIX)
    if (
        not encoded_search
        or "/" in encoded_search
        or _INVALID_PERCENT_ESCAPE.search(encoded_search)
    ):
        return False
    try:
        search = unquote(encoded_search, errors="strict")
    except UnicodeDecodeError:
        return False
    return (
        1 <= len(search) <= _MAX_SEARCH_ROUTE_CHARACTERS
        and not contains_control_or_bidi(search)
        and quote(search, safe="") == encoded_search
    )


def _known(entry: DouyinPageEntry) -> DouyinPageObservation:
    return DouyinPageObservation(
        version=DouyinPageVersion.WEB_V1,
        entry=entry,
        evidence=_KNOWN_EVIDENCE[entry],
    )


def _unknown(evidence: DouyinPageEvidence) -> DouyinPageObservation:
    return DouyinPageObservation(
        version=DouyinPageVersion.UNKNOWN,
        entry=DouyinPageEntry.UNKNOWN,
        evidence=evidence,
    )


__all__ = [
    "DOUYIN_HOME_URL",
    "DOUYIN_PAGE_MODEL_VERSION",
    "DOUYIN_SEARCH_ENTRY_URL",
    "DOUYIN_SESSION_PROBE_URL",
    "DouyinPageEntry",
    "DouyinPageEvidence",
    "DouyinPageObservation",
    "DouyinPageVersion",
    "DouyinPageVersionModel",
    "DouyinPageVersionRejected",
]
