"""Pure Control Plane policy for bounded Douyin candidate exclusions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from automation_tool.protocol import (
    MAX_TASK_TARGET_LIMIT,
    DouyinCandidate,
    DouyinCandidateKey,
)

DOUYIN_CANDIDATE_POLICY_VERSION = "douyin.candidate-policy.v1"
DOUYIN_CANDIDATE_HISTORY_WINDOW = timedelta(days=30)


class InvalidDouyinCandidatePolicy(ValueError):
    """Candidate policy input is not bounded, canonical, or internally consistent."""

    def __init__(self) -> None:
        super().__init__("Douyin candidate policy input is invalid")


class DouyinCandidateDisposition(StrEnum):
    ELIGIBLE = "eligible"
    DUPLICATE_IN_TASK = "duplicate_in_task"
    DUPLICATE_IN_HISTORY = "duplicate_in_history"
    BLACKLISTED = "blacklisted"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCandidateHistoryFact:
    """Latest relevant sighting for one stable key; no candidate summary is retained."""

    dedupe_key: DouyinCandidateKey
    observed_at: datetime

    def __post_init__(self) -> None:
        canonical_time = _canonical_utc(self.observed_at)
        if not isinstance(self.dedupe_key, DouyinCandidateKey) or canonical_time is None:
            raise InvalidDouyinCandidatePolicy
        object.__setattr__(self, "observed_at", canonical_time)

    def __repr__(self) -> str:
        return "DouyinCandidateHistoryFact(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCandidateDecision:
    candidate: DouyinCandidate
    disposition: DouyinCandidateDisposition

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, DouyinCandidate) or not isinstance(
            self.disposition, DouyinCandidateDisposition
        ):
            raise InvalidDouyinCandidatePolicy

    @property
    def eligible(self) -> bool:
        return self.disposition is DouyinCandidateDisposition.ELIGIBLE

    def __repr__(self) -> str:
        return f"DouyinCandidateDecision(disposition={self.disposition.value!r}, <redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinCandidateEvaluation:
    decisions: tuple[DouyinCandidateDecision, ...]
    evaluated_at: datetime
    policy_version: str = DOUYIN_CANDIDATE_POLICY_VERSION

    def __post_init__(self) -> None:
        canonical_time = _canonical_utc(self.evaluated_at)
        if (
            type(self.decisions) is not tuple
            or len(self.decisions) > MAX_TASK_TARGET_LIMIT
            or any(not isinstance(decision, DouyinCandidateDecision) for decision in self.decisions)
            or canonical_time is None
            or _history_cutoff(canonical_time) is None
            or self.policy_version != DOUYIN_CANDIDATE_POLICY_VERSION
            or len({decision.candidate.page_revision for decision in self.decisions}) > 1
            or not _task_duplicate_decisions_are_consistent(self.decisions)
        ):
            raise InvalidDouyinCandidatePolicy
        object.__setattr__(self, "evaluated_at", canonical_time)

    @property
    def candidate_count(self) -> int:
        return len(self.decisions)

    @property
    def eligible_count(self) -> int:
        return self._count(DouyinCandidateDisposition.ELIGIBLE)

    @property
    def task_duplicate_count(self) -> int:
        return self._count(DouyinCandidateDisposition.DUPLICATE_IN_TASK)

    @property
    def history_duplicate_count(self) -> int:
        return self._count(DouyinCandidateDisposition.DUPLICATE_IN_HISTORY)

    @property
    def blacklisted_count(self) -> int:
        return self._count(DouyinCandidateDisposition.BLACKLISTED)

    @property
    def excluded_count(self) -> int:
        return self.candidate_count - self.eligible_count

    @property
    def eligible_candidates(self) -> tuple[DouyinCandidate, ...]:
        return tuple(decision.candidate for decision in self.decisions if decision.eligible)

    @property
    def page_revision(self) -> int | None:
        if not self.decisions:
            return None
        return self.decisions[0].candidate.page_revision

    @property
    def history_cutoff(self) -> datetime:
        return self.evaluated_at - DOUYIN_CANDIDATE_HISTORY_WINDOW

    def _count(self, disposition: DouyinCandidateDisposition) -> int:
        return sum(decision.disposition is disposition for decision in self.decisions)

    def __repr__(self) -> str:
        return (
            "DouyinCandidateEvaluation("
            f"candidate_count={self.candidate_count!r}, eligible_count={self.eligible_count!r}, "
            f"task_duplicate_count={self.task_duplicate_count!r}, "
            f"history_duplicate_count={self.history_duplicate_count!r}, "
            f"blacklisted_count={self.blacklisted_count!r}, "
            f"policy_version={self.policy_version!r}, <redacted>)"
        )


def evaluate_douyin_candidates(
    *,
    candidates: tuple[DouyinCandidate, ...],
    histories: tuple[DouyinCandidateHistoryFact, ...],
    blacklist: tuple[DouyinCandidateKey, ...],
    evaluated_at: datetime,
) -> DouyinCandidateEvaluation:
    """Classify a single ordered page snapshot without I/O or destructive filtering."""

    canonical_time = _canonical_utc(evaluated_at)
    cutoff = None if canonical_time is None else _history_cutoff(canonical_time)
    if (
        type(candidates) is not tuple
        or len(candidates) > MAX_TASK_TARGET_LIMIT
        or any(not isinstance(value, DouyinCandidate) for value in candidates)
        or len({value.page_revision for value in candidates}) > 1
        or type(histories) is not tuple
        or len(histories) > MAX_TASK_TARGET_LIMIT
        or any(not isinstance(value, DouyinCandidateHistoryFact) for value in histories)
        or type(blacklist) is not tuple
        or len(blacklist) > MAX_TASK_TARGET_LIMIT
        or any(not isinstance(value, DouyinCandidateKey) for value in blacklist)
        or canonical_time is None
        or cutoff is None
    ):
        raise InvalidDouyinCandidatePolicy
    history_keys = tuple(value.dedupe_key for value in histories)
    if len(set(history_keys)) != len(history_keys) or len(set(blacklist)) != len(blacklist):
        raise InvalidDouyinCandidatePolicy
    if any(value.observed_at > canonical_time for value in histories):
        raise InvalidDouyinCandidatePolicy

    recent_history = {
        value.dedupe_key for value in histories if cutoff <= value.observed_at <= canonical_time
    }
    blacklisted = frozenset(blacklist)
    task_seen: set[DouyinCandidateKey] = set()
    decisions: list[DouyinCandidateDecision] = []
    for value in candidates:
        key = value.dedupe_key
        if key in blacklisted:
            disposition = DouyinCandidateDisposition.BLACKLISTED
        elif key in task_seen:
            disposition = DouyinCandidateDisposition.DUPLICATE_IN_TASK
        elif key in recent_history:
            disposition = DouyinCandidateDisposition.DUPLICATE_IN_HISTORY
        else:
            disposition = DouyinCandidateDisposition.ELIGIBLE
        decisions.append(DouyinCandidateDecision(value, disposition))
        task_seen.add(key)
    return DouyinCandidateEvaluation(
        decisions=tuple(decisions),
        evaluated_at=canonical_time,
    )


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


def _task_duplicate_decisions_are_consistent(
    decisions: tuple[DouyinCandidateDecision, ...],
) -> bool:
    seen: set[DouyinCandidateKey] = set()
    for decision in decisions:
        key = decision.candidate.dedupe_key
        repeated = key in seen
        if decision.disposition is DouyinCandidateDisposition.DUPLICATE_IN_TASK:
            if not repeated:
                return False
        elif repeated and decision.disposition in {
            DouyinCandidateDisposition.ELIGIBLE,
            DouyinCandidateDisposition.DUPLICATE_IN_HISTORY,
        }:
            return False
        seen.add(key)
    return True


def _history_cutoff(value: datetime) -> datetime | None:
    try:
        return value - DOUYIN_CANDIDATE_HISTORY_WINDOW
    except OverflowError:
        return None


__all__ = [
    "DOUYIN_CANDIDATE_HISTORY_WINDOW",
    "DOUYIN_CANDIDATE_POLICY_VERSION",
    "DouyinCandidateDecision",
    "DouyinCandidateDisposition",
    "DouyinCandidateEvaluation",
    "DouyinCandidateHistoryFact",
    "InvalidDouyinCandidatePolicy",
    "evaluate_douyin_candidates",
]
