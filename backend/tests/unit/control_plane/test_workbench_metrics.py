from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from automation_tool.control_plane.application.workbench_metrics import (
    InvalidWorkbenchMetrics,
    WorkbenchMetricsRepositoryRejected,
    WorkbenchMetricsService,
    WorkbenchMetricsSnapshot,
    WorkbenchMetricsUnavailable,
)
from automation_tool.control_plane.domain import InstallationId

INSTALLATION_ID = InstallationId.new()


def snapshot() -> WorkbenchMetricsSnapshot:
    return WorkbenchMetricsSnapshot(
        task_total=9,
        task_succeeded=3,
        task_failed=2,
        task_handoff_required=1,
        task_outcome_uncertain=1,
        action_total=12,
        action_succeeded=7,
        action_failed=2,
        action_outcome_uncertain=1,
    )


class StaticRepository:
    def __init__(
        self,
        value: WorkbenchMetricsSnapshot | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.value = snapshot() if value is None else value
        self.failure = failure
        self.installation_ids: list[InstallationId] = []

    async def get(self, *, installation_id: InstallationId) -> WorkbenchMetricsSnapshot:
        self.installation_ids.append(installation_id)
        if self.failure is not None:
            raise self.failure
        return self.value


@pytest.mark.asyncio
async def test_metrics_service_returns_one_installation_scoped_structured_snapshot() -> None:
    repository = StaticRepository()
    service = WorkbenchMetricsService(repository=repository)

    result = await service.get(installation_id=INSTALLATION_ID)

    assert result == snapshot()
    assert repository.installation_ids == [INSTALLATION_ID]
    assert "private" not in repr(result)


@pytest.mark.asyncio
async def test_metrics_service_rejects_invalid_scope_and_repository_failure_safely() -> None:
    service = WorkbenchMetricsService(repository=StaticRepository())

    with pytest.raises(InvalidWorkbenchMetrics) as invalid:
        await service.get(installation_id=cast(InstallationId, "private-installation"))
    assert "private-installation" not in repr(invalid.value)

    unavailable = WorkbenchMetricsService(
        repository=StaticRepository(
            failure=WorkbenchMetricsRepositoryRejected("private-database-path")
        )
    )
    with pytest.raises(WorkbenchMetricsUnavailable) as rejected:
        await unavailable.get(installation_id=INSTALLATION_ID)
    assert "private-database-path" not in repr(rejected.value)
    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    (
        cast(WorkbenchMetricsSnapshot, object()),
        replace(snapshot(), task_total=cast(int, True)),
        replace(snapshot(), task_total=-1),
        replace(snapshot(), task_total=9_007_199_254_740_992),
        replace(snapshot(), task_succeeded=10),
        replace(snapshot(), action_failed=13),
    ),
)
async def test_metrics_service_fails_closed_on_invalid_or_incoherent_counts(
    invalid: WorkbenchMetricsSnapshot,
) -> None:
    service = WorkbenchMetricsService(repository=StaticRepository(value=invalid))

    with pytest.raises(WorkbenchMetricsUnavailable):
        await service.get(installation_id=INSTALLATION_ID)
