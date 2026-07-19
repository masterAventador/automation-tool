from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, cast

import pytest
from sqlalchemy.engine import RowMapping

from automation_tool.control_plane.application.task_targets import (
    TaskTargetPersistenceRejected,
    TaskTargetRecord,
)
from automation_tool.control_plane.domain import (
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DouyinCandidateDisposition,
    InstallationId,
    TargetId,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database.task_target_repository import _record
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

NOW = datetime(2026, 7, 19, 2, 0, tzinfo=UTC)


class BrokenTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        raise RuntimeError("private timezone failure")

    def dst(self, value: datetime | None) -> timedelta | None:
        return None

    def tzname(self, value: datetime | None) -> str | None:
        return None


def record() -> TaskTargetRecord:
    return TaskTargetRecord(
        target_id=TargetId.new(),
        task_id=TaskId.new(),
        installation_id=InstallationId.new(),
        ordinal=1,
        candidate=DouyinCandidate(
            platform_target_id="private-target",
            summary=DouyinCandidateSummary(
                display_name="私密昵称",
                public_handle="private_handle",
            ),
            source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
            page_revision=7,
        ),
        disposition=DouyinCandidateDisposition.ELIGIBLE,
        policy_version=DOUYIN_CANDIDATE_POLICY_VERSION,
        evaluated_at=NOW,
        created_at=NOW,
    )


def test_target_record_is_immutable_canonical_and_redacted() -> None:
    value = record()

    assert value.evaluated_at is NOW
    assert value.created_at is NOW
    assert repr(value) == (
        "TaskTargetRecord(ordinal=1, disposition='eligible', page_revision=7, <redacted>)"
    )
    assert "private-target" not in repr(value)
    assert "私密昵称" not in repr(value)
    with pytest.raises(FrozenInstanceError):
        value.ordinal = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("target_id", TaskId.new()),
        ("task_id", TargetId.new()),
        ("installation_id", TaskId.new()),
        ("ordinal", True),
        ("ordinal", 0),
        ("ordinal", 101),
        ("candidate", object()),
        ("disposition", "eligible"),
        ("policy_version", "latest"),
        ("evaluated_at", datetime(2026, 7, 19, 2, 0)),
        ("evaluated_at", datetime(2026, 7, 19, 10, 0, tzinfo=timezone(timedelta(hours=8)))),
        ("evaluated_at", "2026-07-19T02:00:00Z"),
        ("evaluated_at", datetime(2026, 7, 19, 2, 0, tzinfo=BrokenTimezone())),
        ("created_at", NOW - timedelta(microseconds=1)),
    ),
)
def test_target_record_rejects_invalid_fields(field: str, invalid: Any) -> None:
    with pytest.raises(TaskTargetPersistenceRejected) as captured:
        replace(record(), **{field: invalid})

    assert "private" not in repr(captured.value)
    assert captured.value.__cause__ is None


def test_direct_construction_rejects_false_record_types() -> None:
    valid = record()

    with pytest.raises(TaskTargetPersistenceRejected):
        TaskTargetRecord(
            target_id=cast(TargetId, "private"),
            task_id=valid.task_id,
            installation_id=valid.installation_id,
            ordinal=valid.ordinal,
            candidate=valid.candidate,
            disposition=valid.disposition,
            policy_version=valid.policy_version,
            evaluated_at=valid.evaluated_at,
            created_at=valid.created_at,
        )


def test_database_row_adapter_rejects_malformed_rows_without_driver_details() -> None:
    with pytest.raises(TaskTargetPersistenceRejected) as captured:
        _record(cast(RowMapping, {}))

    assert captured.value.__cause__ is None
