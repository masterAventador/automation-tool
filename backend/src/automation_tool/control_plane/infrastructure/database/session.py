"""SQLAlchemy async engine and transaction-scoped session management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from automation_tool.control_plane.domain import DependencyUnavailable


class Database:
    """Own one async engine and expose transaction-scoped sessions."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        connect_timeout_seconds: float = 5.0,
    ) -> "Database":
        engine = create_async_engine(
            url,
            connect_args={"timeout": connect_timeout_seconds},
            hide_parameters=True,
            pool_pre_ping=True,
        )
        return cls(engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session inside a transaction that commits or rolls back atomically."""

        async with self._sessions.begin() as session:
            yield session

    async def check_connection(self) -> None:
        """Check PostgreSQL without exposing driver or credential details."""

        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("select 1"))
        except (OSError, SQLAlchemyError, TimeoutError):
            raise DependencyUnavailable("postgresql") from None

    async def close(self) -> None:
        """Dispose the engine and all pooled connections."""

        await self._engine.dispose()
