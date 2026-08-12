"""SQLAlchemy async engine and transaction-scoped session management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
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
        if engine.dialect.name == "sqlite":
            # SQLite ships with foreign keys off per connection; the schema
            # relies on them. WAL keeps the reader (App HTTP) and the writer
            # (executor thread) from blocking each other.
            @event.listens_for(engine.sync_engine, "connect")
            def _configure_sqlite(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        return cls(engine)

    async def create_schema(self) -> None:
        """Create any missing tables. The demo has no migration history.

        CHECK constraints are dropped wholesale before DDL: their expressions
        were written in PostgreSQL SQL (``::text``, ``~`` regexes) and the
        domain layer already enforces every one of those shapes. Primary keys,
        foreign keys, unique constraints and indexes are kept.
        """

        from sqlalchemy import CheckConstraint

        from automation_tool.control_plane.infrastructure.database.schema import metadata

        for table in metadata.tables.values():
            for constraint in [
                item for item in table.constraints if isinstance(item, CheckConstraint)
            ]:
                table.constraints.remove(constraint)

        async with self._engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session inside a transaction that commits or rolls back atomically."""

        async with self._sessions.begin() as session:
            yield session

    async def check_connection(self) -> None:
        """Check the database without exposing driver or credential details."""

        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("select 1"))
        except (OSError, SQLAlchemyError, TimeoutError):
            raise DependencyUnavailable("database") from None

    async def close(self) -> None:
        """Dispose the engine and all pooled connections."""

        await self._engine.dispose()
