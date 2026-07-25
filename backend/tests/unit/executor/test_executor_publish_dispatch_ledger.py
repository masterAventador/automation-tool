"""PB-06: the durable at-most-once record behind one Douyin publish click.

A publish click is not repeatable and has no Control Plane-signed authority to
lean on: the operator confirms it locally and the executor presses the button
once. This ledger is the only thing standing between "confirmed once" and "the
same clip posted twice", so every transition it grants is asserted here.
"""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from automation_tool.executor.ledger import ExecutorLedger, ExecutorLedgerRejected
from automation_tool.executor.side_effect_ledger import LocalPublishDispatch, SideEffectState

NOW = datetime(2026, 7, 25, 6, 0, tzinfo=UTC)
INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174005"
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174006"
MINIMUM_INTERVAL_SECONDS = 60


def job_id(index: int) -> str:
    return str(UUID(f"423e4567-e89b-42d3-a456-{index:012d}"))


def content_hash(marker: str) -> str:
    return hashlib.sha256(marker.encode("ascii")).hexdigest()


@pytest.fixture
def ledger(tmp_path: Path) -> ExecutorLedger:
    return ExecutorLedger(
        state_directory=tmp_path / "ledger",
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
    )


def prepared(ledger: ExecutorLedger, index: int = 1, *, at: datetime = NOW) -> LocalPublishDispatch:
    return ledger.prepare_publish_dispatch(
        publish_job_id=job_id(index),
        content_hash=content_hash(f"job-{index}"),
        prepared_at=at,
    )


def begun(
    ledger: ExecutorLedger,
    index: int = 1,
    *,
    at: datetime = NOW + timedelta(seconds=1),
) -> LocalPublishDispatch:
    return ledger.begin_publish_dispatch(
        publish_job_id=job_id(index),
        content_hash=content_hash(f"job-{index}"),
        dispatched_at=at,
        minimum_interval_seconds=MINIMUM_INTERVAL_SECONDS,
    )


def test_preparing_records_the_confirmed_content_before_any_click(ledger: ExecutorLedger) -> None:
    effect = prepared(ledger)

    assert effect.publish_job_id == job_id(1)
    assert effect.content_hash == content_hash("job-1")
    assert effect.state is SideEffectState.PREPARED
    assert effect.revision == 1
    assert effect.dispatched_at is None
    assert effect.settled_at is None
    assert effect.replayed is False
    assert ledger.get_publish_dispatch(job_id(1)) == effect


def test_an_unknown_job_has_no_dispatch_record(ledger: ExecutorLedger) -> None:
    assert ledger.get_publish_dispatch(job_id(9)) is None


def test_preparing_the_same_confirmed_content_replays_instead_of_restarting(
    ledger: ExecutorLedger,
) -> None:
    first = prepared(ledger)
    second = prepared(ledger, at=NOW + timedelta(seconds=30))

    assert second.replayed is True
    assert second.prepared_at == first.prepared_at
    assert second.revision == 1


def test_reusing_a_job_id_for_different_content_is_rejected(ledger: ExecutorLedger) -> None:
    prepared(ledger)

    with pytest.raises(ExecutorLedgerRejected):
        ledger.prepare_publish_dispatch(
            publish_job_id=job_id(1),
            content_hash=content_hash("edited-after-confirmation"),
            prepared_at=NOW,
        )


def test_only_one_caller_is_ever_granted_the_click(ledger: ExecutorLedger) -> None:
    prepared(ledger)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = [future.result() for future in [pool.submit(begun, ledger) for _ in range(4)]]

    granted = [outcome for outcome in outcomes if not outcome.replayed]
    assert len(granted) == 1
    assert granted[0].state is SideEffectState.DISPATCHED
    assert granted[0].revision == 2
    assert all(outcome.state is SideEffectState.DISPATCHED for outcome in outcomes)


def test_the_click_cannot_be_granted_without_a_prepared_confirmation(
    ledger: ExecutorLedger,
) -> None:
    with pytest.raises(ExecutorLedgerRejected):
        begun(ledger)


def test_the_click_cannot_be_granted_for_content_that_was_not_confirmed(
    ledger: ExecutorLedger,
) -> None:
    prepared(ledger)

    with pytest.raises(ExecutorLedgerRejected):
        ledger.begin_publish_dispatch(
            publish_job_id=job_id(1),
            content_hash=content_hash("edited-after-confirmation"),
            dispatched_at=NOW + timedelta(seconds=1),
            minimum_interval_seconds=MINIMUM_INTERVAL_SECONDS,
        )


def test_the_emergency_stop_blocks_a_publish_that_was_never_prepared(
    ledger: ExecutorLedger,
) -> None:
    ledger.engage_action_emergency_stop(changed_at=NOW)

    with pytest.raises(ExecutorLedgerRejected):
        prepared(ledger)


def test_the_emergency_stop_blocks_a_publish_that_was_already_prepared(
    ledger: ExecutorLedger,
) -> None:
    prepared(ledger)
    ledger.engage_action_emergency_stop(changed_at=NOW)

    with pytest.raises(ExecutorLedgerRejected):
        begun(ledger)


def test_a_second_publish_inside_the_minimum_interval_is_refused(
    ledger: ExecutorLedger,
) -> None:
    prepared(ledger, 1)
    begun(ledger, 1, at=NOW + timedelta(seconds=1))
    prepared(ledger, 2, at=NOW + timedelta(seconds=2))

    with pytest.raises(ExecutorLedgerRejected):
        begun(ledger, 2, at=NOW + timedelta(seconds=MINIMUM_INTERVAL_SECONDS))


def test_a_second_publish_after_the_minimum_interval_is_granted(ledger: ExecutorLedger) -> None:
    prepared(ledger, 1)
    begun(ledger, 1, at=NOW + timedelta(seconds=1))
    prepared(ledger, 2, at=NOW + timedelta(seconds=2))

    effect = begun(ledger, 2, at=NOW + timedelta(seconds=MINIMUM_INTERVAL_SECONDS + 1))

    assert effect.state is SideEffectState.DISPATCHED
    assert effect.replayed is False


def test_the_interval_is_measured_against_the_click_not_the_confirmation(
    ledger: ExecutorLedger,
) -> None:
    """A job prepared long ago but clicked just now still starts the interval."""
    prepared(ledger, 1, at=NOW)
    begun(ledger, 1, at=NOW + timedelta(hours=1))
    prepared(ledger, 2, at=NOW + timedelta(hours=1, seconds=1))

    with pytest.raises(ExecutorLedgerRejected):
        begun(ledger, 2, at=NOW + timedelta(hours=1, seconds=2))


def test_a_click_that_predates_its_own_confirmation_is_rejected(ledger: ExecutorLedger) -> None:
    prepared(ledger, at=NOW)

    with pytest.raises(ExecutorLedgerRejected):
        begun(ledger, at=NOW - timedelta(seconds=1))


def test_independent_evidence_settles_the_dispatch_as_verified(ledger: ExecutorLedger) -> None:
    prepared(ledger)
    begun(ledger)

    effect = ledger.verify_publish_dispatch(
        publish_job_id=job_id(1),
        content_hash=content_hash("job-1"),
        verification_fingerprint=hashlib.sha256(b"works-list").digest(),
        verified_at=NOW + timedelta(seconds=5),
    )

    assert effect.state is SideEffectState.VERIFIED
    assert effect.revision == 3
    assert effect.verification_fingerprint == hashlib.sha256(b"works-list").digest()
    assert effect.replayed is False


def test_an_unreadable_outcome_settles_the_dispatch_as_uncertain(ledger: ExecutorLedger) -> None:
    prepared(ledger)
    begun(ledger)

    effect = ledger.mark_publish_dispatch_uncertain(
        publish_job_id=job_id(1),
        content_hash=content_hash("job-1"),
        uncertain_at=NOW + timedelta(seconds=5),
    )

    assert effect.state is SideEffectState.UNCERTAIN
    assert effect.revision == 3
    assert effect.verification_fingerprint is None
    assert effect.replayed is False


def test_settling_a_dispatch_that_never_happened_is_rejected(ledger: ExecutorLedger) -> None:
    prepared(ledger)

    with pytest.raises(ExecutorLedgerRejected):
        ledger.verify_publish_dispatch(
            publish_job_id=job_id(1),
            content_hash=content_hash("job-1"),
            verification_fingerprint=hashlib.sha256(b"works-list").digest(),
            verified_at=NOW + timedelta(seconds=5),
        )


def test_a_settled_dispatch_never_reopens(ledger: ExecutorLedger) -> None:
    prepared(ledger)
    begun(ledger)
    ledger.mark_publish_dispatch_uncertain(
        publish_job_id=job_id(1),
        content_hash=content_hash("job-1"),
        uncertain_at=NOW + timedelta(seconds=5),
    )

    replay = ledger.mark_publish_dispatch_uncertain(
        publish_job_id=job_id(1),
        content_hash=content_hash("job-1"),
        uncertain_at=NOW + timedelta(seconds=9),
    )
    assert replay.replayed is True
    assert replay.settled_at == NOW + timedelta(seconds=5)

    with pytest.raises(ExecutorLedgerRejected):
        ledger.verify_publish_dispatch(
            publish_job_id=job_id(1),
            content_hash=content_hash("job-1"),
            verification_fingerprint=hashlib.sha256(b"works-list").digest(),
            verified_at=NOW + timedelta(seconds=9),
        )

    # A second click request reports the settled outcome instead of raising, so
    # a caller can never read "unavailable" as "unknown, safe to press again".
    regrant = begun(ledger, at=NOW + timedelta(seconds=9))
    assert regrant.replayed is True
    assert regrant.state is SideEffectState.UNCERTAIN
    assert regrant.revision == 3
    assert regrant.dispatched_at == NOW + timedelta(seconds=1)


def test_a_replayed_click_reports_the_recorded_outcome_without_regranting(
    ledger: ExecutorLedger,
) -> None:
    prepared(ledger)
    begun(ledger)
    ledger.verify_publish_dispatch(
        publish_job_id=job_id(1),
        content_hash=content_hash("job-1"),
        verification_fingerprint=hashlib.sha256(b"works-list").digest(),
        verified_at=NOW + timedelta(seconds=5),
    )

    replay = begun(ledger, at=NOW + timedelta(seconds=9))

    assert replay.replayed is True
    assert replay.state is SideEffectState.VERIFIED
    assert replay.revision == 3


@pytest.mark.parametrize(
    "publish_job_id",
    ["", "not-a-uuid", job_id(1).upper(), "123e4567-e89b-12d3-a456-426614174000"],
)
def test_a_job_identifier_outside_the_canonical_shape_is_rejected(
    ledger: ExecutorLedger,
    publish_job_id: str,
) -> None:
    with pytest.raises(ExecutorLedgerRejected):
        ledger.prepare_publish_dispatch(
            publish_job_id=publish_job_id,
            content_hash=content_hash("job-1"),
            prepared_at=NOW,
        )


@pytest.mark.parametrize(
    "value",
    ["", "z" * 64, content_hash("job-1").upper(), content_hash("job-1")[:63]],
)
def test_a_content_hash_outside_the_canonical_shape_is_rejected(
    ledger: ExecutorLedger,
    value: str,
) -> None:
    with pytest.raises(ExecutorLedgerRejected):
        ledger.prepare_publish_dispatch(
            publish_job_id=job_id(1),
            content_hash=value,
            prepared_at=NOW,
        )


def test_a_naive_timestamp_is_rejected(ledger: ExecutorLedger) -> None:
    with pytest.raises(ExecutorLedgerRejected):
        ledger.prepare_publish_dispatch(
            publish_job_id=job_id(1),
            content_hash=content_hash("job-1"),
            prepared_at=NOW.replace(tzinfo=None),
        )


def test_the_stored_row_keeps_no_publish_content(ledger: ExecutorLedger, tmp_path: Path) -> None:
    """Title, description and file path must never reach the durable record."""
    prepared(ledger)
    begun(ledger)

    with sqlite3.connect(ledger.database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(executor_publish_dispatches)")
        }

    assert columns == {
        "publish_job_id",
        "content_hash",
        "state",
        "prepared_at",
        "dispatched_at",
        "settled_at",
        "verification_fingerprint",
        "revision",
    }


def test_the_record_redacts_itself_when_printed(ledger: ExecutorLedger) -> None:
    effect = prepared(ledger)

    printed = repr(effect)
    assert content_hash("job-1") not in printed
    assert job_id(1) not in printed
    assert "prepared" in printed
