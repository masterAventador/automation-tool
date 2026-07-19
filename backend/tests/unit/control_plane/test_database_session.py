from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from automation_tool.control_plane.infrastructure.database import Database


class SuccessfulConnection:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    async def execute(self, statement: object) -> None:
        self._statements.append(str(statement))


class ConnectionScope:
    def __init__(self, connection: SuccessfulConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> SuccessfulConnection:
        return self._connection

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class SuccessfulEngine:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def connect(self) -> ConnectionScope:
        return ConnectionScope(SuccessfulConnection(self.statements))


@pytest.mark.asyncio
async def test_database_connection_check_executes_the_real_probe_statement() -> None:
    engine = SuccessfulEngine()
    database = Database(cast(AsyncEngine, engine))

    await database.check_connection()

    assert engine.statements == ["select 1"]
