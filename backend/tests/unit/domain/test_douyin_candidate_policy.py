from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, cast

import pytest

from automation_tool.control_plane.domain.douyin_candidate_policy import (
    DOUYIN_CANDIDATE_HISTORY_WINDOW,
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DouyinCandidateDecision,
    DouyinCandidateDisposition,
    DouyinCandidateEvaluation,
    DouyinCandidateHistoryFact,
    InvalidDouyinCandidatePolicy,
    evaluate_douyin_candidates,
)
from automation_tool.protocol import (
    MAX_TASK_TARGET_LIMIT,
    DouyinCandidate,
    DouyinCandidateKey,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

NOW = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)


def candidate(
    target_id: str,
    *,
    display_name: str | None = None,
    public_handle: str | None = None,
    page_revision: int = 7,
) -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id=target_id,
        summary=DouyinCandidateSummary(
            display_name=target_id if display_name is None else display_name,
            public_handle=public_handle,
        ),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=page_revision,
    )


def history(value: DouyinCandidate, observed_at: datetime) -> DouyinCandidateHistoryFact:
    return DouyinCandidateHistoryFact(
        dedupe_key=value.dedupe_key,
        observed_at=observed_at,
    )


def evaluate(
    candidates: tuple[DouyinCandidate, ...],
    *,
    histories: tuple[DouyinCandidateHistoryFact, ...] = (),
    blacklist: tuple[DouyinCandidateKey, ...] = (),
    evaluated_at: datetime = NOW,
) -> DouyinCandidateEvaluation:
    return evaluate_douyin_candidates(
        candidates=candidates,
        histories=histories,
        blacklist=blacklist,
        evaluated_at=evaluated_at,
    )


def test_policy_preserves_order_and_uses_one_explicit_reason_with_fixed_precedence() -> None:
    blacklisted = candidate("blacklisted-1")
    historical = candidate("historical-1")
    eligible = candidate("eligible-1")

    result = evaluate(
        (
            blacklisted,
            blacklisted,
            historical,
            historical,
            eligible,
            eligible,
        ),
        histories=(history(historical, NOW - timedelta(days=1)),),
        blacklist=(blacklisted.dedupe_key,),
    )

    assert [decision.candidate for decision in result.decisions] == [
        blacklisted,
        blacklisted,
        historical,
        historical,
        eligible,
        eligible,
    ]
    assert [decision.disposition for decision in result.decisions] == [
        DouyinCandidateDisposition.BLACKLISTED,
        DouyinCandidateDisposition.BLACKLISTED,
        DouyinCandidateDisposition.DUPLICATE_IN_HISTORY,
        DouyinCandidateDisposition.DUPLICATE_IN_TASK,
        DouyinCandidateDisposition.ELIGIBLE,
        DouyinCandidateDisposition.DUPLICATE_IN_TASK,
    ]
    assert result.eligible_candidates == (eligible,)
    assert result.eligible_count == 1
    assert result.blacklisted_count == 2
    assert result.task_duplicate_count == 2
    assert result.history_duplicate_count == 1
    assert result.excluded_count == 5
    assert result.candidate_count == 6
    assert result.page_revision == 7
    assert result.policy_version == DOUYIN_CANDIDATE_POLICY_VERSION
    assert result.evaluated_at is NOW


def test_same_key_is_stable_across_summary_changes_and_drives_task_deduplication() -> None:
    first = candidate("same-target", display_name="旧名称", public_handle="old.handle")
    changed = candidate("same-target", display_name="新名称", public_handle="new.handle")

    result = evaluate((first, changed))

    assert first.dedupe_key == changed.dedupe_key
    assert result.decisions[0].disposition is DouyinCandidateDisposition.ELIGIBLE
    assert result.decisions[1].disposition is DouyinCandidateDisposition.DUPLICATE_IN_TASK
    assert result.eligible_candidates == (first,)


def test_history_window_is_fixed_thirty_days_and_includes_the_exact_cutoff() -> None:
    at_cutoff = candidate("at-cutoff")
    before_cutoff = candidate("before-cutoff")
    cutoff = NOW - DOUYIN_CANDIDATE_HISTORY_WINDOW

    result = evaluate(
        (at_cutoff, before_cutoff),
        histories=(
            history(at_cutoff, cutoff),
            history(before_cutoff, cutoff - timedelta(microseconds=1)),
        ),
    )

    assert timedelta(days=30) == DOUYIN_CANDIDATE_HISTORY_WINDOW
    assert result.history_cutoff == cutoff
    assert result.decisions[0].disposition is DouyinCandidateDisposition.DUPLICATE_IN_HISTORY
    assert result.decisions[1].disposition is DouyinCandidateDisposition.ELIGIBLE


def test_empty_candidate_snapshot_is_valid_and_has_zero_counts() -> None:
    result = evaluate(())

    assert result.decisions == ()
    assert result.eligible_candidates == ()
    assert result.candidate_count == 0
    assert result.excluded_count == 0
    assert result.page_revision is None


def test_history_fact_is_immutable_canonical_utc_and_redacted() -> None:
    value = candidate("private-target")
    fact = history(value, NOW)

    assert fact.observed_at is NOW
    assert repr(fact) == "DouyinCandidateHistoryFact(<redacted>)"
    assert "private-target" not in repr(fact)
    assert str(value.dedupe_key) not in repr(fact)
    with pytest.raises(FrozenInstanceError):
        fact.observed_at = NOW + timedelta(days=1)  # type: ignore[misc]


@pytest.mark.parametrize(
    "observed_at",
    (
        datetime(2026, 7, 20),
        datetime(2026, 7, 20, tzinfo=timezone(timedelta(hours=8))),
        "2026-07-20T00:00:00Z",
        True,
    ),
)
def test_history_fact_rejects_non_utc_or_non_datetime_values(observed_at: Any) -> None:
    with pytest.raises(InvalidDouyinCandidatePolicy, match="policy input is invalid"):
        DouyinCandidateHistoryFact(
            dedupe_key=candidate("private-target").dedupe_key,
            observed_at=observed_at,
        )


def test_broken_timezone_and_clock_before_history_window_fail_closed() -> None:
    class BrokenTimezone(tzinfo):
        def utcoffset(self, _value: datetime | None) -> timedelta | None:
            raise RuntimeError("private timezone failure")

        def dst(self, _value: datetime | None) -> timedelta | None:
            return None

        def tzname(self, _value: datetime | None) -> str | None:
            return None

    private = candidate("private-target")
    with pytest.raises(InvalidDouyinCandidatePolicy, match="policy input is invalid"):
        DouyinCandidateHistoryFact(
            dedupe_key=private.dedupe_key,
            observed_at=datetime(2026, 7, 20, tzinfo=BrokenTimezone()),
        )
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate((), evaluated_at=datetime.min.replace(tzinfo=UTC))

    with pytest.raises(InvalidDouyinCandidatePolicy):
        DouyinCandidateHistoryFact(
            dedupe_key=cast(DouyinCandidateKey, object()),
            observed_at=NOW,
        )


def test_future_history_relative_to_evaluation_is_rejected_without_value_echo() -> None:
    private = candidate("private-future-target")
    with pytest.raises(InvalidDouyinCandidatePolicy, match="policy input is invalid") as captured:
        evaluate(
            (private,),
            histories=(history(private, NOW + timedelta(microseconds=1)),),
        )

    assert "private-future-target" not in str(captured.value)
    assert str(private.dedupe_key) not in str(captured.value)


@pytest.mark.parametrize(
    "evaluated_at",
    (
        datetime(2026, 7, 20),
        datetime(2026, 7, 20, tzinfo=timezone(timedelta(hours=-5))),
        "2026-07-20T00:00:00Z",
        True,
    ),
)
def test_evaluation_rejects_non_utc_or_non_datetime_clock(evaluated_at: Any) -> None:
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate((), evaluated_at=evaluated_at)


def test_candidate_snapshot_requires_one_page_revision() -> None:
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate((candidate("one", page_revision=1), candidate("two", page_revision=2)))


@pytest.mark.parametrize("field", ("candidates", "histories", "blacklist"))
def test_collection_inputs_require_exact_bounded_tuples(field: str) -> None:
    private = candidate("private-target")
    values: dict[str, object] = {
        "candidates": (private,),
        "histories": (history(private, NOW),),
        "blacklist": (private.dedupe_key,),
        "evaluated_at": NOW,
    }
    values[field] = list(cast(tuple[object, ...], values[field]))
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate_douyin_candidates(**values)  # type: ignore[arg-type]

    oversized: dict[str, object] = {
        "candidates": (),
        "histories": (),
        "blacklist": (),
        "evaluated_at": NOW,
    }
    if field == "candidates":
        oversized[field] = tuple(private for _ in range(MAX_TASK_TARGET_LIMIT + 1))
    elif field == "histories":
        oversized[field] = tuple(history(private, NOW) for _ in range(MAX_TASK_TARGET_LIMIT + 1))
    else:
        oversized[field] = tuple(private.dedupe_key for _ in range(MAX_TASK_TARGET_LIMIT + 1))
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate_douyin_candidates(**oversized)  # type: ignore[arg-type]


def test_collection_members_and_lookup_inputs_are_strongly_typed_and_canonical() -> None:
    private = candidate("private-target")
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate(cast(tuple[DouyinCandidate, ...], (object(),)))
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate((private,), histories=cast(tuple[DouyinCandidateHistoryFact, ...], (object(),)))
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate((private,), blacklist=cast(tuple[DouyinCandidateKey, ...], (object(),)))
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate(
            (private,),
            histories=(history(private, NOW), history(private, NOW - timedelta(days=1))),
        )
    with pytest.raises(InvalidDouyinCandidatePolicy):
        evaluate((private,), blacklist=(private.dedupe_key, private.dedupe_key))


def test_maximum_candidate_snapshot_is_accepted_without_quadratic_output_growth() -> None:
    candidates = tuple(candidate(f"target-{index}") for index in range(MAX_TASK_TARGET_LIMIT))

    result = evaluate(candidates)

    assert result.candidate_count == MAX_TASK_TARGET_LIMIT
    assert result.eligible_count == MAX_TASK_TARGET_LIMIT
    assert result.excluded_count == 0


def test_decision_and_evaluation_are_immutable_and_redacted() -> None:
    private = candidate("private-target")
    result = evaluate((private,))
    decision = result.decisions[0]

    assert decision.eligible is True
    assert repr(decision) == "DouyinCandidateDecision(disposition='eligible', <redacted>)"
    assert "private-target" not in repr(result)
    assert str(private.dedupe_key) not in repr(result)
    with pytest.raises(FrozenInstanceError):
        decision.disposition = DouyinCandidateDisposition.BLACKLISTED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.decisions = ()  # type: ignore[misc]


def test_decision_rejects_forged_candidate_or_disposition() -> None:
    private = candidate("private-target")
    with pytest.raises(InvalidDouyinCandidatePolicy):
        DouyinCandidateDecision(
            candidate=cast(DouyinCandidate, object()),
            disposition=DouyinCandidateDisposition.ELIGIBLE,
        )
    with pytest.raises(InvalidDouyinCandidatePolicy):
        DouyinCandidateDecision(
            candidate=private,
            disposition=cast(DouyinCandidateDisposition, "eligible"),
        )


def test_evaluation_rejects_forged_payload_version_time_or_revision_mix() -> None:
    one = candidate("one", page_revision=1)
    two = candidate("two", page_revision=2)
    valid = evaluate((one,))
    invalid_values = (
        {"decisions": [valid.decisions[0]]},
        {"decisions": (cast(DouyinCandidateDecision, object()),)},
        {
            "decisions": (
                valid.decisions[0],
                DouyinCandidateDecision(two, DouyinCandidateDisposition.ELIGIBLE),
            )
        },
        {"evaluated_at": datetime(2026, 7, 20)},
        {"evaluated_at": datetime.min.replace(tzinfo=UTC)},
        {"policy_version": "douyin.candidate-policy.v2"},
    )
    for values in invalid_values:
        with pytest.raises(InvalidDouyinCandidatePolicy):
            replace(valid, **values)


def test_evaluation_rejects_inconsistent_task_duplicate_decisions() -> None:
    private = candidate("private-target")
    for decisions in (
        (
            DouyinCandidateDecision(
                private,
                DouyinCandidateDisposition.DUPLICATE_IN_TASK,
            ),
        ),
        (
            DouyinCandidateDecision(private, DouyinCandidateDisposition.ELIGIBLE),
            DouyinCandidateDecision(private, DouyinCandidateDisposition.ELIGIBLE),
        ),
        (
            DouyinCandidateDecision(private, DouyinCandidateDisposition.ELIGIBLE),
            DouyinCandidateDecision(
                private,
                DouyinCandidateDisposition.DUPLICATE_IN_HISTORY,
            ),
        ),
    ):
        with pytest.raises(InvalidDouyinCandidatePolicy):
            DouyinCandidateEvaluation(decisions=decisions, evaluated_at=NOW)


def test_noneligible_decision_property_is_false_for_every_exclusion_reason() -> None:
    private = candidate("private-target")
    for disposition in (
        DouyinCandidateDisposition.DUPLICATE_IN_TASK,
        DouyinCandidateDisposition.DUPLICATE_IN_HISTORY,
        DouyinCandidateDisposition.BLACKLISTED,
    ):
        assert DouyinCandidateDecision(private, disposition).eligible is False
