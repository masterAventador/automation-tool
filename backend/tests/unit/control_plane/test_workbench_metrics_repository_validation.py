from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.workbench_metrics import (
    InvalidWorkbenchMetrics,
    WorkbenchMetricsRepositoryRejected,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.workbench_metrics_repository import (
    SqlAlchemyWorkbenchMetricsRepository,
)


class FailingSessionScope:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def __aenter__(self) -> object:
        raise self.failure

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class FailingSessions:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def begin(self) -> FailingSessionScope:
        return FailingSessionScope(self.failure)


@pytest.mark.asyncio
async def test_repository_rejects_invalid_dependencies_scope_and_database_failure() -> None:
    with pytest.raises(WorkbenchMetricsRepositoryRejected):
        SqlAlchemyWorkbenchMetricsRepository(cast(Database, object()))

    database = Database.from_url(
        "sqlite+aiosqlite:////nonexistent-automation-tool/unused.db",
        connect_timeout_seconds=0.01,
    )
    try:
        repository = SqlAlchemyWorkbenchMetricsRepository(database)
        with pytest.raises(InvalidWorkbenchMetrics):
            await repository.get(installation_id=cast(InstallationId, object()))

        object.__setattr__(
            database,
            "_sessions",
            FailingSessions(SQLAlchemyError("private database failure")),
        )
        with pytest.raises(WorkbenchMetricsRepositoryRejected) as captured:
            await repository.get(installation_id=InstallationId.new())
        assert "private" not in str(captured.value)
        assert captured.value.__cause__ is None
    finally:
        await database.close()
